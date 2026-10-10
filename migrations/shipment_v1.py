"""Atomic legacy -> ER migration, with complete row archives and retry checks.

The CLI entry point is migrations/001_shipment_database.py. This module never
imports Flask or uses its automatic schema creation. It never modifies uploads.
"""

from contextlib import ExitStack
from datetime import UTC, date, datetime
from decimal import Decimal
import hashlib
import json
from uuid import UUID, uuid4, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import (
    Column, ForeignKey, JSON, MetaData, String, Table, Uuid,
    create_engine, event, inspect, insert, select, text, update,
)
from sqlalchemy.dialects.postgresql import JSONB

from database.models import Base, UTCDateTime
from migrations.storage import MigrationError, Storage

VERSION = "001_shipment_database"
LEGACY_TABLES = ("company", "company_location", "consignment", "mis_view", "mis_report", "lead", "newsletter_subscriber")
COLUMN_MAP = {
    "consignment_number": "identifier_value", "status": "current_status",
    "pickup_date": "planned_pickup_date", "drop_date": "expected_delivery_date",
    "pickup_address": "pickup_address_snapshot", "drop_address": "drop_address_snapshot",
}
TARGET_MAP = {
    "company": "companies", "company_location": "company_locations",
    "consignment": "consignments", "mis_view": "mis_views", "mis_report": "mis_reports",
}
LEDGER = MetaData()
JSON_DATA = JSON().with_variant(JSONB(), "postgresql")
RUNS = Table("admin_migration_runs", LEDGER,
    Column("version", String(80), primary_key=True),
    Column("namespace", Uuid, nullable=False),
    Column("source_digest", String(64), nullable=False),
    Column("target_digest", String(64), nullable=False),
    Column("source_schema", JSON_DATA, nullable=False),
    Column("counts", JSON_DATA, nullable=False),
    Column("warnings", JSON_DATA, nullable=False),
    Column("completed_at", UTCDateTime(), nullable=False),
)
RECORDS = Table("admin_migration_records", LEDGER,
    Column("version", String(80), ForeignKey("admin_migration_runs.version"), primary_key=True),
    Column("source_table", String(80), primary_key=True),
    Column("source_id", String(80), primary_key=True),
    Column("target_table", String(80)),
    Column("target_id", Uuid),
    Column("source_values", JSON_DATA, nullable=False),
)


def json_value(value):
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (date, Decimal, UUID)):
        return str(value)
    if isinstance(value, bytes):
        # Retain binary values in unforeseen legacy columns without losing them.
        import base64
        return {"encoding": "base64", "value": base64.b64encode(value).decode("ascii")}
    return value


def digest(value):
    content = json.dumps(json_value(value), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(content.encode()).hexdigest()


def migration_engine(url):
    from sqlalchemy.engine import make_url
    settings = {"connect_timeout": 10} if make_url(url).get_backend_name() == "postgresql" else {}
    engine = create_engine(url, hide_parameters=True, pool_pre_ping=True, connect_args=settings)
    if engine.dialect.name not in {"sqlite", "postgresql"}:
        engine.dispose()
        raise MigrationError("Only SQLite and PostgreSQL databases are supported.")
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def on_connect(connection, record):
            connection.isolation_level = None
            connection.execute("PRAGMA foreign_keys=ON")

        @event.listens_for(engine, "begin")
        def on_begin(connection):
            # Explicit BEGIN is essential for DDL rollback. IMMEDIATE also
            # freezes a separate WAL-mode source against concurrent writers.
            # No source DML is performed, but its file must allow locking.
            connection.exec_driver_sql("BEGIN IMMEDIATE")
    return engine


def read_legacy(connection):
    inspector = inspect(connection)
    names = [name for name in LEGACY_TABLES if inspector.has_table(name)]
    if "consignment" not in names:
        raise MigrationError("The source has no legacy consignment table. Check the source database setting.")
    if connection.dialect.name == "postgresql":
        quoted = ", ".join(connection.dialect.identifier_preparer.quote(name) for name in names)
        connection.exec_driver_sql(f"LOCK TABLE {quoted} IN SHARE MODE")
    schema, rows = {}, {}
    for name in names:
        table = Table(name, MetaData(), autoload_with=connection)
        if "id" not in table.c:
            raise MigrationError(f"Legacy table {name} has no id column.")
        schema[name] = [{"name": col.name, "type": str(col.type), "nullable": col.nullable,
                         "primary_key": col.primary_key} for col in table.c]
        rows[name] = [dict(row) for row in connection.execute(select(table).order_by(table.c.id)).mappings()]
        if len({str(row["id"]) for row in rows[name]}) != len(rows[name]):
            raise MigrationError(f"Legacy table {name} has duplicate IDs.")
    return schema, rows


def parsed_date(value, warnings, label):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.astimezone(ZoneInfo("Asia/Kolkata")).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                return datetime.strptime(stripped, pattern).date()
            except ValueError:
                pass
        try:
            return parsed_date(datetime.fromisoformat(stripped.replace("Z", "+00:00")), warnings, label)
        except ValueError:
            pass
    warnings.append(f"{label}: unrecognized date retained in the migration archive; typed date left null.")
    return None


def timestamp(value, label):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise MigrationError(f"{label}: invalid timestamp.") from None
    if not isinstance(value, datetime):
        raise MigrationError(f"{label}: missing timestamp.")
    # The legacy app writes UTC datetimes, but SQLite drops their timezone.
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value).astimezone(UTC)


