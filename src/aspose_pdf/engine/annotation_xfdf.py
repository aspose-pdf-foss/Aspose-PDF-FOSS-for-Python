"""Export a bounded XFDF subset for portable review annotation exchange."""

from __future__ import annotations

import io
import math

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import _LoadBudget

from .annotation_json import _pages, _subtypes, records

_TYPES = frozenset(
    "Text FreeText Line Square Circle Polygon PolyLine Highlight Underline "
    "Squiggly StrikeOut Stamp Caret Ink Popup Redact".split()
)
_PROPERTIES = tuple(
    "BS C CA CreationDate DA DS F IC InkList L LE M NM Name Open Q QuadPoints "
    "RD RT Rotate State StateModel Subj Vertices".split()
)
_FLAGS = (
    "invisible",
    "hidden",
    "print",
    "nozoom",
    "norotate",
    "noview",
    "readonly",
    "locked",
    "togglenoview",
    "lockedcontents",
)


def _fail(message):
    raise PdfValidationException(f"Invalid annotation XFDF data: {message}")


def _number(value, label, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"{label} must be a finite number")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if (
        not finite
        or (minimum is not None and value < minimum)
        or (maximum is not None and value > maximum)
    ):
        _fail(f"{label} is out of range")
    text = str(value)
    return text[:-2] if text.endswith(".0") else text


def _array(value, label, *, minimum=0, multiple=1, length=None):
    if (
        not isinstance(value, list)
        or len(value) < minimum
        or len(value) % multiple
        or (length is not None and len(value) != length)
    ):
        _fail(f"{label} has an invalid coordinate array")
    return [_number(item, label) for item in value]


def _points(value, label, minimum):
    coordinates = _array(value, label, minimum=minimum, multiple=2)
    return ";".join(
        ",".join(coordinates[index : index + 2])
        for index in range(0, len(coordinates), 2)
    )


def _color(value, label):
    _array(value, label)
    if len(value) not in (0, 1, 3, 4):
        _fail(f"{label} must contain 0, 1, 3 or 4 color components")
    for component in value:
        _number(component, label, 0, 1)
    if not value:
        return None
    if len(value) == 1:
        value = value * 3
    elif len(value) == 4:
        c, m, y, k = value
        value = [(1 - component) * (1 - k) for component in (c, m, y)]
    return "#" + "".join(f"{round(component * 255):02X}" for component in value)


class _Writer:
    """Escape XML 1.0 text in small chunks without a global namespace registry."""

    def __init__(self, budget):
        self.budget = budget
        self.output = io.BytesIO()
        self.nodes = self.depth = 0

    def write(self, value):
        size = self.output.tell() + len(value)
        for limit in (
            "max_input_bytes",
            "max_total_decoded_bytes",
            "max_codec_work_bytes",
        ):
            self.budget.check(size, limit, "annotation XFDF output bytes")
        self.output.write(value)

    def text(self, value, *, attribute=False):
        if not isinstance(value, str):
            _fail("text properties must be strings")
        self.budget.check(
            len(value), "max_object_bytes", "annotation XFDF string bytes"
        )
        size = 0
        for offset in range(0, len(value), 4096):
            chunk = value[offset : offset + 4096]
            for char in chunk:
                code = ord(char)
                if not (
                    code in (9, 10, 13)
                    or 0x20 <= code <= 0xD7FF
                    or 0xE000 <= code <= 0xFFFD
                    or 0x10000 <= code <= 0x10FFFF
                ):
                    _fail("text contains a character forbidden in XML 1.0")
            size += len(chunk.encode("utf-8"))
            self.budget.check(size, "max_object_bytes", "annotation XFDF string bytes")
            chunk = (
                chunk.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("\r", "&#13;")
            )
            if attribute:
                chunk = (
                    chunk.replace('"', "&quot;")
                    .replace("\n", "&#10;")
                    .replace("\t", "&#9;")
                )
            self.write(chunk.encode("utf-8"))

    def start(self, tag, attributes=None):
        attributes = attributes or {}
        self.nodes += 1 + len(attributes)
        self.budget.check(
            self.nodes, "max_container_items", "annotation XFDF XML items"
        )
        self.budget.check(self.depth, "max_nesting_depth", "annotation XFDF XML depth")
        self.write(b"<" + tag.encode("ascii"))
        for name, value in attributes.items():
            self.write(b" " + name.encode("ascii") + b'="')
            self.text(value, attribute=True)
            self.write(b'"')
        self.write(b">")
        self.depth += 1

    def end(self, tag):
        self.depth -= 1
        self.write(b"</" + tag.encode("ascii") + b">")

    def element(self, tag, value):
        self.start(tag)
        self.text(value)
        self.end(tag)


