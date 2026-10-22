"""Stateless calls to the configured Supabase Auth service.

Never reuse the privileged Storage client for a user's authentication session.
Errors deliberately omit provider responses, passwords, keys and bearer tokens.
"""
import time
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID

import requests
import jwt
from flask import current_app


class AuthRejected(Exception):
    pass


class AuthUnavailable(Exception):
    pass


def _configuration():
    url = current_app.config.get('SUPABASE_URL', '').strip().rstrip('/')
    key = current_app.config.get('SUPABASE_ANON_KEY', '').strip()
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment or not key):
        raise AuthUnavailable('Supabase sign-in is not configured. Check SUPABASE_URL and SUPABASE_ANON_KEY.')
    # Inspect API-key metadata only to reject privileged keys; this never verifies a user.
    role = None
    if key.count('.') == 2:
        try:
            role = jwt.decode(key, options={'verify_signature': False}).get('role')
        except jwt.InvalidTokenError:
            pass
    if key.startswith('sb_secret_') or role == 'service_role':
        raise AuthUnavailable('Use the Supabase publishable or anon key for SUPABASE_ANON_KEY, not a server secret key.')
    return url, key


def _call(method, path, *, token=None, payload=None):
    url, key = _configuration()
    headers = {'apikey': key, 'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    try:
        response = requests.request(method, url + '/auth/v1/' + path, headers=headers,
                                    json=payload, timeout=(5, 15), allow_redirects=False)
        if response.status_code in (400, 401, 403, 422):
            raise AuthRejected()
        if not 200 <= response.status_code < 300:
            raise AuthUnavailable('Sign-in is temporarily unavailable. Please try again.')
        if response.status_code == 204:
            return {}
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (requests.RequestException, ValueError):
        raise AuthUnavailable('Sign-in is temporarily unavailable. Please try again.') from None


def _tokens(result):
    access, refresh, lifetime = (result.get('access_token'), result.get('refresh_token'), result.get('expires_in'))
    if (not isinstance(access, str) or not 1 <= len(access) <= 32768
            or not isinstance(refresh, str) or not 1 <= len(refresh) <= 4096
            or isinstance(lifetime, bool) or not isinstance(lifetime, int) or not 0 < lifetime <= 86400):
        raise AuthUnavailable('The sign-in service returned an invalid session. Please try again.')
    return dict(access_token=access, refresh_token=refresh, expires_at=time.time() + lifetime,
                issuer=current_app.config['SUPABASE_URL'].rstrip('/'))


def sign_in(email, password):
    return _tokens(_call('POST', 'token?grant_type=password', payload={'email': email, 'password': password}))


def refresh(refresh_token):
    return _tokens(_call('POST', 'token?grant_type=refresh_token', payload={'refresh_token': refresh_token}))


def verified_user(access_token):
    user = _call('GET', 'user', token=access_token)
    try:
        identifier = UUID(user.get('id', ''))
    except (ValueError, TypeError, AttributeError):
        raise AuthRejected() from None
    if user.get('is_anonymous') or not user.get('email_confirmed_at'):
        raise AuthRejected()
    if user.get('banned_until'):
        try:
            if datetime.fromisoformat(user['banned_until']) > datetime.now(UTC):
                raise AuthRejected()
        except (ValueError, TypeError):
            raise AuthRejected() from None
    return identifier


def sign_out(access_token):
    try:
        _call('POST', 'logout?scope=local', token=access_token)
    except AuthRejected:
        pass  # The provider may already have expired this session.


def check_configuration():
    settings = _call('GET', 'settings')
    if not settings.get('external', {}).get('email'):
        raise AuthUnavailable('Enable the Email provider in Supabase Authentication settings.')
