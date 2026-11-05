"""Imposition: putting several pages of a document onto one sheet.

Two arrangements, both built on the same two pieces -- importing a page as a form
XObject (:func:`.stamps.import_page_form`) and invoking it under a matrix
(:func:`.stamps.place`):

* **N-up**, a grid of pages on each sheet, which is what printing four pages to a
  side does.
* **A booklet**, two pages to a sheet in the order that reads correctly once the
  stack is folded down the middle -- the saddle-stitch order.

Each source page is scaled to fit its cell *proportionally* and centred in it, so
nothing is stretched and nothing is cut off. The page is placed as a reader shows
it, with its crop box and its ``/Rotate``, which is what
:func:`.stamps.import_page_form` brings across.
"""

from __future__ import annotations

import math
from typing import Any

from ..exceptions import PdfValidationException
from .content_authoring import build_path_stream, format_number, wrap_artifact
from .stamps import (
    import_page_form,
    multiply,
    page_form_matrix,
    place,
    scaling,
    translation,
    visible_box,
)

#: A cell to place a page in: ``(x, y, width, height)`` in the sheet's space.
Cell = tuple[float, float, float, float]


def _sheet_size(
    source: Any, page_size: Any, default_scale: tuple[float, float]
) -> tuple[float, float]:
    """The size of the sheet to impose onto."""
    if page_size is not None:
        width, height = _size_of(page_size)
        return width, height
    box = visible_box(source, 0)
    return (
        (box[2] - box[0]) * default_scale[0],
        (box[3] - box[1]) * default_scale[1],
    )


def _size_of(page_size: Any) -> tuple[float, float]:
    """*page_size* as a ``(width, height)`` pair, whatever shape it arrived in."""
    width = getattr(page_size, "width", None)
    height = getattr(page_size, "height", None)
    if width is not None and height is not None:
        return float(width), float(height)
    try:
        width, height = page_size
    except (TypeError, ValueError):
        raise PdfValidationException(
            "A sheet size is a PageSize or a (width, height) pair."
        ) from None
    return float(width), float(height)


def _grid_cells(
    sheet: tuple[float, float],
    *,
    rows: int,
    columns: int,
    margin: float,
    gutter: float,
) -> list[Cell]:
    """The cells of a *rows* x *columns* grid, in reading order (top row first)."""
    width = (sheet[0] - 2 * margin - gutter * (columns - 1)) / columns
    height = (sheet[1] - 2 * margin - gutter * (rows - 1)) / rows
    if width <= 0 or height <= 0:
        raise PdfValidationException(
            "The margin and the gutter leave no room for the pages on this sheet"
        )
    cells: list[Cell] = []
    for row in range(rows):
        for column in range(columns):
            x = margin + column * (width + gutter)
            # Top row first, which is the order a reader goes in; y counts up
            # from the bottom of the sheet.
            y = sheet[1] - margin - (row + 1) * height - row * gutter
            cells.append((x, y, width, height))
    return cells


def _fit(size: tuple[float, float], cell: Cell) -> Any:
    """The matrix that puts a page of *size* inside *cell*, scaled and centred."""
    x, y, width, height = cell
    page_width, page_height = size
    if page_width <= 0 or page_height <= 0:
        raise PdfValidationException("A page to impose has no size")
    factor = min(width / page_width, height / page_height)
    drawn = (page_width * factor, page_height * factor)
    return multiply(
        scaling(factor, factor),
        translation(x + (width - drawn[0]) / 2, y + (height - drawn[1]) / 2),
    )


