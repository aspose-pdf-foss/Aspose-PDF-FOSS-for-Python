"""Bounded CSV export of a single AcroForm value snapshot."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import _LoadBudget

from .csv_export import CsvWriter
from .form_csv import _text


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
    writer = CsvWriter(delimiter, encoding, budget, context="CSV form")
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
    writer.writerow(header)
    writer.writerow(row)
    return writer.finish()
