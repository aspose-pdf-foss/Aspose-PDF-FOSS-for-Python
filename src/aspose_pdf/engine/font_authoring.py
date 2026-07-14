"""Dependency-free Type 0 font preparation for authored Unicode text.

The public page API accepts several convenient font sources, while the PDF
writer needs one normalized font face, stable character codes, descriptor
metrics, widths, and a ToUnicode CMap. This module owns that conversion without
depending on fontTools at runtime.

TrueType faces receive compact sequential CIDs and an explicit CIDToGIDMap.
CFF faces use their native CIDs (or glyph ids for a name-keyed CFF), because a
CIDFontType0 has no separate CIDToGIDMap. Both paths retain glyph numbering when
subsetting so the generated mappings stay valid.
"""

from __future__ import annotations

import hashlib
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aspose_pdf.exceptions import FontEmbeddingException
from aspose_pdf.font_registry import FontDescriptor

from .cff_outlines import CffOutlines
from .font_subset import read_unicode_cmap, subset_truetype
from .font_subset_cff import cff_charset_cid_to_gid, subset_cff
from .glyph_outlines import TrueTypeOutlines
from .sfnt import parse_faces
from .woff import build_sfnt, decode as decode_woff, is_woff, is_woff2

__all__ = ["AuthoredFont", "prepare_authored_font"]


_TTC_TAG = b"ttcf"
_SUPPORTED_SFNT_FLAVORS = {
    0x00010000,  # Windows TrueType
    0x74727565,  # Apple TrueType ('true')
    0x4F54544F,  # OpenType/CFF ('OTTO')
}
_SAFE_FONT_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_MAX_SFNT_TABLES = 4096
_MAX_BFCHAR_BLOCK = 100


@dataclass(frozen=True)
class _SfntMetrics:
    descriptor: dict[str, int | float | tuple[int, int, int, int]]
    advances: tuple[int, ...]
    num_glyphs: int


