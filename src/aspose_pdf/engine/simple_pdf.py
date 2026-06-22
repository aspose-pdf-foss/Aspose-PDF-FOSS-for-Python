"""
SimplePdf - Native Python PDF engine for aspose_pdf SDK.
Provides parsing, writing, and manipulation of PDF documents.
"""

from __future__ import annotations
from __future__ import annotations

import hashlib
import re
import struct
import zlib
import logging
import mmap
from collections import namedtuple
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
)

from .encryption import EncryptionUtils
from .signing import SigningUtils
from .filters import StreamDecoder
from .cos import (
    PdfDocument,
    PdfName,
    PdfDictionary,
    PdfArray,
    PdfString,
    PdfIndirectReference,
    PdfNumber,
    PdfStream,
    PdfBoolean,
    AnnotationName,
    annotation_value_to_cos,
)
from .data.xmp import (
    STANDARD_XMP_NAMESPACES,
    XmpArray,
    XmpField,
    XmpPacket,
    parse_xmp,
    serialize_xmp,
)
from . import conformance
from .pdf_parser_cos import PdfCosParser
from .pdf_writer_cos import PdfCosWriter
from .content_stream_parser import (
    ContentStreamParser,
    parse_image_placements_from_content,
)
from .text_edit import redact_text_in_content, replace_text_in_content
from .content_authoring import (
    AuthoredImage,
    build_image_stream,
    build_line_stream,
    build_rectangle_stream,
    build_text_stream,
    prepare_image,
    safe_resource_name,
    wrap_marked_content,
)
from .pdf_matrix import affine_decimal_to_float, image_placement_bbox
from .rename_resources import safe_rename_names
from .incremental_update import IncrementalUpdate
from ..exceptions import (
    AsposePdfException,
    CONTENT_PARSER_RECOVERABLE,
    PDF_OPERATION_ERRORS,
    PDF_STREAM_DECODE_ERRORS,
    PdfParseException,
    PdfValidationException,
    PdfSecurityException,
)
from ..signature import PdfSignature

if TYPE_CHECKING:
    import datetime

    from ..optimization import OptimizationOptions

logger = logging.getLogger("aspose_pdf")


def _trim_der_padding(data: bytes) -> bytes:
    """Trim trailing placeholder padding from a DER blob (e.g. signature
    ``/Contents``) by reading the outer TLV length, so it parses without a
    BER fallback.  Falls back to *data* unchanged when the header is unusable.
    """
    if len(data) < 2 or data[0] == 0:
        return data
    length_octet = data[1]
    if length_octet < 0x80:
        total = 2 + length_octet
    else:
        num = length_octet & 0x7F
        if num == 0 or len(data) < 2 + num:
            return data
        total = 2 + num + int.from_bytes(data[2 : 2 + num], "big")
    return data[:total] if 0 < total <= len(data) else data

def _glyph_name_to_unicode(name: str) -> Optional[int]:
    """Resolve a glyph name to a unicode codepoint without the full AGL.

    Only the algorithmic ``uniXXXX`` (one BMP value) and ``uXXXX[XX]`` forms are
    handled; arbitrary Adobe glyph names return ``None`` so the caller can bail
    rather than guess a mapping.
    """
    if name.startswith("uni") and len(name) == 7:
        try:
            return int(name[3:], 16)
        except ValueError:
            return None
    if name.startswith("u") and 5 <= len(name) <= 7:
        try:
            return int(name[1:], 16)
        except ValueError:
            return None
    return None


# Maximum /First nesting depth for outline trees; deeper chains raise.
OUTLINE_TREE_MAX_DEPTH = 32


def _outline_link_absent(link: Any) -> bool:
    """True if *link* is missing or PDF null (treat as no First/Next).

    Regular objects must not use ``null`` for required outline-item dictionaries;
    absent vs null both terminate sibling chains and skip child extraction.
    """
    if link is None:
        return True
    from .cos import PdfNull

    return isinstance(link, PdfNull)


def _effective_encryption_password(password: Optional[str]) -> Optional[str]:
    """Return a non-empty stripped password, or None if missing/blank."""
    if password is None:
        return None
    s = password.strip()
    return s if s else None


# Regular PDF name characters (ISO 32000 7.3.5): printable ASCII minus
# whitespace and the delimiter set; everything else is written as ``#XX``.
_NAME_DELIMITERS = set(b"()<>[]{}/%#")


def _encode_mime_name(mime: str) -> "PdfName":
    """Encode a MIME media type as a PDF name (``text/plain`` -> ``/text#2Fplain``)."""
    from .cos import PdfName

    out = []
    for byte in str(mime).strip().encode("ascii", "replace"):
        if 0x21 <= byte <= 0x7E and byte not in _NAME_DELIMITERS:
            out.append(chr(byte))
        else:
            out.append(f"#{byte:02X}")
    return PdfName("".join(out))


def _decode_mime_name(name: Any) -> Optional[str]:
    """Decode a MIME media type from a PDF name (``/text#2Fplain`` -> ``text/plain``).

    The inverse of :func:`_encode_mime_name`: the leading ``/`` is dropped and
    ``#XX`` hex escapes are resolved. Returns ``None`` for a missing/empty name.
    """
    from .cos import PdfName

    if not isinstance(name, PdfName):
        return None
    raw = name.name
    if raw.startswith("/"):
        raw = raw[1:]
    if not raw:
        return None
    out = bytearray()
    i, n = 0, len(raw)
    while i < n:
        ch = raw[i]
        if ch == "#" and i + 3 <= n:
            try:
                out.append(int(raw[i + 1 : i + 3], 16))
                i += 3
                continue
            except ValueError:
                pass
        out.append(ord(ch) & 0xFF)
        i += 1
    return out.decode("ascii", "replace") or None


def _format_pdf_date(value: Any) -> Optional[str]:
    """Format *value* as a PDF date string (``D:YYYYMMDDHHmmSS`` + optional zone).

    Accepts a :class:`datetime.datetime`, an already-formatted string (used as
    is), or ``None``.
    """
    if value is None:
        return None
    import datetime as _dt

    if isinstance(value, str):
        return value
    if isinstance(value, _dt.datetime):
        text = value.strftime("D:%Y%m%d%H%M%S")
        offset = value.utcoffset()
        if offset is None:
            return text
        total = int(offset.total_seconds())
        if total == 0:
            return text + "Z"
        sign = "+" if total > 0 else "-"
        total = abs(total)
        return f"{text}{sign}{total // 3600:02d}'{(total % 3600) // 60:02d}'"
    return None


# PDF date (ISO 32000 7.9.4): ``D:YYYYMMDDHHmmSSOHH'mm'`` with every component
# after the year optional and the zone being ``Z``, ``+`` or ``-``.
_PDF_DATE_RE = re.compile(
    r"^(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?"
    r"([Zz]|[+\-]\d{2}'?(?:\d{2}'?)?)?"
)


def _parse_pdf_date(value: Any) -> "Optional[datetime.datetime]":
    """Parse a PDF date string into a :class:`datetime.datetime`.

    The inverse of :func:`_format_pdf_date`: accepts an optional ``D:`` prefix,
    fills missing month/day with ``01`` and missing time fields with ``0``, and
    maps a ``Z`` / ``+HH'mm'`` / ``-HH'mm'`` suffix to a fixed-offset timezone (a
    naive datetime when no zone is present). Returns ``None`` when *value* is not
    a recognisable date string.
    """
    import datetime as _dt

    if not isinstance(value, str):
        return None
    text = value.strip()
    if text[:2].upper() == "D:":
        text = text[2:]
    match = _PDF_DATE_RE.match(text)
    if not match or not match.group(1):
        return None
    year, month, day, hh, mm, ss, tz = match.groups()
    try:
        result = _dt.datetime(
            int(year), int(month or 1), int(day or 1),
            int(hh or 0), int(mm or 0), int(ss or 0),
        )
    except ValueError:
        return None
    if tz:
        if tz in ("Z", "z"):
            result = result.replace(tzinfo=_dt.timezone.utc)
        else:
            digits = tz[1:].replace("'", "")
            offset = _dt.timedelta(
                hours=int(digits[0:2] or 0), minutes=int(digits[2:4] or 0)
            )
            sign = 1 if tz[0] == "+" else -1
            result = result.replace(tzinfo=_dt.timezone(sign * offset))
    return result


def _pdf_string_octets(s: PdfString) -> bytes:
    """Recover PDF string octets from :class:`PdfString`.

    Literal strings are tokenized into Unicode code points U+0000–U+00FF per
    input byte, then stored in :attr:`PdfString.value` as UTF-8. Hex strings
    store raw octets directly in :attr:`PdfString.value`. Unicode ``PdfString``
    values (constructor from ``str``) keep UTF-8 that may contain code points
    above U+00FF, in which case we treat ``value`` as the final octet sequence.
    """
    raw = s.value
    if not raw:
        return b""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return bytes(raw)
    if all(ord(ch) < 256 for ch in text):
        return bytes(ord(ch) for ch in text)
    return bytes(raw)


def decode_pdf_text_string(s: PdfString) -> str:
    """Decode a PDF ``text string`` (literal or hex) to a Unicode filename or path.

    Supports UTF-16BE / UTF-16LE with BOM (PDF 1.7), UTF-8, and Latin-1 fallback.
    """
    octets = _pdf_string_octets(s)
    if not octets:
        return ""
    if len(octets) >= 2 and octets[0:2] == b"\xfe\xff":
        return octets[2:].decode("utf-16-be", errors="replace")
    if len(octets) >= 2 and octets[0:2] == b"\xff\xfe":
        return octets[2:].decode("utf-16-le", errors="replace")
    try:
        return octets.decode("utf-8")
    except UnicodeDecodeError:
        return octets.decode("latin-1", errors="replace")


# RC4 implementation replaced by cryptography library

# ---------------------------------------------------------------------------
# Text extraction types
# ---------------------------------------------------------------------------
TextFragment = namedtuple("TextFragment", ["page_index", "text"])


class TextFragmentCollection:
    """Collection of TextFragment objects."""

    def __init__(self) -> None:
        self._fragments: List[TextFragment] = []

    def add(self, fragment: TextFragment) -> "TextFragmentCollection":
        if fragment is None:
            return self
        self._fragments.append(fragment)
        return self

    def remove(self, fragment: TextFragment) -> None:
        if fragment not in self._fragments:
            raise AsposePdfException("Fragment not found in collection")
        self._fragments.remove(fragment)

    def clear(self) -> None:
        self._fragments.clear()

    def contains(self, fragment: TextFragment) -> bool:
        return fragment in self._fragments

    def item(self, index: int) -> TextFragment:
        return self._fragments[index]

    def __iter__(self):
        return iter(self._fragments)

    def __len__(self) -> int:
        return len(self._fragments)

    def get_enumerator(self):
        return iter(self._fragments)


class TextFragmentAbsorber:
    """Absorber that extracts text fragments from a SimplePdf instance."""

    def __init__(self) -> None:
        self.fragments: List[TextFragment] = []

    def reset(self) -> None:
        """Clear collected fragments."""
        self.fragments.clear()

    def visit(self, pdf: "SimplePdf") -> None:
        """Collect text fragments using the sophisticated ContentStreamParser."""
        self.fragments.clear()
        pdf._page_text_cursor = 0
        while pdf.has_next_page_text():
            page_idx = pdf._page_text_cursor
            try:
                text = pdf.get_next_page_text()
                if text:
                    # Split into fragments by newline
                    for line in text.split("\n"):
                        if line.strip():
                            self.fragments.append(TextFragment(page_idx, line.strip()))
            except CONTENT_PARSER_RECOVERABLE:
                continue

    def remove_all_text(self, pdf: "SimplePdf") -> None:
        """Remove all visible text from PDF by clearing page contents."""
        self.fragments.clear()
        if hasattr(pdf, "page_contents"):
            pdf.page_contents = [b""] * len(pdf.page_contents)

    def apply_for_all_fragments(self, action) -> None:
        """Apply a function to all fragments."""
        for fragment in self.fragments:
            action(fragment)


# ---------------------------------------------------------------------------
# Image placement types
# ---------------------------------------------------------------------------
class ImagePlacement:
    """Represents an image placement on a page."""

    def __init__(self, name: str, page_index: int, data: bytes) -> None:
        self.name = name
        self.page_index = page_index
        self.data = data
        self._hidden = False

    def save(self, path: Union[str, Path]) -> None:
        """Save image to file."""
        Path(path).write_bytes(self.data)

    def replace(self, new_data: bytes) -> None:
        """Replace image data."""
        self.data = new_data

    def hide(self) -> None:
        """Mark image as hidden."""
        self._hidden = True


class ImagePlacementAbsorber:
    """Absorber that finds image placements in a PDF."""

    def __init__(self) -> None:
        self.image_placements: List[ImagePlacement] = []

    def visit(self, pdf: "SimplePdf") -> None:
        """Find all image placements in the PDF."""
        self.image_placements.clear()
        images = getattr(pdf, "images", {})
        page_map = getattr(pdf, "_page_image_map", {})

        for page_idx, img_names in page_map.items():
            for name in img_names:
                if name in images:
                    self.image_placements.append(
                        ImagePlacement(name, page_idx, images[name])
                    )

        # Fallback: if no page map, assign all images to page 0
        if not page_map and images:
            for name, data in images.items():
                self.image_placements.append(ImagePlacement(name, 0, data))


