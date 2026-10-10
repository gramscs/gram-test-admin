"""Cross-table rules that cannot be expressed as ordinary CHECK constraints."""

from sqlalchemy import DDL, event


POSTGRES_RULES = (
    """CREATE OR REPLACE FUNCTION validate_consignment_presets() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE preset_kind text;
    BEGIN
        IF NEW.pickup_location_id IS NOT NULL THEN
            SELECT kind INTO preset_kind FROM company_locations
            WHERE id = NEW.pickup_location_id FOR SHARE;
            IF preset_kind IS DISTINCT FROM 'pickup' THEN
                RAISE EXCEPTION 'Pickup preset must be a pickup location' USING ERRCODE = '23514';
            END IF;
        END IF;
        IF NEW.drop_location_id IS NOT NULL THEN
            SELECT kind INTO preset_kind FROM company_locations
            WHERE id = NEW.drop_location_id FOR SHARE;
            IF preset_kind IS DISTINCT FROM 'drop' THEN
                RAISE EXCEPTION 'Drop preset must be a drop location' USING ERRCODE = '23514';
            END IF;
        END IF;
        RETURN NEW;
    END $$""",
    """CREATE OR REPLACE TRIGGER consignments_validate_presets BEFORE INSERT OR UPDATE
    ON consignments FOR EACH ROW EXECUTE FUNCTION validate_consignment_presets()""",
    """CREATE OR REPLACE FUNCTION protect_referenced_location_kind() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.kind IS DISTINCT FROM OLD.kind AND EXISTS (
            SELECT 1 FROM consignments
            WHERE pickup_location_id = OLD.id OR drop_location_id = OLD.id
        ) THEN
            RAISE EXCEPTION 'Cannot change the kind of a referenced location' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END $$""",
    """CREATE OR REPLACE TRIGGER company_locations_protect_kind BEFORE UPDATE
    ON company_locations FOR EACH ROW EXECUTE FUNCTION protect_referenced_location_kind()""",
    """CREATE OR REPLACE FUNCTION protect_mis_report_snapshot() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        IF ROW(NEW.id, NEW.generated_by, NEW.file_id, NEW.format,
               NEW.filters_snapshot, NEW.columns_snapshot, NEW.metrics_snapshot,
               NEW.shipment_count, NEW.generated_at)
        IS DISTINCT FROM
           ROW(OLD.id, OLD.generated_by, OLD.file_id, OLD.format,
               OLD.filters_snapshot, OLD.columns_snapshot, OLD.metrics_snapshot,
               OLD.shipment_count, OLD.generated_at) THEN
            RAISE EXCEPTION 'Generated report snapshots cannot be edited' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END $$""",
    """CREATE OR REPLACE TRIGGER mis_reports_protect_snapshot BEFORE UPDATE
    ON mis_reports FOR EACH ROW EXECUTE FUNCTION protect_mis_report_snapshot()""",
)


SQLITE_RULES = tuple(
    f"""CREATE TRIGGER IF NOT EXISTS consignments_validate_presets_{operation.lower()}
    BEFORE {operation} ON consignments FOR EACH ROW BEGIN
        SELECT CASE WHEN NEW.pickup_location_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM company_locations WHERE id = NEW.pickup_location_id AND kind = 'pickup'
        ) THEN RAISE(ABORT, 'Pickup preset must be a pickup location') END;
        SELECT CASE WHEN NEW.drop_location_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM company_locations WHERE id = NEW.drop_location_id AND kind = 'drop'
        ) THEN RAISE(ABORT, 'Drop preset must be a drop location') END;
    END""" for operation in ("INSERT", "UPDATE")
) + (
    """CREATE TRIGGER IF NOT EXISTS company_locations_protect_kind BEFORE UPDATE OF kind
    ON company_locations FOR EACH ROW WHEN NEW.kind IS NOT OLD.kind BEGIN
        SELECT CASE WHEN EXISTS (
            SELECT 1 FROM consignments WHERE pickup_location_id = OLD.id OR drop_location_id = OLD.id
        ) THEN RAISE(ABORT, 'Cannot change the kind of a referenced location') END;
    END""",
    """CREATE TRIGGER IF NOT EXISTS mis_reports_protect_snapshot BEFORE UPDATE ON mis_reports
    FOR EACH ROW WHEN NEW.id IS NOT OLD.id OR NEW.generated_by IS NOT OLD.generated_by
    OR NEW.file_id IS NOT OLD.file_id OR NEW.format IS NOT OLD.format
    OR NEW.filters_snapshot IS NOT OLD.filters_snapshot
    OR NEW.columns_snapshot IS NOT OLD.columns_snapshot
    OR NEW.metrics_snapshot IS NOT OLD.metrics_snapshot
    OR NEW.shipment_count IS NOT OLD.shipment_count
    OR NEW.generated_at IS NOT OLD.generated_at
    BEGIN SELECT RAISE(ABORT, 'Generated report snapshots cannot be edited'); END""",
)


def install_integrity_rules(metadata):
    for dialect, rules in (("postgresql", POSTGRES_RULES), ("sqlite", SQLITE_RULES)):
        for sql in rules:
            event.listen(metadata, "after_create", DDL(sql).execute_if(dialect=dialect))
    for function in (
        "validate_consignment_presets", "protect_referenced_location_kind", "protect_mis_report_snapshot",
    ):
        event.listen(metadata, "after_drop", DDL(
            f"DROP FUNCTION IF EXISTS {function}()"
        ).execute_if(dialect="postgresql"))
