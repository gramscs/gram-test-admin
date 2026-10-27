"""Supabase identity verification and private PostgreSQL-backed login sessions."""
import base64
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import wraps
import hashlib
import hmac
import json
import re
import secrets
import time
from urllib.parse import urlsplit
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app, g, jsonify, redirect, request, session, url_for
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError

from app.auth_sessions import AuthSession
from app.models import db
from app import supabase_auth as provider
from app.user_profiles import profile_for_identity
from database.models import AdminUser

ADMIN_SESSION_KEY = 'admin_auth_session'
ADMIN_SESSION_USERNAME_KEY = 'admin_username'
CSRF_KEY = 'auth_csrf_token'


@dataclass(frozen=True)
class AuthenticatedAdmin:
    id: UUID
    username: str
    role: str
    supabase_user_id: UUID
    display_name: str = ''


def _cipher():
    key = str(current_app.secret_key).encode('utf-8')
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b'gram-scs-auth-tokens-v1\0' + key).digest()))


def _seal(tokens):
    return _cipher().encrypt(json.dumps(tokens).encode('utf-8')).decode('ascii')


def _open(value):
    tokens = json.loads(_cipher().decrypt(value.encode('ascii')))
    if (not isinstance(tokens, dict) or tokens.get('issuer') != current_app.config.get('SUPABASE_URL', '').rstrip('/')
            or not isinstance(tokens.get('access_token'), str) or not tokens['access_token']
            or not isinstance(tokens.get('refresh_token'), str) or not tokens['refresh_token']
            or not isinstance(tokens.get('expires_at'), (int, float))):
        raise ValueError('Invalid stored authentication session.')
    return tokens


def _session_hash():
    value = session.get(ADMIN_SESSION_KEY)
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', value):
        return None
    return hashlib.sha256(value.encode('ascii')).hexdigest()


def csrf_token():
    if not isinstance(session.get(CSRF_KEY), str):
        session[CSRF_KEY] = secrets.token_urlsafe(32)
    return session[CSRF_KEY]


def same_origin():
    origin = request.headers.get('Origin')
    site = request.headers.get('Sec-Fetch-Site')
    if site == 'cross-site':
        return False
    if origin == 'null':
        return site == 'same-origin'
    return not origin or urlsplit(origin).netloc == request.host


def valid_auth_form():
    expected, supplied = session.get(CSRF_KEY), request.form.get('csrf_token')
    token_valid = (isinstance(expected, str) and isinstance(supplied, str)
                   and hmac.compare_digest(expected.encode('utf-8'), supplied.encode('utf-8')))
    return same_origin() and token_valid


def _forget(row=None):
    if row is not None:
        db.session.delete(row)
        db.session.commit()
    else:
        db.session.rollback()
    session.clear()
    g.admin_user = None
    g.auth_checked = True