class LazyImageDict(dict):
    """Dictionary that decodes image streams on demand to save memory."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._loaders: Dict[str, Any] = {}

    def add_loader(self, name: str, loader: Any) -> None:
        """Add a lazy loader for an image name."""
        self._loaders[name] = loader

    def __getitem__(self, key: str) -> bytes:
        if key in self._loaders:
            loader = self._loaders.pop(key)
            try:
                data = loader()
            except PDF_STREAM_DECODE_ERRORS as e:
                logger.error(f"Failed to lazy-decode image {key}: {e}")
                data = b""
            super().__setitem__(key, data)
            return data
        return super().__getitem__(key)

    def __setitem__(self, key: str, value: Any) -> None:
        if key in self._loaders:
            self._loaders.pop(key)
        super().__setitem__(key, value)

    def get(self, key: str, default: Any = None) -> Any:
        if key in self or key in self._loaders:
            return self[key]
        return default

    def __contains__(self, key: object) -> bool:
        return super().__contains__(key) or (
            isinstance(key, str) and key in self._loaders
        )

    def __len__(self) -> int:
        return super().__len__() + len(self._loaders)

    def keys(self):
        return set(super().keys()).union(self._loaders.keys())

    def __iter__(self):
        return iter(self.keys())

    def items(self):
        for k in self.keys():
            yield k, self[k]

    def values(self):
        for k in self.keys():
            yield self[k]

    def pop(self, key: str, *args) -> Any:
        if key in self._loaders:
            loader = self._loaders.pop(key)
            try:
                return loader()
            except PDF_STREAM_DECODE_ERRORS as e:
                logger.error(f"Failed to lazy-decode image {key} during pop: {e}")
                return b""
        return super().pop(key, *args)

    def copy(self) -> "LazyImageDict":
        new_dict = LazyImageDict(super().copy())
        new_dict._loaders = dict(self._loaders)
        return new_dict


# ---------------------------------------------------------------------------
# PDF/A helpers
# ---------------------------------------------------------------------------


def _minimal_srgb_icc_profile() -> bytes:
    """Generate a minimal but structurally valid sRGB ICC v2 profile (~448 bytes)."""

    def s15f16(v: float) -> bytes:
        return struct.pack(">i", round(v * 65536))

    def xyz_type(x: float, y: float, z: float) -> bytes:
        return b"XYZ " + b"\x00\x00\x00\x00" + s15f16(x) + s15f16(y) + s15f16(z)

    def curve_type(gamma: float) -> bytes:
        return (
            b"curv"
            + b"\x00\x00\x00\x00"
            + struct.pack(">I", 1)
            + struct.pack(">H", round(gamma * 256))
        )

    def text_type(text: str) -> bytes:
        return b"text\x00\x00\x00\x00" + text.encode("ascii") + b"\x00"

    tags = [
        (b"desc", text_type("sRGB IEC61966-2.1")),
        (b"cprt", text_type("Copyright (c) 1998 Hewlett-Packard Company")),
        (b"wtpt", xyz_type(0.95047, 1.00000, 1.08883)),
        (b"rXYZ", xyz_type(0.43607, 0.22249, 0.01392)),
        (b"gXYZ", xyz_type(0.38515, 0.71687, 0.09708)),
        (b"bXYZ", xyz_type(0.14308, 0.06061, 0.71410)),
        (b"rTRC", curve_type(2.2)),
        (b"gTRC", curve_type(2.2)),
        (b"bTRC", curve_type(2.2)),
    ]

    header_size = 128
    tag_count = len(tags)
    tag_table_size = 4 + tag_count * 12
    offset = header_size + tag_table_size

    tag_entries: List[Tuple[bytes, int, int]] = []
    tag_data_chunks: List[bytes] = []
    for sig, data in tags:
        padded = data + b"\x00" * ((-len(data)) % 4)
        tag_entries.append((sig, offset, len(data)))
        tag_data_chunks.append(padded)
        offset += len(padded)

    tag_data = b"".join(tag_data_chunks)
    total_size = offset

    d50_x = round(0.96420 * 65536)
    d50_y = round(1.00000 * 65536)
    d50_z = round(0.82491 * 65536)

    header = b""
    header += struct.pack(">I", total_size)  # 0-3:   profile size
    header += b"\x00\x00\x00\x00"  # 4-7:   CMM type (none)
    header += b"\x02\x10\x00\x00"  # 8-11:  version 2.1.0
    header += b"mntr"  # 12-15: profile class (monitor)
    header += b"RGB "  # 16-19: data color space
    header += b"XYZ "  # 20-23: PCS
    header += struct.pack(">6H", 2001, 8, 1, 0, 0, 0)  # 24-35: date/time
    header += b"acsp"  # 36-39: file signature
    header += b"MSFT"  # 40-43: primary platform
    header += struct.pack(">I", 0)  # 44-47: profile flags
    header += struct.pack(">I", 0)  # 48-51: device manufacturer
    header += struct.pack(">I", 0)  # 52-55: device model
    header += struct.pack(">Q", 0)  # 56-63: device attributes (8 bytes)
    header += struct.pack(">I", 0)  # 64-67: rendering intent
    header += struct.pack(">i", d50_x)  # 68-71: illuminant X (D50)
    header += struct.pack(">i", d50_y)  # 72-75: illuminant Y
    header += struct.pack(">i", d50_z)  # 76-79: illuminant Z
    header += struct.pack(">I", 0)  # 80-83: profile creator
    header += b"\x00" * 16  # 84-99: profile ID
    header += b"\x00" * 28  # 100-127: reserved

    assert len(header) == 128, f"ICC header must be 128 bytes, got {len(header)}"

    tag_table = struct.pack(">I", tag_count)
    for sig, off, size in tag_entries:
        tag_table += sig + struct.pack(">II", off, size)

    return header + tag_table + tag_data


def _make_pdfa_xmp(level: str, title: str) -> bytes:
    """Return a minimal UTF-8 XMP metadata packet declaring PDF/A conformance.

    Built from the XMP data model and rendered with
    :func:`aspose_pdf.engine.data.xmp.serialize_xmp` so the PDF/A packet shares
    the package's single XMP serializer.
    """
    level_map = {
        "1a": ("1", "A"),
        "1b": ("1", "B"),
        "2a": ("2", "A"),
        "2b": ("2", "B"),
        "2u": ("2", "U"),
        "3a": ("3", "A"),
        "3b": ("3", "B"),
        "3u": ("3", "U"),
    }
    part, conformance = level_map.get(level.lower(), ("1", "B"))
    pdfaid = STANDARD_XMP_NAMESPACES["pdfaid"]
    dc = STANDARD_XMP_NAMESPACES["dc"]
    packet = XmpPacket()
    packet.add(XmpField(prefix="pdfaid", name="part", namespace_uri=pdfaid, value=part))
    packet.add(
        XmpField(
            prefix="pdfaid",
            name="conformance",
            namespace_uri=pdfaid,
            value=conformance,
        )
    )
    packet.add(
        XmpField(
            prefix="dc",
            name="title",
            namespace_uri=dc,
            value=XmpArray(
                kind="Alt",
                items=[XmpField(value=title, language="x-default")],
            ),
        )
    )
    return serialize_xmp(packet)


def _make_pdfua_xmp(title: str) -> bytes:
    """Return a minimal UTF-8 XMP packet declaring PDF/UA-1 conformance.

    Emits ``pdfuaid:part = 1`` plus a ``dc:title``, rendered through the
    package's shared XMP serializer.
    """
    pdfuaid = STANDARD_XMP_NAMESPACES["pdfuaid"]
    dc = STANDARD_XMP_NAMESPACES["dc"]
    packet = XmpPacket()
    packet.add(
        XmpField(prefix="pdfuaid", name="part", namespace_uri=pdfuaid, value="1")
    )
    packet.add(
        XmpField(
            prefix="dc",
            name="title",
            namespace_uri=dc,
            value=XmpArray(
                kind="Alt",
                items=[XmpField(value=title, language="x-default")],
            ),
        )
    )
    return serialize_xmp(packet)


def _normalize_pdfa_level_short(level: str) -> str:
    """Normalize to short form like ``1b``, ``2a``."""
    v = level.strip().lower()
    if v.startswith("pdf/a-"):
        v = v[6:]
    elif v.startswith("pdfa-"):
        v = v[5:]
    return v


def _parse_expected_pdfaid(level_short: str) -> Optional[Tuple[str, str]]:
    """Return ``(part, conformance_letter_upper)`` e.g. ``('1', 'B')``, or *None*."""
    m = re.match(r"^(\d+)([a-z])$", level_short.strip().lower())
    if not m:
        return None
    return m.group(1), m.group(2).upper()


def _extract_xmp_pdfaid_fields(xmp_bytes: bytes) -> Tuple[Optional[str], Optional[str]]:
    """Extract ``pdfaid:part`` and ``pdfaid:conformance`` from an XMP packet (regex)."""
    text = xmp_bytes.decode("utf-8", errors="replace")
    part: Optional[str] = None
    conf: Optional[str] = None
    mp = re.search(r"pdfaid:part\s*>([^<]+)</", text, re.IGNORECASE)
    if mp:
        part = mp.group(1).strip()
    mc = re.search(r"pdfaid:conformance\s*>([^<]+)</", text, re.IGNORECASE)
    if mc:
        conf = mc.group(1).strip().upper()
    if part is None:
        mp = re.search(r"pdfaid:part\s*=\s*[\"']([^\"']+)[\"']", text, re.IGNORECASE)
        if mp:
            part = mp.group(1).strip()
    if conf is None:
        mc = re.search(
            r"pdfaid:conformance\s*=\s*[\"']([^\"']+)[\"']", text, re.IGNORECASE
        )
        if mc:
            conf = mc.group(1).strip().upper()
    return part, conf


def _scan_resources_for_device_colors(
    obj: Any, rgb: List[bool], cmyk: List[bool]
) -> None:
    """Recursively mark ``DeviceRGB`` / ``DeviceGray`` / ``DeviceCMYK`` usage in *resources*."""
    if isinstance(obj, str):
        name = obj.replace("/", "")
        if name in ("DeviceRGB", "DeviceGray"):
            rgb[0] = True
        elif name == "DeviceCMYK":
            cmyk[0] = True
    elif isinstance(obj, dict):
        for v in obj.values():
            _scan_resources_for_device_colors(v, rgb, cmyk)
    elif isinstance(obj, list):
        for it in obj:
            _scan_resources_for_device_colors(it, rgb, cmyk)


def _collect_filter_names(filter_obj: Any, resolve_fn: Any) -> List[str]:
    """Return PDF /Filter name strings without leading slash."""
    filter_obj = resolve_fn(filter_obj)
    names: List[str] = []
    if isinstance(filter_obj, PdfName):
        names.append(filter_obj.name.lstrip("/"))
    elif isinstance(filter_obj, PdfArray):
        for f in filter_obj.items:
            fo = resolve_fn(f)
            if isinstance(fo, PdfName):
                names.append(fo.name.lstrip("/"))
    return names


def _is_standard14_base_font_name(base: Optional[str]) -> bool:
    if not base:
        return False
    bn = base.split("+")[-1]
    key = re.sub(r"[^a-z0-9]", "", bn.lower())
    standard = {
        "helvetica",
        "helveticabold",
        "helveticaoblique",
        "helveticaboldoblique",
        "timesroman",
        "timesbold",
        "timesitalic",
        "timesbolditalic",
        "courier",
        "courierbold",
        "courieroblique",
        "courierboldoblique",
        "symbol",
        "zapfdingbats",
    }
    return key in standard


# ---------------------------------------------------------------------------
# SimplePdf - main document class
# ---------------------------------------------------------------------------
@dataclass
class SimplePdf:
    """
    Native Python PDF document representation.

    Fields:
        pages: List of MediaBox tuples (x0, y0, x1, y1)
        page_contents: List of bytes (content streams)
        images: Dict mapping image name -> image bytes
        metadata: Dict of document metadata
        encrypted: Whether document is encrypted
        password: Document password if encrypted
    """

    pages: List[Tuple[float, float, float, float]] = field(default_factory=list)
    page_contents: List[bytes] = field(default_factory=list)
    images: Dict[str, bytes] = field(default_factory=LazyImageDict)
    metadata: Dict[str, str] = field(default_factory=dict)
    watermark_text: Optional[str] = None
    encrypted: bool = False
    password: Optional[str] = None
    O: Optional[bytes] = None  # noqa: E741  # PDF encryption /O entry
    U: Optional[bytes] = None
    P: int = -4
    encryption_key: Optional[bytes] = None
    encryption_algorithm: str = "RC4"  # RC4, AES-128, AES-256
    signature: Optional[Dict[str, str]] = None
    signing_creds: Optional[Tuple[Any, Any]] = None
    pades: bool = False  # emit a CAdES/PAdES signature (ETSI.CAdES.detached)
    certify_permissions: Optional[int] = None  # DocMDP /P (1/2/3) -> certifying
    extra_certs: Optional[List[Any]] = None  # extra certs embedded for the chain
    timestamp_url: Optional[str] = None  # RFC 3161 TSA URL (online, opt-in)
    timestamp_tsa: Optional[Tuple[Any, Any]] = None  # local (cert, key) TSA
    timestamp_timeout: float = 10.0
    attachments: Dict[str, bytes] = field(default_factory=dict)
    # Optional per-attachment metadata keyed by the same name as ``attachments``:
    # {"mime": str, "description": str, "creation_date": datetime|str,
    #  "mod_date": datetime|str, "compress": bool}. Absent entries use defaults.
    attachment_meta: Dict[str, dict] = field(default_factory=dict)
    # Typed metadata read back from a loaded document's /Filespec + /EmbeddedFile
    # objects, keyed by attachment name: {"mime": str, "description": str,
    # "creation_date": datetime, "mod_date": datetime}. Populated at load; only
    # names that carried metadata appear.
    attachment_read_meta: Dict[str, dict] = field(default_factory=dict)
    fonts: Dict[str, Any] = field(default_factory=dict)
    extgstates: Dict[str, Any] = field(default_factory=dict)
    signatures: List[PdfSignature] = field(default_factory=list)
    pdf_version: str = "1.7"
    file_id: Optional[List[bytes]] = None
    _page_obj_ids: List[int] = field(default_factory=list)
    _content_obj_ids: List[int] = field(default_factory=list)
    _cos_doc: Optional[PdfDocument] = field(default=None, init=False, repr=False)
    # When True, a full COS rewrite (``to_bytes``) packs objects into an
    # object stream and emits a cross-reference stream. Set by ``optimize``.
    _use_object_streams: bool = field(default=False, init=False, repr=False)
    _raw_bytes: Optional[Union[bytes, mmap.mmap]] = field(
        default=None, init=False, repr=False
    )
    _disposed: bool = field(default=False, init=False, repr=False)
    _hidden_images: Set[str] = field(default_factory=set, init=False, repr=False)
    _extracted_text: Optional[str] = field(default=None, init=False, repr=False)
    _image_names: Optional[List[str]] = field(default=None, init=False, repr=False)
    _image_cursor: int = field(default=0, init=False, repr=False)
    _page_text_cursor: int = field(default=0, init=False, repr=False)
    _page_image_map: Dict[int, List[str]] = field(
        default_factory=dict, init=False, repr=False
    )
    _original_page_count: Optional[int] = field(
        default=None, init=False, repr=False
    )  # Track original state
    _original_metadata: Optional[Dict[str, str]] = field(
        default=None, init=False, repr=False
    )  # Track original metadata
    _original_encrypted: Optional[bool] = field(
        default=None, init=False, repr=False
    )  # Track original encryption state
    _image_sizes: Dict[str, Tuple[int, int]] = field(
        default_factory=dict, init=False, repr=False
    )  # Map image name -> (width, height)
    _image_meta: Dict[str, Dict[str, Any]] = field(
        default_factory=dict, init=False, repr=False
    )  # Map image name -> reconstruction metadata (cs/bpc/palette/filter/...)
    _image_matrix_map: Dict[Tuple[int, str], Tuple[float, ...]] = field(
        default_factory=dict, init=False, repr=False
    )  # (page_idx, name) -> matrix
    _image_rect_map: Dict[Tuple[int, str], Tuple[float, float, float, float]] = field(
        default_factory=dict, init=False, repr=False
    )  # (page_idx, name) -> (x,y,w,h)
    _outlines_data: List[Dict] = field(default_factory=list, init=False, repr=False)
    _lazy: bool = field(default=False, init=False, repr=False)
    _page_refs: List[int] = field(default_factory=list, init=False, repr=False)
    _page_cache_valid: bool = field(default=False, init=False, repr=False)
    _xmp_packet: Optional[XmpPacket] = field(default=None, init=False, repr=False)
    _xmp_loaded: bool = field(default=False, init=False, repr=False)
    _xmp_dirty: bool = field(default=False, init=False, repr=False)

    MIN_MMAP_SIZE = 50 * 1024 * 1024  # 50MB

    def __enter__(self) -> "SimplePdf":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.dispose()

    def _ensure_not_disposed(self) -> None:
        if self._disposed:
            raise AsposePdfException("Document has been disposed")

    def _ensure_cos(self) -> None:
        """Initialize a minimal COS document if not already present."""
        if self._cos_doc is not None:
            return

        from .cos import PdfDocument, PdfDictionary, PdfName, PdfArray, PdfNumber

        self._cos_doc = PdfDocument()

        # Minimal PDF structure: Catalog -> Pages
        pages = PdfDictionary(
            {
                PdfName("Type"): PdfName("Pages"),
                PdfName("Count"): PdfNumber(0),
                PdfName("Kids"): PdfArray([]),
            }
        )
        pages_ref = self._cos_doc.register_object(pages)

        root = PdfDictionary(
            {PdfName("Type"): PdfName("Catalog"), PdfName("Pages"): pages_ref}
        )
        root_ref = self._cos_doc.register_object(root)

        self._cos_doc.trailer.mapping[PdfName("Root")] = root_ref

        # Sync existing pages if any (converting simple rects to COS Page objects)
        for i, (rect, content) in enumerate(zip(self.pages, self.page_contents)):
            self._create_cos_page(i, rect, content)

    def _create_cos_page(
        self, index: int, rect: Tuple[float, float, float, float], content: bytes
    ) -> None:
        if self._cos_doc is None:
            return
        from .cos import PdfDictionary, PdfName, PdfArray, PdfNumber, PdfStream

        # Get Pages root
        root_ref = self._cos_doc.trailer.mapping.get(PdfName("Root"))
        root = self._resolve(root_ref)
        pages_ref = root.mapping.get(PdfName("Pages"))
        pages_dict = self._resolve(pages_ref)

        # Create Page object
        page = PdfDictionary(
            {
                PdfName("Type"): PdfName("Page"),
                PdfName("Parent"): pages_ref,
                PdfName("MediaBox"): PdfArray([PdfNumber(v) for v in rect]),
            }
        )

        if content:
            content_stream = PdfStream(mapping={}, content=content)
            content_ref = self._cos_doc.register_object(content_stream)
            page.mapping[PdfName("Contents")] = content_ref
            if hasattr(self, "_content_obj_ids"):
                if index < len(self._content_obj_ids):
                    self._content_obj_ids[index] = content_ref.object_number
                else:
                    self._content_obj_ids.append(content_ref.object_number)

        page_ref = self._cos_doc.register_object(page)

        # Add to Pages kids
        kids = pages_dict.mapping.get(PdfName("Kids"))
        if isinstance(kids, PdfArray):
            if index < len(kids.items):
                kids.items.insert(index, page_ref)
            else:
                kids.items.append(page_ref)

        pages_dict.mapping[PdfName("Count")] = PdfNumber(len(kids.items))

        if hasattr(self, "_page_obj_ids"):
            if index < len(self._page_obj_ids):
                self._page_obj_ids.insert(index, page_ref.object_number)
            else:
                self._page_obj_ids.append(page_ref.object_number)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def _ensure_page_cache(self) -> None:
        """Populate the page reference cache if it's invalid (resolves O(N^2) traversal)."""
        if self._page_cache_valid and self._page_refs:
            return

        if not self._cos_doc:
            self._ensure_cos()

        from .cos import PdfName, PdfDictionary, PdfArray

        root_ref = self._cos_doc.trailer.mapping.get(PdfName("Root"))
        root = self._resolve(root_ref)
        if not root:
            return
        pages_ref = root.mapping.get(PdfName("Pages"))
        if not pages_ref:
            return

        self._page_refs = []

        def traverse(node_ref):
            node = self._resolve(node_ref)
            if not isinstance(node, PdfDictionary):
                return

            node_type = self._resolve(node.mapping.get(PdfName("Type")))
            if isinstance(node_type, PdfName) and node_type.name == "/Page":
                if hasattr(node_ref, "object_number"):
                    self._page_refs.append(node_ref.object_number)
                return

            kids = self._resolve(node.mapping.get(PdfName("Kids")))
            if isinstance(kids, PdfArray):
                for kid in kids.items:
                    traverse(kid)

        traverse(pages_ref)
        self._page_cache_valid = True

    def _get_page_dict(self, page_index: int) -> Optional[Any]:
        """Find the page dictionary for the given index, using cache for O(1) retrieval."""
        if not self._cos_doc:
            if not self.pages:
                return None
            self._ensure_cos()

        self._ensure_page_cache()
        if page_index < 0 or page_index >= len(self._page_refs):
            # Fallback: if cache is empty but we have pages, try to rebuild once
            if not self._page_refs and self.pages:
                self._page_cache_valid = False
                self._ensure_page_cache()

            if page_index < 0 or page_index >= len(self._page_refs):
                return None

        obj_num = self._page_refs[page_index]
        return self._cos_doc.objects.get(obj_num)

    def _get_page_resources(self, page_index: int) -> Dict[str, Any]:
        """Return the resources dictionary for a specific page."""
        page_dict = self._get_page_dict(page_index)
        if page_dict:
            from .cos import PdfName

            resources = self._resolve(page_dict.mapping.get(PdfName("Resources")))
            if resources:
                return self._convert_cos_to_dict(resources)
        return {}

    def _get_inherited_attr(self, page_dict: Any, key: str) -> Any:
        """Resolve an inheritable page attribute, walking up the /Parent chain."""
        from .cos import PdfDictionary, PdfName

        node = page_dict
        seen: set = set()
        while isinstance(node, PdfDictionary) and id(node) not in seen:
            seen.add(id(node))
            val = node.mapping.get(PdfName(key))
            if val is not None:
                return self._resolve(val)
            node = self._resolve(node.mapping.get(PdfName("Parent")))
        return None

    def get_page_rotation(self, page_index: int) -> int:
        """Return the page's clockwise rotation in degrees (0/90/180/270)."""
        page = self._get_page_dict(page_index)
        if page is None:
            return 0
        value = self._get_number(self._get_inherited_attr(page, "Rotate"))
        if value is None:
            return 0
        return int(value) % 360

    def set_page_rotation(self, page_index: int, degrees: int) -> None:
        """Set the page's /Rotate, normalised to ``[0, 360)``."""
        from .cos import PdfName, PdfNumber

        page = self._get_page_dict(page_index)
        if page is None:
            return
        page.mapping[PdfName("Rotate")] = PdfNumber(int(degrees) % 360)

    def get_page_crop_box(self, page_index: int):
        """Return the page's /CropBox as an ``(x0, y0, x1, y1)`` tuple, or ``None``."""
        from .cos import PdfArray

        page = self._get_page_dict(page_index)
        if page is None:
            return None
        box = self._get_inherited_attr(page, "CropBox")
        if isinstance(box, PdfArray) and len(box.items) >= 4:
            try:
                return tuple(float(self._get_number(v) or 0) for v in box.items[:4])
            except (TypeError, ValueError):
                return None
        return None

    def set_page_crop_box(self, page_index: int, rect) -> None:
        """Set the page's /CropBox to *rect* ``(x0, y0, x1, y1)``."""
        from .cos import PdfArray, PdfName, PdfNumber

        page = self._get_page_dict(page_index)
        if page is None:
            return
        page.mapping[PdfName("CropBox")] = PdfArray(
            [PdfNumber(float(v)) for v in rect[:4]]
        )

    def _update_page_count_recursive(self, node_ref: Any, delta: int) -> None:
        """Update /Count in the page tree nodes up to the root (Structural Integrity fix)."""
        from .cos import PdfName, PdfNumber, PdfDictionary

        curr_ref = node_ref
        while curr_ref:
            node = self._resolve(curr_ref)
            if not isinstance(node, PdfDictionary):
                break

            if PdfName("Count") in node.mapping:
                count_obj = node.mapping[PdfName("Count")]
                if isinstance(count_obj, PdfNumber):
                    node.mapping[PdfName("Count")] = PdfNumber(count_obj.value + delta)

            curr_ref = node.mapping.get(PdfName("Parent"))

    def _convert_cos_to_dict(self, obj: Any) -> Any:
        """Convert COS objects to standard Python types recursively."""
        from .cos import (
            PdfName,
            PdfDictionary,
            PdfArray,
            PdfStream,
            PdfString,
            PdfNumber,
            PdfBoolean,
        )

        obj = self._resolve(obj)
        if isinstance(obj, PdfDictionary):
            return {
                k.name.lstrip("/"): self._convert_cos_to_dict(v)
                for k, v in obj.mapping.items()
            }
        elif isinstance(obj, PdfArray):
            return [self._convert_cos_to_dict(v) for v in obj.items]
        elif isinstance(obj, PdfStream):
            # For resources, we usually want the stream dictionary
            res = self._convert_cos_to_dict(obj.mapping)
            # If it's a ToUnicode CMap, we might need the actual data
            # ContentStreamParser._prepare_font_maps handles PdfStream objects too,
            # but let's make it easy by adding 'content' if it's a stream
            if hasattr(obj, "content"):
                res["content"] = obj.content
            elif hasattr(obj, "decode"):  # For StreamDecoder results
                res["content"] = obj.decode()
            return res
        elif isinstance(obj, PdfName):
            return obj.name.lstrip("/")
        elif isinstance(obj, PdfString):
            return obj.value
        elif isinstance(obj, PdfNumber):
            return obj.value
        elif isinstance(obj, PdfBoolean):
            return obj.value
        return obj

    def _resolve(self, obj: Any) -> Any:
        """Dereference an indirect reference, returning the actual object."""
        if isinstance(obj, PdfIndirectReference) and self._cos_doc:
            return self._cos_doc.objects.get(obj.object_number)
        return obj

    def _get_name(self, obj: Any) -> Optional[str]:
        """Return the string value of a PdfName, or None."""
        from .cos import PdfName

        obj = self._resolve(obj)
        if isinstance(obj, PdfName):
            return obj.name.lstrip("/")
        return None

    def _get_number(self, obj: Any) -> Optional[float]:
        """Return the numeric value, or None."""
        from .cos import PdfNumber

        obj = self._resolve(obj)
        if isinstance(obj, PdfNumber):
            return obj.value
        return None

    # ---------------------------------------------------------------------------
    # Factory methods
    # ---------------------------------------------------------------------------
    @classmethod
    def from_file(
        cls, path: Union[str, Path], password: Optional[str] = None
    ) -> "SimplePdf":
        """Load PDF from file path, using memory-mapping for large files."""
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"File not found: {path}")

        file_size = p.stat().st_size
        # Use mmap for files larger than 50MB
        if file_size > cls.MIN_MMAP_SIZE:
            logger.info(
                f"Large file detected ({file_size / 1024 / 1024:.2f} MB). Using memory-mapped processing."
            )
            with open(p, "rb") as f:
                # Note: mmap keeps the file open; we must close it in dispose()
                mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
                return cls.from_bytes(mm, password)
        else:
            data = p.read_bytes()
            return cls.from_bytes(data, password)

    @classmethod
    def from_bytes(cls, data: bytes, password: Optional[str] = None) -> "SimplePdf":
        """Parse PDF from raw bytes using PdfCosParser."""
        if data[0:5] != b"%PDF-":
            raise PdfParseException("Data does not start with a PDF header")

        cos_doc = PdfCosParser(data).parse()

        extractor = CosExtractor(cos_doc, data)

        eff_pwd = _effective_encryption_password(password)
        if extractor.detect_encryption():
            if eff_pwd is None:
                raise PdfSecurityException("Password required for encrypted document")
            if not extractor.encryption_password_allows_access(eff_pwd):
                raise PdfSecurityException("Incorrect password")
            password = eff_pwd

        if extractor.detect_encryption():
            extractor.attach_stream_decryption(password)

        pdf = cls()
        pdf._cos_doc = cos_doc
        pdf._raw_bytes = data

        pdf.pages = extractor.extract_pages()
        pdf.page_contents = extractor.extract_page_contents()
        pdf.images = extractor.extract_images()
        pdf.metadata = extractor.extract_metadata()
        pdf.signature = extractor.extract_signature()
        att_entries = extractor.extract_attachment_entries()
        pdf.attachments = {name: payload for name, payload, _meta in att_entries}
        pdf.attachment_read_meta = {
            name: meta for name, _payload, meta in att_entries if meta
        }
        pdf.signatures = extractor.extract_signatures(data)
        pdf.fonts = extractor.extract_fonts()
        pdf.extgstates = extractor.extract_extgstates()
        pdf._page_obj_ids = getattr(extractor, "_cached_page_obj_ids", [])
        pdf._content_obj_ids = getattr(extractor, "_cached_content_obj_ids", [])
        pdf._page_image_map = extractor.extract_images_per_page()
        pdf._image_sizes = extractor.extract_image_sizes()
        pdf._image_meta = extractor.extract_image_meta()
        pdf._image_matrix_map, pdf._image_rect_map = (
            extractor.extract_image_placements()
        )

        # --- Feature 5 metadata ---
        # PDF version from header (e.g. b"%PDF-1.4\n...")
        try:
            header_window = data[:20]
            eol = header_window.index(b"\n")
            header_line = data[:eol].rstrip()
            if header_line.startswith(b"%PDF-"):
                pdf.pdf_version = header_line[5:].decode("ascii", errors="ignore")
        except (ValueError, UnicodeDecodeError):
            pass

        pdf.file_id = extractor.extract_file_id()
        pdf._outlines_data = extractor.extract_outlines()

        if extractor.detect_encryption():
            pdf.encrypted = True
            pdf.password = password
            pdf.P = extractor.extract_permissions()
            pdf.encryption_key = extractor._stream_decrypt_key
            pdf.encryption_algorithm = extractor._stream_decrypt_algorithm

        # Ensure at least one page
        if not pdf.pages:
            pdf.pages = [(0, 0, 612, 792)]
        if not pdf.page_contents or len(pdf.page_contents) < len(pdf.pages):
            while len(pdf.page_contents) < len(pdf.pages):
                pdf.page_contents.append(b"")

        # Record original state for modification tracking
        pdf._original_page_count = len(pdf.pages)
        pdf._original_metadata = dict(pdf.metadata)
        pdf._original_encrypted = pdf.encrypted

        return pdf

    @classmethod
    def load_from(cls, source: Union[str, Path, bytes]) -> "SimplePdf":
        """Load from file path or bytes."""
        if isinstance(source, (str, Path)):
            return cls.from_file(source)
        elif isinstance(source, (bytes, bytearray)):
            return cls.from_bytes(bytes(source))
        else:
            raise TypeError("source must be a file path or bytes")

    @classmethod
    def from_file_lazy(
        cls, path: Union[str, Path], password: Optional[str] = None
    ) -> "SimplePdf":
        """Open a PDF in streaming/lazy mode for memory-efficient page processing.

        Unlike :meth:`from_file`, this method does **not** decode page content
        streams upfront.  Instead it:

        * memory-maps the file (always, regardless of file size),
        * parses the COS cross-reference table and object table,
        * extracts only page MediaBoxes and stores their COS object IDs,
        * leaves ``page_contents`` empty.

        Page content is decoded on demand via :meth:`get_page_content`.  This
        is particularly useful for large PDFs where only a subset of pages
        needs to be processed, avoiding the cost of decoding every stream.

        Parameters
        ----------
        path:
            File system path to the PDF.
        password:
            Required when the document has an ``/Encrypt`` dictionary (same as
            :meth:`from_bytes`). Whitespace-only values are treated as missing.
            When the encryption dictionary includes ``/U`` and ``/O`` (and
            ``/UE``/``/OE`` for AES-256 style security), the password must
            unlock those values before page metadata is populated. Lazy mode
            still does not guarantee correct stream decryption for all producer
            variants once a password is supplied.

        Returns
        -------
        SimplePdf
            A ``SimplePdf`` instance with ``_lazy=True`` and empty
            ``page_contents``.
        """
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"File not found: {path}")

        f = open(p, "rb")
        try:
            mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        finally:
            f.close()

        parse_ok = False
        try:
            cos_doc = PdfCosParser(mm).parse()
            parse_ok = True
        finally:
            if not parse_ok:
                mm.close()

        extractor = CosExtractor(cos_doc, mm)
        eff_pwd = _effective_encryption_password(password)
        if extractor.detect_encryption():
            if eff_pwd is None:
                mm.close()
                raise PdfSecurityException("Password required for encrypted document")
            if not extractor.encryption_password_allows_access(eff_pwd):
                mm.close()
                raise PdfSecurityException("Incorrect password")
            password = eff_pwd

        if extractor.detect_encryption():
            extractor.attach_stream_decryption(password)

        pdf = cls()
        pdf._cos_doc = cos_doc
        pdf._raw_bytes = mm
        pdf._lazy = True

        # Discovered resource containers
        pdf.images = LazyImageDict()
        pdf.fonts = {}
        pdf.extgstates = {}
        pdf._page_image_map = {}

        pdf.pages = extractor.extract_pages_lazy(
            pdf.images, pdf._page_image_map, pdf.fonts, pdf.extgstates
        )
        pdf._page_obj_ids = list(extractor._page_obj_ids)
        pdf._image_sizes = extractor.extract_image_sizes()
        pdf._image_meta = extractor.extract_image_meta()
        pdf.page_contents = []  # loaded on demand via get_page_content()
        pdf.metadata = extractor.extract_metadata()

        try:
            eol = mm.find(b"\n", 0)
            header_line = bytes(mm[:eol]).rstrip() if eol > 0 else b""
            if header_line.startswith(b"%PDF-"):
                pdf.pdf_version = header_line[5:].decode("ascii", errors="ignore")
        except (ValueError, TypeError, OSError):
            pass

        pdf.file_id = extractor.extract_file_id()
        pdf._outlines_data = extractor.extract_outlines()

        if extractor.detect_encryption():
            pdf.encrypted = True
            pdf.password = password
            pdf.P = extractor.extract_permissions()
            pdf.encryption_key = extractor._stream_decrypt_key
            pdf.encryption_algorithm = extractor._stream_decrypt_algorithm

        if not pdf.pages:
            pdf.pages = [(0, 0, 612, 792)]

        pdf._original_page_count = len(pdf.pages)
        pdf._original_metadata = dict(pdf.metadata)
        pdf._original_encrypted = pdf.encrypted

        logger.info(
            "Lazy-loaded PDF (%d pages, mmap). Page content streams will be "
            "decoded on demand.",
            len(pdf.pages),
        )
        return pdf

    def get_page_content(self, index: int) -> bytes:
        """Return the decoded content stream for the page at *index*.

        In normal (non-lazy) mode this is equivalent to
        ``self.page_contents[index]``.  In lazy mode the content stream is
        decoded from the COS document on demand so that only one page's worth
        of content needs to be in memory at a time.

        Parameters
        ----------
        index:
            Zero-based page index.

        Returns
        -------
        bytes
            Decoded content stream bytes, or ``b""`` if unavailable.
        """
        if index < len(self.page_contents):
            return self.page_contents[index]
        if self._lazy and self._cos_doc and index < len(self._page_obj_ids):
            extractor = CosExtractor(
                self._cos_doc,
                self._raw_bytes,
                stream_decrypt_key=self.encryption_key,
                stream_decrypt_algorithm=self.encryption_algorithm,
            )
            extractor._page_obj_ids = list(self._page_obj_ids)
            return extractor.get_page_content(index)
        return b""

    def iter_page_content_streams(self):
        """Yield decoded content streams for each page, one at a time.

        In normal mode the pre-loaded ``page_contents`` list is iterated.  In
        lazy mode each stream is decoded from the COS document on demand,
        keeping only one page's content in memory at a time.

        Yields
        ------
        bytes
            Decoded content stream for each page in order.
        """
        page_count = len(self.pages)
        if self._lazy and self._cos_doc:
            extractor = CosExtractor(
                self._cos_doc,
                self._raw_bytes,
                stream_decrypt_key=self.encryption_key,
                stream_decrypt_algorithm=self.encryption_algorithm,
            )
            extractor._page_obj_ids = list(self._page_obj_ids)
            for i in range(page_count):
                yield extractor.get_page_content(i)
        else:
            for i in range(page_count):
                if i < len(self.page_contents):
                    yield self.page_contents[i]
                else:
                    yield b""

    @classmethod
    def from_bytes_safe(
        cls, data: bytes, password: Optional[str] = None
    ) -> "SimplePdf":
        """Load PDF with automatic repair on error.

        Unlike from_bytes(), this method attempts to repair
        the PDF if parsing fails, always returning a usable PDF.

        Args:
            data: Raw PDF bytes
            password: Optional password for encrypted PDFs

        Returns:
            SimplePdf instance (potentially repaired)
        """
        try:
            pdf = cls.from_bytes(data, password)
        except PDF_OPERATION_ERRORS as exc:
            # Tolerating parse failure — log at WARNING for operability.
            logger.warning(
                "SimplePdf.from_bytes_safe: eager parse failed; using minimal "
                "fallback document and repair(): %s",
                exc,
            )
            # Try minimal parsing - create empty PDF
            pdf = cls()
            pdf.pages = [(0, 0, 612, 792)]
            pdf.page_contents = [b""]
            pdf._raw_bytes = data

        pdf.repair()
        return pdf

    @classmethod
    def load_cos(cls, source: Union[str, Path, bytes]) -> "SimplePdf":
        """Load PDF using the generic COS parser (preserves all data)."""
        if isinstance(source, (str, Path)):
            p = Path(source)
            if not p.is_file():
                raise FileNotFoundError(f"File not found: {source}")
            data = p.read_bytes()
        elif isinstance(source, (bytes, bytearray)):
            data = bytes(source)
        else:
            raise TypeError("source must be a file path or bytes")

        doc = PdfCosParser(data).parse()
        pdf = cls()
        pdf._cos_doc = doc
        pdf._raw_bytes = data

        # Probe the catalog for /Pages so a malformed root surfaces as a warning.
        try:
            root = doc.trailer.get(PdfName("Root"))
            if isinstance(root, Dict) or hasattr(root, "__getitem__"):
                _ = root[PdfName("Pages")]
        except PDF_OPERATION_ERRORS as exc:
            logger.warning(
                "SimplePdf.load_cos: could not inspect catalog /Pages: %s",
                exc,
            )

        # Add a synthetic blank page when COS loading cannot resolve the page tree.
        if not pdf.pages:
            pdf.pages = [(0, 0, 612, 792)]
            pdf.page_contents = [b""]

        return pdf

    @classmethod
    def merge(cls, *pdfs: "SimplePdf") -> "SimplePdf":
        """Merge multiple PDFs into one, resolving resource name collisions and deduplicating."""
        merged = cls()
        passwords = set()

        # Track used resource names and hashes for deduplication
        all_res_names = set()
        res_data_to_name = {}  # hash -> name

        for pdf_idx, pdf in enumerate(pdfs):
            if not isinstance(pdf, SimplePdf):
                raise TypeError("All items to merge must be SimplePdf instances")

            # 1. Base copy
            _ = len(merged.pages)  # page_offset
            merged.pages.extend(pdf.pages)

            # 2. Handle Resource Collisions (Images, Fonts, etc.)
            name_map = {}

            # Collect all resources from this PDF
            # SimplePdf currently only exposes 'images' and 'attachments'
            # Fonts are hidden in _cos_doc usually.
            # For a truly robust merge, we'd need to parse resources from each page.
            # Here we implement a renaming strategy for all dictionary-based resources we know.

            # Helper for renaming
            def get_safe_name(old_name, data_hash=None):
                if data_hash and data_hash in res_data_to_name:
                    return res_data_to_name[data_hash]

                new_name = old_name
                counter = 1
                while new_name in all_res_names:
                    new_name = f"{old_name}_{pdf_idx}_{counter}"
                    counter += 1

                if data_hash:
                    res_data_to_name[data_hash] = new_name
                all_res_names.add(new_name)
                return new_name

            # Process Images
            for old_name, img_data in pdf.images.items():
                img_hash = hashlib.sha256(img_data).hexdigest()
                new_name = get_safe_name(old_name, img_hash)
                name_map[old_name] = new_name
                merged.images[new_name] = img_data

                if old_name in pdf._image_sizes:
                    merged._image_sizes[new_name] = pdf._image_sizes[old_name]
                if old_name in pdf._image_meta:
                    merged._image_meta[new_name] = pdf._image_meta[old_name]

            # Process Fonts
            for old_name, font_obj in pdf.fonts.items():
                # For fonts, we don't easily have data hash, so we use name/idx
                new_name = get_safe_name(old_name)
                name_map[old_name] = new_name
                merged.fonts[new_name] = font_obj

            # Process ExtGStates
            for old_name, gs_obj in pdf.extgstates.items():
                new_name = get_safe_name(old_name)
                name_map[old_name] = new_name
                merged.extgstates[new_name] = gs_obj

            # 3. Update Content Streams with renamed resources
            for content in pdf.page_contents:
                updated_content = safe_rename_names(content, name_map)
                merged.page_contents.append(updated_content)

            merged.metadata.update(pdf.metadata)
            if pdf.encrypted:
                passwords.add(pdf.password)

        if passwords:
            merged.encrypt(list(passwords)[0])

        return merged

    # ---------------------------------------------------------------------------
    # Signing
    # ---------------------------------------------------------------------------
    def sign(self, signature: PdfSignature, output_path: str) -> None:
        """Sign the PDF document."""
        self._ensure_not_disposed()
        if self._cos_doc and self._cos_doc.trailer.get(PdfName("Sig")):
            raise PdfSecurityException("PDF already has a signature.")
        self.signature = {
            "Reason": signature.reason,
            "ContactInfo": signature.contact_info,
            "Location": signature.location,
        }

    # ---------------------------------------------------------------------------
    # Save / serialize
    # ---------------------------------------------------------------------------
    def save(self, path: Union[str, Path]) -> None:
        self._ensure_not_disposed()
        Path(path).write_bytes(self.to_bytes())

    def save_cos(self, path: Union[str, Path]) -> None:
        """Save PDF using the generic COS writer (preserves all data)."""
        self._ensure_not_disposed()
        if self._cos_doc is None:
            raise AsposePdfException("No COS document loaded (use load_cos)")
        writer = PdfCosWriter(self._cos_doc)
        data = writer.write()
        Path(path).write_bytes(data)

    def _inject_outlines_to_cos(self, outline_items: List[Dict]) -> None:
        """Build outline tree from _outlines_data and add /Outlines to the Catalog."""
        if not outline_items or self._cos_doc is None:
            return

        root_ref = self._cos_doc.trailer.mapping.get(PdfName("Root"))
        root = self._resolve(root_ref)
        if not isinstance(root, PdfDictionary):
            return

        page_obj_ids = getattr(self, "_page_obj_ids", [])
        if not page_obj_ids or len(self.pages) == 0:
            return

        def make_page_ref(page_index: int) -> PdfIndirectReference:
            idx = max(0, min(page_index, len(page_obj_ids) - 1))
            return PdfIndirectReference(page_obj_ids[idx], 0)

        def build_outline_item(
            item: Dict, parent_ref: PdfIndirectReference
        ) -> Optional[PdfIndirectReference]:
            page_idx = item.get("page_index", 0)
            page_ref = make_page_ref(page_idx)
            dest = PdfArray([page_ref, PdfName("Fit")])
            flags = (1 if item.get("is_italic") else 0) | (
                2 if item.get("is_bold") else 0
            )

            outline_dict = PdfDictionary(
                {
                    PdfName("Title"): PdfString(item.get("title", "")),
                    PdfName("Parent"): parent_ref,
                    PdfName("Dest"): dest,
                }
            )
            if flags:
                outline_dict.mapping[PdfName("F")] = PdfNumber(flags)

            children = item.get("children", [])
            child_refs: List[PdfIndirectReference] = []
            for child in children:
                child_ref = build_outline_item(child, PdfIndirectReference(0, 0))
                if child_ref is not None:
                    child_refs.append(child_ref)

            if child_refs:
                outline_dict.mapping[PdfName("First")] = child_refs[0]
                outline_dict.mapping[PdfName("Last")] = child_refs[-1]
                outline_dict.mapping[PdfName("Count")] = PdfNumber(-len(child_refs))

            item_ref = self._cos_doc.register_object(outline_dict)

            if child_refs:
                for i, ref in enumerate(child_refs):
                    obj = self._cos_doc.objects.get(ref.object_number)
                    if isinstance(obj, PdfDictionary):
                        obj.mapping[PdfName("Parent")] = item_ref
                        if i > 0:
                            obj.mapping[PdfName("Prev")] = child_refs[i - 1]
                        if i < len(child_refs) - 1:
                            obj.mapping[PdfName("Next")] = child_refs[i + 1]

            return item_ref

        outline_root = PdfDictionary(
            {
                PdfName("Type"): PdfName("Outlines"),
                PdfName("Count"): PdfNumber(len(outline_items)),
            }
        )
        outline_root_ref = self._cos_doc.register_object(outline_root)

        item_refs: List[PdfIndirectReference] = []
        for item in outline_items:
            ref = build_outline_item(item, outline_root_ref)
            if ref is not None:
                item_refs.append(ref)

        if item_refs:
            outline_root.mapping[PdfName("First")] = item_refs[0]
            outline_root.mapping[PdfName("Last")] = item_refs[-1]
            for i, ref in enumerate(item_refs):
                obj = self._cos_doc.objects.get(ref.object_number)
                if isinstance(obj, PdfDictionary):
                    obj.mapping[PdfName("Parent")] = outline_root_ref
                    if i > 0:
                        obj.mapping[PdfName("Prev")] = item_refs[i - 1]
                    if i < len(item_refs) - 1:
                        obj.mapping[PdfName("Next")] = item_refs[i + 1]

        root.mapping[PdfName("Outlines")] = outline_root_ref

    def _sync_pages_to_cos(self) -> None:
        """Append any Python-level pages that are not yet in the COS document.

        Only runs when the COS document already has a known-good page structure
        (i.e. _page_obj_ids is non-empty, meaning at least one page was successfully
        parsed).  For broken/minimal PDFs with no real page tree the COS root may
        be absent and _create_cos_page would crash; in those cases the COS is
        written as-is, which is the pre-existing behavior.
        """
        if not self._cos_doc:
            return
        cos_page_count = len(getattr(self, "_page_obj_ids", []))
        if cos_page_count == 0:
            # COS has no known pages — page tree may be absent; do not attempt to inject.
            return
        for i in range(cos_page_count, len(self.pages)):
            rect = self.pages[i]
            content = self.page_contents[i] if i < len(self.page_contents) else b""
            self._create_cos_page(i, rect, content)
        self._page_cache_valid = False

    def _sync_metadata_to_cos(self) -> None:
        """Write self.metadata into the COS /Info dictionary before serialization."""
        if not self._cos_doc or not self.metadata:
            return
        info_ref = self._cos_doc.trailer.mapping.get(PdfName("Info"))
        info_dict = self._resolve(info_ref) if info_ref else None
        if not isinstance(info_dict, PdfDictionary):
            info_dict = PdfDictionary()
            new_ref = self._cos_doc.register_object(info_dict)
            self._cos_doc.trailer.mapping[PdfName("Info")] = new_ref
        for k, v in self.metadata.items():
            info_dict.mapping[PdfName(k)] = PdfString(v.encode("utf-8"))

    def _sync_attachments_to_cos(self) -> None:
        """Write ``self.attachments`` into the catalog name tree before save.

        Each ``name -> bytes`` entry is embedded as an ``/EmbeddedFile`` stream
        wrapped in a ``/Filespec`` dictionary, and the document-level
        ``/Names /EmbeddedFiles`` name tree is rebuilt from the mapping (ISO
        32000-1 name trees and embedded file streams).  Optional per-attachment
        metadata in :attr:`attachment_meta` adds the MIME ``/Subtype``, a
        ``/Desc`` description and ``/Params`` creation/modification dates; the
        payload is Flate-compressed when that makes it smaller.
        """
        if not self._cos_doc or not self.attachments:
            return
        catalog = self._resolve(self._cos_doc.trailer.mapping.get(PdfName("Root")))
        if not isinstance(catalog, PdfDictionary):
            return

        # Name trees must be ordered by key, so emit names sorted.
        array_items: List[Any] = []
        for name in sorted(self.attachments):
            data = bytes(self.attachments[name])
            meta = self.attachment_meta.get(name) or {}

            params = PdfDictionary({PdfName("Size"): PdfNumber(len(data))})
            for date_key, meta_key in (("CreationDate", "creation_date"),
                                       ("ModDate", "mod_date")):
                formatted = _format_pdf_date(meta.get(meta_key))
                if formatted:
                    params.mapping[PdfName(date_key)] = PdfString(formatted)

            ef_mapping = {
                PdfName("Type"): PdfName("EmbeddedFile"),
                PdfName("Params"): params,
            }
            mime = meta.get("mime")
            if mime:
                ef_mapping[PdfName("Subtype")] = _encode_mime_name(mime)

            content = data
            if meta.get("compress", True):
                compressed = zlib.compress(data, 9)
                if len(compressed) < len(data):  # never inflate tiny payloads
                    content = compressed
                    ef_mapping[PdfName("Filter")] = PdfName("FlateDecode")

            ef_stream = PdfStream(mapping=ef_mapping, content=content)
            ef_ref = self._cos_doc.register_object(ef_stream)
            filespec_mapping = {
                PdfName("Type"): PdfName("Filespec"),
                PdfName("F"): PdfString(name),
                PdfName("UF"): PdfString(name),
                PdfName("EF"): PdfDictionary(
                    {PdfName("F"): ef_ref, PdfName("UF"): ef_ref}
                ),
                # Required by PDF/A-3 for every embedded file; harmless
                # otherwise and a valid optional Filespec key (ISO 32000-2).
                PdfName("AFRelationship"): PdfName("Unspecified"),
            }
            description = meta.get("description")
            if description:
                filespec_mapping[PdfName("Desc")] = PdfString(description)
            fs_ref = self._cos_doc.register_object(PdfDictionary(filespec_mapping))
            array_items.append(PdfString(name))
            array_items.append(fs_ref)

        embedded = PdfDictionary({PdfName("Names"): PdfArray(array_items)})
        names_dict = self._resolve(catalog.mapping.get(PdfName("Names")))
        if not isinstance(names_dict, PdfDictionary):
            names_dict = PdfDictionary()
            catalog.mapping[PdfName("Names")] = names_dict
        names_dict.mapping[PdfName("EmbeddedFiles")] = embedded

    @property
    def xmp_packet(self) -> XmpPacket:
        """The document's XMP metadata as an :class:`XmpPacket`.

        Lazily parses the catalog ``/Metadata`` stream on first access (an empty
        packet when the document has none).  To persist edits, assign the packet
        back (``pdf.xmp_packet = packet``); in-place edits to the returned packet
        are also detected and written on the next full save.
        """
        self._ensure_not_disposed()
        if not self._xmp_loaded:
            self._xmp_packet = self._load_xmp_packet()
            self._xmp_loaded = True
        return self._xmp_packet  # type: ignore[return-value]

    @xmp_packet.setter
    def xmp_packet(self, value: Optional[XmpPacket]) -> None:
        self._ensure_not_disposed()
        self._xmp_packet = value if value is not None else XmpPacket()
        self._xmp_loaded = True
        self._xmp_dirty = True

    def _decode_cos_stream(self, stream: Any, source_ref: Any = None) -> bytes:
        """Decode a COS stream (decryption + filters) via a ``CosExtractor``."""
        extractor = CosExtractor(
            self._cos_doc,
            self._raw_bytes or b"",
            stream_decrypt_key=self.encryption_key,
            stream_decrypt_algorithm=self.encryption_algorithm,
        )
        return extractor._decode_stream(stream, source_ref)

    def _load_xmp_packet(self) -> XmpPacket:
        """Resolve and parse the catalog ``/Metadata`` XMP stream (best effort)."""
        if self._cos_doc is None:
            return XmpPacket()
        root = self._resolve(self._cos_doc.trailer.mapping.get(PdfName("Root")))
        if not isinstance(root, PdfDictionary):
            return XmpPacket()
        meta_ref = root.mapping.get(PdfName("Metadata"))
        meta = self._resolve(meta_ref)
        if not isinstance(meta, PdfStream):
            return XmpPacket()
        try:
            return parse_xmp(self._decode_cos_stream(meta, meta_ref))
        except Exception:
            logger.warning("Could not parse catalog /Metadata XMP", exc_info=True)
            return XmpPacket()

    def _sync_xmp_to_cos(self) -> None:
        """Write the managed XMP packet into the catalog ``/Metadata`` stream.

        Writes when the packet was explicitly set, or when an accessed packet's
        serialized form differs from the stored one (so in-place edits persist
        without reformatting untouched metadata).
        """
        if self._cos_doc is None or not self._xmp_loaded or self._xmp_packet is None:
            return
        root = self._resolve(self._cos_doc.trailer.mapping.get(PdfName("Root")))
        if not isinstance(root, PdfDictionary):
            return

        serialized = serialize_xmp(self._xmp_packet)
        if not self._xmp_dirty:
            existing_ref = root.mapping.get(PdfName("Metadata"))
            existing = self._resolve(existing_ref)
            if isinstance(existing, PdfStream):
                try:
                    current = serialize_xmp(
                        parse_xmp(self._decode_cos_stream(existing, existing_ref))
                    )
                    if current == serialized:
                        return
                except Exception:
                    pass
            elif not self._xmp_packet.fields:
                return

        xmp_stream = PdfStream(
            content=serialized,
            mapping={
                PdfName("Type"): PdfName("Metadata"),
                PdfName("Subtype"): PdfName("XML"),
                PdfName("Length"): PdfNumber(len(serialized)),
            },
        )
        xmp_ref = self._cos_doc.register_object(xmp_stream)
        root.mapping[PdfName("Metadata")] = xmp_ref

    def to_bytes(self) -> bytes:
        """Serialize PDF to bytes, preserving structure when possible."""
        self._ensure_not_disposed()

        # Encrypted documents must use PdfWriterV0 (it applies encryption to
        # streams); signing is likewise only implemented in PdfWriterV0, so a
        # request to sign must route there even when a COS document exists
        # (otherwise the signature would be silently dropped).
        if self.encrypted or self.signing_creds:
            writer = PdfWriterV0(self)
            return writer.write()

        # Prefer COS writer when we have a COS document - it preserves annotations,
        # outlines, and full structure. Use legacy writer only when _cos_doc is absent
        # (e.g. minimal parse from malformed input).
        if self._cos_doc is None and (self.attachments or self._xmp_dirty):
            self._ensure_cos()
        if self._cos_doc is not None:
            # Ensure outlines are synced before save
            outline_items = getattr(self, "_outlines_data", None)
            if outline_items:
                self._inject_outlines_to_cos(outline_items)

            # Cache is used during write in some cases, ensure it's valid
            self._ensure_page_cache()

            # Ensure /ID is present in the trailer
            id_key = PdfName("ID")
            if id_key not in self._cos_doc.trailer.mapping:
                id1 = EncryptionUtils.generate_file_id()
                id2 = EncryptionUtils.generate_file_id()
                self.file_id = [id1, id2]
                self._cos_doc.trailer.mapping[id_key] = PdfArray(
                    [
                        PdfString(id1),
                        PdfString(id2),
                    ]
                )
            self._sync_pages_to_cos()
            self._sync_metadata_to_cos()
            self._sync_xmp_to_cos()
            self._sync_attachments_to_cos()
            writer = PdfCosWriter(
                self._cos_doc,
                pdf_version=self.pdf_version,
                use_object_streams=self._use_object_streams,
            )
            return writer.write()

        # Fallback: no COS document (e.g. minimal parse) - use legacy writer
        writer = PdfWriterV0(self)
        return writer.write()

    def _reachable_object_ids(self) -> set:
        """Return the object numbers reachable from the trailer.

        Traversal starts at *every* trailer entry (``/Root``, ``/Info``,
        ``/Encrypt``, ``/ID`` …) so document metadata and encryption
        dictionaries are never mistaken for garbage.
        """
        if self._cos_doc is None:
            return set()

        from .cos import PdfDictionary, PdfArray, PdfIndirectReference

        reachable_ids = set()
        visited_literal_ids = set()
        to_visit = list(self._cos_doc.trailer.mapping.values())

        # Traverse the object graph
        while to_visit:
            current = to_visit.pop()

            if isinstance(current, PdfIndirectReference):
                obj_id = current.object_number
                if obj_id not in reachable_ids:
                    reachable_ids.add(obj_id)
                    obj = self._cos_doc.objects.get(obj_id)
                    if obj:
                        to_visit.append(obj)
                continue

            # For non-indirect containers, we must track their Python id() to avoid
            # infinite loops in case of literal circular references.
            if isinstance(current, (PdfDictionary, PdfArray)):
                lit_id = id(current)
                if lit_id in visited_literal_ids:
                    continue
                visited_literal_ids.add(lit_id)

                if isinstance(current, PdfDictionary):
                    for val in current.mapping.values():
                        if isinstance(
                            val, (PdfIndirectReference, PdfDictionary, PdfArray)
                        ):
                            to_visit.append(val)
                elif isinstance(current, PdfArray):
                    for item in current.items:
                        if isinstance(
                            item, (PdfIndirectReference, PdfDictionary, PdfArray)
                        ):
                            to_visit.append(item)

        return reachable_ids

    def garbage_collect(self) -> int:
        """Remove unreachable COS objects from the document.
        Returns the number of objects removed.
        """
        if self._cos_doc is None:
            return 0

        reachable_ids = self._reachable_object_ids()

        # Removal of unreachable indirect objects
        original_count = len(self._cos_doc.objects)
        unreachable_ids = set(self._cos_doc.objects.keys()) - reachable_ids
        for obj_id in unreachable_ids:
            del self._cos_doc.objects[obj_id]

        removed_count = original_count - len(self._cos_doc.objects)
        if removed_count > 0:
            logger.info(
                f"Garbage collection removed {removed_count} unreachable objects."
            )

        return removed_count

    def save_incremental(self, path: Union[str, Path]) -> None:
        """Save PDF using incremental update to preserve signatures.

        This appends changes to the end of the file rather than rewriting,
        which is required for signed PDFs to maintain signature validity.
        """
        self._ensure_not_disposed()
        if self._raw_bytes is None:
            # No original data, use regular save
            self.save(path)
            return

        # Build incremental update
        data = self.to_bytes_incremental()
        Path(path).write_bytes(data)

    def to_bytes_incremental(self) -> bytes:
        """Serialize PDF using incremental update to preserve signatures.

        Returns the original bytes plus any appended changes.
        """
        self._ensure_not_disposed()
        if self._raw_bytes is None:
            return self.to_bytes()

        from .cos import PdfStream, PdfName, PdfNumber

        incr = IncrementalUpdate(self._raw_bytes)
        writer = PdfCosWriter(self._cos_doc) if self._cos_doc else None

        # Track if any modifications actually occurred
        modified = False

        if writer and self._cos_doc:
            # 1. Update modified content streams
            # We compare current page_contents with the original data only if we have the mapping
            for i, content in enumerate(self.page_contents):
                if i < len(self._content_obj_ids) and self._content_obj_ids[i] > 0:
                    obj_id = self._content_obj_ids[i]
                    original_obj = self._cos_doc.objects.get(obj_id)

                    if isinstance(original_obj, PdfStream):
                        # SimplePdf does not track per-page dirty flags, so each
                        # original page stream is rewritten unconditionally.
                        original_obj.content = content
                        original_obj.mapping[PdfName("Length")] = PdfNumber(
                            len(content)
                        )

                        obj_bytes = f"{obj_id} 0 obj\n{writer.serialize_object(original_obj)}\nendobj\n".encode(
                            "latin-1"
                        )
                        incr.add_object(obj_id, obj_bytes)
                        modified = True

            # 2. Update Metadata if changed
            if self.metadata != self._original_metadata:
                info_ref = self._cos_doc.trailer.mapping.get(PdfName("Info"))
                if info_ref:
                    from .cos import PdfIndirectReference, PdfString, PdfDictionary

                    info_id = (
                        info_ref.object_number
                        if isinstance(info_ref, PdfIndirectReference)
                        else 0
                    )
                    if info_id:
                        info_dict = self._cos_doc.objects.get(info_id)
                        if isinstance(info_dict, PdfDictionary):
                            for k, v in self.metadata.items():
                                info_dict.mapping[PdfName(k)] = PdfString(
                                    v.encode("utf-8")
                                )

                            obj_bytes = f"{info_id} 0 obj\n{writer.serialize_object(info_dict)}\nendobj\n".encode(
                                "latin-1"
                            )
                            incr.add_object(info_id, obj_bytes)
                            modified = True

        if not modified:
            return self._raw_bytes

        inc_section = incr.generate()
        if inc_section:
            return self._raw_bytes + inc_section
        return self._raw_bytes

    def _hydrate_image_info(self) -> None:
        """Populate image metadata and placement maps for lazy-loaded documents.

        This performs a full page tree traversal to discover images and their
        placements on pages, using the underlying COS document and raw bytes.
        """
        if not self._lazy or not self._cos_doc or not self._raw_bytes:
            return

        logger.info("Hydrating image information for lazy-loaded PDF.")
        extractor = CosExtractor(
            self._cos_doc,
            self._raw_bytes,
            stream_decrypt_key=self.encryption_key,
            stream_decrypt_algorithm=self.encryption_algorithm,
        )
        # Traverse page tree to find all resources; this populates caches in extractor
        # It also populates _page_obj_ids and _content_obj_ids
        _ = extractor.extract_pages()

        # Update high-level fields
        self.images = extractor.extract_images()
        self._page_image_map = extractor.extract_images_per_page()
        self._image_sizes = extractor.extract_image_sizes()
        self._image_meta = extractor.extract_image_meta()
        self._image_matrix_map, self._image_rect_map = (
            extractor.extract_image_placements()
        )

        # Also pull in fonts and extgstates while we've done the traversal
        self.fonts = extractor.extract_fonts()
        self.extgstates = extractor.extract_extgstates()

        # Update ID lists if they were missing or incomplete
        if not self._page_obj_ids:
            self._page_obj_ids = list(extractor._page_obj_ids)
        if not self._content_obj_ids:
            self._content_obj_ids = list(extractor._content_obj_ids)

        logger.info(
            f"Image hydration complete. Found {len(self.images)} images "
            f"across {len(self._page_image_map)} pages."
        )

    @property
    def supports_incremental_update(self) -> bool:
        """Check if document supports incremental updates."""
        return self._raw_bytes is not None

    def dispose(self) -> None:
        """Release resources, including memory-mapped backing storage.

        Call this (or use the instance as a context manager) after loading via
        :meth:`from_file` / :meth:`from_file_lazy` when ``_raw_bytes`` is an
        ``mmap`` object, so the mapping and its file handle are not leaked.
        """
        if self._disposed:
            return
        if isinstance(self._raw_bytes, mmap.mmap):
            self._raw_bytes.close()
        self._raw_bytes = None
        self.pages.clear()
        self.page_contents.clear()
        self.images.clear()
        self.metadata.clear()
        self._disposed = True
        logger.info("Document resources released.")

    def free_memory(self) -> None:
        """Free memory by clearing caches."""
        self._extracted_text = None
        self._image_names = None

    def close(self) -> None:
        """Close the document, releasing resources."""
        self.dispose()

    def get_next_attachment(self) -> Tuple[str, bytes]:
        """Return the next attachment name and its data, advancing the cursor.

        The attachment iterator is lazily initialized on first call. If no more
        attachments are available a ``StopIteration`` is raised.
        """
        # Initialise iterator if it hasn't been prepared yet
        if self._attachment_names is None:
            self._attachment_names = list(self.attachments.keys())
            self._attachment_cursor = 0
        if self._attachment_cursor >= len(self._attachment_names):
            raise StopIteration("No more attachments")
        name = self._attachment_names[self._attachment_cursor]
        data = self.attachments[name]
        self._attachment_cursor += 1
        return name, data

    # ---------------------------------------------------------------------------
    # Encryption
    # ---------------------------------------------------------------------------
    def encrypt(
        self,
        user_password: str,
        owner_password: str = "",
        permissions: int = -4,
        algorithm: str = "AES-256",
    ) -> None:
        """Enable encryption for the document."""
        self._ensure_not_disposed()
        logger.info(f"Encrypting document with algorithm {algorithm}")
        self.P = permissions
        self._encryption_algorithm = algorithm
        self.password = user_password
        self.encrypted = True
        self.encryption_algorithm = algorithm

        owner_password = owner_password or user_password

        # Generate or use existing file ID for key derivation
        if not hasattr(self, "_file_id") or self._file_id is None:
            self._file_id = EncryptionUtils.generate_file_id()

        # Determine key length and revision based on algorithm
        if algorithm == "AES-256":
            # V5/R6 - 256-bit AES
            key_length = 32
            revision = 6

            # AES-256 R6 uses a very different key derivation (Algorithm 2.A)
            u_val, o_val, ue_val, oe_val, file_key = (
                EncryptionUtils.compute_user_owner_keys_v6(
                    user_password=user_password, owner_password=owner_password
                )
            )
            self.U = u_val
            self.O = o_val
            self.UE = ue_val
            self.OE = oe_val
            self.encryption_key = file_key

            # Encrypt permissions
            self.Perms = EncryptionUtils.encrypt_perms_v6(self.encryption_key, self.P)
        else:
            if algorithm == "AES-128":
                # V4/R4 - 128-bit AES
                key_length = 16
                revision = 4
            else:
                # V2/R3 - RC4 (legacy)
                key_length = 16  # 128-bit RC4
                revision = 3

            # Compute O value per Algorithm 3.3
            self.O = EncryptionUtils.compute_owner_key_v4(
                user_password=user_password,
                owner_password=owner_password,
                key_length=key_length,
                revision=revision,
            )

            # Compute U value and encryption key per Algorithm 3.4/3.5
            self.U, self.encryption_key = EncryptionUtils.compute_user_key_v4(
                password=user_password,
                o_value=self.O,
                p_value=self.P,
                file_id=self._file_id,
                key_length=key_length,
                revision=revision,
            )

        # Store revision for reference during writing
        self._encryption_revision = revision
        self._encryption_key_length = key_length

    def check_pdfa_compliance(self, level: str = "1b") -> List[str]:
        """Return the list of PDF/A compliance **errors** (heuristic).

        Thin wrapper over :meth:`check_pdfa_compliance_detailed` that discards
        the warnings, preserving the historical errors-only return type used by
        :meth:`convert_to_pdfa` and existing callers.

        Args:
            level: PDF/A level to check against (e.g., '1b', '2b', '3b')

        Returns:
            List of non-compliance errors found.
        """
        return self.check_pdfa_compliance_detailed(level)[0]

    def check_pdfa_compliance_detailed(
        self, level: str = "1b"
    ) -> Tuple[List[str], List[str]]:
        """Check the document against PDF/A standards (heuristic).

        Rule-of-thumb structural checks only — suitable for signals, not
        certification. See :class:`~aspose_pdf.pdfa.PdfAValidationResult`
        ``is_heuristic``. Covers encryption, metadata/XMP, fonts, output
        intents and device colour, plus the extended catalog/page/annotation/
        action/transparency rules in :mod:`aspose_pdf.engine.conformance`.

        Args:
            level: PDF/A level to check against (e.g., '1b', '2b', '3b')

        Returns:
            Tuple of ``(errors, warnings)``. Errors break conformance;
            warnings are advisory (e.g. missing annotation appearances).
        """
        self._ensure_not_disposed()
        logger.info("Beginning PDF/A compliance check.")
        problems = []

        # 1. Prohibited: Encryption
        if self.encrypted:
            problems.append("Encryption is prohibited by PDF/A.")

        # 2. Required: Metadata – only validate when the document has been loaded
        # from a file or bytes (i.e., has a live COS structure).  A brand-new
        # in-memory SimplePdf() that hasn't been persisted yet is intentionally
        # skipped here.
        if self._cos_doc is not None:
            title_found = bool(self.metadata.get("Title"))
            if not title_found:
                # Also probe the COS /Info dict directly so that the title set
                # by convert_to_pdfa() (which writes COS objects without
                # touching self.metadata) is recognised.
                info_ref = self._cos_doc.trailer.get(PdfName("Info"))
                info_dict = self._resolve(info_ref)
                if isinstance(info_dict, PdfDictionary):
                    title_obj = self._resolve(info_dict.mapping.get(PdfName("Title")))
                    if isinstance(title_obj, PdfString) and title_obj.value:
                        title_found = True
            if not title_found:
                problems.append("PDF/A requires a Title in metadata.")

        # 3. Prohibited: JavaScript and Actions
        if self._cos_doc:
            try:
                root = self._resolve(self._cos_doc.trailer.get(PdfName("Root")))
                if isinstance(root, PdfDictionary):
                    if PdfName("Names") in root:
                        names = self._resolve(root[PdfName("Names")])
                        if (
                            isinstance(names, PdfDictionary)
                            and PdfName("JavaScript") in names
                        ):
                            problems.append("PDF/A prohibits JavaScript.")
                    if PdfName("OpenAction") in root:
                        problems.append("PDF/A prohibits OpenAction.")
            except PDF_OPERATION_ERRORS:
                pass

        # 4. Prohibited: Audio/Video/Executable attachments
        if self.attachments and level != "3":
            problems.append("PDF/A prohibits embedded files (unless PDF/A-3).")

        # 5. Required: Font embedding, ToUnicode (level A), symbolic-font mapping
        level_norm = _normalize_pdfa_level_short(level)
        if self._cos_doc:
            for i in range(len(self.pages)):
                page_dict = self._get_page_dict(i)
                if not isinstance(page_dict, PdfDictionary):
                    continue
                res = self._resolve(page_dict.get(PdfName("Resources")))
                if isinstance(res, PdfDictionary):
                    fonts = self._resolve(res.get(PdfName("Font")))
                    if isinstance(fonts, PdfDictionary):
                        for font_ref in fonts.mapping.values():
                            font = self._resolve(font_ref)
                            if isinstance(font, PdfDictionary):
                                subtype = self._get_name(font.get(PdfName("Subtype")))
                                if subtype == "Type3":
                                    continue  # Type 3 fonts are self-contained

                                base_name = self._get_name(
                                    font.get(PdfName("BaseFont"))
                                )
                                standard_subset = _is_standard14_base_font_name(
                                    base_name
                                )

                                descriptor = self._resolve(
                                    font.get(PdfName("FontDescriptor"))
                                )
                                if isinstance(descriptor, PdfDictionary):
                                    embedded = any(
                                        PdfName(k) in descriptor
                                        for k in ["FontFile", "FontFile2", "FontFile3"]
                                    )
                                    if not embedded:
                                        problems.append(
                                            f"Font {font.get(PdfName('BaseFont'))} on page {i + 1} is not embedded."
                                        )

                                    flags_val = descriptor.get(PdfName("Flags"))
                                    flags_n = 0
                                    if isinstance(flags_val, PdfNumber):
                                        flags_n = int(flags_val.value)

                                    has_tounicode = PdfName("ToUnicode") in font
                                    has_encoding = (
                                        font.get(PdfName("Encoding")) is not None
                                    )
                                    # Symbolic font: valid PDF/A text mapping needs Encoding or ToUnicode
                                    if (flags_n & 4) != 0 and not (
                                        has_tounicode or has_encoding
                                    ):
                                        problems.append(
                                            f"Symbolic font {font.get(PdfName('BaseFont'))} on page {i + 1} "
                                            "requires an Encoding or ToUnicode map."
                                        )

                                    # Level A: non-standard fonts must be searchable (ToUnicode)
                                    if level_norm.endswith("a") and not standard_subset:
                                        if not has_tounicode:
                                            problems.append(
                                                f"Font {font.get(PdfName('BaseFont'))} on page {i + 1} "
                                                "missing required ToUnicode map for PDF/A level A."
                                            )
                                else:
                                    problems.append(
                                        f"Font {font.get(PdfName('BaseFont'))} on page {i + 1} missing FontDescriptor."
                                    )

        # 6. OutputIntents + device color spaces (content-level)
        has_rgb = False
        has_cmyk = False
        if self._cos_doc:
            rgb_flag: List[bool] = [False]
            cmyk_flag: List[bool] = [False]
            for i in range(len(self.pages)):
                page_resources = self._get_page_resources(i)
                if page_resources:
                    _scan_resources_for_device_colors(
                        page_resources, rgb_flag, cmyk_flag
                    )
                if i < len(self.page_contents):
                    content = self.page_contents[i]
                    if b"/DeviceRGB" in content or b"/DeviceGray" in content:
                        rgb_flag[0] = True
                    if b"/DeviceCMYK" in content:
                        cmyk_flag[0] = True
            has_rgb = rgb_flag[0]
            has_cmyk = cmyk_flag[0]

            root = self._resolve(self._cos_doc.trailer.get(PdfName("Root")))
            if isinstance(root, PdfDictionary):
                oi_ref = root.get(PdfName("OutputIntents"))
                oi = self._resolve(oi_ref)
                if not isinstance(oi, PdfArray) or len(oi.items) == 0:
                    problems.append(
                        "PDF/A: Catalog /OutputIntents with a valid ICC profile is required."
                    )
                if has_rgb or has_cmyk:
                    if not isinstance(oi, PdfArray) or len(oi.items) == 0:
                        problems.append(
                            "PDF/A: DeviceRGB/DeviceCMYK (or DeviceGray) requires "
                            "Catalog OutputIntents with a color profile."
                        )
                    else:
                        has_icc = False
                        for ir in oi.items:
                            intent = self._resolve(ir)
                            if not isinstance(intent, PdfDictionary):
                                continue
                            prof_ref = intent.get(PdfName("DestOutputProfile"))
                            prof = self._resolve(prof_ref)
                            if isinstance(prof, PdfStream) and len(prof.content) >= 64:
                                has_icc = True
                                break
                        if not has_icc:
                            problems.append(
                                "PDF/A: OutputIntents must include DestOutputProfile "
                                "pointing to a valid ICC profile stream when device color spaces are used."
                            )

        # 7. XMP pdfaid fields vs. declared validation level
        if self._cos_doc:
            root = self._resolve(self._cos_doc.trailer.get(PdfName("Root")))
            if isinstance(root, PdfDictionary):
                metadata_ref = root.get(PdfName("Metadata"))
                if not metadata_ref:
                    problems.append(
                        "PDF/A requires an XMP Metadata stream in the Catalog."
                    )
                else:
                    metadata_stream = self._resolve(metadata_ref)
                    if isinstance(metadata_stream, PdfStream):
                        xmp_bytes = metadata_stream.content
                        expected = _parse_expected_pdfaid(level_norm)
                        xm_part, xm_conf = _extract_xmp_pdfaid_fields(xmp_bytes)
                        if expected is None:
                            problems.append(
                                f"Cannot validate XMP pdfaid fields: unrecognized level {level!r}."
                            )
                        else:
                            exp_part, exp_conf = expected
                            if xm_part is None or xm_conf is None:
                                problems.append(
                                    "XMP metadata must declare pdfaid:part and pdfaid:conformance."
                                )
                            else:
                                if xm_part != exp_part:
                                    problems.append(
                                        f"XMP pdfaid:part is {xm_part!r} but validation level "
                                        f"requires part {exp_part!r}."
                                    )
                                if xm_conf != exp_conf:
                                    problems.append(
                                        f"XMP pdfaid:conformance is {xm_conf!r} but validation level "
                                        f"requires {exp_conf!r}."
                                    )

        # 8. Prohibited stream filters: LZWDecode (PDF/A-1) and Crypt (all parts).
        if self._cos_doc:
            is_part1 = level_norm.startswith("1")
            lzw_flagged = False
            crypt_flagged = False
            for obj in self._cos_doc.objects.values():
                if not isinstance(obj, PdfStream):
                    continue
                names = _collect_filter_names(
                    obj.mapping.get(PdfName("Filter")), self._resolve
                )
                if is_part1 and not lzw_flagged and "LZWDecode" in names:
                    problems.append("LZWDecode is prohibited in PDF/A-1.")
                    lzw_flagged = True
                if not crypt_flagged and "Crypt" in names:
                    problems.append("PDF/A prohibits the Crypt stream filter.")
                    crypt_flagged = True
                if crypt_flagged and (lzw_flagged or not is_part1):
                    break

        # 8b. CIDFontType2 fonts must declare /CIDToGIDMap (Identity or a stream).
        if self._cos_doc:
            for obj in self._cos_doc.objects.values():
                if not isinstance(obj, PdfDictionary):
                    continue
                if self._get_name(obj.get(PdfName("Subtype"))) != "CIDFontType2":
                    continue
                if PdfName("CIDToGIDMap") not in obj:
                    base = obj.get(PdfName("BaseFont"))
                    problems.append(
                        f"CIDFontType2 font {base} requires /CIDToGIDMap for PDF/A."
                    )

        # 9. Extended structural rules (catalog actions, AcroForm, annotations,
        #    actions, transparency, optional content, version, file id, ...).
        ext_errors, warnings = conformance.pdfa_extended(self, level_norm)
        problems.extend(ext_errors)

        if problems:
            logger.warning(
                f"PDF/A compliance check failed for level {level}: {len(problems)} issues found."
            )
        else:
            logger.info(f"PDF/A compliance check passed for level {level}.")

        return problems, warnings

    def check_pdfua_compliance(self) -> Tuple[List[str], List[str]]:
        """Inspect catalog-level PDF/UA prerequisites (heuristic only).

        Verifies a *tagged-PDF shell*: ``/StructTreeRoot``, ``/MarkInfo`` with
        ``/Marked true``, a document title shown via ViewerPreferences
        ``/DisplayDocTitle true``, and an XMP ``pdfuaid:part`` declaration;
        recommends ``/Lang``.  This is not PDF/UA-1 certification — see
        :class:`~aspose_pdf.pdfua.PdfUaValidationResult`.
        """
        self._ensure_not_disposed()
        errors: List[str] = []
        warnings: List[str] = []
        if self._cos_doc is None:
            errors.append("No document loaded")
            return errors, warnings
        try:
            root_ref = self._cos_doc.trailer.get(PdfName("Root"))
            root = self._resolve(root_ref)
        except PDF_OPERATION_ERRORS:
            errors.append("Could not resolve document catalog.")
            return errors, warnings
        if not isinstance(root, PdfDictionary):
            errors.append("Invalid document catalog.")
            return errors, warnings

        str_ref = root.get(PdfName("StructTreeRoot"))
        if str_ref is None:
            errors.append("PDF/UA requires Catalog /StructTreeRoot.")
        else:
            struct_root = self._resolve(str_ref)
            if not isinstance(struct_root, PdfDictionary):
                errors.append("Catalog /StructTreeRoot must be a dictionary.")
            elif self._get_name(struct_root.get(PdfName("Type"))) != "StructTreeRoot":
                errors.append(
                    "StructTreeRoot dictionary must have /Type /StructTreeRoot."
                )

        mi_ref = root.get(PdfName("MarkInfo"))
        if mi_ref is None:
            errors.append("PDF/UA requires Catalog /MarkInfo.")
        else:
            mark_info = self._resolve(mi_ref)
            if not isinstance(mark_info, PdfDictionary):
                errors.append("Catalog /MarkInfo must be a dictionary.")
            else:
                marked = self._resolve(mark_info.get(PdfName("Marked")))
                if not isinstance(marked, PdfBoolean) or not marked.value:
                    errors.append("PDF/UA requires MarkInfo /Marked true.")

        if root.get(PdfName("Lang")) is None:
            warnings.append(
                "PDF/UA: Catalog /Lang is recommended for natural language of the document."
            )

        ext_errors, ext_warnings = conformance.pdfua_extended(self)
        errors.extend(ext_errors)
        warnings.extend(ext_warnings)

        return errors, warnings

    def decrypt(self, password: str) -> None:
        """Decrypt the document with password."""
        self._ensure_not_disposed()
        if not self.encrypted:
            return
        if self.password and password != self.password:
            raise PdfSecurityException("Incorrect password")
        if self._cos_doc is not None and self.encryption_key is None:
            probe = CosExtractor(self._cos_doc, self._raw_bytes or b"")
            self.encryption_key = probe.extract_decryption_key(password)
            if self.encryption_key is not None:
                self.encryption_algorithm = (
                    probe.standard_handler_encryption_algorithm()
                )

        if self._lazy and self._cos_doc and self._raw_bytes is not None:
            n = len(self.pages)
            while len(self.page_contents) < n:
                self.page_contents.append(b"")
            for i in range(n):
                ext = CosExtractor(
                    self._cos_doc,
                    self._raw_bytes,
                    stream_decrypt_key=self.encryption_key,
                    stream_decrypt_algorithm=self.encryption_algorithm,
                )
                ext._page_obj_ids = list(self._page_obj_ids)
                self.page_contents[i] = ext.get_page_content(i)

        self.encrypted = False
        self.encryption_key = None

    def add_password(self, password: str) -> None:
        """Add password protection."""
        self._ensure_not_disposed()
        self.encrypt(password)

    def remove_password(self) -> None:
        """Remove password protection."""
        self._ensure_not_disposed()
        self.password = None
        self.encrypted = False
        self.encryption_key = None

    def change_passwords(
        self,
        old_password: str,
        new_user_password: str,
        new_owner_password: Optional[str] = None,
    ) -> None:
        """Change document passwords."""
        self._ensure_not_disposed()
        if self.password and old_password != self.password:
            raise PdfSecurityException("Incorrect old password")
        self.encrypt(new_user_password, new_owner_password)

    # ---------------------------------------------------------------------------
    # Page content authoring
    # ---------------------------------------------------------------------------
    def _validate_page_index(self, page_index: int) -> None:
        if page_index < 0 or page_index >= len(self.pages):
            raise IndexError("Page index out of range.")

    def _materialize_page_contents_for_edit(self) -> None:
        """Decode lazy page contents before in-place content edits."""
        if self._lazy and self._cos_doc:
            extractor = CosExtractor(
                self._cos_doc,
                self._raw_bytes or b"",
                stream_decrypt_key=self.encryption_key,
                stream_decrypt_algorithm=self.encryption_algorithm,
            )
            extractor._page_obj_ids = list(self._page_obj_ids)
            self.page_contents = [
                extractor.get_page_content(i) for i in range(len(self.pages))
            ]
            self._lazy = False

        while len(self.page_contents) < len(self.pages):
            self.page_contents.append(b"")

    def _append_content_to_page(self, page_index: int, content: bytes) -> None:
        self._ensure_not_disposed()
        self._validate_page_index(page_index)
        if not content:
            return
        self._materialize_page_contents_for_edit()
        if self._cos_doc is None:
            self._ensure_cos()

        current = self.page_contents[page_index]
        separator = (
            b"" if not current or current.endswith((b"\n", b"\r", b" ")) else b"\n"
        )
        self.page_contents[page_index] = current + separator + content

        self._append_content_to_cos_page(page_index, content)

    def _append_content_to_cos_page(self, page_index: int, content: bytes) -> None:
        if self._cos_doc is None:
            return
        page = self._get_page_dict(page_index)
        if not isinstance(page, PdfDictionary):
            return

        new_stream = PdfStream(content=content, mapping={})
        new_ref = self._cos_doc.register_object(new_stream)
        contents_key = PdfName("Contents")
        existing_entry = page.mapping.get(contents_key)
        if existing_entry is None:
            page.mapping[contents_key] = new_ref
        else:
            existing_obj = self._resolve(existing_entry)
            if isinstance(existing_obj, PdfArray):
                existing_obj.items.append(new_ref)
            else:
                if isinstance(existing_obj, PdfStream) and not isinstance(
                    existing_entry, PdfIndirectReference
                ):
                    existing_entry = self._cos_doc.register_object(existing_obj)
                page.mapping[contents_key] = PdfArray([existing_entry, new_ref])

        while len(self._content_obj_ids) < len(self.pages):
            self._content_obj_ids.append(0)
        if page_index < len(self._content_obj_ids):
            self._content_obj_ids[page_index] = 0

    def _ensure_direct_page_resources(self, page_index: int) -> PdfDictionary:
        self._ensure_cos()
        page = self._get_page_dict(page_index)
        if not isinstance(page, PdfDictionary):
            raise PdfValidationException("Page dictionary is unavailable.")

        resources_key = PdfName("Resources")
        direct = self._resolve(page.mapping.get(resources_key))
        if isinstance(direct, PdfDictionary):
            return direct

        inherited = self._resolve_resources_cos(page)
        mapping = dict(inherited.mapping) if isinstance(inherited, PdfDictionary) else {}
        resources = PdfDictionary(mapping)
        page.mapping[resources_key] = resources
        return resources

    def _ensure_resource_subdict(
        self, page_index: int, resource_kind: str
    ) -> PdfDictionary:
        resources = self._ensure_direct_page_resources(page_index)
        key = PdfName(resource_kind)
        current = self._resolve(resources.mapping.get(key))
        if isinstance(current, PdfDictionary):
            subdict = PdfDictionary(dict(current.mapping))
        else:
            subdict = PdfDictionary()
        resources.mapping[key] = subdict
        return subdict

    def _unique_resource_name(
        self,
        subdict: PdfDictionary,
        prefix: str,
        requested_name: Optional[str] = None,
    ) -> str:
        requested = safe_resource_name(requested_name, prefix)
        if requested and PdfName(requested) not in subdict.mapping:
            return requested
        counter = 1
        while True:
            candidate = f"{prefix}{counter}"
            if PdfName(candidate) not in subdict.mapping:
                return candidate
            counter += 1

    def _coerce_structure_type(self, tag: Optional[str]) -> Optional[str]:
        if tag is None:
            return None
        name = str(tag).strip().lstrip("/")
        if not name:
            return None
        safe = safe_resource_name(name, "Tag")
        if safe != name:
            raise PdfValidationException(
                "Structure tag must be a simple PDF name, such as 'P' or 'Figure'."
            )
        return name

    def _page_ref_for_structure(self, page_index: int) -> PdfIndirectReference:
        self._ensure_page_cache()
        if page_index < len(self._page_refs) and self._page_refs[page_index] > 0:
            return PdfIndirectReference(self._page_refs[page_index], 0)
        page = self._get_page_dict(page_index)
        obj_num = getattr(page, "_obj_number", None)
        if obj_num:
            return PdfIndirectReference(obj_num, 0)
        if isinstance(page, PdfDictionary):
            ref = self._cos_doc.register_object(page)
            return ref
        raise PdfValidationException("Page dictionary is unavailable.")

    def _ensure_struct_tree_root(self) -> Tuple[PdfDictionary, PdfIndirectReference]:
        self._ensure_cos()
        root = self._resolve(self._cos_doc.trailer.get(PdfName("Root")))
        if not isinstance(root, PdfDictionary):
            raise PdfValidationException("Document catalog is unavailable.")

        struct_entry = root.mapping.get(PdfName("StructTreeRoot"))
        struct_root = self._resolve(struct_entry)
        if not isinstance(struct_root, PdfDictionary):
            struct_root = PdfDictionary(
                {
                    PdfName("Type"): PdfName("StructTreeRoot"),
                    PdfName("K"): PdfArray([]),
                }
            )
            struct_ref = self._cos_doc.register_object(struct_root)
            root.mapping[PdfName("StructTreeRoot")] = struct_ref
        elif isinstance(struct_entry, PdfIndirectReference):
            struct_ref = struct_entry
            struct_root.mapping.setdefault(PdfName("Type"), PdfName("StructTreeRoot"))
            struct_root.mapping.setdefault(PdfName("K"), PdfArray([]))
        else:
            struct_root.mapping.setdefault(PdfName("Type"), PdfName("StructTreeRoot"))
            struct_root.mapping.setdefault(PdfName("K"), PdfArray([]))
            struct_ref = self._cos_doc.register_object(struct_root)
            root.mapping[PdfName("StructTreeRoot")] = struct_ref

        parent_tree = self._resolve(struct_root.mapping.get(PdfName("ParentTree")))
        if not isinstance(parent_tree, PdfDictionary):
            parent_tree = PdfDictionary({PdfName("Nums"): PdfArray([])})
            struct_root.mapping[PdfName("ParentTree")] = parent_tree
        elif not isinstance(self._resolve(parent_tree.get(PdfName("Nums"))), PdfArray):
            parent_tree.mapping[PdfName("Nums")] = PdfArray([])

        mark_info = self._resolve(root.mapping.get(PdfName("MarkInfo")))
        if not isinstance(mark_info, PdfDictionary):
            mark_info = PdfDictionary({})
            root.mapping[PdfName("MarkInfo")] = mark_info
        mark_info.mapping[PdfName("Marked")] = PdfBoolean(True)
        suspects = self._resolve(mark_info.mapping.get(PdfName("Suspects")))
        if isinstance(suspects, PdfBoolean) and suspects.value:
            mark_info.mapping[PdfName("Suspects")] = PdfBoolean(False)

        return struct_root, struct_ref

    def _parent_tree_array_for_page(
        self, struct_root: PdfDictionary, page: PdfDictionary
    ) -> PdfArray:
        parent_tree = self._resolve(struct_root.mapping.get(PdfName("ParentTree")))
        if not isinstance(parent_tree, PdfDictionary):
            parent_tree = PdfDictionary({PdfName("Nums"): PdfArray([])})
            struct_root.mapping[PdfName("ParentTree")] = parent_tree
        nums = self._resolve(parent_tree.mapping.get(PdfName("Nums")))
        if not isinstance(nums, PdfArray):
            nums = PdfArray([])
            parent_tree.mapping[PdfName("Nums")] = nums

        key_obj = self._resolve(page.mapping.get(PdfName("StructParents")))
        if isinstance(key_obj, PdfNumber):
            key = int(key_obj.value)
        else:
            used_keys: List[int] = []
            for item in nums.items[0::2]:
                num = self._resolve(item)
                if isinstance(num, PdfNumber):
                    used_keys.append(int(num.value))
            next_key_obj = self._resolve(struct_root.mapping.get(PdfName("ParentTreeNextKey")))
            next_key = (
                int(next_key_obj.value)
                if isinstance(next_key_obj, PdfNumber)
                else (max(used_keys) + 1 if used_keys else 0)
            )
            key = next_key
            page.mapping[PdfName("StructParents")] = PdfNumber(key)
            struct_root.mapping[PdfName("ParentTreeNextKey")] = PdfNumber(key + 1)

        for i in range(0, len(nums.items) - 1, 2):
            num = self._resolve(nums.items[i])
            if isinstance(num, PdfNumber) and int(num.value) == key:
                arr = self._resolve(nums.items[i + 1])
                if isinstance(arr, PdfArray):
                    return arr
                replacement = PdfArray([])
                nums.items[i + 1] = replacement
                return replacement

        arr = PdfArray([])
        insert_at = len(nums.items)
        for i in range(0, len(nums.items) - 1, 2):
            num = self._resolve(nums.items[i])
            if isinstance(num, PdfNumber) and int(num.value) > key:
                insert_at = i
                break
        nums.items[insert_at:insert_at] = [PdfNumber(key), arr]
        return arr

    def _append_struct_root_kid(
        self, struct_root: PdfDictionary, elem_ref: PdfIndirectReference
    ) -> None:
        kids = self._resolve(struct_root.mapping.get(PdfName("K")))
        if isinstance(kids, PdfArray):
            kids.items.append(elem_ref)
        elif kids is None:
            struct_root.mapping[PdfName("K")] = PdfArray([elem_ref])
        else:
            struct_root.mapping[PdfName("K")] = PdfArray([kids, elem_ref])

    def _register_marked_content(
        self,
        page_index: int,
        tag: Optional[str],
        *,
        alt: Optional[str] = None,
        actual_text: Optional[str] = None,
    ) -> Optional[Tuple[str, int]]:
        tag_name = self._coerce_structure_type(tag)
        if tag_name is None:
            return None
        struct_root, struct_ref = self._ensure_struct_tree_root()
        page = self._get_page_dict(page_index)
        if not isinstance(page, PdfDictionary):
            raise PdfValidationException("Page dictionary is unavailable.")
        page_ref = self._page_ref_for_structure(page_index)
        parent_array = self._parent_tree_array_for_page(struct_root, page)
        mcid = len(parent_array.items)
        elem = PdfDictionary(
            {
                PdfName("Type"): PdfName("StructElem"),
                PdfName("S"): PdfName(tag_name),
                PdfName("P"): struct_ref,
                PdfName("Pg"): page_ref,
                PdfName("K"): PdfNumber(mcid),
            }
        )
        if alt is not None:
            elem.mapping[PdfName("Alt")] = PdfString(str(alt))
        if actual_text is not None:
            elem.mapping[PdfName("ActualText")] = PdfString(str(actual_text))
        elem_ref = self._cos_doc.register_object(elem)
        parent_array.items.append(elem_ref)
        self._append_struct_root_kid(struct_root, elem_ref)
        return tag_name, mcid

    def auto_tag(
        self,
        image_alt: Optional[Union[str, Callable[[str], str]]] = "Image",
    ) -> int:
        """Heuristically tag existing page content into the structure tree.

        Each text object (``BT`` ... ``ET``) on every page becomes a ``/P`` (or
        ``/H1`` when its font size dominates) structure element, and each image
        XObject paint (``/Name Do``) becomes a ``/Figure`` with ``/Alt`` --
        wrapped in marked content and linked, in reading (stream) order, through
        the page ``/StructParents`` and the ``/StructTreeRoot /ParentTree``.
        Pages that already carry marked content are skipped.  Returns the number
        of structure elements created.

        ``image_alt`` controls the figure alternate text: a string used for
        every image, a callable mapping an image's resource name to its alt
        text, or ``None`` to leave images untagged (text only).  Image alt text
        cannot be inferred, so the default placeholder needs human review.

        This is a heuristic aid -- it does not infer fine-grained reading order
        or paragraph/list/table grouping -- so the result is a real (if coarse)
        tag tree, not certified accessibility.
        """
        self._ensure_not_disposed()
        if self._cos_doc is None:
            raise AsposePdfException(
                "auto_tag requires a document loaded from file or bytes."
            )
        if self.encrypted:
            raise AsposePdfException("Decrypt the document before auto-tagging.")
        total = 0
        for index in range(len(self.pages)):
            total += self._auto_tag_page(index, image_alt)
        if total:
            logger.info("Auto-tagged %d structure element(s).", total)
        return total

    def _auto_tag_page(
        self,
        page_index: int,
        image_alt: Optional[Union[str, Callable[[str], str]]],
    ) -> int:
        from .auto_tag import (
            build_tagged_content,
            choose_tags,
            find_text_objects,
            find_xobject_invocations,
            has_marked_content,
        )

        try:
            content = self.get_page_content(page_index)
        except PDF_OPERATION_ERRORS:
            return 0
        if not content or has_marked_content(content):
            return 0

        text_objects = find_text_objects(content)
        tags = choose_tags(text_objects)
        # (start, end, tag, alt) items; sorted by start so MCIDs follow reading
        # order across both text objects and image figures.
        items: List[Tuple[int, int, str, Optional[str]]] = [
            (obj.start, obj.end, tag, None)
            for obj, tag in zip(text_objects, tags)
        ]
        if image_alt is not None:
            image_names = self._image_xobject_names(page_index)
            if image_names:
                for name, start, end in find_xobject_invocations(content):
                    if name.lstrip("/") in image_names:
                        items.append(
                            (start, end, "Figure", self._figure_alt(image_alt, name))
                        )
        if not items:
            return 0
        items.sort(key=lambda item: item[0])

        marks: List[Tuple[int, int, str, int]] = []
        for start, end, tag, alt in items:
            registered = self._register_marked_content(page_index, tag, alt=alt)
            if registered is not None:
                marks.append((start, end, registered[0], registered[1]))
        if not marks:
            return 0
        self._set_page_content(page_index, build_tagged_content(content, marks))
        return len(marks)

    def _figure_alt(
        self, image_alt: Union[str, Callable[[str], str]], name: str
    ) -> str:
        if callable(image_alt):
            try:
                return str(image_alt(name.lstrip("/")))
            except PDF_OPERATION_ERRORS:
                return "Image"
        return str(image_alt)

    def _image_xobject_names(self, page_index: int) -> Set[str]:
        """Return the names of image (not form) XObjects in a page's resources."""
        names: Set[str] = set()
        page = self._get_page_dict(page_index)
        if not isinstance(page, PdfDictionary):
            return names
        resources = self._resolve_resources_cos(page)
        if not isinstance(resources, PdfDictionary):
            return names
        xobjects = self._resolve(resources.mapping.get(PdfName("XObject")))
        if not isinstance(xobjects, PdfDictionary):
            return names
        for key, value in xobjects.mapping.items():
            obj = self._resolve(value)
            if isinstance(obj, PdfStream):
                subtype = self._resolve(obj.mapping.get(PdfName("Subtype")))
                if isinstance(subtype, PdfName) and subtype.name.lstrip("/") == "Image":
                    names.add(key.name.lstrip("/"))
        return names

    def _set_page_content(self, page_index: int, content: bytes) -> None:
        """Replace a page's content stream(s) with a single decoded stream."""
        self._materialize_page_contents_for_edit()
        self._ensure_cos()
        if page_index < len(self.page_contents):
            self.page_contents[page_index] = content
        page = self._get_page_dict(page_index)
        if isinstance(page, PdfDictionary):
            new_ref = self._cos_doc.register_object(
                PdfStream(content=content, mapping={})
            )
            page.mapping[PdfName("Contents")] = new_ref
        while len(self._content_obj_ids) < len(self.pages):
            self._content_obj_ids.append(0)
        if page_index < len(self._content_obj_ids):
            self._content_obj_ids[page_index] = 0

    def replace_text(
        self,
        search: str,
        replacement: str,
        *,
        page_index: Optional[int] = None,
        case_sensitive: bool = True,
        max_count: int = 0,
    ) -> int:
        """Replace existing text in simple page-content text-showing operands.

        This edits literal and hex string operands used by ``Tj``, ``'``, ``"``
        and individual string elements inside ``TJ`` arrays. It does not perform
        layout reflow or rewrite phrases split across multiple ``TJ`` elements.
        Returns the number of replacements made.
        """
        self._ensure_not_disposed()
        if not isinstance(search, str):
            raise TypeError("search must be a string")
        if not isinstance(replacement, str):
            raise TypeError("replacement must be a string")
        if search == "":
            raise ValueError("search must not be empty")
        max_count = int(max_count)
        if max_count < 0:
            raise ValueError("max_count must be greater than or equal to zero")

        self._materialize_page_contents_for_edit()
        if page_index is None:
            indices = range(len(self.pages))
        else:
            self._validate_page_index(page_index)
            indices = (page_index,)

        total = 0
        for index in indices:
            remaining = 0 if max_count == 0 else max_count - total
            if max_count and remaining <= 0:
                break
            content = self.page_contents[index] if index < len(self.page_contents) else b""
            updated, count = replace_text_in_content(
                content,
                search,
                replacement,
                case_sensitive=case_sensitive,
                max_count=remaining,
            )
            if count:
                self._set_page_content(index, updated)
                total += count

        if total:
            self._extracted_text = None
        return total

    def redact_text(
        self,
        search: str,
        *,
        page_index: Optional[int] = None,
        case_sensitive: bool = True,
        max_count: int = 0,
        overlay: bool = False,
        overlay_color: tuple = (0.0, 0.0, 0.0),
    ) -> int:
        """Remove existing text from simple page-content text-showing operands.

        When *overlay* is true, a filled rectangle (``overlay_color``, a
        DeviceRGB triple of 0..1, default black) is drawn over each removed
        run's location -- the classic redaction bar. The bar is cosmetic: the
        text is already removed from the content stream, so a run whose position
        cannot be tracked (a multi-byte/Type0 font, an unresolved font) is simply
        left unmarked rather than leaking text.
        """
        self._ensure_not_disposed()
        if not isinstance(search, str):
            raise TypeError("search must be a string")
        if search == "":
            raise ValueError("search must not be empty")
        max_count = int(max_count)
        if max_count < 0:
            raise ValueError("max_count must be greater than or equal to zero")

        self._materialize_page_contents_for_edit()
        if page_index is None:
            indices = range(len(self.pages))
        else:
            self._validate_page_index(page_index)
            indices = (page_index,)

        total = 0
        for index in indices:
            remaining = 0 if max_count == 0 else max_count - total
            if max_count and remaining <= 0:
                break
            content = self.page_contents[index] if index < len(self.page_contents) else b""
            quads = []
            if overlay:
                from .text_locate import locate_matches

                quads = locate_matches(
                    content,
                    search,
                    self._build_simple_font_metrics(index),
                    case_sensitive=case_sensitive,
                    max_count=remaining,
                )
            updated, count = redact_text_in_content(
                content,
                search,
                case_sensitive=case_sensitive,
                max_count=remaining,
            )
            if count:
                if overlay and quads:
                    updated = self._append_redaction_overlay(
                        updated, quads, overlay_color
                    )
                self._set_page_content(index, updated)
                total += count

        if total:
            self._extracted_text = None
        return total

    @staticmethod
    def _append_redaction_overlay(
        content: bytes, quads: list, color: tuple
    ) -> bytes:
        """Append filled quads (in default user space) over redacted runs."""
        try:
            r, g, b = (max(0.0, min(1.0, float(c))) for c in color)
        except (TypeError, ValueError):
            r, g, b = 0.0, 0.0, 0.0
        parts = [content, b"\nq\n", f"{r:.4g} {g:.4g} {b:.4g} rg\n".encode("ascii")]
        for quad in quads:
            (x0, y0), (x1, y1), (x2, y2), (x3, y3) = quad
            parts.append(
                (
                    f"{x0:.3f} {y0:.3f} m {x1:.3f} {y1:.3f} l "
                    f"{x2:.3f} {y2:.3f} l {x3:.3f} {y3:.3f} l h f\n"
                ).encode("ascii")
            )
        parts.append(b"Q\n")
        return b"".join(parts)

    def _pdf_number(self, obj: Any) -> Optional[float]:
        obj = self._resolve(obj)
        if isinstance(obj, PdfNumber):
            return float(obj.value)
        if isinstance(obj, (int, float)):
            return float(obj)
        return None

    def _build_simple_font_metrics(self, page_index: int):
        """Return a ``name -> SimpleFontMetric|None`` resolver for a page."""
        from .cos import PdfDictionary, PdfName

        page_dict = self._get_page_dict(page_index)
        fonts_cos = None
        if isinstance(page_dict, PdfDictionary):
            resources = self._resolve_resources_cos(page_dict)
            if isinstance(resources, PdfDictionary):
                fonts_cos = self._resolve(resources.mapping.get(PdfName("Font")))
        cache: Dict[str, Any] = {}

        def resolver(name: str):
            if name in cache:
                return cache[name]
            metric = None
            if isinstance(fonts_cos, PdfDictionary):
                font_dict = self._resolve(fonts_cos.mapping.get(PdfName(name)))
                if isinstance(font_dict, PdfDictionary):
                    metric = self._simple_font_metric(font_dict)
            cache[name] = metric
            return metric

        return resolver

    def _simple_font_metric(self, font_dict: Any):
        """Build a ``SimpleFontMetric`` for a single-byte simple font, or None."""
        from .cos import PdfArray, PdfDictionary, PdfName
        from .text_locate import SimpleFontMetric

        if self._get_name(font_dict.mapping.get(PdfName("Subtype"))) == "Type0":
            return None  # multi-byte / composite -> not a simple font
        descriptor = self._resolve(font_dict.mapping.get(PdfName("FontDescriptor")))
        ascent, descent, missing = 800.0, -200.0, 0.0
        if isinstance(descriptor, PdfDictionary):
            a = self._pdf_number(descriptor.mapping.get(PdfName("Ascent")))
            d = self._pdf_number(descriptor.mapping.get(PdfName("Descent")))
            mw = self._pdf_number(descriptor.mapping.get(PdfName("MissingWidth")))
            ascent = a if a is not None else ascent
            descent = d if d is not None else descent
            missing = mw if mw is not None else missing
        first = self._pdf_number(font_dict.mapping.get(PdfName("FirstChar")))
        widths = self._resolve(font_dict.mapping.get(PdfName("Widths")))
        if first is not None and isinstance(widths, PdfArray):
            first_i = int(first)
            wlist = [self._pdf_number(it) for it in widths.items]
            wlist = [w if w is not None else missing for w in wlist]

            def width_of(code: int, _w=wlist, _f=first_i, _m=missing) -> float:
                idx = code - _f
                return _w[idx] if 0 <= idx < len(_w) else _m

            return SimpleFontMetric(width_of=width_of, ascent=ascent, descent=descent)
        # No /Widths (common for the Standard 14): use a bundled substitute's
        # metrics, which are metric-compatible with Helvetica/Times/Courier.
        return self._substitute_font_metric(font_dict, descriptor, ascent, descent)

    def _substitute_font_metric(
        self, font_dict: Any, descriptor: Any, ascent: float, descent: float
    ):
        from .cos import PdfDictionary, PdfName
        from .font_subset import read_unicode_cmap
        from .glyph_outlines import TrueTypeOutlines
        from .std_font_data import load_substitute_sfnt, resolve_substitute_key
        from .text_locate import SimpleFontMetric

        base = self._get_name(font_dict.mapping.get(PdfName("BaseFont")))
        flags, italic_angle, font_weight = 0, 0.0, None
        if isinstance(descriptor, PdfDictionary):
            f = self._pdf_number(descriptor.mapping.get(PdfName("Flags")))
            ia = self._pdf_number(descriptor.mapping.get(PdfName("ItalicAngle")))
            fw = self._pdf_number(descriptor.mapping.get(PdfName("FontWeight")))
            flags = int(f) if f is not None else 0
            italic_angle = ia if ia is not None else 0.0
            font_weight = fw
        key = resolve_substitute_key(
            base, flags=flags, italic_angle=italic_angle, font_weight=font_weight
        )
        sfnt = load_substitute_sfnt(key)
        if sfnt is None:
            return None
        try:
            outlines = TrueTypeOutlines(sfnt)
            if not outlines.ok:
                return None
            uni = read_unicode_cmap(sfnt)
        except (struct.error, IndexError, ValueError, TypeError, KeyError):
            return None
        upm = outlines.units_per_em or 1000

        def width_of(code: int, _o=outlines, _u=uni, _upm=upm) -> float:
            try:
                cp = ord(bytes([code]).decode("cp1252"))
            except (UnicodeDecodeError, TypeError):
                cp = code
            gid = _u.get(cp)
            if not gid:
                return 500.0
            adv = _o.advance_width(gid)
            return adv * 1000.0 / _upm if adv else 500.0

        return SimpleFontMetric(width_of=width_of, ascent=ascent, descent=descent)

    def _register_standard_font_resource(
        self, page_index: int, base_font: str = "Helvetica"
    ) -> str:
        font_name = str(base_font or "Helvetica").lstrip("/")
        fonts = self._ensure_resource_subdict(page_index, "Font")
        for key, value in fonts.mapping.items():
            font = self._resolve(value)
            if not isinstance(font, PdfDictionary):
                continue
            subtype = self._resolve(font.mapping.get(PdfName("Subtype")))
            existing_base = self._resolve(font.mapping.get(PdfName("BaseFont")))
            if (
                isinstance(subtype, PdfName)
                and subtype.name == "/Type1"
                and isinstance(existing_base, PdfName)
                and existing_base.name.lstrip("/") == font_name
            ):
                return key.name.lstrip("/")

        resource_name = self._unique_resource_name(fonts, "F", "F1")
        font_dict = PdfDictionary(
            {
                PdfName("Type"): PdfName("Font"),
                PdfName("Subtype"): PdfName("Type1"),
                PdfName("BaseFont"): PdfName(font_name),
            }
        )
        fonts.mapping[PdfName(resource_name)] = font_dict
        self.fonts[resource_name] = self._convert_cos_to_dict(font_dict)
        return resource_name

    def _register_page_image(
        self,
        page_index: int,
        image: AuthoredImage,
        requested_name: Optional[str] = None,
    ) -> str:
        xobjects = self._ensure_resource_subdict(page_index, "XObject")
        resource_name = self._unique_resource_name(xobjects, "Im", requested_name)
        mapping = {
            PdfName("Type"): PdfName("XObject"),
            PdfName("Subtype"): PdfName("Image"),
            PdfName("Width"): PdfNumber(image.width),
            PdfName("Height"): PdfNumber(image.height),
            PdfName("ColorSpace"): PdfName(image.color_space),
            PdfName("BitsPerComponent"): PdfNumber(image.bits_per_component),
        }
        if image.filter_name:
            mapping[PdfName("Filter")] = PdfName(image.filter_name)
        stream = PdfStream(content=image.stream_data, mapping=mapping)
        ref = self._cos_doc.register_object(stream)
        xobjects.mapping[PdfName(resource_name)] = ref

        self.images[resource_name] = image.decoded_data
        self._image_sizes[resource_name] = (image.width, image.height)
        self._image_meta[resource_name] = image.meta
        self._page_image_map.setdefault(page_index, []).append(resource_name)
        return resource_name

    def add_text_to_page(
        self,
        page_index: int,
        text: str,
        x: float,
        y: float,
        *,
        font_size: float = 12.0,
        font_name: str = "Helvetica",
        color: Sequence[float] = (0.0, 0.0, 0.0),
        tag: Optional[str] = None,
        actual_text: Optional[str] = None,
    ) -> None:
        """Append positioned text to a page content stream."""
        self._ensure_not_disposed()
        self._validate_page_index(page_index)
        try:
            size = float(font_size)
        except (TypeError, ValueError):
            raise PdfValidationException("font_size must be a positive number.")
        if size <= 0:
            raise PdfValidationException("font_size must be a positive number.")
        resource = self._register_standard_font_resource(page_index, font_name)
        content = build_text_stream(text, x, y, resource, size, color)
        mark = self._register_marked_content(
            page_index,
            tag or ("P" if actual_text is not None else None),
            actual_text=actual_text,
        )
        if mark is not None:
            content = wrap_marked_content(content, mark[0], mark[1])
        self._append_content_to_page(page_index, content)

    def add_image_to_page(
        self,
        page_index: int,
        data: bytes,
        x: float,
        y: float,
        width: Optional[float] = None,
        height: Optional[float] = None,
        *,
        pixel_width: Optional[int] = None,
        pixel_height: Optional[int] = None,
        color_space: str = "DeviceRGB",
        bits_per_component: int = 8,
        name: Optional[str] = None,
        tag: Optional[str] = None,
        alt: Optional[str] = None,
        actual_text: Optional[str] = None,
    ) -> str:
        """Register an image XObject and append a placement operation to a page."""
        self._ensure_not_disposed()
        self._validate_page_index(page_index)
        image = prepare_image(
            bytes(data),
            pixel_width=pixel_width,
            pixel_height=pixel_height,
            color_space=color_space,
            bits_per_component=bits_per_component,
        )
        resource_name = self._register_page_image(page_index, image, name)
        placement_width = image.width if width is None else width
        placement_height = image.height if height is None else height
        content = build_image_stream(
            resource_name, x, y, placement_width, placement_height
        )
        mark = self._register_marked_content(
            page_index,
            tag or ("Figure" if alt is not None or actual_text is not None else None),
            alt=alt,
            actual_text=actual_text,
        )
        if mark is not None:
            content = wrap_marked_content(content, mark[0], mark[1])
        self._append_content_to_page(page_index, content)
        self._image_matrix_map[(page_index, resource_name)] = (
            float(placement_width),
            0.0,
            0.0,
            float(placement_height),
            float(x),
            float(y),
        )
        self._image_rect_map[(page_index, resource_name)] = (
            float(x),
            float(y),
            float(placement_width),
            float(placement_height),
        )
        return resource_name

    def draw_rectangle_on_page(
        self,
        page_index: int,
        x: float,
        y: float,
        width: float,
        height: float,
        *,
        stroke_color: Optional[Sequence[float]] = (0.0, 0.0, 0.0),
        fill_color: Optional[Sequence[float]] = None,
        line_width: float = 1.0,
        tag: Optional[str] = None,
        alt: Optional[str] = None,
        actual_text: Optional[str] = None,
    ) -> None:
        """Append a rectangle path to a page content stream."""
        self._ensure_not_disposed()
        self._validate_page_index(page_index)
        content = build_rectangle_stream(
            x,
            y,
            width,
            height,
            stroke_color=stroke_color,
            fill_color=fill_color,
            line_width=line_width,
        )
        mark = self._register_marked_content(
            page_index,
            tag or ("Figure" if alt is not None or actual_text is not None else None),
            alt=alt,
            actual_text=actual_text,
        )
        if mark is not None:
            content = wrap_marked_content(content, mark[0], mark[1])
        self._append_content_to_page(page_index, content)

    def draw_line_on_page(
        self,
        page_index: int,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        *,
        stroke_color: Sequence[float] = (0.0, 0.0, 0.0),
        line_width: float = 1.0,
        tag: Optional[str] = None,
        alt: Optional[str] = None,
        actual_text: Optional[str] = None,
    ) -> None:
        """Append a stroked line segment to a page content stream."""
        self._ensure_not_disposed()
        self._validate_page_index(page_index)
        content = build_line_stream(
            x1,
            y1,
            x2,
            y2,
            stroke_color=stroke_color,
            line_width=line_width,
        )
        mark = self._register_marked_content(
            page_index,
            tag or ("Figure" if alt is not None or actual_text is not None else None),
            alt=alt,
            actual_text=actual_text,
        )
        if mark is not None:
            content = wrap_marked_content(content, mark[0], mark[1])
        self._append_content_to_page(page_index, content)

    # ---------------------------------------------------------------------------
    # Image handling
    # ---------------------------------------------------------------------------
    def add_image(self, name: str, data: bytes) -> None:
        self._ensure_not_disposed()
        self.images[name] = data

    def hide_image(self, name: str) -> None:
        self._ensure_not_disposed()
        if name not in self.images:
            raise KeyError(name)
        self._hidden_images.add(name)

    def replace_image(self, name: str, data: bytes) -> None:
        self._ensure_not_disposed()
        if name not in self.images:
            raise KeyError(name)
        self.images[name] = data

    def save_image(
        self, name: str, path: Union[str, Path], *, color_space: Optional[str] = None
    ) -> Path:
        """Save an extracted image as a real, openable image file.

        Reconstructs a proper file from the decoded samples plus the captured
        image metadata (``_image_meta``): raster codecs become PNG — applying
        CMYK/Indexed/Gray→RGB colour conversion — DCT/JPEG keeps its JPEG bytes,
        and JPX uses Pillow when installed. ``color_space`` (``"RGB"``/``"Gray"``)
        forces a conversion of reconstructed raster output. When no metadata is
        available the decoded bytes are written verbatim (back-compatible).

        Returns the path actually written; the suffix is adjusted to the produced
        format when the requested one would mislabel the file.
        """
        self._ensure_not_disposed()
        if name not in self.images:
            raise KeyError(name)
        from .image_export import reconstruct_image_file, resolve_output_path

        path = Path(path)
        out_bytes, produced_ext = reconstruct_image_file(
            self._image_meta.get(name), self.images[name], path.suffix, color_space
        )
        out_path = resolve_output_path(path, produced_ext)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(out_bytes)
        return out_path

    def get_attach_names(self) -> List[str]:
        """Return list of attachment names."""
        self._ensure_not_disposed()
        return list(self.attachments.keys())

    def has_next_attachment(self) -> bool:
        """Check if there are more attachments to iterate over.

        Initializes the attachment name list and cursor on first call.
        """
        if self._attachment_names is None:
            self._attachment_names = list(self.attachments.keys())
            self._attachment_cursor = 0
        return self._attachment_cursor < len(self._attachment_names)

    def extract_image(self) -> None:
        """Prepare image iterator."""
        self._image_names = list(self.images.keys())
        self._image_cursor = 0

    def has_next_image(self) -> bool:
        if self._image_names is None:
            self._image_names = list(self.images.keys())
            self._image_cursor = 0
        return self._image_cursor < len(self._image_names)

    def get_next_image(self) -> Tuple[str, bytes]:
        if self._image_names is None:
            self._image_names = list(self.images.keys())
            self._image_cursor = 0
        if self._image_cursor >= len(self._image_names):
            raise StopIteration("No more images")
        name = self._image_names[self._image_cursor]
        data = self.images[name]
        self._image_cursor += 1
        return name, data

    # ---------------------------------------------------------------------------
    # Text extraction
    # ---------------------------------------------------------------------------
    def extract_text(self) -> str:
        """Extract plain text from page contents."""
        if self._extracted_text is not None:
            return self._extracted_text
        texts: List[str] = []
        # Use empty resources when page resources are unavailable.
        empty_resources = {}

        for i, stream in enumerate(self.page_contents):
            try:
                # Try to get resources from COS doc if available
                resources = empty_resources
                if self._cos_doc:
                    try:
                        page_res = self._get_page_resources(i)
                        if page_res:
                            resources = page_res
                    except PDF_OPERATION_ERRORS:
                        pass

                parser = ContentStreamParser(stream, resources)
                text = parser.extract_text()
                if not text:
                    text = parser.best_effort_extract_text()
                texts.append(text)
            except CONTENT_PARSER_RECOVERABLE:
                # Fallback to best-effort extraction if full parser fails
                try:
                    parser = ContentStreamParser(stream, resources)
                    texts.append(parser.best_effort_extract_text())
                except CONTENT_PARSER_RECOVERABLE:
                    pass

        self._extracted_text = "\n".join(texts)
        self._page_text_cursor = 0
        return self._extracted_text

    def get_text(self) -> str:
        if self._extracted_text is None:
            self.extract_text()
        return self._extracted_text

    def get_next_page_text(self) -> str:
        """Return text for next page, advancing cursor."""
        self._ensure_not_disposed()
        if self._page_text_cursor >= len(self.page_contents):
            raise StopIteration("No more pages")
        stream = self.page_contents[self._page_text_cursor]
        self._page_text_cursor += 1
        try:
            # Try to get resources (same logic as extract_text)
            resources = {}
            if self._cos_doc and self._cos_doc.pages:
                try:
                    if (self._page_text_cursor - 1) < len(self._cos_doc.pages):
                        page_node = self._cos_doc.pages[self._page_text_cursor - 1]
                        if isinstance(page_node, dict) and "Resources" in page_node:
                            resources = page_node["Resources"]
                except PDF_OPERATION_ERRORS:
                    pass

            parser = ContentStreamParser(stream, resources)
            text = parser.extract_text()
            if not text:
                text = parser.best_effort_extract_text()
            return text
        except CONTENT_PARSER_RECOVERABLE:
            try:
                parser = ContentStreamParser(stream, resources)
                return parser.best_effort_extract_text()
            except CONTENT_PARSER_RECOVERABLE:
                return ""

    def has_next_page_text(self) -> bool:
        return self._page_text_cursor < len(self.page_contents)

    # ---------------------------------------------------------------------------
    # Page manipulation
    # ---------------------------------------------------------------------------
    def extract_pages(self, selection: Union[Iterable[int], slice]) -> "SimplePdf":
        """Return new PDF with selected pages."""
        if isinstance(selection, slice):
            indices = list(range(*selection.indices(len(self.pages))))
        else:
            indices = list(selection)

        if not indices:
            raise PdfValidationException("Selection of pages is empty")

        max_index = len(self.pages) - 1
        for i in indices:
            if i < 0 or i > max_index:
                raise IndexError(f"Page index out of range: {i}")

        new_pdf = SimplePdf()
        new_pdf.pages = [self.pages[i] for i in indices]
        new_pdf.page_contents = [self.page_contents[i] for i in indices]
        new_pdf.images = dict(self.images)
        new_pdf.metadata = dict(self.metadata)
        new_pdf.watermark_text = self.watermark_text
        new_pdf.encrypted = self.encrypted
        new_pdf.password = self.password
        new_pdf.O = self.O
        new_pdf.U = self.U
        new_pdf.P = self.P
        new_pdf.encryption_key = self.encryption_key
        return new_pdf

    def delete_pages(self, start_index: int, count: int) -> None:
        """Delete range of pages."""
        self._ensure_not_disposed()
        if count <= 0:
            return
        if start_index < 0 or start_index >= len(self.pages):
            raise PdfValidationException("start_index out of range")

        # Delete pages one by one from the same start_index
        # because each deletion shifts subsequent pages.
        for _ in range(count):
            if start_index >= len(self.pages):
                break
            self.delete(start_index)

    def delete(self, index: int) -> None:
        """Delete a single page at index with COS synchronization."""
        self._ensure_not_disposed()
        if index < 0 or index >= len(self.pages):
            raise PdfValidationException("Index out of range")

        del self.pages[index]
        del self.page_contents[index]

        if hasattr(self, "_page_obj_ids") and index < len(self._page_obj_ids):
            del self._page_obj_ids[index]
        if hasattr(self, "_content_obj_ids") and index < len(self._content_obj_ids):
            del self._content_obj_ids[index]

        if self._cos_doc:
            self._delete_cos_page(index)

        self._page_cache_valid = False

        # Re-map images (Shift indices)
        new_image_map = {}
        for idx, imgs in self._page_image_map.items():
            if idx < index:
                new_image_map[idx] = imgs
            elif idx > index:
                new_image_map[idx - 1] = imgs
        self._page_image_map = new_image_map

    def _delete_cos_page(self, index: int) -> None:
        """Remove a page from the COS page tree and update counts (Nested Tree support)."""
        if not self._cos_doc:
            return

        from .cos import PdfName, PdfArray, PdfIndirectReference, PdfDictionary

        # Use cache to find the specific leaf page object
        self._ensure_page_cache()
        if index < 0 or index >= len(self._page_refs):
            return

        obj_num = self._page_refs[index]
        page_dict = self._cos_doc.objects.get(obj_num)
        if not isinstance(page_dict, PdfDictionary):
            return

        parent_ref = page_dict.mapping.get(PdfName("Parent"))
        if not parent_ref:
            return

        # 1. Remove leaf from its parent's Kids array
        parent = self._resolve(parent_ref)
        if isinstance(parent, PdfDictionary) and PdfName("Kids") in parent.mapping:
            kids = parent.mapping[PdfName("Kids")]
            if isinstance(kids, PdfArray):
                for i, kid in enumerate(kids.items):
                    if (
                        isinstance(kid, PdfIndirectReference)
                        and kid.object_number == obj_num
                    ):
                        del kids.items[i]
                        break

        # 2. Recursively update /Count in all ancestors (Structural Integrity fix)
        self._update_page_count_recursive(parent_ref, -1)

    def insert_pages(
        self,
        index: int,
        new_pages: List[Tuple[float, float, float, float]],
        new_contents: Optional[List[bytes]] = None,
    ) -> None:
        """Insert multiple pages."""
        self._ensure_not_disposed()
        if new_contents is None:
            new_contents = [b""] * len(new_pages)
        for i, (rect, content) in enumerate(zip(new_pages, new_contents)):
            self.insert(index + i, (rect, content))

    def add(self, page: Any) -> None:
        """Add a page to the end with COS synchronization."""
        self.insert(len(self.pages), page)

    def insert(self, index: int, page: Any) -> None:
        """Insert a page at index with COS synchronization."""
        self._ensure_not_disposed()
        self._ensure_cos()  # Ensure COS doc exists for new PDFs
        if index < 0 or index > len(self.pages):
            raise PdfValidationException("Index out of range")

        if isinstance(page, tuple) and len(page) >= 2:
            media_box, content = page[0], page[1] if len(page) > 1 else b""
        else:
            media_box = getattr(page, "media_box", (0, 0, 612, 792))
            content = getattr(page, "content", b"")

        self.pages.insert(index, media_box)
        self.page_contents.insert(index, content if isinstance(content, bytes) else b"")

        if hasattr(self, "_page_obj_ids"):
            self._page_obj_ids.insert(index, 0)
        if hasattr(self, "_content_obj_ids"):
            self._content_obj_ids.insert(index, 0)

        # Shift image map
        new_image_map = {}
        for idx, imgs in self._page_image_map.items():
            if idx < index:
                new_image_map[idx] = imgs
            else:
                new_image_map[idx + 1] = imgs
        self._page_image_map = new_image_map

        self._page_cache_valid = False
        if self._cos_doc:
            self._create_cos_page(index, media_box, content)

    def add_page_break(self) -> None:
        """Add a blank page."""
        self._ensure_not_disposed()
        self.add(((0, 0, 612, 792), b""))

    # ---------------------------------------------------------------------------
    # Annotations
    # ---------------------------------------------------------------------------

    def _register_annotation_appearance(
        self,
        rect: Tuple[float, float, float, float],
        ap_spec: Dict[str, Any],
        resources: Optional[PdfDictionary] = None,
    ) -> PdfIndirectReference:
        """Create a registered /AP dictionary with /N form XObject for *ap_spec*.

        *ap_spec* must be ``{\"N\": bytes}`` — PDF content bytes in the form's
        coordinate system (origin lower-left of the annotation rectangle, width
        and height matching ``Rect``). *resources* is an optional ``/Resources``
        dictionary for the form (e.g. an ``/ExtGState`` for blend modes).
        """
        if self._cos_doc is None:
            raise AsposePdfException("COS document required for annotation appearance")
        n_raw = ap_spec.get("N")
        if not isinstance(n_raw, (bytes, bytearray)):
            raise TypeError("AP['N'] must be bytes")
        n_bytes = bytes(n_raw)
        llx, lly, urx, ury = (
            float(rect[0]),
            float(rect[1]),
            float(rect[2]),
            float(rect[3]),
        )
        w = urx - llx
        h = ury - lly
        mapping = {
            PdfName("Type"): PdfName("XObject"),
            PdfName("Subtype"): PdfName("Form"),
            PdfName("FormType"): PdfNumber(1),
            PdfName("BBox"): PdfArray(
                [
                    PdfNumber(0),
                    PdfNumber(0),
                    PdfNumber(w),
                    PdfNumber(h),
                ]
            ),
        }
        if isinstance(resources, PdfDictionary):
            mapping[PdfName("Resources")] = resources
        stream = PdfStream(content=n_bytes, mapping=mapping)
        stream_ref = self._cos_doc.register_object(stream)
        ap_dict = PdfDictionary({PdfName("N"): stream_ref})
        return self._cos_doc.register_object(ap_dict)

    def _build_appearance_resources(
        self, ext_gstates: Dict[str, Dict[str, Any]]
    ) -> Optional[PdfDictionary]:
        """Build a form ``/Resources`` dict carrying generated ``/ExtGState`` entries."""
        if not ext_gstates:
            return None
        gs_dict = PdfDictionary({})
        for name, params in ext_gstates.items():
            entry = PdfDictionary({})
            for key, value in params.items():
                if key == "BM":
                    entry.mapping[PdfName("BM")] = PdfName(str(value))
                elif key in ("ca", "CA"):
                    entry.mapping[PdfName(key)] = PdfNumber(float(value))
            gs_dict.mapping[PdfName(str(name))] = entry
        return PdfDictionary({PdfName("ExtGState"): gs_dict})

    def generate_annotation_appearance(
        self, page_index: int, annot_index: int, *, force: bool = False
    ) -> bool:
        """Synthesise an ``/AP /N`` appearance for one annotation.

        Returns ``True`` when the annotation has an appearance after the call —
        either because one already existed (and *force* is ``False``) or because
        one was generated. Returns ``False`` for unsupported subtypes or when the
        required geometry is missing. With *force*, an existing appearance is
        regenerated.
        """
        self._ensure_not_disposed()
        self._ensure_cos()
        if self._cos_doc is None:
            return False
        if page_index < 0 or page_index >= len(self._page_obj_ids):
            return False
        page_id = self._page_obj_ids[page_index]
        page_dict = self._cos_doc.objects.get(page_id) if page_id else None
        if not isinstance(page_dict, PdfDictionary):
            return False
        annots = self._resolve(page_dict.mapping.get(PdfName("Annots")))
        if not isinstance(annots, PdfArray):
            return False
        if annot_index < 0 or annot_index >= len(annots.items):
            return False
        annot = self._resolve(annots.items[annot_index])
        if not isinstance(annot, PdfDictionary):
            return False

        has_ap = isinstance(self._resolve(annot.mapping.get(PdfName("AP"))), PdfDictionary)
        if has_ap and not force:
            return True

        from .appearance import build_appearance

        subtype = self._get_cos_name(annot.mapping.get(PdfName("Subtype")))
        rect = self._get_cos_rect(annot.mapping.get(PdfName("Rect")))
        props = self._extract_annotation_properties(annot)
        generated = build_appearance(subtype, rect, props)
        if generated is None:
            return False

        resources = self._build_appearance_resources(generated.ext_gstates)
        annot.mapping[PdfName("AP")] = self._register_annotation_appearance(
            rect, {"N": generated.content}, resources
        )
        return True

    def generate_appearances(
        self, page_index: Optional[int] = None, *, force: bool = False
    ) -> int:
        """Generate missing appearances across a page (or all pages).

        Returns the number of appearances created. Annotations that already have
        an appearance are left untouched unless *force* is given.
        """
        self._ensure_not_disposed()
        self._ensure_cos()
        if self._cos_doc is None:
            return 0
        pages = (
            range(len(self.pages)) if page_index is None else [page_index]
        )
        created = 0
        for pi in pages:
            existing = self.get_annotations(pi)
            for ai, entry in enumerate(existing):
                had_ap = bool(entry.get("has_AP", False))
                if had_ap and not force:
                    continue
                if self.generate_annotation_appearance(pi, ai, force=force):
                    created += 1
        return created

    def get_annotations(self, page_index: int) -> List[Dict[str, Any]]:
        """Get annotations for a page."""
        self._ensure_not_disposed()
        if self._cos_doc is None:
            return []
        if page_index < 0 or page_index >= len(self.pages):
            return []
        if not hasattr(self, "_page_obj_ids") or page_index >= len(self._page_obj_ids):
            return []

        page_id = self._page_obj_ids[page_index]
        if page_id == 0:
            return []

        page_dict = self._cos_doc.objects.get(page_id)
        if not isinstance(page_dict, PdfDictionary):
            return []

        annots = page_dict.mapping.get(PdfName("Annots"))
        if annots is None:
            return []

        resolved_annots = self._resolve(annots)
        if not isinstance(resolved_annots, PdfArray):
            return []

        results = []
        for annot_ref in resolved_annots.items:
            annot_dict = self._resolve(annot_ref)
            if isinstance(annot_dict, PdfDictionary):
                entry: Dict[str, Any] = {
                    "Subtype": self._get_cos_name(
                        annot_dict.mapping.get(PdfName("Subtype"))
                    ),
                    "Rect": self._get_cos_rect(annot_dict.mapping.get(PdfName("Rect"))),
                    "Contents": self._get_cos_string(
                        annot_dict.mapping.get(PdfName("Contents"))
                    ),
                    "T": self._get_cos_string(annot_dict.mapping.get(PdfName("T"))),
                    "has_AP": False,
                }
                ap = self._resolve(annot_dict.mapping.get(PdfName("AP")))
                if isinstance(ap, PdfDictionary):
                    n = self._resolve(ap.get(PdfName("N")))
                    if isinstance(n, PdfStream):
                        entry["has_AP"] = True
                        entry["AP_N"] = n.content
                entry["Properties"] = self._extract_annotation_properties(annot_dict)
                results.append(entry)
        return results

    # Keys handled explicitly elsewhere (or unsafe to round-trip generically);
    # every other entry of an annotation dictionary travels through the generic
    # property channel so all subtypes preserve their defining attributes.
    _RESERVED_ANNOT_KEYS = frozenset(
        {"/Type", "/Subtype", "/Rect", "/Contents", "/T", "/AP", "/P"}
    )

    def _annotation_cos_to_value(self, obj: Any) -> Any:
        """Convert a COS annotation property value into a plain Python value."""
        obj = self._resolve(obj)
        if isinstance(obj, PdfBoolean):
            return obj.value
        if isinstance(obj, PdfNumber):
            return obj.value
        if isinstance(obj, PdfName):
            return AnnotationName(obj.name.lstrip("/"))
        if isinstance(obj, PdfStream):
            # Stream-valued entries (rare for annotations) are not surfaced here.
            return None
        if isinstance(obj, PdfString):
            try:
                return obj.value.decode("utf-8")
            except (UnicodeDecodeError, AttributeError):
                return bytes(obj.value)
        if isinstance(obj, PdfArray):
            return [self._annotation_cos_to_value(item) for item in obj.items]
        if isinstance(obj, PdfDictionary):
            return {
                key.name.lstrip("/"): self._annotation_cos_to_value(val)
                for key, val in obj.mapping.items()
                if isinstance(key, PdfName)
            }
        return None

    def _extract_annotation_properties(
        self, annot_dict: PdfDictionary
    ) -> Dict[str, Any]:
        """Collect type-specific annotation entries for the public surface."""
        props: Dict[str, Any] = {}
        for key, value in annot_dict.mapping.items():
            if not isinstance(key, PdfName):
                continue
            if key.name in self._RESERVED_ANNOT_KEYS:
                continue
            converted = self._annotation_cos_to_value(value)
            if converted is not None:
                props[key.name.lstrip("/")] = converted
        return props

    def _apply_annotation_properties(
        self, annot: PdfDictionary, properties: Dict[str, Any]
    ) -> None:
        """Write type-specific properties onto *annot* (``None`` removes a key)."""
        for name, value in properties.items():
            pdf_key = PdfName(str(name))
            if pdf_key.name in self._RESERVED_ANNOT_KEYS:
                continue
            if value is None:
                annot.mapping.pop(pdf_key, None)
            else:
                annot.mapping[pdf_key] = annotation_value_to_cos(value)

    def _get_cos_name(self, val: Any) -> str:
        if isinstance(val, PdfName):
            return val.name.lstrip("/")
        return ""

    def _get_cos_rect(self, val: Any) -> Tuple[float, float, float, float]:
        if isinstance(val, PdfArray) and len(val.items) >= 4:
            try:
                return tuple(float(getattr(i, "value", i)) for i in val.items[:4])
            except (TypeError, ValueError):
                pass
        return (0, 0, 0, 0)

    def _get_cos_string(self, val: Any) -> str:
        if isinstance(val, PdfString):
            try:
                return val.value.decode("utf-8")
            except (UnicodeDecodeError, AttributeError):
                return str(val.value)
        return ""

    def add_annotation(self, page_index: int, data: Dict[str, Any]) -> None:
        """Add an annotation to a page."""
        self._ensure_not_disposed()
        self._ensure_cos()
        if self._cos_doc is None:
            return
        if page_index < 0 or page_index >= len(self._page_obj_ids):
            return

        page_id = self._page_obj_ids[page_index]
        if page_id == 0:
            return

        page_dict = self._cos_doc.objects.get(page_id)
        if not isinstance(page_dict, PdfDictionary):
            return

        # Create annotation dictionary
        annot = PdfDictionary(
            {
                PdfName("Type"): PdfName("Annot"),
                PdfName("Subtype"): PdfName(data.get("Subtype", "Text")),
                PdfName("Rect"): PdfArray(
                    [PdfNumber(v) for v in data.get("Rect", (0, 0, 100, 100))]
                ),
                PdfName("Contents"): PdfString(data.get("Contents", "")),
            }
        )
        if data.get("T"):
            annot.mapping[PdfName("T")] = PdfString(data["T"])

        ap_spec = data.get("AP")
        if isinstance(ap_spec, dict) and isinstance(
            ap_spec.get("N"), (bytes, bytearray)
        ):
            rect = tuple(float(x) for x in data.get("Rect", (0, 0, 100, 100)))
            try:
                annot.mapping[PdfName("AP")] = self._register_annotation_appearance(
                    rect, ap_spec
                )
            except TypeError:
                pass

        properties = data.get("Properties")
        if isinstance(properties, dict):
            self._apply_annotation_properties(annot, properties)

        annot_ref = self._cos_doc.register_object(annot)

        annots_ref = page_dict.mapping.get(PdfName("Annots"))
        if annots_ref is None:
            annots_array = PdfArray([annot_ref])
            page_dict.mapping[PdfName("Annots")] = annots_array
        else:
            annots_array = self._resolve(annots_ref)
            if isinstance(annots_array, PdfArray):
                annots_array.items.append(annot_ref)

    def insert_annotation(
        self, page_index: int, annot_index: int, data: Dict[str, Any]
    ) -> None:
        """Insert an annotation at a specific index on a page."""
        self._ensure_not_disposed()
        self._ensure_cos()
        if self._cos_doc is None:
            return
        if page_index < 0 or page_index >= len(self._page_obj_ids):
            return

        page_id = self._page_obj_ids[page_index]
        if page_id == 0:
            return

        page_dict = self._cos_doc.objects.get(page_id)
        if not isinstance(page_dict, PdfDictionary):
            return

        annot = PdfDictionary(
            {
                PdfName("Type"): PdfName("Annot"),
                PdfName("Subtype"): PdfName(data.get("Subtype", "Text")),
                PdfName("Rect"): PdfArray(
                    [PdfNumber(v) for v in data.get("Rect", (0, 0, 100, 100))]
                ),
                PdfName("Contents"): PdfString(data.get("Contents", "")),
            }
        )
        if data.get("T"):
            annot.mapping[PdfName("T")] = PdfString(data["T"])

        ap_spec = data.get("AP")
        if isinstance(ap_spec, dict) and isinstance(
            ap_spec.get("N"), (bytes, bytearray)
        ):
            rect = tuple(float(x) for x in data.get("Rect", (0, 0, 100, 100)))
            try:
                annot.mapping[PdfName("AP")] = self._register_annotation_appearance(
                    rect, ap_spec
                )
            except TypeError:
                pass

        properties = data.get("Properties")
        if isinstance(properties, dict):
            self._apply_annotation_properties(annot, properties)

        annot_ref = self._cos_doc.register_object(annot)

        annots_ref = page_dict.mapping.get(PdfName("Annots"))
        if annots_ref is None:
            annots_array = PdfArray([annot_ref])
            page_dict.mapping[PdfName("Annots")] = annots_array
        else:
            annots_array = self._resolve(annots_ref)
            if isinstance(annots_array, PdfArray):
                if annot_index < 0:
                    annot_index = 0
                if annot_index > len(annots_array.items):
                    annot_index = len(annots_array.items)
                annots_array.items.insert(annot_index, annot_ref)

    def clear_annotations(self, page_index: int) -> None:
        """Remove all annotations from a page."""
        self._ensure_not_disposed()
        if self._cos_doc is None:
            return
        if page_index < 0 or page_index >= len(self._page_obj_ids):
            return

        page_id = self._page_obj_ids[page_index]
        page_dict = self._cos_doc.objects.get(page_id)
        if not isinstance(page_dict, PdfDictionary):
            return

        annots_ref = page_dict.mapping.get(PdfName("Annots"))
        if annots_ref is None:
            return

        annots_array = self._resolve(annots_ref)
        if isinstance(annots_array, PdfArray):
            annots_array.items.clear()

    def update_annotation(
        self, page_index: int, annot_index: int, data: Dict[str, Any]
    ) -> None:
        """Update an existing annotation."""
        self._ensure_not_disposed()
        if self._cos_doc is None:
            return
        if page_index < 0 or page_index >= len(self._page_obj_ids):
            return

        page_id = self._page_obj_ids[page_index]
        page_dict = self._cos_doc.objects.get(page_id)
        if not isinstance(page_dict, PdfDictionary):
            return

        annots_ref = page_dict.mapping.get(PdfName("Annots"))
        if annots_ref is None:
            return

        annots_array = self._resolve(annots_ref)
        if not isinstance(annots_array, PdfArray) or annot_index >= len(
            annots_array.items
        ):
            return

        annot_ref = annots_array.items[annot_index]
        annot_dict = self._resolve(annot_ref)
        if not isinstance(annot_dict, PdfDictionary):
            return

        if "Contents" in data:
            annot_dict.mapping[PdfName("Contents")] = PdfString(data["Contents"])
        if "Rect" in data:
            annot_dict.mapping[PdfName("Rect")] = PdfArray(
                [PdfNumber(v) for v in data["Rect"]]
            )
        if "T" in data:
            annot_dict.mapping[PdfName("T")] = PdfString(data["T"])

        if "AP" in data:
            ap_spec = data["AP"]
            if ap_spec is None:
                annot_dict.mapping.pop(PdfName("AP"), None)
            elif isinstance(ap_spec, dict) and isinstance(
                ap_spec.get("N"), (bytes, bytearray)
            ):
                rect = self._get_cos_rect(annot_dict.mapping.get(PdfName("Rect")))
                annot_dict.mapping[PdfName("AP")] = (
                    self._register_annotation_appearance(rect, ap_spec)
                )

        properties = data.get("Properties")
        if isinstance(properties, dict):
            self._apply_annotation_properties(annot_dict, properties)

    def delete_annotation(self, page_index: int, annot_index: int) -> None:
        """Delete an annotation from a page."""
        self._ensure_not_disposed()
        if self._cos_doc is None:
            return
        if page_index < 0 or page_index >= len(self._page_obj_ids):
            return

        page_id = self._page_obj_ids[page_index]
        page_dict = self._cos_doc.objects.get(page_id)
        if not isinstance(page_dict, PdfDictionary):
            return

        annots_ref = page_dict.mapping.get(PdfName("Annots"))
        if annots_ref is None:
            return

        annots_array = self._resolve(annots_ref)
        if not isinstance(annots_array, PdfArray) or annot_index >= len(
            annots_array.items
        ):
            return

        del annots_array.items[annot_index]

    def append(self, other: "SimplePdf") -> None:
        """Append another PDF's pages (with COS synchronization)."""
        self._ensure_not_disposed()
        for rect, content in zip(other.pages, other.page_contents):
            self.add((rect, content))

        # Merge images and other metadata
        self.images.update(other.images)
        self.metadata.update(other.metadata)
        self._page_cache_valid = False

    # ---------------------------------------------------------------------------
    # Attachments
    # ---------------------------------------------------------------------------
    def extract_attachment(self) -> None:
        """Prepare attachment iteration."""
        pass

    def get_attachment(self, name: str) -> bytes:
        """Get attachment by name."""
        self._ensure_not_disposed()
        if name not in self.attachments:
            raise KeyError(name)
        return self.attachments[name]

    # ---------------------------------------------------------------------------
    # Signature
    # ---------------------------------------------------------------------------
    def add_signature(self, reason: str, contact: str, location: str) -> None:
        self._ensure_not_disposed()
        if self.signature:
            raise PdfSecurityException("PDF already has a signature.")
        self.signature = {
            "Reason": reason,
            "ContactInfo": contact,
            "Location": location,
        }

    # ---------------------------------------------------------------------------
    # Validation/Repair/Optimization
    # ---------------------------------------------------------------------------
    def validate(self, max_depth: int = 100) -> bool:
        """Validate PDF structure and integrity.

        Checks:
        1. Basic page structure (MediaBox presence)
        2. COS document integrity (if loaded)
        3. Metadata dictionary consistency
        4. Cross-reference table consistency (if COS doc exists)
        5. Circular references and deep recursion in COS graph
        """
        self._ensure_not_disposed()

        # 1. Basic check: must have at least one page
        if not self.pages:
            return False

        # 2. Check MediaBox for each page
        for mbox in self.pages:
            if not isinstance(mbox, tuple) or len(mbox) != 4:
                return False

        # 3. COS graph validation (if available)
        if self._cos_doc:
            try:
                # Check root catalog presence
                from .cos import PdfName, PdfDictionary, PdfArray, PdfIndirectReference

                root_ref = self._cos_doc.trailer.get(PdfName("Root"))
                if not root_ref:
                    return False
                root = self._resolve(root_ref)
                if not isinstance(root, PdfDictionary):
                    return False

                # Check cross-reference consistency
                if not hasattr(self._cos_doc, "objects") or not self._cos_doc.objects:
                    return False

                # 5. Deep Structural Validation (Detect circular references & deep recursion)
                visited = set()

                def _validate_object(obj: Any, depth: int) -> bool:
                    if depth > max_depth:
                        return False  # Exceeded max depth

                    obj_id = id(obj)
                    if obj_id in visited:
                        return False  # Circular reference detected

                    visited.add(obj_id)

                    try:
                        if isinstance(obj, PdfDictionary):
                            for v in obj.mapping.values():
                                if not _validate_object(v, depth + 1):
                                    return False
                        elif isinstance(obj, PdfArray):
                            for item in obj.items:
                                if not _validate_object(item, depth + 1):
                                    return False
                        elif isinstance(obj, PdfIndirectReference):
                            # Follow reference if possible
                            ref_obj = self._cos_doc.objects.get(obj.object_number)
                            if ref_obj and not _validate_object(ref_obj, depth + 1):
                                return False
                    finally:
                        # Backtrack so sibling branches can revisit shared nodes;
                        # whole-document cycle detection runs separately below.
                        visited.remove(obj_id)
                    return True

                # Reset visited and do global cycle detection
                global_visited = set()

                def _has_cycles(obj: Any, path: set) -> bool:
                    obj_id = id(obj)
                    if obj_id in path:
                        return True
                    if obj_id in global_visited:
                        return False

                    global_visited.add(obj_id)
                    path.add(obj_id)

                    res = False
                    if isinstance(obj, PdfDictionary):
                        for v in obj.mapping.values():
                            if _has_cycles(v, path):
                                res = True
                                break
                    elif isinstance(obj, PdfArray):
                        for item in obj.items:
                            if _has_cycles(item, path):
                                res = True
                                break
                    elif isinstance(obj, PdfIndirectReference):
                        ref_obj = self._cos_doc.objects.get(obj.object_number)
                        if ref_obj and _has_cycles(ref_obj, path):
                            res = True

                    path.remove(obj_id)
                    return res

                if _has_cycles(self._cos_doc.trailer, set()):
                    return False

            except PDF_OPERATION_ERRORS:
                return False

        # 4. Metadata consistency
        if self.metadata and not isinstance(self.metadata, dict):
            return False

        return True

    def check(self) -> bool:
        """Check PDF integrity. Alias for validate()."""
        return self.validate()

    def repair(self) -> bool:
        """Attempt to repair PDF structure issues.

        Fixes:
        - Page/content count mismatch
        - Empty pages list (adds default page)
        - Missing metadata dict
        - Invalid MediaBox values (normalizes to A4)
        - Invalid image references
        - Missing default fonts ( Helvetica)

        Returns:
            True if repair succeeded, False if PDF is too corrupted
        """
        self._ensure_not_disposed()

        # 1. Ensure at least one page exists
        if not self.pages:
            self.pages = [(0, 0, 612, 792)]

        # 2. Ensure page_contents matches pages count
        while len(self.page_contents) < len(self.pages):
            self.page_contents.append(b"")
        while len(self.page_contents) > len(self.pages):
            self.page_contents.pop()

        # 3. Ensure metadata is a dict
        if not isinstance(self.metadata, dict):
            self.metadata = {}

        # 4. Validate and normalize MediaBox values
        for i, mbox in enumerate(self.pages):
            if not isinstance(mbox, tuple) or len(mbox) != 4:
                self.pages[i] = (0, 0, 612, 792)  # Default A4/Letter
            else:
                try:
                    # Fix order (x0 < x1, y0 < y1)
                    x0, y0, x1, y1 = (float(v) for v in mbox)
                    if x0 > x1:
                        x0, x1 = x1, x0
                    if y0 > y1:
                        y0, y1 = y1, y0
                    self.pages[i] = (x0, y0, x1, y1)
                except (ValueError, TypeError):
                    self.pages[i] = (0, 0, 612, 792)

        # 5. Check if _cos_doc is present, if not, try a safe re-parsing if data available
        if not self._cos_doc and self._raw_bytes:
            try:
                from .pdf_parser_cos import PdfCosParser

                parser = PdfCosParser(self._raw_bytes)
                self._cos_doc = parser.parse()
            except PDF_OPERATION_ERRORS as exc:
                logger.warning(
                    "SimplePdf.repair: COS re-parse failed; continuing without "
                    "refreshed _cos_doc: %s",
                    exc,
                )

        # 6. Ensure Root and Pages exist in COS doc if it's there
        if self._cos_doc:
            from .cos import PdfName, PdfDictionary, PdfArray

            if PdfName("Root") not in self._cos_doc.trailer:
                # Try to find Catalog or create one
                catalog = PdfDictionary({PdfName("Type"): PdfName("Catalog")})
                cat_ref = self._cos_doc.register_object(catalog)
                self._cos_doc.trailer[PdfName("Root")] = cat_ref

            root = self._resolve(self._cos_doc.trailer.get(PdfName("Root")))
            if isinstance(root, PdfDictionary):
                if PdfName("Pages") not in root:
                    pages_dict = PdfDictionary(
                        {
                            PdfName("Type"): PdfName("Pages"),
                            PdfName("Kids"): PdfArray([]),
                            PdfName("Count"): 0,
                        }
                    )
                    pages_ref = self._cos_doc.register_object(pages_dict)
                    root[PdfName("Pages")] = pages_ref

        # 7. Remove invalid image references
        valid_images = {}
        for name, data in self.images.items():
            if isinstance(name, str) and isinstance(data, (bytes, bytearray)):
                valid_images[name] = bytes(data)
        self.images = valid_images

        logger.info("Repair process completed")
        return True

    def optimize(
        self,
        options: "OptimizationOptions | None" = None,
        *,
        compress_streams: bool = True,
    ) -> None:
        """Perform PDF optimization driven by *options*.

        Honors :class:`~aspose_pdf.optimization.OptimizationOptions`:

        * ``remove_duplicate_images`` – collapse byte-identical images.
        * ``link_duplicate_streams`` / ``allow_reuse_page_content`` – share a
          single copy of byte-identical content streams.
        * ``remove_unused_objects`` – garbage-collect unreachable objects;
          ``remove_unused_streams`` prunes only unreachable streams when full GC
          is off.
        * ``compress_fonts`` – include embedded font programs in Flate
          compression.
        * ``unembed_fonts`` – drop the embedded program of Standard-14 fonts
          (safe; custom fonts are kept). See :meth:`_unembed_fonts`.
        * ``subset_fonts`` – strip unused glyphs from embedded TrueType programs
          (off by default). See :meth:`_subset_fonts`.
        * ``use_object_streams`` – record that the next full rewrite should pack
          objects into an object stream + cross-reference stream (the largest
          file-size lever); honored by :meth:`to_bytes` via :class:`PdfCosWriter`.

        ``image_compression_quality`` re-encodes eligible RGB/grayscale image
        XObjects as baseline JPEG at that quality; ``image_max_dimension`` caps
        the longest pixel side (downscaling first). See
        :meth:`_recompress_images_cos`. The private ``compress_streams`` keyword
        lets callers (e.g. the low-code ``Optimizer``) suppress stream
        compression entirely, which also disables object-stream packing.
        """
        self._ensure_not_disposed()
        if options is None:
            from ..optimization import OptimizationOptions

            options = OptimizationOptions()

        logger.info("Starting PDF optimization")

        # 1. Image deduplication. On a COS document, collapse content-identical
        # image XObjects across the object graph; otherwise fall back to the
        # simple name->bytes model (self.images + self.page_contents).
        if options.remove_duplicate_images:
            if self._cos_doc is not None:
                try:
                    self._dedup_images_cos()
                except PDF_OPERATION_ERRORS as exc:
                    logger.warning(
                        "SimplePdf.optimize: image dedup skipped: %s", exc
                    )
            else:
                self._dedup_images()

        # 1b. Recompress / downscale images (COS graph). Runs after dedup so a
        # shared image is recompressed once, and before GC/compression.
        if self._cos_doc is not None and (
            options.image_compression_quality is not None
            or options.image_max_dimension is not None
        ):
            try:
                self._recompress_images_cos(
                    options.image_compression_quality, options.image_max_dimension
                )
            except PDF_OPERATION_ERRORS as exc:
                logger.warning(
                    "SimplePdf.optimize: image recompression skipped: %s", exc
                )

        # 2. Link byte-identical content streams (COS graph)
        if self._cos_doc is not None and options.link_duplicate_streams:
            try:
                self._link_duplicate_streams(options.allow_reuse_page_content)
            except PDF_OPERATION_ERRORS as exc:
                logger.warning("SimplePdf.optimize: stream linking skipped: %s", exc)

        # 2b. Unembed Standard-14 font programs (COS graph). Runs before GC so
        # the freed font-program streams are also swept by full collection.
        if self._cos_doc is not None and options.unembed_fonts:
            try:
                self._unembed_fonts()
            except PDF_OPERATION_ERRORS as exc:
                logger.warning(
                    "SimplePdf.optimize: font unembedding skipped: %s", exc
                )

        # 2c. Subset embedded TrueType fonts to the glyphs actually used. Runs
        # before GC/compression so the smaller font program is what gets swept
        # and Flate-compressed.
        if self._cos_doc is not None and options.subset_fonts:
            try:
                self._subset_fonts()
            except PDF_OPERATION_ERRORS as exc:
                logger.warning(
                    "SimplePdf.optimize: font subsetting skipped: %s", exc
                )

        # 3. Garbage collection / unused-stream removal (COS graph)
        if self._cos_doc is not None:
            try:
                if options.remove_unused_objects:
                    self.garbage_collect()
                elif options.remove_unused_streams:
                    self._prune_unused_streams()
            except PDF_OPERATION_ERRORS as exc:
                logger.warning(
                    "SimplePdf.optimize: garbage-collection skipped: %s", exc
                )

        # 4. Stream compression (COS graph), independent of GC success
        if self._cos_doc is not None and compress_streams:
            try:
                self.compress_streams(include_fonts=options.compress_fonts)
            except PDF_OPERATION_ERRORS as exc:
                logger.warning(
                    "SimplePdf.optimize: stream compression skipped: %s", exc
                )

        # 5. Remember whether a full rewrite should pack objects into an object
        # stream + cross-reference stream (the largest size lever). Tied to the
        # private ``compress_streams`` switch so the low-code "no compression"
        # path still emits a classic, uncompressed xref table.
        if self._cos_doc is not None:
            self._use_object_streams = (
                bool(options.use_object_streams) and compress_streams
            )

    def _dedup_images(self) -> None:
        """Collapse byte-identical images, rewriting page-content references."""
        hashes: dict = {}
        duplicates = []
        for name, data in self.images.items():
            h = hashlib.md5(data).hexdigest()
            if h in hashes:
                duplicates.append((name, hashes[h]))
            else:
                hashes[h] = name

        for dup_name, orig_name in duplicates:
            # Replace references in page contents
            pattern = rb"/" + dup_name.encode() + rb"\b"
            replacement = rb"/" + orig_name.encode()
            for i in range(len(self.page_contents)):
                self.page_contents[i] = re.sub(
                    pattern, replacement, self.page_contents[i]
                )
            del self.images[dup_name]

    def _dedup_images_cos(self) -> None:
        """Collapse content-identical image XObjects across the COS graph.

        Unlike :meth:`_link_duplicate_streams` (which only merges streams whose
        stored bytes match) this compares the *decoded* image samples plus the
        geometry/colour-space/soft-mask, so two image XObjects with identical
        pixels but different stored bytes (e.g. one stored raw, one Flate) are
        merged. Duplicates are repointed at one canonical object and removed.
        """
        if self._cos_doc is None:
            return
        from .cos import PdfName, PdfStream

        subtype_key = PdfName("Subtype")
        image_name = PdfName("Image")
        objects = self._cos_doc.objects

        canonical: dict = {}
        remap: dict = {}
        for obj_num in sorted(objects.keys()):
            obj = objects[obj_num]
            if not isinstance(obj, PdfStream):
                continue
            if obj.mapping.get(subtype_key) != image_name:
                continue
            key = self._image_identity_key(obj)
            if key is None:
                continue
            if key in canonical:
                remap[obj_num] = canonical[key]
            else:
                canonical[key] = obj_num

        if not remap:
            return
        self._remap_references(remap)
        for dup_num in remap:
            objects.pop(dup_num, None)
        logger.info("Image dedup collapsed %d duplicate image(s).", len(remap))

    def _image_identity_key(self, stream: Any, _depth: int = 0):
        """Return a content-identity tuple for an image XObject, or ``None``.

        ``None`` means the image cannot be keyed safely and must not be merged.
        """
        from .cos import PdfName, PdfStream

        if _depth > 4:
            return None
        m = stream.mapping
        width = self._get_number(m.get(PdfName("Width")))
        height = self._get_number(m.get(PdfName("Height")))
        if width is None or height is None:
            return None

        payload = self._image_payload_hash(stream)
        if payload is None:
            return None

        # Fold a soft mask's own identity in: an image with a soft mask must not
        # collapse with the same base pixels carrying a different (or no) mask.
        smask = self._resolve(m.get(PdfName("SMask")))
        if isinstance(smask, PdfStream):
            smask_key = self._image_identity_key(smask, _depth + 1)
            if smask_key is None:
                return None
        elif smask is None:
            smask_key = None
        else:
            return None

        return (
            int(width),
            int(height),
            self._get_number(m.get(PdfName("BitsPerComponent"))),
            repr(self._convert_cos_to_dict(m.get(PdfName("ColorSpace")))),
            repr(self._convert_cos_to_dict(m.get(PdfName("ImageMask")))),
            repr(self._convert_cos_to_dict(m.get(PdfName("Decode")))),
            smask_key,
            payload,
        )

    def _image_payload_hash(self, stream: Any):
        """Hash an image's pixels: decoded when cheap, else the stored bytes."""
        from .cos import PdfArray, PdfName

        # Opaque image codecs whose raw samples we cannot cheaply recover; key
        # on the encoded bytes + codec so only byte-identical copies collapse.
        opaque = {
            "DCTDecode",
            "DCT",
            "JPXDecode",
            "CCITTFaxDecode",
            "CCF",
            "JBIG2Decode",
        }
        filt = self._resolve(stream.mapping.get(PdfName("Filter")))
        names: list = []
        if isinstance(filt, PdfName):
            names = [filt.name.lstrip("/")]
        elif isinstance(filt, PdfArray):
            names = [self._get_name(f) or "" for f in filt.items]
        terminal = names[-1] if names else None

        # Encrypted streams are stored ciphertext; comparing the stored bytes is
        # the only safe option without per-object decryption here.
        if getattr(self, "encryption_key", None) or terminal in opaque:
            return ("enc", terminal, hashlib.md5(stream.content).digest())
        try:
            raw = self._decode_cos_stream(stream)
        except PDF_OPERATION_ERRORS:
            return ("raw", hashlib.md5(stream.content).digest())
        return ("dec", hashlib.md5(raw).digest())

    # ------------------------------------------------------------------
    # Image recompression / downscaling (image_compression_quality)
    # ------------------------------------------------------------------
    _OPAQUE_IMAGE_FILTERS = frozenset(
        {"DCTDecode", "DCT", "JPXDecode", "CCITTFaxDecode", "CCF", "JBIG2Decode"}
    )
    _JPEG_FILTERS = frozenset({"DCTDecode", "DCT"})

    def _recompress_images_cos(
        self, quality: Optional[int], max_dim: Optional[int]
    ) -> None:
        """Recompress and/or downscale eligible RGB/grayscale image XObjects.

        With *quality* set, eligible images are re-encoded as baseline JPEG
        (``/DCTDecode``) at that quality; *max_dim* caps the longest pixel side,
        downscaling first.  An image is rewritten only when the result is
        smaller and its colour model is reproduced exactly, so masks, odd colour
        spaces and images carrying a ``/Decode`` array are left untouched.
        """
        if self._cos_doc is None or getattr(self, "encryption_key", None):
            return
        if quality is None and max_dim is None:
            return
        from .cos import PdfName, PdfStream

        objects = self._cos_doc.objects
        # Objects used as soft masks / stencil masks must not be lossily
        # recompressed — JPEG ringing on a sharp mask is visible.
        mask_targets: set = set()
        for obj in objects.values():
            if not isinstance(obj, PdfStream):
                continue
            for key in (PdfName("SMask"), PdfName("Mask")):
                ref = obj.mapping.get(key)
                if isinstance(ref, PdfIndirectReference):
                    mask_targets.add(ref.object_number)

        count = 0
        for obj_num in sorted(objects.keys()):
            if obj_num in mask_targets:
                continue
            stream = objects[obj_num]
            if isinstance(stream, PdfStream) and self._recompress_one_image(
                stream, quality, max_dim
            ):
                count += 1
        if count:
            logger.info("Recompressed/resized %d image(s).", count)

    def _recompress_one_image(
        self, stream: Any, quality: Optional[int], max_dim: Optional[int]
    ) -> bool:
        from . import dct, jpeg_encoder
        from .cos import PdfBoolean, PdfName, PdfNumber
        from .image_resample import downscale, fit_within

        m = stream.mapping
        if m.get(PdfName("Subtype")) != PdfName("Image"):
            return False
        mask = self._resolve(m.get(PdfName("ImageMask")))
        if isinstance(mask, PdfBoolean) and mask.value:
            return False
        if m.get(PdfName("Decode")) is not None:
            return False  # sample remapping we would have to reproduce.
        width = self._get_number(m.get(PdfName("Width")))
        height = self._get_number(m.get(PdfName("Height")))
        if not width or not height:
            return False
        width, height = int(width), int(height)
        comps = self._cs_components(m.get(PdfName("ColorSpace")))
        if comps not in (1, 3):
            return False

        new_w, new_h = (
            fit_within(width, height, max_dim) if max_dim else (width, height)
        )
        resized = new_w != width or new_h != height
        if quality is None and not resized:
            return False  # nothing to do for this image.

        names = self._filter_names(stream)
        terminal = names[-1] if names else None
        if terminal in self._OPAQUE_IMAGE_FILTERS - self._JPEG_FILTERS:
            return False  # JPX/CCITT/JBIG2: cannot recover samples cheaply.

        is_jpeg = terminal in self._JPEG_FILTERS
        if is_jpeg:
            decoded = dct.decode(stream.content)
            if (
                decoded is None
                or decoded.components != comps
                or decoded.width != width
                or decoded.height != height
            ):
                return False
            samples = decoded.samples
        else:
            if self._get_number(m.get(PdfName("BitsPerComponent"))) != 8:
                return False
            try:
                samples = self._decode_cos_stream(stream)
            except PDF_OPERATION_ERRORS:
                return False
            if len(samples) < width * height * comps:
                return False
            samples = samples[: width * height * comps]

        if resized:
            samples = downscale(samples, width, height, comps, new_w, new_h)

        if quality is None and not is_jpeg:
            # Loss-free downscale of a raster image: keep it lossless (Flate).
            from .filters import StreamEncoder

            new_content = StreamEncoder.encode(bytes(samples), "FlateDecode")
            new_filter = "FlateDecode"
        else:
            try:
                new_content = jpeg_encoder.encode(
                    new_w, new_h, comps, samples, quality if quality is not None else 90
                )
            except (ValueError, OverflowError):
                return False
            new_filter = "DCTDecode"

        if len(new_content) >= len(stream.content):
            return False  # never grow the file.

        stream.content = new_content
        m[PdfName("Filter")] = PdfName(new_filter)
        m.pop(PdfName("DecodeParms"), None)
        m[PdfName("BitsPerComponent")] = PdfNumber(8)
        m[PdfName("Width")] = PdfNumber(new_w)
        m[PdfName("Height")] = PdfNumber(new_h)
        m[PdfName("Length")] = PdfNumber(len(new_content))
        return True

    def _filter_names(self, stream: Any) -> list:
        from .cos import PdfArray, PdfName

        filt = self._resolve(stream.mapping.get(PdfName("Filter")))
        if isinstance(filt, PdfName):
            return [filt.name.lstrip("/")]
        if isinstance(filt, PdfArray):
            return [self._get_name(f) or "" for f in filt.items]
        return []

    def _cs_components(self, cs: Any) -> Optional[int]:
        """Return the component count of an image colour space (1/3/4) or None.

        Only device/calibrated gray and RGB (and ICCBased with N=1/3) are mapped;
        Indexed/Separation/DeviceN/Lab/Pattern return ``None`` so the caller
        leaves those images untouched.
        """
        from .cos import PdfArray, PdfName, PdfStream

        cs = self._resolve(cs)
        if isinstance(cs, PdfName):
            return {
                "DeviceGray": 1, "G": 1, "CalGray": 1,
                "DeviceRGB": 3, "RGB": 3, "CalRGB": 3,
                "DeviceCMYK": 4, "CMYK": 4,
            }.get(cs.name.lstrip("/"))
        if isinstance(cs, PdfArray) and cs.items:
            head = self._get_name(cs.items[0])
            if head == "ICCBased" and len(cs.items) > 1:
                profile = self._resolve(cs.items[1])
                if isinstance(profile, PdfStream):
                    n = self._get_number(profile.mapping.get(PdfName("N")))
                    return int(n) if n in (1, 3) else None
                return None
            if head == "CalGray":
                return 1
            if head == "CalRGB":
                return 3
        return None

    def _unembed_fonts(self) -> None:
        """Drop embedded font programs for Standard-14 fonts (safe unembedding).

        Only fonts whose ``/BaseFont`` (after stripping a subset prefix such as
        ``ABCDEF+``) is one of the 14 standard fonts are unembedded: PDF viewers
        substitute those from built-in metrics, so rendering is preserved.
        Custom embedded fonts are left untouched. The freed ``/FontFile*``
        streams are deleted here when they become unreferenced (full garbage
        collection, if enabled, is a backstop).
        """
        if self._cos_doc is None:
            return

        from .cos import PdfDictionary, PdfIndirectReference, PdfName
        from .std_fonts import StandardFonts

        type_key = PdfName("Type")
        font_type = PdfName("Font")
        base_font_key = PdfName("BaseFont")
        descriptor_key = PdfName("FontDescriptor")
        file_keys = (PdfName("FontFile"), PdfName("FontFile2"), PdfName("FontFile3"))

        orphan_ids: set = set()
        unembedded = 0
        for obj in list(self._cos_doc.objects.values()):
            if not isinstance(obj, PdfDictionary) or obj.get(type_key) != font_type:
                continue
            base = self._get_name(obj.get(base_font_key))
            if base is None:
                continue
            # Strip a 6-uppercase-letter subset prefix, e.g. "ABCDEF+Helvetica".
            if (
                len(base) > 7
                and base[6] == "+"
                and base[:6].isalpha()
                and base[:6].isupper()
            ):
                base = base[7:]
            if not StandardFonts.is_standard_font(base):
                continue
            descriptor = self._resolve(obj.get(descriptor_key))
            if not isinstance(descriptor, PdfDictionary):
                continue
            for fk in file_keys:
                ref = descriptor.get(fk)
                if ref is None:
                    continue
                if isinstance(ref, PdfIndirectReference):
                    orphan_ids.add(ref.object_number)
                del descriptor[fk]
                unembedded += 1

        # Delete font-program streams that are no longer referenced (guards the
        # pathological case of a stream shared with a font we did not touch).
        if orphan_ids:
            reachable = self._reachable_object_ids()
            for obj_num in orphan_ids:
                if obj_num not in reachable:
                    self._cos_doc.objects.pop(obj_num, None)

        if unembedded:
            logger.info("Unembedded %d Standard-14 font program(s).", unembedded)

    # -- Font subsetting ---------------------------------------------------

    def _subset_fonts(self) -> None:
        """Strip unused glyphs from embedded TrueType and CFF font programs.

        Glyph usage is gathered from page (and form-XObject) content streams.
        TrueType programs (``/FontFile2``) and name-keyed CFF programs
        (``/FontFile3``) are subset using glyph-erasure that preserves glyph
        numbering so ``cmap``/``charset``/``CIDToGIDMap`` stay valid (see
        :mod:`aspose_pdf.engine.font_subset` and
        :mod:`aspose_pdf.engine.font_subset_cff`). Fonts whose code->glyph
        mapping cannot be resolved confidently are left untouched.
        """
        if self._cos_doc is None:
            return
        # Subsetting rewrites a font program to plaintext; on an encrypted
        # document that would desynchronise it from the ciphertext streams the
        # writer re-encrypts, so leave embedded fonts whole there.
        if getattr(self, "encryption_key", None):
            return
        from .cos import PdfName, PdfNumber

        usage = self._collect_used_glyph_codes()
        if not usage:
            return

        objects = self._cos_doc.objects
        # Merge keep-sets per program so a font file shared by several font
        # dictionaries is subset once to the union of glyphs they need.
        plans: dict = {}
        for font_objnum, codes in usage.items():
            plan = self._plan_font_subset(objects.get(font_objnum), codes)
            if plan is None:
                continue
            ff_stream, keep_gids, program, subsetter = plan
            slot = plans.get(id(ff_stream))
            if slot is None:
                plans[id(ff_stream)] = [ff_stream, set(keep_gids), program, subsetter]
            else:
                slot[1] |= keep_gids

        subset_count = 0
        for ff_stream, keep_gids, program, subsetter in plans.values():
            new_program = subsetter(program, keep_gids)
            if new_program is None:
                continue
            # Store the subset program uncompressed; the later compression pass
            # re-Flates it when compress_fonts is on.
            ff_stream.content = new_program
            ff_stream.mapping.pop(PdfName("Filter"), None)
            ff_stream.mapping.pop(PdfName("DecodeParms"), None)
            ff_stream.mapping[PdfName("Length")] = PdfNumber(len(new_program))
            ff_stream.mapping[PdfName("Length1")] = PdfNumber(len(new_program))
            subset_count += 1

        if subset_count:
            logger.info("Subset %d embedded font program(s).", subset_count)

    def _plan_font_subset(self, font: Any, codes: set):
        """Return ``(fontfile_stream, keep_gids, program, subsetter)`` or ``None``."""
        from .cos import PdfDictionary, PdfName

        if not isinstance(font, PdfDictionary):
            return None
        subtype = self._get_name(font.mapping.get(PdfName("Subtype")))
        if subtype == "Type0":
            return self._plan_type0_subset(font, codes)
        if subtype == "TrueType":
            return self._plan_simple_truetype_subset(font, codes)
        return None

    def _plan_type0_subset(self, font: Any, codes: set):
        from .cos import PdfArray, PdfDictionary, PdfName
        from .font_subset import subset_truetype
        from .font_subset_cff import subset_cff

        # Only Identity encodings let us read CIDs straight from the codes.
        encoding = self._get_name(font.mapping.get(PdfName("Encoding")))
        if encoding not in ("Identity-H", "Identity-V"):
            return None
        descendants = self._resolve(font.mapping.get(PdfName("DescendantFonts")))
        cidfont = None
        if isinstance(descendants, PdfArray) and descendants.items:
            cidfont = self._resolve(descendants.items[0])
        if not isinstance(cidfont, PdfDictionary):
            return None
        cid_subtype = self._get_name(cidfont.mapping.get(PdfName("Subtype")))
        descriptor = self._resolve(cidfont.mapping.get(PdfName("FontDescriptor")))
        if cid_subtype == "CIDFontType2":  # TrueType outlines (/FontFile2)
            located = self._fontfile2(descriptor)
            subsetter = subset_truetype
        elif cid_subtype == "CIDFontType0":  # CFF outlines (/FontFile3)
            located = self._fontfile3(descriptor)
            subsetter = subset_cff
        else:
            return None
        if located is None:
            return None
        ff_stream, ff_ref = located
        try:
            program = self._decode_cos_stream(ff_stream, ff_ref)
        except PDF_OPERATION_ERRORS:
            return None

        # Resolve CID -> GID. CIDFontType2 uses /CIDToGIDMap. A CIDFontType0 with
        # a CID-keyed CFF (/CIDFontType0C) maps CID -> GID through the CFF charset;
        # a name-keyed CFF uses the CID as the glyph index directly (PDF 32000
        # 9.7.4.2), which the identity fallback yields.
        if cid_subtype == "CIDFontType0":
            from .font_subset_cff import cff_charset_cid_to_gid

            charset_map = cff_charset_cid_to_gid(program)
            if charset_map is not None:
                cid_to_gid = charset_map.get
            else:
                cid_to_gid = lambda cid: cid  # noqa: E731
        else:
            cid_to_gid = self._build_cid_to_gid(cidfont)
        keep = {0}
        for code_bytes in codes:
            for i in range(0, len(code_bytes) - 1, 2):
                cid = (code_bytes[i] << 8) | code_bytes[i + 1]
                gid = cid_to_gid(cid)
                if gid is not None:
                    keep.add(gid)
        return ff_stream, keep, program, subsetter

    def _plan_simple_truetype_subset(self, font: Any, codes: set):
        from .cos import PdfName
        from .font_subset import read_symbol_code_to_gid, subset_truetype

        descriptor = self._resolve(font.mapping.get(PdfName("FontDescriptor")))
        located = self._fontfile2(descriptor)
        if located is None:
            return None
        ff_stream, ff_ref = located
        try:
            program = self._decode_cos_stream(ff_stream, ff_ref)
        except PDF_OPERATION_ERRORS:
            return None

        # A symbol cmap maps the font's byte codes straight to glyph ids.
        code_to_gid = read_symbol_code_to_gid(program)
        if code_to_gid:
            keep = {0}
            for code_bytes in codes:
                for b in code_bytes:
                    gid = code_to_gid.get(b)
                    if gid is None:
                        gid = code_to_gid.get(0xF000 | b)
                    if gid is not None:
                        keep.add(gid)
            if len(keep) <= 1:
                return None
            return ff_stream, keep, program, subset_truetype

        # Otherwise resolve the PDF /Encoding (code -> unicode) and the font's
        # Unicode cmap (unicode -> gid).  To stay safe this bails (keeping the
        # font whole) the moment any *used* code cannot be resolved, so a used
        # glyph is never erased.
        keep = self._simple_truetype_unicode_keep(font, program, codes)
        if keep is None:
            return None
        return ff_stream, keep, program, subset_truetype

    def _simple_truetype_unicode_keep(self, font: Any, program: bytes, codes: set):
        """Return the keep-GID set for a simple TrueType via /Encoding, or None."""
        from .font_subset import read_unicode_cmap

        uni_to_gid = read_unicode_cmap(program)
        if not uni_to_gid:
            return None
        code_to_unicode = self._simple_code_to_unicode(font)
        if not code_to_unicode:
            return None
        keep = {0}
        for code_bytes in codes:
            for b in code_bytes:
                uni = code_to_unicode.get(b)
                if uni is None:
                    return None  # unresolved used code -> bail (never erase it).
                gid = uni_to_gid.get(uni)
                if gid is None:
                    return None  # used glyph absent from the cmap -> bail.
                keep.add(gid)
        if len(keep) <= 1:
            return None
        return keep

    def _simple_code_to_unicode(self, font: Any):
        """Build a ``byte code -> unicode`` map from a simple font's /Encoding.

        Only the encodings we can resolve exactly are honoured: a
        WinAnsi/MacRoman base (via the stdlib codecs) overlaid with /Differences
        whose glyph names are ``uniXXXX`` / ``uXXXXXX`` forms.  Anything else is
        left unmapped so the caller bails rather than risk a wrong glyph.
        """
        from .cos import PdfArray, PdfDictionary, PdfName, PdfNumber

        enc = self._resolve(font.mapping.get(PdfName("Encoding")))
        base_name = None
        differences = None
        if isinstance(enc, PdfName):
            base_name = enc.name.lstrip("/")
        elif isinstance(enc, PdfDictionary):
            base_name = self._get_name(enc.mapping.get(PdfName("BaseEncoding")))
            differences = self._resolve(enc.mapping.get(PdfName("Differences")))

        codec = {
            "WinAnsiEncoding": "cp1252",
            "MacRomanEncoding": "mac_roman",
        }.get(base_name)
        mapping: dict[int, int] = {}
        if codec:
            for code in range(256):
                try:
                    mapping[code] = ord(bytes([code]).decode(codec))
                except (UnicodeDecodeError, TypeError):
                    pass

        if isinstance(differences, PdfArray):
            current = 0
            for item in differences.items:
                if isinstance(item, PdfNumber):
                    current = int(item.value)
                elif isinstance(item, PdfName):
                    uni = _glyph_name_to_unicode(item.name.lstrip("/"))
                    if uni is None:
                        mapping.pop(current, None)  # force a bail if this is used.
                    else:
                        mapping[current] = uni
                    current += 1
        return mapping

    def _fontfile2(self, descriptor: Any):
        """Return ``(stream, ref)`` for a descriptor's ``/FontFile2`` or ``None``."""
        return self._font_program_stream(descriptor, "FontFile2")

    def _fontfile3(self, descriptor: Any):
        """Return ``(stream, ref)`` for a descriptor's ``/FontFile3`` or ``None``."""
        return self._font_program_stream(descriptor, "FontFile3")

    def _font_program_stream(self, descriptor: Any, key: str):
        """Return ``(stream, ref)`` for a descriptor's ``/<key>`` stream or ``None``."""
        from .cos import PdfDictionary, PdfName, PdfStream

        if not isinstance(descriptor, PdfDictionary):
            return None
        ref = descriptor.mapping.get(PdfName(key))
        stream = self._resolve(ref)
        if isinstance(stream, PdfStream):
            return stream, ref
        return None

    def _build_cid_to_gid(self, cidfont: Any):
        """Return a ``cid -> gid`` callable for a CIDFontType2's CIDToGIDMap."""
        from .cos import PdfName, PdfStream

        ref = cidfont.mapping.get(PdfName("CIDToGIDMap"))
        mapping = self._resolve(ref)
        if isinstance(mapping, PdfStream):
            try:
                data = self._decode_cos_stream(mapping, ref)
            except PDF_OPERATION_ERRORS:
                data = b""

            def lookup(cid: int, _d: bytes = data):
                off = cid * 2
                if 0 <= off and off + 2 <= len(_d):
                    return (_d[off] << 8) | _d[off + 1]
                return None

            return lookup
        # /Identity (or absent) means glyph id equals CID.
        return lambda cid: cid

    def _collect_used_glyph_codes(self) -> dict:
        """Map embedded-font object numbers to the show-text operand bytes used."""
        usage: dict = {}
        visited: set = set()
        for i in range(len(self.pages)):
            page = self._get_page_dict(i)
            if page is None:
                continue
            resources = self._resolve_resources_cos(page)
            if resources is None:
                continue
            try:
                content = self.get_page_content(i)
            except PDF_OPERATION_ERRORS:
                continue
            self._scan_content_for_glyphs(content, resources, usage, visited, 0)
        return usage

    def _resolve_resources_cos(self, node: Any):
        """Resolve a page/XObject ``/Resources`` dict, walking inherited /Parent."""
        from .cos import PdfDictionary, PdfName

        seen: set = set()
        current = node
        while isinstance(current, PdfDictionary):
            res = self._resolve(current.mapping.get(PdfName("Resources")))
            if isinstance(res, PdfDictionary):
                return res
            parent = current.mapping.get(PdfName("Parent"))
            if parent is None or id(parent) in seen:
                break
            seen.add(id(parent))
            current = self._resolve(parent)
        return None

    def _scan_content_for_glyphs(
        self, content: bytes, resources: Any, usage: dict, visited: set, depth: int
    ) -> None:
        from .content_stream_parser import ContentStreamParser
        from .cos import PdfName

        font_dict = self._resolve(resources.mapping.get(PdfName("Font")))
        xobject_dict = self._resolve(resources.mapping.get(PdfName("XObject")))

        try:
            tokens = list(ContentStreamParser(content, {})._tokenize())
        except PDF_OPERATION_ERRORS:
            return

        operands: list = []
        current_font: Optional[int] = None
        for tok in tokens:
            is_operator = (
                isinstance(tok, str)
                and not tok.startswith("/")
                and tok not in ("<<", ">>")
            )
            if not is_operator:
                operands.append(tok)
                continue

            if tok == "Tf":
                name = None
                for operand in reversed(operands):
                    if isinstance(operand, str) and operand.startswith("/"):
                        name = operand[1:]
                        break
                current_font = self._font_resource_objnum(font_dict, name)
            elif tok in ("Tj", "'", '"') and current_font is not None:
                for operand in operands:
                    if isinstance(operand, (bytes, bytearray)):
                        usage.setdefault(current_font, set()).add(bytes(operand))
            elif tok == "TJ" and current_font is not None:
                for operand in operands:
                    if isinstance(operand, list):
                        for element in operand:
                            if isinstance(element, (bytes, bytearray)):
                                usage.setdefault(current_font, set()).add(
                                    bytes(element)
                                )
            elif tok == "Do" and depth < 4:
                for operand in operands:
                    if isinstance(operand, str) and operand.startswith("/"):
                        self._recurse_form_xobject(
                            operand[1:], xobject_dict, resources, usage, visited, depth
                        )
            operands = []

    def _font_resource_objnum(self, font_dict: Any, name: Optional[str]):
        from .cos import PdfDictionary, PdfIndirectReference, PdfName

        if name is None or not isinstance(font_dict, PdfDictionary):
            return None
        ref = font_dict.mapping.get(PdfName(name))
        if isinstance(ref, PdfIndirectReference):
            return ref.object_number
        return None

    def _recurse_form_xobject(
        self,
        name: str,
        xobject_dict: Any,
        parent_resources: Any,
        usage: dict,
        visited: set,
        depth: int,
    ) -> None:
        from .cos import PdfDictionary, PdfIndirectReference, PdfName, PdfStream

        if not isinstance(xobject_dict, PdfDictionary):
            return
        ref = xobject_dict.mapping.get(PdfName(name))
        xobj = self._resolve(ref)
        if not isinstance(xobj, PdfStream):
            return
        if self._get_name(xobj.mapping.get(PdfName("Subtype"))) != "Form":
            return
        key = ref.object_number if isinstance(ref, PdfIndirectReference) else id(xobj)
        if key in visited:
            return
        visited.add(key)
        try:
            content = self._decode_cos_stream(xobj, ref)
        except PDF_OPERATION_ERRORS:
            return
        sub_resources = self._resolve(xobj.mapping.get(PdfName("Resources")))
        if not isinstance(sub_resources, PdfDictionary):
            sub_resources = parent_resources
        self._scan_content_for_glyphs(content, sub_resources, usage, visited, depth + 1)

    def _link_duplicate_streams(self, allow_reuse_page_content: bool = True) -> None:
        """Point references at one canonical copy of each byte-identical stream.

        Two streams are duplicates when their dictionaries (excluding
        ``/Length``) and their raw content bytes match. The duplicate objects
        are repointed and removed. When *allow_reuse_page_content* is False,
        streams used as page ``/Contents`` are left untouched.
        """
        if self._cos_doc is None:
            return

        from .cos import (
            PdfArray,
            PdfDictionary,
            PdfIndirectReference,
            PdfName,
            PdfStream,
        )

        objects = self._cos_doc.objects

        # Optionally protect page-content streams from being shared.
        protected: set = set()
        if not allow_reuse_page_content:
            contents_key = PdfName("Contents")
            type_key = PdfName("Type")
            page_name = PdfName("Page")
            for obj in objects.values():
                if isinstance(obj, PdfDictionary) and obj.get(type_key) == page_name:
                    contents = obj.get(contents_key)
                    if isinstance(contents, PdfIndirectReference):
                        protected.add(contents.object_number)
                    elif isinstance(contents, PdfArray):
                        for item in contents.items:
                            if isinstance(item, PdfIndirectReference):
                                protected.add(item.object_number)

        length_name = PdfName("Length")

        def stream_key(stream: PdfStream):
            dict_sig = tuple(
                sorted(
                    (k.name, repr(v))
                    for k, v in stream.mapping.items()
                    if k != length_name
                )
            )
            return (dict_sig, hashlib.md5(stream.content).digest())

        canonical: dict = {}
        remap: dict = {}
        for obj_num in sorted(objects.keys()):
            obj = objects[obj_num]
            if not isinstance(obj, PdfStream) or obj_num in protected:
                continue
            key = stream_key(obj)
            if key in canonical:
                remap[obj_num] = canonical[key]
            else:
                canonical[key] = obj_num

        if not remap:
            return

        self._remap_references(remap)
        for dup_num in remap:
            objects.pop(dup_num, None)
        logger.info("Stream linking collapsed %d duplicate stream(s).", len(remap))

    def _remap_references(self, remap: dict) -> None:
        """Rewrite, in place, every indirect reference whose target is remapped."""
        if self._cos_doc is None or not remap:
            return

        from .cos import PdfArray, PdfDictionary, PdfIndirectReference

        seen: set = set()
        stack = list(self._cos_doc.objects.values())
        stack.extend(self._cos_doc.trailer.mapping.values())
        while stack:
            current = stack.pop()
            if isinstance(current, PdfIndirectReference):
                if current.object_number in remap:
                    current.object_number = remap[current.object_number]
                continue
            if isinstance(current, (PdfDictionary, PdfArray)):
                lit_id = id(current)
                if lit_id in seen:
                    continue
                seen.add(lit_id)
                if isinstance(current, PdfDictionary):
                    stack.extend(current.mapping.values())
                else:
                    stack.extend(current.items)

    def _prune_unused_streams(self) -> None:
        """Remove only unreachable stream objects (narrow garbage collection)."""
        if self._cos_doc is None:
            return
        from .cos import PdfStream

        reachable = self._reachable_object_ids()
        removed = 0
        for obj_num in list(self._cos_doc.objects.keys()):
            if obj_num in reachable:
                continue
            if isinstance(self._cos_doc.objects[obj_num], PdfStream):
                del self._cos_doc.objects[obj_num]
                removed += 1
        if removed:
            logger.info("Removed %d unused stream object(s).", removed)

    def compress_streams(self, *, include_fonts: bool = True) -> None:
        """Compress uncompressed streams using FlateDecode.

        When *include_fonts* is False, embedded font programs
        (``/FontFile``/``/FontFile2``/``/FontFile3``) are left uncompressed.
        """
        if not self._cos_doc:
            return

        from .cos import PdfDictionary, PdfIndirectReference, PdfName, PdfStream

        font_program_ids: set = set()
        if not include_fonts:
            font_keys = (
                PdfName("FontFile"),
                PdfName("FontFile2"),
                PdfName("FontFile3"),
            )
            for obj in self._cos_doc.objects.values():
                if isinstance(obj, PdfDictionary):
                    for fk in font_keys:
                        ref = obj.get(fk)
                        if isinstance(ref, PdfIndirectReference):
                            font_program_ids.add(ref.object_number)

        logger.info("Compressing PDF streams")
        filter_key = PdfName("Filter")
        length_key = PdfName("Length")
        for obj_num, obj in self._cos_doc.objects.items():
            if not isinstance(obj, PdfStream) or obj_num in font_program_ids:
                continue
            # Only compress if not already compressed
            if filter_key in obj:
                continue
            try:
                compressed = zlib.compress(obj.content, 9)
            except (zlib.error, ValueError, TypeError) as exc:
                logger.warning(
                    "SimplePdf.compress_streams: skipped stream compression: %s",
                    exc,
                )
                continue
            # Only use compression if it actually reduces size
            if len(compressed) < len(obj.content):
                obj.content = compressed
                obj[filter_key] = PdfName("FlateDecode")
                obj[length_key] = len(obj.content)

    def optimize_resources(
        self, options: "OptimizationOptions | None" = None
    ) -> None:
        """Optimize embedded resources (alias of :meth:`optimize`)."""
        self.optimize(options)

    def _flatten_appearance_matrix(
        self, form: Any, coords: List[float]
    ) -> Tuple[float, float, float, float, float, float]:
        """Matrix mapping a form XObject's transformed /BBox onto the annot /Rect.

        Implements the appearance-placement step of ISO 32000-1 12.5.5: the form
        /BBox is transformed by the form /Matrix, and the resulting quadrilateral's
        bounding box is mapped (scale + translate) onto the annotation rectangle.
        The form's own /Matrix is re-applied by the ``Do`` operator, so this
        matrix is concatenated *before* drawing.
        """
        rx0, rx1 = min(coords[0], coords[2]), max(coords[0], coords[2])
        ry0, ry1 = min(coords[1], coords[3]), max(coords[1], coords[3])

        bbox = self._resolve(form.mapping.get(PdfName("BBox")))
        bvals = None
        if isinstance(bbox, PdfArray) and len(bbox.items) >= 4:
            try:
                bvals = [float(self._resolve(c).value) for c in bbox.items[:4]]
            except (AttributeError, ValueError):
                bvals = None
        if bvals is None:
            # No usable BBox: fall back to a pure translation onto the rect origin.
            return (1.0, 0.0, 0.0, 1.0, rx0, ry0)

        m = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
        matrix = self._resolve(form.mapping.get(PdfName("Matrix")))
        if isinstance(matrix, PdfArray) and len(matrix.items) >= 6:
            try:
                m = [float(self._resolve(c).value) for c in matrix.items[:6]]
            except (AttributeError, ValueError):
                m = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]

        bx0, by0, bx1, by1 = bvals
        corners = [(bx0, by0), (bx1, by0), (bx1, by1), (bx0, by1)]
        txs = [m[0] * x + m[2] * y + m[4] for x, y in corners]
        tys = [m[1] * x + m[3] * y + m[5] for x, y in corners]
        tx0, tx1 = min(txs), max(txs)
        ty0, ty1 = min(tys), max(tys)
        tw, th = tx1 - tx0, ty1 - ty0
        sx = (rx1 - rx0) / tw if tw else 1.0
        sy = (ry1 - ry0) / th if th else 1.0
        return (sx, 0.0, 0.0, sy, rx0 - sx * tx0, ry0 - sy * ty0)

    def flatten(self) -> None:
        """Flatten annotations and form fields into static page content.

        This process:
        1. Synthesises appearances for supported annotations that lack one
        2. Iterates through all pages
        3. Retrieves the /Annots array
        4. For each annotation with an appearance stream (/AP), appends it to page content
        5. Removes the /Annots entry and /AcroForm from the trailer
        """
        self._ensure_not_disposed()
        logger.info("Flattening annotations and form fields")

        if not self._cos_doc:
            return

        from .cos import PdfName, PdfDictionary, PdfArray, PdfStream

        # Give shape/markup annotations and form fields a renderable appearance
        # before flattening so their content is not silently dropped.
        self.generate_appearances()
        self.generate_field_appearances()

        # 1. Process each page
        for i in range(len(self.pages)):
            page_dict = self._get_page_dict(i)
            if not page_dict:
                continue

            annots_ref = page_dict.get(PdfName("Annots"))
            annots = self._resolve(annots_ref)
            if not isinstance(annots, PdfArray) or not annots.items:
                continue

            # Ensure page resources has an XObject dictionary
            resources = self._resolve(page_dict.get(PdfName("Resources")))
            if not isinstance(resources, PdfDictionary):
                resources = PdfDictionary({})
                page_dict[PdfName("Resources")] = resources

            xobjects = resources.get(PdfName("XObject"))
            if not isinstance(xobjects, PdfDictionary):
                xobjects = PdfDictionary({})
                resources[PdfName("XObject")] = xobjects

            new_content = bytearray(self.page_contents[i])

            for annot_ref in annots.items:
                annot = self._resolve(annot_ref)
                if not isinstance(annot, PdfDictionary):
                    continue

                # Only flatten if it has an appearance stream
                ap = self._resolve(annot.get(PdfName("AP")))
                if not isinstance(ap, PdfDictionary):
                    continue

                n = self._resolve(ap.get(PdfName("N")))
                if not isinstance(n, PdfStream):
                    continue

                # Get annotation rectangle [llx lly urx ury]
                rect = self._resolve(annot.get(PdfName("Rect")))
                if not isinstance(rect, PdfArray) or len(rect.items) != 4:
                    continue

                try:
                    coords = [float(self._resolve(c).value) for c in rect.items]
                except (AttributeError, ValueError):
                    continue

                # Generate unique name for this XObject in this page's resources
                xi = 0
                while PdfName(f"FlatAnnot{xi}") in xobjects.mapping:
                    xi += 1
                obj_name = PdfName(f"FlatAnnot{xi}")

                # Add to XObjects
                xobjects[obj_name] = n

                # Position the XObject: map its (matrix-transformed) /BBox onto
                # the annotation /Rect per ISO 32000-1 12.5.5.
                a, b, c, d, e, f = self._flatten_appearance_matrix(n, coords)

                # 'q' (save), 'cm' (concat matrix), 'Do' (draw), 'Q' (restore)
                op = (
                    f"\nq\n{a:g} {b:g} {c:g} {d:g} {e:g} {f:g} cm\n"
                    f"/{obj_name.name} Do\nQ\n"
                )
                new_content.extend(op.encode("ascii"))

            self.page_contents[i] = bytes(new_content)

            # Remove annotations after flattening
            page_dict[PdfName("Annots")] = PdfArray([])

        # 2. Remove AcroForm from catalog and trailer
        root_ref = self._cos_doc.trailer.mapping.get(PdfName("Root"))
        root = self._resolve(root_ref)
        if isinstance(root, PdfDictionary) and PdfName("AcroForm") in root.mapping:
            del root.mapping[PdfName("AcroForm")]

        if PdfName("AcroForm") in self._cos_doc.trailer.mapping:
            del self._cos_doc.trailer.mapping[PdfName("AcroForm")]

    def get_form_fields(self) -> Dict[str, Dict[str, Any]]:
        """Extract all form fields with values and types."""
        self._ensure_not_disposed()
        if not self._cos_doc:
            return {}

        extractor = CosExtractor(self._cos_doc, b"")
        return extractor.extract_form_fields()

    def set_field_value(self, name: str, value: Any) -> None:
        """Set the value of a form field by name."""
        self._ensure_not_disposed()
        if not self._cos_doc:
            return

        root_ref = self._cos_doc.trailer.mapping.get(PdfName("Root"))
        root = self._resolve(root_ref)
        if not isinstance(root, PdfDictionary):
            return

        acroform_ref = root.mapping.get(PdfName("AcroForm"))
        acroform = self._resolve(acroform_ref)
        if not isinstance(acroform, PdfDictionary):
            return

        fields_ref = acroform.mapping.get(PdfName("Fields"))
        fields = self._resolve(fields_ref)
        if isinstance(fields, PdfArray):
            self._update_field_value_rec(fields, name, value)

    def _update_field_value_rec(
        self, fields_arr: Any, target_name: str, value: Any, prefix: str = ""
    ) -> bool:
        for field_ref in fields_arr.items:
            field_obj = self._resolve(field_ref)
            if not isinstance(field_obj, PdfDictionary):
                continue

            t = field_obj.mapping.get(PdfName("T"))
            t = self._resolve(t)
            if not isinstance(t, PdfString):
                continue

            local_name = (
                t.value.decode("utf-8", errors="ignore")
                if isinstance(t.value, bytes)
                else str(t.value)
            )
            full_name = f"{prefix}.{local_name}" if prefix else local_name

            if full_name == target_name:
                pdf_val = self._value_to_pdf(field_obj, value)
                if pdf_val is not None:
                    field_obj.mapping[PdfName("V")] = pdf_val
                return True

            kids = field_obj.mapping.get(PdfName("Kids"))
            kids = self._resolve(kids)
            if isinstance(kids, PdfArray):
                if self._update_field_value_rec(kids, target_name, value, full_name):
                    return True
        return False

    def _value_to_pdf(self, field_obj: Any, value: Any) -> Optional[Any]:
        """Convert Python value to appropriate PDF object for form field /V."""
        ft = self._resolve(field_obj.mapping.get(PdfName("FT")))
        ff = self._resolve(field_obj.mapping.get(PdfName("Ff")))
        ff_val = int(ff.value) if isinstance(ff, PdfNumber) else 0
        is_radio = bool(ff_val & (1 << 15))
        is_checkbox = isinstance(ft, PdfName) and ft.name == "/Btn" and not is_radio
        is_choice = isinstance(ft, PdfName) and ft.name == "/Ch"

        if is_checkbox:
            checked = value in (True, "Yes", "1", "On", "yes", "true")
            return PdfName("Yes" if checked else "Off")
        if is_radio:
            return PdfName(str(value))
        if is_choice:
            if isinstance(value, (list, tuple)):
                return PdfArray([PdfString(str(v).encode()) for v in value])
            return PdfString(str(value).encode())
        return PdfString(str(value).encode())

    def generate_field_appearances(self, *, drop_need_appearances: bool = True) -> int:
        """Regenerate ``/AP`` appearance streams for AcroForm fields from their values.

        Builds the variable-text appearance of every text and choice field from
        its ``/V`` and ``/DA`` — so the value is visible without relying on the
        viewer honouring ``/NeedAppearances`` — and points each check box / radio
        widget's ``/AS`` at the appearance state matching its value. Field
        attributes (``/DA``, ``/Q``, ``/FT``, ``/Ff``, ``/V``) are inherited from
        ancestors per ISO 32000-1. Returns the number of widgets updated; when
        *drop_need_appearances* is true and at least one was updated, the AcroForm
        ``/NeedAppearances`` flag is cleared.
        """
        self._ensure_not_disposed()
        self._ensure_cos()
        if self._cos_doc is None:
            return 0
        root = self._resolve(self._cos_doc.trailer.mapping.get(PdfName("Root")))
        if not isinstance(root, PdfDictionary):
            return 0
        acro = self._resolve(root.mapping.get(PdfName("AcroForm")))
        if not isinstance(acro, PdfDictionary):
            return 0
        fields = self._resolve(acro.mapping.get(PdfName("Fields")))
        if not isinstance(fields, PdfArray):
            return 0

        inherited = {
            "da": self._get_cos_string(acro.mapping.get(PdfName("DA"))) or None,
            "q": None,
            "ft": None,
            "ff": 0,
            "v": None,
        }
        updated = 0
        for field_ref in fields.items:
            updated += self._gen_field_appearance_rec(field_ref, inherited, acro)

        if updated and drop_need_appearances:
            acro.mapping[PdfName("NeedAppearances")] = PdfBoolean(False)
        return updated

    def _gen_field_appearance_rec(
        self, field_ref: Any, inherited: Dict[str, Any], acro: PdfDictionary
    ) -> int:
        field = self._resolve(field_ref)
        if not isinstance(field, PdfDictionary):
            return 0

        # Each node overrides the attributes it declares; the rest are inherited.
        da = self._get_cos_string(field.mapping.get(PdfName("DA"))) or inherited["da"]
        q_obj = self._resolve(field.mapping.get(PdfName("Q")))
        q = int(q_obj.value) if isinstance(q_obj, PdfNumber) else inherited["q"]
        ft_obj = self._resolve(field.mapping.get(PdfName("FT")))
        ft = ft_obj.name.lstrip("/") if isinstance(ft_obj, PdfName) else inherited["ft"]
        ff_obj = self._resolve(field.mapping.get(PdfName("Ff")))
        ff = int(ff_obj.value) if isinstance(ff_obj, PdfNumber) else inherited["ff"]
        v_raw = field.mapping.get(PdfName("V"))
        v = v_raw if v_raw is not None else inherited["v"]

        child = {"da": da, "q": q, "ft": ft, "ff": ff, "v": v}

        count = 0
        kids = self._resolve(field.mapping.get(PdfName("Kids")))
        if isinstance(kids, PdfArray) and kids.items:
            for kid_ref in kids.items:
                count += self._gen_field_appearance_rec(kid_ref, child, acro)
        elif PdfName("Rect") in field.mapping:
            # Terminal (merged field + widget) annotation.
            try:
                if self._set_widget_appearance(field, ft, ff or 0, q or 0, v, da, acro):
                    count += 1
            except Exception:
                logger.warning("Could not generate field appearance", exc_info=True)
        return count

    def _set_widget_appearance(
        self,
        widget: PdfDictionary,
        ft: Optional[str],
        ff: int,
        q: int,
        v: Any,
        da: Optional[str],
        acro: PdfDictionary,
    ) -> bool:
        rect = self._get_cos_rect(widget.mapping.get(PdfName("Rect")))
        llx, urx = min(rect[0], rect[2]), max(rect[0], rect[2])
        lly, ury = min(rect[1], rect[3]), max(rect[1], rect[3])
        w, h = urx - llx, ury - lly
        if w <= 0 or h <= 0:
            return False
        rect_n = (llx, lly, urx, ury)

        is_radio = bool(ff & (1 << 15))
        if ft == "Btn":
            if ff & (1 << 16):  # push button: keep its own appearance
                return False
            return self._set_button_widget_state(widget, v, is_radio)
        if ft in ("Tx", "Ch"):
            return self._set_text_widget_appearance(
                widget, ft, ff, q, v, da, acro, rect_n, w, h
            )
        return False

    def _set_text_widget_appearance(
        self,
        widget: PdfDictionary,
        ft: str,
        ff: int,
        q: int,
        v: Any,
        da: Optional[str],
        acro: PdfDictionary,
        rect: Tuple[float, float, float, float],
        w: float,
        h: float,
    ) -> bool:
        from .field_appearance import build_text_appearance, parse_default_appearance

        text = self._field_value_to_text(v)
        font_name, size, color = parse_default_appearance(da or "/Helv 0 Tf 0 g")
        font_name = font_name or "Helv"
        multiline = ft == "Tx" and bool(ff & (1 << 12))
        font_ref, used_name = self._resolve_field_font(font_name, acro)
        content = build_text_appearance(
            text,
            w,
            h,
            font_name=used_name,
            font_size=size,
            color_op=color,
            quadding=q if q in (0, 1, 2) else 0,
            multiline=multiline,
        )
        resources = PdfDictionary(
            {PdfName("Font"): PdfDictionary({PdfName(used_name): font_ref})}
        )
        widget.mapping[PdfName("AP")] = self._register_annotation_appearance(
            rect, {"N": content}, resources
        )
        return True

    def _field_value_to_text(self, v: Any) -> str:
        """Render a form-field ``/V`` value as display text."""
        v = self._resolve(v)
        if isinstance(v, PdfString):
            return decode_pdf_text_string(v)
        if isinstance(v, PdfName):
            return v.name.lstrip("/")
        if isinstance(v, PdfArray):  # multi-select choice
            parts = []
            for item in v.items:
                item = self._resolve(item)
                if isinstance(item, PdfString):
                    parts.append(decode_pdf_text_string(item))
                elif isinstance(item, PdfName):
                    parts.append(item.name.lstrip("/"))
            return "\n".join(parts)
        return ""

    def _resolve_field_font(
        self, font_name: str, acro: PdfDictionary
    ) -> Tuple[Any, str]:
        """Resolve a ``/DR`` font by name, synthesising Helvetica as a fallback.

        Returns ``(font_reference, resource_name)``; the resource name always
        equals *font_name* so the generated ``Tf`` operator matches the form's
        ``/Resources /Font`` key.
        """
        dr = self._resolve(acro.mapping.get(PdfName("DR")))
        fonts = (
            self._resolve(dr.mapping.get(PdfName("Font")))
            if isinstance(dr, PdfDictionary)
            else None
        )
        if isinstance(fonts, PdfDictionary):
            existing = fonts.mapping.get(PdfName(font_name))
            if existing is not None:
                ref = (
                    existing
                    if isinstance(existing, PdfIndirectReference)
                    else self._cos_doc.register_object(self._resolve(existing))
                )
                return ref, font_name

        font_dict = PdfDictionary(
            {
                PdfName("Type"): PdfName("Font"),
                PdfName("Subtype"): PdfName("Type1"),
                PdfName("BaseFont"): PdfName("Helvetica"),
                PdfName("Encoding"): PdfName("WinAnsiEncoding"),
            }
        )
        font_ref = self._cos_doc.register_object(font_dict)
        # Cache in the AcroForm default resources so later fields reuse it.
        if not isinstance(dr, PdfDictionary):
            dr = PdfDictionary({})
            acro.mapping[PdfName("DR")] = dr
        fonts = self._resolve(dr.mapping.get(PdfName("Font")))
        if not isinstance(fonts, PdfDictionary):
            fonts = PdfDictionary({})
            dr.mapping[PdfName("Font")] = fonts
        fonts.mapping[PdfName(font_name)] = font_ref
        return font_ref, font_name

    def _set_button_widget_state(
        self, widget: PdfDictionary, v: Any, is_radio: bool
    ) -> bool:
        """Point a check box / radio widget's ``/AS`` at the state matching ``/V``."""
        on_state: Optional[str] = None
        ap = self._resolve(widget.mapping.get(PdfName("AP")))
        if isinstance(ap, PdfDictionary):
            n = self._resolve(ap.mapping.get(PdfName("N")))
            if isinstance(n, PdfDictionary):
                for key in n.mapping:
                    if isinstance(key, PdfName) and key.name != "/Off":
                        on_state = key.name.lstrip("/")
                        break

        v_res = self._resolve(v)
        value_name: Optional[str] = None
        if isinstance(v_res, PdfName):
            value_name = v_res.name.lstrip("/")
        elif isinstance(v_res, PdfString):
            value_name = decode_pdf_text_string(v_res)

        off_like = (None, "", "Off", "0", "false", "No")
        if is_radio:
            chosen = on_state if (on_state and value_name == on_state) else "Off"
        elif on_state is not None:
            on = value_name == on_state or value_name not in off_like
            chosen = on_state if on else "Off"
        else:
            chosen = value_name if value_name not in off_like else "Off"

        widget.mapping[PdfName("AS")] = PdfName(chosen)
        return True

    def convert_to_pdfa(
        self,
        level: str = "1b",
        *,
        font_lookup_directory: Optional[Union[str, Path]] = None,
    ) -> List[str]:
        """Convert this document to PDF/A format in-place.

        Modifies the COS object graph to satisfy the most common PDF/A
        structural requirements:

        - Removes ``/OpenAction`` and ``/AA`` (additional actions) from the
          document catalog.
        - Removes ``/JavaScript`` from the ``/Names`` tree.
        - Removes ``/EmbeddedFiles`` from the ``/Names`` tree (for levels other
          than PDF/A-3).
        - Ensures the ``/Info`` dictionary contains a ``/Title`` entry.
        - Injects an XMP metadata stream (``/Metadata``) into the catalog
          declaring the chosen PDF/A part and conformance level.
        - Adds an ``/OutputIntents`` array with an sRGB ICC v2 profile so that
          device colours are unambiguously specified.

        Font embedding is *not* performed automatically (it requires access to
        the raw font binary data).  Any unembedded fonts are reported as
        warnings in the returned list.

        Args:
            level: Target PDF/A conformance level string, e.g. ``"1b"``,
                ``"2b"``, ``"3b"``.  Case-insensitive.

        Returns:
            A list of remaining compliance issues that could *not* be fixed
            automatically (typically font-embedding warnings).  An empty list
            means the document should now pass ``check_pdfa_compliance()``.

        Raises:
            AsposePdfException: If the document is disposed, has no loaded COS
                structure (use ``from_file`` / ``from_bytes`` first), or is
                encrypted (PDF/A prohibits encryption).
        """
        self._ensure_not_disposed()

        if self._cos_doc is None:
            raise AsposePdfException(
                "convert_to_pdfa requires a document loaded from file or bytes. "
                "Use SimplePdf.from_file() or SimplePdf.from_bytes() first."
            )

        if self.encrypted:
            raise AsposePdfException(
                "PDF/A prohibits encryption. "
                "Decrypt the document before converting to PDF/A."
            )

        root = self._resolve(self._cos_doc.trailer.get(PdfName("Root")))
        if not isinstance(root, PdfDictionary):
            raise AsposePdfException(
                "Cannot locate the PDF catalog (/Root). The document may be corrupt."
            )

        level_norm = _normalize_pdfa_level_short(level)

        # 1. Remove prohibited content from the catalog
        for key in (PdfName("OpenAction"), PdfName("AA")):
            if key in root.mapping:
                del root.mapping[key]
        # Optional content (layers) is prohibited in PDF/A-1.
        if level_norm.startswith("1") and PdfName("OCProperties") in root.mapping:
            del root.mapping[PdfName("OCProperties")]
        # AcroForm: drop NeedAppearances and dynamic XFA.
        acro = self._resolve(root.mapping.get(PdfName("AcroForm")))
        if isinstance(acro, PdfDictionary):
            acro.mapping.pop(PdfName("NeedAppearances"), None)
            acro.mapping.pop(PdfName("XFA"), None)

        names_obj = self._resolve(root.mapping.get(PdfName("Names")))
        if isinstance(names_obj, PdfDictionary):
            if PdfName("JavaScript") in names_obj.mapping:
                del names_obj.mapping[PdfName("JavaScript")]
            if level.lower() not in ("3a", "3b", "3u"):
                if PdfName("EmbeddedFiles") in names_obj.mapping:
                    del names_obj.mapping[PdfName("EmbeddedFiles")]
                self.attachments.clear()
            else:
                # PDF/A-3 permits embedded files, but every Filespec must declare
                # an /AFRelationship. Stamp a default on any that lack one.
                ef_tree = self._resolve(
                    names_obj.mapping.get(PdfName("EmbeddedFiles"))
                )
                if isinstance(ef_tree, PdfDictionary):
                    for value in conformance._iter_name_tree_values(
                        self, ef_tree, set(), 0
                    ):
                        filespec = self._resolve(value)
                        if isinstance(filespec, PdfDictionary):
                            filespec.mapping.setdefault(
                                PdfName("AFRelationship"), PdfName("Unspecified")
                            )

        # Page-level remediation: strip page additional actions and normalise
        # annotation flags (set Print, clear Hidden/NoView/Invisible).
        self._pdfa_remediate_pages()

        # 2. Ensure /Info has a /Title; create /Info if absent
        title = self.metadata.get("Title", "").strip()
        if not title:
            # Also check the COS /Info directly (already-set title)
            info_ref = self._cos_doc.trailer.get(PdfName("Info"))
            info_dict_check = self._resolve(info_ref)
            if isinstance(info_dict_check, PdfDictionary):
                t = self._resolve(info_dict_check.mapping.get(PdfName("Title")))
                if isinstance(t, PdfString) and t.value:
                    title = (
                        t.value.decode("utf-8", errors="replace")
                        if isinstance(t.value, bytes)
                        else str(t.value)
                    )
            if not title:
                title = "Untitled"

        info_ref = self._cos_doc.trailer.get(PdfName("Info"))
        info_dict = self._resolve(info_ref)
        if isinstance(info_dict, PdfDictionary):
            existing_title = self._resolve(info_dict.mapping.get(PdfName("Title")))
            if not (isinstance(existing_title, PdfString) and existing_title.value):
                info_dict.mapping[PdfName("Title")] = PdfString(title)
        else:
            info_dict = PdfDictionary({PdfName("Title"): PdfString(title)})
            new_info_ref = self._cos_doc.register_object(info_dict)
            self._cos_doc.trailer.mapping[PdfName("Info")] = new_info_ref

        # 3. Inject XMP metadata stream into the catalog
        xmp_bytes = _make_pdfa_xmp(level, title)
        xmp_stream = PdfStream(
            content=xmp_bytes,
            mapping={
                PdfName("Type"): PdfName("Metadata"),
                PdfName("Subtype"): PdfName("XML"),
                PdfName("Length"): PdfNumber(len(xmp_bytes)),
            },
        )
        xmp_ref = self._cos_doc.register_object(xmp_stream)
        root.mapping[PdfName("Metadata")] = xmp_ref

        # 4. Add /OutputIntents with an sRGB ICC v2 profile
        icc_bytes = _minimal_srgb_icc_profile()
        icc_stream = PdfStream(
            content=icc_bytes,
            mapping={
                PdfName("N"): PdfNumber(3),
                PdfName("Alternate"): PdfName("DeviceRGB"),
                PdfName("Length"): PdfNumber(len(icc_bytes)),
            },
        )
        icc_ref = self._cos_doc.register_object(icc_stream)

        output_intent = PdfDictionary(
            {
                PdfName("Type"): PdfName("OutputIntent"),
                PdfName("S"): PdfName("GTS_PDFA1"),
                PdfName("OutputConditionIdentifier"): PdfString("sRGB IEC61966-2.1"),
                PdfName("Info"): PdfString("sRGB IEC61966-2.1"),
                PdfName("DestOutputProfile"): icc_ref,
            }
        )
        intent_ref = self._cos_doc.register_object(output_intent)

        intents_array = PdfArray([intent_ref])
        intents_ref = self._cos_doc.register_object(intents_array)
        root.mapping[PdfName("OutputIntents")] = intents_ref

        # 5. Ensure a trailer /ID is present (required by PDF/A).
        if PdfName("ID") not in self._cos_doc.trailer.mapping:
            id1 = EncryptionUtils.generate_file_id()
            id2 = EncryptionUtils.generate_file_id()
            self.file_id = [id1, id2]
            self._cos_doc.trailer.mapping[PdfName("ID")] = PdfArray(
                [PdfString(id1), PdfString(id2)]
            )

        # 6. Cap the PDF header version (PDF/A-1 -> 1.4, PDF/A-2/3 -> 1.7).
        max_version = 1.4 if level_norm.startswith("1") else 1.7
        current = conformance._parse_pdf_version(self.pdf_version or "")
        if current is None or current > max_version:
            self.pdf_version = "1.4" if max_version == 1.4 else "1.7"

        # 7. Automatic font embedding
        if font_lookup_directory:
            self._embed_missing_fonts(Path(font_lookup_directory))

        logger.info(
            "PDF/A conversion complete for level %s. Checking remaining issues.", level
        )

        # 8. Return any compliance issues that remain (e.g. font warnings,
        #    transparency or prohibited annotations that cannot be auto-fixed).
        return self.check_pdfa_compliance(level)

    def _pdfa_remediate_pages(self) -> None:
        """Strip page additional actions and normalise annotation flags.

        For every annotation other than ``/Popup`` this sets the Print flag and
        clears the Hidden, NoView and Invisible flags, matching the PDF/A rule
        that annotations be visible when printed. Prohibited annotation
        subtypes and transparency are *not* removed here (that would discard
        content); they remain in the issues returned by ``convert_to_pdfa``.
        """
        if self._cos_doc is None:
            return
        for i in range(len(self.pages)):
            page = self._get_page_dict(i)
            if not isinstance(page, PdfDictionary):
                continue
            page.mapping.pop(PdfName("AA"), None)
            annots = self._resolve(page.mapping.get(PdfName("Annots")))
            if not isinstance(annots, PdfArray):
                continue
            for ref in annots.items:
                annot = self._resolve(ref)
                if not isinstance(annot, PdfDictionary):
                    continue
                if self._get_name(annot.mapping.get(PdfName("Subtype"))) == "Popup":
                    continue
                flags_obj = self._resolve(annot.mapping.get(PdfName("F")))
                flags = int(flags_obj.value) if isinstance(flags_obj, PdfNumber) else 0
                flags |= conformance.ANNOT_FLAG_PRINT
                flags &= ~(
                    conformance.ANNOT_FLAG_HIDDEN
                    | conformance.ANNOT_FLAG_NOVIEW
                    | conformance.ANNOT_FLAG_INVISIBLE
                )
                annot.mapping[PdfName("F")] = PdfNumber(flags)

    def convert_to_pdfua(
        self,
        *,
        language: str = "en",
        title: Optional[str] = None,
        auto_tag: bool = False,
    ) -> List[str]:
        """Add the catalog-level PDF/UA prerequisites to this document in place.

        Creates the *structural shell* a PDF/UA-1 document needs at the catalog
        level: a ``/StructTreeRoot`` (an empty one is created if absent),
        ``/MarkInfo`` with ``/Marked true``, a document ``/Lang``,
        ``/ViewerPreferences`` with ``/DisplayDocTitle true``, an ``/Info``
        ``/Title``, and an XMP metadata stream declaring ``pdfuaid:part = 1``
        (merged into any existing packet so a combined PDF/A + PDF/UA identifier
        is preserved).

        This makes :meth:`check_pdfua_compliance` pass. By default it does **not**
        generate a real tag tree, alternate texts, or reading order — those
        require semantic knowledge of the content. Pass ``auto_tag=True`` to
        heuristically tag existing page text (see :meth:`auto_tag`) into a real
        (if coarse) structure tree first. Either way the result is a PDF/UA-ready
        document, not certified accessibility.

        Args:
            language: BCP 47 language tag written to the catalog ``/Lang``.
            title: Document title; falls back to any existing title, else
                ``"Untitled"``.
            auto_tag: When ``True``, infer a structure tree from existing page
                text before building the shell (one ``/P``/``/H1`` per text
                object). Images and fine reading order are still not inferred.

        Returns:
            Remaining PDF/UA issues (empty when the shell is complete).

        Raises:
            AsposePdfException: If the document is disposed, has no loaded COS
                structure, or is encrypted.
        """
        self._ensure_not_disposed()
        if self._cos_doc is None:
            raise AsposePdfException(
                "convert_to_pdfua requires a document loaded from file or bytes. "
                "Use SimplePdf.from_file() or SimplePdf.from_bytes() first."
            )
        if self.encrypted:
            raise AsposePdfException(
                "Decrypt the document before adding PDF/UA structure."
            )

        # Optionally infer a real tag tree from existing page text first; the
        # shell built below only fills the catalog-level prerequisites and uses
        # setdefault, so it never clobbers the auto-tagged structure.
        if auto_tag:
            self.auto_tag()

        root = self._resolve(self._cos_doc.trailer.get(PdfName("Root")))
        if not isinstance(root, PdfDictionary):
            raise AsposePdfException(
                "Cannot locate the PDF catalog (/Root). The document may be corrupt."
            )

        # Resolve a title (argument > metadata > existing /Info > "Untitled").
        title = (title or self.metadata.get("Title", "") or "").strip()
        info_ref = self._cos_doc.trailer.get(PdfName("Info"))
        info_dict = self._resolve(info_ref)
        if not title and isinstance(info_dict, PdfDictionary):
            existing = self._resolve(info_dict.mapping.get(PdfName("Title")))
            if isinstance(existing, PdfString) and existing.value:
                title = (
                    existing.value.decode("utf-8", errors="replace")
                    if isinstance(existing.value, bytes)
                    else str(existing.value)
                )
        if not title:
            title = "Untitled"

        # /Info /Title
        if isinstance(info_dict, PdfDictionary):
            info_dict.mapping[PdfName("Title")] = PdfString(title)
        else:
            info_dict = PdfDictionary({PdfName("Title"): PdfString(title)})
            self._cos_doc.trailer.mapping[PdfName("Info")] = (
                self._cos_doc.register_object(info_dict)
            )

        # /StructTreeRoot shell
        struct_root = self._resolve(root.mapping.get(PdfName("StructTreeRoot")))
        if not isinstance(struct_root, PdfDictionary):
            struct_root = PdfDictionary(
                {
                    PdfName("Type"): PdfName("StructTreeRoot"),
                    PdfName("K"): PdfArray([]),
                }
            )
            root.mapping[PdfName("StructTreeRoot")] = self._cos_doc.register_object(
                struct_root
            )
        else:
            struct_root.mapping.setdefault(PdfName("Type"), PdfName("StructTreeRoot"))
        # A /ParentTree is required once the tree carries marked content; add an
        # empty one so the shell is structurally complete and stays valid as
        # real tagging is layered on later.
        struct_root.mapping.setdefault(
            PdfName("ParentTree"), PdfDictionary({PdfName("Nums"): PdfArray([])})
        )

        # /MarkInfo /Marked true (and never /Suspects true)
        mark_info = self._resolve(root.mapping.get(PdfName("MarkInfo")))
        if not isinstance(mark_info, PdfDictionary):
            mark_info = PdfDictionary({})
            root.mapping[PdfName("MarkInfo")] = mark_info
        mark_info.mapping[PdfName("Marked")] = PdfBoolean(True)
        suspects = self._resolve(mark_info.mapping.get(PdfName("Suspects")))
        if isinstance(suspects, PdfBoolean) and suspects.value:
            mark_info.mapping[PdfName("Suspects")] = PdfBoolean(False)

        # /Lang
        root.mapping[PdfName("Lang")] = PdfString(language)

        # /ViewerPreferences /DisplayDocTitle true
        viewer = self._resolve(root.mapping.get(PdfName("ViewerPreferences")))
        if not isinstance(viewer, PdfDictionary):
            viewer = PdfDictionary({})
            root.mapping[PdfName("ViewerPreferences")] = viewer
        viewer.mapping[PdfName("DisplayDocTitle")] = PdfBoolean(True)

        # Pages carrying annotations require structure tab order (/Tabs /S).
        for i in range(len(self.pages)):
            page = self._get_page_dict(i)
            if not isinstance(page, PdfDictionary):
                continue
            annots = self._resolve(page.mapping.get(PdfName("Annots")))
            if isinstance(annots, PdfArray) and annots.items:
                page.mapping[PdfName("Tabs")] = PdfName("S")

        # XMP metadata declaring pdfuaid:part (merged into any existing packet).
        self._inject_pdfua_xmp(root, title)

        logger.info("PDF/UA structure added; checking remaining issues.")
        return self.check_pdfua_compliance()[0]

    def _inject_pdfua_xmp(self, root: PdfDictionary, title: str) -> None:
        """Ensure the catalog ``/Metadata`` XMP declares ``pdfuaid:part = 1``.

        Merges into an existing XMP packet when one is present so a document
        that is already PDF/A keeps its ``pdfaid`` identifier.
        """
        xmp_bytes: Optional[bytes] = None
        existing = self._resolve(root.mapping.get(PdfName("Metadata")))
        if isinstance(existing, PdfStream):
            try:
                packet = XmpPacket.parse(existing.content)
                packet.set_value(
                    "pdfuaid",
                    "part",
                    "1",
                    uri=STANDARD_XMP_NAMESPACES["pdfuaid"],
                )
                if packet.get("dc", "title") is None:
                    packet.add(
                        XmpField(
                            prefix="dc",
                            name="title",
                            namespace_uri=STANDARD_XMP_NAMESPACES["dc"],
                            value=XmpArray(
                                kind="Alt",
                                items=[XmpField(value=title, language="x-default")],
                            ),
                        )
                    )
                xmp_bytes = serialize_xmp(packet)
            except PDF_OPERATION_ERRORS:
                xmp_bytes = None
        if xmp_bytes is None:
            xmp_bytes = _make_pdfua_xmp(title)

        xmp_stream = PdfStream(
            content=xmp_bytes,
            mapping={
                PdfName("Type"): PdfName("Metadata"),
                PdfName("Subtype"): PdfName("XML"),
                PdfName("Length"): PdfNumber(len(xmp_bytes)),
            },
        )
        root.mapping[PdfName("Metadata")] = self._cos_doc.register_object(xmp_stream)

    def _embed_missing_fonts(self, lookup_dir: Path) -> None:
        """Attempt to embed missing fonts from the lookup directory."""
        if not self._cos_doc or not lookup_dir.is_dir():
            return

        for i in range(len(self.pages)):
            page_dict = self._get_page_dict(i)
            if not isinstance(page_dict, PdfDictionary):
                continue
            res = self._resolve(page_dict.get(PdfName("Resources")))
            if not isinstance(res, PdfDictionary):
                continue
            fonts = self._resolve(res.get(PdfName("Font")))
            if not isinstance(fonts, PdfDictionary):
                continue

            for font_ref in fonts.mapping.values():
                font = self._resolve(font_ref)
                if not isinstance(font, PdfDictionary):
                    continue

                descriptor = self._resolve(font.get(PdfName("FontDescriptor")))
                if not isinstance(descriptor, PdfDictionary):
                    continue

                # Check if already embedded
                embedded = any(
                    PdfName(k) in descriptor
                    for k in ["FontFile", "FontFile2", "FontFile3"]
                )
                if embedded:
                    continue

                base_font = self._get_name(font.get(PdfName("BaseFont")))
                if not base_font:
                    continue

                # Clean base font name (remove subset prefix if any, e.g. ABCDEF+Arial -> Arial)
                font_name = base_font.split("+")[-1]

                # Search for font file in lookup directory
                font_file = None
                for ext in [".ttf", ".otf", ".TTF", ".OTF"]:
                    candidate = lookup_dir / (font_name + ext)
                    if candidate.is_file():
                        font_file = candidate
                        break

                if font_file:
                    try:
                        font_data = font_file.read_bytes()
                        font_stream = PdfStream(
                            content=font_data,
                            mapping={
                                PdfName("Length1"): PdfNumber(len(font_data)),
                                PdfName("Length"): PdfNumber(len(font_data)),
                            },
                        )
                        # For TrueType fonts, use FontFile2
                        key = (
                            "FontFile2"
                            if font_file.suffix.lower() == ".ttf"
                            else "FontFile"
                        )
                        stream_ref = self._cos_doc.register_object(font_stream)
                        descriptor.mapping[PdfName(key)] = stream_ref
                        logger.info(f"Embedded font {base_font} from {font_file}")
                    except PDF_OPERATION_ERRORS as e:
                        logger.error(f"Failed to embed font {base_font}: {e}")

    def set_watermark(self, text: str) -> None:
        """Set watermark text."""
        self._ensure_not_disposed()
        self.watermark_text = text