def _crop_options(enabled: bool, length: float, offset: float, width: float):
    """Validate physical mark dimensions even when marks are disabled."""
    if not isinstance(enabled, bool):
        raise PdfValidationException("crop_marks must be a boolean")
    values = []
    for name, value, zero_allowed in (
        ("crop_mark_length", length, False),
        ("crop_mark_offset", offset, True),
        ("crop_mark_width", width, False),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise PdfValidationException(f"{name} must be a finite number")
        try:
            number = float(value)
        except OverflowError:
            raise PdfValidationException(f"{name} must be finite") from None
        if (
            not math.isfinite(number)
            or number < 0
            or (not zero_allowed and number == 0)
        ):
            raise PdfValidationException(
                f"{name} must be finite and {'non-negative' if zero_allowed else 'positive'}"
            )
        number = float(format_number(number))
        if not zero_allowed and number == 0:
            raise PdfValidationException(
                f"{name} is too small for PDF number precision"
            )
        values.append(number)
    return tuple(values) if enabled else None


def _crop_content(
    source, sheet, assignment, margin, gutter, options
) -> dict[int, bytes]:
    """Plan marks from displayed page bounds before importing any content.

    Performs early validation of crop mark placement using conservative cell bounds
    (worst-case scenario) before reading page content. This catches invalid layouts
    (e.g., marks extending beyond sheet bounds or inside the page area) without
    loading page streams.
    """
    from aspose_pdf.paths import GraphicsPath

    if options is None:
        return {}
    length, offset, width = options
    budget = source._load_budget
    count = sum(page is not None for _, page, _ in assignment)
    budget.check(count * 8, "max_container_items", "crop mark segments")
    budget.check(count * 16, "max_content_tokens", "crop mark path operators")
    paths, sizes = {}, {}
    # Early validation: check if marks fit when placed at cell bounds (worst case).
    # This avoids reading page content for obviously invalid layouts.
    # All cells have the same size, so we validate once and break.
    for sheet_index, source_index, cell in assignment:
        if source_index is None:
            continue
        # Compute the conservative (worst-case) page bounds: use cell bounds.
        # The actual page bounds will be within or equal to these.
        x, y, cell_width, cell_height = cell
        x0, y0 = x, y  # Bottom-left of cell
        x1, y1 = x + cell_width, y + cell_height  # Top-right of cell
        x_left, x_right = x0, x1
        y_bottom, y_top = y0, y1
        # Generate the mark segments using cell bounds.
        segments = (
            [(x_left - offset - length, edge, x_left - offset, edge) for edge in (y0, y1)]
            + [(x_right + offset, edge, x_right + offset + length, edge) for edge in (y0, y1)]
            + [(edge, y_bottom - offset - length, edge, y_bottom - offset) for edge in (x0, x1)]
            + [(edge, y_top + offset, edge, y_top + offset + length) for edge in (x0, x1)]
        )
        # Check if each mark fits within the sheet and outside the page area.
        sheet_width, sheet_height = sheet
        for a, b, c, d in segments:
            a, b, c, d = (float(format_number(value)) for value in (a, b, c, d))
            if a == c and b == d:
                raise PdfValidationException(
                    "Crop marks are too small at these sheet coordinates"
                )
            horizontal = b == d
            # Include stroke width in bounds check.
            bounds = (
                min(a, c) - (0 if horizontal else width / 2),
                min(b, d) - (width / 2 if horizontal else 0),
                max(a, c) + (0 if horizontal else width / 2),
                max(b, d) + (width / 2 if horizontal else 0),
            )
            # Mark must be outside the page area (cell bounds in worst case).
            # For horizontal marks (top/bottom), check horizontal position only.
            # For vertical marks (left/right), check vertical position only.
            # A mark is outside the page if it's fully left/right of the page.
            horiz_ok = bounds[2] <= x0 + 1e-6 or bounds[0] >= x1 - 1e-6
            # A mark is outside the page if it's fully below/above the page.
            vert_ok = bounds[3] <= y0 + 1e-6 or bounds[1] >= y1 - 1e-6
            if horizontal and not horiz_ok:
                raise PdfValidationException(
                    "Crop marks do not fit; increase margin or gutter, or reduce mark dimensions"
                )
            if not horizontal and not vert_ok:
                raise PdfValidationException(
                    "Crop marks do not fit; increase margin or gutter, or reduce mark dimensions"
                )
            # Mark must also stay within the sheet bounds.
            if bounds[0] < 0 or bounds[2] > sheet_width or bounds[1] < 0 or bounds[3] > sheet_height:
                raise PdfValidationException(
                    "Crop marks do not fit; increase margin or gutter, or reduce mark dimensions"
                )
        break  # Only need to validate once (all cells are the same size)
    
    def near(a, b):
        return math.isclose(a, b, rel_tol=0, abs_tol=1e-6)

    for sheet_index, source_index, cell in assignment:
        if source_index is None:
            continue
        if source_index not in sizes:
            _, size = page_form_matrix(
                visible_box(source, source_index),
                source.get_page_rotation(source_index),
            )
            if not all(math.isfinite(value) and value > 0 for value in size):
                raise PdfValidationException(
                    "Crop marks need finite, positive source page dimensions"
                )
            sizes[source_index] = size
        size = sizes[source_index]
        matrix = tuple(float(format_number(value)) for value in _fit(size, cell))
        scale, _, _, _, x0, y0 = matrix
        if scale <= 0:
            raise PdfValidationException(
                "The imposed page scale is too small for crop marks"
            )
        x1, y1 = x0 + scale * size[0], y0 + scale * size[1]
        x, y, cell_width, cell_height = cell
        # The area represents the page bounds (where content is placed).
        # Marks must be outside this area.
        area = (x0, y0, x1, y1)
        # Marks are placed at the page edge, not the cell edge.
        # x_left = x0 (page left edge), x_right = x1 (page right edge)
        # y_bottom = y0 (page bottom edge), y_top = y1 (page top edge)
        # The page may be letterboxed within the cell.
        x_left, x_right = x0, x1
        y_bottom, y_top = y0, y1
        segments = (
            [(x_left - offset - length, edge, x_left - offset, edge) for edge in (y0, y1)]
            + [(x_right + offset, edge, x_right + offset + length, edge) for edge in (y0, y1)]
            + [(edge, y_bottom - offset - length, edge, y_bottom - offset) for edge in (x0, x1)]
            + [(edge, y_top + offset, edge, y_top + offset + length) for edge in (x0, x1)]
        )
        path = paths.setdefault(sheet_index, GraphicsPath())
        for a, b, c, d in segments:
            # Use the coordinates and butt-cap stroke that the PDF will hold.
            a, b, c, d = (float(format_number(value)) for value in (a, b, c, d))
            if a == c and b == d:
                raise PdfValidationException(
                    "Crop marks are too small at these sheet coordinates"
                )
            horizontal = b == d
            bounds = (
                min(a, c) - (0 if horizontal else width / 2),
                min(b, d) - (width / 2 if horizontal else 0),
                max(a, c) + (0 if horizontal else width / 2),
                max(b, d) + (width / 2 if horizontal else 0),
            )
            # Marks must be outside the printable area.
            # For horizontal marks (left/right edges), only check horizontal position.
            # For vertical marks (top/bottom edges), only check vertical position.
            # Left/right check: mark must be fully left of or fully right of printable area
            horiz_ok = bounds[2] <= area[0] + 1e-6 or bounds[0] >= area[2] - 1e-6
            # Bottom/top check: mark must be fully below or fully above printable area
            vert_ok = bounds[3] <= area[1] + 1e-6 or bounds[1] >= area[3] - 1e-6
            # Horizontal marks only need to be horizontally outside; vertical marks only need vertically
            import sys
            print(f"DEBUG: sheet={sheet_index}, cell=({x},{y},{cell_width},{cell_height}), page=({x0},{y0},{x1},{y1})", file=sys.stderr)
            print(f"DEBUG: area={area}, x_left={x_left}, x_right={x_right}, y_bottom={y_bottom}, y_top={y_top}", file=sys.stderr)
            print(f"DEBUG: mark=({a},{b},{c},{d}), horizontal={horizontal}, bounds={bounds}", file=sys.stderr)
            print(f"DEBUG: horiz_ok={horiz_ok}, vert_ok={vert_ok}", file=sys.stderr)
            if horizontal and not horiz_ok:
                raise PdfValidationException(
                    "Crop marks do not fit; increase margin or gutter, or reduce mark dimensions"
                )
            if not horizontal and not vert_ok:
                raise PdfValidationException(
                    "Crop marks do not fit; increase margin or gutter, or reduce mark dimensions"
                )
            path.move_to(a, b).line_to(c, d)
    content, total = {}, 0
    for sheet_index, path in paths.items():
        data = wrap_artifact(
            build_path_stream(
                path,
                stroke_color=(0, 0, 0, 1),
                line_width=width,
                line_cap=0,
                dash=[],
            ),
            artifact_type="Layout",
        )
        budget.check(len(data), "max_content_stream_bytes", "crop mark content")
        budget.check(len(data), "max_decoded_stream_bytes", "crop mark content")
        total += len(data)
        budget.check(total, "max_total_decoded_bytes", "crop mark content")
        content[sheet_index] = data
    return content


def _mark_layout(source, sheet, margin, gutter, cells, sheets, options):
    if options is None:
        return
    if not all(math.isfinite(value) and value > 0 for value in sheet):
        raise PdfValidationException("Crop marks need a finite, positive sheet size")
    if not all(math.isfinite(value) and value >= 0 for value in (margin, gutter)):
        raise PdfValidationException(
            "Crop marks need finite, non-negative margin and gutter"
        )
    source._load_budget.check(cells, "max_container_items", "crop mark grid cells")
    source._load_budget.check_pages(sheets)


def _new_document(source: Any, sheet: tuple[float, float], count: int) -> Any:
    """A document of *count* blank sheets, under the source's resource limits."""
    from .simple_pdf import SimplePdf

    target = SimplePdf()
    target._load_limits = source._load_limits
    target._load_budget = source._load_budget
    target.pages = [(0.0, 0.0, sheet[0], sheet[1])] * count
    target.page_contents = [b""] * count
    target._ensure_cos()
    return target


def _place_pages(
    target: Any,
    source: Any,
    assignment: list[tuple[int, int | None, Cell]],
) -> None:
    """Place each ``(sheet, source page, cell)``; a ``None`` page leaves a blank.

    The import memo is shared across the whole run, so a font or an image several
    source pages have in common is copied **once**, not once per placement.
    """
    imported: dict[int, Any] = {}
    forms: dict[int, tuple[Any, tuple[float, float]]] = {}
    for sheet_index, source_index, cell in assignment:
        if source_index is None:
            continue
        built = forms.get(source_index)
        if built is None:
            built = import_page_form(target, source, source_index, imported=imported)
            forms[source_index] = built
        form, size = built
        place(target, sheet_index, form, _fit(size, cell), background=False)


def _impose_pages(source, sheet, sheets, assignment, marks):
    target = _new_document(source, sheet, sheets)
    try:
        _place_pages(target, source, assignment)
        for sheet_index, content in marks.items():
            target._append_content_to_page(sheet_index, content)
    except BaseException:
        target.dispose()
        raise
    return target


def n_up(
    source: Any,
    *,
    rows: int = 2,
    columns: int = 2,
    page_size: Any = None,
    margin: float = 0.0,
    gutter: float = 0.0,
    order: str = "row",
    crop_marks: bool = False,
    crop_mark_length: float = 9.0,
    crop_mark_offset: float = 3.0,
    crop_mark_width: float = 0.5,
) -> Any:
    """Put ``rows * columns`` pages of *source* on each sheet of a new document.

    The sheet is *page_size*, or the first source page's own size when none is
    given -- so four pages go onto a sheet the size of one, which is what printing
    4-up does. A landscape sheet for a 2-up of portrait pages is asked for by
    passing one: nothing is rotated behind the caller's back.

    *order* is ``"row"`` (left to right, top row first) or ``"column"`` (top to
    bottom, first column first).
    """
    source._ensure_not_disposed()
    options = _crop_options(
        crop_marks, crop_mark_length, crop_mark_offset, crop_mark_width
    )
    rows, columns = int(rows), int(columns)
    if rows < 1 or columns < 1:
        raise PdfValidationException("A grid needs at least one row and one column")
    count = len(source.pages)
    if not count:
        raise PdfValidationException("There are no pages to impose")
    margin, gutter = float(margin), float(gutter)
    for name, value in (("margin", margin), ("gutter", gutter)):
        if math.isnan(value) or math.isinf(value):
            raise PdfValidationException(f"{name} must be a finite number")
    if margin < 0 or gutter < 0:
        raise PdfValidationException("The margin and the gutter cannot be negative")
    if str(order).lower() not in ("row", "column"):
        raise PdfValidationException("order is 'row' or 'column'")

    sheet = _sheet_size(source, page_size, (1.0, 1.0))
    per_sheet = rows * columns
    sheets = (count + per_sheet - 1) // per_sheet
    _mark_layout(source, sheet, margin, gutter, per_sheet, sheets, options)
    cells = _grid_cells(sheet, rows=rows, columns=columns, margin=margin, gutter=gutter)
    if str(order).lower() == "column":
        # The same cells, taken down each column instead of along each row.
        cells = [
            cells[row * columns + column]
            for column in range(columns)
            for row in range(rows)
        ]
    assignment = [
        (index // per_sheet, index, cells[index % per_sheet]) for index in range(count)
    ]
    marks = _crop_content(source, sheet, assignment, margin, gutter, options)
    return _impose_pages(source, sheet, sheets, assignment, marks)


def booklet_order(count: int) -> list[int | None]:
    """The page order a saddle-stitched booklet is printed in, 0-based.

    Pairs are laid out so that folding the printed stack down the middle reads in
    order: the last page beside the first, the second beside the second-last, and
    so on, alternating which side each pair starts on. The count is padded to a
    multiple of four with blanks (``None``), because a folded sheet carries four
    pages whether they are all used or not.
    """
    if count < 1:
        raise PdfValidationException("There are no pages to make a booklet of")
    padded = count + (-count % 4)
    pages: list[int | None] = [
        index if index < count else None for index in range(padded)
    ]
    order: list[int | None] = []
    for index in range(padded // 2):
        first, last = pages[index], pages[padded - 1 - index]
        order.extend((last, first) if index % 2 == 0 else (first, last))
    return order


def booklet(
    source: Any,
    *,
    page_size: Any = None,
    margin: float = 0.0,
    gutter: float = 0.0,
    crop_marks: bool = False,
    crop_mark_length: float = 9.0,
    crop_mark_offset: float = 3.0,
    crop_mark_width: float = 0.5,
) -> Any:
    """Impose *source* as a saddle-stitched booklet, two pages to a sheet.

    The sheet is twice the width of a source page unless *page_size* says
    otherwise, since that is what holds two of them side by side. Printed on both
    sides, stacked and folded down the middle, the result reads in order; see
    :func:`booklet_order`.
    """
    source._ensure_not_disposed()
    options = _crop_options(
        crop_marks, crop_mark_length, crop_mark_offset, crop_mark_width
    )
    if not len(source.pages):
        raise PdfValidationException("There are no pages to impose")
    sheet = _sheet_size(source, page_size, (2.0, 1.0))
    order = booklet_order(len(source.pages))
    margin, gutter = float(margin), float(gutter)
    for name, value in (("margin", margin), ("gutter", gutter)):
        if math.isnan(value) or math.isinf(value):
            raise PdfValidationException(f"{name} must be a finite number")
    sheets = len(order) // 2
    _mark_layout(source, sheet, margin, gutter, 2, sheets, options)
    cells = _grid_cells(sheet, rows=1, columns=2, margin=margin, gutter=gutter)
    assignment = [
        (index // 2, source_index, cells[index % 2])
        for index, source_index in enumerate(order)
    ]
    marks = _crop_content(source, sheet, assignment, margin, gutter, options)
    return _impose_pages(source, sheet, sheets, assignment, marks)
