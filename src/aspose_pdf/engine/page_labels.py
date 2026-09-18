"""Page labels (ISO 32000-1 12.4.2): the numbering a viewer shows for each page.

The catalog's ``/PageLabels`` is a number tree from page index to a label
dictionary -- ``/S`` style, ``/P`` prefix, ``/St`` first number -- each entry
starting a range that runs to the next. Where readers disagree on a malformed
tree, this follows the majority of pdfium, qpdf, pdf.js and MuPDF, and the
specification where they split evenly:

* no ``/PageLabels`` at all: the document has no labels (pdfium, pdf.js);
* a page before the first range is numbered in decimal from 1 (pdfium, qpdf);
* keys are sorted, a repeated key's later value wins, a negative key covers the
  pages after it (three of four each);
* an unknown ``/S`` leaves the prefix alone; a key or ``/St`` must be a whole
  number (``3.0`` is 3) -- any other key is skipped, any other ``/St``
  ignored so the range starts at 1;
* letters repeat -- ``A`` to ``Z``, then ``AA`` to ``ZZ``, then ``AAA`` -- as
  the specification says and pdfium and pdf.js do (qpdf and MuPDF count
  ``AA, AB, AC`` instead); a number below 1 has no letters and no Roman
  numeral, and thousands are repeated ``M``.

Inserting or deleting a page moves the ranges after it, as MuPDF does: a page
inserted inside a range, or at the start of the next one, continues the range
before it. Pages brought in from another document keep the labels they had
there, as qpdf does when it assembles pages.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .cos import (
    PdfArray,
    PdfDictionary,
    PdfIndirectReference,
    PdfName,
    PdfNumber,
    PdfString,
    decode_pdf_text_string,
)

__all__ = [
    "NO_LABEL",
    "LabelRange",
    "format_label",
    "label_at",
    "label_values",
    "ranges_after_delete",
    "ranges_after_insert",
    "ranges_from_values",
    "read_ranges",
    "write_ranges",
]

#: ``/S`` values (ISO 32000-1 table 159).
STYLES = ("D", "R", "r", "A", "a")

_ROMAN = (
    (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
    (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
)


@dataclass(frozen=True)
class LabelRange:
    """How the pages from one range start onward are labelled."""

    style: str | None = "D"
    """``/S``: ``D``, ``R``, ``r``, ``A``, ``a``, or ``None`` for the prefix alone."""

    prefix: str = ""
    """``/P``."""

    start: int = 1
    """``/St``: the number of the range's first page."""


#: A page's label as ``(range, number)``: the rule and this page's number in it.
LabelValue = tuple[LabelRange, int]

#: What a page before every range shows: decimal, from 1.
_DEFAULT = LabelRange()

#: A page brought in from a document without labels: an empty label, as qpdf
#: gives it when it assembles pages from several files.
NO_LABEL = LabelRange(style=None)


def format_label(label: LabelRange, number: int) -> str:
    """The label text for page *number* of a range styled as *label*."""
    style = label.style
    if style == "D":
        digits = str(number)
    elif style in ("R", "r"):
        digits = _roman(number) if style == "R" else _roman(number).lower()
    elif style in ("A", "a"):
        digits = _letters(number, "A" if style == "A" else "a")
    else:
        digits = ""
    return label.prefix + digits


def _roman(number: int) -> str:
    if number < 1:
        return ""
    out = []
    for value, symbol in _ROMAN:
        count, number = divmod(number, value)
        out.append(symbol * count)
    return "".join(out)


def _letters(number: int, first: str) -> str:
    if number < 1:
        return ""
    repeat, index = divmod(number - 1, 26)
    return chr(ord(first) + index) * (repeat + 1)


# ---------------------------------------------------------------------------
# Reading and writing the tree
# ---------------------------------------------------------------------------
def _catalog(pdf: Any) -> PdfDictionary | None:
    doc = getattr(pdf, "_cos_doc", None)
    if doc is None:
        return None
    root = pdf._resolve(doc.trailer.mapping.get(PdfName("Root")))
    return root if isinstance(root, PdfDictionary) else None


def read_ranges(pdf: Any) -> list[tuple[int, LabelRange]] | None:
    """The document's ranges, sorted by first page; ``None`` without ``/PageLabels``."""
    catalog = _catalog(pdf)
    if catalog is None:
        return None
    tree = pdf._resolve(catalog.mapping.get(PdfName("PageLabels")))
    if not isinstance(tree, PdfDictionary):
        return None
    found: dict[int, LabelRange] = {}
    budget = pdf._load_budget
    stack: list[tuple[PdfDictionary, int]] = [(tree, 1)]
    seen: set[int] = set()
    while stack:
        node, depth = stack.pop()
        budget.check(depth, "max_nesting_depth", "PageLabels number-tree depth")
        if id(node) in seen:
            continue  # a cycle: each node is read once
        seen.add(id(node))
        nums = pdf._resolve(node.mapping.get(PdfName("Nums")))
        if isinstance(nums, PdfArray):
            budget.check(len(nums.items), "max_container_items", "PageLabels entries")
            for offset in range(0, len(nums.items) - 1, 2):
                key = pdf._resolve(nums.items[offset])
                value = pdf._resolve(nums.items[offset + 1])
                if _is_integer(key) and isinstance(value, PdfDictionary):
                    found[int(key.value)] = _label_range(pdf, value)
        kids = pdf._resolve(node.mapping.get(PdfName("Kids")))
        if isinstance(kids, PdfArray):
            budget.check(len(kids.items), "max_container_items", "PageLabels children")
            for kid in reversed(kids.items):
                child = pdf._resolve(kid)
                if isinstance(child, PdfDictionary):
                    stack.append((child, depth + 1))
    return sorted(found.items())


