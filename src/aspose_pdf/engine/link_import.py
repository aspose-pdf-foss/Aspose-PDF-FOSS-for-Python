"""Import link data from JSON or CSV reports and add them to a document."""

from __future__ import annotations

import csv
import io
import json
import os
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, BinaryIO

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.interactive import (
    Action,
    Destination,
    FitBDestination,
    FitBHDestination,
    FitBVDestination,
    FitDestination,
    FitHDestination,
    FitRDestination,
    FitVDestination,
    GoToAction,
    GoToRAction,
    NamedAction,
    URIAction,
    XYZDestination,
)
from aspose_pdf.load_limits import _LoadBudget, _read_limited

if TYPE_CHECKING:
    from aspose_pdf.document import Document

__all__ = sorted(["from_json", "from_csv"])


def _fail(message: str) -> Any:
    raise PdfValidationException(f"Invalid link import data: {message}")


def _source_page_index(value: Any) -> int:
    """Validate a one-based page index for the source page where link will be created."""
    if type(value) is not int or isinstance(value, bool):
        _fail("page_index must be an integer")
    if value < 1:
        _fail(f"page_index must be >= 1, got {value}")
    return value


def _destination_page_index(value: Any, max_page: int, budget: _LoadBudget) -> int:
    """Validate a one-based page index for a destination view (must be <= document pages)."""
    budget.check(1, "max_container_items", "link import destination page index")
    if type(value) is not int or isinstance(value, bool):
        _fail("destination page_index must be an integer")
    if value < 1:
        _fail(f"destination page_index must be >= 1, got {value}")
    if value > max_page:
        _fail(f"destination page_index {value} exceeds document page count {max_page}")
    return value


def _view_name(value: Any) -> str:
    """Validate a destination view name."""
    if not isinstance(value, str):
        _fail("view must be a string")
    if value not in ("Fit", "FitB", "XYZ", "FitH", "FitBH", "FitV", "FitBV", "FitR"):
        _fail(f"Unknown view type: {value}")
    return value


def _parameters(value: Any, view: str, budget: _LoadBudget) -> tuple:
    """Validate and decode destination parameters."""
    budget.check(1, "max_container_items", "link import destination parameters")
    if not isinstance(value, list):
        _fail(f"parameters must be an array for view {view}")

    counts = {
        "Fit": 0,
        "FitB": 0,
        "XYZ": 3,
        "FitH": 1,
        "FitBH": 1,
        "FitV": 1,
        "FitBV": 1,
        "FitR": 4,
    }
    expected = counts.get(view, 0)
    if len(value) != expected:
        _fail(f"view {view} expects {expected} parameters, got {len(value)}")

    result = []
    for item in value:
        if item is None or isinstance(item, (int, float)):
            result.append(None if item is None else float(item))
        else:
            _fail(f"parameter must be a number or null, got {type(item).__name__}")

    return tuple(result)


def _create_destination(
    page: int, view: str, params: tuple, budget: _LoadBudget
) -> Destination:
    """Create a Destination object from parsed components."""
    # All views use their respective classes directly
    views: dict[str, type[Destination]] = {
        "Fit": FitDestination,
        "FitB": FitBDestination,
        "XYZ": XYZDestination,
        "FitH": FitHDestination,
        "FitBH": FitBHDestination,
        "FitV": FitVDestination,
        "FitBV": FitBVDestination,
        "FitR": FitRDestination,
    }

    return views[view](page, *params)


def _destination_from_record(
    record: dict[str, Any], max_page: int, budget: _LoadBudget
) -> Destination | None:
    """Create a Destination from a JSON/CSV record."""
    destination = record.get("destination")
    if destination is None:
        return None

    if not isinstance(destination, dict):
        _fail("destination must be an object")

    expected_keys = {"page_index", "view", "parameters"}
    if set(destination.keys()) != expected_keys:
        _fail(f"destination must have exactly these fields: {', '.join(sorted(expected_keys))}")

    page = _destination_page_index(destination["page_index"], max_page, budget)
    view = _view_name(destination["view"])
    params = _parameters(destination["parameters"], view, budget)

    return _create_destination(page, view, params, budget)


