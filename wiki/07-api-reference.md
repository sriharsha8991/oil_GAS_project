# 07 — API Reference

> _Owns: every public HTTP endpoint and its request/response contract.
> Authoritative shape lives in [openapi.json](../openapi.json); this
> page is the human-readable summary._

## Conventions

- All non-anonymous endpoints require `Authorization: Bearer <jwt>`.
- All bodies are JSON unless noted (`/ingest/upload` is multipart).
- Identifiers in paths are uuids unless noted.
- Error shape (when not soft-landing): `{"detail": "<error_code or message>"}`
  or `{"detail": {"error_code": "...", ...}}` for structured errors.

## Anonymous

### `GET /health`

Returns `{"status": "ok"}` if the app is up. No auth.

### `POST /auth/login`

Body: `{ "email": str, "password": str }`

200: `{ access_token, refresh_token, expires_in, token_type: "bearer", user }`
where `user = { id, email, role, display_name, organization }`.

401 `invalid_credentials` — wrong email or password.

### `POST /auth/refresh`

Body: `{ "refresh_token": str }`

200: same shape as `/auth/login` (rotates both tokens).

401 `invalid_refresh_token`.

## Auth (bearer)

### `POST /auth/change-password` → 204

Body: `{ "current_password": str, "new_password": str }`. `new_password`
must be ≥ 8 chars.

401 `invalid_current_password` · 400 `password_update_failed: ...`.

### `GET /auth/me`

200: `{ id, email, role, display_name, organization }`.

### `PATCH /auth/me/profile`

Body (all optional): `{ "display_name": str?, "organization": str? }`.

200: refreshed `me` shape.

## Admin / users (bearer; admin or super_admin)

### `GET /admin/users`

Query: `q`, `role`, `limit` (1-200, default 50), `offset` (≥0).

200: `{ items: UserRow[], total, limit, offset }` where

```
UserRow = { id, email, role, display_name, organization,
            user_quota, invited_by, created_at }
```

Admin sees only `caller.organization`. Super_admin sees everyone.

403 `requires_role` if caller is below admin tier.

### `POST /admin/users/invite` → 201

Body:
```json
{
  "email": "...",
  "display_name": "...",
  "role": "admin | super_admin | drilling_engineer | ...",
  "organization": "...",
  "user_quota": 5
}
```

Constraints:
- Admin caller: `role` must be in `{drilling_engineer, geologist, asset_manager, qa_steward}`. `organization` is ignored (forced to caller's).
- Super_admin: any role; `organization` is required when inviting an admin; `user_quota` only meaningful when inviting an `admin`.

201: `UserRow` + `message`.

403 `role_not_assignable_by_admin` · 403 `quota_exceeded` · 409 `email_already_registered` · 409 `admin_missing_organization` · 502 `email_send_failed: ...`.

### `PATCH /admin/users/{id}/role` → super_admin only

Body: `{ "role": str }`. Returns updated `UserRow`.

400 `invalid_role` · 404 `user_not_found`.

### `PATCH /admin/users/{id}/quota` → super_admin only

Body: `{ "user_quota": int }` (0–10000). Returns updated `UserRow`.

### `POST /admin/users/{id}/resend-invite`

Generates a new random password, updates `auth.users`, mails it.

403 `cannot_resend_admin_or_super` (admin caller) · 403 `cross_org_forbidden` · 400 `cannot_resend_self` · 502 `email_send_failed: ...`.

### `DELETE /admin/users/{id}` → 204

Admin can delete only same-org non-admin users. Cannot delete self.

403 `cannot_delete_admin_or_super` · 403 `cross_org_forbidden` · 400 `cannot_delete_self`.

## Projects (bearer)

| Method | Path | Notes |
|---|---|---|
| `POST` | `/projects` | Body `{ name, description? }` → `{ id, ... }` |
| `GET` | `/projects` | `{ count, projects: [...] }` |
| `GET` | `/projects/{id}` | Project detail |
| `PATCH` | `/projects/{id}` | Body `{ name?, description? }` |
| `DELETE` | `/projects/{id}` | Cascades documents/jobs/Storage/Qdrant |

## Documents (bearer)

| Method | Path | Notes |
|---|---|---|
| `GET` | `/documents` | `{ count, documents: [...] }`. Optional `?project_id=` filter |
| `GET` | `/documents/{id}` | Metadata + `latest_job` |
| `DELETE` | `/documents/{id}` | Cascades Storage/Qdrant |
| `GET` | `/documents/{id}/full` | Full markdown (text/markdown) |
| `GET` | `/documents/{id}/pages/{n}/image` | 302 → signed PNG URL |
| `GET` | `/documents/{id}/pages/{n}/image/crop` | Bbox crop. Query: `ymin, xmin, ymax, xmax` (0..1) |

## Ingest (bearer)

| Method | Path | Notes |
|---|---|---|
| `POST` | `/ingest` | Body `{ project_id, file_paths: [...] }` → `[{ job_id, document_id, filename, status }]` |
| `POST` | `/ingest/upload` | Multipart: `project_id` + `files`. Same response. |
| `GET` | `/ingest` | `{ count, jobs: [...] }` scoped to caller |
| `GET` | `/ingest/{job_id}` | Single job row (404 if not yours) |

## Search (bearer)

`GET /search`

Query (required): `q`. Optional: `limit` (1-100, default 10), `project_id`, `document_id`, `page_num`, `score_threshold`.

200: list of chunks with `{ score, text, document_id, page_num, ... }`.

## Chat (bearer)

`POST /chat`

Body:
```json
{ "message": "...", "project_id": "...?", "document_id": "...?" }
```

200:
```json
{
  "answer": "...",
  "sources": [{"document_id": "...", "page_num": 4, "score": 0.83, "snippet": "..."}],
  "evidence": [...],
  "attached_pages": [{"document_id": "...", "page_num": 4, "url": "/documents/.../pages/4/image"}]
}
```

## Smoke coverage

[app/tools/api_smoke.py](../app/tools/api_smoke.py) exercises 23 of
the above contracts in ~30 seconds against a live stack. See
[09-testing.md](09-testing.md).
