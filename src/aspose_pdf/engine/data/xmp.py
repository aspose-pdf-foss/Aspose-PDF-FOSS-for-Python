"""Internal XMP helpers kept importable for the prerelease package.

This module is the single source of truth for the XMP data model.  It provides:

* a working namespace provider (:class:`XmpNamespaceProvider`) preloaded with
  the standard XMP namespaces (Dublin Core, Adobe XMP, PDF, PDF/A, EXIF, TIFF,
  ...), extensible with custom mappings;
* lightweight data containers (:class:`XmpField`, :class:`XmpArray`,
  :class:`XmpStruct`, :class:`XmpProperty`, :class:`XmpPacket`); and
* a hardened XMP packet parser (:func:`parse_xmp`) and a deterministic
  serializer (:func:`serialize_xmp`) that round-trip the common XMP shapes
  (simple properties, ``rdf:Bag``/``Seq``/``Alt`` arrays, ``xml:lang``, and
  structured values via ``rdf:parseType="Resource"`` / nested
  ``rdf:Description`` — including arrays of structs such as ``xmpMM:History``).

The public :mod:`aspose_pdf.xmp` module re-exports these names.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Iterator, Mapping

__all__ = [
    "STANDARD_XMP_NAMESPACES",
    "XmpArray",
    "XmpField",
    "XmpNamespaceProvider",
    "XmpPacket",
    "XmpProperty",
    "XmpStruct",
    "info_to_xmp",
    "iso8601_to_pdf_date",
    "parse_xmp",
    "pdf_date_to_iso8601",
    "serialize_xmp",
    "xmp_to_info",
]


# Canonical prefix -> URI mappings for the well-known XMP namespaces.
# Each URI is unique so the reverse (URI -> prefix) mapping is deterministic.
STANDARD_XMP_NAMESPACES: dict[str, str] = {
    # Core RDF / XML wrappers.
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "xml": "http://www.w3.org/XML/1998/namespace",
    "x": "adobe:ns:meta/",
    # Dublin Core.
    "dc": "http://purl.org/dc/elements/1.1/",
    # Adobe XMP schemas.
    "xmp": "http://ns.adobe.com/xap/1.0/",
    "xmpRights": "http://ns.adobe.com/xap/1.0/rights/",
    "xmpMM": "http://ns.adobe.com/xap/1.0/mm/",
    "xmpBJ": "http://ns.adobe.com/xap/1.0/bj/",
    "xmpTPg": "http://ns.adobe.com/xap/1.0/t/pg/",
    "xmpDM": "http://ns.adobe.com/xmp/1.0/DynamicMedia/",
    "xmpidq": "http://ns.adobe.com/xmp/Identifier/qual/1.0/",
    # PDF-specific schemas.
    "pdf": "http://ns.adobe.com/pdf/1.3/",
    "pdfx": "http://ns.adobe.com/pdfx/1.3/",
    "pdfaid": "http://www.aiim.org/pdfa/ns/id/",
    "pdfuaid": "http://www.aiim.org/pdfua/ns/id/",
    "pdfaExtension": "http://www.aiim.org/pdfa/ns/extension/",
    "pdfaSchema": "http://www.aiim.org/pdfa/ns/schema#",
    "pdfaProperty": "http://www.aiim.org/pdfa/ns/property#",
    # Imaging schemas.
    "photoshop": "http://ns.adobe.com/photoshop/1.0/",
    "tiff": "http://ns.adobe.com/tiff/1.0/",
    "exif": "http://ns.adobe.com/exif/1.0/",
    "aux": "http://ns.adobe.com/exif/1.0/aux/",
    "crs": "http://ns.adobe.com/camera-raw-settings/1.0/",
    # IPTC schemas.
    "Iptc4xmpCore": "http://iptc.org/std/Iptc4xmpCore/1.0/xmlns/",
    "Iptc4xmpExt": "http://iptc.org/std/Iptc4xmpExt/2008-02-29/",
    # Structured value types.
    "stDim": "http://ns.adobe.com/xap/1.0/sType/Dimensions#",
    "stEvt": "http://ns.adobe.com/xap/1.0/sType/ResourceEvent#",
    "stRef": "http://ns.adobe.com/xap/1.0/sType/ResourceRef#",
    "stVer": "http://ns.adobe.com/xap/1.0/sType/Version#",
    "stJob": "http://ns.adobe.com/xap/1.0/sType/Job#",
    "stFnt": "http://ns.adobe.com/xap/1.0/sType/Font#",
}

# Reverse lookup for the standard table (URI -> prefix); used by the serializer.
_STANDARD_URI_TO_PREFIX: dict[str, str] = {
    uri: prefix for prefix, uri in STANDARD_XMP_NAMESPACES.items()
}

_RDF_URI = STANDARD_XMP_NAMESPACES["rdf"]
_XML_URI = STANDARD_XMP_NAMESPACES["xml"]


class XmpNamespaceProvider:
    """Bidirectional XMP namespace prefix <-> URI resolver.

    By default the provider is preloaded with :data:`STANDARD_XMP_NAMESPACES`.
    Pass ``include_defaults=False`` to start empty, and/or supply an initial
    mapping of ``prefix -> uri``. Additional mappings can be added later with
    :meth:`register`.
    """

    def __init__(
        self,
        namespaces: dict[str, str] | None = None,
        *,
        include_defaults: bool = True,
    ) -> None:
        self._prefix_to_uri: dict[str, str] = {}
        self._uri_to_prefix: dict[str, str] = {}
        if include_defaults:
            for prefix, uri in STANDARD_XMP_NAMESPACES.items():
                self.register(prefix, uri)
        if namespaces:
            for prefix, uri in dict(namespaces).items():
                self.register(prefix, uri)

    @staticmethod
    def _normalize_prefix(prefix: str) -> str:
        return prefix.rstrip(":") if prefix else prefix

    def register(self, prefix: str, uri: str) -> XmpNamespaceProvider:
        """Register a ``prefix`` <-> ``uri`` mapping.

        A trailing colon on *prefix* (e.g. ``"dc:"``) is ignored. The most
        recent registration wins for both lookup directions, and any stale
        reverse mapping is cleaned up. Returns ``self`` to allow chaining.
        """
        if not prefix or not uri:
            raise ValueError("Both prefix and uri must be non-empty")
        prefix = self._normalize_prefix(prefix)

        previous_uri = self._prefix_to_uri.get(prefix)
        if previous_uri is not None and previous_uri != uri:
            if self._uri_to_prefix.get(previous_uri) == prefix:
                del self._uri_to_prefix[previous_uri]

        self._prefix_to_uri[prefix] = uri
        self._uri_to_prefix[uri] = prefix
        return self

    def get_uri(self, prefix: str) -> str | None:
        """Return the namespace URI for *prefix*, or ``None`` if unknown."""
        if not prefix:
            return None
        return self._prefix_to_uri.get(self._normalize_prefix(prefix))

    def get_prefix(self, uri: str) -> str | None:
        """Return the prefix bound to *uri*, or ``None`` if unknown."""
        if not uri:
            return None
        return self._uri_to_prefix.get(uri)

    def __contains__(self, prefix: object) -> bool:
        if not isinstance(prefix, str) or not prefix:
            return False
        return self._normalize_prefix(prefix) in self._prefix_to_uri

    def prefixes(self) -> list[str]:
        """Return all registered prefixes."""
        return list(self._prefix_to_uri)

    def uris(self) -> list[str]:
        """Return all registered URIs."""
        return list(self._uri_to_prefix)

    def items(self) -> list[tuple[str, str]]:
        """Return all ``(prefix, uri)`` mappings."""
        return list(self._prefix_to_uri.items())


@dataclass(slots=True)
class XmpField:
    """A single XMP property.

    ``value`` holds a ``str`` for a simple property, an :class:`XmpArray` for
    an array-valued property (``rdf:Bag``/``Seq``/``Alt``), or an
    :class:`XmpStruct` for a structured value (``rdf:parseType="Resource"``).
    ``language`` carries an ``xml:lang`` qualifier when present. When
    ``is_uri`` is set, a string ``value`` is a URI reference and is serialized
    as an ``rdf:resource`` attribute rather than as element text. ``qualifiers``
    holds any RDF qualifiers attached to the value (the ``rdf:value`` +
    qualifier-sibling form); it is used for fields nested inside an array item
    or struct member — top-level qualified properties use :class:`XmpProperty`.
    """

    prefix: str = ""
    name: str = ""
    namespace_uri: str = ""
    value: Any = None
    language: str | None = None
    is_uri: bool = False
    qualifiers: list[XmpField] = field(default_factory=list)

    LANG = "lang"


@dataclass(slots=True)
class XmpArray:
    """An ordered XMP array value.

    ``kind`` is one of ``"Bag"`` (unordered), ``"Seq"`` (ordered) or ``"Alt"``
    (alternatives, e.g. language alternatives).
    """

    items: list[XmpField] = field(default_factory=list)
    namespace_provider: XmpNamespaceProvider | None = field(
        default=None, compare=False
    )
    kind: str = "Bag"

    def add(self, item: XmpField) -> None:
        self.items.append(item)

    def remove(self, item: XmpField) -> bool:
        if item in self.items:
            self.items.remove(item)
            return True
        return False

    def __iter__(self) -> Iterator[XmpField]:
        return iter(self.items)


@dataclass(slots=True)
class XmpStruct:
    """A structured XMP value (an ``rdf:parseType="Resource"`` block).

    Holds an ordered list of member :class:`XmpField` objects, e.g. the
    ``stDim:w``/``stDim:h``/``stDim:unit`` of an ``xmpTPg:MaxPageSize`` or the
    ``stEvt:*`` members of an ``xmpMM:History`` entry. A member's ``value`` may
    itself be a ``str``, an :class:`XmpArray`, or a nested :class:`XmpStruct`.
    """

    fields: list[XmpField] = field(default_factory=list)
    namespace_provider: XmpNamespaceProvider | None = field(
        default=None, compare=False
    )

    def add(self, item: XmpField) -> None:
        self.fields.append(item)

    def get(self, name: str) -> XmpField | None:
        """Return the first member named *name*, or ``None``."""
        for fld in self.fields:
            if fld.name == name:
                return fld
        return None

    def __iter__(self) -> Iterator[XmpField]:
        return iter(self.fields)


@dataclass(slots=True)
class XmpProperty:
    """A property carrying arbitrary qualifiers."""

    field: XmpField
    namespace_provider: XmpNamespaceProvider | None = field(
        default=None, compare=False
    )
    qualifiers: list[XmpField] = field(default_factory=list)

    def add_qualifier(self, qualifier: XmpField) -> None:
        self.qualifiers.append(qualifier)

    def remove_qualifier(self, qualifier: XmpField) -> None:
        if qualifier in self.qualifiers:
            self.qualifiers.remove(qualifier)


@dataclass(slots=True)
class XmpPacket:
    """An in-memory XMP packet: an ordered collection of properties."""

    fields: list[XmpField | XmpArray | XmpProperty] = field(default_factory=list)
    qualifiers: list[XmpField] = field(default_factory=list)
    namespace_provider: XmpNamespaceProvider | None = field(
        default=None, compare=False
    )

    def add(self, value: XmpField | XmpArray | XmpProperty) -> None:
        self.fields.append(value)

    @classmethod
    def parse(
        cls, data: str | bytes, *, provider: XmpNamespaceProvider | None = None
    ) -> XmpPacket:
        """Parse an XMP packet (bytes or text) into an :class:`XmpPacket`."""
        return parse_xmp(data, provider=provider)

    def serialize(self, **kwargs: Any) -> bytes:
        """Serialize this packet to XMP packet bytes (see :func:`serialize_xmp`)."""
        return serialize_xmp(self, **kwargs)

    # ``to_bytes`` is an alias kept for symmetry with other engine objects.
    def to_bytes(self, **kwargs: Any) -> bytes:
        return serialize_xmp(self, **kwargs)

    def _iter_fields(self) -> Iterator[XmpField]:
        for entry in self.fields:
            if isinstance(entry, XmpProperty):
                yield entry.field
            elif isinstance(entry, XmpField):
                yield entry

    def get(self, prefix_or_uri: str, name: str) -> XmpField | None:
        """Return the first property matching *name* under *prefix_or_uri*.

        *prefix_or_uri* is matched against both the field prefix and its
        namespace URI, so either ``get("dc", "format")`` or
        ``get("http://purl.org/dc/elements/1.1/", "format")`` works.
        """
        for fld in self._iter_fields():
            if fld.name == name and (
                fld.prefix == prefix_or_uri or fld.namespace_uri == prefix_or_uri
            ):
                return fld
        return None

    def set_value(
        self, prefix: str, name: str, value: Any, *, uri: str = ""
    ) -> XmpField:
        """Set (or add) a simple property and return the affected :class:`XmpField`."""
        existing = self.get(prefix, name)
        if existing is None and uri:
            existing = self.get(uri, name)
        if existing is not None:
            existing.value = value
            return existing
        fld = XmpField(prefix=prefix, name=name, namespace_uri=uri, value=value)
        self.fields.append(fld)
        return fld

    # -- Typed convenience accessors --------------------------------------
    # XMP values are stored as strings/arrays; these helpers read and write
    # the common typed shapes (dates, language alternatives, arrays) without
    # the caller hand-building XmpArray/XmpField values.

    def set_date(
        self, prefix: str, name: str, value: datetime | str, *, uri: str = ""
    ) -> XmpField:
        """Set a date property, storing an ISO-8601 string (XMP date form)."""
        iso = value.isoformat() if isinstance(value, datetime) else str(value)
        return self.set_value(prefix, name, iso, uri=uri)

    def get_date(self, prefix_or_uri: str, name: str) -> datetime | None:
        """Return a date property as a :class:`~datetime.datetime`, or ``None``.

        Returns ``None`` when the property is absent or not an ISO-8601 date.
        """
        fld = self.get(prefix_or_uri, name)
        if fld is None or not isinstance(fld.value, str) or not fld.value:
            return None
        try:
            return datetime.fromisoformat(fld.value)
        except ValueError:
            return None

    def set_localized_text(
        self,
        prefix: str,
        name: str,
        text: str,
        *,
        uri: str = "",
        lang: str = "x-default",
    ) -> XmpField:
        """Set a language-alternative (``rdf:Alt``) text property, e.g. dc:title."""
        array = XmpArray(kind="Alt", items=[XmpField(value=text, language=lang)])
        return self.set_value(prefix, name, array, uri=uri)

    def get_localized_text(
        self, prefix_or_uri: str, name: str, *, lang: str = "x-default"
    ) -> str | None:
        """Return the *lang* (or ``x-default``) alternative of a text property.

        Falls back to the first available alternative, and to the plain value
        for a non-array property. Returns ``None`` when the property is absent.
        """
        fld = self.get(prefix_or_uri, name)
        if fld is None:
            return None
        value = fld.value
        if isinstance(value, XmpArray):
            for item in value.items:
                if item.language in (None, "", lang):
                    return str(item.value)
            return str(value.items[0].value) if value.items else None
        return None if value is None else str(value)

    def set_array(
        self,
        prefix: str,
        name: str,
        values: Iterable[Any],
        *,
        uri: str = "",
        kind: str = "Seq",
    ) -> XmpField:
        """Set an array property (``Seq``/``Bag``/``Alt``) from *values*.

        Items may be plain values (wrapped as simple :class:`XmpField` items)
        or pre-built :class:`XmpField` objects (to carry language/URI flags).
        """
        items = [
            v if isinstance(v, XmpField) else XmpField(value=str(v)) for v in values
        ]
        return self.set_value(prefix, name, XmpArray(kind=kind, items=items), uri=uri)

    def get_array(self, prefix_or_uri: str, name: str) -> list[str] | None:
        """Return an array property's items as a list of strings, or ``None``."""
        fld = self.get(prefix_or_uri, name)
        if fld is None or not isinstance(fld.value, XmpArray):
            return None
        return [str(item.value) for item in fld.value.items]

    def set_bool(
        self, prefix: str, name: str, value: bool, *, uri: str = ""
    ) -> XmpField:
        """Set a Boolean property (XMP encodes it as ``"True"``/``"False"``)."""
        return self.set_value(prefix, name, "True" if value else "False", uri=uri)

    def get_bool(self, prefix_or_uri: str, name: str) -> bool | None:
        """Return a Boolean property, or ``None`` if absent / not a boolean."""
        fld = self.get(prefix_or_uri, name)
        if fld is None or not isinstance(fld.value, str):
            return None
        lowered = fld.value.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        return None

    def set_int(self, prefix: str, name: str, value: int, *, uri: str = "") -> XmpField:
        """Set an Integer property."""
        return self.set_value(prefix, name, str(int(value)), uri=uri)

    def get_int(self, prefix_or_uri: str, name: str) -> int | None:
        """Return an Integer property, or ``None`` if absent / not an integer."""
        fld = self.get(prefix_or_uri, name)
        if fld is None or not isinstance(fld.value, str):
            return None
        try:
            return int(fld.value.strip())
        except (ValueError, TypeError):
            return None

    def set_real(
        self, prefix: str, name: str, value: float, *, uri: str = ""
    ) -> XmpField:
        """Set a Real (floating-point) property."""
        return self.set_value(prefix, name, str(float(value)), uri=uri)

    def get_real(self, prefix_or_uri: str, name: str) -> float | None:
        """Return a Real property, or ``None`` if absent / not a number."""
        fld = self.get(prefix_or_uri, name)
        if fld is None or not isinstance(fld.value, str):
            return None
        try:
            return float(fld.value.strip())
        except (ValueError, TypeError):
            return None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _decode_xmp_bytes(data: str | bytes) -> str:
    """Decode XMP *data* to text, stripping a leading BOM (default UTF-8)."""
    if isinstance(data, str):
        return data
    if data[:3] == b"\xef\xbb\xbf":
        return data[3:].decode("utf-8", errors="replace")
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    return data.decode("utf-8", errors="replace")


