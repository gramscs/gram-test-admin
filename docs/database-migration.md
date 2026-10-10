# One-run import into a fresh database

[`migrations/001_shipment_database.py`](../migrations/001_shipment_database.py)
creates **all ten ER tables** and imports **all available source data in one
transaction**. It accepts the current app's legacy database or a database already
using the full ER model. SQLite and PostgreSQL/Supabase are supported, including
SQLite-to-PostgreSQL imports. It does not require Flask startup or manual SQL.

## Configure source and destination

Store `DATABASE_URL` securely in `.env` or environment settings, pointing to the
**new, empty destination**. This setting is required. The destination must have
no tables in its current schema before the first import. Do not run `init-db`,
the app, or `database-schema.sql` against it first.

The source defaults to the existing local `instance/admin.db`. To use another
local file, pass `--source-local /path/to/source.db`. To use an existing remote
database, set `MIGRATION_SOURCE_DATABASE_URL` securely to its connection string.
Choose one source setting. The source and destination must be separate.

For Supabase, provision the new project/database first and use its PostgreSQL
connection (Connect → Session pooler). This script creates the tables, indexes
and integrity rules **inside that database**; it does not provision a Supabase
project or issue `CREATE DATABASE`. Placeholder or invalid URLs fail; there is
no silent fallback to SQLite. Supabase connections require SSL automatically.

For another PostgreSQL schema, configure the connection's search path. Only
**tables in the source connection's current schema** are inventoried; tables in
other schemas, SQL views/functions and unrelated databases are outside this
import. Use the owner/BYPASSRLS source role: the script refuses a role that could
read only part of an RLS-protected table. The target role needs schema-creation
privileges and the source role needs read/locking privileges.

## Run in one command

With the project's Python environment activated, from the repository:

```bash
python migrations/001_shipment_database.py --apply
```

With `DATABASE_URL` set to the fresh Supabase destination, this reads local
`instance/admin.db`, creates the full schema and fills it. An explicit local
source works too:

```bash
python migrations/001_shipment_database.py --source-local instance/admin.db --apply
```

For a remote source, set `MIGRATION_SOURCE_DATABASE_URL` and use the first command.
Keep passwords in secure settings, outside command arguments and chat.

To check everything without applying, omit `--apply`:

```bash
python migrations/001_shipment_database.py
```

Preflight reads the complete source and storage, validates against the model in
an isolated in-memory database, and reports source, target and archive counts.
It creates no destination tables; connecting to a fresh SQLite target may create
an empty file. The source database and uploads are never modified.

For a disposable local destination, for example:

```bash
DATABASE_URL=sqlite:///instance/migration-preview.db python migrations/001_shipment_database.py --source-local instance/admin.db --apply
```

Before importing operational data, download the complete app backup and pause
app/import/storage writers. Database locks protect the source during the import;
external storage has no transaction lock. A SQLite source file must permit
locking even though the script performs no source-data updates.

## All ten business tables are created

| Table | Existing full-model source | Current legacy app source |
| --- | --- | --- |
| `admin_users` | All users, roles and active flags; UUIDs preserved | Administrator identity and historical MIS usernames |
| `companies` | All company records | Converted client records |
| `company_locations` | All presets and their company links | Converted pickup/drop presets |
| `consignments` | All shipments, preset links, dates and snapshots | Converted shipments and supported shipment fields |
| `shipment_events` | Entire event history and attribution | Empty when history was never stored |
| `files` | All file records and upload attribution, plus unlinked uploads | Metadata for all referenced and inventoried uploads |
| `shipment_documents` | All POD/invoice versions and current flags | Current version-1 POD/invoice links |
| `mis_views` | All saved settings and creator links | Converted saved MIS views |
| `mis_reports` | All report/view/file links and historical snapshots | Converted historical MIS reports |
| `audit_logs` | All existing audit history, including deleted-entity references | Explicit migration audit entries |

Every table is created even if its source contains zero rows. Missing history,
upload times, actual pickup/delivery times or unknown links are not fabricated.
For a full-model import, UUIDs and foreign-key connections remain intact; no
extra login identity or replacement audit history is injected.

Source format is detected automatically. If both legacy and ER tables contain
data, the script stops until you choose `--source-format er` or
`--source-format legacy`. Only the chosen model feeds the business tables;
**every source table is still archived**, including the unchosen model. A partial
ER schema also stops automatic detection instead of silently losing records.

## Every source table and column is accounted for

The script inventories **every table in the source's current schema**, including
custom tables, retained enquiries/subscribers and extra columns. It creates two
bookkeeping tables in addition to the ten business tables:

- `admin_migration_runs`: source column definitions, checksums, warnings and counts.
- `admin_migration_records`: every original row and its destination table/UUID,
  where a model mapping exists.

Older fields omitted by the ER diagram—PIN/tag values, raw dates, ETA/debug
values, report notes and saved-view update times—are preserved in
`admin_migration_records.source_values`. Unmapped tables are archived completely
rather than turned into additional business tables outside the diagram. This
also preserves composite-key rows and duplicate rows from tables without a
primary key. Empty tables appear in the source schema/count report.

The JSON archive retains decimal/date/timestamp representations; unexpected
binary column values are base64 encoded. The original source tables remain
intact. The dashboard adapter copies the existing screen fields into
`admin_record_extras` during `upgrade-db`. This support table is separate from
the ten business tables; subsequent edits never change the original archive.

The result includes `source` and `target` row counts and `coverage` for each
source table: `source_rows`, `archived_rows` and `mapped_rows`. Every source row
must be archived. Counts in generated tables can differ from the source:
legacy imports create file/document/user/audit records, and unlinked storage
objects create additional file records.

