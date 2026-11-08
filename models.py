"""Persistent business records. No seed customers, shipments, prices or payments."""
import secrets
from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import MetaData

NAMING = {"ix": "ix_%(column_0_label)s", "uq": "uq_%(table_name)s_%(column_0_name)s", "ck": "ck_%(table_name)s_%(constraint_name)s", "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s", "pk": "pk_%(table_name)s"}
db = SQLAlchemy(metadata=MetaData(naming_convention=NAMING))
def now(): return datetime.now(timezone.utc)
def uid(): return secrets.token_hex(16)

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(254), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(24), nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(16), default="customer", nullable=False)
    enabled = db.Column(db.Boolean, default=True, nullable=False)
    email_verified = db.Column(db.Boolean, default=False, nullable=False)
    token_version = db.Column(db.Integer, default=0, nullable=False)
    mfa_secret = db.Column(db.Text)  # Fernet-encrypted; decryption key stays in environment.
    mfa_counter = db.Column(db.BigInteger, default=-1, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=now, nullable=False)
    @property
    def is_active(self): return self.enabled
    def set_password(self, password): self.password_hash = generate_password_hash(password, method="scrypt")
    def check_password(self, password): return check_password_hash(self.password_hash, password)

class Tariff(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    origin = db.Column(db.String(2), nullable=False, index=True)
    destination = db.Column(db.String(2), nullable=False, index=True)
    origin_city = db.Column(db.String(80), default='', server_default='', nullable=False)
    destination_city = db.Column(db.String(80), default='', server_default='', nullable=False)
    zone = db.Column(db.String(40), nullable=False)
    service = db.Column(db.String(20), nullable=False)
    currency = db.Column(db.String(3), default="KES", nullable=False)
    max_kg = db.Column(db.Numeric(10, 3), nullable=False)
    base_minor = db.Column(db.BigInteger, nullable=False)
    included_kg = db.Column(db.Numeric(10, 3), nullable=False)
    step_kg = db.Column(db.Numeric(10, 3), nullable=False)
    step_minor = db.Column(db.BigInteger, nullable=False)
    divisor = db.Column(db.Integer, nullable=False)
    fuel_bps = db.Column(db.Integer, default=0, nullable=False)
    tax_bps = db.Column(db.Integer, default=0, nullable=False)
    handling_minor = db.Column(db.BigInteger, default=0, nullable=False)
    min_days = db.Column(db.Integer, nullable=False)
    max_days = db.Column(db.Integer, nullable=False)
    valid_from = db.Column(db.Date, nullable=False)
    valid_to = db.Column(db.Date, nullable=False)
    active = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=now, nullable=False)
    __table_args__ = (db.CheckConstraint('base_minor >= 0 AND step_minor >= 0 AND handling_minor >= 0',name='nonnegative_charges'), db.CheckConstraint('step_kg > 0 AND divisor > 0 AND max_kg > 0',name='positive_weights'))

