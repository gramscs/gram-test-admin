"""ER-backed compatibility fields for the existing shipment screens.

The ten business tables remain authoritative. UI-only legacy fields are editable
in a small support table, while the complete original migration archive stays
untouched. Canonical file metadata and versioned documents use the ER tables.
"""
from datetime import UTC, date, datetime
import json
from pathlib import Path
from uuid import UUID, uuid4

from flask import current_app, session, has_request_context
from sqlalchemy import String, Uuid, and_, cast, func, inspect, insert, select, text
from sqlalchemy.orm import DeclarativeBase, mapped_column, relationship, foreign, synonym
from sqlalchemy.ext.hybrid import hybrid_property

from app.models import db
from database.models import (Base, Company, CompanyLocation, Consignment, MisView,
    MisReport, AdminUser, File, ShipmentDocument, ShipmentEvent, AuditLog, JSON_DATA)
from migrations.shipment_v1 import COLUMN_MAP, RECORDS, json_value
from migrations.storage import Storage


class ExtraBase(DeclarativeBase):
    pass


class RecordExtras(ExtraBase):
    __tablename__ = 'admin_record_extras'
    entity_type = mapped_column(String(80), primary_key=True)
    entity_id = mapped_column(Uuid, primary_key=True)
    data = mapped_column(JSON_DATA, nullable=False, default=dict)


def _attach_extras(model):
    model.extra_record = relationship(RecordExtras,
        primaryjoin=and_(foreign(RecordExtras.entity_id) == model.id,
                         RecordExtras.entity_type == model.__tablename__),
        uselist=False, lazy='selectin', cascade='all, delete-orphan', single_parent=True, overlaps='extra_record')


def _get_extra(row, key):
    return (row.extra_record.data or {}).get(key) if row.extra_record else None


def _set_extra(row, key, value):
    if row.id is None:
        row.id = uuid4()
    if row.extra_record is None:
        row.extra_record = RecordExtras(entity_type=row.__tablename__, entity_id=row.id, data={})
    row.extra_record.data = {**row.extra_record.data, key: json_value(value)}


def _extra_expression(model, key):
    return select(RecordExtras.data[key].as_string()).where(
        RecordExtras.entity_type == model.__tablename__, RecordExtras.entity_id == model.id).scalar_subquery()


def _extra_field(model, key):
    def get(row):
        return _get_extra(row, key)
    def set_value(row, value):
        _set_extra(row, key, value)
    setattr(model, key, hybrid_property(get, set_value).expression(lambda cls: _extra_expression(cls, key)))


for _model in (Consignment, MisView, MisReport):
    _attach_extras(_model)
Company.address = synonym('registered_address')
Consignment.consignment_number = synonym('identifier_value')
Consignment.pickup_address = synonym('pickup_address_snapshot')
Consignment.drop_address = synonym('drop_address_snapshot')


def _status_get(row):
    return row.current_status


def _status_set(row, value):
    row.current_status = str(value or '').strip() or 'No status'


Consignment.status = hybrid_property(_status_get, _status_set).expression(lambda cls: cls.current_status)
for _key in ('pickup_pincode', 'drop_pincode', 'pickup_tag', 'drop_tag', 'eta', 'eta_debug_json'):
    _extra_field(Consignment, _key)


def _date_field(key, typed):
    def get(row):
        raw = _get_extra(row, key)
        value = getattr(row, typed)
        return raw if raw is not None else value.isoformat() if value else None
    def set_value(row, value):
        from app.admin.reporting import parse_date
        _set_extra(row, key, value)
        setattr(row, typed, parse_date(value))
    setattr(Consignment, key, hybrid_property(get, set_value).expression(
        lambda cls: func.coalesce(_extra_expression(cls, key), cast(getattr(cls, typed), String))))


_date_field('pickup_date', 'planned_pickup_date')
_date_field('drop_date', 'expected_delivery_date')
_extra_field(MisReport, 'notes')
_extra_field(MisView, 'updated_at')
MisView.output_format = synonym('preferred_format')
MisReport.output_format = synonym('format')
MisReport.row_count = synonym('shipment_count')


