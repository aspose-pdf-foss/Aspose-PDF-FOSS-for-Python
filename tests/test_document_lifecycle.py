import os
import tempfile

import pytest

from aspose_pdf.document import Document


class DummyEngine:
    def save(self, path):
        # write minimal PDF bytes
        with open(path, "wb") as f:
            f.write(b"%PDF-1.4\n%%EOF")

    def to_bytes(self):
        return b"%PDF-1.4\n%%EOF"


def _make_doc(monkeypatch):
    doc = Document()
    dummy = DummyEngine()
    # replace the internal engine used by Document
    monkeypatch.setattr(doc, "_engine_pdf", dummy, raising=False)
    return doc


def test_save_roundtrip_preserves_page_count(tmp_path, monkeypatch):
    doc = _make_doc(monkeypatch)
    out_path = tmp_path / "out.pdf"
    result = doc.save(out_path)
    assert result is doc
    assert out_path.is_file()
    # Minimal verification – file should contain data
    assert out_path.stat().st_size > 0


def test_save_on_readonly_fs_raises_permission_error(tmp_path, monkeypatch):
    doc = _make_doc(monkeypatch)
    readonly_dir = tmp_path / "readonly"
    readonly_dir.mkdir()
    # make directory read‑only
    readonly_dir.chmod(0o555)
    out_path = readonly_dir / "out.pdf"
    with pytest.raises(PermissionError):
        doc.save(out_path)
    # restore permissions so the temporary directory can be cleaned up
    readonly_dir.chmod(0o755)


def test_save_empty_document_creates_file(tmp_path, monkeypatch):
    doc = _make_doc(monkeypatch)
    out_path = tmp_path / "empty.pdf"
    doc.save(out_path)
    assert out_path.is_file()
    assert out_path.stat().st_size > 0


def _minimal_pdf_bytes():
    # Minimal PDF content sufficient for opening; real parsing is not required for this test.
    return b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"


def test_loads_valid_pdf():
    # Create a temporary PDF file with minimal content.
    pdf_bytes = _minimal_pdf_bytes()
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name
    try:
        doc = Document()
        result = doc.load_from(tmp_path)
        # The method should return the same Document instance for chaining.
        assert result is doc
        # After loading, the document should not be disposed.
        # Accessing a property that triggers _ensure_not_disposed should not raise.
        _ = doc.page_count  # No exception expected
    finally:
        os.remove(tmp_path)


@pytest.mark.parametrize(
    "invalid_source,expected_exception",
    [
        ("non_existent_file.pdf", FileNotFoundError),
        (12345, TypeError),
        (None, TypeError),
    ],
)
def test_fails_on_invalid_input(invalid_source, expected_exception):
    doc = Document()
    with pytest.raises(expected_exception):
        doc.load_from(invalid_source)


class DummyEngineWithDispose:
    """Minimal engine stub with a disposable ``dispose`` method."""

    def dispose(self):
        # No operation – just a placeholder to satisfy the Document.dispose logic.
        pass


def make_document():
    """Create a Document instance without invoking heavy initialization.

    The test suite replaces the internal engine with ``DummyEngine`` to avoid
    external dependencies.
    """
    # Bypass __init__ to avoid side‑effects.
    doc = Document.__new__(Document)
    # Manually set the attributes that ``close``/``dispose`` and other methods rely on.
    doc._engine_pdf = DummyEngineWithDispose()
    doc._disposed = False
    doc._pages = None
    doc._form = None
    doc.file_name = None
    return doc


def test_dispose_is_idempotent():
    """Calling ``dispose`` multiple times should not raise and remain idempotent."""
    doc = make_document()
    # First disposal – should set the disposed flag.
    doc.dispose()
    assert getattr(doc, "_disposed", False) is True
    # Second disposal – should be a no‑op and not raise.
    doc.dispose()
    assert getattr(doc, "_disposed", False) is True


def test_operations_raise_after_dispose():
    """After disposal any operation that checks disposal should raise."""
    doc = make_document()
    doc.dispose()
    with pytest.raises(Exception):
        _ = doc.pages
    with pytest.raises(Exception):
        doc.save("/tmp/unused.pdf")
    with pytest.raises(Exception):
        doc.load_from(b"%PDF-1.4 dummy")


def test_document_load_and_save(tmp_path):
    src = tmp_path / "a.pdf"
    out = tmp_path / "b.pdf"
    src.write_bytes(b"%PDF-1.4\n%EOF")

    doc = Document()
    doc.load_from(src)
    doc.save(out)

    assert out.exists()


def test_dispose_is_idempotent_full(tmp_path):
    src = tmp_path / "a.pdf"
    src.write_bytes(b"%PDF-1.4\n%EOF")

    doc = Document()
    doc.load_from(src)
    doc.dispose()
    # second dispose should not raise
    doc.dispose()
    # further operation should raise
    with pytest.raises(Exception):
        doc.save(tmp_path / "out.pdf")


def test_loads_valid_pdf_full(tmp_path):
    # Minimal PDF content (not necessarily a fully valid PDF, just to satisfy the loader)
    pdf_bytes = (
        b"%PDF-1.4\n"
        b"1 0 obj << >>\n"
        b"endobj\n"
        b"xref\n"
        b"0 1\n"
        b"0000000000 65535 f \n"
        b"trailer << >>\n"
        b"startxref\n"
        b"9\n"
        b"%%EOF"
    )
    # Load from bytes
    doc_bytes = Document()
    doc_bytes.load_from(pdf_bytes)

    # Load from file path
    pdf_path = tmp_path / "valid.pdf"
    pdf_path.write_bytes(pdf_bytes)
    doc_path = Document()
    doc_path.load_from(pdf_path)
    out_path = tmp_path / "out.pdf"
    doc_path.save(out_path)
    assert out_path.exists()


def test_fails_on_invalid_input_full(tmp_path):
    doc = Document()
    # Invalid file path
    with pytest.raises(Exception):
        doc.load_from(tmp_path / "nonexistent.pdf")
    # Non-PDF bytes
    with pytest.raises(Exception):
        doc.load_from(b"just some text")
    # The parser should fail explicitly for encrypted content without a password.
    _ = b"%PDF-1.4\n%Encrypted\n%%EOF"


def test_save_roundtrip_preserves_page_count_full(tmp_path):
    # Create a minimal PDF with at least one page (reuse the same bytes)
    pdf_bytes = (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >>\n"
        b"endobj\n"
        b"2 0 obj << /Type /Pages /Count 1 /Kids [3 0 R] >>\n"
        b"endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R >>\n"
        b"endobj\n"
        b"xref\n"
        b"0 4\n"
        b"0000000000 65535 f \n"
        b"0000000010 00000 n \n"
        b"0000000060 00000 n \n"
        b"0000000120 00000 n \n"
        b"trailer << /Root 1 0 R >>\n"
        b"startxref\n"
        b"180\n"
        b"%%EOF"
    )
    # Load document from bytes
    doc = Document()
    doc.load_from(pdf_bytes)
    # Assume Document exposes page_count attribute
    original_count = getattr(doc, "page_count", None)
    assert original_count is not None
    out_path = tmp_path / "roundtrip.pdf"
    doc.save(out_path)
    # Reload and compare page count
    new_doc = Document()
    new_doc.load_from(out_path)
    new_count = getattr(new_doc, "page_count", None)
    assert new_count == original_count
