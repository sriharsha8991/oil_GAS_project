"""Quick smoke test against the live wellsynthai API.

Uses the bootstrapped super_admin for auth and exercises every public
endpoint. Cleans up everything it creates. Never changes the
super_admin's password.

Run: python -m app.tools.api_smoke
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from typing import Any

import httpx
from dotenv import load_dotenv

APP_URL = os.getenv("APP_URL", "http://127.0.0.1:8080")
SUPER_EMAIL = os.getenv("SMOKE_SUPER_EMAIL", "super-admin@data-smith.ai")
SUPER_PW = os.getenv("SMOKE_SUPER_PW", "12345@st67890")

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def step(name: str):
    def deco(fn):
        def wrapped(*a, **kw):
            t0 = time.time()
            try:
                rv = fn(*a, **kw)
                ms = int((time.time() - t0) * 1000)
                PASSED.append(f"{name} ({ms}ms)")
                print(f"[PASS] {name} ({ms}ms)")
                return rv
            except AssertionError as exc:
                ms = int((time.time() - t0) * 1000)
                FAILED.append((name, str(exc)))
                print(f"[FAIL] {name} ({ms}ms): {exc}")
                return None
            except Exception as exc:
                ms = int((time.time() - t0) * 1000)
                FAILED.append((name, repr(exc)))
                print(f"[ERR ] {name} ({ms}ms): {exc!r}")
                return None
        return wrapped
    return deco


def expect(resp: httpx.Response, *, status: int) -> Any:
    assert resp.status_code == status, f"status {resp.status_code} body={resp.text[:300]}"
    if resp.text:
        try:
            return resp.json()
        except Exception:
            return resp.text
    return None


def main() -> int:
    load_dotenv()

    with httpx.Client(base_url=APP_URL, timeout=30.0) as http:
        # ---------------- 1. Health ----------------
        @step("GET /health")
        def t_health():
            r = http.get("/health")
            body = expect(r, status=200)
            assert body.get("status") == "ok", body

        t_health()

        # ---------------- 2. Auth ----------------
        access_token = {"v": ""}
        refresh_token = {"v": ""}
        super_id = {"v": ""}

        @step("POST /auth/login (super_admin)")
        def t_login():
            r = http.post(
                "/auth/login", json={"email": SUPER_EMAIL, "password": SUPER_PW}
            )
            body = expect(r, status=200)
            assert body["token_type"] == "bearer"
            assert body["user"]["role"] == "super_admin"
            access_token["v"] = body["access_token"]
            refresh_token["v"] = body["refresh_token"]
            super_id["v"] = body["user"]["id"]

        t_login()
        if not access_token["v"]:
            print("\n[fatal] cannot login; aborting")
            return 1

        h = lambda: {"Authorization": f"Bearer {access_token['v']}"}

        @step("POST /auth/login (bad password) -> 401")
        def t_login_bad():
            r = http.post(
                "/auth/login", json={"email": SUPER_EMAIL, "password": "wrong"}
            )
            body = expect(r, status=401)
            assert body["detail"] == "invalid_credentials"

        t_login_bad()

        @step("GET /auth/me")
        def t_me():
            r = http.get("/auth/me", headers=h())
            body = expect(r, status=200)
            assert body["email"] == SUPER_EMAIL
            assert body["role"] == "super_admin"

        t_me()

        @step("PATCH /auth/me/profile")
        def t_me_patch():
            r = http.patch(
                "/auth/me/profile",
                json={"display_name": "Smoke Test", "organization": "Wellsynth HQ"},
                headers=h(),
            )
            body = expect(r, status=200)
            assert body["display_name"] == "Smoke Test"
            assert body["organization"] == "Wellsynth HQ"
            # restore
            r2 = http.patch(
                "/auth/me/profile",
                json={"display_name": "Wellsynth Super Admin", "organization": None},
                headers=h(),
            )
            expect(r2, status=200)

        t_me_patch()

        @step("POST /auth/refresh")
        def t_refresh():
            r = http.post(
                "/auth/refresh", json={"refresh_token": refresh_token["v"]}
            )
            body = expect(r, status=200)
            assert body["access_token"]
            assert body["refresh_token"]
            access_token["v"] = body["access_token"]
            refresh_token["v"] = body["refresh_token"]

        t_refresh()

        @step("POST /auth/refresh (bad token) -> 401")
        def t_refresh_bad():
            r = http.post("/auth/refresh", json={"refresh_token": "garbage"})
            body = expect(r, status=401)
            assert body["detail"] == "invalid_refresh_token"

        t_refresh_bad()

        @step("GET /auth/me (no token) -> 401")
        def t_me_noauth():
            r = http.get("/auth/me")
            assert r.status_code == 401, r.status_code

        t_me_noauth()

        # ---------------- 3. Admin / users ----------------
        @step("GET /admin/users (super_admin)")
        def t_users_list():
            r = http.get("/admin/users", params={"limit": 10}, headers=h())
            body = expect(r, status=200)
            assert isinstance(body["items"], list)
            assert any(u["id"] == super_id["v"] for u in body["items"]) or body["total"] >= 1

        t_users_list()

        @step("GET /admin/users?role=super_admin")
        def t_users_role():
            r = http.get(
                "/admin/users", params={"role": "super_admin"}, headers=h()
            )
            body = expect(r, status=200)
            assert all(u["role"] == "super_admin" for u in body["items"])

        t_users_role()

        @step("GET /admin/users?role=BOGUS -> 400")
        def t_users_role_bad():
            r = http.get("/admin/users", params={"role": "nope"}, headers=h())
            expect(r, status=400)

        t_users_role_bad()

        # Invite a throwaway admin so we can exercise the full surface.
        invitee = f"smoke-{uuid.uuid4().hex[:8]}@example.com"
        invitee_id = {"v": ""}

        @step(f"POST /admin/users/invite role=admin email={invitee}")
        def t_invite():
            r = http.post(
                "/admin/users/invite",
                json={
                    "email": invitee,
                    "display_name": "Smoke Invitee",
                    "role": "admin",
                    "organization": "Smoke Org",
                    "user_quota": 5,
                },
                headers=h(),
            )
            # Resend may legitimately reject an example.com address.
            # In that case the user was rolled back and we got 502.
            if r.status_code == 502:
                body = r.json()
                assert "email_send_failed" in body["detail"]
                print("       (note: Resend rejected example.com, rollback OK)")
                return False
            body = expect(r, status=201)
            assert body["role"] == "admin"
            assert body["organization"] == "Smoke Org"
            assert body["user_quota"] == 5
            invitee_id["v"] = body["id"]
            return True

        invite_ok = t_invite()

        # If the invite went through, exercise downstream endpoints.
        if invite_ok and invitee_id["v"]:

            @step(f"PATCH /admin/users/{{id}}/quota -> 7")
            def t_quota():
                r = http.patch(
                    f"/admin/users/{invitee_id['v']}/quota",
                    json={"user_quota": 7},
                    headers=h(),
                )
                body = expect(r, status=200)
                assert body["user_quota"] == 7

            t_quota()

            @step(f"PATCH /admin/users/{{id}}/role -> drilling_engineer")
            def t_role():
                r = http.patch(
                    f"/admin/users/{invitee_id['v']}/role",
                    json={"role": "drilling_engineer"},
                    headers=h(),
                )
                body = expect(r, status=200)
                assert body["role"] == "drilling_engineer"

            t_role()

            @step(f"PATCH /admin/users/{{id}}/role with bogus -> 400")
            def t_role_bad():
                r = http.patch(
                    f"/admin/users/{invitee_id['v']}/role",
                    json={"role": "nope"},
                    headers=h(),
                )
                expect(r, status=400)

            t_role_bad()

            # Skip resend-invite to avoid burning Resend quota on smoke.

            @step(f"DELETE /admin/users/{{id}}")
            def t_delete():
                r = http.delete(
                    f"/admin/users/{invitee_id['v']}", headers=h()
                )
                expect(r, status=204)

            t_delete()

        # ---------------- 4. Projects ----------------
        project_id = {"v": ""}

        @step("POST /projects")
        def t_project_create():
            r = http.post(
                "/projects",
                json={"name": f"smoke-{uuid.uuid4().hex[:6]}", "description": "smoke"},
                headers=h(),
            )
            # 200 or 201 acceptable
            assert r.status_code in (200, 201), f"{r.status_code} {r.text[:200]}"
            body = r.json()
            assert body.get("id"), body
            project_id["v"] = body["id"]

        t_project_create()

        @step("GET /projects (mine)")
        def t_project_list():
            r = http.get("/projects", headers=h())
            body = expect(r, status=200)
            assert isinstance(body.get("projects"), list), body
            assert "count" in body

        t_project_list()

        if project_id["v"]:

            @step("GET /projects/{id}")
            def t_project_get():
                r = http.get(f"/projects/{project_id['v']}", headers=h())
                body = expect(r, status=200)
                assert body["id"] == project_id["v"]

            t_project_get()

            @step("PATCH /projects/{id}")
            def t_project_patch():
                r = http.patch(
                    f"/projects/{project_id['v']}",
                    json={"description": "smoke updated"},
                    headers=h(),
                )
                expect(r, status=200)

            t_project_patch()

        # ---------------- 5. Documents ----------------
        @step("GET /documents")
        def t_docs():
            r = http.get("/documents", headers=h())
            body = expect(r, status=200)
            assert isinstance(body.get("documents"), list), body

        t_docs()

        @step("GET /documents (random uuid) -> 404")
        def t_docs_404():
            r = http.get(f"/documents/{uuid.uuid4()}", headers=h())
            assert r.status_code in (403, 404), r.status_code

        t_docs_404()

        # ---------------- 6. Ingest ----------------
        @step("GET /ingest")
        def t_ingest_list():
            r = http.get("/ingest", headers=h())
            body = expect(r, status=200)
            assert isinstance(body.get("jobs"), list), body

        t_ingest_list()

        @step("GET /ingest/{random uuid} -> 404")
        def t_ingest_404():
            r = http.get(f"/ingest/{uuid.uuid4()}", headers=h())
            assert r.status_code in (403, 404), r.status_code

        t_ingest_404()

        # ---------------- 7. Search + Chat (soft-land on empty corpus) ----
        @step("GET /search?query=hello (empty corpus tolerated)")
        def t_search():
            r = http.get("/search", params={"query": "hello"}, headers=h())
            assert r.status_code in (200, 422), f"{r.status_code} {r.text[:200]}"

        t_search()

        @step("POST /chat (empty corpus -> 200 with soft-land)")
        def t_chat():
            r = http.post(
                "/chat",
                json={"message": "Are there any documents I can ask about?"},
                headers=h(),
                timeout=60.0,
            )
            assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
            body = r.json()
            assert "answer" in body, body

        t_chat()

        # ---------------- 8. Cleanup project ----------------
        if project_id["v"]:

            @step("DELETE /projects/{id}")
            def t_project_delete():
                r = http.delete(f"/projects/{project_id['v']}", headers=h())
                assert r.status_code in (200, 204), r.status_code

            t_project_delete()

    # -------- summary --------
    print("\n=== smoke summary ===")
    print(f"passed: {len(PASSED)}")
    for s in PASSED:
        print(f"  [ok] {s}")
    print(f"failed: {len(FAILED)}")
    for n, e in FAILED:
        print(f"  [no] {n}: {e}")
    return 0 if not FAILED else 2


if __name__ == "__main__":
    sys.exit(main())
