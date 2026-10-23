"""Immutable layout-block snapshots and versioned JSON structure export."""

from __future__ import annotations

import base64
import io
import json
import math
from collections.abc import Iterable
from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, Any

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import _LoadBudget
from aspose_pdf.table_extraction import ExtractedTable

if TYPE_CHECKING:
    from aspose_pdf.document import Document

__all__ = ["ExtractedBlock"]


@dataclass(frozen=True, slots=True)
class ExtractedBlock:
    """A detached heading, paragraph, list, table or figure in reading order.

    ``page_index`` is zero-based. ``x`` and ``y`` locate the first text
    element's baseline origin, or a figure's center, in unrotated PDF user
    space with a lower-left origin; they are not a bounding box.
    ``level`` is a heading's inferred level, otherwise zero.
    Lists carry marker-free ``items`` and ``ordered``. Tables carry rectangular
    ``rows`` and ``column_spans``, following :class:`ExtractedTable`.
    Figures carry ``alt`` and optional PNG ``image`` bytes. Recognition uses
    the HTML/Markdown layout heuristics, without OCR or semantic guarantees.
    """

    page_index: int
    kind: str
    x: float
    y: float
    text: str = ""
    level: int = 0
    items: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()
    column_spans: tuple[tuple[int, ...], ...] = ()
    ordered: bool = False
    alt: str = ""
    image: bytes | None = None

    def __post_init__(self) -> None:
        if self.kind not in ("heading", "paragraph", "list", "table", "figure"):
            raise PdfValidationException("Unknown extracted block kind")
        for name in ("x", "y"):
            value = getattr(self, name)
            try:
                finite = math.isfinite(value)
            except (TypeError, ValueError, OverflowError):
                finite = False
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not finite
            ):
                raise PdfValidationException(f"{name} must be a finite coordinate")
        if not isinstance(self.text, str) or not isinstance(self.alt, str):
            raise PdfValidationException("Block text and alt must be strings")
        if (
            isinstance(self.level, bool)
            or not isinstance(self.level, int)
            or not 0 <= self.level <= 6
            or (self.kind == "heading") != (self.level > 0)
        ):
            raise PdfValidationException("Only headings have a level from one to six")
        if not isinstance(self.ordered, bool):
            raise PdfValidationException("ordered must be a boolean")
        if self.image is not None and not isinstance(self.image, bytes):
            raise PdfValidationException("image must be PNG bytes or None")
        try:
            if isinstance(self.items, (str, bytes, bytearray)):
                raise TypeError
            items = tuple(self.items)
        except TypeError:
            raise PdfValidationException("items must be an iterable of strings") from None
        if any(not isinstance(item, str) for item in items):
            raise PdfValidationException("List items must be strings")
        table = ExtractedTable(self.page_index, self.rows, self.column_spans)
        object.__setattr__(self, "items", items)
        object.__setattr__(self, "rows", table.rows)
        object.__setattr__(self, "column_spans", table.column_spans)

    def to_dict(self) -> dict[str, Any]:
        """Return independent JSON-ready data; PNG bytes become a data URI."""
        return {
            "page_index": self.page_index,
            "kind": self.kind,
            "x": self.x,
            "y": self.y,
            "text": self.text,
            "level": self.level,
            "items": list(self.items),
            "rows": [list(row) for row in self.rows],
            "column_spans": [list(row) for row in self.column_spans],
            "ordered": self.ordered,
            "alt": self.alt,
            "image": (
                "data:image/png;base64," + base64.b64encode(self.image).decode("ascii")
                if self.image is not None else None
            ),
        }


