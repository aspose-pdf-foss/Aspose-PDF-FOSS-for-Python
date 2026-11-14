"""Conversions between public page numbers and the engine's page indices.

Public page identifiers start at one. Engine lists, PDF destinations and
standard interchange formats retain their zero-based representation.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import fields, is_dataclass, replace
from functools import wraps
from inspect import signature
from typing import Any

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import PdfLoadLimits, _LoadBudget


def page_index(
    number: int, count: int | None = None, *, insertion: bool = False
) -> int:
    """Validate a public page number and return its zero-based index."""
    if isinstance(number, bool) or not isinstance(number, int):
        raise TypeError("Page number must be an integer")
    upper = None if count is None else count + int(insertion)
    if number < 1 or (upper is not None and number > upper):
        raise IndexError(f"Page number out of range: {number}")
    return number - 1


def page_indices(
    numbers: Iterable[int] | None,
    count: int | None = None,
    *,
    limits: PdfLoadLimits | None = None,
) -> tuple[int, ...] | None:
    """Snapshot a public selection once, preserving order and repetitions."""
    if numbers is None:
        return None
    if isinstance(numbers, (str, bytes, bytearray, slice)):
        raise PdfValidationException(
            "Page selection must be an iterable of page numbers"
        )
    try:
        iterator = iter(numbers)
    except TypeError:
        raise PdfValidationException(
            "Page selection must be an iterable of page numbers"
        ) from None
    budget = _LoadBudget(limits)
    result = []
    for position, number in enumerate(iterator, 1):
        budget.check(position, "max_container_items", "page selection")
        try:
            result.append(page_index(number, count))
        except (TypeError, IndexError) as error:
            raise PdfValidationException(str(error)) from None
    return tuple(result)


def page_mapping(values, count, *, limits=None):
    """Normalize a public source-to-destination page map for an importer."""
    if values is None:
        return None
    if not isinstance(values, Mapping):
        raise PdfValidationException("page_map must be a mapping of page numbers")
    budget = _LoadBudget(limits)
    result = {}
    for position, (source, target) in enumerate(values.items(), 1):
        budget.check(position, "max_container_items", "page mapping")
        try:
            result[page_index(source)] = page_index(target, count)
        except (TypeError, IndexError) as error:
            raise PdfValidationException(str(error)) from None
    return result


class _EngineSelection:
    """Normalize on first use, after the operation validates its other options.

    Repeated internal passes reuse the same bounded snapshot, including when
    the caller supplied a generator. No document content is read here.
    """

    def __init__(self, numbers, count, limits):
        self._numbers = numbers
        self._count = count
        self._limits = limits
        self._snapshot = None

    def __iter__(self):
        if self._snapshot is None:
            self._snapshot = page_indices(
                self._numbers, self._count, limits=self._limits
            )
        return iter(self._snapshot)


def numbered_records(value: Any) -> Any:
    """Detach engine result records with public page identifiers.

    Typed destinations already carry public page numbers. Annotation indices,
    table indices, coordinates and counts are deliberately left untouched.
    """
    if isinstance(value, list):
        return [numbered_records(item) for item in value]
    if isinstance(value, tuple):
        return tuple(numbered_records(item) for item in value)
    if isinstance(value, dict):
        return {key: numbered_records(item) for key, item in value.items()}
    if is_dataclass(value) and not isinstance(value, type):
        changes = {}
        for field in fields(value):
            item = getattr(value, field.name)
            if field.name in {"page_index", "other_page_index"} and item is not None:
                changes[field.name] = item + 1
            elif field.name == "page_indexes":
                changes[field.name] = tuple(index + 1 for index in item)
            elif field.name in {"skipped", "skipped_fragments", "existing_fragments"}:
                changes[field.name] = numbered_records(item)
        return replace(value, **changes) if changes else value
    return value


def page_api(*, selections=(), scalars=(), result=None):
    """Declare a Document boundary around a zero-based implementation.

    Each decorated method has a private ``_api_<name>`` counterpart for calls
    originating inside the engine. This keeps conversions local and avoids
    changing the numbering of engine algorithms or adding runtime modes.
    """

    def decorate(method):
        parameters = signature(method)

        @wraps(method)
        def public(self, *args, **kwargs):
            self._ensure_not_disposed()
            bound = parameters.bind(self, *args, **kwargs)
            bound.apply_defaults()
            for name in selections:
                value = bound.arguments[name]
                document = bound.arguments["other"] if name == "other_pages" else self
                if name == "selection" and isinstance(value, str):
                    from aspose_pdf.page_ranges import parse_page_range

                    value = parse_page_range(
                        value, document.page_count, limits=document.load_limits
                    )
                bound.arguments[name] = (
                    None
                    if value is None
                    else _EngineSelection(
                        value, document.page_count, document.load_limits
                    )
                )
            for name in scalars:
                value = bound.arguments[name]
                if value is not None:
                    bound.arguments[name] = page_index(
                        value, self.page_count, insertion=name == "at"
                    )
            value = method(*bound.args, **bound.kwargs)
            if result == "records":
                return numbered_records(value)
            if result == "indices":
                return [index + 1 for index in value]
            if result == "groups":
                return [[index + 1 for index in group] for group in value]
            if result == "mapping":
                return {index + 1: item for index, item in value.items()}
            return value

        return public

    return decorate


def layout_api(method):
    """Expose page numbers in the result of a paginated drawing operation."""

    @wraps(method)
    def public(*args, **kwargs):
        result = method(*args, **kwargs)
        return {**result, "pages": [index + 1 for index in result["pages"]]}

    return public
