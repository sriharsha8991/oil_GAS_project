"""File storage for markdown output, images, and table JSON files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiofiles

from app.utils.logging import get_logger

logger = get_logger(__name__)


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

    async def write_page_markdown(self, doc_name: str, page_num: int, content: str) -> Path:
        """Write a single page's markdown to disk."""
        d = self.doc_dir(doc_name)
        path = d / f"page_{page_num:03d}.md"
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(content)
        return path

    async def write_full_document(self, doc_name: str, content: str) -> Path:
        """Write the merged full-document markdown."""
        d = self.doc_dir(doc_name)
        path = d / "full_document.md"
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(content)
        return path

    async def write_table_json(
        self, doc_name: str, page_num: int, table_index: int, data: dict[str, Any]
    ) -> Path:
        """Write a table's structured JSON."""
        d = self.doc_dir(doc_name) / "tables"
        d.mkdir(exist_ok=True)
        path = d / f"page_{page_num:03d}_table_{table_index:02d}.json"
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(data, indent=2, ensure_ascii=False))
        return path

    async def write_table_markdown(
        self, doc_name: str, page_num: int, table_index: int, content: str
    ) -> Path:
        """Write a table's markdown representation."""
        d = self.doc_dir(doc_name) / "tables"
        d.mkdir(exist_ok=True)
        path = d / f"page_{page_num:03d}_table_{table_index:02d}.md"
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(content)
        return path

    async def write_image(self, doc_name: str, page_num: int, img_index: int, data: bytes) -> Path:
        """Write a raw image file."""
        d = self.doc_dir(doc_name) / "images"
        d.mkdir(exist_ok=True)
        path = d / f"page_{page_num:03d}_img_{img_index:02d}.png"
        async with aiofiles.open(path, "wb") as f:
            await f.write(data)
        return path

    @staticmethod
    def _sanitize(name: str) -> str:
        """Remove/replace characters unsafe for directory names."""
        return name.replace(" ", "_").replace("/", "_").replace("\\", "_").rstrip(".")
