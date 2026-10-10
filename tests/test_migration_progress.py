"""Migration progress and bounded waits, including real PostgreSQL lock contention."""
import os

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, insert, select, text

from migrations import shipment_v1
from migrations.shipment_v1 import insert_batches, migrate
from migrations.storage import MigrationError
from test_shipment_migration import populated, legacy_engine
from test_complete_database_import import disposable_engine


def test_batches_preserve_omitted_defaults_and_report_committed_work(populated, tmp_path):
    source, storage, _ = populated
    with disposable_engine(source.dialect.name, tmp_path / 'batch.db') as target:
        table = Table('batch_example', MetaData(), Column('id', Integer, primary_key=True),
            Column('name', String, server_default='default name'))
        rows = [{'id': index, 'name': str(index)} for index in range(401)] + [{'id': 401}]
        progress = []
        with target.begin() as connection:
            table.create(connection)
            insert_batches(connection, table, rows, progress.append)
            actual = connection.execute(select(table).order_by(table.c.id)).mappings().all()
            assert len(actual) == 402 and actual[-1]['name'] == 'default name'
        assert len(progress) == 4 and '402/402' in progress[-1]


def test_progress_precedes_writes_and_reaches_commit(populated, tmp_path):
    source, storage, _ = populated
    with disposable_engine(source.dialect.name, tmp_path / 'progress.db') as target:
        progress = []
        migrate(target, storage, source_engine=source, fresh_target=True, progress=progress.append)
        assert progress.index('Connecting to the destination database...') < progress.index('Creating all ten model tables, indexes and integrity rules...')
        assert any(message.startswith('Imported consignments:') for message in progress)
        assert progress[-1] == 'Committing the database transaction...'


def test_postgresql_lock_wait_is_bounded_and_target_is_unchanged(populated, tmp_path, monkeypatch):
    source, storage, _ = populated
    if source.dialect.name != 'postgresql':
        pytest.skip('Requires the disposable PostgreSQL test server.')
    real_limits = shipment_v1.configure_wait_limits
    monkeypatch.setattr(shipment_v1, 'configure_wait_limits', lambda connection: real_limits(connection, lock_timeout_ms=100, statement_timeout_ms=1000))
    with disposable_engine('postgresql', tmp_path / 'blocked.db') as target:
        with target.begin() as blocker:
            blocker.execute(text('SELECT pg_advisory_xact_lock(723910042001)'))
            with pytest.raises(MigrationError, match='lock'):
                migrate(target, storage, source_engine=source, fresh_target=True)
        from sqlalchemy import inspect
        assert not inspect(target).get_table_names()
