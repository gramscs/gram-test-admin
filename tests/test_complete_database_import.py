"""Fresh-database imports retain the full ER graph and every source table."""
from contextlib import contextmanager
from datetime import UTC, date, datetime
import hashlib
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, insert, select, text, update
from sqlalchemy.engine import make_url

from database.models import Base
from migrations import shipment_v1
from migrations.shipment_v1 import LEDGER, RECORDS, migrate, migration_engine, target_snapshot
from migrations.storage import MigrationError, Storage


@contextmanager
def disposable_engine(backend, path):
    cleanup = None
    if backend == "sqlite":
        engine = migration_engine(f"sqlite:///{path}")
    else:
        url = make_url(os.environ["ER_TEST_DATABASE_URL"])
        if url.host not in {"localhost", "127.0.0.1"} or url.database != "gram_admin_er_test":
            pytest.fail("Complete import tests require the disposable loopback database.")
        schema = "full_import_" + uuid4().hex
        cleanup = create_engine(url)
        with cleanup.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        engine = migration_engine(url.update_query_dict({"options": f"-csearch_path={schema}"}))
    try:
        yield engine
    finally:
        engine.dispose()
        if cleanup:
            with cleanup.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
            cleanup.dispose()


@pytest.fixture(params=["sqlite"] + (["postgresql"] if os.getenv("ER_TEST_DATABASE_URL") else []))
def complete_source(request, tmp_path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    storage = Storage(uploads)
    moment = datetime(2026, 10, 1, 12, tzinfo=UTC)
    rows = {name: [] for name in Base.metadata.tables}
    user, inactive, company, pickup, drop, shipment, view = [uuid4() for _ in range(7)]
    rows["admin_users"] = [{"id": user, "username": "team-admin", "role": "administrator", "active": True},
        {"id": inactive, "username": "former-operator", "role": "operator", "active": False}]
    rows["companies"] = [{"id": company, "name": "Example client", "registered_address": "Office", "email": "client@example.test", "phone": "1234567890", "active": True}]
    rows["company_locations"] = [{"id": id, "company_id": company, "kind": kind, "label": kind.title(),
        "address": kind + " warehouse", "pincode": "110001", "is_default": True} for id, kind in ((pickup, "pickup"), (drop, "drop"))]
    rows["consignments"] = [{"id": shipment, "company_id": company, "pickup_location_id": pickup, "drop_location_id": drop,
        "identifier_type": "AWB", "identifier_value": "AWB-001", "current_status": "Delivered", "pieces": 2,
        "chargeable_weight": "12.345", "chargeable_volume": "0.125", "pickup_address_snapshot": "Edited warehouse",
        "drop_address_snapshot": "Customer store", "planned_pickup_date": date(2026, 10, 1),
        "expected_delivery_date": date(2026, 10, 2), "actual_pickup_at": moment, "actual_delivery_at": moment}]
    rows["shipment_events"] = [{"id": uuid4(), "consignment_id": shipment, "recorded_by": inactive,
        "status": status, "occurred_at": moment, "recorded_at": moment, "notes": "Historical event"} for status in ("Picked Up", "Delivered")]
    for index, filename in enumerate(("old-pod.pdf", "current-pod.pdf", "invoice.pdf", "report.csv")):
        content = b"%PDF-1.4\n" + filename.encode() if filename.endswith(".pdf") else b"shipment,pieces\nAWB-001,2\n"
        (uploads / filename).write_bytes(content)
        file_id = uuid4()
        rows["files"].append({"id": file_id, "uploaded_by": user, "storage_bucket": "local", "storage_path": "local:" + filename,
            "original_filename": filename, "mime_type": "application/pdf" if filename.endswith(".pdf") else "text/csv",
            "size_bytes": len(content), "checksum": hashlib.sha256(content).hexdigest(), "uploaded_at": moment})
        if index < 3:
            rows["shipment_documents"].append({"id": uuid4(), "consignment_id": shipment, "file_id": file_id,
                "document_type": "POD" if index < 2 else "invoice", "version": index + 1 if index < 2 else 1, "is_current": index != 0})
    (uploads / "unlinked.pdf").write_bytes(b"%PDF-1.4\nUnlinked upload")
    rows["mis_views"] = [{"id": view, "created_by": user, "name": "Client MIS", "filters": {"company": str(company)},
        "selected_columns": ["identifier_value", "pieces"], "preferred_format": "csv", "notes": "Keep these settings"}]
    rows["mis_reports"] = [{"id": uuid4(), "view_id": view, "generated_by": inactive, "file_id": rows["files"][-1]["id"],
        "name": "Saved report", "format": "csv", "filters_snapshot": {"company": str(company)},
        "columns_snapshot": ["identifier_value", "pieces"], "metrics_snapshot": {"count": 1, "nested": {"pieces": 2}},
        "shipment_count": 1, "generated_at": moment}]
    rows["audit_logs"] = [{"id": uuid4(), "actor_id": inactive, "entity_type": "consignments", "entity_id": uuid4(),
        "action": "delete", "before_values": {"identifier_value": "Deleted shipment"}, "after_values": None, "recorded_at": moment}]
    with disposable_engine(request.param, tmp_path / "source.db") as source:
        with source.begin() as connection:
            Base.metadata.create_all(connection)
            shipment_v1.insert_rows(connection, rows)
            connection.exec_driver_sql("ALTER TABLE companies ADD COLUMN extra_notes TEXT")
            connection.execute(text("UPDATE companies SET extra_notes = :notes"), {"notes": "Extra client data"})
            connection.exec_driver_sql("CREATE TABLE operator_settings (section TEXT, name TEXT, value TEXT, PRIMARY KEY(section, name))")
            connection.execute(text("INSERT INTO operator_settings VALUES ('labels', 'size', '4x6')"))
            connection.exec_driver_sql("CREATE TABLE retained_notes (note TEXT)")
            connection.execute(text("INSERT INTO retained_notes VALUES ('duplicate'), ('duplicate')"))
            connection.exec_driver_sql("CREATE TABLE empty_custom_table (id INTEGER PRIMARY KEY)")
        yield source, storage


def source_digest(engine):
    with engine.begin() as connection:
        schema, rows, _ = shipment_v1.read_source(connection, "er")
        return shipment_v1.digest({"schema": schema, "rows": rows})


def test_full_graph_all_versions_all_history_and_extra_data(complete_source, tmp_path):
    source, storage = complete_source
    target_backend = "sqlite" if source.dialect.name == "postgresql" else ("postgresql" if os.getenv("ER_TEST_DATABASE_URL") else "sqlite")
    before = source_digest(source)
    with source.begin() as connection:
        expected = target_snapshot(connection)
    with disposable_engine(target_backend, tmp_path / "target.db") as target:
        preflight = migrate(target, storage, source_engine=source, fresh_target=True, dry_run=True)
        assert preflight["status"] == "ready" and not inspect(target).get_table_names()
        result = migrate(target, storage, source_engine=source, fresh_target=True)
        assert result["status"] == "applied"
        assert set(result["counts"]["target"]) == set(Base.metadata.tables)
        assert result["counts"]["archived_rows"] == sum(result["counts"]["source"].values())
        assert all(item["source_rows"] == item["archived_rows"] for item in result["counts"]["coverage"].values())
        assert result["counts"]["coverage"]["shipment_events"]["mapped_rows"] == 2
        assert result["counts"]["coverage"]["operator_settings"]["mapped_rows"] == 0
        assert result["counts"]["source"]["empty_custom_table"] == 0
        with target.begin() as connection:
            actual = target_snapshot(connection)
            for name in Base.metadata.tables:
                expected_rows = {row["id"]: row for row in expected[name]}
                actual_rows = {row["id"]: row for row in actual[name]}
                assert all(actual_rows[id] == row for id, row in expected_rows.items()), name
                if name != "files":
                    assert expected_rows.keys() == actual_rows.keys(), name
            assert len(actual["files"]) == 5
            versions = sorted(doc["version"] for doc in actual["shipment_documents"] if doc["document_type"] == "POD")
            assert versions == [1, 2]
            archive = connection.execute(select(RECORDS).where(RECORDS.c.source_table == "companies")).mappings().one()
            assert archive["source_values"]["extra_notes"] == "Extra client data"
            notes = connection.execute(select(RECORDS).where(RECORDS.c.source_table == "retained_notes")).mappings().all()
            assert len(notes) == 2 and notes[0]["source_id"] != notes[1]["source_id"]
            setting = connection.execute(select(RECORDS).where(RECORDS.c.source_table == "operator_settings")).mappings().one()
            assert setting["source_values"]["value"] == "4x6"
            if target.dialect.name == "postgresql":
                enabled = connection.execute(text("SELECT relname FROM pg_class WHERE relnamespace = current_schema()::regnamespace AND relrowsecurity")).scalars().all()
                assert set(enabled) == set(Base.metadata.tables) | set(LEDGER.tables)
        assert migrate(target, storage, source_engine=source, fresh_target=True)["status"] == "already_applied"
    assert source_digest(source) == before


def test_full_graph_failure_rolls_back_and_can_retry(complete_source, tmp_path, monkeypatch):
    source, storage = complete_source
    real_insert, calls = shipment_v1.insert_rows, 0

    def fail_after_insert(connection, rows):
        nonlocal calls
        real_insert(connection, rows)
        calls += 1
        if calls == 2:
            raise RuntimeError("Injected complete-import failure")

    monkeypatch.setattr(shipment_v1, "insert_rows", fail_after_insert)
    with disposable_engine(source.dialect.name, tmp_path / "rollback-target.db") as target:
        with pytest.raises(RuntimeError, match="Injected"):
            migrate(target, storage, source_engine=source, fresh_target=True)
        assert not inspect(target).get_table_names()
        monkeypatch.setattr(shipment_v1, "insert_rows", real_insert)
        assert migrate(target, storage, source_engine=source, fresh_target=True)["status"] == "applied"


@pytest.mark.parametrize("problem", ["checksum", "size", "missing"])
def test_bad_file_stops_the_whole_import(complete_source, tmp_path, problem):
    source, storage = complete_source
    table = Base.metadata.tables["files"]
    if problem == "missing":
        (storage.root / "old-pod.pdf").unlink()
    else:
        with source.begin() as connection:
            connection.execute(update(table).where(table.c.storage_path == "local:old-pod.pdf").values(
                **({"checksum": "0" * 64} if problem == "checksum" else {"size_bytes": 999})))
    with disposable_engine("sqlite", tmp_path / "bad-file-target.db") as target:
        with pytest.raises(MigrationError):
            migrate(target, storage, source_engine=source, fresh_target=True)
        assert not inspect(target).get_table_names()


def test_nonempty_target_and_same_source_are_refused(complete_source, tmp_path):
    source, storage = complete_source
    with disposable_engine("sqlite", tmp_path / "nonempty.db") as target:
        with target.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE existing_data (id INTEGER PRIMARY KEY)")
            connection.exec_driver_sql("INSERT INTO existing_data VALUES (1)")
        with pytest.raises(MigrationError, match="fresh"):
            migrate(target, storage, source_engine=source, fresh_target=True)
        assert inspect(target).get_table_names() == ["existing_data"]
    with pytest.raises(MigrationError, match="separate"):
        migrate(source, storage, source_engine=source, fresh_target=True)


def test_partial_er_source_is_not_silently_dropped(tmp_path):
    with disposable_engine("sqlite", tmp_path / "partial.db") as source, disposable_engine("sqlite", tmp_path / "target.db") as target:
        with source.begin() as connection:
            Base.metadata.tables["admin_users"].create(connection)
        with pytest.raises(MigrationError, match="incomplete ER"):
            migrate(target, Storage(tmp_path / "empty"), source_engine=source, fresh_target=True)
        assert not inspect(target).get_table_names()


def test_empty_model_creates_all_ten_empty_tables(tmp_path):
    with disposable_engine("sqlite", tmp_path / "empty-source.db") as source, disposable_engine("sqlite", tmp_path / "target.db") as target:
        with source.begin() as connection:
            Base.metadata.create_all(connection)
        result = migrate(target, Storage(tmp_path / "empty-uploads"), source_engine=source, fresh_target=True)
        assert set(result["counts"]["target"]) == set(Base.metadata.tables)
        assert all(count == 0 for count in result["counts"]["target"].values())
        assert set(inspect(target).get_table_names()) == set(Base.metadata.tables) | set(LEDGER.tables)


def test_mixed_sources_require_explicit_choice_and_archive_both(complete_source, tmp_path):
    source, storage = complete_source
    from app.models import db
    with source.begin() as connection:
        db.metadata.create_all(connection)
        connection.execute(insert(db.metadata.tables["consignment"]).values(consignment_number="LEGACY-001", pieces=1))
    with disposable_engine("sqlite", tmp_path / "mixed-target.db") as target:
        with pytest.raises(MigrationError, match="Both legacy and ER"):
            migrate(target, storage, source_engine=source, fresh_target=True)
        result = migrate(target, storage, source_engine=source, fresh_target=True, source_format="er")
        assert result["counts"]["target"]["consignments"] == 1
        assert result["counts"]["coverage"]["consignment"] == {"source_rows": 1, "archived_rows": 1, "mapped_rows": 0}
        with target.begin() as connection:
            archived = connection.execute(select(RECORDS).where(RECORDS.c.source_table == "consignment")).mappings().one()
            assert archived["source_values"]["consignment_number"] == "LEGACY-001"


def test_rls_filtered_source_cannot_produce_an_incomplete_import(complete_source, tmp_path):
    source, storage = complete_source
    if source.dialect.name != "postgresql":
        pytest.skip("Source RLS is a PostgreSQL feature")
    role = "source_reader_" + uuid4().hex
    restricted = None
    try:
        with source.begin() as connection:
            schema = connection.execute(text("SELECT current_schema()")).scalar_one()
            connection.exec_driver_sql("ALTER TABLE companies ENABLE ROW LEVEL SECURITY")
            connection.exec_driver_sql(f'CREATE ROLE "{role}"')
            connection.exec_driver_sql(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"')
            connection.exec_driver_sql(f'GRANT SELECT ON ALL TABLES IN SCHEMA "{schema}" TO "{role}"')
        restricted = migration_engine(source.url.update_query_dict({"options": f"-csearch_path={schema} -crole={role}"}))
        with disposable_engine("sqlite", tmp_path / "rls-target.db") as target:
            with pytest.raises(MigrationError, match="only part of the RLS"):
                migrate(target, storage, source_engine=restricted, fresh_target=True)
            assert not inspect(target).get_table_names()
    finally:
        if restricted:
            restricted.dispose()
        with source.begin() as connection:
            connection.exec_driver_sql(f'DROP OWNED BY "{role}"')
            connection.exec_driver_sql(f'DROP ROLE "{role}"')
