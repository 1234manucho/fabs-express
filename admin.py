import csv, hashlib, io, secrets
from datetime import date, timedelta
from decimal import Decimal
from flask import Blueprint, current_app, request, render_template, redirect, url_for, flash, abort
from flask_login import current_user
from models import db, User, Tariff, Shipment, Payment, SupportRequest, AuditLog, now
from auth import admin_required
from services.shipping import COUNTRIES,SERVICES,STATUS,TRANSITIONS,num,minor,text_field,add_event,audit,notify_shipment
from services import payments as pay
admin=Blueprint('admin',__name__,url_prefix='/admin')

def get_shipment(public_id):
    item=db.session.scalar(db.select(Shipment).where(Shipment.public_id==public_id).with_for_update())
    if not item:abort(404)
    return item

def has_open_payment(shipment):return db.session.scalar(db.select(Payment.id).where(Payment.shipment_id==shipment.id,Payment.status.in_(['created','pending','unknown','review'])).limit(1)) is not None

@admin.get('/')
@admin_required
def overview():
    stmt=db.select(Shipment)
    q=request.args.get('q','').strip();status=request.args.get('status','')
    if q:stmt=stmt.where(Shipment.tracking.ilike('%'+q[:60]+'%'))
    if status in STATUS:stmt=stmt.where(Shipment.status==status)
    page=db.paginate(stmt.order_by(Shipment.created_at.desc()),per_page=20,max_per_page=20)
    counts={s:n for s,n in db.session.execute(db.select(Shipment.status,db.func.count()).group_by(Shipment.status))}
    collected=dict(db.session.execute(db.select(Payment.currency,db.func.sum(Payment.amount_minor)).where(Payment.status=='paid').group_by(Payment.currency)).all())
    return render_template('admin/overview.html',page=page,counts=counts,collected=collected)

@admin.route('/shipments/<public_id>',methods=['GET','POST'])
@admin_required
def shipment(public_id):
    item=get_shipment(public_id);driver_url=None
    if request.method=='POST':
        try:
            action=request.form.get('action')
            if action=='quote':
                if item.status not in ('quote_requested','awaiting_payment') or item.payment_status=='paid' or has_open_payment(item):raise ValueError('A quote cannot change while a payment is active or after payment.')
                charges={key:minor(request.form.get(key,'0')) for key in ('transport_minor','fuel_minor','handling_minor','tax_minor')}
                total=sum(charges.values())
                if total<=0:raise ValueError('Enter a positive shipping total.')
                currency=request.form.get('currency')
                if currency not in ('KES','USD'):raise ValueError('Choose KES or USD.')
                details={**charges,'total_minor':total,'currency':currency,'tariff':'Shipment-specific approved quote','transit':text_field(request.form,'transit',3,100),'duties':text_field(request.form,'quote_notes',10,500),'approved_by':current_user.id}
                item.currency=currency;item.amount_minor=total;item.quote_details=details;item.quote_version+=1;item.quote_expires_at=now()+timedelta(hours=48)
                add_event(item,'awaiting_payment',item.origin['city'],'Shipping quote confirmed. Review the charges before payment.',current_user.id);notify_shipment(item,current_app.config['PUBLIC_BASE_URL'])
            elif action=='event':
                status=request.form.get('status')
                if status not in TRANSITIONS.get(item.status,set()):raise ValueError('That status change is not valid for this shipment.')
                if status=='cancelled' and has_open_payment(item):raise ValueError('Reconcile the open payment before cancelling.')
                if status!='cancelled' and item.payment_status!='paid':raise ValueError('A verified payment is required before dispatch or movement.')
                location=text_field(request.form,'location',2,160);note=text_field(request.form,'note',3,500)
                if status=='delivered':
                    item.delivery_recipient=text_field(request.form,'delivery_recipient',2,100);item.driver_token_hash=None;item.driver_token_expires=None
                add_event(item,status,location,note,current_user.id);notify_shipment(item,current_app.config['PUBLIC_BASE_URL'])
            elif action=='driver':
                if item.status not in ('in_transit','out_for_delivery') or item.payment_status!='paid':raise ValueError('Start the paid shipment before issuing a driver link.')
                token=secrets.token_urlsafe(40);item.driver_token_hash=hashlib.sha256(token.encode()).hexdigest();item.driver_token_expires=now()+timedelta(days=7);driver_url=current_app.config['PUBLIC_BASE_URL']+'/driver#'+token;audit('driver.token_issued',item.public_id,current_user.id)
            elif action=='revoke-driver':item.driver_token_hash=None;item.driver_token_expires=None;audit('driver.token_revoked',item.public_id,current_user.id)
            elif action=='carrier':
                item.carrier_name=text_field(request.form,'carrier_name',0,80);item.carrier_tracking=text_field(request.form,'carrier_tracking',0,80);audit('shipment.carrier_updated',item.public_id,current_user.id)
            else:raise ValueError('Select a valid action.')
            db.session.commit();flash('Shipment updated.','success')
            if not driver_url:return redirect(url_for('admin.shipment',public_id=public_id))
        except ValueError as exc:db.session.rollback();flash(str(exc),'error')
    return render_template('admin/shipment.html',shipment=item,transitions=TRANSITIONS.get(item.status,set()),driver_url=driver_url)

