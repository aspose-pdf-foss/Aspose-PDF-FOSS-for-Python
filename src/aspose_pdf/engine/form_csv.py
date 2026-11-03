"""Read bounded CSV records for the existing PDF form batch workflow."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any, BinaryIO

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import PdfLoadLimits, _LoadBudget

from .csv_import import _text, read_rows, validate_options

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
    validate_options(encoding, delimiter)

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

    header = None
    columns: list[tuple[int, str]] = []
    for line_number, row in read_rows(
        source, encoding=encoding, delimiter=delimiter, budget=budget
    ):
        if header is None:
            header = row
            names = set()
            for name in header:
                if not name.strip():
                    raise PdfValidationException("CSV column names must not be blank")
                if name in names:
                    raise PdfValidationException("CSV column names must be unique")
                names.add(name)
            if mapping is not None and not mapping.keys() <= names:
                raise PdfValidationException("field_map names a missing CSV column")
            columns = [
                (index, name if mapping is None else mapping[name])
                for index, name in enumerate(header)
                if mapping is None or name in mapping
            ]
            if not checkboxes <= {name for _, name in columns}:
                raise PdfValidationException("checkbox_fields names an unmapped PDF field")
            continue
        record: dict[str, str | bool] = {}
        for index, name in columns:
            value = row[index]
            if name in checkboxes:
                token = value.strip().casefold()
                if token not in booleans:
                    raise PdfValidationException(
                        f"Invalid checkbox value for field {name!r} "
                        f"at CSV line {line_number}"
                    )
                record[name] = booleans[token]
            else:
                record[name] = value
        yield record
    if header is None:
        raise PdfValidationException("CSV source must contain a header row")
