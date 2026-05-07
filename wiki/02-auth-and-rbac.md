# 02 — Auth & RBAC

> _Owns: how identity, sessions, and role-based gates work end-to-end._
> _Last contract change: 2026-05-07 — invite-only model with org+admin+quota tiers._

## Tiers

| Role | Origin | Scope |
|---|---|---|
| `super_admin` | Bootstrapped via CLI; can be cloned via the invite endpoint by another super_admin. | Unrestricted. Adds & deletes admins; sets per-admin quotas. |
| `admin` | Invited by a super_admin with `role: "admin"`, an `organization`, and a `user_quota`. | Org-scoped. Invites users in their own org only, up to `user_quota`. Cannot mint admins/super_admins. |
| `drilling_engineer` / `geologist` / `asset_manager` / `qa_steward` | Invited by an admin or super_admin. | End users. Can access their own projects/documents only. |

There is **no public sign-up.** Every account is created via
`POST /admin/users/invite`.

## How sign-in works

1. FE posts `{email, password}` to `/auth/login`.
2. Backend signs in against Supabase using the **anon key** in a
   throwaway client (the service-role admin client must stay
   unauthenticated to keep `auth.admin.*` calls working — this was a
   real bug we fixed; see commit history of [app/auth/routes.py](../app/auth/routes.py)).
3. Backend looks up `users_profile` and returns `{access_token, refresh_token, expires_in, token_type, user}`.
4. FE attaches `Authorization: Bearer <access_token>` to every
   subsequent request.
5. ~60s before expiry FE rotates via `/auth/refresh`. Both tokens are
   reissued on each call.

## Middleware verification

[app/auth/middleware.py](../app/auth/middleware.py) does on every non-anonymous request:

1. Decode JWT against Supabase JWKS (cached `PyJWKClient`, ES256).
2. Pull `users_profile` row by `id` to attach `role`, `display_name`,
   `organization`.
3. Set `request.state.user = AuthUser(...)`.
4. On any failure → `401`.

`OPTIONS` preflights are early-returned before auth so CORS works.

Anonymous paths (no JWT required):

```
/                          /openapi.json
/health                    /docs
/redoc                     /docs/oauth2-redirect
/auth/login                /auth/refresh
/static/*                  (prefix)
```

## Role enforcement

Two helpers in [app/auth/deps.py](../app/auth/deps.py):

- `current_user(request)` — `AuthUser` dataclass; raises 401 if absent.
- `require_role(*roles)` — Dependency factory. On mismatch returns
  `403 {error_code: "requires_role", required_roles, actual_role}`.

The admin-users router does not gate the whole router any more —
individual routes pick `require_role("super_admin")` or
`_require_inviter(...)` (admin or super_admin) so admins can call
invite/list/delete with org-scoping enforced inside the handler.

## Invite flow (one place to change)

[app/routes/admin_users.py](../app/routes/admin_users.py):

1. `_require_inviter(caller)` — must be admin or super_admin.
2. Resolve `requested_role`. Admin callers may not pick `admin` or
   `super_admin` → 403 `role_not_assignable_by_admin`.
3. Resolve `organization`:
   - Admin callers: forced to `caller.organization` (409 if null).
   - Super_admin: free choice via request body.
4. Quota check (admin only): count rows where `users_profile.invited_by = caller.id`.
   If `>= caller.user_quota` (and quota > 0) → 403 `quota_exceeded`.
5. `auth.admin.create_user({email, password, email_confirm: True})`
   with a 16-char `secrets.token_urlsafe(12)` password.
6. Wait up to 2s for the `handle_new_user` Postgres trigger to insert
   `users_profile`. Then `UPDATE users_profile SET role, organization,
   user_quota, invited_by = caller.id`.
7. Send email via Resend ([app/auth/email.py](../app/auth/email.py)).
   On any send error: delete the auth.users row → 502 `email_send_failed`.

The resend-invite endpoint does steps 5b (admin update password) and 7
only — does not touch role/org/quota.

## Quota semantics

`user_quota` is meaningful **only for admins** (a number on every other
row is informational). 0 = unlimited.

The count uses `users_profile.invited_by = admin.id` rather than
"users in the same org" so that a super_admin manually adjusting an
admin's organization later doesn't retroactively change the quota count.

## Password change

`POST /auth/change-password` is the **only** way a user can change their
own password. Backend:

1. Verifies `current_password` via a throwaway anon-key sign-in (so
   the service-role admin client stays unauthenticated).
2. Calls `auth.admin.update_user_by_id(user.id, {"password": new})` on
   a fresh service-role client.

A user signing in with a temp password can use the existing access
token to call this endpoint immediately; the token stays valid across
the password change.

## Bootstrapping super_admin

For a fresh environment (or a "no super_admin can sign in" emergency):

```powershell
.\.venv\Scripts\python.exe -m app.tools.bootstrap_super_admin `
    --email me@example.com `
    --password "yourStrongPassword" `
    --display-name "Your Name"
```

Idempotent: if the email exists, only the role is upgraded. See
[10-runbooks.md](10-runbooks.md).

## Error codes (FE-facing)

See [§0.0.8 of communication_from_backend.md](../frontend_docs/communication_from_backend.md)
for the full FE-facing list. Backend authoritative source:

- 401: `invalid_credentials`, `invalid_refresh_token`, `invalid_current_password`, `unauthorized`
- 403: `requires_role`, `role_not_assignable_by_admin`, `quota_exceeded`, `cross_org_forbidden`, `cannot_delete_admin_or_super`, `cannot_resend_admin_or_super`
- 400: `cannot_delete_self`, `cannot_resend_self`, `invalid_role`, `password_update_failed: ...`
- 409: `email_already_registered`, `admin_missing_organization`
- 502: `email_send_failed: ...`, `invite_no_user_returned`, `profile_not_created`

## Things this page does not own

- Storage RLS / vector tenant filter → [05-storage-and-vectors.md](05-storage-and-vectors.md).
- Schema definition of `users_profile` → [06-database-schema.md](06-database-schema.md).
- Endpoint-by-endpoint contract → [07-api-reference.md](07-api-reference.md).
