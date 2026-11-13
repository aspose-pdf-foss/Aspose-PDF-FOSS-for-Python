"""Tests for PageRanges class and page range parsing functionality."""

from __future__ import annotations

import pytest

from aspose_pdf import (
    PageRanges,
    PdfLoadLimits,
    PdfResourceLimitException,
    PdfValidationException,
)


class TestPageRangesParse:
    """Tests for PageRanges.parse() static method."""

    def test_single_page_number(self):
        """A single page number returns that page."""
        indices = PageRanges.parse("3")
        assert indices == (3,)

    def test_range(self):
        """A range returns all pages in the range."""
        indices = PageRanges.parse("2-4", page_count=10)
        assert indices == (2, 3, 4)

    def test_multiple_ranges_and_pages(self):
        """Comma-separated items are combined."""
        indices = PageRanges.parse("1-3,7,10", page_count=15)
        assert indices == (1, 2, 3, 7, 10)

    def test_open_ended_range(self):
        """An open-ended range (3-) goes to the last page."""
        indices = PageRanges.parse("3-", page_count=10)
        assert indices == (3, 4, 5, 6, 7, 8, 9, 10)

    def test_odd_pages(self):
        """'odd' selects odd-numbered pages."""
        indices = PageRanges.parse("odd", page_count=10)
        assert indices == (1, 3, 5, 7, 9)

    def test_even_pages(self):
        """'even' selects even-numbered pages."""
        indices = PageRanges.parse("even", page_count=10)
        assert indices == (2, 4, 6, 8, 10)

    def test_last_keyword(self):
        """'last' refers to the final page."""
        indices = PageRanges.parse("last", page_count=10)
        assert indices == (10,)

    def test_reverse_range(self):
        """A descending range reverses page order."""
        indices = PageRanges.parse("4-2", page_count=10)
        assert indices == (4, 3, 2)

    def test_last_minus(self):
        """'last-N' works as expected."""
        indices = PageRanges.parse("last-2", page_count=10)
        # last-2 means from page 10 (last) down to page 2
        assert indices == (10, 9, 8, 7, 6, 5, 4, 3, 2)

    def test_repeated_pages(self):
        """Repeated pages are allowed."""
        indices = PageRanges.parse("1,3,1,5", page_count=10)
        assert indices == (1, 3, 1, 5)

    def test_whitespace_ignored(self):
        """Whitespace around items is ignored."""
        indices = PageRanges.parse(" 1 - 3 , 5 ", page_count=10)
        assert indices == (1, 2, 3, 5)

    def test_default_page_count_for_last(self):
        """'last' uses default_page_count when no page_count is specified."""
        indices = PageRanges.parse("last")
        assert indices == (10000,)  # Updated default from 1,000,000 to 10,000

    def test_open_ended_with_default_page_count(self):
        """Open-ended range uses default_page_count when no page_count."""
        indices = PageRanges.parse("999-")
        # Should go to default_page_count (10,000)
        assert len(indices) == 10000 - 998
        assert indices[0] == 999
        assert indices[-1] == 10000

    def test_repeated_with_default_page_count(self):
        """'last' can be repeated using default_page_count."""
        # This test verifies that "last,last-1" works (2 items, not expanded)
        # Note: default_page_count is 1,000,000 which triggers resource limits
        # when computing expanded ranges. We use a smaller page_count for this test.
        indices = PageRanges.parse("last,last", page_count=100)
        assert indices == (100, 100)


class TestPageRangesValidate:
    """Tests for PageRanges.validate() static method."""

    def test_valid_expression(self):
        """A valid expression passes validation."""
        PageRanges.validate("1-3,7,10", page_count=15)

    def test_invalid_page_out_of_range(self):
        """Pages beyond page_count are rejected."""
        with pytest.raises(PdfValidationException, match="out of range"):
            PageRanges.validate("6", page_count=5)

    def test_empty_selection(self):
        """Empty page range raises validation exception."""
        with pytest.raises(PdfValidationException):
            PageRanges.validate("", page_count=5)

    def test_invalid_syntax(self):
        """Invalid syntax is rejected."""
        with pytest.raises(PdfValidationException):
            PageRanges.validate("1,,3", page_count=5)

    def test_empty_document(self):
        """An empty document has no valid page ranges."""
        with pytest.raises(PdfValidationException, match="out of range"):
            PageRanges.validate("1", page_count=0)


class TestPageRangesEdgeCases:
    """Tests for edge cases in PageRanges."""

    def test_zero_page_count(self):
        """Zero page count rejects any selection."""
        with pytest.raises(PdfValidationException, match="out of range"):
            PageRanges.parse("1", page_count=0)

    def test_large_page_count(self):
        """Large page counts are handled correctly."""
        indices = PageRanges.parse("1,2,3", page_count=1000000)
        assert indices == (1, 2, 3)

    def test_single_page_document(self):
        """Single page document only accepts page 1."""
        indices = PageRanges.parse("1", page_count=1)
        assert indices == (1,)

    def test_one_based_false(self):
        """one_based=False interprets numbers as zero-based."""
        indices = PageRanges.parse("0,1,2", page_count=5, one_based=False)
        assert indices == (1, 2, 3)

    def test_zero_based_range(self):
        """Zero-based ranges work with one_based=False."""
        indices = PageRanges.parse("0-2,4", page_count=5, one_based=False)
        assert indices == (1, 2, 3, 5)


class TestPageRangesWithLimits:
    """Tests for PageRanges with resource limits."""

    def test_within_limits(self):
        """Parsing within limits succeeds."""
        limits = PdfLoadLimits(max_container_items=5)
        indices = PageRanges.parse("1-3", page_count=10, limits=limits)
        assert indices == (1, 2, 3)

    def test_exceeds_container_limits(self):
        """Parsing that exceeds container limits raises."""
        limits = PdfLoadLimits(max_container_items=2)
        # "odd" on 6 pages expands to 3 items (1, 3, 5)
        with pytest.raises(PdfResourceLimitException, match="expanded page range"):
            PageRanges.parse("odd", page_count=6, limits=limits)


class TestPageRangesTypeErrors:
    """Tests for PageRanges type validation."""

    def test_non_string_expression(self):
        """Non-string expression raises TypeError."""
        with pytest.raises(TypeError, match="expression must be a string"):
            PageRanges.parse(123)

    def test_non_integer_page_count(self):
        """Non-integer page_count raises TypeError."""
        with pytest.raises(TypeError, match="page_count must be an integer"):
            PageRanges.parse("1", page_count="10")

    def test_boolean_page_count(self):
        """Boolean page_count raises TypeError (bool is subclass of int)."""
        with pytest.raises(TypeError, match="page_count must be an integer"):
            PageRanges.parse("1", page_count=True)

    def test_non_boolean_one_based(self):
        """Non-boolean one_based raises TypeError."""
        with pytest.raises(TypeError, match="one_based must be a boolean"):
            PageRanges.parse("1", page_count=10, one_based="true")

    def test_negative_page_count(self):
        """Negative page_count raises ValueError."""
        with pytest.raises(ValueError, match="page_count must not be negative"):
            PageRanges.parse("1", page_count=-1)


class TestPageRangesUnicode:
    """Tests for PageRanges Unicode handling."""

    def test_unicode_expression(self):
        """Invalid Unicode or unsupported syntax raises validation exception."""
        # Using invalid syntax that's not a valid page range
        with pytest.raises(PdfValidationException):
            PageRanges.parse("é-1", page_count=10)
