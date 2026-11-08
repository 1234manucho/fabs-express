import os
from dotenv import load_dotenv
load_dotenv()
def flag(name, default=False): return os.getenv(name,str(default)).lower() in ('1','true','yes')
class Config:
    APP_ENV = os.getenv('APP_ENV','production')
    SECRET_KEY = os.getenv('SECRET_KEY','')
    DATA_ENCRYPTION_KEY = os.getenv('DATA_ENCRYPTION_KEY','')
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL','sqlite:///fabsexpress.db').replace('postgres://','postgresql+psycopg://',1).replace('postgresql://','postgresql+psycopg://',1)
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {'pool_pre_ping':True}
    RATELIMIT_STORAGE_URI = os.getenv('REDIS_URL','memory://')
    PUBLIC_BASE_URL = os.getenv('PUBLIC_BASE_URL','http://localhost:5000').rstrip('/')
    TRUST_PROXY = flag('TRUST_PROXY')
    SESSION_COOKIE_SECURE = APP_ENV == 'production'
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    REMEMBER_COOKIE_SECURE = APP_ENV == 'production'
    PERMANENT_SESSION_LIFETIME = 3600
    MAX_CONTENT_LENGTH = 1024 * 1024
    WTF_CSRF_TIME_LIMIT = 3600
    EMAIL_VERIFICATION_REQUIRED = True
    # Email delivery mode: 'queue' (default), 'direct' (send inside the request,
    # fall back to queued retry on failure), or 'async' (currently same as queue).
    MAIL_MODE = os.getenv('MAIL_MODE','queue')
    SMTP_HOST = os.getenv('SMTP_HOST','')
    SMTP_PORT = int(os.getenv('SMTP_PORT','587'))
    SMTP_USERNAME = os.getenv('SMTP_USERNAME','')
    SMTP_PASSWORD = os.getenv('SMTP_PASSWORD','')
    SMTP_FROM = os.getenv('SMTP_FROM','')
    SMTP_TLS = flag('SMTP_TLS',True)
    SMTP_SSL = flag('SMTP_SSL',False)
    SUPPORT_EMAIL = os.getenv('SUPPORT_EMAIL','')
    SUPPORT_PHONE = os.getenv('SUPPORT_PHONE','')
    BUSINESS_ADDRESS = os.getenv('BUSINESS_ADDRESS','')
    LEGAL_NAME = os.getenv('LEGAL_NAME','FabsExpress')
    TAX_PIN = os.getenv('TAX_PIN','')
    MPESA_CONSUMER_KEY = os.getenv('MPESA_CONSUMER_KEY','')
    MPESA_CONSUMER_SECRET = os.getenv('MPESA_CONSUMER_SECRET','')
    MPESA_SHORTCODE = os.getenv('MPESA_SHORTCODE','')
    MPESA_PASSKEY = os.getenv('MPESA_PASSKEY','')
    MPESA_TRANSACTION_TYPE = os.getenv('MPESA_TRANSACTION_TYPE','CustomerPayBillOnline')
    DPO_COMPANY_TOKEN = os.getenv('DPO_COMPANY_TOKEN','')
    DPO_SERVICE_TYPE = os.getenv('DPO_SERVICE_TYPE','')
    DPO_CALLBACK_SECRET = os.getenv('DPO_CALLBACK_SECRET','')
    MAP_TILE_URL = os.getenv('MAP_TILE_URL','')
    MAP_ATTRIBUTION = os.getenv('MAP_ATTRIBUTION','')
    MAP_ORIGIN = os.getenv('MAP_ORIGIN','')
    GPS_STALE_SECONDS = 120
    GPS_RETENTION_DAYS = int(os.getenv('GPS_RETENTION_DAYS','30'))