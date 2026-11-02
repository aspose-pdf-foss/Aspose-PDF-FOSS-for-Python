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

import math
from dataclasses import dataclass, field
from typing import Any

from ..exceptions import PdfValidationException
from .cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNull,
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
    link: Any = None


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
        "borders": _borders_of(table, row, cell),
        "font_name": cell.font_name or row.font_name or table.font_name,
    }


def _borders_of(table: Any, row: Any, cell: Any) -> list[tuple[float, Any]]:
    """Resolve side styles before drawing, including edits made after creation."""
    from ..tables import _BORDER_SIDES, _border_sides, _positive
    from .content_authoring import normalize_color

    widths = dict.fromkeys(_BORDER_SIDES, 0.0)
    colors = dict.fromkeys(_BORDER_SIDES)
    for source in (table, row, cell):
        if source.border_width is not None:
            width = _positive(source.border_width, "border_width", allow_zero=True)
            widths = dict.fromkeys(_BORDER_SIDES, width)
        if source.border_color is not None:
            colors = dict.fromkeys(_BORDER_SIDES, source.border_color)
        widths.update(_border_sides(source.border_widths, "border_widths") or {})
        colors.update(_border_sides(source.border_colors, "border_colors") or {})
    return [
        (widths[side], normalize_color(colors[side]))
        if widths[side] > 0 and colors[side] is not None
        else (0.0, None)
        for side in _BORDER_SIDES
    ]


def _auto_widths(
    table: Any, faces: dict[str, _Face], count: int, total: float
) -> list[float]:
    """Fit measured preferred widths while retaining room for padding and glyphs."""
    from ..tables import _positive

    constraints: dict[tuple[int, int], tuple[float, float]] = {}
    for row in table.rows:
        column = 0
        for cell in row.cells:
            if (
                isinstance(cell.column_span, bool)
                or not isinstance(cell.column_span, int)
                or cell.column_span < 1
            ):
                raise PdfValidationException("column_span must be a positive integer")
            if column >= count:
                raise PdfValidationException(
                    f"A row covers more than the table's {count} columns"
                )
            span = min(cell.column_span, count - column)
            style = _style_of(table, row, cell)
            padding = _positive(style["padding"], "padding", allow_zero=True)
            size = _positive(style["font_size"], "font_size")
            face = faces[style["font_name"]]
            # Measure the same whitespace and hard breaks the wrapper renders.
            paragraphs = cell.text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            lines = [" ".join(line.split()) for line in paragraphs]
            preferred = max((face.width(line, size) for line in lines), default=0.0)
            glyph = max(
                (face.width(char, size) for char in set("".join(lines))), default=0.0
            )
            minimum = max(1.0, 2 * padding + glyph)
            preferred = max(minimum, 2 * padding + preferred)
            if not all(math.isfinite(value) for value in (minimum, preferred)):
                raise PdfValidationException(
                    "Automatic column measurements must be finite"
                )
            old_min, old_pref = constraints.get((column, span), (0.0, 0.0))
            constraints[column, span] = max(old_min, minimum), max(old_pref, preferred)
            column += span

    def grow(widths: list[float], start: int, span: int, required: float) -> None:
        """Share a span's deficit proportionally across the covered columns."""
        current = math.fsum(widths[start : start + span])
        if required > current:
            scale = required / current
            for index in range(start, start + span):
                widths[index] *= scale

    ordered = sorted(constraints, key=lambda key: (key[1], key[0]))
    try:
        # Prefix lower bounds find feasible minimum widths even for overlapping spans.
        endings: dict[int, list[tuple[int, float]]] = {}
        for (start, span), (minimum, _) in constraints.items():
            endings.setdefault(start + span, []).append((start, minimum))
        edges = [0.0]
        for end in range(1, count + 1):
            edge = edges[-1] + 1.0
            for start, required in endings.get(end, ()):
                edge = max(edge, edges[start] + required)
            edges.append(edge)
        minimums = [edges[index + 1] - edges[index] for index in range(count)]
        preferreds = [1.0] * count
        for start, span in ordered:
            grow(preferreds, start, span, constraints[start, span][1])
        minimum_total = math.fsum(minimums)
        preferred_total = math.fsum(preferreds)
        if minimum_total > total and not math.isclose(
            minimum_total, total, rel_tol=0, abs_tol=1e-8
        ):
            raise PdfValidationException(
                "Table width is too narrow for cell padding and glyphs"
            )
        if preferred_total <= total or preferred_total == minimum_total:
            widths = [width * (total / preferred_total) for width in preferreds]
        else:
            scale = max(0.0, total - minimum_total) / (preferred_total - minimum_total)
            widths = [
                low + (high - low) * scale
                for low, high in zip(minimums, preferreds)
            ]
        widths[-1] += total - math.fsum(widths)
    except OverflowError as exc:
        raise PdfValidationException("Automatic column measurements are too large") from exc
    return widths


