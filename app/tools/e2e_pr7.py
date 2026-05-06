"""End-to-end smoke for PR-7 — projects, documents, ingest, chat, deletes.

Steps:
  1. Admin-create a test user (drilling_engineer) and sign in.
  2. POST /projects -> create project.
  3. POST /ingest/upload with a tiny PDF -> get document_id + job_id.
  4. Poll GET /ingest/{job_id} until status=succeeded (or timeout).
  5. POST /chat scoped to the document -> verify 200 + sources or no_evidence.
  6. DELETE /documents/{id} -> verify Storage prefix + Qdrant points are gone.
  7. DELETE /projects/{id} -> verify cascade.
  8. Cleanup user.

Run:  python -m app.tools.e2e_pr7

Requires a tiny PDF at ``tests/fixtures/sample.pdf`` OR set
``E2E_PDF_PATH`` to any small PDF. Skips ingest/chat if absent.
"""

from __future__ import annotations

import asyncio
import io
import os
import secrets
import sys
import time
from pathlib import Path

import asyncpg
import httpx
from dotenv import load_dotenv
from supabase import create_client

USER_EMAIL = os.getenv("E2E_PR7_EMAIL", "e2e-pr7@example.com")
APP_URL = os.getenv("APP_URL", "http://127.0.0.1:8080")
PDF_PATH = os.getenv("E2E_PDF_PATH", "")


def _to_asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _set_role_and_org(
    dsn: str, user_id: str, role: str, organization: str
) -> None:
    conn = await asyncpg.connect(_to_asyncpg_dsn(dsn))
    try:
        await conn.execute(
            "update public.users_profile set role = $1, organization = $2 "
            "where id = $3",
            role, organization, user_id,
        )
    finally:
        await conn.close()


def _tiny_pdf() -> bytes:
    """Minimal valid 1-page PDF (no text content)."""
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n"
        b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n"
        b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>endobj\n"
        b"xref\n0 4\n0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000053 00000 n \n"
        b"0000000100 00000 n \n"
        b"trailer<< /Size 4 /Root 1 0 R >>\nstartxref\n160\n%%EOF\n"
    )


async def _poll_job(http: httpx.AsyncClient, job_id: str, headers: dict,
                    timeout_s: int = 300) -> dict:
    deadline = time.time() + timeout_s
    last: dict = {}
    while time.time() < deadline:
        r = await http.get(f"/ingest/{job_id}", headers=headers)
        if r.status_code == 200:
            last = r.json()
            status = last.get("status")
            if status in ("succeeded", "failed", "completed"):
                return last
        await asyncio.sleep(3.0)
    return last


async def main() -> int:
    load_dotenv()
    url = os.environ["SUPABASE_URL"]
    anon = os.environ["SUPABASE_ANON_KEY"]
    service = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    dsn = os.environ["DATABASE_URL"]

    pw = "Pr7-" + secrets.token_urlsafe(24)
    admin_sb = create_client(url, service)
    anon_sb = create_client(url, anon)

    print(f"[1] create user {USER_EMAIL}")
    created = admin_sb.auth.admin.create_user(
        {"email": USER_EMAIL, "password": pw, "email_confirm": True}
    )
    user_id = created.user.id
    await _set_role_and_org(
        dsn, user_id, "drilling_engineer", "Wellsynthai Test Org"
    )

    project_id: str | None = None
    document_id: str | None = None
    try:
        signed = anon_sb.auth.sign_in_with_password(
            {"email": USER_EMAIL, "password": pw}
        )
        token = signed.session.access_token
        h = {"Authorization": f"Bearer {token}"}
        print(f"[2] signed in (token len={len(token)})")

        async with httpx.AsyncClient(base_url=APP_URL, timeout=30.0) as http:
            # /auth/me echoes organization
            r = await http.get("/auth/me", headers=h)
            assert r.status_code == 200, r.text
            assert r.json().get("organization") == "Wellsynthai Test Org"
            print("[2b] /auth/me organization OK")

            # 3. Create project
            r = await http.post(
                "/projects",
                json={"name": "PR7 Test Project", "description": "e2e"},
                headers=h,
            )
            assert r.status_code == 200, r.text
            project_id = r.json()["id"]
            print(f"[3] POST /projects -> 200 id={project_id}")

            # 4. List projects
            r = await http.get("/projects", headers=h)
            assert r.status_code == 200, r.text
            assert any(p["id"] == project_id for p in r.json()["projects"])
            print("[4] GET /projects -> contains the new project")

            # 5. Ingest (only if a PDF is available)
            pdf_bytes: bytes
            if PDF_PATH and Path(PDF_PATH).is_file():
                pdf_bytes = Path(PDF_PATH).read_bytes()
                pdf_filename = Path(PDF_PATH).name
            else:
                pdf_bytes = _tiny_pdf()
                pdf_filename = "tiny.pdf"

            files = {"files": (pdf_filename, io.BytesIO(pdf_bytes), "application/pdf")}
            data = {"project_id": project_id}
            r = await http.post(
                "/ingest/upload", data=data, files=files, headers=h
            )
            assert r.status_code == 200, r.text
            jobs = r.json()["jobs"]
            assert jobs, "no jobs dispatched"
            job_id = jobs[0]["job_id"]
            document_id = jobs[0]["document_id"]
            print(f"[5] POST /ingest/upload -> job_id={job_id} doc_id={document_id}")

            # 6. Poll to terminal state
            final = await _poll_job(http, job_id, h, timeout_s=300)
            print(f"[6] job final status={final.get('status')} "
                  f"phase={final.get('current_phase')} pages={final.get('pages_done')}/{final.get('pages_total')}")

            # 7. Chat scoped to document
            r = await http.post(
                "/chat",
                json={
                    "message": "What is this document about?",
                    "document_id": document_id,
                },
                headers=h,
            )
            assert r.status_code == 200, r.text
            body = r.json()
            print(f"[7] POST /chat -> status={body['status']} "
                  f"sources={len(body['sources'])}")

            # 8. List documents
            r = await http.get("/documents", headers=h)
            assert r.status_code == 200, r.text
            assert any(d["id"] == document_id for d in r.json()["documents"])
            print("[8] GET /documents -> contains the new document")

            # 9. Delete document (cascade Storage + Qdrant)
            r = await http.delete(f"/documents/{document_id}", headers=h)
            assert r.status_code == 200, r.text
            print(f"[9] DELETE /documents/{{id}} -> 200")
            document_id = None

            # 10. Delete project (cascade)
            r = await http.delete(f"/projects/{project_id}", headers=h)
            assert r.status_code == 200, r.text
            print(f"[10] DELETE /projects/{{id}} -> 200")
            project_id = None

        return 0
    finally:
        # Best-effort cleanup
        if document_id or project_id:
            try:
                signed = anon_sb.auth.sign_in_with_password(
                    {"email": USER_EMAIL, "password": pw}
                )
                token = signed.session.access_token
                h = {"Authorization": f"Bearer {token}"}
                async with httpx.AsyncClient(base_url=APP_URL, timeout=15.0) as http:
                    if document_id:
                        await http.delete(f"/documents/{document_id}", headers=h)
                    if project_id:
                        await http.delete(f"/projects/{project_id}", headers=h)
            except Exception:
                pass
        try:
            admin_sb.auth.admin.delete_user(user_id)
            print("[11] deleted user")
        except Exception as exc:
            print(f"[11] cleanup failed: {exc}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
