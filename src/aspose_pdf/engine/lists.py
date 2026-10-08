"""Laying a list out and drawing it: markers, bodies, nesting, breaking.

The public value objects are in :mod:`aspose_pdf.lists`; this is what turns one
into marks on a page. A list is a flowing block with two columns -- a marker in
the gutter and a body that wraps -- so the measuring and the line emitting are
:mod:`.text_faces` and :mod:`.text_blocks`'s, reused rather than rewritten. What
is new here is the gutter, the numbering that restarts at each sub-list, and the
nested structure the result is tagged with.

Reading order is the order the items were given, which is also the order they
are placed and the order their marked-content ids are allocated in -- so one
walk decides all three.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..exceptions import PdfValidationException
from .text_blocks import _BASELINE_FRACTION, _Line, _line_content
from .text_faces import DEFAULT_LINE_HEIGHT, _EmbeddedFace, _Face, _StandardFace


@dataclass
class _ItemBox:
    """One item, laid out: its marker, its wrapped body, the room around it."""

    depth: int
    label: str
    #: Where the marker starts, from the list's left edge. The marker is set in
    #: the gutter the body's indent reserves, right-aligned against the body by
    #: default -- which is how "9." and "10." line up in a numbered list.
    label_x: float
    lines: list[_Line] = field(default_factory=list)
    space_before: float = 0.0
    space_after: float = 0.0
    style: dict[str, Any] = field(default_factory=dict)

    @property
    def height(self) -> float:
        return sum(line.height for line in self.lines)


def _style_of(text_list: Any, item: Any) -> dict[str, Any]:
    """What an item is drawn with: its own, else the list's."""
    color = item.text_color
    if color is None:
        color = text_list.text_color
    return {
        "font_name": item.font_name or text_list.font_name,
        "font_size": float(item.font_size or text_list.font_size),
        "text_color": color,
        "alignment": item.alignment or text_list.alignment,
        "line_height": item.line_height or text_list.line_height,
        "first_line_indent": 0.0,
        "left_indent": 0.0,
        "right_indent": 0.0,
    }


def flatten(text_list: Any) -> list[tuple[int, Any, str]]:
    """Every item in reading order, as ``(depth, item, label)``.

    The numbering restarts at each sub-list, which is what a nested ordered list
    means: the second level counts its own items, not the ones above it.
    """
    out: list[tuple[int, Any, str]] = []

    def walk(items: Any, depth: int) -> None:
        for position, item in enumerate(items, start=1):
            label = (
                item.label
                if item.label is not None
                else text_list.label_for(depth, position)
            )
            out.append((depth, item, label))
            if item.items:
                walk(item.items, depth + 1)

    walk(text_list.items, 0)
    return out


def label_column(text_list: Any, faces: dict[str, _Face], entries: Any) -> float:
    """How wide the marker gutter is: the widest marker, plus the gap.

    One width for the whole list rather than one per depth, so the bodies of
    every level line up at a fixed step from the margin instead of wandering
    with the longest marker that happens to be at that level.
    """
    if text_list.label_width is not None:
        return float(text_list.label_width) + float(text_list.label_gap)
    widest = 0.0
    for depth, item, label in entries:
        if not label:
            continue
        style = _style_of(text_list, item)
        face = faces[style["font_name"]]
        widest = max(widest, face.width(label, style["font_size"]))
    return widest + float(text_list.label_gap)


def layout(
    text_list: Any, faces: dict[str, _Face], measure: float
) -> list[_ItemBox]:
    """Measure every item: its marker, the lines its body wraps to, its place."""
    if measure <= 0:
        raise PdfValidationException(
            "A list needs a width, or room to the right of where it starts"
        )
    entries = flatten(text_list)
    gutter = label_column(text_list, faces, entries)
    boxes: list[_ItemBox] = []
    for position, (depth, item, label) in enumerate(entries):
        style = _style_of(text_list, item)
        face = faces[style["font_name"]]
        size = style["font_size"]
        line_height = style["line_height"] or max(
            face.line_height(size), size * DEFAULT_LINE_HEIGHT
        )
        body_x = depth * float(text_list.indent) + gutter
        inner = measure - body_x
        if inner <= 0:
            raise PdfValidationException(
                "The list's indents leave no room for an item's text"
            )
        lines: list[_Line] = []
        for segment in (
            str(item.text).replace("\r\n", "\n").replace("\r", "\n").split("\n")
        ):
            if not segment.strip():
                lines.append(_Line([], body_x, inner, line_height, False, style))
                continue
            wrapped = face.wrap(segment, inner, size)
            for index, text in enumerate(wrapped):
                lines.append(
                    _Line(
                        words=text.split(" ") if text else [],
                        x=body_x,
                        measure=inner,
                        height=line_height,
                        justify=index < len(wrapped) - 1,
                        style=style,
                    )
                )
        if not lines:
            lines.append(_Line([], body_x, inner, line_height, False, style))
        # Right-aligned against the body edge unless asked otherwise: a marker
        # column reads as a column when its right edges agree.
        label_width = face.width(label, size) if label else 0.0
        if text_list.label_alignment == "right":
            label_x = body_x - float(text_list.label_gap) - label_width
        else:
            label_x = depth * float(text_list.indent)
        boxes.append(
            _ItemBox(
                depth=depth,
                label=label,
                label_x=label_x,
                lines=lines,
                space_before=item.space_before
                + (float(text_list.item_gap) if position else 0.0),
                space_after=item.space_after,
                style=style,
            )
        )
    return boxes


