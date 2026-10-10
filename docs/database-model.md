# Shipment database model

This implements the supplied **ten-table ER diagram** as a next database
version. It is a concrete SQLAlchemy model and a tested PostgreSQL schema.
The running dashboard still uses `app/models.py`; its database and uploads
have not been migrated or changed.

A [single-run migration](database-migration.md) now creates this model in a
fresh destination and copies legacy or full-model data, including every source
table/column in a complete original-row archive. It preserves existing full-model
UUIDs, history, document versions and relationships. It is run
explicitly and is not part of app startup or `upgrade-db`.

## In simple language

The frontend is the screen you operate. Flask is the backend that receives
your actions. SQLAlchemy describes how backend objects map to database
tables. PostgreSQL stores the records. Private file storage holds the actual
PDF/image/report bytes; the `files` table stores their details and location.

| Table | What it stores | How it connects |
| --- | --- | --- |
| `admin_users` | Internal team usernames, roles, active/inactive state | Identifies who entered events, uploaded files, created views/reports, and made changes |
| `companies` | Client name, registered address, contact details, active state | One client can have many locations and shipments |
| `company_locations` | Saved pickup/drop addresses and the default of each kind | Belongs to one company; may be selected as a shipment preset |
| `consignments` | Shipment identifier, client, current status, pieces, weight/volume, copied addresses and dates | Links to its client and optional pickup/drop presets |
| `shipment_events` | Shipment status history, when each event happened, and when it was entered | Many events belong to one shipment; the recording user is optional for historical imports |
| `files` | Storage bucket/path, filename, MIME type, bytes, checksum, upload time | Shared metadata for documents and generated reports |
| `shipment_documents` | POD/invoice attachments, their version and current/old state | Links a shipment to a file; retains earlier versions |
| `mis_views` | Reusable report filters, selected columns, preferred format and notes | Belongs to its creator; can generate many reports |
| `mis_reports` | Generated report details, fixed filters/columns/metrics, shipment count | Links to its generating user, optional saved view, and one unique report file |
| `audit_logs` | Who changed which record, action, before/after values, and time | References the actor; keeps the changed record's UUID even if that record is deleted |

All primary keys are UUIDs. All relationships in the diagram have SQLAlchemy
relationships and database foreign keys, except `audit_logs.entity_id`: that
is deliberately a logical reference so deletion does not erase the audit trail.

## Files you can review

- [`database/models.py`](../database/models.py): every table and relationship.
- [`database/integrity.py`](../database/integrity.py): cross-table integrity triggers.
- [`database/schema.py`](../database/schema.py): generates PostgreSQL SQL without a connection.
- [`database-schema.sql`](database-schema.sql): the generated schema, including constraints,
  indexes, defaults and triggers. This is for an **empty staging database**;
  it is not an upgrade script for the running dashboard.
- [`tests/test_database_model.py`](../tests/test_database_model.py): actual ORM and raw-SQL
  checks against SQLite and optionally isolated PostgreSQL.

To regenerate/review SQL, from the repository with the Python environment activated:

```bash
python -m database.schema
```

This command does not load `.env`, read `DATABASE_URL`, connect to Supabase,
or change any database. The model has separate SQLAlchemy metadata, so
the current app's `init-db`, `upgrade-db`, `check-db`, and backup behavior
do not change merely because this package exists.

## Rules built into the database

- Usernames and client names are unique, following the current app's client rule.
  Roles are `administrator`, `operator`, or `viewer`.
- Shipment identifiers are `LRN`, `Order ID`, or `AWB`. The identifier value
  remains globally unique, matching today's app; introducing per-client Order ID
  reuse would require a separate business-rule change.
- Pieces must be at least one. Weight (kg) and volume (m³) use exact decimal
  `NUMERIC(12,3)` values and cannot be negative. Both are optional.
- A shipment may have no assigned company or presets. A selected preset must
  belong to that shipment's company and have the correct pickup/drop kind.
  Composite foreign keys enforce ownership; triggers enforce kind, including
  changes made using raw SQL. A referenced preset's kind cannot be changed.
- A company can have at most one default pickup and one default drop location.
  Other presets remain available. Zero defaults is also allowed.
- Copied shipment addresses are independent strings. Editing the company's
  preset address does not rewrite existing shipments; the backend must copy
  or edit the snapshot when saving the shipment form.
- Planned/expected dates are `DATE`. Actual pickup/delivery, event, upload,
  report and audit times are timezone-aware timestamps, normalized to UTC
  by the ORM. Naive Python datetimes are rejected. Actual delivery cannot
  precede actual pickup when both are known.
- `occurred_at` and `recorded_at` are separate: an event may be entered later
  than it happened. Unknown actual times must remain unknown.
- Storage paths are unique, as shown in the diagram. Generate a fresh object
  path for every upload, including across buckets. Replacing bytes at an
  existing path would defeat document versioning and report preservation.
- File sizes cannot be negative. A known SHA-256 checksum has 64 characters.
  Historical checksums and upload times may be null when unknown; new uploads
  should compute checksums from the validated bytes and record the real time.
  Stored MIME/size/checksum values do not replace
  the backend's existing PDF/image content-validation checks.
