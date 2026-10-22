"""Email/password sign-in through Supabase Auth; no shared-password fallback."""
from flask import redirect, render_template, request, url_for

from app import limiter
from app.admin import admin_bp
from app.admin.auth import is_admin_authenticated, login_admin, logout_admin, valid_auth_form
from app.supabase_auth import AuthRejected, AuthUnavailable


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
