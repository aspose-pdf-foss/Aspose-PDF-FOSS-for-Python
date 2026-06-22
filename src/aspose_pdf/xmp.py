"""Public XMP compatibility types for the prerelease package.

The data model (:class:`XmpField`, :class:`XmpArray`, :class:`XmpProperty`,
:class:`XmpPacket`), the namespace table, and the XMP packet
:func:`parse`/:func:`serialize` helpers live in
:mod:`aspose_pdf.engine.data.xmp` and are re-exported here as the public surface.

:class:`NamespaceProvider` is a working bidirectional resolver between XMP
namespace prefixes and URIs, preloaded with the standard XMP namespaces (see
:data:`STANDARD_XMP_NAMESPACES`) and accepting custom mappings.
"""

from __future__ import annotations

from aspose_pdf.engine.data.xmp import (
    STANDARD_XMP_NAMESPACES,
    XmpArray,
    XmpField,
    XmpNamespaceProvider,
    XmpPacket,
    XmpProperty,
    XmpStruct,
    info_to_xmp,
    iso8601_to_pdf_date,
    parse_xmp,
    pdf_date_to_iso8601,
    serialize_xmp,
    xmp_to_info,
)

__all__ = [
    "STANDARD_XMP_NAMESPACES",
    "NamespaceProvider",
    "XmpArray",
    "XmpField",
    "XmpPacket",
    "XmpProperty",
    "XmpStruct",
    "info_to_xmp",
    "iso8601_to_pdf_date",
    "parse",
    "pdf_date_to_iso8601",
    "serialize",
    "xmp_to_info",
]


class NamespaceProvider(XmpNamespaceProvider):
    """Resolve XMP namespace prefixes and URIs.

    Inherits the bidirectional registry from
    :class:`aspose_pdf.engine.data.xmp.XmpNamespaceProvider` (preloaded with
    the standard XMP namespaces and supporting :meth:`register`), and exposes
    :meth:`get_namespace_uri` as the public prefix -> URI accessor.
    """

    def get_namespace_uri(self, prefix: str) -> str | None:
        """Return the namespace URI bound to *prefix*, or ``None``."""
        return self.get_uri(prefix)


def parse(data: str | bytes, *, provider: XmpNamespaceProvider | None = None) -> XmpPacket:
    """Parse an XMP packet (bytes or text) into an :class:`XmpPacket`.

    See :func:`aspose_pdf.engine.data.xmp.parse_xmp`.
    """
    return parse_xmp(data, provider=provider)


def serialize(packet: XmpPacket, **kwargs: object) -> bytes:
    """Serialize an :class:`XmpPacket` into XMP packet bytes.

    See :func:`aspose_pdf.engine.data.xmp.serialize_xmp`.
    """
    return serialize_xmp(packet, **kwargs)  # type: ignore[arg-type]
