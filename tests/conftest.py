"""Shared test fixtures."""

from pathlib import Path

import pytest

# Root of the project
PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def sample_pdf_path(tmp_path: Path) -> Path:
    """Create a minimal valid PDF for testing.

    Uses PyMuPDF to generate a 3-page PDF with known text content
    so tests don't depend on external fixtures.
    """
    import fitz  # PyMuPDF

    doc = fitz.open()

    # Page 0: simple text
    page0 = doc.new_page(width=612, height=792)
    page0.insert_text((72, 72), "Page 1: Well Completion Report", fontsize=14)
    page0.insert_text((72, 100), "Well Name: ABC-1\nOperator: XYZ Energy")

    # Page 1: text with a small table-like block
    page1 = doc.new_page(width=612, height=792)
    page1.insert_text((72, 72), "Page 2: Formation Evaluation", fontsize=14)
    page1.insert_text(
        (72, 100),
        "Depth (ft) | Porosity (%) | Permeability (mD)\n"
        "3000       | 12.5         | 45\n"
        "3100       | 14.2         | 62\n"
        "3200       | 11.8         | 38",
    )

    # Page 2: sparse text (simulates a mostly-image page)
    page2 = doc.new_page(width=612, height=792)
    page2.insert_text((72, 72), "Fig 3.1")

    pdf_path = tmp_path / "test_well_report.pdf"
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


@pytest.fixture
def empty_pdf_path(tmp_path: Path) -> Path:
    """Create a 1-page PDF with no text (simulates scanned page)."""
    import fitz

    doc = fitz.open()
    doc.new_page(width=612, height=792)  # blank page
    pdf_path = tmp_path / "empty.pdf"
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path
