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
