"""Email/password registration and sign-in through Supabase Auth."""
import re
from flask import redirect, render_template, request, url_for

from app import limiter
from app.admin import admin_bp
from app.admin.auth import is_admin_authenticated, login_admin, logout_admin, valid_auth_form
from app.supabase_auth import AuthRejected, AuthUnavailable, RegistrationRejected, sign_up


@admin_bp.get('/admin/signup')
def signup():
    try:
        if is_admin_authenticated():
            return redirect(url_for('admin.dashboard'))
    except AuthUnavailable as error:
        return render_template('admin/signup.html', error=str(error)), 503
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
        sign_up(full_name, email, password)
    except RegistrationRejected as error:
        return render_template('admin/signup.html', error=str(error), full_name=full_name, email=email), 400
    except AuthRejected:
        return render_template('admin/signup.html', error='Unable to register. Check your details and try again.'), 400
    except AuthUnavailable as error:
        return render_template('admin/signup.html', error=str(error)), 503
    # Do not auto-login or expose tokens if Supabase has email confirmation disabled.
    return render_template('admin/signup.html', submitted=True)


@admin_bp.get('/admin/login')
def login():
    try:
        if is_admin_authenticated():
            return redirect(url_for('admin.dashboard'))
    except AuthUnavailable as error:
        return render_template('admin/login.html', error=str(error)), 503
    return render_template('admin/login.html', error=None)


@admin_bp.post('/admin/login')
@limiter.limit('5 per minute')
def login_submit():
    if not valid_auth_form():
        return render_template('admin/login.html', error='Your sign-in form expired. Please try again.'), 400
    email = (request.form.get('email') or '').strip()
    password = request.form.get('password') or ''
    if not email or '@' not in email or len(email) > 254 or not password or len(password) > 4096:
        return render_template('admin/login.html', error='Enter your email address and password.'), 400
    try:
        login_admin(email, password)
    except AuthRejected:
        return render_template('admin/login.html', error='Unable to sign in. Check your email and password, or contact your administrator.'), 401
    except AuthUnavailable as error:
        return render_template('admin/login.html', error=str(error)), 503
    return redirect(url_for('admin.dashboard'))


@admin_bp.post('/admin/logout')
@limiter.limit('10 per minute')
def logout():
    if not valid_auth_form():
        return 'Your sign-out form expired. Reload the page and try again.', 400
    try:
        logout_admin()
    except AuthUnavailable as error:
        return str(error), 503
    return redirect(url_for('admin.login'))
