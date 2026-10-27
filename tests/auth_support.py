"""Supabase HTTP fixtures for isolated unit and browser test applications."""
from types import SimpleNamespace
import base64
import hashlib
from uuid import UUID, uuid4

AUTH_USER_ID = UUID('00000000-0000-4000-8000-000000000001')


class FakeAuth:
    """Mock only the Supabase HTTP boundary, keeping real app session checks."""
    def __init__(self, project_url='https://storage-test.supabase.co'):
        self.project_url = project_url
        self.users = {'admin@example.test': {'id': str(AUTH_USER_ID), 'email_confirmed_at': '2026-01-01T00:00:00Z'}}
        self.passwords = {'admin@example.test': 'test-admin-password'}
        self.access, self.refresh, self.calls = {}, {}, []
        self.counter = 0
        self.confirm_email = True
        self.confirmations = {}

    def confirm(self, email):
        """Model Supabase verifying the email before redirecting with a PKCE code."""
        self.users[email]['email_confirmed_at'] = '2026-01-01T00:00:00Z'
        code = 'confirmation-' + uuid4().hex
        challenge = self.users[email].pop('_challenge')
        self.confirmations[code] = (self.users[email]['id'], challenge)
        return code

    def tokens(self, identifier):
        self.counter += 1
        access, refresh = f'access-{self.counter}', f'refresh-{self.counter}'
        self.access[access], self.refresh[refresh] = identifier, identifier
        return dict(access_token=access, refresh_token=refresh, expires_in=3600)

    def request(self, method, url, *, headers, json=None, **kwargs):
        assert url.startswith(self.project_url + '/auth/v1/'), 'Unexpected external auth request'
        assert headers['apikey'] == 'test-anon-key'
        self.calls.append((method, url, headers, json))
        path = url.split('/auth/v1/', 1)[1]
        status, result = 200, {}
        if path == 'signup':
            email = json['email']
            if email in self.users:
                status, result = 422, {'code': 'user_already_exists'}
            else:
                result = {'id': str(uuid4()), 'email_confirmed_at': None, 'user_metadata': json['data']}
                self.users[email] = result
                self.passwords[email] = json['password']
                self.users[email]['_challenge'] = json['code_challenge']
                if not self.confirm_email:
                    self.users[email]['email_confirmed_at'] = '2026-01-01T00:00:00Z'
                    result = self.tokens(result['id'])
        elif path == 'token?grant_type=password':
            email = json['email']
            if self.passwords.get(email) != json['password'] or email not in self.users:
                status = 400
            else:
                result = self.tokens(self.users[email]['id'])
        elif path == 'token?grant_type=refresh_token':
            subject = self.refresh.pop(json['refresh_token'], None)
            if subject:
                result = self.tokens(subject)
            else:
                status = 400
        elif path == 'token?grant_type=pkce':
            confirmation = self.confirmations.get(json['auth_code'])
            challenge = base64.urlsafe_b64encode(hashlib.sha256(json['code_verifier'].encode()).digest()).rstrip(b'=').decode()
            if confirmation and confirmation[1] == challenge:
                del self.confirmations[json['auth_code']]
                result = self.tokens(confirmation[0])
            else:
                status = 400
        elif path == 'user':
            subject = self.access.get(headers.get('Authorization', '').removeprefix('Bearer '))
            result = next((user for user in self.users.values() if user['id'] == subject), {})
            if not result:
                status = 401
        elif path == 'logout?scope=local':
            access = headers.get('Authorization', '').removeprefix('Bearer ')
            self.access.pop(access, None)
            status = 204
        elif path == 'settings':
            result = {'external': {'email': True}}
        else:
            raise AssertionError('Unexpected Supabase Auth route')
        return SimpleNamespace(status_code=status, json=lambda: result)


def sign_in(client, email='admin@example.test', password='test-admin-password'):
    client.get('/admin/login')
    with client.session_transaction() as state:
        token = state['auth_csrf_token']
    return client.post('/admin/login', data={'email': email, 'password': password, 'csrf_token': token})


def sign_out(client, **kwargs):
    with client.session_transaction() as state:
        token = state['auth_csrf_token']
    return client.post('/admin/logout', data={'csrf_token': token}, **kwargs)
