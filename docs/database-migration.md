# One-run shipment database migration

The entry point is [`migrations/001_shipment_database.py`](../migrations/001_shipment_database.py).
It creates the ER model and copies the existing records **in one transaction**.
It supports SQLite, PostgreSQL/Supabase, and a local SQLite source imported
into a separate PostgreSQL target. Use the project's existing Python environment
and dependencies; no Flask startup or manual SQL execution is needed.

## Run once

From this repository, with the virtual environment activated and the database
settings already stored securely in `.env` or environment settings:

```bash
python migrations/001_shipment_database.py --apply
```

This reads the legacy tables from `DATABASE_URL` and creates the new tables
alongside them in that same database. If `DATABASE_URL` is blank, it uses
the local `instance/admin.db`, matching the app's development default.
An invalid/placeholder PostgreSQL URL causes an error; it does not silently
fall back to another database. Supabase connections automatically require SSL.

**If your records are local but your target is Supabase**, set `DATABASE_URL`
securely to the target Supabase PostgreSQL connection, then use this single command:

```bash
python migrations/001_shipment_database.py --source-local instance/admin.db --apply
```

For a different PostgreSQL source, set `MIGRATION_SOURCE_DATABASE_URL` securely
to the source connection and use the first command. It defaults to the target
when unset. Do not put passwords in shell arguments or chat. For a source
in a different PostgreSQL schema, configure the connection's search path;
tables are created in the target connection's current schema.

Before running against operational data, download the app's complete backup
and pause the app, imports and other database/storage writers. Test on a copy
first. The migration locks database tables during its transaction, but does not
lock external file storage or synchronize future writes into the old tables.
Database credentials need schema-creation privileges on the target and table
locking privileges on the source. A SQLite source file must permit locking;
the migration still performs no source-data updates.

For example, test the local data in a separate SQLite file:

```bash
DATABASE_URL=sqlite:///instance/migration-preview.db python migrations/001_shipment_database.py --source-local instance/admin.db --apply
```

To check the real configuration without applying anything, omit `--apply`:

```bash
python migrations/001_shipment_database.py
```

Preflight reads records/files, checks the new model's rules in an isolated
in-memory database, and reports counts/warnings. It does not create the target
tables or change source records. A new SQLite target connection may create
an empty database file.

## What the command does

1. Reads the existing client, location, consignment, MIS view/report, retained
   enquiry and subscriber tables, including every column on those tables.
2. Inventories referenced files, all regular local uploads, and the configured/
   referenced Supabase buckets' `consignments/` namespace, including unlinked
   uploads. It verifies readability and computes byte counts and SHA-256 hashes.
3. Validates records against the new model. Missing files, malformed settings,
   bad identifiers/counts, duplicate defaults, dangling clients, and incompatible
   report-file sharing stop the migration.
4. Creates all ten ER tables, indexes and integrity triggers. It generates stable
   UUID mappings, copies clients/shipments/settings/report records, and creates
   current version-1 POD/invoice links. Shared document files remain shared.
5. Creates two migration bookkeeping tables, separate from the ten business tables:
   `admin_migration_runs` records version, source schema, checksums, counts and
   warnings; `admin_migration_records` stores every original row and its UUID mapping.
6. Adds `migrate` audit entries for the original rows. It does not invent past
   shipment events, upload times, actual delivery times or unknown preset/view links.
7. Reconciles counts and rechecks the database source, then commits everything
   together. A failure rolls back this run's tables and inserts on both PostgreSQL
   and SQLite. Original tables and storage objects are never deleted or overwritten.

On PostgreSQL, the migration **enables RLS on all 12 newly created tables** and
creates no browser-access policies. The owner/backend can operate normally;
ordinary API roles are denied rows even if default grants exist. This does not
alter RLS on legacy tables, create auth accounts, or enforce team roles in Flask.

## Every original field is retained

