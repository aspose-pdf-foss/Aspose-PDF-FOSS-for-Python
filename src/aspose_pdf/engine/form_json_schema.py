"""Describe normalized AcroForm answers with standard JSON Schema keywords."""

from __future__ import annotations

from typing import Any

_DIALECT = "https://json-schema.org/draft/2020-12/schema"
# Match a character outside Python's str.strip() whitespace set, in ECMA-262 syntax.
_NONBLANK = (
    r"[^\u0009-\u000D\u001C-\u0020\u0085\u00A0\u1680"
    r"\u2000-\u200A\u2028\u2029\u202F\u205F\u3000]"
)


def _enum(result: dict[str, Any], choices: list[Any]) -> None:
    """Keep enum entries unique and represent an impossible selection explicitly."""
    if choices:
        result["enum"] = list(dict.fromkeys(choices))
    else:
        result["not"] = {}


def _property(field: dict[str, Any]) -> dict[str, Any]:
    required = field["required"]
    kind = field["field_type"]
    result: dict[str, Any] = {
        "title": field["alternate_name"] or field["name"],
        "readOnly": field["read_only"],
        "default": field["default_value"],
    }
    if kind == "checkbox":
        result["type"] = "boolean"
        if required:
            result["const"] = True
    elif field["multi_select"]:
        choices = list(dict.fromkeys(item["export_value"] for item in field["options"]))
        result.update(type="array", uniqueItems=True)
        result["items"] = {"type": "string", "enum": choices} if choices else False
        if required:
            result["minItems"] = 1
    else:
        result["type"] = "string" if required else ["string", "null"]
        if kind == "text":
            if field["max_length"] is not None:
                result["maxLength"] = field["max_length"]
            if required:
                result.update(minLength=1, pattern=_NONBLANK)
        elif kind == "radio":
            choices = [value for value in field["export_values"] if value not in ("", "Off")]
            if field["export_values"]:
                _enum(result, choices + ([] if required else ["", None]))
            elif required:
                result["minLength"] = 1
                result["not"] = {"const": "Off"}
        else:
            choices = [item["export_value"] for item in field["options"]]
            if field["editable"]:
                if required:
                    nonblank = {"pattern": _NONBLANK}
                    if choices:
                        result["anyOf"] = [{"enum": list(dict.fromkeys(choices))}, nonblank]
                    else:
                        result.update(nonblank)
            else:
                _enum(result, choices + ([] if required else [None]))
    return result


def to_json_schema(inventory: dict[str, Any]) -> dict[str, Any]:
    """Map the bounded template inventory to flat JSON Schema Draft 2020-12."""
    properties = {}
    required = []
    for field in inventory["fields"]:
        if field["field_type"] in ("signature", "pushbutton"):
            continue
        name = field["name"]
        properties[name] = _property(field)
        if field["required"]:
            required.append(name)
    return {
        "$schema": _DIALECT,
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }
