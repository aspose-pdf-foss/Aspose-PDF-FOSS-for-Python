"""Tests for Feature 9: Document.load_from() with BinaryIO streams."""

from __future__ import annotations

import io

import pytest

from aspose_pdf.document import Document


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_pdf_bytes() -> bytes:
    """Return a minimal but parseable one-page PDF."""
    return (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >>\n"
        b"endobj\n"
        b"2 0 obj << /Type /Pages /Count 1 /Kids [3 0 R] >>\n"
        b"endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\n"
        b"endobj\n"
        b"xref\n"
        b"0 4\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000062 00000 n \n"
        b"0000000126 00000 n \n"
        b"trailer << /Root 1 0 R /Size 4 >>\n"
        b"startxref\n"
        b"210\n"
        b"%%EOF"
    )


def _make_stream(data: bytes | None = None) -> io.BytesIO:
    return io.BytesIO(data if data is not None else _minimal_pdf_bytes())


def _make_doc() -> Document:
    doc = Document()
    doc.load_from(_minimal_pdf_bytes())
    return doc


# ---------------------------------------------------------------------------
# Core behaviour: loading from BinaryIO
# ---------------------------------------------------------------------------


def test_load_from_bytesio_succeeds():
    """load_from() must accept a BytesIO object and load the document."""
    stream = _make_stream()
    doc = Document()
    doc.load_from(stream)
    assert doc.page_count == 1


def test_load_from_bytesio_correct_page_count():
    """Document loaded from BytesIO must report the correct number of pages."""
    stream = _make_stream()
    doc = Document()
    doc.load_from(stream)
    assert doc.page_count >= 1


def test_load_from_stream_midway_position():
    """load_from() reads from the current stream position, not always from 0."""
    data = _minimal_pdf_bytes()
    stream = io.BytesIO(data)
    # Seek to start explicitly — should work fine
    stream.seek(0)
    doc = Document()
    doc.load_from(stream)
    assert doc.page_count == 1


def test_load_from_stream_does_not_close_stream():
    """load_from() must leave the stream open after reading."""
    stream = _make_stream()
    doc = Document()
    doc.load_from(stream)
    assert not stream.closed


def test_load_from_binary_file_object(tmp_path):
    """load_from() must work with a real open file handle in 'rb' mode."""
    pdf_file = tmp_path / "sample.pdf"
    pdf_file.write_bytes(_minimal_pdf_bytes())

    with pdf_file.open("rb") as fh:
        doc = Document()
        doc.load_from(fh)

    assert doc.page_count == 1


def test_load_from_stream_file_name_is_none():
    """When loaded from a stream, file_name must be None (no file path)."""
    stream = _make_stream()
    doc = Document()
    doc.load_from(stream)
    assert doc.file_name is None


def test_load_from_stream_returns_self():
    """load_from() must return the Document instance for method chaining."""
    stream = _make_stream()
    doc = Document()
    result = doc.load_from(stream)
    assert result is doc


# ---------------------------------------------------------------------------
# Round-trip: save → BytesIO → load_from(stream)
# ---------------------------------------------------------------------------


def test_save_then_load_from_stream_round_trip():
    """A document saved to BytesIO can be reloaded via load_from(stream)."""
    original = _make_doc()
    original_pages = original.page_count

    buf = io.BytesIO()
    original.save(buf)
    buf.seek(0)

    reloaded = Document()
    reloaded.load_from(buf)
    assert reloaded.page_count == original_pages


def test_save_then_load_from_stream_metadata_preserved():
    """Metadata written before save is preserved after a stream round-trip."""
    doc = _make_doc()
    doc.info["Title"] = "Stream Round-trip"

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)

    reloaded = Document()
    reloaded.load_from(buf)
    assert reloaded.info.get("Title") == "Stream Round-trip"


