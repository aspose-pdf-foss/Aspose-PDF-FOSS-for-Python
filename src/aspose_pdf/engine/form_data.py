"""Form data in and out: FDF (ISO 32000-1 12.7.8) and XFDF (ISO 19444-1).

Both carry the field tree by partial names -- FDF as ``/T`` with ``/Kids``,
XFDF as nested ``<field name>`` -- and each field's ``/V`` where the document
stores it, a parent's value included, which is how Apache PDFBox writes and
reads them. On import a value goes to the field of that name through the same
path as :attr:`Field.value <aspose_pdf.forms.Field.value>`, so its widgets are
redrawn, and FDF's ``/Ff``, ``/SetFf``, ``/ClrFf`` (field flags) and ``/F``,
``/SetF``, ``/ClrF`` (widget flags) are applied as PDFBox applies them.

A signature field's value is a signature over *this* file's bytes: it is not
exported, and never imported. (PDFBox copies the signature dictionary into FDF
and stops writing XFDF at the field.)
"""

from __future__ import annotations

import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from typing import Any

from aspose_pdf.exceptions import PdfValidationException

from .cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfString,
    decode_pdf_text_string,
)
from .form_fields import field_attribute, field_dictionary, widget_dictionaries

__all__ = ["FieldData", "apply", "read_fdf", "read_xfdf", "to_fdf", "to_xfdf"]

_XFDF_NS = "http://ns.adobe.com/xfdf/"
_XML_NS = "http://www.w3.org/XML/1998/namespace"
_FIELD_FLAGS = ("Ff", "SetFf", "ClrFf")
_WIDGET_FLAGS = ("F", "SetF", "ClrF")
_NO_VALUE = object()


@dataclass
class FieldData:
    """One field's entry in an FDF or XFDF file."""

    name: str
    """The fully qualified name."""

    value: Any = _NO_VALUE
    """Text, a list of texts, or absent."""

    flags: dict[str, int] = field(default_factory=dict)
    """``Ff``/``SetFf``/``ClrFf`` and ``F``/``SetF``/``ClrF`` as given."""

    @property
    def has_value(self) -> bool:
        return self.value is not _NO_VALUE


# ---------------------------------------------------------------------------
# The document's field tree
# ---------------------------------------------------------------------------
@dataclass
class _Node:
    name: str
    value: Any  # a PdfString, PdfName or PdfArray of them, or None
    kids: list[_Node]


def _tree(pdf: Any) -> list[_Node]:
    if pdf._cos_doc is None:
        return []
    root = pdf._resolve(pdf._cos_doc.trailer.mapping.get(PdfName("Root")))
    acro = pdf._resolve(root.mapping.get(PdfName("AcroForm"))) if isinstance(root, PdfDictionary) else None
    fields = pdf._resolve(acro.mapping.get(PdfName("Fields"))) if isinstance(acro, PdfDictionary) else None
    seen: set[int] = set()
    budget = pdf._load_budget

    def nodes(items: Any, depth: int) -> list[_Node]:
        budget.check(depth, "max_nesting_depth", "form field tree depth")
        out: list[_Node] = []
        for item in items.items if isinstance(items, PdfArray) else ():
            entry = pdf._resolve(item)
            if not isinstance(entry, PdfDictionary) or id(entry) in seen:
                continue
            seen.add(id(entry))
            budget.check(len(seen), "max_container_items", "form field tree items")
            kids = pdf._resolve(entry.mapping.get(PdfName("Kids")))
            title = pdf._resolve(entry.mapping.get(PdfName("T")))
            if not isinstance(title, PdfString):
                # A widget, or a node with no name: its named kids are its
                # parent's, under the same name.
                out.extend(nodes(kids, depth + 1))
                continue
            value = _plain_value(pdf, entry.mapping.get(PdfName("V")))
            out.append(_Node(decode_pdf_text_string(title), value, nodes(kids, depth + 1)))
        return out

    return nodes(fields, 1)


def _plain_value(pdf: Any, value: Any) -> Any:
    """*value* if it is form data -- text, a name, or an array of those."""
    value = pdf._resolve(value)
    if isinstance(value, (PdfString, PdfName)):
        return value
    if isinstance(value, PdfArray):
        items = [pdf._resolve(item) for item in value.items]
        if all(isinstance(item, (PdfString, PdfName)) for item in items):
            return PdfArray(items)
    return None  # a stream (rich text lives in /RV), or a signature dictionary


