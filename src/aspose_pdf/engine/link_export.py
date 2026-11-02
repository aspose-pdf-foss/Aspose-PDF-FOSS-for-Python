"""Bounded JSON and CSV reports from the existing link inventory."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Any

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.link_extraction import ExtractedLink
from aspose_pdf.load_limits import _LoadBudget

from .csv_export import CsvWriter

if TYPE_CHECKING:
    from aspose_pdf.document import Document

_COLUMNS = (
    "page_number", "annotation_index", "action_type", "action_path",
    "uri", "resolved_uri", "target_page_number", "remote_page_number",
    "destination_name", "destination_view", "destination_parameters", "file",
    "named_action", "x0", "y0", "x1", "y1", "issues",
)


def _record(link: ExtractedLink) -> dict[str, Any]:
    destination = None
    if link.destination is not None:
        view, parameters = link.destination._spec()
        destination = {
            "page_index": link.destination.page,
            "view": view,
            "parameters": parameters,
        }
    return {
        "page_index": link.page_index,
        "annotation_index": link.annotation_index,
        "rect": None if link.rect is None else list(link.rect),
        "action_type": link.action_type,
        "action_path": link.action_path,
        "uri": link.uri,
        "resolved_uri": link.resolved_uri,
        "destination": destination,
        "destination_name": link.destination_name,
        "target_page_index": link.target_page_index,
        "file": link.file,
        "named_action": link.named_action,
        "issues": list(link.issues),
    }


def _json_tokens(value: Any) -> Iterator[str]:
    """Encode the fixed report schema without allocating a huge escaped string."""
    if isinstance(value, str):
        yield '"'
        for offset in range(0, len(value), 8192):
            yield json.dumps(value[offset:offset + 8192], ensure_ascii=False)[1:-1]
        yield '"'
    elif isinstance(value, dict):
        yield "{"
        for index, (key, item) in enumerate(value.items()):
            if index:
                yield ","
            yield from _json_tokens(key)
            yield ":"
            yield from _json_tokens(item)
        yield "}"
    elif isinstance(value, (list, tuple)):
        yield "["
        for index, item in enumerate(value):
            if index:
                yield ","
            yield from _json_tokens(item)
        yield "]"
    else:
        yield json.dumps(value, allow_nan=False)


def _append(output: bytearray, data: bytes, budget: _LoadBudget) -> None:
    size = len(output) + len(data)
    budget.check(size, "max_decoded_stream_bytes", "JSON links output bytes")
    budget.check(size, "max_total_decoded_bytes", "JSON links output bytes")
    output.extend(data)


def to_json(document: Document, pages: Iterable[int] | None) -> bytes:
    """Encode one record at a time, retaining all inventory ordering and issues."""
    budget = _LoadBudget(document.load_limits)
    output = bytearray()
    _append(output, b'{"schema_version":1,"links":[', budget)
    for index, link in enumerate(document.extract_links(pages=pages)):
        if index:
            _append(output, b",", budget)
        try:
            for token in _json_tokens(_record(link)):
                _append(output, token.encode("utf-8"), budget)
        except (UnicodeError, ValueError, TypeError) as exc:
            raise PdfValidationException("Links cannot be encoded as UTF-8 JSON") from exc
    _append(output, b"]}\n", budget)
    return bytes(output)


def _cell(value: Any) -> str:
    return "" if value is None else str(value)


def to_csv(
    document: Document, pages: Iterable[int] | None, *, delimiter: str, encoding: str,
) -> bytes:
    """Write flat rows with separate local and remote one-based page numbers."""
    budget = _LoadBudget(document.load_limits)
    writer = CsvWriter(delimiter, encoding, budget, context="CSV links")
    writer.writerow(_COLUMNS)
    for link in document.extract_links(pages=pages):
        record = _record(link)
        destination = record["destination"]
        local_page = link.target_page_index
        remote_page = (
            destination["page_index"]
            if link.action_type == "GoToR" and destination is not None else None
        )
        writer.writerow([
            str(link.page_index + 1), str(link.annotation_index),
            _cell(link.action_type), link.action_path,
            _cell(link.uri), _cell(link.resolved_uri),
            "" if local_page is None else str(local_page + 1),
            "" if remote_page is None else str(remote_page + 1),
            _cell(link.destination_name),
            "" if destination is None else destination["view"],
            "" if destination is None else json.dumps(destination["parameters"], allow_nan=False),
            _cell(link.file), _cell(link.named_action),
            *[_cell(value) for value in (link.rect or (None,) * 4)],
            json.dumps(record["issues"]),
        ])
    return writer.finish()
