from datetime import date,timedelta
from decimal import Decimal
import hashlib,json,re,secrets
import pytest
from models import db,User,Tariff,Shipment,Payment,ShipmentEvent,GPSPing,MailOutbox,now
from services.shipping import calculate,find_quote
from services import payments as pay
from conftest import login

def test_fresh_install_has_no_business_records(app,client):
    for model in (User,Tariff,Shipment,Payment,GPSPing):assert db.session.scalar(db.select(db.func.count()).select_from(model))==0
    assert b'No shipments' not in client.get('/').data  # public homepage remains useful with an empty DB
    assert b'Request a route-specific quote' in client.get('/rates').data

@pytest.mark.parametrize('path',['/','/services','/rates','/track','/shipping-guide','/privacy','/support','/login','/register','/driver','/forgot-password','/rate-sheet.csv'])
def test_public_pages_render(client,path):assert client.get(path).status_code==200

def test_customer_registration_verification_and_booking(app,client):
    r=client.post('/register',data={'name':'New Customer','email':'new@example.com','phone':'254712345678','password':'Long-Secure-Password-123','terms':'yes'})
    assert r.status_code==302
    user=db.session.scalar(db.select(User).where(User.email=='new@example.com'));assert user and not user.email_verified
    client.post('/login',data={'email':user.email,'password':'Long-Secure-Password-123'})
    assert client.get('/ship').status_code==302
    from auth import token_for
    client.get('/verify/'+token_for(user,'verify'));db.session.refresh(user);assert user.email_verified
    page=client.get('/ship');key=re.search(rb'name="booking_key" value="([^"]+)"',page.data).group(1).decode()
    payload={'booking_key':key,'origin_name':'Sender','origin_phone':'254712345678','origin_address':'Private collection address','origin_city':'Nairobi','origin_country':'KE','origin_postal':'00100','destination_name':'Recipient','destination_phone':'256712345678','destination_address':'Private destination address','destination_city':'Kampala','destination_country':'UG','destination_postal':'','description':'Printed paper documents','declared_value':'1000','service':'express','pickup_date':(date.today()+timedelta(days=1)).isoformat(),'pickup_window':'09:00–13:00','weight':'1','length':'20','width':'10','height':'5','restricted':'yes','terms':'yes'}
    result=client.post('/ship',data=payload);assert result.status_code==302
    saved=db.session.scalar(db.select(Shipment));assert saved.status=='quote_requested';assert saved.amount_minor is None
    again=client.post('/ship',data=payload);assert again.location==result.location;assert db.session.scalar(db.select(db.func.count()).select_from(Shipment))==1

def test_no_unapproved_tariff_quotes(app,tariff):
    p=[{'weight':Decimal('1'),'length':Decimal('20'),'width':Decimal('10'),'height':Decimal('5')}]
    tariff.active=False;db.session.commit();assert find_quote('KE','UG','express',p,date.today()) is None
    tariff.active=True;tariff.valid_to=date.today()-timedelta(days=1);db.session.commit();assert find_quote('KE','UG','express',p,date.today()) is None

def test_dimensional_weight_per_package_and_minor_unit_rounding(tariff):
    packages=[{'weight':Decimal('4'),'length':Decimal('10'),'width':Decimal('10'),'height':Decimal('10')},{'weight':Decimal('1'),'length':Decimal('50'),'width':Decimal('20'),'height':Decimal('20')}]
    quote=calculate(tariff,packages)
    assert Decimal(quote['billable_kg'])==Decimal('8')  # not max(total actual, total volumetric)
    assert quote['transport_minor']==24000
    assert quote['fuel_minor']==2400 and quote['tax_minor']==4304
    assert quote['total_minor']==31204

def test_customer_cannot_view_another_shipment(client,users,shipment):
    login(client,users[1]);assert client.get('/shipments/'+shipment.public_id).status_code==404
    assert client.get('/shipments/'+shipment.public_id+'/invoice.pdf').status_code==404
    assert client.get('/admin/').status_code==403

def test_public_tracking_redacts_private_information(client,shipment):
    result=client.get('/track?tracking='+shipment.tracking)
    for private in [b'Private Sender',b'Private Recipient',b'Private origin address',b'254712345678']:assert private not in result.data
    data=client.get('/api/tracking/'+shipment.tracking).json;assert 'positions' not in data and 'user_id' not in data

