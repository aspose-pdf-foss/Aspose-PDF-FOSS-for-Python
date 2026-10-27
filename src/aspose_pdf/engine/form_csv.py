"""Read bounded CSV records for the existing PDF form batch workflow."""

from __future__ import annotations

import codecs
import csv
import io
import os
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any, BinaryIO

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import PdfLoadLimits, _LoadBudget, _read_limited

from .cos import encode_pdf_text_string

_CHECKBOX_VALUES = {
    "true": True,
    "1": True,
    "yes": True,
    "y": True,
    "on": True,
    "false": False,
    "0": False,
    "no": False,
    "n": False,
    "off": False,
    "": False,
}


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


def records(
    source: str | Path | bytes | bytearray | BinaryIO,
    *,
    encoding: str,
    delimiter: str,
    field_map: Mapping[str, str] | None,
    checkbox_fields: Iterable[str] | None,
    checkbox_values: Mapping[str, bool] | None,
    limits: PdfLoadLimits,
) -> Iterator[dict[str, str | bool]]:
    """Validate CSV configuration and yield one field mapping per data row."""
    budget = _LoadBudget(limits)
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

    option_bytes = 0

    def option_text(value: Any, label: str, *, nonempty: bool = True) -> None:
        nonlocal option_bytes
        option_bytes += _text(value, budget, label, nonempty=nonempty)
        budget.check(option_bytes, "max_total_decoded_bytes", "CSV option text")

    mapping = None
    if field_map is not None:
        if not isinstance(field_map, Mapping):
            raise TypeError("field_map must be a mapping of CSV columns to PDF fields")
        mapping = {}
        targets = set()
        for index, (column, name) in enumerate(field_map.items(), 1):
            budget.check(index, "max_container_items", "CSV field map")
            option_text(column, "CSV column name")
            option_text(name, "PDF field name")
            if column in mapping or name in targets:
                raise PdfValidationException(
                    "field_map columns and target fields must be unique"
                )
            mapping[column] = name
            targets.add(name)
        if not mapping:
            raise PdfValidationException(
                "field_map must select at least one CSV column"
            )

    checkboxes = set()
    if checkbox_fields is not None:
        if isinstance(checkbox_fields, (str, bytes, bytearray, Mapping)):
            raise TypeError("checkbox_fields must be an iterable of PDF field names")
        for index, name in enumerate(checkbox_fields, 1):
            budget.check(index, "max_container_items", "CSV checkbox fields")
            option_text(name, "Checkbox field name")
            checkboxes.add(name)
    if checkbox_values is not None and not checkboxes:
        raise PdfValidationException("checkbox_values requires checkbox_fields")
    if checkbox_values is None:
        booleans = _CHECKBOX_VALUES
    else:
        if not isinstance(checkbox_values, Mapping):
            raise TypeError("checkbox_values must map text values to booleans")
        booleans = {}
        for index, (token, value) in enumerate(checkbox_values.items(), 1):
            budget.check(index, "max_container_items", "CSV checkbox values")
            option_text(token, "Checkbox text value", nonempty=False)
            if not isinstance(value, bool):
                raise TypeError("checkbox_values must contain boolean values")
            normalized = token.strip().casefold()
            if normalized in booleans:
                raise PdfValidationException(
                    "Checkbox text values must be unique after normalization"
                )
            booleans[normalized] = value
        if not booleans:
            raise PdfValidationException("checkbox_values must not be empty")

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
        header = None
        columns: list[tuple[int, str]] = []
        items = 0
        try:
            for row in reader:
                items += 1 + len(row)
                budget.check(items, "max_container_items", "CSV records and cells")
                if not row:
                    continue
                for value in row:
                    _text(value, budget, "CSV cell")
                if header is None:
                    header = row
                    names = set()
                    for name in header:
                        if not name.strip():
                            raise PdfValidationException(
                                "CSV column names must not be blank"
                            )
                        if name in names:
                            raise PdfValidationException(
                                "CSV column names must be unique"
                            )
                        names.add(name)
                    if mapping is not None and not mapping.keys() <= names:
                        raise PdfValidationException(
                            "field_map names a missing CSV column"
                        )
                    columns = [
                        (index, name if mapping is None else mapping[name])
                        for index, name in enumerate(header)
                        if mapping is None or name in mapping
                    ]
                    if not checkboxes <= {name for _, name in columns}:
                        raise PdfValidationException(
                            "checkbox_fields names an unmapped PDF field"
                        )
                    continue
                if len(row) != len(header):
                    raise PdfValidationException(
                        f"CSV row ending at line {reader.line_num} has {len(row)} columns; "
                        f"expected {len(header)}"
                    )
                record: dict[str, str | bool] = {}
                for index, name in columns:
                    value = row[index]
                    if name in checkboxes:
                        token = value.strip().casefold()
                        if token not in booleans:
                            raise PdfValidationException(
                                f"Invalid checkbox value for field {name!r} "
                                f"at CSV line {reader.line_num}"
                            )
                        record[name] = booleans[token]
                    else:
                        record[name] = value
                yield record
        except csv.Error as exc:
            raise PdfValidationException(
                f"Invalid CSV at line {reader.line_num}: {exc}"
            ) from exc
        if header is None:
            raise PdfValidationException("CSV source must contain a header row")