class Shipment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), default=uid, unique=True, nullable=False)
    tracking = db.Column(db.String(28), default=lambda:"FX"+secrets.token_hex(10).upper(), unique=True, nullable=False, index=True)
    booking_key = db.Column(db.String(64), nullable=False, unique=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    user = db.relationship(User)
    origin = db.Column(db.JSON, nullable=False)
    destination = db.Column(db.JSON, nullable=False)
    service = db.Column(db.String(20), nullable=False)
    description = db.Column(db.String(500), nullable=False)
    hs_code = db.Column(db.String(10))
    country_of_manufacture = db.Column(db.String(2))
    declared_minor = db.Column(db.BigInteger, nullable=False)
    currency = db.Column(db.String(3), default="KES", nullable=False)
    pickup_date = db.Column(db.Date, nullable=False)
    pickup_window = db.Column(db.String(30), nullable=False)
    status = db.Column(db.String(24), default="quote_requested", nullable=False, index=True)
    payment_status = db.Column(db.String(24), default="unpaid", nullable=False)
    amount_minor = db.Column(db.BigInteger)
    quote_details = db.Column(db.JSON)
    quote_version = db.Column(db.Integer, default=1, nullable=False)
    quote_expires_at = db.Column(db.DateTime(timezone=True))
    accepted_at = db.Column(db.DateTime(timezone=True))
    driver_token_hash = db.Column(db.String(64), unique=True)
    driver_token_expires = db.Column(db.DateTime(timezone=True))
    carrier_name = db.Column(db.String(80))
    carrier_tracking = db.Column(db.String(80))
    delivery_recipient = db.Column(db.String(100))
    created_at = db.Column(db.DateTime(timezone=True), default=now, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=now, onupdate=now, nullable=False)
    packages = db.relationship('Package', cascade='all, delete-orphan', backref='shipment', lazy='selectin')
    events = db.relationship('ShipmentEvent', cascade='all, delete-orphan', backref='shipment', order_by='ShipmentEvent.created_at', lazy='selectin')
    __table_args__ = (db.CheckConstraint('amount_minor IS NULL OR amount_minor > 0', name='positive_amount'),)

class Package(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    shipment_id = db.Column(db.Integer, db.ForeignKey('shipment.id'), nullable=False, index=True)
    weight = db.Column(db.Numeric(10,3), nullable=False)
    length = db.Column(db.Numeric(10,2), nullable=False)
    width = db.Column(db.Numeric(10,2), nullable=False)
    height = db.Column(db.Numeric(10,2), nullable=False)

class ShipmentEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    shipment_id = db.Column(db.Integer, db.ForeignKey('shipment.id'), nullable=False, index=True)
    status = db.Column(db.String(24), nullable=False)
    location = db.Column(db.String(160), nullable=False)
    note = db.Column(db.String(500), nullable=False)
    actor_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime(timezone=True), default=now, nullable=False)

class GPSPing(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    shipment_id = db.Column(db.Integer, db.ForeignKey('shipment.id'), nullable=False, index=True)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    accuracy = db.Column(db.Float, nullable=False)
    recorded_at = db.Column(db.DateTime(timezone=True), nullable=False)
    received_at = db.Column(db.DateTime(timezone=True), default=now, nullable=False)

class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), default=uid, unique=True, nullable=False)
    shipment_id = db.Column(db.Integer, db.ForeignKey('shipment.id'), nullable=False, index=True)
    shipment = db.relationship(Shipment, backref='payments')
    provider = db.Column(db.String(12), nullable=False)
    amount_minor = db.Column(db.BigInteger, nullable=False)
    currency = db.Column(db.String(3), nullable=False)
    status = db.Column(db.String(24), default="created", nullable=False, index=True)
    provider_ref = db.Column(db.String(200), unique=True)
    receipt = db.Column(db.String(100), unique=True)
    phone = db.Column(db.String(24))
    callback_hash = db.Column(db.String(64))
    error = db.Column(db.String(300))
    verified_at = db.Column(db.DateTime(timezone=True))
    provider_acknowledged_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), default=now, nullable=False)

class WebhookReceipt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    dedupe = db.Column(db.String(64), unique=True, nullable=False)
    payment_id = db.Column(db.Integer, db.ForeignKey('payment.id'), nullable=False)
    provider = db.Column(db.String(12), nullable=False)
    result_code = db.Column(db.String(30))
    received_at = db.Column(db.DateTime(timezone=True), default=now, nullable=False)

class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    action = db.Column(db.String(80), nullable=False)
    object_id = db.Column(db.String(80), nullable=False)
    detail = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime(timezone=True), default=now, nullable=False)

class SupportRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(16), default=lambda:'FXS-'+secrets.token_hex(5).upper(), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(254), nullable=False)
    subject = db.Column(db.String(160), nullable=False)
    message = db.Column(db.Text, nullable=False)
    resolved = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=now, nullable=False)

class MailOutbox(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    recipient = db.Column(db.String(254), nullable=False)
    subject = db.Column(db.String(160), nullable=False)
    body = db.Column(db.Text, nullable=False)
    attempts = db.Column(db.Integer, default=0, nullable=False)
    sent_at = db.Column(db.DateTime(timezone=True))
    next_attempt_at = db.Column(db.DateTime(timezone=True), default=now, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=now, nullable=False)
