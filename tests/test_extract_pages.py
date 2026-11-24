"""Tests for PdfFileEditor.extract_pages method."""

from pathlib import Path

import pytest

from aspose_pdf import Document
from aspose_pdf.facades import PdfFileEditor


@pytest.fixture
def four_page_pdf(tmp_path: Path) -> Path:
    """Create a 4-page PDF for testing."""
    doc = Document()
    for i in range(4):
        page = doc.pages.add()
        page.add_text(f"Page {i+1} content", x=40, y=700)

    dst = tmp_path / "4page.pdf"
    doc.save(str(dst))
    return dst


def test_extract_pages_basic(four_page_pdf: Path, tmp_path: Path) -> None:
    """Test basic extraction of pages [1, 3]."""
    src = four_page_pdf
    dst = tmp_path / "extracted.pdf"

    editor = PdfFileEditor()
    ok = editor.extract_pages(str(src), str(dst), [1, 3])

    assert ok
    assert editor.last_exception is None

    with Document(str(dst)) as d:
        assert len(d.pages) == 2


def test_extract_pages_with_duplicates(four_page_pdf: Path, tmp_path: Path) -> None:
    """Test extraction with duplicate pages [2, 2, 1]."""
    src = four_page_pdf
    dst = tmp_path / "duplicates.pdf"

    editor = PdfFileEditor()
    ok = editor.extract_pages(str(src), str(dst), [2, 2, 1])

    assert ok
    assert editor.last_exception is None

    with Document(str(dst)) as d:
        assert len(d.pages) == 3


def test_extract_pages_page_number_zero(four_page_pdf: Path, tmp_path: Path) -> None:
    """Test that page number 0 raises ValueError."""
    src = four_page_pdf
    dst = tmp_path / "out.pdf"

    editor = PdfFileEditor()
    with pytest.raises(ValueError, match="Page number must be positive"):
        editor.extract_pages(str(src), str(dst), [0])


def test_extract_pages_negative_page(four_page_pdf: Path, tmp_path: Path) -> None:
    """Test that negative page number raises ValueError."""
    src = four_page_pdf
    dst = tmp_path / "out.pdf"

    editor = PdfFileEditor()
    with pytest.raises(ValueError, match="Page number must be positive"):
        editor.extract_pages(str(src), str(dst), [-1])


def test_extract_pages_exceeds_count(four_page_pdf: Path, tmp_path: Path) -> None:
    """Test that page number > page count raises ValueError."""
    src = four_page_pdf
    dst = tmp_path / "out.pdf"

    editor = PdfFileEditor()
    with pytest.raises(ValueError, match="exceeds source document page count"):
        editor.extract_pages(str(src), str(dst), [5])


def test_extract_pages_empty_list(four_page_pdf: Path, tmp_path: Path) -> None:
    """Test extraction with empty list raises ValueError."""
    src = four_page_pdf
    dst = tmp_path / "out.pdf"

    editor = PdfFileEditor()
    with pytest.raises(ValueError, match="pages list must not be empty"):
        editor.extract_pages(str(src), str(dst), [])


def test_extract_pages_order_preserved(four_page_pdf: Path, tmp_path: Path) -> None:
    """Test that page order is preserved as specified."""
    src = four_page_pdf
    dst = tmp_path / "order.pdf"

    editor = PdfFileEditor()
    pages = [4, 3, 2, 1]
    ok = editor.extract_pages(str(src), str(dst), pages)

    assert ok

    with Document(str(dst)) as d:
        assert len(d.pages) == 4
        for i, page in enumerate(d.pages):
            expected_page_num = pages[i]
            text = page.extract_text()
            assert f"Page {expected_page_num} content" in text


def test_extract_pages_disposed_editor(four_page_pdf: Path, tmp_path: Path) -> None:
    """Test that disposed editor raises exception."""
    src = four_page_pdf
    dst = tmp_path / "out.pdf"

    editor = PdfFileEditor()
    editor.dispose()

    with pytest.raises(Exception, match="disposed"):
        editor.extract_pages(str(src), str(dst), [1])


def test_extract_pages_invalid_source(tmp_path: Path) -> None:
    """Test that invalid source returns False and sets last_exception."""
    dst = tmp_path / "out.pdf"

    editor = PdfFileEditor()
    ok = editor.extract_pages(str(tmp_path / "nonexistent.pdf"), str(dst), [1])

    assert not ok
    assert editor.last_exception is not None


def test_extract_pages_single_page(four_page_pdf: Path, tmp_path: Path) -> None:
    """Test extraction of a single page."""
    src = four_page_pdf
    dst = tmp_path / "single.pdf"

    editor = PdfFileEditor()
    ok = editor.extract_pages(str(src), str(dst), [2])

    assert ok

    with Document(str(dst)) as d:
        assert len(d.pages) == 1
