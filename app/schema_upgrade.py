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
        }
        for name, definition in definitions.items():
            if name not in columns:
                connection.execute(text(f'ALTER TABLE consignment ADD COLUMN {name} {definition}'))
                added.append(name)
        if engine.dialect.name == "postgresql" and getattr(columns["consignment_number"]["type"], "length", None) is not None and columns["consignment_number"]["type"].length < 64:
            connection.execute(text("ALTER TABLE consignment ALTER COLUMN consignment_number TYPE VARCHAR(64)"))
            added.append("consignment_number length")
    return added
