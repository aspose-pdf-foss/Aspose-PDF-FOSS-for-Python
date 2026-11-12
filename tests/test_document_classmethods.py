"""Tests for Document class methods: from_bytes, from_file, from_stream."""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document
from aspose_pdf.exceptions import PdfParseException


@pytest.fixture
def pdf_bytes():
    """Load a real PDF file as bytes for testing."""
    with open("tests/fixtures_4pages.pdf", "rb") as f:
        return f.read()


class TestDocumentFromClassMethods:
    """Test Document.from_bytes, Document.from_file, and Document.from_stream."""

    def test_from_bytes_returns_document(self, pdf_bytes):
        """from_bytes creates a Document from PDF bytes."""
        doc = Document.from_bytes(pdf_bytes)
        assert isinstance(doc, Document)
        assert len(doc) == 4
        doc.dispose()

    def test_from_bytes_with_bytearray(self, pdf_bytes):
        """from_bytes works with bytearray input."""
        doc = Document.from_bytes(bytearray(pdf_bytes))
        assert isinstance(doc, Document)
        assert len(doc) == 4
        doc.dispose()

    def test_from_bytes_with_bytesio(self, pdf_bytes):
        """from_bytes works with BytesIO stream."""
        stream = io.BytesIO(pdf_bytes)
        doc = Document.from_bytes(stream)
        assert isinstance(doc, Document)
        assert len(doc) == 4
        doc.dispose()

    def test_from_bytes_with_password(self):
        """from_bytes works with encrypted PDFs."""
        # Load the AES-128 encrypted fixture (password is "owner")
        with open("tests/fixtures_encrypted_aes_128.pdf", "rb") as f:
            encrypted_pdf = f.read()

        doc = Document.from_bytes(encrypted_pdf, password="owner")
        assert isinstance(doc, Document)
        assert len(doc) == 1
        doc.dispose()

    def test_from_bytes_invalid_pdf_raises(self):
        """from_bytes raises an exception for invalid PDF data."""
        with pytest.raises(PdfParseException):
            Document.from_bytes(b"not a PDF at all")

    def test_from_bytes_empty_bytes_raises(self):
        """from_bytes raises an exception for empty bytes."""
        with pytest.raises(PdfParseException):
            Document.from_bytes(b"")

    def test_from_file_returns_document(self):
        """from_file creates a Document from a file path."""
        doc = Document.from_file("tests/fixtures_4pages.pdf")
        assert isinstance(doc, Document)
        assert len(doc) == 4
        doc.dispose()

    def test_from_file_with_pathlib(self):
        """from_file works with pathlib.Path."""
        from pathlib import Path

        doc = Document.from_file(Path("tests/fixtures_4pages.pdf"))
        assert isinstance(doc, Document)
        assert len(doc) == 4
        doc.dispose()

    def test_from_file_with_password(self):
        """from_file works with encrypted PDFs."""
        doc = Document.from_file("tests/fixtures_encrypted_aes_128.pdf", password="owner")
        assert isinstance(doc, Document)
        assert len(doc) == 1
        doc.dispose()

    def test_from_file_nonexistent_raises(self):
        """from_file raises FileNotFoundError for missing file."""
        with pytest.raises(FileNotFoundError):
            Document.from_file("nonexistent_file.pdf")

    def test_from_stream_returns_document(self, pdf_bytes):
        """from_stream creates a Document from a binary stream."""
        stream = io.BytesIO(pdf_bytes)
        doc = Document.from_stream(stream)
        assert isinstance(doc, Document)
        assert len(doc) == 4
        doc.dispose()

    def test_from_stream_does_not_close_stream(self, pdf_bytes):
        """from_stream does not close the stream."""
        stream = io.BytesIO(pdf_bytes)
        doc = Document.from_stream(stream)
        # Stream should still be open after loading
        assert not stream.closed
        doc.dispose()

    def test_from_stream_with_open_file(self, pdf_bytes, tmp_path):
        """from_stream works with an open file handle."""
        test_file = tmp_path / "test.pdf"
        test_file.write_bytes(pdf_bytes)

        with open(test_file, "rb") as f:
            doc = Document.from_stream(f)
            assert isinstance(doc, Document)
            assert len(doc) == 4
            doc.dispose()

    def test_context_manager_with_from_bytes(self, pdf_bytes):
        """from_bytes can be used as a context manager."""
        with Document.from_bytes(pdf_bytes) as doc:
            assert isinstance(doc, Document)
            assert len(doc) == 4
        # Document should be disposed after context exits
        assert doc.closed

    def test_context_manager_with_from_file(self):
        """from_file can be used as a context manager."""
        with Document.from_file("tests/fixtures_4pages.pdf") as doc:
            assert isinstance(doc, Document)
            assert len(doc) == 4
        # Document should be disposed after context exits
        assert doc.closed

    def test_context_manager_with_from_stream(self, pdf_bytes):
        """from_stream can be used as a context manager."""
        with Document.from_bytes(pdf_bytes) as doc:
            assert isinstance(doc, Document)
            assert len(doc) == 4
        # Document should be disposed after context exits
        assert doc.closed

    def test_from_bytes_with_limits(self, pdf_bytes):
        """from_bytes accepts and uses PdfLoadLimits."""
        from aspose_pdf import PdfLoadLimits

        limits = PdfLoadLimits(max_pages=10, max_object_bytes=1000000)
        doc = Document.from_bytes(pdf_bytes, limits=limits)
        assert isinstance(doc, Document)
        assert doc.load_limits.max_pages == 10
        assert len(doc) == 4
        doc.dispose()

    def test_from_file_with_limits(self, pdf_bytes, tmp_path):
        """from_file accepts and uses PdfLoadLimits."""
        from aspose_pdf import PdfLoadLimits

        test_file = tmp_path / "test.pdf"
        test_file.write_bytes(pdf_bytes)

        limits = PdfLoadLimits(max_pages=10, max_object_bytes=1000000)
        doc = Document.from_file(test_file, limits=limits)
        assert isinstance(doc, Document)
        assert doc.load_limits.max_pages == 10
        assert len(doc) == 4
        doc.dispose()

    def test_from_stream_with_limits(self, pdf_bytes):
        """from_stream accepts and uses PdfLoadLimits."""
        from aspose_pdf import PdfLoadLimits

        limits = PdfLoadLimits(max_pages=10, max_object_bytes=1000000)
        doc = Document.from_stream(io.BytesIO(pdf_bytes), limits=limits)
        assert isinstance(doc, Document)
        assert doc.load_limits.max_pages == 10
        assert len(doc) == 4
        doc.dispose()

    def test_from_bytes_equivalent_to_load_from(self, pdf_bytes):
        """from_bytes produces the same result as load_from."""
        doc1 = Document.from_bytes(pdf_bytes)
        doc2 = Document()
        doc2.load_from(pdf_bytes)

        assert len(doc1) == len(doc2)
        assert doc1.page_count == doc2.page_count
        doc1.dispose()
        doc2.dispose()

    def test_from_file_equivalent_to_load_from(self, pdf_bytes, tmp_path):
        """from_file produces the same result as load_from."""
        test_file = tmp_path / "test.pdf"
        test_file.write_bytes(pdf_bytes)

        doc1 = Document.from_file(test_file)
        doc2 = Document()
        doc2.load_from(test_file)

        assert len(doc1) == len(doc2)
        assert doc1.page_count == doc2.page_count
        doc1.dispose()
        doc2.dispose()

    def test_from_stream_equivalent_to_load_from(self, pdf_bytes):
        """from_stream produces the same result as load_from."""
        doc1 = Document.from_stream(io.BytesIO(pdf_bytes))
        doc2 = Document()
        doc2.load_from(io.BytesIO(pdf_bytes))

        assert len(doc1) == len(doc2)
        assert doc1.page_count == doc2.page_count
        doc1.dispose()
        doc2.dispose()

    def test_from_bytes_multiple_times(self, pdf_bytes):
        """Multiple from_bytes calls create independent documents."""
        doc1 = Document.from_bytes(pdf_bytes)
        doc2 = Document.from_bytes(pdf_bytes)
        doc3 = Document.from_bytes(pdf_bytes)

        assert len(doc1) == len(doc2) == len(doc3) == 4
        # Each should be independent
        doc1.dispose()
        doc2.dispose()
        doc3.dispose()

    def test_from_file_multiple_times(self, pdf_bytes, tmp_path):
        """Multiple from_file calls create independent documents."""
        test_file = tmp_path / "test.pdf"
        test_file.write_bytes(pdf_bytes)

        doc1 = Document.from_file(test_file)
        doc2 = Document.from_file(test_file)
        doc3 = Document.from_file(test_file)

        assert len(doc1) == len(doc2) == len(doc3) == 4
        # Each should be independent
        doc1.dispose()
        doc2.dispose()
        doc3.dispose()

    def test_from_stream_multiple_times(self, pdf_bytes):
        """Multiple from_stream calls create independent documents."""
        doc1 = Document.from_stream(io.BytesIO(pdf_bytes))
        doc2 = Document.from_stream(io.BytesIO(pdf_bytes))
        doc3 = Document.from_stream(io.BytesIO(pdf_bytes))

        assert len(doc1) == len(doc2) == len(doc3) == 4
        # Each should be independent
        doc1.dispose()
        doc2.dispose()
        doc3.dispose()