def _names(rows):
    """Keep usable source names, reserving them before generating fallback IDs."""
    reserved = set()
    for row in rows:
        name = row["properties"].get("NM")
        if name is not None and not isinstance(name, str):
            _fail("NM must be a string")
        if name:
            reserved.add(name)
    names = {}
    used = set()
    for row in rows:
        identity = row["id"]
        if identity in names:
            _fail("an annotation object occurs more than once in the selection")
        name = row["properties"].get("NM")
        if not name or name in used:
            base = "xfdf-" + identity
            name = base
            suffix = 1
            while name in used or name in reserved:
                suffix += 1
                name = f"{base}-{suffix}"
        names[identity] = name
        used.add(name)
    return names


def _popup_owners(rows):
    """Validate selected reply graphs and resolve both popup link directions."""
    by_id = {row["id"]: row for row in rows}
    replies = {}
    for row in rows:
        target = row["links"]["in_reply_to"]
        if target in by_id and (
            row["subtype"] == "Popup" or by_id[target]["subtype"] == "Popup"
        ):
            _fail("reply links must connect markup annotations")
        replies[row["id"]] = target if target in by_id else None
    checked = set()
    for identity in replies:
        active = set()
        current = identity
        while current is not None and current not in checked:
            if current in active:
                _fail("reply links contain a cycle")
            active.add(current)
            current = replies[current]
        checked.update(active)
    owners = {}
    for row in rows:
        if row["subtype"] == "Popup":
            owner, popup = row["links"]["parent"], row["id"]
        else:
            owner, popup = row["id"], row["links"]["popup"]
        if owner not in by_id or popup not in by_id:
            continue
        if by_id[owner]["subtype"] == "Popup" or by_id[popup]["subtype"] != "Popup":
            _fail("popup links must connect a markup annotation to a popup")
        if by_id[owner]["page_index"] != by_id[popup]["page_index"]:
            _fail("a popup and its parent must be on the same page")
        if popup in owners and owners[popup] != owner:
            _fail("a popup has conflicting parents")
        owners[popup] = owner
    grouped = {}
    for popup, owner in owners.items():
        if owner in grouped:
            _fail("an annotation has more than one popup")
        grouped[owner] = by_id[popup]
    return grouped


