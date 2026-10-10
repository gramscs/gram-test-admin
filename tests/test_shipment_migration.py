"""Migration success, reconciliation, failure rollback and retry tests."""
from datetime import UTC, datetime
import hashlib
import io
import json
import os
import sqlite3
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from PIL import Image
import pytest
from sqlalchemy import create_engine, inspect, insert, select, text, update
from sqlalchemy.engine import make_url

from app.models import db
from database.models import Base
from migrations import shipment_v1
from migrations.shipment_v1 import LEDGER, RECORDS, migrate, migration_engine
from migrations.storage import MigrationError, Storage


@pytest.fixture(params=["sqlite"] + (["postgresql"] if os.getenv("ER_TEST_DATABASE_URL") else []))
def legacy_engine(request, tmp_path):
    cleanup = None
    if request.param == "sqlite":
        engine = migration_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    else:
        url = make_url(os.environ["ER_TEST_DATABASE_URL"])
        if url.host not in {"127.0.0.1", "localhost"} or url.database != "gram_admin_er_test":
            pytest.fail("PostgreSQL migration tests require the disposable loopback database.")
        schema = "migration_test_" + uuid4().hex
        cleanup = create_engine(url)
        with cleanup.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        engine = migration_engine(url.update_query_dict({"options": f"-csearch_path={schema}"}))
    try:
        with engine.begin() as connection:
            db.metadata.create_all(connection)
        yield engine
    finally:
        engine.dispose()
        if cleanup is not None:
            with cleanup.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
            cleanup.dispose()


