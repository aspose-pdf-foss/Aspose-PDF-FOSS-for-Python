"""Bounded CSV export of a single AcroForm value snapshot."""

from __future__ import annotations

import codecs
import csv
import json
from collections.abc import Iterable, Mapping
from typing import Any

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import _LoadBudget

from .form_csv import _text


class _Output:
    def __init__(self, encoding: str, budget: _LoadBudget):
        self.encoder = codecs.getincrementalencoder(encoding)(errors="strict")
        self.budget = budget
        self.data = bytearray()

    def append(self, text: str, *, final: bool = False) -> None:
        data = self.encoder.encode(text, final=final)
        size = len(self.data) + len(data)
        self.budget.check(size, "max_decoded_stream_bytes", "CSV form output bytes")
        self.budget.check(size, "max_total_decoded_bytes", "CSV form output bytes")
        self.data.extend(data)

    def write(self, text: str) -> int:
        for offset in range(0, len(text), 65536):
            self.append(text[offset : offset + 65536])
        return len(text)


def _cell(value: Any, budget: _LoadBudget) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        _text(value, budget, "CSV field value")
        return value
    if isinstance(value, list):
        budget.check(len(value), "max_container_items", "CSV multiselect values")
        for item in value:
            _text(item, budget, "CSV multiselect value")
        parts = []
        size = 0
        encoder = json.JSONEncoder(
            ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
        for chunk in encoder.iterencode(value):
            size += len(chunk)
            budget.check(size, "max_object_bytes", "CSV multiselect cell")
            budget.check(size, "max_decoded_stream_bytes", "CSV multiselect cell")
            parts.append(chunk)
        return "".join(parts)
    raise PdfValidationException(
        "CSV form values must be strings, booleans, lists or None"
    )


def to_csv(
    values: dict[str, Any],
    *,
    fields: Iterable[str] | None,
    delimiter: str,
    encoding: str,
    budget: _LoadBudget,
) -> bytes:
    """Encode one header and one record, keeping column and form value order."""
    if not isinstance(encoding, str):
        raise TypeError("encoding must be a string")
    try:
        "".encode(encoding)
        output = _Output(encoding, budget)
    except (LookupError, UnicodeError, ValueError, TypeError) as exc:
        raise PdfValidationException(
            "encoding must name a supported text encoding"
        ) from exc
    if not isinstance(delimiter, str):
        raise TypeError("delimiter must be a string")
    if len(delimiter) != 1 or delimiter in '\r\n"\x00':
        raise PdfValidationException(
            "delimiter must be one character other than a quote, newline or NUL"
        )
    _text(delimiter, budget, "CSV delimiter")
    if fields is None:
        selected = values
    else:
        if isinstance(fields, (str, bytes, bytearray, Mapping)) or not isinstance(
            fields, Iterable
        ):
            raise TypeError("fields must be an iterable of fully qualified field names")
        selected = {}
        for count, name in enumerate(fields, 1):
            budget.check(count, "max_container_items", "CSV selected fields")
            _text(name, budget, "CSV field name")
            if name in selected:
                raise PdfValidationException("CSV field names must be unique")
            if name not in values:
                raise KeyError(f"No exportable form field named {name!r}")
            selected[name] = values[name]
    budget.check(len(selected), "max_container_items", "CSV exported fields")
    if not selected:
        return b""

    header = list(selected)
    row = [_cell(value, budget) for value in selected.values()]
    total = 0
    writer = csv.writer(output, delimiter=delimiter, lineterminator="\r\n")
    try:
        for record in (header, row):
            characters = len(record) - 1 + 2
            for value in record:
                total += _text(value, budget, "CSV cell")
                budget.check(total, "max_total_decoded_bytes", "CSV form cell text")
                quoted = any(char in value for char in (delimiter, '"', "\r", "\n"))
                quoted = quoted or (len(record) == 1 and not value)
                characters += len(value) + value.count('"') + 2 * quoted
                budget.check(
                    characters, "max_decoded_stream_bytes", "CSV record characters"
                )
            writer.writerow(record)
        output.append("", final=True)
    except (UnicodeError, LookupError, ValueError, TypeError, csv.Error) as exc:
        raise PdfValidationException(
            f"Form values cannot be encoded as CSV using {encoding!r}"
        ) from exc
    return bytes(output.data)
