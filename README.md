# Gram SCS — Standalone Admin Panel

This is a portable copy of the admin panel from `gramscs/gram-scs`. It runs as
its own Flask application. You can copy this folder's contents into an empty
repository and run it without the original website repository.

## Start locally

Requires Python 3.12. Node.js, Docker, and PostgreSQL are not needed for the
default local setup.

From this folder:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python run.py
```

On Windows, activate with `.venv\Scripts\Activate.ps1` in PowerShell and copy
the settings with `Copy-Item .env.example .env` instead.

Open `http://127.0.0.1:5000`. It takes you to the admin login.

Local development login:

- Username: `admin`
- Password: `admin-pass`

The application automatically reads `.env`. These development defaults apply
when `FLASK_ENV=development` and credentials are left blank. To change the
password, generate a hash and put it in `ADMIN_PASSWORD_HASH` in `.env`:

```bash
python -c "from getpass import getpass; from werkzeug.security import generate_password_hash; print(generate_password_hash(getpass('New admin password: ')))"
```

## Update an existing installation

Stop the running Flask server, activate your virtual environment, then run:

```bash
git pull origin standalone-admin
python -m pip install -r requirements.txt
python -m flask --app wsgi:app upgrade-db
python -m flask --app wsgi:app check-db
python run.py
```

The document validator requires the updated dependencies. The schema upgrade
adds document fields and preserves existing records; restart before opening
the new pages. Use your normal production restart command on a hosted app.

## Add dummy data locally

With your virtual environment activated, run from this repository:

```bash
DATABASE_URL=sqlite:///instance/admin.db python -m flask --app wsgi:app seed-demo
```

This adds **25 sample shipments**, including all four delivery statuses,
addresses, PIN codes, and dates. It only seeds shipment data.
Refresh the admin pages after running it. Existing records are preserved,
and running the command again does not duplicate the demo records. The command
only works in local SQLite development and does not seed Supabase.

The command explicitly targets `instance/admin.db`. Your running app must use
the same SQLite database to display these records. On Windows PowerShell, set
`$env:DATABASE_URL = 'sqlite:///instance/admin.db'` first, then run
`python -m flask --app wsgi:app seed-demo`.

## What is included

- A shared green/charcoal design, Space Grotesk typography, and responsive navigation.
- Management dashboard with pickup trends, status mix, client/route rankings,
  document watchlists, date/client/status filters and presentation mode.
- MIS reports in Excel, PDF and CSV, with configurable columns, saved reporting
  views, immutable report history, private downloads, names/notes and deletion.
- Light and dark themes across every admin screen, including login.
- Shipment creation, editing, deletion, searching, sorting, and pagination.
- PDF/image POD and invoice uploads inside the shipment form, previews, downloads,
  staged removal, and file-content validation.
- Excel import/export, import-template download, and a complete shipment MIS PDF.
- Client company masters with multiple pickup/drop presets and editable shipment snapshots.
- B2B piece labels with optional Code 128 barcodes and PDF preview.
- Complete ZIP backup of all admin tables and uploaded files, with ordered
  consignment folders, Excel/JSON data, checksums, and a missing-file report.
- Local Bootstrap, icon fonts, interface fonts, scripts, and logo. The interface
  does not need a CDN or the public website to load.

## Where the code is

```text
app/
  __init__.py                  Standalone application setup and health checks
  admin/                      Original admin routes and business logic
  models.py                   Shipment/client/MIS tables; retained legacy records
  db_maintenance.py           Optional PostgreSQL schema repair helper
  templates/admin/            Admin screen HTML and shared theme components
  static/js/consignments.js    Shipment screen behavior
  static/js/admin/             Requests, pending edits, input checks, theme preference
  static/vendor/              Bundled Bootstrap and Space Grotesk font
  static/css/                 Shared themes, consignment layout, and icons
  static/fonts/               Icon fonts
  static/images/logo.png      Original branding
tests/                        Standalone application tests
run.py                        Local server entry point
wsgi.py                       Production server entry point
.env.example                  Configuration template
requirements.txt              Python dependencies
```

