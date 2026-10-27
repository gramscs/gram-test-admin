"""Email/password registration and sign-in through Supabase Auth."""
import base64
import hashlib
import hmac
import re
import secrets
import time
from urllib.parse import urlencode, urlsplit

from flask import current_app, redirect, render_template, request, session, url_for

from app import limiter
from app.admin import admin_bp
from app.admin.auth import establish_session, is_admin_authenticated, login_admin, logout_admin, valid_auth_form
from app.supabase_auth import (AuthRejected, AuthUnavailable, RegistrationRejected,
                               exchange_code, sign_up)

SIGNUP_FLOW_KEY = 'supabase_signup_flow'


def _signup_flow():
    redirect_to = current_app.config.get('SUPABASE_AUTH_REDIRECT_URL') or url_for('admin.auth_callback', _external=True)
    parsed = urlsplit(redirect_to)
    local = parsed.hostname in ('localhost', '127.0.0.1', '::1')
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
            or (parsed.scheme != 'https' and not (local and parsed.scheme == 'http'))):
        raise AuthUnavailable('Set SUPABASE_AUTH_REDIRECT_URL to the app\'s HTTPS /admin/auth/callback URL (HTTP is allowed on localhost).')
    verifier, state = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).rstrip(b'=').decode('ascii')
    session[SIGNUP_FLOW_KEY] = {'verifier': verifier, 'state': state, 'created_at': time.time()}
    return challenge, redirect_to + '?' + urlencode({'state': state})


def _unavailable(template, error, **context):
    return render_template(template, error=str(error), **context), error.status_code


@admin_bp.after_request
def private_auth_response(response):
    if request.endpoint in {'admin.login', 'admin.login_submit', 'admin.signup', 'admin.signup_submit',
                            'admin.auth_callback', 'admin.logout'}:
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@admin_bp.get('/admin/signup')
def signup():
    try:
        if is_admin_authenticated():
            return redirect(url_for('admin.dashboard'))
    except AuthUnavailable as error:
        return _unavailable('admin/signup.html', error)
    return render_template('admin/signup.html')


@admin_bp.post('/admin/signup')
@limiter.limit('5 per minute')
def signup_submit():
    if not valid_auth_form():
        return render_template('admin/signup.html', error='Your registration form expired. Please try again.'), 400
    full_name = (request.form.get('full_name') or '').strip()
    email = (request.form.get('email') or '').strip()
    password = request.form.get('password') or ''
    confirmation = request.form.get('confirm_password') or ''
    error = None
    if not 1 <= len(full_name) <= 120 or any(ord(c) < 32 or ord(c) == 127 for c in full_name):
        error = 'Enter your full name (up to 120 characters).'
    elif len(email) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        error = 'Enter a valid email address.'
    elif not 8 <= len(password) <= 4096 or not password.strip():
        error = 'Use a password of at least 8 characters.'
    elif password != confirmation:
        error = 'Your passwords do not match.'
    if error:
        return render_template('admin/signup.html', error=error, full_name=full_name, email=email), 400
    try:
        challenge, redirect_to = _signup_flow()
        tokens = sign_up(full_name, email, password, code_challenge=challenge, redirect_to=redirect_to)
        if tokens:
            establish_session(tokens)
            return redirect(url_for('admin.dashboard'))
    except RegistrationRejected as error:
        return render_template('admin/signup.html', error=str(error), full_name=full_name, email=email), 400
    except AuthRejected:
        return render_template('admin/signup.html', error='Unable to register. Check your details and try again.'), 400
    except AuthUnavailable as error:
        return _unavailable('admin/signup.html', error, full_name=full_name, email=email)
    return render_template('admin/signup.html', submitted=True)


@admin_bp.get('/admin/auth/callback')
@limiter.limit('10 per minute')
def auth_callback():
    """Exchange email confirmation codes, bound to the browser that registered."""
    flow = session.get(SIGNUP_FLOW_KEY)
    code, state = request.args.get('code', ''), request.args.get('state', '')
    if request.args.get('error'):
        session.pop(SIGNUP_FLOW_KEY, None)
        return render_template('admin/login.html', error='The confirmation link is invalid or expired. Try signing in, or register again to request a new link.'), 400
    if (not isinstance(flow, dict) or not isinstance(flow.get('state'), str)
            or not isinstance(flow.get('verifier'), str)
            or time.time() - flow.get('created_at', 0) > 86400
            or not re.fullmatch(r'[A-Za-z0-9_-]{43}', state) or not hmac.compare_digest(flow['state'], state)
            or not 1 <= len(code) <= 4096):
        return render_template('admin/login.html', error='To finish signing in, open the confirmation link in the browser where you registered, or sign in below with your confirmed email.'), 400
    try:
        establish_session(exchange_code(code, flow['verifier']))
    except AuthRejected:
        session.pop(SIGNUP_FLOW_KEY, None)
        return render_template('admin/login.html', error='The confirmation link is invalid or expired. Please sign in with your email and password.'), 400
    except AuthUnavailable as error:
        return _unavailable('admin/login.html', error)
    return redirect(url_for('admin.dashboard'))


@admin_bp.get('/admin/login')
def login():
    try:
        if is_admin_authenticated():
            return redirect(url_for('admin.dashboard'))
    except AuthUnavailable as error:
        return _unavailable('admin/login.html', error)
    return render_template('admin/login.html', error=None)


@admin_bp.post('/admin/login')
@limiter.limit('5 per minute')
def login_submit():
    if not valid_auth_form():
        return render_template('admin/login.html', error='Your sign-in form expired. Please try again.'), 400
    email = (request.form.get('email') or '').strip()
    password = request.form.get('password') or ''
    if not email or '@' not in email or len(email) > 254 or not password or len(password) > 4096:
        return render_template('admin/login.html', error='Enter your email address and password.', email=email), 400
    try:
        login_admin(email, password)
    except AuthRejected:
        return render_template('admin/login.html', error='Unable to sign in. Check your email and password and confirm your email before signing in.', email=email), 401
    except AuthUnavailable as error:
        return _unavailable('admin/login.html', error, email=email)
    return redirect(url_for('admin.dashboard'))


@admin_bp.post('/admin/logout')
@limiter.limit('10 per minute')
def logout():
    if not valid_auth_form():
        return 'Your sign-out form expired. Reload the page and try again.', 400
    try:
        logout_admin()
    except AuthUnavailable as error:
        return str(error), error.status_code
    return redirect(url_for('admin.login'))
