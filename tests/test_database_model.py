"""Real SQL integrity tests. Optional PostgreSQL is restricted to a disposable
loopback database named gram_admin_er_test; DATABASE_URL is never used here.
"""

import os
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, event, inspect, insert, null, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from database.models import (
    AdminUser, AuditLog, Base, Company, CompanyLocation, Consignment, File,
    MisReport, MisView, ShipmentDocument, ShipmentEvent,
)
from database.schema import postgresql_ddl


@pytest.fixture(params=["sqlite"] + (["postgresql"] if os.getenv("ER_TEST_DATABASE_URL") else []))
def er_engine(request):
    if request.param == "sqlite":
        engine = create_engine("sqlite://")

        @event.listens_for(engine, "connect")
        def foreign_keys(dbapi_connection, connection_record):
            dbapi_connection.execute("PRAGMA foreign_keys=ON")

        cleanup_engine = None
    else:
        url = make_url(os.environ["ER_TEST_DATABASE_URL"])
        if (url.get_backend_name() != "postgresql" or url.host not in {"127.0.0.1", "localhost"}
                or url.database != "gram_admin_er_test"):
            pytest.fail("ER tests require the disposable loopback database gram_admin_er_test.")
        schema = "er_test_" + uuid4().hex
        cleanup_engine = create_engine(url)
        with cleanup_engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        Base.metadata.create_all(engine)
        yield engine
    finally:
        engine.dispose()
        if cleanup_engine is not None:
            with cleanup_engine.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
            cleanup_engine.dispose()


@pytest.fixture
def er_session(er_engine):
    with Session(er_engine) as session:
        yield session


def shipment(session, **values):
    row = Consignment(identifier_value="SHIP-" + uuid4().hex, **values)
    session.add(row)
    session.flush()
    return row


def stored_file(session):
    row = File(storage_bucket="pod-uploads", storage_path=uuid4().hex + ".pdf",
               original_filename="proof.pdf", mime_type="application/pdf", size_bytes=2048,
               checksum="a" * 64)
    session.add(row)
    session.flush()
    return row


def presets(session):
    company = Company(name="Client " + uuid4().hex)
    pickup = CompanyLocation(company=company, kind="pickup", label="Warehouse", address="Old pickup address", is_default=True)
    drop = CompanyLocation(company=company, kind="drop", label="Store", address="Drop address", is_default=True)
    session.add_all([company, pickup, drop])
    session.flush()
    return company, pickup, drop


def report(session, user=None):
    view = MisView(name="Monthly MIS", filters={"period": "month"}, selected_columns=["identifier_value"])
    file = stored_file(session)
    row = MisReport(view=view, file=file, generated_by_user=user, name="October MIS", format="pdf", shipment_count=1,
                    filters_snapshot={"period": "month"}, columns_snapshot=["identifier_value"],
                    metrics_snapshot={"shipments": 1})
    session.add(row)
    session.flush()
    return view, file, row


def test_all_relationships_and_uuid_round_trip(er_session):
    user = AdminUser(username="manager", role="administrator")
    company, pickup, drop = presets(er_session)
    row = shipment(er_session, company=company, pickup_location=pickup, drop_location=drop,
                   pickup_address_snapshot=pickup.address, drop_address_snapshot=drop.address,
                   chargeable_weight=Decimal("12.345"), chargeable_volume=Decimal("0.125"))
    file = stored_file(er_session)
    file.uploaded_by_user = user
    doc = ShipmentDocument(consignment=row, file=file, document_type="POD")
    history = ShipmentEvent(consignment=row, recorded_by_user=user, status="In Transit", occurred_at=datetime.now(UTC))
    er_session.add_all([doc, history])
    view, report_file, generated = report(er_session, user=user)
    view.created_by_user = user
    audit = AuditLog(actor=user, entity_type="consignments", entity_id=row.id, action="create", after_values={"pieces": 1})
    er_session.add_all([doc, history, audit])
    er_session.commit()
    er_session.expire_all()
    assert isinstance(row.id, UUID)
    assert set(row.company.locations) == {pickup, drop}
    assert row.events == [history] and row.documents == [doc]
    assert row.pickup_location is pickup and row.drop_location is drop
    assert file.shipment_documents == [doc] and report_file.mis_report is generated
    assert user.shipment_events == [history] and user.audit_logs == [audit]
    assert user.files == [file] and user.mis_views == [view] and user.mis_reports == [generated]
    assert row.chargeable_weight == Decimal("12.345") and row.chargeable_volume == Decimal("0.125")


