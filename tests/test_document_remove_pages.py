"""Tests for Document.remove_pages() method."""

from __future__ import annotations

import pytest

from aspose_pdf import Document, NumberingStyle, PageLabel, PageSize


class TestRemovePages:
    """Test Document.remove_pages() method."""

    def test_remove_pages_returns_pages(self):
        """remove_pages returns a list of Page objects."""
        doc = Document()
        for _ in range(5):
            doc.pages.add(PageSize.A4)

        removed_pages = doc.remove_pages([1, 3, 5])

        assert len(removed_pages) == 3
        from aspose_pdf.pages import Page
        assert all(isinstance(p, Page) for p in removed_pages)
        doc.dispose()

    def test_remove_pages_reduces_count(self):
        """remove_pages decreases the page count by the number of removed pages."""
        doc = Document()
        for _ in range(5):
            doc.pages.add(PageSize.A4)

        initial_count = len(doc)
        removed_pages = doc.remove_pages([1, 3, 5])

        assert len(doc) == initial_count - len(removed_pages)
        doc.dispose()

    def test_remove_pages_first_middle_last(self):
        """remove_pages can remove first, middle, and last pages."""
        doc = Document()
        for i in range(1, 6):
            page = doc.pages.add(PageSize.A4)
            page.add_text(f"Page {i}", x=72, y=700, font_size=12)

        removed_pages = doc.remove_pages([1, 3, 5])

        assert len(removed_pages) == 3
        assert removed_pages[0].extract_text().strip() == "Page 1"
        assert removed_pages[1].extract_text().strip() == "Page 3"
        assert removed_pages[2].extract_text().strip() == "Page 5"
        assert len(doc) == 2
        assert doc.pages[1].extract_text().strip() == "Page 2"
        assert doc.pages[2].extract_text().strip() == "Page 4"
        doc.dispose()

    def test_remove_pages_content_preserved(self):
        """remove_pages preserves the pages' content."""
        doc = Document()
        doc.pages.add(PageSize.A4)
        doc.pages[1].add_text("Content to preserve", x=72, y=700, font_size=12)
        doc.pages.add(PageSize.A4)
        doc.pages[2].add_text("More content", x=72, y=700, font_size=12)

        removed_pages = doc.remove_pages([1, 2])

        assert removed_pages[0].extract_text().strip() == "Content to preserve"
        assert removed_pages[1].extract_text().strip() == "More content"
        doc.dispose()

    def test_remove_pages_returns_removable_pages(self):
        """remove_pages returns pages that can be inserted elsewhere."""
        doc1 = Document()
        for i in range(1, 4):
            page = doc1.pages.add(PageSize.A4)
            page.add_text(f"Page {i}", x=72, y=700, font_size=12)

        doc2 = Document()
        removed_pages = doc1.remove_pages([2])
        doc2.pages.insert(1, removed_pages[0])

        assert len(doc2) == 1
        assert doc2.pages[1].extract_text().strip() == "Page 2"
        doc1.dispose()
        doc2.dispose()

    def test_remove_pages_empty_selection(self):
        """remove_pages with empty selection returns empty list."""
        doc = Document()
        doc.pages.add(PageSize.A4)
        doc.pages.add(PageSize.A4)

        removed_pages = doc.remove_pages([])

        assert removed_pages == []
        assert len(doc) == 2
        doc.dispose()

    def test_remove_pages_range_string(self):
        """remove_pages accepts range strings."""
        doc = Document()
        for i in range(1, 11):
            page = doc.pages.add(PageSize.A4)
            page.add_text(f"Page {i}", x=72, y=700, font_size=12)

        removed_pages = doc.remove_pages("2-5")

        assert len(removed_pages) == 4
        assert removed_pages[0].extract_text().strip() == "Page 2"
        assert removed_pages[3].extract_text().strip() == "Page 5"
        assert len(doc) == 6
        doc.dispose()

    def test_remove_pages_odd_even(self):
        """remove_pages accepts odd/even range strings."""
        doc = Document()
        for i in range(1, 7):
            page = doc.pages.add(PageSize.A4)
            page.add_text(f"Page {i}", x=72, y=700, font_size=12)

        # Remove odd pages (1, 3, 5)
        removed_pages = doc.remove_pages("odd")
        assert len(removed_pages) == 3
        assert removed_pages[0].extract_text().strip() == "Page 1"
        assert removed_pages[1].extract_text().strip() == "Page 3"
        assert removed_pages[2].extract_text().strip() == "Page 5"
        assert len(doc) == 3
        # Remaining pages are now 2, 4, 6 (renumbered as 1, 2, 3)

        # Remove even pages from remaining document (originally page 4, now page 2)
        removed_pages = doc.remove_pages("even")
        assert len(removed_pages) == 1
        assert removed_pages[0].extract_text().strip() == "Page 4"
        doc.dispose()

    def test_remove_pages_invalid_page_number(self):
        """remove_pages raises for invalid page numbers."""
        doc = Document()
        doc.pages.add(PageSize.A4)
        doc.pages.add(PageSize.A4)

        with pytest.raises(Exception):
            doc.remove_pages([0])

        with pytest.raises(Exception):
            doc.remove_pages([3])

        with pytest.raises(Exception):
            doc.remove_pages([-1])
        doc.dispose()

    def test_remove_pages_disposed_document(self):
        """remove_pages raises after document is disposed."""
        doc = Document()
        doc.pages.add(PageSize.A4)
        doc.dispose()

        with pytest.raises(Exception):
            doc.remove_pages([1])

    def test_remove_pages_single_page(self):
        """remove_pages works on a single-page document."""
        doc = Document()
        page = doc.pages.add(PageSize.A4)
        page.add_text("Only page", x=72, y=700, font_size=12)

        removed_pages = doc.remove_pages([1])

        assert len(removed_pages) == 1
        assert len(doc) == 0
        assert removed_pages[0].extract_text().strip() == "Only page"
        doc.dispose()

    def test_remove_pages_annotations_preserved(self):
        """remove_pages preserves annotations on the removed pages."""
        doc = Document()
        for _ in range(3):
            doc.pages.add(PageSize.A4)

        # Add an annotation to page 2
        doc.pages[2].annotations.add_highlight(
            quads=[(100, 700), (200, 700), (100, 650), (200, 650)],
            color=(1, 0, 0),
        )

        removed_pages = doc.remove_pages([2])

        assert len(removed_pages) == 1
        assert len(removed_pages[0].annotations) == 1
        doc.dispose()

    def test_remove_pages_multiple_times(self):
        """remove_pages can be called multiple times."""
        doc = Document()
        for i in range(1, 6):
            page = doc.pages.add(PageSize.A4)
            page.add_text(f"Page {i}", x=72, y=700, font_size=12)

        # Remove pages in different orders
        removed_3 = doc.remove_pages([3])
        removed_1 = doc.remove_pages([1])
        removed_2 = doc.remove_pages([2])  # Now this is the old page 4

        assert len(doc) == 2
        assert removed_3[0].extract_text().strip() == "Page 3"
        assert removed_1[0].extract_text().strip() == "Page 1"
        assert removed_2[0].extract_text().strip() == "Page 4"
        doc.dispose()

    def test_remove_pages_in_context_manager(self):
        """remove_pages works correctly in a context manager."""
        with Document() as doc:
            for i in range(1, 4):
                page = doc.pages.add(PageSize.A4)
                page.add_text(f"Page {i}", x=72, y=700, font_size=12)

            removed_pages = doc.remove_pages([2])

            assert len(doc) == 2
            assert len(removed_pages) == 1
            assert removed_pages[0].extract_text().strip() == "Page 2"

        # Document should be disposed after context exits
        assert doc.closed

    def test_remove_pages_from_file(self, tmp_path):
        """remove_pages works on a document loaded from file."""
        # Create a test PDF
        doc = Document()
        for i in range(1, 4):
            page = doc.pages.add(PageSize.A4)
            page.add_text(f"Page {i}", x=72, y=700, font_size=12)

        test_file = tmp_path / "test.pdf"
        doc.save(test_file)

        # Load and remove pages
        loaded = Document.from_file(test_file)
        removed_pages = loaded.remove_pages([2])

        assert len(loaded) == 2
        assert len(removed_pages) == 1
        assert removed_pages[0].extract_text().strip() == "Page 2"
        loaded.dispose()

    def test_remove_pages_preserves_page_properties(self):
        """remove_pages preserves page properties like rotation."""
        doc = Document()
        for _ in range(3):
            doc.pages.add(PageSize.A4)

        # Set rotation on page 2
        doc.pages[2].rotation = 90

        removed_pages = doc.remove_pages([2])

        assert len(removed_pages) == 1
        assert removed_pages[0].rotation == 90
        doc.dispose()

    def test_remove_pages_with_page_labels(self):
        """remove_pages works with documents that have page labels."""
        doc = Document()
        for _ in range(3):
            doc.pages.add(PageSize.A4)

        # Set page labels
        doc.page_labels[0] = PageLabel(NumberingStyle.NUMERALS_ARABIC)
        doc.page_labels[1] = PageLabel(NumberingStyle.NUMERALS_ROMAN_LOWERCASE, "ii")
        doc.page_labels[2] = PageLabel(NumberingStyle.NUMERALS_ROMAN_LOWERCASE, "iii")

        # Get the label before removal
        page2_label = doc.pages[2].label

        removed_pages = doc.remove_pages([2])

        assert len(removed_pages) == 1
        # The removed page should have the label it had before removal
        assert removed_pages[0].label == page2_label
        doc.dispose()

    def test_remove_pages_repeated_pages(self):
        """remove_pages with repeated page numbers extracts duplicates but deletes unique pages."""
        doc = Document()
        for i in range(1, 6):
            page = doc.pages.add(PageSize.A4)
            page.add_text(f"Page {i}", x=72, y=700, font_size=12)

        # Request to remove page 2 twice
        # extract_pages preserves duplicates (4 pages extracted)
        # but delete only removes each unique page once (3 pages deleted)
        removed_pages = doc.remove_pages([1, 2, 2, 3])

        # Should extract 4 pages (2 appears twice)
        assert len(removed_pages) == 4
        # After deleting 3 unique pages, 2 remain
        assert len(doc) == 2
        # Remaining pages are original 4 and 5
        assert doc.pages[1].extract_text().strip() == "Page 4"
        assert doc.pages[2].extract_text().strip() == "Page 5"
        doc.dispose()

    def test_remove_pages_empty_document(self):
        """remove_pages raises on an empty document."""
        doc = Document()

        with pytest.raises(Exception):
            doc.remove_pages([1])
        doc.dispose()
