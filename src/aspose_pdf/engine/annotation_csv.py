"""Flat CSV review reports from selected annotation metadata."""

from __future__ import annotations

from typing import Any

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import _LoadBudget

from .annotation_json import iter_records
from .csv_export import CsvWriter

_COLUMNS = (
    "page_number",
    "annotation_index",
    "id",
    "subtype",
    "author",
    "subject",
    "contents",
    "created",
    "modified",
    "in_reply_to",
    "popup",
    "parent",
)


def _text(value: Any, name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise PdfValidationException(f"Annotation {name} must be a text string")
    return value


def export(
    document: Any, pages: Any, subtypes: Any, *, delimiter: str, encoding: str
) -> bytes:
    """Reuse the metadata reader while omitting unused geometry/style properties."""
    budget = _LoadBudget(document.load_limits)
    writer = CsvWriter(delimiter, encoding, budget, context="CSV annotations")
    writer.writerow(_COLUMNS)
    annotations = iter_records(
        document._engine_pdf,
        pages,
        subtypes,
        property_names=("Subj", "CreationDate", "M"),
    )
    for annotation in annotations:
        properties, links = annotation["properties"], annotation["links"]
        writer.writerow(
            [
                str(annotation["page_index"] + 1),
                str(annotation["annotation_index"]),
                annotation["id"],
                annotation["subtype"],
                annotation["author"],
                _text(properties.get("Subj"), "Subj"),
                annotation["contents"],
                _text(properties.get("CreationDate"), "CreationDate"),
                _text(properties.get("M"), "M"),
                _text(links["in_reply_to"], "in_reply_to"),
                _text(links["popup"], "popup"),
                _text(links["parent"], "parent"),
            ]
        )
    return writer.finish()
