# Task 2: current backend map

This describes the code at the baseline commit. It is not a proposed new
architecture or an instruction to move every file.

## How a request reaches the application

```text
Browser
  -> run.py (development) or wsgi.py (Gunicorn)
  -> app.create_app() configures Flask and registers the admin blueprint
  -> a route in app/admin/
  -> access checks and request validation
  -> feature operations, SQLAlchemy queries, PDF/spreadsheet code, or Storage
  -> HTML, JSON, a PDF, a spreadsheet, or a ZIP response
```

Routes currently perform different amounts of work. In particular,
`consignment_controller.py` contains storage helpers, validation, serialization,
exports, and shipment-save orchestration as well as HTTP handlers. There is
not yet a uniform route-to-service boundary.

## Where to look

Paths below are relative to the repository root.

| Responsibility | Current files | What they do |
| --- | --- | --- |
| Startup and commands | `run.py`, `wsgi.py`, `app/__init__.py`, `app/admin/__init__.py` | Build the app, configure extensions, register routes and maintenance commands. |
| Sign-in and registration | `app/admin/auth_routes.py`, `app/admin/auth.py`, `app/supabase_auth.py` | Handle sign-in, sign-up, session verification, refresh, logout, and route access. |
| Local user/session records | `app/user_profiles.py`, `app/auth_sessions.py` | Link verified identities to application roles and retain server-side session records. |
| Shipments | `app/admin/consignment_controller.py` | List and save shipments; normalize input, serialize rows, import/export, and coordinate document changes. |
| Shipment queries and compatibility | `app/orm.py`, `app/er_adapter.py`, `app/models.py` | Select models, expose compatible fields, load supporting relationships, apply default visibility, and share the Flask database session. |
| Database models | `database/models.py`, `database/lr.py` | Define canonical business records and LR/archive support records. |
| LR issuance | `app/lr_issuance.py`, `app/admin/lr_control.py` | Handle number issuance, retry protection, issuing locations, cancellation, import review/confirmation, and LR history. The control module also supplies shared mutation checks. |
| Archiving | `app/admin/shipment_archives.py` | Create archive snapshots, restore eligible shipments, and record audit events. |
| Package information | `app/shipment_packages.py` | Normalize shared package contents and measurements. |
| Document operations | `app/admin/documents.py`, `app/admin/document_lifecycle.py` | Validate attachments, manage versions, serve files, coordinate removal, and recover from storage/database failures. Some Storage helpers still live in the shipment controller. |
| Document screens and batches | `app/admin/document_library.py`, `app/admin/bulk_documents.py` | Display document filters, match bulk uploads, and produce document downloads. Document listing also uses a route in the shipment controller. |
| B2B labels | `app/admin/labels.py`, `app/admin/label_runs.py` | Select/review shipments, render label PDFs, and retain numbering/reprint assignments. |
| LR PDFs | `app/admin/lr.py`, `app/admin/lr_fields.py`, `app/admin/lr_pdf.py` | Review LR values, validate fields, and generate receipt PDFs against the fixed artwork. |
| Dashboard and reporting | `app/admin/routes.py`, `app/admin/reporting.py`, `app/admin/mis.py`, `app/admin/mis_exports.py` | Filter and analyze shipments, display dashboards, save report views, and generate/export MIS reports. |
| Companies and locations | `app/admin/companies.py` | Manage companies, locations, and company archive state. |
| Backups | `app/admin/backup.py`, backup routes in `app/admin/routes.py` | Inventory application tables and documents and assemble backup outputs, including retained historical data. |
| File/maintenance utilities | `app/upload_request.py`, `app/spreadsheets.py`, `app/db_maintenance.py` | Handle upload streams, in-memory spreadsheets, and database maintenance helpers. |
| Schema changes and imports | `migrations/`, `workflows/database-change.md` | Version schema changes and data imports; define the database-change workflow. |

## Important boundaries already present

- `app/orm.py` supplies the default shipment query. Normal shipment queries
  exclude archive snapshots and cancelled records; the consignments archive
  list explicitly requests inactive rows.
- Cancellation state and archive snapshots are separate stored facts even
  though both are excluded from operational lists.
- SQLAlchemy manages database work; the Supabase client also handles private
  object storage. Database and file-storage failure handling must remain
  coordinated when helpers move.
- Generated LR PDFs are transient until explicitly attached. B2B label runs
  have persisted numbering and retry/reprint behavior.
- Backup inventory intentionally reads retained data, not just the active
  shipment view. It should not inherit the operational visibility filter.

## Existing cross-feature dependencies to inspect in later tasks

These are observed connections, not yet a complete dependency audit:

- `app/admin/lr.py` imports `_label_rows` from the labels route module.
- Document and backup code reuse Storage helpers from the shipment controller.
- Shipment-save code calls document preparation and cleanup functions.
- Reporting supplies shared filtering/date helpers used by other features.
- `lr_control.py` supplies mutation/CSRF helpers used outside LR control.
- `er_adapter.py` attaches compatibility relationships and audit hooks; moving
  imports can affect when those hooks are registered.

These are likely extraction boundaries. Their names or locations alone do not
prove that a helper can be safely deleted or moved.
