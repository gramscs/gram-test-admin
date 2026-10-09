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

## Add dummy data locally

With your virtual environment activated, run from this repository:

```bash
DATABASE_URL=sqlite:///instance/admin.db python -m flask --app wsgi:app seed-demo
```

This adds **25 sample shipments**, **5 customer enquiries**, and **3 newsletter
subscribers**. The shipments include all four delivery statuses, addresses,
PIN codes, and dates. The enquiries appear in the Leads panel; subscribers
appear in the JSON backup.

Refresh the admin pages after running it. Existing records are preserved,
and running the command again does not duplicate the demo records. The command
only works in local SQLite development and does not seed Supabase.

The command explicitly targets `instance/admin.db`. Your running app must use
the same SQLite database to display these records. On Windows PowerShell, set
`$env:DATABASE_URL = 'sqlite:///instance/admin.db'` first, then run
`python -m flask --app wsgi:app seed-demo`.

## What is included

- A shared green/charcoal design, Space Grotesk typography, and responsive navigation.
- Dashboard totals from your database, recent shipments, and shortcuts to each tool.
- Light and dark themes across every admin screen, including login.
- Shipment creation, editing, deletion, searching, sorting, and pagination.
- Delivery-proof image upload, viewing, downloading, and removal.
- Excel import, Excel export, import-template download, and the original PDF button.
- Enquiry CRM: create/edit, statuses, follow-up dates, internal notes, filters,
  sorting, selection, bulk updates/deletion, Excel export, and email-app drafts.
- Client company masters with multiple pickup/drop presets and editable shipment snapshots.
- B2B piece labels with optional Code 128 barcodes and PDF preview.
- JSON backup for shipments, enquiries, subscribers, companies, and saved locations.
- Local Bootstrap, icon fonts, interface fonts, scripts, and logo. The interface
  does not need a CDN or the public website to load.

## Where the code is

```text
app/
  __init__.py                  Standalone application setup and health checks
  admin/                      Original admin routes and business logic
  models.py                   Shipment, enquiry, and subscriber database tables
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
**Delivered** status counts, customer lead totals, and the five most recently
added shipments. These are database values, not sample analytics. On small
screens, use the menu button beside the logo to open navigation.

## Database and delivery-proof files

By default, this app creates a **new, empty** SQLite database at
`instance/admin.db` and stores uploaded delivery proofs in `instance/uploads/`.
It does not include your original database, enquiries, uploads, passwords, or
test records.

To work with the same records as your website, set `DATABASE_URL` to the same
database connection in `.env`. PostgreSQL connections use
`postgresql://user:password@host:5432/database`.

The standalone app displays existing enquiries; it does not include the
public enquiry form. The website can keep writing enquiries to a shared
database, and this app can read them.

For shared Supabase delivery-proof storage, configure `SUPABASE_URL`,
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

`SUPABASE_URL` and `SUPABASE_KEY` are for delivery-proof file storage; they
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
exports. The add/edit form groups shipment, pickup, drop, and POD details;
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

The upgrade adds shipment and CRM columns, creates company/location master
tables, and allows longer identifiers without deleting records. It does not require copying or replacing the database.

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
screens, shipment editing, delivery proofs, Excel import/export, shipping labels,
enquiries, backups, startup, and database persistence.

## Behavior retained from the original

- The original PDF export currently produces a PDF with the title
  “Consignments Export”; it does not print shipment rows. Excel export includes
  records.
- JSON backups contain database records and delivery-proof references, not the
  delivery-proof files themselves. No backup-restore interface is included.

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

## Enquiry CRM and email drafts

**Leads → Add Enquiry** captures a contact, company, subject, and customer
message. **Edit** also lets you set New / Contacted / Qualified / Won / Closed,
an internal note, and a follow-up date. Customer messages expand in the table.
Received timestamps display in IST; timestamps in exports remain ISO values.

Search, status filters, and sorting help find records. Select rows for bulk
status updates, deletion, or Excel export. Selection applies to the current
page. **Data tools** also exports all filtered results and retains the optional
blank-phone cleanup action. Deletions require an explicit confirmation.

Use a row's envelope button or **Email selected**. Enter any recipient (up to
ten comma-separated addresses), review/edit the subject and message, then
**Open email app**. Your browser needs a configured `mailto:` handler; you
review and send from your own account. The admin app never sends these emails
and needs no SMTP credentials. Internal notes, status, and follow-up dates are
excluded unless **Include internal notes** is checked.

Long drafts cannot fit reliably in an email-app link. **Download complete
draft** saves the full message as an unsent `.eml` file, including recipients
and subject, for opening/importing in an email app. The draft endpoint also
supports short messages. Up to 50 enquiries can be combined in one draft.

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