def decoded(value, expected_type, label):
    try:
        result = json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        raise MigrationError(f"{label}: invalid JSON settings.") from None
    if not isinstance(result, expected_type):
        raise MigrationError(f"{label}: unexpected JSON settings type.")
    return result


def build_plan(source_rows, storage, namespace, admin_username, now):
    rows = {name: [] for name in Base.metadata.tables}
    warnings, archive = [], []

    def identity(table, key):
        return uuid5(namespace, f"{table}:{key}")

    companies = {str(row["id"]): identity("companies", row["id"]) for row in source_rows.get("company", [])}
    users = {admin_username: identity("admin_users", admin_username)}
    for table, key in (("mis_view", "created_by"), ("mis_report", "generated_by")):
        for record in source_rows.get(table, []):
            if record.get(key):
                username = str(record[key])
                users[username] = identity("admin_users", username)
    for username, id in sorted(users.items()):
        rows["admin_users"].append({"id": id, "username": username,
            "role": "administrator" if username == admin_username else "operator",
            "active": username == admin_username})

    def actor(value):
        return users.get(str(value)) if value else None

    def company_ref(value, label):
        if value is None:
            return None
        if str(value) not in companies:
            raise MigrationError(f"{label}: the referenced client does not exist.")
        return companies[str(value)]

    def settings(record):
        filters = dict(decoded(record.get("filters_json"), dict, "MIS filters"))
        company = filters.get("company")
        if company not in (None, "", "unassigned"):
            if str(company) in companies:
                filters["company"] = str(companies[str(company)])
            else:
                warnings.append("A MIS filter refers to a removed client; its original value is retained for review.")
        columns = decoded(record.get("columns_json"), list, "MIS columns")
        if any(not isinstance(key, str) for key in columns):
            raise MigrationError("MIS selected columns must be strings.")
        return filters, [COLUMN_MAP.get(key, key) for key in columns]

    references = {}
    for record in source_rows.get("consignment", []):
        for field, name_field in (("pod_image", "pod_original_name"), ("invoice_file", "invoice_original_name")):
            if record.get(field):
                references.setdefault(record[field], record.get(name_field))
    for record in source_rows.get("mis_report", []):
        if not record.get("file_ref"):
            raise MigrationError("A MIS report has no file reference.")
        references.setdefault(record["file_ref"], record.get("file_name"))
    all_references = set(references)
    all_references.update(storage.inventory(all_references))
    files, manifest = {}, []
    for reference in sorted(all_references):
        metadata = storage.describe(reference)
        if metadata.path not in files:
            id = identity("files", metadata.path)
            files[metadata.path] = id
            rows["files"].append({"id": id, "uploaded_by": None, "storage_bucket": metadata.bucket,
                "storage_path": metadata.path, "original_filename": references.get(reference) or metadata.filename,
                "mime_type": metadata.mime_type, "size_bytes": metadata.size_bytes,
                "checksum": metadata.checksum, "uploaded_at": None})
            manifest.append({"path": metadata.path, "size": metadata.size_bytes, "checksum": metadata.checksum})

    def file_ref(reference):
        return files[storage.location(reference)[1]]

    for name in LEGACY_TABLES:
        for original in source_rows.get(name, []):
            id = identity(TARGET_MAP.get(name, "archive." + name), original["id"])
            target, record = TARGET_MAP.get(name), None
            if name == "company":
                record = {"id": id, "name": original.get("name"), "registered_address": original.get("address"),
                    "email": original.get("email"), "phone": original.get("phone"), "active": original.get("active", True)}
            elif name == "company_location":
                record = {"id": id, "company_id": company_ref(original.get("company_id"), "Company location"),
                    "kind": original.get("kind"), "label": original.get("label"), "address": original.get("address"),
                    "pincode": original.get("pincode"), "is_default": original.get("is_default", False)}
            elif name == "consignment":
                record = {"id": id, "company_id": company_ref(original.get("company_id"), "Consignment"),
                    "identifier_type": original.get("identifier_type", "LRN"), "identifier_value": original.get("consignment_number"),
                    "current_status": original.get("status") if str(original.get("status") or "").strip() else "No status",
                    "pieces": original.get("pieces", 1), "chargeable_weight": original.get("chargeable_weight"),
                    "chargeable_volume": original.get("chargeable_volume"),
                    "pickup_address_snapshot": original.get("pickup_address"), "drop_address_snapshot": original.get("drop_address"),
                    "planned_pickup_date": parsed_date(original.get("pickup_date"), warnings, f"Consignment {original['id']} pickup"),
                    "expected_delivery_date": parsed_date(original.get("drop_date"), warnings, f"Consignment {original['id']} drop"),
                    "pickup_location_id": None, "drop_location_id": None, "actual_pickup_at": None, "actual_delivery_at": None}
                for field, kind in (("pod_image", "POD"), ("invoice_file", "invoice")):
                    if original.get(field):
                        rows["shipment_documents"].append({"id": identity("shipment_documents", f"{original['id']}:{kind}"),
                            "consignment_id": id, "file_id": file_ref(original[field]), "document_type": kind, "version": 1, "is_current": True})
            elif name == "mis_view":
                filters, columns = settings(original)
                record = {"id": id, "created_by": actor(original.get("created_by")), "name": original.get("name"),
                    "filters": filters, "selected_columns": columns, "preferred_format": original.get("output_format"),
                    "notes": original.get("notes")}
            elif name == "mis_report":
                filters, columns = settings(original)
                record = {"id": id, "view_id": None, "generated_by": actor(original.get("generated_by")),
                    "file_id": file_ref(original["file_ref"]), "name": original.get("name"), "format": original.get("output_format"),
                    "filters_snapshot": filters, "columns_snapshot": columns,
                    "metrics_snapshot": decoded(original.get("summary_json"), dict, "MIS metrics"),
                    "shipment_count": original.get("row_count"), "generated_at": timestamp(original.get("generated_at"), "MIS report")}
            if record is not None:
                rows[target].append(record)
            archive.append({"version": VERSION, "source_table": name, "source_id": str(original["id"]),
                "target_table": target, "target_id": id if target else None, "source_values": json_value(original)})
            rows["audit_logs"].append({"id": identity("audit_logs", f"{name}:{original['id']}"),
                "actor_id": users[admin_username], "entity_type": target or "archive." + name, "entity_id": id,
                "action": "migrate", "before_values": None,
                "after_values": json_value(record) if record is not None else {"archived_source_table": name, "archived_source_id": str(original["id"])},
                "recorded_at": now})
    return rows, archive, manifest, warnings