TARIFF_FIELDS=['name','origin','destination','origin_city','destination_city','zone','service','currency','max_kg','base_charge','included_kg','step_kg','step_charge','divisor','fuel_percent','tax_percent','handling_charge','min_days','max_days','valid_from','valid_to']
def parse_tariff(data):
    origin=text_field(data,'origin',2,2).upper();destination=text_field(data,'destination',2,2).upper()
    if origin not in dict(COUNTRIES) or destination not in dict(COUNTRIES):raise ValueError('Use valid ISO two-letter country codes.')
    service=data.get('service');currency=data.get('currency','KES')
    if service not in SERVICES or currency not in ('KES','USD'):raise ValueError('Choose a valid service and KES or USD currency.')
    start=date.fromisoformat(data.get('valid_from',''));end=date.fromisoformat(data.get('valid_to',''))
    if end<start:raise ValueError('Rate expiry must follow its start date.')
    max_kg=num(data.get('max_kg'));included=num(data.get('included_kg'),Decimal('0'))
    if included>max_kg:raise ValueError('Included weight cannot exceed maximum weight.')
    min_days=int(num(data.get('min_days'),Decimal('0'),Decimal('365'),0));max_days=int(num(data.get('max_days'),Decimal('1'),Decimal('365'),0))
    if min_days>max_days:raise ValueError('Transit minimum cannot exceed maximum.')
    base=minor(data.get('base_charge'));step=minor(data.get('step_charge'));handling=minor(data.get('handling_charge'))
    if base+handling<=0:raise ValueError('A base or handling charge greater than zero is required.')
    return Tariff(name=text_field(data,'name',3,100),origin=origin,destination=destination,origin_city=text_field(data,'origin_city',0,80),destination_city=text_field(data,'destination_city',0,80),zone=text_field(data,'zone',1,40),service=service,currency=currency,max_kg=max_kg,base_minor=base,included_kg=included,step_kg=num(data.get('step_kg')),step_minor=step,divisor=int(num(data.get('divisor'),Decimal('1'),Decimal('100000'),0)),fuel_bps=int(num(data.get('fuel_percent'),Decimal('0'),Decimal('100'),2)*100),tax_bps=int(num(data.get('tax_percent'),Decimal('0'),Decimal('100'),2)*100),handling_minor=handling,min_days=min_days,max_days=max_days,valid_from=start,valid_to=end,active=False)

