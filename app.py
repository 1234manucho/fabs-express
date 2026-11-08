"""FabsExpress Flask application factory and customer / tracking / payment routes."""
import csv, hashlib, hmac, io, json, os, re, secrets, time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from functools import wraps
from urllib.parse import urlsplit
import click
from flask import Flask, request, render_template, redirect, url_for, flash, jsonify, abort, session, g, send_file
from flask_login import LoginManager, current_user, login_required, logout_user
from flask_wtf.csrf import CSRFProtect, CSRFError
from flask_migrate import Migrate, upgrade
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix
from sqlalchemy.exc import IntegrityError
from defusedxml.ElementTree import fromstring
from config import Config
from models import db, User, Tariff, Shipment, Package, ShipmentEvent, GPSPing, Payment, WebhookReceipt, SupportRequest, AuditLog, MailOutbox, now
from auth import auth, admin_required, verified_required, email_address
from services.shipping import COUNTRIES, SERVICES, STATUS, TRANSITIONS, num, minor, money, country, text_field, packages_from, find_quote, utc, iso, add_event, audit, notify_shipment
from services import payments as pay
from services.documents import label_pdf, invoice_pdf

csrf=CSRFProtect();login_manager=LoginManager();migrate=Migrate();limiter=Limiter(key_func=get_remote_address,default_limits=['300 per minute'])

