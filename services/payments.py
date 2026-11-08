"""Live-only provider adapters. Payment initiation is never treated as settlement."""
import base64, hashlib, secrets
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
from xml.etree.ElementTree import Element, SubElement, tostring
from defusedxml.ElementTree import fromstring
import requests
from flask import current_app
from models import db, Payment, Shipment, now
from services.shipping import add_event, notify_shipment, audit

class ProviderError(Exception): pass
class ProviderUnavailable(ProviderError): pass
class ProviderAmbiguous(ProviderError): pass

def config(): return current_app.config
def ready(provider):
    keys={'mpesa':['MPESA_CONSUMER_KEY','MPESA_CONSUMER_SECRET','MPESA_SHORTCODE','MPESA_PASSKEY'], 'dpo':['DPO_COMPANY_TOKEN','DPO_SERVICE_TYPE','DPO_CALLBACK_SECRET']}[provider]
    return all(config().get(k) for k in keys) and config()['PUBLIC_BASE_URL'].startswith('https://')
def request(method,url,**kwargs):
    try:
        r=requests.request(method,url,timeout=(5,25),allow_redirects=False,**kwargs)
        r.raise_for_status()
        return r
    except requests.RequestException as exc:
        # Never log provider bodies, authentication headers or credentials.
        raise ProviderAmbiguous('Provider response unavailable. Verification is required before retrying.') from exc

def mpesa_auth():
    r=request('GET','https://api.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials',auth=(config()['MPESA_CONSUMER_KEY'],config()['MPESA_CONSUMER_SECRET']))
    try:return r.json()['access_token']
    except (ValueError,KeyError):raise ProviderError('M-Pesa authentication response was invalid.')
def mpesa_password():
    timestamp=datetime.now(ZoneInfo('Africa/Nairobi')).strftime('%Y%m%d%H%M%S')
    password=base64.b64encode((config()['MPESA_SHORTCODE']+config()['MPESA_PASSKEY']+timestamp).encode()).decode()
    return timestamp,password