def insert_rows(connection, rows):
    # SQLAlchemy executemany expects consistent keys per batch. Missing optional
    # columns are inserted individually, avoiding an accidental default rewrite.
    for table in Base.metadata.sorted_tables:
        for record in rows[table.name]:
            connection.execute(insert(table).values(**record))


def validate_plan(rows):
    for name, records in rows.items():
        for record in records:
            for field, value in record.items():
                column = Base.metadata.tables[name].c[field]
                if isinstance(column.type, String) and value is not None:
                    if not isinstance(value, str) or (column.type.length and len(value) > column.type.length):
                        raise MigrationError(f"{name}.{field}: invalid text or value too long.")
            for field in ("chargeable_weight", "chargeable_volume"):
                if record.get(field) is not None:
                    value = Decimal(str(record[field]))
                    if not value.is_finite() or abs(value) > Decimal("999999999.999") or value != value.quantize(Decimal("0.001")):
                        raise MigrationError(f"{name}.{field}: outside the supported decimal range/precision.")
            for field, minimum in (("pieces", 1), ("shipment_count", 0)):
                if field in record and (not isinstance(record[field], int) or isinstance(record[field], bool)
                        or not minimum <= record[field] <= 2147483647):
                    raise MigrationError(f"{name}.{field}: invalid integer count.")
    engine = migration_engine("sqlite://")
    try:
        with engine.begin() as connection:
            Base.metadata.create_all(connection)
            insert_rows(connection, rows)
    except MigrationError:
        raise
    except Exception:
        raise MigrationError("Source records violate the ER model. Check unique identifiers, defaults, document/report file sharing, required values and numeric ranges.") from None
    finally:
        engine.dispose()