def column_edges(
    table: Any, available: float, faces: dict[str, _Face] | None = None
) -> list[float]:
    """Where each column starts, plus where the table ends, from its left edge.

    Explicit widths are taken as they are. Otherwise the table's width -- or the
    room left on the page -- is split equally, or fitted to measured content
    when ``auto_column_widths`` is enabled and *faces* supplies the metrics.
    """
    if not isinstance(table.auto_column_widths, bool):
        raise PdfValidationException("auto_column_widths must be a boolean")
    if table.column_widths:
        widths = list(table.column_widths)
    else:
        count = table.column_count
        if count < 1:
            raise PdfValidationException("A table needs at least one column")
        total = table.width if table.width is not None else available
        if table.auto_column_widths:
            from ..tables import _positive

            total = _positive(total, "table width")
            if faces is None:
                raise PdfValidationException(
                    "Automatic column widths require font measurements"
                )
            widths = _auto_widths(table, faces, count, total)
        else:
            if total is None or total <= 0:
                raise PdfValidationException(
                    "A table without column widths needs a width, or room on the page"
                )
            widths = [float(total) / count] * count
    edges = [0.0]
    for width in widths:
        edge = edges[-1] + float(width)
        if table.auto_column_widths and not table.column_widths and (
            not math.isfinite(edge) or edge <= edges[-1]
        ):
            raise PdfValidationException("Automatic column widths cannot be represented")
        edges.append(edge)
    return edges


def layout(
    table: Any,
    faces: dict[str, _Face],
    available: float,
) -> list[_RowBox]:
    """Measure every row: its cells' lines, their boxes, and its height."""
    edges = column_edges(table, available, faces)
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
                    link=cell.link,
                )
            )
            tallest = max(tallest, text_height + 2 * padding)
            column += span
        height = max(tallest, table.row_height or 0.0, row.height or 0.0)
        boxes.append(_RowBox(height=height, cells=cells, is_header=row.header))
    return boxes


def _prepare_cell_link(pdf: Any, cell: _CellBox) -> Any:
    """Validate and encode a target before any table pages are drawn."""
    from ..interactive import Action, Destination
    from ..tables import _cell_link
    from .simple_pdf import _pdf_text_string

    target = _cell_link(cell.link)
    if target is None:
        return None
    destinations = [target] if isinstance(target, Destination) else []
    if isinstance(target, Action):
        spec = target._spec()
        if spec.get("S") != "GoToR":
            destinations.extend(
                value for value in spec.values() if isinstance(value, Destination)
            )
    for destination in destinations:
        page = destination.page
        if (
            isinstance(page, bool)
            or not isinstance(page, int)
            or not 0 <= page < len(pdf.pages)
        ):
            raise PdfValidationException(
                "A cell link destination must name an existing page"
            )
        for value in destination._spec()[1]:
            if value is None:
                continue
            try:
                valid = not isinstance(value, bool) and math.isfinite(float(value))
            except (TypeError, ValueError, OverflowError):
                valid = False
            if not valid:
                raise PdfValidationException(
                    "Cell link destination coordinates must be finite numbers"
                )
    try:
        value, key = pdf._interactive_target_cos(target)
        label = _pdf_text_string("\n".join(cell.lines))
    except UnicodeError as exc:
        raise PdfValidationException("Cell link contains invalid Unicode text") from exc
    return value, key, label