class AuthoredFont:
    """A normalized embedded font and the mutable CID mapping for authored text.

    Instances are created by :func:`prepare_authored_font`. Calling
    :meth:`encode` adds any newly used characters to the font's mapping. The
    remaining methods always describe that current mapping and can be called
    repeatedly as more text is authored.
    """

    def __init__(
        self,
        *,
        sfnt_program: bytes,
        embedded_program: bytes,
        plain_base_name: str,
        kind: str,
        unicode_to_gid: dict[int, int],
        metrics: _SfntMetrics,
        gid_to_native_cid: dict[int, int] | None = None,
    ) -> None:
        self.fingerprint = hashlib.sha256(sfnt_program).hexdigest()
        self.cid_font_subtype = (
            "CIDFontType2" if kind == "truetype" else "CIDFontType0"
        )
        self.font_file_key = "FontFile2" if kind == "truetype" else "FontFile3"
        self.font_file_subtype = None if kind == "truetype" else "CIDFontType0C"

        self._sfnt_program = sfnt_program
        self._full_embedded_program = embedded_program
        self._plain_base_name = plain_base_name
        self._subset_prefix = _subset_prefix(bytes.fromhex(self.fingerprint))
        self._kind = kind
        self._unicode_to_gid = unicode_to_gid
        self._advances = metrics.advances
        self._descriptor_metrics = metrics.descriptor
        self._gid_to_native_cid = gid_to_native_cid

        self._unicode_to_cid: dict[int, int] = {}
        self._cid_to_unicode: dict[int, int] = {}
        self._cid_to_gid: dict[int, int] = {}
        self._used_gids: set[int] = {0}

        self._state_version = 0
        self._embedded_version = -1
        self._embedded_cache = embedded_program
        self._is_subset = False

    @property
    def base_name(self) -> str:
        """Return the exact current PDF ``BaseFont``/``FontName`` value."""
        self._refresh_embedded_program()
        if self._is_subset:
            return f"{self._subset_prefix}+{self._plain_base_name}"
        return self._plain_base_name

    @property
    def pdf_base_name(self) -> str:
        """Alias for :attr:`base_name` used by COS integration code."""
        return self.base_name

    @property
    def is_subset(self) -> bool:
        """Whether :meth:`embedded_program` currently returns a reduced font."""
        self._refresh_embedded_program()
        return self._is_subset

    @property
    def descriptor_metrics(
        self,
    ) -> dict[str, int | float | tuple[int, int, int, int]]:
        """Return PDF FontDescriptor metrics using PDF dictionary key names."""
        return dict(self._descriptor_metrics)

    def encode(self, text: str) -> bytes:
        """Encode *text* as two-byte CIDs and extend the current font mapping.

        This method performs character-to-glyph mapping only. Shaping, bidi
        reordering, ligature selection, and positioning remain separate layout
        concerns. An unsupported scalar is rejected instead of being silently
        rendered as ``.notdef``.
        """
        if not isinstance(text, str):
            raise TypeError("text must be a string")

        planned: list[tuple[int, int, int]] = []
        new_by_codepoint: dict[int, tuple[int, int]] = {}
        new_by_cid: dict[int, int] = {}
        next_truetype_cid = len(self._unicode_to_cid) + 1
        for character in text:
            codepoint = ord(character)
            if 0xD800 <= codepoint <= 0xDFFF:
                raise FontEmbeddingException(
                    f"The font text contains an isolated surrogate U+{codepoint:04X}."
                )
            gid = self._unicode_to_gid.get(codepoint)
            if gid is None or gid <= 0 or gid >= len(self._advances):
                raise FontEmbeddingException(
                    f"The font {self._plain_base_name!r} has no glyph for "
                    f"U+{codepoint:04X}."
                )

            cid = self._unicode_to_cid.get(codepoint)
            if cid is None:
                pending = new_by_codepoint.get(codepoint)
                if pending is not None:
                    cid = pending[0]
                elif self._kind == "truetype":
                    cid = next_truetype_cid
                    next_truetype_cid += 1
                    if cid > 0xFFFF:
                        raise FontEmbeddingException(
                            "A Type 0 font cannot encode more than 65,535 "
                            "distinct CIDs."
                        )
                    new_by_codepoint[codepoint] = (cid, gid)
                    new_by_cid[cid] = codepoint
                else:
                    cid = (
                        self._gid_to_native_cid.get(gid)
                        if self._gid_to_native_cid is not None
                        else gid
                    )
                    if cid is None or not 0 < cid <= 0xFFFF:
                        raise FontEmbeddingException(
                            f"The CFF font glyph {gid} has no encodable native CID."
                        )
                    previous = self._cid_to_unicode.get(cid)
                    if previous is None:
                        previous = new_by_cid.get(cid)
                    if previous is not None and previous != codepoint:
                        raise FontEmbeddingException(
                            "The CFF font cmap maps multiple Unicode characters "
                            f"to CID {cid}; exact ToUnicode round-trip is not "
                            "possible."
                        )
                    new_by_codepoint[codepoint] = (cid, gid)
                    new_by_cid[cid] = codepoint
            planned.append((codepoint, cid, gid))

        for codepoint, (cid, gid) in new_by_codepoint.items():
            self._unicode_to_cid[codepoint] = cid
            self._cid_to_unicode[cid] = codepoint
            self._cid_to_gid[cid] = gid
        self._used_gids.update(gid for _codepoint, _cid, gid in planned)
        if new_by_codepoint:
            self._state_version += 1

        encoded = bytearray()
        for _codepoint, cid, _gid in planned:
            encoded.extend(cid.to_bytes(2, "big"))
        return bytes(encoded)

    def embedded_program(self) -> bytes:
        """Return the current embedded program, subset when it becomes smaller."""
        self._refresh_embedded_program()
        return self._embedded_cache

    def to_unicode_cmap(self) -> bytes:
        """Build a ToUnicode CMap for the current two-byte CID assignments."""
        rows = [
            (f"<{cid:04X}> <{chr(codepoint).encode('utf-16-be').hex().upper()}>")
            for cid, codepoint in sorted(self._cid_to_unicode.items())
        ]
        lines = [
            "/CIDInit /ProcSet findresource begin",
            "12 dict begin",
            "begincmap",
            "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
            "/CMapName /Adobe-Identity-UCS def",
            "/CMapType 2 def",
            "1 begincodespacerange",
            "<0000> <FFFF>",
            "endcodespacerange",
        ]
        for offset in range(0, len(rows), _MAX_BFCHAR_BLOCK):
            block = rows[offset : offset + _MAX_BFCHAR_BLOCK]
            lines.append(f"{len(block)} beginbfchar")
            lines.extend(block)
            lines.append("endbfchar")
        lines.extend(
            [
                "endcmap",
                "CMapName currentdict /CMap defineresource pop",
                "end",
                "end",
                "",
            ]
        )
        return "\n".join(lines).encode("ascii")

    def cid_widths(self) -> dict[int, int]:
        """Return current ``CID -> advance`` widths in 1000-unit PDF space."""
        return {
            cid: self._advances[gid]
            for cid, gid in sorted(self._cid_to_gid.items())
        }

    def cid_to_gid_bytes(self) -> bytes | None:
        """Return a CIDToGIDMap stream for TrueType, or ``None`` for CFF.

        A TrueType result always contains at least the two-byte CID 0 entry.
        CFF CIDFonts use their CFF charset directly and must not carry a
        CIDToGIDMap entry.
        """
        if self._kind != "truetype":
            return None
        max_cid = max(self._cid_to_gid, default=0)
        result = bytearray((max_cid + 1) * 2)
        for cid, gid in self._cid_to_gid.items():
            struct.pack_into(">H", result, cid * 2, gid)
        return bytes(result)

    def _refresh_embedded_program(self) -> None:
        if self._embedded_version == self._state_version:
            return
        if self._kind == "truetype":
            reduced = subset_truetype(self._full_embedded_program, self._used_gids)
        else:
            reduced = subset_cff(self._full_embedded_program, self._used_gids)
        if reduced is not None and len(reduced) < len(self._full_embedded_program):
            self._embedded_cache = reduced
            self._is_subset = True
        else:
            self._embedded_cache = self._full_embedded_program
            self._is_subset = False
        self._embedded_version = self._state_version


