"""Detached text-table results and CSV export, without optional dependencies."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aspose_pdf.exceptions import PdfValidationException

__all__ = ["ExtractedTable"]


@dataclass(frozen=True, slots=True)
class ExtractedTable:
    """An inferred table, independent of the document it came from.

    ``page_index`` is zero-based. ``rows`` is a rectangular tuple of tuples;
    :meth:`to_list` returns editable lists. A missing cell is an empty string.
    A horizontal merge keeps its text in the first column and leaves covered
    columns empty. ``column_spans`` has the same shape as ``rows`` and records
    each cell's inferred width in columns (one for ordinary and empty cells).

    All rows are data: no header is invented, removed or renamed. Recognition
    follows the HTML/Markdown export's layout heuristics, not PDF structure
    tags or drawn borders. Cell whitespace is normalized to single spaces;
    scanned images, multiline cells and vertical merges are not reconstructed.
    """

    page_index: int
    rows: tuple[tuple[str, ...], ...]
    column_spans: tuple[tuple[int, ...], ...] = ()

    def __post_init__(self) -> None:
        if (
            isinstance(self.page_index, bool)
            or not isinstance(self.page_index, int)
            or self.page_index < 0
        ):
            raise PdfValidationException("page_index must be a non-negative integer")
        rows = _matrix(self.rows, "rows")
        width = len(rows[0]) if rows else 0
        if any(len(row) != width for row in rows):
            raise PdfValidationException("rows must be rectangular")
        if any(not isinstance(cell, str) for row in rows for cell in row):
            raise PdfValidationException("Every table cell must be a string")
        spans = _matrix(self.column_spans, "column_spans")
        if not spans:
            spans = tuple((1,) * width for _ in rows)
        if len(spans) != len(rows) or any(len(row) != width for row in spans):
            raise PdfValidationException("column_spans must have the same shape as rows")
        for row_index, row in enumerate(spans):
            for column, span in enumerate(row):
                if (
                    isinstance(span, bool)
                    or not isinstance(span, int)
                    or not 1 <= span <= width - column
                ):
                    raise PdfValidationException(
                        "Each column span must be a positive integer within the row"
                    )
                if span > 1 and any(
                    rows[row_index][covered] or row[covered] != 1
                    for covered in range(column + 1, column + span)
                ):
                    raise PdfValidationException(
                        "Columns covered by a span must be empty and have span one"
                    )
        object.__setattr__(self, "rows", rows)
        object.__setattr__(self, "column_spans", spans)

    @property
    def row_count(self) -> int:
        """Number of rows, including any inferred header row."""
        return len(self.rows)

    @property
    def column_count(self) -> int:
        """Number of columns, including empty and covered columns."""
        return len(self.rows[0]) if self.rows else 0

    def to_list(self) -> list[list[str]]:
        """Return a fresh list of row lists without changing this snapshot."""
        return [list(row) for row in self.rows]

    def to_csv(self, *, delimiter: str = ",") -> str:
        """Return CSV with CRLF records and standard double-quote escaping.

        ``delimiter`` is one character, such as a comma, semicolon or tab.
        Empty and covered cells remain separate columns. Numbers and formula
        text are preserved as strings, without type conversion or rewriting.
        """
        if (
            not isinstance(delimiter, str)
            or len(delimiter) != 1
            or delimiter in '\r\n"\x00'
        ):
            raise PdfValidationException(
                "delimiter must be one character other than a quote, newline or NUL"
            )
        output = io.StringIO(newline="")
        csv.writer(output, delimiter=delimiter, lineterminator="\r\n").writerows(self.rows)
        return output.getvalue()

    def save_csv(
        self,
        destination: str | Path,
        *,
        delimiter: str = ",",
        encoding: str = "utf-8",
        overwrite: bool = True,
    ) -> Path:
        """Save one table atomically and return its path.

        Parent directories are created as needed. ``encoding="utf-8-sig"``
        adds a BOM for spreadsheet applications that require one. Encoding or
        write failures leave an existing file intact. ``overwrite=False``
        refuses an existing destination with :class:`FileExistsError`.
        """
        from aspose_pdf._export_files import write_files

        target = Path(destination)
        data = self.to_csv(delimiter=delimiter).encode(encoding)
        write_files([(target, data)], overwrite)
        return target


def _matrix(value: Any, name: str) -> tuple[tuple[Any, ...], ...]:
    """Snapshot a nested iterable without accepting strings as rows."""
    try:
        if isinstance(value, (str, bytes)):
            raise TypeError
        result = []
        for row in value:
            if isinstance(row, (str, bytes)):
                raise TypeError
            result.append(tuple(row))
        return tuple(result)
    except TypeError:
        raise PdfValidationException(f"{name} must be an iterable of rows") from None


def _page_tables(pdf: Any, page_index: int, clip_to_page: bool) -> list[ExtractedTable]:
    """Reuse layout analysis without images or markup's cell-text truncation."""
    from aspose_pdf.engine.text_export import page_blocks

    return [
        ExtractedTable(page_index, block.rows, block.spans)
        for block in page_blocks(
            pdf, page_index, include_images=False,
            clip_to_page=clip_to_page, cell_text_limit=None,
        )
        if block.kind == "table"
    ]
