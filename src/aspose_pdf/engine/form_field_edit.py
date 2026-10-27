"""Validated edits to AcroForm field configuration and defaults."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from aspose_pdf.exceptions import PdfValidationException, UnsupportedFeatureException

from .cos import PdfArray, PdfDictionary, PdfName, PdfNull, PdfNumber, PdfString
from .form_fields import (
    alignment_value,
    field_attribute,
    field_quadding,
    form_value,
    on_states,
    validate_choice_value,
    widget_dictionaries,
)

_FLAGS = {
    "multiline": ("text", 1 << 12),
    "password": ("text", 1 << 13),
    "comb": ("text", 1 << 24),
    "multi_select": ("listbox", 1 << 21),
    "editable": ("combobox", 1 << 18),
}
_COMB_CONFLICTS = (1 << 12) | (1 << 13) | (1 << 20) | (1 << 25)


def _check_text(pdf: Any, value: Any, *, size: int = 0) -> int:
    """Check text encoding and authored metadata size before changing the field."""
    texts = value if isinstance(value, list) else [value]
    for text in texts:
        if not isinstance(text, str):
            continue
        size += 2
        for character in text:
            code = ord(character)
            if 0xD800 <= code <= 0xDFFF:
                raise PdfValidationException(
                    "Field configuration contains invalid Unicode text"
                )
            size += 4 if code > 0xFFFF else 2
            pdf._load_budget.check(size, "max_object_bytes", "field configuration text")
        pdf._load_budget.check(size, "max_object_bytes", "field configuration text")
    return size


def _options(pdf: Any, values: Any) -> tuple[PdfArray, list[str]]:
    if isinstance(values, (str, bytes, bytearray, Mapping)) or not isinstance(
        values, Iterable
    ):
        raise TypeError(
            "options must be an iterable of strings or export/display pairs"
        )
    result = []
    exports: list[str] = []
    text_size = 0
    seen: set[str] = set()
    for index, value in enumerate(values, 1):
        pdf._load_budget.check(index, "max_container_items", "choice options")
        if isinstance(value, str):
            export, label = value, value
            text_size = _check_text(pdf, value, size=text_size)
            encoded = PdfString(value)
        elif (
            isinstance(value, (list, tuple))
            and len(value) == 2
            and all(isinstance(v, str) for v in value)
        ):
            pdf._load_budget.check(2, "max_container_items", "choice option pair")
            export, label = value
            text_size = _check_text(pdf, [export, label], size=text_size)
            encoded = PdfArray([PdfString(export), PdfString(label)])
        else:
            raise PdfValidationException(
                "Choice options must be strings or (export, display) pairs"
            )
        if export in seen:
            raise PdfValidationException("Choice option export values must be unique")
        seen.add(export)
        exports.append(export)
        result.append(encoded)
    if not result:
        raise PdfValidationException("Choice field requires at least one option")
    return PdfArray(result), exports


def _choice_value(
    pdf: Any, field: PdfDictionary, kind: str, key: str, flags: int, exports: list[str]
) -> Any:
    value = form_value(
        pdf._resolve, pdf._load_budget, field_attribute(pdf, field, key), kind
    )
    multiple = kind == "listbox" and bool(flags & (1 << 21))
    if multiple:
        value = value if isinstance(value, list) else ([] if value is None else [value])
    elif isinstance(value, list):
        if len(value) > 1:
            raise PdfValidationException(
                "Cannot disable multiple selection while current or default values select several options"
            )
        value = value[0] if value else None
    return validate_choice_value(
        value,
        exports,
        combo=kind == "combobox",
        editable=bool(flags & (1 << 18)),
        multiselect=multiple,
    )


def _check_text_appearance_font(pdf: Any, field: PdfDictionary) -> None:
    """Refuse layout edits that the existing CID appearance path cannot rebuild."""
    from .field_appearance import parse_default_appearance

    root = pdf._resolve(pdf._cos_doc.trailer.mapping.get(PdfName("Root")))
    acro = (
        pdf._resolve(root.mapping.get(PdfName("AcroForm")))
        if isinstance(root, PdfDictionary)
        else None
    )
    if not isinstance(acro, PdfDictionary):
        return
    da = field_attribute(pdf, field, "DA") or pdf._resolve(
        acro.mapping.get(PdfName("DA"))
    )
    font_name, _, _ = parse_default_appearance(pdf._get_cos_string(da))
    resources = pdf._resolve(acro.mapping.get(PdfName("DR")))
    fonts = (
        pdf._resolve(resources.mapping.get(PdfName("Font")))
        if isinstance(resources, PdfDictionary)
        else None
    )
    font = (
        pdf._resolve(fonts.mapping.get(PdfName(font_name or "Helv")))
        if isinstance(fonts, PdfDictionary)
        else None
    )
    if isinstance(font, PdfDictionary) and pdf._resolve(
        font.mapping.get(PdfName("Subtype"))
    ) == PdfName("Type0"):
        raise UnsupportedFeatureException(
            "Layout changes cannot rebuild an embedded CID field appearance"
        )


def set_parameter(
    pdf: Any, field: PdfDictionary, kind: str, parameter: str, value: Any
) -> None:
    """Validate the complete resulting configuration before writing local overrides."""
    changes: dict[PdfName, Any] = {}
    flags_obj = field_attribute(pdf, field, "Ff")
    flags = int(flags_obj.value) if isinstance(flags_obj, PdfNumber) else 0
    if parameter in _FLAGS:
        expected, mask = _FLAGS[parameter]
        if kind != expected:
            raise PdfValidationException(
                f"{parameter} applies only to {expected} fields"
            )
        if not isinstance(value, bool):
            raise TypeError(f"{parameter} must be a boolean")
        flags = flags | mask if value else flags & ~mask
        changes[PdfName("Ff")] = PdfNumber(flags)
    elif parameter == "alignment":
        if kind not in ("text", "listbox", "combobox"):
            raise PdfValidationException("alignment applies only to text and choice fields")
        changes[PdfName("Q")] = (
            None if value is None else PdfNumber(alignment_value(value))
        )
    elif parameter == "max_length":
        if kind != "text":
            raise PdfValidationException("max_length applies only to text fields")
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError("max_length must be an integer or None")
            if not 1 <= value <= 2**31 - 1:
                raise PdfValidationException("max_length must be from 1 to 2147483647")
        changes[PdfName("MaxLen")] = None if value is None else PdfNumber(value)
    elif parameter == "options":
        if kind not in ("listbox", "combobox"):
            raise PdfValidationException("options applies only to choice fields")
        options, exports = _options(pdf, value)
        changes[PdfName("Opt")] = options
    elif parameter == "default_value":
        if kind in ("signature", "pushbutton"):
            raise PdfValidationException(
                "This field type does not support a default_value"
            )
        if kind == "text" and value is not None and not isinstance(value, str):
            raise TypeError("Text default_value must be a string or None")
        if kind == "checkbox" and value is not None and not isinstance(value, bool):
            raise TypeError("Checkbox default_value must be a boolean or None")
        if kind == "radio":
            if value is not None and not isinstance(value, str):
                raise TypeError("Radio default_value must be a string or None")
            if value not in (None, "Off") and value not in on_states(pdf, field):
                raise PdfValidationException(
                    "Radio default_value is not one of the on states"
                )
        if kind in ("listbox", "combobox"):
            if kind == "listbox" and flags & (1 << 21) and value is not None:
                if isinstance(value, (str, bytes, bytearray)) or not isinstance(
                    value, Sequence
                ):
                    raise TypeError(
                        "A multiselect default_value must be a sequence of strings"
                    )
                pdf._load_budget.check(
                    len(value), "max_container_items", "default choice selections"
                )
            exports = [
                export
                for export, _ in pdf._choice_options_for_appearance(
                    field_attribute(pdf, field, "Opt")
                )
            ]
            value = validate_choice_value(
                value,
                exports,
                combo=kind == "combobox",
                editable=bool(flags & (1 << 18)),
                multiselect=bool(flags & (1 << 21)),
            )
        _check_text(pdf, list(value) if isinstance(value, tuple) else value)
        changes[PdfName("DV")] = (
            PdfNull() if value is None else pdf._value_to_pdf(field, value)
        )
    else:
        raise ValueError(f"Unknown field parameter {parameter!r}")

    if kind == "text" and flags & (1 << 24) and parameter in (*_FLAGS, "max_length"):
        if flags & _COMB_CONFLICTS:
            raise PdfValidationException(
                "comb requires plain text without multiline, password or file selection"
            )
        limit = changes.get(PdfName("MaxLen"), field_attribute(pdf, field, "MaxLen"))
        if parameter == "max_length" and value is None:
            parent = pdf._resolve(field.mapping.get(PdfName("Parent")))
            limit = (
                field_attribute(pdf, parent, "MaxLen")
                if isinstance(parent, PdfDictionary)
                else None
            )
        if (
            not isinstance(limit, PdfNumber)
            or not 1 <= limit.value <= 2**31 - 1
            or limit.value != int(limit.value)
        ):
            raise PdfValidationException("comb requires a positive max_length")

    if kind in ("listbox", "combobox") and parameter in (
        "options",
        "multi_select",
        "editable",
    ):
        if parameter != "options":
            exports = [
                export
                for export, _ in pdf._choice_options_for_appearance(
                    field_attribute(pdf, field, "Opt")
                )
            ]
        current = _choice_value(pdf, field, kind, "V", flags, exports)
        _choice_value(pdf, field, kind, "DV", flags, exports)
        staged = PdfDictionary(dict(field.mapping))
        staged.mapping.update(changes)
        changes[PdfName("V")] = pdf._value_to_pdf(staged, current) or PdfNull()
        if parameter == "multi_select":
            default = _choice_value(pdf, field, kind, "DV", flags, exports)
            changes[PdfName("DV")] = pdf._value_to_pdf(staged, default) or PdfNull()

    text_mode_changed = parameter in ("multiline", "password", "comb") and changes[
        PdfName("Ff")
    ].value != (int(flags_obj.value) if isinstance(flags_obj, PdfNumber) else 0)
    if text_mode_changed or (parameter == "max_length" and flags & (1 << 24)):
        _check_text_appearance_font(pdf, field)

    widgets = []
    if parameter == "alignment":
        widgets = [
            widget
            for widget in widget_dictionaries(pdf, field)
            if widget is not field and PdfName("T") not in widget.mapping
        ]
        staged = PdfDictionary(dict(field.mapping))
        setting = changes[PdfName("Q")]
        if setting is None:
            staged.mapping.pop(PdfName("Q"), None)
        else:
            staged.mapping[PdfName("Q")] = setting
        resulting_alignment = field_quadding(pdf, staged)
        for target in widgets or [field]:
            if field_quadding(pdf, target) != resulting_alignment:
                _check_text_appearance_font(pdf, target)

    for key, item in changes.items():
        if item is None:
            field.mapping.pop(key, None)
        else:
            field.mapping[key] = item
    if kind in ("listbox", "combobox") and PdfName("V") in changes:
        pdf._update_choice_indices(field, changes[PdfName("V")])
    for widget in widgets:
        widget.mapping.pop(PdfName("Q"), None)