@admin.route('/tariffs',methods=['GET','POST'])
@admin_required
def tariffs():
    if request.method=='POST':
        try:
            if request.form.get('action')=='toggle':
                tariff=db.session.get(Tariff,request.form.get('id',0,type=int))
                if not tariff:abort(404)
                tariff.active=not tariff.active;audit('tariff.activated' if tariff.active else 'tariff.deactivated',tariff.id,current_user.id)
            elif request.form.get('action')=='import':
                file=request.files.get('csv_file')
                if not file:raise ValueError('Choose a CSV file.')
                content=file.read(500001)
                if len(content)>500000:raise ValueError('CSV exceeds 500 KB.')
                reader=csv.DictReader(io.StringIO(content.decode('utf-8-sig')))
                if not reader.fieldnames or set(reader.fieldnames)!=set(TARIFF_FIELDS):raise ValueError('Use the exact header from the downloadable CSV template.')
                count=0
                for i,row in enumerate(reader,start=2):
                    if i>1001:raise ValueError('Import at most 1,000 rates at a time.')
                    try:db.session.add(parse_tariff(row))
                    except ValueError as exc:raise ValueError(f'CSV row {i}: {exc}')
                    count+=1
                if not count:raise ValueError('The file has no rates. Add your approved charges below the header.')
                audit('tariff.imported','batch',current_user.id,{'count':count})
            else:
                tariff=parse_tariff(request.form);db.session.add(tariff);db.session.flush();audit('tariff.created',tariff.id,current_user.id)
            db.session.commit();flash('Rates saved. New rates remain inactive until you explicitly publish them.','success');return redirect(url_for('admin.tariffs'))
        except (ValueError,UnicodeError) as exc:db.session.rollback();flash(str(exc),'error')
    page=db.paginate(db.select(Tariff).order_by(Tariff.created_at.desc()),per_page=25,max_per_page=25)
    return render_template('admin/tariffs.html',page=page,today=date.today().isoformat())

@admin.get('/tariffs/template.csv')
@admin_required
def tariff_template():
    output=io.StringIO();csv.writer(output).writerow(TARIFF_FIELDS)
    return current_app.response_class(output.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=FabsExpress_Rate_Import_Template.csv'})

@admin.route('/payments',methods=['GET','POST'])
@admin_required
def payments():
    if request.method=='POST':
        item=db.session.scalar(db.select(Payment).where(Payment.public_id==request.form.get('payment')))
        if not item:abort(404)
        try:
            if request.form.get('action')=='reconcile':
                pay.verify_payment(item);audit('payment.reconciled',item.public_id,current_user.id);db.session.commit();flash('Provider verification completed: '+item.status,'info')
            elif request.form.get('action')=='resolve-failed':
                if item.status not in ('created','unknown','review'):raise ValueError('Only held attempts may be resolved here.')
                evidence=text_field(request.form,'evidence',20,500)
                if request.form.get('confirmed')!='yes':raise ValueError('Confirm that the provider verified no funds were collected, or refunded the payment.')
                item.status='failed';item.error='Resolved by administrator after provider reconciliation.';audit('payment.hold_resolved',item.public_id,current_user.id,{'evidence':evidence});db.session.commit();flash('Hold resolved. The customer may retry payment.','success')
        except (pay.ProviderError,ValueError) as exc:db.session.rollback();flash(str(exc),'error')
        return redirect(url_for('admin.payments'))
    page=db.paginate(db.select(Payment).order_by(Payment.created_at.desc()),per_page=25,max_per_page=25)
    return render_template('admin/payments.html',page=page)

@admin.route('/customers',methods=['GET','POST'])
@admin_required
def customers():
    if request.method=='POST':
        user=db.session.get(User,request.form.get('id',0,type=int))
        if not user or user.role=='admin':abort(403)
        user.enabled=not user.enabled;user.token_version+=1;audit('customer.enabled' if user.enabled else 'customer.disabled',user.id,current_user.id);db.session.commit();return redirect(url_for('admin.customers'))
    page=db.paginate(db.select(User).where(User.role=='customer').order_by(User.created_at.desc()),per_page=25,max_per_page=25)
    return render_template('admin/customers.html',page=page)

@admin.route('/support',methods=['GET','POST'])
@admin_required
def support():
    if request.method=='POST':
        item=db.session.get(SupportRequest,request.form.get('id',0,type=int))
        if not item:abort(404)
        item.resolved=not item.resolved;audit('support.resolved' if item.resolved else 'support.reopened',item.reference,current_user.id);db.session.commit();return redirect(url_for('admin.support'))
    page=db.paginate(db.select(SupportRequest).order_by(SupportRequest.created_at.desc()),per_page=20,max_per_page=20)
    return render_template('admin/support.html',page=page)

@admin.get('/audit')
@admin_required
def audit_page():
    page=db.paginate(db.select(AuditLog).order_by(AuditLog.created_at.desc()),per_page=40,max_per_page=40)
    return render_template('admin/audit.html',page=page)

@admin.get('/settings')
@admin_required
def settings():return render_template('admin/settings.html',mpesa_ready=pay.ready('mpesa'),dpo_ready=pay.ready('dpo'))