def create_app(overrides=None):
    app=Flask(__name__);app.config.from_object(Config)
    if overrides:app.config.update(overrides)
    production=app.config['APP_ENV']=='production' and not app.config.get('TESTING')
    if not app.config['SECRET_KEY'] or len(app.config['SECRET_KEY'])<32:raise RuntimeError('Set SECRET_KEY to a securely generated value of at least 32 characters.')
    if production:
        required=['LEGAL_NAME','DATA_ENCRYPTION_KEY','SMTP_HOST','SMTP_FROM','BUSINESS_ADDRESS','SUPPORT_EMAIL']
        missing=[key for key in required if not app.config.get(key)]
        if not app.config['SQLALCHEMY_DATABASE_URI'].startswith('postgresql+psycopg://'):missing.append('PostgreSQL DATABASE_URL')
        if not app.config['RATELIMIT_STORAGE_URI'].startswith(('redis://','rediss://')):missing.append('REDIS_URL')
        if not app.config['PUBLIC_BASE_URL'].startswith('https://'):missing.append('HTTPS PUBLIC_BASE_URL')
        if missing:raise RuntimeError('Production configuration missing: '+', '.join(missing))
    if app.config.get('DATA_ENCRYPTION_KEY'):
        from cryptography.fernet import Fernet
        Fernet(app.config['DATA_ENCRYPTION_KEY'].encode())
    if app.config['TRUST_PROXY']:app.wsgi_app=ProxyFix(app.wsgi_app,x_for=1,x_proto=1,x_host=0,x_port=0)
    db.init_app(app);csrf.init_app(app);login_manager.init_app(app);migrate.init_app(app,db);limiter.init_app(app)
    login_manager.login_view='auth.login';login_manager.session_protection='strong'
    app.register_blueprint(auth)
    from admin import admin
    app.register_blueprint(admin)
    for endpoint,limits in [('auth.login','8 per minute; 50 per hour'),('auth.register','5 per hour'),('auth.forgot_password','5 per hour'),('auth.verification','5 per hour')]:
        app.view_functions[endpoint]=limiter.limit(limits)(app.view_functions[endpoint])

    @login_manager.user_loader
    def load_user(uid):
        try:return db.session.get(User,int(uid))
        except (ValueError,TypeError):return None

    @app.before_request
    def request_setup():
        g.nonce=secrets.token_urlsafe(18)
        if current_user.is_authenticated and (not current_user.enabled or session.get('token_version')!=current_user.token_version):logout_user();session.clear()

    @app.after_request
    def headers(response):
        map_origin=app.config.get('MAP_ORIGIN','')
        if map_origin and (not map_origin.startswith('https://') or any(c in map_origin for c in ' ;\n\r')):map_origin=''
        response.headers['Content-Security-Policy']=f"default-src 'self'; script-src 'self' 'nonce-{g.get('nonce','')}'; style-src 'self' 'unsafe-inline'; img-src 'self' data: {map_origin}; connect-src 'self' {map_origin}; font-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self' https://secure.3gdirectpay.com"
        response.headers['X-Content-Type-Options']='nosniff';response.headers['Referrer-Policy']='strict-origin-when-cross-origin';response.headers['Permissions-Policy']='geolocation=(self), camera=(), microphone=()'
        if production:response.headers['Strict-Transport-Security']='max-age=31536000'
        if request.endpoint!='static':response.headers['Cache-Control']='no-store'
        return response

    @app.context_processor
    def context():return dict(countries=COUNTRIES,services=SERVICES,status_labels=STATUS,money=money,country_name=country,nonce=g.get('nonce',''),year=now().year,company=app.config)
    app.jinja_env.filters['datefmt']=lambda value:utc(value).astimezone(__import__('zoneinfo').ZoneInfo('Africa/Nairobi')).strftime('%d %b %Y · %H:%M EAT') if isinstance(value,datetime) else value.strftime('%d %b %Y') if value else 'Not set'
    app.jinja_env.filters['iso']=iso

    @app.errorhandler(CSRFError)
    def csrf_error(exc):return render_template('error.html',code=400,message='Your form session expired. Reload the page and try again.'),400
    for code in (400,403,404,413,429,500):
        def handler(exc,code=code):
            if code==500:db.session.rollback()
            messages={400:'The request could not be processed.',403:'You do not have access to this page.',404:'We could not find that page or shipment.',413:'The submitted request is too large.',429:'Too many requests. Please wait a moment before trying again.',500:'Something went wrong. Please try again or contact support.'}
            if request.path.startswith('/api/'):return jsonify(error=messages[code]),code
            return render_template('error.html',code=code,message=messages[code]),code
        app.register_error_handler(code,handler)

    @app.get('/')
    def index():return render_template('index.html')
    @app.get('/healthz')
    @limiter.exempt
    def health():
        try:db.session.execute(db.text('SELECT 1'));return jsonify(status='ok')
        except Exception:return jsonify(status='unavailable'),503
    @app.get('/services')
    def services_page():return render_template('services.html')
    @app.get('/shipping-guide')
    def guide():return render_template('guide.html')
    @app.get('/privacy')
    def privacy():return render_template('privacy.html')

    @app.route('/support',methods=['GET','POST'])
    @limiter.limit('15 per hour',methods=['POST'])
    def support():
        if request.method=='POST':
            try:
                item=SupportRequest(name=text_field(request.form,'name',2,100),email=email_address(request.form.get('email','')),subject=text_field(request.form,'subject',3,160),message=text_field(request.form,'message',10,5000));db.session.add(item);db.session.commit();flash('Your message was received. Reference: '+item.reference,'success');return redirect(url_for('support'))
            except ValueError as exc:flash(str(exc),'error')
        return render_template('support.html')

    @app.route('/rates',methods=['GET','POST'])
    def rates():
        quote=None;searched=False
        if request.method=='POST':
            try:
                origin=request.form.get('origin');destination=request.form.get('destination');service=request.form.get('service');ship_date=date.fromisoformat(request.form.get('ship_date',''))
                if origin not in dict(COUNTRIES) or destination not in dict(COUNTRIES) or service not in SERVICES:raise ValueError('Choose valid countries and a service.')
                if ship_date<date.today():raise ValueError('Choose today or a future shipping date.')
                quote=find_quote(origin,destination,service,packages_from(request.form),ship_date,text_field(request.form,'origin_city',1,80),text_field(request.form,'destination_city',1,80));searched=True
            except ValueError as exc:flash(str(exc),'error')
        rows=db.session.scalars(db.select(Tariff).where(Tariff.active.is_(True),Tariff.valid_from<=date.today(),Tariff.valid_to>=date.today()).order_by(Tariff.origin,Tariff.destination,Tariff.service,Tariff.max_kg)).all()
        return render_template('rates.html',quote=quote,searched=searched,tariffs=rows,today=date.today().isoformat())

    @app.get('/rate-sheet.csv')
    def rate_sheet():
        rows=db.session.scalars(db.select(Tariff).where(Tariff.active.is_(True),Tariff.valid_from<=date.today(),Tariff.valid_to>=date.today()).order_by(Tariff.origin,Tariff.destination)).all()
        output=io.StringIO();writer=csv.writer(output);writer.writerow(['Origin','Destination','Origin city','Destination city','Zone','Service','Currency','Maximum kg','Base charge','Included kg','Increment kg','Increment charge','Dimensional divisor','Fuel %','Tax %','Handling','Transit days','Valid from','Valid to'])
        safe=lambda v: "'"+str(v) if str(v).startswith(('=','+','-','@','\t','\r')) else str(v)
        for r in rows:writer.writerow([safe(v) for v in [country(r.origin),country(r.destination),r.origin_city or 'Country-wide',r.destination_city or 'Country-wide',r.zone,SERVICES[r.service],r.currency,r.max_kg,Decimal(r.base_minor)/100,r.included_kg,r.step_kg,Decimal(r.step_minor)/100,r.divisor,Decimal(r.fuel_bps)/100,Decimal(r.tax_bps)/100,Decimal(r.handling_minor)/100,f'{r.min_days}-{r.max_days}',r.valid_from,r.valid_to]])
        return app.response_class(output.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=FabsExpress_Approved_Rate_Sheet.csv'})

    @app.get('/dashboard')
    @login_required
    def dashboard():
        page=db.paginate(db.select(Shipment).where(Shipment.user_id==current_user.id).order_by(Shipment.created_at.desc()),per_page=15,max_per_page=15)
        totals={status:count for status,count in db.session.execute(db.select(Shipment.status,db.func.count()).where(Shipment.user_id==current_user.id).group_by(Shipment.status))}
        return render_template('dashboard.html',page=page,totals=totals)

    @app.route('/ship',methods=['GET','POST'])
    @verified_required
    def ship():
        if request.method=='POST':
            try:
                key=text_field(request.form,'booking_key',32,64)
                existing=db.session.scalar(db.select(Shipment).where(Shipment.booking_key==key))
                if existing:
                    if existing.user_id!=current_user.id:abort(400)
                    return redirect(url_for('shipment_detail',public_id=existing.public_id))
                if key not in session.get('booking_keys',[]):raise ValueError('Reload the booking form before submitting.')
                addresses={}
                for prefix in ('origin','destination'):
                    addr={field:text_field(request.form,prefix+'_'+field,1,limit) for field,limit in [('name',100),('phone',24),('address',180),('city',80),('country',2)]}
                    if addr['country'] not in dict(COUNTRIES):raise ValueError('Select a valid country.')
                    addr['postal']=text_field(request.form,prefix+'_postal',0,20);addresses[prefix]=addr
                service=request.form.get('service')
                if service not in SERVICES:raise ValueError('Choose a valid service.')
                pickup=date.fromisoformat(request.form.get('pickup_date',''))
                if not date.today()<=pickup<=date.today()+timedelta(days=90):raise ValueError('Pickup must be within the next 90 days.')
                window=request.form.get('pickup_window')
                if window not in ('09:00–13:00','13:00–17:00'):raise ValueError('Select a pickup window.')
                if request.form.get('restricted')!='yes' or request.form.get('terms')!='yes':raise ValueError('Confirm your package contents and accept the shipping conditions.')
                packages=packages_from(request.form);quote=find_quote(addresses['origin']['country'],addresses['destination']['country'],service,packages,pickup,addresses['origin']['city'],addresses['destination']['city'])
                hs=text_field(request.form,'hs_code',0,10);manufacture=text_field(request.form,'country_of_manufacture',0,2)
                if hs and not re.fullmatch(r'\d{6,10}',hs):raise ValueError('HS code must contain 6–10 digits.')
                if manufacture and manufacture not in dict(COUNTRIES):raise ValueError('Choose a valid country of manufacture.')
                shipment=Shipment(user_id=current_user.id,booking_key=key,origin=addresses['origin'],destination=addresses['destination'],service=service,description=text_field(request.form,'description',5,500),hs_code=hs,country_of_manufacture=manufacture,declared_minor=minor(request.form.get('declared_value','0')),currency=quote['currency'] if quote else 'KES',pickup_date=pickup,pickup_window=window,amount_minor=quote['total_minor'] if quote else None,quote_details=quote,quote_expires_at=now()+timedelta(hours=48) if quote else None)
                for package in packages:shipment.packages.append(Package(**package))
                db.session.add(shipment);db.session.flush();add_event(shipment,'awaiting_payment' if quote else 'quote_requested',addresses['origin']['city'],'Shipment request received. '+('Review and pay your shipping quote.' if quote else 'Our team will confirm service availability and pricing.'),current_user.id);notify_shipment(shipment,app.config['PUBLIC_BASE_URL']);db.session.commit()
                return redirect(url_for('shipment_detail',public_id=shipment.public_id))
            except ValueError as exc:flash(str(exc),'error')
            except IntegrityError:
                db.session.rollback();flash('This booking was already submitted. Check your shipments.','warning');return redirect(url_for('dashboard'))
        key=secrets.token_hex(20);session['booking_keys']=(session.get('booking_keys',[])+[key])[-10:]
        return render_template('ship.html',booking_key=key,today=date.today().isoformat())

    def owned_shipment(public_id,lock=False):
        stmt=db.select(Shipment).where(Shipment.public_id==public_id)
        if lock:stmt=stmt.with_for_update()
        shipment=db.session.scalar(stmt)
        if not shipment or (shipment.user_id!=current_user.id and current_user.role!='admin'):abort(404)
        return shipment

    @app.get('/shipments/<public_id>')
    @login_required
    def shipment_detail(public_id):
        shipment=owned_shipment(public_id)
        active=next((p for p in sorted(shipment.payments,key=lambda p:p.id,reverse=True) if p.status in ('created','pending','unknown','review')),None)
        return render_template('shipment.html',shipment=shipment,payment=active,can_pay=shipment.amount_minor and shipment.status=='awaiting_payment' and shipment.quote_expires_at and utc(shipment.quote_expires_at)>now(),mpesa_ready=pay.ready('mpesa'),dpo_ready=pay.ready('dpo'))

    @app.get('/shipments/<public_id>/<kind>.pdf')
    @login_required
    def shipment_pdf(public_id,kind):
        shipment=owned_shipment(public_id)
        if kind=='label':
            if shipment.payment_status!='paid':abort(403)
            data=label_pdf(shipment,app.config['PUBLIC_BASE_URL'])
        elif kind=='invoice' and shipment.amount_minor:data=invoice_pdf(shipment,app.config)
        else:abort(404)
        return send_file(data,mimetype='application/pdf',as_attachment=True,download_name=f'FabsExpress_{shipment.tracking}_{kind}.pdf')

    @app.get('/track')
    @limiter.limit('30 per minute')
    def track():
        number=request.args.get('tracking','').strip().upper();shipment=None
        if number:shipment=db.session.scalar(db.select(Shipment).where(Shipment.tracking==number))
        owner=shipment and current_user.is_authenticated and (current_user.id==shipment.user_id or current_user.role=='admin')
        return render_template('tracking.html',shipment=shipment,number=number,owner=owner)

    @app.get('/api/tracking/<tracking>')
    @limiter.limit('60 per minute')
    def tracking_api(tracking):
        shipment=db.session.scalar(db.select(Shipment).where(Shipment.tracking==tracking.upper()))
        if not shipment:abort(404)
        data={'tracking':shipment.tracking,'status':shipment.status,'label':STATUS[shipment.status],'updated_at':iso(shipment.updated_at),'events':[{'status':STATUS[e.status],'location':e.location,'note':e.note,'at':iso(e.created_at)} for e in shipment.events]}
        if current_user.is_authenticated and (current_user.id==shipment.user_id or current_user.role=='admin'):
            pings=list(reversed(db.session.scalars(db.select(GPSPing).where(GPSPing.shipment_id==shipment.id).order_by(GPSPing.recorded_at.desc()).limit(1000)).all()))
            data['positions']=[{'lat':p.latitude,'lng':p.longitude,'accuracy':p.accuracy,'at':iso(p.recorded_at)} for p in pings];data['gps_stale_seconds']=app.config['GPS_STALE_SECONDS'];data['map_tile_url']=app.config['MAP_TILE_URL'];data['map_attribution']=app.config['MAP_ATTRIBUTION']
        return jsonify(data)

    @app.get('/driver')
    def driver():return render_template('driver.html')

    @app.post('/api/driver/location')
    @csrf.exempt
    @limiter.limit('30 per minute')
    def driver_location():
        token=request.headers.get('Authorization','').removeprefix('Bearer ').strip()
        if len(token)<32:abort(403)
        shipment=db.session.scalar(db.select(Shipment).where(Shipment.driver_token_hash==hashlib.sha256(token.encode()).hexdigest()).with_for_update())
        if not shipment or not shipment.driver_token_expires or utc(shipment.driver_token_expires)<now() or shipment.status not in ('in_transit','out_for_delivery'):abort(403)
        try:
            data=request.get_json();lat=float(num(data.get('latitude'),Decimal('-90'),Decimal('90'),places=18));lng=float(num(data.get('longitude'),Decimal('-180'),Decimal('180'),places=18));accuracy=float(num(data.get('accuracy'),Decimal('0'),Decimal('500'),places=18));recorded=datetime.fromisoformat(data.get('timestamp','').replace('Z','+00:00'))
            if recorded.tzinfo is None or abs((now()-recorded).total_seconds())>180:raise ValueError('GPS reading is not current.')
            last=db.session.scalar(db.select(GPSPing).where(GPSPing.shipment_id==shipment.id).order_by(GPSPing.recorded_at.desc()).limit(1))
            if last and recorded<=utc(last.recorded_at):return jsonify(status='ignored',reason='old_reading')
            db.session.add(GPSPing(shipment_id=shipment.id,latitude=lat,longitude=lng,accuracy=accuracy,recorded_at=recorded));db.session.commit();return jsonify(status='received',at=iso(recorded))
        except (ValueError,TypeError,AttributeError):return jsonify(error='Provide a current, accurate GPS reading with valid coordinates.'),400

    @app.post('/shipments/<public_id>/pay')
    @verified_required
    @limiter.limit('6 per minute')
    def payment_start(public_id):
        shipment=owned_shipment(public_id,True);provider=request.form.get('provider')
        if provider not in ('mpesa','dpo'):abort(400)
        if shipment.payment_status=='paid':return redirect(url_for('shipment_detail',public_id=public_id))
        if shipment.status!='awaiting_payment' or not shipment.amount_minor or not shipment.quote_expires_at or utc(shipment.quote_expires_at)<now():flash('Your quote needs confirmation before payment. Contact support.','warning');return redirect(url_for('shipment_detail',public_id=public_id))
        if str(shipment.quote_version)!=request.form.get('quote_version') or request.form.get('accept_quote')!='yes':flash('Review and accept the current shipping quote before paying.','error');return redirect(url_for('shipment_detail',public_id=public_id))
        existing=db.session.scalar(db.select(Payment).where(Payment.shipment_id==shipment.id,Payment.status.in_(['created','pending','unknown','review'])))
        if existing:flash('A payment is already pending. Check its status before trying again.','warning');return redirect(url_for('shipment_detail',public_id=public_id))
        if not pay.ready(provider):flash('This payment method is awaiting activation. Please contact support.','warning');return redirect(url_for('shipment_detail',public_id=public_id))
        phone=request.form.get('phone','').strip().replace(' ','').replace('+','')
        if provider=='mpesa':
            if phone.startswith('0'):phone='254'+phone[1:]
            if not re.fullmatch(r'254[17]\d{8}',phone):flash('Enter a valid Kenyan M-Pesa phone number.','error');return redirect(url_for('shipment_detail',public_id=public_id))
            if shipment.currency!='KES' or shipment.amount_minor%100:flash('Choose card payment for a quote with fractional KES or a different currency.','warning');return redirect(url_for('shipment_detail',public_id=public_id))
        callback=secrets.token_urlsafe(32)
        payment=Payment(shipment_id=shipment.id,provider=provider,amount_minor=shipment.amount_minor,currency=shipment.currency,phone=phone if provider=='mpesa' else None,callback_hash=hashlib.sha256(callback.encode()).hexdigest());shipment.accepted_at=now();db.session.add(payment);db.session.commit()
        # Persist attempt BEFORE the network call. A crash is reconciled, never silently retried.
        try:
            target=None
            if provider=='mpesa':pay.start_mpesa(payment,callback)
            else:target=pay.start_dpo(payment)
            db.session.commit()
            if target:return redirect(target,code=303)
            flash('M-Pesa request sent. Enter your PIN on your phone, then check payment status.','success')
        except pay.ProviderAmbiguous as exc:payment.status='unknown';payment.error=str(exc);db.session.commit();flash('The provider response is delayed. This attempt is held for verification to prevent a duplicate charge.','warning')
        except pay.ProviderError as exc:payment.status='failed';payment.error=str(exc);db.session.commit();flash(str(exc),'error')
        return redirect(url_for('shipment_detail',public_id=public_id))

    @app.post('/payments/<public_id>/verify')
    @login_required
    @limiter.limit('10 per minute')
    def payment_verify(public_id):
        payment=db.session.scalar(db.select(Payment).where(Payment.public_id==public_id))
        if not payment:abort(404)
        owned_shipment(payment.shipment.public_id)
        try:pay.verify_payment(payment);db.session.commit();flash('Payment status: '+payment.status.replace('_',' '), 'success' if payment.status=='paid' else 'info')
        except pay.ProviderError:db.session.rollback();flash('Verification is temporarily unavailable. Please try again shortly.','warning')
        return redirect(url_for('shipment_detail',public_id=payment.shipment.public_id))

    @app.get('/payments/dpo/return/<public_id>')
    @limiter.limit('20 per minute')
    def dpo_return(public_id):
        payment=db.session.scalar(db.select(Payment).where(Payment.public_id==public_id,Payment.provider=='dpo'))
        if not payment:abort(404)
        # Ignore all caller-provided status, amount, reference and token query parameters.
        try:pay.verify_payment(payment);db.session.commit()
        except pay.ProviderError:db.session.rollback()
        return redirect(url_for('shipment_detail',public_id=payment.shipment.public_id))

    @app.post('/webhooks/mpesa/<public_id>/<secret>')
    @csrf.exempt
    @limiter.limit('120 per minute')
    def mpesa_callback(public_id,secret):
        payment=db.session.scalar(db.select(Payment).where(Payment.public_id==public_id,Payment.provider=='mpesa'))
        if not payment or not hmac.compare_digest(payment.callback_hash or '',hashlib.sha256(secret.encode()).hexdigest()):abort(404)
        data=request.get_json(silent=True) or {}
        if not isinstance(data,dict) or not isinstance(data.get('Body'),dict):abort(400)
        callback=data['Body'].get('stkCallback',{})
        if not isinstance(callback,dict):abort(400)
        if not payment.provider_ref or callback.get('CheckoutRequestID')!=payment.provider_ref:return jsonify(error='Unknown checkout request'),409
        dedupe=hashlib.sha256((payment.public_id+json.dumps(callback,sort_keys=True)).encode()).hexdigest()
        if db.session.scalar(db.select(WebhookReceipt).where(WebhookReceipt.dedupe==dedupe)):return jsonify(ResultCode=0,ResultDesc='Accepted')
        receipt=None
        if str(callback.get('ResultCode'))=='0':
            meta={item.get('Name'):item.get('Value') for item in callback.get('CallbackMetadata',{}).get('Item',[])}
            try:
                if Decimal(str(meta.get('Amount')))!=Decimal(payment.amount_minor)/100 or str(meta.get('PhoneNumber'))!=payment.phone:return jsonify(error='Payment metadata mismatch'),400
                receipt=str(meta['MpesaReceiptNumber'])
                if not re.fullmatch(r'[A-Z0-9]{8,30}',receipt):raise ValueError()
            except Exception:return jsonify(error='Invalid payment metadata'),400
        db.session.add(WebhookReceipt(dedupe=dedupe,payment_id=payment.id,provider='mpesa',result_code=str(callback.get('ResultCode',''))));db.session.commit()
        try:
            pay.verify_payment(payment)
            if payment.status=='paid' and receipt:
                other=db.session.scalar(db.select(Payment).where(Payment.receipt==receipt,Payment.id!=payment.id))
                if not other:payment.receipt=receipt
            db.session.commit()
        except (pay.ProviderError,IntegrityError):db.session.rollback()
        return jsonify(ResultCode=0,ResultDesc='Accepted')

    @app.post('/webhooks/dpo/<secret>')
    @csrf.exempt
    @limiter.limit('120 per minute')
    def dpo_callback(secret):
        expected=app.config['DPO_CALLBACK_SECRET']
        if not expected or not hmac.compare_digest(expected,secret):abort(404)
        try:data={n.tag:(n.text or '').strip() for n in fromstring(request.data).iter()}
        except Exception:abort(400)
        if not data.get('TransactionToken'):abort(400)
        payment=db.session.scalar(db.select(Payment).where(Payment.provider=='dpo',Payment.provider_ref==data.get('TransactionToken')))
        if not payment:abort(404)
        dedupe=hashlib.sha256(request.data).hexdigest()
        if not db.session.scalar(db.select(WebhookReceipt).where(WebhookReceipt.dedupe==dedupe)):
            db.session.add(WebhookReceipt(dedupe=dedupe,payment_id=payment.id,provider='dpo',result_code=data.get('Result')));db.session.commit()
        try:pay.verify_payment(payment);db.session.commit()
        except pay.ProviderError:db.session.rollback()
        return app.response_class('<API3G><Response>OK</Response></API3G>',mimetype='application/xml')

    register_cli(app)
    return app


def register_cli(app):
    @app.cli.command('init-db')
    def init_db():
        """Apply schema migrations only. No business records are inserted."""
        upgrade();click.echo('Schema is current. No accounts, shipments or prices were seeded.')

    @app.cli.command('create-admin')
    @click.option('--email',prompt=True)
    @click.option('--name',prompt=True)
    @click.option('--phone',prompt=True)
    @click.password_option(confirmation_prompt=True)
    def create_admin(email,name,phone,password):
        """Create an administrator and enroll a TOTP authenticator."""
        from auth import email_address,password_value
        from cryptography.fernet import Fernet
        import pyotp
        email=email_address(email);password_value(password)
        if db.session.scalar(db.select(User).where(User.email==email)):raise click.ClickException('This email already exists. Use the existing administrator or manage the account explicitly.')
        if not app.config['DATA_ENCRYPTION_KEY']:raise click.ClickException('Set DATA_ENCRYPTION_KEY before creating an administrator.')
        secret=pyotp.random_base32();totp=pyotp.TOTP(secret)
        click.echo('Add this provisioning URI to your authenticator (keep it secret):');click.echo(totp.provisioning_uri(email,issuer_name='FabsExpress'))
        if not totp.verify(click.prompt('Current authenticator code'),valid_window=1):raise click.ClickException('Authenticator verification failed. No account created.')
        user=User(email=email,name=name,phone=phone,role='admin',email_verified=True,mfa_secret=Fernet(app.config['DATA_ENCRYPTION_KEY'].encode()).encrypt(secret.encode()).decode());user.set_password(password);db.session.add(user);db.session.flush();audit('admin.created',user.id);db.session.commit();click.echo('Administrator created.')

    @app.cli.command('check-config')
    def check_config():
        """Report readiness without revealing credentials."""
        for name,state in [('M-Pesa',pay.ready('mpesa')),('DPO',pay.ready('dpo')),('SMTP',bool(app.config['SMTP_HOST'] and app.config['SMTP_FROM'])),('Map tiles',bool(app.config['MAP_TILE_URL'] and app.config['MAP_ORIGIN'])),('Approved tariffs',bool(db.session.scalar(db.select(Tariff.id).where(Tariff.active.is_(True)).limit(1))))]:click.echo(f'{name}: '+('configured' if state else 'not configured'))
        click.echo('Configuration checks do not certify provider activation or end-to-end processing.')

    @app.cli.command('reconcile-payments')
    def reconcile_payments():
        from worker import reconcile
        click.echo(f'Checked {reconcile()} pending payments.')

    @app.cli.command('send-mail')
    def send_mail():
        from worker import send_outbox
        click.echo(f'Sent {send_outbox()} queued messages.')

    @app.cli.command('prune-gps')
    def prune_gps():
        cutoff=now()-timedelta(days=app.config['GPS_RETENTION_DAYS']);count=db.session.execute(db.delete(GPSPing).where(GPSPing.received_at<cutoff)).rowcount;db.session.commit();click.echo(f'Removed {count} old GPS readings.')

if __name__=='__main__':
    create_app().run(host='0.0.0.0',port=int(os.getenv('PORT','5000')),debug=False)
