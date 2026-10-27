"""Stateless calls to the configured Supabase Auth service.

Never reuse the privileged Storage client for a user's authentication session.
Errors deliberately omit provider responses, passwords, keys and bearer tokens.
"""
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID

import requests
import jwt
from flask import current_app


class AuthRejected(Exception):
    pass


class AuthUnavailable(Exception):
    status_code = 503


class AuthRateLimited(AuthUnavailable):
    status_code = 429


class RegistrationRejected(AuthRejected):
    """Only fixed, safe validation messages may be shown to the registrant."""


@dataclass(frozen=True)
class SupabaseIdentity:
    id: UUID
    full_name: str = ''


def validate_public_key(key):
    """Reject a privileged API key without printing its value."""
    # Inspect API-key metadata only to reject privileged keys; this never verifies a user.
    role = None
    if key.count('.') == 2:
        try:
            role = jwt.decode(key, options={'verify_signature': False}).get('role')
        except jwt.InvalidTokenError:
            pass
    if key.startswith('sb_secret_') or role == 'service_role':
        raise AuthUnavailable('Use the Supabase publishable or anon key for SUPABASE_ANON_KEY, not a server secret key.')


def _configuration():
    url = current_app.config.get('SUPABASE_URL', '').strip().rstrip('/')
    key = current_app.config.get('SUPABASE_ANON_KEY', '').strip()
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment or not key):
        raise AuthUnavailable('Supabase sign-in is not configured. Check SUPABASE_URL and SUPABASE_ANON_KEY.')
    validate_public_key(key)
    return url, key


def _call(method, path, *, token=None, payload=None, params=None):
    url, key = _configuration()
    headers = {'apikey': key, 'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    unavailable = ('Registration' if path == 'signup' else 'Sign-in') + ' is temporarily unavailable. Please try again.'
    try:
        options = {'params': params} if params else {}
        response = requests.request(method, url + '/auth/v1/' + path, headers=headers,
                                    json=payload, timeout=(5, 15), allow_redirects=False, **options)
        if response.status_code == 429:
            raise AuthRateLimited('Too many authentication attempts. Please wait a few minutes and try again.')
        if response.status_code == 401 and not token:
            raise AuthUnavailable('Supabase rejected the Auth API key. Check SUPABASE_ANON_KEY for this project.')
        if response.status_code in (400, 401, 403, 422):
            if path == 'signup':
                try:
                    error = response.json()
                    code = error.get('error_code', error.get('code')) if isinstance(error, dict) else None
                except ValueError:
                    code = None
                if code in ('user_already_exists', 'email_exists'):
                    return {}  # Match Supabase's generic response without exposing account existence.
                if code == 'weak_password':
                    raise RegistrationRejected('Choose a stronger password and try again.')
                if code in ('signup_disabled', 'email_provider_disabled'):
                    raise RegistrationRejected('Registration is currently disabled. Contact your administrator.')
            raise AuthRejected()
        if not 200 <= response.status_code < 300:
            current_app.logger.warning('Supabase Auth returned HTTP %s.', response.status_code)
            raise AuthUnavailable(unavailable)
        if response.status_code == 204:
            return {}
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (requests.RequestException, ValueError):
        current_app.logger.warning('Supabase Auth could not be reached or returned an invalid response.')
        raise AuthUnavailable(unavailable) from None


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


def sign_up(full_name, email, password, *, code_challenge, redirect_to):
    # Supabase owns the account, email confirmation, password policy and name.
    # Never accept application roles or profile IDs from a registration form.
    result = _call('POST', 'signup', params={'redirect_to': redirect_to}, payload={
        'email': email, 'password': password, 'data': {'full_name': full_name},
        'code_challenge': code_challenge, 'code_challenge_method': 's256'})
    # With Confirm email disabled, Supabase returns an immediately usable session.
    # Otherwise confirmation must complete before an application session is created.
    if result.get('access_token') or result.get('refresh_token'):
        return _tokens(result)
    return None


def exchange_code(code, verifier):
    return _tokens(_call('POST', 'token?grant_type=pkce',
                        payload={'auth_code': code, 'code_verifier': verifier}))


def refresh(refresh_token):
    return _tokens(_call('POST', 'token?grant_type=refresh_token', payload={'refresh_token': refresh_token}))


def verified_identity(access_token):
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
    metadata = user.get('user_metadata')
    name = metadata.get('full_name') if isinstance(metadata, dict) else None
    name = ''.join(c for c in name.strip() if ord(c) >= 32 and ord(c) != 127)[:120] if isinstance(name, str) else ''
    return SupabaseIdentity(identifier, name)


def verified_user(access_token):
    return verified_identity(access_token).id


def sign_out(access_token):
    try:
        _call('POST', 'logout?scope=local', token=access_token)
    except AuthRejected:
        pass  # The provider may already have expired this session.


def check_configuration():
    settings = _call('GET', 'settings')
    if not settings.get('external', {}).get('email'):
        raise AuthUnavailable('Enable the Email provider in Supabase Authentication settings.')
