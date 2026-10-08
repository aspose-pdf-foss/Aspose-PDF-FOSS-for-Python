"""Laying a table out and drawing it: measuring, wrapping, breaking, emitting.

The public value objects are in :mod:`aspose_pdf.tables`; this is what turns one
into marks on a page. Three pieces:

* **Measuring.** A face's own advances, the Standard 14 from
  :mod:`.std_metrics` and an embedded font from its CID widths, so a column's
  text is wrapped to what it will actually occupy rather than to a guess.
* **Laying out.** Column edges from the widths, a height per row from the lines
  its cells wrap to, and a break onto the next page when the next row would not
  fit -- repeating the header rows there.
* **Drawing.** Cell backgrounds, then the text, then the borders, through the
  same content builders :meth:`Page.draw_path` and :meth:`Page.add_text` use.

The structure is tagged as it is drawn: a ``/Table`` of ``/TR`` of ``/TH`` and
``/TD``, nested the way ISO 32000-1 14.8.4.3 asks for -- not flattened, which
would tell a reader every cell is a sibling of the table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..exceptions import PdfValidationException
from .cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNumber,
)
from .text_faces import DEFAULT_LINE_HEIGHT, _EmbeddedFace, _Face, _StandardFace

__all__ = [
    "DEFAULT_LINE_HEIGHT",
    "column_edges",
    "draw",
    "layout",
]

@dataclass
class _CellBox:
    """One cell, laid out: where it is, what it says, and how."""

    x: float
    width: float
    lines: list[str]
    text_height: float
    style: dict[str, Any]
    is_header: bool


@dataclass
class _RowBox:
    """One row, laid out: its height and its cells."""

    height: float
    cells: list[_CellBox] = field(default_factory=list)
    is_header: bool = False


def _style_of(table: Any, row: Any, cell: Any) -> dict[str, Any]:
    """What a cell is drawn with: its own, else the row's, else the table's."""
    header = cell.is_header if cell.is_header is not None else row.header
    size = cell.font_size or row.font_size or (
        table.header_font_size if header else None
    ) or table.font_size
    color = cell.text_color
    if color is None:
        color = row.text_color
    if color is None and header:
        color = table.header_text_color
    if color is None:
        color = table.text_color
    background = cell.background_color
    if background is None:
        background = row.background_color
    if background is None:
        background = (
            table.header_background_color if header else table.background_color
        )
    return {
        "font_size": float(size),
        "text_color": color,
        "background_color": background,
        "padding": table.padding if cell.padding is None else cell.padding,
        "alignment": cell.alignment or table.alignment,
        "vertical_alignment": cell.vertical_alignment or table.vertical_alignment,
        "border_width": (
            table.border_width if cell.border_width is None else cell.border_width
        ),
        "border_color": (
            table.border_color if cell.border_color is None else cell.border_color
        ),
        "font_name": cell.font_name or row.font_name or table.font_name,
    }


def column_edges(table: Any, available: float) -> list[float]:
    """Where each column starts, plus where the table ends, from its left edge.

    Explicit widths are taken as they are. Otherwise the table's width -- or the
    room left on the page -- is split equally among its columns, which is the
    only answer that needs nothing measured.
    """
    if table.column_widths:
        widths = list(table.column_widths)
    else:
        count = table.column_count
        if count < 1:
            raise PdfValidationException("A table needs at least one column")
        total = table.width if table.width is not None else available
        if total is None or total <= 0:
            raise PdfValidationException(
                "A table without column widths needs a width, or room on the page"
            )
        widths = [float(total) / count] * count
    edges = [0.0]
    for width in widths:
        edges.append(edges[-1] + float(width))
    return edges


def layout(
    table: Any,
    faces: dict[str, _Face],
    available: float,
) -> list[_RowBox]:
    """Measure every row: its cells' lines, their boxes, and its height."""
    edges = column_edges(table, available)
    columns = len(edges) - 1
    boxes: list[_RowBox] = []
    for row in table.rows:
        cells: list[_CellBox] = []
        column = 0
        tallest = 0.0
        for cell in row.cells:
            if column >= columns:
                raise PdfValidationException(
                    f"A row covers more than the table's {columns} columns"
                )
            span = min(cell.column_span, columns - column)
            x = edges[column]
            width = edges[column + span] - x
            style = _style_of(table, row, cell)
            face = faces[style["font_name"]]
            padding = style["padding"]
            size = style["font_size"]
            inner = width - 2 * padding
            lines = face.wrap(cell.text, inner, size) if cell.text else [""]
            line_height = table.line_height or max(
                face.line_height(size), size * DEFAULT_LINE_HEIGHT
            )
            text_height = len(lines) * line_height
            cells.append(
                _CellBox(
                    x=x,
                    width=width,
                    lines=lines,
                    text_height=text_height,
                    style={**style, "line_height": line_height},
                    is_header=cell.is_header
                    if cell.is_header is not None
                    else row.header,
                )
            )
            tallest = max(tallest, text_height + 2 * padding)
            column += span
        height = max(tallest, table.row_height or 0.0, row.height or 0.0)
        boxes.append(_RowBox(height=height, cells=cells, is_header=row.header))
    return boxes