def _add_cell_links(
    pdf: Any, page_index: int, rows: list[_RowBox], tops: list[float], left: float
) -> list[list[Any]]:
    """Place one borderless annotation per linked cell occurrence."""
    if not any(cell.link is not None for row in rows for cell in row.cells):
        return [[None for cell in row.cells] for row in rows]
    page = pdf._get_page_dict(page_index)
    annots = pdf._resolve(page.mapping.get(PdfName("Annots")))
    if annots is not None and not isinstance(annots, (PdfArray, PdfNull)):
        raise PdfValidationException("Page annotations must be an array")
    refs = []
    links = []
    for row, top in zip(rows, tops):
        row_links = []
        for cell in row.cells:
            ref = None
            if cell.link is not None:
                value, key, label = cell.link
                x = left + cell.x
                annotation = PdfDictionary(
                    {
                        PdfName("Type"): PdfName("Annot"),
                        PdfName("Subtype"): PdfName("Link"),
                        PdfName("Rect"): PdfArray(
                            [
                                PdfNumber(v)
                                for v in (x, top - row.height, x + cell.width, top)
                            ]
                        ),
                        PdfName("Border"): PdfArray([PdfNumber(0) for _ in range(3)]),
                        PdfName("P"): pdf._page_ref_for_structure(page_index),
                        PdfName("Contents"): label,
                        PdfName(key): value,
                    }
                )
                ref = pdf._cos_doc.register_object(annotation)
                refs.append(ref)
            row_links.append(ref)
        links.append(row_links)
    if refs:
        # A shared /Annots array must not put the new links on another page.
        page.mapping[PdfName("Annots")] = PdfArray(
            (annots.items if isinstance(annots, PdfArray) else []) + refs
        )
    return links


def _link_structure_parents(
    pdf: Any, root: PdfDictionary, links: list[tuple[Any, Any]]
) -> None:
    """Associate annotations with Link elements using consecutive unique keys."""
    if not links:
        return
    tree = pdf._resolve(root.mapping[PdfName("ParentTree")])
    nums = pdf._resolve(tree.mapping[PdfName("Nums")])
    keys = (pdf._resolve(item) for item in nums.items[0::2])
    key = max(
        (int(item.value) for item in keys if isinstance(item, PdfNumber)),
        default=-1,
    ) + 1
    next_key = pdf._resolve(root.mapping.get(PdfName("ParentTreeNextKey")))
    if isinstance(next_key, PdfNumber):
        key = max(key, int(next_key.value))
    for annotation, link in links:
        nums.items.extend([PdfNumber(key), link])
        pdf._resolve(annotation).mapping[PdfName("StructParent")] = PdfNumber(key)
        key += 1
    root.mapping[PdfName("ParentTreeNextKey")] = PdfNumber(key)