@pytest.mark.parametrize("slot", ["pickup_location_id", "drop_location_id"])
def test_presets_cannot_belong_to_another_company(er_session, slot):
    company, pickup, drop = presets(er_session)
    other = Company(name="Other")
    er_session.add(other)
    er_session.flush()
    with pytest.raises(IntegrityError):
        shipment(er_session, company_id=other.id, **{slot: pickup.id if slot.startswith("pickup") else drop.id})


@pytest.mark.parametrize("slot", ["pickup_location_id", "drop_location_id"])
def test_raw_sql_cannot_use_the_wrong_preset_kind(er_session, slot):
    company, pickup, drop = presets(er_session)
    with pytest.raises(IntegrityError):
        er_session.execute(insert(Consignment).values(company_id=company.id,
            identifier_value="BAD-KIND", **{slot: drop.id if slot.startswith("pickup") else pickup.id}))


def test_preset_requires_a_client(er_session):
    company, pickup, drop = presets(er_session)
    with pytest.raises(IntegrityError):
        shipment(er_session, pickup_location_id=pickup.id)


def test_existing_shipment_cannot_change_to_a_wrong_kind_or_company(er_session):
    company, pickup, drop = presets(er_session)
    row = shipment(er_session, company=company, pickup_location=pickup)
    with pytest.raises(IntegrityError):
        er_session.execute(update(Consignment).where(Consignment.id == row.id).values(pickup_location_id=drop.id))


def test_dangling_foreign_keys_are_rejected(er_session):
    with pytest.raises(IntegrityError):
        shipment(er_session, company_id=uuid4())


def test_unknown_historical_file_time_can_remain_null(er_session):
    result = er_session.execute(insert(File).values(
        storage_bucket="local", storage_path="historical.pdf", original_filename="historical.pdf",
        mime_type="application/pdf", size_bytes=2048, uploaded_at=null(),
    ).returning(File.id))
    row = er_session.get(File, result.scalar_one())
    assert row.uploaded_at is None and row.checksum is None


@pytest.mark.parametrize("role", ["guest", ""])
def test_invalid_roles_are_rejected(er_session, role):
    with pytest.raises(IntegrityError):
        er_session.add(AdminUser(username="invalid-role", role=role))
        er_session.flush()


def test_preset_kind_cannot_change_after_use(er_session):
    company, pickup, drop = presets(er_session)
    shipment(er_session, company_id=company.id, pickup_location_id=pickup.id)
    with pytest.raises(IntegrityError):
        er_session.execute(update(CompanyLocation).where(CompanyLocation.id == pickup.id).values(kind="drop", is_default=False))


def test_preset_edits_do_not_change_address_snapshots(er_session):
    company, pickup, drop = presets(er_session)
    row = shipment(er_session, company=company, pickup_location=pickup, pickup_address_snapshot=pickup.address)
    pickup.address = "New warehouse address"
    er_session.commit()
    er_session.expire_all()
    assert row.pickup_address_snapshot == "Old pickup address"
    assert row.pickup_location.address == "New warehouse address"


def test_referenced_preset_cannot_be_deleted(er_session):
    company, pickup, drop = presets(er_session)
    shipment(er_session, company=company, pickup_location=pickup)
    with pytest.raises(IntegrityError):
        er_session.delete(pickup)
        er_session.flush()


