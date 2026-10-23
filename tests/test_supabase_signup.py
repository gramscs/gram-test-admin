"""Registration, automatic audit profiles and shared panel access."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import requests

from app.admin import auth
from app.auth_sessions import AuthSession
from app.models import db
from database.models import AdminUser, AuditLog
from auth_support import AUTH_USER_ID, sign_in


def register(client, **fields):
    client.get('/admin/signup')
    with client.session_transaction() as state:
        token = state[auth.CSRF_KEY]
    return client.post('/admin/signup', data={
        'csrf_token': token, 'full_name': 'New Member', 'email': 'new@example.test',
        'password': 'signup-password', 'confirm_password': 'signup-password', **fields})


def test_registration_confirmation_login_and_audit_profile(app, client, isolated_auth):
    assert b'/admin/signup' in client.get('/admin/login').data
    response = register(client, role='administrator', supabase_user_id=str(AUTH_USER_ID))
    assert response.status_code == 200 and b'Registration submitted' in response.data
    assert isolated_auth.calls[-1][3] == {
        'email': 'new@example.test', 'password': 'signup-password', 'data': {'full_name': 'New Member'}}
    user = isolated_auth.users['new@example.test']
    subject = UUID(user['id'])
    with app.app_context():
        assert db.session.query(AdminUser).count() == 1
        assert db.session.query(AuthSession).count() == 0
    assert sign_in(client, 'new@example.test', 'signup-password').status_code == 401
    user['email_confirmed_at'] = '2026-01-01T00:00:00Z'
    assert sign_in(client, 'new@example.test', 'signup-password').status_code == 302
    assert b'New Member' in client.get('/admin/consignments').data
    assert client.get('/admin/signup').status_code == 302
    assert client.post('/admin/consignments/save', json={'rows': [{'consignment_number': 'NEW-USER-001'}]}).status_code == 200
    with app.app_context():
        profile = db.session.query(AdminUser).filter_by(supabase_user_id=subject).one()
        assert profile.id == subject and profile.username == 'supabase-' + subject.hex
        assert profile.role == 'operator'
        assert {log.actor_id for log in db.session.query(AuditLog)} == {subject}
    assert sign_in(client, 'new@example.test', 'signup-password').status_code == 302
    with app.app_context():
        assert db.session.query(AdminUser).count() == 2
        assert db.session.query(AuthSession).count() == 1


def test_multiple_users_have_independent_sessions_and_full_access(app, isolated_auth):
    identifiers = []
    for email in ('first@example.test', 'second@example.test'):
        client = app.test_client()
        assert register(client, email=email).status_code == 200
        isolated_auth.users[email]['email_confirmed_at'] = '2026-01-01T00:00:00Z'
        assert sign_in(client, email, 'signup-password').status_code == 302
        assert client.post('/admin/consignments/save', json={'rows': []}).status_code == 200
        with client.session_transaction() as state:
            identifiers.append(state[auth.ADMIN_SESSION_KEY])
    assert len(set(identifiers)) == 2
    with app.app_context():
        assert db.session.query(AdminUser).count() == 3
        assert db.session.query(AuthSession).count() == 2


def test_duplicate_registration_is_generic_and_does_not_change_password(client, isolated_auth):
    first = register(client)
    second = register(client, password='different-password', confirm_password='different-password')
    assert first.status_code == second.status_code == 200
    assert first.data == second.data
    assert isolated_auth.passwords['new@example.test'] == 'signup-password'
    assert len(isolated_auth.users) == 2


@pytest.mark.parametrize('fields', [
    {'full_name': ''}, {'full_name': 'a' * 121}, {'full_name': 'Bad\x00Name'},
    {'email': 'invalid'}, {'email': 'a@ b.test'}, {'email': 'a@b@c.test'},
    {'email': 'a' * 250 + '@b.test'}, {'password': 'short'}, {'password': ' ' * 8},
    {'password': 'p' * 4097}, {'confirm_password': 'mismatch'},
])
def test_invalid_registration_never_calls_supabase(client, isolated_auth, fields):
    response = register(client, **fields)
    assert response.status_code == 400
    assert isolated_auth.calls == []
    assert b'value="signup-password"' not in response.data


def test_registration_csrf_and_origin(client, isolated_auth):
    assert client.post('/admin/signup', data={'email': 'new@example.test'}).status_code == 400
    client.get('/admin/signup')
    with client.session_transaction() as state:
        token = state[auth.CSRF_KEY]
    response = client.post('/admin/signup', data={'csrf_token': token}, headers={'Origin': 'https://other.example'})
    assert response.status_code == 400
    assert isolated_auth.calls == []


@pytest.mark.parametrize('code,message,status', [
    ('weak_password', b'stronger password', 400),
    ('signup_disabled', b'Registration is currently disabled', 400),
    ('email_provider_disabled', b'Registration is currently disabled', 400),
    ('validation_failed', b'Unable to register', 400),
    ('email_exists', b'Registration submitted', 200),
])
def test_provider_signup_errors_are_safe(client, monkeypatch, code, message, status):
    monkeypatch.setattr(requests, 'request', lambda *a, **k: SimpleNamespace(
        status_code=422, json=lambda: {'code': code, 'msg': 'private-provider-details'}))
    response = register(client)
    assert response.status_code == status and message in response.data
    assert b'private-provider-details' not in response.data


def test_signup_outage_does_not_leak_or_create_profile(app, client, monkeypatch):
    def unavailable(*a, **k):
        raise requests.ConnectionError('private-password-and-key')
    monkeypatch.setattr(requests, 'request', unavailable)
    response = register(client)
    assert response.status_code == 503 and b'private-password-and-key' not in response.data
    with app.app_context():
        assert db.session.query(AdminUser).count() == 1
        assert db.session.query(AuthSession).count() == 0


def test_auto_confirm_response_tokens_are_not_used_or_exposed(app, client, monkeypatch):
    monkeypatch.setattr(requests, 'request', lambda *a, **k: SimpleNamespace(
        status_code=200, json=lambda: {'access_token': 'private-access', 'refresh_token': 'private-refresh'}))
    response = register(client)
    assert response.status_code == 200
    assert b'private-access' not in response.data and b'private-refresh' not in response.data
    with client.session_transaction() as state:
        assert auth.ADMIN_SESSION_KEY not in state
    with app.app_context():
        assert db.session.query(AuthSession).count() == 0


def test_name_is_escaped_and_metadata_cannot_replace_legacy_profile(app, client, isolated_auth):
    isolated_auth.users['admin@example.test']['user_metadata'] = {
        'full_name': '<script>alert(1)</script>', 'role': 'administrator', 'id': str(uuid4())}
    with app.app_context():
        existing = db.session.query(AdminUser).filter_by(supabase_user_id=AUTH_USER_ID).one()
        identifier = existing.id
        existing.role, existing.active = 'viewer', False
        db.session.commit()
    assert sign_in(client).status_code == 302
    response = client.get('/admin/consignments')
    assert b'&lt;script&gt;alert(1)&lt;/script&gt;' in response.data
    assert b'<script>alert(1)</script>' not in response.data
    with app.app_context():
        profile = db.session.get(AdminUser, identifier)
        assert profile.username == 'admin' and profile.role == 'viewer' and profile.active is False
        assert db.session.query(AdminUser).count() == 1


def test_concurrent_first_logins_create_one_audit_profile(app, isolated_auth):
    subject = uuid4()
    isolated_auth.users['new@example.test'] = {'id': str(subject), 'email_confirmed_at': '2026-01-01T00:00:00Z'}
    isolated_auth.passwords['new@example.test'] = 'signup-password'
    def login(_):
        return sign_in(app.test_client(), 'new@example.test', 'signup-password').status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(login, range(2))) == [302, 302]
    with app.app_context():
        assert db.session.query(AdminUser).filter_by(supabase_user_id=subject).count() == 1
        assert db.session.query(AuthSession).count() == 2


def test_profile_conflict_fails_closed_without_overwriting_history(app, client, isolated_auth):
    with app.app_context():
        existing = db.session.query(AdminUser).filter_by(username='admin').one()
        subject = existing.id
    isolated_auth.users['new@example.test'] = {'id': str(subject), 'email_confirmed_at': '2026-01-01T00:00:00Z'}
    isolated_auth.passwords['new@example.test'] = 'signup-password'
    assert sign_in(client, 'new@example.test', 'signup-password').status_code == 503
    with app.app_context():
        assert db.session.query(AdminUser).count() == 1
        assert db.session.query(AuthSession).count() == 0
        assert db.session.get(AdminUser, subject).supabase_user_id == AUTH_USER_ID


@pytest.mark.parametrize('change', ['banned', 'deleted'])
def test_supabase_account_status_removes_access_to_existing_session(app, admin_client, isolated_auth, change):
    if change == 'banned':
        isolated_auth.users['admin@example.test']['banned_until'] = '2999-01-01T00:00:00Z'
    else:
        del isolated_auth.users['admin@example.test']
    assert admin_client.get('/admin/consignments/list', headers={'Accept': 'application/json'}).status_code == 401
    with app.app_context():
        assert db.session.query(AuthSession).count() == 0
        assert db.session.query(AdminUser).count() == 1  # Keep ownership and audit references.
