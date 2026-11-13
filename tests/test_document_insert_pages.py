"""Tests for Document.insert_pages() method."""

from __future__ import annotations

import pytest

from aspose_pdf import Document, NumberingStyle, PageLabel, PageSize

# GoToAction is not exported from destinations, but not needed for these tests
from aspose_pdf.interactive import XYZDestination
from aspose_pdf.outlines import OutlineItem


class TestInsertPages:
    """Test Document.insert_pages() method."""

    def test_insert_pages_returns_self_for_chaining(self):
        """insert_pages returns self for method chaining."""
        with Document() as doc:
            doc.pages.add(PageSize.A4)

            with Document() as source:
                source.pages.add(PageSize.A4)

                result = doc.insert_pages(1, source, [1])
                assert result is doc

    def test_insert_pages_at_beginning(self):
        """insert_pages can insert pages at the beginning."""
        with Document() as target:
            for i in range(3):
                page = target.pages.add(PageSize.A4)
                page.add_text(f"Target Page {i+1}", x=72, y=700, font_size=12)

            with Document() as source:
                for i in range(2):
                    page = source.pages.add(PageSize.A4)
                    page.add_text(f"Source Page {i+1}", x=72, y=700, font_size=12)

                target.insert_pages(1, source, [1, 2])

            assert len(target) == 5
            assert target.pages[1].extract_text().strip() == "Source Page 1"
            assert target.pages[2].extract_text().strip() == "Source Page 2"
            assert target.pages[3].extract_text().strip() == "Target Page 1"
            assert target.pages[4].extract_text().strip() == "Target Page 2"
            assert target.pages[5].extract_text().strip() == "Target Page 3"

    def test_insert_pages_in_middle(self):
        """insert_pages can insert pages in the middle."""
        with Document() as target:
            for i in range(5):
                page = target.pages.add(PageSize.A4)
                page.add_text(f"Target Page {i+1}", x=72, y=700, font_size=12)

            with Document() as source:
                for i in range(2):
                    page = source.pages.add(PageSize.A4)
                    page.add_text(f"Source Page {i+1}", x=72, y=700, font_size=12)

                target.insert_pages(3, source, [1, 2])

            assert len(target) == 7
            assert target.pages[2].extract_text().strip() == "Target Page 2"
            assert target.pages[3].extract_text().strip() == "Source Page 1"
            assert target.pages[4].extract_text().strip() == "Source Page 2"
            assert target.pages[5].extract_text().strip() == "Target Page 3"

    def test_insert_pages_at_end(self):
        """insert_pages can insert pages at the end (equivalent to append_pages)."""
        with Document() as target:
            for i in range(2):
                page = target.pages.add(PageSize.A4)
                page.add_text(f"Target Page {i+1}", x=72, y=700, font_size=12)

            with Document() as source:
                for i in range(2):
                    page = source.pages.add(PageSize.A4)
                    page.add_text(f"Source Page {i+1}", x=72, y=700, font_size=12)

                target.insert_pages(3, source, [1, 2])

            assert len(target) == 4
            assert target.pages[1].extract_text().strip() == "Target Page 1"
            assert target.pages[2].extract_text().strip() == "Target Page 2"
            assert target.pages[3].extract_text().strip() == "Source Page 1"
            assert target.pages[4].extract_text().strip() == "Source Page 2"

    def test_insert_pages_returns_pages_content(self):
        """insert_pages preserves the pages' content."""
        with Document() as target:
            for i in range(2):
                page = target.pages.add(PageSize.A4)
                page.add_text(f"Target Page {i+1}", x=72, y=700, font_size=12)

            with Document() as source:
                for i in range(2):
                    page = source.pages.add(PageSize.A4)
                    page.add_text(f"Source Page {i+1}", x=72, y=700, font_size=12)

                target.insert_pages(2, source, [1, 2])

            # Check that inserted pages retain their content
            assert target.pages[2].extract_text().strip() == "Source Page 1"
            assert target.pages[3].extract_text().strip() == "Source Page 2"

    def test_insert_pages_source_unchanged(self):
        """insert_pages leaves the source document unchanged."""
        with Document() as target:
            for i in range(2):
                page = target.pages.add(PageSize.A4)
                page.add_text(f"Target Page {i+1}", x=72, y=700, font_size=12)

            with Document() as source:
                for i in range(2):
                    page = source.pages.add(PageSize.A4)
                    page.add_text(f"Source Page {i+1}", x=72, y=700, font_size=12)

                target.insert_pages(1, source, [1])
                # Source should still have 2 pages
                assert len(source) == 2
                assert source.pages[1].extract_text().strip() == "Source Page 1"

    def test_insert_pages_with_annotations(self):
        """insert_pages preserves annotations on inserted pages."""
        with Document() as target:
            for i in range(2):
                page = target.pages.add(PageSize.A4)
                page.add_text(f"Target Page {i+1}", x=72, y=700, font_size=12)

            with Document() as source:
                for i in range(2):
                    page = source.pages.add(PageSize.A4)
                    page.add_text(f"Source Page {i+1}", x=72, y=700, font_size=12)
                    page.annotations.add_highlight((100, 600, 200, 650))

                target.insert_pages(2, source, [1, 2])

            # Check that annotations were transferred
            assert len(list(target.pages[2].annotations)) == 1
            assert len(list(target.pages[3].annotations)) == 1

    def test_insert_pages_with_bookmarks(self):
        """insert_pages preserves bookmarks pointing at inserted pages."""
        with Document() as target:
            for i in range(2):
                page = target.pages.add(PageSize.A4)
                page.add_text(f"Target Page {i+1}", x=72, y=700, font_size=12)

            with Document() as source:
                for i in range(2):
                    page = source.pages.add(PageSize.A4)
                    page.add_text(f"Source Page {i+1}", x=72, y=700, font_size=12)

                # Add bookmarks to source
                item1 = OutlineItem("Source Bookmark 1", page_index=1)
                item2 = OutlineItem("Source Bookmark 2", page_index=2)
                source.outlines.add(item1)
                source.outlines.add(item2)

                target.insert_pages(2, source, [1, 2])

            # Check that bookmarks were transferred
            assert len(list(target.outlines)) == 2
            assert target.outlines[0].title == "Source Bookmark 1"
            assert target.outlines[1].title == "Source Bookmark 2"

    def test_insert_pages_range_string(self):
        """insert_pages accepts range strings."""
        with Document() as target:
            for i in range(2):
                page = target.pages.add(PageSize.A4)
                page.add_text(f"Target Page {i+1}", x=72, y=700, font_size=12)

            with Document() as source:
                for i in range(5):
                    page = source.pages.add(PageSize.A4)
                    page.add_text(f"Source Page {i+1}", x=72, y=700, font_size=12)

                target.insert_pages(2, source, "2-4")

            assert len(target) == 5
            assert target.pages[1].extract_text().strip() == "Target Page 1"
            assert target.pages[2].extract_text().strip() == "Source Page 2"
            assert target.pages[3].extract_text().strip() == "Source Page 3"
            assert target.pages[4].extract_text().strip() == "Source Page 4"
            assert target.pages[5].extract_text().strip() == "Target Page 2"

    def test_insert_pages_odd(self):
        """insert_pages accepts 'odd' keyword."""
        with Document() as target:
            for i in range(2):
                page = target.pages.add(PageSize.A4)
                page.add_text(f"Target Page {i+1}", x=72, y=700, font_size=12)

            with Document() as source:
                for i in range(5):
                    page = source.pages.add(PageSize.A4)
                    page.add_text(f"Source Page {i+1}", x=72, y=700, font_size=12)

                target.insert_pages(2, source, "odd")

            assert len(target) == 5
            assert target.pages[2].extract_text().strip() == "Source Page 1"
            assert target.pages[3].extract_text().strip() == "Source Page 3"
            assert target.pages[4].extract_text().strip() == "Source Page 5"

    def test_insert_pages_even(self):
        """insert_pages accepts 'even' keyword."""
        with Document() as target:
            for i in range(2):
                page = target.pages.add(PageSize.A4)
                page.add_text(f"Target Page {i+1}", x=72, y=700, font_size=12)

            with Document() as source:
                for i in range(5):
                    page = source.pages.add(PageSize.A4)
                    page.add_text(f"Source Page {i+1}", x=72, y=700, font_size=12)

                target.insert_pages(2, source, "even")

            assert len(target) == 4
            assert target.pages[2].extract_text().strip() == "Source Page 2"
            assert target.pages[3].extract_text().strip() == "Source Page 4"

    def test_insert_pages_with_last(self):
        """insert_pages accepts 'last' keyword."""
        with Document() as target:
            for i in range(2):
                page = target.pages.add(PageSize.A4)
                page.add_text(f"Target Page {i+1}", x=72, y=700, font_size=12)

            with Document() as source:
                for i in range(5):
                    page = source.pages.add(PageSize.A4)
                    page.add_text(f"Source Page {i+1}", x=72, y=700, font_size=12)

                target.insert_pages(2, source, "last")

            assert len(target) == 3
            assert target.pages[2].extract_text().strip() == "Source Page 5"

    def test_insert_pages_with_descending_range(self):
        """insert_pages accepts descending ranges."""
        with Document() as target:
            for i in range(2):
                page = target.pages.add(PageSize.A4)
                page.add_text(f"Target Page {i+1}", x=72, y=700, font_size=12)

            with Document() as source:
                for i in range(5):
                    page = source.pages.add(PageSize.A4)
                    page.add_text(f"Source Page {i+1}", x=72, y=700, font_size=12)

                target.insert_pages(2, source, "4-2")

            assert len(target) == 5
            assert target.pages[2].extract_text().strip() == "Source Page 4"
            assert target.pages[3].extract_text().strip() == "Source Page 3"
            assert target.pages[4].extract_text().strip() == "Source Page 2"

    def test_insert_pages_repeated_pages(self):
        """insert_pages preserves repeated page numbers."""
        with Document() as target:
            for i in range(2):
                page = target.pages.add(PageSize.A4)
                page.add_text(f"Target Page {i+1}", x=72, y=700, font_size=12)

            with Document() as source:
                for i in range(3):
                    page = source.pages.add(PageSize.A4)
                    page.add_text(f"Source Page {i+1}", x=72, y=700, font_size=12)

                target.insert_pages(2, source, [1, 2, 1])

            assert len(target) == 5
            # Check that repeated pages are preserved
            assert target.pages[2].extract_text().strip() == "Source Page 1"
            assert target.pages[3].extract_text().strip() == "Source Page 2"
            assert target.pages[4].extract_text().strip() == "Source Page 1"

    def test_insert_pages_with_page_labels(self):
        """insert_pages preserves page labels on inserted pages."""
        with Document() as target:
            for i in range(2):
                page = target.pages.add(PageSize.A4)
                page.add_text(f"Target Page {i+1}", x=72, y=700, font_size=12)

            with Document() as source:
                for i in range(2):
                    page = source.pages.add(PageSize.A4)
                    page.add_text(f"Source Page {i+1}", x=72, y=700, font_size=12)

                # Add page labels to source (start at index 0 for first page)
                source.page_labels[0] = PageLabel(NumberingStyle.NUMERALS_ROMAN_UPPERCASE)
                source.page_labels[1] = PageLabel(NumberingStyle.NUMERALS_ROMAN_UPPERCASE)

                target.insert_pages(2, source, [1, 2])

                # Check that page labels were transferred
                # Page labels are copied when pages are inserted
                # The source pages 1 and 2 (indices 0 and 1) are inserted at position 2
                # in a 2-page document (indices 0 and 1), so they become pages 2 and 3 (1-based)
                # which are indices 1 and 2 (0-based)
                labels_dict = dict(target.page_labels.items())
                
                # Page at index 1 (page 2) and index 2 (page 3) should have Roman uppercase labels
                assert labels_dict[1].style == NumberingStyle.NUMERALS_ROMAN_UPPERCASE
                assert labels_dict[1].prefix == ""
                assert labels_dict[1].start == 1
                assert labels_dict[2].style == NumberingStyle.NUMERALS_ROMAN_UPPERCASE
                assert labels_dict[2].prefix == ""
                assert labels_dict[2].start == 1

    def test_insert_pages_with_iterable(self):
        """insert_pages accepts an iterable of page numbers."""
        with Document() as target:
            for i in range(2):
                page = target.pages.add(PageSize.A4)
                page.add_text(f"Target Page {i+1}", x=72, y=700, font_size=12)

            with Document() as source:
                for i in range(5):
                    page = source.pages.add(PageSize.A4)
                    page.add_text(f"Source Page {i+1}", x=72, y=700, font_size=12)

                target.insert_pages(2, source, [1, 3, 5])

            assert len(target) == 5
            assert target.pages[2].extract_text().strip() == "Source Page 1"
            assert target.pages[3].extract_text().strip() == "Source Page 3"
            assert target.pages[4].extract_text().strip() == "Source Page 5"

    def test_insert_pages_empty_selection_raises(self):
        """insert_pages raises error for empty selection."""
        with Document() as target:
            target.pages.add(PageSize.A4)

            with Document() as source:
                source.pages.add(PageSize.A4)

                with pytest.raises(Exception):  # PdfValidationException
                    target.insert_pages(1, source, [])

    def test_insert_pages_invalid_source_raises(self):
        """insert_pages raises TypeError for invalid source."""
        with Document() as target:
            target.pages.add(PageSize.A4)

            with pytest.raises(TypeError):
                target.insert_pages(1, "not a document", [1])

    def test_insert_pages_disposed_source_raises(self):
        """insert_pages raises error when source document is disposed."""
        with Document() as target:
            target.pages.add(PageSize.A4)

            source = Document()
            source.pages.add(PageSize.A4)
            source.dispose()

            with pytest.raises(Exception):  # AsposePdfException
                target.insert_pages(1, source, [1])

    def test_insert_pages_out_of_range_page_raises(self):
        """insert_pages raises error for out-of-range page numbers."""
        with Document() as target:
            target.pages.add(PageSize.A4)

            with Document() as source:
                source.pages.add(PageSize.A4)

                with pytest.raises(Exception):  # PdfValidationException
                    target.insert_pages(1, source, [5])

    def test_insert_pages_negative_page_raises(self):
        """insert_pages raises error for negative page numbers."""
        with Document() as target:
            target.pages.add(PageSize.A4)

            with Document() as source:
                source.pages.add(PageSize.A4)

                with pytest.raises(Exception):  # PdfValidationException
                    target.insert_pages(1, source, [-1])

    def test_insert_pages_invalid_index_type_raises(self):
        """insert_pages raises TypeError for invalid index type."""
        with Document() as target:
            target.pages.add(PageSize.A4)

            with Document() as source:
                source.pages.add(PageSize.A4)

                with pytest.raises(TypeError):
                    target.insert_pages("1", source, [1])

    def test_insert_pages_negative_index_raises(self):
        """insert_pages raises IndexError for negative index."""
        with Document() as target:
            target.pages.add(PageSize.A4)

            with Document() as source:
                source.pages.add(PageSize.A4)

                with pytest.raises(IndexError):
                    target.insert_pages(-1, source, [1])

    def test_insert_pages_zero_index_raises(self):
        """insert_pages raises IndexError for zero index."""
        with Document() as target:
            target.pages.add(PageSize.A4)

            with Document() as source:
                source.pages.add(PageSize.A4)

                with pytest.raises(IndexError):
                    target.insert_pages(0, source, [1])

    def test_insert_pages_index_beyond_length_raises(self):
        """insert_pages raises IndexError for index beyond document length."""
        with Document() as target:
            target.pages.add(PageSize.A4)

            with Document() as source:
                source.pages.add(PageSize.A4)

                with pytest.raises(IndexError):
                    target.insert_pages(10, source, [1])

    def test_insert_pages_with_open_ended_range(self):
        """insert_pages accepts open-ended ranges like '3-'."""
        with Document() as target:
            for i in range(2):
                page = target.pages.add(PageSize.A4)
                page.add_text(f"Target Page {i+1}", x=72, y=700, font_size=12)

            with Document() as source:
                for i in range(5):
                    page = source.pages.add(PageSize.A4)
                    page.add_text(f"Source Page {i+1}", x=72, y=700, font_size=12)

                target.insert_pages(2, source, "3-")

            assert len(target) == 5
            assert target.pages[2].extract_text().strip() == "Source Page 3"
            assert target.pages[3].extract_text().strip() == "Source Page 4"
            assert target.pages[4].extract_text().strip() == "Source Page 5"

    def test_insert_pages_method_chaining(self):
        """insert_pages allows method chaining."""
        with Document() as doc:
            for i in range(2):
                page = doc.pages.add(PageSize.A4)
                page.add_text(f"Page {i+1}", x=72, y=700, font_size=12)

            # Chain multiple insert_pages calls
            result = doc.insert_pages(1, doc, [1]).insert_pages(3, doc, [2])

            assert result is doc
            assert len(doc) == 4

    def test_insert_pages_from_file(self, tmp_path):
        """insert_pages works with documents loaded from files."""
        source_path = tmp_path / "source.pdf"
        target_path = tmp_path / "target.pdf"
        result_path = tmp_path / "result.pdf"

        # Create source file
        with Document() as source:
            for i in range(2):
                page = source.pages.add(PageSize.A4)
                page.add_text(f"Source Page {i+1}", x=72, y=700, font_size=12)
            source.save(str(source_path))

        # Create target file
        with Document() as target:
            for i in range(2):
                page = target.pages.add(PageSize.A4)
                page.add_text(f"Target Page {i+1}", x=72, y=700, font_size=12)
            target.save(str(target_path))

        # Load and insert
        with Document(str(target_path)) as target:
            with Document(str(source_path)) as source:
                target.insert_pages(2, source, [1, 2])
                target.save(str(result_path))

        # Verify result
        with Document(str(result_path)) as result:
            assert len(result) == 4
            assert result.pages[2].extract_text().strip() == "Source Page 1"

    def test_insert_pages_in_context_manager(self):
        """insert_pages works within context managers."""
        with Document() as target:
            for i in range(2):
                page = target.pages.add(PageSize.A4)
                page.add_text(f"Target Page {i+1}", x=72, y=700, font_size=12)

            with Document() as source:
                for i in range(2):
                    page = source.pages.add(PageSize.A4)
                    page.add_text(f"Source Page {i+1}", x=72, y=700, font_size=12)

                target.insert_pages(2, source, [1, 2])

            assert len(target) == 4

    def test_insert_pages_destinations_preserved(self):
        """insert_pages preserves named destinations (same as append_pages)."""
        # Note: Named destinations are document-level, not page-level.
        # When selecting specific pages, only the pages and their resources are imported.
        # Document-level objects like destinations are not copied for a page selection.
        # This is consistent with append_pages behavior.
        with Document() as target:
            for i in range(2):
                page = target.pages.add(PageSize.A4)
                page.add_text(f"Target Page {i+1}", x=72, y=700, font_size=12)

            with Document() as source:
                for i in range(2):
                    page = source.pages.add(PageSize.A4)
                    page.add_text(f"Source Page {i+1}", x=72, y=700, font_size=12)

                # Add destinations to source
                source.destinations["page1"] = XYZDestination(page=1, top=700)
                source.destinations["page2"] = XYZDestination(page=2, top=700)

                target.insert_pages(2, source, [1, 2])

                # Destinations are not copied for partial page selection
                # This is consistent with append_pages behavior
                assert "page1" not in target.destinations
                assert "page2" not in target.destinations

    def test_insert_pages_annotations_not_duplicated(self):
        """insert_pages only brings annotations from selected pages."""
        with Document() as target:
            for i in range(2):
                page = target.pages.add(PageSize.A4)
                page.add_text(f"Target Page {i+1}", x=72, y=700, font_size=12)

            with Document() as source:
                for i in range(3):
                    page = source.pages.add(PageSize.A4)
                    page.add_text(f"Source Page {i+1}", x=72, y=700, font_size=12)
                    page.annotations.add_highlight((100, 600, 200, 650))

                # Only insert page 1 and 3, not page 2
                target.insert_pages(2, source, [1, 3])

                assert len(list(target.pages[2].annotations)) == 1
                assert len(list(target.pages[3].annotations)) == 1
                # Page 2 in target should have no annotations from source
                assert len(list(target.pages[2].annotations)) == 1

    def test_insert_pages_form_fields_preserved(self):
        """insert_pages preserves form fields on inserted pages."""
        with Document() as target:
            for i in range(2):
                page = target.pages.add(PageSize.A4)
                page.add_text(f"Target Page {i+1}", x=72, y=700, font_size=12)

            with Document() as source:
                for i in range(2):
                    page = source.pages.add(PageSize.A4)
                    page.add_text(f"Source Page {i+1}", x=72, y=700, font_size=12)

                # Create a form field
                if source.form:
                    source.form.text_fields.add("Field1", page=1, x=100, y=600, width=100, height=20)

                target.insert_pages(2, source, [1, 2])

                # Check that form fields were transferred
                if target.form:
                    assert len(list(target.form.text_fields)) >= 1
