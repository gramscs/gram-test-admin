"""Isolated PostgreSQL-backed app for browser tests."""
import os
import sys
from datetime import date
from pathlib import Path
from uuid import uuid4


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from auth_support import AUTH_USER_ID, FakeAuth


class BrowserBucket:
    def __init__(self, objects, name):
        self.objects, self.name = objects, name

    def list(self, folder="", options=None):
        return []

    def upload(self, path, content, options=None):
        self.objects[self.name, path] = bytes(content)
        return {"Key": path}

    def download(self, path):
        return self.objects[self.name, path]

    def info(self, path):
        if (self.name, path) not in self.objects:
            raise FileNotFoundError(path)
        return {"name": path}

    def remove(self, paths):
        for path in paths:
            self.objects.pop((self.name, path), None)
        return []


class BrowserClient:
    def __init__(self):
        self.objects = {}
        self.storage = self

    def from_(self, name):
        return BrowserBucket(self.objects, name)

    def get_bucket(self, name):
        return {"name": name}


def main():
    setting = os.getenv("ER_TEST_DATABASE_URL", "").strip()
    if not setting:
        raise RuntimeError("Set ER_TEST_DATABASE_URL to a dedicated PostgreSQL test database.")
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    url = make_url(setting)
    if url.get_backend_name() != "postgresql" or "test" not in (url.database or "").lower():
        raise RuntimeError("ER_TEST_DATABASE_URL must point to a dedicated PostgreSQL test database.")
    schema = "browser_" + uuid4().hex
    admin_engine = create_engine(url, pool_pre_ping=True)
    with admin_engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    app_url = url.update_query_dict({"options": f"-csearch_path={schema},public"})
    engine = create_engine(app_url, pool_pre_ping=True)
    from sqlalchemy.orm import Session
    from database.models import AdminUser, Base, Company, Consignment
    from app.er_adapter import prepare_backend

    Base.metadata.create_all(engine)
    from app.auth_sessions import AuthBase
    AuthBase.metadata.create_all(engine)
    prepare_backend(engine)
    with Session(engine) as session:
        session.add(AdminUser(username="admin", role="administrator", active=True, supabase_user_id=AUTH_USER_ID))
        company = Company(name="System Test Client", registered_address="Mumbai office")
        session.add(company)
        session.add_all(Consignment(
            identifier_value=f"SYS-{index:03}",
            company=company,
            identifier_type="LRN",
            pieces=2,
            weight=10,
            current_status=["In Transit", "Delivered", "Pickup Scheduled"][index % 3],
            pickup_address_snapshot="Delhi warehouse",
            drop_address_snapshot="Mumbai store",
            planned_pickup_date=date(2026, 10, 1),
            expected_delivery_date=date(2026, 10, 2),
        ) for index in range(60))
        session.commit()
    client = BrowserClient()
    os.environ.update(
        DATABASE_URL=app_url.render_as_string(hide_password=False),
        FLASK_ENV="development",
        SUPABASE_ANON_KEY="test-anon-key",
        SUPABASE_URL="https://browser-test.supabase.co",
        SUPABASE_KEY="browser-test-key",
        SUPABASE_MIS_BUCKET="mis-reports",
    )
    import requests
    auth_remote = FakeAuth('https://browser-test.supabase.co')
    auth_remote.passwords['admin@example.test'] = 'system-test-only'
    requests.request = auth_remote.request
    import supabase
    supabase.create_client = lambda *_: client
    from app import create_app
    from app.models import db

    application = create_app({
        "AUTO_CREATE_TABLES": False,
        "ADMIN_DATABASE_MODEL": "er",
        "SECRET_KEY": "isolated-browser-test-key",
        "RATELIMIT_ENABLED": False,
    })
    print("Disposable PostgreSQL browser app: 60 test shipments.", flush=True)
    try:
        application.run(host="127.0.0.1", port=int(os.getenv("SYSTEM_TEST_PORT", "10003")), debug=False)
    finally:
        with application.app_context():
            db.session.remove()
            db.engine.dispose()
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin_engine.dispose()


if __name__ == "__main__":
    main()