# ---------------------------------------------------------------------------
# COS Extractor — extracts high-level data from PdfDocument COS graph
# ---------------------------------------------------------------------------
class CosExtractor:
    """Extract pages, streams, images and metadata from a PdfDocument.

    Works entirely on the typed COS object model produced by PdfCosParser.
    """

    def __init__(
        self,
        doc: PdfDocument,
        raw_data: bytes,
        *,
        stream_decrypt_key: Optional[bytes] = None,
        stream_decrypt_algorithm: str = "AES-256",
    ) -> None:
        self._doc = doc
        self._raw = raw_data
        self._stream_decrypt_key = stream_decrypt_key
        self._stream_decrypt_algorithm = stream_decrypt_algorithm
        from .cos import PdfName

        # cache frequently used names
        self._N = PdfName
        self._image_sizes: Dict[str, Tuple[int, int]] = {}
        self._image_meta: Dict[str, Dict[str, Any]] = {}
        self._page_obj_ids: List[int] = []
        self._content_obj_ids: List[int] = []

    def attach_stream_decryption(self, password: str) -> None:
        """Configure per-stream decryption after the password is verified."""
        if not self.detect_encryption():
            return
        key = self.extract_decryption_key(password)
        if key is not None:
            self._stream_decrypt_key = key
            self._stream_decrypt_algorithm = (
                self.standard_handler_encryption_algorithm()
            )

    # ----- helpers ----------------------------------------------------------
    def _resolve(self, obj: Any) -> Any:
        """Dereference an indirect reference, returning the actual object."""
        if isinstance(obj, PdfIndirectReference):
            return self._doc.objects.get(obj.object_number)
        return obj

    def _get_name(self, obj: Any) -> Optional[str]:
        """Return the string value of a PdfName, or None."""
        from .cos import PdfName

        obj = self._resolve(obj)
        if isinstance(obj, PdfName):
            return obj.name.lstrip("/")
        return None

    def _get_number(self, obj: Any) -> Optional[float]:
        from .cos import PdfNumber

        obj = self._resolve(obj)
        if isinstance(obj, PdfNumber):
            return obj.value
        return None

    def _get_dict(self, obj: Any) -> Optional[Any]:
        from .cos import PdfDictionary

        obj = self._resolve(obj)
        if isinstance(obj, PdfDictionary):
            return obj
        return None

    def _get_array(self, obj: Any) -> Optional[Any]:
        from .cos import PdfArray

        obj = self._resolve(obj)
        if isinstance(obj, PdfArray):
            return obj
        return None

    def _dict_get(self, d: Any, key: str) -> Any:
        """Lookup *key* in a PdfDictionary (resolving the value reference)."""
        from .cos import PdfName, PdfDictionary

        if not isinstance(d, PdfDictionary):
            return None
        val = d.mapping.get(PdfName(key))
        return self._resolve(val) if val is not None else None

    # ----- page tree --------------------------------------------------------
    def _traverse_page_tree(
        self,
        node_ref: Any,
        pages_out: list,
        contents_out: list,
        images_out: dict,
        page_image_map: dict,
        fonts_out: dict,
        extgstates_out: dict,
    ) -> None:
        from .cos import PdfDictionary, PdfArray, PdfName, PdfStream

        node = self._resolve(node_ref)
        if not isinstance(node, PdfDictionary):
            return

        obj_id = (
            node_ref.object_number if isinstance(node_ref, PdfIndirectReference) else 0
        )

        node_type = self._get_name(node.mapping.get(PdfName("Type")))

        if node_type == "Page":
            # -- MediaBox ---------------------------------------------------
            mbox_obj = self._dict_get(node, "MediaBox")
            if isinstance(mbox_obj, PdfArray) and len(mbox_obj.items) >= 4:
                try:
                    mbox = tuple(
                        float(self._get_number(v) or 0) for v in mbox_obj.items[:4]
                    )
                except (TypeError, ValueError):
                    mbox = (0, 0, 612, 792)
            else:
                mbox = (0, 0, 612, 792)
            pages_out.append(mbox)
            self._page_obj_ids.append(obj_id)

            # -- Contents stream(s) -----------------------------------------
            contents_ref = node.mapping.get(PdfName("Contents"))
            if isinstance(contents_ref, PdfIndirectReference):
                self._content_obj_ids.append(contents_ref.object_number)
            else:
                self._content_obj_ids.append(0)

            content_bytes = self._read_content_stream(contents_ref)
            contents_out.append(content_bytes)

            # -- Images from /Resources/XObject -----------------------------
            page_idx = len(pages_out) - 1
            resources = self._dict_get(node, "Resources")
            if resources:
                xobj = self._dict_get(resources, "XObject")
                if isinstance(xobj, PdfDictionary):
                    for name_key, ref in xobj.mapping.items():
                        img_obj = self._resolve(ref)
                        if isinstance(img_obj, PdfStream):
                            subtype = self._get_name(
                                img_obj.mapping.get(PdfName("Subtype"))
                            )
                            if subtype == "Image":
                                img_name = name_key.name.lstrip("/")
                                # Lazy decoding: store a loader instead of bytes
                                if isinstance(images_out, LazyImageDict):

                                    def make_loader(
                                        s=img_obj,
                                        r=ref,
                                    ):
                                        return self._decode_stream(
                                            s,
                                            r
                                            if isinstance(
                                                r,
                                                PdfIndirectReference,
                                            )
                                            else None,
                                        )

                                    images_out.add_loader(img_name, make_loader)
                                else:
                                    images_out[img_name] = self._decode_stream(
                                        img_obj,
                                        ref
                                        if isinstance(ref, PdfIndirectReference)
                                        else None,
                                    )
                                # Extract dimensions
                                w = self._get_number(
                                    img_obj.mapping.get(PdfName("Width"))
                                )
                                h = self._get_number(
                                    img_obj.mapping.get(PdfName("Height"))
                                )
                                if w is not None and h is not None:
                                    self._image_sizes[img_name] = (int(w), int(h))
                                self._image_meta[img_name] = self._resolve_image_meta(
                                    img_obj
                                )
                                page_image_map.setdefault(page_idx, []).append(img_name)

                # -- Fonts --------------------------------------------------
                fonts = self._dict_get(resources, "Font")
                if isinstance(fonts, PdfDictionary):
                    for name_key, ref in fonts.mapping.items():
                        font_name = name_key.name.lstrip("/")
                        fonts_out[font_name] = self._resolve(ref)

                # -- ExtGState ----------------------------------------------
                extgs = self._dict_get(resources, "ExtGState")
                if isinstance(extgs, PdfDictionary):
                    for name_key, ref in extgs.mapping.items():
                        gs_name = name_key.name.lstrip("/")
                        extgstates_out[gs_name] = self._resolve(ref)
            return

        # Pages node — recurse into /Kids
        kids = node.mapping.get(PdfName("Kids"))
        kids_arr = self._resolve(kids)
        if isinstance(kids_arr, PdfArray):
            for child_ref in kids_arr.items:
                self._traverse_page_tree(
                    child_ref,
                    pages_out,
                    contents_out,
                    images_out,
                    page_image_map,
                    fonts_out,
                    extgstates_out,
                )

    def _read_content_stream(self, ref: Any) -> bytes:
        """Read and decode a /Contents entry (may be stream or array of streams)."""
        from .cos import PdfArray, PdfStream, PdfIndirectReference

        obj = self._resolve(ref)
        if isinstance(obj, PdfStream):
            src_ref = ref if isinstance(ref, PdfIndirectReference) else None
            return self._decode_stream(obj, src_ref)
        if isinstance(obj, PdfArray):
            parts = []
            for item in obj.items:
                resolved = self._resolve(item)
                if isinstance(resolved, PdfStream):
                    src_ref = item if isinstance(item, PdfIndirectReference) else None
                    parts.append(self._decode_stream(resolved, src_ref))
            return b"\n".join(parts)
        return b""

    def _decode_stream(self, stream: Any, source_ref: Any = None) -> bytes:
        """Decode a PdfStream: Standard security decryption (if configured), then filters."""
        from .cos import (
            PdfName,
            PdfStream as PdfStreamCls,
            PdfArray,
            PdfIndirectReference,
        )

        if not isinstance(stream, PdfStreamCls):
            return b""

        data = stream.content
        obj_id = 0
        if isinstance(source_ref, PdfIndirectReference):
            obj_id = source_ref.object_number
        if self._stream_decrypt_key and data:
            data = EncryptionUtils.decrypt_writer_encrypted_stream(
                self._stream_decrypt_key,
                self._stream_decrypt_algorithm,
                obj_id,
                data,
            )

        filter_obj = stream.mapping.get(PdfName("Filter"))
        parms_obj = stream.mapping.get(PdfName("DecodeParms"))

        if filter_obj is None:
            return data

        # Normalize filter list
        filter_obj = self._resolve(filter_obj)
        if isinstance(filter_obj, PdfName):
            filter_names = [filter_obj.name.lstrip("/")]
        elif isinstance(filter_obj, PdfArray):
            filter_names = [self._get_name(f) or "" for f in filter_obj.items]
        else:
            return data

        # Normalize parms list
        parms_obj = self._resolve(parms_obj)
        if isinstance(parms_obj, PdfArray):
            parms_list = [
                self._cos_dict_to_plain(self._resolve(p)) for p in parms_obj.items
            ]
        elif parms_obj is not None:
            parms_list = [self._cos_dict_to_plain(parms_obj)]
        else:
            parms_list = [None] * len(filter_names)

        # Pad if needed
        while len(parms_list) < len(filter_names):
            parms_list.append(None)

        try:
            return StreamDecoder.decode(
                data,
                filter_names if len(filter_names) > 1 else filter_names[0],
                parms_list[0] if len(parms_list) == 1 else parms_list,
            )
        except PDF_STREAM_DECODE_ERRORS:
            return data

    def _cos_dict_to_plain(self, obj: Any) -> Optional[dict]:
        """Convert PdfDictionary to plain dict with string keys for StreamDecoder."""
        from .cos import PdfDictionary, PdfNumber, PdfName, PdfBoolean

        if not isinstance(obj, PdfDictionary):
            return None
        result = {}
        for k, v in obj.mapping.items():
            key = k.name.lstrip("/")
            v_resolved = self._resolve(v)
            if isinstance(v_resolved, PdfNumber):
                result[key] = v_resolved.value
            elif isinstance(v_resolved, PdfName):
                result[key] = v_resolved.name.lstrip("/")
            elif isinstance(v_resolved, PdfBoolean):
                result[key] = v_resolved.value
            else:
                result[key] = v_resolved
        return result

    # ----- public API -------------------------------------------------------
    def extract_pages(self) -> List[Tuple[float, float, float, float]]:
        pages: list = []
        contents: list = []
        images = LazyImageDict()
        pmap: dict = {}
        fonts: dict = {}
        extgs: dict = {}
        root = self._dict_get(self._doc.trailer, "Root")
        if root:
            pages_node = self._dict_get(root, "Pages")
            if pages_node:
                self._traverse_page_tree(
                    pages_node, pages, contents, images, pmap, fonts, extgs
                )
        self._cached_contents = contents
        self._cached_images = images
        self._cached_pmap = pmap
        self._cached_fonts = fonts
        self._cached_extgs = extgs
        # Update SimplePdf fields with extracted IDs
        self._cached_page_obj_ids = self._page_obj_ids
        self._cached_content_obj_ids = self._content_obj_ids
        return pages

    def extract_page_contents(self) -> List[bytes]:
        if hasattr(self, "_cached_contents"):
            return self._cached_contents
        return []

    # ----- lazy page tree traversal (for streaming mode) --------------------

    def _traverse_page_tree_lazy(
        self,
        node_ref: Any,
        pages_out: list,
        images_out: dict,
        page_image_map: dict,
        fonts_out: dict,
        extgstates_out: dict,
    ) -> None:
        """Traverse the page tree collecting MediaBoxes, object IDs, and resource metadata.

        Unlike ``_traverse_page_tree`` this method does **not** decode content
        streams. It records page bounding boxes and COS object numbers, and
        discovers image/font metadata so they are available in lazy mode.
        """
        from .cos import PdfDictionary, PdfArray, PdfName, PdfStream

        node = self._resolve(node_ref)
        if not isinstance(node, PdfDictionary):
            return

        node_type = self._get_name(node.mapping.get(PdfName("Type")))

        if node_type == "Page":
            mbox_obj = self._dict_get(node, "MediaBox")
            if isinstance(mbox_obj, PdfArray) and len(mbox_obj.items) >= 4:
                try:
                    mbox = tuple(
                        float(self._get_number(v) or 0) for v in mbox_obj.items[:4]
                    )
                except (TypeError, ValueError):
                    mbox = (0, 0, 612, 792)
            else:
                mbox = (0, 0, 612, 792)
            pages_out.append(mbox)
            obj_id = (
                node_ref.object_number
                if isinstance(node_ref, PdfIndirectReference)
                else 0
            )
            self._page_obj_ids.append(obj_id)

            # Image and resource discovery (LZY-01)
            page_idx = len(pages_out) - 1
            resources = self._dict_get(node, "Resources")
            if resources:
                xobj = self._dict_get(resources, "XObject")
                if isinstance(xobj, PdfDictionary):
                    for name_key, ref in xobj.mapping.items():
                        img_obj = self._resolve(ref)
                        if isinstance(img_obj, PdfStream):
                            subtype = self._get_name(
                                img_obj.mapping.get(PdfName("Subtype"))
                            )
                            if subtype == "Image":
                                img_name = name_key.name.lstrip("/")
                                # Lazy decoding: store a loader
                                if isinstance(images_out, LazyImageDict):
                                    images_out.add_loader(
                                        img_name,
                                        lambda s=img_obj, r=ref: self._decode_stream(
                                            s,
                                            r
                                            if isinstance(
                                                r,
                                                PdfIndirectReference,
                                            )
                                            else None,
                                        ),
                                    )
                                else:
                                    # Fallback if not using LazyImageDict
                                    pass

                                # Extract dimensions for Absorbers
                                w = self._get_number(
                                    img_obj.mapping.get(PdfName("Width"))
                                )
                                h = self._get_number(
                                    img_obj.mapping.get(PdfName("Height"))
                                )
                                if w is not None and h is not None:
                                    self._image_sizes[img_name] = (int(w), int(h))
                                self._image_meta[img_name] = self._resolve_image_meta(
                                    img_obj
                                )
                                page_image_map.setdefault(page_idx, []).append(img_name)

                # Collect Font metadata
                fonts = self._dict_get(resources, "Font")
                if isinstance(fonts, PdfDictionary):
                    for name_key, ref in fonts.mapping.items():
                        font_name = name_key.name.lstrip("/")
                        fonts_out[font_name] = self._resolve(ref)

                # Collect ExtGState metadata
                extgs = self._dict_get(resources, "ExtGState")
                if isinstance(extgs, PdfDictionary):
                    for name_key, ref in extgs.mapping.items():
                        gs_name = name_key.name.lstrip("/")
                        extgstates_out[gs_name] = self._resolve(ref)
            return

        # Pages node — recurse into /Kids
        kids = node.mapping.get(PdfName("Kids"))
        kids_arr = self._resolve(kids)
        if isinstance(kids_arr, PdfArray):
            for child_ref in kids_arr.items:
                self._traverse_page_tree_lazy(
                    child_ref,
                    pages_out,
                    images_out,
                    page_image_map,
                    fonts_out,
                    extgstates_out,
                )

    def extract_pages_lazy(
        self,
        images_out: dict,
        page_image_map: dict,
        fonts_out: dict,
        extgstates_out: dict,
    ) -> List[Tuple[float, float, float, float]]:
        """Return page MediaBoxes and discover resources without decoding streams.

        Page object IDs are stored in ``self._page_obj_ids`` so that
        :meth:`get_page_content` can decode content on demand.
        """
        pages: list = []
        root = self._dict_get(self._doc.trailer, "Root")
        if root:
            pages_node = self._dict_get(root, "Pages")
            if pages_node:
                self._traverse_page_tree_lazy(
                    pages_node,
                    pages,
                    images_out,
                    page_image_map,
                    fonts_out,
                    extgstates_out,
                )
        self._cached_page_obj_ids = list(self._page_obj_ids)
        return pages

    def get_page_content(self, page_index: int) -> bytes:
        """Decode and return the content stream for the page at *page_index*.

        Used in streaming/lazy mode where ``page_contents`` is not pre-loaded.
        """
        ids = getattr(self, "_page_obj_ids", [])
        if page_index >= len(ids):
            return b""
        page_obj_id = ids[page_index]
        page_obj = self._doc.objects.get(page_obj_id)
        if page_obj is None:
            return b""
        from .cos import PdfName

        contents_ref = page_obj.mapping.get(PdfName("Contents"))
        if contents_ref is None:
            return b""
        return self._read_content_stream(contents_ref)

    def extract_fonts(self) -> Dict[str, Any]:
        if hasattr(self, "_cached_fonts"):
            return self._cached_fonts
        return {}

    def extract_extgstates(self) -> Dict[str, Any]:
        if hasattr(self, "_cached_extgs"):
            return self._cached_extgs
        return {}

    def extract_images(self) -> Dict[str, bytes]:
        if hasattr(self, "_cached_images"):
            return self._cached_images
        return {}

    def extract_images_per_page(self) -> Dict[int, List[str]]:
        if hasattr(self, "_cached_pmap"):
            return self._cached_pmap
        return {}

    def extract_image_sizes(self) -> Dict[str, Tuple[int, int]]:
        return dict(self._image_sizes)

    def extract_image_meta(self) -> Dict[str, Dict[str, Any]]:
        """Return per-image reconstruction metadata gathered during traversal."""
        return dict(self._image_meta)

    def _resolve_image_meta(self, img_obj: Any) -> Dict[str, Any]:
        """Resolve reconstruction metadata for an image XObject stream.

        Captures geometry, bits-per-component, the terminal stream filter, the
        ``/Decode`` array, soft-mask presence, and a resolved colour space
        (including an ``/Indexed`` palette) so the image can later be rebuilt as
        a real file by :mod:`aspose_pdf.engine.image_export`.
        """
        from .cos import PdfArray, PdfBoolean, PdfName, PdfStream

        m = img_obj.mapping
        meta: Dict[str, Any] = {}
        w = self._get_number(m.get(PdfName("Width")))
        h = self._get_number(m.get(PdfName("Height")))
        meta["width"] = int(w) if w is not None else 0
        meta["height"] = int(h) if h is not None else 0
        bpc = self._get_number(m.get(PdfName("BitsPerComponent")))
        meta["bpc"] = int(bpc) if bpc is not None else 8

        im = self._resolve(m.get(PdfName("ImageMask")))
        is_mask = isinstance(im, PdfBoolean) and im.value
        if is_mask:
            meta["bpc"] = 1
            meta["cs_kind"] = "gray"
            meta["n_comps"] = 1

        filt = self._resolve(m.get(PdfName("Filter")))
        names: List[str] = []
        if isinstance(filt, PdfName):
            names = [filt.name.lstrip("/")]
        elif isinstance(filt, PdfArray):
            names = [self._get_name(f) or "" for f in filt.items]
        meta["filter"] = names[-1] if names else None

        decode = self._resolve(m.get(PdfName("Decode")))
        if isinstance(decode, PdfArray):
            vals: List[float] = []
            for it in decode.items:
                n = self._get_number(it)
                vals.append(float(n) if n is not None else 0.0)
            meta["decode"] = vals

        meta["smask"] = isinstance(self._resolve(m.get(PdfName("SMask"))), PdfStream)

        if not is_mask:
            cs = self._resolve(m.get(PdfName("ColorSpace")))
            kind, ncomps, palette, base_comps = self._resolve_colorspace(cs)
            meta["cs_kind"] = kind
            if ncomps is not None:
                meta["n_comps"] = ncomps
            if palette is not None:
                meta["palette"] = palette
                meta["palette_base_comps"] = base_comps
        return meta

    def _resolve_colorspace(self, cs: Any, _depth: int = 0):
        """Resolve a ``/ColorSpace`` to ``(kind, n_comps, palette, base_comps)``.

        ``kind`` is one of ``"gray"``/``"rgb"``/``"cmyk"``/``"indexed"``/
        ``"unknown"``. For indexed spaces ``palette`` holds the lookup bytes and
        ``base_comps`` the components per palette entry.
        """
        from .cos import PdfArray, PdfName, PdfStream, PdfString

        if _depth > 4:
            return ("unknown", None, None, None)
        if isinstance(cs, PdfName):
            name = cs.name.lstrip("/")
            if name in ("DeviceGray", "G", "CalGray"):
                return ("gray", 1, None, None)
            if name in ("DeviceRGB", "RGB", "CalRGB"):
                return ("rgb", 3, None, None)
            if name in ("DeviceCMYK", "CMYK"):
                return ("cmyk", 4, None, None)
            return ("unknown", None, None, None)
        if isinstance(cs, PdfArray) and cs.items:
            head = self._get_name(cs.items[0])
            if head == "ICCBased" and len(cs.items) >= 2:
                stream = self._resolve(cs.items[1])
                n = (
                    self._get_number(stream.mapping.get(PdfName("N")))
                    if isinstance(stream, PdfStream)
                    else None
                )
                n = int(n) if n is not None else None
                kind = {1: "gray", 3: "rgb", 4: "cmyk"}.get(n or 0, "unknown")
                return (kind, n, None, None)
            if head in ("Indexed", "I") and len(cs.items) >= 4:
                base = self._resolve(cs.items[1])
                _, base_comps, _, _ = self._resolve_colorspace(base, _depth + 1)
                lookup = self._resolve(cs.items[3])
                palette = None
                if isinstance(lookup, PdfString):
                    palette = bytes(lookup.value)
                elif isinstance(lookup, PdfStream):
                    try:
                        palette = self._decode_stream(lookup, cs.items[3])
                    except PDF_STREAM_DECODE_ERRORS:
                        palette = lookup.content
                return ("indexed", 1, palette, base_comps or 3)
            if head in ("CalRGB", "Lab"):
                return ("rgb", 3, None, None)
            if head == "CalGray":
                return ("gray", 1, None, None)
            if head == "DeviceN" and len(cs.items) >= 2:
                colorants = self._resolve(cs.items[1])
                nc = (
                    len(colorants.items) if isinstance(colorants, PdfArray) else None
                )
                return ("unknown", nc, None, None)
            if head == "Separation":
                return ("gray", 1, None, None)
        return ("unknown", None, None, None)

    def extract_image_placements(
        self,
    ) -> Tuple[
        Dict[Tuple[int, str], Tuple[float, ...]],
        Dict[Tuple[int, str], Tuple[float, float, float, float]],
    ]:
        """Extract image placement matrix and rect from page content streams.

        Returns (matrix_map, rect_map) where keys are (page_idx, image_name).
        matrix is (a,b,c,d,e,f), rect is (x,y,width,height) in PDF points.
        """
        matrix_map: Dict[Tuple[int, str], Tuple[float, ...]] = {}
        rect_map: Dict[Tuple[int, str], Tuple[float, float, float, float]] = {}
        contents = getattr(self, "_cached_contents", [])
        _ = getattr(self, "_cached_pmap", {})  # pmap

        for page_idx, content in enumerate(contents):
            placements = parse_image_placements_from_content(content)
            for name, matrix_dec in placements:
                key = (page_idx, name)
                if key not in matrix_map:
                    matrix_map[key] = affine_decimal_to_float(matrix_dec)
                    w, h = self._image_sizes.get(name, (1, 1))
                    rect_map[key] = image_placement_bbox(matrix_dec, w, h)
        return matrix_map, rect_map

    def extract_metadata(self) -> Dict[str, str]:
        from .cos import PdfName, PdfString, PdfNumber, PdfDictionary

        info_ref = self._doc.trailer.mapping.get(PdfName("Info"))
        info = self._resolve(info_ref) if info_ref else None
        if not isinstance(info, PdfDictionary):
            return {}
        metadata: Dict[str, str] = {}
        for k, v in info.mapping.items():
            key = k.name.lstrip("/")
            v_resolved = self._resolve(v)
            if isinstance(v_resolved, PdfString):
                raw = v_resolved.value
                if isinstance(raw, bytes):
                    metadata[key] = raw.decode("utf-8", errors="ignore")
                else:
                    metadata[key] = str(raw)
            elif isinstance(v_resolved, PdfNumber):
                metadata[key] = str(v_resolved.value)
            else:
                metadata[key] = str(v_resolved) if v_resolved else ""
        return metadata

    def standard_handler_encryption_algorithm(self) -> str:
        """Return ``AES-256``, ``AES-128``, or ``RC4`` for Standard security handler."""
        from .cos import PdfName, PdfDictionary, PdfNumber

        enc_ref = self._doc.trailer.mapping.get(PdfName("Encrypt"))
        enc = self._resolve(enc_ref)
        if not isinstance(enc, PdfDictionary):
            return "AES-256"
        filt = self._get_name(enc.mapping.get(PdfName("Filter")))
        if filt is not None and filt != "Standard":
            return "AES-256"
        v = self._get_number(enc.mapping.get(PdfName("V")))
        r_obj = self._resolve(enc.mapping.get(PdfName("R")))
        r: Optional[int] = None
        if isinstance(r_obj, PdfNumber):
            r = int(r_obj.value)
        if v is not None and v >= 5 and r is not None and r >= 5:
            return "AES-256"
        if v == 1 or r == 2:
            return "RC4"
        return "AES-128"

    def extract_decryption_key(self, password: str) -> Optional[bytes]:
        """Derive the file encryption key if *password* unlocks Standard encryption."""
        if not self.detect_encryption():
            return None
        from .cos import PdfName, PdfDictionary, PdfString, PdfNumber, PdfBoolean

        enc_ref = self._doc.trailer.mapping.get(PdfName("Encrypt"))
        enc = self._resolve(enc_ref)
        if not isinstance(enc, PdfDictionary):
            return None

        def as_bytes(obj: Any) -> Optional[bytes]:
            o = self._resolve(obj)
            if isinstance(o, PdfString):
                raw = o.value
                return raw if isinstance(raw, bytes) else raw.encode("latin-1")
            return None

        u_b = as_bytes(enc.mapping.get(PdfName("U")))
        o_b = as_bytes(enc.mapping.get(PdfName("O")))
        if u_b is None or o_b is None or len(u_b) < 16 or len(o_b) < 16:
            return None

        v = self._get_number(enc.mapping.get(PdfName("V")))
        r_obj = self._resolve(enc.mapping.get(PdfName("R")))
        r: Optional[int] = None
        if isinstance(r_obj, PdfNumber):
            r = int(r_obj.value)

        p_val = self.extract_permissions()
        fid = self.extract_file_id()
        file_id = fid[0] if fid else b""

        encrypt_metadata = True
        em_obj = self._resolve(enc.mapping.get(PdfName("EncryptMetadata")))
        if isinstance(em_obj, PdfBoolean):
            encrypt_metadata = bool(em_obj.value)

        if v is not None and v >= 5 and r is not None and r >= 5:
            ue_b = as_bytes(enc.mapping.get(PdfName("UE"))) or b""
            oe_b = as_bytes(enc.mapping.get(PdfName("OE"))) or b""
            user_material = len(u_b) >= 48 and bool(ue_b)
            owner_material = len(o_b) >= 48 and bool(oe_b)
            if not user_material and not owner_material:
                raise PdfSecurityException(
                    "Cannot verify password: encryption uses revision 5+ security "
                    "but /UE and /OE (or valid /U / /O lengths) are missing"
                )
            return EncryptionUtils.verify_password_v6(password, u_b, o_b, ue_b, oe_b)

        key_length = 16
        length_obj = self._resolve(enc.mapping.get(PdfName("Length")))
        if isinstance(length_obj, PdfNumber):
            Lbits = int(length_obj.value)
            key_length = max(5, min(32, Lbits // 8))
        if r == 2:
            key_length = 5

        rev = r if r is not None else 4
        return EncryptionUtils.verify_password_v4(
            password,
            u_b,
            o_b,
            p_val,
            file_id,
            key_length,
            rev,
            encrypt_metadata,
        )

    def encryption_password_allows_access(self, password: str) -> bool:
        """True if *password* is correct, or verification is unavailable (minimal /Encrypt dict)."""
        if not self.detect_encryption():
            return True
        from .cos import PdfName, PdfDictionary, PdfString

        enc_ref = self._doc.trailer.mapping.get(PdfName("Encrypt"))
        enc = self._resolve(enc_ref)
        if not isinstance(enc, PdfDictionary):
            return True

        def as_bytes(obj: Any) -> Optional[bytes]:
            o = self._resolve(obj)
            if isinstance(o, PdfString):
                raw = o.value
                return raw if isinstance(raw, bytes) else raw.encode("latin-1")
            return None

        u_b = as_bytes(enc.mapping.get(PdfName("U")))
        o_b = as_bytes(enc.mapping.get(PdfName("O")))
        if u_b is None or o_b is None or len(u_b) < 16 or len(o_b) < 16:
            return True

        return self.extract_decryption_key(password) is not None

    def detect_encryption(self) -> bool:
        from .cos import PdfName

        return PdfName("Encrypt") in self._doc.trailer.mapping

    def extract_file_id(self) -> Optional[List[bytes]]:
        """Return the two-element /ID array from the trailer, or None."""
        from .cos import PdfName, PdfArray, PdfString

        id_ref = self._doc.trailer.mapping.get(PdfName("ID"))
        id_obj = self._resolve(id_ref)
        if not isinstance(id_obj, PdfArray) or len(id_obj.items) < 2:
            return None
        result: List[bytes] = []
        for item in id_obj.items[:2]:
            item = self._resolve(item)
            if isinstance(item, PdfString):
                raw = item.value
                result.append(raw if isinstance(raw, bytes) else raw.encode("latin-1"))
            else:
                result.append(b"")
        return result

    def extract_permissions(self) -> int:
        """Return the /P integer from the /Encrypt dictionary, or -4."""
        from .cos import PdfName, PdfDictionary, PdfNumber

        enc_ref = self._doc.trailer.mapping.get(PdfName("Encrypt"))
        enc = self._resolve(enc_ref)
        if isinstance(enc, PdfDictionary):
            p_val = enc.mapping.get(PdfName("P"))
            p_val = self._resolve(p_val)
            if isinstance(p_val, PdfNumber):
                return int(p_val.value)
        return -4

    def extract_outlines(self) -> List[Dict]:
        """Return a list of outline-item dicts from the catalog /Outlines tree."""
        from .cos import PdfName, PdfDictionary

        root_ref = self._doc.trailer.mapping.get(PdfName("Root"))
        root = self._resolve(root_ref)
        if not isinstance(root, PdfDictionary):
            return []
        outlines_ref = root.mapping.get(PdfName("Outlines"))
        outlines = self._resolve(outlines_ref)
        if not isinstance(outlines, PdfDictionary):
            return []
        first_ref = outlines.mapping.get(PdfName("First"))
        if _outline_link_absent(first_ref):
            return []
        return self._collect_outline_items(first_ref)

    def _collect_outline_items(self, item_ref: Any, depth: int = 0) -> List[Dict]:
        """Recursively walk the outline linked list, returning dicts.

        Raises
        ------
        PdfParseException
            If the outline tree is cyclic, truncated by a non-dictionary link,
            references a missing object, or nests deeper than
            :data:`OUTLINE_TREE_MAX_DEPTH`.
        """
        from .cos import PdfName, PdfDictionary, PdfArray, PdfString, PdfNumber

        if depth > OUTLINE_TREE_MAX_DEPTH:
            raise PdfParseException(
                f"Outline tree nesting exceeds maximum depth ({OUTLINE_TREE_MAX_DEPTH})"
            )
        items: List[Dict] = []
        visited: set = set()
        while not _outline_link_absent(item_ref):
            item = self._resolve(item_ref)
            if item is None:
                raise PdfParseException(
                    "Outline item indirect reference points to a missing object"
                )
            if not isinstance(item, PdfDictionary):
                raise PdfParseException(
                    "Outline item must be a dictionary; outline tree may be corrupt"
                )
            if isinstance(item_ref, PdfIndirectReference):
                cycle_key: Any = item_ref.object_number
            else:
                cycle_key = id(item)
            if cycle_key in visited:
                raise PdfParseException(
                    "Outline tree contains a cycle (repeated outline item reference)"
                )
            visited.add(cycle_key)

            # Title
            title = ""
            title_obj = self._resolve(item.mapping.get(PdfName("Title")))
            if isinstance(title_obj, PdfString):
                raw = title_obj.value
                title = (
                    raw.decode("utf-8", errors="ignore")
                    if isinstance(raw, bytes)
                    else str(raw)
                )

            # Destination → page index
            page_index = 0
            dest = self._resolve(item.mapping.get(PdfName("Dest")))
            if isinstance(dest, PdfArray) and len(dest.items) > 0:
                page_ref = dest.items[0]
                page_index = self._page_ref_to_index(page_ref)

            # Style flags (/F bit 1 = italic, bit 2 = bold)
            flags = 0
            flags_obj = self._resolve(item.mapping.get(PdfName("F")))
            if isinstance(flags_obj, PdfNumber):
                flags = int(flags_obj.value)

            # Children
            children: List[Dict] = []
            first_child = item.mapping.get(PdfName("First"))
            if not _outline_link_absent(first_child):
                children = self._collect_outline_items(first_child, depth + 1)

            items.append(
                {
                    "title": title,
                    "page_index": page_index,
                    "is_bold": bool(flags & 2),
                    "is_italic": bool(flags & 1),
                    "children": children,
                }
            )

            item_ref = item.mapping.get(PdfName("Next"))
        return items

    def _page_ref_to_index(self, page_ref: Any) -> int:
        """Map a page object reference to a zero-based page index."""
        _ = self._resolve(page_ref)  # resolved
        if isinstance(page_ref, PdfIndirectReference):
            obj_num = page_ref.object_number
            # Use the cached page object ID list if available
            cached = getattr(self, "_page_obj_ids", [])
            if obj_num in cached:
                return cached.index(obj_num)
        return 0

    def extract_signature(self) -> Optional[Dict[str, str]]:
        from .cos import PdfName, PdfString, PdfDictionary

        sig_ref = self._doc.trailer.mapping.get(PdfName("Sig"))
        if sig_ref is None:
            sig_ref = self._doc.trailer.mapping.get(PdfName("Signature"))
        if sig_ref is None:
            return None
        sig_obj = self._resolve(sig_ref)
        if not isinstance(sig_obj, PdfDictionary):
            return None
        res = {}
        for field_name in ["Reason", "ContactInfo", "Location"]:
            val = sig_obj.mapping.get(PdfName(field_name))
            val = self._resolve(val)
            if isinstance(val, PdfString):
                raw = val.value
                res[field_name] = (
                    raw.decode("utf-8", errors="ignore")
                    if isinstance(raw, bytes)
                    else str(raw)
                )
        return res if res else None

    def extract_signatures(self, data: bytes) -> List[PdfSignature]:
        from .cos import PdfName, PdfDictionary, PdfArray

        signatures: List[PdfSignature] = []

        # 1. Get AcroForm
        root = self._doc.trailer.mapping.get(PdfName("Root"))
        root = self._resolve(root)
        if isinstance(root, PdfDictionary):
            acroform = root.mapping.get(PdfName("AcroForm"))
            acroform = self._resolve(acroform)
            if isinstance(acroform, PdfDictionary):
                fields = acroform.mapping.get(PdfName("Fields"))
                fields = self._resolve(fields)
                if isinstance(fields, PdfArray):
                    for field_ref in fields.items:
                        self._collect_signatures_from_field(field_ref, signatures, data)
        return signatures

    def _collect_signatures_from_field(
        self, field_ref: Any, signatures: List[PdfSignature], data: bytes
    ) -> None:
        from .cos import PdfName, PdfDictionary, PdfString, PdfArray

        field_obj = self._resolve(field_ref)
        if not isinstance(field_obj, PdfDictionary):
            return

        # Check FT
        ft = field_obj.mapping.get(PdfName("FT"))
        ft = self._resolve(ft)
        if isinstance(ft, PdfName) and ft.name == "/Sig":
            # Parse Signature Dictionary check
            v_ref = field_obj.mapping.get(PdfName("V"))
            v_obj = self._resolve(v_ref)
            if isinstance(v_obj, PdfDictionary):
                br = v_obj.mapping.get(PdfName("ByteRange"))
                br = self._resolve(br)
                contents = v_obj.mapping.get(PdfName("Contents"))
                contents = self._resolve(contents)

                if isinstance(br, PdfArray) and isinstance(contents, PdfString):
                    try:
                        byte_range = [int(self._get_number(x) or 0) for x in br.items]
                        # /Contents is zero-padded to a fixed placeholder; trim to
                        # the exact DER length so the PKCS#7 blob parses cleanly.
                        pkcs7_content = _trim_der_padding(contents.value)

                        t = field_obj.mapping.get(PdfName("T"))
                        t = self._resolve(t)
                        name = ""
                        if isinstance(t, PdfString):
                            name = (
                                t.value.decode("utf-8", errors="ignore")
                                if isinstance(t.value, bytes)
                                else str(t.value)
                            )

                        sig = PdfSignature(
                            name=name,
                            contents=pkcs7_content,
                            byte_range=byte_range,
                            reference_data=data,
                        )

                        for key, attr in [
                            ("Reason", "reason"),
                            ("Location", "location"),
                            ("M", "date"),
                        ]:
                            val = v_obj.mapping.get(PdfName(key))
                            val = self._resolve(val)
                            if isinstance(val, PdfString):
                                s_val = (
                                    val.value.decode("utf-8", errors="ignore")
                                    if isinstance(val.value, bytes)
                                    else str(val.value)
                                )
                                setattr(sig, attr, s_val)

                        sub = self._resolve(v_obj.mapping.get(PdfName("SubFilter")))
                        if isinstance(sub, PdfName):
                            sig.sub_filter = sub.name.lstrip("/")

                        sig.docmdp_level = self._extract_docmdp_level(v_obj)

                        signatures.append(sig)
                    except (
                        ValueError,
                        TypeError,
                        KeyError,
                        IndexError,
                        AttributeError,
                    ):
                        pass

        # Recurse Kids
        kids = field_obj.mapping.get(PdfName("Kids"))
        kids = self._resolve(kids)
        if isinstance(kids, PdfArray):
            for kid_ref in kids.items:
                self._collect_signatures_from_field(kid_ref, signatures, data)

    def _extract_docmdp_level(self, v_obj: Any) -> Optional[int]:
        """Return the DocMDP ``/P`` level of a certifying signature, if any."""
        from .cos import PdfName, PdfDictionary, PdfArray

        refs = self._resolve(v_obj.mapping.get(PdfName("Reference")))
        if not isinstance(refs, PdfArray):
            return None
        for ref in refs.items:
            ref = self._resolve(ref)
            if not isinstance(ref, PdfDictionary):
                continue
            method = self._resolve(ref.mapping.get(PdfName("TransformMethod")))
            if not (isinstance(method, PdfName) and method.name == "/DocMDP"):
                continue
            params = self._resolve(ref.mapping.get(PdfName("TransformParams")))
            if isinstance(params, PdfDictionary):
                p = self._get_number(self._resolve(params.mapping.get(PdfName("P"))))
                if p is not None:
                    return int(p)
            return 2  # DocMDP present without explicit /P -> default level 2
        return None

    def extract_form_fields(self) -> Dict[str, Dict[str, Any]]:
        """Extract all form fields with values and types.

        Returns
        -------
        dict
            Mapping of field name to {"value": ..., "type": "text"|"checkbox"|"radio"|"choice"}.
        """
        from .cos import PdfName, PdfDictionary, PdfArray

        fields: Dict[str, Dict[str, Any]] = {}
        root = self._doc.trailer.mapping.get(PdfName("Root"))
        root = self._resolve(root)
        if isinstance(root, PdfDictionary):
            acroform = root.mapping.get(PdfName("AcroForm"))
            acroform = self._resolve(acroform)
            if isinstance(acroform, PdfDictionary):
                fields_arr = acroform.mapping.get(PdfName("Fields"))
                fields_arr = self._resolve(fields_arr)
                if isinstance(fields_arr, PdfArray):
                    for field_ref in fields_arr.items:
                        self._collect_fields_rec(field_ref, fields)
        return fields

    def _collect_fields_rec(
        self, field_ref: Any, fields: Dict[str, Dict[str, Any]], prefix: str = ""
    ) -> None:
        from .cos import PdfName, PdfDictionary, PdfString, PdfArray

        field_obj = self._resolve(field_ref)
        if not isinstance(field_obj, PdfDictionary):
            return

        t = field_obj.mapping.get(PdfName("T"))
        t = self._resolve(t)
        if not isinstance(t, PdfString):
            return

        name = (
            t.value.decode("utf-8", errors="ignore")
            if isinstance(t.value, bytes)
            else str(t.value)
        )
        full_name = f"{prefix}.{name}" if prefix else name

        from .cos import PdfNumber

        ft = self._resolve(field_obj.mapping.get(PdfName("FT")))
        ff = self._resolve(field_obj.mapping.get(PdfName("Ff")))
        ff_val = int(ff.value) if isinstance(ff, PdfNumber) else 0
        is_radio = bool(ff_val & (1 << 15))  # Ff bit 16 = Radio
        is_checkbox = isinstance(ft, PdfName) and ft.name == "/Btn" and not is_radio
        is_choice = isinstance(ft, PdfName) and ft.name == "/Ch"
        combo = is_choice and bool(ff_val & (1 << 18))  # Ff bit 19 = Combo

        if is_checkbox:
            field_type = "checkbox"
        elif is_radio:
            field_type = "radio"
        elif is_choice:
            field_type = "combobox" if combo else "listbox"
        else:
            field_type = "text"

        v = field_obj.mapping.get(PdfName("V"))
        v = self._resolve(v)
        if v is not None:
            if isinstance(v, PdfString):
                val_str = (
                    v.value.decode("utf-8", errors="ignore")
                    if isinstance(v.value, bytes)
                    else str(v.value)
                )
                if is_checkbox:
                    val = val_str in ("Yes", "1", "On", "true")
                else:
                    val = val_str
            elif isinstance(v, PdfName):
                name_val = v.name.lstrip("/")
                if is_checkbox:
                    val = name_val in ("Yes", "1", "On", "true")
                else:
                    val = name_val
            elif isinstance(v, PdfArray):
                items = []
                for item in v.items:
                    item = self._resolve(item)
                    if isinstance(item, PdfString):
                        s = (
                            item.value.decode("utf-8", errors="ignore")
                            if isinstance(item.value, bytes)
                            else str(item.value)
                        )
                        items.append(s)
                    elif isinstance(item, PdfName):
                        items.append(item.name.lstrip("/"))
                val = items if len(items) > 1 else (items[0] if items else None)
            else:
                val = str(v)
        else:
            val = False if is_checkbox else None

        fields[full_name] = {"value": val, "type": field_type}

        kids = field_obj.mapping.get(PdfName("Kids"))
        kids = self._resolve(kids)
        if isinstance(kids, PdfArray):
            for kid_ref in kids.items:
                self._collect_fields_rec(kid_ref, fields, full_name)

    def extract_attachment_entries(self) -> List[Tuple[str, bytes, dict]]:
        """Walk ``/Names /EmbeddedFiles``, one entry per embedded file.

        Each entry is ``(name, decoded_bytes, metadata)``. *metadata* mirrors the
        keys written by ``_sync_attachments_to_cos`` and may carry ``mime`` (from
        the embedded file ``/Subtype``), ``description`` (the Filespec ``/Desc``)
        and ``creation_date`` / ``mod_date`` (:class:`datetime.datetime`, parsed
        from the embedded file ``/Params``). Absent fields are omitted.
        """
        from .cos import (
            PdfName,
            PdfDictionary,
            PdfArray,
            PdfString,
            PdfStream,
            PdfIndirectReference,
        )

        entries: List[Tuple[str, bytes, dict]] = []

        # 1. Get Catalog
        catalog = self._resolve(self._doc.trailer.mapping.get(PdfName("Root")))
        if not isinstance(catalog, PdfDictionary):
            return entries

        # 2. Get Names
        names = self._resolve(catalog.mapping.get(PdfName("Names")))
        if not isinstance(names, PdfDictionary):
            return entries

        # 3. Get EmbeddedFiles
        emb_files = self._resolve(names.mapping.get(PdfName("EmbeddedFiles")))
        if not isinstance(emb_files, PdfDictionary):
            return entries

        # 4. Process 'Names' array (flat list of [key, value, key, value...])
        names_arr = self._resolve(emb_files.mapping.get(PdfName("Names")))
        if not isinstance(names_arr, PdfArray):
            return entries

        items = names_arr.items
        for i in range(0, len(items), 2):
            if i + 1 >= len(items):
                break

            key_obj = self._resolve(items[i])
            val_obj = self._resolve(items[i + 1])

            filename = ""
            if isinstance(key_obj, PdfString):
                filename = decode_pdf_text_string(key_obj)

            if not filename or not isinstance(val_obj, PdfDictionary):
                continue

            # 5. Process FileSpec — /EF may use /F, /UF, or platform variants
            ef = self._resolve(val_obj.mapping.get(PdfName("EF")))
            if not isinstance(ef, PdfDictionary):
                continue

            f_ref = None
            f_stream = None
            for ef_key in (
                PdfName("F"),
                PdfName("UF"),
                PdfName("Unix"),
                PdfName("Mac"),
                PdfName("DOS"),
            ):
                cand_raw = ef.mapping.get(ef_key)
                cand = self._resolve(cand_raw)
                if isinstance(cand, PdfStream):
                    f_ref = (
                        cand_raw
                        if isinstance(cand_raw, PdfIndirectReference)
                        else None
                    )
                    f_stream = cand
                    break

            if not isinstance(f_stream, PdfStream):
                continue

            data = self._decode_stream(
                f_stream,
                f_ref if isinstance(f_ref, PdfIndirectReference) else None,
            )
            entries.append((filename, data, self._read_filespec_meta(val_obj, f_stream)))

        return entries

    def _read_filespec_meta(self, filespec: Any, ef_stream: Any) -> dict:
        """Read typed metadata from a ``/Filespec`` and its ``/EmbeddedFile`` stream.

        Reverses ``_sync_attachments_to_cos``: the MIME ``/Subtype`` on the
        embedded file stream, the ``/Desc`` on the file specification, and the
        ``/CreationDate`` / ``/ModDate`` in the stream's ``/Params``.
        """
        from .cos import PdfName, PdfDictionary, PdfString

        meta: dict = {}

        mime = _decode_mime_name(self._resolve(ef_stream.mapping.get(PdfName("Subtype"))))
        if mime:
            meta["mime"] = mime

        desc = self._resolve(filespec.mapping.get(PdfName("Desc")))
        if isinstance(desc, PdfString):
            text = decode_pdf_text_string(desc)
            if text:
                meta["description"] = text

        params = self._resolve(ef_stream.mapping.get(PdfName("Params")))
        if isinstance(params, PdfDictionary):
            for date_key, meta_key in (
                ("CreationDate", "creation_date"),
                ("ModDate", "mod_date"),
            ):
                raw = self._resolve(params.mapping.get(PdfName(date_key)))
                if isinstance(raw, PdfString):
                    parsed = _parse_pdf_date(decode_pdf_text_string(raw))
                    if parsed is not None:
                        meta[meta_key] = parsed
        return meta

    def extract_attachments(self) -> Dict[str, bytes]:
        """Return ``{name: bytes}`` for every embedded file.

        See :meth:`extract_attachment_entries` for the metadata-aware variant.
        """
        return {name: data for name, data, _meta in self.extract_attachment_entries()}


# ---------------------------------------------------------------------------
# PDF Writer
# ---------------------------------------------------------------------------
class PdfWriterV0:
    """Writes SimplePdf to PDF 1.7 format."""

    def __init__(self, pdf: SimplePdf) -> None:
        self.pdf = pdf
        self.out = bytearray()
        self.xref: List[int] = []

    def _write_line(self, data: bytes, newline: bool = True) -> None:
        self.out.extend(data)
        if newline:
            self.out.extend(b"\n")

    def _start_obj(self, obj_id: int) -> None:
        self.xref.append(len(self.out))
        self._write_line(f"{obj_id} 0 obj".encode())

    def _end_obj(self) -> None:
        self._write_line(b"endobj")

    def _encrypt_data(self, data: bytes, obj_id: int) -> bytes:
        if not self.pdf.encrypted or not data:
            return data
        if self.pdf.encryption_key is None:
            return data

        if self.pdf.encryption_algorithm.startswith("AES"):
            # AES encryption (AES-128 or AES-256)
            # AES V4 usually encrypts streams/strings using the key directly (or with per-object salt in some versions).
            # For this task, we use the simple AES-CBC from Utils with the key.
            # We don't modify the key with obj_id for AES V4 (usually).
            # (Actually V4 uses CFB? Standard is CBC for streams in V4).
            # We'll use the key as is.
            return EncryptionUtils.encrypt_aes_cbc(self.pdf.encryption_key, data)

        # RC4
        key = self.pdf.encryption_key + bytes(
            [obj_id & 0xFF, (obj_id >> 8) & 0xFF, (obj_id >> 16) & 0xFF, 0, 0]
        )
        real_key = hashlib.md5(key).digest()[:10]

        return EncryptionUtils.encrypt_rc4(real_key, data)

    def _write_outlines(
        self,
        outline_items: List[Dict],
        root_id: int,
        first_page_obj_id: int,
        page_count: int,
    ) -> int:
        """Write outline root + all item objects.

        Returns the next available object ID after all outlines are written.

        Parameters
        ----------
        outline_items:
            Serialised outline data (list of dicts from ``_outlines_data``).
        root_id:
            Object ID to use for the outline root dictionary.
        first_page_obj_id:
            Object ID of the first page object (used to build /Dest arrays).
        page_count:
            Number of pages in the document.
        """
        if not outline_items:
            return root_id

        # --- Phase 1: assign object IDs to every node (DFS, level-first) ---
        # nodes list built in place-order; cross-references filled below.
        nodes: List[Dict] = []
        id_counter = [root_id + 1]

        def assign_ids(items: List[Dict], parent_obj_id: int) -> None:
            # Assign IDs to the whole level first so siblings can reference each other.
            level_ids = []
            for item in items:
                level_ids.append(id_counter[0])
                id_counter[0] += 1

            for i, (item, node_id) in enumerate(zip(items, level_ids)):
                prev_id = level_ids[i - 1] if i > 0 else 0
                next_id = level_ids[i + 1] if i < len(level_ids) - 1 else 0
                children = item.get("children", [])
                first_child_id = id_counter[0] if children else 0

                node: Dict = {
                    "id": node_id,
                    "title": item.get("title", ""),
                    "page_index": item.get("page_index", 0),
                    "is_bold": item.get("is_bold", False),
                    "is_italic": item.get("is_italic", False),
                    "parent_id": parent_obj_id,
                    "prev_id": prev_id,
                    "next_id": next_id,
                    "first_child_id": first_child_id,
                    "last_child_id": 0,
                    "open_count": 0,
                }
                node_list_idx = len(nodes)
                nodes.append(node)

                if children:
                    assign_ids(children, node_id)
                    # Update last_child_id to the last direct child
                    direct_children = [
                        n
                        for n in nodes[node_list_idx + 1 :]
                        if n["parent_id"] == node_id
                    ]
                    if direct_children:
                        nodes[node_list_idx]["last_child_id"] = direct_children[-1][
                            "id"
                        ]
                    # open_count = total descendants
                    nodes[node_list_idx]["open_count"] = len(
                        nodes[node_list_idx + 1 :]  # includes all descendants
                    )

        assign_ids(outline_items, root_id)

        # --- Phase 2: identify top-level items ---
        top_level = [n for n in nodes if n["parent_id"] == root_id]
        first_top_id = top_level[0]["id"] if top_level else 0
        last_top_id = top_level[-1]["id"] if top_level else 0

        # --- Phase 3: write root object ---
        self._start_obj(root_id)
        root_line = f"<< /Type /Outlines /Count {len(top_level)}"
        if first_top_id:
            root_line += f" /First {first_top_id} 0 R /Last {last_top_id} 0 R"
        root_line += " >>"
        self._write_line(root_line.encode())
        self._end_obj()

        # --- Phase 4: write each node ---
        for node in nodes:
            page_idx = max(0, min(node["page_index"], page_count - 1))
            page_obj_id = first_page_obj_id + page_idx
            title_esc = (
                node["title"]
                .replace("\\", "\\\\")
                .replace("(", "\\(")
                .replace(")", "\\)")
            )
            flags = (1 if node["is_italic"] else 0) | (2 if node["is_bold"] else 0)

            parts = [
                f"/Title ({title_esc})",
                f"/Parent {node['parent_id']} 0 R",
                f"/Dest [{page_obj_id} 0 R /Fit]",
            ]
            if node["prev_id"]:
                parts.append(f"/Prev {node['prev_id']} 0 R")
            if node["next_id"]:
                parts.append(f"/Next {node['next_id']} 0 R")
            if node["first_child_id"]:
                parts.append(f"/First {node['first_child_id']} 0 R")
            if node["last_child_id"]:
                parts.append(f"/Last {node['last_child_id']} 0 R")
            if node["open_count"]:
                parts.append(f"/Count {node['open_count']}")
            if flags:
                parts.append(f"/F {flags}")

            self._start_obj(node["id"])
            self._write_line(("<< " + " ".join(parts) + " >>").encode())
            self._end_obj()

        return id_counter[0]

    def write(self) -> bytes:
        self.out = bytearray()
        self.xref = []
        self._write_line(f"%PDF-{self.pdf.pdf_version}".encode())

        count = self.pdf.page_count
        if count == 0:
            count = 1
            self.pdf.pages = [(0, 0, 612, 792)]
            self.pdf.page_contents = [b""]

        cat_id = 1
        p_root_id = 2
        f_p_id = 3
        f_c_id = f_p_id + count
        f_im_id = f_c_id + count

        visible_images = {
            name: data
            for name, data in self.pdf.images.items()
            if name not in self.pdf._hidden_images
        }
        img_count = len(visible_images)

        # Pre-compute IDs for objects written after image objects so that the
        # catalog can reference them (outline root, AcroForm signature field)
        # before any of those objects are actually serialised.
        _base = f_im_id + img_count
        _info_slot = 1 if self.pdf.metadata else 0
        _enc_slot = 1 if self.pdf.encrypted else 0
        _sig_present = bool(self.pdf.signing_creds or self.pdf.signature)
        sig_obj_id = (_base + _info_slot + _enc_slot) if _sig_present else 0
        # A proper AcroForm signature field is emitted only for real signing.
        sig_field_id = (sig_obj_id + 1) if self.pdf.signing_creds else 0
        _pre = _base + _info_slot + _enc_slot
        _pre += 1 if _sig_present else 0
        _pre += 1 if self.pdf.signing_creds else 0
        has_outlines = bool(getattr(self.pdf, "_outlines_data", None))
        outline_root_id = _pre if has_outlines else 0

        self._start_obj(cat_id)
        catalog = f"<< /Type /Catalog /Pages {p_root_id} 0 R"
        if outline_root_id:
            catalog += f" /Outlines {outline_root_id} 0 R"
        if sig_field_id:
            catalog += f" /AcroForm << /Fields [{sig_field_id} 0 R] /SigFlags 3 >>"
            if self.pdf.certify_permissions in (1, 2, 3):
                # DocMDP certification: the document's permissions are bound to
                # this (certifying) signature.
                catalog += f" /Perms << /DocMDP {sig_obj_id} 0 R >>"
        catalog += " >>"
        self._write_line(catalog.encode())
        self._end_obj()

        kids = " ".join([f"{f_p_id + i} 0 R" for i in range(count)])
        self._start_obj(p_root_id)
        self._write_line(f"<< /Type /Pages /Count {count} /Kids [ {kids} ] >>".encode())
        self._end_obj()

        for i in range(count):
            mbox = self.pdf.pages[i] if i < len(self.pdf.pages) else (0, 0, 612, 792)
            self._start_obj(f_p_id + i)
            self._write_line(
                f"<< /Type /Page /Parent 2 0 R /MediaBox [{mbox[0]} {mbox[1]} {mbox[2]} {mbox[3]}]".encode()
            )
            self._write_line(f"/Contents {f_c_id + i} 0 R".encode())
            self._write_line(
                b"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >>"
            )
            if visible_images:
                xobjs = " ".join(
                    [
                        f"/{name} {f_im_id + idx} 0 R"
                        for idx, name in enumerate(visible_images.keys())
                    ]
                )
                self._write_line(f"/XObject << {xobjs} >>".encode())
            self._write_line(b">> >>")
            self._end_obj()

        for i in range(count):
            content = (
                self.pdf.page_contents[i] if i < len(self.pdf.page_contents) else b""
            )
            if self.pdf.watermark_text:
                watermark = f"BT /F1 48 Tf 0.5 G 1 0 0 1 100 100 Tm ({self.pdf.watermark_text}) Tj ET".encode()
                content += b"\n" + watermark
            if self.pdf.encrypted:
                content = self._encrypt_data(content, f_c_id + i)
            self._start_obj(f_c_id + i)
            self._write_line(f"<< /Length {len(content)} >>".encode())
            self._write_line(b"stream")
            self.out.extend(content)
            self.out.extend(b"\n")
            self._write_line(b"endstream")
            self._end_obj()

        for idx, (name, data) in enumerate(visible_images.items()):
            img_id = f_im_id + idx
            img_data = self._encrypt_data(data, img_id) if self.pdf.encrypted else data
            self._start_obj(img_id)
            w, h = self.pdf._image_sizes.get(name, (1, 1))
            self._write_line(
                f"<< /Subtype /Image /Width {w} /Height {h} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Length {len(img_data)} >>".encode()
            )
            self._write_line(b"stream")
            self.out.extend(img_data)
            self.out.extend(b"\n")
            self._write_line(b"endstream")
            self._end_obj()

        curr_id = f_im_id + img_count
        info_id = 0
        if self.pdf.metadata:
            info_id = curr_id
            curr_id += 1
            self._start_obj(info_id)
            entries = " ".join([f"/{k} ({v})" for k, v in self.pdf.metadata.items()])
            self._write_line(f"<< {entries} >>".encode())
            self._end_obj()

        enc_id = 0
        if self.pdf.encrypted:
            enc_id = curr_id
            curr_id += 1
            self._start_obj(enc_id)
            if self.pdf.encryption_algorithm == "RC4":
                # V1 R2 RC4 40-bit
                O_val = self.pdf.O.hex() if self.pdf.O else "00" * 32
                U_val = self.pdf.U.hex() if self.pdf.U else "00" * 32
                self._write_line(
                    f"<< /Filter /Standard /V 1 /R 2 /O <{O_val}> /U <{U_val}> /P {self.pdf.P} >>".encode()
                )
            elif self.pdf.encryption_algorithm.startswith("AES"):
                # Minimal AES metadata
                is_256 = "256" in self.pdf.encryption_algorithm
                v_val = 5 if is_256 else 4
                r_val = 5 if is_256 else 4
                cfm_val = "AESV3" if is_256 else "AESV2"

                U_val = self.pdf.U.hex() if self.pdf.U else ""
                O_val = self.pdf.O.hex() if self.pdf.O else ""
                UE_raw = getattr(self.pdf, "UE", None) or b""
                OE_raw = getattr(self.pdf, "OE", None) or b""
                UE_val = UE_raw.hex() if UE_raw else ""
                OE_val = OE_raw.hex() if OE_raw else ""
                uo = ""
                if U_val and O_val:
                    uo = f" /U <{U_val}> /O <{O_val}>"
                    if UE_val and OE_val:
                        uo += f" /UE <{UE_val}> /OE <{OE_val}>"

                self._write_line(
                    f"<< /Filter /Standard /V {v_val} /R {r_val} /P {self.pdf.P}{uo} "
                    f"/CF << /StdCF << /Type /CryptFilter /CFM /{cfm_val} >> >> "
                    f"/StmF /StdCF /StrF /StdCF >>".encode()
                )
            self._end_obj()

        sig_id = 0
        if self.pdf.signing_creds:
            sig_id = sig_obj_id
            self._start_obj(sig_id)
            reason = (
                self.pdf.signature.get("Reason", "") if self.pdf.signature else "Signed"
            )
            location = (
                self.pdf.signature.get("Location", "") if self.pdf.signature else ""
            )
            sig_name = (
                self.pdf.signature.get("Name", "Signature1")
                if self.pdf.signature
                else "Signature1"
            )

            sub_filter = (
                "ETSI.CAdES.detached" if self.pdf.pades else "adbe.pkcs7.detached"
            )
            self.out.extend(
                f"<< /Type /Signature /Filter /Adobe.PPKLite /SubFilter /{sub_filter} /Reason ({reason}) /Location ({location})".encode()
            )
            # DocMDP certification reference binds document permissions to this sig.
            if self.pdf.certify_permissions in (1, 2, 3):
                self.out.extend(
                    (
                        " /Reference [ << /Type /SigRef /TransformMethod /DocMDP"
                        " /TransformParams << /Type /TransformParams"
                        f" /P {self.pdf.certify_permissions} /V /1.2 >>"
                        " /DigestMethod /SHA256 >> ]"
                    ).encode()
                )

            self.out.extend(b" /ByteRange [")
            br_start_offset = len(self.out)
            self.out.extend(
                b"0" * 10 + b" " + b"0" * 10 + b" " + b"0" * 10 + b" " + b"0" * 10
            )
            self.out.extend(b"]")

            # Reserve a generous placeholder: an embedded chain and/or RFC 3161
            # timestamp make the PKCS#7 blob much larger than a bare signature.
            sig_max_len = 16384
            if (
                self.pdf.extra_certs
                or self.pdf.timestamp_tsa
                or self.pdf.timestamp_url
            ):
                sig_max_len = 65536
            self.out.extend(b" /Contents <")
            contents_start_offset = len(self.out)
            self.out.extend(b"0" * sig_max_len)
            contents_end_offset = len(self.out)
            self.out.extend(b"> >>\n")

            self._end_obj()

            # AcroForm signature field that references the signature value dict,
            # so signed PDFs round-trip through ``extract_signatures``.
            self._start_obj(sig_field_id)
            self._write_line(
                f"<< /FT /Sig /T ({sig_name}) /V {sig_id} 0 R >>".encode()
            )
            self._end_obj()
            curr_id = sig_field_id + 1
        elif self.pdf.signature:
            sig_id = curr_id
            curr_id += 1
            self._start_obj(sig_id)
            reason = self.pdf.signature.get("Reason", "")
            contact = self.pdf.signature.get("ContactInfo", "")
            location = self.pdf.signature.get("Location", "")
            self._write_line(
                f"<< /Type /Signature /Filter /Adobe.PPKLite /SubFilter /adbe.pkcs7.detached /Reason ({reason}) /ContactInfo ({contact}) /Location ({location}) /Contents <0000> >>".encode()
            )
            self._end_obj()

        # Write outline objects (bookmarks)
        if has_outlines:
            curr_id = self._write_outlines(
                getattr(self.pdf, "_outlines_data", []),
                outline_root_id,
                f_p_id,
                count,
            )

        t_objs = len(self.xref)
        startxref = len(self.out)
        self._write_line(b"xref")
        self._write_line(f"0 {t_objs + 1}".encode())
        self._write_line(b"0000000000 65535 f ")
        for offset in self.xref:
            self._write_line(f"{offset:010d} 00000 n ".encode())

        # Build /ID array for the trailer
        file_id = self.pdf.file_id
        if not file_id or len(file_id) < 2:
            id1 = EncryptionUtils.generate_file_id()
            id2 = EncryptionUtils.generate_file_id()
            file_id = [id1, id2]
        id1_hex = file_id[0].hex() if isinstance(file_id[0], bytes) else file_id[0]
        id2_hex = file_id[1].hex() if isinstance(file_id[1], bytes) else file_id[1]

        self._write_line(b"trailer")
        tr = f"<< /Size {t_objs + 1} /Root 1 0 R"
        if enc_id:
            tr += f" /Encrypt {enc_id} 0 R"
        if info_id:
            tr += f" /Info {info_id} 0 R"
        if sig_id:
            tr += f" /Sig {sig_id} 0 R"
        tr += f" /ID [<{id1_hex}> <{id2_hex}>]"

        self._write_line((tr + " >>").encode())
        self._write_line(b"startxref")
        self._write_line(f"{startxref}".encode())
        self._write_line(b"%%EOF")

        # Patching Signature if needed
        if self.pdf.signing_creds:
            cert, key = self.pdf.signing_creds
            file_len = len(self.out)

            # Range 1: 0 to contents_start_offset
            # Range 2: contents_end_offset to file_len
            range1 = [0, contents_start_offset]
            range2 = [contents_end_offset, file_len - contents_end_offset]

            # Patch ByteRange (43 bytes: 10 + 1 + 10 + 1 + 10 + 1 + 10)
            br_patched = f"{range1[0]:010d} {range1[1]:010d} {range2[0]:010d} {range2[1]:010d}".encode()
            self.out[br_start_offset : br_start_offset + 43] = br_patched

            # Calculate Digest
            data_to_sign = bytes(
                self.out[range1[0] : range1[1]] + self.out[range2[0] :]
            )

            # Sign, embedding any chain certificates and RFC 3161 timestamp.
            # PAdES uses CAdES-BES (signing-certificate-v2); otherwise plain
            # PKCS#7.  A timestamp upgrades either to its -T variant.
            signer_fn = (
                SigningUtils.sign_data_cades
                if self.pdf.pades
                else SigningUtils.sign_data_pkcs7
            )
            signature = signer_fn(
                data_to_sign,
                cert,
                key,
                extra_certs=self.pdf.extra_certs,
                tsa=self.pdf.timestamp_tsa,
                timestamp_url=self.pdf.timestamp_url,
                timestamp_timeout=self.pdf.timestamp_timeout,
            )
            sig_hex = signature.hex().encode()

            # Fail explicitly rather than silently truncating a too-large blob.
            if len(sig_hex) > sig_max_len:
                raise ValueError(
                    "PKCS#7 signature exceeds the reserved /Contents placeholder "
                    f"({len(sig_hex)} > {sig_max_len} hex chars)"
                )
            # Pad
            if len(sig_hex) < sig_max_len:
                sig_hex += b"0" * (sig_max_len - len(sig_hex))

            # Patch Contents
            self.out[contents_start_offset:contents_end_offset] = sig_hex[:sig_max_len]

        return bytes(self.out)


# ---------------------------------------------------------------------------
# PageCollection helper class
# ---------------------------------------------------------------------------
class PageCollection:
    """Collection wrapper for pages in SimplePdf."""

    def __init__(self, pdf: Optional[SimplePdf] = None) -> None:
        self._pdf = pdf
        self._disposed = False
        self.pages: List[Tuple[float, float, float, float]] = []
        self.page_contents: List[bytes] = []
        if pdf:
            self.pages = pdf.pages
            self.page_contents = pdf.page_contents

    def _ensure_not_disposed(self) -> None:
        if self._disposed:
            raise AsposePdfException("PageCollection has been disposed")

    @property
    def count(self) -> int:
        return len(self.pages)

    @property
    def is_read_only(self) -> bool:
        return False

    def contains(self, page: Any) -> bool:
        self._ensure_not_disposed()
        if hasattr(page, "identifier"):
            return any(
                getattr(p, "identifier", None) == page.identifier for p in self.pages
            )
        return page in self.pages

    def index_of(self, page: Any) -> int:
        self._ensure_not_disposed()
        for i, p in enumerate(self.pages):
            if p is page or p == page:
                return i
        raise PdfValidationException("Page not found in collection")

    def item(self, index: int) -> Any:
        self._ensure_not_disposed()
        if index < 0 or index >= len(self.pages):
            raise IndexError("Page index out of range")
        return self.pages[index]

    def get_enumerator(self):
        self._ensure_not_disposed()
        return iter(self.pages)

    def __iter__(self):
        return iter(self.pages)

    def __len__(self) -> int:
        return len(self.pages)

    def __getitem__(self, index: int) -> Any:
        return self.pages[index]
