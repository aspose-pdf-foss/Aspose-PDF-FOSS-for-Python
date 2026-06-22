"""Dependency-free SFNT (TrueType/OpenType) font parser.

This module reads just enough of the SFNT container format to support font
discovery and embedding:

* the table directory (to classify TrueType vs. OpenType/CFF), and
* the ``name`` table (to recover the real family / subfamily / full /
  PostScript names instead of guessing from the file name).

TrueType Collections (``.ttc``) are supported and yield one
:class:`SfntFace` per contained face. WOFF 1.0 wrappers are transparently
unwrapped to their underlying SFNT first (see :mod:`aspose_pdf.engine.woff`),
so a ``.woff`` font reports the same names and type as the ``.ttf`` / ``.otf``
inside it.

Parsing is intentionally defensive: malformed or truncated input never
raises, it simply yields fewer (or no) faces. WOFF2 wrappers are unwrapped
too when the optional ``brotli`` package is installed; otherwise they fall
back to file-name based metadata.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from aspose_pdf.engine.woff import decode as decode_woff

__all__ = [
    "SfntFace",
    "parse_faces",
    "is_sfnt",
]

# sfnt version magic numbers.
_VERSION_TRUETYPE = 0x00010000
_VERSION_TRUE = 0x74727565  # 'true' (legacy Apple TrueType)
_VERSION_TYP1 = 0x74797031  # 'typ1'
_VERSION_OTTO = 0x4F54544F  # 'OTTO' (OpenType with CFF outlines)

_TTC_TAG = b"ttcf"
_WOFF_TAG = b"wOFF"
_WOFF2_TAG = b"wOF2"

# name table identifiers (see OpenType spec, "name" table).
_NAME_FAMILY = 1
_NAME_SUBFAMILY = 2
_NAME_FULL = 4
_NAME_POSTSCRIPT = 6


@dataclass
class SfntFace:
    """Metadata recovered from a single SFNT face."""

    family_name: str = ""
    subfamily_name: str = ""
    full_name: str = ""
    postscript_name: str = ""
    font_type: str = "TrueType"
    table_tags: frozenset[str] = field(default_factory=frozenset)

    @property
    def best_name(self) -> str:
        """Return the most user-meaningful name available."""
        return (
            self.family_name
            or self.full_name
            or self.postscript_name
            or self.subfamily_name
        )


def is_sfnt(data: bytes) -> bool:
    """Return ``True`` if *data* looks like a parseable SFNT container."""
    if len(data) < 4:
        return False
    if data[:4] == _TTC_TAG:
        return True
    version = struct.unpack_from(">I", data, 0)[0]
    return version in (
        _VERSION_TRUETYPE,
        _VERSION_TRUE,
        _VERSION_TYP1,
        _VERSION_OTTO,
    )


def parse_faces(data: bytes) -> list[SfntFace]:
    """Parse *data* and return one :class:`SfntFace` per contained face.

    WOFF 1.0 wrappers are unwrapped to their SFNT first (and WOFF2 too when the
    optional ``brotli`` package is present). Returns an empty list for an
    undecodable wrapper or unparseable input.
    """
    if len(data) < 12:
        return []

    head = data[:4]
    if head in (_WOFF_TAG, _WOFF2_TAG):
        # WOFF 1.0 is a zlib wrapper we can always unwrap; WOFF2 is unwrapped
        # only when brotli is installed (decode() returns None otherwise).
        decoded = decode_woff(data)
        if decoded is None:
            return []
        data = decoded
        head = data[:4]

    if head == _TTC_TAG:
        return _parse_collection(data)

    face = _parse_offset_table(data, 0)
    return [face] if face is not None else []


def _parse_collection(data: bytes) -> list[SfntFace]:
    try:
        num_fonts = struct.unpack_from(">I", data, 8)[0]
    except struct.error:
        return []
    # Guard against absurd counts from corrupt files.
    max_fonts = (len(data) - 12) // 4
    if num_fonts > max_fonts:
        num_fonts = max_fonts

    faces: list[SfntFace] = []
    for i in range(num_fonts):
        try:
            offset = struct.unpack_from(">I", data, 12 + 4 * i)[0]
        except struct.error:
            break
        face = _parse_offset_table(data, offset)
        if face is not None:
            faces.append(face)
    return faces


def _parse_offset_table(data: bytes, base: int) -> SfntFace | None:
    try:
        version, num_tables = struct.unpack_from(">IH", data, base)
    except struct.error:
        return None

    tables: dict[str, tuple[int, int]] = {}
    record = base + 12
    for _ in range(num_tables):
        if record + 16 > len(data):
            break
        tag = data[record : record + 4].decode("latin-1")
        offset, length = struct.unpack_from(">II", data, record + 8)
        tables[tag] = (offset, length)
        record += 16

    names = _parse_name_table(data, tables.get("name"))
    return SfntFace(
        family_name=names.get(_NAME_FAMILY, ""),
        subfamily_name=names.get(_NAME_SUBFAMILY, ""),
        full_name=names.get(_NAME_FULL, ""),
        postscript_name=names.get(_NAME_POSTSCRIPT, ""),
        font_type=_classify(version, tables),
        table_tags=frozenset(tables),
    )


def _classify(version: int, tables: dict[str, tuple[int, int]]) -> str:
    if version == _VERSION_OTTO or "CFF " in tables or "CFF2" in tables:
        return "OpenType"
    if "glyf" in tables:
        return "TrueType"
    # Unknown but still an sfnt; default by version.
    return "OpenType" if version == _VERSION_OTTO else "TrueType"


def _parse_name_table(
    data: bytes, location: tuple[int, int] | None
) -> dict[int, str]:
    if location is None:
        return {}
    table_offset, _length = location
    try:
        fmt, count, string_offset = struct.unpack_from(">HHH", data, table_offset)
    except struct.error:
        return {}

    storage_base = table_offset + string_offset
    # For each name id keep the highest-scoring decoded string.
    best: dict[int, tuple[int, str]] = {}
    record = table_offset + 6
    for _ in range(count):
        if record + 12 > len(data):
            break
        (
            platform_id,
            encoding_id,
            language_id,
            name_id,
            str_len,
            str_off,
        ) = struct.unpack_from(">HHHHHH", data, record)
        record += 12

        start = storage_base + str_off
        end = start + str_len
        if start < 0 or end > len(data) or str_len == 0:
            continue
        raw = data[start:end]
        value = _decode_name(platform_id, encoding_id, raw)
        if not value:
            continue
        score = _name_score(platform_id, encoding_id, language_id)
        current = best.get(name_id)
        if current is None or score > current[0]:
            best[name_id] = (score, value)

    return {name_id: text for name_id, (_score, text) in best.items()}


def _decode_name(platform_id: int, encoding_id: int, raw: bytes) -> str:
    try:
        if platform_id == 3 or platform_id == 0:
            # Windows / Unicode platforms use UTF-16BE.
            return raw.decode("utf-16-be").rstrip("\x00").strip()
        if platform_id == 1:
            # Macintosh platform; encoding 0 is Mac Roman.
            codec = "mac-roman" if encoding_id == 0 else "latin-1"
            return raw.decode(codec, errors="replace").strip()
        return raw.decode("latin-1", errors="replace").strip()
    except (UnicodeDecodeError, LookupError):
        return ""


def _name_score(platform_id: int, encoding_id: int, language_id: int) -> int:
    """Rank name records, preferring Windows English Unicode entries."""
    score = 0
    if platform_id == 3:  # Windows
        score += 100
        if encoding_id == 1:  # Unicode BMP
            score += 10
        if language_id == 0x0409:  # English (US)
            score += 5
    elif platform_id == 0:  # Unicode
        score += 80
    elif platform_id == 1:  # Macintosh
        score += 50
        if language_id == 0:  # English
            score += 5
    return score
