"""Flowing text: paragraphs wrapped to a width, and placed on a page.

Until now authored text was placed one string at a time at one point, and
wrapping it meant measuring the font yourself -- or passing
``layout=TextLayoutOptions(...)`` to :meth:`Page.add_text`, which shapes with
HarfBuzz and so needs both an embedded font and the optional ``text-layout``
extra. A text block is the plain case: wrap to a measure, align the lines, and
carry on to the next page when the room runs out, with nothing to install::

    from aspose_pdf import Document, PageSize, TextBlock

    block = TextBlock(width=440, font_size=11, alignment="justify")
    block.add_paragraph("Of Man's first disobedience, and the fruit", tag="H1",
                        font_size=18, alignment="center", space_after=12)
    block.add_paragraph(long_text, first_line_indent=18)

    with Document() as document:
        page = document.pages.add(PageSize.A4)
        end = page.add_text_block(block, 72, 740)
        document.save("essay.pdf")

``x``/``y`` are the top-left corner of the first line's measure, with ``y``
counting up from the bottom of the page as everywhere else in the package, and
everything is in points. The return value says where the text ended, so the next
thing on the page knows where to go.

A plain string is one paragraph, and a **blank line starts a new one** -- the
convention of every plain-text format there is. A single newline stays a hard
break inside the paragraph it is in.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from aspose_pdf.exceptions import PdfValidationException

__all__ = ["Paragraph", "TextBlock"]

#: How a paragraph's lines sit across the measure. ``justify`` spreads every
#: line but the last of each hard-break segment; see :mod:`.engine.text_blocks`.
_ALIGNMENTS = ("left", "center", "centre", "right", "justify")


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


def _alignment(value: Any, name: str = "alignment") -> str | None:
    """One of :data:`_ALIGNMENTS`, normalised, or ``None`` when nothing said."""
    if value is None:
        return None
    key = str(value).strip().lower()
    if key not in _ALIGNMENTS:
        raise PdfValidationException(
            f"{name} is one of: " + ", ".join(sorted(set(_ALIGNMENTS)))
        )
    return "center" if key == "centre" else key


def _split_paragraphs(text: str) -> list[str]:
    """*text* cut into paragraphs at blank lines.

    A single newline is left where it is -- the wrap treats it as a hard break
    inside the paragraph -- while a run of two or more ends the paragraph. Text
    that is nothing but whitespace is one empty paragraph rather than none, so
    that a block built from it still occupies the room it was given.
    """
    normalised = str(text).replace("\r\n", "\n").replace("\r", "\n")
    paragraphs: list[str] = []
    current: list[str] = []
    for line in normalised.split("\n"):
        if line.strip():
            current.append(line)
        elif current:
            paragraphs.append("\n".join(current))
            current = []
    if current:
        paragraphs.append("\n".join(current))
    return paragraphs or [""]


@dataclass
class Paragraph:
    """One paragraph: what it says, and how it differs from the block.

    Every style is ``None`` by default, which means *take the block's* -- so a
    block is styled once and a paragraph only says where it departs from it.
    ``space_before``/``space_after`` are added to the block's ``paragraph_gap``
    rather than replacing it, since the gap is about the space *between*
    paragraphs and these are about this one.
    """

    text: str = ""
    alignment: str | None = None
    font_name: str | None = None
    font_size: float | None = None
    text_color: Any = None
    line_height: float | None = None
    space_before: float = 0.0
    space_after: float = 0.0
    first_line_indent: float | None = None
    left_indent: float = 0.0
    right_indent: float = 0.0
    #: Structure type for the paragraph, or ``None`` to leave it untagged.
    #: ``"H1"``, ``"Lbl"`` and the rest of ISO 32000-1 Table 333 all work.
    tag: str | None = "P"
    #: Keep the paragraph whole: move it to the next page rather than split it.
    keep_together: bool = False

    def __post_init__(self) -> None:
        self.text = "" if self.text is None else str(self.text)
        self.alignment = _alignment(self.alignment)
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
        if self.first_line_indent is not None:
            # A negative first-line indent is a hanging indent, which is how a
            # dictionary entry or a bibliography is set.
            self.first_line_indent = _measurement(
                self.first_line_indent, "first_line_indent", allow_negative=True
            )
        self.left_indent = _measurement(self.left_indent, "left_indent")
        self.right_indent = _measurement(self.right_indent, "right_indent")
        self.keep_together = bool(self.keep_together)


@dataclass
class TextBlock:
    """Paragraphs ready to be placed on a page, with the styles they share.

    ``text`` takes a string (one paragraph per blank-line-separated block), a
    sequence of strings, or :class:`Paragraph` objects -- or build it up with
    :meth:`add_paragraph`.

    ``width`` is the measure the lines wrap to. Without one the block uses the
    room between where it is placed and the right edge of the page, which is
    what a page of running text wants.

    ``font_name`` picks one of the standard 14 fonts and ``font`` embeds a
    Unicode font instead, exactly as for a :class:`~aspose_pdf.Table`.
    """

    text: Any = ""
    width: float | None = None
    font_name: str = "Helvetica"
    font_size: float = 11.0
    font: Any = None
    text_color: Any = (0.0, 0.0, 0.0)
    alignment: str = "left"
    line_height: float | None = None
    #: Space between one paragraph and the next, on top of their own
    #: ``space_after``/``space_before``.
    paragraph_gap: float = 0.0
    first_line_indent: float = 0.0

    #: Filled from *text*; append to it directly or through
    #: :meth:`add_paragraph`.
    paragraphs: list[Paragraph] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.paragraphs = [
            item if isinstance(item, Paragraph) else Paragraph(str(item))
            for item in self.paragraphs
        ]
        if isinstance(self.text, Paragraph):
            self.paragraphs.append(self.text)
        elif isinstance(self.text, (bytes, bytearray)):
            raise PdfValidationException("text is a string, not bytes")
        elif isinstance(self.text, str):
            if self.text.strip():
                self.paragraphs.extend(
                    Paragraph(part) for part in _split_paragraphs(self.text)
                )
        elif isinstance(self.text, Sequence):
            for item in self.text:
                self.paragraphs.append(
                    item if isinstance(item, Paragraph) else Paragraph(str(item))
                )
        elif self.text is not None:
            raise PdfValidationException(
                "text is a string, a Paragraph, or a sequence of either"
            )
        self.text = ""

        if self.width is not None:
            self.width = _measurement(self.width, "width", allow_zero=False)
        self.font_size = _measurement(self.font_size, "font_size", allow_zero=False)
        if self.line_height is not None:
            self.line_height = _measurement(
                self.line_height, "line_height", allow_zero=False
            )
        self.paragraph_gap = _measurement(self.paragraph_gap, "paragraph_gap")
        self.first_line_indent = _measurement(
            self.first_line_indent, "first_line_indent", allow_negative=True
        )
        self.alignment = _alignment(self.alignment) or "left"

    # -- building ----------------------------------------------------------

    def add_paragraph(self, text: Any = "", **options: Any) -> Paragraph:
        """Append a paragraph and return it.

        *text* is a string, or a :class:`Paragraph` that carries its own
        options.
        """
        if isinstance(text, Paragraph):
            if options:
                raise PdfValidationException(
                    "A Paragraph carries its own options; pass one or the other"
                )
            self.paragraphs.append(text)
            return text
        paragraph = Paragraph(str(text), **options)
        self.paragraphs.append(paragraph)
        return paragraph

    def add_paragraphs(self, text: str, **options: Any) -> list[Paragraph]:
        """Append one paragraph per blank-line-separated block of *text*."""
        return [
            self.add_paragraph(part, **options) for part in _split_paragraphs(text)
        ]

    @property
    def is_empty(self) -> bool:
        """Whether there is nothing here that would put a mark on a page."""
        return not any(paragraph.text.strip() for paragraph in self.paragraphs)
