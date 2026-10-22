# Supabase authentication setup

Sign-in now uses Supabase Auth **email and password**. The app no longer reads
`ADMIN_USERNAME`, `ADMIN_PASSWORD`, `ADMIN_PASSWORD_HASH`, `ADMIN_E2E_PASSWORD`,
or accepts old authenticated-session flags. There is no shared-password fallback.
Existing accounts must be explicitly linked to verified Supabase Auth identities.

## 1. Configure the intended test project

Use the same test Supabase project for Auth, PostgreSQL, and Storage. In this
workflow the supplied connection identifies the project already configured in
the cloud environment. No live database upgrade or user creation was performed
by the coding agent. Configure these values securely in the running app's
environment settings (or your Codespace's ignored `.env`):

| Variable | Where to get it |
| --- | --- |
| `DATABASE_URL` | Test project's PostgreSQL session-pooler connection string; keep its password private |
| `SUPABASE_URL` | Test project's HTTPS Project URL |
| `SUPABASE_ANON_KEY` | Project Settings → API Keys → publishable key (`sb_publishable_...`) or legacy anon/public key |
| `SUPABASE_KEY` | Server-only Storage secret/service-role key for that same project |
| `SECRET_KEY` | Generate a random app secret using the command below; keep it stable across restarts and workers |

Generate `SECRET_KEY` in your own terminal, then paste the output into the secure
setting, never source code or chat:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

`SECRET_KEY` signs browser cookies and encrypts private server-side tokens.
Changing it requires users to sign in again. `SUPABASE_ANON_KEY` is deliberately
separate from the privileged Storage client. No user session is installed into
that client. Keep `AUTO_CREATE_TABLES=false`. Use `FLASK_ENV=production` when
serving the app over HTTPS; production cookies are Secure and HttpOnly.

Create the four **private** buckets if absent: `pod-uploads`, `invoice-uploads`,
`lr-uploads`, `mis-reports` (or retain the existing configured names).
No database or document-storage fallback is created locally.

## 2. Apply migration 004 manually

Pull the `authentication` branch and install its dependencies:

```bash
git pull --ff-only origin authentication
python -m pip install -r requirements.txt
```

In the **test project's Supabase SQL Editor**, run the entire contents of
[`004_supabase_authentication.sql`](../migrations/sql/004_supabase_authentication.sql).
The final queries must return revision `004_supabase_authentication`, a UUID
`admin_users.supabase_user_id` column, and `admin_auth_sessions` with
`relrowsecurity = true`. Existing app tables and its migration ledger are
required. If this is a completely new project, initialize the application schema
using its documented database setup first; do not import or reset another project
as a workaround.

If direct PostgreSQL connectivity works in your terminal, the equivalent is:

```bash
python migrations/004_supabase_authentication.py
python migrations/004_supabase_authentication.py --apply
```

This migration preserves existing user IDs, roles, active states, shipments,
documents, and audit history. Applying it does not automatically grant access.

## 3. Create and link your authorized test account

In **Authentication → Users**, create the test email/password account and confirm
its email. Use the dashboard to manage its password; never put passwords in SQL
or application environment variables. The Email provider must be enabled.
The app has no public signup screen. For an invite-only project you can also
disable new public signups in Supabase's Auth settings.

Inspect the existing application users in SQL Editor:

```sql
SELECT id, username, role, active, supabase_user_id
FROM public.admin_users
ORDER BY username;
```

Replace `YOUR_TEST_EMAIL` and, if necessary, the existing application username
`admin` below. Run this in the **same project's SQL Editor** to link that existing
account. The update intentionally preserves its role, active state and ID:

```sql
UPDATE public.admin_users AS app_user
SET supabase_user_id = auth_user.id
FROM auth.users AS auth_user
WHERE app_user.username = 'admin'
  AND app_user.supabase_user_id IS NULL
  AND lower(auth_user.email) = lower('YOUR_TEST_EMAIL')
  AND auth_user.email_confirmed_at IS NOT NULL
RETURNING app_user.id, app_user.username, app_user.role,
          app_user.active, app_user.supabase_user_id;
```

Expect exactly one returned row. If there are zero rows, check the username,
confirmed Auth email, and whether it is already linked. Do not create duplicate
identities or overwrite an existing link blindly. The linked application account
must be active and have the intended `administrator`, `operator`, or `viewer`
role. Supabase user metadata does not assign application permissions.

## 4. Verify sign-in and permissions

Restart the app after changing its environment. For Codex, review/save the
prepared environment settings and publish the updated environment; saving a
draft alone does not apply migrations or prove a new runtime is configured.
Run these read-only checks using that environment:

```bash
python -m flask --app wsgi:app check-db
python -m flask --app wsgi:app check-auth
python -m flask --app wsgi:app check-storage
python run.py
```

Open the app's login screen, sign in with the linked **email** and Supabase
password, open Shipments, then use **Logout**. Confirm protected pages require
sign-in afterward. A viewer must not be able to save shipments or upload files;
authorized operators and administrators retain their normal document workflow.

The browser stores an opaque session identifier in a signed HttpOnly cookie.
Supabase tokens are encrypted in private PostgreSQL session records, never
returned in pages or included in business-data backups. Expiring access tokens
refresh with row locking to coordinate concurrent requests. Every protected
request verifies the Supabase identity and the linked account's active state
and role. Sessions expire after eight hours; logout deletes the local session
and requests revocation of that Supabase session. Auth outages return an error
and never fall back to shared credentials.

Focused automated verification uses the existing disposable PostgreSQL test
database and a mocked Auth HTTP boundary. It does not sign in to or change the
live Supabase project:

```bash
python -m pytest -q tests/test_supabase_auth.py tests/test_supabase_auth_migration.py
```