def from_json(
    document: Document,
    source: str | bytes | bytearray | BinaryIO,
    *,
    page_map: Mapping[int, int] | None = None,
) -> int:
    """Import links from a JSON report and return the number added.

    Accepts a path, bytes, bytearray, or a readable binary stream.
    Streams are read from their current position and left open.
    The JSON schema matches :meth:`export_links_json` output:
    ``{"schema_version": 2, "links": [...]}``.

    Each link record can contain:
    - ``page_index`` (1-based): the source page for the link
    - ``uri``: URL for URI actions
    - ``destination``: a typed page view ``{page_index, view, parameters}``
    - ``file``: remote file path for GoToR actions
    - ``named_action``: named action like "NextPage"
    - ``new_window``: boolean for GoToR new_window option
    - Other fields are ignored for import.

    ``page_map`` maps source ``page_index`` to existing destination pages.
    Without it, the source page is imported as-is (must exist).

    Links are appended without deduplication. Existing annotations are preserved.
    """
    budget = _LoadBudget(document.load_limits)

    # Read the JSON data
    if isinstance(source, (bytes, bytearray)):
        budget.check_input(len(source))
        data = bytes(source)
    elif isinstance(source, (str, os.PathLike)):
        with open(source, "rb") as stream:
            data = _read_limited(stream, budget)
    elif hasattr(source, "read"):
        data = _read_limited(source, budget)
    else:
        raise TypeError("source must be a path, bytes, bytearray or a readable binary stream")

    try:
        text = data.decode("utf-8-sig")
    except UnicodeError as error:
        raise PdfValidationException("Invalid UTF-8 in link JSON") from error

    _check_limits(text, budget)

    try:
        payload = json.loads(text)
    except (ValueError, UnicodeError) as error:
        raise PdfValidationException("Invalid JSON in link import") from error

    if not isinstance(payload, dict):
        _fail("root must be an object")
    if set(payload.keys()) != {"schema_version", "links"}:
        _fail("root must have exactly 'schema_version' and 'links'")

    if payload["schema_version"] != 2:
        _fail(f"schema_version must be 2, got {payload['schema_version']}")

    if not isinstance(payload["links"], list):
        _fail("'links' must be an array")

    links = payload["links"]
    if page_map is not None:
        budget.check(len(page_map), "max_container_items", "link import page_map")

    count = 0
    for index, record in enumerate(links):
        budget.check(index + 1, "max_container_items", "link import records")
        count += _add_link_from_record(document, record, page_map, budget)

    return count


def _check_limits(text: str, budget: _LoadBudget) -> None:
    """Apply budget checks to the input text."""
    budget.check(len(text), "max_decoded_stream_bytes", "link import text bytes")
    budget.check(len(text), "max_total_decoded_bytes", "link import text bytes")


def _add_link_from_record(
    document: Document,
    record: Any,
    page_map: Mapping[int, int] | None,
    budget: _LoadBudget,
) -> int:
    """Add a link from a record, returning 1 if successful."""
    if not isinstance(record, dict):
        return 0

    # Get the source page (from page_map or directly)
    source_page_index = record.get("page_index")
    if source_page_index is None:
        return 0  # Skip records without a page

    # Validate source page index (must be >= 1)
    source_page = _source_page_index(source_page_index)

    # Apply page mapping if provided
    # Note: page_map from page_mapping() uses zero-based indices
    if page_map is not None:
        # Convert source_page to zero-based for lookup
        source_zero_based = source_page - 1
        if source_zero_based not in page_map:
            _fail(f"page_index {source_page} not in page_map")
        target_page_zero_based = page_map[source_zero_based]
    else:
        # No page_map: source_page (1-based) maps directly to target (same page)
        # Convert to zero-based for engine
        target_page_zero_based = source_page - 1

    # Validate that the target page exists (1-based for user-facing messages)
    max_page = document.page_count
    if target_page_zero_based >= max_page:
        _fail(f"target page {target_page_zero_based + 1} exceeds document page count {max_page}")

    # Create the appropriate action based on the record
    action = _create_action_from_record(record, max_page, budget)
    if action is None:
        return 0

    # Add the link annotation
    rect = record.get("rect")
    if rect is not None:
        if not isinstance(rect, (list, tuple)) or len(rect) != 4:
            return 0
        try:
            rect_tuple = tuple(float(x) for x in rect)
        except (TypeError, ValueError):
            return 0
    else:
        # Default rect if not specified
        rect_tuple = (0, 0, 100, 100)

    # Add annotation to the page (convert to 1-based for public API)
    page = document.pages[target_page_zero_based + 1]
    page.add_link(rect_tuple, action)

    return 1


def _create_action_from_record(
    record: dict[str, Any], page_index: int, budget: _LoadBudget
) -> Action | None:
    """Create an Action from a link record."""
    # Check for URI action
    uri = record.get("uri")
    if uri is not None and isinstance(uri, str):
        return URIAction(uri)

    # Check for GoToR (remote file) - priority over local destination
    remote_file = record.get("file")
    if remote_file is not None and isinstance(remote_file, str):
        dest = None
        if "destination" in record:
            dest = _destination_from_record(record, page_index, budget)
        new_window = record.get("new_window")
        return GoToRAction(remote_file, destination=dest, new_window=bool(new_window) if new_window is not None else None)

    # Check for local destination (GoTo)
    if "destination" in record:
        dest = _destination_from_record(record, page_index, budget)
        if dest is not None:
            return GoToAction(dest)

    # Check for named action
    named_action = record.get("named_action")
    if named_action is not None and isinstance(named_action, str):
        if named_action in ("FirstPage", "LastPage", "NextPage", "PrevPage"):
            return NamedAction(named_action)

    return None


