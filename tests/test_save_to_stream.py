"""Tests for Feature 1: Document.save() to BinaryIO / BytesIO streams."""

from __future__ import annotations

import io

import pytest

from aspose_pdf.document import Document


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_pdf_bytes() -> bytes:
    """Return a minimal but parseable PDF."""
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


def _make_doc(source: bytes | None = None) -> Document:
    doc = Document()
    doc.load_from(source if source is not None else _minimal_pdf_bytes())
    return doc


# ---------------------------------------------------------------------------
# Core behaviour: saving to a stream
# ---------------------------------------------------------------------------


def test_save_to_bytesio_writes_data():
    """save() must write non-empty bytes into a BytesIO buffer."""
    buf = io.BytesIO()
    _make_doc().save(buf)
    assert buf.tell() > 0


def test_save_to_bytesio_produces_valid_pdf_header():
    """Bytes written to stream must start with the %PDF- magic."""
    buf = io.BytesIO()
    _make_doc().save(buf)
    buf.seek(0)
    assert buf.read(5) == b"%PDF-"


def test_save_to_stream_matches_save_to_file(tmp_path):
    """Bytes written to a stream must be identical to those written to a file.

    Both destinations are produced from the *same* Document instance so that
    any once-generated values (e.g. the /ID trailer entry) are identical.
    """
    src = _minimal_pdf_bytes()
    out_file = tmp_path / "out.pdf"

    doc = _make_doc(src)
    doc.save(out_file)

    buf = io.BytesIO()
    doc.save(buf, overwrite=True)

    buf.seek(0)
    assert buf.read() == out_file.read_bytes()


def test_save_to_stream_can_be_reloaded():
    """A document saved to BytesIO can be loaded back and has the same page count."""
    original = _make_doc()
    original_pages = original.page_count

    buf = io.BytesIO()
    original.save(buf)

    buf.seek(0)
    reloaded = Document()
    reloaded.load_from(buf.read())
    assert reloaded.page_count == original_pages


def test_save_to_stream_leaves_stream_position_after_written_data():
    """After save() the stream position must be at the end of the written data."""
    buf = io.BytesIO()
    _make_doc().save(buf)
    pos = buf.tell()
    buf.seek(0, 2)  # seek to end
    assert buf.tell() == pos


def test_save_to_stream_overwrite_flag_is_ignored():
    """overwrite= has no meaning for streams — save() must not raise."""
    buf = io.BytesIO()
    _make_doc().save(buf, overwrite=False)
    buf.seek(0)
    assert buf.read(5) == b"%PDF-"


def test_save_to_binary_file_object(tmp_path):
    """save() must work with an open binary file handle (not only BytesIO)."""
    out = tmp_path / "via_handle.pdf"
    with out.open("wb") as fh:
        _make_doc().save(fh)
    assert out.stat().st_size > 0
    assert out.read_bytes()[:5] == b"%PDF-"


# ---------------------------------------------------------------------------
# Type / error guards
# ---------------------------------------------------------------------------


def test_save_to_stream_after_dispose_raises():
    """save() to stream on a disposed document must raise."""
    doc = _make_doc()
    doc.dispose()
    buf = io.BytesIO()
    with pytest.raises(Exception):
        doc.save(buf)


def test_save_to_stream_raises_on_io_error():
    """save() must propagate IOError when the stream is closed."""
    buf = io.BytesIO()
    buf.close()
    with pytest.raises((ValueError, OSError)):
        _make_doc().save(buf)


# ---------------------------------------------------------------------------
# Backwards-compatibility: path-based save still works
# ---------------------------------------------------------------------------


def test_save_to_path_still_works(tmp_path):
    """Existing path-based save() must not be broken by the new overload."""
    out = tmp_path / "compat.pdf"
    _make_doc().save(out)
    assert out.exists()
    assert out.stat().st_size > 0


def test_save_to_str_path_still_works(tmp_path):
    out = tmp_path / "str_path.pdf"
    _make_doc().save(str(out))
    assert out.exists()
    assert out.stat().st_size > 0


def test_save_to_path_overwrite_false_raises_if_exists(tmp_path):
    out = tmp_path / "exists.pdf"
    out.write_bytes(b"%PDF-1.4\n%%EOF")
    with pytest.raises(FileExistsError):
        _make_doc().save(out, overwrite=False)


def test_save_to_path_overwrite_true_replaces_file(tmp_path):
    out = tmp_path / "replace.pdf"
    out.write_bytes(b"old content")
    _make_doc().save(out, overwrite=True)
    assert out.read_bytes()[:5] == b"%PDF-"
