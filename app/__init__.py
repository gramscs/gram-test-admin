"""Standalone admin application; no dependency on the public website."""

import os
from pathlib import Path

import click
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, url_for
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.models import db

PROJECT_ROOT = Path(__file__).resolve().parent.parent
limiter = Limiter(key_func=get_remote_address)


def create_app(test_config=None):
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    overrides = dict(test_config or {})
    instance_path = Path(overrides.pop("INSTANCE_PATH", PROJECT_ROOT / "instance")).resolve()
    instance_path.mkdir(parents=True, exist_ok=True)
    app = Flask(__name__, instance_path=str(instance_path))
    production = os.getenv("FLASK_ENV", "development").lower() == "production"
    secret_key = os.getenv("SECRET_KEY", "").strip()
    database_url = os.getenv("DATABASE_URL", "").strip()
    if production and not (secret_key or overrides.get("SECRET_KEY")):
        raise RuntimeError("Set SECRET_KEY before starting in production.")
    if production and not os.getenv("ADMIN_PASSWORD_HASH", "").strip():
        raise RuntimeError("Set ADMIN_PASSWORD_HASH before starting in production.")
    if database_url.startswith("postgres://"):
        database_url = "postgresql://" + database_url[len("postgres://"):]
    if production and not (database_url or overrides.get("SQLALCHEMY_DATABASE_URI")):
        raise RuntimeError("Set DATABASE_URL before starting in production.")
    database_url = database_url or f"sqlite:///{instance_path / 'admin.db'}"
    url = make_url(overrides.get("SQLALCHEMY_DATABASE_URI", database_url))
    is_postgres = url.get_backend_name() == "postgresql"
    if is_postgres and url.password == "[YOUR-PASSWORD]":
        raise RuntimeError("Replace the password placeholder in DATABASE_URL through your secure environment settings.")
    is_supabase = bool(url.host) and (
        url.host.endswith(".supabase.co") or url.host.endswith(".pooler.supabase.com")
    )
    if is_postgres and is_supabase and "sslmode" not in url.query:
        url = url.update_query_dict({"sslmode": "require"})
    if url.get_backend_name() == "sqlite" and url.database not in (None, "", ":memory:"):
        database_path = Path(url.database)
        if not database_path.is_absolute():
            database_path = PROJECT_ROOT / database_path
        database_path = database_path.resolve()
        database_path.parent.mkdir(parents=True, exist_ok=True)
        url = url.set(database=str(database_path))

    app.config.update(
        SECRET_KEY=secret_key or "dev-local-secret-key",
        SQLALCHEMY_DATABASE_URI=url.render_as_string(hide_password=False),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=production,
        RATELIMIT_STORAGE_URI=os.getenv("RATELIMIT_STORAGE_URI") or os.getenv("REDIS_URL") or "memory://",
        RATELIMIT_HEADERS_ENABLED=True,
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,
        AUTO_CREATE_TABLES=os.getenv("AUTO_CREATE_TABLES", "false" if production or is_postgres else "true").lower() == "true",
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
    # Use the normalized URI even when it was supplied through test_config.
    app.config["SQLALCHEMY_DATABASE_URI"] = url.render_as_string(hide_password=False)
    db.init_app(app)
    limiter.init_app(app)

    from app.admin import admin_bp
    app.register_blueprint(admin_bp)
    from app.seed_data import seed_demo
    app.cli.add_command(seed_demo)

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
        db.create_all()
        click.echo("Database tables created.")

    @app.cli.command("upgrade-db")
    def upgrade_db():
        """Add shipment fields to existing tables without deleting records."""
        from app.schema_upgrade import upgrade_consignment_fields
        changed = upgrade_consignment_fields(db.engine)
        db.create_all()
        click.echo("Database upgraded. Existing records preserved. " + ("Added: " + ", ".join(changed) if changed else "Schema already current."))

    @app.cli.command("check-db")
    def check_db():
        """Test the connection and required table structure without changing data."""
        from sqlalchemy import inspect
        try:
            db.session.execute(text("SELECT 1"))
            inspector = inspect(db.engine)
            missing = []
            for table in db.metadata.sorted_tables:
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
            raise click.ClickException("Missing required database structure: " + ", ".join(missing))
        click.echo("All required admin tables and columns are present. No data was changed.")

    @app.cli.command("repair-consignment-schema")
    def repair_schema():
        """Add missing shipment columns to an existing PostgreSQL database."""
        if db.engine.dialect.name != "postgresql":
            raise click.ClickException("This command is only for PostgreSQL.")
        from app.db_maintenance import ensure_consignment_columns
        ensure_consignment_columns(app.config["SQLALCHEMY_DATABASE_URI"], app.logger)
        click.echo("Consignment columns checked.")

    if app.config["AUTO_CREATE_TABLES"]:
        with app.app_context():
            if db.engine.dialect.name == "sqlite":
                from app.schema_upgrade import upgrade_consignment_fields
                upgrade_consignment_fields(db.engine)
            db.create_all()

    return app