def _ids(pdf: Any) -> list[bytes] | None:
    ids = pdf._resolve(pdf._cos_doc.trailer.mapping.get(PdfName("ID"))) if pdf._cos_doc else None
    if not isinstance(ids, PdfArray) or len(ids.items) != 2:
        return None
    values = [pdf._resolve(item) for item in ids.items]
    return [v.value for v in values] if all(isinstance(v, PdfString) for v in values) else None


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
def to_fdf(pdf: Any) -> bytes:
    """The document's form data as an FDF file."""
    from .pdf_writer_cos import PdfCosWriter

    def entry(node: _Node) -> PdfDictionary:
        mapping: dict[PdfName, Any] = {PdfName("T"): PdfString(node.name)}
        if node.value is not None:
            mapping[PdfName("V")] = node.value
        if node.kids:
            mapping[PdfName("Kids")] = PdfArray([entry(kid) for kid in node.kids])
        return PdfDictionary(mapping)

    fdf: dict[PdfName, Any] = {PdfName("Fields"): PdfArray([entry(node) for node in _tree(pdf)])}
    ids = _ids(pdf)
    if ids is not None:
        fdf[PdfName("ID")] = PdfArray([PdfString(value) for value in ids])
    body = PdfCosWriter(pdf._cos_doc).serialize_object(PdfDictionary({PdfName("FDF"): PdfDictionary(fdf)}))
    head = b"%FDF-1.2\n%\xe2\xe3\xcf\xd3\n"
    obj = b"1 0 obj\n" + body.encode("latin-1") + b"\nendobj\n"
    xref_at = len(head) + len(obj)
    xref = b"xref\n0 2\n0000000000 65535 f \n%010d 00000 n \n" % len(head)
    trailer = b"trailer\n<< /Root 1 0 R /Size 2 >>\nstartxref\n%d\n%%%%EOF\n" % xref_at
    return head + obj + xref + trailer


def to_xfdf(pdf: Any) -> bytes:
    """The document's form data as an XFDF file."""
    root = ElementTree.Element("xfdf", {"xmlns": _XFDF_NS, f"{{{_XML_NS}}}space": "preserve"})
    ids = _ids(pdf)
    if ids is not None:
        ElementTree.SubElement(root, "ids", {"original": ids[0].hex().upper(), "modified": ids[1].hex().upper()})
    fields = ElementTree.SubElement(root, "fields")

    def add(parent: ElementTree.Element, node: _Node) -> None:
        element = ElementTree.SubElement(parent, "field", {"name": node.name})
        values = node.value.items if isinstance(node.value, PdfArray) else [node.value] if node.value is not None else []
        for value in values:
            ElementTree.SubElement(element, "value").text = _text(value)
        for kid in node.kids:
            add(element, kid)

    for node in _tree(pdf):
        add(fields, node)
    ElementTree.indent(root, space="")
    return ElementTree.tostring(root, encoding="UTF-8", xml_declaration=True) + b"\n"


def _text(value: Any) -> str:
    return decode_pdf_text_string(value) if isinstance(value, PdfString) else value.name.lstrip("/")


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def read_fdf(data: bytes, budget: Any) -> list[FieldData]:
    """The field entries of an FDF file, parents before their kids."""
    from .pdf_parser_cos import PdfCosParser

    if not bytes(data[:1024]).lstrip().startswith(b"%FDF-"):
        raise PdfValidationException("Not an FDF file: it does not start with %FDF-")
    doc = PdfCosParser(bytes(data), budget=budget).parse()

    def resolve(value: Any) -> Any:
        from .cos import PdfIndirectReference

        return doc.get_object(value) if isinstance(value, PdfIndirectReference) else value

    root = resolve(doc.trailer.mapping.get(PdfName("Root")))
    fdf = resolve(root.mapping.get(PdfName("FDF"))) if isinstance(root, PdfDictionary) else None
    if not isinstance(fdf, PdfDictionary):
        raise PdfValidationException("Not an FDF file: its catalog has no /FDF dictionary")
    out: list[FieldData] = []
    seen: set[int] = set()

    def walk(items: Any, prefix: str, depth: int) -> None:
        budget.check(depth, "max_nesting_depth", "FDF field depth")
        for item in items.items if isinstance(items, PdfArray) else ():
            entry = resolve(item)
            if not isinstance(entry, PdfDictionary) or id(entry) in seen:
                continue
            seen.add(id(entry))
            budget.check(len(seen), "max_container_items", "FDF fields")
            title = resolve(entry.mapping.get(PdfName("T")))
            name = decode_pdf_text_string(title) if isinstance(title, PdfString) else ""
            full = f"{prefix}.{name}" if prefix and name else (prefix or name)
            data_entry = FieldData(full)
            value = _python_value(resolve, entry.mapping.get(PdfName("V")))
            if value is not None:
                data_entry.value = value
            for key in _FIELD_FLAGS + _WIDGET_FLAGS:
                flag = resolve(entry.mapping.get(PdfName(key)))
                if isinstance(flag, PdfNumber) and isinstance(flag.value, int):
                    data_entry.flags[key] = int(flag.value) & 0xFFFFFFFF
            if full and (data_entry.has_value or data_entry.flags):
                out.append(data_entry)
            walk(resolve(entry.mapping.get(PdfName("Kids"))), full, depth + 1)

    walk(resolve(fdf.mapping.get(PdfName("Fields"))), "", 1)
    return out


