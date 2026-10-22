#!/usr/bin/env python3
"""Link authorized application users to Supabase Auth; keep sessions private.

Without --apply, checks the configured PostgreSQL database without changing it.
The matching SQL Editor file applies the same transaction and migration history.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import inspect, select, text
from migrations.shipment_v1 import RUNS, configure_wait_limits
from migrations.storage import MigrationError

VERSION = '004_supabase_authentication'
REVISION_DIGEST = hashlib.sha256(b'Supabase Auth: nullable unique admin_users.supabase_user_id UUID; private encrypted admin_auth_sessions v1').hexdigest()
SQL_FILE = Path(__file__).with_name('sql') / '004_supabase_authentication.sql'


def migrate(engine, *, apply=False):
    if engine.dialect.name != 'postgresql':
        raise MigrationError('Use the existing PostgreSQL database configured by DATABASE_URL.')
    with engine.begin() as connection:
        if not apply:
            connection.execute(text('SET TRANSACTION READ ONLY'))
        configure_wait_limits(connection)
        inspector = inspect(connection)
        schema = connection.execute(text('SELECT current_schema()')).scalar_one()
        if not inspector.has_table('admin_users') or not inspector.has_table(RUNS.name):
            raise MigrationError('Expected the existing application database and migration ledger. No tables were created.')
        previous = connection.execute(select(RUNS).where(RUNS.c.version == VERSION)).mappings().first()
        if previous and previous['target_digest'] != REVISION_DIGEST:
            raise MigrationError('Migration 004 differs from its recorded revision. Use the original migration.')
        column = next((item for item in inspector.get_columns('admin_users') if item['name'] == 'supabase_user_id'), None)
        sessions = inspector.has_table('admin_auth_sessions')
        if previous and (not column or not sessions):
            raise MigrationError('Migration 004 is recorded but its structure is missing. Review the database.')
        users = connection.execute(text('SELECT count(*) FROM admin_users')).scalar_one()
        if apply:
            script = SQL_FILE.read_text().split('-- Verification queries', 1)[0]
            script = script.replace('BEGIN;\n', '', 1).replace('\nCOMMIT;', '')
            quoted = connection.dialect.identifier_preparer.quote(schema)
            script = script.replace('SET LOCAL search_path = public;', f'SET LOCAL search_path = {quoted};')
            connection.exec_driver_sql(script, execution_options={'no_parameters': True})
        return dict(version=VERSION, status=('already_applied' if previous else 'applied') if apply else 'ready',
                    recorded=previous is not None or apply, existing_admin_users=users,
                    add_identity_column=column is None, create_auth_sessions=not sessions)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    engine = None
    try:
        from app import create_app
        from app.models import db
        app = create_app({'AUTO_CREATE_TABLES': False})
        with app.app_context():
            engine = db.engine
            result = migrate(engine, apply=args.apply)
        print(json.dumps(result, indent=2))
        print('Committed and verified. Link authorized Auth users manually next.' if args.apply
              else 'Read-only preflight passed. Add --apply to upgrade the configured database.')
        return 0
    except MigrationError as error:
        print(f'Migration stopped: {error}', file=sys.stderr)
        return 1
    except Exception:
        print('Migration could not be confirmed. Check database connectivity, permissions and schema. '
              'No credentials were printed.', file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
