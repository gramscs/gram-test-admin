"""SQLAlchemy implementation of the ten-table shipment ER diagram.

Importing this module never connects to a database or registers tables with
the legacy Flask application. PostgreSQL is the production target; SQLite is
supported for isolated tests. See docs/database-model.md before any migration.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey,
    ForeignKeyConstraint, Index, Integer, JSON, MetaData, Numeric, String,
    Text, UniqueConstraint, Uuid, false, func, text, true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql.functions import FunctionElement
from sqlalchemy.types import TypeDecorator


class RandomUUID(FunctionElement):
    type = Uuid()
    inherit_cache = True


@compiles(RandomUUID, "postgresql")
def _postgres_uuid(element, compiler, **kw):
    return "gen_random_uuid()"


@compiles(RandomUUID, "sqlite")
def _sqlite_uuid(element, compiler, **kw):
    # The ORM uses uuid4(); this fallback also supports raw SQL inserts.
    return "(lower(hex(randomblob(16))))"


class JsonKind(FunctionElement):
    type = String()
    inherit_cache = True


@compiles(JsonKind, "postgresql")
def _postgres_json_kind(element, compiler, **kw):
    return "jsonb_typeof(%s)" % compiler.process(element.clauses, **kw)


@compiles(JsonKind, "sqlite")
def _sqlite_json_kind(element, compiler, **kw):
    return "json_type(%s)" % compiler.process(element.clauses, **kw)


class UTCDateTime(TypeDecorator):
    """Reject ambiguous naive dates; restore UTC on SQLite round trips."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Use a timezone-aware datetime for shipment timestamps.")
        return value.astimezone(UTC)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


JSON_DATA = JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    })


class UUIDRecord:
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4, server_default=RandomUUID())


def utcnow():
    return datetime.now(UTC)


class AdminUser(UUIDRecord, Base):
    __tablename__ = "admin_users"
    __table_args__ = (
        CheckConstraint("trim(username) <> ''", name="username_not_blank"),
        CheckConstraint("role IN ('administrator', 'operator', 'viewer')", name="valid_role"),
    )

    username: Mapped[str] = mapped_column(String(120), unique=True)
    role: Mapped[str] = mapped_column(String(20), default="operator", server_default="operator")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    shipment_events: Mapped[list["ShipmentEvent"]] = relationship(back_populates="recorded_by_user", passive_deletes="all")
    files: Mapped[list["File"]] = relationship(back_populates="uploaded_by_user", passive_deletes="all")
    mis_views: Mapped[list["MisView"]] = relationship(back_populates="created_by_user", passive_deletes="all")
    mis_reports: Mapped[list["MisReport"]] = relationship(back_populates="generated_by_user", passive_deletes="all")
    audit_logs: Mapped[list["AuditLog"]] = relationship(back_populates="actor", passive_deletes="all")


class Company(UUIDRecord, Base):
    __tablename__ = "companies"
    __table_args__ = (CheckConstraint("trim(name) <> ''", name="name_not_blank"),)

    name: Mapped[str] = mapped_column(String(200), unique=True)
    registered_address: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(String(254))
    phone: Mapped[str | None] = mapped_column(String(30))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    locations: Mapped[list["CompanyLocation"]] = relationship(back_populates="company", passive_deletes="all")
    consignments: Mapped[list["Consignment"]] = relationship(back_populates="company", foreign_keys="Consignment.company_id", passive_deletes="all")


class CompanyLocation(UUIDRecord, Base):
    __tablename__ = "company_locations"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_company_locations_company_id_id"),
        CheckConstraint("kind IN ('pickup', 'drop')", name="valid_kind"),
        CheckConstraint("trim(label) <> ''", name="label_not_blank"),
        CheckConstraint("trim(address) <> ''", name="address_not_blank"),
        Index("uq_company_locations_default", "company_id", "kind", unique=True,
              postgresql_where=text("is_default"), sqlite_where=text("is_default")),
    )

    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id", ondelete="RESTRICT"), index=True)
    kind: Mapped[str] = mapped_column(String(10))
    label: Mapped[str] = mapped_column(String(100))
    address: Mapped[str] = mapped_column(Text)
    pincode: Mapped[str | None] = mapped_column(String(16))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    company: Mapped[Company] = relationship(back_populates="locations")
    pickup_consignments: Mapped[list["Consignment"]] = relationship(back_populates="pickup_location", foreign_keys="Consignment.pickup_location_id", passive_deletes="all")
    drop_consignments: Mapped[list["Consignment"]] = relationship(back_populates="drop_location", foreign_keys="Consignment.drop_location_id", passive_deletes="all")


