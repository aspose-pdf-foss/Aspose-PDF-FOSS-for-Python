"""Tests for PageCollection.pop() method."""

from __future__ import annotations

import pytest

from aspose_pdf import Document


class TestPageCollectionPop:
    """Test PageCollection.pop() method."""

    def test_pop_returns_page(self):
        """pop returns a Page object."""
        doc = Document()
        doc.pages.add()
        doc.pages.add()

        page = doc.pages.pop()

        assert page is not None

    def test_pop_reduces_count(self):
        """pop decreases the page count by one."""
        doc = Document()
        doc.pages.add()
        doc.pages.add()
        doc.pages.add()

        initial_count = len(doc)
        doc.pages.pop()
        assert len(doc) == initial_count - 1

    def test_pop_last_page(self):
        """pop removes the last page by default."""
        doc = Document()
        doc.pages.add()
        doc.pages.add()
        doc.pages.add()

        doc.pages[1].add_text("First page", x=72, y=700, font_size=12)
        doc.pages[2].add_text("Second page", x=72, y=700, font_size=12)
        doc.pages[3].add_text("Third page", x=72, y=700, font_size=12)

        last_page = doc.pages.pop()

        assert len(doc) == 2
        assert doc.pages[1].extract_text() == "First page"
        assert doc.pages[2].extract_text() == "Second page"
        assert last_page.extract_text() == "Third page"

    def test_pop_first_page(self):
        """pop can remove the first page when index is 1."""
        doc = Document()
        doc.pages.add()
        doc.pages.add()
        doc.pages.add()

        doc.pages[1].add_text("First page", x=72, y=700, font_size=12)
        doc.pages[2].add_text("Second page", x=72, y=700, font_size=12)
        doc.pages[3].add_text("Third page", x=72, y=700, font_size=12)

        first_page = doc.pages.pop(1)

        assert len(doc) == 2
        assert doc.pages[1].extract_text() == "Second page"
        assert doc.pages[2].extract_text() == "Third page"
        assert first_page.extract_text() == "First page"

    def test_pop_middle_page(self):
        """pop can remove a middle page."""
        doc = Document()
        doc.pages.add()
        doc.pages.add()
        doc.pages.add()
        doc.pages.add()
        doc.pages.add()

        for i in range(1, 6):
            doc.pages[i].add_text(f"Page {i}", x=72, y=700, font_size=12)

        middle_page = doc.pages.pop(3)

        assert len(doc) == 4
        assert doc.pages[1].extract_text() == "Page 1"
        assert doc.pages[2].extract_text() == "Page 2"
        assert doc.pages[3].extract_text() == "Page 4"
        assert doc.pages[4].extract_text() == "Page 5"
        assert middle_page.extract_text() == "Page 3"

    def test_pop_content_preserved(self):
        """pop preserves the page's content."""
        doc = Document()
        doc.pages.add()
        doc.pages.add()

        doc.pages[2].add_text("Content to preserve", x=72, y=700, font_size=12)
        removed = doc.pages.pop()

        assert removed.extract_text() == "Content to preserve"

    def test_pop_returns_removable_page(self):
        """pop returns a page that can be inserted elsewhere."""
        doc1 = Document()
        doc1.pages.add()
        doc1.pages.add()
        doc1.pages.add()
        doc1.pages[2].add_text("Page to move", x=72, y=700, font_size=12)

        doc2 = Document()
        popped = doc1.pages.pop(2)
        doc2.pages.insert(1, popped)

        assert len(doc2) == 1
        assert doc2.pages[1].extract_text() == "Page to move"

    def test_pop_error_empty_document(self):
        """pop raises IndexError on an empty document."""
        doc = Document()

        with pytest.raises(IndexError):
            doc.pages.pop()

    def test_pop_error_invalid_page_number(self):
        """pop raises IndexError for invalid page numbers."""
        doc = Document()
        doc.pages.add()
        doc.pages.add()

        # Page number 0 is invalid
        with pytest.raises(IndexError):
            doc.pages.pop(0)

        # Page number greater than count is invalid
        with pytest.raises(IndexError):
            doc.pages.pop(3)

        # Negative page number is invalid
        with pytest.raises(IndexError):
            doc.pages.pop(-1)

    def test_pop_disposed_document(self):
        """pop raises after document is disposed."""
        doc = Document()
        doc.pages.add()

        doc.dispose()

        with pytest.raises(Exception):
            doc.pages.pop()

    def test_pop_single_page(self):
        """pop works on a single-page document."""
        doc = Document()
        doc.pages.add()
        doc.pages[1].add_text("Only page", x=72, y=700, font_size=12)

        removed = doc.pages.pop()

        assert len(doc) == 0
        assert removed.extract_text() == "Only page"

    def test_pop_annotations_preserved(self):
        """pop preserves annotations on the removed page."""
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

        removed = doc.pages.pop(2)

        # The removed page should have the annotation
        assert len(removed.annotations) == 1

    def test_pop_multiple_times(self):
        """pop can be called multiple times."""
        doc = Document()
        for i in range(1, 6):
            doc.pages.add()
            doc.pages[i].add_text(f"Page {i}", x=72, y=700, font_size=12)

        # Pop pages in different orders
        popped_3 = doc.pages.pop(3)  # Pop page 3; pages now: 1, 2, 4, 5
        popped_1 = doc.pages.pop(1)  # Pop page 1; pages now: 2, 4, 5
        popped_last = doc.pages.pop()  # Pop last; pages now: 2, 4

        assert len(doc) == 2
        assert popped_3.extract_text() == "Page 3"
        assert popped_1.extract_text() == "Page 1"
        assert popped_last.extract_text() == "Page 5"

    def test_pop_in_context_manager(self):
        """pop works correctly in a context manager."""
        with Document() as doc:
            doc.pages.add()
            doc.pages.add()
            doc.pages.add()

            doc.pages[2].add_text("To be removed", x=72, y=700, font_size=12)
            removed = doc.pages.pop(2)

            assert len(doc) == 2
            assert removed.extract_text() == "To be removed"

        # Document should be disposed after context exits
        assert doc.closed

    def test_pop_from_file(self, tmp_path):
        """pop works on a document loaded from file."""
        # Create a test PDF
        doc = Document()
        for i in range(1, 4):
            doc.pages.add()
            doc.pages[i].add_text(f"Page {i}", x=72, y=700, font_size=12)

        test_file = tmp_path / "test.pdf"
        doc.save(test_file)

        # Load and pop a page
        loaded = Document.from_file(test_file)
        popped = loaded.pages.pop(2)

        assert len(loaded) == 2
        assert popped.extract_text() == "Page 2"

    def test_pop_preserves_page_properties(self):
        """pop preserves page properties like rotation."""
        doc = Document()
        doc.pages.add()
        doc.pages.add()
        doc.pages.add()

        # Set rotation on page 2
        doc.pages[2].rotation = 90

        removed = doc.pages.pop(2)

        assert removed.rotation == 90

    def test_pop_with_page_labels(self):
        """pop works with documents that have page labels."""
        from aspose_pdf import NumberingStyle, PageLabel

        doc = Document()
        doc.pages.add()
        doc.pages.add()
        doc.pages.add()

        # Set page labels
        doc.page_labels[0] = PageLabel(NumberingStyle.NUMERALS_ARABIC)
        doc.page_labels[1] = PageLabel(NumberingStyle.NUMERALS_ROMAN_LOWERCASE, "ii")
        doc.page_labels[2] = PageLabel(NumberingStyle.NUMERALS_ROMAN_LOWERCASE, "iii")

        # Check what we actually get for page 2
        page2_label = doc.pages[2].label

        removed = doc.pages.pop(2)

        assert len(doc) == 2
        # The removed page should have the label it had before removal
        assert removed.label == page2_label

    def test_pop_after_save(self, tmp_path):
        """pop works after saving a document."""
        doc = Document()
        doc.pages.add()
        doc.pages.add()
        doc.pages.add()

        test_file = tmp_path / "test.pdf"
        doc.save(test_file)

        # Reload
        reloaded = Document.from_file(test_file)
        removed = reloaded.pages.pop(2)

        assert len(reloaded) == 2
        assert removed is not None

    def test_pop_with_annotations(self, tmp_path):
        """pop preserves annotations on the removed page."""
        doc = Document()
        doc.pages.add()
        doc.pages.add()
        doc.pages.add()

        # Add various annotations to page 2
        quad_points = [(100, 700), (200, 700), (100, 650), (200, 650)]
        doc.pages[2].annotations.add_highlight(
            quads=quad_points,
            color=(1, 0, 0),
        )
        doc.pages[2].annotations.add_underline(
            quads=quad_points,
            color=(0, 1, 0),
        )

        removed = doc.pages.pop(2)

        # The removed page should have both annotations
        assert len(removed.annotations) == 2

    def test_pop_negative_index(self):
        """pop with negative index raises IndexError (no negative indexing)."""
        doc = Document()
        doc.pages.add()
        doc.pages.add()

        # Negative indices should raise IndexError (not supported like Python lists)
        with pytest.raises(IndexError):
            doc.pages.pop(-1)

    def test_pop_type_error(self):
        """pop raises TypeError for non-integer, non-None index."""
        doc = Document()
        doc.pages.add()

        with pytest.raises(TypeError):
            doc.pages.pop("invalid")

        with pytest.raises(TypeError):
            doc.pages.pop(1.5)
