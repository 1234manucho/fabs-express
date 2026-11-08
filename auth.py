import hashlib, secrets
from functools import wraps
from urllib.parse import urlsplit
from flask import Blueprint, current_app, request, render_template, redirect, url_for, flash, session, abort
from flask_login import login_user, logout_user, current_user, login_required
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from email_validator import validate_email, EmailNotValidError
from cryptography.fernet import Fernet
import pyotp
from models import db, User, MailOutbox
from services.shipping import text_field, audit

auth = Blueprint('auth',__name__)
def email_address(value):
    try:return validate_email(value.strip(),check_deliverability=False).normalized.lower()
    except (EmailNotValidError,AttributeError):raise ValueError('Enter a valid email address.')
def password_value(value):
    if not 12<=len(value)<=128:raise ValueError('Use a password of 12–128 characters.')
    return value

def serializer():return URLSafeTimedSerializer(current_app.config['SECRET_KEY'],salt='fabsexpress-auth-v1')
def token_for(user,purpose):return serializer().dumps({'id':user.id,'purpose':purpose,'version':user.token_version})
def read_token(value,purpose,max_age):
    try:
        data=serializer().loads(value,max_age=max_age)
        user=db.session.get(User,data['id'])
        if not user or not user.enabled or data['purpose']!=purpose or data['version']!=user.token_version:raise ValueError()
        return user
    except (BadSignature,SignatureExpired,KeyError,ValueError):raise ValueError('This link is invalid or has expired. Request a new one.')
def send_link(user,purpose):
    from worker import queue_or_send
    route='auth.verify_email' if purpose=='verify' else 'auth.reset_password'
    link=current_app.config['PUBLIC_BASE_URL']+url_for(route,token=token_for(user,purpose))
    queue_or_send(MailOutbox(recipient=user.email,subject='Verify your FabsExpress email' if purpose=='verify' else 'Reset your FabsExpress password',body=f'Hello {user.name},\n\n'+('Verify your email address' if purpose=='verify' else 'Reset your password')+f':\n{link}\n\n'+('This link expires in 24 hours.' if purpose=='verify' else 'This link expires in 30 minutes. If you did not request it, ignore this email.')+'\n\nFabsExpress'))
def admin_required(fn):
    @wraps(fn)
    @login_required
    def wrapped(*a,**kw):
        if current_user.role!='admin':abort(403)
        if current_app.config['APP_ENV']=='production' and not session.get('mfa_verified'):abort(403)
        return fn(*a,**kw)
    return wrapped

def verified_required(fn):
    @wraps(fn)
    @login_required
    def wrapped(*a,**kw):
        if current_app.config['EMAIL_VERIFICATION_REQUIRED'] and not current_user.email_verified:
            flash('Please verify your email before booking a shipment.','warning')
            return redirect(url_for('auth.verification'))
        return fn(*a,**kw)
    return wrapped

@auth.route('/register',methods=['GET','POST'])
def register():
    if current_user.is_authenticated:return redirect(url_for('dashboard'))
    if request.method=='POST':
        try:
            name=text_field(request.form,'name',2,100);email=email_address(request.form.get('email',''));phone=text_field(request.form,'phone',8,24);password=password_value(request.form.get('password',''))
            if request.form.get('terms')!='yes':raise ValueError('Please accept the shipping conditions and privacy notice.')
            existing=db.session.scalar(db.select(User).where(User.email==email))
            if not existing:
                user=User(name=name,email=email,phone=phone);user.set_password(password);db.session.add(user);db.session.flush();send_link(user,'verify');audit('account.registered',user.id,user.id);db.session.commit()
            flash('If this address is new, a verification email will arrive shortly. You can then sign in.','success')
            return redirect(url_for('auth.login'))
        except ValueError as exc:flash(str(exc),'error')
    return render_template('auth/form.html',mode='register')

@auth.route('/login',methods=['GET','POST'])
def login():
    if current_user.is_authenticated:return redirect(url_for('dashboard'))
    if request.method=='POST':
        user=db.session.scalar(db.select(User).where(User.email==request.form.get('email','').strip().lower()))
        authenticated=user and user.enabled and user.check_password(request.form.get('password',''))
        if authenticated and user.role=='admin':
            if not user.mfa_secret and current_app.config['APP_ENV']=='production':authenticated=False
            elif user.mfa_secret:
                try:
                    secret=Fernet(current_app.config['DATA_ENCRYPTION_KEY'].encode()).decrypt(user.mfa_secret.encode()).decode()
                    totp=pyotp.TOTP(secret);counter=int(__import__('time').time())//30;code=request.form.get('otp','').strip()
                    matched=next((step for step in (counter-1,counter,counter+1) if step>user.mfa_counter and secrets.compare_digest(totp.at(step*30),code)),None)
                    if matched is None:authenticated=False
                    else:
                        # Atomically reject replayed OTPs across workers.
                        updated=db.session.execute(db.update(User).where(User.id==user.id,User.mfa_counter<matched).values(mfa_counter=matched)).rowcount
                        authenticated=updated==1
                except Exception:authenticated=False
        if authenticated:
            session.clear();login_user(user);session.permanent=True;session['token_version']=user.token_version;session['mfa_verified']=user.role=='admin';audit('account.login',user.id,user.id);db.session.commit()
            target=request.args.get('next','')
            if not target.startswith('/') or target.startswith('//') or urlsplit(target).netloc or '\\' in target:target=url_for('admin.overview') if user.role=='admin' else url_for('dashboard')
            return redirect(target)
        db.session.rollback();flash('Sign-in details or authenticator code are incorrect.','error')
    return render_template('auth/form.html',mode='login')

@auth.post('/logout')
@login_required
def logout():logout_user();session.clear();return redirect(url_for('index'))

@auth.route('/verify/<token>')
def verify_email(token):
    try:user=read_token(token,'verify',86400);user.email_verified=True;db.session.commit();flash('Your email is verified. You can sign in.','success')
    except ValueError as exc:flash(str(exc),'error')
    return redirect(url_for('auth.login'))

@auth.route('/verify-email',methods=['GET','POST'])
@login_required
def verification():
    if request.method=='POST' and not current_user.email_verified:send_link(current_user,'verify');db.session.commit();flash('A new verification link has been queued for your email address.','success')
    return render_template('auth/form.html',mode='verify')

@auth.route('/forgot-password',methods=['GET','POST'])
def forgot_password():
    if request.method=='POST':
        user=db.session.scalar(db.select(User).where(User.email==request.form.get('email','').strip().lower(),User.enabled.is_(True)))
        if user:send_link(user,'reset');db.session.commit()
        flash('If the account exists, password reset instructions will be emailed.','success');return redirect(url_for('auth.login'))
    return render_template('auth/form.html',mode='forgot')

@auth.route('/reset-password/<token>',methods=['GET','POST'])
def reset_password(token):
    try:user=read_token(token,'reset',1800)
    except ValueError as exc:flash(str(exc),'error');return redirect(url_for('auth.forgot_password'))
    if request.method=='POST':
        try:
            user.set_password(password_value(request.form.get('password','')));user.token_version+=1;audit('account.password_reset',user.id,user.id);db.session.commit();flash('Password updated. Sign in using your new password.','success');return redirect(url_for('auth.login'))
        except ValueError as exc:flash(str(exc),'error')
    return render_template('auth/form.html',mode='reset')