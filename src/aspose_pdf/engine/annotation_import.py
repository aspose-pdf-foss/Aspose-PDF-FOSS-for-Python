"""Validate and append review annotations from the version 1 JSON schema."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
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
)
from aspose_pdf.engine.form_json import _check_limits
from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import _LoadBudget, _read_limited

_SUBTYPES = frozenset(
    "Text FreeText Line Square Circle Polygon PolyLine Highlight Underline "
    "Squiggly StrikeOut Stamp Caret Ink Popup Redact".split()
)
_FIELDS = frozenset(
    "id page_index annotation_index subtype rect contents author properties links".split()
)
_LINKS = {"in_reply_to": "IRT", "popup": "Popup", "parent": "Parent"}
_TEXT = frozenset(
    "CreationDate DA DS Lang M NM OverlayText RC State StateModel Subj".split()
)
_NAMES = frozenset("CP IT Name RT Sy".split())
_NUMBERS = frozenset("LL LLE LLO".split())
_BOOLS = frozenset("Cap Open Repeat".split())


def _fail(message: str) -> Any:
    raise PdfValidationException(f"Invalid annotation JSON: {message}")


def _read(source: Any, budget: _LoadBudget) -> dict[str, Any]:
    if isinstance(source, (bytes, bytearray)):
        budget.check_input(len(source))
        data = bytes(source)
    elif isinstance(source, (str, os.PathLike)):
        with open(source, "rb") as stream:
            data = _read_limited(stream, budget)
    elif hasattr(source, "read"):
        data = _read_limited(source, budget)
    else:
        raise TypeError("source must be a path, bytes or a readable binary stream")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                _fail(f"duplicate key {key!r}")
            result[key] = value
        return result

    try:
        text = data.decode("utf-8-sig")
        _check_limits(text, budget, "JSON annotation data")
        result = json.loads(
            text,
            object_pairs_hook=pairs,
            parse_constant=lambda value: _fail(f"non-finite number {value}"),
        )
        # Include keys and scalar values in a cumulative conversion budget.
        pending = [result]
        count = 0
        while pending:
            value = pending.pop()
            count += 1
            budget.check(count, "max_container_items", "annotation import values")
            if isinstance(value, str):
                budget.check(
                    len(value.encode("utf-8")),
                    "max_object_bytes",
                    "annotation import string bytes",
                )
            elif isinstance(value, float) and not math.isfinite(value):
                _fail("numbers must be finite")
            elif isinstance(value, dict):
                pending.extend(value)
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise PdfValidationException("Invalid UTF-8 annotation JSON") from exc
    if not isinstance(result, dict):
        _fail("the root must be an object")
    if set(result) != {"schema_version", "annotations"}:
        _fail("expected schema_version and annotations")
    if type(result["schema_version"]) is not int or result["schema_version"] != 1:
        _fail("unsupported schema_version (expected 1)")
    if not isinstance(result["annotations"], list):
        _fail("annotations must be an array")
    return result


def _integer(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        _fail(f"{label} must be a non-negative integer")
    return value


def _number(value: Any, label: str, *, minimum: float | None = None) -> PdfNumber:
    if type(value) not in (int, float):
        _fail(f"{label} must be a finite number")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite or (minimum is not None and value < minimum):
        _fail(f"{label} is out of range")
    return PdfNumber(value)


def _string(value: Any, label: str, *, name: bool = False) -> Any:
    if not isinstance(value, str):
        _fail(f"{label} must be a string")
    if name and (not value or value.startswith("/")):
        _fail(f"{label} must be a non-empty PDF name without a leading slash")
    return PdfName(value) if name else PdfString(value)


def _array(value: Any, label: str, lengths: tuple[int, ...] | None = None) -> PdfArray:
    if not isinstance(value, list) or (
        lengths is not None and len(value) not in lengths
    ):
        _fail(f"{label} has an invalid array length")
    return PdfArray([_number(item, label) for item in value])


def _dictionary(value: Any, label: str, fields: dict[str, str]) -> PdfDictionary:
    if not isinstance(value, dict) or set(value) - fields.keys():
        _fail(f"{label} contains unsupported dictionary entries")
    result = {}
    for key, item in value.items():
        kind = fields[key]
        if item is None:
            converted = PdfNull()
        elif kind in ("name", "text"):
            converted = _string(item, f"{label}.{key}", name=kind == "name")
        elif kind == "bool":
            if type(item) is not bool:
                _fail(f"{label}.{key} must be a boolean")
            converted = PdfBoolean(item)
        elif kind == "array":
            converted = _array(item, f"{label}.{key}")
        elif kind == "formats":
            if not isinstance(item, list):
                _fail(f"{label}.{key} must be an array of number formats")
            converted = PdfArray(
                [
                    _dictionary(
                        entry,
                        "number format",
                        {
                            "Type": "name",
                            "U": "text",
                            "C": "number",
                            "F": "name",
                            "D": "number",
                            "FD": "bool",
                            "RT": "text",
                            "RD": "text",
                            "PS": "text",
                            "SS": "text",
                            "O": "name",
                        },
                    )
                    for entry in item
                ]
            )
        else:
            converted = _number(item, f"{label}.{key}")
        result[PdfName(key)] = converted
    if label == "BS":
        if value.get("W") is not None and value["W"] < 0:
            _fail("BS.W must be non-negative")
        if value.get("D") is not None:
            _dash(value["D"])
    elif label == "BE" and value.get("I") is not None and not 0 <= value["I"] <= 2:
        _fail("BE.I must be between 0 and 2")
    elif label == "Measure":
        if value.get("Subtype") not in (None, "RL"):
            _fail("only rectilinear (RL) measurement dictionaries are supported")
        if value.get("O") is not None and len(value["O"]) != 2:
            _fail("Measure.O must contain two coordinates")
    return PdfDictionary(result)


def _dash(value: list[Any]) -> PdfArray:
    result = _array(value, "dash array")
    if any(item < 0 for item in value) or (value and not any(value)):
        _fail("dash lengths must be non-negative and cannot all be zero")
    return result


def _property(key: str, value: Any) -> Any:
    from aspose_pdf.engine.annotation_json import _PROPERTIES

    if key not in _PROPERTIES:
        _fail(f"unsupported property {key!r}")
    if value is None:
        return PdfNull()
    if key in _TEXT | _NAMES:
        return _string(value, key, name=key in _NAMES)
    if key in _NUMBERS:
        return _number(value, key)
    if key in _BOOLS:
        if type(value) is not bool:
            _fail(f"{key} must be a boolean")
        return PdfBoolean(value)
    if key in ("F", "Q"):
        _integer(value, key)
        if key == "F" and value > 2**31 - 1:
            _fail("F must fit a PDF integer")
        if key == "Q" and value > 2:
            _fail("Q must be 0, 1 or 2")
        return _number(value, key)
    if key == "CA":
        result = _number(value, key, minimum=0)
        if value > 1:
            _fail("CA must be between 0 and 1")
        return result
    if key in ("C", "IC"):
        result = _array(value, key, (0, 1, 3, 4))
        if any(not 0 <= item <= 1 for item in value):
            _fail(f"{key} components must be between 0 and 1")
        return result
    if key == "LE":
        if not isinstance(value, list) or len(value) != 2:
            _fail("LE must contain two PDF names")
        return PdfArray([_string(item, key, name=True) for item in value])
    if key == "InkList":
        if not isinstance(value, list) or not value:
            _fail("InkList must contain paths")
        return PdfArray([_geometry(path, key, 2, 2) for path in value])
    if key in ("QuadPoints", "Vertices"):
        return _geometry(
            value, key, 8 if key == "QuadPoints" else 2, 8 if key == "QuadPoints" else 4
        )
    if key == "Border":
        if not isinstance(value, list) or len(value) not in (3, 4):
            _fail("Border must contain three numbers and an optional dash array")
        result = _array(value[:3], key)
        if any(item < 0 for item in value[:3]):
            _fail("Border radii and width must be non-negative")
        if len(value) == 4:
            result.items.append(_dash(value[3]))
        return result
    if key in ("BS", "BE", "Measure"):
        fields = {
            "BS": {"Type": "name", "W": "number", "S": "name", "D": "array"},
            "BE": {"S": "name", "I": "number"},
            "Measure": {
                "Type": "name",
                "Subtype": "name",
                "R": "text",
                "CYX": "number",
                "O": "array",
                **dict.fromkeys(("X", "Y", "D", "A", "T", "S"), "formats"),
            },
        }
        return _dictionary(value, key, fields[key])
    return _array(value, key, {"CL": (4, 6), "CO": (2,), "L": (4,), "RD": (4,)}[key])


def _geometry(value: Any, label: str, group: int, minimum: int) -> PdfArray:
    result = _array(value, label)
    if len(value) < minimum or len(value) % group:
        _fail(f"{label} has incomplete coordinates")
    return result


def _page_map(value: Any, count: int, budget: _LoadBudget) -> dict[int, int] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        _fail("page_map must be a mapping of source to destination page indices")
    result = {}
    for offset, (source, target) in enumerate(value.items(), 1):
        budget.check(offset, "max_container_items", "annotation import page_map")
        _integer(source, "page_map source")
        _integer(target, "page_map target")
        if target >= count:
            _fail(f"page_map target is out of range: {target}")
        result[source] = target
    return result


def import_json(
    pdf: Any, source: Any, *, page_map: Any = None, target_page: int | None = None
) -> int:
    """Prepare a complete import before attaching new objects to target pages."""
    budget = _LoadBudget(pdf._load_limits)
    payload = _read(source, budget)
    count = pdf.page_count
    mapping = _page_map(page_map, count, budget)
    if target_page is not None:
        _integer(target_page, "destination page")
        if target_page >= count:
            _fail(f"destination page is out of range: {target_page}")
    entries: dict[str, tuple[int, PdfDictionary, dict[str, Any]]] = {}
    source_pages = set()
    for record in payload["annotations"]:
        if not isinstance(record, dict) or set(record) != _FIELDS:
            _fail("each annotation must contain exactly the version 1 record fields")
        identity = record["id"]
        if not isinstance(identity, str) or not identity or identity in entries:
            _fail("annotation IDs must be non-empty unique strings")
        page = _integer(record["page_index"], "page_index")
        source_pages.add(page)
        _integer(record["annotation_index"], "annotation_index")
        if target_page is not None:
            target = target_page
        elif mapping is not None:
            if page not in mapping:
                _fail(f"page_map has no destination for source page {page}")
            target = mapping[page]
        else:
            target = page
        if target >= count:
            _fail(f"destination page is out of range: {target}")
        subtype = record["subtype"]
        if not isinstance(subtype, str) or subtype not in _SUBTYPES:
            _fail(f"unsupported annotation subtype {subtype!r}")
        properties = record["properties"]
        if not isinstance(properties, dict):
            _fail("properties must be an object")
        annot = PdfDictionary(
            {
                PdfName("Type"): PdfName("Annot"),
                PdfName("Subtype"): PdfName(subtype),
                PdfName("Rect"): _array(record["rect"], "rect", (4,)),
                PdfName("Contents"): _string(record["contents"], "contents"),
                PdfName("T"): _string(record["author"], "author"),
                **{
                    PdfName(key): _property(key, value)
                    for key, value in properties.items()
                },
            }
        )
        required = {
            "Line": "L",
            "Polygon": "Vertices",
            "PolyLine": "Vertices",
            "Ink": "InkList",
            "Highlight": "QuadPoints",
            "Underline": "QuadPoints",
            "Squiggly": "QuadPoints",
            "StrikeOut": "QuadPoints",
        }.get(subtype)
        if required is not None and properties.get(required) is None:
            _fail(f"{subtype} requires {required} geometry")
        if subtype == "Polygon" and len(properties["Vertices"]) < 6:
            _fail("Polygon requires at least three vertices")
        links = record["links"]
        if not isinstance(links, dict) or set(links) != _LINKS.keys():
            _fail("links must contain in_reply_to, popup and parent")
        for key, value in links.items():
            if value is not None and (not isinstance(value, str) or not value):
                _fail(f"{key} must be a non-empty ID or null")
        if links["parent"] is not None and subtype != "Popup":
            _fail("only Popup annotations may have parent links")
        entries[identity] = target, annot, dict(links)
    if target_page is not None and len(source_pages) > 1:
        _fail("page import requires records from a single source page")
    if not entries:
        return 0
    _link_entries(entries)
    return _attach(pdf, entries, budget)


def _link_entries(entries: dict[str, tuple[int, PdfDictionary, dict]]) -> None:
    """Validate internal links and complete reciprocal popup relationships."""
    for _page, _annot, links in entries.values():
        for key, target in links.items():
            if target not in entries:
                # Snapshot IDs never resolve against existing PDF objects.
                links[key] = None
    for identity, (page, annot, links) in entries.items():
        for key in ("popup", "parent", "in_reply_to"):
            target = links[key]
            if target is None:
                continue
            if target == identity:
                _fail("an annotation cannot link to itself")
            other_page, other, other_links = entries[target]
            is_popup = annot.mapping[PdfName("Subtype")] == PdfName("Popup")
            other_popup = other.mapping[PdfName("Subtype")] == PdfName("Popup")
            if key == "in_reply_to":
                if is_popup or other_popup:
                    _fail("reply links must connect markup annotations")
                continue
            if (
                page != other_page
                or is_popup != (key == "parent")
                or other_popup != (key == "popup")
            ):
                _fail(
                    "popup links must join a markup annotation and a Popup on the same page"
                )
            inverse = "parent" if key == "popup" else "popup"
            if other_links[inverse] not in (None, identity):
                _fail("conflicting popup relationships")
            other_links[inverse] = identity
    # Iterative traversal prevents cycles and avoids recursive reply walks.
    checked = set()
    for identity in entries:
        active = set()
        current = identity
        while current is not None and current not in checked:
            if current in active:
                _fail("reply links contain a cycle")
            active.add(current)
            current = entries[current][2]["in_reply_to"]
        checked.update(active)


def _attach(
    pdf: Any, entries: dict[str, tuple[int, PdfDictionary, dict]], budget: _LoadBudget
) -> int:
    pdf._ensure_cos()
    cos = pdf._cos_doc
    start = max(max(cos.objects, default=0), max(cos.generations, default=0)) + 1
    budget.check_object_id(start + len(entries) - 1)
    budget.check_objects(len(cos.objects) + len(entries))
    refs = {
        identity: PdfIndirectReference(start + index, 0)
        for index, identity in enumerate(entries)
    }
    pages = {}
    objects = {}
    for identity, (page, annot, links) in entries.items():
        if page not in pages:
            target = pdf._get_page_dict(page)
            if not isinstance(target, PdfDictionary) or isinstance(target, PdfStream):
                _fail(f"destination page {page} is not a page dictionary")
            old = target.mapping.get(PdfName("Annots"))
            array = pdf._resolve(old)
            if (old is not None and array is None) or (
                array is not None and not isinstance(array, (PdfNull, PdfArray))
            ):
                _fail(f"destination page {page} Annots must be an array")
            pages[page] = (
                target,
                old,
                PdfArray(array.items if isinstance(array, PdfArray) else []),
            )
        array = pages[page][2]
        array.items.append(refs[identity])
        budget.check(
            len(array.items), "max_container_items", "destination page annotations"
        )
        for key, target in links.items():
            if target is not None:
                annot.mapping[PdfName(_LINKS[key])] = refs[target]
        number = refs[identity].object_number
        annot._obj_number = number
        objects[number] = annot
    # All parsing, conversion, reference and destination checks precede mutation.
    try:
        cos.objects.update(objects)
        for target, _old, array in pages.values():
            target.mapping[PdfName("Annots")] = array
    except BaseException:
        for number in objects:
            cos.objects.pop(number, None)
        for target, old, _array in pages.values():
            if old is None:
                target.mapping.pop(PdfName("Annots"), None)
            else:
                target.mapping[PdfName("Annots")] = old
        raise
    return len(entries)