def _register_table_structure(
    pdf: Any, page_index: int, rows: list[_RowBox]
) -> list[list[tuple[str, int]]] | None:
    """Build a ``/Table`` → ``/TR`` → ``/TH``/``/TD`` tree; return a mark per cell.

    14.8.4.3 nests a table's structure: cells belong to their row and rows to the
    table. Registering each cell as a child of the structure root instead would
    say every cell is a sibling of the table it is in, which is what a reader
    would then read out.
    """
    struct_root, struct_ref = pdf._ensure_struct_tree_root()
    page = pdf._get_page_dict(page_index)
    if not isinstance(page, PdfDictionary):
        return None
    page_ref = pdf._page_ref_for_structure(page_index)
    parent_array = pdf._parent_tree_array_for_page(struct_root, page)

    table_elem = PdfDictionary(
        {
            PdfName("Type"): PdfName("StructElem"),
            PdfName("S"): PdfName("Table"),
            PdfName("P"): struct_ref,
            PdfName("Pg"): page_ref,
        }
    )
    table_ref = pdf._cos_doc.register_object(table_elem)

    marks: list[list[tuple[str, int]]] = []
    row_refs: list[Any] = []
    for row in rows:
        row_elem = PdfDictionary(
            {
                PdfName("Type"): PdfName("StructElem"),
                PdfName("S"): PdfName("TR"),
                PdfName("P"): table_ref,
                PdfName("Pg"): page_ref,
            }
        )
        row_ref = pdf._cos_doc.register_object(row_elem)
        cell_refs: list[Any] = []
        row_marks: list[tuple[str, int]] = []
        for cell in row.cells:
            tag = "TH" if cell.is_header else "TD"
            mcid = len(parent_array.items)
            cell_elem = PdfDictionary(
                {
                    PdfName("Type"): PdfName("StructElem"),
                    PdfName("S"): PdfName(tag),
                    PdfName("P"): row_ref,
                    PdfName("Pg"): page_ref,
                    PdfName("K"): PdfNumber(mcid),
                }
            )
            cell_ref = pdf._cos_doc.register_object(cell_elem)
            parent_array.items.append(cell_ref)
            cell_refs.append(cell_ref)
            row_marks.append((tag, mcid))
        if cell_refs:
            row_elem.mapping[PdfName("K")] = PdfArray(cell_refs)
        row_refs.append(row_ref)
        marks.append(row_marks)
    if row_refs:
        table_elem.mapping[PdfName("K")] = PdfArray(row_refs)
    pdf._append_struct_root_kid(struct_root, table_ref)
    return marks


def _cell_content(
    pdf: Any,
    page_index: int,
    cell: _CellBox,
    left: float,
    top: float,
    height: float,
    faces: dict[str, _Face],
    resources: dict[str, str],
) -> bytes:
    """The background, the text and the border of one cell, in that order."""
    from ..paths import GraphicsPath
    from .content_authoring import build_path_stream

    style = cell.style
    padding = style["padding"]
    size = style["font_size"]
    line_height = style["line_height"]
    face = faces[style["font_name"]]
    x = left + cell.x
    bottom = top - height
    parts: list[bytes] = []

    if style["background_color"] is not None:
        parts.append(
            build_path_stream(
                GraphicsPath().rect(x, bottom, cell.width, height),
                stroke_color=None,
                fill_color=style["background_color"],
            )
        )

    # Where the first baseline sits: the text block placed by the vertical
    # alignment, then the face's ascent inside the first line.
    block = cell.text_height
    if style["vertical_alignment"] == "middle":
        text_top = bottom + (height + block) / 2
    elif style["vertical_alignment"] == "bottom":
        text_top = bottom + padding + block
    else:
        text_top = top - padding
    resource = resources[style["font_name"]]
    for index, line in enumerate(cell.lines):
        if not line:
            continue
        width = face.width(line, size)
        inner = cell.width - 2 * padding
        if style["alignment"] == "center":
            offset = padding + max(0.0, (inner - width) / 2)
        elif style["alignment"] == "right":
            offset = padding + max(0.0, inner - width)
        else:
            offset = padding
        # The baseline of this line, dropped by the face's own ascent so the
        # text sits inside the line rather than on top of it.
        baseline = text_top - (index + 1) * line_height + line_height * 0.24
        parts.append(
            face.content(line, x + offset, baseline, resource, size, style["text_color"])
        )

    if style["border_width"] > 0 and style["border_color"] is not None:
        parts.append(
            build_path_stream(
                GraphicsPath().rect(x, bottom, cell.width, height),
                stroke_color=style["border_color"],
                fill_color=None,
                line_width=style["border_width"],
            )
        )
    return b"".join(parts)


