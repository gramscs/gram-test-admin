"""Real login/session checks with Supabase's HTTP boundary mocked."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import hashlib
from threading import Barrier, local
import time
from uuid import uuid4

import pytest
import requests
from sqlalchemy import select

from app import supabase_auth as provider
from app.admin import auth
from app.admin.backup import collect_admin_data
from app.auth_sessions import AuthSession
from app.models import db
from database.models import AdminUser, AuditLog
from conftest import AUTH_USER_ID, sign_in, sign_out

API = {'Accept': 'application/json'}


def row(app):
    with app.app_context():
        result = db.session.execute(select(AuthSession)).scalar_one()
        return result.session_hash, result.token_data


def test_supabase_login_uses_email_public_key_and_opaque_cookie(app, client, isolated_auth):
    assert b'name="email"' in client.get('/admin/login').data
    assert sign_in(client).status_code == 302
    assert client.get('/admin/consignments/list', headers=API).status_code == 200
    identifier, encrypted = row(app)
    with client.session_transaction() as state:
        assert hashlib.sha256(state[auth.ADMIN_SESSION_KEY].encode()).hexdigest() == identifier
        assert 'access_token' not in state and 'refresh_token' not in state
        assert state['admin_username'] == 'admin'
    assert 'access-1' not in encrypted and 'refresh-1' not in encrypted
    assert all(call[2]['apikey'] == 'test-anon-key' for call in isolated_auth.calls)
    assert isolated_auth.calls[0][3] == {'email': 'admin@example.test', 'password': 'test-admin-password'}
    with app.app_context():
        backup = collect_admin_data('admin')
        assert 'admin_auth_sessions' not in backup
        assert 'token_data' not in str(backup)


@pytest.mark.parametrize('reason', ['password', 'unconfirmed', 'anonymous', 'banned'])
def test_invalid_accounts_never_get_a_session(app, client, isolated_auth, reason):
    password = 'wrong' if reason == 'password' else 'test-admin-password'
    user = isolated_auth.users['admin@example.test']
    if reason == 'unconfirmed':
        user['email_confirmed_at'] = None
    elif reason == 'anonymous':
        user['is_anonymous'] = True
    elif reason == 'banned':
        user['banned_until'] = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    response = sign_in(client, password=password)
    assert response.status_code == 401
    assert b'Unable to sign in' in response.data
    assert client.get('/admin/consignments/list', headers=API).status_code == 401
    with app.app_context():
        assert db.session.query(AuthSession).count() == 0


def test_shared_passwords_and_legacy_cookies_no_longer_authenticate(client, monkeypatch):
    monkeypatch.setenv('ADMIN_USERNAME', 'admin')
    monkeypatch.setenv('ADMIN_PASSWORD', 'old-password')
    with client.session_transaction() as state:
        state['admin_authenticated'] = True
        state['admin_username'] = 'admin'
    assert client.get('/admin/consignments/list', headers=API).status_code == 401
    response = client.post('/admin/login', data={'username': 'admin', 'password': 'old-password'})
    assert response.status_code == 400


def test_login_and_logout_forms_require_csrf_and_same_origin(client, admin_client, isolated_auth):
    before = len(isolated_auth.calls)
    assert client.post('/admin/login', data={'email': 'admin@example.test', 'password': 'test-admin-password'}).status_code == 400
    with client.session_transaction() as state:
        token = state[auth.CSRF_KEY]
    assert client.post('/admin/login', data={'email': 'admin@example.test', 'password': 'test-admin-password', 'csrf_token': token},
                       headers={'Origin': 'https://other.example'}).status_code == 400
    assert client.post('/admin/logout').status_code == 400
    assert len(isolated_auth.calls) == before
    assert admin_client.get('/admin/logout').status_code == 405
    assert admin_client.get('/admin/consignments/list', headers=API).status_code == 200


def test_logout_deletes_server_session_and_replay_is_rejected(app, admin_client, isolated_auth):
    cookie = admin_client.get_cookie('session').value
    assert sign_out(admin_client).status_code == 302
    with app.app_context():
        assert db.session.query(AuthSession).count() == 0
    assert any('/logout?scope=local' in call[1] for call in isolated_auth.calls)
    copied = app.test_client()
    copied.set_cookie('session', cookie)
    assert copied.get('/admin/consignments/list', headers=API).status_code == 401


@pytest.mark.parametrize('change', ['expired', 'wrong_subject', 'revoked'])
def test_session_checks_expiry_and_verified_identity(app, admin_client, isolated_auth, change):
    with app.app_context():
        record = db.session.execute(select(AuthSession)).scalar_one()
        if change == 'expired':
            record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        elif change == 'wrong_subject':
            isolated_auth.users['admin@example.test']['id'] = str(uuid4())
        else:
            db.session.delete(record)
        db.session.commit()
    assert admin_client.get('/admin/consignments/list', headers=API).status_code == 401
    with app.app_context():
        assert db.session.query(AuthSession).count() == 0


def expire_access(app):
    with app.app_context():
        record = db.session.execute(select(AuthSession)).scalar_one()
        tokens = auth._open(record.token_data)
        tokens['expires_at'] = time.time() - 10
        record.token_data = auth._seal(tokens)
        db.session.commit()


def test_expired_access_token_refreshes_without_exposing_or_rotating_browser_id(app, admin_client, isolated_auth):
    before = row(app)[0]
    expire_access(app)
    assert admin_client.get('/admin/consignments/list', headers=API).status_code == 200
    assert row(app)[0] == before
    assert sum('grant_type=refresh_token' in call[1] for call in isolated_auth.calls) == 1
    with app.app_context():
        assert auth._open(row(app)[1])['access_token'] == 'access-2'


def test_concurrent_requests_refresh_the_same_session_only_once(app, admin_client, isolated_auth, monkeypatch):
    expire_access(app)
    cookie = admin_client.get_cookie('session').value
    barrier, per_thread = Barrier(2), local()
    original = auth._open
    def simultaneous_read(value):
        tokens = original(value)
        if not getattr(per_thread, 'read_once', False):
            per_thread.read_once = True
            barrier.wait(timeout=10)
        return tokens
    monkeypatch.setattr(auth, '_open', simultaneous_read)
    def request():
        client = app.test_client()
        client.set_cookie('session', cookie)
        return client.get('/admin/consignments/list', headers=API).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(lambda _: request(), range(2))) == [200, 200]
    assert sum('grant_type=refresh_token' in call[1] for call in isolated_auth.calls) == 1


@pytest.mark.parametrize('where', ['login', 'verification', 'refresh'])
def test_provider_outage_fails_closed_without_leaking_secrets(app, admin_client, client, monkeypatch, where):
    if where == 'refresh':
        expire_access(app)
    def unavailable(*args, **kwargs):
        raise requests.ConnectionError('private-provider-key-and-password')
    monkeypatch.setattr(requests, 'request', unavailable)
    if where == 'login':
        response = sign_in(client)
    else:
        response = admin_client.get('/admin/consignments/list', headers=API)
    assert response.status_code == 503
    assert b'private-provider-key-and-password' not in response.data
    with app.app_context():
        assert db.session.query(AuthSession).count() == 1  # An outage does not silently revoke the current session.


def test_legacy_role_and_status_do_not_restrict_access_and_audit_identity_is_verified(app, admin_client):
    with app.app_context():
        user = db.session.query(AdminUser).filter_by(supabase_user_id=AUTH_USER_ID).one()
        identifier = user.id
        user.role = 'viewer'
        user.active = False
        db.session.commit()
    assert admin_client.get('/admin/consignments/list', headers=API).status_code == 200
    assert admin_client.post('/admin/consignments/save', json={'rows': []}).status_code == 200
    assert sign_in(admin_client).status_code == 302
    with admin_client.session_transaction() as state:
        state['admin_username'] = 'impersonated-admin'
    saved = admin_client.post('/admin/consignments/save', json={'rows': [{'consignment_number': 'AUTH-001'}]})
    assert saved.status_code == 200
    with app.app_context():
        assert {record.actor_id for record in db.session.query(AuditLog).all()} == {identifier}


def test_relogin_rotates_session_and_encryption_key_change_invalidates_it(app, admin_client):
    old_cookie = admin_client.get_cookie('session').value
    old_hash = row(app)[0]
    assert sign_in(admin_client).status_code == 302
    assert row(app)[0] != old_hash
    replay = app.test_client()
    replay.set_cookie('session', old_cookie)
    assert replay.get('/admin/consignments/list', headers=API).status_code == 401
    # Simulate key rotation without asking Flask to decode a cookie signed by a different key.
    with app.app_context():
        record = db.session.execute(select(AuthSession)).scalar_one()
        record.token_data = 'unreadable-old-ciphertext'
        db.session.commit()
    assert admin_client.get('/admin/consignments/list', headers=API).status_code == 401


def test_missing_public_key_does_not_fall_back_to_storage_key(app, client):
    app.config['SUPABASE_ANON_KEY'] = ''
    response = sign_in(client)
    assert response.status_code == 503 and b'SUPABASE_ANON_KEY' in response.data


@pytest.mark.parametrize('key', ['sb_secret_test-key', 'service_role_jwt'])
def test_privileged_storage_keys_are_not_accepted_for_user_signin(app, client, isolated_auth, key):
    import jwt
    app.config['SUPABASE_ANON_KEY'] = jwt.encode({'role': 'service_role'}, 'test-only-signing-key-at-least-32-bytes') if key == 'service_role_jwt' else key
    response = sign_in(client)
    assert response.status_code == 503
    assert b'publishable or anon key' in response.data
    assert isolated_auth.calls == []


def test_invalid_refresh_token_requires_signin_again(app, admin_client, isolated_auth):
    expire_access(app)
    isolated_auth.refresh.clear()
    response = admin_client.get('/admin/consignments/list', headers=API)
    assert response.status_code == 401
    with app.app_context():
        assert db.session.query(AuthSession).count() == 0


def test_local_logout_stays_effective_if_supabase_is_unavailable(app, admin_client, monkeypatch):
    def unavailable(*args, **kwargs):
        raise requests.ConnectionError('secret must not be logged')
    monkeypatch.setattr(requests, 'request', unavailable)
    assert sign_out(admin_client).status_code == 302
    with app.app_context():
        assert db.session.query(AuthSession).count() == 0
    assert admin_client.get('/admin/consignments/list', headers=API).status_code == 401