## Appearance

Dashboard previews with local demo data: [Light mode](docs/screenshots/dashboard-light.png)
· [Dark mode](docs/screenshots/dashboard-dark.png).

Use the theme toggle in the top bar (sun/moon icon on phones) to switch between
light and dark mode. Your choice is saved in this browser and applies to every
admin screen, including login; it also syncs between open tabs. Before you make
a choice, the app follows your device theme. The toggle still works if browser
storage is blocked, although the preference cannot be remembered after reload.

The dashboard shows saved consignment totals, exact **In Transit** and
**Delivered** and **Out for Delivery** status counts, and the five most recently
added shipments. These are database values, not sample analytics. On small
screens, use the menu button beside the logo to open navigation.

## Database and delivery-proof files

By default, this app creates a **new, empty** SQLite database at
`instance/admin.db` and stores uploaded PODs and invoices in `instance/uploads/`.
It does not include your original database, enquiries, uploads, passwords, or
test records.

To work with the same records as your website, set `DATABASE_URL` to the same
database connection in `.env`. PostgreSQL connections use
`postgresql://user:password@host:5432/database`.

This admin app is dedicated to shipment tracking. Leads pages, editing routes,
and email tools have been removed. Existing enquiry/subscriber records remain
in the database and complete backup so the change does not discard data.
For shared Supabase shipment-document storage, configure `SUPABASE_URL`,
`SUPABASE_KEY`, and `SUPABASE_BUCKET`. If the website stores proofs on its
local disk, a shared database alone does not share those files: copy/mount
the uploads into this app's `instance/uploads/`, or configure the common
Supabase storage. Back up the `instance/` directory when using local storage.

Table creation runs automatically for local SQLite development. Remote PostgreSQL
tables are left unchanged by default. You can create missing tables explicitly:

```bash
python -m flask --app wsgi:app init-db
```

This creates missing tables and preserves existing records. It is not a schema
migration tool. For a legacy PostgreSQL shipment table missing fields, the
original repair helper is available as an explicit command:

```bash
python -m flask --app wsgi:app repair-consignment-schema
```

## Connect to Supabase PostgreSQL

1. Open your Supabase project and click **Connect**.
2. Select **Session pooler** and copy the PostgreSQL connection URI. The
   session pooler on port 5432 works with IPv4 hosting environments.
3. Replace the password placeholder with the database password, URL-encoding
   special characters. Store the URI securely as `DATABASE_URL` in your
   hosting/environment settings, or in your ignored local `.env` file.
4. Restart the app, then run the read-only connection and table check:

   ```bash
   python -m flask --app wsgi:app check-db
   ```

`SUPABASE_URL` and `SUPABASE_KEY` are for shipment-document file storage; they
do not replace the PostgreSQL database connection string. No Supabase API key
is required for the database connection.

The app requests encrypted connections for Supabase hosts unless the connection
URI already specifies an SSL mode. Supplied SSL settings, including `verify-full`
and a CA certificate path, are preserved. PostgreSQL connections have a 10-second
connection timeout and check pooled connections before reuse.

`check-db` does not create tables, copy records, or change data. If it reports
missing tables on a new database, use `init-db`. If you are sharing an existing
website database, inspect missing fields before using the PostgreSQL repair
command. Existing data is not automatically migrated from SQLite to Supabase.

Allow outbound access to the exact hostname from your connection URI and port
5432 in your hosting environment. The credentials must be available to the
Python process for PostgreSQL authentication.

## Shipment fields and B2B labels

Click **Add Row** to open the Add Consignment modal. Fill in the shipment details
and click **Save** to stage the completed row in the table. Click **Save All**
to write staged changes to the database. Cancelling the modal adds no row.
The row's Edit button opens the same form for existing shipment details.

The sheet groups identifier type/number, chargeable weight/volume, pickup
tag/date, and drop pincode/date together. Every displayed field can still be
edited directly, and each sortable field has its own header button. Use
**Import / Export** for the Excel import dialog, import template, and Excel/PDF
exports. The add/edit form groups shipment, pickup, drop, and document details;
its footer stays visible while you scroll.

