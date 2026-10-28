"""Split displayed page views into a uniform grid without rasterizing content."""

from __future__ import annotations

import math

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import _LoadBudget

from .blank_pages import _number
from .imposition import _grid_cells
from .spread_split import _map_regions, _split_pages
from .stamps import visible_box
from .text_selection import page_indexes


def _cells(source, index, rows, columns, gutter, order):
    """Plan cells in the displayed CropBox and map them back to source coordinates."""
    x0, y0, x1, y1 = visible_box(source, index)
    width, height = x1 - x0, y1 - y0
    if (
        not all(math.isfinite(value) for value in (x0, y0, x1, y1, width, height))
        or min(width, height) <= 0
    ):
        raise PdfValidationException(
            "A grid page must have finite positive visible dimensions"
        )
    turn = source.get_page_rotation(index) % 360
    if turn not in (0, 90, 180, 270):
        raise PdfValidationException(
            "Grid page rotation must be a multiple of 90 degrees"
        )
    displayed = (height, width) if turn in (90, 270) else (width, height)
    cells = _grid_cells(displayed, rows=rows, columns=columns, margin=0, gutter=gutter)
    if order == "column":
        cells = [
            cells[row * columns + column]
            for column in range(columns)
            for row in range(rows)
        ]
    return _map_regions(
        x0,
        y0,
        width,
        height,
        turn,
        (
            (x, y, x + cell_width, y + cell_height)
            for x, y, cell_width, cell_height in cells
        ),
    )


def split_grid(document, rows, columns, *, pages, order, gutter, flatten_fields):
    """Preflight the complete output and every cell before importing source pages."""
    for name, value in (("rows", rows), ("columns", columns)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise PdfValidationException(f"{name} must be a positive integer")
    if not isinstance(order, str) or order.lower() not in ("row", "column"):
        raise PdfValidationException("order must be 'row' or 'column'")
    if not isinstance(flatten_fields, bool):
        raise PdfValidationException("flatten_fields must be a boolean")
    gutter = _number(gutter, "gutter")
    if gutter < 0:
        raise PdfValidationException("gutter must be non-negative")
    budget = _LoadBudget(document.load_limits)
    count = document.page_count
    selection = page_indexes(pages, count, limits=budget.limits)
    selected = range(count) if selection is None else sorted(set(selection))
    total = count + len(selected) * (rows * columns - 1)
    budget.check_pages(total)
    budget.check(total, "max_container_items", "grid output pages")
    budget.check_objects(2 + total * 2)
    plan = {
        index: _cells(document._engine_pdf, index, rows, columns, gutter, order.lower())
        for index in selected
    }
    return _split_pages(
        document, plan, budget, context="grid", flatten_fields=flatten_fields
    )