def from_csv(
    document: Document,
    source: str | bytes | bytearray | BinaryIO,
    *,
    page_map: Mapping[int, int] | None = None,
    delimiter: str = ",",
    encoding: str = "utf-8",
) -> int:
    """Import links from a CSV report and return the number added.

    Accepts a path, bytes, bytearray, or a readable binary stream.
    Streams are read from their current position and left open.
    The CSV schema matches :meth:`export_links_csv` output with columns:
    ``page_number``, ``annotation_index``, ``action_type``, ``uri``,
    ``target_page_number``, ``file``, ``named_action``, ``destination_view``,
    ``destination_parameters``, ``x0``, ``y0``, ``x1``, ``y1``.

    ``page_map`` maps one-based source ``page_number`` to existing destination
    pages. ``delimiter`` and ``encoding`` follow the ``csv`` module.

    Supported actions are URI, GoTo, GoToR, and Named. Links are inferred
    from destination fields. Existing annotations are preserved and repeat
    imports append duplicates. All checks finish before any annotation is
    added. Resource limits and CSV parsing errors are handled atomically.
    """
    budget = _LoadBudget(document.load_limits)

    # Read the CSV data
    if isinstance(source, (bytes, bytearray)):
        budget.check_input(len(source))
        data = bytes(source)
    elif isinstance(source, (str, os.PathLike)):
        with open(source, "rb") as stream:
            data = _read_limited(stream, budget)
    elif hasattr(source, "read"):
        data = _read_limited(source, budget)
    else:
        raise TypeError("source must be a path, bytes, bytearray or a readable binary stream")

    try:
        text = data.decode(encoding)
    except UnicodeError as error:
        raise PdfValidationException(f"Invalid {encoding} in link CSV") from error

    _check_limits(text, budget)

    # Parse CSV
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)

    # Validate header
    expected_columns = {
        "page_number", "annotation_index", "action_type", "uri",
        "resolved_uri", "target_page_number", "file", "named_action",
        "destination_view", "destination_parameters", "remote_page_number",
        "x0", "y0", "x1", "y1",
    }
    if reader.fieldnames is None:
        _fail("CSV has no header row")

    actual_columns = set(reader.fieldnames)
    missing = expected_columns - actual_columns
    if missing:
        _fail(f"CSV missing expected columns: {', '.join(sorted(missing))}")

    count = 0
    for row_index, row in enumerate(reader):
        budget.check(row_index + 1, "max_container_items", "link import CSV rows")
        count += _add_link_from_csv_row(document, row, page_map, budget)

    return count


def _add_link_from_csv_row(
    document: Document,
    row: Mapping[str, str],
    page_map: Mapping[int, int] | None,
    budget: _LoadBudget,
) -> int:
    """Add a link from a CSV row, returning 1 if successful."""
    # Get page number (1-based)
    page_str = row.get("page_number", "").strip()
    if not page_str:
        return 0

    try:
        source_page = int(page_str)
    except ValueError:
        return 0

    # Validate source page index
    source_page = _source_page_index(source_page)

    # Apply page mapping
    # Note: page_map from page_mapping() uses zero-based indices
    if page_map is not None:
        # Convert source_page to zero-based for lookup
        source_zero_based = source_page - 1
        if source_zero_based not in page_map:
            _fail(f"page_number {source_page} not in page_map")
        target_page_zero_based = page_map[source_zero_based]
    else:
        # No page_map: source_page (1-based) maps directly to target (same page)
        target_page_zero_based = source_page - 1

    # Validate target page exists (1-based for user-facing messages)
    max_page = document.page_count
    if target_page_zero_based >= max_page:
        _fail(f"target page {target_page_zero_based + 1} exceeds document page count {max_page}")

    # Build the record from CSV row
    record: dict[str, Any] = {
        "page_index": source_page,  # 1-based page index for the source page
    }

    # URI
    uri = row.get("uri", "").strip()
    if uri:
        record["uri"] = uri

    # Destination
    view = row.get("destination_view", "").strip()
    params_str = row.get("destination_parameters", "").strip()
    if view:
        try:
            params = json.loads(params_str) if params_str else []
        except (ValueError, UnicodeError):
            params = []
        record["destination"] = {
            "page_index": target_page_zero_based + 1,
            "view": view,
            "parameters": params,
        }

    # File (GoToR)
    file_path = row.get("file", "").strip()
    if file_path:
        record["file"] = file_path

    # Named action
    named_action = row.get("named_action", "").strip()
    if named_action:
        record["named_action"] = named_action

    # Rect
    rect_parts = []
    for key in ("x0", "y0", "x1", "y1"):
        val = row.get(key, "").strip()
        if val:
            try:
                rect_parts.append(float(val))
            except ValueError:
                rect_parts = []
                break
    if len(rect_parts) == 4:
        record["rect"] = rect_parts

    return _add_link_from_record(document, record, page_map, budget)
