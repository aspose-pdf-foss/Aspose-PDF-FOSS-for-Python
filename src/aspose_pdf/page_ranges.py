"""Parse page range expressions for the package's page selection APIs."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import PdfLoadLimits, _LoadBudget

if TYPE_CHECKING:
    pass

__all__ = ["PageRanges", "parse_page_range"]

_RANGE = re.compile(r"([0-9]+|last)(?:\s*-\s*(last|[0-9]*))?")


def parse_page_range(
    expression: str,
    page_count: int,
    *,
    one_based: bool = True,
    limits: PdfLoadLimits | None = None,
) -> tuple[int, ...]:
    """Return one-based page numbers from page numbers, keywords and ranges.

    ``"1-3,7,10-"`` selects pages 1 through 3, page 7, and page 10 through
    the last page. Numbers in the expression are one-based by default;
    ``one_based=False`` interprets them as zero-based instead. The returned
    page numbers are always one-based, ready for ``Document.extract_pages`` and
    APIs accepting a ``pages`` argument. The same numbers can be passed to facades.

    Case-insensitive ``odd`` and ``even`` select physical pages 1, 3, 5, ...
    and 2, 4, 6, ... regardless of ``one_based`` or page labels. ``last``
    selects the final page and can be either endpoint: ``"2-last"`` selects
    from page 2 onward; ``"last-1"`` selects every page in reverse order with
    the default numbering. Any range may descend, such as ``"4-2"``.

    Whitespace around items and hyphens is ignored. Input order and repeated
    pages are preserved, including overlaps between comma-separated items.
    Empty items, unsupported syntax and pages outside ``page_count`` raise
    ``PdfValidationException``. Numbers, ``last`` and open-ended ranges require
    an existing page. ``odd`` and ``even`` return an empty tuple when no pages
    match; document extraction and splitting still reject empty selections.
    Expression bytes and the total expanded selection obey ``limits``.
    Parsing reads no document content.
    """
    if not isinstance(expression, str):
        raise TypeError("expression must be a string")
    if isinstance(page_count, bool) or not isinstance(page_count, int):
        raise TypeError("page_count must be an integer")
    if page_count < 0:
        raise ValueError("page_count must not be negative")
    if not isinstance(one_based, bool):
        raise TypeError("one_based must be a boolean")

    budget = _LoadBudget(limits)
    budget.check(len(expression), "max_object_bytes", "page range expression")
    try:
        size = len(expression.encode("utf-8"))
    except UnicodeEncodeError:
        raise PdfValidationException("Page range contains invalid Unicode text") from None
    budget.check(size, "max_object_bytes", "page range expression")
    budget.check(expression.count(",") + 1, "max_container_items", "page range items")
    offset = 1 if one_based else 0
    upper = page_count - 1 + offset
    indexes: list[int] = []

    def number(text: str) -> int:
        try:
            value = upper if text == "last" else int(text.lstrip("0") or "0")
        except ValueError:
            raise PdfValidationException("Page number out of range") from None
        if not offset <= value <= upper:
            raise PdfValidationException("Page number out of range")
        return value

    for item in expression.split(","):
        item = item.strip().lower()
        if item in ("odd", "even"):
            start = 0 if item == "odd" else 1
            budget.check(
                len(indexes) + (page_count + 1 - start) // 2,
                "max_container_items",
                "expanded page range selection",
            )
            indexes.extend(range(start, page_count, 2))
            continue
        match = _RANGE.fullmatch(item)
        if match is None:
            raise PdfValidationException(
                "Page range must contain page numbers, odd, even, last, "
                "inclusive ranges (1-3 or 3-1), or open-ended ranges (3-), "
                "separated by commas"
            )
        start = number(match[1])
        if match[2] is None:
            end = start
        elif match[2]:
            end = number(match[2])
        else:
            end = upper
        step = 1 if end >= start else -1
        budget.check(
            len(indexes) + abs(end - start) + 1,
            "max_container_items",
            "expanded page range selection",
        )
        indexes.extend(range(start - offset, end - offset + step, step))
    return tuple(index + 1 for index in indexes)


class PageRanges:
    """A convenience class for parsing and validating page range expressions.

    This class provides a more object-oriented interface for working with
    page range expressions. It can be used to parse expressions without
    requiring a loaded document, making it useful for validation and
    preprocessing before loading a PDF.

    Example
    -------
    .. code-block:: python

        from aspose_pdf import PageRanges

        # Parse a page range without a document
        indices = PageRanges.parse("1-3,7,10-")
        print(indices)  # (1, 2, 3, 7, 10, 11, ... up to default_page_count)

        # Parse with a specific page count
        indices = PageRanges.parse("1-3,7,10-", page_count=15)
        print(indices)  # (1, 2, 3, 7, 10, 11, 12, 13, 14, 15)

        # Use with limits
        from aspose_pdf import PdfLoadLimits
        limits = PdfLoadLimits(max_container_items=5)
        indices = PageRanges.parse("1-3", page_count=10, limits=limits)
        print(indices)  # (1, 2, 3)

    Note
    ----
    When *page_count* is ``None``, the default value is 10,000 pages.
    This provides a reasonable limit for parsing expressions containing
    ``last`` without loading a document first. For larger documents, specify
    the actual page count explicitly.
    """

    DEFAULT_PAGE_COUNT = 10000
    """Default page count used when none is specified."""

    @staticmethod
    def parse(
        expression: str,
        page_count: int | None = None,
        *,
        one_based: bool = True,
        limits: PdfLoadLimits | None = None,
    ) -> tuple[int, ...]:
        """Parse a page range expression and return one-based page numbers.

        *expression* is a page range string such as ``"1-3,7,10-"``. String
        ranges include both endpoints and may descend (``"last-1"`` reverses
        all pages); an omitted end selects through the last page.
        ``odd`` and ``even`` select physical odd/even pages; ``last`` names
        the final page. Order and repeated pages are retained.

        *page_count* is the total number of pages in the document. If ``None``,
        a default of 10,000 pages is used, which allows parsing expressions
        with ``last`` without requiring a document to be loaded first. Set a
        specific page count to validate that all selected pages exist.

        *one_based* controls how numbers in the expression are interpreted.
        ``True`` (the default) treats them as one-based; ``False`` treats them
        as zero-based. The returned page numbers are always one-based.

        *limits* applies resource limits to the parsing operation. See
        :class:`~aspose_pdf.PdfLoadLimits` for details.

        Returns
        -------
        tuple of int
            One-based page numbers in the order specified by the selection.

        Examples
        --------
        Simple page range:

        .. code-block:: python

            from aspose_pdf import PageRanges

            indices = PageRanges.parse("1-3,7", page_count=10)
            # (1, 2, 3, 7)

        Raises
        ------
        PdfValidationException
            The expression is invalid or contains pages beyond *page_count*.
        TypeError
            *expression* is not a string, *page_count* is not an integer, or
            *one_based* is not a boolean.
        ValueError
            *page_count* is negative.

        Examples
        --------
        Parse a range without a document:

        .. code-block:: python

            from aspose_pdf import PageRanges

            indices = PageRanges.parse("1-3,7,10-")
            # Uses default_page_count, so "10-" means "10 to 1000000"

        Parse with a specific page count:

        .. code-block:: python

            indices = PageRanges.parse("1-3,7,10-", page_count=15)
            # (1, 2, 3, 7, 10, 11, 12, 13, 14, 15)

        Use with resource limits:

        .. code-block:: python

            from aspose_pdf import PageRanges, PdfLoadLimits

            limits = PdfLoadLimits(max_container_items=5)
            indices = PageRanges.parse("1-3", page_count=10, limits=limits)
            # (1, 2, 3)

        Validate before extracting:

        .. code-block:: python

            from aspose_pdf import Document, PageRanges, PdfLoadLimits

            # Validate the expression against the actual document
            with Document("report.pdf") as doc:
                PageRanges.validate("1-3,7,10-", doc.page_count, limits=doc.load_limits)
                # Now safe to extract
                selected = doc.extract_pages("1-3,7,10-")
        """
        if page_count is None:
            page_count = PageRanges.DEFAULT_PAGE_COUNT
        return parse_page_range(expression, page_count, one_based=one_based, limits=limits)

    @staticmethod
    def validate(
        expression: str,
        page_count: int,
        *,
        one_based: bool = True,
        limits: PdfLoadLimits | None = None,
    ) -> None:
        """Validate a page range expression against a page count.

        This method parses the expression and validates it against *page_count*,
        raising an exception if the expression is invalid or contains pages
        beyond *page_count*. Unlike :meth:`parse`, this method does not return
        the parsed indices, making it useful for quick validation before
        extracting pages.

        Parameters
        ----------
        expression : str
            A page range string such as ``"1-3,7,10-"``.
        page_count : int
            The total number of pages in the document. Must be non-negative.
        one_based : bool, optional
            Whether numbers in the expression are one-based. Default is ``True``.
        limits : PdfLoadLimits, optional
            Resource limits to apply to the parsing operation.

        Raises
        ------
        PdfValidationException
            The expression is invalid or contains pages beyond *page_count*.
        TypeError
            *expression* is not a string, *page_count* is not an integer, or
            *one_based* is not a boolean.
        ValueError
            *page_count* is negative.

        Examples
        --------
        .. code-block:: python

            from aspose_pdf import Document, PageRanges

            with Document("report.pdf") as doc:
                try:
                    PageRanges.validate("1-3,7,10-", doc.page_count)
                    selected = doc.extract_pages("1-3,7,10-")
                except PdfValidationException as e:
                    print(f"Invalid page selection: {e}")
        """
        parse_page_range(expression, page_count, one_based=one_based, limits=limits)
