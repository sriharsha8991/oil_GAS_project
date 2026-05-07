# 09 — Testing

> _Owns: every kind of test in the repo and how to run/extend it._

## Test layers

| Layer | Where | Purpose |
|---|---|---|
| Unit | `tests/test_*.py` | Pure-python tests for pipeline + agents (no network) |
| End-to-end | `app/tools/e2e_*.py` | Drives the live HTTP + Postgres + Supabase + Resend stack |
| API smoke | `app/tools/api_smoke.py` | 30s contract check across 23 endpoints |

There is intentionally **no** integration layer between unit and e2e
— either the function is pure enough to test in isolation, or you
need the whole stack.

## Unit tests

Run:
```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

Files:
- [tests/test_agents.py](../tests/test_agents.py) — extractor/validator/escalator with stubbed Gemini.
- [tests/test_page_streamer.py](../tests/test_page_streamer.py) — concurrency + retry semantics.
- [tests/test_postprocessor.py](../tests/test_postprocessor.py) — full-document aggregation.
- [tests/test_preprocessing.py](../tests/test_preprocessing.py) — PDF rendering edge cases.

When you add a feature, add a unit test under the relevant file.
Don't introduce new test files unless the topic is genuinely new.

## End-to-end

[app/tools/e2e_invite_flow.py](../app/tools/e2e_invite_flow.py) — 15-step invite/quota/role/login flow.
[app/tools/e2e_admin.py](../app/tools/e2e_admin.py) — earlier PR-5.1 admin-user smoke (legacy; kept for regression).
[app/tools/e2e_auth.py](../app/tools/e2e_auth.py) — JWT verification edge cases.
[app/tools/e2e_pr7.py](../app/tools/e2e_pr7.py) — projects + documents + tenant-scoped vectors.

Run:
```powershell
.\.venv\Scripts\python.exe -m app.tools.e2e_invite_flow
```

Each script:

- Cleans up the users/projects it creates (try/finally).
- Asserts loudly with `assert ... , msg` so PowerShell shows the failing line.
- Prints `[step N] ...` for every checkpoint.

## API smoke

[app/tools/api_smoke.py](../app/tools/api_smoke.py) — exercises the
full FE-facing surface in ~30 seconds. Logs in as the bootstrapped
super_admin (`super-admin@data-smith.ai`) and runs:

- `/health`, `/auth/login` (good + bad), `/auth/me` (with + without token),
- `/auth/me/profile` round-trip, `/auth/refresh` (good + bad),
- `/admin/users` list + filter, invalid-role 400, `/admin/users/invite`,
- `/projects` create/list/get/patch/delete,
- `/documents` list + bogus 404,
- `/ingest` list + bogus 404,
- `/search` (empty corpus tolerated),
- `/chat` (empty corpus → soft-land 200).

Run:
```powershell
.\.venv\Scripts\python.exe -m app.tools.api_smoke
```

Expected: `passed: 23 / failed: 0`.

This is the first thing to run after any change that touches routing,
middleware, or config.

## Adding a new endpoint test

1. Add the call to [app/tools/api_smoke.py](../app/tools/api_smoke.py)
   in the same `with httpx.Client(...)` block as a new `@step` function.
2. Re-run the smoke and ensure your step shows `[PASS]`.
3. If the endpoint needs role gating beyond super_admin, also add the
   negative test (call as a lower-tier user, expect 403) under
   [app/tools/e2e_invite_flow.py](../app/tools/e2e_invite_flow.py).

## Resend in tests

The smoke and e2e scripts deliberately do **not** trigger Resend with
real-deliverable addresses; they use `example.com` (Resend rejects → backend
rolls back, smoke accepts the 502). The full mail round-trip is a manual
test against your own inbox — see [10-runbooks.md](10-runbooks.md).

## Things this page does not own

- The actual contract being tested → [07-api-reference.md](07-api-reference.md).
- How to fix a failing test → that's debugging, not testing.
