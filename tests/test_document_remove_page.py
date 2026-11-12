"""Tests for Document.remove_page() method."""

from __future__ import annotations

import pytest

from aspose_pdf import Document


class TestRemovePage:
    """Test Document.remove_page() method."""

    def test_remove_page_returns_page(self):
        """remove_page returns a Page object."""
        doc = Document()
        doc.pages.add()
        doc.pages.add()
        doc.pages.add()

        page = doc.remove_page(2)
        assert page is not None

    def test_remove_page_reduces_count(self):
        """remove_page decreases the page count by one."""
        doc = Document()
        doc.pages.add()
        doc.pages.add()
        doc.pages.add()

        initial_count = len(doc)
        doc.remove_page(2)
        assert len(doc) == initial_count - 1

    def test_remove_page_first_page(self):
        """remove_page can remove the first page."""
        doc = Document()
        doc.pages.add()
        doc.pages.add()
        doc.pages.add()

        doc.pages[1].add_text("First page", x=72, y=700, font_size=12)
        doc.pages[2].add_text("Second page", x=72, y=700, font_size=12)
        doc.pages[3].add_text("Third page", x=72, y=700, font_size=12)

        doc.remove_page(1)

        assert len(doc) == 2
        assert doc.pages[1].extract_text() == "Second page"
        assert doc.pages[2].extract_text() == "Third page"

    def test_remove_page_last_page(self):
        """remove_page can remove the last page."""
        doc = Document()
        doc.pages.add()
        doc.pages.add()
        doc.pages.add()

        doc.pages[1].add_text("First page", x=72, y=700, font_size=12)
        doc.pages[2].add_text("Second page", x=72, y=700, font_size=12)
        doc.pages[3].add_text("Third page", x=72, y=700, font_size=12)

        doc.remove_page(3)

        assert len(doc) == 2
        assert doc.pages[1].extract_text() == "First page"
        assert doc.pages[2].extract_text() == "Second page"

    def test_remove_page_middle_page(self):
        """remove_page can remove a middle page."""
        doc = Document()
        doc.pages.add()
        doc.pages.add()
        doc.pages.add()
        doc.pages.add()
        doc.pages.add()

        for i in range(1, 6):
            doc.pages[i].add_text(f"Page {i}", x=72, y=700, font_size=12)

        doc.remove_page(3)

        assert len(doc) == 4
        assert doc.pages[1].extract_text() == "Page 1"
        assert doc.pages[2].extract_text() == "Page 2"
        assert doc.pages[3].extract_text() == "Page 4"
        assert doc.pages[4].extract_text() == "Page 5"

    def test_remove_page_content_preserved(self):
        """remove_page preserves the page's content."""
        doc = Document()
        doc.pages.add()
        doc.pages.add()

        doc.pages[2].add_text("Content to preserve", x=72, y=700, font_size=12)
        removed = doc.remove_page(2)

        assert removed.extract_text() == "Content to preserve"

    def test_remove_page_returns_removable_page(self):
        """remove_page returns a page that can be inserted elsewhere."""
        doc1 = Document()
        doc1.pages.add()
        doc1.pages.add()
        doc1.pages.add()
        doc1.pages[2].add_text("Page to move", x=72, y=700, font_size=12)

        doc2 = Document()
        removed = doc1.remove_page(2)
        doc2.pages.insert(1, removed)

        assert len(doc2) == 1
        assert doc2.pages[1].extract_text() == "Page to move"

    def test_remove_page_error_empty_document(self):
        """remove_page raises IndexError on an empty document."""
        doc = Document()

        with pytest.raises(IndexError):
            doc.remove_page(1)

    def test_remove_page_error_invalid_page_number(self):
        """remove_page raises IndexError for invalid page numbers."""
        doc = Document()
        doc.pages.add()
        doc.pages.add()

        # Page number 0 is invalid
        with pytest.raises(IndexError):
            doc.remove_page(0)

        # Page number greater than count is invalid
        with pytest.raises(IndexError):
            doc.remove_page(3)

        # Negative page number is invalid
        with pytest.raises(IndexError):
            doc.remove_page(-1)

    def test_remove_page_page_number_zero_based_error(self):
        """remove_page expects 1-based page numbers."""
        doc = Document()
        doc.pages.add()

        # Using 0-based index should raise
        with pytest.raises(IndexError):
            doc.remove_page(0)

    def test_remove_page_disposed_document(self):
        """remove_page raises after document is disposed."""
        doc = Document()
        doc.pages.add()

        doc.dispose()

        with pytest.raises(Exception):
            doc.remove_page(1)

    def test_remove_page_single_page(self):
        """remove_page works on a single-page document."""
        doc = Document()
        doc.pages.add()
        doc.pages[1].add_text("Only page", x=72, y=700, font_size=12)

        removed = doc.remove_page(1)

        assert len(doc) == 0
        assert removed.extract_text() == "Only page"

    def test_remove_page_annotations_preserved(self):
        """remove_page preserves annotations on the removed page."""
        doc = Document()
        doc.pages.add()
        doc.pages.add()
        doc.pages.add()

        # Add an annotation to page 2 using proper quad points format
        quad_points = [(100, 700), (200, 700), (100, 650), (200, 650)]
        doc.pages[2].annotations.add_highlight(
            quads=quad_points,
            color=(1, 0, 0),
        )

        removed = doc.remove_page(2)

        # The removed page should have the annotation
        assert len(removed.annotations) == 1

    def test_remove_page_multiple_times(self):
        """remove_page can be called multiple times."""
        doc = Document()
        for i in range(1, 6):
            doc.pages.add()
            doc.pages[i].add_text(f"Page {i}", x=72, y=700, font_size=12)

        # Remove pages in different orders
        removed_3 = doc.remove_page(3)
        removed_1 = doc.remove_page(1)
        removed_2 = doc.remove_page(2)  # Now this is the old page 4

        assert len(doc) == 2
        assert removed_3.extract_text() == "Page 3"
        assert removed_1.extract_text() == "Page 1"
        assert removed_2.extract_text() == "Page 4"

    def test_remove_page_in_context_manager(self):
        """remove_page works correctly in a context manager."""
        with Document() as doc:
            doc.pages.add()
            doc.pages.add()
            doc.pages.add()

            doc.pages[2].add_text("To be removed", x=72, y=700, font_size=12)
            removed = doc.remove_page(2)

            assert len(doc) == 2
            assert removed.extract_text() == "To be removed"

        # Document should be disposed after context exits
        assert doc.closed

    def test_remove_page_from_file(self, tmp_path):
        """remove_page works on a document loaded from file."""
        # Create a test PDF
        doc = Document()
        for i in range(1, 4):
            doc.pages.add()
            doc.pages[i].add_text(f"Page {i}", x=72, y=700, font_size=12)

        test_file = tmp_path / "test.pdf"
        doc.save(test_file)

        # Load and remove a page
        loaded = Document.from_file(test_file)
        removed = loaded.remove_page(2)

        assert len(loaded) == 2
        assert removed.extract_text() == "Page 2"

    def test_remove_page_preserves_page_properties(self):
        """remove_page preserves page properties like rotation."""
        doc = Document()
        doc.pages.add()
        doc.pages.add()
        doc.pages.add()

        # Set rotation on page 2
        doc.pages[2].rotation = 90

        removed = doc.remove_page(2)

        assert removed.rotation == 90

    def test_remove_page_with_page_labels(self):
        """remove_page works with documents that have page labels."""
        from aspose_pdf import NumberingStyle, PageLabel

        doc = Document()
        doc.pages.add()
        doc.pages.add()
        doc.pages.add()

        # Set page labels - each entry defines a new range starting at that page
        # page_labels[0] = Arabic 1,2,3...
        # page_labels[1] = Roman ii,iii,iv... (continuing from previous range)
        # page_labels[2] = Roman iii,iv,v... (continuing from previous range)
        doc.page_labels[0] = PageLabel(NumberingStyle.NUMERALS_ARABIC)
        doc.page_labels[1] = PageLabel(NumberingStyle.NUMERALS_ROMAN_LOWERCASE, "ii")
        doc.page_labels[2] = PageLabel(NumberingStyle.NUMERALS_ROMAN_LOWERCASE, "iii")

        # After setting, page 1 should be "1", page 2 should be "ii", page 3 should be "iii"
        # But due to the way ranges work, let's check what we actually get
        page2_label = doc.pages[2].label

        removed = doc.remove_page(2)

        assert len(doc) == 2
        # The removed page should have the label it had before removal
        assert removed.label == page2_label

    def test_remove_page_after_save(self, tmp_path):
        """remove_page works after saving a document."""
        doc = Document()
        doc.pages.add()
        doc.pages.add()
        doc.pages.add()

        test_file = tmp_path / "test.pdf"
        doc.save(test_file)

        # Reload
        reloaded = Document.from_file(test_file)
        removed = reloaded.remove_page(2)

        assert len(reloaded) == 2
        assert removed is not None