def current_admin_user():
    if getattr(g, 'auth_checked', False):
        return g.admin_user
    g.auth_checked, g.admin_user = True, None
    identifier = _session_hash()
    if identifier is None:
        # In particular, a legacy admin_authenticated=True cookie grants no access.
        session.pop('admin_authenticated', None)
        return None
    try:
        row = db.session.get(AuthSession, identifier)
        user = db.session.get(AdminUser, row.admin_user_id) if row else None
        now = datetime.now(UTC)
        if not row or row.expires_at <= now or not user or not user.supabase_user_id:
            _forget(row)
            return None
        tokens = _open(row.token_data)
        refreshed = False
        if tokens['expires_at'] <= time.time() + 60:
            # Serialize refresh rotation, then reload a token another request may have refreshed.
            row = db.session.execute(select(AuthSession).where(AuthSession.session_hash == identifier)
                .with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
            if not row:
                _forget()
                return None
            tokens = _open(row.token_data)
            if tokens['expires_at'] <= time.time() + 60:
                tokens = provider.refresh(tokens['refresh_token'])
                row.token_data = _seal(tokens)
                refreshed = True
        identity = provider.verified_identity(tokens['access_token'])
        if identity.id != user.supabase_user_id:
            raise provider.AuthRejected()
        verified = AuthenticatedAdmin(user.id, user.username, user.role, user.supabase_user_id,
                                     identity.full_name or user.username)
        # Finish authentication's transaction before a view starts changing business records.
        if refreshed:
            db.session.commit()
        else:
            db.session.rollback()
        g.admin_user = verified
        session[ADMIN_SESSION_USERNAME_KEY] = verified.username
        return verified
    except (provider.AuthRejected, InvalidToken, ValueError, KeyError, UnicodeError):
        _forget(row)
        return None
    except provider.AuthUnavailable:
        db.session.rollback()
        raise
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.error('Authentication storage is unavailable; check database migration 004.')
        raise provider.AuthUnavailable('Sign-in storage is unavailable. Ask an administrator to check database migration 004.') from None


def is_admin_authenticated():
    return current_admin_user() is not None


def login_admin(email, password):
    establish_session(provider.sign_in(email, password))


def establish_session(tokens):
    """Verify a Supabase session before creating the app's private session."""
    identity = provider.verified_identity(tokens['access_token'])
    try:
        user = profile_for_identity(identity.id)
        verified = AuthenticatedAdmin(user.id, user.username, user.role, identity.id, identity.full_name or user.username)
        previous = _session_hash()
        if previous:
            db.session.execute(delete(AuthSession).where(AuthSession.session_hash == previous))
        now = datetime.now(UTC)
        db.session.execute(delete(AuthSession).where(AuthSession.expires_at <= now))
        identifier = secrets.token_urlsafe(32)
        db.session.add(AuthSession(session_hash=hashlib.sha256(identifier.encode('ascii')).hexdigest(),
            admin_user_id=user.id, token_data=_seal(tokens), created_at=now,
            expires_at=now + current_app.permanent_session_lifetime))
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.error('Unable to create authentication session; check database migration 004.')
        raise provider.AuthUnavailable('Sign-in storage is unavailable. Ask an administrator to check database migration 004.') from None
    except ValueError:
        db.session.rollback()
        raise provider.AuthUnavailable('Unable to create your application profile. Contact your administrator.') from None
    session.clear()
    session.permanent = True
    session[ADMIN_SESSION_KEY] = identifier
    session[ADMIN_SESSION_USERNAME_KEY] = verified.username
    csrf_token()
    g.admin_user, g.auth_checked = verified, True


def logout_admin():
    identifier, access = _session_hash(), None
    try:
        row = db.session.get(AuthSession, identifier) if identifier else None
        if row:
            try:
                access = _open(row.token_data)['access_token']
            except (InvalidToken, ValueError, KeyError, UnicodeError):
                pass
            db.session.delete(row)
            db.session.commit()
        else:
            db.session.rollback()
    except SQLAlchemyError:
        db.session.rollback()
        raise provider.AuthUnavailable('Sign-out could not be completed. Please try again.') from None
    session.clear()
    g.admin_user, g.auth_checked = None, True
    if access:
        try:
            provider.sign_out(access)
        except provider.AuthUnavailable:
            current_app.logger.warning('Local session removed; Supabase session revocation is temporarily unavailable.')


def require_admin(f=None, *, read_only=False):
    if f is None:
        return lambda view: require_admin(view, read_only=read_only)

    @wraps(f)
    def decorated(*args, **kwargs):
        try:
            user = current_admin_user()
        except provider.AuthUnavailable as error:
            return jsonify(success=False, message=str(error)), error.status_code
        if user:
            if request.method not in ('GET', 'HEAD', 'OPTIONS'):
                if not same_origin():
                    return jsonify(success=False, message='Changes must come from this admin app.'), 403
                # All confirmed Supabase members currently have full panel access.
                # read_only is retained for compatibility with existing route declarations.
            return f(*args, **kwargs)
        wants_json = (request.path.startswith('/api/') or 'application/json' in (request.accept_mimetypes.best or '')
                      or (request.content_type or '').startswith('application/json'))
        if wants_json:
            return jsonify(error='Authentication required'), 401
        return redirect(url_for('admin.login'))

    return decorated