def draw(
    pdf: Any,
    page_index: int,
    table: Any,
    x: float,
    y: float,
    *,
    bottom_margin: float = 36.0,
    tag: bool = True,
) -> dict[str, Any]:
    """Draw *table* with its top-left corner at ``(x, y)``.

    Rows are placed down the page and the table continues onto the next page when
    the next row would cross *bottom_margin* -- adding a page like the one it is
    leaving when there is none to continue onto, and repeating the header rows at
    the top of each.

    Returns ``{"pages": [...], "bottom": y, "rows": n}``: which pages were drawn
    on, where the last row ended, and how many rows were placed.
    """
    from .content_authoring import wrap_marked_content

    pdf._ensure_not_disposed()
    pdf._validate_page_index(page_index)
    if not table.rows:
        raise PdfValidationException("A table with no rows has nothing to draw")

    faces: dict[str, _Face] = {}
    names = {table.font_name}
    for row in table.rows:
        names.add(row.font_name or table.font_name)
        for cell in row.cells:
            names.add(cell.font_name or row.font_name or table.font_name)
    for name in names:
        faces[name] = (
            _EmbeddedFace(pdf, table.font)
            if table.font is not None
            else _StandardFace(pdf, name)
        )

    box = pdf.get_page_crop_box(page_index) or tuple(pdf.pages[page_index])
    available = (box[2] - box[0]) - (float(x) - box[0])
    rows = layout(table, faces, available)
    headers = [row for row in rows if row.is_header] if table.repeat_header else []

    pages: list[int] = []
    current_page = page_index
    cursor = float(y)
    drawn = 0
    pending = list(rows)
    page_rows: list[_RowBox] = []
    page_tops: list[float] = []

    def flush() -> None:
        """Emit the rows collected for the current page."""
        nonlocal page_rows, page_tops
        if not page_rows:
            return
        resources = {name: face.resource(current_page) for name, face in faces.items()}
        marks = (
            _register_table_structure(pdf, current_page, page_rows) if tag else None
        )
        content: list[bytes] = []
        for index, (row, top) in enumerate(zip(page_rows, page_tops)):
            for position, cell in enumerate(row.cells):
                fragment = _cell_content(
                    pdf, current_page, cell, float(x), top, row.height, faces, resources
                )
                if marks is not None and fragment:
                    tag_name, mcid = marks[index][position]
                    fragment = wrap_marked_content(fragment, tag_name, mcid)
                content.append(fragment)
        pdf._append_content_to_page(current_page, b"".join(content))
        pages.append(current_page)
        page_rows = []
        page_tops = []

    def start_new_page() -> None:
        """Move to the page after this one, making one if there is none."""
        nonlocal current_page, cursor
        flush()
        if current_page + 1 >= len(pdf.pages):
            pdf.insert(len(pdf.pages), (tuple(pdf.pages[current_page]), b""))
        current_page += 1
        new_box = pdf.get_page_crop_box(current_page) or tuple(pdf.pages[current_page])
        cursor = new_box[3] - (box[3] - float(y))
        for header in headers:
            page_rows.append(header)
            page_tops.append(cursor)
            cursor -= header.height

    while pending:
        row = pending[0]
        if cursor - row.height < bottom_margin and (page_rows or pages):
            start_new_page()
            continue
        if cursor - row.height < bottom_margin and not page_rows and not pages:
            raise PdfValidationException(
                "The first row does not fit between this position and the bottom "
                "margin; start higher up or make the row shorter"
            )
        pending.pop(0)
        page_rows.append(row)
        page_tops.append(cursor)
        cursor -= row.height
        drawn += 1
    flush()
    return {"pages": pages, "bottom": cursor, "rows": drawn}
