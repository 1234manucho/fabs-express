"""Isolated automated checks only. These fixtures never seed the application."""
import os,sys
from pathlib import Path
from datetime import date,timedelta
from decimal import Decimal
import pytest
os.environ.setdefault('APP_ENV','development')
os.environ.setdefault('SECRET_KEY','isolated-test-secret-never-use-in-production')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import create_app
from models import db,User,Tariff,Shipment,Package,now
from services.shipping import add_event

@pytest.fixture
def app():
    app=create_app({'TESTING':True,'APP_ENV':'development','SECRET_KEY':'isolated-tests-not-a-production-secret','SQLALCHEMY_DATABASE_URI':'sqlite://','WTF_CSRF_ENABLED':False,'RATELIMIT_ENABLED':False,'SESSION_COOKIE_SECURE':False,'EMAIL_VERIFICATION_REQUIRED':True,'PUBLIC_BASE_URL':'https://shipping.example.invalid','DPO_COMPANY_TOKEN':'test-not-live','DPO_SERVICE_TYPE':'approved-test-type','DPO_CALLBACK_SECRET':'test-callback-not-live','MPESA_CONSUMER_KEY':'test-not-live','MPESA_CONSUMER_SECRET':'test-not-live','MPESA_SHORTCODE':'test','MPESA_PASSKEY':'test'})
    with app.app_context():
        db.create_all();yield app;db.session.remove();db.drop_all()
@pytest.fixture
def client(app):return app.test_client()
@pytest.fixture
def users(app):
    result=[]
    for name,role in [('Owner','customer'),('Other','customer'),('Operator','admin')]:
        user=User(name=name,email=name.lower()+'@example.invalid',phone='254712345678',role=role,email_verified=True);user.set_password('Correct-Passphrase-123');db.session.add(user);result.append(user)
    db.session.commit();return result

def login(client,user):
    return client.post('/login',data={'email':user.email,'password':'Correct-Passphrase-123'})

@pytest.fixture
def tariff(app):
    rate=Tariff(name='Isolated test tariff',origin='KE',destination='UG',zone='Test',service='express',currency='KES',max_kg=100,base_minor=10000,included_kg=1,step_kg=Decimal('0.5'),step_minor=1000,divisor=5000,fuel_bps=1000,tax_bps=1600,handling_minor=500,min_days=2,max_days=4,valid_from=date.today()-timedelta(days=1),valid_to=date.today()+timedelta(days=20),active=True);db.session.add(rate);db.session.commit();return rate
@pytest.fixture
def shipment(app,users):
    origin={'name':'Private Sender','phone':'254712345678','address':'Private origin address','city':'Nairobi','country':'KE','postal':'00100'}
    destination={'name':'Private Recipient','phone':'256712345678','address':'Private destination address','city':'Kampala','country':'UG','postal':''}
    s=Shipment(user_id=users[0].id,booking_key='test-booking-key',origin=origin,destination=destination,service='express',description='Sealed paper documents',declared_minor=50000,currency='KES',pickup_date=date.today()+timedelta(days=1),pickup_window='09:00–13:00',amount_minor=12300,quote_details={'transport_minor':10000,'fuel_minor':1000,'handling_minor':500,'tax_minor':800,'total_minor':12300,'transit':'2–4 days','duties':'Excluded'},quote_expires_at=now()+timedelta(hours=48));s.packages.append(Package(weight=1,length=20,width=10,height=5));db.session.add(s);db.session.flush();add_event(s,'awaiting_payment','Nairobi','Request received.');db.session.commit();return s