Each shipment has an identifier type (LRN, Order ID, or AWB), an identifier of
up to 64 characters, a number of pieces, chargeable weight in kg, and chargeable
volume in m³. Weight/volume accept non-negative values with up to three decimal
places. Pieces must be a whole number from 1 to 10000. Identifiers remain unique
across all types. Existing records default to LRN and one piece.

Local SQLite development upgrades existing shipment tables automatically on
startup. To upgrade an existing PostgreSQL/Supabase database, run explicitly:

```bash
python -m flask --app wsgi:app upgrade-db
python -m flask --app wsgi:app check-db
```

The upgrade adds shipment/document columns, creates company/location master
tables, retains legacy enquiry columns, and allows longer identifiers without deleting records. It does not require copying or replacing the database.

Open **B2B Labels** in the sidebar, select saved shipments, and download a
4×6-inch PDF. The PDF has one page per piece with the identifier, Code 128
barcode (optional), client company, sender and recipient addresses/PIN codes,
piece number, and shipment chargeable weight/volume totals. The label screen
shows selected shipment and piece counts and offers PDF preview before download. Print at 100% scale. Each download is limited
to 500 labels; selections apply to the current page. The Archive Delivered
button and deletion endpoint have been removed.

## Deploy as an individual app

Set `FLASK_ENV=production`, a strong `SECRET_KEY`, your `ADMIN_PASSWORD_HASH`,
and `DATABASE_URL` in your hosting service's environment settings. The app
requires these settings in production and uses secure login cookies, so serve
it over HTTPS. Initialize missing tables with `init-db` before serving traffic.

On a Linux host, start with:

```bash
gunicorn --bind 0.0.0.0:8000 --workers 2 wsgi:app
```

A `Procfile` is included for hosts that supply `PORT`. For shared rate limits
across workers, install `redis` and set `RATELIMIT_STORAGE_URI` to your Redis URL;
the default in-memory limits operate within each worker.

Readiness checks:

- `GET /health`: application responds with `status: ok`.
- `GET /health/db`: database query succeeds with `status: ok`.

## Run tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Tests use temporary databases and uploads. They cover login protection, admin
screens, shipment editing, PDF/image PODs and invoices, content validation and
failed-save recovery, Excel import/export, labels, backups, startup, and persistence.

## Behavior retained from the original

- Shipment Tracker **Export PDF** uses the MIS generator and includes the full
  saved shipment register matching the current search, together with management
  charts and summaries. Unsaved form edits require **Save All** first.
- Complete backups include database records and uploaded files; the optional
  data-only JSON export contains records and file references. No backup-restore
  interface is included.

The public website link on the login screen now points to this application's
home route. The admin business logic, models, and JavaScript were copied from
the original. The standalone application setup and bundled browser assets are
the additions that let this folder run independently.

## Client companies and shipment presets

Open **Companies → Add Company** to save a name, registered address, contact
information, and any number of regular pickup/drop locations (up to 50 per
company). Choose one default for each group. If no default is marked, the
first location in that group becomes the default.

In **Consignments → Add Row**, select your client company. Its default pickup
and drop locations fill automatically. Choose another saved location or edit
the address, pincode, or tag manually. **Save**, then **Save All**, as before.
Shipment addresses are copies: editing a master later does not change existing
shipments. No company is required for one-off shipments.

Company **Archive** keeps master data and existing shipment links, while
preventing new selections. Use **Include archived → Restore** to reactivate a
client. Editing an archived company also lets you mark it active again. Excel
exports include `company_id` and `client_company`; the import template accepts
an optional `company_id` from a master in this database.

## Complete admin backup

Choose **Complete backup** in the sidebar or **Download Backup ZIP** on the
dashboard. The ZIP includes every row and column from every application-owned
table: consignments, client companies (including archived clients), saved
pickup/drop locations, and preserved legacy enquiries/subscribers. The backup
is read-only; it does not change or delete records or uploads.

