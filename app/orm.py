"""Select the existing local model or the migrated UUID model per application."""
from uuid import UUID

from flask import current_app
from sqlalchemy import inspect
from flask_sqlalchemy.query import Query

from app import models as legacy


def using_er():
    mode = current_app.config.get('ADMIN_DATABASE_MODEL', 'auto')
    if mode not in ('auto', 'legacy', 'er'):
        raise ValueError('ADMIN_DATABASE_MODEL must be auto, legacy or er.')
    if mode != 'auto':
        return mode == 'er'
    if 'admin_database_model' not in current_app.extensions:
        names = set(inspect(legacy.db.engine).get_table_names())
        # Existing local databases with both versions continue using their
        # operational legacy tables. A migrated fresh target has only ER tables.
        current_app.extensions['admin_database_model'] = 'er' if 'consignments' in names and 'consignment' not in names else 'legacy'
    return current_app.extensions['admin_database_model'] == 'er'


def __getattr__(name):
    if name not in ('Company', 'CompanyLocation', 'Consignment', 'MisView', 'MisReport'):
        raise AttributeError(name)
    if not using_er():
        return getattr(legacy, name)
    from app import er_adapter
    er_adapter.Base.query = legacy.db.session.query_property(query_cls=Query)
    return getattr(er_adapter, name)


def record_id(value):
    if using_er():
        try:
            return UUID(str(value))
        except (ValueError, TypeError, AttributeError):
            raise ValueError('Invalid record identifier.') from None
    if isinstance(value, bool) or not str(value).isdigit() or int(value) < 1:
        raise ValueError('Invalid record identifier.')
    return int(value)


def export_id(value):
    return str(value) if isinstance(value, UUID) else value


def shipment_query():
    """Fetch screen relationships in batches rather than per remote shipment."""
    model = __getattr__('Consignment')
    query = model.query
    if using_er():
        from sqlalchemy.orm import selectinload
        from database.models import ShipmentDocument
        query = query.options(selectinload(model.company),
                              selectinload(model.documents).selectinload(ShipmentDocument.file))
    return query


def tables():
    if not using_er():
        return list(legacy.db.metadata.sorted_tables)
    from app.er_adapter import Base, ExtraBase
    from migrations.shipment_v1 import LEDGER
    return [*Base.metadata.sorted_tables, *ExtraBase.metadata.sorted_tables, *LEDGER.sorted_tables]
