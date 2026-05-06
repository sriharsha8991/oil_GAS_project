"""File storage for markdown output, images, and table JSON files.

All writes use the atomic write-to-tmp + os.replace() pattern to prevent
corrupt partial files on crash.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import aiofiles

from app.utils.logging import get_logger

logger = get_logger(__name__)


async def _atomic_write_text(path: Path, content: str) -> None:
    """Write text content atomically: .tmp → os.replace()."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    async with aiofiles.open(tmp, "w", encoding="utf-8") as f:
        await f.write(content)
    os.replace(tmp, path)


async def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write binary content atomically: .tmp → os.replace()."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    async with aiofiles.open(tmp, "wb") as f:
        await f.write(data)
    os.replace(tmp, path)


class FileStorage:
    """Write extraction outputs to the local filesystem.

    Directory layout:
        {output_dir}/{doc_name}/
            page_001.md
            page_002.md
            ...
            full_document.md
            tables/
                page_001_table_01.json
                page_001_table_01.md
            images/
                page_003_img_01.png
    """

    def __init__(self, output_dir: Path) -> None:
        self._output_dir = output_dir

    def doc_dir(self, doc_name: str) -> Path:
        """Return (and create) the directory for a specific document."""
        d = self._output_dir / self._sanitize(doc_name)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def pages_dir(self, doc_name: str) -> Path:
        """Return (and create) the directory for persisted page PNGs."""
        d = self.doc_dir(doc_name) / "pages_png"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def page_png_path(self, doc_name: str, page_num: int) -> Path:
        """Resolve the on-disk path for a persisted page PNG (no I/O)."""
        return self.pages_dir(doc_name) / f"page_{page_num:03d}.png"

    async def write_entities_json(self, doc_name: str, data: dict[str, Any]) -> Path:
        """Persist the consolidated per-doc entities JSON."""
        d = self.doc_dir(doc_name)
        path = d / "entities.json"
        await _atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False))
        return path

    async def write_page_markdown(self, doc_name: str, page_num: int, content: str) -> Path:
        """Write a single page's markdown to disk."""
        d = self.doc_dir(doc_name)
        path = d / f"page_{page_num:03d}.md"
        await _atomic_write_text(path, content)
        return path

    def page_state_path(self, doc_name: str, page_num: int) -> Path:
        """On-disk path for the per-page extraction state JSON."""
        return self.doc_dir(doc_name) / f"page_{page_num:03d}.state.json"

    async def write_page_state(
        self, doc_name: str, page_num: int, state: dict[str, Any]
    ) -> Path:
        """Persist the locked-in extraction state for a page.

        Written by the worker after the LangGraph completes so a crash
        between extraction and post-processing can be resumed without
        re-paying for Gemini calls AND without losing tables/entities/
        images (the markdown-only resume previously discarded them).
        """
        path = self.page_state_path(doc_name, page_num)
        await _atomic_write_text(
            path, json.dumps(state, indent=2, ensure_ascii=False, default=str)
        )
        return path

    async def write_full_document(self, doc_name: str, content: str) -> Path:
        """Write the merged full-document markdown."""
        d = self.doc_dir(doc_name)
        path = d / "full_document.md"
        await _atomic_write_text(path, content)
        return path

    async def write_table_json(
        self, doc_name: str, page_num: int, table_index: int, data: dict[str, Any]
    ) -> Path:
        """Write a table's structured JSON."""
        d = self.doc_dir(doc_name) / "tables"
        d.mkdir(exist_ok=True)
        path = d / f"page_{page_num:03d}_table_{table_index:02d}.json"
        await _atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False))
        return path

    async def write_table_markdown(
        self, doc_name: str, page_num: int, table_index: int, content: str
    ) -> Path:
        """Write a table's markdown representation."""
        d = self.doc_dir(doc_name) / "tables"
        d.mkdir(exist_ok=True)
        path = d / f"page_{page_num:03d}_table_{table_index:02d}.md"
        await _atomic_write_text(path, content)
        return path

    @staticmethod
    def _sanitize(name: str) -> str:
        """Remove/replace characters unsafe for directory names."""
        return name.replace(" ", "_").replace("/", "_").replace("\\", "_").rstrip(".")