def _reject_dtd(text: str) -> None:
    """Reject DTD / entity declarations (billion-laughs / XXE guard).

    XMP packets are standalone and must not carry a DTD, so the presence of a
    ``<!DOCTYPE`` or ``<!ENTITY`` declaration is treated as hostile input.
    """
    lowered = text.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        raise ValueError(
            "XMP packet must not contain a DTD or entity declaration"
        )


def _extract_root_xml(text: str) -> str:
    """Slice *text* to the outer ``x:xmpmeta`` / ``rdf:RDF`` element.

    Drops the surrounding ``<?xpacket?>`` processing instructions, any XML
    declaration, and trailing whitespace padding.
    """
    for tag in ("x:xmpmeta", "rdf:RDF"):
        start = text.find("<" + tag)
        if start != -1:
            end_marker = "</" + tag + ">"
            end = text.rfind(end_marker)
            if end != -1:
                return text[start : end + len(end_marker)]
    return text


def _split_qn(tag: str) -> tuple[str, str]:
    """Split an ElementTree ``{uri}local`` qualified name into ``(uri, local)``."""
    if tag.startswith("{"):
        uri, _, local = tag[1:].partition("}")
        return uri, local
    return "", tag


_ARRAY_KINDS = {
    f"{{{_RDF_URI}}}Bag": "Bag",
    f"{{{_RDF_URI}}}Seq": "Seq",
    f"{{{_RDF_URI}}}Alt": "Alt",
}
_RDF_LI = f"{{{_RDF_URI}}}li"
_RDF_DESCRIPTION = f"{{{_RDF_URI}}}Description"
_RDF_RDF = f"{{{_RDF_URI}}}RDF"
_RDF_ABOUT = f"{{{_RDF_URI}}}about"
_RDF_RESOURCE = f"{{{_RDF_URI}}}resource"
_RDF_PARSE_TYPE = f"{{{_RDF_URI}}}parseType"
_RDF_VALUE = f"{{{_RDF_URI}}}value"
_XML_LANG = f"{{{_XML_URI}}}lang"