def start_mpesa(payment,callback_secret):
    if not ready('mpesa'): raise ProviderUnavailable('M-Pesa is not configured.')
    if payment.currency!='KES' or payment.amount_minor%100: raise ProviderError('M-Pesa requires a whole KES amount. Choose card payment for this charge.')
    token=mpesa_auth();stamp,password=mpesa_password()
    payload={'BusinessShortCode':config()['MPESA_SHORTCODE'],'Password':password,'Timestamp':stamp,'TransactionType':config()['MPESA_TRANSACTION_TYPE'],'Amount':payment.amount_minor//100,'PartyA':payment.phone,'PartyB':config()['MPESA_SHORTCODE'],'PhoneNumber':payment.phone,'CallBackURL':f"{config()['PUBLIC_BASE_URL']}/webhooks/mpesa/{payment.public_id}/{callback_secret}",'AccountReference':payment.public_id[:12],'TransactionDesc':'FabsExpress shipping'}
    try:data=request('POST','https://api.safaricom.co.ke/mpesa/stkpush/v1/processrequest',headers={'Authorization':'Bearer '+token},json=payload).json()
    except ValueError:raise ProviderAmbiguous('M-Pesa returned an unreadable response.')
    if str(data.get('ResponseCode'))!='0' or not data.get('CheckoutRequestID'): raise ProviderError('M-Pesa did not accept this payment request.')
    payment.provider_ref=data['CheckoutRequestID'];payment.status='pending'

def dpo_call(operation,fields=None,transaction=None,service=None):
    root=Element('API3G');SubElement(root,'CompanyToken').text=config()['DPO_COMPANY_TOKEN'];SubElement(root,'Request').text=operation
    for key,value in (fields or {}).items():SubElement(root,key).text=str(value)
    if transaction:
        node=SubElement(root,'Transaction')
        for key,value in transaction.items():SubElement(node,key).text=str(value)
    if service:
        node=SubElement(SubElement(root,'Services'),'Service')
        for key,value in service.items():SubElement(node,key).text=str(value)
    r=request('POST','https://secure.3gdirectpay.com/API/v6/',data=tostring(root,encoding='utf-8'),headers={'Content-Type':'application/xml'})
    if len(r.content)>262144:raise ProviderError('DPO response too large.')
    try:parsed=fromstring(r.content)
    except Exception:raise ProviderAmbiguous('DPO returned an unreadable response.')
    return {n.tag:(n.text or '').strip() for n in parsed.iter()}

def start_dpo(payment):
    if not ready('dpo'):raise ProviderUnavailable('Card payments are not configured.')
    result=dpo_call('createToken',transaction={'PaymentAmount':f'{Decimal(payment.amount_minor)/100:.2f}','PaymentCurrency':payment.currency,'CompanyRef':payment.public_id,'CompanyRefUnique':'1','RedirectURL':f"{config()['PUBLIC_BASE_URL']}/payments/dpo/return/{payment.public_id}",'BackURL':f"{config()['PUBLIC_BASE_URL']}/shipments/{payment.shipment.public_id}",'DefaultPayment':'CC','TransactionChargeType':'1','PTL':'30','PTLtype':'minutes'},service={'ServiceDescription':'FabsExpress shipping '+payment.shipment.tracking,'ServiceType':config()['DPO_SERVICE_TYPE'],'ServiceDate':datetime.now(ZoneInfo('Africa/Nairobi')).strftime('%Y/%m/%d %H:%M')})
    if result.get('Result')!='000' or not result.get('TransToken'):raise ProviderError('DPO did not accept this payment request. Check the approved logistics service type.')
    payment.provider_ref=result['TransToken'];payment.status='pending'
    return 'https://secure.3gdirectpay.com/payv2.php?'+urlencode({'ID':payment.provider_ref})

def mark_paid(payment,receipt=None):
    # Lock parent first everywhere to serialize checkout, callbacks and admin edits.
    shipment=db.session.scalar(db.select(Shipment).where(Shipment.id==payment.shipment_id).with_for_update())
    payment=db.session.scalar(db.select(Payment).where(Payment.id==payment.id).execution_options(populate_existing=True).with_for_update())
    if payment.status=='paid':return
    if shipment.status=='cancelled' or shipment.payment_status=='paid':
        payment.status='review';payment.error='Payment received after cancellation or another settlement. Review and refund with the provider.';audit('payment.review',payment.public_id);return
    if payment.amount_minor!=shipment.amount_minor or payment.currency!=shipment.currency:
        payment.status='review';payment.error='Payment does not match current quote.';return
    if receipt:
        duplicate=db.session.scalar(db.select(Payment).where(Payment.receipt==receipt,Payment.id!=payment.id))
        if duplicate:payment.status='review';payment.error='Provider receipt already used.';return
        payment.receipt=receipt
    payment.status='paid';payment.verified_at=now();payment.error=None
    shipment.payment_status='paid'
    if shipment.status=='awaiting_payment':add_event(shipment,'booked',shipment.origin['city'],'Payment verified. Booking confirmed.')
    audit('payment.verified',payment.public_id,detail={'provider':payment.provider,'amount_minor':payment.amount_minor,'currency':payment.currency})
    notify_shipment(shipment,config()['PUBLIC_BASE_URL'])

def lock_payment(payment):
    db.session.scalar(db.select(Shipment).where(Shipment.id==payment.shipment_id).with_for_update())
    return db.session.scalar(db.select(Payment).where(Payment.id==payment.id).execution_options(populate_existing=True).with_for_update())

def verify_payment(payment):
    if payment.status=='paid':return 'paid'
    if not payment.provider_ref:return payment.status
    if payment.provider=='dpo':
        data=dpo_call('verifyToken',fields={'TransactionToken':payment.provider_ref,'VerifyTransaction':'0'})
        payment=lock_payment(payment)
        if payment.status=='paid':return 'paid'
        code=data.get('Result','')
        if code=='000':
            try:amount=Decimal(data.get('TransactionAmount','NaN'))
            except InvalidOperation:amount=Decimal('NaN')
            if not amount.is_finite() or amount!=Decimal(payment.amount_minor)/100 or data.get('TransactionCurrency','').upper()!=payment.currency:
                payment.status='review';payment.error='Provider currency or amount differs from the accepted quote.'
            else:mark_paid(payment, data.get('TransactionRef') or payment.provider_ref)
        elif code in ('901','903','904'):payment.status='failed';payment.error='Card payment declined, cancelled or expired.'
        elif code in ('001','002','007'):payment.status='review';payment.error='DPO payment requires reconciliation.'
    else:
        stamp,password=mpesa_password();token=mpesa_auth()
        try:data=request('POST','https://api.safaricom.co.ke/mpesa/stkpushquery/v1/query',headers={'Authorization':'Bearer '+token},json={'BusinessShortCode':config()['MPESA_SHORTCODE'],'Password':password,'Timestamp':stamp,'CheckoutRequestID':payment.provider_ref}).json()
        except ValueError:raise ProviderAmbiguous('M-Pesa returned an unreadable status.')
        if data.get('CheckoutRequestID') and data['CheckoutRequestID']!=payment.provider_ref:raise ProviderError('M-Pesa request identifier mismatch.')
        payment=lock_payment(payment)
        if payment.status=='paid':return 'paid'
        code=str(data.get('ResultCode',''))
        if code=='0':mark_paid(payment)
        elif code in ('1032','1037','2001','1'):payment.status='failed';payment.error='M-Pesa payment was not completed.'
    return payment.status


def acknowledge_dpo(payment):
    """Confirm durable processing to DPO only after our paid state is committed."""
    if payment.provider!='dpo' or payment.status!='paid' or payment.provider_acknowledged_at:return False
    data=dpo_call('verifyToken',fields={'TransactionToken':payment.provider_ref,'VerifyTransaction':'1'})
    if data.get('Result')=='000':
        payment.provider_acknowledged_at=now()
        return True
    return False
