#!/usr/bin/env python3
"""One-run schema + legacy-data migration. No Flask startup required.

Run from the repository: python migrations/001_shipment_database.py --apply
Without --apply, performs the complete preflight without database writes.
"""

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from sqlalchemy.engine import make_url

from migrations.shipment_v1 import migrate, migration_engine
from migrations.storage import MigrationError, Storage


def database_url(value, *, source=False):
    value = value.strip()
    if value.startswith("postgres://"):
        value = "postgresql://" + value[len("postgres://"):]
    if any(marker in value.lower() for marker in ("[your-password]", "[your_password]", "pooler_host")):
        raise MigrationError("Replace DATABASE_URL placeholders securely before migration.")
    try:
        url = make_url(value)
    except Exception:
        raise MigrationError("A database connection setting is invalid.") from None
    if url.get_backend_name() not in {"postgresql", "sqlite"}:
        raise MigrationError("Use a PostgreSQL or SQLite database connection.")
    if url.get_backend_name() == "sqlite":
        if url.database in (None, "", ":memory:"):
            raise MigrationError("The migration CLI requires a persistent SQLite database file.")
        path = Path(url.database)
        path = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
        if source and not path.is_file():
            raise MigrationError("The source SQLite database file does not exist.")
        if not source and not path.parent.is_dir():
            raise MigrationError("Create the target SQLite database folder before migration.")
        url = url.set(database=str(path))
    elif url.host and (url.host.endswith(".supabase.co") or url.host.endswith(".pooler.supabase.com")) and "sslmode" not in url.query:
        url = url.update_query_dict({"sslmode": "require"})
    return url


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Apply the schema and data in one transaction; otherwise preflight only.")
    parser.add_argument("--source-local", type=Path, help="Import a local SQLite file into the configured target instead of importing in place.")
    parser.add_argument("--uploads-dir", type=Path, default=ROOT / "instance" / "uploads", help="Existing local uploads folder (never moved or rewritten).")
    args = parser.parse_args(argv)
    load_dotenv(ROOT / ".env", override=False)
    engine = source_engine = None
    try:
        target_setting = os.getenv("DATABASE_URL", "").strip() or f"sqlite:///{ROOT / 'instance' / 'admin.db'}"
        source_setting = os.getenv("MIGRATION_SOURCE_DATABASE_URL", "").strip() or target_setting
        if args.source_local:
            if os.getenv("MIGRATION_SOURCE_DATABASE_URL", "").strip():
                raise MigrationError("Choose either --source-local or MIGRATION_SOURCE_DATABASE_URL, not both.")
            source_setting = f"sqlite:///{args.source_local.resolve()}"
        target_url = database_url(target_setting)
        source_url = database_url(source_setting, source=True)
        engine = migration_engine(target_url)
        if source_url != target_url:
            source_engine = migration_engine(source_url)
        supabase = None
        supabase_url, supabase_key = os.getenv("SUPABASE_URL", "").strip(), os.getenv("SUPABASE_KEY", "").strip()
        if supabase_url and supabase_key:
            from supabase import create_client
            supabase = create_client(supabase_url, supabase_key)
        storage = Storage(args.uploads_dir, supabase, os.getenv("SUPABASE_BUCKET", "pod-uploads"))
        result = migrate(engine, storage, source_engine=source_engine,
                         admin_username=os.getenv("ADMIN_USERNAME", "admin"), dry_run=not args.apply)
        print(json.dumps(result, indent=2))
        if result["status"] == "ready":
            print("Preflight passed. No database data was changed. Add --apply to run the migration.")
        elif result["status"] == "applied":
            print("Migration committed. Legacy tables and upload bytes were retained. The dashboard still uses its legacy tables until backend cutover.")
        else:
            print("Migration already applied and verified. No duplicate records were added.")
        return 0
    except MigrationError as error:
        print(f"Migration stopped: {error}", file=sys.stderr)
        return 1
    except Exception:
        # Driver/storage exceptions can contain SQL values, usernames, URLs and
        # passwords. Never print their raw messages or tracebacks to the CLI.
        print("Migration failed; this run's database writes were rolled back. Check database permissions/connectivity and source data. No credentials were printed.", file=sys.stderr)
        return 1
    finally:
        if source_engine is not None:
            source_engine.dispose()
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
