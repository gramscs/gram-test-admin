# PostgreSQL database upgrades and imports

The application supports PostgreSQL for records and Supabase Storage for files.
It does not create a database, run schema migrations at startup, or fall back to
local persistence.

## Upgrade an existing application database

### Version 004: Supabase authentication

Follow [the Supabase Auth setup guide](supabase-authentication.md) for environment
settings, signup, optional legacy profile linking and verification. The versioned
upgrade adds a nullable unique `admin_users.supabase_user_id` UUID and the private
`admin_auth_sessions` support table. Existing user IDs, roles, records and audit
references are preserved. The migration creates no Auth accounts. Current app
code automatically creates an audit profile at a confirmed user's first login;
all confirmed Supabase users have full panel access. Signup requires no schema
upgrade beyond 004.

Use `python migrations/004_supabase_authentication.py` for a read-only preflight
and add `--apply` to apply. Alternatively run the entire
[`migrations/sql/004_supabase_authentication.sql`](../migrations/sql/004_supabase_authentication.sql)
file in the intended Supabase project's SQL Editor. Both paths write the same
migration revision transactionally and can be rerun without resetting sessions.
Existing incompatible structures or missing structures for a recorded revision
stop the operation. Pulling code alone does not apply this upgrade.

Application rollback requires restoring the previous application's deployment
and settings; keep the additive schema in place. Never drop linked user IDs or
business records to roll back authentication. Reverting code reintroduces its
previous authentication behavior; review that deliberately before deploying it.

### Version 003: Lorry Receipt documents

LR attachments use the existing `shipment_documents` / `files` tables with
`document_type = 'LR'`. No shipment columns or new tables are needed in the ER
schema. The migration widens only the named document-type check constraint,
verifies existing row counts, and records revision `003_lorry_receipts` in the
same PostgreSQL transaction. Existing POD/invoice versions, current-version
indexes, file records, foreign keys, RLS and uploaded objects are preserved.

Using the existing securely configured `DATABASE_URL`:

```bash
python migrations/003_lorry_receipts.py
python migrations/003_lorry_receipts.py --apply
AUTO_CREATE_TABLES=false python -m flask --app wsgi:app check-db
```

The first command is read-only. Application is idempotent; pulling code alone
does not execute it. If the transaction fails, the old constraint and migration
history remain intact. Do not run a one-time importer for this upgrade.

If an LR image or PDF causes **Save failed. Failed to save consignments.**, the
existing database may still permit only POD and invoice links. Creating the
Storage bucket or pulling application code does not update that constraint.
Apply migration 003 using the commands above, then retry the upload.

If PostgreSQL is inaccessible from your terminal, open the **same project's
Supabase SQL Editor**, paste the entire contents of
[`migrations/sql/003_lorry_receipts.sql`](../migrations/sql/003_lorry_receipts.sql),
and run it as the schema owner. This is an alternative way to apply the same
revision, with compatible migration history. It requires the existing migration
ledger and stops on unexpected constraints or a conflicting revision. The
transaction preserves existing shipments, document versions and Storage files;
it does not add an LR column or make Storage public. The final queries must
show `'LR'` in the constraint and `003_lorry_receipts` in migration history.
Then retry **Save All** with the LR image attached.

If an earlier copy stops with **Unexpected document types**, use the current SQL
file and rerun the entire transaction. The corrected check compares type
membership independently of the database's text sorting rules. It still rejects
additional or missing types; in that case, share the reported type list and the
constraint definition from the verification query below before changing it.

LR files use the private `lr-uploads` bucket by default. Set
`SUPABASE_LR_BUCKET=lr-uploads` in the running app environment. Optional overrides
must name another private bucket with the same server-side permissions. Keep MIS reports in a separate bucket. `check-storage` includes LR
bucket reads. No public bucket or automatic bucket creation is used.

LR PDFs are processed in memory and persisted only in Supabase Storage. Their
database links use `shipment_documents.document_type = 'LR'` and `file_id`, with
bucket, object path and file metadata in `files`. There is no physical `lr`
column to add to the ER `consignments` table; `lr_file` is the application's
compatibility property for that relationship.

In Supabase SQL Editor, verify that the type constraint permits LR:

```sql
SELECT conname, pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE conrelid = 'public.shipment_documents'::regclass
  AND conname = 'ck_shipment_documents_valid_type';
```

The definition must include `'LR'`. After uploading an LR, verify its shipment
link and the actual Storage object:

```sql
SELECT c.identifier_value AS shipment, d.document_type, d.version, d.is_current,
       f.storage_bucket, f.storage_path,
       EXISTS (
           SELECT 1 FROM storage.objects o
           WHERE o.bucket_id = f.storage_bucket AND o.name = f.storage_path
       ) AS object_exists
FROM public.shipment_documents d
JOIN public.consignments c ON c.id = d.consignment_id
JOIN public.files f ON f.id = d.file_id
WHERE d.document_type = 'LR'
ORDER BY f.uploaded_at DESC;
```