def _register_table_structure(
    pdf: Any, page_index: int, rows: list[_RowBox], links: list[list[Any]]
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
    link_parents: list[tuple[Any, Any]] = []
    for row, row_links in zip(rows, links):
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
        for cell, annotation in zip(row.cells, row_links):
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
            if annotation is not None:
                link_elem = PdfDictionary(
                    {
                        PdfName("Type"): PdfName("StructElem"),
                        PdfName("S"): PdfName("Link"),
                        PdfName("P"): cell_ref,
                        PdfName("Pg"): page_ref,
                        PdfName("K"): PdfArray(
                            [
                                PdfNumber(mcid),
                                PdfDictionary(
                                    {
                                        PdfName("Type"): PdfName("OBJR"),
                                        PdfName("Obj"): annotation,
                                        PdfName("Pg"): page_ref,
                                    }
                                ),
                            ]
                        ),
                    }
                )
                link_ref = pdf._cos_doc.register_object(link_elem)
                cell_elem.mapping[PdfName("K")] = link_ref
                parent_array.items.append(link_ref)
                link_parents.append((annotation, link_ref))
                tag = "Link"
                page.mapping[PdfName("Tabs")] = PdfName("S")
            else:
                parent_array.items.append(cell_ref)
            cell_refs.append(cell_ref)
            row_marks.append((tag, mcid))
        if cell_refs:
            row_elem.mapping[PdfName("K")] = PdfArray(cell_refs)
        row_refs.append(row_ref)
        marks.append(row_marks)
    if row_refs:
        table_elem.mapping[PdfName("K")] = PdfArray(row_refs)
    _link_structure_parents(pdf, struct_root, link_parents)
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

    borders = style["borders"]
    if borders[0][0] > 0 and all(border == borders[0] for border in borders):
        parts.append(
            build_path_stream(
                GraphicsPath().rect(x, bottom, cell.width, height),
                stroke_color=borders[0][1],
                fill_color=None,
                line_width=borders[0][0],
            )
        )
    else:
        edges = (
            (x, top, x + cell.width, top),
            (x + cell.width, top, x + cell.width, bottom),
            (x + cell.width, bottom, x, bottom),
            (x, bottom, x, top),
        )
        for (width, color), (x0, y0, x1, y1) in zip(borders, edges):
            if width > 0:
                parts.append(
                    build_path_stream(
                        GraphicsPath().move_to(x0, y0).line_to(x1, y1),
                        stroke_color=color,
                        line_width=width,
                        line_cap="butt",
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
    for row in rows:
        for cell in row.cells:
            cell.link = _prepare_cell_link(pdf, cell)
            if cell.link is not None:
                coordinates = (
                    float(x) + cell.x, float(x) + cell.x + cell.width,
                    float(y), row.height, cell.width,
                )
                if (
                    not all(math.isfinite(value) for value in coordinates)
                    or row.height <= 0 or cell.width <= 0
                ):
                    raise PdfValidationException(
                        "Cell link rectangle must have finite, positive dimensions"
                    )
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
        isolation = None
        sections = getattr(pdf, "_open_graphics_sections", None)
        if pdf._lazy and not (sections and sections.get(current_page)):
            from .content_isolation import isolation_for

            isolation = isolation_for(
                pdf.get_page_content(current_page), budget=pdf._load_budget
            )
            if not isolation.complete:
                raise PdfValidationException("Cannot safely isolate table page content")
        resources = {name: face.resource(current_page) for name, face in faces.items()}
        fragments = [
            [
                _cell_content(
                    pdf, current_page, cell, float(x), top, row.height, faces, resources
                )
                for cell in row.cells
            ]
            for row, top in zip(page_rows, page_tops)
        ]
        links = _add_cell_links(pdf, current_page, page_rows, page_tops, float(x))
        marks = (
            _register_table_structure(pdf, current_page, page_rows, links)
            if tag
            else None
        )
        content: list[bytes] = []
        for index, row_fragments in enumerate(fragments):
            for position, fragment in enumerate(row_fragments):
                if marks is not None and fragment:
                    tag_name, mcid = marks[index][position]
                    fragment = wrap_marked_content(fragment, tag_name, mcid)
                content.append(fragment)
        if isolation is None:
            pdf._append_content_to_page(current_page, b"".join(content))
        else:
            pdf._append_content_to_cos_page(
                current_page, isolation.closing + b"".join(content),
                opening=isolation.opening,
            )
            pdf._extracted_text = None
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
            if current_page + 1 >= len(pdf.pages):
                next_top = pdf.pages[current_page][3] - (box[3] - float(y))
                repeated_height = sum(header.height for header in headers)
                if next_top - repeated_height - row.height < bottom_margin:
                    raise PdfValidationException(
                        "A row does not fit on a continuation page with its repeated headers"
                    )
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