# ---------------------------------------------------------------------------
# to_bytes() tests
# ---------------------------------------------------------------------------


class TestDocumentToBytes:
    """Test Document.to_bytes()."""

    def test_to_bytes_returns_bytes(self, pdf_bytes):
        """to_bytes() returns bytes."""
        doc = Document.from_bytes(pdf_bytes)
        result = doc.to_bytes()
        assert isinstance(result, bytes)
        doc.dispose()

    def test_to_bytes_roundtrip(self, pdf_bytes):
        """to_bytes() / from_bytes() roundtrip preserves content."""
        doc1 = Document.from_bytes(pdf_bytes)
        pdf_out = doc1.to_bytes()
        doc2 = Document.from_bytes(pdf_out)

        assert len(doc1) == len(doc2)
        assert doc1.page_count == doc2.page_count
        doc1.dispose()
        doc2.dispose()

    def test_to_bytes_empty_document(self):
        """to_bytes() on an empty document returns valid PDF bytes."""
        with Document() as doc:
            result = doc.to_bytes()
            assert isinstance(result, bytes)
            # Minimal PDF should have a header
            assert b"%PDF" in result

    def test_to_bytes_programmatic_document(self):
        """to_bytes() on a programmatically created document."""
        with Document() as doc:
            doc.pages.add()
            # Simple test: bytes should be valid PDF
            result = doc.to_bytes()
            assert isinstance(result, bytes)
            assert b"%PDF" in result

    def test_to_bytes_incremental(self, pdf_bytes, tmp_path):
        """to_bytes(incremental=True) works for documents loaded from file."""
        # Write the bytes to a file first
        test_file = tmp_path / "test.pdf"
        test_file.write_bytes(pdf_bytes)

        with Document.from_file(test_file) as doc:
            # Add a page to ensure there are changes
            doc.pages.add()
            result = doc.to_bytes(incremental=True)
            assert isinstance(result, bytes)
            assert b"%PDF" in result

    def test_to_bytes_full_rewrite(self, pdf_bytes):
        """to_bytes(incremental=False) forces a full rewrite."""
        doc = Document.from_bytes(pdf_bytes)
        result = doc.to_bytes(incremental=False)
        assert isinstance(result, bytes)
        assert b"%PDF" in result
        doc.dispose()

    def test_to_bytes_equivalent_to_save_to_stream(self, pdf_bytes):
        """to_bytes() produces same bytes as save_to_stream."""
        import io

        doc1 = Document.from_bytes(pdf_bytes)
        bytes_direct = doc1.to_bytes()

        doc2 = Document.from_bytes(pdf_bytes)
        buffer = io.BytesIO()
        doc2.save_to_stream(buffer)
        bytes_stream = buffer.getvalue()

        assert bytes_direct == bytes_stream
        doc1.dispose()
        doc2.dispose()

    def test_to_bytes_multiple_calls(self, pdf_bytes):
        """Multiple to_bytes() calls on the same document work."""
        doc = Document.from_bytes(pdf_bytes)
        result1 = doc.to_bytes()
        result2 = doc.to_bytes()
        result3 = doc.to_bytes()

        assert result1 == result2 == result3
        doc.dispose()

    def test_to_bytes_after_dispose_raises(self, pdf_bytes):
        """to_bytes() raises after dispose."""
        doc = Document.from_bytes(pdf_bytes)
        doc.dispose()

        with pytest.raises(Exception):
            doc.to_bytes()

    def test_to_bytes_empty_document_succeeds(self):
        """to_bytes() on a newly created empty document returns valid PDF bytes."""
        doc = Document()
        # A new Document has an engine for creating new content
        result = doc.to_bytes()
        assert isinstance(result, bytes)
        assert b"%PDF" in result
