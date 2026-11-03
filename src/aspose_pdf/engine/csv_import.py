"""Shared bounded CSV decoding for form batches and authored PDF tables."""

from __future__ import annotations

import codecs
import csv
import io
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any, BinaryIO

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import _LoadBudget, _read_limited

from .cos import encode_pdf_text_string


def _text(
    value: Any, budget: _LoadBudget, label: str, *, nonempty: bool = False
) -> int:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    budget.check(len(value), "max_object_bytes", label)
    if nonempty and not value.strip():
        raise PdfValidationException(f"{label} must not be blank")
    try:
        size = len(encode_pdf_text_string(value))
    except UnicodeError as exc:
        raise PdfValidationException(f"{label} contains invalid Unicode") from exc
    budget.check(size, "max_object_bytes", label)
    return size


def _read(source: Any, budget: _LoadBudget) -> bytes:
    if isinstance(source, (bytes, bytearray)):
        budget.check_input(len(source))
        return bytes(source)
    if isinstance(source, (str, os.PathLike)):
        path = Path(source)
        budget.check_input(path.stat().st_size)
        with path.open("rb") as stream:
            return _read_limited(stream, budget)
    if hasattr(source, "read"):
        return _read_limited(source, budget)
    raise TypeError("CSV source must be a path, bytes or a readable binary stream")


def _check_structure(text: str, delimiter: str, budget: _LoadBudget) -> None:
    """Bound CSV containers before the standard reader allocates a wide row."""
    items = index = 0
    quoted = record = False
    field_start = True
    while index < len(text):
        char = text[index]
        if quoted:
            if char == '"':
                if index + 1 < len(text) and text[index + 1] == '"':
                    index += 2
                    continue
                quoted = False
        elif char in "\r\n":
            items += 1 + int(record and field_start)
            budget.check(items, "max_container_items", "CSV records and cells")
            field_start, record = True, False
            if char == "\r" and index + 1 < len(text) and text[index + 1] == "\n":
                index += 1
        elif field_start:
            items += 1
            budget.check(items, "max_container_items", "CSV records and cells")
            field_start, record = False, True
            quoted = char == '"'
            if char == delimiter:
                field_start = True
        elif char == delimiter:
            field_start = True
        index += 1
    if record:
        items += 1 + int(field_start)
        budget.check(items, "max_container_items", "CSV records and cells")


def validate_options(encoding: str, delimiter: str) -> None:
    """Validate the text codec and delimiter before reading input."""
    if not isinstance(encoding, str):
        raise TypeError("encoding must be a string")
    try:
        decoded, _ = codecs.lookup(encoding).decode(b"")
    except (LookupError, UnicodeError, ValueError, TypeError) as exc:
        raise PdfValidationException(
            "encoding must name a supported text encoding"
        ) from exc
    if not isinstance(decoded, str):
        raise PdfValidationException("encoding must name a supported text encoding")
    if not isinstance(delimiter, str):
        raise TypeError("delimiter must be a string")
    if len(delimiter) != 1 or delimiter in '\r\n"\x00':
        raise PdfValidationException(
            "delimiter must be one character other than a quote, newline or NUL"
        )


def read_rows(
    source: str | Path | bytes | bytearray | BinaryIO,
    *,
    encoding: str,
    delimiter: str,
    budget: _LoadBudget,
) -> Iterator[tuple[int, list[str]]]:
    """Yield line numbers and rectangular string rows using validated options."""
    data = _read(source, budget)
    try:
        text = data.decode(encoding)
    except (UnicodeError, LookupError, ValueError) as exc:
        raise PdfValidationException(
            f"CSV source is not valid {encoding} text"
        ) from exc
    del data
    budget.check(len(text), "max_total_decoded_bytes", "CSV decoded text")
    decoded_bytes = 0
    try:
        for offset in range(0, len(text), 4096):
            decoded_bytes += len(text[offset : offset + 4096].encode("utf-8"))
            budget.check(decoded_bytes, "max_total_decoded_bytes", "CSV decoded text")
    except UnicodeError as exc:
        raise PdfValidationException("CSV source contains invalid Unicode") from exc

    _check_structure(text, delimiter, budget)
    with io.StringIO(text, newline="") as stream:
        del text
        reader = csv.reader(stream, delimiter=delimiter, strict=True)
        width = None
        items = 0
        try:
            for row in reader:
                items += 1 + len(row)
                budget.check(items, "max_container_items", "CSV records and cells")
                if not row:
                    continue
                for value in row:
                    _text(value, budget, "CSV cell")
                if width is None:
                    width = len(row)
                if len(row) != width:
                    raise PdfValidationException(
                        f"CSV row ending at line {reader.line_num} has {len(row)} columns; "
                        f"expected {width}"
                    )
                yield reader.line_num, row
        except csv.Error as exc:
            raise PdfValidationException(
                f"Invalid CSV at line {reader.line_num}: {exc}"
            ) from exc