def test_admin_pages_render(client,users,shipment,tariff):
    login(client,users[2])
    for path in ['/admin/','/admin/tariffs','/admin/payments','/admin/customers','/admin/support','/admin/audit','/admin/settings','/admin/shipments/'+shipment.public_id]:assert client.get(path).status_code==200,path

def test_dispatch_requires_paid_and_delivery_proof(client,users,shipment):
    login(client,users[2]);shipment.status='booked';db.session.commit()
    r=client.post('/admin/shipments/'+shipment.public_id,data={'action':'event','status':'in_transit','location':'Nairobi warehouse','note':'Departed warehouse.'})
    db.session.refresh(shipment);assert shipment.status=='booked' and b'verified payment is required' in r.data
    shipment.payment_status='paid';db.session.commit()
    client.post('/admin/shipments/'+shipment.public_id,data={'action':'event','status':'in_transit','location':'Nairobi warehouse','note':'Departed warehouse.'});db.session.refresh(shipment);assert shipment.status=='in_transit'
    client.post('/admin/shipments/'+shipment.public_id,data={'action':'event','status':'delivered','location':'Kampala','note':'Delivered.'});db.session.refresh(shipment);assert shipment.status=='in_transit'
    shipment.status='out_for_delivery';db.session.commit()
    client.post('/admin/shipments/'+shipment.public_id,data={'action':'event','status':'delivered','location':'Kampala','note':'Delivered.'});db.session.refresh(shipment);assert shipment.status=='out_for_delivery'
    client.post('/admin/shipments/'+shipment.public_id,data={'action':'event','status':'delivered','location':'Kampala','note':'Delivered.','delivery_recipient':'Named recipient'});db.session.refresh(shipment);assert shipment.status=='delivered'

def payment_for(shipment,provider='dpo',**kwargs):
    p=Payment(shipment_id=shipment.id,provider=provider,amount_minor=shipment.amount_minor,currency=shipment.currency,status='pending',provider_ref=secrets.token_hex(10),**kwargs);db.session.add(p);db.session.commit();return p

def test_dpo_paid_verification_is_idempotent(app,shipment,monkeypatch):
    p=payment_for(shipment)
    monkeypatch.setattr(pay,'dpo_call',lambda *a,**k:{'Result':'000','TransactionAmount':'123.00','TransactionCurrency':'KES','TransactionRef':'UNIQUE-RECEIPT'})
    pay.verify_payment(p);db.session.commit();pay.verify_payment(p);db.session.commit()
    assert p.status=='paid' and shipment.payment_status=='paid' and shipment.status=='booked'
    assert db.session.scalar(db.select(db.func.count()).select_from(ShipmentEvent).where(ShipmentEvent.status=='booked'))==1

@pytest.mark.parametrize('result,amount,currency,expected',[('001','123.00','KES','review'),('000','120.00','KES','review'),('000','123.00','USD','review'),('000','NaN','KES','review'),('900','123.00','KES','pending')])
def test_dpo_nonsettled_or_mismatched_payment_does_not_credit(shipment,monkeypatch,result,amount,currency,expected):
    p=payment_for(shipment);monkeypatch.setattr(pay,'dpo_call',lambda *a,**k:{'Result':result,'TransactionAmount':amount,'TransactionCurrency':currency});pay.verify_payment(p);db.session.commit();assert p.status==expected and shipment.payment_status=='unpaid'

def test_checkout_duplicate_request_does_not_charge_twice(client,users,shipment,monkeypatch):
    calls=[]
    def start(p):calls.append(p.id);p.provider_ref='one-token';p.status='pending';return 'https://secure.3gdirectpay.com/payv2.php?ID=one-token'
    monkeypatch.setattr(pay,'start_dpo',start);login(client,users[0]);data={'provider':'dpo','quote_version':'1','accept_quote':'yes'}
    assert client.post('/shipments/'+shipment.public_id+'/pay',data=data).status_code==303
    client.post('/shipments/'+shipment.public_id+'/pay',data=data)
    assert len(calls)==1 and db.session.scalar(db.select(db.func.count()).select_from(Payment))==1

