"""Read a bounded XFDF review subset and reuse atomic annotation attachment."""

from __future__ import annotations

import math
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

from aspose_pdf.exceptions import PdfResourceLimitException, PdfValidationException
from aspose_pdf.load_limits import _LoadBudget, _read_limited

from .annotation_import import import_records
from .annotation_xfdf import _FLAGS, _TYPES

_NAMESPACE = "http://ns.adobe.com/xfdf/"
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
_TAGS = {name.lower(): name for name in _TYPES}
_COMMON = frozenset(
    "page rect name title date creationdate subject color interior-color opacity "
    "flags open width style dashes fringe inreplyto replyType".split()
)
_EXTRA = {
    "Text": {"icon", "state", "statemodel"},
    "FreeText": {"rotation", "justification"},
    "Stamp": {"icon", "rotation"},
    "Line": {"start", "end", "head", "tail"},
    **dict.fromkeys(
        ("Highlight", "Underline", "Squiggly", "StrikeOut", "Redact"),
        frozenset({"coords"}),
    ),
}
_STYLES = dict(solid="S", dash="D", bevelled="B", inset="I", underline="U")
_ENDINGS = frozenset(
    "None Square Circle Diamond OpenArrow ClosedArrow Butt ROpenArrow RClosedArrow Slash".split()
)
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")


def _fail(message: str) -> Any:
    raise PdfValidationException(f"Invalid annotation XFDF data: {message}")


def _page(value: str) -> int:
    if not re.fullmatch(r"[0-9]+", value):
        _fail("page must be a non-negative integer")
    try:
        return int(value)
    except ValueError:
        _fail("page index is too large")


def _number(value: str, label: str, *, minimum=None, maximum=None) -> float:
    value = value.strip()
    if not _NUMBER.fullmatch(value):
        _fail(f"{label} must be a finite number")
    number = float(value)
    if (
        not math.isfinite(number)
        or (minimum is not None and number < minimum)
        or (maximum is not None and number > maximum)
    ):
        _fail(f"{label} is out of range")
    return number


@dataclass
class _Element:
    tag: str
    record: dict | None = None
    text: list[str] = field(default_factory=list)
    text_bytes: int = 0
    children: set[str] = field(default_factory=set)


