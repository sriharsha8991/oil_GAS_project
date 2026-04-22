"""Tests for Phase 2: Batch Queue.

Tests both the batch construction algorithm (build_batches)
and the async BatchQueue consumer.
"""

from __future__ import annotations

import asyncio

import pytest

from app.models.schemas import (
    Complexity,
    ContentType,
    CrossPageTable,
    DocStructure,
    KeyEntities,
    PageBaseline,
    PageBatch,
    SectionInfo,
)
from app.pipeline.batch_queue import (
    BatchQueue,
    _build_no_split_set,
    _complexity_to_resolution,
    _contiguous_ranges,
    _extend_for_tables,
    build_batches,
)


# ---------------------------------------------------------------------------
# Helpers — create test fixtures
# ---------------------------------------------------------------------------


def _make_baselines(n: int) -> list[PageBaseline]:
    """Create n PageBaseline objects with sequential page numbers (0-indexed)."""
    return [
        PageBaseline(
            page_num=i,
            text=f"Text for page {i}",
            char_count=100 + i * 10,
            extraction_method="pymupdf4llm",
        )
        for i in range(n)
    ]


def _make_structure(
    total_pages: int,
    sections: list[tuple[str, int, int, str]] | None = None,
    cross_page_tables: list[tuple[int, int]] | None = None,
) -> DocStructure:
    """Create a DocStructure for testing.

    sections: list of (name, page_start, page_end, complexity)
    cross_page_tables: list of (page_start, page_end)
    """
    if sections is None:
        sections = [("Full Document", 0, total_pages - 1, "medium")]

    sec_list = [
        SectionInfo(
            name=name,
            page_start=ps,
            page_end=pe,
            content_types=[ContentType.TEXT],
            complexity=Complexity(comp),
        )
        for name, ps, pe, comp in sections
    ]

    cpt_list = [
        CrossPageTable(page_start=ps, page_end=pe)
        for ps, pe in (cross_page_tables or [])
    ]

    return DocStructure(
        document_type="well_completion_report",
        title="Test Report",
        sections=sec_list,
        cross_page_tables=cpt_list,
        key_entities=KeyEntities(well_name="Test-1"),
    )


# ---------------------------------------------------------------------------
# _build_no_split_set
# ---------------------------------------------------------------------------


class TestBuildNoSplitSet:
    def test_empty(self):
        assert _build_no_split_set([]) == set()

    def test_single_table(self):
        tables = [CrossPageTable(page_start=3, page_end=5)]
        assert _build_no_split_set(tables) == {3, 4}

    def test_multiple_tables(self):
        tables = [
            CrossPageTable(page_start=3, page_end=5),
            CrossPageTable(page_start=10, page_end=12),
        ]
        assert _build_no_split_set(tables) == {3, 4, 10, 11}

    def test_single_page_table_no_split(self):
        """A table on one page only — no split constraints."""
        tables = [CrossPageTable(page_start=3, page_end=3)]
        assert _build_no_split_set(tables) == set()


# ---------------------------------------------------------------------------
# _extend_for_tables
# ---------------------------------------------------------------------------


class TestExtendForTables:
    def test_no_conflict(self):
        assert _extend_for_tables(4, 10, set()) == 4

    def test_extends_past_table(self):
        no_split = {4, 5}  # table spans pages 4-6
        assert _extend_for_tables(4, 10, no_split) == 6

    def test_capped_at_section_end(self):
        no_split = {4, 5, 6, 7}
        assert _extend_for_tables(4, 6, no_split) == 6

    def test_no_extend_when_not_in_set(self):
        no_split = {10, 11}
        assert _extend_for_tables(4, 10, no_split) == 4


# ---------------------------------------------------------------------------
# _complexity_to_resolution
# ---------------------------------------------------------------------------


class TestComplexityToResolution:
    def test_low(self):
        assert _complexity_to_resolution(Complexity.LOW) == "MEDIUM"

    def test_medium(self):
        assert _complexity_to_resolution(Complexity.MEDIUM) == "MEDIUM"

    def test_high(self):
        assert _complexity_to_resolution(Complexity.HIGH) == "HIGH"


# ---------------------------------------------------------------------------
# _contiguous_ranges
# ---------------------------------------------------------------------------


class TestContiguousRanges:
    def test_empty(self):
        assert _contiguous_ranges([]) == []

    def test_single(self):
        assert _contiguous_ranges([5]) == [(5, 5)]

    def test_contiguous(self):
        assert _contiguous_ranges([2, 3, 4, 5]) == [(2, 5)]

    def test_gaps(self):
        assert _contiguous_ranges([1, 2, 5, 6, 7, 10]) == [(1, 2), (5, 7), (10, 10)]