Inside the ZIP:

- `data.json`: all admin database records, with each shipment's backup order,
  document path, and retrieval status.
- `admin-data.xlsx`: a sheet for each table, plus document and inventory reports.
  JSON is the authoritative full-data copy; Excel is for browsing the records.
- `documents/000001_<consignment-number>_id-<id>/`: that shipment's POD and invoice
  files. Numeric prefixes follow the consignment data/Excel order (database ID
  ascending), so repeated original filenames cannot overwrite one another.
- `other_uploads/`: all discovered uploaded files that no longer have a record
  link, including local legacy uploads and unlinked admin Supabase uploads.
- `document_report.json` and `README.txt`: included/missing/unavailable files,
  upload-listing errors, original references, sizes, and SHA-256 checksums.

Local inventory covers `instance/uploads/`. Supabase inventory covers the
`consignments/` namespace used by the admin uploader in configured/referenced
buckets, plus every file directly referenced by shipments or saved MIS reports. It does not
export unrelated website buckets/tables or environment credentials.

Missing files or failed storage listings make the filename end in
`_incomplete.zip`. All readable data/files are still included; check the report
before relying on that backup. Live edits to uploads during export can cause
retrieval issues. Files are copied exactly rather than re-encoded. Large
archives spill to temporary disk instead of holding all file bytes in memory.

**Download data only (JSON)** on the dashboard retains a faster records-only
option (`/admin/generate-backup?format=json`). The default backup URL downloads
the complete ZIP. Neither format includes an automatic restore interface.

## POD and invoice documents

Open a shipment with **Add Row** or **Edit**. The **Shipment documents** section
contains separate POD and invoice cards. Both accept **PDF, JPG, PNG and WebP**,
up to **5 MB per file**. Neither document appears as a table column. Image
previews appear in the form; PDFs use **Download** instead of an embedded viewer.

Choose, replace, remove or undo a document change in the form, then click
**Save → Save All**. **Cancel** discards the form's changes. Files already saved
are retained until the database save succeeds. Staged changes survive shipment
filtering/pagination. Download buttons work for saved and selected documents.

The server detects the file contents independently of the browser MIME type.
Images are decoded/re-encoded without metadata, limited to one image and
16 megapixels. PDFs must be readable, unencrypted and 1–200 pages; files with
scripts, automatic actions or embedded attachments are rejected. SVG and other
file formats are rejected. Stored names are generated, document requests require
admin login, and downloads disable content sniffing and caching. This is file
validation, not antivirus scanning. For Supabase storage, use a **private bucket**
and keep the storage key on the server; a public bucket has its own access rules.

The sidebar groups **Reporting**, **Shipment operations**, **Master data**, and
**Data & account**. Labels use a compact print-settings panel with normal page
scrolling; on tablets and phones, settings appear before the shipment list.
[View the updated label settings](docs/screenshots/labels-settings.png).

## Label barcode choice