def target_snapshot(connection):
    return {table.name: [dict(row) for row in connection.execute(select(table).order_by(table.c.id)).mappings()]
            for table in Base.metadata.sorted_tables}


def migrate(engine, storage, *, source_engine=None, admin_username="admin", dry_run=False):
    if not admin_username or not admin_username.strip():
        raise MigrationError("ADMIN_USERNAME must not be blank.")
    now = datetime.now(UTC)
    with ExitStack() as stack:
        source = stack.enter_context(source_engine.begin()) if source_engine is not None else None
        target = stack.enter_context(engine.begin())
        source = source if source is not None else target
        if target.dialect.name == "postgresql":
            target.execute(text("SELECT pg_advisory_xact_lock(723910042001)"))
        schema, source_rows = read_legacy(source)
        inspector = inspect(target)
        completed = target.execute(select(RUNS).where(RUNS.c.version == VERSION)).mappings().first() if inspector.has_table(RUNS.name) else None
        existing = set(inspector.get_table_names()) & set(Base.metadata.tables)
        if not completed and (existing or inspector.has_table(RECORDS.name) or inspector.has_table(RUNS.name)):
            raise MigrationError("Unmanaged ER/migration tables already exist. Use a clean target database/schema rather than merging or replacing them.")
        namespace = completed["namespace"] if completed else uuid4()
        rows, archive, manifest, warnings = build_plan(source_rows, storage, namespace, admin_username, now)
        source_digest = digest({"schema": schema, "rows": source_rows, "files": manifest, "admin_username": admin_username})
        if completed:
            if target.dialect.name == "postgresql":
                tables = sorted(set(Base.metadata.tables) | set(LEDGER.tables))
                quoted = ", ".join(target.dialect.identifier_preparer.quote(name) for name in tables)
                target.exec_driver_sql(f"LOCK TABLE {quoted} IN SHARE MODE")
            if source_digest != completed["source_digest"]:
                raise MigrationError("The source data/files changed after migration. This one-time migration will not overwrite the completed import.")
            if digest(target_snapshot(target)) != completed["target_digest"]:
                raise MigrationError("The migrated ER records changed. The completed import will not be overwritten.")
            if digest([dict(row) for row in target.execute(select(RECORDS).where(RECORDS.c.version == VERSION)
                    .order_by(RECORDS.c.source_table, RECORDS.c.source_id)).mappings()]) != digest(sorted(archive, key=lambda row: (row["source_table"], row["source_id"]))):
                raise MigrationError("The migration archive changed. Check it against your backup.")
            return {"status": "already_applied", "counts": completed["counts"], "warnings": completed["warnings"]}
        validate_plan(rows)
        counts = {"source": {name: len(records) for name, records in source_rows.items()},
                  "target": {name: len(records) for name, records in rows.items()}, "archived_rows": len(archive)}
        if dry_run:
            return {"status": "ready", "counts": counts, "warnings": warnings}
        Base.metadata.create_all(target)
        LEDGER.create_all(target)
        if target.dialect.name == "postgresql":
            # Supabase's public schema may have browser/API grants by default.
            # Owner/backend access still works; other roles have no policies.
            for name in sorted(set(Base.metadata.tables) | set(LEDGER.tables)):
                quoted = target.dialect.identifier_preparer.quote(name)
                target.exec_driver_sql(f"ALTER TABLE {quoted} ENABLE ROW LEVEL SECURITY")
        insert_rows(target, rows)
        snapshot = target_snapshot(target)
        if {name: len(records) for name, records in snapshot.items()} != counts["target"]:
            raise MigrationError("Target reconciliation failed; the entire migration will be rolled back.")
        target.execute(insert(RUNS).values(version=VERSION, namespace=namespace, source_digest=source_digest,
            target_digest=digest(snapshot), source_schema=schema, counts=counts, warnings=warnings, completed_at=now))
        for record in archive:
            target.execute(insert(RECORDS).values(**record))
        # Detect unexpected database changes even on a separate source connection.
        final_schema, final_rows = read_legacy(source)
        if digest({"schema": final_schema, "rows": final_rows}) != digest({"schema": schema, "rows": source_rows}):
            raise MigrationError("The source changed during migration; all target changes will be rolled back.")
    return {"status": "applied", "counts": counts, "warnings": warnings}
