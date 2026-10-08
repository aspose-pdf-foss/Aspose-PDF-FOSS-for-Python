"""Laying a flowing text block out and drawing it.

The public value objects are in :mod:`aspose_pdf.text_block`; this is what turns
one into marks on a page. Three pieces, as for a table:

* **Measuring.** Each paragraph's text is wrapped to its own measure with the
  face's real advances (:mod:`.text_faces`), so a line holds what it will
  actually occupy. A paragraph is wrapped **per hard-break segment**, which is
  what makes the last line of each segment knowable -- and that is the line
  justification must leave alone.
* **Breaking.** Lines are placed down the page and the block carries on to the
  next one when the next line would cross the bottom margin, adding a page like
  the one it is leaving when there is none to continue onto. A paragraph marked
  ``keep_together`` moves whole rather than splitting.
* **Drawing.** One text fragment per line, through the same builders
  :meth:`Page.add_text` uses -- plus the ``TJ`` form for a justified line.

A paragraph that continues onto the next page stays **one** structure element:
its marked-content ids live on different pages, so the ones that are not on the
element's own page are named with marked-content references (14.7.4.3). Tagging
a page's part as its own ``/P`` would have told a reader the sentence ended at
the foot of the page.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..exceptions import PdfValidationException
from .text_faces import DEFAULT_LINE_HEIGHT, _EmbeddedFace, _Face, _StandardFace

#: Where a line's baseline sits inside its line box, as a fraction of the line
#: height measured up from the bottom. The same value :mod:`.tables` drops a
#: cell's first baseline by, so a block and a table set side by side line up.
_BASELINE_FRACTION = 0.24


@dataclass
class _Line:
    """One laid-out line: what it says, where across the measure, how tall."""

    words: list[str]
    x: float
    measure: float
    height: float
    #: ``True`` for every line a justified paragraph should spread. The last
    #: line of each hard-break segment is excluded: it was ended by its author,
    #: not by the measure, and stretching it would open gaps nobody asked for.
    justify: bool
    style: dict[str, Any]

    @property
    def text(self) -> str:
        return " ".join(self.words)


@dataclass
class _ParagraphBox:
    """One paragraph, laid out: its lines and the space around them."""

    lines: list[_Line] = field(default_factory=list)
    space_before: float = 0.0
    space_after: float = 0.0
    tag: str | None = "P"
    keep_together: bool = False

    @property
    def height(self) -> float:
        return sum(line.height for line in self.lines)


def _style_of(block: Any, paragraph: Any) -> dict[str, Any]:
    """What a paragraph is drawn with: its own, else the block's."""
    size = paragraph.font_size or block.font_size
    color = paragraph.text_color
    if color is None:
        color = block.text_color
    indent = (
        block.first_line_indent
        if paragraph.first_line_indent is None
        else paragraph.first_line_indent
    )
    return {
        "font_name": paragraph.font_name or block.font_name,
        "font_size": float(size),
        "text_color": color,
        "alignment": paragraph.alignment or block.alignment,
        "line_height": paragraph.line_height or block.line_height,
        "first_line_indent": float(indent),
        "left_indent": float(paragraph.left_indent),
        "right_indent": float(paragraph.right_indent),
    }


def _segments(text: str) -> list[str]:
    """*text* split at its hard breaks, keeping empty ones as blank lines."""
    return str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n")


def layout(block: Any, faces: dict[str, _Face], measure: float) -> list[_ParagraphBox]:
    """Measure every paragraph: its lines, their place across the measure.

    *measure* is the width the block wraps to, before a paragraph's own
    indents. The ``x`` each line carries is its offset from the block's left
    edge, so a first-line or hanging indent is decided here rather than at
    drawing time.
    """
    if measure <= 0:
        raise PdfValidationException(
            "A text block needs a width, or room to the right of where it starts"
        )
    boxes: list[_ParagraphBox] = []
    for index, paragraph in enumerate(block.paragraphs):
        style = _style_of(block, paragraph)
        face = faces[style["font_name"]]
        size = style["font_size"]
        line_height = style["line_height"] or max(
            face.line_height(size), size * DEFAULT_LINE_HEIGHT
        )
        left = style["left_indent"]
        inner = measure - left - style["right_indent"]
        if inner <= 0:
            raise PdfValidationException(
                "A paragraph's indents leave no room for its text"
            )
        indent = style["first_line_indent"]
        lines: list[_Line] = []
        for segment in _segments(paragraph.text):
            if not segment.strip():
                # A blank line inside a paragraph still takes its line's room.
                lines.append(
                    _Line([], left, inner, line_height, False, style)
                )
                continue
            first = not lines
            # The first line of the paragraph is the one the indent moves; a
            # hanging indent (a negative one) widens it instead.
            head_indent = indent if first else 0.0
            wrapped = face.wrap(segment, inner - max(head_indent, 0.0), size)
            for position, text in enumerate(wrapped):
                on_first = first and position == 0
                offset = left + (head_indent if on_first else 0.0)
                width = inner - (max(head_indent, 0.0) if on_first else 0.0)
                lines.append(
                    _Line(
                        words=text.split(" ") if text else [],
                        x=offset,
                        measure=width,
                        height=line_height,
                        justify=position < len(wrapped) - 1,
                        style=style,
                    )
                )
        gap = block.paragraph_gap if index else 0.0
        boxes.append(
            _ParagraphBox(
                lines=lines,
                space_before=paragraph.space_before + gap,
                space_after=paragraph.space_after,
                tag=paragraph.tag,
                keep_together=paragraph.keep_together,
            )
        )
    return boxes


