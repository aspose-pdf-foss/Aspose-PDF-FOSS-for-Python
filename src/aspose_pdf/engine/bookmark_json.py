"""Versioned, bounded JSON exchange for the public bookmark tree."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
from typing import Any

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.interactive import Action, Destination, GoToAction
from aspose_pdf.load_limits import _LoadBudget, _read_limited
from aspose_pdf.outlines import OutlineCollection, OutlineItem

from .cos import (
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
from .form_json import _check_limits
from .simple_pdf import (
    action_from_cos,
    destination_from_cos,
    explicit_destination,
    named_destinations,
)

_VIEWS = {
    "Fit": 0,
    "FitB": 0,
    "XYZ": 3,
    "FitH": 1,
    "FitBH": 1,
    "FitV": 1,
    "FitBV": 1,
    "FitR": 4,
}
_FIELDS = {"title", "target", "is_bold", "is_italic", "color", "open", "children"}


def _fail(message: str) -> Any:
    raise PdfValidationException(f"Invalid bookmark JSON: {message}")


def _number(value: Any) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _text(value: Any, budget: _LoadBudget) -> str:
    if not isinstance(value, str):
        _fail("text values must be strings")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError:
        _fail("text contains invalid Unicode")
    budget.check(size, "max_object_bytes", "bookmark JSON text bytes")
    return value


def _boolean(value: Any, name: str) -> bool:
    if type(value) is not bool:
        _fail(f"{name} must be a boolean")
    return value


def _keys(value: Any, expected: set[str]) -> None:
    if not isinstance(value, dict) or value.keys() != expected:
        _fail(f"expected exactly these fields: {', '.join(sorted(expected))}")


def _json_limits(value: Any, budget: _LoadBudget) -> None:
    pending, count = [(value, 1)], 0
    while pending:
        value, depth = pending.pop()
        count += 1
        budget.check(count, "max_container_items", "bookmark JSON values")
        if isinstance(value, (dict, list)):
            budget.check(depth, "max_nesting_depth", "bookmark JSON nesting")
            budget.check(len(value), "max_container_items", "bookmark JSON container")
            if isinstance(value, dict):
                pending.extend((key, depth + 1) for key in value)
                pending.extend((item, depth + 1) for item in value.values())
            else:
                pending.extend((item, depth + 1) for item in value)
        elif isinstance(value, str):
            _text(value, budget)
        elif isinstance(value, float) and not math.isfinite(value):
            _fail("numbers must be finite")


def _read(source: Any, budget: _LoadBudget) -> list:
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

    def pairs(items: list) -> dict:
        result = {}
        for key, value in items:
            if key in result:
                _fail(f"duplicate key {key!r}")
            result[key] = value
        return result

    try:
        text = data.decode("utf-8-sig")
        _check_limits(text, budget, "bookmark JSON")
        payload = json.loads(
            text,
            object_pairs_hook=pairs,
            parse_constant=lambda value: _fail(f"invalid number {value}"),
        )
    except (UnicodeError, ValueError, RecursionError) as error:
        raise PdfValidationException("Invalid UTF-8 bookmark JSON") from error
    _json_limits(payload, budget)
    _keys(payload, {"schema_version", "bookmarks"})
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        _fail("schema_version must be 1")
    if not isinstance(payload["bookmarks"], list):
        _fail("bookmarks must be an array")
    return payload["bookmarks"]


class _Context:
    def __init__(self, document: Any):
        self.document, self.pdf = document, document._engine_pdf
        self.budget = _LoadBudget(self.pdf._load_limits)
        self.budget.check(
            self.pdf.page_count, "max_container_items", "bookmark page index"
        )
        numbers = self.pdf._page_reference_numbers() if self.pdf.page_count else []
        self.refs = [self.pdf._cos_doc.reference(number) for number in numbers]
        self.indices = {
            (ref.object_number, ref.gen_number): index
            for index, ref in enumerate(self.refs)
        }
        self.names = None
        self.work = 0

    def resolve(self, value: Any) -> Any:
        seen = set()
        while isinstance(value, PdfIndirectReference):
            key = value.object_number, value.gen_number
            if key in seen:
                _fail("target contains an indirect-reference cycle")
            seen.add(key)
            self.budget.check(
                len(seen), "max_nesting_depth", "bookmark target references"
            )
            value = self.pdf._resolve(value)
            if value is None:
                _fail("target contains a missing indirect object")
        return value

    def named(self) -> dict:
        if self.names is None:
            catalog = (
                self.resolve(self.pdf._cos_doc.trailer.mapping.get(PdfName("Root")))
                if self.pdf._cos_doc
                else None
            )
            self.names = named_destinations(catalog, self.resolve, self.budget)
        return self.names

    def tick(self, depth: int) -> None:
        self.work += 1
        self.budget.check(self.work, "max_container_items", "bookmark tree items")
        self.budget.check(depth, "max_nesting_depth", "bookmark tree depth")

    def page(self, value: Any, mapping: dict | None = None) -> int:
        if type(value) is not int or value < 0:
            _fail("page_index must be a non-negative integer")
        if mapping is not None:
            if value not in mapping:
                _fail(f"page_map has no destination for source page {value}")
            value = mapping[value]
        if value >= len(self.refs):
            _fail(f"page_index is outside the destination document: {value}")
        return value

    def view(self, target: dict) -> None:
        view, params = target["view"], target["parameters"]
        if not isinstance(view, str) or view not in _VIEWS:
            _fail("unsupported destination view")
        if not isinstance(params, list) or len(params) != _VIEWS[view]:
            _fail("destination view has the wrong parameter count")
        if any(
            not (_number(value) or (value is None and view != "FitR"))
            for value in params
        ):
            _fail("destination parameters must be finite numbers or allowed nulls")
        if view == "XYZ" and params[2] is not None and params[2] < 0:
            _fail("XYZ zoom must be non-negative")
        if view == "FitR" and (params[0] >= params[2] or params[1] >= params[3]):
            _fail("FitR bounds must increase")

    def encode_destination(
        self, raw: Any, resolve_names: bool, remote: bool = False
    ) -> dict:
        raw = self.resolve(raw)
        if isinstance(raw, (PdfString, PdfName)):
            if resolve_names and not remote:
                raw = explicit_destination(raw, self.resolve, self.named())
                if raw is None:
                    _fail("named destination is undefined or cannot be resolved")
            else:
                return {
                    "kind": "named",
                    "name": raw.value.decode("latin-1")
                    if isinstance(raw, PdfString)
                    else raw.name.removeprefix("/"),
                    "representation": "string"
                    if isinstance(raw, PdfString)
                    else "name",
                }
        if not isinstance(raw, PdfArray) or len(raw.items) < 2:
            _fail("unsupported or malformed bookmark destination")
        self.budget.check(
            len(raw.items), "max_container_items", "bookmark destination array"
        )
        page, view, *params = raw.items
        view = self.resolve(view)
        if not isinstance(view, PdfName):
            _fail("destination view must be a PDF name")
        if remote:
            page = self.resolve(page)
            if (
                not isinstance(page, PdfNumber)
                or type(page.value) is not int
                or not 0 <= page.value <= 2**31 - 1
            ):
                _fail("remote destination needs a non-negative integer page number")
            page_index = page.value
        else:
            if (
                not isinstance(page, PdfIndirectReference)
                or (page.object_number, page.gen_number) not in self.indices
            ):
                _fail("bookmark target is not a current document page")
            page_index = self.indices[page.object_number, page.gen_number]
        values = []
        for value in params:
            value = self.resolve(value)
            if isinstance(value, PdfNull):
                values.append(None)
            elif isinstance(value, PdfNumber):
                values.append(value.value)
            else:
                _fail("unsupported destination parameter")
        target = {
            "kind": "page",
            "page_index": page_index,
            "view": view.name.removeprefix("/"),
            "parameters": values,
        }
        self.view(target)
        return target

    def encode_target(
        self, loaded: Any, target: Any, page: Any, resolve_names: bool
    ) -> Any:
        if loaded is None:
            if target is None:
                if page is None:
                    return None
                page = self.page(page)
                raw, key = PdfArray([self.refs[page], PdfName("Fit")]), "Dest"
            elif isinstance(target, (Destination, GoToAction)):
                local = target.destination if isinstance(target, GoToAction) else target
                if isinstance(local, str) and isinstance(target, GoToAction):
                    destination = self.decode_destination(
                        {"kind": "named", "name": local, "representation": "string"},
                        None,
                    )
                elif isinstance(local, Destination):
                    page = self.page(local.page)
                    try:
                        view, params = local._spec()
                    except NotImplementedError:
                        _fail("unsupported destination class")
                    values = {
                        "kind": "page",
                        "page_index": page,
                        "view": view,
                        "parameters": list(params),
                    }
                    destination = self.decode_destination(values, None)
                else:
                    _fail("GoTo action must contain a typed destination")
                if isinstance(target, GoToAction):
                    raw, key = (
                        PdfDictionary(
                            {PdfName("S"): PdfName("GoTo"), PdfName("D"): destination}
                        ),
                        "A",
                    )
                else:
                    raw, key = destination, "Dest"
            elif isinstance(target, (Action, str)):
                if isinstance(target, Action):
                    spec = target._spec()
                    kind = spec.get("S")
                    field = {"URI": "URI", "GoToR": "F", "Named": "N"}.get(kind)
                    if field is None or not isinstance(spec.get(field), str):
                        _fail("unsupported action or invalid action string")
                    _text(spec[field], self.budget)
                    remote = spec.get("D")
                    if kind == "GoToR" and isinstance(remote, Destination):
                        view, params = remote._spec()
                        self.view({"view": view, "parameters": list(params)})
                raw, key = self.pdf._interactive_target_cos(target)
            else:
                _fail("unsupported bookmark target")
        else:
            if not isinstance(loaded, tuple) or len(loaded) != 2:
                _fail("invalid retained bookmark target")
            key, raw = loaded
        if key == "Dest":
            return self.encode_destination(raw, resolve_names)
        raw = self.resolve(raw)
        if (
            key != "A"
            or not isinstance(raw, PdfDictionary)
            or isinstance(raw, PdfStream)
        ):
            _fail("unsupported bookmark target entry")
        kind = self.resolve(raw.mapping.get(PdfName("S")))
        kind = kind.name.removeprefix("/") if isinstance(kind, PdfName) else ""
        action_type = self.resolve(raw.mapping.get(PdfName("Type")))
        if (
            action_type is not None
            and not isinstance(action_type, PdfNull)
            and action_type != PdfName("Action")
        ):
            _fail("action Type must be Action when present")
        allowed = {
            "GoTo": {"D"},
            "URI": {"URI", "IsMap"},
            "GoToR": {"F", "D", "NewWindow"},
            "Named": {"N"},
        }.get(kind)
        if allowed is None or any(
            key.name.removeprefix("/") not in allowed | {"Type", "S"}
            for key in raw.mapping
        ):
            _fail(f"unsupported bookmark action or action entries: {kind!r}")

        def string(name: str) -> str:
            value = self.resolve(raw.mapping.get(PdfName(name)))
            if not isinstance(value, PdfString):
                _fail(f"action {name} must be a string")
            return decode_pdf_text_string(value)

        def flag(name: str) -> bool | None:
            value = self.resolve(raw.mapping.get(PdfName(name)))
            if value is None or isinstance(value, PdfNull):
                return None
            if not isinstance(value, PdfBoolean):
                _fail(f"action {name} must be a boolean")
            return value.value

        if kind == "GoTo":
            return {
                "kind": "goto",
                "destination": self.encode_destination(
                    raw.mapping.get(PdfName("D")), resolve_names
                ),
            }
        if kind == "URI":
            return {"kind": "uri", "uri": string("URI"), "is_map": flag("IsMap")}
        if kind == "GoToR":
            return {
                "kind": "remote",
                "file": string("F"),
                "destination": self.encode_destination(
                    raw.mapping.get(PdfName("D")), False, True
                ),
                "new_window": flag("NewWindow"),
            }
        name = self.resolve(raw.mapping.get(PdfName("N")))
        if not isinstance(name, PdfName):
            _fail("Named action must have a PDF name")
        return {"kind": "named_action", "name": name.name.removeprefix("/")}

    def decode_destination(
        self, target: Any, mapping: dict | None, remote: bool = False
    ) -> Any:
        if not isinstance(target, dict):
            _fail("destination must be an object")
        if target.get("kind") == "named":
            _keys(target, {"kind", "name", "representation"})
            name = _text(target["name"], self.budget)
            if target["representation"] == "string":
                try:
                    return PdfString(name.encode("latin-1"))
                except UnicodeError:
                    _fail("string destination names must be Latin-1")
            if target["representation"] == "name" and name and not name.startswith("/"):
                return PdfName(name)
            _fail("invalid name representation")
        _keys(target, {"kind", "page_index", "view", "parameters"})
        if target["kind"] != "page":
            _fail("unsupported destination kind")
        self.view(target)
        page = target["page_index"]
        if remote:
            if type(page) is not int or not 0 <= page <= 2**31 - 1:
                _fail("remote page_index must be a non-negative PDF integer")
            page = PdfNumber(page)
        else:
            page = self.refs[self.page(page, mapping)]
        return PdfArray(
            [
                page,
                PdfName(target["view"]),
                *[
                    PdfNull() if value is None else PdfNumber(value)
                    for value in target["parameters"]
                ],
            ]
        )

    def decode_target(self, target: Any, mapping: dict | None) -> Any:
        if target is None:
            return None
        if not isinstance(target, dict):
            _fail("target must be an object or null")
        kind = target.get("kind")
        if kind in ("page", "named"):
            return "Dest", self.decode_destination(target, mapping)
        if kind == "goto":
            _keys(target, {"kind", "destination"})
            values = {
                "S": PdfName("GoTo"),
                "D": self.decode_destination(target["destination"], mapping),
            }
        elif kind == "uri":
            _keys(target, {"kind", "uri", "is_map"})
            values = {
                "S": PdfName("URI"),
                "URI": PdfString(_text(target["uri"], self.budget)),
            }
            flag = target["is_map"]
            if flag is not None:
                values["IsMap"] = PdfBoolean(_boolean(flag, "is_map"))
        elif kind == "remote":
            _keys(target, {"kind", "file", "destination", "new_window"})
            values = {
                "S": PdfName("GoToR"),
                "F": PdfString(_text(target["file"], self.budget)),
                "D": self.decode_destination(target["destination"], None, True),
            }
            if target["new_window"] is not None:
                values["NewWindow"] = PdfBoolean(
                    _boolean(target["new_window"], "new_window")
                )
        elif kind == "named_action":
            _keys(target, {"kind", "name"})
            name = _text(target["name"], self.budget)
            if not name or name.startswith("/"):
                _fail("named action must have a non-empty PDF name")
            values = {"S": PdfName("Named"), "N": PdfName(name)}
        else:
            _fail(f"unsupported target kind: {kind!r}")
        return "A", PdfDictionary(
            {PdfName(key): value for key, value in values.items()}
        )

    def current_page(self, loaded: Any) -> int | None:
        if loaded is None:
            return None
        key, raw = loaded
        raw = self.resolve(raw)
        if key == "A":
            if not isinstance(raw, PdfDictionary) or self.resolve(
                raw.mapping.get(PdfName("S"))
            ) != PdfName("GoTo"):
                return None
            raw = self.resolve(raw.mapping.get(PdfName("D")))
        if isinstance(raw, (PdfString, PdfName)):
            raw = explicit_destination(raw, self.resolve, self.named())
        if (
            isinstance(raw, PdfArray)
            and raw.items
            and isinstance(raw.items[0], PdfIndirectReference)
        ):
            page = raw.items[0]
            return self.indices.get((page.object_number, page.gen_number))
        return None

    def check_new_objects(self, node_count: int) -> None:
        if not node_count or self.pdf._cos_doc is None:
            return
        cos = self.pdf._cos_doc
        catalog = self.resolve(cos.trailer.mapping.get(PdfName("Root")))
        slots = (
            self.pdf._outline_slots(catalog)
            if isinstance(catalog, PdfDictionary)
            else []
        )
        for number in slots:
            obj = cos.objects.get(number)
            kind = (
                self.resolve(obj.mapping.get(PdfName("Type")))
                if isinstance(obj, PdfDictionary)
                else None
            )
            if kind in (PdfName("Catalog"), PdfName("Pages"), PdfName("Page")):
                _fail("existing outline slots reference document/page objects")
        reusable = len(slots)
        additional = max(0, node_count + 1 - reusable)
        self.budget.check_objects(len(cos.objects) + additional)
        if additional:
            largest = max(max(cos.objects, default=0), max(cos.generations, default=0))
            self.budget.check_object_id(largest + additional)


def _walk(roots: Any, context: _Context):
    if not isinstance(roots, (list, tuple)):
        _fail("bookmark children must be an array")
    context.budget.check(len(roots), "max_container_items", "bookmark siblings")
    stack = [(item, None, 1) for item in reversed(roots)]
    seen = set()
    while stack:
        item, parent, depth = stack.pop()
        context.tick(depth)
        if id(item) in seen:
            _fail("bookmark tree contains a cycle or repeated item")
        seen.add(id(item))
        if isinstance(item, OutlineItem):
            entry = {
                "title": item.title,
                "is_bold": item.is_bold,
                "is_italic": item.is_italic,
                "color": item.color,
                "open": item.open,
                "children": item.children,
                "target": item.destination,
                "loaded_target": item._loaded_target,
                "page_index": item.page_index,
            }
        elif isinstance(item, dict):
            entry = item
        else:
            _fail("each bookmark must be an object")
        children = entry.get("children", [])
        if not isinstance(children, (list, tuple)):
            _fail("bookmark children must be an array")
        context.budget.check(
            len(stack) + len(children),
            "max_container_items",
            "bookmark traversal stack",
        )
        yield item, entry, parent
        stack.extend((child, id(item), depth + 1) for child in reversed(children))


def _style(entry: dict, context: _Context) -> dict:
    color = entry.get("color")
    if color is not None and (
        not isinstance(color, (list, tuple))
        or len(color) != 3
        or any(not _number(value) or not 0 <= value <= 1 for value in color)
    ):
        _fail("color must contain three finite RGB components between 0 and 1")
    return {
        "title": _text(entry.get("title", ""), context.budget),
        "is_bold": _boolean(entry.get("is_bold", False), "is_bold"),
        "is_italic": _boolean(entry.get("is_italic", False), "is_italic"),
        "color": list(color) if color is not None else None,
        "open": _boolean(entry.get("open", False), "open"),
    }


def export_json(document: Any, resolve_names: bool) -> bytes:
    _boolean(resolve_names, "resolve_named_destinations")
    context = _Context(document)
    roots = (
        document._outlines._items
        if document._outlines is not None
        else context.pdf._outlines_data
    )
    result, outputs = [], {}
    for item, entry, parent in _walk(roots, context):
        style = _style(entry, context)
        try:
            target = context.encode_target(
                entry.get("loaded_target"),
                entry.get("target"),
                entry.get("page_index"),
                resolve_names,
            )
        except (TypeError, ValueError, OverflowError, NotImplementedError) as error:
            raise PdfValidationException(
                "Unsupported or invalid bookmark target"
            ) from error
        # Use the import validator too, so every exported target is importable.
        context.decode_target(target, None)
        record = {
            "title": style.pop("title"),
            "target": target,
            **style,
            "children": [],
        }
        (result if parent is None else outputs[parent]).append(record)
        outputs[id(item)] = record["children"]
    payload = {"schema_version": 1, "bookmarks": result}
    _json_limits(payload, context.budget)
    try:
        return (
            json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
        ).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise PdfValidationException(
            "Bookmarks cannot be encoded as UTF-8 JSON"
        ) from error


def import_json(document: Any, source: Any, replace: bool, page_map: Any) -> int:
    _boolean(replace, "replace")
    budget = _LoadBudget(document.load_limits)
    data = _read(source, budget)
    context = _Context(document)
    mapping = None
    if page_map is not None:
        if not isinstance(page_map, Mapping):
            _fail("page_map must map source page indices to destination indices")
        mapping = {}
        for offset, (source_page, target_page) in enumerate(page_map.items(), 1):
            budget.check(offset, "max_container_items", "bookmark page_map")
            if type(source_page) is not int or source_page < 0:
                _fail("page_map keys must be non-negative integers")
            mapping[source_page] = context.page(target_page)
    if data and not context.refs:
        _fail("a nonempty bookmark import requires a document page")
    roots, outputs = [], {}
    for record, entry, parent in _walk(data, context):
        _keys(entry, _FIELDS)
        if not isinstance(entry["children"], list):
            _fail("children must be an array")
        style = _style(entry, context)
        loaded = context.decode_target(entry["target"], mapping)
        item = OutlineItem(**style, page_index=None)
        if loaded is not None:
            key, raw = loaded

            def index_of(ref):
                return context.indices.get((ref.object_number, ref.gen_number))

            typed = (
                action_from_cos(raw, context.resolve, index_of)
                if key == "A"
                else destination_from_cos(raw, context.resolve, index_of)
            )
            item.destination = (
                typed if isinstance(typed, (Action, Destination)) else None
            )
            item._loaded_target = loaded
            item._page_index = context.current_page(loaded)
        (roots if parent is None else outputs[parent]).append(item)
        outputs[id(record)] = item.children
    if not roots and not replace:
        return 0
    count = context.work
    collection = document._outlines
    if collection is None:
        collection = OutlineCollection() if replace else _existing_collection(context)
    combined = roots if replace else [*collection._items, *roots]
    # Bound the combined tree before changing either the live collection or PDF.
    combined_context = _Context(document)
    for _ in _walk(combined, combined_context):
        pass
    combined_context.check_new_objects(combined_context.work)
    collection._items = combined
    document._outlines = collection
    return count


def _existing_collection(context: _Context) -> OutlineCollection:
    collection, outputs = OutlineCollection(), {}
    for item, entry, parent in _walk(context.pdf._outlines_data, context):
        copied = OutlineItem(
            entry.get("title", ""),
            page_index=entry.get("page_index"),
            is_bold=entry.get("is_bold", False),
            is_italic=entry.get("is_italic", False),
            color=entry.get("color"),
            open=entry.get("open", False),
            destination=entry.get("target"),
        )
        copied._loaded_target = entry.get("loaded_target")
        index = context.current_page(copied._loaded_target)
        if index is not None:
            copied._page_index = index
        (collection._items if parent is None else outputs[parent]).append(copied)
        outputs[id(item)] = copied.children
    return collection
