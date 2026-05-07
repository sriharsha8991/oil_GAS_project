# 10 — Runbooks

> _Owns: copy-pasteable operational procedures. Every runbook here
> is something we have actually executed in production or staging._

All commands assume you are at the repo root with the venv active:

```powershell
cd d:\Sriharsha\professional\Wellsynthai
.\.venv\Scripts\Activate.ps1
```

## Bootstrap a super_admin

```powershell
.\.venv\Scripts\python.exe -m app.tools.bootstrap_super_admin `
    --email super-admin@data-smith.ai `
    --password 12345@st67890 `
    --display-name "Super Admin"
```

Idempotent: promotes the email if it already exists, otherwise creates
the user, waits for the `handle_new_user` trigger to insert the
profile, then flips the role to `super_admin`. Prints the user id on
success.

## Add a second super_admin

Three equivalent options:

1. **UI** — sign in as super_admin → Super Admin Console → "Add super
   admin" tab. (Requires FE deployed.)
2. **API** — `POST /admin/users/invite` with `role: "super_admin"`.
   See [07-api-reference.md](07-api-reference.md).
3. **CLI** — re-run `bootstrap_super_admin.py` with the new email.

## Recover a lost password

A user reports they cannot log in:

```powershell
# As super_admin, get the user id
curl -H "Authorization: Bearer $TOKEN" "http://localhost:8080/admin/users?q=lost@user.com"

# Trigger a fresh-password email
curl -X POST `
  -H "Authorization: Bearer $TOKEN" `
  "http://localhost:8080/admin/users/<user_id>/resend-invite"
```

The endpoint generates a new random password, writes it via service-role,
and emails it via Resend. The previous password is invalidated.

## Apply migrations

```powershell
.\.venv\Scripts\python.exe -m app.tools.apply_migrations
```

This reads [supabase/migrations/](../supabase/migrations/) in filename
order and applies any not yet recorded in the `_migrations` ledger
table. Safe to run repeatedly.

To add a migration:
1. Create `supabase/migrations/000N_<short_description>.sql`. Use
   `if not exists` and `drop policy if exists ... ; create policy ...`
   so the file is idempotent.
2. Update [app/db/models.py](../app/db/models.py) to mirror the change.
3. Run `apply_migrations`.

## Reset Qdrant (full wipe)

> Destructive. Requires re-ingesting every document.

```powershell
.\.venv\Scripts\python.exe -m app.tools.qdrant_reset
```

Drops and recreates `wellsynthai_chunks`, `wellsynthai_entities`,
`wellsynthai_images` with the correct payload indices.

## Replay an ingest

If a job is `failed` and you've fixed the underlying bug:

```powershell
# Find the document_id from the failed job
curl -H "Authorization: Bearer $TOKEN" "http://localhost:8080/ingest" | jq

# Re-ingest by uploading the same file again.
# The document row is reused (matched on user_id + project_id + filename)
# so the document_id stays stable.
```

We do not currently support "rerun this job id" — the contract is
"re-upload the file"; the document identity persists.

## Verify Resend domain

If invite emails 502 with `email_send_failed`:

1. Log into Resend dashboard → Domains.
2. Confirm `rax-resume.xyz` shows "Verified".
3. If not, re-add the SPF/DKIM records in DNS.
4. Test with `curl -X POST .../admin/users/invite ...` to your own inbox.

## Promote / demote a user role

```powershell
curl -X PATCH `
  -H "Authorization: Bearer $TOKEN" `
  -H "Content-Type: application/json" `
  -d '{"role": "admin"}' `
  "http://localhost:8080/admin/users/<user_id>/role"
```

Allowed values: `super_admin`, `admin`, `drilling_engineer`, `geologist`,
`asset_manager`, `qa_steward`. Super_admin only.

## Set / change an admin's user_quota

```powershell
curl -X PATCH `
  -H "Authorization: Bearer $TOKEN" `
  -H "Content-Type: application/json" `
  -d '{"user_quota": 25}' `
  "http://localhost:8080/admin/users/<admin_id>/quota"
```

`0` means unlimited. Quota is checked at invite time against the count
of profiles where `invited_by = admin.id`.

## Restart the live stack after env / code change

```powershell
docker restart wellsynthai-app
docker restart wellsynthai-celery-worker
```

Usually enough. Full rebuild (only if Dockerfile changed):

```powershell
docker compose build
docker compose up -d
```

## Smoke after any deploy

```powershell
.\.venv\Scripts\python.exe -m app.tools.api_smoke
```

Expect `passed: 23 / failed: 0`. If any step fails, do **not** declare
the deploy good — fix forward or roll back.

## Things this page does not own

- Why a given step is needed → the topical wiki page.
- New runbooks for features that don't exist yet — add them here only
  after you've executed the procedure for real.