def _line_content(
    line: _Line,
    left: float,
    baseline: float,
    face: _Face,
    resource: str,
) -> bytes:
    """One line, placed across its measure by the alignment it carries."""
    style = line.style
    size = style["font_size"]
    color = style["text_color"]
    if not line.words:
        return b""
    text = line.text
    width = face.width(text, size)
    alignment = style["alignment"]
    x = left + line.x
    if alignment == "center":
        x += max(0.0, (line.measure - width) / 2)
    elif alignment == "right":
        x += max(0.0, line.measure - width)
    elif alignment == "justify" and line.justify and len(line.words) > 1:
        return face.justified_content(
            line.words, x, baseline, resource, size, color, line.measure
        )
    return face.content(text, x, baseline, resource, size, color)


def draw(
    pdf: Any,
    page_index: int,
    block: Any,
    x: float,
    y: float,
    *,
    bottom_margin: float = 36.0,
    tag: bool = True,
) -> dict[str, Any]:
    """Draw *block* with the top-left of its measure at ``(x, y)``.

    Returns ``{"pages": [...], "bottom": y, "lines": n}``: which pages were
    drawn on, where the text ended, and how many lines were placed -- so the
    next thing on the page knows where to go.
    """
    from .content_authoring import wrap_marked_content

    pdf._ensure_not_disposed()
    pdf._validate_page_index(page_index)
    if not block.paragraphs:
        raise PdfValidationException("A text block with no paragraphs has nothing to draw")

    faces: dict[str, _Face] = {}
    names = {block.font_name} | {
        paragraph.font_name or block.font_name for paragraph in block.paragraphs
    }
    for name in names:
        faces[name] = (
            _EmbeddedFace(pdf, block.font)
            if block.font is not None
            else _StandardFace(pdf, name)
        )

    box = pdf.get_page_crop_box(page_index) or tuple(pdf.pages[page_index])
    room = (box[2] - box[0]) - (float(x) - box[0])
    measure = float(block.width) if block.width is not None else room
    boxes = layout(block, faces, measure)

    # One list per page of what goes on it: the lines, their baselines, and
    # which paragraph each belongs to, so a paragraph's marks can be allocated
    # across the pages it reaches.
    placed: list[tuple[int, list[tuple[_ParagraphBox, _Line, float]]]] = []
    current_page = page_index
    current: list[tuple[_ParagraphBox, _Line, float]] = []
    cursor = float(y)
    top_offset = box[3] - float(y)
    drawn = 0

    def start_new_page() -> None:
        nonlocal current_page, current, cursor
        placed.append((current_page, current))
        current = []
        if current_page + 1 >= len(pdf.pages):
            pdf.insert(len(pdf.pages), (tuple(pdf.pages[current_page]), b""))
        current_page += 1
        new_box = pdf.get_page_crop_box(current_page) or tuple(pdf.pages[current_page])
        cursor = new_box[3] - top_offset

    for paragraph in boxes:
        if not paragraph.lines:
            cursor -= paragraph.space_before + paragraph.space_after
            continue
        cursor -= paragraph.space_before
        needed = (
            paragraph.height
            if paragraph.keep_together
            else (paragraph.lines[0].height if paragraph.lines else 0.0)
        )
        if cursor - needed < bottom_margin:
            if not current and not placed:
                raise PdfValidationException(
                    "The first line does not fit between this position and the "
                    "bottom margin; start higher up or lower the margin"
                )
            start_new_page()
        for line in paragraph.lines:
            if cursor - line.height < bottom_margin:
                if not current and not placed:
                    raise PdfValidationException(
                        "The first line does not fit between this position and "
                        "the bottom margin; start higher up or lower the margin"
                    )
                start_new_page()
            baseline = cursor - line.height + line.height * _BASELINE_FRACTION
            current.append((paragraph, line, baseline))
            cursor -= line.height
            drawn += 1
        cursor -= paragraph.space_after
    placed.append((current_page, current))

    # How many marked-content sequences each paragraph has on each page, in
    # reading order, so one element can be registered across them all.
    spans: dict[int, list[tuple[int, int]]] = {}
    for page, items in placed:
        for paragraph, line, _baseline in items:
            if not line.words:
                continue
            parts = spans.setdefault(id(paragraph), [])
            if parts and parts[-1][0] == page:
                parts[-1] = (page, parts[-1][1] + 1)
            else:
                parts.append((page, 1))

    marks: dict[int, tuple[str, dict[int, list[int]]]] = {}
    if tag:
        for paragraph in boxes:
            parts = spans.get(id(paragraph))
            if not parts or paragraph.tag is None:
                continue
            registered = pdf._register_spanning_marked_content(paragraph.tag, parts)
            if registered is not None:
                marks[id(paragraph)] = (
                    pdf._coerce_structure_type(paragraph.tag),
                    registered,
                )

    taken: dict[tuple[int, int], int] = {}
    pages: list[int] = []
    for page, items in placed:
        content: list[bytes] = []
        resources = {name: face.resource(page) for name, face in faces.items()}
        for paragraph, line, baseline in items:
            face = faces[line.style["font_name"]]
            fragment = _line_content(
                line, float(x), baseline, face, resources[line.style["font_name"]]
            )
            if not fragment:
                continue
            entry = marks.get(id(paragraph))
            if entry is not None:
                tag_name, per_page = entry
                key = (id(paragraph), page)
                position = taken.get(key, 0)
                mcids = per_page.get(page, [])
                if position < len(mcids):
                    fragment = wrap_marked_content(fragment, tag_name, mcids[position])
                    taken[key] = position + 1
            content.append(fragment)
        if content:
            pdf._append_content_to_page(page, b"".join(content))
            pages.append(page)
    return {"pages": pages, "bottom": cursor, "lines": drawn}
