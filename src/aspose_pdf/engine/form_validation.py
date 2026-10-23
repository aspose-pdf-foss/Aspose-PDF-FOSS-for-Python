"""Read-only checks of effective AcroForm values, without lossy normalization."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from aspose_pdf.exceptions import PdfParseException

from .cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNull,
    PdfNumber,
    PdfStream,
    PdfString,
    decode_pdf_text_string,
)
from .form_fields import _OFF_VALUES

_ATTRIBUTES = ("FT", "Ff", "V", "MaxLen", "Opt")
_MESSAGES = {
    "invalid_value": "Stored value has an invalid type, cardinality, or duplicate selection",
    "required": "A required field has no value or selection",
    "max_length": "Text exceeds the field's maximum character count",
    "invalid_choice": "Selection is not one of the declared export or on-state values",
}


@dataclass(frozen=True, slots=True)
class FormValidationIssue:
    """An immutable field-value problem; messages never contain stored values.

    ``field_name`` is fully qualified; ``field_type`` is a lowercase data-field
    type. ``code`` is ``invalid_value``, ``required``, ``max_length`` or
    ``invalid_choice``. An empty report only confirms these local value checks.
    """

    field_name: str
    field_type: str
    code: str
    message: str


class _Reader:
    """Bound metadata reads and memoize complete parent chains for this call."""

    def __init__(self, pdf: Any, *, attributes: tuple[str, ...] = _ATTRIBUTES):
        self.pdf = pdf
        self.attributes = attributes
        self.budget = pdf._load_budget
        self.parents: dict[int, tuple[dict[str, Any], int]] = {}

    def resolve(self, value: Any) -> Any:
        return self.pdf._resolve(value)

    def array(self, value: Any, context: str) -> list:
        value = self.resolve(value)
        if not isinstance(value, PdfArray):
            return []
        self.budget.check(len(value.items), "max_container_items", context)
        return value.items

    def text(self, value: Any) -> str | None:
        if isinstance(value, PdfString):
            self.budget.check(
                len(value.value), "max_object_bytes", "form validation text"
            )
            return decode_pdf_text_string(value)
        if isinstance(value, PdfName):
            self.budget.check(
                len(value.name), "max_object_bytes", "form validation name"
            )
            return value.name.lstrip("/")
        return None

    def field_name(self, prefix: str, partial: str) -> str:
        """Bound a fully qualified name before retaining it in the field index."""
        self.budget.check(
            len(prefix) + len(partial) + 1,
            "max_object_bytes",
            "form validation field name",
        )
        return f"{prefix}.{partial}" if prefix else partial

    def inherited(self, entry: PdfDictionary) -> dict[str, Any]:
        chain: list[PdfDictionary] = []
        seen: set[int] = set()
        node = entry
        attributes: dict[str, Any] = {}
        depth = 0
        while node is not None and not isinstance(node, PdfNull):
            if not isinstance(node, PdfDictionary) or isinstance(node, PdfStream):
                raise PdfParseException("Invalid form field parent dictionary")
            key = id(node)
            if key in seen:
                raise PdfParseException("Form field parent chain contains a cycle")
            if key in self.parents:
                attributes, depth = self.parents[key]
                break
            self.budget.check(
                len(chain) + 1, "max_nesting_depth", "form validation parents"
            )
            self.budget.check(
                len(self.parents) + len(chain) + 1,
                "max_container_items",
                "form validation parent index",
            )
            seen.add(key)
            chain.append(node)
            node = self.resolve(node.mapping.get(PdfName("Parent")))
        self.budget.check(
            depth + len(chain), "max_nesting_depth", "form validation parents"
        )
        for node in reversed(chain):
            attributes = dict(attributes)
            for key in self.attributes:
                if PdfName(key) in node.mapping:
                    attributes[key] = self.resolve(node.mapping[PdfName(key)])
            depth += 1
            self.parents[id(node)] = (attributes, depth)
        return attributes


def _flags(attributes: dict) -> int:
    value = attributes.get("Ff")
    if isinstance(value, PdfNumber) and math.isfinite(value.value):
        return int(value.value)
    return 0


def _kind(attributes: dict) -> str:
    value = attributes["FT"].name
    flags = _flags(attributes)
    if value == "/Btn":
        if flags & (1 << 16):
            return "pushbutton"
        return "radio" if flags & (1 << 15) else "checkbox"
    if value == "/Ch":
        return "combobox" if flags & (1 << 17) else "listbox"
    return "signature" if value == "/Sig" else "text"


def _field_index(
    reader: _Reader, *, strict: bool = False,
) -> dict[str, tuple[PdfDictionary, dict, str]]:
    pdf, budget = reader.pdf, reader.budget
    if pdf._cos_doc is None:
        return {}
    root = reader.resolve(pdf._cos_doc.trailer.mapping.get(PdfName("Root")))
    if strict and (not isinstance(root, PdfDictionary) or isinstance(root, PdfStream)):
        raise PdfParseException("Invalid form catalog dictionary")
    acro = (
        reader.resolve(root.mapping.get(PdfName("AcroForm")))
        if isinstance(root, PdfDictionary)
        else None
    )
    if acro is None or isinstance(acro, PdfNull):
        return {}
    if not isinstance(acro, PdfDictionary) or (strict and isinstance(acro, PdfStream)):
        if strict:
            raise PdfParseException("Invalid AcroForm dictionary")
        return {}
    roots = reader.array(acro.mapping.get(PdfName("Fields")), "form validation roots")
    stack = [(ref, "", {}, None, 1) for ref in reversed(roots)]
    seen: set[int] = set()
    nonterminal: set[int] = set()
    candidates = []
    while stack:
        reference, prefix, inherited, named_parent, depth = stack.pop()
        budget.check(depth, "max_nesting_depth", "form validation field depth")
        entry = reader.resolve(reference)
        if not isinstance(entry, PdfDictionary) or isinstance(entry, PdfStream):
            if strict:
                raise PdfParseException("Each form field must be a dictionary")
            continue
        key = id(entry)
        if key in seen:
            raise PdfParseException(
                "Form field tree contains a cycle or repeated field"
            )
        budget.check(
            len(seen) + 1, "max_container_items", "form validation field nodes"
        )
        seen.add(key)
        attributes = dict(inherited)
        attributes.update(reader.inherited(entry))
        title = reader.resolve(entry.mapping.get(PdfName("T")))
        if strict and title is not None and not isinstance(title, (PdfString, PdfNull)):
            raise PdfParseException("Form field names must be PDF text strings")
        name = prefix
        if isinstance(title, PdfString):
            partial = reader.text(title)
            name = reader.field_name(prefix, partial)
            if named_parent is not None:
                nonterminal.add(named_parent)
            named_parent = key
            if isinstance(attributes.get("FT"), PdfName):
                candidates.append((name, entry, attributes, _kind(attributes)))
        children = reader.array(
            entry.mapping.get(PdfName("Kids")), "form validation children"
        )
        budget.check(
            len(stack) + len(children),
            "max_container_items",
            "form validation traversal stack",
        )
        stack.extend(
            (ref, name, attributes, named_parent, depth + 1)
            for ref in reversed(children)
        )

    fields = {}
    for name, entry, attributes, kind in candidates:
        if id(entry) in nonterminal:
            continue
        if name in fields:
            raise PdfParseException(
                "Form contains duplicate fully qualified field names"
            )
        fields[name] = (entry, attributes, kind)
    return fields


def _on_states(reader: _Reader, field: PdfDictionary) -> set[str]:
    widgets = []
    if reader.resolve(field.mapping.get(PdfName("Subtype"))) == PdfName("Widget"):
        widgets.append(field)
    for ref in reader.array(
        field.mapping.get(PdfName("Kids")), "form validation widget children"
    ):
        widget = reader.resolve(ref)
        if isinstance(widget, PdfDictionary) and reader.resolve(
            widget.mapping.get(PdfName("Subtype"))
        ) == PdfName("Widget"):
            reader.budget.check(
                len(widgets) + 1, "max_container_items", "form validation widgets"
            )
            widgets.append(widget)
    states = set()
    for widget in widgets:
        ap = reader.resolve(widget.mapping.get(PdfName("AP")))
        if not isinstance(ap, PdfDictionary):
            continue
        normal = reader.resolve(ap.mapping.get(PdfName("N")))
        if not isinstance(normal, PdfDictionary) or isinstance(normal, PdfStream):
            normal = reader.resolve(ap.mapping.get(PdfName("D")))
        if not isinstance(normal, PdfDictionary) or isinstance(normal, PdfStream):
            continue
        reader.budget.check(
            len(normal.mapping),
            "max_container_items",
            "form validation appearance states",
        )
        for key in normal.mapping:
            state = reader.text(key)
            if state is not None and state != "Off":
                states.add(state)
                break
    return states


def _exports(reader: _Reader, attributes: dict) -> set[str]:
    exports = set()
    for item in reader.array(attributes.get("Opt"), "form validation choice options"):
        item = reader.resolve(item)
        if isinstance(item, PdfArray):
            pair = reader.array(item, "form validation option pair")
            item = reader.resolve(pair[0]) if len(pair) >= 2 else None
        text = reader.text(item)
        if text is not None:
            exports.add(text)
    return exports


def _check(
    reader: _Reader, field: PdfDictionary, attributes: dict, kind: str
) -> str | None:
    value = attributes.get("V")
    empty = value is None or isinstance(value, PdfNull)
    required = bool(_flags(attributes) & 2)
    if kind == "text":
        text = "" if empty else reader.text(value)
        if text is None:
            return "invalid_value"
        if required and not text.strip():
            return "required"
        limit = attributes.get("MaxLen")
        if isinstance(limit, PdfNumber):
            number = limit.value
            if (
                (
                    isinstance(number, int)
                    or (
                        isinstance(number, float)
                        and math.isfinite(number)
                        and number.is_integer()
                    )
                )
                and number > 0
                and len(text) > number
            ):
                return "max_length"
        return None
    if kind in ("checkbox", "radio"):
        state = "" if empty else reader.text(value)
        if state is None:
            return "invalid_value"
        off = (
            _OFF_VALUES
            if kind == "checkbox" and isinstance(value, PdfString)
            else ("", "Off")
        )
        if state in off:
            return "required" if required else None
        states = _on_states(reader, field)
        return "invalid_choice" if states and state not in states else None

    multiple = kind == "listbox" and bool(_flags(attributes) & (1 << 21))
    if empty:
        selected = []
    elif isinstance(value, PdfArray):
        items = reader.array(value, "form validation selected values")
        if not multiple and len(items) > 1:
            return "invalid_value"
        selected = [reader.text(reader.resolve(item)) for item in items]
    else:
        selected = [reader.text(value)]
    if any(item is None for item in selected) or len(set(selected)) != len(selected):
        return "invalid_value"
    if not selected:
        return "required" if required else None
    exports = _exports(reader, attributes)
    if kind == "combobox" and _flags(attributes) & (1 << 18):
        return (
            "required"
            if required and selected[0] not in exports and not selected[0].strip()
            else None
        )
    return "invalid_choice" if any(item not in exports for item in selected) else None


def validate_form_fields(
    pdf: Any,
    fields: Iterable[str] | None = None,
    *,
    strict: bool = True,
) -> list[FormValidationIssue]:
    """Validate selected current terminal fields using the document's budget."""
    if not isinstance(strict, bool):
        raise TypeError("strict must be a boolean")
    reader = _Reader(pdf)
    selection = None
    if fields is not None:
        if isinstance(fields, (str, bytes, bytearray, Mapping)) or not isinstance(
            fields, Iterable
        ):
            raise TypeError("fields must be an iterable of field names")
        selection = []
        seen = set()
        for count, name in enumerate(fields, 1):
            reader.budget.check(
                count, "max_container_items", "form validation requested names"
            )
            if not isinstance(name, str):
                raise TypeError("field names must be strings")
            if name not in seen:
                seen.add(name)
                selection.append(name)
        if not selection:
            return []
    index = _field_index(reader)
    if selection is None:
        selection = index
    elif strict:
        for name in selection:
            if name not in index:
                raise KeyError(f"Field '{name}' not found")
    issues = []
    for name in selection:
        if name not in index:
            continue
        field, attributes, kind = index[name]
        if kind in ("signature", "pushbutton"):
            continue
        code = _check(reader, field, attributes, kind)
        if code is not None:
            reader.budget.check(
                len(issues) + 1, "max_container_items", "form validation issues"
            )
            issues.append(FormValidationIssue(name, kind, code, _MESSAGES[code]))
    return issues
