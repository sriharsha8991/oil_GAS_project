# 06 — Database Schema

> _Owns: every table, enum, trigger, and RLS policy in Supabase
> Postgres. The single source-of-truth is the SQL migrations under
> [supabase/migrations/](../supabase/migrations/); this page summarises._

## Migrations (apply order)

| # | File | Adds |
|---|---|---|
| 0001 | `0001_users_profile.sql` | `user_role` enum, `users_profile`, `handle_new_user` trigger, RLS |
| 0002 | `0002_storage_buckets.sql` | `pdfs`, `pages`, `artifacts` buckets + storage RLS |
| 0003 | `0003_projects_documents_jobs.sql` | `projects`, `documents`, `jobs` tables; Realtime publication on `jobs` |
| 0004 | `0004_admin_role_and_quota.sql` | `admin` role enum value; `users_profile.user_quota`, `users_profile.invited_by` |

Apply via [app/tools/apply_migrations.py](../app/tools/apply_migrations.py)
(idempotent — files already applied are skipped via a `_migrations`
ledger table).

## Enums

```
public.user_role = (
  super_admin, admin,
  drilling_engineer, geologist, asset_manager, qa_steward
)
```

## Tables

### users_profile (1-to-1 with `auth.users`)

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | FK to `auth.users.id`, on delete cascade |
| `email` | text | populated by `handle_new_user` trigger |
| `display_name` | text | nullable |
| `role` | `user_role` | default `drilling_engineer` |
| `organization` | text | nullable; org-scoped admin reads use this |
| `user_quota` | int NOT NULL DEFAULT 0 | meaningful only for admins; 0 = unlimited |
| `invited_by` | uuid | FK to `auth.users.id` ON DELETE SET NULL |
| `created_at` | timestamptz | default now() |
| `updated_at` | timestamptz | maintained by `tg_set_updated_at` trigger |

Triggers:
- `on_auth_user_created` (after insert on `auth.users`) → calls
  `handle_new_user()` which inserts the matching `users_profile` row
  with default role `drilling_engineer` and `display_name` from
  `raw_user_meta_data` if present.
- `set_updated_at` (before update) → bumps `updated_at`.

RLS:
- `users_profile_self_select` — user can SELECT their own row.
- (No self-update; profile updates go through `/auth/me/profile`
  using the service-role key.)

### projects

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | default `gen_random_uuid()` |
| `user_id` | uuid | FK to `auth.users.id`, ON DELETE CASCADE |
| `name` | text NOT NULL | |
| `description` | text | nullable |
| `created_at` / `updated_at` | timestamptz | |

RLS: owner-scoped SELECT/INSERT/UPDATE/DELETE on `user_id = auth.uid()`.

### documents

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | **stable across re-ingestions** |
| `user_id` | uuid | FK |
| `project_id` | uuid | FK to `projects`, ON DELETE CASCADE |
| `filename` | text | display name |
| `created_at` / `updated_at` | timestamptz | |

RLS: owner-scoped on `user_id = auth.uid()`.

### jobs

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | celery task id |
| `document_id` | uuid | FK |
| `user_id` | uuid | FK |
| `status` | text | `queued | running | succeeded | failed` |
| `current_phase` | text | free-form; UI shows it as-is |
| `pages_total` / `pages_done` | int | progress meter |
| `error` | text | truncated traceback on failure |
| `created_at` / `started_at` / `ended_at` | timestamptz | |

RLS: owner-scoped SELECT/INSERT/UPDATE.

Realtime: `jobs` is added to the `supabase_realtime` publication so
the frontend can subscribe instead of polling.

## RLS rule of thumb

Every user-owned table has the same four owner-scoped policies. The
backend writes/reads with the **service-role key** (which bypasses
RLS), so RLS is defence-in-depth, not the primary auth gate.

## Schema vs. ORM

[app/db/models.py](../app/db/models.py) is the SQLAlchemy 2.0 mapping.
It must mirror the SQL exactly (column types, enum values, defaults).
When you change a migration, change the ORM in the same PR. The
`ROLE_VALUES` tuple in `models.py` is asserted to match the SQL enum
in the test suite.

## Things this page does not own

- Storage bucket layout → [05-storage-and-vectors.md](05-storage-and-vectors.md).
- Auth tier semantics → [02-auth-and-rbac.md](02-auth-and-rbac.md).
- How to apply / roll back migrations → [10-runbooks.md](10-runbooks.md).
