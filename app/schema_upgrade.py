"""Additive upgrade for existing local and PostgreSQL shipment tables."""

from sqlalchemy import inspect, text


def upgrade_consignment_fields(engine):
    added = []
    with engine.begin() as connection:
        inspector = inspect(connection)
        if not inspector.has_table("consignment"):
            return added
        columns = {column["name"]: column for column in inspector.get_columns("consignment")}
        definitions = {
            "identifier_type": "VARCHAR(12) NOT NULL DEFAULT 'LRN'",
            "pieces": "INTEGER NOT NULL DEFAULT 1",
            "chargeable_weight": "NUMERIC(12, 3)",
            "chargeable_volume": "NUMERIC(12, 3)",
            "company_id": "INTEGER REFERENCES company(id)",
        }
        for name, definition in definitions.items():
            if name not in columns:
                connection.execute(text(f'ALTER TABLE consignment ADD COLUMN {name} {definition}'))
                added.append(name)
        if engine.dialect.name == "postgresql" and getattr(columns["consignment_number"]["type"], "length", None) is not None and columns["consignment_number"]["type"].length < 64:
            connection.execute(text("ALTER TABLE consignment ALTER COLUMN consignment_number TYPE VARCHAR(64)"))
            added.append("consignment_number length")
    return added


def upgrade_admin_fields(engine):
    """Create master tables and add fields without replacing legacy records."""
    from app.models import Company, CompanyLocation
    Company.__table__.create(engine, checkfirst=True)
    CompanyLocation.__table__.create(engine, checkfirst=True)
    added = upgrade_consignment_fields(engine)
    with engine.begin() as connection:
        inspector = inspect(connection)
        if inspector.has_table("lead"):
            columns = {column["name"] for column in inspector.get_columns("lead")}
            for name, definition in {
                "company_name": "VARCHAR(200)",
                "status": "VARCHAR(20) NOT NULL DEFAULT 'New'",
                "notes": "TEXT",
                "follow_up_date": "DATE",
            }.items():
                if name not in columns:
                    connection.execute(text(f'ALTER TABLE lead ADD COLUMN {name} {definition}'))
                    added.append("lead." + name)
    return added
