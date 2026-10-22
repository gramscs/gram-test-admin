"""Standalone admin application; no dependency on the public website."""

import os
from datetime import timedelta
from pathlib import Path

import click
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, url_for
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.models import db
from app.upload_request import MemoryUploadRequest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
limiter = Limiter(key_func=get_remote_address)


def create_app(test_config=None):
    env_file = PROJECT_ROOT / ".env"
    example_file = PROJECT_ROOT / ".env.example"
    if env_file.exists():
        load_dotenv(env_file, override=False)
        if not os.getenv("DATABASE_URL", "").strip() and example_file.exists():
            load_dotenv(example_file, override=False)
    elif example_file.exists():
        load_dotenv(example_file, override=False)
    overrides = dict(test_config or {})
    overrides.pop("INSTANCE_PATH", None)
    app = Flask(__name__)
    app.request_class = MemoryUploadRequest
    production = os.getenv("FLASK_ENV", "development").lower() == "production"
    secret_key = os.getenv("SECRET_KEY", "").strip()
    database_url = str(overrides.pop("SQLALCHEMY_DATABASE_URI", "")).strip() or os.getenv("DATABASE_URL", "").strip()
    if production and not (secret_key or overrides.get("SECRET_KEY")):
        raise RuntimeError("Set SECRET_KEY before starting in production.")
    if production and not (overrides.get('SUPABASE_ANON_KEY') or os.getenv('SUPABASE_ANON_KEY', '').strip()):
        raise RuntimeError('Set SUPABASE_ANON_KEY before starting in production.')
    if database_url.startswith("postgres://"):
        database_url = "postgresql://" + database_url[len("postgres://"):]
    placeholder_markers = ("[YOUR_PASSWORD]", "[YOUR-PASSWORD]", "POOLER_HOST", "your_password")
    if not database_url:
        raise RuntimeError("Set DATABASE_URL to a PostgreSQL database. Local database storage is disabled.")
    if any(marker.lower() in database_url.lower() for marker in placeholder_markers):
        raise RuntimeError("Replace the password placeholder in DATABASE_URL.")
    url = make_url(database_url)
    is_postgres = url.get_backend_name() == "postgresql"
    if not is_postgres:
        raise RuntimeError("Only PostgreSQL database connections are supported. Local database storage is disabled.")
    if is_postgres and url.password == "[YOUR-PASSWORD]":
        raise RuntimeError("Replace the password placeholder in DATABASE_URL through your secure environment settings.")
    is_supabase = bool(url.host) and (
        url.host.endswith(".supabase.co") or url.host.endswith(".pooler.supabase.com")
    )
    if is_postgres and is_supabase and "sslmode" not in url.query:
        url = url.update_query_dict({"sslmode": "require"})
    if not (secret_key or overrides.get('SECRET_KEY')):
        raise RuntimeError('Set SECRET_KEY to a secure random value before starting.')
    app.config.update(
        SECRET_KEY=secret_key,
        SUPABASE_URL=os.getenv('SUPABASE_URL', '').strip().rstrip('/'),
        SUPABASE_ANON_KEY=os.getenv('SUPABASE_ANON_KEY', '').strip(),
        SQLALCHEMY_DATABASE_URI=url.render_as_string(hide_password=False),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=production,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        SESSION_REFRESH_EACH_REQUEST=False,
        RATELIMIT_STORAGE_URI=os.getenv("RATELIMIT_STORAGE_URI") or os.getenv("REDIS_URL") or "memory://",
        RATELIMIT_HEADERS_ENABLED=True,
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,
        AUTO_CREATE_TABLES=os.getenv("AUTO_CREATE_TABLES", "false").lower() == "true",
        ADMIN_DATABASE_MODEL="er",
    )
    if is_postgres:
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
            "pool_pre_ping": True,
            "pool_recycle": 180,
            "pool_size": 3,
            "max_overflow": 2,
            "pool_timeout": 30,
            "connect_args": {"connect_timeout": 10},
        }
    app.config.update(overrides)
    # Use the validated PostgreSQL URI.
    app.config["SQLALCHEMY_DATABASE_URI"] = url.render_as_string(hide_password=False)
    db.init_app(app)
    limiter.init_app(app)

    from app.admin import admin_bp
    app.register_blueprint(admin_bp)
    from app.admin.auth import csrf_token
    app.context_processor(lambda: {'auth_csrf_token': csrf_token})
    from app.admin.document_lifecycle import purge_removed_documents
    app.cli.add_command(purge_removed_documents)

    @app.get("/")
    def index():
        return redirect(url_for("admin.dashboard"))

    @app.get("/admin")
    def admin_index():
        return redirect(url_for("admin.dashboard"))

    @app.get("/health")
    def health():
        return jsonify(status="ok", application="gram-scs-admin")

    @app.get("/health/db")
    def database_health():
        try:
            db.session.execute(text("SELECT 1"))
            return jsonify(status="ok", database=db.engine.dialect.name)
        except Exception:
            db.session.rollback()
            app.logger.exception("Database health check failed")
            return jsonify(status="error", message="Database connection failed"), 503

    @app.cli.command("init-db")
    def init_db():
        """Create missing tables; does not import or replace existing records."""
        from app.orm import using_er
        if using_er():
            from app.er_adapter import prepare_backend
            prepare_backend(db.engine)
            click.echo("ER backend ready. Existing model tables and data preserved.")
            return
        db.create_all()
        click.echo("Database tables created.")

    @app.cli.command("upgrade-db")
    def upgrade_db():
        """Add application support tables without replacing existing records."""
        from app.orm import using_er
        if not using_er():
            raise click.ClickException("Only the PostgreSQL shipment ER schema is supported.")
        from app.er_adapter import prepare_backend
        prepare_backend(db.engine)
        click.echo("PostgreSQL support tables are ready. Existing records preserved.")

    @app.cli.command("check-db")
    def check_db():
        """Test the connection and required table structure without changing data."""
        from sqlalchemy import inspect
        try:
            db.session.execute(text("SELECT 1"))
            inspector = inspect(db.engine)
            missing = []
            from app.orm import tables
            from app.auth_sessions import AuthSession
            for table in [*tables(), AuthSession.__table__]:
                if not inspector.has_table(table.name):
                    missing.append(f"table {table.name}")
                    continue
                columns = {column["name"] for column in inspector.get_columns(table.name)}
                missing.extend(f"column {table.name}.{column.name}" for column in table.columns if column.name not in columns)
        except Exception:
            db.session.rollback()
            # Connection errors can contain sensitive connection details.
            raise click.ClickException("Database connection failed. Check DATABASE_URL, network access, and Supabase project status.") from None
        click.echo(f"Connected to {db.engine.dialect.name}.")
        if missing:
            raise click.ClickException("Missing required database structure: " + ", ".join(missing)
                + '. Supabase authentication requires migration 004_supabase_authentication.')
        document_types = next((item['sqltext'] for item in inspector.get_check_constraints('shipment_documents')
                               if item['name'] == 'ck_shipment_documents_valid_type'), '')
        if "'LR'" not in document_types:
            raise click.ClickException('Lorry Receipt documents require migration 003. Run python migrations/003_lorry_receipts.py --apply against this database.')
        click.echo("All required admin tables and columns are present. No data was changed.")

    @app.cli.command('check-auth')
    def check_auth():
        """Read Supabase's Auth settings without signing in or changing users."""
        from app.supabase_auth import AuthRejected, AuthUnavailable, check_configuration
        try:
            check_configuration()
        except AuthRejected:
            raise click.ClickException('Supabase rejected the Auth API key. Check SUPABASE_ANON_KEY.') from None
        except AuthUnavailable as error:
            raise click.ClickException(str(error)) from None
        click.echo('Supabase Auth is reachable and its Email provider is enabled. No users or sessions were created.')

    @app.cli.command("check-storage")
    def check_storage():
        """Check document storage configuration and bucket reads without uploading."""
        from app.admin.consignment_controller import _get_supabase_client
        try:
            client = _get_supabase_client()
        except RuntimeError as error:
            raise click.ClickException(str(error)) from None
        if client is None:
            raise click.ClickException('Set SUPABASE_URL and SUPABASE_KEY. Local file storage is disabled.')
        click.echo('Storage: Supabase.')
        buckets = {
            'POD': os.getenv('SUPABASE_BUCKET', '').strip() or 'pod-uploads',
            'Invoice': os.getenv('SUPABASE_INVOICE_BUCKET', '').strip() or 'invoice-uploads',
        }
        mis_bucket = os.getenv('SUPABASE_MIS_BUCKET', '').strip() or 'mis-reports'
        if mis_bucket in buckets.values():
            raise click.ClickException('SUPABASE_MIS_BUCKET must be separate from the POD and invoice buckets. No files were changed.')
        lr_bucket = os.getenv('SUPABASE_LR_BUCKET', '').strip() or 'lr-uploads'
        if lr_bucket == mis_bucket:
            raise click.ClickException('SUPABASE_LR_BUCKET must be separate from the MIS bucket.')
        buckets['LR'] = lr_bucket
        buckets['MIS'] = mis_bucket
        failed = []
        for kind, bucket in buckets.items():
            namespace = 'mis_reports' if kind == 'MIS' else 'consignments'
            try:
                client.storage.get_bucket(bucket)
                client.storage.from_(bucket).list(namespace, {'limit': 1})
            except Exception:
                click.echo(f'{kind}: cannot read {bucket}/{namespace}. Check the project, bucket, key and Storage permissions.')
                failed.append(bucket)
            else:
                click.echo(f'{kind}: readable at {bucket}/{namespace}.')
        click.echo('No files or database records were changed. Upload permissions were not tested.')
        if failed:
            raise click.ClickException('Storage checks failed. Existing uploaded files were left untouched.')
        click.echo('Read-only storage check passed.')

    if app.config["AUTO_CREATE_TABLES"]:
        with app.app_context():
            from app.orm import using_er
            if using_er():
                from app.er_adapter import prepare_backend
                prepare_backend(db.engine)

    return app