def test_only_one_default_of_each_location_kind(er_session):
    company, pickup, drop = presets(er_session)
    with pytest.raises(IntegrityError):
        er_session.add(CompanyLocation(company=company, kind="pickup", label="Second", address="Elsewhere", is_default=True))
        er_session.flush()


@pytest.mark.parametrize("values", [
    {"pieces": 0}, {"pieces": -1}, {"chargeable_weight": -1}, {"chargeable_volume": -1},
    {"identifier_type": "invalid"}, {"current_status": " "},
    {"actual_pickup_at": datetime(2026, 10, 10, tzinfo=UTC), "actual_delivery_at": datetime(2026, 10, 9, tzinfo=UTC)},
])
def test_invalid_shipment_values_are_rejected(er_session, values):
    with pytest.raises(IntegrityError):
        shipment(er_session, **values)


def test_shipment_identifier_remains_unique_across_identifier_types(er_session):
    row = shipment(er_session, identifier_type="LRN")
    with pytest.raises(IntegrityError):
        er_session.execute(insert(Consignment).values(identifier_value=row.identifier_value, identifier_type="AWB"))


def test_document_replacement_preserves_versions_and_can_share_a_file(er_session):
    row = shipment(er_session)
    first_file, second_file = stored_file(er_session), stored_file(er_session)
    first = ShipmentDocument(consignment=row, file=first_file, document_type="POD")
    er_session.add(first)
    er_session.flush()
    first.is_current = False
    er_session.flush()
    second = ShipmentDocument(consignment=row, file=second_file, document_type="POD", version=2)
    er_session.add(second)
    shared = ShipmentDocument(consignment=shipment(er_session), file=second_file, document_type="POD")
    er_session.add_all([second, shared])
    er_session.commit()
    assert len(row.documents) == 2 and not first.is_current and second.is_current
    assert len(second_file.shipment_documents) == 2


@pytest.mark.parametrize("version,is_current", [(2, True), (1, False), (0, False)])
def test_document_version_and_current_uniqueness(er_session, version, is_current):
    row, file = shipment(er_session), stored_file(er_session)
    er_session.add(ShipmentDocument(consignment=row, file=file, document_type="POD"))
    er_session.flush()
    with pytest.raises(IntegrityError):
        er_session.add(ShipmentDocument(consignment=row, file=file, document_type="POD", version=version, is_current=is_current))
        er_session.flush()


def test_referenced_file_cannot_be_deleted(er_session):
    row, file = shipment(er_session), stored_file(er_session)
    er_session.add(ShipmentDocument(consignment=row, file=file, document_type="invoice"))
    er_session.flush()
    with pytest.raises(IntegrityError):
        er_session.delete(file)
        er_session.flush()


@pytest.mark.parametrize("values", [{"size_bytes": -1}, {"checksum": "bad"}, {"storage_path": " "}])
def test_invalid_file_metadata_is_rejected(er_session, values):
    file = stored_file(er_session)
    with pytest.raises(IntegrityError):
        er_session.execute(update(File).where(File.id == file.id).values(**values))


def test_storage_paths_are_unique_even_across_buckets(er_session):
    file = stored_file(er_session)
    with pytest.raises(IntegrityError):
        er_session.execute(insert(File).values(storage_bucket="reports", storage_path=file.storage_path,
            original_filename="duplicate.pdf", mime_type="application/pdf", size_bytes=2048))


def test_deleting_view_preserves_report_file_and_snapshots(er_session):
    view, file, row = report(er_session)
    original = dict(row.filters_snapshot)
    view.filters = {"period": "quarter"}
    er_session.commit()
    er_session.delete(view)
    er_session.commit()
    er_session.expire_all()
    assert row.view_id is None and row.file is file and row.filters_snapshot == original
    row.name = "Renamed report"
    er_session.commit()
    assert row.name == "Renamed report"


