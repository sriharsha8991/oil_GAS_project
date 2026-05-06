"""Per-tenant artifact storage backed by Supabase Storage.

Path scheme inside every bucket:

    <user_id>/<project_id>/<document_id>/<filename>

Buckets:
    - ``pdfs``      — the original uploaded PDF (``source.pdf``)
    - ``pages``     — rendered page PNGs (``page_NNN.png``)
    - ``artifacts`` — markdown / tables / entities / per-page state JSON

The backend writes via the service-role key (which bypasses RLS) and
issues short-TTL signed URLs for reads. Bucket RLS only checks
``path[1] == auth.uid()`` so user-context clients are still scoped to
their own ``<user_id>/...`` subtree regardless of project/doc layout.

The ``supabase-py`` storage client is sync; every call is wrapped in
``asyncio.to_thread`` so callers stay async-compatible.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from supabase import Client, create_client

from app.config import Settings
from app.utils.logging import get_logger

logger = get_logger(__name__)


PDFS_BUCKET = "pdfs"
PAGES_BUCKET = "pages"
ARTIFACTS_BUCKET = "artifacts"


def _key(user_id: str, project_id: str, document_id: str, *parts: str) -> str:
    return "/".join([user_id, project_id, document_id, *parts])


class SupabaseStorage:
    """All ingestion artifact I/O against Supabase Storage."""

    def __init__(self, settings: Settings) -> None:
        if not settings.supabase_url or not settings.supabase_service_role_key:
            raise RuntimeError(
                "SupabaseStorage requires supabase_url and "
                "supabase_service_role_key in settings"
            )
        self._client: Client = create_client(
            settings.supabase_url, settings.supabase_service_role_key
        )
        self._signed_url_ttl = settings.signed_url_ttl_seconds

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _upload_sync(
        self, bucket: str, key: str, data: bytes, content_type: str
    ) -> None:
        self._client.storage.from_(bucket).upload(
            path=key,
            file=data,
            file_options={"content-type": content_type, "upsert": "true"},
        )

    async def _upload(
        self, bucket: str, key: str, data: bytes, content_type: str
    ) -> None:
        await asyncio.to_thread(self._upload_sync, bucket, key, data, content_type)

    def _download_sync(self, bucket: str, key: str) -> bytes | None:
        try:
            return self._client.storage.from_(bucket).download(key)
        except Exception:
            return None

    async def _download(self, bucket: str, key: str) -> bytes | None:
        return await asyncio.to_thread(self._download_sync, bucket, key)

    # ------------------------------------------------------------------
    # PDFs bucket
    # ------------------------------------------------------------------

    async def write_pdf(
        self, user_id: str, project_id: str, document_id: str, data: bytes
    ) -> str:
        key = _key(user_id, project_id, document_id, "source.pdf")
        await self._upload(PDFS_BUCKET, key, data, "application/pdf")
        return key

    async def read_pdf(
        self, user_id: str, project_id: str, document_id: str
    ) -> bytes | None:
        return await self._download(
            PDFS_BUCKET, _key(user_id, project_id, document_id, "source.pdf")
        )

    # ------------------------------------------------------------------
    # Pages bucket
    # ------------------------------------------------------------------

    async def write_page_png(
        self,
        user_id: str,
        project_id: str,
        document_id: str,
        page_num: int,
        data: bytes,
    ) -> str:
        key = _key(user_id, project_id, document_id, f"page_{page_num:03d}.png")
        await self._upload(PAGES_BUCKET, key, data, "image/png")
        return key

    async def read_page_png(
        self,
        user_id: str,
        project_id: str,
        document_id: str,
        page_num: int,
    ) -> bytes | None:
        return await self._download(
            PAGES_BUCKET,
            _key(user_id, project_id, document_id, f"page_{page_num:03d}.png"),
        )

    def signed_url_page_png(
        self,
        user_id: str,
        project_id: str,
        document_id: str,
        page_num: int,
    ) -> str:
        key = _key(user_id, project_id, document_id, f"page_{page_num:03d}.png")
        resp = self._client.storage.from_(PAGES_BUCKET).create_signed_url(
            key, self._signed_url_ttl
        )
        url = resp.get("signedURL") or resp.get("signedUrl")
        if not url:
            raise RuntimeError(f"Could not sign URL for {key}: {resp}")
        return url

    # ------------------------------------------------------------------
    # Artifacts bucket
    # ------------------------------------------------------------------

    async def write_page_markdown(
        self,
        user_id: str,
        project_id: str,
        document_id: str,
        page_num: int,
        content: str,
    ) -> str:
        key = _key(user_id, project_id, document_id, f"page_{page_num:03d}.md")
        await self._upload(
            ARTIFACTS_BUCKET, key, content.encode("utf-8"), "text/markdown"
        )
        return key

    async def read_page_markdown(
        self,
        user_id: str,
        project_id: str,
        document_id: str,
        page_num: int,
    ) -> str | None:
        data = await self._download(
            ARTIFACTS_BUCKET,
            _key(user_id, project_id, document_id, f"page_{page_num:03d}.md"),
        )
        return data.decode("utf-8") if data is not None else None

    async def write_page_state(
        self,
        user_id: str,
        project_id: str,
        document_id: str,
        page_num: int,
        state: dict[str, Any],
    ) -> str:
        key = _key(
            user_id, project_id, document_id, f"page_{page_num:03d}.state.json"
        )
        body = json.dumps(state, indent=2, ensure_ascii=False, default=str)
        await self._upload(
            ARTIFACTS_BUCKET, key, body.encode("utf-8"), "application/json"
        )
        return key

    async def write_full_document(
        self, user_id: str, project_id: str, document_id: str, content: str
    ) -> str:
        key = _key(user_id, project_id, document_id, "full_document.md")
        await self._upload(
            ARTIFACTS_BUCKET, key, content.encode("utf-8"), "text/markdown"
        )
        return key

    async def read_full_document(
        self, user_id: str, project_id: str, document_id: str
    ) -> str | None:
        data = await self._download(
            ARTIFACTS_BUCKET,
            _key(user_id, project_id, document_id, "full_document.md"),
        )
        return data.decode("utf-8") if data is not None else None

    async def write_table_json(
        self,
        user_id: str,
        project_id: str,
        document_id: str,
        page_num: int,
        table_index: int,
        data: dict[str, Any],
    ) -> str:
        key = _key(
            user_id,
            project_id,
            document_id,
            "tables",
            f"page_{page_num:03d}_table_{table_index:02d}.json",
        )
        body = json.dumps(data, indent=2, ensure_ascii=False)
        await self._upload(
            ARTIFACTS_BUCKET, key, body.encode("utf-8"), "application/json"
        )
        return key

    async def write_table_markdown(
        self,
        user_id: str,
        project_id: str,
        document_id: str,
        page_num: int,
        table_index: int,
        content: str,
    ) -> str:
        key = _key(
            user_id,
            project_id,
            document_id,
            "tables",
            f"page_{page_num:03d}_table_{table_index:02d}.md",
        )
        await self._upload(
            ARTIFACTS_BUCKET, key, content.encode("utf-8"), "text/markdown"
        )
        return key

    async def write_entities_json(
        self,
        user_id: str,
        project_id: str,
        document_id: str,
        data: dict[str, Any],
    ) -> str:
        key = _key(user_id, project_id, document_id, "entities.json")
        body = json.dumps(data, indent=2, ensure_ascii=False)
        await self._upload(
            ARTIFACTS_BUCKET, key, body.encode("utf-8"), "application/json"
        )
        return key

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def _list_keys_recursive(
        self, bucket: str, prefix: str, depth: int = 0
    ) -> list[str]:
        if depth > 6:
            return []
        keys: list[str] = []
        try:
            entries = self._client.storage.from_(bucket).list(prefix) or []
        except Exception:
            return keys
        for entry in entries:
            name = entry.get("name")
            if not name:
                continue
            full = f"{prefix}/{name}" if prefix else name
            if entry.get("id") is None:
                keys.extend(self._list_keys_recursive(bucket, full, depth + 1))
            else:
                keys.append(full)
        return keys

    async def delete_document(
        self, user_id: str, project_id: str, document_id: str
    ) -> None:
        prefix = f"{user_id}/{project_id}/{document_id}"
        await self._delete_prefix(prefix)

    async def delete_project(self, user_id: str, project_id: str) -> None:
        prefix = f"{user_id}/{project_id}"
        await self._delete_prefix(prefix)

    async def _delete_prefix(self, prefix: str) -> None:
        def _wipe() -> None:
            for bucket in (PDFS_BUCKET, PAGES_BUCKET, ARTIFACTS_BUCKET):
                keys = self._list_keys_recursive(bucket, prefix)
                if keys:
                    try:
                        self._client.storage.from_(bucket).remove(keys)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "supabase_storage_delete_failed",
                            bucket=bucket,
                            prefix=prefix,
                            count=len(keys),
                            error=str(exc),
                        )

        await asyncio.to_thread(_wipe)