The ER diagram omits shipment PIN/tag values, raw dates, ETA/debug values,
report notes, and saved-view update times. These are copied intact into
`admin_migration_records.source_values`, alongside all other original fields
and any extra columns found on the known legacy tables. Retained enquiries and
subscribers are archived there too; their UI is not reintroduced.

The archive records the original table and ID plus the new table/UUID where one
exists. Its values preserve date/timestamp/decimal representations; unexpected
binary column values are base64 encoded. `source_schema` retains column types
for interpretation. The original tables remain intact as well. This preserves
data without inventing new business tables outside the diagram.

Typed shipment dates use the app's existing ISO/day-first date convention.
Unrecognized dates remain null in the typed columns and produce warnings;
their exact source strings remain in the archive. Empty status becomes
`No status`, with the original value retained. Legacy MIS timestamps are treated
as UTC, matching the current app's timestamp-writing behavior.

MIS filters' known client IDs and renamed column keys are mapped to the new
UUIDs/field names. Original filters/columns and report notes remain in the
archive. Filters referencing removed clients are retained with a review warning;
the migration does not turn them into an all-client report.

The configured `ADMIN_USERNAME` becomes the active administrator identity.
Other usernames found in historical MIS records become inactive operator
identities, preserving attribution without granting login. Unknown upload
actors/times remain null. No password is read from or written to these tables.

## Files remain in existing storage

The database stores metadata and links; it does not hold the image/PDF bytes.
The command reads existing storage and does not move, rewrite, upload or delete
its objects. Local files continue to need the uploads folder; remote files
continue to need their original Supabase project/bucket. Moving the database
to another project does not by itself move object storage.

`--uploads-dir /path/to/existing/uploads` selects the local source folder.
The default is this repository's `instance/uploads`. Supabase storage reads
use `SUPABASE_URL`, `SUPABASE_KEY` and `SUPABASE_BUCKET` from secure settings.
The key must allow listing and downloading the relevant private objects.
Keep those settings pointed at the original storage project during migration.

`files.storage_path` uses canonical `local:relative/path` or
`supabase:bucket/object/path` references, preserving uniqueness across buckets.
The future ER-backed file adapter must parse these references and use the
configured local folder or Supabase client. MIME detection describes the existing
bytes; it does not replace the app's PDF/image safety validation or authorize
previewing unknown file types.

External URLs, escaping paths, symlinked inventory entries and unavailable
objects fail instead of being fetched unsafely or silently omitted. No partial
or “missing file” import is committed. Unrelated Supabase folders outside the
admin namespace are not inventoried unless directly referenced by a record.

## Reruns and existing target tables

After a successful run, the same command verifies the source/file digest,
target data and complete migration archive and reports `already_applied`.
It does not add duplicates, regenerate UUIDs or overwrite records.

If source data, source files, target records or the archive have changed, the
command stops. This is a one-time migration, not an ongoing synchronization
tool. Do not keep using legacy writes after a production cutover.

Before the first run, the target must have **no ER/ledger tables**. Legacy
tables may exist for an in-place import; otherwise the target can be empty.
The command refuses unmanaged existing ER tables, even empty ones, to avoid
merging incompatible structures or replacing existing data. Do not run the
standalone `database-schema.sql` first: this migration creates the schema itself.

## Dashboard cutover remains a separate change

This migrates the database schema and data. The dashboard's existing routes,
JavaScript and uploads still use the legacy tables and integer IDs. They must
be switched together to the new UUID models, canonical file references,
history/audit writes, role checks, and complete-backup enumeration. Fields
retained in the archive need an adapter or explicit schema extensions before
the corresponding UI features can use the ER tables.

Therefore the script does not switch the running dashboard automatically.
After migration, verify the new records and keep the original app/database
and full upload backup available until the backend cutover is tested. If a
database migration fails, retry after fixing the reported source/configuration
problem; this run's target writes have already been rolled back.