def test_expired_quote_and_tampered_version_block_checkout(client,users,shipment,monkeypatch):
    monkeypatch.setattr(pay,'start_dpo',lambda p:pytest.fail('Provider must not be called'))
    login(client,users[0]);client.post('/shipments/'+shipment.public_id+'/pay',data={'provider':'dpo','quote_version':'9','accept_quote':'yes'});assert not shipment.payments
    shipment.quote_expires_at=now()-timedelta(seconds=1);db.session.commit();client.post('/shipments/'+shipment.public_id+'/pay',data={'provider':'dpo','quote_version':'1','accept_quote':'yes'});assert not shipment.payments

def test_admin_cannot_change_quote_with_pending_payment(client,users,shipment):
    payment_for(shipment);login(client,users[2]);client.post('/admin/shipments/'+shipment.public_id,data={'action':'quote','transport_minor':'999','fuel_minor':'0','handling_minor':'0','tax_minor':'0','currency':'KES','transit':'2 days','quote_notes':'Customs duties are excluded.'});db.session.refresh(shipment);assert shipment.amount_minor==12300

def test_callback_secret_and_provider_verification(client,shipment,monkeypatch):
    token=secrets.token_urlsafe(32);p=payment_for(shipment,'mpesa',phone='254712345678',callback_hash=hashlib.sha256(token.encode()).hexdigest())
    payload={'Body':{'stkCallback':{'CheckoutRequestID':p.provider_ref,'ResultCode':0,'CallbackMetadata':{'Item':[{'Name':'Amount','Value':123},{'Name':'MpesaReceiptNumber','Value':'TXRECEIPT123'},{'Name':'PhoneNumber','Value':254712345678}]}}}}
    assert client.post('/webhooks/mpesa/'+p.public_id+'/wrong',json=payload).status_code==404
    def unavailable(payment):raise pay.ProviderUnavailable('offline')
    monkeypatch.setattr(pay,'verify_payment',unavailable)
    assert client.post('/webhooks/mpesa/'+p.public_id+'/'+token,json=payload).status_code==200
    db.session.refresh(p);assert p.status=='pending' and shipment.payment_status=='unpaid'

def test_dpo_return_ignores_browser_paid_claim(client,shipment,monkeypatch):
    p=payment_for(shipment);monkeypatch.setattr(pay,'dpo_call',lambda *a,**kw:{'Result':'900'})
    client.get('/payments/dpo/return/'+p.public_id+'?Result=000&TransactionAmount=123&TransactionToken=evil');db.session.refresh(p);assert p.status=='pending'

def test_driver_authorization_scope_freshness_and_completion(client,users,shipment):
    token=secrets.token_urlsafe(32);shipment.driver_token_hash=hashlib.sha256(token.encode()).hexdigest();shipment.driver_token_expires=now()+timedelta(days=1);shipment.status='in_transit';shipment.payment_status='paid';db.session.commit();headers={'Authorization':'Bearer '+token}
    payload={'latitude':-1.286389,'longitude':36.817223,'accuracy':8,'timestamp':now().isoformat()}
    assert client.post('/api/driver/location',json=payload).status_code==403
    assert client.post('/api/driver/location',json={**payload,'latitude':999},headers=headers).status_code==400
    assert client.post('/api/driver/location',json={**payload,'timestamp':(now()-timedelta(hours=1)).isoformat()},headers=headers).status_code==400
    assert client.post('/api/driver/location',json=payload,headers=headers).status_code==200
    assert client.post('/api/driver/location',json=payload,headers=headers).json['status']=='ignored'
    assert 'positions' not in client.get('/api/tracking/'+shipment.tracking).json
    login(client,users[0]);assert len(client.get('/api/tracking/'+shipment.tracking).json['positions'])==1
    shipment.status='delivered';db.session.commit();assert client.post('/api/driver/location',json={**payload,'timestamp':now().isoformat()},headers=headers).status_code==403

def test_csrf_is_required_for_mutating_forms(app,client):
    app.config['WTF_CSRF_ENABLED']=True
    assert client.post('/support',data={'name':'A name'}).status_code==400
    r=client.get('/support');token=re.search(rb'name="csrf_token" value="([^"]+)"',r.data).group(1).decode()
    assert client.post('/support',data={'csrf_token':token,'name':'A name','email':'help@example.com','subject':'Shipment enquiry','message':'Please help with this shipment.'}).status_code==302

