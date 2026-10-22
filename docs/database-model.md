# Shipment database model

The dashboard stores application records in PostgreSQL and uploaded document
bytes in private Supabase Storage buckets. It does not use a local database or
persistent local upload storage.

The [SQLAlchemy ER model](../database/models.py) implements these ten business
tables:

| Table | Purpose |
| --- | --- |
| `admin_users` | Audit/ownership profiles and unique Supabase Auth identity links; legacy role/status metadata |
| `companies` | Client names, contact details, and registered address |
| `company_locations` | Saved pickup/drop addresses and defaults |
| `consignments` | Shipment identifiers, status, pieces, weight, volume, dates, and address snapshots |
| `shipment_events` | Shipment status history and event attribution |
| `files` | Supabase bucket/path, filename, MIME type, byte size, checksum, and upload time |
| `shipment_documents` | Versioned POD, invoice and lorry receipt (LR) references |
| `mis_views` | Saved report filters, columns, format, and notes |
| `mis_reports` | Generated report metadata and immutable snapshots |
| `audit_logs` | Change actor, entity, action, before/after values, and time |

All primary keys are UUIDs. Database foreign keys protect relationships, except
`audit_logs.entity_id`, which is a logical reference so audit history survives
deletion. Physical shipment measurement columns, model attributes, and API
fields are `weight` and `volume`; aliases for the previous column names are not
supported.

## Integrity rules

- Usernames and company names are unique. Legacy roles are `administrator`,
  `operator`, or `viewer`; roles and profile `active` do not gate panel access.
  Supabase Auth manages credentials and account status.
- Shipment identifiers use `LRN`, `Order ID`, or `AWB`; identifier values are
  globally unique.
- Pieces must be at least one. Weight (kg) and volume (ft³ / CBFT) use
  `NUMERIC(12,3)` and cannot be negative. Both may be unknown.
- A selected pickup/drop preset must belong to the shipment's company and have
  the matching location kind. A company can have at most one default of each
  kind.
- Shipment address snapshots are independent from company preset addresses.
- Planned/expected dates use `DATE`; actual pickup/delivery, event, upload,
  report, and audit times are timezone-aware UTC timestamps. Unknown physical
  times are not inferred from expected dates.
- Supabase storage paths are unique. Every upload gets a distinct object path;
  replacement creates a new document version rather than overwriting a prior
  object's bytes.
- Each shipment/document kind has unique version numbers and at most one current
  version. Prior versions remain available unless explicitly removed.
- Report snapshots are immutable after creation except for the display name.
- Database cascades do not delete Supabase objects. Application deletion
  coordinates document/reference removal with object cleanup and rolls back
  database changes if storage cleanup fails.

The standalone schema generator can inspect the model without connecting to a
database:

```bash
python -m database.schema
```

The generated [`database-schema.sql`](database-schema.sql) is for an empty
staging database, not a routine upgrade. For an existing ER database,
`upgrade-db` only creates missing application support tables; it does not
recreate business tables or rename/drop columns. See the
[database upgrade and import guide](database-migration.md).

## Application support data

The dashboard adapter preserves editable screen fields that are not part of the
ten-table ER diagram in `admin_record_extras`. Database-backed label numbering
is stored in `label_runs`. These are additive support tables; the original
migration archive remains unchanged.

## Historical local file references

Existing records that refer to files stored on local disk are left untouched.
The application does not read or write those paths, so the files are not
retrievable through the dashboard until separately moved to Supabase and their
references are migrated with authorization.

## Tests

Database tests require an explicitly configured, dedicated PostgreSQL database.
Fixtures create a unique schema for each test and reject the active application
database. Supabase Storage is mocked; tests do not contact real Supabase
services.

```bash
ER_TEST_DATABASE_URL=postgresql://.../gram_admin_test python -m pytest -q
```

## Shared package contents

`pieces` counts outer boxes/pallets. In `admin_record_extras`, `package_contents`
stores `kind` (`box` or `pallet`) and `default_pieces`, the same count of pieces
inside every package. Counts are optional; entered counts must be integers from
1 to 1,000,000. Unknown counts print as **—**.

Per-package overrides are no longer supported. Older `boxes` entries are ignored
on reads and printing, excluded from normal Excel exports, and discarded when
saving or importing a shipment. Historical backups retain the original stored
data. Removing these controls does not require a schema migration.


Supabase Auth identity UUIDs are linked by the nullable, unique
`admin_users.supabase_user_id` column. Existing primary keys and audit foreign
keys stay unchanged. First verified login automatically creates a missing profile
with its Auth UUID as the primary key. Existing linked profiles retain their IDs.
All confirmed Supabase users have full shared-panel access. Authentication checks
use the verified identity, not user-editable metadata or an email match.

`admin_auth_sessions` is a private support table for encrypted Supabase access
and refresh tokens, hashed opaque session identifiers, and eight-hour session
expiry. RLS is enabled and direct access by `anon` / `authenticated` is revoked.
It is excluded from business-data backups and exports. Migration 004 adds these
structures without modifying historical profiles. Registration needs no further
schema changes. The private session table holds login state, not another user
account or password database.