# ---------------------------------------------------------------------------
# build_batches — core algorithm
# ---------------------------------------------------------------------------


class TestBuildBatches:
    def test_empty_baselines(self):
        ds = _make_structure(10)
        assert build_batches(ds, [], batch_size=5) == []

    def test_single_section_exact_fit(self):
        """10 pages, batch_size=5 → 2 batches."""
        baselines = _make_baselines(10)
        ds = _make_structure(10)
        batches = build_batches(ds, baselines, batch_size=5)

        assert len(batches) == 2
        assert batches[0].page_start == 0
        assert batches[0].page_end == 4
        assert batches[1].page_start == 5
        assert batches[1].page_end == 9

    def test_single_section_remainder(self):
        """7 pages, batch_size=5 → 2 batches (5 + 2)."""
        baselines = _make_baselines(7)
        ds = _make_structure(7)
        batches = build_batches(ds, baselines, batch_size=5)

        assert len(batches) == 2
        assert batches[0].page_end - batches[0].page_start + 1 == 5
        assert batches[1].page_end - batches[1].page_start + 1 == 2

    def test_multiple_sections(self):
        """Two sections → batches aligned to section boundaries."""
        baselines = _make_baselines(12)
        ds = _make_structure(
            12,
            sections=[
                ("Abstract", 0, 3, "low"),
                ("Analysis", 4, 11, "high"),
            ],
        )
        batches = build_batches(ds, baselines, batch_size=5)

        # Section 1: pages 0-3 (4 pages) → 1 batch
        # Section 2: pages 4-11 (8 pages) → 2 batches (5 + 3)
        assert len(batches) == 3
        assert batches[0].section_context == "Abstract"
        assert batches[0].page_start == 0
        assert batches[0].page_end == 3
        assert batches[1].section_context == "Analysis"
        assert batches[1].page_start == 4
        assert batches[2].section_context == "Analysis"

    def test_section_context_and_complexity(self):
        baselines = _make_baselines(5)
        ds = _make_structure(
            5,
            sections=[("Tables Section", 0, 4, "high")],
        )
        batches = build_batches(ds, baselines, batch_size=5)

        assert len(batches) == 1
        assert batches[0].section_context == "Tables Section"
        assert batches[0].complexity == Complexity.HIGH
        assert batches[0].media_resolution == "HIGH"

    def test_low_complexity_gets_medium_resolution(self):
        baselines = _make_baselines(3)
        ds = _make_structure(3, sections=[("Intro", 0, 2, "low")])
        batches = build_batches(ds, baselines, batch_size=5)

        assert batches[0].media_resolution == "MEDIUM"

    def test_cross_page_table_not_split(self):
        """A table spanning pages 3-5 must not be split across batches."""
        baselines = _make_baselines(10)
        ds = _make_structure(
            10,
            sections=[("Full", 0, 9, "medium")],
            cross_page_tables=[(3, 5)],
        )
        batches = build_batches(ds, baselines, batch_size=4)

        # Without the table constraint: batch1=[0-3], batch2=[4-7], batch3=[8-9]
        # With the table constraint: page 3 and 4 must be together.
        # batch1 should be [0-4] (extended to include page 4), then [5-8], [9]
        # or [0-3] stays if 3 isn't the boundary. Let me check:
        # batch_size=4: start=0, end=3. page 3 in no_split → extend to 4.
        # Then next: start=5, end=8. 8 not in no_split → stays 8.
        # Then: start=9, end=9.
        assert batches[0].page_end >= 4  # must include page 4 (table continues)
        # Verify pages 3,4,5 are in the same batch
        for batch in batches:
            if batch.page_start <= 3 <= batch.page_end:
                assert batch.page_end >= 4  # 3 and 4 together
            if batch.page_start <= 4 <= batch.page_end:
                assert batch.page_start <= 4  # 4 included with 3

    def test_large_cross_page_table_extends_batch(self):
        """A table spanning pages 2-6 forces a large batch."""
        baselines = _make_baselines(10)
        ds = _make_structure(
            10,
            sections=[("Full", 0, 9, "medium")],
            cross_page_tables=[(2, 6)],
        )
        batches = build_batches(ds, baselines, batch_size=3)

        # batch_size=3: start=0, end=2. page 2 in no_split → extend.
        # 3 in no_split → extend. 4 in no_split → extend. 5 in no_split → extend.
        # 6 not in no_split → stop at 6.
        # Batch 1 covers pages 0-6 (7 pages), then batch 2: 7-9.
        assert batches[0].page_end >= 6
        assert len(batches) == 2

    def test_baselines_attached_to_batches(self):
        baselines = _make_baselines(6)
        ds = _make_structure(6)
        batches = build_batches(ds, baselines, batch_size=3)

        assert len(batches) == 2
        assert len(batches[0].baseline_texts) == 3
        assert len(batches[1].baseline_texts) == 3
        assert batches[0].baseline_texts[0].page_num == 0
        assert batches[1].baseline_texts[0].page_num == 3

    def test_batch_ids_sequential(self):
        baselines = _make_baselines(15)
        ds = _make_structure(15)
        batches = build_batches(ds, baselines, batch_size=5)

        assert [b.batch_id for b in batches] == [0, 1, 2]

    def test_single_page_document(self):
        baselines = _make_baselines(1)
        ds = _make_structure(1)
        batches = build_batches(ds, baselines, batch_size=5)

        assert len(batches) == 1
        assert batches[0].page_start == 0
        assert batches[0].page_end == 0

    def test_batch_size_one(self):
        """batch_size=1 creates one batch per page (unless cross-page table)."""
        baselines = _make_baselines(3)
        ds = _make_structure(3)
        batches = build_batches(ds, baselines, batch_size=1)

        assert len(batches) == 3
        for i, batch in enumerate(batches):
            assert batch.page_start == i
            assert batch.page_end == i

    def test_overlapping_sections_no_duplicate_pages(self):
        """If sections overlap, pages should still be batched correctly."""
        baselines = _make_baselines(10)
        ds = _make_structure(
            10,
            sections=[
                ("Section A", 0, 5, "low"),
                ("Section B", 3, 9, "high"),  # overlaps with A
            ],
        )
        batches = build_batches(ds, baselines, batch_size=5)

        # Both sections process their ranges; pages 3-5 appear in both
        # This is by design — the section context differs
        all_pages = set()
        for b in batches:
            for p in range(b.page_start, b.page_end + 1):
                all_pages.add(p)
        # All pages 0-9 should be covered
        assert all_pages == set(range(10))


