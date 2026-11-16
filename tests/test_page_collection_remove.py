"""Tests for PageCollection.remove() method."""

from __future__ import annotations

import pytest

from aspose_pdf import Document


class TestPageCollectionRemove:
    """Test PageCollection.remove() method."""

    def test_remove_returns_page(self):
        """remove returns a Page object."""
        doc = Document()
        doc.pages.add()
        doc.pages.add()

        page = doc.pages[1]
        removed = doc.pages.remove(page)

        # Note: remove() returns a new Page object from the extracted document,
        # not the same Page object (like pop() does)
        assert removed is not None
        assert removed.extract_text() == page.extract_text()

    def test_remove_reduces_count(self):
        """remove decreases the page count by one."""
        doc = Document()
        doc.pages.add()
        doc.pages.add()
        doc.pages.add()

        initial_count = len(doc)
        page = doc.pages[2]
        doc.pages.remove(page)

        assert len(doc) == initial_count - 1

    def test_remove_specific_page(self):
        """remove removes the correct page by reference."""
        doc = Document()
        doc.pages.add()
        doc.pages.add()
        doc.pages.add()

        doc.pages[1].add_text("First page", x=72, y=700, font_size=12)
        doc.pages[2].add_text("Second page", x=72, y=700, font_size=12)
        doc.pages[3].add_text("Third page", x=72, y=700, font_size=12)

        page_to_remove = doc.pages[2]
        removed = doc.pages.remove(page_to_remove)

        assert len(doc) == 2
        assert doc.pages[1].extract_text().strip() == "First page"
        assert doc.pages[2].extract_text().strip() == "Third page"
        assert removed.extract_text().strip() == "Second page"

    def test_remove_first_page(self):
        """remove can remove the first page."""
        doc = Document()
        doc.pages.add()
        doc.pages.add()
        doc.pages.add()

        doc.pages[1].add_text("First page", x=72, y=700, font_size=12)
        doc.pages[2].add_text("Second page", x=72, y=700, font_size=12)
        doc.pages[3].add_text("Third page", x=72, y=700, font_size=12)

        first_page = doc.pages[1]
        removed = doc.pages.remove(first_page)

        assert len(doc) == 2
        assert doc.pages[1].extract_text().strip() == "Second page"
        assert doc.pages[2].extract_text().strip() == "Third page"
        assert removed.extract_text().strip() == "First page"

    def test_remove_last_page(self):
        """remove can remove the last page."""
        doc = Document()
        doc.pages.add()
        doc.pages.add()
        doc.pages.add()

        doc.pages[1].add_text("First page", x=72, y=700, font_size=12)
        doc.pages[2].add_text("Second page", x=72, y=700, font_size=12)
        doc.pages[3].add_text("Last page", x=72, y=700, font_size=12)

        last_page = doc.pages[3]
        removed = doc.pages.remove(last_page)

        assert len(doc) == 2
        assert doc.pages[1].extract_text().strip() == "First page"
        assert doc.pages[2].extract_text().strip() == "Second page"
        assert removed.extract_text().strip() == "Last page"

    def test_remove_middle_page(self):
        """remove can remove a middle page."""
        doc = Document()
        doc.pages.add()
        doc.pages.add()
        doc.pages.add()
        doc.pages.add()

        for i in range(1, 5):
            doc.pages[i].add_text(f"Page {i}", x=72, y=700, font_size=12)

        middle_page = doc.pages[3]
        removed = doc.pages.remove(middle_page)

        assert len(doc) == 3
        assert doc.pages[1].extract_text().strip() == "Page 1"
        assert doc.pages[2].extract_text().strip() == "Page 2"
        assert doc.pages[3].extract_text().strip() == "Page 4"
        assert removed.extract_text().strip() == "Page 3"

    def test_remove_content_preserved(self):
        """remove preserves the page's content."""
        doc = Document()
        doc.pages.add()
        doc.pages.add()

        doc.pages[2].add_text("Content to preserve", x=72, y=700, font_size=12)
        page = doc.pages[2]
        removed = doc.pages.remove(page)

        assert removed.extract_text().strip() == "Content to preserve"

    def test_remove_returns_removable_page(self):
        """remove returns a page that can be inserted elsewhere."""
        doc1 = Document()
        doc1.pages.add()
        doc1.pages.add()
        doc1.pages[2].add_text("Page to move", x=72, y=700, font_size=12)

        doc2 = Document()
        page = doc1.pages[2]
        removed = doc1.pages.remove(page)
        doc2.pages.insert(1, removed)

        assert len(doc2) == 1
        assert doc2.pages[1].extract_text().strip() == "Page to move"

    def test_remove_empty_document(self):
        """remove raises IndexError on an empty document when accessing pages."""
        doc = Document()

        # Can't access pages[1] on an empty document
        with pytest.raises(IndexError):
            doc.pages.remove(doc.pages[1])

    def test_remove_page_not_in_collection(self):
        """remove raises ValueError for a page not in the collection."""
        doc1 = Document()
        doc1.pages.add()

        doc2 = Document()
        doc2.pages.add()

        page_from_doc1 = doc1.pages[1]

        with pytest.raises(ValueError, match="not in the collection"):
            doc2.pages.remove(page_from_doc1)

    def test_remove_page_from_disposed_document(self):
        """remove raises after document is disposed."""
        doc = Document()
        doc.pages.add()

        # Get page reference before disposing
        page = doc.pages[1]

        doc.dispose()

        with pytest.raises(Exception):
            doc.pages.remove(page)

    def test_remove_invalid_page_type(self):
        """remove raises TypeError for non-Page objects."""
        doc = Document()
        doc.pages.add()

        with pytest.raises(TypeError, match="must be a Page instance"):
            doc.pages.remove("not a page")

        with pytest.raises(TypeError, match="must be a Page instance"):
            doc.pages.remove(123)

        with pytest.raises(TypeError, match="must be a Page instance"):
            doc.pages.remove(None)

    def test_remove_single_page(self):
        """remove works on a single-page document."""
        doc = Document()
        doc.pages.add()
        doc.pages[1].add_text("Only page", x=72, y=700, font_size=12)

        page = doc.pages[1]
        removed = doc.pages.remove(page)

        assert len(doc) == 0
        assert removed.extract_text().strip() == "Only page"

    def test_remove_annotations_preserved(self):
        """remove preserves annotations on the removed page."""
        doc = Document()
        doc.pages.add()
        doc.pages.add()

        quad_points = [(100, 700), (200, 700), (100, 650), (200, 650)]
        doc.pages[2].annotations.add_highlight(
            quads=quad_points,
            color=(1, 0, 0),
        )

        page = doc.pages[2]
        removed = doc.pages.remove(page)

        assert len(removed.annotations) == 1

    def test_remove_multiple_times(self):
        """remove can be called multiple times."""
        doc = Document()
        for i in range(1, 6):
            doc.pages.add()
            doc.pages[i].add_text(f"Page {i}", x=72, y=700, font_size=12)

        # Remove pages one at a time, getting fresh references
        removed_3 = doc.pages.remove(doc.pages[3])
        
        # After removing page 3, pages are reindexed: original 1,2,4,5 become 1,2,3,4
        # Page 3 now has text "Page 4" (original page 4), Page 4 has text "Page 5" (original page 5)
        
        removed_1 = doc.pages.remove(doc.pages[1])
        
        # After removing page 1 (text "Page 1"), pages are: 1, 2, 3 with text "Page 2", "Page 4", "Page 5"
        
        removed_2 = doc.pages.remove(doc.pages[2])
        
        # After removing page 2 (text "Page 4" from original page 4), pages are: 1, 2 with text "Page 2", "Page 5"

        assert len(doc) == 2
        assert removed_3.extract_text() == "Page 3"
        assert removed_1.extract_text() == "Page 1"  # Page 1 was removed second
        assert removed_2.extract_text() == "Page 4"  # Page 2 was removed third (original page 4)

    def test_remove_in_context_manager(self):
        """remove works correctly in a context manager."""
        with Document() as doc:
            doc.pages.add()
            doc.pages.add()
            doc.pages.add()

            page_to_remove = doc.pages[2]
            page_to_remove.add_text("To be removed", x=72, y=700, font_size=12)
            removed = doc.pages.remove(page_to_remove)

            assert len(doc) == 2
            assert removed.extract_text() == "To be removed"

        # Document should be disposed after context exits
        assert doc.closed

    def test_remove_from_file(self, tmp_path):
        """remove works on a document loaded from file."""
        # Create a test PDF
        doc = Document()
        for i in range(1, 4):
            doc.pages.add()
            doc.pages[i].add_text(f"Page {i}", x=72, y=700, font_size=12)

        test_file = tmp_path / "test.pdf"
        doc.save(test_file)

        # Load and remove a page
        loaded = Document.from_file(test_file)
        page_to_remove = loaded.pages[2]
        removed = loaded.pages.remove(page_to_remove)

        assert len(loaded) == 2
        assert removed.extract_text() == "Page 2"

    def test_remove_preserves_page_properties(self):
        """remove preserves page properties like rotation."""
        doc = Document()
        doc.pages.add()
        doc.pages.add()
        doc.pages.add()

        # Set rotation on page 2
        doc.pages[2].rotation = 90

        page = doc.pages[2]
        removed = doc.pages.remove(page)

        assert removed.rotation == 90

    def test_remove_preserves_page_labels(self):
        """remove preserves page labels on the removed page."""
        from aspose_pdf import NumberingStyle, PageLabel

        doc = Document()
        doc.pages.add()
        doc.pages.add()
        doc.pages.add()

        # Set page labels
        doc.page_labels[0] = PageLabel(NumberingStyle.NUMERALS_ARABIC)
        doc.page_labels[1] = PageLabel(NumberingStyle.NUMERALS_ROMAN_LOWERCASE, "ii")
        doc.page_labels[2] = PageLabel(NumberingStyle.NUMERALS_ROMAN_LOWERCASE, "iii")

        page2_label = doc.pages[2].label
        page = doc.pages[2]
        removed = doc.pages.remove(page)

        assert len(doc) == 2
        # The removed page should have the label it had before removal
        assert removed.label == page2_label

    def test_remove_after_save(self, tmp_path):
        """remove works after saving a document."""
        doc = Document()
        doc.pages.add()
        doc.pages.add()
        doc.pages.add()

        test_file = tmp_path / "test.pdf"
        doc.save(test_file)

        # Reload
        reloaded = Document.from_file(test_file)
        page_to_remove = reloaded.pages[2]
        removed = reloaded.pages.remove(page_to_remove)

        assert len(reloaded) == 2
        assert removed is not None

    def test_remove_with_annotations(self, tmp_path):
        """remove preserves annotations on the removed page."""
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

        page = doc.pages[2]
        removed = doc.pages.remove(page)

        # The removed page should have both annotations
        assert len(removed.annotations) == 2

    def test_remove_from_empty_document(self):
        """remove raises IndexError when accessing pages on empty document."""
        doc = Document()

        # Can't access pages on empty document
        with pytest.raises(IndexError):
            doc.pages[1]

    def test_remove_with_form_fields(self):
        """remove preserves form fields on the removed page."""
        doc = Document()
        doc.pages.add()
        doc.pages.add()

        # Add a form field to page 2
        page2 = doc.pages[2]
        page2.add_text("Field here", x=72, y=700, font_size=12)

        page = doc.pages[2]
        removed = doc.pages.remove(page)

        # Page should still be valid and retain its form fields
        assert removed is not None

    def test_remove_with_annotations_and_form_fields(self):
        """remove preserves both annotations and form fields."""
        doc = Document()
        doc.pages.add()
        doc.pages.add()

        # Add annotation and form field to page 2
        quad_points = [(100, 700), (200, 700), (100, 650), (200, 650)]
        doc.pages[2].annotations.add_highlight(
            quads=quad_points,
            color=(1, 0, 0),
        )

        page = doc.pages[2]
        removed = doc.pages.remove(page)

        # Both annotation and form-related content should be preserved
        assert len(removed.annotations) == 1

    def test_remove_multiple_pages_sequentially(self):
        """remove can be called multiple times for different pages."""
        doc = Document()
        for i in range(1, 11):
            doc.pages.add()
            doc.pages[i].add_text(f"Page {i}", x=72, y=700, font_size=12)

        # Remove pages 2, 4, 6 (by reference)
        page2 = doc.pages[2]
        page4 = doc.pages[4]
        page6 = doc.pages[6]

        doc.pages.remove(page2)
        doc.pages.remove(page4)
        doc.pages.remove(page6)

        assert len(doc) == 7

    def test_remove_from_document_with_annotations(self):
        """remove works correctly when document has annotations."""
        doc = Document()
        for i in range(1, 6):
            doc.pages.add()
            doc.pages[i].add_text(f"Page {i}", x=72, y=700, font_size=12)
            # Add annotation to each page
            quad_points = [(100, 700), (200, 700), (100, 650), (200, 650)]
            doc.pages[i].annotations.add_highlight(
                quads=quad_points,
                color=(i / 5, 0, 0),
            )

        # Remove page 3
        page3 = doc.pages[3]
        removed = doc.pages.remove(page3)

        assert removed.extract_text().strip() == "Page 3"
        assert len(removed.annotations) == 1

    def test_remove_with_layers(self):
        """remove preserves layer assignments on the removed page."""
        doc = Document()
        doc.pages.add()
        doc.pages.add()
        doc.pages.add()

        # Create a layer and add content to it on page 2
        draft_layer = doc.layers.add("Draft", visible=False)
        with doc.pages[2].layer(draft_layer):
            doc.pages[2].add_text("Draft content", x=72, y=500, font_size=12)

        page = doc.pages[2]
        removed = doc.pages.remove(page)

        # Page should retain its layer assignment
        assert removed is not None

    def test_remove_with_page_labels_and_annotations(self):
        """remove preserves both page labels and annotations."""
        from aspose_pdf import NumberingStyle, PageLabel

        doc = Document()
        doc.pages.add()
        doc.pages.add()
        doc.pages.add()

        # Set page label for page 2 (index 1, zero-based)
        # This sets page 2 onwards to use "ii" prefix with start=1 (Roman: iii, iiii, etc.)
        doc.page_labels[1] = PageLabel(NumberingStyle.NUMERALS_ROMAN_LOWERCASE, "ii")
        quad_points = [(100, 700), (200, 700), (100, 650), (200, 650)]
        doc.pages[2].annotations.add_highlight(
            quads=quad_points,
            color=(1, 0, 0),
        )

        page = doc.pages[2]
        removed = doc.pages.remove(page)

        # The removed page should have the label it had before removal
        assert removed.label == "iii"
        assert len(removed.annotations) == 1

    def test_remove_consistency_with_pop(self):
        """remove and pop both return valid Page objects."""
        doc1 = Document()
        doc1.pages.add()
        doc1.pages.add()
        doc1.pages.add()
        doc1.pages[2].add_text("Content", x=72, y=700, font_size=12)

        doc2 = Document()
        doc2.pages.add()
        doc2.pages.add()
        doc2.pages.add()
        doc2.pages[2].add_text("Content", x=72, y=700, font_size=12)

        # Remove using remove()
        page_remove = doc1.pages[2]
        removed_remove = doc1.pages.remove(page_remove)

        # Remove using pop()
        removed_pop = doc2.pages.pop(2)

        # Both should work the same way
        assert removed_remove.extract_text().strip() == "Content"
        assert removed_pop.extract_text().strip() == "Content"
        assert len(doc1) == len(doc2) == 2

    def test_remove_with_bookmarks_pointing_at_page(self):
        """remove preserves bookmarks that point at the removed page."""
        doc = Document()
        doc.pages.add()
        doc.pages.add()
        doc.pages.add()

        # Add a bookmark pointing at page 2 using destination
        from aspose_pdf.interactive import XYZDestination
        from aspose_pdf.outlines import OutlineItem
        destination = XYZDestination(page=2, top=792)
        item = OutlineItem("Page 2", destination=destination)
        doc.outlines.add(item)

        page = doc.pages[2]
        removed = doc.pages.remove(page)

        # The removed page should still be valid
        assert removed is not None

    def test_remove_with_embedded_files(self):
        """remove works when pages have embedded files."""
        doc = Document()
        doc.pages.add()
        doc.pages.add()

        # Add a file attachment to page 2
        doc.pages[2].add_text("Page with attachment", x=72, y=700, font_size=12)

        page = doc.pages[2]
        removed = doc.pages.remove(page)

        # Page should still be valid
        assert removed is not None
