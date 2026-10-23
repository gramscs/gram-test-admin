"""Authentication upgrades are atomic, repeatable and preserve business IDs."""
import importlib
from pathlib import Path

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.auth_sessions import AuthSession
from app.models import db
from database.models import AdminUser
from migrations.shipment_v1 import RUNS
from migrations.storage import MigrationError

migration = importlib.import_module('migrations.004_supabase_authentication')


def legacy_schema(app):
    with app.app_context():
        db.session.remove()
        engine = db.engine
        with engine.begin() as connection:
            users = connection.execute(text('SELECT id, username, role, active FROM admin_users')).mappings().all()
            AuthSession.__table__.drop(connection)
            connection.exec_driver_sql('ALTER TABLE admin_users DROP COLUMN supabase_user_id')
        return engine, [dict(user) for user in users]


def sql_for(connection):
    schema = connection.execute(text('SELECT current_schema()')).scalar_one()
    return migration.SQL_FILE.read_text().replace('SET LOCAL search_path = public;', f'SET LOCAL search_path = "{schema}";')


def test_preflight_and_native_and_sql_upgrades_preserve_users_and_history(app):
    engine, before = legacy_schema(app)
    with app.app_context():
        preflight = migration.migrate(engine)
        assert preflight['add_identity_column'] and preflight['create_auth_sessions']
        assert not inspect(engine).has_table('admin_auth_sessions')
        assert migration.migrate(engine, apply=True)['status'] == 'applied'
        assert migration.migrate(engine, apply=True)['status'] == 'already_applied'
        with engine.connect() as connection:
            script = sql_for(connection)
            connection.rollback()
            connection.exec_driver_sql(script, execution_options={'no_parameters': True})
            connection.commit()
        assert migration.migrate(engine, apply=True)['status'] == 'already_applied'
        users = db.session.query(AdminUser).all()
        assert [dict(id=user.id, username=user.username, role=user.role, active=user.active) for user in users] == before
        assert all(user.supabase_user_id is None for user in users)  # No implicit account grants.
        record = db.session.execute(select(RUNS).where(RUNS.c.version == migration.VERSION)).mappings().one()
        assert record['counts']['admin_users']['preserved_rows'] == len(before)
        assert record['target_digest'] == migration.REVISION_DIGEST
        assert db.session.execute(text("SELECT relrowsecurity FROM pg_class WHERE oid='admin_auth_sessions'::regclass")).scalar_one()
        assert db.session.query(AuthSession).count() == 0
        assert app.test_cli_runner().invoke(args=['check-db']).exit_code == 0


def test_sql_editor_upgrade_and_history_failure_roll_back_together(app):
    engine, before = legacy_schema(app)
    with app.app_context():
        with engine.begin() as connection:
            script = sql_for(connection)
            connection.exec_driver_sql("""CREATE FUNCTION reject_auth_history() RETURNS trigger LANGUAGE plpgsql AS
                $$ BEGIN RAISE EXCEPTION 'Simulated authentication ledger failure'; END $$""")
            connection.exec_driver_sql("""CREATE TRIGGER reject_auth_history BEFORE INSERT ON admin_migration_runs
                FOR EACH ROW EXECUTE FUNCTION reject_auth_history()""")
        with engine.connect() as connection:
            with pytest.raises(DBAPIError, match='Simulated authentication ledger failure'):
                connection.exec_driver_sql(script, execution_options={'no_parameters': True})
            connection.rollback()
        inspector = inspect(engine)
        assert not inspector.has_table('admin_auth_sessions')
        assert 'supabase_user_id' not in {column['name'] for column in inspector.get_columns('admin_users')}
        with engine.begin() as connection:
            assert [dict(row) for row in connection.execute(text('SELECT id, username, role, active FROM admin_users')).mappings()] == before
            connection.exec_driver_sql('DROP TRIGGER reject_auth_history ON admin_migration_runs')
            connection.exec_driver_sql('DROP FUNCTION reject_auth_history()')
        with engine.connect() as connection:
            connection.exec_driver_sql(script, execution_options={'no_parameters': True})
            connection.commit()
        assert migration.migrate(engine)['recorded']
        assert app.test_cli_runner().invoke(args=['check-db']).exit_code == 0


def test_recorded_migration_with_missing_session_table_stops_instead_of_resetting(app):
    with app.app_context():
        assert migration.migrate(db.engine, apply=True)['status'] == 'applied'
        AuthSession.__table__.drop(db.engine)
        with pytest.raises(MigrationError, match='structure is missing'):
            migration.migrate(db.engine, apply=True)
        assert not inspect(db.engine).has_table('admin_auth_sessions')


def test_incompatible_existing_session_table_is_not_replaced(app):
    engine, _ = legacy_schema(app)
    with app.app_context():
        with engine.begin() as connection:
            connection.exec_driver_sql('CREATE TABLE admin_auth_sessions (unrelated text NOT NULL)')
            connection.exec_driver_sql("INSERT INTO admin_auth_sessions VALUES ('preserve this')")
        with pytest.raises(DBAPIError):
            migration.migrate(engine, apply=True)
        with engine.connect() as connection:
            assert connection.exec_driver_sql('SELECT unrelated FROM admin_auth_sessions').scalar_one() == 'preserve this'
        assert 'supabase_user_id' not in {column['name'] for column in inspect(engine).get_columns('admin_users')}


def test_one_supabase_identity_cannot_be_linked_to_two_app_accounts(app):
    with app.app_context():
        existing = db.session.query(AdminUser).one()
        db.session.add(AdminUser(username='other', supabase_user_id=existing.supabase_user_id))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
        assert db.session.query(AdminUser).count() == 1