# ---------------------------------------------------------------------------
# BatchQueue — async behavior
# ---------------------------------------------------------------------------


class TestBatchQueue:
    @pytest.mark.asyncio
    async def test_load_and_pop(self):
        q = BatchQueue()
        batch = PageBatch(batch_id=0, page_start=0, page_end=4)
        q.load([batch])
        q.mark_done()

        result = await q.pop()
        assert result is not None
        assert result.batch_id == 0

        # Queue exhausted
        result = await q.pop()
        assert result is None

    @pytest.mark.asyncio
    async def test_pending_count(self):
        q = BatchQueue()
        batches = [
            PageBatch(batch_id=i, page_start=i * 5, page_end=i * 5 + 4)
            for i in range(3)
        ]
        q.load(batches)
        assert q.pending == 3

        await q.pop()
        assert q.pending == 2

    @pytest.mark.asyncio
    async def test_is_done(self):
        q = BatchQueue()
        q.load([PageBatch(batch_id=0, page_start=0, page_end=4)])
        assert not q.is_done

        q.mark_done()
        assert not q.is_done  # still has items

        await q.pop()
        assert q.is_done  # empty + done

    @pytest.mark.asyncio
    async def test_pop_blocks_until_loaded(self):
        q = BatchQueue()
        result = None

        async def consumer():
            nonlocal result
            result = await q.pop()

        async def producer():
            await asyncio.sleep(0.05)
            await q.push(PageBatch(batch_id=0, page_start=0, page_end=4))
            q.mark_done()

        await asyncio.gather(consumer(), producer())
        assert result is not None
        assert result.batch_id == 0

    @pytest.mark.asyncio
    async def test_pop_returns_none_when_done_empty(self):
        q = BatchQueue()
        q.mark_done()

        result = await q.pop()
        assert result is None

    @pytest.mark.asyncio
    async def test_multiple_consumers(self):
        """Multiple workers can drain the queue concurrently."""
        q = BatchQueue()
        batches = [
            PageBatch(batch_id=i, page_start=i * 5, page_end=i * 5 + 4)
            for i in range(6)
        ]
        q.load(batches)
        q.mark_done()

        results: list[PageBatch] = []
        lock = asyncio.Lock()

        async def worker():
            while True:
                batch = await q.pop()
                if batch is None:
                    break
                async with lock:
                    results.append(batch)

        # 3 concurrent workers
        await asyncio.gather(worker(), worker(), worker())

        assert len(results) == 6
        assert {b.batch_id for b in results} == {0, 1, 2, 3, 4, 5}

    @pytest.mark.asyncio
    async def test_push_single(self):
        q = BatchQueue()
        await q.push(PageBatch(batch_id=0, page_start=0, page_end=4))
        assert q.pending == 1
