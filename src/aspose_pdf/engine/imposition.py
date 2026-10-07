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

from typing import Any

from ..exceptions import PdfValidationException
from .stamps import import_page_form, multiply, place, scaling, translation, visible_box

#: A cell to place a page in: ``(x, y, width, height)`` in the sheet's space.
Cell = tuple[float, float, float, float]


def _sheet_size(source: Any, page_size: Any, default_scale: tuple[float, float]) -> tuple[float, float]:
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


def n_up(
    source: Any,
    *,
    rows: int = 2,
    columns: int = 2,
    page_size: Any = None,
    margin: float = 0.0,
    gutter: float = 0.0,
    order: str = "row",
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
    rows, columns = int(rows), int(columns)
    if rows < 1 or columns < 1:
        raise PdfValidationException("A grid needs at least one row and one column")
    count = len(source.pages)
    if not count:
        raise PdfValidationException("There are no pages to impose")
    margin, gutter = float(margin), float(gutter)
    if margin < 0 or gutter < 0:
        raise PdfValidationException("The margin and the gutter cannot be negative")
    if str(order).lower() not in ("row", "column"):
        raise PdfValidationException("order is 'row' or 'column'")

    sheet = _sheet_size(source, page_size, (1.0, 1.0))
    cells = _grid_cells(sheet, rows=rows, columns=columns, margin=margin, gutter=gutter)
    if str(order).lower() == "column":
        # The same cells, taken down each column instead of along each row.
        cells = [
            cells[row * columns + column]
            for column in range(columns)
            for row in range(rows)
        ]
    per_sheet = rows * columns
    sheets = (count + per_sheet - 1) // per_sheet
    target = _new_document(source, sheet, sheets)
    assignment = [
        (index // per_sheet, index, cells[index % per_sheet]) for index in range(count)
    ]
    _place_pages(target, source, assignment)
    return target


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
) -> Any:
    """Impose *source* as a saddle-stitched booklet, two pages to a sheet.

    The sheet is twice the width of a source page unless *page_size* says
    otherwise, since that is what holds two of them side by side. Printed on both
    sides, stacked and folded down the middle, the result reads in order; see
    :func:`booklet_order`.
    """
    source._ensure_not_disposed()
    if not len(source.pages):
        raise PdfValidationException("There are no pages to impose")
    sheet = _sheet_size(source, page_size, (2.0, 1.0))
    order = booklet_order(len(source.pages))
    cells = _grid_cells(sheet, rows=1, columns=2, margin=float(margin), gutter=float(gutter))
    sheets = len(order) // 2
    target = _new_document(source, sheet, sheets)
    assignment = [
        (index // 2, source_index, cells[index % 2])
        for index, source_index in enumerate(order)
    ]
    _place_pages(target, source, assignment)
    return target
