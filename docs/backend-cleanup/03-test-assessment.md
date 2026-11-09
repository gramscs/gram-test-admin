# Task 3: test assessment

This assessment runs against the application source pinned in `01-baseline.md`.
No application code or test expectations were changed to obtain these results.

## Test environments and interpretation

- Python 3.12.14, Node.js 24.19.0, PostgreSQL 17.11.
- Python collection found 617 test cases, including parameterized cases.
- PostgreSQL tests run in a disposable container with synthetic records and
  per-test schemas. The database name must be exactly `gram_admin_er_test`:
  several fixtures require both that name and a loopback host.
- Supabase authentication and file storage are substituted by existing test
  fixtures. Passing tests do not establish live Auth/Storage integration health.
- The existing visibility unit test uses a small in-memory SQLite fixture for
  query checks. Application configuration remains PostgreSQL-only; this is not
  an alternative application database.
- All 18 JavaScript unit cases passed. Browser scripts were inventoried but
  were not rerun in this documentation batch; their previous results are not
  counted as a new browser test run.
- This is an inventory and regression baseline, not a line-coverage report.

An initial run used a different disposable database name, which caused setup
rejections. That run was superseded by the run with the required database name;
its totals are not the official baseline below.

## Reproduce the baseline

Install the existing development requirements in a project virtual environment.
Use a disposable PostgreSQL instance, not the application's configured database.
A loopback-only test container can be started with:

```bash
docker run -d --rm --name gram-backend-cleanup-pg \
  -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=gram_admin_er_test \
  -p 127.0.0.1:55432:5432 postgres:17-alpine
```

Wait for `docker exec gram-backend-cleanup-pg pg_isready -U postgres` to succeed,
then run:

```bash
mkdir -p work/backend-cleanup
ER_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/gram_admin_er_test \
  .venv/bin/python -m pytest tests -q --tb=short \
  --junitxml=work/backend-cleanup/python-baseline.xml
node --test tests/unit/*.cjs
```

After testing, remove the disposable container with
`docker stop gram-backend-cleanup-pg`. It was started with `--rm` and contains
synthetic test data only. Keep captured logs in the ignored local `work/`
directory; do not commit credentials, environment dumps, or raw application data.
The test source and dependencies are pinned by Git and the requirements files;
the Docker image tag should be checked when comparing future environment changes.

## Official results

| Run | Passed | Failed assertions / test failures | Setup errors | Total |
| --- | ---: | ---: | ---: | ---: |
| Python suite | 291 | 169 | 157 | 617 |
| JavaScript unit suite | 18 | 0 | 0 | 18 |

The Python run completed in 172.50 seconds with two upstream deprecation
warnings. **The full Python suite is not green.** An error here means a test
could not complete setup; it is not a passing check and not necessarily a
separate application defect. Test failures can also have shared causes.

The [machine-readable result inventory](test-results.json) records each
nonpassing case by its JUnit identifier (module plus test name) and records
module-level counts. It contains no captured application data or tracebacks.
It is an observation, not a skip list or permission to accept future regressions.

## Verified failure clusters

| Finding | Evidence | Consequence for cleanup |
| --- | --- | --- |
| A shared legacy-data fixture fails before its consumers run | 145 setup errors report `Unconsumed column names: extra_operator_notes`. `tests/test_shipment_migration.py` adds the column with raw SQL, then supplies it through a SQLAlchemy table object whose column list was not updated. | Many storage, backup, migration, and ER integration scenarios have not exercised their intended behavior in this run. Fix the fixture in a separately scoped test-maintenance change before relying on them. |
| Older requests do not satisfy current mutation protection | 108 failure messages explicitly report `Refresh the page and retry (CSRF token required).` Helpers such as `tests/test_label_review.py::create` submit older save payloads without the current CSRF/request setup. | Bring test requests into line with current authenticated workflows. Keep production checks enabled. Then reassess any subsequent validation failures. |
| Some tests select legacy tables against ER setup | 12 setup errors report missing `company`; further failures reference missing `consignment`. | Review test model selection and schema prerequisites rather than creating legacy tables in the application as a workaround. |
| Some prerequisites and assertions are outdated or unresolved | Remaining failures include missing `shipment_archives`, target-schema expectations, authentication/migration assertions, and downstream HTTP status mismatches. | Triage individually after shared blockers. These have not all been proven to be test-only problems. |

