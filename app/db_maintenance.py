import logging
import threading


logger = logging.getLogger(__name__)


CONSIGNMENT_ALTER_STATEMENTS = [
    "ALTER TABLE consignment ADD COLUMN IF NOT EXISTS pickup_address TEXT",
    "ALTER TABLE consignment ADD COLUMN IF NOT EXISTS drop_address TEXT",
    "ALTER TABLE consignment ADD COLUMN IF NOT EXISTS pickup_tag TEXT",
    "ALTER TABLE consignment ADD COLUMN IF NOT EXISTS pickup_date VARCHAR(100)",
    "ALTER TABLE consignment ADD COLUMN IF NOT EXISTS drop_tag TEXT",
    "ALTER TABLE consignment ADD COLUMN IF NOT EXISTS drop_date VARCHAR(100)",
    "ALTER TABLE consignment ADD COLUMN IF NOT EXISTS eta VARCHAR(100)",
    "ALTER TABLE consignment ADD COLUMN IF NOT EXISTS eta_debug_json TEXT",
    "ALTER TABLE consignment ADD COLUMN IF NOT EXISTS pod_image VARCHAR(1024)",
]


def ensure_consignment_columns(dsn, log=None):
    """Add any missing consignment columns in a PostgreSQL database.

    This is intentionally idempotent and safe to call on every app startup.
    """
    if not dsn:
        raise ValueError("A PostgreSQL DSN is required to ensure consignment columns.")

    active_logger = log or logger

    import psycopg2

    conn = psycopg2.connect(dsn)
    try:
        with conn:
            with conn.cursor() as cur:
                for statement in CONSIGNMENT_ALTER_STATEMENTS:
                    active_logger.info("Ensuring schema: %s", statement)
                    cur.execute(statement)
        active_logger.info("Consignment schema check complete.")
    finally:
        conn.close()


def ensure_consignment_columns_async(dsn, log=None):
    """Run the schema repair in a background daemon thread.

    This keeps app startup non-blocking so the site can open even if the
    database is slow to respond.
    """
    active_logger = log or logger

    def worker():
        try:
            ensure_consignment_columns(dsn, active_logger)
        except Exception as exc:
            active_logger.warning("Consignment schema repair failed: %s", exc)

    worker = threading.Thread(
        target=worker,
        daemon=True,
        name="consignment-schema-repair",
    )
    worker.start()
    return worker