def prepare_authored_font(
    source: FontDescriptor | bytes | bytearray | str | Path,
    *,
    font_name: str | None = None,
) -> AuthoredFont:
    """Normalize *source* and prepare a Type 0 font for Unicode authoring.

    ``source`` may be a discovered :class:`FontDescriptor`, in-memory bytes, or
    a filesystem path. WOFF/WOFF2 wrappers are decoded before parsing. A TTC
    descriptor selects its ``face_index``; other source forms select face 0.

    Supported outlines are TrueType ``glyf`` and OpenType CFF 1. CFF2 and
    malformed or incomplete fonts raise :class:`FontEmbeddingException`.
    """
    raw, face_index, suggested_name = _read_source(source)
    normalized = _normalize_wrapper(raw)
    sfnt = _select_sfnt_face(normalized, face_index)
    _flavor, tables = _read_sfnt_tables(sfnt, 0)

    if "CFF2" in tables:
        raise FontEmbeddingException("The font uses unsupported OpenType CFF2 outlines.")
    has_glyf = "glyf" in tables
    has_cff = "CFF " in tables
    if has_glyf == has_cff:
        raise FontEmbeddingException(
            "The font must contain exactly one supported outline table: glyf or CFF."
        )

    cmap = read_unicode_cmap(sfnt)
    if not cmap:
        raise FontEmbeddingException("The font has no usable Unicode cmap.")

    metrics = _read_sfnt_metrics(sfnt, tables)
    faces = parse_faces(sfnt)
    parsed_name = None
    if faces:
        face = faces[0]
        parsed_name = face.postscript_name or face.full_name or face.best_name
    base_name = _safe_font_name(font_name or parsed_name or suggested_name or "Font")

    if has_glyf:
        outlines = TrueTypeOutlines(sfnt)
        if not outlines.ok or outlines.num_glyphs != metrics.num_glyphs:
            raise FontEmbeddingException("The TrueType font glyph tables are malformed.")
        return AuthoredFont(
            sfnt_program=sfnt,
            embedded_program=sfnt,
            plain_base_name=base_name,
            kind="truetype",
            unicode_to_gid=cmap,
            metrics=metrics,
        )

    cff_program = tables["CFF "]
    if len(cff_program) < 4 or cff_program[0] != 1:
        raise FontEmbeddingException("The font does not contain supported CFF 1 data.")
    outlines = CffOutlines(cff_program)
    if not outlines.ok or outlines.num_glyphs != metrics.num_glyphs:
        raise FontEmbeddingException("The OpenType CFF font glyph tables are malformed.")
    cid_to_gid = cff_charset_cid_to_gid(cff_program)
    gid_to_cid = None
    if cid_to_gid is not None:
        gid_to_cid = {}
        for cid, gid in cid_to_gid.items():
            gid_to_cid.setdefault(gid, cid)
    return AuthoredFont(
        sfnt_program=sfnt,
        embedded_program=cff_program,
        plain_base_name=base_name,
        kind="cff",
        unicode_to_gid=cmap,
        metrics=metrics,
        gid_to_native_cid=gid_to_cid,
    )