class Consignment(UUIDRecord, Base):
    __tablename__ = "consignments"
    __table_args__ = (
        ForeignKeyConstraint(["company_id", "pickup_location_id"], ["company_locations.company_id", "company_locations.id"], name="fk_consignments_pickup_company", ondelete="RESTRICT"),
        ForeignKeyConstraint(["company_id", "drop_location_id"], ["company_locations.company_id", "company_locations.id"], name="fk_consignments_drop_company", ondelete="RESTRICT"),
        CheckConstraint("company_id IS NOT NULL OR (pickup_location_id IS NULL AND drop_location_id IS NULL)", name="preset_requires_company"),
        CheckConstraint("identifier_type IN ('LRN', 'Order ID', 'AWB')", name="valid_identifier_type"),
        CheckConstraint("trim(identifier_value) <> ''", name="identifier_not_blank"),
        CheckConstraint("trim(current_status) <> ''", name="status_not_blank"),
        CheckConstraint("pieces >= 1", name="positive_pieces"),
        CheckConstraint("chargeable_weight >= 0", name="nonnegative_weight"),
        CheckConstraint("chargeable_volume >= 0", name="nonnegative_volume"),
        CheckConstraint("actual_delivery_at >= actual_pickup_at", name="delivery_after_pickup"),
        Index("ix_consignments_current_status_planned_pickup_date", "current_status", "planned_pickup_date"),
    )

    company_id: Mapped[UUID | None] = mapped_column(ForeignKey("companies.id", ondelete="RESTRICT"), index=True)
    pickup_location_id: Mapped[UUID | None] = mapped_column(ForeignKey("company_locations.id", ondelete="RESTRICT"), index=True)
    drop_location_id: Mapped[UUID | None] = mapped_column(ForeignKey("company_locations.id", ondelete="RESTRICT"), index=True)
    identifier_type: Mapped[str] = mapped_column(String(12), default="LRN", server_default="LRN")
    identifier_value: Mapped[str] = mapped_column(String(64), unique=True)
    current_status: Mapped[str] = mapped_column(String(200), default="Pickup Scheduled", server_default="Pickup Scheduled")
    pieces: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    chargeable_weight: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    chargeable_volume: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    pickup_address_snapshot: Mapped[str | None] = mapped_column(Text)
    drop_address_snapshot: Mapped[str | None] = mapped_column(Text)
    planned_pickup_date: Mapped[date | None] = mapped_column(Date, index=True)
    expected_delivery_date: Mapped[date | None] = mapped_column(Date, index=True)
    actual_pickup_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    actual_delivery_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    company: Mapped[Company | None] = relationship(back_populates="consignments", foreign_keys=[company_id])
    pickup_location: Mapped[CompanyLocation | None] = relationship(back_populates="pickup_consignments", foreign_keys=[pickup_location_id])
    drop_location: Mapped[CompanyLocation | None] = relationship(back_populates="drop_consignments", foreign_keys=[drop_location_id])
    events: Mapped[list["ShipmentEvent"]] = relationship(back_populates="consignment", passive_deletes="all", order_by="(ShipmentEvent.occurred_at, ShipmentEvent.recorded_at)")
    documents: Mapped[list["ShipmentDocument"]] = relationship(back_populates="consignment", passive_deletes="all")


class ShipmentEvent(UUIDRecord, Base):
    __tablename__ = "shipment_events"
    __table_args__ = (
        CheckConstraint("trim(status) <> ''", name="status_not_blank"),
        Index("ix_shipment_events_consignment_occurred_at", "consignment_id", "occurred_at"),
    )

    consignment_id: Mapped[UUID] = mapped_column(ForeignKey("consignments.id", ondelete="CASCADE"))
    recorded_by: Mapped[UUID | None] = mapped_column(ForeignKey("admin_users.id", ondelete="RESTRICT"), index=True)
    status: Mapped[str] = mapped_column(String(200))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime())
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, server_default=func.current_timestamp())
    notes: Mapped[str | None] = mapped_column(Text)
    consignment: Mapped[Consignment] = relationship(back_populates="events")
    recorded_by_user: Mapped[AdminUser | None] = relationship(back_populates="shipment_events")


class File(UUIDRecord, Base):
    __tablename__ = "files"
    __table_args__ = (
        CheckConstraint("trim(storage_bucket) <> '' AND trim(storage_path) <> ''", name="storage_not_blank"),
        CheckConstraint("trim(original_filename) <> '' AND trim(mime_type) <> ''", name="metadata_not_blank"),
        CheckConstraint("size_bytes >= 0", name="nonnegative_size"),
        CheckConstraint("length(checksum) = 64", name="sha256_length"),
    )

    uploaded_by: Mapped[UUID | None] = mapped_column(ForeignKey("admin_users.id", ondelete="RESTRICT"), index=True)
    storage_bucket: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(1024), unique=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    checksum: Mapped[str | None] = mapped_column(String(64))
    uploaded_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), default=utcnow, server_default=func.current_timestamp())
    uploaded_by_user: Mapped[AdminUser | None] = relationship(back_populates="files")
    shipment_documents: Mapped[list["ShipmentDocument"]] = relationship(back_populates="file", passive_deletes="all")
    mis_report: Mapped["MisReport | None"] = relationship(back_populates="file", passive_deletes="all", uselist=False)