@pytest.mark.parametrize("field,value", [
    ("shipment_count", 2), ("filters_snapshot", {"period": "quarter"}),
    ("columns_snapshot", ["pieces"]), ("metrics_snapshot", {"shipments": 2}), ("format", "csv"),
])
def test_generated_report_snapshots_cannot_be_edited(er_session, field, value):
    view, file, row = report(er_session)
    with pytest.raises(IntegrityError):
        er_session.execute(update(MisReport).where(MisReport.id == row.id).values(**{field: value}))


def test_a_file_can_belong_to_only_one_report(er_session):
    view, file, row = report(er_session)
    with pytest.raises(IntegrityError):
        er_session.add(MisReport(file_id=file.id, name="Duplicate", format="pdf", shipment_count=0))
        er_session.flush()


@pytest.mark.parametrize("values", [{"filters": []}, {"selected_columns": {}}, {"preferred_format": "html"}])
def test_invalid_report_settings_are_rejected(er_session, values):
    with pytest.raises(IntegrityError):
        er_session.add(MisView(name="Invalid", **values))
        er_session.flush()


def test_audit_survives_deleted_entity_but_referenced_user_is_retained(er_session):
    user = AdminUser(username="operator")
    row = shipment(er_session)
    audit = AuditLog(actor=user, entity_type="consignments", entity_id=row.id, action="delete", before_values={"pieces": 1})
    er_session.add(audit)
    er_session.commit()
    er_session.delete(row)
    er_session.commit()
    assert er_session.get(AuditLog, audit.id) is audit
    user.active = False
    er_session.commit()
    with pytest.raises(IntegrityError):
        er_session.delete(user)
        er_session.flush()


def test_timezone_round_trip_and_naive_datetime_rejection(er_session):
    local = datetime(2026, 10, 10, 15, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    row = shipment(er_session, actual_pickup_at=local)
    er_session.commit()
    assert row.actual_pickup_at == datetime(2026, 10, 10, 9, 30, tzinfo=UTC)
    with pytest.raises(StatementError, match="timezone-aware"):
        shipment(er_session, actual_pickup_at=datetime(2026, 10, 10))


def test_raw_sql_defaults_generate_ids(er_engine):
    with er_engine.begin() as connection:
        connection.exec_driver_sql("INSERT INTO admin_users (username) VALUES ('raw-sql-user')")
        row = connection.execute(select(AdminUser.__table__)).one()
        assert isinstance(row.id, UUID) and row.active is True and row.role == "operator"


def test_schema_creation_can_be_repeated(er_engine):
    Base.metadata.create_all(er_engine)


def test_target_schema_is_separate_from_active_app_metadata():
    from app.models import db
    assert set(Base.metadata.tables) == {
        "admin_users", "companies", "company_locations", "consignments", "shipment_events",
        "files", "shipment_documents", "mis_views", "mis_reports", "audit_logs",
    }
    assert not (set(Base.metadata.tables) & set(db.metadata.tables))


def test_published_sql_matches_the_models():
    path = Path(__file__).resolve().parents[1] / "docs" / "database-schema.sql"
    assert path.read_text() == postgresql_ddl()


def test_generated_sql_executes_on_postgresql(er_engine):
    if er_engine.dialect.name != "postgresql":
        pytest.skip("Requires the optional isolated PostgreSQL test database")
    Base.metadata.drop_all(er_engine)
    with er_engine.connect() as connection:
        connection.exec_driver_sql(postgresql_ddl())
    assert set(inspect(er_engine).get_table_names()) == set(Base.metadata.tables)
    with er_engine.begin() as connection:
        connection.exec_driver_sql("INSERT INTO admin_users (username) VALUES ('ddl-user')")
    with Session(er_engine) as session:
        company, pickup, drop = presets(session)
        with pytest.raises(IntegrityError):
            shipment(session, company_id=company.id, pickup_location_id=drop.id)
