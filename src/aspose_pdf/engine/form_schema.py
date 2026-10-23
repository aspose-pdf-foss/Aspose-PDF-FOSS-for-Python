"""Read-only AcroForm template descriptions and bounded JSON export."""

from __future__ import annotations

import io
import json
import math
from typing import Any

from aspose_pdf.exceptions import PdfValidationException, UnsupportedFeatureException
from aspose_pdf.load_limits import _LoadBudget

from .cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNull,
    PdfNumber,
    PdfStream,
)
from .form_validation import _field_index, _Reader

_ATTRIBUTES = ("FT", "Ff", "DV", "MaxLen", "Opt")


class _SchemaReader(_Reader):
    """Index effective metadata without resolving current field values."""

    def __init__(self, pdf: Any):
        super().__init__(pdf, attributes=_ATTRIBUTES)
        self.retained = 0
        self.items = 0

    def array(self, value: Any, context: str) -> list:
        value = self.resolve(value)
        if value is None or isinstance(value, PdfNull):
            return []
        if not isinstance(value, PdfArray):
            raise PdfValidationException(f"{context} must be an array")
        self.items += len(value.items)
        self.budget.check(self.items, "max_container_items", "form schema metadata items")
        return super().array(value, context)

    def text(self, value: Any) -> str | None:
        text = super().text(value)
        if text is not None:
            self.account_text(text)
        return text

    def field_name(self, prefix: str, partial: str) -> str:
        name = super().field_name(prefix, partial)
        self.account_text(name)
        return name

    def account_text(self, text: str) -> None:
        """Bound metadata text work, including expanded fully qualified names."""
        size = 0
        try:
            for offset in range(0, len(text), 4096):
                chunk_size = len(text[offset:offset + 4096].encode("utf-8"))
                size += chunk_size
                self.retained += chunk_size
                self.budget.check(size, "max_object_bytes", "form schema text")
                self.budget.check(self.retained, "max_total_decoded_bytes", "form schema text")
        except UnicodeError as exc:
            raise PdfValidationException("Form schema text is not valid Unicode") from exc

    def required_text(self, value: Any, context: str) -> str:
        text = self.text(self.resolve(value))
        if text is None:
            raise PdfValidationException(f"{context} must be text")
        return text

    def optional_text(self, field: PdfDictionary, key: str) -> str | None:
        value = self.resolve(field.mapping.get(PdfName(key)))
        if value is None or isinstance(value, PdfNull):
            return None
        return self.required_text(value, f"Form field {key}")

    def inherited(self, entry: PdfDictionary) -> dict[str, Any]:
        attributes = super().inherited(entry)
        flags = attributes.get("Ff")
        if flags is not None and not isinstance(flags, PdfNull):
            _integer(flags, "Field flags", 2**32 - 1)
        kind = attributes.get("FT")
        if kind is not None and not isinstance(kind, (PdfName, PdfNull)):
            raise PdfValidationException("Form field type must be a PDF name")
        return attributes


def _integer(value: Any, context: str, maximum: int, minimum: int = 0) -> int:
    try:
        if (
            isinstance(value, PdfNumber)
            and not isinstance(value.value, bool)
            and math.isfinite(value.value)
            and value.value == int(value.value)
            and minimum <= value.value <= maximum
        ):
            return int(value.value)
    except (TypeError, ValueError, OverflowError):
        pass
    raise PdfValidationException(f"{context} must be an integer from {minimum} to {maximum}")


def _options(reader: _SchemaReader, attributes: dict) -> list[dict[str, str]]:
    result = []
    for raw in reader.array(attributes.get("Opt"), "form schema choice options"):
        raw = reader.resolve(raw)
        if isinstance(raw, PdfArray):
            pair = reader.array(raw, "form schema choice option")
            if len(pair) != 2:
                raise PdfValidationException("A form schema choice option must have two strings")
            value = reader.required_text(pair[0], "Choice export value")
            label = reader.required_text(pair[1], "Choice display text")
        else:
            value = label = reader.required_text(raw, "Choice option")
        result.append({"export_value": value, "display_text": label})
    return result


def _on_states(reader: _SchemaReader, field: PdfDictionary) -> list[str]:
    widgets = []
    if reader.resolve(field.mapping.get(PdfName("Subtype"))) == PdfName("Widget"):
        widgets.append(field)
    for ref in reader.array(field.mapping.get(PdfName("Kids")), "form schema widgets"):
        widget = reader.resolve(ref)
        if isinstance(widget, PdfDictionary) and reader.resolve(
            widget.mapping.get(PdfName("Subtype"))
        ) == PdfName("Widget"):
            widgets.append(widget)
    reader.budget.check(len(widgets), "max_container_items", "form schema widgets")
    states = []
    for widget in widgets:
        appearance = reader.resolve(widget.mapping.get(PdfName("AP")))
        if not isinstance(appearance, PdfDictionary):
            continue
        normal = reader.resolve(appearance.mapping.get(PdfName("N")))
        if not isinstance(normal, PdfDictionary) or isinstance(normal, PdfStream):
            normal = reader.resolve(appearance.mapping.get(PdfName("D")))
        if not isinstance(normal, PdfDictionary) or isinstance(normal, PdfStream):
            continue
        reader.budget.check(len(normal.mapping), "max_container_items", "form schema button states")
        for key in normal.mapping:
            text = reader.required_text(key, "Button on-state")
            if text != "Off":
                states.append(text)
                break
    return states