## Legacy conversion rules

Legacy integer IDs receive stable UUID mappings recorded in the archive. Typed
shipment dates use the app's ISO/day-first date convention; unrecognized dates
remain null with a warning and their exact source strings stay in the archive.
Empty status becomes `No status`. Legacy MIS timestamps are treated as UTC,
matching the app's existing timestamp-writing behavior.

Known company IDs and renamed column keys in MIS filters/settings map to the
new identifiers. Filters referring to removed clients retain their values with
a warning. Original filters/columns remain in the archive.

`ADMIN_USERNAME` becomes the active administrator identity for a legacy import.
Other historical MIS usernames become inactive operators, preserving attribution
without granting login. Unknown upload actors/times remain null. No password is
read from or written to these tables.

## Files stay in their existing storage

The database holds file metadata and relationships, not image/PDF bytes. The
script reads files without moving, uploading, rewriting or deleting them. Local
files still need the uploads folder; remote files still need their original
storage project/bucket. A new database does not automatically move storage.

`--uploads-dir /path/to/existing/uploads` selects the local folder; the default
is `instance/uploads`. Supabase storage uses `SUPABASE_URL`, `SUPABASE_KEY` and
`SUPABASE_BUCKET`, pointed to the **original** storage project. The key must
allow listing/downloading private objects.

The inventory covers all regular local uploads, referenced remote files and
the configured/referenced Supabase buckets' `consignments/` namespace, including
unlinked uploads. Unrelated remote folders are not enumerated unless referenced.
Readable files get verified byte counts and SHA-256 checksums. A full-model
source's existing sizes and known checksums must match its stored bytes.

Missing/unreadable files, escaping paths, unsupported external URLs, symlinked
inventory entries and mismatched metadata stop the whole import. Storage paths
use canonical `local:relative/path` or `supabase:bucket/object/path` references;
noncanonical ER paths are converted with their original values archived. The
dashboard file adapter parses these references. Existing full-model
file UUIDs, actors, upload times and original filenames are preserved.

## Atomicity, RLS and repeat runs

The script validates constraints before creating the destination schema, creates
all tables/indexes/triggers, inserts data and the complete archive, reconciles
counts and rechecks the source before committing. A failure rolls back this
run's tables and data on PostgreSQL and SQLite; it does not leave a partial import.

On PostgreSQL, **RLS is enabled on all 12 new tables**, with no browser policies.
The owner/backend can operate normally; ordinary API roles cannot read rows even
if default grants exist. This does not create Supabase Auth accounts or enforce
Supabase Auth roles. The Flask dashboard checks its configured login against
an active `admin_users` identity; viewer identities cannot perform write requests.

Repeating the same command verifies source/file checksums, destination records
and the archive and reports `already_applied`, without duplicates. Changes to
source data/files, destination records or the archive stop a repeat run instead
of overwriting data. This is a one-time import, not ongoing synchronization.

## Dashboard cutover

The current dashboard supports both the original local schema and the migrated
UUID schema. When `consignments` exists and the old `consignment` table does not,
it selects the ER backend automatically. A local database containing both versions
continues using its operational legacy tables. `ADMIN_DATABASE_MODEL=er` or
`legacy` can explicitly select a model when needed.

Once the importer has printed **Migration committed**, keep `DATABASE_URL`
pointing to that destination and run:

```bash
git pull --ff-only origin standalone-admin
AUTO_CREATE_TABLES=false python -m flask --app wsgi:app upgrade-db
AUTO_CREATE_TABLES=false python -m flask --app wsgi:app check-db
```

Stop the old Flask process and restart it using your usual command. The expected
messages are **ER backend ready**, then **Connected to postgresql** and **All
required admin tables and columns are present**. Do not run the importer again
or recreate legacy tables to fix a dashboard `UndefinedTable` error.

`upgrade-db` adds `admin_record_extras`, with RLS enabled on PostgreSQL, and
restores archived PIN/tag/date/ETA values, report notes and view update times.
This repeatable step leaves the original import archive and ten business tables
intact. Existing support fields from an ER-to-ER import are restored too.

The dashboard uses UUIDs for editing, companies, labels and MIS; document links
use `files` and versioned `shipment_documents`. Replaced PODs/invoices retain
historical file versions. Status changes and record edits add shipment events
and audit entries in the same database transaction. Status event times describe
when the change was recorded; they do not invent actual pickup/delivery times.
Complete backups include the model, support table, original-row archive and
current/historical/unlinked uploaded files. Saved reports retain their fixed
snapshots; newly generated reports can link their selected saved view.

Login still uses `ADMIN_USERNAME` and the environment password/hash. The matching
`admin_users` row must be active. This is not a new multi-user login system or a
Supabase Auth integration. Keep original databases and upload backups available
until you have checked the migrated dashboard.

## Progress and bounded waits

The migration prints elapsed-time progress to the terminal immediately, while
its final JSON report stays on standard output. Messages identify source reads,
file checks, table imports, archive verification and commit. Inserts are batched
(up to 200 matching records per batch) to reduce network round trips.

PostgreSQL connections have a 10-second connection timeout. Each transaction
sets a 15-second lock timeout and a 120-second statement timeout; these are
limits per lock/statement, not a deadline for the entire import. Network failure
and lock errors stop the run instead of waiting silently indefinitely. Storage
inventory and checksums can still take time on larger collections.

If interrupted or disconnected, wait until the process exits before retrying.
Do not assume success or rollback without a confirmation: a retry verifies any
completed import and does not duplicate it. Once the dashboard begins editing
the destination, repeat-import verification appropriately reports changed data;
it is not a synchronization command.
