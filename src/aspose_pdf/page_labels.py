"""Page labels: the page numbers a viewer shows -- ``i``, ``ii``, ``1``, ``A-1``.

A document's pages are labelled in ranges (ISO 32000-1 12.4.2): each range
starts at a page and gives it and the pages after it a numbering style, a
prefix and a first number, up to the page the next range starts at. Front
matter numbered ``i, ii, iii`` and a body numbered from ``1`` are two ranges.

:attr:`Document.page_labels <aspose_pdf.Document.page_labels>` is the ranges
by the page they start at; :attr:`Page.label <aspose_pdf.pages.Page.label>`
is the label one page ends up with::

    document.page_labels[0] = PageLabel(NumberingStyle.NUMERALS_ROMAN_LOWERCASE)
    document.page_labels[4] = PageLabel()                  # 1, 2, 3, ...
    document.page_labels[20] = PageLabel(prefix="A-")      # A-1, A-2, ...
    document.pages[4].label                                # '1'
"""

from __future__ import annotations

from collections.abc import Iterator, MutableMapping
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from aspose_pdf.engine import page_labels as engine_labels

if TYPE_CHECKING:
    from aspose_pdf.document import Document

__all__ = ["NumberingStyle", "PageLabel", "PageLabelCollection"]


class NumberingStyle(Enum):
    """How the number part of a label is written (``/S``)."""

    NUMERALS_ARABIC = "D"
    """1, 2, 3."""

    NUMERALS_ROMAN_UPPERCASE = "R"
    """I, II, III."""

    NUMERALS_ROMAN_LOWERCASE = "r"
    """i, ii, iii."""

    LETTERS_UPPERCASE = "A"
    """A to Z, then AA to ZZ, then AAA to ZZZ."""

    LETTERS_LOWERCASE = "a"
    """a to z, then aa to zz, then aaa to zzz."""

    NONE = ""
    """No number: every page of the range is labelled with the prefix alone."""


@dataclass(frozen=True)
class PageLabel:
    """The labelling of one range of pages."""

    style: NumberingStyle = NumberingStyle.NUMERALS_ARABIC
    """How the number is written."""

    prefix: str = ""
    """Text put before the number, such as ``"A-"``."""

    start: int = 1
    """The number of the range's first page; 1 or more."""

    def __post_init__(self) -> None:
        if not isinstance(self.style, NumberingStyle):
            raise TypeError("style must be a NumberingStyle")
        if not isinstance(self.prefix, str):
            raise TypeError("prefix must be a string")
        if isinstance(self.start, bool) or not isinstance(self.start, int):
            raise TypeError("start must be an integer")
        if self.start < 1:
            # ISO 32000-1 table 159: /St "shall be greater than or equal to 1".
            raise ValueError("start must be 1 or more")

    def text(self, offset: int = 0) -> str:
        """The label of the page *offset* pages into the range."""
        return engine_labels.format_label(self._range(), self.start + offset)

    def _range(self) -> engine_labels.LabelRange:
        return engine_labels.LabelRange(self.style.value or None, self.prefix, self.start)

    @classmethod
    def _read(cls, label: engine_labels.LabelRange) -> PageLabel:
        """A range as a file states it -- which may break the rules __init__ keeps."""
        page_label = object.__new__(cls)
        object.__setattr__(page_label, "style", NumberingStyle(label.style or ""))
        object.__setattr__(page_label, "prefix", label.prefix)
        object.__setattr__(page_label, "start", label.start)
        return page_label


class PageLabelCollection(MutableMapping[int, PageLabel]):
    """A document's label ranges, keyed by the index of the page each starts at.

    Setting a range where none starts at the first page adds one there, since
    the tree has to begin at page 0 (ISO 32000-1 table 28): numbered 1, 2, 3,
    which is what readers show for pages no range covers. Deleting that first
    range while others remain puts the same one back. Deleting the last range,
    or :meth:`clear`, removes the labels altogether.

    Inserting or deleting pages moves the ranges after them, so a page added
    inside a range is numbered as part of it; pages merged in from another
    document keep the labels they had there.
    """

    def __init__(self, document: Document) -> None:
        self._document = document

    def _engine(self):
        self._document._ensure_not_disposed()
        return self._document._engine_pdf

    def _ranges(self) -> dict[int, engine_labels.LabelRange]:
        return dict(self._engine().page_label_ranges() or [])

    def _write(self, ranges: dict[int, engine_labels.LabelRange]) -> None:
        if ranges and not any(key <= 0 for key in ranges):
            ranges[0] = engine_labels.LabelRange()
        self._engine().set_page_label_ranges(sorted(ranges.items()) if ranges else None)

    def _page_index(self, index: object) -> int:
        if isinstance(index, bool) or not isinstance(index, int):
            raise TypeError("a page index must be an integer")
        count = len(self._engine().pages)
        if not 0 <= index < count:
            raise IndexError(f"page index {index} is out of range for {count} pages")
        return index

    def __getitem__(self, start: int) -> PageLabel:
        ranges = self._ranges()
        if start not in ranges:
            raise KeyError(start)
        return PageLabel._read(ranges[start])

    def __setitem__(self, start: int, label: PageLabel) -> None:
        start = self._page_index(start)
        if not isinstance(label, PageLabel):
            raise TypeError("a page label range must be a PageLabel")
        ranges = self._ranges()
        ranges[start] = label._range()
        self._write(ranges)

    def __delitem__(self, start: int) -> None:
        ranges = self._ranges()
        if start not in ranges:
            raise KeyError(start)
        del ranges[start]
        self._write(ranges)

    def __iter__(self) -> Iterator[int]:
        return iter(sorted(self._ranges()))

    def __len__(self) -> int:
        return len(self._ranges())

    def clear(self) -> None:
        """Remove the labels: the document goes back to having none."""
        self._engine().set_page_label_ranges(None)

    def label(self, page_index: int) -> str | None:
        """The label of page *page_index*, or ``None`` when the document has none."""
        return self._engine().page_label(self._page_index(page_index))

    def __repr__(self) -> str:
        return f"PageLabelCollection({dict(self.items())!r})"
