import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest
import httpx
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app
from app.models import db
from auth_support import AUTH_USER_ID, FakeAuth, sign_in, sign_out


class MemoryBucket:
    def __init__(self, objects, name):
        self.objects = objects
        self.name = name

    def upload(self, path, content, options=None):
        if hasattr(content, "read"):
            content = content.read()
        self.objects[(self.name, path)] = (bytes(content), dict(options or {}))
        return {"Key": path}

    def download(self, path):
        try:
            return self.objects[(self.name, path)][0]
        except KeyError:
            raise FileNotFoundError(path) from None

    def remove(self, paths):
        removed = []
        for path in paths:
            value = self.objects.pop((self.name, path), None)
            if value is not None:
                removed.append({"name": path})
        return removed

    def list(self, folder="", options=None):
        prefix = f"{folder.rstrip('/')}/" if folder else ""
        entries = []
        for bucket, path in self.objects:
            if bucket != self.name or not path.startswith(prefix):
                continue
            remainder = path[len(prefix):]
            if not remainder or "/" in remainder:
                continue
            content, metadata = self.objects[(bucket, path)]
            entries.append({"name": remainder, "metadata": {"size": len(content), **metadata}})
        options = options or {}
        offset, limit = options.get("offset", 0), options.get("limit", len(entries))
        return sorted(entries, key=lambda entry: entry["name"])[offset:offset + limit]


class MemoryStorage:
    def __init__(self):
        self.objects = {}

    def from_(self, name):
        return MemoryBucket(self.objects, name)

    def get_bucket(self, name):
        return {"name": name}


class MemorySupabase:
    def __init__(self):
        self.storage = MemoryStorage()


@pytest.fixture(autouse=True)
def isolated_storage(monkeypatch):
    client = MemorySupabase()
    monkeypatch.setenv("SUPABASE_URL", "https://storage-test.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "test-service-key")
    monkeypatch.setenv("SUPABASE_MIS_BUCKET", "mis-reports")
    import supabase
    monkeypatch.setattr(supabase, "create_client", lambda url, key: client)
    def blocked_http(*args, **kwargs):
        raise AssertionError('Tests must mock HTTP clients; external services are not test storage.')
    async def blocked_async_http(*args, **kwargs):
        blocked_http()
    monkeypatch.setattr(httpx.Client, 'send', blocked_http)
    monkeypatch.setattr(httpx.AsyncClient, 'send', blocked_async_http)
    return client


@pytest.fixture(autouse=True)
def isolated_auth(monkeypatch):
    import requests
    remote = FakeAuth()
    monkeypatch.setenv('SUPABASE_ANON_KEY', 'test-anon-key')
    monkeypatch.setenv('SECRET_KEY', 'test-session-key-not-for-deployment')
    monkeypatch.setattr(requests, 'request', remote.request)
    return remote


@pytest.fixture
def app(monkeypatch, isolated_storage):
    test_url_value = os.getenv("ER_TEST_DATABASE_URL", "").strip()
    if not test_url_value:
        pytest.fail("Set ER_TEST_DATABASE_URL to a dedicated PostgreSQL test database; tests never use local storage or DATABASE_URL.")
    test_url = make_url(test_url_value)
    if test_url.get_backend_name() != "postgresql" or "test" not in (test_url.database or "").lower():
        pytest.fail("ER_TEST_DATABASE_URL must point to a dedicated PostgreSQL database with 'test' in its name.")
    active_url = os.getenv("DATABASE_URL", "").strip()
    if active_url:
        active = make_url(active_url)
        if (active.host, active.port, active.database) == (test_url.host, test_url.port, test_url.database):
            pytest.fail("ER_TEST_DATABASE_URL must not point to the active application database.")
    schema = "test_" + uuid4().hex
    schema_admin = create_engine(test_url, pool_pre_ping=True)
    with schema_admin.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    connect_args = {"connect_timeout": 10, "options": f"-csearch_path={schema},public"}
    database_engine = create_engine(test_url, pool_pre_ping=True, connect_args=connect_args)
    from database.models import Base
    Base.metadata.create_all(database_engine)
    from app.auth_sessions import AuthBase
    AuthBase.metadata.create_all(database_engine)
    from migrations.shipment_v1 import LEDGER
    LEDGER.create_all(database_engine)
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", test_url.render_as_string(hide_password=False))
    application = create_app({
        "TESTING": True,
        "SECRET_KEY": "standalone-test-key",
        "AUTO_CREATE_TABLES": True,
        "RATELIMIT_ENABLED": False,
        "SQLALCHEMY_ENGINE_OPTIONS": {
            "pool_pre_ping": True,
            "connect_args": connect_args,
        },
    })
    from database.models import AdminUser
    with application.app_context():
        db.session.add(AdminUser(username="admin", role="administrator", active=True, supabase_user_id=AUTH_USER_ID))
        db.session.commit()
    yield application
    with application.app_context():
        db.session.remove()
        db.engine.dispose()
    database_engine.dispose()
    with schema_admin.begin() as connection:
        connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
    schema_admin.dispose()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def admin_client(client):
    response = sign_in(client)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/dashboard")
    return client