# Container element name for each array kind (serializer side).
_ARRAY_CONTAINER = {"Bag": "rdf:Bag", "Seq": "rdf:Seq", "Alt": "rdf:Alt"}


def _resource_flag(element: ET.Element, value: Any) -> bool:
    """True if *element* carries a string value via an ``rdf:resource`` URI."""
    return _RDF_RESOURCE in element.attrib and isinstance(value, str)


def _parse_array(
    array_elem: ET.Element, uri_to_prefix: dict[str, str]
) -> XmpArray:
    """Build an :class:`XmpArray` from an ``rdf:Bag``/``Seq``/``Alt`` element.

    Each ``rdf:li`` is parsed recursively, so an item may be a simple string,
    a nested array, or an :class:`XmpStruct` (``rdf:li rdf:parseType="Resource"``
    — e.g. the ``stEvt:*`` entries of an ``xmpMM:History`` ``Seq``).
    """
    items: list[XmpField] = []
    for li in array_elem.findall(_RDF_LI):
        items.append(_parse_member_field(li, "", "", "", uri_to_prefix))
    return XmpArray(items=items, kind=_ARRAY_KINDS[array_elem.tag])


def _parse_struct_members(
    container: ET.Element, uri_to_prefix: dict[str, str]
) -> XmpStruct:
    """Build an :class:`XmpStruct` from the members of *container*.

    *container* is either the property element itself (``parseType="Resource"``
    form) or the nested ``rdf:Description`` (general form). Members may be
    expressed as child elements or as qualified attributes (the abbreviated
    form, e.g. ``stRef:instanceID="..."``). RDF/XML-namespaced and unqualified
    attributes (``rdf:about``, ``xml:lang``, ``rdf:parseType``) are skipped.
    """
    struct = XmpStruct()
    # Abbreviated attribute-form members.
    for attr_key, attr_value in container.attrib.items():
        a_uri, a_local = _split_qn(attr_key)
        if not a_local or not a_uri or a_uri in (_RDF_URI, _XML_URI):
            continue
        struct.add(
            XmpField(
                prefix=uri_to_prefix.get(a_uri, ""),
                name=a_local,
                namespace_uri=a_uri,
                value=attr_value,
            )
        )
    # Element-form members.
    for member in container:
        if not isinstance(member.tag, str):  # comments / processing instructions
            continue
        m_uri, m_local = _split_qn(member.tag)
        if not m_local or m_uri == _RDF_URI:
            continue
        struct.add(
            _parse_member_field(
                member, uri_to_prefix.get(m_uri, ""), m_local, m_uri, uri_to_prefix
            )
        )
    return struct


