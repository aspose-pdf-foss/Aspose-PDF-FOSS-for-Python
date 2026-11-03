"""Tables: rows, cells, and the measurements that put them on a page.

A table is built and then placed, which is what a package with no flow layout
can honestly offer::

    from aspose_pdf import Document, PageSize, Table

    table = Table(column_widths=[220, 70, 90], border_width=0.75)
    table.add_row(["Item", "Qty", "Price"], header=True)
    table.add_row(["Widget, the larger kind", "2", "9.99"])
    table.add_row(["Grommet", "14", "0.40"])

    with Document() as document:
        page = document.pages.add(PageSize.A4)
        page.add_table(table, 60, 760)
        document.save("invoice.pdf")

Cell text is wrapped to the column's width with the font's own advances, rows
grow to hold what they are given, and a table taller than the page continues onto
the next one -- repeating the header rows, which is the reason a header is marked
as one. Everything is measured in PDF points, with ``y`` counting up from the
bottom of the page as it does everywhere else in the package.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.interactive import Action, Destination
from aspose_pdf.load_limits import PdfLoadLimits

__all__ = ["Cell", "Row", "Table"]

#: How a cell's text sits across its width and down its height.
_HORIZONTAL = ("left", "center", "centre", "right")
_VERTICAL = ("top", "middle", "center", "centre", "bottom")
_BORDER_SIDES = ("top", "right", "bottom", "left")


def _cell_link(value: Any) -> Action | Destination | str | None:
    """Check the target type without needing a document or resolving a page."""
    if value is not None and not isinstance(value, (Action, Destination, str)):
        raise TypeError("link must be an Action, a Destination, a destination name or None")
    return value


def _positive(value: Any, name: str, *, allow_zero: bool = False) -> float:
    """*value* as a finite number, refused when it cannot be a measurement."""
    if isinstance(value, bool):
        raise PdfValidationException(f"{name} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise PdfValidationException(f"{name} must be a number") from None
    if number != number or number in (float("inf"), float("-inf")):
        raise PdfValidationException(f"{name} must be finite")
    if number < 0 or (number == 0 and not allow_zero):
        limit = "not be negative" if allow_zero else "be above zero"
        raise PdfValidationException(f"{name} must {limit}")
    return number


def _border_sides(value: Any, name: str) -> dict[str, Any] | None:
    """Validate and copy a partial mapping of side widths or colours."""
    if value is None:
        return None
    if not isinstance(value, Mapping) or any(
        side not in _BORDER_SIDES for side in value
    ):
        raise PdfValidationException(
            f"{name} must map side names: top, right, bottom, left"
        )
    from aspose_pdf.engine.content_authoring import normalize_color

    result = {}
    for side, item in value.items():
        label = f"{name}[{side!r}]"
        if name == "border_widths":
            result[side] = _positive(item, label, allow_zero=True)
        elif item is None:
            result[side] = None
        else:
            try:
                result[side] = normalize_color(item)
            except (PdfValidationException, OverflowError) as exc:
                raise PdfValidationException(f"{label}: {exc}") from exc
    return result


def _alignment(value: Any, allowed: tuple[str, ...], name: str) -> str | None:
    """One of *allowed*, normalised, or ``None`` when nothing was said."""
    if value is None:
        return None
    key = str(value).strip().lower()
    if key not in allowed:
        raise PdfValidationException(
            f"{name} is one of: " + ", ".join(sorted(set(allowed)))
        )
    if key in ("centre", "center"):
        return "center" if name == "alignment" else "middle"
    return key


@dataclass
class Cell:
    """One cell of a table: what it says, and how it says it.

    Every style is ``None`` by default, which means *take the row's, then the
    table's* -- so a table is styled once and a cell only says where it differs.
    ``border_widths`` and ``border_colors`` map any of ``top``, ``right``,
    ``bottom`` and ``left`` to overrides of the uniform border style. A width
    of zero or a side colour of ``None`` hides that side.

    ``link`` makes the whole cell clickable: pass an ``Action`` such as
    ``URIAction``, a ``Destination``, or a named destination string. Strings
    name destinations; wrap web addresses in ``URIAction``. ``None`` adds no
    link. Links keep the cell's existing text and visual style.
    Local destination pages must exist when the table is placed. A linked
    header cell gets a separate link each time the header is repeated.
    """

    text: str = ""
    column_span: int = 1
    alignment: str | None = None
    vertical_alignment: str | None = None
    background_color: Any = None
    text_color: Any = None
    font_size: float | None = None
    font_name: str | None = None
    padding: float | None = None
    border_width: float | None = None
    border_color: Any = None
    is_header: bool | None = None
    border_widths: Mapping[str, float] | None = None
    border_colors: Mapping[str, Any] | None = None
    link: Action | Destination | str | None = None

    def __post_init__(self) -> None:
        self.text = "" if self.text is None else str(self.text)
        if isinstance(self.column_span, bool) or int(self.column_span) < 1:
            raise PdfValidationException("column_span is a whole number from one")
        self.column_span = int(self.column_span)
        self.alignment = _alignment(self.alignment, _HORIZONTAL, "alignment")
        self.vertical_alignment = _alignment(
            self.vertical_alignment, _VERTICAL, "vertical_alignment"
        )
        if self.font_size is not None:
            self.font_size = _positive(self.font_size, "font_size")
        if self.padding is not None:
            self.padding = _positive(self.padding, "padding", allow_zero=True)
        if self.border_width is not None:
            self.border_width = _positive(
                self.border_width, "border_width", allow_zero=True
            )
        self.border_widths = _border_sides(self.border_widths, "border_widths")
        self.border_colors = _border_sides(self.border_colors, "border_colors")
        _cell_link(self.link)


@dataclass
class Row:
    """One row of a table: its cells, and what they have in common.

    Border defaults override the table's, with per-side mappings applied after
    uniform width and colour. Each cell can override the resulting defaults.
    """

    cells: list[Cell] = field(default_factory=list)
    height: float | None = None
    background_color: Any = None
    text_color: Any = None
    font_size: float | None = None
    font_name: str | None = None
    header: bool = False
    border_width: float | None = None
    border_color: Any = None
    border_widths: Mapping[str, float] | None = None
    border_colors: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        self.cells = [
            cell if isinstance(cell, Cell) else Cell(str(cell)) for cell in self.cells
        ]
        if self.height is not None:
            self.height = _positive(self.height, "height")
        if self.font_size is not None:
            self.font_size = _positive(self.font_size, "font_size")
        self.header = bool(self.header)
        if self.border_width is not None:
            self.border_width = _positive(
                self.border_width, "border_width", allow_zero=True
            )
        self.border_widths = _border_sides(self.border_widths, "border_widths")
        self.border_colors = _border_sides(self.border_colors, "border_colors")

    def add_cell(self, text: Any = "", **options: Any) -> Cell:
        """Append a cell and return it."""
        cell = text if isinstance(text, Cell) else Cell(str(text), **options)
        if isinstance(text, Cell) and options:
            raise PdfValidationException(
                "A Cell carries its own options; pass one or the other"
            )
        self.cells.append(cell)
        return cell

    @property
    def column_count(self) -> int:
        """How many columns this row covers, spans counted."""
        return sum(cell.column_span for cell in self.cells)


@dataclass
class Table:
    """A table of rows, ready to be placed on a page.

    ``column_widths`` are in points and decide the table's width. Without them,
    ``columns`` (or the widest row) splits ``width`` -- or the room left on the
    page -- into equal columns. Set ``auto_column_widths=True`` to distribute
    that space by measured content instead. Fonts, sizes, padding, headers and
    column spans are considered. Explicit ``column_widths`` take precedence.

    The styles here are the defaults every row and cell falls back to:
    ``font_name`` and ``font_size`` for the text, ``font`` to embed a Unicode
    font instead, ``padding`` inside each cell, ``border_width`` and
    ``border_color`` for the lines, ``background_color`` behind the cells, and
    ``header_background_color``/``header_text_color``/``header_font_size`` for the
    rows marked as headers. ``repeat_header`` repeats those rows at the top of
    every page the table continues onto.

    ``border_widths`` and ``border_colors`` are partial mappings keyed by
    ``top``, ``right``, ``bottom`` and ``left``. At each level (table, row, cell),
    uniform border styles override inherited defaults, then per-side entries
    override the uniform styles. Width zero or a side colour of ``None`` hides
    that side. Width and colour inherit independently.
    """

    rows: list[Row] = field(default_factory=list)
    column_widths: list[float] | None = None
    columns: int | None = None
    width: float | None = None
    padding: float = 4.0
    font_name: str = "Helvetica"
    font_size: float = 10.0
    font: Any = None
    text_color: Any = (0.0, 0.0, 0.0)
    alignment: str = "left"
    vertical_alignment: str = "top"
    line_height: float | None = None
    row_height: float | None = None
    border_width: float = 0.5
    border_color: Any = (0.0, 0.0, 0.0)
    background_color: Any = None
    header_background_color: Any = None
    header_text_color: Any = None
    header_font_size: float | None = None
    repeat_header: bool = True
    border_widths: Mapping[str, float] | None = None
    border_colors: Mapping[str, Any] | None = None
    auto_column_widths: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.auto_column_widths, bool):
            raise PdfValidationException("auto_column_widths must be a boolean")
        self.rows = [
            row if isinstance(row, Row) else Row(list(row)) for row in self.rows
        ]
        if self.column_widths is not None:
            widths = [
                _positive(value, "a column width") for value in self.column_widths
            ]
            if not widths:
                raise PdfValidationException("column_widths names no column")
            self.column_widths = widths
        if self.columns is not None:
            if isinstance(self.columns, bool) or int(self.columns) < 1:
                raise PdfValidationException("columns is a whole number from one")
            self.columns = int(self.columns)
        if self.width is not None:
            self.width = _positive(self.width, "width")
        self.padding = _positive(self.padding, "padding", allow_zero=True)
        self.font_size = _positive(self.font_size, "font_size")
        self.border_width = _positive(
            self.border_width, "border_width", allow_zero=True
        )
        self.border_widths = _border_sides(self.border_widths, "border_widths")
        self.border_colors = _border_sides(self.border_colors, "border_colors")
        if self.line_height is not None:
            self.line_height = _positive(self.line_height, "line_height")
        if self.row_height is not None:
            self.row_height = _positive(self.row_height, "row_height")
        if self.header_font_size is not None:
            self.header_font_size = _positive(self.header_font_size, "header_font_size")
        self.alignment = _alignment(self.alignment, _HORIZONTAL, "alignment") or "left"
        self.vertical_alignment = (
            _alignment(self.vertical_alignment, _VERTICAL, "vertical_alignment")
            or "top"
        )

    # -- building ----------------------------------------------------------

    @classmethod
    def from_csv(
        cls,
        source: str | Path | bytes | bytearray | BinaryIO,
        *,
        encoding: str = "utf-8-sig",
        delimiter: str = ",",
        header: bool = True,
        limits: PdfLoadLimits | None = None,
        **options: Any,
    ) -> Table:
        """Build an editable table from a CSV path, bytes or binary stream.

        The first nonblank record is a header by default. ``header=False``
        keeps it as an ordinary row. All cells remain exact strings, including
        leading zeros, whitespace, empty cells, quotes and embedded newlines.
        Blank or repeated header values are allowed. Blank records are skipped.
        Empty input, unequal row widths and malformed CSV are refused.

        ``options`` are Table constructor settings, including column widths,
        fonts, colors and ``repeat_header``. Rows come only from the CSV.
        Binary streams are read from their current position and left open.
        Parsing finishes before returning and obeys ``limits``. Place the
        result with ``Page.add_table()`` to wrap and paginate its rows.
        """
        from aspose_pdf.engine.csv_import import read_rows, validate_options
        from aspose_pdf.load_limits import _LoadBudget

        if not isinstance(header, bool):
            raise TypeError("header must be a boolean")
        budget = _LoadBudget(limits)
        validate_options(encoding, delimiter)
        table = cls(rows=[], **options)
        for _line_number, row in read_rows(
            source, encoding=encoding, delimiter=delimiter, budget=budget
        ):
            table.add_row(row, header=header and not table.rows)
        if not table.rows:
            raise PdfValidationException("CSV source must contain at least one row")
        return table

    def add_row(
        self,
        cells: Any = (),
        *,
        header: bool = False,
        **options: Any,
    ) -> Row:
        """Append a row and return it.

        *cells* is a sequence of strings or :class:`Cell` objects -- or a
        :class:`Row`, which is appended as it is.
        """
        if isinstance(cells, Row):
            if options or header:
                raise PdfValidationException(
                    "A Row carries its own options; pass one or the other"
                )
            self.rows.append(cells)
            return cells
        if isinstance(cells, (str, bytes)):
            raise PdfValidationException(
                "A row is a sequence of cells; a single string is one cell, so "
                "pass [text]"
            )
        row = Row(list(cells), header=header, **options)
        self.rows.append(row)
        return row

    def add_header_row(self, cells: Any = (), **options: Any) -> Row:
        """Append a row marked as a header -- repeated when the table breaks."""
        return self.add_row(cells, header=True, **options)

    @property
    def column_count(self) -> int:
        """How many columns the table has, from its widths or its widest row."""
        if self.column_widths is not None:
            return len(self.column_widths)
        if self.columns is not None:
            return self.columns
        return max((row.column_count for row in self.rows), default=0)

    def __len__(self) -> int:
        return len(self.rows)

    def __iter__(self):
        return iter(self.rows)

    def __getitem__(self, index: int) -> Row:
        return self.rows[index]

    def __repr__(self) -> str:
        return f"Table({len(self.rows)} rows, {self.column_count} columns)"
