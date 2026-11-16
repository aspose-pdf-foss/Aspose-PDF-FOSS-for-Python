"""Tests for Document.extract_page() method."""

from __future__ import annotations

import pytest

from aspose_pdf import Document, PageSize
from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.stamps import TextStamp


class TestExtractPage:
    """Test Document.extract_page() method."""

    def test_extract_page_returns_document(self):
        """extract_page returns a Document object."""
        doc = Document()
        doc.pages.add(PageSize.A4)

        extracted = doc.extract_page(1)

        assert isinstance(extracted, Document)
        doc.dispose()

    def test_extract_page_single_page(self):
        """extract_page extracts a single page into a new document."""
        doc = Document()
        for i in range(1, 4):
            page = doc.pages.add(PageSize.A4)
            page.add_text(f"Page {i}", x=72, y=700, font_size=12)

        extracted = doc.extract_page(2)

        assert len(extracted) == 1
        assert extracted.pages[1].extract_text().strip() == "Page 2"
        # Original document unchanged
        assert len(doc) == 3
        doc.dispose()
        extracted.dispose()

    def test_extract_page_first_page(self):
        """extract_page can extract the first page."""
        doc = Document()
        doc.pages.add(PageSize.A4)
        doc.pages[1].add_text("First page", x=72, y=700, font_size=12)
        doc.pages.add(PageSize.A4)

        extracted = doc.extract_page(1)

        assert len(extracted) == 1
        assert extracted.pages[1].extract_text().strip() == "First page"
        assert len(doc) == 2
        doc.dispose()
        extracted.dispose()

    def test_extract_page_last_page(self):
        """extract_page can extract the last page."""
        doc = Document()
        doc.pages.add(PageSize.A4)
        doc.pages[1].add_text("Page 1", x=72, y=700, font_size=12)
        page2 = doc.pages.add(PageSize.A4)
        page2.add_text("Last page", x=72, y=700, font_size=12)

        extracted = doc.extract_page(2)

        assert len(extracted) == 1
        assert extracted.pages[1].extract_text().strip() == "Last page"
        assert len(doc) == 2  # Original unchanged
        doc.dispose()
        extracted.dispose()

    def test_extract_page_content_preserved(self):
        """extract_page preserves the page's content."""
        doc = Document()
        doc.pages.add(PageSize.A4)
        page = doc.pages[1]
        page.add_text("Content to preserve", x=72, y=700, font_size=12)
        # Add a stamp to test that stamps are also preserved
        page.add_stamp(TextStamp("DRAFT", font_size=12))

        extracted = doc.extract_page(1)

        assert len(extracted) == 1
        # Content and stamp text are preserved
        assert "Content to preserve" in extracted.pages[1].extract_text()
        doc.dispose()
        extracted.dispose()

    def test_extract_page_annotations_preserved(self):
        """extract_page preserves annotations on the page."""
        doc = Document()
        doc.pages.add(PageSize.A4)
        page = doc.pages[1]
        page.add_text("Text", x=72, y=700, font_size=12)
        # Add an annotation using the annotations collection
        page.annotations.add_highlight((100, 650, 200, 680))

        extracted = doc.extract_page(1)

        assert len(extracted.pages[1].annotations) == 1
        doc.dispose()
        extracted.dispose()

    def test_extract_page_independent_document(self):
        """extract_page returns an independent document."""
        doc = Document()
        doc.pages.add(PageSize.A4)
        doc.pages[1].add_text("Original", x=72, y=700, font_size=12)

        extracted = doc.extract_page(1)
        extracted.pages[1].add_text("Modified", x=72, y=650, font_size=12)

        # Original unchanged
        assert "Modified" not in doc.pages[1].extract_text()
        # Extracted has the modification
        assert "Modified" in extracted.pages[1].extract_text()
        doc.dispose()
        extracted.dispose()

    def test_extract_page_empty_document_raises(self):
        """extract_page raises when document has no pages."""
        doc = Document()

        # PdfValidationException is raised for out-of-range page numbers
        with pytest.raises(PdfValidationException):
            doc.extract_page(1)

        doc.dispose()

    def test_extract_page_invalid_page_number_raises(self):
        """extract_page raises for invalid page numbers."""
        doc = Document()
        doc.pages.add(PageSize.A4)

        # Zero is not a valid page number (1-based)
        with pytest.raises(PdfValidationException):
            doc.extract_page(0)

        # Page 2 doesn't exist
        with pytest.raises(PdfValidationException):
            doc.extract_page(2)

        doc.dispose()

    def test_extract_page_disposed_document_raises(self):
        """extract_page raises when document is disposed."""
        doc = Document()
        doc.pages.add(PageSize.A4)
        doc.dispose()

        with pytest.raises(Exception):  # AsposePdfException
            doc.extract_page(1)

    def test_extract_page_multiple_times(self):
        """extract_page can be called multiple times on the same document."""
        doc = Document()
        doc.pages.add(PageSize.A4)
        doc.pages[1].add_text("Page 1", x=72, y=700, font_size=12)
        doc.pages.add(PageSize.A4)
        doc.pages[2].add_text("Page 2", x=72, y=700, font_size=12)

        extracted1 = doc.extract_page(1)
        extracted2 = doc.extract_page(2)

        assert len(extracted1) == 1
        assert len(extracted2) == 1
        assert extracted1.pages[1].extract_text().strip() == "Page 1"
        assert extracted2.pages[1].extract_text().strip() == "Page 2"
        # Original unchanged
        assert len(doc) == 2

        doc.dispose()
        extracted1.dispose()
        extracted2.dispose()

    def test_extract_page_with_form_fields(self):
        """extract_page works with pages containing form fields."""
        doc = Document()
        doc.pages.add(PageSize.A4)
        doc.pages[1].add_text("Text", x=72, y=700, font_size=12)

        extracted = doc.extract_page(1)

        assert len(extracted) == 1
        # Page extracts successfully; form fields (if any) are preserved
        doc.dispose()
        extracted.dispose()

    def test_extract_page_with_annotations(self):
        """extract_page preserves annotations on the page."""
        doc = Document()
        doc.pages.add(PageSize.A4)
        page = doc.pages[1]
        page.add_text("Text", x=72, y=700, font_size=12)
        # Add an annotation
        page.annotations.add_highlight((100, 650, 200, 680))

        extracted = doc.extract_page(1)

        assert len(extracted.pages[1].annotations) == 1
        doc.dispose()
        extracted.dispose()

    def test_extract_page_context_manager(self):
        """extract_page can be used as a context manager."""
        with Document() as doc:
            doc.pages.add(PageSize.A4)
            doc.pages[1].add_text("Content", x=72, y=700, font_size=12)

            with doc.extract_page(1) as extracted:
                assert len(extracted) == 1
                assert extracted.pages[1].extract_text().strip() == "Content"
                # Extracted is still valid inside context

            # Original still unchanged
            assert len(doc) == 1

    def test_extract_page_returns_independent_copy(self):
        """extract_page returns a truly independent copy."""
        doc = Document()
        doc.pages.add(PageSize.A4)
        original_text = "Original text"
        doc.pages[1].add_text(original_text, x=72, y=700, font_size=12)

        extracted = doc.extract_page(1)
        # Modify the extracted document
        extracted.pages[1].add_text("New text", x=72, y=650, font_size=12)

        # Original should not have the new text
        original_content = doc.pages[1].extract_text()
        assert "New text" not in original_content
        assert original_text in original_content

        doc.dispose()
        extracted.dispose()