def _json_alias(model, old, new, columns=False):
    def get(row):
        value = getattr(row, new)
        if columns and value:
            reverse = {v: k for k, v in COLUMN_MAP.items()}
            value = [reverse.get(key, key) for key in value]
        return json.dumps(value)
    def set_value(row, value):
        decoded = json.loads(value) if isinstance(value, str) else value
        if columns:
            decoded = [COLUMN_MAP.get(key, key) for key in decoded]
        setattr(row, new, decoded)
    setattr(model, old, property(get, set_value))


for _model, _fields in ((MisView, {'filters_json': 'filters', 'columns_json': 'selected_columns'}),
                        (MisReport, {'filters_json': 'filters_snapshot', 'columns_json': 'columns_snapshot', 'summary_json': 'metrics_snapshot'})):
    for _old, _new in _fields.items():
        _json_alias(_model, _old, _new, columns=_old == 'columns_json')


def actor_id():
    if not has_request_context():
        return None
    username = session.get('admin_username')
    if not username:
        return None
    with db.session.no_autoflush:
        user = db.session.query(AdminUser).filter_by(username=username, active=True).first()
    if user is None:
        raise ValueError('The signed-in admin has no active identity in this database. Check ADMIN_USERNAME.')
    return user.id


def register_file(reference, original_name=None, mime=None):
    from app.admin.consignment_controller import _get_supabase_client
    storage = Storage(Path(current_app.instance_path, 'uploads'), _get_supabase_client())
    reference = reference.removeprefix('local:')
    metadata = storage.describe(reference)
    with db.session.no_autoflush:
        existing = db.session.query(File).filter_by(storage_path=metadata.path).first()
    if existing:
        return existing
    row = File(id=uuid4(), uploaded_by=actor_id(), storage_bucket=metadata.bucket,
        storage_path=metadata.path, original_filename=original_name or metadata.filename,
        mime_type=mime or metadata.mime_type, size_bytes=metadata.size_bytes,
        checksum=metadata.checksum, uploaded_at=datetime.now(UTC))
    db.session.add(row)
    return row


def current_document(row, kind):
    return next((doc for doc in row.documents if doc.document_type == kind and doc.is_current), None)


def set_document(row, kind, file):
    old = current_document(row, kind)
    if old:
        old.is_current = False
        # Retire first, before the unique current-version index sees the new row.
        db.session.flush()
    if file:
        version = max((doc.version for doc in row.documents if doc.document_type == kind), default=0) + 1
        row.documents.append(ShipmentDocument(id=uuid4(), file=file,
            document_type=kind, version=version, is_current=True))


def _document_fields(kind, field, name_field):
    def get(row):
        doc = current_document(row, kind)
        return doc.file.storage_path if doc else None
    def set_reference(row, reference):
        set_document(row, kind, register_file(reference) if reference else None)
    def get_name(row):
        doc = current_document(row, kind)
        return doc.file.original_filename if doc else None
    def set_name(row, value):
        doc = current_document(row, kind)
        if doc and value:
            doc.file.original_filename = value
    setattr(Consignment, field, property(get, set_reference))
    setattr(Consignment, name_field, property(get_name, set_name))


_document_fields('POD', 'pod_image', 'pod_original_name')
_document_fields('invoice', 'invoice_file', 'invoice_original_name')
MisReport.file_ref = property(lambda row: row.file.storage_path if row.file else None,
    lambda row, value: setattr(row, 'file', register_file(value)))
MisReport.file_name = property(lambda row: row.file.original_filename if row.file else None,
    lambda row, value: setattr(row.file, 'original_filename', value))


EXTRA_FIELDS = {'consignments': ('pickup_pincode', 'drop_pincode', 'pickup_tag', 'drop_tag', 'pickup_date', 'drop_date', 'eta', 'eta_debug_json'),
                'mis_reports': ('notes',), 'mis_views': ('updated_at',)}


