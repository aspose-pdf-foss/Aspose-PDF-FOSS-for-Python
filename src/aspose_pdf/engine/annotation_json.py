"""Read-only annotation metadata snapshots for review and JSON export."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from typing import Any

from aspose_pdf.engine.cos import (
    PdfArray,
    PdfBoolean,
    PdfDictionary,
    PdfIndirectReference,
    PdfName,
    PdfNull,
    PdfNumber,
    PdfStream,
    PdfString,
    decode_pdf_text_string,
)
from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import _LoadBudget

# Export review metadata, not executable actions, field values or embedded assets.
_PROPERTIES = (
    "BE",
    "BS",
    "Border",
    "C",
    "CA",
    "CL",
    "CO",
    "CP",
    "Cap",
    "CreationDate",
    "DA",
    "DS",
    "F",
    "IC",
    "IT",
    "InkList",
    "L",
    "LE",
    "LL",
    "LLE",
    "LLO",
    "Lang",
    "M",
    "Measure",
    "NM",
    "Name",
    "Open",
    "OverlayText",
    "Q",
    "QuadPoints",
    "RC",
    "RD",
    "RT",
    "Repeat",
    "State",
    "StateModel",
    "Subj",
    "Sy",
    "Vertices",
)


def _pages(values: Iterable[int] | None, count: int, budget: _LoadBudget) -> list[int]:
    """Validate the entire selection, retaining each page's first occurrence."""
    if values is None:
        budget.check(count, "max_container_items", "annotation export page selection")
        return list(range(count))
    if isinstance(values, (str, bytes, bytearray)):
        raise PdfValidationException("pages must be an iterable of integer indices")
    try:
        iterator = iter(values)
    except TypeError:
        raise PdfValidationException(
            "pages must be an iterable of integer indices"
        ) from None
    selected: dict[int, None] = {}
    for offset, index in enumerate(iterator, 1):
        budget.check(offset, "max_container_items", "annotation export page selection")
        if isinstance(index, bool) or not isinstance(index, int):
            raise PdfValidationException("Page indices must be integers")
        if not 0 <= index < count:
            raise PdfValidationException(f"Page index out of range: {index}")
        selected[index] = None
    return list(selected)


def _subtypes(values: Any, budget: _LoadBudget) -> set[str] | None:
    """Accept a subtype string, a string enum, or an iterable of those."""
    if values is None:
        return None
    if isinstance(values, (bytes, bytearray)):
        raise PdfValidationException("subtypes must contain annotation subtype strings")
    if isinstance(values, str):
        values = [values]
    try:
        iterator = iter(values)
    except TypeError:
        raise PdfValidationException(
            "subtypes must contain annotation subtype strings"
        ) from None
    wanted: set[str] = set()
    for offset, value in enumerate(iterator, 1):
        budget.check(
            offset, "max_container_items", "annotation export subtype selection"
        )
        if not isinstance(value, str):
            raise PdfValidationException(
                "subtypes must contain annotation subtype strings"
            )
        name = getattr(value, "value", value).strip().lstrip("/")
        if not name:
            raise PdfValidationException("Annotation subtype strings must not be empty")
        wanted.add(name)
    return wanted


def _object_id(reference: PdfIndirectReference) -> str:
    return f"object-{reference.object_number}-{reference.gen_number}"


class _Reader:
    """Bound conversion of the small, non-stream subset exported as metadata."""

    def __init__(self, pdf: Any, budget: _LoadBudget) -> None:
        self.pdf = pdf
        self.budget = budget
        self.items = 0
        self.active: set[int] = set()

    def value(self, raw: Any, depth: int = 0) -> Any:
        self.items += 1
        self.budget.check(self.items, "max_container_items", "annotation export values")
        self.budget.check(
            depth, "max_nesting_depth", "annotation export property depth"
        )
        obj = self.pdf._resolve(raw)
        if obj is None or isinstance(obj, PdfNull):
            return None
        if isinstance(obj, (PdfBoolean, PdfNumber)):
            value = obj.value
            if isinstance(value, float) and not math.isfinite(value):
                raise PdfValidationException(
                    "Annotation metadata contains a non-finite number"
                )
            return value
        if isinstance(obj, PdfName):
            return obj.name.lstrip("/")
        if isinstance(obj, PdfString):
            return decode_pdf_text_string(obj)
        if isinstance(obj, PdfStream):
            raise PdfValidationException(
                "Annotation metadata must not contain a stream"
            )
        if not isinstance(obj, (PdfArray, PdfDictionary)):
            raise PdfValidationException("Unsupported annotation metadata value")
        if id(obj) in self.active:
            raise PdfValidationException("Annotation metadata contains a cycle")
        self.active.add(id(obj))
        try:
            if isinstance(obj, PdfArray):
                self.budget.check(
                    len(obj.items), "max_container_items", "annotation export array"
                )
                return [self.value(item, depth + 1) for item in obj.items]
            self.budget.check(
                len(obj.mapping), "max_container_items", "annotation export dictionary"
            )
            return {
                key.name.lstrip("/"): self.value(value, depth + 1)
                for key, value in sorted(
                    obj.mapping.items(), key=lambda item: item[0].name
                )
            }
        finally:
            self.active.remove(id(obj))

    def text(self, obj: PdfDictionary, key: str) -> str:
        raw = self.pdf._resolve(obj.mapping.get(PdfName(key)))
        if raw is None or isinstance(raw, PdfNull):
            return ""
        if not isinstance(raw, PdfString):
            raise PdfValidationException(f"Annotation {key} must be a text string")
        return self.value(raw)