def extract_blocks(
    document: Document,
    pages: Iterable[int] | None,
    include_images: bool,
    paragraph_gap: float | None,
    clip_to_page: bool,
) -> list[ExtractedBlock]:
    """Validate a complete selection before reading only its page streams."""
    from aspose_pdf.engine.text_export import page_blocks
    from aspose_pdf.engine.text_selection import page_indexes

    for name, value in (("include_images", include_images), ("clip_to_page", clip_to_page)):
        if not isinstance(value, bool):
            raise PdfValidationException(f"{name} must be a boolean")
    if paragraph_gap is not None:
        try:
            if isinstance(paragraph_gap, (bool, str, bytes)):
                raise ValueError
            paragraph_gap = float(paragraph_gap)
            if not math.isfinite(paragraph_gap) or paragraph_gap <= 0:
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            raise PdfValidationException(
                "paragraph_gap must be a finite positive number of font sizes"
            ) from None
    budget = _LoadBudget(document.load_limits)
    selected = page_indexes(pages, document.page_count, limits=document.load_limits)
    selected = range(document.page_count) if selected is None else selected
    budget.check_pages(len(selected))
    budget.check(len(selected), "max_container_items", "block extraction page selection")
    result = []
    retained = 0
    for index in selected:
        blocks = page_blocks(
            document._engine_pdf, index, include_images=include_images,
            paragraph_gap=paragraph_gap, clip_to_page=clip_to_page, cell_text_limit=None,
        )
        for block in blocks:
            budget.check(len(result) + 1, "max_container_items", "extracted blocks")
            for text in (block.text, block.alt, *block.items):
                retained = _text_bytes(text, retained, budget)
            for row in block.rows:
                for cell in row:
                    retained = _text_bytes(cell, retained, budget)
            retained += len(block.image or b"")
            budget.check(retained, "max_total_decoded_bytes", "extracted block data")
            result.append(ExtractedBlock(
                index, block.kind, block.x, block.y, text=block.text, level=block.level,
                items=block.items, rows=block.rows, column_spans=block.spans,
                ordered=block.ordered, alt=block.alt, image=block.image,
            ))
    return result


def to_json(blocks: list[ExtractedBlock], budget: _LoadBudget) -> bytes:
    """Bound escaping and base64 work before growing the JSON output buffer."""
    with io.BytesIO() as output:
        def write(data: bytes) -> None:
            budget.check(output.tell() + len(data), "max_codec_work_bytes", "block JSON bytes")
            output.write(data)

        def value(raw: Any) -> None:
            if isinstance(raw, str):
                write(b'"')
                for offset in range(0, len(raw), 4096):
                    chunk = json.dumps(raw[offset:offset + 4096], ensure_ascii=False)[1:-1]
                    write(chunk.encode("utf-8"))
                write(b'"')
            elif isinstance(raw, bytes):
                write(b'"data:image/png;base64,')
                # Multiples of three keep concatenated base64 chunks valid.
                for offset in range(0, len(raw), 12288):
                    write(base64.b64encode(raw[offset:offset + 12288]))
                write(b'"')
            elif isinstance(raw, tuple):
                write(b"[")
                for index, entry in enumerate(raw):
                    if index:
                        write(b",")
                    value(entry)
                write(b"]")
            else:
                write(json.dumps(raw, allow_nan=False).encode("ascii"))

        try:
            write(b'{"schema_version":1,"blocks":[')
            for index, block in enumerate(blocks):
                if index:
                    write(b",")
                write(b"{")
                for position, item in enumerate(fields(block)):
                    if position:
                        write(b",")
                    value(item.name)
                    write(b":")
                    value(getattr(block, item.name))
                write(b"}")
            write(b"]}\n")
        except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
            raise PdfValidationException("Blocks cannot be encoded as UTF-8 JSON") from exc
        return output.getvalue()


def _text_bytes(text: str, retained: int, budget: _LoadBudget) -> int:
    """Account for Unicode in bounded pieces, including long table cells."""
    try:
        for offset in range(0, len(text), 4096):
            retained += len(text[offset:offset + 4096].encode("utf-8"))
            budget.check(retained, "max_total_decoded_bytes", "extracted block data")
    except UnicodeError as exc:
        raise PdfValidationException("Block text cannot be encoded as UTF-8") from exc
    return retained
