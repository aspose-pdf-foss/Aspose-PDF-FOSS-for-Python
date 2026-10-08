"""Lists: items, their markers, and the nesting that makes them a list.

The structure a list needs has been in the engine since ``auto_tag`` learned to
recognise one -- a nested ``/L`` of ``/LI`` of ``/Lbl`` and ``/LBody`` -- but
only for *re-tagging* content that was already there. Authoring one meant
placing each bullet and each line by hand::

    from aspose_pdf import Document, PageSize, TextList

    bullets = TextList(width=420)
    bullets.add_item("Measure the room")
    bullets.add_item("Wrap the text to it", items=["even when it nests"])
    bullets.add_item("Put a marker in the gutter")

    with Document() as document:
        page = document.pages.add(PageSize.A4)
        page.add_list(bullets, 72, 740)
        document.save("list.pdf")

``ordered=True`` numbers the items instead of bulleting them, restarting at each
sub-list, and both the markers and the numbering style are chosen per depth --
the convention of every list ever set. Everything is measured in points, with
``y`` counting up from the bottom of the page as elsewhere in the package.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from aspose_pdf.exceptions import PdfValidationException

__all__ = ["ListItem", "TextList"]

#: Bullets used at successive depths when nothing says otherwise. The sequence
#: is cycled, so a list nested deeper than it is long repeats from the start.
DEFAULT_MARKERS = ("\u2022", "\u2013", "\u00b7")

#: Numbering styles used at successive depths of an ordered list, likewise
#: cycled. The names are those of the ``/ListNumbering`` attribute of ISO
#: 32000-1 Table 345, in the spelling this API takes.
DEFAULT_NUMBERING = ("decimal", "lower-alpha", "lower-roman")

#: ``/ListNumbering`` (Table 345) for each numbering style and each bullet, so
#: the structure says what the markers show. A reader that cannot see the glyphs
#: still knows an ordered list from an unordered one.
LIST_NUMBERING_ATTRIBUTES = {
    "decimal": "Decimal",
    "lower-alpha": "LowerAlpha",
    "upper-alpha": "UpperAlpha",
    "lower-roman": "LowerRoman",
    "upper-roman": "UpperRoman",
    "none": "None",
    "\u2022": "Disc",
    "\u25e6": "Circle",
    "\u25aa": "Square",
}

_NUMBERING_STYLES = (
    "decimal",
    "lower-alpha",
    "upper-alpha",
    "lower-roman",
    "upper-roman",
    "none",
)

_LABEL_ALIGNMENTS = ("left", "right")

_ROMAN = (
    (1000, "m"), (900, "cm"), (500, "d"), (400, "cd"),
    (100, "c"), (90, "xc"), (50, "l"), (40, "xl"),
    (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"),
)


def _measurement(
    value: Any, name: str, *, allow_zero: bool = True, allow_negative: bool = False
) -> float:
    """*value* as a finite number of points, refused when it cannot be one."""
    if isinstance(value, bool):
        raise PdfValidationException(f"{name} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise PdfValidationException(f"{name} must be a number") from None
    if number != number or number in (float("inf"), float("-inf")):
        raise PdfValidationException(f"{name} must be finite")
    if not allow_negative and number < 0:
        raise PdfValidationException(f"{name} must not be negative")
    if number == 0 and not allow_zero:
        raise PdfValidationException(f"{name} must be above zero")
    return number


def roman(number: int) -> str:
    """*number* as a lower-case Roman numeral.

    Zero and negatives have no numeral, so they are written as decimals: a list
    starting at zero is a strange thing to ask for, but inventing a glyph for it
    would be stranger.
    """
    if number < 1:
        return str(number)
    out: list[str] = []
    remaining = int(number)
    for value, numeral in _ROMAN:
        while remaining >= value:
            out.append(numeral)
            remaining -= value
    return "".join(out)


def alphabetic(number: int) -> str:
    """*number* as ``a``..``z``, then ``aa``..``az``, as a spreadsheet counts."""
    if number < 1:
        return str(number)
    out = ""
    remaining = int(number)
    while remaining > 0:
        remaining, rest = divmod(remaining - 1, 26)
        out = chr(ord("a") + rest) + out
    return out


def number_label(style: str, number: int) -> str:
    """The marker text for *number* in *style*, without its format applied."""
    if style == "decimal":
        return str(int(number))
    if style == "lower-alpha":
        return alphabetic(number)
    if style == "upper-alpha":
        return alphabetic(number).upper()
    if style == "lower-roman":
        return roman(number)
    if style == "upper-roman":
        return roman(number).upper()
    return ""


@dataclass
class ListItem:
    """One item: what it says, what hangs under it, and how it differs.

    ``items`` are the item's own sub-items, which become a nested list inside
    this item's ``/LBody`` -- which is where ISO 32000-1 puts one. A string is
    taken as an item of its own, so a nested list may be written as a plain
    list of strings.

    Every style is ``None`` by default, meaning *take the list's*. ``label``
    overrides the marker this item would otherwise be given, which is how a list
    with one odd item -- a dash among the numbers, or no marker at all (``""``)
    -- is set.
    """

    text: str = ""
    items: list[ListItem | str] = field(default_factory=list)
    label: str | None = None
    alignment: str | None = None
    font_name: str | None = None
    font_size: float | None = None
    text_color: Any = None
    line_height: float | None = None
    space_before: float = 0.0
    space_after: float = 0.0

    def __post_init__(self) -> None:
        self.text = "" if self.text is None else str(self.text)
        self.items = [
            item if isinstance(item, ListItem) else ListItem(str(item))
            for item in self.items
        ]
        if self.label is not None:
            self.label = str(self.label)
        if self.font_size is not None:
            self.font_size = _measurement(
                self.font_size, "font_size", allow_zero=False
            )
        if self.line_height is not None:
            self.line_height = _measurement(
                self.line_height, "line_height", allow_zero=False
            )
        self.space_before = _measurement(self.space_before, "space_before")
        self.space_after = _measurement(self.space_after, "space_after")

    def add_item(self, text: Any = "", **options: Any) -> ListItem:
        """Append a sub-item and return it."""
        if isinstance(text, ListItem):
            if options:
                raise PdfValidationException(
                    "A ListItem carries its own options; pass one or the other"
                )
            self.items.append(text)
            return text
        item = ListItem(str(text), **options)
        self.items.append(item)
        return item


@dataclass
class TextList:
    """Items ready to be placed on a page, with the styles they share.

    ``items`` takes strings, :class:`ListItem` objects, or nested sequences of
    either; :meth:`add_item` is the explicit form.

    ``width`` is the measure the items wrap to. Without one the list uses the
    room between where it is placed and the right edge of the page.

    ``indent`` is how far each depth is set in from the one above, and the
    marker sits in the gutter that ``label_width`` reserves -- measured from the
    widest marker in the list when nothing says. ``label_gap`` is the space
    between the marker and the text.
    """

    items: Any = ()
    ordered: bool = False
    markers: Any = DEFAULT_MARKERS
    numbering: Any = DEFAULT_NUMBERING
    number_format: str = "{number}."
    start: int = 1
    indent: float = 18.0
    label_gap: float = 6.0
    label_width: float | None = None
    label_alignment: str = "right"
    width: float | None = None
    font_name: str = "Helvetica"
    font_size: float = 11.0
    font: Any = None
    text_color: Any = (0.0, 0.0, 0.0)
    alignment: str = "left"
    line_height: float | None = None
    item_gap: float = 0.0

    def __post_init__(self) -> None:
        given = self.items
        self.items = []
        if isinstance(given, (str, bytes, bytearray)):
            raise PdfValidationException(
                "items is a sequence of items; a single string is one item, so "
                "pass [text]"
            )
        if isinstance(given, ListItem):
            self.items.append(given)
        elif isinstance(given, Sequence):
            for item in given:
                self.items.append(
                    item if isinstance(item, ListItem) else ListItem(str(item))
                )
        elif given is not None:
            raise PdfValidationException(
                "items is a sequence of strings or ListItem objects"
            )

        self.markers = self._sequence(self.markers, "markers", str)
        if not self.markers:
            raise PdfValidationException("markers names no marker")
        styles = self._sequence(self.numbering, "numbering", str)
        if not styles:
            raise PdfValidationException("numbering names no style")
        for style in styles:
            if style not in _NUMBERING_STYLES:
                raise PdfValidationException(
                    "a numbering style is one of: " + ", ".join(_NUMBERING_STYLES)
                )
        self.numbering = styles

        if not isinstance(self.number_format, str) or "{number}" not in self.number_format:
            raise PdfValidationException(
                "number_format is a string naming {number}, such as '{number}.'"
            )
        if isinstance(self.start, bool) or int(self.start) != self.start:
            raise PdfValidationException("start is a whole number")
        self.start = int(self.start)
        self.indent = _measurement(self.indent, "indent")
        self.label_gap = _measurement(self.label_gap, "label_gap")
        if self.label_width is not None:
            self.label_width = _measurement(self.label_width, "label_width")
        key = str(self.label_alignment).strip().lower()
        if key not in _LABEL_ALIGNMENTS:
            raise PdfValidationException(
                "label_alignment is one of: " + ", ".join(_LABEL_ALIGNMENTS)
            )
        self.label_alignment = key
        if self.width is not None:
            self.width = _measurement(self.width, "width", allow_zero=False)
        self.font_size = _measurement(self.font_size, "font_size", allow_zero=False)
        if self.line_height is not None:
            self.line_height = _measurement(
                self.line_height, "line_height", allow_zero=False
            )
        self.item_gap = _measurement(self.item_gap, "item_gap")
        self.ordered = bool(self.ordered)

    @staticmethod
    def _sequence(value: Any, name: str, kind: type) -> list[Any]:
        """*value* as a list, taking a bare one as a sequence of itself."""
        if isinstance(value, kind):
            return [value]
        if isinstance(value, Sequence):
            return [kind(item) for item in value]
        raise PdfValidationException(f"{name} is one value or a sequence of them")

    # -- building ----------------------------------------------------------

    def add_item(self, text: Any = "", **options: Any) -> ListItem:
        """Append an item and return it.

        *text* is a string, or a :class:`ListItem` that carries its own options.
        """
        if isinstance(text, ListItem):
            if options:
                raise PdfValidationException(
                    "A ListItem carries its own options; pass one or the other"
                )
            self.items.append(text)
            return text
        item = ListItem(str(text), **options)
        self.items.append(item)
        return item

    # -- what each depth is set with ---------------------------------------

    def marker_for(self, depth: int) -> str:
        """The bullet for *depth*, cycling through :attr:`markers`."""
        return self.markers[depth % len(self.markers)]

    def numbering_for(self, depth: int) -> str:
        """The numbering style for *depth*, cycling through :attr:`numbering`."""
        return self.numbering[depth % len(self.numbering)]

    def label_for(self, depth: int, position: int) -> str:
        """The marker an item at *depth* in *position* (counting from 1) gets."""
        if not self.ordered:
            return self.marker_for(depth)
        style = self.numbering_for(depth)
        if style == "none":
            return ""
        return self.number_format.format(
            number=number_label(style, self.start + position - 1)
        )

    def numbering_attribute(self, depth: int) -> str | None:
        """``/ListNumbering`` for *depth*: what the markers mean, as a name.

        ``None`` when the marker is not one Table 345 has a value for -- an en
        dash is a perfectly good bullet and none of ``Disc``/``Circle``/
        ``Square``, so the attribute is left off rather than claiming it is a
        disc. Saying nothing is better than saying the wrong thing about what a
        reader cannot see.
        """
        key = self.numbering_for(depth) if self.ordered else self.marker_for(depth)
        return LIST_NUMBERING_ATTRIBUTES.get(key)

    @property
    def is_empty(self) -> bool:
        """Whether there is nothing here that would put a mark on a page."""

        def any_text(items: Sequence[ListItem]) -> bool:
            return any(
                item.text.strip() or any_text(item.items) for item in items
            )

        return not any_text(self.items)