The extra-operator-notes diagnosis follows the fixture's SQL and SQLAlchemy
metadata use. The CSRF diagnosis is limited to the explicit messages counted
above; not every HTTP 403 assertion has been assigned that cause.

## What currently has useful passing coverage

Entire passing modules in this run include archive recovery, LR generator,
LR issuance, LR SQL export, shipment archive/restore, shipment visibility,
issuing-location readiness, and label PDF layout. The module table below gives
exact counts. This does not establish that adjacent features are safe merely
because a similarly named module passed.

## Coverage gaps and prerequisites

1. Storage extraction needs functioning deletion/versioning/rollback integration
   tests. The shared legacy fixture currently blocks many of these checks.
2. Modern shipment-save helpers should consistently supply verified login,
   CSRF, issuing location, and retry identifiers where required. Some older
   tests still assume manually supplied identifiers or previous save behavior.
3. Establish a consistent test schema/migration setup before treating older
   dashboard, MIS, and legacy-import assertions as product regressions.
4. Retain cross-feature checks for visibility, permissions, numbering, retries,
   and document recovery when functions move. Do not replace those checks with
   tests that only verify a new helper calls another helper.
5. The JavaScript checks use an in-memory DOM/transport. They do not replace a
   browser check when page wiring or script loading changes later.
6. Live Supabase Auth, bucket permissions, provider caching, and deployment
   configuration remain outside this isolated baseline.

No tests were skipped, loosened, deleted, or rewritten in this batch. Resolve
shared setup issues and establish the affected feature's passing checks before
its refactor. Track actual behavior fixes separately; do not silently expand
cleanup scope to make all failures disappear.

## Results by Python module

| Module | Passed | Failed | Setup errors |
| --- | ---: | ---: | ---: |
| `test_archive_recovery` | 7 | 0 | 0 |
| `test_backup_legacy_keys` | 0 | 0 | 11 |
| `test_bulk_documents` | 1 | 7 | 7 |
| `test_companies_presets` | 3 | 3 | 0 |
| `test_complete_backup` | 0 | 3 | 0 |
| `test_complete_database_import` | 1 | 0 | 0 |
| `test_dashboard_overview` | 0 | 3 | 0 |
| `test_database_model` | 47 | 2 | 0 |
| `test_document_deletion` | 5 | 0 | 68 |
| `test_document_names` | 0 | 0 | 4 |
| `test_er_dashboard` | 0 | 0 | 18 |
| `test_label_layout` | 17 | 0 | 0 |
| `test_label_names_metrics` | 0 | 8 | 0 |
| `test_label_review` | 2 | 27 | 0 |
| `test_label_runs_migration` | 6 | 2 | 0 |
| `test_location_auto_ready` | 6 | 0 | 0 |
| `test_lr_documents` | 0 | 17 | 0 |
| `test_lr_generator` | 33 | 0 | 0 |
| `test_lr_issuance` | 32 | 0 | 0 |
| `test_lr_sql_editor` | 4 | 0 | 0 |
| `test_migration_progress` | 0 | 0 | 3 |
| `test_mis_storage_separation` | 0 | 1 | 8 |
| `test_package_contents` | 0 | 16 | 0 |
| `test_reporting_mis` | 15 | 9 | 12 |
| `test_shipment_archives` | 9 | 0 | 0 |
| `test_shipment_documents` | 7 | 22 | 0 |
| `test_shipment_fields_labels` | 1 | 19 | 0 |
| `test_shipment_filters_documents` | 3 | 7 | 8 |
| `test_shipment_migration` | 1 | 0 | 2 |
| `test_shipment_visibility` | 16 | 0 | 0 |
| `test_standalone` | 20 | 9 | 0 |
| `test_storage_diagnostics` | 8 | 4 | 0 |
| `test_storage_download_cache` | 0 | 0 | 16 |
| `test_storage_isolation` | 1 | 1 | 0 |
| `test_supabase_auth` | 18 | 5 | 0 |
| `test_supabase_auth_migration` | 3 | 2 | 0 |
| `test_supabase_signup` | 25 | 2 | 0 |
