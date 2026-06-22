"""Tests for Feature 10: Streaming/incremental page processing for large PDFs.

Covers:
* Document.open_streaming()  — lazy-load mode via mmap
* Document.iter_pages()      — lightweight page iterator (regular + lazy mode)
* Document.iter_page_content_streams() — generator of per-page bytes
* SimplePdf.from_file_lazy() — internal lazy factory
* SimplePdf.get_page_content() — on-demand content decoding
* Page.content               — unified accessor for both modes
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aspose_pdf.document import Document
from aspose_pdf.exceptions import PdfSecurityException
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.pages import Page


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_pdf_bytes(page_count: int = 1) -> bytes:
    """Return a minimal but parseable PDF with *page_count* pages."""
    objects: list[bytes] = []
    page_ids = list(range(3, 3 + page_count))  # object numbers for pages
    kids_str = " ".join(f"{i} 0 R" for i in page_ids)

    objects.append(b"1 0 obj << /Type /Catalog /Pages 2 0 R >>\nendobj")
    objects.append(
        f"2 0 obj << /Type /Pages /Count {page_count} /Kids [{kids_str}] >>\nendobj".encode()
    )
    for pid in page_ids:
        objects.append(
            f"{pid} 0 obj << /Type /Page /Parent 2 0 R "
            f"/MediaBox [0 0 612 792] >>\nendobj".encode()
        )

    # Build xref + trailer manually
    body = b"%PDF-1.4\n"
    offsets: list[int] = []
    for obj in objects:
        offsets.append(len(body))
        body += obj + b"\n"

    xref_offset = len(body)
    n_objs = len(objects) + 1  # +1 for object 0
    xref = f"xref\n0 {n_objs}\n0000000000 65535 f \n".encode()
    for off in offsets:
        xref += f"{off:010d} 00000 n \n".encode()

    trailer = (
        f"trailer << /Root 1 0 R /Size {n_objs} >>\nstartxref\n{xref_offset}\n%%%%EOF"
    ).encode()

    return body + xref + trailer


def _write_pdf(tmp_path: Path, page_count: int = 1) -> Path:
    p = tmp_path / "test.pdf"
    p.write_bytes(_minimal_pdf_bytes(page_count))
    return p


def _encrypted_pdf_path(tmp_path: Path, password: str) -> Path:
    """Single-page encrypted PDF written via :meth:`SimplePdf.encrypt`."""
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 612, 792)]
    pdf.page_contents = [b"BT /F1 12 Tf 100 700 Td (Secret) Tj ET"]
    pdf.encrypt(password)
    path = tmp_path / "encrypted_streaming.pdf"
    path.write_bytes(pdf.to_bytes())
    return path


# ---------------------------------------------------------------------------
# open_streaming — basic functionality
# ---------------------------------------------------------------------------


def test_open_streaming_returns_document(tmp_path):
    path = _write_pdf(tmp_path)
    doc = Document.open_streaming(path)
    try:
        assert isinstance(doc, Document)
        assert doc.page_count == 1
    finally:
        doc.close()


def test_open_streaming_as_context_manager(tmp_path):
    path = _write_pdf(tmp_path)
    with Document.open_streaming(path) as doc:
        assert doc.page_count == 1


def test_open_streaming_page_contents_not_preloaded(tmp_path):
    """In lazy mode page_contents must be empty — content is loaded on demand."""
    path = _write_pdf(tmp_path)
    with Document.open_streaming(path) as doc:
        assert doc._engine_pdf._lazy is True
        assert doc._engine_pdf.page_contents == []


def test_open_streaming_file_name_is_set(tmp_path):
    path = _write_pdf(tmp_path)
    with Document.open_streaming(path) as doc:
        assert doc.file_name == str(path)


def test_open_streaming_nonexistent_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        Document.open_streaming(tmp_path / "does_not_exist.pdf")


def test_open_streaming_multipage(tmp_path):
    path = _write_pdf(tmp_path, page_count=3)
    with Document.open_streaming(path) as doc:
        assert doc.page_count == 3


# ---------------------------------------------------------------------------
# open_streaming — page content accessible on demand
# ---------------------------------------------------------------------------


def test_open_streaming_page_content_accessible_on_demand(tmp_path):
    path = _write_pdf(tmp_path)
    with Document.open_streaming(path) as doc:
        for page in doc.iter_pages():
            assert isinstance(page.content, bytes)


def test_page_content_property_lazy_mode(tmp_path):
    """Page.content must delegate to get_page_content() in lazy mode."""
    path = _write_pdf(tmp_path, page_count=2)
    with Document.open_streaming(path) as doc:
        for i in range(doc.page_count):
            page = next(p for p in doc.iter_pages() if p.index == i)
            assert page.content is not None
            assert isinstance(page.content, bytes)


def test_open_streaming_metadata_accessible(tmp_path):
    path = _write_pdf(tmp_path)
    with Document.open_streaming(path) as doc:
        assert isinstance(doc.info, dict)


# ---------------------------------------------------------------------------
# iter_pages — works in both regular and streaming mode
# ---------------------------------------------------------------------------


def test_iter_pages_count_matches_regular_mode():
    doc = Document()
    doc.load_from(_minimal_pdf_bytes())
    pages = list(doc.iter_pages())
    assert len(pages) == 1
    doc.close()


def test_iter_pages_streaming_count_matches(tmp_path):
    path = _write_pdf(tmp_path, page_count=3)
    with Document.open_streaming(path) as doc:
        pages = list(doc.iter_pages())
    assert len(pages) == 3


def test_iter_pages_yields_page_instances_regular():
    doc = Document()
    doc.load_from(_minimal_pdf_bytes())
    for page in doc.iter_pages():
        assert isinstance(page, Page)
    doc.close()


def test_iter_pages_yields_page_instances_streaming(tmp_path):
    path = _write_pdf(tmp_path)
    with Document.open_streaming(path) as doc:
        for page in doc.iter_pages():
            assert isinstance(page, Page)


def test_iter_pages_page_content_type_regular():
    doc = Document()
    doc.load_from(_minimal_pdf_bytes())
    for page in doc.iter_pages():
        assert isinstance(page.content, bytes)
    doc.close()


def test_iter_pages_page_content_type_streaming(tmp_path):
    path = _write_pdf(tmp_path, page_count=2)
    with Document.open_streaming(path) as doc:
        for page in doc.iter_pages():
            assert isinstance(page.content, bytes)


def test_iter_pages_indices_are_sequential(tmp_path):
    path = _write_pdf(tmp_path, page_count=4)
    with Document.open_streaming(path) as doc:
        indices = [p.index for p in doc.iter_pages()]
    assert indices == [0, 1, 2, 3]


# ---------------------------------------------------------------------------
# SimplePdf.get_page_content — per-page access
# ---------------------------------------------------------------------------


def test_get_page_content_returns_bytes_for_each_page(tmp_path):
    path = _write_pdf(tmp_path, page_count=3)
    with Document.open_streaming(path) as doc:
        eng = doc._engine_pdf
        for i in range(doc.page_count):
            content = eng.get_page_content(i)
            assert isinstance(content, bytes)


def test_get_page_content_out_of_range_returns_empty(tmp_path):
    path = _write_pdf(tmp_path)
    with Document.open_streaming(path) as doc:
        eng = doc._engine_pdf
        assert eng.get_page_content(999) == b""


def test_get_page_content_regular_mode():
    """get_page_content() must also work in normal (non-lazy) mode."""
    from aspose_pdf.engine.simple_pdf import SimplePdf

    pdf = SimplePdf.from_bytes(_minimal_pdf_bytes())
    for i in range(len(pdf.pages)):
        content = pdf.get_page_content(i)
        assert isinstance(content, bytes)


# ---------------------------------------------------------------------------
# iter_page_content_streams
# ---------------------------------------------------------------------------


def test_iter_page_content_streams_regular():
    doc = Document()
    doc.load_from(_minimal_pdf_bytes())
    streams = list(doc.iter_page_content_streams())
    assert len(streams) == 1
    assert all(isinstance(s, bytes) for s in streams)
    doc.close()


def test_iter_page_content_streams_lazy(tmp_path):
    path = _write_pdf(tmp_path, page_count=3)
    with Document.open_streaming(path) as doc:
        streams = list(doc.iter_page_content_streams())
    assert len(streams) == 3
    assert all(isinstance(s, bytes) for s in streams)


def test_iter_page_content_streams_one_at_a_time(tmp_path):
    """Each call to next() on the generator should work independently."""
    path = _write_pdf(tmp_path, page_count=2)
    with Document.open_streaming(path) as doc:
        gen = doc.iter_page_content_streams()
        s0 = next(gen)
        s1 = next(gen)
        assert isinstance(s0, bytes)
        assert isinstance(s1, bytes)
        with pytest.raises(StopIteration):
            next(gen)


# ---------------------------------------------------------------------------
# Consistency: streaming vs. regular mode
# ---------------------------------------------------------------------------


def test_streaming_and_regular_same_page_count(tmp_path):
    path = _write_pdf(tmp_path, page_count=2)
    with Document.open_streaming(path) as lazy_doc:
        lazy_count = lazy_doc.page_count

    regular_doc = Document()
    regular_doc.load_from(path)
    assert regular_doc.page_count == lazy_count
    regular_doc.close()


def test_streaming_and_regular_same_iter_pages_count(tmp_path):
    path = _write_pdf(tmp_path, page_count=2)
    with Document.open_streaming(path) as lazy_doc:
        lazy_pages = list(lazy_doc.iter_pages())

    regular_doc = Document()
    regular_doc.load_from(path)
    regular_pages = list(regular_doc.iter_pages())
    assert len(lazy_pages) == len(regular_pages)
    regular_doc.close()


# ---------------------------------------------------------------------------
# Lazy streaming + encryption (AUDIT issue #2)
# ---------------------------------------------------------------------------


def test_from_file_lazy_encrypted_without_password_raises(tmp_path):
    path = _encrypted_pdf_path(tmp_path, "lazy-secret")
    with pytest.raises(PdfSecurityException, match="Password required"):
        SimplePdf.from_file_lazy(path)


def test_open_streaming_encrypted_without_password_raises(tmp_path):
    path = _encrypted_pdf_path(tmp_path, "doc-open-sec")
    with pytest.raises(PdfSecurityException, match="Password required"):
        Document.open_streaming(path)


def test_from_file_lazy_encrypted_with_password_opens(tmp_path):
    path = _encrypted_pdf_path(tmp_path, "allowed")
    pdf = SimplePdf.from_file_lazy(path, password="allowed")
    try:
        assert pdf.encrypted is True
        assert pdf._lazy is True
        assert pdf.page_contents == []
    finally:
        pdf.dispose()


# ---------------------------------------------------------------------------
# from_file_lazy — internal API
# ---------------------------------------------------------------------------


def test_from_file_lazy_page_count(tmp_path):
    path = _write_pdf(tmp_path, page_count=2)
    pdf = SimplePdf.from_file_lazy(path)
    try:
        assert len(pdf.pages) == 2
        assert pdf._lazy is True
        assert pdf.page_contents == []
    finally:
        pdf.dispose()


def test_from_file_lazy_nonexistent_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        SimplePdf.from_file_lazy(tmp_path / "missing.pdf")
