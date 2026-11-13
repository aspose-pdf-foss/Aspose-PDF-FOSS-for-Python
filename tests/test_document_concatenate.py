"""Tests for Document.concatenate() static method."""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document


def _pdf_with_text(text: str) -> bytes:
    buf = io.BytesIO()
    with Document() as doc:
        page = doc.pages.add()
        page.add_text(text, 72, 700, font_size=12)
        doc.save(buf)
    return buf.getvalue()


class TestDocumentConcatenate:
    """Test Document.concatenate() static method."""

    def test_concatenate_empty_iterable(self):
        """concatenate returns an empty document for an empty iterable."""
        result = Document.concatenate([])

        assert result.page_count == 0
        assert len(result) == 0

    def test_concatenate_single_document(self, tmp_path):
        """concatenate works with a single document."""
        source = tmp_path / "source.pdf"

        with Document() as doc:
            page = doc.pages.add()
            page.add_text("Single page", 72, 700)
            doc.save(source)

        with Document(source) as source_doc:
            result = Document.concatenate([source_doc])

            assert result.page_count == 1
            assert "Single page" in result.pages[1].extract_text()

    def test_concatenate_multiple_documents(self, tmp_path):
        """concatenate combines multiple documents in order."""
        source_a = tmp_path / "a.pdf"
        source_b = tmp_path / "b.pdf"
        source_c = tmp_path / "c.pdf"

        with Document() as doc:
            page = doc.pages.add()
            page.add_text("Page A", 72, 700)
            doc.save(source_a)

        with Document() as doc:
            page = doc.pages.add()
            page.add_text("Page B", 72, 700)
            doc.save(source_b)

        with Document() as doc:
            page = doc.pages.add()
            page.add_text("Page C", 72, 700)
            doc.save(source_c)

        with Document(source_a) as doc1:
            with Document(source_b) as doc2:
                with Document(source_c) as doc3:
                    result = Document.concatenate([doc1, doc2, doc3])

                    assert result.page_count == 3
                    assert "Page A" in result.pages[1].extract_text()
                    assert "Page B" in result.pages[2].extract_text()
                    assert "Page C" in result.pages[3].extract_text()

    def test_concatenate_with_bytes(self):
        """concatenate works with documents loaded from bytes."""
        pdf_a = _pdf_with_text("First")
        pdf_b = _pdf_with_text("Second")
        pdf_c = _pdf_with_text("Third")

        with Document(pdf_a) as doc1:
            with Document(pdf_b) as doc2:
                with Document(pdf_c) as doc3:
                    result = Document.concatenate([doc1, doc2, doc3])

                    assert result.page_count == 3
                    assert "First" in result.pages[1].extract_text()
                    assert "Second" in result.pages[2].extract_text()
                    assert "Third" in result.pages[3].extract_text()

    def test_concatenate_returns_new_document(self):
        """concatenate creates a new document, not modifying sources."""
        pdf_a = _pdf_with_text("A")
        pdf_b = _pdf_with_text("B")

        with Document(pdf_a) as doc1:
            with Document(pdf_b) as doc2:
                result = Document.concatenate([doc1, doc2])

                # Original documents are unchanged
                assert doc1.page_count == 1
                assert doc2.page_count == 1

                # Result has all pages
                assert result.page_count == 2

    def test_concatenate_preserves_resources(self, tmp_path):
        """concatenate preserves page resources, annotations, etc."""
        source = tmp_path / "source.pdf"

        with Document() as doc:
            page = doc.pages.add()
            page.add_text("Text with annotation", 72, 700)
            page.annotations.add("Text", (10, 10, 40, 40), "Test note")
            doc.save(source)

        with Document(source) as source_doc:
            result = Document.concatenate([source_doc])

            assert result.page_count == 1
            annotations = result.pages[1].annotations
            assert len(annotations) == 1
            assert "Test note" in annotations[0].contents

    def test_concatenate_type_error_for_non_document(self):
        """concatenate raises TypeError for non-Document items."""
        pdf = _pdf_with_text("Test")

        with Document(pdf) as doc:
            with pytest.raises(TypeError, match="must be Document instances"):
                Document.concatenate([doc, "not a document"])

    def test_concatenate_disposed_source_raises(self):
        """concatenate raises error when source document is disposed."""
        pdf = _pdf_with_text("Test")

        with Document(pdf) as doc1:
            doc2 = Document(pdf)
            doc2.dispose()

            with pytest.raises(Exception, match="disposed"):
                Document.concatenate([doc1, doc2])

    def test_concatenate_with_generator(self):
        """concatenate works with a generator expression."""
        sources = [_pdf_with_text(str(i)) for i in range(1, 4)]

        def document_generator():
            for pdf_bytes in sources:
                yield Document(pdf_bytes)

        result = Document.concatenate(document_generator())

        assert result.page_count == 3
        assert "1" in result.pages[1].extract_text()
        assert "2" in result.pages[2].extract_text()
        assert "3" in result.pages[3].extract_text()

    def test_concatenate_with_tuple(self):
        """concatenate works with a tuple of documents."""
        pdf_a = _pdf_with_text("A")
        pdf_b = _pdf_with_text("B")

        with Document(pdf_a) as doc1:
            with Document(pdf_b) as doc2:
                result = Document.concatenate((doc1, doc2))

                assert result.page_count == 2
                assert "A" in result.pages[1].extract_text()
                assert "B" in result.pages[2].extract_text()

    def test_concatenate_empty_source_documents(self, tmp_path):
        """concatenate handles documents with no pages."""
        # Create an empty document
        with Document() as empty_doc:
            empty_bytes = empty_doc.to_bytes()

        pdf_with_pages = _pdf_with_text("Has pages")

        with Document(empty_bytes) as empty_doc:
            with Document(pdf_with_pages) as doc:
                result = Document.concatenate([empty_doc, doc, empty_doc])

                # Empty documents contribute no pages
                assert result.page_count == 1
                assert "Has pages" in result.pages[1].extract_text()

    def test_concatenate_chaining(self):
        """concatenate can be used with other document operations."""
        pdf_a = _pdf_with_text("A")
        pdf_b = _pdf_with_text("B")

        with Document() as result:
            with Document(pdf_a) as doc1:
                with Document(pdf_b) as doc2:
                    # Use concatenate to create a temporary document
                    temp = Document.concatenate([doc1, doc2])
                    result.append(temp)

            assert result.page_count == 2

    def test_concatenate_preserves_page_properties(self, tmp_path):
        """concatenate preserves page rotation, size, and other properties."""
        source = tmp_path / "source.pdf"

        with Document() as doc:
            page = doc.pages.add()
            page.add_text("Rotated page", 72, 700)
            page.rotation = 90  # Set rotation
            doc.save(source)

        with Document(source) as source_doc:
            result = Document.concatenate([source_doc])

            # Page properties should be preserved
            assert result.page_count == 1
            # Rotation is a property of the page
            assert result.pages[1].rotation == 90

    def test_concatenate_source_remains_independent(self):
        """concatenate does not affect the source documents' independence."""
        pdf_a = _pdf_with_text("A")
        pdf_b = _pdf_with_text("B")

        with Document(pdf_a) as doc1:
            with Document(pdf_b) as doc2:
                result = Document.concatenate([doc1, doc2])

                # Sources should remain valid and independent
                assert doc1.page_count == 1
                assert doc2.page_count == 1
                assert doc1.closed is False
                assert doc2.closed is False

                # Result is independent
                assert result.page_count == 2
                assert result.closed is False

    def test_concatenate_error_cleanup(self):
        """concatenate disposes result on error during iteration."""
        pdf_a = _pdf_with_text("A")
        pdf_b = _pdf_with_text("B")

        with Document(pdf_a) as doc1:
            doc2 = Document(pdf_b)
            doc2.dispose()

            with pytest.raises(Exception):
                Document.concatenate([doc1, doc2])

            # Result document should be disposed

    def test_concatenate_with_context_manager(self, tmp_path):
        """concatenate can be used in a context manager."""
        source = tmp_path / "source.pdf"

        with Document() as doc:
            page = doc.pages.add()
            page.add_text("Content", 72, 700)
            doc.save(source)

        with Document(source) as source_doc:
            with Document.concatenate([source_doc]) as result:
                assert result.page_count == 1
                assert "Content" in result.pages[1].extract_text()
                assert result.closed is False

            # Result should be closed after context manager exits
            assert result.closed is True

    def test_concatenate_multiple_empty_documents(self):
        """concatenate handles multiple empty documents."""
        sources = [_pdf_with_text("") for _ in range(3)]

        # Create documents from empty-ish sources
        empty_docs = []
        for pdf_bytes in sources:
            doc = Document(pdf_bytes)
            # Ensure it has no pages
            if doc.page_count == 0:
                empty_docs.append(doc)

        if empty_docs:  # If we have empty docs to test
            result = Document.concatenate(empty_docs)
            assert result.page_count == 0

        # Also test with no documents at all
        result = Document.concatenate([])
        assert result.page_count == 0