def prepare_backend(engine):
    """Create UI bookkeeping and copy archived legacy fields once, atomically."""
    with engine.begin() as connection:
        if connection.dialect.name == 'postgresql':
            connection.execute(text("SET LOCAL lock_timeout = '15s'"))
            connection.execute(text("SET LOCAL statement_timeout = '120s'"))
            connection.execute(text('SELECT pg_advisory_xact_lock(723910042002)'))
        missing = set(Base.metadata.tables) - set(inspect(connection).get_table_names())
        if missing:
            raise ValueError('The ER migration is incomplete. Missing model tables: ' + ', '.join(sorted(missing)))
        ExtraBase.metadata.create_all(connection)
        if connection.dialect.name == 'postgresql':
            connection.execute(text('ALTER TABLE admin_record_extras ENABLE ROW LEVEL SECURITY'))
        existing = {(row.entity_type, row.entity_id) for row in connection.execute(select(RecordExtras.__table__))}
        if inspect(connection).has_table(RECORDS.name):
            # A subsequent ER-to-ER import archives this support table too.
            # Restore its editable values before considering original legacy fields.
            archived_extras = connection.execute(select(RECORDS.c.source_values).where(
                RECORDS.c.source_table == RecordExtras.__tablename__)).scalars().all()
            for values in archived_extras:
                entity_type = values.get('entity_type')
                if entity_type not in EXTRA_FIELDS or not isinstance(values.get('data'), dict):
                    continue
                entity_id = UUID(str(values['entity_id']))
                key = entity_type, entity_id
                if key not in existing and connection.execute(select(Base.metadata.tables[entity_type].c.id).where(
                        Base.metadata.tables[entity_type].c.id == entity_id)).first():
                    connection.execute(insert(RecordExtras.__table__).values(entity_type=entity_type,
                        entity_id=entity_id, data=values['data']))
                    existing.add(key)
            for row in connection.execute(select(RECORDS).where(RECORDS.c.target_table.in_(EXTRA_FIELDS))).mappings():
                key = row['target_table'], row['target_id']
                if key in existing:
                    continue
                values = {name: row['source_values'][name] for name in EXTRA_FIELDS[key[0]] if name in row['source_values']}
                if values:
                    connection.execute(insert(RecordExtras.__table__).values(entity_type=key[0], entity_id=key[1], data=values))
                    existing.add(key)


def install_tracking():
    from flask import has_app_context
    from sqlalchemy import event
    from sqlalchemy.orm import Session
    from sqlalchemy import inspect as orm_inspect
    from app.orm import using_er

    @event.listens_for(Session, 'before_flush')
    def track_changes(database_session, flush_context, instances):
        if not has_app_context() or not using_er():
            return
        candidates = list(database_session.new | database_session.dirty | database_session.deleted)
        candidates = [row for row in candidates if isinstance(row, (Company, CompanyLocation,
            Consignment, File, ShipmentDocument, MisView, MisReport, RecordExtras))]
        if not candidates:
            return
        actor, moment = actor_id(), datetime.now(UTC)
        for row in candidates:
            if not isinstance(row, RecordExtras) and row.id is None:
                row.id = uuid4()
        for row in candidates:
            state = orm_inspect(row)
            is_new = row in database_session.new
            is_deleted = row in database_session.deleted
            if isinstance(row, CompanyLocation) and row.company:
                row.company_id = row.company.id
            if isinstance(row, ShipmentDocument):
                if row.consignment:
                    row.consignment_id = row.consignment.id
                if row.file:
                    row.file_id = row.file.id
            if isinstance(row, MisReport) and row.file:
                row.file_id = row.file.id
            if isinstance(row, Consignment) and not is_deleted and (is_new or state.attrs.current_status.history.has_changes()):
                row.current_status = row.current_status or 'No status'
                database_session.add(ShipmentEvent(id=uuid4(), consignment=row,
                    recorded_by=actor, status=row.current_status,
                    occurred_at=moment, recorded_at=moment, notes='Recorded by the admin dashboard.'))
            before, after = {}, {}
            for column in state.mapper.columns:
                history = state.attrs[column.key].history
                value = getattr(row, column.key)
                if is_new:
                    after[column.key] = json_value(value)
                elif is_deleted:
                    before[column.key] = json_value(value)
                elif history.has_changes():
                    before[column.key] = json_value(history.deleted[0]) if history.deleted else None
                    after[column.key] = json_value(value)
            if not is_new and not is_deleted and not after:
                continue
            entity_type = row.entity_type if isinstance(row, RecordExtras) else row.__tablename__
            entity_id = row.entity_id if isinstance(row, RecordExtras) else row.id
            database_session.add(AuditLog(id=uuid4(), actor_id=actor, entity_type=entity_type,
                entity_id=entity_id, action='create' if is_new else 'delete' if is_deleted else 'update',
                before_values=before or None, after_values=after or None, recorded_at=moment))


install_tracking()