def _has_member_attrs(element: ET.Element) -> bool:
    """True if *element* carries qualified (non-RDF/XML) attribute members."""
    return any(
        _split_qn(key)[0] not in ("", _RDF_URI, _XML_URI) for key in element.attrib
    )


def _parse_value(element: ET.Element, uri_to_prefix: dict[str, str]) -> Any:
    """Parse the *value* carried by a property / member / ``rdf:li`` element.

    Returns an :class:`XmpArray` (``rdf:Bag``/``Seq``/``Alt``), an
    :class:`XmpStruct` (``rdf:parseType="Resource"``, a nested
    ``rdf:Description``, or member children/attributes), or a ``str`` (an
    ``rdf:resource`` URI or plain text).
    """
    for sub in element:
        if sub.tag in _ARRAY_KINDS:
            return _parse_array(sub, uri_to_prefix)

    if element.attrib.get(_RDF_PARSE_TYPE) == "Resource":
        return _parse_struct_members(element, uri_to_prefix)

    nested = element.find(_RDF_DESCRIPTION)
    if nested is not None:
        return _parse_struct_members(nested, uri_to_prefix)

    # A URI value carried as rdf:resource (e.g. xmpMM:DerivedFrom).
    resource = element.attrib.get(_RDF_RESOURCE)
    if resource is not None:
        return resource

    # A struct written without an explicit parseType marker (lenient form):
    # member child elements and/or abbreviated attribute members.
    if any(isinstance(c.tag, str) for c in element) or _has_member_attrs(element):
        return _parse_struct_members(element, uri_to_prefix)

    return element.text or ""


def _qualifier_container(element: ET.Element) -> ET.Element | None:
    """Return the container holding ``rdf:value`` + qualifiers, or ``None``.

    A property carrying qualifiers is written as an ``rdf:value`` member next to
    one or more qualifier members (typically under ``rdf:parseType="Resource"``
    or a nested ``rdf:Description``). Returns the element whose members are the
    ``rdf:value`` and the qualifiers, or ``None`` when *element* is not the
    qualifier form (plain value, array, or a struct without ``rdf:value``).
    """
    for sub in element:  # arrays are never the qualifier form
        if sub.tag in _ARRAY_KINDS:
            return None
    if element.attrib.get(_RDF_PARSE_TYPE) == "Resource":
        container = element
    else:
        nested = element.find(_RDF_DESCRIPTION)
        if nested is not None:
            container = nested
        elif any(isinstance(c.tag, str) for c in element) or _has_member_attrs(element):
            container = element
        else:
            return None
    if container.find(_RDF_VALUE) is not None or _RDF_VALUE in container.attrib:
        return container
    return None


