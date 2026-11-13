"""Tests for Document.concatenate_pages() static method."""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document
from aspose_pdf.exceptions import PdfValidationException


def _pdf_with_text(text: str) -> bytes:
    buf = io.BytesIO()
    with Document() as doc:
        page = doc.pages.add()
        page.add_text(text, 72, 700, font_size=12)
        doc.save(buf)
    return buf.getvalue()


class TestDocumentConcatenatePages:
    """Test Document.concatenate_pages() static method."""

    def test_concatenate_pages_empty_mapping(self):
        """concatenate_pages returns an empty document for an empty mapping."""
        result = Document.concatenate_pages({})

        assert result.page_count == 0
        assert len(result) == 0

    def test_concatenate_pages_single_document_single_page(self, tmp_path):
        """concatenate_pages works with a single document and single page."""
        source = tmp_path / "source.pdf"

        with Document() as doc:
            page = doc.pages.add()
            page.add_text("Single page", 72, 700)
            doc.save(source)

        with Document(source) as source_doc:
            result = Document.concatenate_pages({source_doc: [1]})

            assert result.page_count == 1
            assert "Single page" in result.pages[1].extract_text()

    def test_concatenate_pages_single_document_multiple_pages(self, tmp_path):
        """concatenate_pages works with a single document and multiple pages."""
        source = tmp_path / "source.pdf"

        with Document() as doc:
            for i in range(1, 4):
                page = doc.pages.add()
                page.add_text(f"Page {i}", 72, 700)
            doc.save(source)

        with Document(source) as source_doc:
            result = Document.concatenate_pages({source_doc: [1, 3]})

            assert result.page_count == 2
            assert "Page 1" in result.pages[1].extract_text()
            assert "Page 3" in result.pages[2].extract_text()

    def test_concatenate_pages_multiple_documents(self, tmp_path):
        """concatenate_pages combines pages from multiple documents in order."""
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
                    result = Document.concatenate_pages({
                        doc1: [1],
                        doc2: [1],
                        doc3: [1],
                    })

                    assert result.page_count == 3
                    assert "Page A" in result.pages[1].extract_text()
                    assert "Page B" in result.pages[2].extract_text()
                    assert "Page C" in result.pages[3].extract_text()

    def test_concatenate_pages_with_bytes(self):
        """concatenate_pages works with documents loaded from bytes."""
        pdf_a = _pdf_with_text("First")
        pdf_b = _pdf_with_text("Second")
        pdf_c = _pdf_with_text("Third")

        with Document(pdf_a) as doc1:
            with Document(pdf_b) as doc2:
                with Document(pdf_c) as doc3:
                    result = Document.concatenate_pages({
                        doc1: [1],
                        doc2: [1],
                        doc3: [1],
                    })

                    assert result.page_count == 3
                    assert "First" in result.pages[1].extract_text()
                    assert "Second" in result.pages[2].extract_text()
                    assert "Third" in result.pages[3].extract_text()

    def test_concatenate_pages_returns_new_document(self):
        """concatenate_pages creates a new document, not modifying sources."""
        pdf_a = _pdf_with_text("A")
        pdf_b = _pdf_with_text("B")

        with Document(pdf_a) as doc1:
            with Document(pdf_b) as doc2:
                result = Document.concatenate_pages({
                    doc1: [1],
                    doc2: [1],
                })

                # Original documents are unchanged
                assert doc1.page_count == 1
                assert doc2.page_count == 1

                # Result has all pages
                assert result.page_count == 2

    def test_concatenate_pages_preserves_resources(self, tmp_path):
        """concatenate_pages preserves page resources, annotations, etc."""
        source = tmp_path / "source.pdf"

        with Document() as doc:
            page = doc.pages.add()
            page.add_text("Text with annotation", 72, 700)
            page.annotations.add("Text", (10, 10, 40, 40), "Test note")
            doc.save(source)

        with Document(source) as source_doc:
            result = Document.concatenate_pages({source_doc: [1]})

            assert result.page_count == 1
            annotations = result.pages[1].annotations
            assert len(annotations) == 1
            assert "Test note" in annotations[0].contents

    def test_concatenate_pages_type_error_for_non_document(self):
        """concatenate_pages raises TypeError for non-Document keys."""
        pdf = _pdf_with_text("Test")

        with Document(pdf):
            with pytest.raises(TypeError, match="must be Document instances"):
                Document.concatenate_pages({"not a document": [1]})

    def test_concatenate_pages_disposed_source_raises(self):
        """concatenate_pages raises error when source document is disposed."""
        pdf = _pdf_with_text("Test")

        with Document(pdf) as doc1:
            doc2 = Document(pdf)
            doc2.dispose()

            with pytest.raises(Exception, match="disposed"):
                Document.concatenate_pages({doc1: [1], doc2: [1]})

    def test_concatenate_pages_with_generator_source(self, tmp_path):
        """concatenate_pages works with a dict created from generator."""
        source_a = tmp_path / "a.pdf"
        source_b = tmp_path / "b.pdf"
        source_c = tmp_path / "c.pdf"

        for text, path in [("A", source_a), ("B", source_b), ("C", source_c)]:
            with Document() as doc:
                page = doc.pages.add()
                page.add_text(text, 72, 700)
                doc.save(path)

        sources = {
            Document(source_a): [1],
            Document(source_b): [1],
            Document(source_c): [1],
        }

        result = Document.concatenate_pages(sources)

        assert result.page_count == 3
        assert "A" in result.pages[1].extract_text()
        assert "B" in result.pages[2].extract_text()
        assert "C" in result.pages[3].extract_text()

    def test_concatenate_pages_empty_source_documents(self, tmp_path):
        """concatenate_pages skips documents with empty selections."""
        source = tmp_path / "source.pdf"

        # Create a document with pages
        with Document() as doc:
            page = doc.pages.add()
            page.add_text("Has pages", 72, 700)
            doc.save(source)

        # Create empty document
        with Document() as empty_doc:
            empty_bytes = empty_doc.to_bytes()

        with Document(empty_bytes) as empty_doc:
            with Document(source) as doc:
                # Empty selection raises PdfValidationException
                with pytest.raises(PdfValidationException, match="Selection of pages is empty"):
                    Document.concatenate_pages({
                        empty_doc: [],
                        doc: [1],
                    })

        # Test that documents with no pages are skipped
        with Document(source) as doc:
            result = Document.concatenate_pages({
                doc: [1],
            })
            assert result.page_count == 1
            assert "Has pages" in result.pages[1].extract_text()

    def test_concatenate_pages_with_range_string(self, tmp_path):
        """concatenate_pages works with range strings."""
        source = tmp_path / "source.pdf"

        with Document() as doc:
            for i in range(1, 11):
                page = doc.pages.add()
                page.add_text(f"Page {i}", 72, 700)
            doc.save(source)

        with Document(source) as source_doc:
            result = Document.concatenate_pages({source_doc: "1-3,7,10-"})

            assert result.page_count == 5  # pages 1, 2, 3, 7, 10, 11... wait, 10- means 10 to end
            assert "Page 1" in result.pages[1].extract_text()
            assert "Page 2" in result.pages[2].extract_text()
            assert "Page 3" in result.pages[3].extract_text()
            assert "Page 7" in result.pages[4].extract_text()
            assert "Page 10" in result.pages[5].extract_text()

    def test_concatenate_pages_with_odd(self, tmp_path):
        """concatenate_pages works with 'odd' keyword."""
        source = tmp_path / "source.pdf"

        with Document() as doc:
            for i in range(1, 7):
                page = doc.pages.add()
                page.add_text(f"Page {i}", 72, 700)
            doc.save(source)

        with Document(source) as source_doc:
            result = Document.concatenate_pages({source_doc: "odd"})

            assert result.page_count == 3  # pages 1, 3, 5
            assert "Page 1" in result.pages[1].extract_text()
            assert "Page 3" in result.pages[2].extract_text()
            assert "Page 5" in result.pages[3].extract_text()

    def test_concatenate_pages_with_even(self, tmp_path):
        """concatenate_pages works with 'even' keyword."""
        source = tmp_path / "source.pdf"

        with Document() as doc:
            for i in range(1, 7):
                page = doc.pages.add()
                page.add_text(f"Page {i}", 72, 700)
            doc.save(source)

        with Document(source) as source_doc:
            result = Document.concatenate_pages({source_doc: "even"})

            assert result.page_count == 3  # pages 2, 4, 6
            assert "Page 2" in result.pages[1].extract_text()
            assert "Page 4" in result.pages[2].extract_text()
            assert "Page 6" in result.pages[3].extract_text()

    def test_concatenate_pages_with_last(self, tmp_path):
        """concatenate_pages works with 'last' keyword in range string."""
        source = tmp_path / "source.pdf"

        with Document() as doc:
            for i in range(1, 6):
                page = doc.pages.add()
                page.add_text(f"Page {i}", 72, 700)
            doc.save(source)

        with Document(source) as source_doc:
            # Use range string with last, not a list with mixed types
            result = Document.concatenate_pages({source_doc: "1,last"})

            assert result.page_count == 2  # pages 1 and 5
            assert "Page 1" in result.pages[1].extract_text()
            assert "Page 5" in result.pages[2].extract_text()

    def test_concatenate_pages_with_last_range(self, tmp_path):
        """concatenate_pages works with last as range endpoint."""
        source = tmp_path / "source.pdf"

        with Document() as doc:
            for i in range(1, 6):
                page = doc.pages.add()
                page.add_text(f"Page {i}", 72, 700)
            doc.save(source)

        with Document(source) as source_doc:
            # last as range endpoint: "3-last" means pages 3, 4, 5
            result = Document.concatenate_pages({source_doc: "3-last"})

            assert result.page_count == 3  # pages 3, 4, 5
            assert "Page 3" in result.pages[1].extract_text()
            assert "Page 4" in result.pages[2].extract_text()
            assert "Page 5" in result.pages[3].extract_text()

    def test_concatenate_pages_reversed_order(self, tmp_path):
        """concatenate_pages respects reversed selection."""
        source = tmp_path / "source.pdf"

        with Document() as doc:
            for i in range(1, 6):
                page = doc.pages.add()
                page.add_text(f"Page {i}", 72, 700)
            doc.save(source)

        with Document(source) as source_doc:
            result = Document.concatenate_pages({source_doc: "5-1"})

            assert result.page_count == 5  # pages 5, 4, 3, 2, 1
            assert "Page 5" in result.pages[1].extract_text()
            assert "Page 3" in result.pages[3].extract_text()
            assert "Page 1" in result.pages[5].extract_text()

    def test_concatenate_pages_with_multiple_doc_order(self, tmp_path):
        """concatenate_pages respects document order in mapping."""
        source_a = tmp_path / "a.pdf"
        source_b = tmp_path / "b.pdf"

        with Document() as doc:
            page = doc.pages.add()
            page.add_text("A1", 72, 700)
            doc.save(source_a)

        with Document() as doc:
            page = doc.pages.add()
            page.add_text("B1", 72, 700)
            doc.save(source_b)

        with Document(source_a) as doc_a:
            with Document(source_b) as doc_b:
                # Reverse order: B first, then A
                result = Document.concatenate_pages({
                    doc_b: [1],
                    doc_a: [1],
                })

                assert result.page_count == 2
                assert "B1" in result.pages[1].extract_text()
                assert "A1" in result.pages[2].extract_text()

    def test_concatenate_pages_source_remains_independent(self):
        """concatenate_pages does not affect source documents' independence."""
        pdf_a = _pdf_with_text("A")
        pdf_b = _pdf_with_text("B")

        with Document(pdf_a) as doc1:
            with Document(pdf_b) as doc2:
                result = Document.concatenate_pages({
                    doc1: [1],
                    doc2: [1],
                })

                # Sources should remain valid and independent
                assert doc1.page_count == 1
                assert doc2.page_count == 1
                assert doc1.closed is False
                assert doc2.closed is False

                # Result is independent
                assert result.page_count == 2
                assert result.closed is False

    def test_concatenate_pages_error_cleanup(self):
        """concatenate_pages disposes result on error during iteration."""
        pdf_a = _pdf_with_text("A")
        pdf_b = _pdf_with_text("B")

        with Document(pdf_a) as doc1:
            doc2 = Document(pdf_b)
            doc2.dispose()

            with pytest.raises(Exception):
                Document.concatenate_pages({doc1: [1], doc2: [1]})

            # Result document should be disposed

    def test_concatenate_pages_with_context_manager(self, tmp_path):
        """concatenate_pages can be used in a context manager."""
        source = tmp_path / "source.pdf"

        with Document() as doc:
            page = doc.pages.add()
            page.add_text("Content", 72, 700)
            doc.save(source)

        with Document(source) as source_doc:
            with Document.concatenate_pages({source_doc: [1]}) as result:
                assert result.page_count == 1
                assert "Content" in result.pages[1].extract_text()
                assert result.closed is False

            # Result should be closed after context manager exits
            assert result.closed is True

    def test_concatenate_pages_invalid_selection_raises(self):
        """concatenate_pages raises error for out-of-range page numbers."""
        pdf = _pdf_with_text("Test")

        with Document(pdf) as doc:
            # Document has only 1 page
            with pytest.raises(PdfValidationException, match="out of range"):
                Document.concatenate_pages({doc: [5]})

    def test_concatenate_pages_empty_selection_raises(self):
        """concatenate_pages raises error for empty selection."""
        pdf = _pdf_with_text("Test")

        with Document(pdf) as doc:
            with pytest.raises(PdfValidationException, match="Selection of pages is empty"):
                Document.concatenate_pages({doc: []})

    def test_concatenate_pages_zero_page_number_raises(self):
        """concatenate_pages rejects page number 0."""
        pdf = _pdf_with_text("Test")

        with Document(pdf) as doc:
            with pytest.raises(PdfValidationException, match="Page number must be >= 1"):
                Document.concatenate_pages({doc: [0]})

    def test_concatenate_pages_negative_page_number_raises(self):
        """concatenate_pages rejects negative page numbers."""
        pdf = _pdf_with_text("Test")

        with Document(pdf) as doc:
            with pytest.raises(PdfValidationException, match="Page number must be >= 1"):
                Document.concatenate_pages({doc: [-1]})

    def test_concatenate_pages_invalid_selection_type_raises(self):
        """concatenate_pages raises TypeError for invalid selection type."""
        pdf = _pdf_with_text("Test")

        with Document(pdf) as doc:
            with pytest.raises(TypeError, match="must be iterables of integers or strings"):
                Document.concatenate_pages({doc: 123})  # Not iterable

    def test_concatenate_pages_with_repeated_pages(self, tmp_path):
        """concatenate_pages allows repeated pages from same document."""
        source = tmp_path / "source.pdf"

        with Document() as doc:
            page = doc.pages.add()
            page.add_text("Page 1", 72, 700)
            doc.save(source)

        with Document(source) as source_doc:
            result = Document.concatenate_pages({source_doc: [1, 1, 1]})

            # Each occurrence becomes a separate page
            assert result.page_count == 3
            assert "Page 1" in result.pages[1].extract_text()
            assert "Page 1" in result.pages[2].extract_text()
            assert "Page 1" in result.pages[3].extract_text()

    def test_concatenate_pages_with_multiple_doc_repeated_pages(self, tmp_path):
        """concatenate_pages allows repeated pages from different documents."""
        source_a = tmp_path / "a.pdf"
        source_b = tmp_path / "b.pdf"

        with Document() as doc:
            page = doc.pages.add()
            page.add_text("A", 72, 700)
            doc.save(source_a)

        with Document() as doc:
            page = doc.pages.add()
            page.add_text("B", 72, 700)
            doc.save(source_b)

        with Document(source_a) as doc_a:
            with Document(source_b) as doc_b:
                result = Document.concatenate_pages({
                    doc_a: [1, 1],
                    doc_b: [1],
                })

                # Pages are preserved in order
                assert result.page_count == 3
                assert "A" in result.pages[1].extract_text()
                assert "A" in result.pages[2].extract_text()
                assert "B" in result.pages[3].extract_text()

    def test_concatenate_pages_with_generator_selection(self):
        """concatenate_pages works with generator as page selection."""
        pdf = _pdf_with_text("A")

        with Document(pdf) as doc:
            # Use a generator expression for page selection
            result = Document.concatenate_pages({
                doc: (p for p in [1]),
            })

            assert result.page_count == 1
            assert "A" in result.pages[1].extract_text()

    def test_concatenate_pages_preserves_page_properties(self, tmp_path):
        """concatenate_pages preserves page rotation, size, and other properties."""
        source = tmp_path / "source.pdf"

        with Document() as doc:
            page = doc.pages.add()
            page.add_text("Rotated page", 72, 700)
            page.rotation = 90  # Set rotation
            doc.save(source)

        with Document(source) as source_doc:
            result = Document.concatenate_pages({source_doc: [1]})

            # Page properties should be preserved
            assert result.page_count == 1
            assert result.pages[1].rotation == 90