def _python_value(resolve: Any, value: Any) -> Any:
    value = resolve(value)
    if isinstance(value, PdfString):
        return decode_pdf_text_string(value)
    if isinstance(value, PdfName):
        return value.name.lstrip("/")
    if isinstance(value, PdfArray):
        items = [_python_value(resolve, item) for item in value.items]
        texts = [item for item in items if isinstance(item, str)]
        return texts if len(texts) != 1 else texts[0]
    return None


@dataclass(slots=True)
class _XfdfElement:
    kind: str
    prefix: str = ""
    entry: FieldData | None = None
    values: list[str] | None = None
    text: list[str] | None = None
    has_child: bool = False


class _XfdfReader:
    """Collect field values while bounding the XML parse itself."""

    def __init__(self, budget: Any) -> None:
        self.budget = budget
        self.elements: list[_XfdfElement] = []
        self.entries: list[FieldData] = []
        self.count = 0

    def start(self, tag: str, attributes: dict[str, str]) -> None:
        self.count += 1
        self.budget.check(self.count, "max_container_items", "XFDF elements")
        self.budget.check(len(self.elements) + 1, "max_nesting_depth", "XFDF XML depth")
        local = _local(tag)
        if not self.elements:
            if local != "xfdf":
                raise PdfValidationException("Not an XFDF file: its root element is not <xfdf>")
            self.elements.append(_XfdfElement("root"))
            return

        parent = self.elements[-1]
        if parent.kind == "value":
            parent.has_child = True
        if local == "fields" and parent.kind == "root":
            element = _XfdfElement("fields")
        elif local == "field" and parent.kind in ("fields", "field"):
            name = attributes.get("name") or ""
            full = f"{parent.prefix}.{name}" if parent.prefix and name else (parent.prefix or name)
            entry = FieldData(full) if full else None
            if entry is not None:
                self.entries.append(entry)
            element = _XfdfElement("field", full, entry, values=[])
        elif local == "value" and parent.kind == "field":
            element = _XfdfElement("value", text=[])
        else:
            element = _XfdfElement("other")
        self.elements.append(element)

    def data(self, text: str) -> None:
        if self.elements:
            element = self.elements[-1]
            if element.kind == "value" and not element.has_child and element.text is not None:
                element.text.append(text)

    def end(self, tag: str) -> None:
        element = self.elements.pop()
        if element.kind == "value":
            parent = self.elements[-1]
            if parent.values is not None:
                parent.values.append("".join(element.text or []))
        elif element.kind == "field" and element.entry is not None and element.values:
            element.entry.value = (
                element.values if len(element.values) > 1 else element.values[0]
            )

    def doctype(self, name: str, pubid: str | None, system: str | None) -> None:
        raise PdfValidationException(
            "XFDF with a DOCTYPE or entity declarations is not accepted"
        )

    def close(self) -> list[FieldData]:
        return [entry for entry in self.entries if entry.has_value]


def read_xfdf(data: bytes, budget: Any) -> list[FieldData]:
    """The field entries of an XFDF file, parents before their kids."""
    budget.check_input(len(data))
    data = bytes(data)
    parser = ElementTree.XMLParser(target=_XfdfReader(budget))
    try:
        for offset in range(0, len(data), 64 * 1024):
            parser.feed(data[offset : offset + 64 * 1024])
        return parser.close()
    except ElementTree.ParseError as exc:
        raise PdfValidationException(f"Not a well-formed XFDF file: {exc}") from None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


# ---------------------------------------------------------------------------
# Importing
# ---------------------------------------------------------------------------
def apply(pdf: Any, entries: list[FieldData]) -> list[str]:
    """Set each entry's value and flags on the field of its name; return those found."""
    imported: list[str] = []
    for entry in entries:
        target = field_dictionary(pdf, entry.name)
        if target is None:
            continue  # a field this document does not have, as PDFBox skips it
        if entry.has_value and field_attribute(pdf, target, "FT") != PdfName("Sig"):
            pdf.set_field_value(entry.name, entry.value)
        _apply_flags(pdf, [target], entry.flags, _FIELD_FLAGS, lambda d: field_attribute(pdf, d, "Ff"))
        _apply_flags(
            pdf, widget_dictionaries(pdf, target), entry.flags, _WIDGET_FLAGS,
            lambda d: pdf._resolve(d.mapping.get(PdfName("F"))),
        )
        imported.append(entry.name)
    return imported


def _apply_flags(pdf: Any, targets: list[PdfDictionary], given: dict[str, int], keys: tuple[str, str, str], current: Any) -> None:
    """A full value replaces the flags; otherwise set, then clear, bits."""
    whole, set_bits, clear_bits = (given.get(key) for key in keys)
    if whole is None and set_bits is None and clear_bits is None:
        return
    for target in targets:
        if whole is not None:
            flags = whole
        else:
            existing = current(target)
            flags = int(existing.value) if isinstance(existing, PdfNumber) else 0
            flags = (flags | (set_bits or 0)) & ~(clear_bits or 0) & 0xFFFFFFFF
        target.mapping[PdfName(keys[0])] = PdfNumber(flags)
