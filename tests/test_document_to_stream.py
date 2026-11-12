"""Tests for Document.to_stream() method."""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document


@pytest.fixture
def pdf_bytes():
    """Load a real PDF file as bytes for testing."""
    with open("tests/fixtures_4pages.pdf", "rb") as f:
        return f.read()


class TestDocumentToStream:
    """Test Document.to_stream() method."""

    def test_to_stream_writes_to_bytesio(self, pdf_bytes):
        """to_stream writes PDF content to a BytesIO buffer."""
        doc = Document.from_bytes(pdf_bytes)
        buffer = io.BytesIO()
        doc.to_stream(buffer)
        buffer.seek(0)
        written_bytes = buffer.read()
        # PDFs have unique IDs that change on save, so we can't compare exact bytes
        # Instead, verify the output is valid PDF and has the expected content
        assert written_bytes.startswith(b"%PDF-")
        # Reload and verify page count
        doc2 = Document.from_bytes(written_bytes)
        assert doc2.page_count == doc.page_count
        doc2.dispose()
        doc.dispose()

    def test_to_stream_does_not_close_stream(self, pdf_bytes):
        """to_stream does not close the stream."""
        doc = Document.from_bytes(pdf_bytes)
        stream = io.BytesIO()
        doc.to_stream(stream)
        # Stream should still be open after writing
        assert not stream.closed
        doc.dispose()

    def test_to_stream_multiple_writes(self, pdf_bytes, tmp_path):
        """Multiple to_stream calls can write to the same stream."""
        doc = Document.from_bytes(pdf_bytes)
        stream = io.BytesIO()
        doc.to_stream(stream)
        first_pos = stream.tell()
        doc.to_stream(stream)
        second_pos = stream.tell()
        # Second write should append
        assert second_pos > first_pos
        doc.dispose()

    def test_to_stream_with_open_file(self, pdf_bytes, tmp_path):
        """to_stream works with an open file handle."""
        test_file = tmp_path / "output.pdf"
        doc = Document.from_bytes(pdf_bytes)
        # Open file and write to it without using context manager
        f = open(test_file, "wb")
        try:
            doc.to_stream(f)
            # File should still be open after to_stream
            assert not f.closed
        finally:
            f.close()
        # Now the file should exist and be valid
        assert test_file.exists()
        assert test_file.stat().st_size > 0
        # Verify it's valid PDF by reloading
        doc2 = Document.from_file(test_file)
        assert doc2.page_count == doc.page_count
        doc2.dispose()
        doc.dispose()

    def test_to_stream_roundtrip_with_from_stream(self, pdf_bytes):
        """to_stream and from_stream roundtrip produces equivalent documents."""
        # Load original
        doc1 = Document.from_bytes(pdf_bytes)
        original_page_count = doc1.page_count
        doc1.dispose()

        # Round-trip through stream
        buffer = io.BytesIO()
        doc2 = Document.from_bytes(pdf_bytes)
        doc2.to_stream(buffer)
        doc2.dispose()

        buffer.seek(0)
        doc3 = Document.from_stream(buffer)
        assert doc3.page_count == original_page_count
        doc3.dispose()

    def test_to_stream_empty_document(self, tmp_path):
        """to_stream works on an empty document."""
        doc = Document()
        buffer = io.BytesIO()
        doc.to_stream(buffer)
        buffer.seek(0)
        # The written content should start with PDF header
        assert buffer.read(5) == b"%PDF-"
        doc.dispose()

    def test_to_stream_after_modification(self, pdf_bytes, tmp_path):
        """to_stream reflects modifications made to the document."""
        doc = Document.from_bytes(pdf_bytes)
        original_page_count = doc.page_count

        # Add a page
        doc.pages.add()

        buffer = io.BytesIO()
        doc.to_stream(buffer)
        buffer.seek(0)

        # Reload and check
        doc2 = Document.from_stream(buffer)
        assert doc2.page_count == original_page_count + 1
        doc2.dispose()
        doc.dispose()

    def test_to_stream_incremental(self, pdf_bytes, tmp_path):
        """to_stream supports incremental updates."""
        # First, create a document and save it
        test_file = tmp_path / "source.pdf"
        with Document() as doc:
            doc.pages.add()
            doc.save(test_file)

        # Load it and make an incremental change
        doc = Document.from_file(test_file)
        original_size = test_file.stat().st_size

        # Add some content
        doc.pages[1].add_text("Incremental update", x=72, y=700, font_size=12)

        buffer = io.BytesIO()
        doc.to_stream(buffer, incremental=True)
        buffer.seek(0)
        buffer_data = buffer.read()

        # The buffer should contain PDF content
        assert buffer_data.startswith(b"%PDF-")
        doc.dispose()

    def test_to_stream_incremental_on_new_document_raises(self, pdf_bytes):
        """to_stream with incremental=True on a new document should use full save."""
        doc = Document()
        doc.pages.add()

        buffer = io.BytesIO()
        # Incremental on a new document should fall back to full write
        # (no error, just no incremental update)
        doc.to_stream(buffer, incremental=True)
        buffer.seek(0)
        assert buffer.read(5) == b"%PDF-"
        doc.dispose()

    def test_to_stream_context_manager(self, pdf_bytes):
        """to_stream works correctly when used in a context manager."""
        buffer = io.BytesIO()
        with Document.from_bytes(pdf_bytes) as doc:
            doc.to_stream(buffer)
        # Document should be disposed after context exits
        assert buffer.tell() > 0

    def test_to_stream_after_dispose_raises(self, pdf_bytes):
        """to_stream raises after document is disposed."""
        doc = Document.from_bytes(pdf_bytes)
        doc.dispose()
        buffer = io.BytesIO()
        with pytest.raises(Exception):
            doc.to_stream(buffer)

    def test_to_stream_preserves_metadata(self, pdf_bytes, tmp_path):
        """to_stream preserves document metadata."""
        doc = Document.from_bytes(pdf_bytes)
        info_before = dict(doc.info) if doc.info else {}

        buffer = io.BytesIO()
        doc.to_stream(buffer)
        buffer.seek(0)

        doc2 = Document.from_stream(buffer)
        info_after = dict(doc2.info) if doc2.info else {}

        # Metadata should be preserved
        assert info_before == info_after
        doc2.dispose()
        doc.dispose()

    def test_to_stream_bytes_matches_to_bytes(self, pdf_bytes):
        """to_stream produces the same bytes as to_bytes()."""
        doc = Document.from_bytes(pdf_bytes)

        # Get bytes via to_bytes
        expected_bytes = doc.to_bytes()

        # Get bytes via to_stream
        buffer = io.BytesIO()
        doc.to_stream(buffer)
        buffer.seek(0)
        actual_bytes = buffer.read()

        assert actual_bytes == expected_bytes
        doc.dispose()
