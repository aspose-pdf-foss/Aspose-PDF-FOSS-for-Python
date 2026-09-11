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
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.exceptions import PdfSecurityException
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


# ---------------------------------------------------------------------------
# Editing in streaming mode
#
# Every test above reads. Deleting a page raised ``IndexError`` from an
# internal list for every index, in every document opened this way, because
# ``page_contents`` is empty until something materialises it -- and deletion
# never did.
# ---------------------------------------------------------------------------


def _numbered_pdf(tmp_path: Path, count: int = 6) -> Path:
    """A document whose pages say which page they are."""
    doc = Document()
    for index in range(count):
        doc.pages.add().add_text(f"PAGE-{index}", x=20, y=700)
    path = tmp_path / "numbered.pdf"
    doc.save(str(path))
    return path


def _text_after(path: Path, deletions, opener) -> list[str]:
    doc = opener(str(path))
    for index in deletions:
        doc.pages.delete(index)
    out = path.with_name(f"out_{id(doc)}.pdf")
    doc.save(str(out))
    return [page.extract_text().strip() for page in Document(str(out)).pages]


def test_deleting_a_page_from_a_streamed_document_works(tmp_path):
    path = _numbered_pdf(tmp_path, 3)
    with Document.open_streaming(path) as doc:
        doc.pages.delete(1)
        assert doc.page_count == 2


@pytest.mark.parametrize(
    "deletions", [(0,), (5,), (0, 0), (2, 2), (1, 3), (4, 0), (0, 1, 2)]
)
def test_streaming_deletion_matches_the_eager_path(tmp_path, deletions):
    """The eager path is the reference: the same deletions, the same pages."""
    path = _numbered_pdf(tmp_path)
    assert _text_after(path, deletions, Document.open_streaming) == _text_after(
        path, deletions, Document
    )


def test_deleting_a_page_does_not_decode_the_whole_document(tmp_path):
    """The point of streaming mode. Materialising instead would decode every
    page of the file the caller opened this way to avoid loading."""
    path = _numbered_pdf(tmp_path)
    with Document.open_streaming(path) as doc:
        doc.pages.delete(2)
        doc.pages.delete(0)
        assert doc._engine_pdf._lazy is True
        assert doc._engine_pdf.page_contents == []


def test_text_extraction_works_in_streaming_mode(tmp_path):
    """It returned "" for every page, from any document opened this way."""
    path = _numbered_pdf(tmp_path, 3)
    with Document.open_streaming(path) as streamed, Document(str(path)) as eager:
        assert [p.extract_text().strip() for p in streamed.pages] == [
            "PAGE-0",
            "PAGE-1",
            "PAGE-2",
        ]
        assert streamed.extract_text() == eager.extract_text()


def test_the_page_text_cursor_walks_a_streamed_document(tmp_path):
    path = _numbered_pdf(tmp_path, 3)
    with Document.open_streaming(path) as doc:
        engine = doc._engine_pdf
        assert engine.has_next_page_text() is True
        assert [engine.get_next_page_text().strip() for _ in range(3)] == [
            "PAGE-0",
            "PAGE-1",
            "PAGE-2",
        ]
        assert engine.has_next_page_text() is False


def test_extracting_text_does_not_decode_the_whole_document(tmp_path):
    path = _numbered_pdf(tmp_path, 4)
    with Document.open_streaming(path) as doc:
        doc.pages[1].extract_text()
        assert doc._engine_pdf._lazy is True
        assert doc._engine_pdf.page_contents == []


def test_content_still_follows_the_pages_after_a_streamed_deletion(tmp_path):
    path = _numbered_pdf(tmp_path, 4)
    with Document.open_streaming(path) as doc:
        doc.pages.delete(1)
        assert [page.extract_text().strip() for page in doc.pages] == [
            "PAGE-0",
            "PAGE-2",
            "PAGE-3",
        ]


def test_clearing_the_pages_of_a_streamed_document_works(tmp_path):
    path = _numbered_pdf(tmp_path, 3)
    with Document.open_streaming(path) as doc:
        doc.pages.clear()
        assert doc.page_count == 0


# ---------------------------------------------------------------------------
# PDF/A and repair in streaming mode
#
# Three more paths read the lazy cache as though it were the document. The
# conversion rewrote no CMYK on a streamed document and left it under the sRGB
# OutputIntent it had just installed; the validator skipped the same content
# scan and passed that file; and repair() padded the cache with empty pages.
# ---------------------------------------------------------------------------

_CMYK_CONTENT = b"0.1 0.9 0.8 0 k 20 20 100 60 re f\n0 0 0 1 K 4 w 20 100 m 160 100 l S\n"


def _cmyk_pdf(tmp_path: Path) -> Path:
    """A page painted with DeviceCMYK operators, and no OutputIntent yet."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 150] /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(_CMYK_CONTENT), _CMYK_CONTENT),
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref_at,
    )
    path = tmp_path / "cmyk.pdf"
    path.write_bytes(bytes(out))
    return path


def _colour_operators(content: bytes) -> set[bytes]:
    import re

    return set(re.findall(rb"(?<![A-Za-z])(k|K|rg|RG)(?![A-Za-z])", content))


def test_converting_a_streamed_document_to_pdfa_rewrites_its_cmyk(tmp_path):
    path = _cmyk_pdf(tmp_path)
    out = tmp_path / "pdfa.pdf"
    with Document.open_streaming(path) as doc:
        doc.convert_to_pdfa("1b")
        doc.save(str(out))

    converted = Document(str(out))
    assert _colour_operators(converted.pages[0].content) == {b"rg", b"RG"}
    assert converted.validate_pdfa("1b").is_valid


def test_a_streamed_conversion_writes_what_an_eager_one_writes(tmp_path):
    path = _cmyk_pdf(tmp_path)
    contents = []
    for opener in (Document, Document.open_streaming):
        doc = opener(str(path))
        doc.convert_to_pdfa("1b")
        out = tmp_path / f"converted_{len(contents)}.pdf"
        doc.save(str(out))
        contents.append(Document(str(out)).pages[0].content)
    assert contents[0] == contents[1]


def test_validating_a_streamed_document_scans_its_content(tmp_path):
    """The false pass: CMYK content under an RGB OutputIntent."""
    path = _cmyk_pdf(tmp_path)
    # A real sRGB OutputIntent, from the eager conversion, then CMYK put back
    # into the content underneath it.
    doc = Document(str(path))
    doc.convert_to_pdfa("1b")
    doc._engine_pdf._set_page_content(0, _CMYK_CONTENT)
    bad = tmp_path / "cmyk_under_srgb.pdf"
    doc.save(str(bad))

    eager = Document(str(bad)).validate_pdfa("1b")
    with Document.open_streaming(bad) as streamed:
        lazy = streamed.validate_pdfa("1b")
    assert not eager.is_valid
    assert not lazy.is_valid
    assert lazy.errors == eager.errors


def test_repair_leaves_a_streamed_document_readable_and_lazy(tmp_path):
    path = _numbered_pdf(tmp_path, 3)
    with Document.open_streaming(path) as doc:
        doc.repair()
        assert [page.extract_text().strip() for page in doc.pages] == [
            "PAGE-0",
            "PAGE-1",
            "PAGE-2",
        ]
        assert doc._engine_pdf._lazy is True
        assert doc._engine_pdf.page_contents == []