def records(
    pdf: Any, pages: Iterable[int] | None, subtypes: Any
) -> list[dict[str, Any]]:
    """Snapshot selected annotations without traversing appearances or page content."""
    budget = _LoadBudget(pdf._load_limits)
    selected = _pages(pages, pdf.page_count, budget)
    wanted = _subtypes(subtypes, budget)
    if wanted == set():
        return []
    reader = _Reader(pdf, budget)
    result: list[dict[str, Any]] = []
    for page_index in selected:
        page = pdf._get_page_dict(page_index)
        if not isinstance(page, PdfDictionary):
            continue
        annots = pdf._resolve(page.mapping.get(PdfName("Annots")))
        if annots is None or isinstance(annots, PdfNull):
            continue
        if not isinstance(annots, PdfArray):
            raise PdfValidationException("Page Annots must be an array")
        budget.check(
            len(annots.items), "max_container_items", "annotation export page entries"
        )
        direct_ids = {
            id(pdf._resolve(ref)): f"page-{page_index}-annotation-{index}"
            for index, ref in enumerate(annots.items)
            if not isinstance(ref, PdfIndirectReference)
        }
        for index, ref in enumerate(annots.items):
            obj = pdf._resolve(ref)
            if not isinstance(obj, PdfDictionary) or isinstance(obj, PdfStream):
                raise PdfValidationException(
                    "Each page annotation must be a dictionary"
                )
            subtype = pdf._resolve(obj.mapping.get(PdfName("Subtype")))
            if not isinstance(subtype, PdfName):
                raise PdfValidationException("Annotation Subtype must be a PDF name")
            subtype_name = subtype.name.lstrip("/")
            if not subtype_name:
                raise PdfValidationException("Annotation Subtype must not be empty")
            if wanted is not None and subtype_name not in wanted:
                continue
            rect = reader.value(obj.mapping.get(PdfName("Rect")))
            if (
                not isinstance(rect, list)
                or len(rect) != 4
                or any(
                    isinstance(value, bool) or not isinstance(value, (int, float))
                    for value in rect
                )
            ):
                raise PdfValidationException(
                    "Annotation Rect must contain four finite numbers"
                )
            properties = {}
            for key in _PROPERTIES:
                raw = obj.mapping.get(PdfName(key))
                if raw is None:
                    continue
                if key == "RC" and isinstance(pdf._resolve(raw), PdfStream):
                    continue  # Stream-valued rich text is outside metadata-only export.
                properties[key] = reader.value(raw)
            links = {}
            for key, name in (
                ("IRT", "in_reply_to"),
                ("Popup", "popup"),
                ("Parent", "parent"),
            ):
                target = (
                    obj.mapping.get(PdfName(key))
                    if key != "Parent" or subtype_name == "Popup"
                    else None
                )
                links[name] = (
                    _object_id(target)
                    if isinstance(target, PdfIndirectReference)
                    else direct_ids.get(id(target))
                    if target is not None
                    else None
                )
            result.append(
                {
                    "id": _object_id(ref)
                    if isinstance(ref, PdfIndirectReference)
                    else direct_ids[id(obj)],
                    "page_index": page_index,
                    "annotation_index": index,
                    "subtype": subtype_name,
                    "rect": rect,
                    "contents": reader.text(obj, "Contents"),
                    "author": reader.text(obj, "T"),
                    "properties": properties,
                    "links": links,
                }
            )
            budget.check(
                len(result), "max_container_items", "annotation export records"
            )
    return result


def to_json(annotations: list[dict[str, Any]]) -> bytes:
    """Encode the versioned schema as deterministic UTF-8 JSON."""
    try:
        return (
            json.dumps(
                {"schema_version": 1, "annotations": annotations},
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise PdfValidationException(
            "Annotations cannot be encoded as UTF-8 JSON"
        ) from exc
