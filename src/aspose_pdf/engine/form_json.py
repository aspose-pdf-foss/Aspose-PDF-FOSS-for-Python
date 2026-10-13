"""Flat JSON data exchange for AcroForm field values."""

from __future__ import annotations

import json
from typing import Any

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import _LoadBudget


def to_json(values: dict[str, Any]) -> bytes:
    """Encode a form value snapshot as UTF-8 JSON."""
    try:
        return (
            json.dumps(values, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise PdfValidationException(
            "Form values cannot be encoded as UTF-8 JSON"
        ) from exc


def _check_limits(text: str, budget: _LoadBudget) -> None:
    """Bound container allocation before the standard JSON decoder runs.

    Only structural delimiters outside strings matter here; the decoder
    validates JSON syntax afterwards. Each stack entry counts the items in
    that container, including the first item before any comma appears.
    """
    counts: list[int] = []
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char in " \t\r\n":
            continue
        if counts and char not in "}]" and (counts[-1] == 0 or char == ","):
            counts[-1] += 1
            budget.check(
                counts[-1], "max_container_items", "JSON form data container items"
            )
        if char == '"':
            quoted = True
        elif char in "[{":
            counts.append(0)
            budget.check(len(counts), "max_nesting_depth", "JSON form data nesting")
        elif char in "]}" and counts:
            counts.pop()


def read_json(data: bytes, budget: _LoadBudget) -> dict[str, Any]:
    """Decode one JSON object, refusing duplicate keys and non-finite numbers."""
    budget.check_input(len(data))

    def object_values(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for name, value in pairs:
            if name in values:
                raise PdfValidationException(
                    f"Duplicate JSON form field name: {name!r}"
                )
            values[name] = value
        return values

    def invalid_constant(value: str) -> Any:
        raise PdfValidationException(f"Invalid JSON form data constant: {value}")

    try:
        text = data.decode("utf-8-sig")
        _check_limits(text, budget)
        values = json.loads(
            text, object_pairs_hook=object_values, parse_constant=invalid_constant
        )
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise PdfValidationException("Invalid UTF-8 JSON form data") from exc
    if not isinstance(values, dict):
        raise PdfValidationException(
            "JSON form data must be an object keyed by field names"
        )
    return values