def _parse_value_and_qualifiers(
    container: ET.Element, base_lang: str | None, uri_to_prefix: dict[str, str]
) -> tuple[Any, str | None, bool, list[XmpField]]:
    """Parse a value-plus-qualifiers container.

    Returns ``(main_value, language, is_uri, qualifiers)`` where the main value
    comes from the ``rdf:value`` member and the qualifiers are the remaining
    members (element- or abbreviated-attribute form; ``rdf:*`` members ignored).
    """
    main_value: Any = container.attrib.get(_RDF_VALUE, "")
    main_lang = base_lang
    main_is_uri = False
    qualifiers: list[XmpField] = []

    for member in container:
        if not isinstance(member.tag, str):
            continue
        m_uri, m_local = _split_qn(member.tag)
        if not m_local:
            continue
        if member.tag == _RDF_VALUE:
            main_value = _parse_value(member, uri_to_prefix)
            main_lang = member.attrib.get(_XML_LANG, main_lang)
            main_is_uri = _resource_flag(member, main_value)
            continue
        if m_uri == _RDF_URI:
            continue
        # Parse via the member builder so a qualifier may itself carry
        # qualifiers (recursive qualification).
        qualifiers.append(
            _parse_member_field(
                member, uri_to_prefix.get(m_uri, ""), m_local, m_uri, uri_to_prefix
            )
        )

    # Abbreviated attribute-form qualifiers.
    for attr_key, attr_value in container.attrib.items():
        a_uri, a_local = _split_qn(attr_key)
        if not a_local or not a_uri or a_uri in (_RDF_URI, _XML_URI):
            continue
        qualifiers.append(
            XmpField(
                prefix=uri_to_prefix.get(a_uri, ""),
                name=a_local,
                namespace_uri=a_uri,
                value=attr_value,
            )
        )

    return main_value, main_lang, main_is_uri, qualifiers


def _parse_qualified_property(
    prefix: str,
    local: str,
    uri: str,
    lang: str | None,
    container: ET.Element,
    uri_to_prefix: dict[str, str],
) -> XmpProperty:
    """Build a top-level :class:`XmpProperty` (main value + qualifiers)."""
    value, main_lang, is_uri, qualifiers = _parse_value_and_qualifiers(
        container, lang, uri_to_prefix
    )
    return XmpProperty(
        field=XmpField(
            prefix=prefix,
            name=local,
            namespace_uri=uri,
            value=value,
            language=main_lang,
            is_uri=is_uri,
        ),
        qualifiers=qualifiers,
    )


def _parse_member_field(
    element: ET.Element,
    prefix: str,
    name: str,
    uri: str,
    uri_to_prefix: dict[str, str],
) -> XmpField:
    """Build an :class:`XmpField` for a nested position (``rdf:li`` / struct member).

    Unlike a top-level property (which becomes an :class:`XmpProperty`), a
    qualified value nested inside an array or struct is represented as an
    :class:`XmpField` carrying its qualifiers, so the ``rdf:value`` + qualifier
    form round-trips here too.
    """
    container = _qualifier_container(element)
    if container is not None:
        value, lang, is_uri, qualifiers = _parse_value_and_qualifiers(
            container, element.attrib.get(_XML_LANG), uri_to_prefix
        )
        return XmpField(
            prefix=prefix,
            name=name,
            namespace_uri=uri,
            value=value,
            language=lang,
            is_uri=is_uri,
            qualifiers=qualifiers,
        )
    value = _parse_value(element, uri_to_prefix)
    return XmpField(
        prefix=prefix,
        name=name,
        namespace_uri=uri,
        value=value,
        language=element.attrib.get(_XML_LANG),
        is_uri=_resource_flag(element, value),
    )


def _parse_property(
    element: ET.Element, uri_to_prefix: dict[str, str]
) -> XmpField | XmpProperty | None:
    """Convert a single ``rdf:Description`` child element into a property.

    Returns an :class:`XmpProperty` when the element carries RDF qualifiers
    (``rdf:value`` + sibling members), otherwise a plain :class:`XmpField`.
    """
    uri, local = _split_qn(element.tag)
    if not local:
        return None
    lang = element.attrib.get(_XML_LANG)

    container = _qualifier_container(element)
    if container is not None:
        return _parse_qualified_property(
            prefix=uri_to_prefix.get(uri, ""),
            local=local,
            uri=uri,
            lang=lang,
            container=container,
            uri_to_prefix=uri_to_prefix,
        )

    value = _parse_value(element, uri_to_prefix)
    return XmpField(
        prefix=uri_to_prefix.get(uri, ""),
        name=local,
        namespace_uri=uri,
        value=value,
        language=lang,
        is_uri=_resource_flag(element, value),
    )


def parse_xmp(
    data: str | bytes, *, provider: XmpNamespaceProvider | None = None
) -> XmpPacket:
    """Parse an XMP packet into an :class:`XmpPacket`.

    Accepts raw bytes (with or without the ``<?xpacket?>`` wrapper) or text.
    Namespace prefixes declared in the packet are preserved; the returned
    packet's :attr:`XmpPacket.namespace_provider` resolves them. DTD/entity
    declarations are rejected as a billion-laughs / XXE guard.
    """
    text = _decode_xmp_bytes(data)
    _reject_dtd(text)
    xml_text = _extract_root_xml(text)

    # Seed the URI -> prefix map with the standard table and any caller mapping,
    # then let in-document declarations override.
    uri_to_prefix: dict[str, str] = dict(_STANDARD_URI_TO_PREFIX)
    out_provider = XmpNamespaceProvider()
    if provider is not None:
        for prefix, uri in provider.items():
            uri_to_prefix[uri] = prefix
            out_provider.register(prefix, uri)

    pull = ET.XMLPullParser(events=("start-ns", "start", "end"))
    pull.feed(xml_text)
    pull.close()
    root: ET.Element | None = None
    for event, payload in pull.read_events():
        if event == "start-ns":
            ns_prefix, ns_uri = payload
            if ns_prefix and ns_uri:
                uri_to_prefix[ns_uri] = ns_prefix
                out_provider.register(ns_prefix, ns_uri)
        elif event == "end":
            root = payload

    packet = XmpPacket(namespace_provider=out_provider)
    if root is None:
        return packet

    rdf = root if root.tag == _RDF_RDF else root.find(_RDF_RDF)
    if rdf is None:
        rdf = next(root.iter(_RDF_RDF), None)
    if rdf is None:
        return packet

    for description in rdf.findall(_RDF_DESCRIPTION):
        # Abbreviated attribute form: properties expressed as attributes.
        for attr_key, attr_value in description.attrib.items():
            attr_uri, attr_local = _split_qn(attr_key)
            if attr_uri in (_RDF_URI, _XML_URI) or not attr_local:
                continue
            packet.add(
                XmpField(
                    prefix=uri_to_prefix.get(attr_uri, ""),
                    name=attr_local,
                    namespace_uri=attr_uri,
                    value=attr_value,
                )
            )
        # Element form.
        for child in description:
            prop = _parse_property(child, uri_to_prefix)
            if prop is not None:
                packet.add(prop)

    return packet


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