def _attributes(row, names):
    props = row["properties"]
    attributes = dict(
        page=str(row["page_index"]),
        rect=",".join(_array(row["rect"], "Rect", length=4)),
        name=names[row["id"]],
    )
    if row["author"]:
        attributes["title"] = row["author"]
    for source, target in (
        ("M", "date"),
        ("CreationDate", "creationdate"),
        ("Subj", "subject"),
    ):
        if props.get(source) is not None:
            attributes[target] = props[source]
    for source, target in (("C", "color"), ("IC", "interior-color")):
        if props.get(source) is not None:
            color = _color(props[source], source)
            if color is not None:
                attributes[target] = color
    if props.get("CA") is not None:
        attributes["opacity"] = _number(props["CA"], "CA", 0, 1)
    if props.get("F") is not None:
        flags = props["F"]
        if type(flags) is not int or not 0 <= flags < 1024:
            _fail("F must be an integer with supported annotation flag bits")
        attributes["flags"] = ",".join(
            name for index, name in enumerate(_FLAGS) if flags & (1 << index)
        )
    if props.get("Open") is not None:
        if type(props["Open"]) is not bool:
            _fail("Open must be a boolean")
        attributes["open"] = "yes" if props["Open"] else "no"
    if props.get("BS") is not None:
        border = props["BS"]
        if not isinstance(border, dict):
            _fail("BS must be a dictionary")
        if border.get("W") is not None:
            attributes["width"] = _number(border["W"], "BS.W", 0)
        if border.get("S") is not None:
            styles = dict(S="solid", D="dash", B="bevelled", I="inset", U="underline")
            if not isinstance(border["S"], str) or border["S"] not in styles:
                _fail("BS.S is not a supported border style")
            attributes["style"] = styles[border["S"]]
        if border.get("D") is not None:
            attributes["dashes"] = ",".join(_array(border["D"], "BS.D"))
            if any(value < 0 for value in border["D"]):
                _fail("BS.D must contain non-negative dash lengths")
            if border["D"] and not any(border["D"]):
                _fail("BS.D must not contain only zero dash lengths")
    if props.get("RD") is not None:
        attributes["fringe"] = ",".join(_array(props["RD"], "RD", length=4))
    subtype = row["subtype"]
    if subtype in ("Text", "Stamp") and props.get("Name") is not None:
        attributes["icon"] = props["Name"]
    if subtype == "Text":
        for source, target in (("State", "state"), ("StateModel", "statemodel")):
            if props.get(source) is not None:
                attributes[target] = props[source]
    target = row["links"]["in_reply_to"]
    if target in names:
        attributes["inreplyto"] = names[target]
        if props.get("RT") is not None:
            if props["RT"] not in ("Reply", "Group"):
                _fail("RT must be Reply or Group")
            attributes["replyType"] = props["RT"]
    if subtype in ("FreeText", "Stamp") and props.get("Rotate") is not None:
        attributes["rotation"] = _number(props["Rotate"], "Rotate")
    if subtype == "FreeText" and props.get("Q") is not None:
        if type(props["Q"]) is not int or props["Q"] not in (0, 1, 2):
            _fail("Q must be an integer from 0 to 2")
        attributes["justification"] = ("left", "centered", "right")[props["Q"]]
    if subtype == "Line":
        line = _array(props.get("L"), "L", length=4)
        attributes["start"], attributes["end"] = ",".join(line[:2]), ",".join(line[2:])
        if props.get("LE") is not None:
            endings = props["LE"]
            if (
                not isinstance(endings, list)
                or len(endings) != 2
                or not all(
                    isinstance(value, str)
                    and value
                    in {
                        "None",
                        "Square",
                        "Circle",
                        "Diamond",
                        "OpenArrow",
                        "ClosedArrow",
                        "Butt",
                        "ROpenArrow",
                        "RClosedArrow",
                        "Slash",
                    }
                    for value in endings
                )
            ):
                _fail("LE must contain two line-ending names")
            attributes["head"], attributes["tail"] = endings
    if subtype in ("Highlight", "Underline", "Squiggly", "StrikeOut") or (
        subtype == "Redact" and props.get("QuadPoints") is not None
    ):
        attributes["coords"] = ",".join(
            _array(props.get("QuadPoints"), "QuadPoints", minimum=8, multiple=8)
        )
    return attributes


def _annotation(writer, row, names, popup=None):
    tag = row["subtype"].lower()
    writer.start(tag, _attributes(row, names))
    if row["subtype"] != "Popup":
        writer.element("contents", row["contents"])
    props = row["properties"]
    if row["subtype"] == "FreeText":
        for source, child in (("DA", "defaultappearance"), ("DS", "defaultstyle")):
            if props.get(source) is not None:
                writer.element(child, props[source])
    if row["subtype"] in ("Polygon", "PolyLine"):
        writer.element(
            "vertices",
            _points(props.get("Vertices"), "Vertices", 6 if tag == "polygon" else 4),
        )
    if row["subtype"] == "Ink":
        strokes = props.get("InkList")
        if not isinstance(strokes, list) or not strokes:
            _fail("InkList must contain at least one stroke")
        writer.start("inklist")
        for stroke in strokes:
            writer.element("gesture", _points(stroke, "InkList stroke", 2))
        writer.end("inklist")
    if popup is not None:
        _annotation(writer, popup, names)
    writer.end(tag)


def export(document, pages, subtypes):
    """Snapshot supported review annotations without reading any page streams."""
    budget = _LoadBudget(document.load_limits)
    selected = _pages(pages, document.page_count, budget)
    wanted = _subtypes(subtypes, budget)
    wanted = _TYPES if wanted is None else wanted & _TYPES
    rows = records(document._engine_pdf, selected, wanted, property_names=_PROPERTIES)
    names = _names(rows)
    popups = _popup_owners(rows)
    emitted = {row["id"] for row in rows if row["subtype"] != "Popup"}
    emitted.update(popup["id"] for popup in popups.values())
    names = {identity: name for identity, name in names.items() if identity in emitted}
    writer = _Writer(budget)
    try:
        writer.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
        writer.start(
            "xfdf", {"xmlns": "http://ns.adobe.com/xfdf/", "xml:space": "preserve"}
        )
        writer.start("annots")
        for row in rows:
            if row["subtype"] != "Popup":
                _annotation(writer, row, names, popups.get(row["id"]))
        writer.end("annots")
        writer.end("xfdf")
        writer.write(b"\n")
        return writer.output.getvalue()
    finally:
        writer.output.close()
