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
intact. Archived fields need a backend adapter or explicit schema extension
before the UI can use them from the new database.

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
future backend file adapter must parse these references. Existing full-model
file UUIDs, actors, upload times and original filenames are preserved.

## Atomicity, RLS and repeat runs

The script validates constraints before creating the destination schema, creates
all tables/indexes/triggers, inserts data and the complete archive, reconciles
counts and rechecks the source before committing. A failure rolls back this
run's tables and data on PostgreSQL and SQLite; it does not leave a partial import.

On PostgreSQL, **RLS is enabled on all 12 new tables**, with no browser policies.
The owner/backend can operate normally; ordinary API roles cannot read rows even
if default grants exist. This does not create Supabase Auth accounts or enforce
team roles in the current Flask app.

Repeating the same command verifies source/file checksums, destination records
and the archive and reports `already_applied`, without duplicates. Changes to
source data/files, destination records or the archive stop a repeat run instead
of overwriting data. This is a one-time import, not ongoing synchronization.

## Dashboard cutover

The dashboard currently uses legacy tables, integer IDs and its existing upload
adapter. This migration does not change the running app's backend. Switching it
to the new database requires the UUID models, history/audit writes, canonical
file references, role checks and backup enumeration to be updated together.
Keep the original database and complete upload backup available until that
backend cutover is tested.
