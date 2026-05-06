"""Tests for the v3 per-page post-processor."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.schemas import PageBaseline
from app.pipeline.postprocessor import (
    PostProcessor,
    _build_full_document,
    _build_page_map,
    _detect_header_footer,
    _format_extracted_page,
    _merge_cross_page_tables,
    _merge_table_json,
    _merge_table_markdown,
    _normalize_header_line,
    _remove_header_footer,
    _table_md_to_json,
)
from app.storage.supabase_storage import SupabaseStorage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def baseline(page_num: int, text: str = "", **kwargs) -> PageBaseline:
    return PageBaseline(page_num=page_num, text=text, char_count=len(text), **kwargs)


def page_state(page_num: int, **extracted) -> dict:
    """Build a PageState-shaped dict with `final` populated."""
    final = {
        "page_num": page_num,
        "markdown": extracted.get("markdown", ""),
        "tables": extracted.get("tables", []),
        "images": extracted.get("images", []),
        "cross_page_notes": extracted.get("cross_page_notes", ""),
    }
    return {
        "page_num": page_num,
        "iteration": extracted.get("iteration", 1),
        "escalated": extracted.get("escalated", False),
        "final": final,
        "extracted": final,
    }


# ---------------------------------------------------------------------------
# _build_page_map
# ---------------------------------------------------------------------------


class TestBuildPageMap:
    def test_basic(self):
        results = {0: page_state(0, markdown="m0"), 1: page_state(1, markdown="m1")}
        m = _build_page_map(results)
        assert m[0]["markdown"] == "m0"
        assert m[1]["markdown"] == "m1"

    def test_falls_back_to_extracted_when_no_final(self):
        results = {
            0: {
                "page_num": 0,
                "extracted": {"page_num": 0, "markdown": "from_extracted"},
                "final": {},
            }
        }
        m = _build_page_map(results)
        assert m[0]["markdown"] == "from_extracted"

    def test_skips_non_dict(self):
        m = _build_page_map({0: "not a dict"})  # type: ignore[arg-type]
        assert m == {}


# ---------------------------------------------------------------------------
# Cross-page table merging
# ---------------------------------------------------------------------------


class TestMergeTableMarkdown:
    def test_empty(self):
        assert _merge_table_markdown([]) == ""

    def test_single_fragment_returned_unchanged(self):
        assert _merge_table_markdown(["| h |\n|---|\n| a |"]) == "| h |\n|---|\n| a |"

    def test_two_fragments_keeps_first_header(self):
        f1 = "| h |\n|---|\n| a |"
        f2 = "| h |\n|---|\n| b |"
        merged = _merge_table_markdown([f1, f2])
        assert merged.count("| h |") == 1  # header kept once
        assert "| a |" in merged
        assert "| b |" in merged


class TestMergeTableJson:
    def test_empty(self):
        assert _merge_table_json([]) == {}

    def test_single(self):
        f = {"headers": ["a"], "rows": [["1"]]}
        assert _merge_table_json([f]) == f

    def test_combines_rows(self):
        f1 = {"headers": ["a"], "rows": [["1"]]}
        f2 = {"headers": ["a"], "rows": [["2"], ["3"]]}
        merged = _merge_table_json([f1, f2])
        assert merged["headers"] == ["a"]
        assert merged["rows"] == [["1"], ["2"], ["3"]]


class TestMergeCrossPageTables:
    def test_empty_page_map(self):
        assert _merge_cross_page_tables({}) == []

    def test_no_continuation_flag(self):
        page_map = {
            0: {"tables": [{"markdown": "t1", "json_data": {"headers": ["a"], "rows": []}}]},
        }
        assert _merge_cross_page_tables(page_map) == []

    def test_simple_continuation(self):
        page_map = {
            0: {"tables": [{
                "markdown": "| a |\n|---|\n| 1 |",
                "json_data": {"headers": ["a"], "rows": [["1"]]},
                "continues_on_next_page": True,
            }]},
            1: {"tables": [{
                "markdown": "| a |\n|---|\n| 2 |",
                "json_data": {"headers": ["a"], "rows": [["2"]]},
                "continues_on_next_page": False,
            }]},
        }
        merged = _merge_cross_page_tables(page_map)
        assert len(merged) == 1
        assert merged[0]["page_start"] == 0
        assert merged[0]["page_end"] == 1
        assert merged[0]["json_data"]["rows"] == [["1"], ["2"]]

    def test_three_page_chain(self):
        def t(val: str, cont: bool):
            return {
                "markdown": f"| a |\n|---|\n| {val} |",
                "json_data": {"headers": ["a"], "rows": [[val]]},
                "continues_on_next_page": cont,
            }

        page_map = {
            0: {"tables": [t("1", True)]},
            1: {"tables": [t("2", True)]},
            2: {"tables": [t("3", False)]},
        }
        merged = _merge_cross_page_tables(page_map)
        assert len(merged) == 1
        assert merged[0]["page_end"] == 2
        assert merged[0]["json_data"]["rows"] == [["1"], ["2"], ["3"]]

    def test_non_adjacent_pages_break_chain(self):
        page_map = {
            0: {"tables": [{
                "markdown": "| a |", "json_data": {"headers": ["a"], "rows": []},
                "continues_on_next_page": True,
            }]},
            5: {"tables": [{
                "markdown": "| a |", "json_data": {"headers": ["a"], "rows": []},
                "continues_on_next_page": False,
            }]},
        }
        assert _merge_cross_page_tables(page_map) == []

    def test_column_count_mismatch_breaks_chain(self):
        page_map = {
            0: {"tables": [{
                "markdown": "| a | b |",
                "json_data": {"headers": ["a", "b"], "rows": []},
                "continues_on_next_page": True,
            }]},
            1: {"tables": [{
                "markdown": "| x |",
                "json_data": {"headers": ["x"], "rows": []},
                "continues_on_next_page": False,
            }]},
        }
        assert _merge_cross_page_tables(page_map) == []


# ---------------------------------------------------------------------------
# Header/footer detection
# ---------------------------------------------------------------------------


class TestDetectHeaderFooter:
    def test_too_few_pages_returns_empty(self):
        baselines = [baseline(i, "Header A\nbody") for i in range(3)]
        assert _detect_header_footer({}, baselines) == set()

    def test_repeated_header_detected(self):
        baselines = [
            baseline(i, f"COMPANY HEADER\npage {i} content here\nFooter line")
            for i in range(10)
        ]
        result = _detect_header_footer({}, baselines)
        # both header and footer normalized strings should appear
        assert any("COMPANY HEADER" in r for r in result)


class TestNormalizeHeaderLine:
    def test_strips_markdown(self):
        assert _normalize_header_line("**Hello**") == "Hello"

    def test_strips_pure_page_numbers(self):
        assert _normalize_header_line("42") == ""


class TestRemoveHeaderFooter:
    def test_removes_matching_lines(self):
        text = "header\nbody line\nfooter"
        out = _remove_header_footer(text, {"header", "footer"})
        assert "body line" in out
        assert "header" not in out


# ---------------------------------------------------------------------------
# Page formatting
# ---------------------------------------------------------------------------


class TestFormatExtractedPage:
    def test_includes_markdown(self):
        ext = {"markdown": "## Heading\n\ntext", "tables": [], "images": []}
        out = _format_extracted_page(0, ext, set())
        assert "## Heading" in out

    def test_renders_image_descriptions(self):
        ext = {
            "markdown": "body",
            "tables": [],
            "images": [{"id": "img1", "description": "well log", "location": "top"}],
        }
        out = _format_extracted_page(0, ext, set())
        assert "well log" in out
        assert "img1" in out
        assert "top" in out

    def test_renders_cross_page_notes(self):
        ext = {
            "markdown": "body", "tables": [], "images": [],
            "cross_page_notes": "table continues on page 5",
        }
        out = _format_extracted_page(0, ext, set())
        assert "table continues on page 5" in out


# ---------------------------------------------------------------------------
# Full document builder
# ---------------------------------------------------------------------------


class TestBuildFullDocument:
    def test_uses_first_h1_as_title(self):
        baselines = [baseline(0, "x")]
        page_map = {0: {"markdown": "# Well Report ABC\nbody", "tables": [], "images": []}}
        doc = _build_full_document("file.pdf", baselines, page_map, set())
        assert doc.startswith("# Well Report ABC")

    def test_falls_back_to_filename(self):
        baselines = [baseline(0)]
        page_map: dict = {}
        doc = _build_full_document("myfile.pdf", baselines, page_map, set())
        assert doc.startswith("# myfile.pdf")

    def test_has_per_page_markers(self):
        baselines = [baseline(0, "a"), baseline(1, "b")]
        page_map = {
            0: {"markdown": "p0", "tables": [], "images": []},
            1: {"markdown": "p1", "tables": [], "images": []},
        }
        doc = _build_full_document("f.pdf", baselines, page_map, set())
        assert "<!-- Page 1 -->" in doc
        assert "<!-- Page 2 -->" in doc


# ---------------------------------------------------------------------------
# Table markdown → JSON
# ---------------------------------------------------------------------------


class TestTableMdToJson:
    def test_basic(self):
        md = "| a | b |\n|---|---|\n| 1 | 2 |"
        out = _table_md_to_json(md)
        assert out["headers"] == ["a", "b"]
        assert out["rows"] == [["1", "2"]]

    def test_empty(self):
        assert _table_md_to_json("") == {}


# ---------------------------------------------------------------------------
# End-to-end PostProcessor.run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_postprocessor_run_writes_files(tmp_path: Path):
    # In-memory stand-in that mimics the subset of SupabaseStorage that
    # PostProcessor calls. Avoids any network or filesystem dependency.
    written: dict[str, object] = {}

    class _MemStorage:
        async def write_page_markdown(self, user_id, project_id, document_id, page_num, content):
            written[f"{user_id}/{project_id}/{document_id}/page_{page_num:03d}.md"] = content

        async def write_full_document(self, user_id, project_id, document_id, content):
            written[f"{user_id}/{project_id}/{document_id}/full_document.md"] = content

        async def write_table_json(
            self, user_id, project_id, document_id, page_num, table_index, data
        ):
            written[
                f"{user_id}/{project_id}/{document_id}/tables/"
                f"page_{page_num:03d}_table_{table_index:02d}.json"
            ] = data

        async def write_table_markdown(
            self, user_id, project_id, document_id, page_num, table_index, content
        ):
            written[
                f"{user_id}/{project_id}/{document_id}/tables/"
                f"page_{page_num:03d}_table_{table_index:02d}.md"
            ] = content

        async def write_entities_json(self, user_id, project_id, document_id, data):
            written[f"{user_id}/{project_id}/{document_id}/entities.json"] = data

    storage: SupabaseStorage = _MemStorage()  # type: ignore[assignment]
    pp = PostProcessor(storage, user_id="u1", project_id="p1", document_id="d1")

    baselines = [baseline(0, "page 0 baseline"), baseline(1, "page 1 baseline")]
    page_results = {
        0: page_state(0, markdown="# Title\nfirst page",
                      tables=[{"markdown": "| a |\n|---|\n| 1 |",
                               "json_data": {"headers": ["a"], "rows": [["1"]]}}]),
        1: page_state(1, markdown="second page"),
    }

    result = await pp.run(
        doc_name="test_doc",
        filename="test.pdf",
        baselines=baselines,
        page_results=page_results,
    )

    assert result.pages_written == 2
    assert result.tables_written >= 1
    assert "u1/p1/d1/page_000.md" in written
    assert "u1/p1/d1/page_001.md" in written
    assert "u1/p1/d1/full_document.md" in written
    assert "u1/p1/d1/entities.json" in written