def test_output_escapes_customer_content(client,users,shipment):
    shipment.description='<script>alert(1)</script>';db.session.commit();login(client,users[0]);r=client.get('/shipments/'+shipment.public_id)
    assert b'<script>alert(1)</script>' not in r.data and b'&lt;script&gt;' in r.data

def test_pdf_access_and_output(client,users,shipment):
    login(client,users[0]);assert client.get('/shipments/'+shipment.public_id+'/label.pdf').status_code==403
    assert client.get('/shipments/'+shipment.public_id+'/invoice.pdf').data.startswith(b'%PDF')
    shipment.payment_status='paid';db.session.commit();assert client.get('/shipments/'+shipment.public_id+'/label.pdf').data.startswith(b'%PDF')

def test_security_headers(client):
    r=client.get('/');assert "frame-ancestors 'none'" in r.headers['Content-Security-Policy'];assert 'unsafe-eval' not in r.headers['Content-Security-Policy'];assert r.headers['X-Content-Type-Options']=='nosniff';assert r.headers['Cache-Control']=='no-store'

def test_disabled_account_session_is_revoked(client,users):
    login(client,users[0]);assert client.get('/dashboard').status_code==200
    users[0].enabled=False;db.session.commit();assert client.get('/dashboard').status_code==302

def test_city_specific_tariff_never_prices_another_city(tariff):
    tariff.origin_city='Nairobi';tariff.destination_city='Kampala';db.session.commit()
    package=[{'weight':Decimal('1'),'length':Decimal('20'),'width':Decimal('10'),'height':Decimal('5')}]
    assert find_quote('KE','UG','express',package,date.today(),'Nairobi','Entebbe') is None
    assert find_quote('KE','UG','express',package,date.today(),'nairobi','kampala')['tariff_id']==tariff.id

def test_late_negative_verification_cannot_overwrite_paid(shipment,monkeypatch):
    p=payment_for(shipment)
    def other_worker_settles(*args,**kwargs):
        db.session.execute(db.update(Payment).where(Payment.id==p.id).values(status='paid'));db.session.execute(db.update(Shipment).where(Shipment.id==shipment.id).values(payment_status='paid',status='booked'));db.session.commit()
        return {'Result':'904'}
    monkeypatch.setattr(pay,'dpo_call',other_worker_settles);pay.verify_payment(p);db.session.commit();db.session.refresh(p);assert p.status=='paid'

def test_browser_precision_gps_reading_is_accepted(client,shipment):
    token=secrets.token_urlsafe(32);shipment.driver_token_hash=hashlib.sha256(token.encode()).hexdigest();shipment.driver_token_expires=now()+timedelta(days=1);shipment.status='in_transit';db.session.commit()
    r=client.post('/api/driver/location',headers={'Authorization':'Bearer '+token},json={'latitude':-1.286389123456789,'longitude':36.81722312345678,'accuracy':12.12345678912345,'timestamp':now().isoformat()});assert r.status_code==200

def test_dpo_acknowledgement_only_follows_settlement(shipment,monkeypatch):
    p=payment_for(shipment);calls=[]
    def call(operation,fields):calls.append(fields);return {'Result':'000'}
    monkeypatch.setattr(pay,'dpo_call',call)
    assert pay.acknowledge_dpo(p) is False and not calls
    p.status='paid';p.verified_at=now();db.session.commit()
    assert pay.acknowledge_dpo(p) is True;db.session.commit()
    assert calls[0]['VerifyTransaction']=='1' and p.provider_acknowledged_at
    assert pay.acknowledge_dpo(p) is False and len(calls)==1

def test_production_startup_rejects_incomplete_configuration():
    from app import create_app
    with pytest.raises(RuntimeError,match='Production configuration missing'):
        create_app({'APP_ENV':'production','TESTING':False,'SECRET_KEY':'long-enough-but-not-a-real-production-secret','SQLALCHEMY_DATABASE_URI':'sqlite://','RATELIMIT_STORAGE_URI':'memory://','DATA_ENCRYPTION_KEY':'','SMTP_HOST':'','SMTP_FROM':''})