New uploads should show `storage_bucket = 'lr-uploads'` and `object_exists = true`.
Previously uploaded documents keep their original bucket references; they are
not moved or deleted by changing the default bucket.

After preview, **Upload LR** submits each shipment's own two-page PDF through
the same shipment-document upload endpoint used for manual POD, invoice and LR
uploads. Standard file validation, permissions, stale-reference protection and
version history apply. If one upload fails, already uploaded documents remain
saved and the generator retries only the remaining files. Editing the form
requires another preview. Files are stored in the private LR bucket and linked
through the existing document tables, exactly like other document uploads.

Application rollback can leave this backwards-compatible constraint in place.
If a database rollback is needed, first verify there are no `LR` document rows;
otherwise reverting the check would reject existing records. Restore the old
`CHECK (document_type IN ('POD', 'invoice'))` transactionally only after that
verification and record the rollback in migration history. Never delete LR
records or stored files merely to downgrade the application.

### Version 002: PostgreSQL label numbering

[`002_label_runs.py`](../migrations/002_label_runs.py) adds the missing
`label_runs` table to the **existing PostgreSQL database in `DATABASE_URL`**.
It uses the application's `.env` loading and connection settings. Running a
migration from Codespaces changes that configured database, not a database on
the Codespaces filesystem.

From the repository, with the app's virtual environment activated:

```bash
git pull origin standalone-admin
python migrations/002_label_runs.py --apply
AUTO_CREATE_TABLES=false python -m flask --app wsgi:app check-db
```

The migration creates the table, constraints and indexes, enables row-level
security, and records `002_label_runs` in `admin_migration_runs` in the same
database. It preserves existing shipments and label-numbering assignments.
DDL and the history entry commit together; failure rolls back the transaction.
Repeated application verifies the existing table and keeps the same history
entry and numbering. Omit `--apply` for a read-only preflight.

After the command succeeds, reopen label review and retry preview/download.
Do not rerun migration 001 or move data to another database for this error.
Pushing/pulling this file alone does not execute it on Supabase. No local
database or persistence fallback is created. The migration does not import old
local numbering files or regenerate historical PDFs.

If migration 002 is already recorded but its table has subsequently disappeared,
the command stops instead of silently restarting numbering. Restore the
missing numbering history before retrying. Application rollback can leave this
additive table in place; do not drop it after labels have been generated.

### General support-table maintenance

Set `DATABASE_URL` to the intended PostgreSQL database. Run:

```bash
AUTO_CREATE_TABLES=false python -m flask --app wsgi:app upgrade-db
AUTO_CREATE_TABLES=false python -m flask --app wsgi:app check-db
AUTO_CREATE_TABLES=false python -m flask --app wsgi:app check-storage
```

`upgrade-db` is idempotent and additive: it creates missing application support
tables (`admin_record_extras` and `label_runs`) and preserves existing business
tables and records. It does not rebuild the schema, rename or drop business
columns, or import data. Database columns and public API fields for shipment
measurements are `weight` and `volume`.

For future business-schema changes, add a dedicated versioned PostgreSQL
migration that alters the existing tables transactionally and preserves their
data. Do not use the one-time importer below for routine schema changes.

## One-time import into a fresh database

[`migrations/001_shipment_database.py`](../migrations/001_shipment_database.py)
imports an existing PostgreSQL source into a **fresh PostgreSQL destination**.
It creates the ER business tables and migration archive transactionally. It is
not used for regular application upgrades.

Configure both connection strings securely:

- `MIGRATION_SOURCE_DATABASE_URL`: the existing PostgreSQL source.
- `DATABASE_URL`: a different, empty PostgreSQL destination.

Then run a read-only preflight:

```bash
python migrations/001_shipment_database.py
```

Apply only after reviewing the reported counts and warnings:

```bash
python migrations/001_shipment_database.py --apply
```

The source and destination must be separate. Source files must already be
available in the configured private Supabase buckets; local file references are
not imported. The importer verifies referenced objects and inventories the
configured Supabase namespaces. Existing source records and object bytes are
not modified. For a full-model source, identities, document history, and
relationships are preserved. Every source table and row is retained in the
migration archive.

The database role must be able to read and lock all source tables, create target
tables, indexes, and constraints, and enable row-level security. Supabase
connections require SSL. Keep connection strings outside command arguments and
source control.

## Historical files

Historical database rows that point to local file paths are intentionally left
unchanged. The application no longer reads, creates, or deletes local upload
files, so those references are not retrievable through the application. Move
such files to Supabase and update their records only as a separately authorized
data migration.
