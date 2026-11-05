"""Bounded CSV exchange for simple page bookmarks and targetless headings."""

from __future__ import annotations

import re
from typing import Any

from aspose_pdf.exceptions import PdfResourceLimitException, PdfValidationException
from aspose_pdf.interactive import FitDestination
from aspose_pdf.load_limits import _LoadBudget
from aspose_pdf.outlines import OutlineItem

from .bookmark_json import _commit_roots, _Context, _style, _walk
from .csv_export import CsvWriter
from .csv_import import read_rows, validate_options

_HEADER = ("level", "title", "page")


def _fail(message: str) -> Any:
    raise PdfValidationException(f"Invalid bookmark CSV: {message}")


def export_csv(document: Any, *, delimiter: str, encoding: str) -> bytes:
    """Reject unsupported properties before writing a simple preorder table."""
    writer = CsvWriter(
        delimiter, encoding, _LoadBudget(document.load_limits), context="CSV bookmarks"
    )
    context = _Context(document)
    roots = (
        document._outlines._items
        if document._outlines is not None
        else context.pdf._outlines_data
    )
    writer.writerow(_HEADER)
    depths = {}
    for item, entry, parent in _walk(roots, context):
        style = _style(entry, context)
        if (
            style["is_bold"]
            or style["is_italic"]
            or style["color"] is not None
            or style["open"]
        ):
            _fail("styles and open state require bookmark JSON exchange")
        try:
            target = context.encode_target(
                entry.get("loaded_target"),
                entry.get("target"),
                entry.get("page_index"),
                False,
            )
            context.decode_target(target, None)
        except PdfResourceLimitException:
            raise
        except (
            PdfValidationException,
            TypeError,
            ValueError,
            OverflowError,
            NotImplementedError,
        ) as exc:
            raise PdfValidationException(
                "Unsupported bookmark CSV target; use bookmark JSON exchange"
            ) from exc
        if target is not None and target["kind"] == "goto":
            target = target["destination"]
        if target is not None and (
            target["kind"] != "page" or target["view"] != "Fit" or target["parameters"]
        ):
            _fail(
                "named targets, actions and precise page views require bookmark JSON exchange"
            )
        level = 1 if parent is None else depths[parent] + 1
        depths[id(item)] = level
        writer.writerow(
            (
                str(level),
                style["title"],
                "" if target is None else str(target["page_index"] + 1),
            )
        )
    return writer.finish()


def _positive_integer(value: str, name: str, line: int) -> int:
    value = value.strip()
    if not re.fullmatch(r"[0-9]+", value):
        _fail(f"{name} at line {line} must be a positive decimal integer")
    try:
        number = int(value)
    except ValueError:
        _fail(f"{name} at line {line} is too large")
    if number < 1:
        _fail(f"{name} at line {line} must be positive")
    return number


def import_csv(
    document: Any,
    source: Any,
    *,
    replace: bool,
    page_map: Any,
    delimiter: str,
    encoding: str,
) -> int:
    """Parse and validate a complete preorder table, then commit it atomically."""
    if type(replace) is not bool:
        _fail("replace must be a boolean")
    validate_options(encoding, delimiter)
    context = _Context(document)
    mapping = None
    if page_map is not None:
        mapping = {}
        for offset, (page, target) in enumerate(page_map.items(), 1):
            context.budget.check(offset, "max_container_items", "bookmark page_map")
            if type(page) is not int or page < 0:
                _fail("page_map keys must be non-negative page indices")
            mapping[page] = context.page(target)
    header = None
    roots, parents = [], []
    for line, row in read_rows(
        source, encoding=encoding, delimiter=delimiter, budget=context.budget
    ):
        if header is None:
            if tuple(row) != _HEADER:
                _fail("expected exactly the header level,title,page")
            header = row
            continue
        level = _positive_integer(row[0], "level", line)
        if level > len(parents) + 1:
            _fail(f"level at line {line} skips a parent; the first level must be 1")
        context.tick(level)
        if not context.refs:
            _fail("a nonempty bookmark import requires a document page")
        item = OutlineItem(row[1], page_index=None)
        if row[2].strip():
            target = {
                "kind": "page",
                "page_index": _positive_integer(row[2], "page", line) - 1,
                "view": "Fit",
                "parameters": [],
            }
            loaded = context.decode_target(target, mapping)
            page = context.current_page(loaded)
            item.destination = FitDestination(page + 1)
            item._loaded_target = loaded
            item._page_index = page
        del parents[level - 1 :]
        (roots if not parents else parents[-1].children).append(item)
        parents.append(item)
    if header is None:
        _fail("source must contain a header row")
    return _commit_roots(context, roots, context.work, replace)