class ShipmentDocument(UUIDRecord, Base):
    __tablename__ = "shipment_documents"
    __table_args__ = (
        CheckConstraint("document_type IN ('POD', 'invoice')", name="valid_type"),
        CheckConstraint("version >= 1", name="positive_version"),
        UniqueConstraint("consignment_id", "document_type", "version", name="uq_shipment_documents_version"),
        Index("uq_shipment_documents_current", "consignment_id", "document_type", unique=True,
              postgresql_where=text("is_current"), sqlite_where=text("is_current")),
    )

    consignment_id: Mapped[UUID] = mapped_column(ForeignKey("consignments.id", ondelete="CASCADE"))
    file_id: Mapped[UUID] = mapped_column(ForeignKey("files.id", ondelete="RESTRICT"), index=True)
    document_type: Mapped[str] = mapped_column(String(10))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    consignment: Mapped[Consignment] = relationship(back_populates="documents")
    file: Mapped[File] = relationship(back_populates="shipment_documents")


class MisView(UUIDRecord, Base):
    __tablename__ = "mis_views"
    __table_args__ = (
        CheckConstraint("trim(name) <> ''", name="name_not_blank"),
        CheckConstraint("preferred_format IN ('pdf', 'xlsx', 'csv')", name="valid_format"),
    )

    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("admin_users.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    filters: Mapped[dict] = mapped_column(JSON_DATA, default=dict, server_default="{}")
    selected_columns: Mapped[list] = mapped_column(JSON_DATA, default=list, server_default="[]")
    preferred_format: Mapped[str] = mapped_column(String(10), default="xlsx", server_default="xlsx")
    notes: Mapped[str | None] = mapped_column(Text)
    created_by_user: Mapped[AdminUser | None] = relationship(back_populates="mis_views")
    reports: Mapped[list["MisReport"]] = relationship(back_populates="view", passive_deletes="all")


class MisReport(UUIDRecord, Base):
    __tablename__ = "mis_reports"
    __table_args__ = (
        CheckConstraint("trim(name) <> ''", name="name_not_blank"),
        CheckConstraint("format IN ('pdf', 'xlsx', 'csv')", name="valid_format"),
        CheckConstraint("shipment_count >= 0", name="nonnegative_count"),
    )

    view_id: Mapped[UUID | None] = mapped_column(ForeignKey("mis_views.id", ondelete="SET NULL"), index=True)
    generated_by: Mapped[UUID | None] = mapped_column(ForeignKey("admin_users.id", ondelete="RESTRICT"), index=True)
    file_id: Mapped[UUID] = mapped_column(ForeignKey("files.id", ondelete="RESTRICT"), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    format: Mapped[str] = mapped_column(String(10))
    filters_snapshot: Mapped[dict] = mapped_column(JSON_DATA, default=dict, server_default="{}")
    columns_snapshot: Mapped[list] = mapped_column(JSON_DATA, default=list, server_default="[]")
    metrics_snapshot: Mapped[dict] = mapped_column(JSON_DATA, default=dict, server_default="{}")
    shipment_count: Mapped[int] = mapped_column(Integer)
    generated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, server_default=func.current_timestamp(), index=True)
    view: Mapped[MisView | None] = relationship(back_populates="reports")
    generated_by_user: Mapped[AdminUser | None] = relationship(back_populates="mis_reports")
    file: Mapped[File] = relationship(back_populates="mis_report")


class AuditLog(UUIDRecord, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        CheckConstraint("trim(entity_type) <> '' AND trim(action) <> ''", name="description_not_blank"),
        Index("ix_audit_logs_entity", "entity_type", "entity_id", "recorded_at"),
    )

    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("admin_users.id", ondelete="RESTRICT"), index=True)
    entity_type: Mapped[str] = mapped_column(String(100))
    entity_id: Mapped[UUID] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(String(100))
    before_values: Mapped[dict | None] = mapped_column(JSON_DATA)
    after_values: Mapped[dict | None] = mapped_column(JSON_DATA)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, server_default=func.current_timestamp())
    actor: Mapped[AdminUser | None] = relationship(back_populates="audit_logs")


for _model, _fields in (
    (MisView, {"filters": "object", "selected_columns": "array"}),
    (MisReport, {"filters_snapshot": "object", "columns_snapshot": "array", "metrics_snapshot": "object"}),
    (AuditLog, {"before_values": "object", "after_values": "object"}),
):
    for _field, _kind in _fields.items():
        _model.__table__.append_constraint(CheckConstraint(
            JsonKind(_model.__table__.c[_field]) == _kind, name=f"{_field}_{_kind}",
        ))

# Triggers enforce location kind in raw SQL as well as ORM writes.
from database.integrity import install_integrity_rules

install_integrity_rules(Base.metadata)