def _is_integer(value: Any) -> bool:
    """A whole number, ``2.0`` included (three references of four agree)."""
    return (
        isinstance(value, PdfNumber)
        and isinstance(value.value, (int, float))
        and not isinstance(value.value, bool)
        and float(value.value).is_integer()
    )


def _label_range(pdf: Any, value: PdfDictionary) -> LabelRange:
    style = pdf._resolve(value.mapping.get(PdfName("S")))
    name = style.name.lstrip("/") if isinstance(style, PdfName) else None
    prefix = pdf._resolve(value.mapping.get(PdfName("P")))
    start = pdf._resolve(value.mapping.get(PdfName("St")))
    return LabelRange(
        style=name if name in STYLES else None,
        prefix=decode_pdf_text_string(prefix) if isinstance(prefix, PdfString) else "",
        start=int(start.value) if _is_integer(start) else 1,
    )


def write_ranges(pdf: Any, ranges: list[tuple[int, LabelRange]] | None) -> None:
    """Replace the document's ``/PageLabels`` with *ranges* (``None`` removes it)."""
    catalog = _catalog(pdf)
    if catalog is None:
        return
    if ranges is None:
        catalog.mapping.pop(PdfName("PageLabels"), None)
        return
    nums: list[Any] = []
    for key, label in sorted(ranges, key=lambda item: item[0]):
        entry: dict[PdfName, Any] = {}
        if label.style is not None:
            entry[PdfName("S")] = PdfName(label.style)
        if label.prefix:
            entry[PdfName("P")] = PdfString(label.prefix)
        if label.start != 1:
            entry[PdfName("St")] = PdfNumber(label.start)
        nums.extend((PdfNumber(key), PdfDictionary(entry)))
    tree = PdfDictionary({PdfName("Nums"): PdfArray(nums)})
    existing = catalog.mapping.get(PdfName("PageLabels"))
    if isinstance(existing, PdfIndirectReference) and existing.object_number in pdf._cos_doc.objects:
        pdf._cos_doc.objects[existing.object_number] = tree  # keep the object's number
    else:
        catalog.mapping[PdfName("PageLabels")] = pdf._cos_doc.register_object(tree)


# ---------------------------------------------------------------------------
# Labels per page, and back
# ---------------------------------------------------------------------------
def label_at(ranges: list[tuple[int, LabelRange]], page: int) -> str:
    """The label of page *page* under *ranges*."""
    covering: tuple[int, LabelRange] | None = None
    for key, label in ranges:
        if key > page:
            break
        covering = (key, label)
    if covering is None:
        return format_label(_DEFAULT, page + 1)
    key, label = covering
    return format_label(label, label.start + page - key)


def label_values(ranges: list[tuple[int, LabelRange]], count: int) -> list[LabelValue]:
    """Each of *count* pages' range and number."""
    values: list[LabelValue] = []
    position = 0
    for page in range(count):
        while position < len(ranges) and ranges[position][0] <= page:
            position += 1
        if position == 0:
            values.append((_DEFAULT, page + 1))
        else:
            key, label = ranges[position - 1]
            values.append((label, label.start + page - key))
    return values


def ranges_from_values(values: list[LabelValue]) -> list[tuple[int, LabelRange]]:
    """The fewest ranges that give each page its label; the first always at 0."""
    ranges: list[tuple[int, LabelRange]] = []
    previous: LabelValue | None = None
    for page, (label, number) in enumerate(values):
        continues = (
            previous is not None
            and previous[0].style == label.style
            and previous[0].prefix == label.prefix
            # Without a style the number is not shown, so any continues.
            and (label.style is None or number == previous[1] + 1)
        )
        if not continues:
            start = number if label.style is not None else 1
            ranges.append((page, LabelRange(label.style, label.prefix, start)))
        previous = (label, number)
    return ranges


# ---------------------------------------------------------------------------
# Inserting and deleting pages
# ---------------------------------------------------------------------------
def ranges_after_insert(
    ranges: list[tuple[int, LabelRange]], index: int
) -> list[tuple[int, LabelRange]]:
    """The ranges once a page is inserted at *index*.

    Every range from *index* on moves back one page, so the new page continues
    the range before it; a page inserted before all of them is numbered as a
    page before every range is, from a range of its own at 0.
    """
    moved = [(key + 1 if key >= index else key, label) for key, label in ranges]
    if index == 0:
        moved.insert(0, (0, _DEFAULT))
    return moved


def ranges_after_delete(
    ranges: list[tuple[int, LabelRange]], index: int, count: int
) -> list[tuple[int, LabelRange]]:
    """The ranges once the page at *index* is deleted, *count* pages remaining.

    Every range after it moves forward one page. A range that started on the
    deleted page starts on the page that follows, unless that page began a
    range of its own -- then the range had no other page and goes.
    """
    moved: dict[int, LabelRange] = {}
    for key, label in ranges:  # ascending, so a range from the next page wins
        moved[key - 1 if key > index else key] = label
    return sorted((key, label) for key, label in moved.items() if key < count)