class _Reader:
    def __init__(self, budget: _LoadBudget):
        self.budget = budget
        self.stack: list[_Element] = []
        self.rows: list[dict] = []
        self.names: dict[str, str] = {}
        self.replies: dict[str, str] = {}
        self.namespace: str | None = None
        self.root_seen = self.annots_seen = False
        self.items = self.text_bytes = 0

    def tick(self, count=1):
        self.items += count
        self.budget.check(self.items, "max_container_items", "annotation XFDF items")

    def text_size(self, value: str) -> int:
        size = len(value.encode("utf-8"))
        self.budget.check(size, "max_object_bytes", "annotation XFDF string bytes")
        self.text_bytes += size
        self.budget.check(
            self.text_bytes, "max_total_decoded_bytes", "annotation XFDF text"
        )
        return size

    def numbers(self, value, label, *, length=None, minimum=0, multiple=1):
        count = value.count(",") + 1 if value.strip() else 0
        self.tick(count)
        if (
            count < minimum
            or count % multiple
            or (length is not None and count != length)
        ):
            _fail(f"{label} has incomplete coordinates")
        return [_number(item, label) for item in value.split(",")] if count else []

    def points(self, value, label, minimum):
        self.tick(value.count(";") + 1)
        result = []
        for point in value.split(";"):
            result.extend(self.numbers(point, label, length=2))
        if len(result) < minimum:
            _fail(f"{label} has incomplete coordinates")
        return result

    def annotation(self, tag, attrs, parent=None):
        subtype = _TAGS[tag]
        allowed = _COMMON | _EXTRA.get(subtype, set())
        if attrs.keys() - allowed:
            _fail("unsupported annotation attribute")
        if "rect" not in attrs or ("page" not in attrs and parent is None):
            _fail("annotations require page and rect")
        page = _page(attrs["page"]) if "page" in attrs else parent["page_index"]
        if parent is not None and page != parent["page_index"]:
            _fail("a popup and its parent must be on the same source page")
        record = {
            "id": f"xfdf-record-{len(self.rows)}",
            "page_index": page,
            "annotation_index": len(self.rows),
            "subtype": subtype,
            "rect": self.numbers(attrs["rect"], "rect", length=4),
            "contents": "",
            "author": attrs.get("title", ""),
            "properties": {},
            "links": {"in_reply_to": None, "popup": None, "parent": None},
        }
        props = record["properties"]
        if "name" in attrs:
            name = attrs["name"]
            if not name or name in self.names:
                _fail("annotation names must be non-empty and unique")
            self.names[name] = record["id"]
            props["NM"] = name
        for source, target in (
            ("date", "M"),
            ("creationdate", "CreationDate"),
            ("subject", "Subj"),
            ("icon", "Name"),
            ("state", "State"),
            ("statemodel", "StateModel"),
        ):
            if source in attrs:
                props[target] = attrs[source]
        for source, target in (("color", "C"), ("interior-color", "IC")):
            if source in attrs:
                color = attrs[source]
                if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
                    _fail("colors must be #RRGGBB values")
                props[target] = [
                    int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)
                ]
        if "opacity" in attrs:
            props["CA"] = _number(attrs["opacity"], "opacity", minimum=0, maximum=1)
        if "open" in attrs:
            if attrs["open"] not in ("yes", "no"):
                _fail("open must be yes or no")
            props["Open"] = attrs["open"] == "yes"
        if "flags" in attrs:
            self.tick(attrs["flags"].count(",") + 1)
            flags = (
                [item.strip() for item in attrs["flags"].split(",")]
                if attrs["flags"]
                else []
            )
            if len(set(flags)) != len(flags) or set(flags) - set(_FLAGS):
                _fail("flags contain unknown or repeated names")
            props["F"] = sum(1 << _FLAGS.index(flag) for flag in flags)
        border = {}
        if "width" in attrs:
            border["W"] = _number(attrs["width"], "width", minimum=0)
        if "style" in attrs:
            if attrs["style"] not in _STYLES:
                _fail("unsupported border style")
            border["S"] = _STYLES[attrs["style"]]
        if "dashes" in attrs:
            border["D"] = self.numbers(attrs["dashes"], "dashes")
        if border:
            props["BS"] = border
        if "fringe" in attrs:
            props["RD"] = self.numbers(attrs["fringe"], "fringe", length=4)
        if "inreplyto" in attrs:
            if not attrs["inreplyto"] or subtype == "Popup":
                _fail("inreplyto must name a markup annotation")
            self.replies[record["id"]] = attrs["inreplyto"]
        if "replyType" in attrs:
            if "inreplyto" not in attrs or attrs["replyType"] not in ("Reply", "Group"):
                _fail("replyType requires inreplyto and must be Reply or Group")
            props["RT"] = attrs["replyType"]
        if "rotation" in attrs:
            props["Rotate"] = _number(attrs["rotation"], "rotation")
        if "justification" in attrs:
            positions = {"left": 0, "centered": 1, "right": 2}
            if attrs["justification"] not in positions:
                _fail("unsupported FreeText justification")
            props["Q"] = positions[attrs["justification"]]
        if subtype == "Line":
            if "start" not in attrs or "end" not in attrs:
                _fail("Line requires start and end")
            props["L"] = self.numbers(attrs["start"], "start", length=2) + self.numbers(
                attrs["end"], "end", length=2
            )
            if "head" in attrs or "tail" in attrs:
                endings = [attrs.get("head", "None"), attrs.get("tail", "None")]
                if set(endings) - _ENDINGS:
                    _fail("unsupported line ending")
                props["LE"] = endings
        if "coords" in attrs:
            props["QuadPoints"] = self.numbers(
                attrs["coords"], "coords", minimum=8, multiple=8
            )
        if parent is not None:
            if parent["links"]["popup"] is not None:
                _fail("an annotation cannot have multiple popups")
            parent["links"]["popup"] = record["id"]
            record["links"]["parent"] = parent["id"]
        self.rows.append(record)
        return record

    def start(self, tag, attributes):
        self.tick(1 + len(attributes))
        self.budget.check(
            len(self.stack) + 1, "max_nesting_depth", "annotation XFDF XML depth"
        )
        for value in attributes.values():
            self.text_size(value)
        if tag.startswith("{"):
            namespace, _, local = tag[1:].partition("}")
        else:
            namespace, local = "", tag
        if self.namespace is not None and namespace != self.namespace:
            _fail("XML elements must use the XFDF namespace consistently")
        if not self.stack:
            if self.root_seen or local != "xfdf" or namespace not in ("", _NAMESPACE):
                _fail("expected an xfdf root in the XFDF namespace")
            if attributes.keys() - {_XML_SPACE} or attributes.get(
                _XML_SPACE, "preserve"
            ) not in ("preserve", "default"):
                _fail("unsupported xfdf root attributes")
            self.namespace, self.root_seen = namespace, True
            self.stack.append(_Element(local))
            return
        parent = self.stack[-1]
        record = None
        if parent.tag == "xfdf":
            allowed = {"annots": set(), "f": {"href"}, "ids": {"original", "modified"}}
            if (
                local not in allowed
                or attributes.keys() - allowed[local]
                or local in parent.children
            ):
                _fail("unsupported or repeated XFDF container")
            parent.children.add(local)
            if local == "annots":
                self.annots_seen = True
        elif parent.tag == "annots":
            if local not in _TAGS or local == "popup":
                _fail("unsupported annotation subtype or orphan popup")
            record = self.annotation(local, attributes)
        elif (
            parent.record is not None and parent.tag in _TAGS and parent.tag != "popup"
        ):
            subtype = parent.record["subtype"]
            allowed = {"contents", "popup"}
            if subtype == "FreeText":
                allowed.update(("defaultappearance", "defaultstyle"))
            elif subtype in ("Polygon", "PolyLine"):
                allowed.add("vertices")
            elif subtype == "Ink":
                allowed.add("inklist")
            if local not in allowed or local in parent.children:
                _fail("unsupported or repeated annotation child element")
            parent.children.add(local)
            if local == "popup":
                record = self.annotation(local, attributes, parent.record)
            else:
                if attributes:
                    _fail("annotation value elements cannot have attributes")
                record = parent.record
                if local == "inklist":
                    record["properties"]["InkList"] = []
        elif parent.tag == "inklist" and local == "gesture" and not attributes:
            record = parent.record
        else:
            _fail("unsupported nested XML element")
        self.stack.append(_Element(local, record))

    def data(self, text):
        size = self.text_size(text)
        if self.stack and self.stack[-1].tag in (
            "contents",
            "defaultappearance",
            "defaultstyle",
            "vertices",
            "gesture",
        ):
            element = self.stack[-1]
            element.text_bytes += size
            self.budget.check(
                element.text_bytes, "max_object_bytes", "annotation XFDF string bytes"
            )
            self.tick()
            element.text.append(text)
        elif text.strip():
            _fail("unexpected text outside an annotation value")

    def end(self, tag):
        element = self.stack.pop()
        if element.tag in (
            "contents",
            "defaultappearance",
            "defaultstyle",
            "vertices",
            "gesture",
        ):
            value = "".join(element.text)
            record = element.record
            if element.tag == "contents":
                record["contents"] = value
            elif element.tag in ("defaultappearance", "defaultstyle"):
                record["properties"][
                    "DA" if element.tag == "defaultappearance" else "DS"
                ] = value
            elif element.tag == "vertices":
                record["properties"]["Vertices"] = self.points(
                    value, "vertices", 6 if record["subtype"] == "Polygon" else 4
                )
            else:
                record["properties"]["InkList"].append(self.points(value, "gesture", 2))

    def doctype(self, name, pubid, system):
        _fail("DOCTYPE and entity declarations are not accepted")

    def close(self):
        if not self.root_seen or not self.annots_seen:
            _fail("an xfdf root and annots container are required")
        for record in self.rows:
            target = self.replies.get(record["id"])
            record["links"]["in_reply_to"] = self.names.get(target)
        return self.rows


def import_xfdf(pdf, source, *, page_map=None, target_page=None):
    """Parse all XML and validate review records before changing the PDF."""
    budget = _LoadBudget(pdf._load_limits)
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
    parser = ET.XMLParser(target=_Reader(budget))
    try:
        for offset in range(0, len(data), 64 * 1024):
            parser.feed(data[offset : offset + 64 * 1024])
        rows = parser.close()
    except ET.ParseError as exc:
        raise PdfValidationException("Invalid annotation XFDF XML") from exc
    try:
        return import_records(
            pdf,
            rows,
            page_map=page_map,
            target_page=target_page,
            budget=budget,
        )
    except PdfResourceLimitException:
        raise
    except PdfValidationException as exc:
        _fail(str(exc).removeprefix("Invalid annotation JSON: "))