def _default(reader: _SchemaReader, raw: Any, kind: str, multiple: bool) -> Any:
    if kind in ("signature", "pushbutton"):
        return None
    if raw is None or isinstance(raw, PdfNull):
        return [] if multiple else False if kind == "checkbox" else None
    if isinstance(raw, PdfArray):
        if not multiple:
            raise PdfValidationException("Only multiselect fields may have array defaults")
        return [reader.required_text(item, "Form default value") for item in reader.array(
            raw, "form schema default selections",
        )]
    text = reader.required_text(raw, "Form default value")
    if multiple:
        return [text]
    if kind == "checkbox":
        return text not in ("", "Off") if isinstance(raw, PdfName) else text not in (
            "", "Off", "0", "false", "No",
        )
    return None if kind == "radio" and text == "Off" else text


def schema(pdf: Any) -> dict[str, Any]:
    """Describe terminal fields in tree order using effective inherited rules."""
    reader = _SchemaReader(pdf)
    result = []
    for name, (field, attributes, kind) in _field_index(reader, strict=True).items():
        ft = attributes.get("FT")
        if ft not in (PdfName("Tx"), PdfName("Btn"), PdfName("Ch"), PdfName("Sig")):
            raise UnsupportedFeatureException("Cannot describe an unsupported AcroForm field type")
        raw_flags = attributes.get("Ff")
        flags = 0 if raw_flags is None or isinstance(raw_flags, PdfNull) else _integer(
            raw_flags, "Field flags", 2**32 - 1,
        )
        multiple = kind == "listbox" and bool(flags & (1 << 21))
        raw_length = attributes.get("MaxLen") if kind == "text" else None
        limit = None if raw_length is None or isinstance(raw_length, PdfNull) else _integer(
            raw_length, "Text maximum length", 2**31 - 1, 1,
        )
        result.append({
            "name": name,
            "partial_name": reader.optional_text(field, "T"),
            "alternate_name": reader.optional_text(field, "TU"),
            "mapping_name": reader.optional_text(field, "TM"),
            "field_type": kind,
            "flags": flags,
            "read_only": bool(flags & 1),
            "required": bool(flags & 2),
            "no_export": bool(flags & 4),
            "default_value": _default(reader, attributes.get("DV"), kind, multiple),
            "max_length": limit,
            "multiline": kind == "text" and bool(flags & (1 << 12)),
            "password": kind == "text" and bool(flags & (1 << 13)),
            "comb": kind == "text" and bool(flags & (1 << 24)),
            "options": _options(reader, attributes) if kind in ("listbox", "combobox") else [],
            "export_values": _on_states(reader, field) if kind in ("checkbox", "radio") else [],
            "multi_select": multiple,
            "editable": kind == "combobox" and bool(flags & (1 << 18)),
        })
        reader.budget.check(len(result), "max_container_items", "form schema fields")
    return {"schema_version": 1, "fields": result}


def to_json(data: dict[str, Any], budget: _LoadBudget) -> bytes:
    """Encode Unicode and escaped strings in bounded pieces before writing."""
    with io.BytesIO() as output:
        def write(part: bytes) -> None:
            budget.check(output.tell() + len(part), "max_codec_work_bytes", "form schema JSON bytes")
            output.write(part)

        def value(raw: Any) -> None:
            if isinstance(raw, str):
                write(b'"')
                for offset in range(0, len(raw), 4096):
                    part = json.dumps(raw[offset:offset + 4096], ensure_ascii=False)[1:-1]
                    write(part.encode("utf-8"))
                write(b'"')
            elif isinstance(raw, (list, dict)):
                write(b"{" if isinstance(raw, dict) else b"[")
                for index, item in enumerate(raw):
                    if index:
                        write(b",")
                    value(item)
                    if isinstance(raw, dict):
                        write(b":")
                        value(raw[item])
                write(b"}" if isinstance(raw, dict) else b"]")
            else:
                write(json.dumps(raw, allow_nan=False).encode("ascii"))

        try:
            value(data)
            write(b"\n")
        except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
            raise PdfValidationException("Form schema cannot be encoded as UTF-8 JSON") from exc
        return output.getvalue()
