# Task 1: starting-version record

Recorded on 9 November 2026, before backend refactoring.

| Item | Recorded value |
| --- | --- |
| Source repository | `gramscs/gram-test-admin` |
| Source branch | `standalone-admin` |
| Source commit | `99ae65553e23ab50f5cb006dfd7bf2379683aac5` |
| Source commit subject | Exclude cancelled shipments from active workflows |
| Source Git tree | `fbb8ff90b0fa8829f38990a1652451cf114838a8` |
| Cleanup branch | `backend_code_cleaning` |
| Starting worktree | Clean; no uncommitted files |
| Backend | Flask, Flask-SQLAlchemy / SQLAlchemy, PostgreSQL |
| Frontend | Jinja templates and browser JavaScript; retained during backend cleanup |
| Application dependencies | `requirements.txt` |
| Development dependencies | `requirements-dev.txt` |
| Schema history | Custom scripts and SQL under `migrations/` |

A Git commit identifies the tracked source code, not the contents of a running
database, private object storage, environment variables, or a running server.
This record is not a production-data backup or confirmation of a deployment.

## Compare future changes with the starting point

```bash
git diff --stat 99ae65553e23ab50f5cb006dfd7bf2379683aac5 HEAD
git diff 99ae65553e23ab50f5cb006dfd7bf2379683aac5 HEAD -- app database migrations
```

After this documentation-only batch, the second command should show no changes.
Use the existing commit to inspect earlier code. Do not reset a working branch
or overwrite application data simply to compare versions.

## Review boundaries

Keep request URLs, response fields, permissions, database columns, document
formats, and transaction behavior stable during a cleanup-only change. Track
any necessary behavior fix separately. Historical migrations and bundled
third-party assets are not routine cleanup targets.