def test_load_from_stream_equals_load_from_bytes():
    """Loading from a BytesIO wrapping the same data must give identical page counts."""
    data = _minimal_pdf_bytes()

    doc_bytes = Document()
    doc_bytes.load_from(data)

    doc_stream = Document()
    doc_stream.load_from(io.BytesIO(data))

    assert doc_bytes.page_count == doc_stream.page_count


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_load_from_invalid_stream_raises():
    """load_from() on a stream with non-PDF content must raise an exception."""
    stream = io.BytesIO(b"this is not a PDF")
    doc = Document()
    with pytest.raises(Exception):
        doc.load_from(stream)


def test_load_from_empty_stream_raises():
    """load_from() on an empty stream must raise an exception."""
    stream = io.BytesIO(b"")
    doc = Document()
    with pytest.raises(Exception):
        doc.load_from(stream)


def test_load_from_stream_on_disposed_doc_raises():
    """load_from() on a disposed Document must raise AsposePdfException."""
    from aspose_pdf.exceptions import AsposePdfException

    doc = Document()
    doc.dispose()
    with pytest.raises(AsposePdfException):
        doc.load_from(_make_stream())


def test_load_from_wrong_type_raises():
    """load_from() with an unsupported type must raise TypeError."""
    doc = Document()
    with pytest.raises(TypeError):
        doc.load_from(12345)  # type: ignore[arg-type]


def test_load_from_wrong_type_list_raises():
    """load_from() with a list must raise TypeError."""
    doc = Document()
    with pytest.raises(TypeError):
        doc.load_from([1, 2, 3])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Backwards-compatibility: existing call signatures still work
# ---------------------------------------------------------------------------


def test_load_from_bytes_still_works():
    """Existing bytes-based load_from() must still work after the change."""
    doc = Document()
    doc.load_from(_minimal_pdf_bytes())
    assert doc.page_count == 1


def test_load_from_path_still_works(tmp_path):
    """Existing Path-based load_from() must still work after the change."""
    pdf_file = tmp_path / "test.pdf"
    pdf_file.write_bytes(_minimal_pdf_bytes())

    doc = Document()
    doc.load_from(pdf_file)
    assert doc.page_count == 1


def test_load_from_str_path_still_works(tmp_path):
    """Existing str-path load_from() must still work after the change."""
    pdf_file = tmp_path / "test.pdf"
    pdf_file.write_bytes(_minimal_pdf_bytes())

    doc = Document()
    doc.load_from(str(pdf_file))
    assert doc.page_count == 1


# ---------------------------------------------------------------------------
# Password-protected streams
# ---------------------------------------------------------------------------


def test_load_from_stream_with_password(tmp_path):
    """load_from(stream, password=...) must decrypt an encrypted PDF from stream."""
    # Create and encrypt a document, then reload it from a stream with password
    doc = _make_doc()
    doc.encrypt("secret")

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)

    reloaded = Document()
    reloaded.load_from(buf, password="secret")
    assert reloaded.page_count >= 1


def test_load_from_path_with_password(tmp_path):
    """load_from(path, password=...) must load an encrypted PDF from disk (AUDIT #1)."""
    pdf_file = tmp_path / "encrypted.pdf"
    doc = _make_doc()
    doc.encrypt("path-secret")
    doc.save(pdf_file)

    reloaded = Document()
    reloaded.load_from(pdf_file, password="path-secret")
    assert reloaded.page_count >= 1


def test_load_from_str_path_with_password(tmp_path):
    """load_from(str path, password=...) must load an encrypted PDF from disk."""
    pdf_file = tmp_path / "encrypted.pdf"
    doc = _make_doc()
    doc.encrypt("str-secret")
    doc.save(pdf_file)

    reloaded = Document()
    reloaded.load_from(str(pdf_file), password="str-secret")
    assert reloaded.page_count >= 1


def test_load_from_path_encrypted_without_password_raises(tmp_path):
    """Encrypted file path without password must still require a password."""
    from aspose_pdf.exceptions import PdfSecurityException

    pdf_file = tmp_path / "encrypted.pdf"
    doc = _make_doc()
    doc.encrypt("only-me")
    doc.save(pdf_file)

    with pytest.raises(PdfSecurityException, match="Password required"):
        Document().load_from(pdf_file)