# NOTE: the BOM bytes are written verbatim into the ``begin`` attribute to match
# the historical PDF/A packet exactly (see tests in test_pdfa_conversion.py).
_XPACKET_BEGIN = '<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>'
_XPACKET_END = '<?xpacket end="w"?>'


def _esc_text(value: Any) -> str:
    return (
        str("" if value is None else value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _esc_attr(value: Any) -> str:
    return _esc_text(value).replace('"', "&quot;")


def _as_field(entry: XmpField | XmpArray | XmpProperty) -> XmpField | None:
    """Normalize a packet entry to the :class:`XmpField` to serialize."""
    if isinstance(entry, XmpProperty):
        fld = entry.field
        # Fold a single xml:lang qualifier into the field for round-tripping.
        if fld.language is None:
            for qual in entry.qualifiers:
                if qual.name == "lang":
                    fld.language = str(qual.value)
                    break
        return fld
    if isinstance(entry, XmpField):
        return entry
    return None


def _entry_qualifiers(entry: XmpField | XmpArray | XmpProperty) -> list[XmpField]:
    """Return the qualifiers to serialize for *entry* (excluding ``xml:lang``).

    An ``xml:lang`` qualifier is folded into the field language by
    :func:`_as_field`, so it is not re-emitted as a separate qualifier.
    """
    if isinstance(entry, XmpProperty):
        return [qual for qual in entry.qualifiers if qual.name != "lang"]
    if isinstance(entry, XmpField):
        return [qual for qual in entry.qualifiers if qual.name != "lang"]
    return []


def _resolve_uri(
    prefix: str, uri: str, provider: XmpNamespaceProvider | None
) -> str:
    """Resolve the namespace URI for a (prefix, uri) pair, best-effort."""
    if uri:
        return uri
    candidate = STANDARD_XMP_NAMESPACES.get(prefix)
    if candidate:
        return candidate
    if provider is not None:
        resolved = provider.get_uri(prefix)
        if resolved:
            return resolved
    return ""


def _resolve_prefix(
    prefix: str, uri: str, provider: XmpNamespaceProvider | None
) -> str:
    """Resolve the namespace prefix for a (prefix, uri) pair, best-effort."""
    if prefix:
        return prefix
    if uri:
        if provider is not None:
            resolved = provider.get_prefix(uri)
            if resolved:
                return resolved
        return _STANDARD_URI_TO_PREFIX.get(uri, "")
    return ""


def _render_value(value: Any, provider: XmpNamespaceProvider | None) -> str:
    """Render the inner XML of a property/member value (no enclosing element).

    Handles arrays, structured values, and simple text recursively.
    """
    if isinstance(value, XmpArray):
        container = _ARRAY_CONTAINER.get(value.kind, "rdf:Bag")
        lis = "".join(_render_li(item, provider) for item in value.items)
        return f"<{container}>{lis}</{container}>"
    if isinstance(value, XmpStruct):
        return _render_struct_members(value, provider)
    return _esc_text(value)


def _render_li(item: XmpField, provider: XmpNamespaceProvider | None) -> str:
    """Render a single ``rdf:li`` (simple, URI, qualified, array, or struct item)."""
    lang = f' xml:lang="{_esc_attr(item.language)}"' if item.language else ""
    quals = [q for q in item.qualifiers if q.name != "lang"]
    if quals:
        value_xml = _render_rdf_value(item, provider)
        quals_xml = _render_struct_members(XmpStruct(fields=quals), provider)
        return (
            f'<rdf:li{lang} rdf:parseType="Resource">{value_xml}{quals_xml}</rdf:li>'
        )
    if item.is_uri and isinstance(item.value, str):
        return f'<rdf:li{lang} rdf:resource="{_esc_attr(item.value)}"/>'
    if isinstance(item.value, XmpStruct):
        members = _render_struct_members(item.value, provider)
        return f'<rdf:li{lang} rdf:parseType="Resource">{members}</rdf:li>'
    return f"<rdf:li{lang}>{_render_value(item.value, provider)}</rdf:li>"


def _render_struct_members(
    struct: XmpStruct, provider: XmpNamespaceProvider | None
) -> str:
    """Render the member elements of a struct (inline, no enclosing element)."""
    parts = []
    for member in struct.fields:
        m_prefix = _resolve_prefix(member.prefix, member.namespace_uri, provider)
        member_quals = [q for q in member.qualifiers if q.name != "lang"]
        parts.append(_render_property(m_prefix, member, "", provider, member_quals))
    return "".join(parts)


def _render_rdf_value(fld: XmpField, provider: XmpNamespaceProvider | None) -> str:
    """Render an ``rdf:value`` element (the main value of a qualified property)."""
    if fld.is_uri and isinstance(fld.value, str):
        return f'<rdf:value rdf:resource="{_esc_attr(fld.value)}"/>'
    if isinstance(fld.value, XmpStruct):
        members = _render_struct_members(fld.value, provider)
        return f'<rdf:value rdf:parseType="Resource">{members}</rdf:value>'
    return f"<rdf:value>{_render_value(fld.value, provider)}</rdf:value>"


def _render_property(
    prefix: str,
    fld: XmpField,
    indent: str,
    provider: XmpNamespaceProvider | None,
    qualifiers: list[XmpField] = (),  # type: ignore[assignment]
) -> str:
    qname = f"{prefix}:{fld.name}" if prefix else fld.name
    lang = f' xml:lang="{_esc_attr(fld.language)}"' if fld.language else ""
    if qualifiers:
        # value + qualifiers form: <prop ...><rdf:value>..</rdf:value><qual/>..</prop>
        value_xml = _render_rdf_value(fld, provider)
        quals_xml = _render_struct_members(XmpStruct(fields=list(qualifiers)), provider)
        return (
            f'{indent}<{qname}{lang} rdf:parseType="Resource">'
            f"{value_xml}{quals_xml}</{qname}>"
        )
    if fld.is_uri and isinstance(fld.value, str):
        return f'{indent}<{qname}{lang} rdf:resource="{_esc_attr(fld.value)}"/>'
    if isinstance(fld.value, XmpStruct):
        members = _render_struct_members(fld.value, provider)
        return f'{indent}<{qname}{lang} rdf:parseType="Resource">{members}</{qname}>'
    return f"{indent}<{qname}{lang}>{_render_value(fld.value, provider)}</{qname}>"


def _collect_field_ns(
    fld: XmpField, provider: XmpNamespaceProvider | None, acc: dict[str, str]
) -> None:
    """Collect the namespace of *fld* and recurse into its value and qualifiers."""
    f_prefix = _resolve_prefix(fld.prefix, fld.namespace_uri, provider)
    f_uri = _resolve_uri(f_prefix, fld.namespace_uri, provider)
    if f_prefix and f_uri:
        acc.setdefault(f_prefix, f_uri)
    _collect_member_ns(fld.value, provider, acc)
    for qual in fld.qualifiers:
        _collect_field_ns(qual, provider, acc)


def _collect_member_ns(
    value: Any,
    provider: XmpNamespaceProvider | None,
    acc: dict[str, str],
) -> None:
    """Collect ``prefix -> uri`` for namespaces used inside struct/array values.

    Top-level property prefixes are declared on their ``rdf:Description`` by the
    serializer; struct *members* (e.g. ``stDim:``/``stEvt:``) and qualifiers
    introduce extra prefixes that must also be declared, gathered here
    recursively.
    """
    if isinstance(value, XmpStruct):
        for member in value.fields:
            _collect_field_ns(member, provider, acc)
    elif isinstance(value, XmpArray):
        for item in value.items:
            _collect_member_ns(item.value, provider, acc)
            for qual in item.qualifiers:
                _collect_field_ns(qual, provider, acc)


def serialize_xmp(
    packet: XmpPacket,
    *,
    encoding: str = "utf-8",
    pretty: bool = True,
    with_wrapper: bool = True,
) -> bytes:
    """Serialize an :class:`XmpPacket` to a well-formed XMP packet.

    Properties are grouped into one ``rdf:Description`` per namespace prefix (in
    first-seen order). When *with_wrapper* is true the output is framed with the
    ``<?xpacket?>`` processing instructions.
    """
    provider = packet.namespace_provider

    def uri_for(prefix: str, uri: str) -> str:
        return _resolve_uri(prefix, uri, provider)

    def prefix_for(prefix: str, uri: str) -> str:
        return _resolve_prefix(prefix, uri, provider)

    # Group rendered properties by prefix, preserving first-seen prefix order.
    groups: dict[str, list[str]] = {}
    group_uri: dict[str, str] = {}
    # Extra namespaces introduced by struct members, keyed by group prefix.
    group_member_ns: dict[str, dict[str, str]] = {}
    prop_indent = "      " if pretty else ""
    for entry in packet.fields:
        fld = _as_field(entry)
        if fld is None:
            continue
        prefix = prefix_for(fld.prefix, fld.namespace_uri)
        uri = uri_for(prefix, fld.namespace_uri)
        groups.setdefault(prefix, [])
        group_uri.setdefault(prefix, uri)
        if uri and not group_uri[prefix]:
            group_uri[prefix] = uri
        qualifiers = _entry_qualifiers(entry)
        groups[prefix].append(
            _render_property(prefix, fld, prop_indent, provider, qualifiers)
        )
        acc = group_member_ns.setdefault(prefix, {})
        _collect_member_ns(fld.value, provider, acc)
        if qualifiers:
            _collect_member_ns(XmpStruct(fields=qualifiers), provider, acc)

    descriptions: list[str] = []
    for prefix, props in groups.items():
        uri = group_uri.get(prefix, "")
        # Declare the group prefix plus any namespaces used by struct members.
        ns_pairs: list[tuple[str, str]] = []
        if prefix and uri:
            ns_pairs.append((prefix, uri))
        for member_prefix, member_uri in group_member_ns.get(prefix, {}).items():
            if member_prefix in (prefix, "rdf", "xml"):
                continue
            ns_pairs.append((member_prefix, member_uri))
        if ns_pairs:
            sep = "\n        " if pretty else " "
            xmlns = "".join(
                f'{sep}xmlns:{p}="{_esc_attr(u)}"' for p, u in ns_pairs
            )
        else:
            xmlns = ""
        if pretty:
            body = "\n".join(props)
            descriptions.append(
                f'    <rdf:Description rdf:about=""{xmlns}>\n'
                f"{body}\n"
                f"    </rdf:Description>"
            )
        else:
            descriptions.append(
                f'<rdf:Description rdf:about=""{xmlns}>{"".join(props)}'
                f"</rdf:Description>"
            )

    if pretty:
        rdf_body = "\n".join(descriptions)
        meta = (
            '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
            f'  <rdf:RDF xmlns:rdf="{_RDF_URI}">\n'
            f"{rdf_body}\n"
            "  </rdf:RDF>\n"
            "</x:xmpmeta>"
        )
    else:
        meta = (
            '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
            f'<rdf:RDF xmlns:rdf="{_RDF_URI}">'
            f'{"".join(descriptions)}'
            "</rdf:RDF></x:xmpmeta>"
        )

    if with_wrapper:
        document = f"{_XPACKET_BEGIN}\n{meta}\n{_XPACKET_END}"
    else:
        document = meta
    return document.encode(encoding)


# ---------------------------------------------------------------------------
# Info dictionary <-> XMP synchronisation
# ---------------------------------------------------------------------------

_DC_URI = STANDARD_XMP_NAMESPACES["dc"]
_PDF_URI = STANDARD_XMP_NAMESPACES["pdf"]
_XMP_URI = STANDARD_XMP_NAMESPACES["xmp"]

# Mapping between document /Info keys and XMP properties (ISO 32000-1 §14.3.3 /
# the XMP specification). ``kind`` selects the XMP value shape:
#   "text" simple text · "alt" language alternative · "seq" ordered array ·
#   "date" simple text holding an ISO-8601 date (PDF date in /Info).
_INFO_XMP_FIELDS: tuple[tuple[str, str, str, str, str], ...] = (
    ("Title", "dc", _DC_URI, "title", "alt"),
    ("Author", "dc", _DC_URI, "creator", "seq"),
    ("Subject", "dc", _DC_URI, "description", "alt"),
    ("Keywords", "pdf", _PDF_URI, "Keywords", "text"),
    ("Creator", "xmp", _XMP_URI, "CreatorTool", "text"),
    ("Producer", "pdf", _PDF_URI, "Producer", "text"),
    ("CreationDate", "xmp", _XMP_URI, "CreateDate", "date"),
    ("ModDate", "xmp", _XMP_URI, "ModifyDate", "date"),
)

_PDF_DATE_RE = re.compile(
    r"(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?(.*)$"
)
_ISO_DATE_RE = re.compile(
    r"(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?"
    r"(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?"
    r"([Zz]|[+\-]\d{2}:?\d{2})?"
)


def _pdf_tz_to_iso(rest: str) -> str:
    """Convert a PDF date trailing timezone (``Z`` / ``+HH'mm'``) to ISO form."""
    rest = rest.strip()
    if not rest:
        return ""
    if rest[0] in "Zz":
        return "Z"
    if rest[0] in "+-":
        digits = re.findall(r"\d{2}", rest)
        if not digits:
            return ""
        hours = digits[0]
        minutes = digits[1] if len(digits) > 1 else "00"
        return f"{rest[0]}{hours}:{minutes}"
    return ""


def pdf_date_to_iso8601(value: str) -> str:
    """Convert a PDF date (``D:YYYYMMDDHHmmSS+HH'mm'``) to an ISO-8601 string.

    Returns ``""`` when *value* is empty or not recognisable as a PDF date.
    Missing components default to the start of their range (month/day ``01``).
    """
    if not value:
        return ""
    text = value.strip()
    if text[:2].upper() == "D:":
        text = text[2:]
    match = _PDF_DATE_RE.match(text)
    if not match:
        return ""
    year, month, day, hh, mm, ss, rest = match.groups()
    iso = f"{year}-{month or '01'}-{day or '01'}"
    if hh is not None:
        iso += f"T{hh}:{mm or '00'}:{ss or '00'}"
        iso += _pdf_tz_to_iso(rest or "")
    return iso


def iso8601_to_pdf_date(value: str) -> str:
    """Convert an ISO-8601 date/time to a PDF date (``D:YYYYMMDDHHmmSS+HH'mm'``).

    Returns ``""`` when *value* is empty or not recognisable.
    """
    if not value:
        return ""
    match = _ISO_DATE_RE.match(value.strip())
    if not match:
        return ""
    year, month, day, hh, mm, ss, tz = match.groups()
    out = f"D:{year}{month or '01'}{day or '01'}"
    if hh is not None:
        out += f"{hh}{mm}{ss or '00'}"
        if tz in ("Z", "z"):
            out += "Z"
        elif tz:
            digits = tz.replace(":", "")
            out += f"{digits[0]}{digits[1:3]}'{digits[3:5] or '00'}'"
    return out


def _set_field(
    packet: XmpPacket, prefix: str, uri: str, name: str, value: Any
) -> XmpField:
    """Set (replacing) or add the simple/array property ``prefix:name``."""
    existing = packet.get(prefix, name) or packet.get(uri, name)
    if existing is not None:
        existing.value = value
        if not existing.prefix:
            existing.prefix = prefix
        if not existing.namespace_uri:
            existing.namespace_uri = uri
        return existing
    fld = XmpField(prefix=prefix, name=name, namespace_uri=uri, value=value)
    packet.add(fld)
    return fld


def info_to_xmp(
    info: Mapping[str, str], packet: XmpPacket | None = None
) -> XmpPacket:
    """Populate XMP properties from a document ``/Info`` dictionary.

    Maps the standard ``/Info`` keys (``Title``/``Author``/``Subject``/
    ``Keywords``/``Creator``/``Producer``/``CreationDate``/``ModDate``) onto
    their XMP equivalents (``dc:title``/``dc:creator``/``dc:description``/
    ``pdf:Keywords``/``xmp:CreatorTool``/``pdf:Producer``/``xmp:CreateDate``/
    ``xmp:ModifyDate``), converting PDF dates to ISO-8601. Existing properties
    are overwritten; unrelated properties are left untouched. When *packet* is
    ``None`` a fresh packet is created. Returns the (mutated) packet.
    """
    if packet is None:
        packet = XmpPacket(namespace_provider=XmpNamespaceProvider())
    for info_key, prefix, uri, name, kind in _INFO_XMP_FIELDS:
        raw = info.get(info_key)
        if raw is None or str(raw) == "":
            continue
        text = str(raw)
        if kind == "date":
            _set_field(packet, prefix, uri, name, pdf_date_to_iso8601(text) or text)
        elif kind == "alt":
            _set_field(
                packet,
                prefix,
                uri,
                name,
                XmpArray(kind="Alt", items=[XmpField(value=text, language="x-default")]),
            )
        elif kind == "seq":
            _set_field(
                packet,
                prefix,
                uri,
                name,
                XmpArray(kind="Seq", items=[XmpField(value=text)]),
            )
        else:  # "text"
            _set_field(packet, prefix, uri, name, text)
    return packet


def _xmp_field_to_info_text(fld: XmpField, kind: str) -> str:
    """Reduce an XMP field value to the plain text stored in ``/Info``."""
    value = fld.value
    if isinstance(value, XmpArray):
        if value.kind == "Alt":
            for item in value.items:
                if item.language in (None, "", "x-default"):
                    return str(item.value)
            return str(value.items[0].value) if value.items else ""
        return ", ".join(str(item.value) for item in value.items)
    if isinstance(value, XmpStruct):
        return ""  # structured values have no flat /Info representation
    text = "" if value is None else str(value)
    if kind == "date" and text:
        return iso8601_to_pdf_date(text) or text
    return text


def xmp_to_info(packet: XmpPacket) -> dict[str, str]:
    """Extract ``/Info``-equivalent fields from an XMP packet.

    The inverse of :func:`info_to_xmp`: returns a ``dict`` of ``/Info`` keys
    for every mapped XMP property present, converting ISO-8601 dates back to
    PDF date strings and collapsing ``dc:title``/``dc:description`` language
    alternatives (preferring ``x-default``) and the ``dc:creator`` sequence.
    """
    info: dict[str, str] = {}
    for info_key, prefix, uri, name, kind in _INFO_XMP_FIELDS:
        fld = packet.get(prefix, name) or packet.get(uri, name)
        if fld is None:
            continue
        text = _xmp_field_to_info_text(fld, kind)
        if text:
            info[info_key] = text
    return info