def _read_source(
    source: FontDescriptor | bytes | bytearray | str | Path,
) -> tuple[bytes, int, str | None]:
    if isinstance(source, FontDescriptor):
        try:
            raw = source.get_font_bytes()
        except FontEmbeddingException as exc:
            raise FontEmbeddingException(f"Could not read the font source: {exc}") from exc
        name = source.postscript_name or source.full_name or source.name
        return raw, int(source.face_index), name
    if isinstance(source, (bytes, bytearray)):
        return bytes(source), 0, None
    if isinstance(source, (str, Path)):
        path = Path(source)
        try:
            return path.read_bytes(), 0, path.stem
        except OSError as exc:
            raise FontEmbeddingException(
                f"Could not read font file {str(path)!r}: {exc}"
            ) from exc
    raise TypeError(
        "source must be FontDescriptor, bytes, bytearray, str, or pathlib.Path"
    )


def _normalize_wrapper(data: bytes) -> bytes:
    if is_woff(data) or is_woff2(data):
        decoded = decode_woff(data)
        if decoded is None:
            if is_woff2(data):
                raise FontEmbeddingException(
                    "WOFF2 font decoding failed; install the optional woff2 extra "
                    "when Brotli support is unavailable."
                )
            raise FontEmbeddingException("WOFF font decoding failed.")
        return decoded
    return data


def _select_sfnt_face(data: bytes, face_index: int) -> bytes:
    if face_index < 0:
        raise FontEmbeddingException("The font face_index cannot be negative.")
    if data[:4] == _TTC_TAG:
        if len(data) < 12:
            raise FontEmbeddingException("The font collection header is truncated.")
        num_faces = struct.unpack_from(">I", data, 8)[0]
        if num_faces == 0 or num_faces > (len(data) - 12) // 4:
            raise FontEmbeddingException("The font collection directory is malformed.")
        if face_index >= num_faces:
            raise FontEmbeddingException(
                f"The font collection has {num_faces} faces; index {face_index} "
                "is out of range."
            )
        offset = struct.unpack_from(">I", data, 12 + face_index * 4)[0]
        flavor, tables = _read_sfnt_tables(data, offset)
        return build_sfnt(flavor, list(tables.items()))

    if face_index != 0:
        raise FontEmbeddingException("A non-collection font only has face index 0.")
    flavor, tables = _read_sfnt_tables(data, 0)
    return build_sfnt(flavor, list(tables.items()))


def _read_sfnt_tables(data: bytes, directory_offset: int) -> tuple[int, dict[str, bytes]]:
    if directory_offset < 0 or directory_offset + 12 > len(data):
        raise FontEmbeddingException("The font SFNT table directory is truncated.")
    flavor, num_tables = struct.unpack_from(">IH", data, directory_offset)
    if flavor not in _SUPPORTED_SFNT_FLAVORS:
        raise FontEmbeddingException("The input is not a supported SFNT font.")
    if num_tables == 0 or num_tables > _MAX_SFNT_TABLES:
        raise FontEmbeddingException("The font SFNT table count is invalid.")
    record = directory_offset + 12
    if record + num_tables * 16 > len(data):
        raise FontEmbeddingException("The font SFNT table directory is truncated.")
    tables: dict[str, bytes] = {}
    for _ in range(num_tables):
        tag_bytes = data[record : record + 4]
        offset, length = struct.unpack_from(">II", data, record + 8)
        record += 16
        if offset > len(data) or length > len(data) - offset:
            raise FontEmbeddingException("An SFNT table points outside the font data.")
        tag = tag_bytes.decode("latin-1")
        if tag in tables:
            raise FontEmbeddingException(
                f"The font SFNT contains duplicate {tag!r} tables."
            )
        tables[tag] = data[offset : offset + length]
    return flavor, tables


