"""Durable email outbox and payment reconciliation. Run ONE worker process."""
import logging, smtplib, ssl, time
from datetime import timedelta
from email.message import EmailMessage
from flask import current_app
from models import db, Payment, MailOutbox, GPSPing, now
from services import payments

def reconcile():
    items=db.session.scalars(db.select(Payment).where(Payment.status.in_(['created','pending','unknown']),Payment.provider_ref.is_not(None),Payment.created_at<now()-timedelta(seconds=45)).order_by(Payment.created_at.desc()).limit(100)).all()
    for item in items:
        try:payments.verify_payment(item);db.session.commit()
        except Exception:db.session.rollback();current_app.logger.warning('Payment reconciliation deferred (payment id %s).',item.id)
    acknowledgements=db.session.scalars(db.select(Payment).where(Payment.provider=='dpo',Payment.status=='paid',Payment.provider_acknowledged_at.is_(None)).order_by(Payment.created_at).limit(100)).all()
    for item in acknowledgements:
        try:payments.acknowledge_dpo(item);db.session.commit()
        except payments.ProviderError:db.session.rollback();current_app.logger.warning('DPO processing acknowledgement deferred (payment id %s).',item.id)
    return len(items)+len(acknowledgements)

def _deliver(item):
    """Send one outbox row. Mutates item but does NOT commit — the caller owns
    the transaction. On failure, sets next_attempt_at for worker retry and
    re-raises. On success, sets sent_at and clears body."""
    cfg=current_app.config
    item.attempts+=1
    try:
        msg=EmailMessage();msg['Subject']=item.subject;msg['From']=cfg['SMTP_FROM'];msg['To']=item.recipient;msg['Message-ID']=f'<fabsexpress-{item.id}@{cfg["PUBLIC_BASE_URL"].split("//")[-1].split("/")[0]}>';msg.set_content(item.body)
        cls=smtplib.SMTP_SSL if cfg['SMTP_SSL'] else smtplib.SMTP
        with cls(cfg['SMTP_HOST'],cfg['SMTP_PORT'],timeout=20) as smtp:
            if cfg['SMTP_TLS'] and not cfg['SMTP_SSL']:smtp.starttls(context=ssl.create_default_context())
            if cfg['SMTP_USERNAME']:smtp.login(cfg['SMTP_USERNAME'],cfg['SMTP_PASSWORD'])
            smtp.send_message(msg)
        item.sent_at=now();item.body='Message delivered; original content removed.'
    except Exception:
        item.next_attempt_at=now()+timedelta(seconds=min(3600,30*2**item.attempts))
        current_app.logger.warning('Email delivery deferred (outbox id %s).',item.id)
        raise

def send_outbox():
    cfg=current_app.config
    if not cfg.get('SMTP_HOST') or not cfg.get('SMTP_FROM'):return 0
    sent=0
    for _ in range(100):
        # Row lock prevents simultaneous sends from separate workers. SMTP has
        # at-least-once semantics if the process dies after sending before commit.
        item=db.session.scalar(db.select(MailOutbox).where(MailOutbox.sent_at.is_(None),MailOutbox.attempts<10,MailOutbox.next_attempt_at<=now()).order_by(MailOutbox.created_at).with_for_update(skip_locked=True).limit(1))
        if not item:db.session.rollback();break
        try:
            _deliver(item);sent+=1
        except Exception:
            pass
        db.session.commit()
    return sent

def queue_or_send(msg):
    """Add msg to the outbox and attempt immediate delivery.

    If SMTP is configured, the message is delivered synchronously inside the
    caller's request. If delivery fails, the row remains in MailOutbox with
    retry state set (next_attempt_at, attempts), so the worker or `flask
    send-mail` will retry it later. Nothing is lost.

    IMPORTANT: the caller must still call db.session.commit(). This function
    only flushes; it never commits, so the caller's transaction stays atomic.
    """
    db.session.add(msg);db.session.flush()
    cfg=current_app.config
    if not cfg.get('SMTP_HOST') or not cfg.get('SMTP_FROM'):return
    try:
        _deliver(msg)
    except Exception:
        pass  # row stays queued; worker or flask send-mail will retry it later

if __name__=='__main__':
    from app import create_app
    app=create_app();last_prune=0
    while True:
        with app.app_context():
            try:
                send_outbox();reconcile()
                if time.monotonic()-last_prune>3600:
                    db.session.execute(db.delete(GPSPing).where(GPSPing.received_at<now()-timedelta(days=app.config['GPS_RETENTION_DAYS'])));db.session.commit();last_prune=time.monotonic()
            except Exception:db.session.rollback();app.logger.exception('Worker cycle failed.')
        time.sleep(30)