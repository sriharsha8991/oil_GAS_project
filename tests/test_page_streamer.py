"""Tests for the v3 streaming page rasterizer + PageDeque."""

from __future__ import annotations

import asyncio

import pytest

from app.models.schemas import PageBaseline
from app.pipeline.page_streamer import PageDeque, rasterize_pages


@pytest.mark.asyncio
async def test_deque_push_and_pop():
    from app.models.schemas import PageImage

    deque = PageDeque()
    page = PageImage(page_num=0, png_bytes=b"x")
    await deque.push(page)
    out = await deque.pop()
    assert out is not None and out.page_num == 0


@pytest.mark.asyncio
async def test_deque_close_returns_none_for_each_consumer():
    deque = PageDeque()
    deque.close(num_consumers=3)
    a = await deque.pop()
    b = await deque.pop()
    c = await deque.pop()
    assert a is None and b is None and c is None


@pytest.mark.asyncio
async def test_rasterize_pushes_one_image_per_page(sample_pdf_path):
    deque = PageDeque()
    baselines = [
        PageBaseline(page_num=i, text=f"p{i}", char_count=2)
        for i in range(3)
    ]

    pushed = await rasterize_pages(sample_pdf_path, baselines, deque, dpi=100)
    assert pushed == 3
    assert deque.qsize() == 3

    # Drain and verify
    pages = [await deque.pop() for _ in range(3)]
    assert [p.page_num for p in pages] == [0, 1, 2]
    for p in pages:
        assert p.png_bytes.startswith(b"\x89PNG")
        assert p.dpi == 100
        assert p.width > 0 and p.height > 0


@pytest.mark.asyncio
async def test_rasterize_carries_baseline_metadata(sample_pdf_path):
    deque = PageDeque()
    baselines = [
        PageBaseline(page_num=0, text="t0", is_scanned=False),
        PageBaseline(page_num=1, text="t1", is_scanned=False),
        PageBaseline(page_num=2, text="", is_scanned=True),
    ]
    await rasterize_pages(sample_pdf_path, baselines, deque, dpi=72)

    pages = [await deque.pop() for _ in range(3)]
    assert pages[0].baseline_text == "t0"
    assert pages[2].is_scanned is True


@pytest.mark.asyncio
async def test_streaming_consumer_starts_before_producer_finishes(sample_pdf_path):
    """Worker should be able to pop the first page before the producer ends."""
    deque = PageDeque()
    baselines = [PageBaseline(page_num=i, text="x") for i in range(3)]

    consumed: list[int] = []

    async def consumer():
        for _ in range(3):
            p = await deque.pop()
            if p is None:
                break
            consumed.append(p.page_num)

    async def producer():
        await rasterize_pages(sample_pdf_path, baselines, deque, dpi=72)

    await asyncio.gather(producer(), consumer())

    assert consumed == [0, 1, 2]