EAN/UPC codes generally identify retail products. These shipment labels use
ordinary **Code 128**, which can encode an LRN, Order ID, or AWB without an EAN.
Uncheck **Include Code 128 barcode** to print address labels without it. The
format does not claim GS1-128/SSCC compliance; carrier-specific GS1 requirements
need a separate identifier/format. See the [GS1 barcode overview](https://www.gs1.org/standards/barcodes/1d-barcodes).

Labels always print on a white background with black text, irrespective of the
admin theme. The destination address and PIN receive priority; shipment totals
are clearly marked so they are not mistaken for per-piece weights/volumes.
Fonts are embedded for consistent printing. [View a sample three-piece label PDF](docs/b2b-label-example.pdf).


## Management dashboard and MIS

The dashboard uses saved shipment data, with locally bundled interactive SVG
charts and the existing light/dark themes. **Present** opens a clean presentation
layout; click **Exit presentation** or press Escape to return. Trend points and
status segments show exact values on hover/focus, and the pickup chart includes
an expandable data table.

Use **Pickup-date period**, **Client** and **Current status** to select a view.
Periods include all time, the last 7/30 days, this month/quarter and custom dates.
Archived clients remain available for historical reporting. Click a client in
**Volume by client** to filter the dashboard to that client. Dashboard downloads
use the same applied filters. Shipment search can also narrow reports by
identifier, client, status, route, address or pincode.

Reporting definitions:

- Delivery metrics show **current status for the selected pickup-date cohort**.
  The app does not record actual delivery timestamps, so reports do not calculate
  on-time delivery or actual delivery trends.
- **Past expected date** counts open shipments whose estimated drop date (or ETA
  fallback) is before today in IST. This is an estimate-based attention flag.
- Date filters use pickup dates. All time includes undated records; date ranges
  exclude them and show the number excluded. Undated records never appear in the
  pickup trend. Supported dates include ISO dates/timestamps and DD/MM/YYYY or
  DD-MM-YYYY. Charts aggregate daily, monthly or yearly to fit the date span.
- Missing POD counts delivered shipments without a POD reference; missing
  invoices counts all selected shipments without an invoice reference. These
  indicators do not verify that an attached file can still be retrieved.
- Chargeable totals include recorded, valid, non-negative measurements only.
  Weight/volume coverage shows how many shipments have a value. Invalid legacy
  measurements are flagged and excluded; blank measurements are not estimates.

Open **MIS Reports** to manage reports:

1. Choose filters, optional shipment search and the columns for your register.
2. Enter a report/view name, optional notes and a download format.
3. **Generate & save report** stores a snapshot and starts its download. The
   success message provides a download link and **Refresh report history**.
4. **Save as new view** keeps the filters, columns and format for reuse. Load a
   saved view to change it, then **Update loaded view**. Relative periods refresh
   when loaded/generated. Deleting a view leaves generated reports intact.
5. History keeps the original generated files, even if shipment data changes.
   Download again, edit the display name/notes, or delete a report and its file.
   Deleting reports does not change shipment records. History is paginated.

**Excel** includes editable charts, summary/coverage metrics, current status,
pickup trend, client and route breakdowns, attention flags and the full filtered
shipment register with your chosen columns. **CSV** contains the full selected
register. Spreadsheet text is protected against formula execution.

**PDF** includes a landscape management summary with vector charts, metrics and
coverage, the top 15 clients/routes, and the first 50 attention records. It also
includes **every filtered shipment**, even those with no attention flags, using
your selected register columns. Wide tables are divided into readable column
groups with row numbers and the selected shipment identifier repeated; long
tables/addresses continue across pages. Reports show pickup-date filters, search
and generation time in IST.

When no shipments match, the PDF states this clearly, shows the number of saved
shipments, and explains which dates/filters to check. An empty database explains
that shipments must be added/imported and saved first. Downloads read and check
the response before saving the file: login pages, server errors, empty files and
invalid PDF responses are shown as errors instead of becoming misleading PDFs.
Generated reports are checked for an overview and register before being offered.

After updating, restart the app, refresh the browser, and **generate a new PDF**.
Previously saved reports remain their original snapshots and are not rewritten.

Complete backups include the `mis_view` and `mis_report` tables. Generated MIS
files are linked to their history rows in `mis_reports/`, with checksums and
missing-file reporting, alongside the other uploaded documents.

After updating an existing installation, run the following against your intended
database (keep the configured DATABASE_URL), then restart the app:

```bash
python -m flask --app wsgi:app upgrade-db
python -m flask --app wsgi:app check-db
```

This additive upgrade creates `mis_view` and `mis_report`; it preserves existing
shipments, companies, documents and retained legacy records. No new dependency or
external chart service is required.

Preview using sample data: [Management dashboard](docs/screenshots/dashboard-light.png)
· [Dark dashboard](docs/screenshots/dashboard-dark.png)
· [MIS reports](docs/screenshots/mis-reports-dark.png)
· [Example management PDF](docs/management-mis-example.pdf).