@pytest.fixture
def populated(legacy_engine, tmp_path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    output = io.BytesIO()
    Image.new("RGB", (2, 2), "green").save(output, "PNG")
    content = output.getvalue()
    (uploads / "pod.png").write_bytes(content)
    (uploads / "invoice.pdf").write_bytes(b"%PDF-1.4\nlegacy invoice bytes")
    (uploads / "report.csv").write_bytes(b"identifier,pieces\nLRN-001,2\n")
    (uploads / "orphan.pdf").write_bytes(b"%PDF-1.4\nunlinked upload")
    with legacy_engine.begin() as connection:
        connection.execute(insert(db.metadata.tables["company"]).values(id=7, name="Acme", address="Registered address", active=True))
        for id, kind in ((8, "pickup"), (9, "drop")):
            connection.execute(insert(db.metadata.tables["company_location"]).values(id=id, company_id=7,
                kind=kind, label=kind + " warehouse", address=kind + " address", pincode="110001", is_default=True))
        connection.exec_driver_sql("ALTER TABLE consignment ADD COLUMN extra_operator_notes TEXT")
        connection.execute(insert(db.metadata.tables["consignment"]).values(id=11, consignment_number="LRN-001",
            identifier_type="LRN", company_id=7, status="In Transit", pieces=2, chargeable_weight="12.345", chargeable_volume="0.125",
            pickup_address="Edited pickup address", drop_address="Drop snapshot", pickup_pincode="110001", drop_pincode="400001",
            pickup_tag="Warehouse A", drop_tag="Store B", pickup_date="2026-10-01", drop_date="02/10/2026", eta="ETA text",
            eta_debug_json='{"source":"manual"}', pod_image="pod.png", pod_original_name="Customer proof.png",
            invoice_file="invoice.pdf", invoice_original_name="Invoice 123.pdf"))
        connection.execute(insert(db.metadata.tables["consignment"]).values(id=12, consignment_number="AWB-002",
            identifier_type="AWB", status=None, pieces=1, pickup_date="next Tuesday", pod_image="pod.png"))
        connection.execute(text("UPDATE consignment SET extra_operator_notes = :notes WHERE id = 11"), {"notes": "Keep this extra field"})
        connection.execute(insert(db.metadata.tables["mis_view"]).values(id=13, name="Client MIS", notes="Saved view notes",
            created_by="admin", updated_at=datetime(2026, 10, 1, tzinfo=UTC), output_format="csv",
            filters_json=json.dumps({"company": "7", "period": "month"}), columns_json=json.dumps(["consignment_number", "status", "pickup_date"])))
        connection.execute(insert(db.metadata.tables["mis_report"]).values(id=14, name="October report", notes="Management notes",
            generated_by="manager", generated_at=datetime(2026, 10, 2, 12, tzinfo=UTC), output_format="csv", row_count=2,
            file_ref="report.csv", file_name="October MIS.csv", filters_json=json.dumps({"company": "7"}),
            columns_json=json.dumps(["consignment_number", "status"]), summary_json=json.dumps({"total": 2})))
        connection.execute(insert(db.metadata.tables["lead"]).values(id=15, name="Archived lead", email="lead@example.test", message="Preserve enquiry"))
        connection.execute(insert(db.metadata.tables["newsletter_subscriber"]).values(id=16, email="subscriber@example.test"))
    return legacy_engine, Storage(uploads), content


def legacy_snapshot(engine):
    with engine.begin() as connection:
        _, rows = shipment_v1.read_legacy(connection)
        return shipment_v1.digest(rows)


def assert_no_migration_tables(engine):
    assert not set(inspect(engine).get_table_names()).intersection(set(Base.metadata.tables) | set(LEDGER.tables))


def test_one_run_copies_relationships_files_and_every_source_field(populated):
    engine, storage, pod_content = populated
    before = legacy_snapshot(engine)
    result = migrate(engine, storage)
    assert result["status"] == "applied"
    assert result["counts"]["archived_rows"] == 9
    assert result["counts"]["target"]["shipment_events"] == 0
    assert result["warnings"]
    assert before == legacy_snapshot(engine)
    with engine.begin() as connection:
        target = shipment_v1.target_snapshot(connection)
        company = target["companies"][0]
        row = next(row for row in target["consignments"] if row["identifier_value"] == "LRN-001")
        assert row["company_id"] == company["id"]
        assert row["pickup_address_snapshot"] == "Edited pickup address"
        assert row["planned_pickup_date"].isoformat() == "2026-10-01"
        assert row["expected_delivery_date"].isoformat() == "2026-10-02"
        assert row["pickup_location_id"] is None and row["actual_delivery_at"] is None
        unknown = next(row for row in target["consignments"] if row["identifier_value"] == "AWB-002")
        assert unknown["planned_pickup_date"] is None and unknown["current_status"] == "No status"
        assert len(target["files"]) == 4 and len(target["shipment_documents"]) == 3
        pod = next(row for row in target["files"] if row["storage_path"] == "local:pod.png")
        assert pod["checksum"] == hashlib.sha256(pod_content).hexdigest()
        assert pod["size_bytes"] == len(pod_content) and pod["uploaded_at"] is None and pod["mime_type"] == "image/png"
        assert len([doc for doc in target["shipment_documents"] if doc["file_id"] == pod["id"]]) == 2
        view, report = target["mis_views"][0], target["mis_reports"][0]
        assert view["filters"]["company"] == str(company["id"])
        assert view["selected_columns"] == ["identifier_value", "current_status", "planned_pickup_date"]
        assert report["view_id"] is None and report["metrics_snapshot"] == {"total": 2}
        assert report["generated_at"] == datetime(2026, 10, 2, 12, tzinfo=UTC)
        users = {user["username"]: user for user in target["admin_users"]}
        assert users["admin"]["active"] and not users["manager"]["active"]
        assert report["generated_by"] == users["manager"]["id"]
        archive = connection.execute(select(RECORDS).where(RECORDS.c.source_table == "consignment", RECORDS.c.source_id == "11")).mappings().one()
        assert archive["target_id"] == row["id"]
        assert archive["source_values"]["pickup_pincode"] == "110001"
        assert archive["source_values"]["pickup_tag"] == "Warehouse A"
        assert archive["source_values"]["eta"] == "ETA text"
        assert archive["source_values"]["extra_operator_notes"] == "Keep this extra field"
        report_archive = connection.execute(select(RECORDS).where(RECORDS.c.source_table == "mis_report")).mappings().one()
        assert report_archive["source_values"]["notes"] == "Management notes"
        assert len(target["audit_logs"]) == 9
    assert (storage.root / "pod.png").read_bytes() == pod_content


def test_preflight_does_not_create_tables_or_modify_data(populated):
    engine, storage, content = populated
    before = legacy_snapshot(engine)
    assert migrate(engine, storage, dry_run=True)["status"] == "ready"
    assert_no_migration_tables(engine)
    assert before == legacy_snapshot(engine)


def test_retry_keeps_ids_and_counts(populated):
    engine, storage, content = populated
    first = migrate(engine, storage)
    with engine.begin() as connection:
        before = shipment_v1.digest(shipment_v1.target_snapshot(connection))
    second = migrate(engine, storage)
    assert second["status"] == "already_applied" and first["counts"] == second["counts"]
    with engine.begin() as connection:
        assert before == shipment_v1.digest(shipment_v1.target_snapshot(connection))


@pytest.mark.parametrize("change", ["source", "target", "file", "archive"])
def test_retry_refuses_changed_data(populated, change):
    engine, storage, content = populated
    migrate(engine, storage)
    if change == "file":
        (storage.root / "pod.png").write_bytes(content + b"changed")
    else:
        with engine.begin() as connection:
            if change == "source":
                connection.execute(update(db.metadata.tables["consignment"]).where(db.metadata.tables["consignment"].c.id == 11).values(pieces=3))
            elif change == "target":
                connection.execute(update(Base.metadata.tables["consignments"]).values(pieces=3))
            else:
                connection.execute(update(RECORDS).where(RECORDS.c.source_table == "lead").values(source_values={"changed": True}))
    with pytest.raises(MigrationError, match="changed"):
        migrate(engine, storage)


def test_missing_upload_aborts_before_schema_creation(populated):
    engine, storage, content = populated
    (storage.root / "invoice.pdf").unlink()
    with pytest.raises(MigrationError, match="missing"):
        migrate(engine, storage)
    assert_no_migration_tables(engine)


@pytest.mark.parametrize("field,value", [("pieces", 0), ("pieces", 1.5), ("consignment_number", "X" * 65)])
def test_invalid_legacy_records_abort_without_partial_data(populated, field, value):
    engine, storage, content = populated
    if engine.dialect.name == "postgresql" and (value == 1.5 or field == "consignment_number"):
        pytest.skip("PostgreSQL source column types prevent this invalid legacy value")
    with engine.begin() as connection:
        connection.execute(update(db.metadata.tables["consignment"]).where(db.metadata.tables["consignment"].c.id == 11).values(**{field: value}))
    with pytest.raises(MigrationError):
        migrate(engine, storage)
    assert_no_migration_tables(engine)


def test_malformed_mis_json_is_not_silently_replaced(populated):
    engine, storage, content = populated
    with engine.begin() as connection:
        connection.execute(update(db.metadata.tables["mis_view"]).values(filters_json="not-json"))
    with pytest.raises(MigrationError, match="JSON"):
        migrate(engine, storage)
    assert_no_migration_tables(engine)


def test_sqlite_legacy_orphan_is_reported_not_unassigned(populated):
    engine, storage, content = populated
    if engine.dialect.name != "sqlite":
        pytest.skip("This models legacy SQLite installations without FK enforcement")
    with sqlite3.connect(engine.url.database) as connection:
        connection.execute("UPDATE consignment SET company_id = 999 WHERE id = 11")
    with pytest.raises(MigrationError, match="referenced client"):
        migrate(engine, storage)
    assert_no_migration_tables(engine)


def test_shared_mis_file_is_rejected_before_creating_target(populated):
    engine, storage, content = populated
    table = db.metadata.tables["mis_report"]
    with engine.begin() as connection:
        original = dict(connection.execute(select(table)).mappings().one())
        original["id"] = 17
        connection.execute(insert(table).values(**original))
    with pytest.raises(MigrationError, match="file sharing"):
        migrate(engine, storage)
    assert_no_migration_tables(engine)


def test_separate_wal_source_blocks_concurrent_writers(populated, tmp_path, monkeypatch):
    source, storage, content = populated
    if source.dialect.name != "sqlite":
        pytest.skip("WAL-mode locking is specific to SQLite")
    with sqlite3.connect(source.url.database) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
    destination = migration_engine(f"sqlite:///{tmp_path / 'wal-target.db'}")
    describe = storage.describe
    attempts = 0

    def check_writer_is_blocked(reference):
        nonlocal attempts
        with sqlite3.connect(source.url.database, timeout=0.01) as writer:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                writer.execute("UPDATE consignment SET pieces = 99 WHERE id = 11")
        attempts += 1
        return describe(reference)

    monkeypatch.setattr(storage, "describe", check_writer_is_blocked)
    try:
        assert migrate(destination, storage, source_engine=source)["status"] == "applied"
        assert attempts > 0
    finally:
        destination.dispose()


def test_failure_after_ddl_rolls_back_tables_and_can_retry(populated, monkeypatch):
    engine, storage, content = populated
    real_insert = shipment_v1.insert_rows
    calls = 0

    def fail_during_apply(connection, rows):
        nonlocal calls
        calls += 1
        real_insert(connection, rows)
        if calls == 2:
            raise RuntimeError("Injected failure after all target rows were inserted")

    monkeypatch.setattr(shipment_v1, "insert_rows", fail_during_apply)
    before = legacy_snapshot(engine)
    with pytest.raises(RuntimeError, match="Injected"):
        migrate(engine, storage)
    assert_no_migration_tables(engine)
    assert before == legacy_snapshot(engine)
    monkeypatch.setattr(shipment_v1, "insert_rows", real_insert)
    assert migrate(engine, storage)["status"] == "applied"


def test_existing_unmanaged_target_is_never_overwritten(populated):
    engine, storage, content = populated
    with engine.begin() as connection:
        Base.metadata.create_all(connection)
    with pytest.raises(MigrationError, match="Unmanaged"):
        migrate(engine, storage)


def test_all_new_postgres_tables_deny_other_roles(populated):
    engine, storage, content = populated
    if engine.dialect.name != "postgresql":
        pytest.skip("RLS is a PostgreSQL feature")
    migrate(engine, storage)
    role = "migration_reader_" + uuid4().hex
    with engine.begin() as connection:
        names = sorted(set(Base.metadata.tables) | set(LEDGER.tables))
        enabled = connection.execute(text("SELECT relname FROM pg_class WHERE relnamespace = current_schema()::regnamespace AND relrowsecurity")).scalars().all()
        assert set(names) == set(enabled)
        schema = connection.execute(text("SELECT current_schema()")).scalar_one()
        connection.exec_driver_sql(f'CREATE ROLE "{role}"')
        connection.exec_driver_sql(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"')
        connection.exec_driver_sql(f'GRANT SELECT ON ALL TABLES IN SCHEMA "{schema}" TO "{role}"')
        connection.exec_driver_sql(f'SET LOCAL ROLE "{role}"')
        assert connection.execute(text("SELECT count(*) FROM consignments")).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM admin_migration_records")).scalar_one() == 0
        connection.exec_driver_sql("RESET ROLE")
        connection.exec_driver_sql(f'DROP OWNED BY "{role}"')
        connection.exec_driver_sql(f'DROP ROLE "{role}"')


def test_separate_sqlite_source_can_migrate_to_target(populated, tmp_path):
    source, storage, content = populated
    original_engine = source
    if source.dialect.name == "sqlite":
        destination = migration_engine(f"sqlite:///{tmp_path / 'separate-target.db'}")
    else:
        destination = source
        source = migration_engine(f"sqlite:///{tmp_path / 'separate-source.db'}")
        with source.begin() as connection:
            db.metadata.create_all(connection)
            connection.execute(insert(db.metadata.tables["consignment"]).values(id=101, consignment_number="LOCAL-TO-PG", pieces=1))
        with destination.begin() as connection:
            db.metadata.drop_all(connection)
        storage = Storage(tmp_path / "empty-uploads")
    try:
        before = legacy_snapshot(source)
        assert migrate(destination, storage, source_engine=source)["status"] == "applied"
        assert before == legacy_snapshot(source)
        assert migrate(destination, storage, source_engine=source)["status"] == "already_applied"
    finally:
        if source is not original_engine:
            source.dispose()
        if destination is not original_engine:
            destination.dispose()


def test_cli_runs_without_flask_startup_and_redacts_credentials(populated, tmp_path):
    engine, storage, content = populated
    if engine.dialect.name != "sqlite":
        pytest.skip("CLI process smoke uses a disposable SQLite file")
    root = Path(__file__).resolve().parents[1]
    settings = dict(os.environ, DATABASE_URL=f"sqlite:///{tmp_path / 'cli-target.db'}",
        MIGRATION_SOURCE_DATABASE_URL="", SUPABASE_URL="", SUPABASE_KEY="", ADMIN_USERNAME="admin")
    args = [sys.executable, str(root / "migrations/001_shipment_database.py"), "--source-local", engine.url.database, "--uploads-dir", str(storage.root)]
    first = subprocess.run(args, env=settings, capture_output=True, text=True)
    assert first.returncode == 0 and '"status": "ready"' in first.stdout
    assert_no_migration_tables(engine)
    second = subprocess.run(args + ["--apply"], env=settings, capture_output=True, text=True)
    assert second.returncode == 0 and '"status": "applied"' in second.stdout
    settings["DATABASE_URL"] = engine.url.render_as_string(hide_password=False)
    same_database = subprocess.run(args + ["--apply"], env=settings, capture_output=True, text=True)
    assert same_database.returncode == 1 and "must be different" in same_database.stderr
    settings["DATABASE_URL"] = ""
    missing_target = subprocess.run(args + ["--apply"], env=settings, capture_output=True, text=True)
    assert missing_target.returncode == 1 and "fresh target" in missing_target.stderr
    assert_no_migration_tables(engine)
    settings["DATABASE_URL"] = "postgresql://user:do-not-print-this@[YOUR-PASSWORD]:5432/postgres"
    rejected = subprocess.run(args + ["--apply"], env=settings, capture_output=True, text=True)
    assert rejected.returncode == 1 and "do-not-print-this" not in rejected.stdout + rejected.stderr


def test_fresh_legacy_import_accounts_for_every_table(populated, tmp_path):
    source, storage, content = populated
    with source.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE custom_operator_data (data TEXT)")
        connection.execute(text("INSERT INTO custom_operator_data VALUES ('Keep this too')"))
        before_schema, before_rows, _ = shipment_v1.read_source(connection)
    destination = migration_engine(f"sqlite:///{tmp_path / 'all-legacy-target.db'}")
    try:
        result = migrate(destination, storage, source_engine=source, fresh_target=True)
        assert result["counts"]["archived_rows"] == 10
        assert len(result["counts"]["target"]) == 10
        assert all(item["source_rows"] == item["archived_rows"] for item in result["counts"]["coverage"].values())
        with destination.begin() as connection:
            original = connection.execute(select(RECORDS).where(RECORDS.c.source_table == "custom_operator_data")).mappings().one()
            assert original["source_values"] == {"data": "Keep this too"}
            assert original["target_table"] is None
        with source.begin() as connection:
            schema, rows, _ = shipment_v1.read_source(connection)
            assert shipment_v1.digest((schema, rows)) == shipment_v1.digest((before_schema, before_rows))
    finally:
        destination.dispose()


class FakeBucket:
    def __init__(self, data):
        self.data = data

    def download(self, path):
        return self.data[path]

    def list(self, folder, options):
        return [{"name": key.removeprefix(folder + "/"), "id": str(index), "metadata": {"size": len(value)}}
                for index, (key, value) in enumerate(self.data.items()) if key.startswith(folder + "/")]


class FakeSupabase:
    storage = property(lambda self: self)

    def __init__(self, data):
        self.data = data

    def from_(self, bucket):
        return FakeBucket(self.data[bucket])


def test_remote_private_files_are_read_deduplicated_and_not_rewritten(populated):
    engine, storage, content = populated
    remote = FakeSupabase({"pod-uploads": {"consignments/proof.png": content, "consignments/unlinked.png": content}})
    with engine.begin() as connection:
        connection.execute(update(db.metadata.tables["consignment"]).values(pod_image="supabase:pod-uploads/consignments/proof.png"))
    result = migrate(engine, Storage(storage.root, remote))
    assert result["counts"]["target"]["files"] == 6
    with engine.begin() as connection:
        table = Base.metadata.tables["files"]
        file = connection.execute(select(table).where(table.c.storage_path == "supabase:pod-uploads/consignments/proof.png")).mappings().one()
        assert file["storage_bucket"] == "pod-uploads" and file["checksum"] == hashlib.sha256(content).hexdigest()
    assert remote.data["pod-uploads"]["consignments/proof.png"] == content


@pytest.mark.parametrize("reference", ["../outside.pdf", "https://example.test/proof.pdf", "supabase:bucket/../secret", "supabase:bucket//proof.pdf", "supabase:../secret"])
def test_unsafe_references_are_not_fetched(tmp_path, reference):
    with pytest.raises(MigrationError):
        Storage(tmp_path).describe(reference)
