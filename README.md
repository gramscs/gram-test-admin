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

## What is included

- The original login, dashboard, sidebar, and admin page designs.
- Shipment creation, editing, deletion, searching, sorting, and pagination.
- Delivery-proof image upload, viewing, downloading, and removal.
- Excel import, Excel export, import-template download, and the original PDF button.
- Viewing customer enquiries and removing enquiries with blank phone numbers.
- JSON backup download for shipments, enquiries, and newsletter subscribers.
- Local Bootstrap, icon fonts, login fonts, scripts, and logo. The interface
  does not need a CDN or the public website to load.

## Where the code is

```text
app/
  __init__.py                  Standalone application setup and health checks
  admin/                      Original admin routes and business logic
  models.py                   Shipment, enquiry, and subscriber database tables
  db_maintenance.py           Optional PostgreSQL schema repair helper
  templates/admin/            Admin screen HTML and page styling
  static/js/consignments.js    Shipment screen behavior
  static/js/admin/             Server requests, pending edits, input checks
  static/vendor/              Bundled Bootstrap and login font
  static/css/                 Icon styling
  static/fonts/               Icon fonts
  static/images/logo.png      Original branding
tests/                        Standalone application tests
run.py                        Local server entry point
wsgi.py                       Production server entry point
.env.example                  Configuration template
requirements.txt              Python dependencies
```

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
screens, shipment editing, delivery proofs, Excel import/export, archive deletion,
enquiries, backups, startup, and database persistence.

## Behavior retained from the original

- **Archive Delivered deletes old delivered shipments and their delivery-proof
  files.** It does not move them into a separate archive.
- The original PDF export currently produces a PDF with the title
  “Consignments Export”; it does not print shipment rows. Excel export includes
  records.
- JSON backups contain database records and delivery-proof references, not the
  delivery-proof files themselves. No backup-restore interface is included.

The public website link on the login screen now points to this application's
home route. The admin business logic, models, and JavaScript were copied from
the original. The standalone application setup and bundled browser assets are
the additions that let this folder run independently.