- Each shipment/document kind has unique version numbers starting at one,
  and at most one current version. To replace a POD or invoice, retire the
  current row and add the next version **in one transaction**, locking the
  shipment to serialize simultaneous replacements. Keep the old file.
- Multiple shipment documents may reference the same file, supporting
  existing shared-file cases. Each generated report has a unique file reference.
- MIS formats are `pdf`, `xlsx`, and `csv`. Filters/metrics are JSON objects;
  selected columns are JSON arrays. PostgreSQL uses native `JSONB`.
  Assign replacement objects/lists when editing these ORM fields; in-place
  nested JSON mutations are not automatically tracked.
- Report snapshot fields, format, file, generator, shipment count and generation
  time cannot be edited after insertion. The display name can change.
  Deleting a saved view clears `view_id` while preserving the report and file.

## Deletion and attribution

Referenced companies, presets, files and users cannot be deleted while links
exist. Deactivate a company/user instead, or explicitly remove a preset link
without changing the shipment's copied address. These restrictions apply
to ORM deletion as well as raw SQL; relationships do not silently clear links.

Deleting a shipment cascades to its events and document links. Its stored file
records/bytes and audit entries remain. File deletion is a separate cleanup
operation that must check all document/report references first. Database
cascades never delete objects from Supabase or local storage automatically.

Actor fields are nullable for legacy/system records where attribution is
unknown. New authenticated actions should populate the real user ID. The
diagram has no password/auth-provider fields: `admin_users` does not itself
implement login. No password is stored in this model.

## Backend work needed before using this version

The database migration is available; application cutover is a separate change. The current routes,
JavaScript, imports, reports, uploads and backup use the old schema and numeric
IDs. They must be updated together before the app can use these tables.

The backend must save a status change, its event and its audit entry in the
same transaction. The model does not automatically produce history or audit
records for arbitrary writes. Late events require an explicit rule for whether
they change `current_status`; no event timestamp should be fabricated. File
uploads must be validated, written to private storage, then linked; failed
database saves require staged-upload cleanup. Backups must include every file
version and every generated report, including unlinked retained files.

Roles here are data, not enforced permissions. Authentication/authorization
must map the logged-in person to an active user and check their role in Flask.
For Supabase, enable RLS or keep these tables outside exposed API schemas;
grant no browser/anonymous access for this internal backend-only app. Choose
the backend database role and grants before production use. This schema
does not create RLS policies, storage buckets, auth accounts or login grants.
The migration enables RLS on the new PostgreSQL business/archive tables with
no browser policies; the standalone generated SQL does not enable it.

## Existing-data migration plan

Do this on a copy first, retaining the original database and a complete upload
backup until reconciliation and rollback have been tested.

| Current data | Target mapping |
| --- | --- |
| `company.id` / `company.address` | Generate stable UUID mappings; `companies.id` / `registered_address` |
| `company_location` | `company_locations`; remap its client ID, preserve kind/address/default |
| `consignment.consignment_number` / `status` | `identifier_value` / `current_status` |
| Shipment client/pieces/weight/volume | Same meaning; map `company_id` through the UUID map |
| Shipment pickup/drop addresses | Copy into `pickup_address_snapshot` / `drop_address_snapshot` |
| String pickup and expected-delivery dates | Parse only recognized formats into typed dates; flag ambiguous values for review |
| `pod_image` / `invoice_file` | Inventory real files; create deduplicated `files` rows and current version-1 document links |
| `mis_view` settings and creator strings | Decode JSON text; remap client IDs and renamed column keys; link known creators |
| `mis_report` files/settings/summary/count | `files` + `mis_reports`; populate fixed snapshots, preserve real generation times |
| Existing leads/newsletter records | Keep archived in the old schema and complete backup; do not discard or re-enable their UI |

Keep a persisted old-ID → UUID mapping and migration ledger outside the ten
business tables, so restarts/retries do not invent new identities. Validate
unique values, defaults, foreign keys, numeric ranges and file checksums
before importing. Flag missing/unreadable files rather than inventing bytes,
sizes, MIME types or timestamps. Do not infer preset IDs from similar-looking
addresses, historical status events from today's current status, or actual
delivery times from planned dates. Unproven links/times remain null.

**The diagram omits some fields used by today's app:** shipment PIN/tag
snapshots, the separate drop-date/ETA/debug values, report notes, and saved-view
update times. The migration retains these in the target original-row archive
and leaves the original tables intact. Agree on explicit schema extensions or
an archive adapter before cutover. Saved-view links for old reports also cannot be
reconstructed reliably, so leave `view_id` null when unknown.

After the importer and UUID-aware backend are implemented, compare every
record count, shipment identifier/client, report snapshot and file checksum
against the source. Test the dashboard, labels, imports, downloads and full
backup on the migrated staging copy. Cut over during a controlled write pause,
with the verified backup and original app/schema available for rollback.

## Validation

The regular test suite checks SQLite without contacting Supabase:

```bash
python -m pytest tests/test_database_model.py -q
```

The optional PostgreSQL run uses `ER_TEST_DATABASE_URL`, not the app's
`DATABASE_URL`. Its fixture rejects anything other than a loopback PostgreSQL
database called `gram_admin_er_test`. It creates and removes a unique schema
per test. Set that variable to your disposable test database connection, then
run the same command. PostgreSQL tests also execute the exported SQL itself.