def _read_sfnt_metrics(data: bytes, tables: dict[str, bytes]) -> _SfntMetrics:
    required = {"head", "hhea", "hmtx", "maxp"}
    if not required <= tables.keys():
        missing = ", ".join(sorted(required - tables.keys()))
        raise FontEmbeddingException(f"The font is missing required tables: {missing}.")
    head = tables["head"]
    hhea = tables["hhea"]
    hmtx = tables["hmtx"]
    maxp = tables["maxp"]
    if len(head) < 54 or len(hhea) < 36 or len(maxp) < 6:
        raise FontEmbeddingException("The font metric tables are truncated.")

    units_per_em = struct.unpack_from(">H", head, 18)[0]
    num_glyphs = struct.unpack_from(">H", maxp, 4)[0]
    num_h_metrics = struct.unpack_from(">H", hhea, 34)[0]
    if not 16 <= units_per_em <= 16384 or num_glyphs == 0:
        raise FontEmbeddingException("The font units or glyph count is invalid.")
    if not 0 < num_h_metrics <= num_glyphs:
        raise FontEmbeddingException("The font horizontal metric count is invalid.")
    required_hmtx = num_h_metrics * 4 + (num_glyphs - num_h_metrics) * 2
    if len(hmtx) < required_hmtx:
        raise FontEmbeddingException("The font hmtx table is truncated.")

    raw_advances = [
        struct.unpack_from(">H", hmtx, index * 4)[0]
        for index in range(num_h_metrics)
    ]
    if num_h_metrics < num_glyphs:
        raw_advances.extend(
            [raw_advances[-1]] * (num_glyphs - num_h_metrics)
        )
    advances = tuple(_scale_metric(value, units_per_em) for value in raw_advances)

    x_min, y_min, x_max, y_max = struct.unpack_from(">hhhh", head, 36)
    ascent, descent = struct.unpack_from(">hh", hhea, 4)
    cap_height = ascent
    weight = 400
    italic_selection = False
    os2 = tables.get("OS/2")
    if os2 is not None:
        if len(os2) >= 6:
            weight = struct.unpack_from(">H", os2, 4)[0] or 400
        if len(os2) >= 64:
            italic_selection = bool(struct.unpack_from(">H", os2, 62)[0] & 0x0001)
        if len(os2) >= 72:
            typo_ascent, typo_descent = struct.unpack_from(">hh", os2, 68)
            if typo_ascent or typo_descent:
                ascent, descent = typo_ascent, typo_descent
        os2_version = struct.unpack_from(">H", os2, 0)[0] if len(os2) >= 2 else 0
        if os2_version >= 2 and len(os2) >= 90:
            parsed_cap_height = struct.unpack_from(">h", os2, 88)[0]
            if parsed_cap_height:
                cap_height = parsed_cap_height

    italic_angle = 0.0
    fixed_pitch = False
    post = tables.get("post")
    if post is not None and len(post) >= 16:
        italic_angle = struct.unpack_from(">i", post, 4)[0] / 65536.0
        fixed_pitch = struct.unpack_from(">I", post, 12)[0] != 0

    flags = 32  # Nonsymbolic
    if fixed_pitch:
        flags |= 1
    if italic_selection or italic_angle:
        flags |= 64
    bbox = tuple(
        _scale_metric(value, units_per_em)
        for value in (x_min, y_min, x_max, y_max)
    )
    descriptor: dict[str, int | float | tuple[int, int, int, int]] = {
        "Flags": flags,
        "FontBBox": bbox,
        "ItalicAngle": italic_angle,
        "Ascent": _scale_metric(ascent, units_per_em),
        "Descent": _scale_metric(descent, units_per_em),
        "CapHeight": _scale_metric(cap_height, units_per_em),
        "StemV": max(50, min(200, round(weight / 5))),
        "MissingWidth": advances[0],
    }
    return _SfntMetrics(descriptor, advances, num_glyphs)


def _scale_metric(value: int, units_per_em: int) -> int:
    return round(value * 1000.0 / units_per_em)


def _safe_font_name(value: Any) -> str:
    text = str(value or "Font").strip().lstrip("/")
    text = text.encode("ascii", errors="ignore").decode("ascii")
    text = _SAFE_FONT_NAME_RE.sub("-", text).strip("-.")
    return (text or "Font")[:127]


def _subset_prefix(digest: bytes) -> str:
    return "".join(chr(ord("A") + value % 26) for value in digest[:6])