def draw(
    pdf: Any,
    page_index: int,
    text_list: Any,
    x: float,
    y: float,
    *,
    bottom_margin: float = 36.0,
    tag: bool = True,
) -> dict[str, Any]:
    """Draw *text_list* with the top-left of its measure at ``(x, y)``.

    An item's marker never ends up alone at the foot of a page: the marker rides
    the item's first line, so whichever page that line goes on is the page the
    marker goes on.

    Returns ``{"pages": [...], "bottom": y, "items": n}``: the pages drawn on,
    where the list ended, and how many items were placed.
    """
    from .content_authoring import wrap_marked_content

    pdf._ensure_not_disposed()
    pdf._validate_page_index(page_index)
    if not text_list.items:
        raise PdfValidationException("A list with no items has nothing to draw")

    faces: dict[str, _Face] = {}
    names = {text_list.font_name}
    for _depth, item, _label in flatten(text_list):
        names.add(item.font_name or text_list.font_name)
    for name in names:
        faces[name] = (
            _EmbeddedFace(pdf, text_list.font)
            if text_list.font is not None
            else _StandardFace(pdf, name)
        )

    box = pdf.get_page_crop_box(page_index) or tuple(pdf.pages[page_index])
    room = (box[2] - box[0]) - (float(x) - box[0])
    measure = float(text_list.width) if text_list.width is not None else room
    boxes = layout(text_list, faces, measure)

    # Placement: each line gets a page and a baseline, the marker riding the
    # first line of its item. One walk, in reading order.
    placed: list[tuple[_ItemBox, _Line | None, int, float]] = []
    current_page = page_index
    cursor = float(y)
    top_offset = box[3] - float(y)
    pages_touched: list[int] = []

    def start_new_page() -> None:
        nonlocal current_page, cursor
        if current_page + 1 >= len(pdf.pages):
            pdf.insert(len(pdf.pages), (tuple(pdf.pages[current_page]), b""))
        current_page += 1
        new_box = pdf.get_page_crop_box(current_page) or tuple(pdf.pages[current_page])
        cursor = new_box[3] - top_offset

    for item in boxes:
        cursor -= item.space_before
        for index, line in enumerate(item.lines):
            if cursor - line.height < bottom_margin:
                if not placed:
                    raise PdfValidationException(
                        "The first line does not fit between this position and "
                        "the bottom margin; start higher up or lower the margin"
                    )
                start_new_page()
            baseline = cursor - line.height + line.height * _BASELINE_FRACTION
            if index == 0 and item.label:
                placed.append((item, None, current_page, baseline))
            placed.append((item, line, current_page, baseline))
            cursor -= line.height
        cursor -= item.space_after

    # The structure, from the placement: one entry per item, naming the page its
    # marker is on and the pages its lines are on.
    marks: list[dict[str, Any]] | None = None
    if tag:
        plan: list[dict[str, Any]] = []
        for item in boxes:
            label_page: int | None = None
            line_pages: list[int] = []
            for owner, line, page, _baseline in placed:
                if owner is not item:
                    continue
                if line is None:
                    label_page = page
                elif line.words:
                    line_pages.append(page)
            plan.append(
                {"depth": item.depth, "label": label_page, "lines": line_pages}
            )
        marks = pdf._register_authored_list(
            plan, numbering_for=text_list.numbering_attribute
        )

    by_item = {id(item): index for index, item in enumerate(boxes)}
    taken: dict[int, int] = {}
    content: dict[int, list[bytes]] = {}
    resources: dict[int, dict[str, str]] = {}
    for item, line, page, baseline in placed:
        if page not in resources:
            resources[page] = {
                name: face.resource(page) for name, face in faces.items()
            }
        face = faces[item.style["font_name"]]
        resource = resources[page][item.style["font_name"]]
        if line is None:
            fragment = face.content(
                item.label,
                float(x) + item.label_x,
                baseline,
                resource,
                item.style["font_size"],
                item.style["text_color"],
            )
            if marks is not None:
                mcid = marks[by_item[id(item)]]["label"]
                if mcid is not None:
                    fragment = wrap_marked_content(fragment, "Lbl", mcid)
        else:
            fragment = _line_content(line, float(x), baseline, face, resource)
            if not fragment:
                continue
            if marks is not None:
                entry = marks[by_item[id(item)]]["lines"]
                position = taken.get(id(item), 0)
                if position < len(entry):
                    fragment = wrap_marked_content(fragment, "LBody", entry[position])
                    taken[id(item)] = position + 1
        content.setdefault(page, []).append(fragment)

    for page in sorted(content):
        pdf._append_content_to_page(page, b"".join(content[page]))
        pages_touched.append(page)
    return {"pages": pages_touched, "bottom": cursor, "items": len(boxes)}
