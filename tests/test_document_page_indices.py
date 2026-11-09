"""Document.get_page_indices() resolves page range strings to one-based page numbers."""

from __future__ import annotations

import io

import pytest

from aspose_pdf import (
    Document,
    NumberingStyle,
    PageLabel,
    PdfLoadLimits,
    PdfResourceLimitException,
    PdfValidationException,
)


def _pdf(count=4):
    """Create a simple PDF with *count* pages."""
    with Document() as document:
        for index in range(count):
            document.pages.add(size=(24, 24)).add_text(f"P{index + 1}", 2, 12, font_size=6)
        with io.BytesIO() as output:
            document.save(output)
            return output.getvalue()


class TestGetPageIndices:
    """Tests for Document.get_page_indices()."""

    def test_single_page_number(self):
        """A single page number returns that page."""
        with Document(_pdf(5)) as document:
            indices = document.get_page_indices("3")
            assert indices == (3,)

    def test_range(self):
        """A range returns all pages in the range."""
        with Document(_pdf(5)) as document:
            indices = document.get_page_indices("2-4")
            assert indices == (2, 3, 4)

    def test_multiple_ranges_and_pages(self):
        """Comma-separated items are combined."""
        with Document(_pdf(10)) as document:
            indices = document.get_page_indices("1-3,7,10")
            assert indices == (1, 2, 3, 7, 10)

    def test_open_ended_range(self):
        """An open-ended range (3-) goes to the last page."""
        with Document(_pdf(5)) as document:
            indices = document.get_page_indices("3-")
            assert indices == (3, 4, 5)

    def test_odd_pages(self):
        """'odd' selects odd-numbered pages."""
        with Document(_pdf(6)) as document:
            indices = document.get_page_indices("odd")
            assert indices == (1, 3, 5)

    def test_even_pages(self):
        """'even' selects even-numbered pages."""
        with Document(_pdf(6)) as document:
            indices = document.get_page_indices("even")
            assert indices == (2, 4, 6)

    def test_last_keyword(self):
        """'last' refers to the final page."""
        with Document(_pdf(5)) as document:
            indices = document.get_page_indices("last")
            assert indices == (5,)

    def test_reverse_range(self):
        """A descending range reverses page order."""
        with Document(_pdf(5)) as document:
            indices = document.get_page_indices("4-2")
            assert indices == (4, 3, 2)

    def test_last_minus(self):
        """'last-N' works as expected."""
        with Document(_pdf(5)) as document:
            indices = document.get_page_indices("last-2")
            # last-2 means from page 5 (last) down to page 2
            assert indices == (5, 4, 3, 2)

    def test_repeated_pages(self):
        """Repeated pages are allowed."""
        with Document(_pdf(5)) as document:
            indices = document.get_page_indices("1,3,1,5")
            assert indices == (1, 3, 1, 5)

    def test_whitespace_ignored(self):
        """Whitespace around items is ignored."""
        with Document(_pdf(5)) as document:
            indices = document.get_page_indices(" 1 - 3 , 5 ")
            assert indices == (1, 2, 3, 5)

    def test_invalid_page_out_of_range(self):
        """Pages beyond document count are rejected."""
        with Document(_pdf(5)) as document:
            with pytest.raises(PdfValidationException, match="out of range"):
                document.get_page_indices("6")

    def test_invalid_empty_selection(self):
        """Empty page range raises validation exception."""
        with Document(_pdf(5)) as document:
            with pytest.raises(PdfValidationException):
                document.get_page_indices("")

    def test_invalid_syntax(self):
        """Invalid syntax is rejected."""
        with Document(_pdf(5)) as document:
            with pytest.raises(PdfValidationException):
                document.get_page_indices("1,,3")

    def test_empty_document(self):
        """An empty document has no valid page ranges."""
        with Document() as document:
            with pytest.raises(PdfValidationException, match="out of range"):
                document.get_page_indices("1")

    def test_empty_document_no_pages(self):
        """An empty document (0 pages) rejects any page range."""
        with Document() as document:
            with pytest.raises(PdfValidationException, match="out of range"):
                document.get_page_indices("1")

    def test_combined_with_extract_pages(self):
        """get_page_indices can be used before extract_pages to validate."""
        with Document(_pdf(5)) as document:
            # First validate the range
            indices = document.get_page_indices("1-3,5")
            assert indices == (1, 2, 3, 5)
            # Then extract using the same range
            selected = document.extract_pages("1-3,5")
            assert selected.page_count == 4

    def test_pagination_order_preserved(self):
        """Page order from the range string is preserved."""
        with Document(_pdf(10)) as document:
            indices = document.get_page_indices("5,1-3,7,10-8")
            assert indices == (5, 1, 2, 3, 7, 10, 9, 8)

    def test_with_resource_limits(self):
        """Resource limits are applied to range expansion."""
        limits = PdfLoadLimits(max_container_items=3)
        with Document(_pdf(10)) as document:
            document._load_limits = limits
            # This should pass (3 pages)
            indices = document.get_page_indices("1-3")
            assert indices == (1, 2, 3)
            # This should fail (expands to more items)
            with pytest.raises(PdfResourceLimitException, match="expanded page range"):
                document.get_page_indices("odd")


class TestGetPageIndicesWithPageLabels:
    """Tests that page labels don't affect get_page_indices behavior."""

    def test_page_labels_dont_change_indices(self):
        """Page labels are ignored; indices are based on physical page count."""
        with Document() as document:
            for i in range(5):
                document.pages.add()
            # Set custom page labels
            document.page_labels[0] = PageLabel(NumberingStyle.NUMERALS_ROMAN_LOWERCASE, "App-", 1)
            document.page_labels[2] = PageLabel(NumberingStyle.NUMERALS_ARABIC, "Doc-", 1)

            # Indices should still be 1-5 for a 5-page document
            indices = document.get_page_indices("1-3")
            assert indices == (1, 2, 3)

            indices = document.get_page_indices("odd")
            assert indices == (1, 3, 5)
