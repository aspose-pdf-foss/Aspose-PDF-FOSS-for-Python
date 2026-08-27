"""Discover external font programs to substitute for non-embedded PDF fonts.

The page renderer falls back to the bundled substitute faces (see
:mod:`aspose_pdf.engine.std_font_data`) when a font carries no embedded
program. Those faces are Latin and symbol only, so a document that names a CJK
or otherwise non-Latin face -- routinely the case for East Asian PDFs, which
leave the system fonts unembedded -- has nothing to draw with and falls back to
glyph boxes.

This module indexes font files the caller makes available (explicit
directories, explicit in-memory programs, and optionally the platform font
directories) and resolves a PDF ``/BaseFont`` name against them. Resolution
goes in three steps:

1. **By name** -- the PostScript, full and family names of every indexed face,
   matched on a separator-insensitive form and refined by bold/italic style.
2. **By preference** -- when a name misses and the text's script is known, the
   well-known families for that script in order, so a document naming
   ``SimSun`` still renders on a machine that only has ``PingFang SC``.
3. **By coverage** -- the ``OS/2`` ``ulUnicodeRange`` bits of each indexed face
   pre-filter candidates that claim the required blocks, and the winner is
   confirmed against its real ``cmap``.

Indexing reads only the SFNT table directory, the ``name`` table and 16 bytes
of ``OS/2`` per face, so a directory of large CJK fonts costs a few small reads
each rather than a full decode. Whole programs are read (and TrueType
Collection faces extracted) only for a face that actually wins, and are cached
under a byte budget.

Nothing here is reached unless the caller opts in with
:class:`aspose_pdf.font_substitution.FontSubstitutionOptions`; rendering
without it behaves exactly as before.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from weakref import WeakKeyDictionary

from .sfnt import _parse_name_table

__all__ = ["FontResolver", "ResolvedFace", "resolver_for"]

_TTC_TAG = b"ttcf"
_WOFF_TAGS = (b"wOFF", b"wOF2")
_SFNT_VERSIONS = (0x00010000, 0x74727565, 0x74797031, 0x4F54544F)
_OTTO = 0x4F54544F

_NAME_FAMILY = 1
_NAME_SUBFAMILY = 2
_NAME_FULL = 4
_NAME_POSTSCRIPT = 6

# Indexing bounds. They cap the work a hostile or merely enormous font
# directory can cause; a directory past the limit is indexed up to it.
_MAX_INDEXED_FILES = 4096
_MAX_FACES_PER_FILE = 128
_MAX_TABLES_PER_FACE = 512
_MAX_NAME_TABLE_BYTES = 1 << 20
_MAX_FILE_BYTES = 64 << 20
_MAX_CACHED_PROGRAM_BYTES = 192 << 20
# Faces whose OS/2 bits claim the required blocks but whose cmap must still be
# checked. Bounded so a coverage miss cannot walk a whole font directory.
_MAX_COVERAGE_PROBES = 8

_FONT_SUFFIXES = frozenset({".otf", ".ttc", ".ttf", ".otc", ".woff", ".woff2"})

_SEPARATORS = re.compile(r"[\s,_+\-]")
_SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")

_BOLD_WORDS = ("bold", "black", "heavy", "semibold", "demibold")
_ITALIC_WORDS = ("italic", "oblique")


def _normalize(name: str) -> str:
    """Lower-case *name* and drop separators so name spellings align."""
    return _SEPARATORS.sub("", _SUBSET_PREFIX.sub("", name or "")).lower()


# ---------------------------------------------------------------------------
# OS/2 ulUnicodeRange bits, for the blocks that decide a substitution.
# ---------------------------------------------------------------------------
# (first, last, bit) over the ranges worth pre-filtering on. Anything outside
# the table reports "unknown", which never rejects a candidate.
_UNICODE_RANGE_BITS: tuple[tuple[int, int, int], ...] = (
    (0x0000, 0x007F, 0),
    (0x0080, 0x00FF, 1),
    (0x0100, 0x017F, 2),
    (0x0370, 0x03FF, 7),
    (0x0400, 0x052F, 9),
    (0x0590, 0x05FF, 11),
    (0x0600, 0x06FF, 13),
    (0x0900, 0x097F, 15),
    (0x0980, 0x09FF, 16),
    (0x0A00, 0x0A7F, 17),
    (0x0A80, 0x0AFF, 18),
    (0x0B00, 0x0B7F, 19),
    (0x0B80, 0x0BFF, 20),
    (0x0C00, 0x0C7F, 21),
    (0x0C80, 0x0CFF, 22),
    (0x0D00, 0x0D7F, 23),
    (0x0E00, 0x0E7F, 24),
    (0x0E80, 0x0EFF, 25),
    (0x10A0, 0x10FF, 26),
    (0x1100, 0x11FF, 28),
    (0x1E00, 0x1EFF, 29),
    (0x2000, 0x206F, 31),
    (0x20A0, 0x20CF, 32),
    (0x2190, 0x21FF, 37),
    (0x2200, 0x22FF, 38),
    (0x2500, 0x257F, 40),
    (0x2600, 0x26FF, 42),
    (0x3000, 0x303F, 48),
    (0x3040, 0x309F, 49),
    (0x30A0, 0x30FF, 50),
    (0x3100, 0x312F, 51),
    (0x3130, 0x318F, 52),
    (0x3200, 0x32FF, 54),
    (0x3300, 0x33FF, 55),
    (0xAC00, 0xD7A3, 56),
    (0x2E80, 0x2FDF, 59),
    (0x3190, 0x319F, 59),
    (0x3400, 0x4DBF, 59),
    (0x4E00, 0x9FFF, 59),
    (0xF900, 0xFAFF, 61),
    (0xE000, 0xF8FF, 60),
    (0xFB00, 0xFB4F, 62),
    (0xFE30, 0xFE4F, 65),
    (0xFF00, 0xFFEF, 68),
    (0x20000, 0x2A6DF, 59),
)


def _range_bit(scalar: int) -> int | None:
    """OS/2 ``ulUnicodeRange`` bit covering *scalar*, or ``None``."""
    for first, last, bit in _UNICODE_RANGE_BITS:
        if first <= scalar <= last:
            return bit
    return None


# ---------------------------------------------------------------------------
# Per-script family preferences, tried when the document's own name misses.
# ---------------------------------------------------------------------------
# Faces that cover every East Asian collection, tried after the script's own
# families and before falling back to a coverage scan.
_PAN_UNICODE = ("Arial Unicode MS", "Noto Sans CJK", "Source Han Sans")

_SANS_PREFERENCES: dict[str, tuple[str, ...]] = {
    "Japan1": (
        "Hiragino Sans", "Hiragino Kaku Gothic ProN", "Hiragino Kaku Gothic Pro",
        "Yu Gothic", "Meiryo", "MS Gothic", "MS PGothic", "Noto Sans CJK JP",
        "Noto Sans JP", "Source Han Sans JP", "Source Han Sans", "IPAGothic",
        "TakaoGothic", "VL Gothic", "Osaka", *_PAN_UNICODE,
    ),
    "GB1": (
        "PingFang SC", "Noto Sans CJK SC", "Noto Sans SC", "Source Han Sans SC",
        "Source Han Sans CN", "Microsoft YaHei", "SimHei", "SimSun", "Heiti SC",
        "STHeiti", "Hiragino Sans GB", "WenQuanYi Micro Hei",
        "WenQuanYi Zen Hei", "Droid Sans Fallback", *_PAN_UNICODE,
    ),
    "CNS1": (
        "PingFang TC", "Noto Sans CJK TC", "Noto Sans TC", "Source Han Sans TC",
        "Microsoft JhengHei", "Heiti TC", "STHeiti", "PMingLiU", "MingLiU",
        "Droid Sans Fallback", *_PAN_UNICODE,
    ),
    "Korea1": (
        "Apple SD Gothic Neo", "Noto Sans CJK KR", "Noto Sans KR",
        "Source Han Sans KR", "Source Han Sans K", "Malgun Gothic",
        "AppleGothic", "NanumGothic", "Gulim", "Dotum", *_PAN_UNICODE,
    ),
}

_SERIF_PREFERENCES: dict[str, tuple[str, ...]] = {
    "Japan1": (
        "Hiragino Mincho ProN", "Hiragino Mincho Pro", "YuMincho", "MS Mincho",
        "Noto Serif CJK JP", "Noto Serif JP", "Source Han Serif JP",
        "IPAMincho", "TakaoMincho",
    ),
    "GB1": (
        "Songti SC", "STSong", "SimSun", "NSimSun", "Noto Serif CJK SC",
        "Noto Serif SC", "Source Han Serif SC", "FangSong", "KaiTi",
    ),
    "CNS1": (
        "Songti TC", "STSong", "PMingLiU", "MingLiU", "Noto Serif CJK TC",
        "Noto Serif TC", "Source Han Serif TC",
    ),
    "Korea1": (
        "AppleMyungjo", "Batang", "BatangChe", "Noto Serif CJK KR",
        "Noto Serif KR", "Source Han Serif KR", "NanumMyeongjo",
    ),
}

# A representative scalar per character collection, used to confirm coverage
# when no preferred family is installed.
_ORDERING_PROBE_SCALARS: dict[str, tuple[int, ...]] = {
    "Japan1": (0x3042, 0x4E00),
    "GB1": (0x4E00, 0x5B57),
    "CNS1": (0x4E00, 0x5B57),
    "Korea1": (0xAC00, 0xD55C),
}


@dataclass(frozen=True)
class ResolvedFace:
    """A font program picked for a non-embedded PDF font."""

    data: bytes
    """The standalone SFNT program (a collection face is extracted first)."""

    name: str
    """The face's best display name, for diagnostics."""

    is_cff: bool
    """``True`` when outlines live in a ``CFF `` table rather than ``glyf``."""

    variation: dict[str, float] | None = None
    """Variable-font axis coordinates to draw at, when the face has axes.

    A modern system font is usually one variable file rather than four static
    ones, so reaching Bold or Italic means asking for a *coordinate*, not a
    different file. Honoured for CFF2 outlines.
    """


@dataclass
class _Source:
    """One place a font program can be read from."""

    key: str
    path: Path | None = None
    data: bytes | None = None
    label: str = ""
    """Caller-supplied name or file stem, indexed alongside the real names.

    Subset and stripped fonts routinely ship without a usable ``name`` table,
    and a caller who hands over a program under an explicit name means that
    name to be matchable. Real ``name`` table entries still rank first.
    """


@dataclass
class _Face:
    """One face inside a source, indexed by its names and coverage bits."""

    source: _Source
    face_index: int
    family: str = ""
    subfamily: str = ""
    full: str = ""
    postscript: str = ""
    is_cff: bool = False
    unicode_bits: int = 0
    priority: int = 0
    names: frozenset[str] = field(default_factory=frozenset)

    @property
    def best_name(self) -> str:
        return (
            self.family
            or self.full
            or self.postscript
            or self.source.label
            or self.source.key
        )

    def claims(self, scalar: int) -> bool:
        """Whether ``OS/2`` claims *scalar*'s block (``True`` when unknown)."""
        if not self.unicode_bits:
            return True
        bit = _range_bit(scalar)
        if bit is None:
            return True
        return bool(self.unicode_bits >> bit & 1)


class _Reader:
    """Bounded random access over a font file or an in-memory program."""

    def __init__(self, source: _Source):
        self._source = source
        self._handle: Any = None
        self._data = source.data

    def __enter__(self) -> _Reader:
        if self._data is None and self._source.path is not None:
            self._handle = self._source.path.open("rb")
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def at(self, offset: int, length: int) -> bytes:
        if offset < 0 or length <= 0:
            return b""
        if self._data is not None:
            return self._data[offset : offset + length]
        if self._handle is None:
            return b""
        self._handle.seek(offset)
        return self._handle.read(length)


class FontResolver:
    """Index external font sources and resolve PDF font names against them."""

    def __init__(
        self,
        *,
        directories: tuple[Path, ...] = (),
        programs: tuple[tuple[str, bytes], ...] = (),
        use_system_fonts: bool = False,
        max_files: int = _MAX_INDEXED_FILES,
        max_file_bytes: int = _MAX_FILE_BYTES,
    ) -> None:
        self._directories = directories
        self._programs = programs
        self._use_system_fonts = use_system_fonts
        self._max_files = max(0, int(max_files))
        self._max_file_bytes = max(0, int(max_file_bytes))
        self._faces: list[_Face] | None = None
        self._by_name: dict[str, list[_Face]] = {}
        self._program_cache: dict[tuple[str, int], bytes | None] = {}
        self._cached_bytes = 0

    # -- index ------------------------------------------------------------

    def _sources(self) -> list[_Source]:
        sources = [
            _Source(key=f"memory:{index}:{name}", data=data, label=name)
            for index, (name, data) in enumerate(self._programs)
        ]
        seen: set[str] = set()
        budget = self._max_files
        for directory in self._roots():
            if budget <= 0:
                break
            for path in _iter_font_files(directory):
                key = str(path)
                if key in seen:
                    continue
                seen.add(key)
                sources.append(_Source(key=key, path=path, label=path.stem))
                budget -= 1
                if budget <= 0:
                    break
        return sources

    def _roots(self) -> list[Path]:
        roots = list(self._directories)
        if self._use_system_fonts:
            roots.extend(_system_font_directories())
        return roots

    def _index(self) -> list[_Face]:
        if self._faces is not None:
            return self._faces
        faces: list[_Face] = []
        for priority, source in enumerate(self._sources()):
            for face in _read_faces(source):
                # Explicit programs come first and keep their declared order,
                # so a caller-supplied face always outranks a discovered one.
                face.priority = priority
                faces.append(face)
        by_name: dict[str, list[_Face]] = {}
        for face in faces:
            for name in face.names:
                by_name.setdefault(name, []).append(face)
        for bucket in by_name.values():
            bucket.sort(key=lambda item: item.priority)
        self._faces = faces
        self._by_name = by_name
        return faces

    # -- resolution -------------------------------------------------------

    def by_name(
        self,
        base_font: str | None,
        *,
        flags: int = 0,
        italic_angle: float = 0.0,
        font_weight: float | None = None,
    ) -> ResolvedFace | None:
        """Resolve a PDF ``/BaseFont`` name to an indexed face, or ``None``."""
        if not base_font:
            return None
        self._index()
        wanted = _normalize(base_font)
        if not wanted:
            return None
        bold, italic = _style_wanted(base_font, flags, italic_angle, font_weight)
        candidates = self._by_name.get(wanted)
        if not candidates:
            # "Arial-BoldMT" indexes as one name; also try it without its
            # trailing style words so it can match the plain family face.
            stripped = _strip_style_words(wanted)
            if stripped and stripped != wanted:
                candidates = self._by_name.get(stripped)
        if not candidates:
            return None
        return self._load_best(candidates, bold=bold, italic=italic)

    def by_ordering(
        self,
        ordering: str | None,
        *,
        serif: bool = False,
        bold: bool = False,
        italic: bool = False,
        probe_scalars: tuple[int, ...] = (),
    ) -> ResolvedFace | None:
        """Resolve a face for a character collection (``GB1``, ``Japan1``...)."""
        self._index()
        preferences: list[str] = []
        if ordering:
            if serif:
                preferences.extend(_SERIF_PREFERENCES.get(ordering, ()))
                preferences.extend(_SANS_PREFERENCES.get(ordering, ()))
            else:
                preferences.extend(_SANS_PREFERENCES.get(ordering, ()))
                preferences.extend(_SERIF_PREFERENCES.get(ordering, ()))
        for preferred in preferences:
            candidates = self._by_name.get(_normalize(preferred))
            if candidates:
                face = self._load_best(candidates, bold=bold, italic=italic)
                if face is not None:
                    return face
        scalars = probe_scalars or _ORDERING_PROBE_SCALARS.get(ordering or "", ())
        return self.by_coverage(scalars, bold=bold, italic=italic) if scalars else None

    def by_coverage(
        self,
        scalars: tuple[int, ...],
        *,
        bold: bool = False,
        italic: bool = False,
    ) -> ResolvedFace | None:
        """Resolve any indexed face whose ``cmap`` covers every scalar."""
        wanted = tuple(dict.fromkeys(s for s in scalars if s > 0))
        if not wanted:
            return None
        faces = self._index()
        claiming = [f for f in faces if all(f.claims(s) for s in wanted)]
        claiming.sort(key=lambda f: (f.priority, _style_distance(f, bold, italic)))
        from .font_subset import read_unicode_cmap

        probes = 0
        for face in claiming:
            if probes >= _MAX_COVERAGE_PROBES:
                break
            program = self._program(face)
            if program is None:
                continue
            probes += 1
            cmap = read_unicode_cmap(program)
            if cmap and all(cmap.get(s) for s in wanted):
                return ResolvedFace(program, face.best_name, face.is_cff)
        return None

    # -- program loading --------------------------------------------------

    def _load_best(
        self, candidates: list[_Face], *, bold: bool, italic: bool
    ) -> ResolvedFace | None:
        # Closest style first, then source order -- which puts caller-supplied
        # programs ahead of discovered files at equal style distance.
        ordered = sorted(
            candidates, key=lambda f: (_style_distance(f, bold, italic), f.priority)
        )
        for face in ordered:
            program = self._program(face)
            if program is not None:
                variation = _variation_for(
                    program, bold=bold, italic=italic, face=face
                )
                return ResolvedFace(
                    program, face.best_name, face.is_cff, variation
                )
        return None

    def _program(self, face: _Face) -> bytes | None:
        key = (face.source.key, face.face_index)
        if key in self._program_cache:
            return self._program_cache[key]
        program = self._read_program(face)
        if program is not None and self._cached_bytes + len(program) <= (
            _MAX_CACHED_PROGRAM_BYTES
        ):
            self._program_cache[key] = program
            self._cached_bytes += len(program)
        elif program is None:
            self._program_cache[key] = None
        return program

    def _read_program(self, face: _Face) -> bytes | None:
        source = face.source
        data = source.data
        if data is None:
            path = source.path
            if path is None:
                return None
            try:
                if path.stat().st_size > self._max_file_bytes:
                    return None
                data = path.read_bytes()
            except OSError:
                return None
        if data[:4] in _WOFF_TAGS:
            from .woff import decode as decode_woff

            decoded = decode_woff(data)
            if decoded is None:
                return None
            data = decoded
        if data[:4] != _TTC_TAG and face.face_index == 0:
            return data
        return _extract_collection_face(data, face.face_index)


def _extract_collection_face(data: bytes, face_index: int) -> bytes | None:
    """Lift one face out of a TrueType Collection as a standalone SFNT.

    A collection shares table bodies between faces, so the outline parsers --
    which all read from offset 0 -- need the face's own directory rewritten
    against a fresh layout. The original per-table checksums are copied
    verbatim rather than recomputed: nothing downstream validates them, and
    summing a 50 MB CJK font in Python costs seconds.
    (:func:`aspose_pdf.engine.font_authoring._select_sfnt_face` is the strict
    variant, for programs that get embedded into a PDF.)
    """
    if len(data) < 12 or data[:4] != _TTC_TAG:
        return None
    try:
        num_fonts = struct.unpack_from(">I", data, 8)[0]
        if not 0 <= face_index < min(num_fonts, _MAX_FACES_PER_FILE):
            return None
        base = struct.unpack_from(">I", data, 12 + 4 * face_index)[0]
        version, num_tables = struct.unpack_from(">IH", data, base)
        if version not in _SFNT_VERSIONS or not 0 < num_tables <= (
            _MAX_TABLES_PER_FACE
        ):
            return None
        records = []
        for i in range(num_tables):
            record = base + 12 + 16 * i
            tag = data[record : record + 4]
            checksum, offset, length = struct.unpack_from(">III", data, record + 4)
            if len(tag) != 4 or offset > len(data) or length > len(data) - offset:
                return None
            records.append((tag, checksum, offset, length))
    except struct.error:
        return None

    entry_selector = max(num_tables.bit_length() - 1, 0)
    search_range = (1 << entry_selector) * 16
    out = bytearray(
        struct.pack(
            ">IHHHH",
            version,
            num_tables,
            search_range,
            entry_selector,
            num_tables * 16 - search_range,
        )
    )
    cursor = 12 + 16 * num_tables
    body = bytearray()
    for tag, checksum, offset, length in records:
        out += tag + struct.pack(">III", checksum, cursor, length)
        body += data[offset : offset + length]
        padding = -length % 4
        body += b"\x00" * padding
        cursor += length + padding
    return bytes(out + body)


def _variation_for(
    program: bytes, *, bold: bool, italic: bool, face: _Face
) -> dict[str, float] | None:
    """Axis coordinates that reach the wanted style, or ``None``.

    Only asked of a face that actually carries axes, and only for the part of
    the style the face does not already supply: a file whose own name says
    "Bold" is already there, and moving its ``wght`` further would overshoot.
    """
    from .cff_outlines import _parse_fvar_axes

    try:
        axes = {axis["tag"]: axis for axis in _parse_fvar_axes(program)}
    except Exception:  # noqa: BLE001 - a malformed fvar just means no axes
        return None
    if not axes:
        return None
    # The label counts alongside the name table: a caller-supplied program is
    # known by the name the caller gave it, and a stripped subset may carry no
    # name table at all.
    text = _normalize(
        f"{face.subfamily} {face.full} {face.postscript} {face.source.label}"
    )
    variation: dict[str, float] = {}
    if bold and "wght" in axes and not any(word in text for word in _BOLD_WORDS):
        variation["wght"] = min(700.0, float(axes["wght"]["max"]))
    if italic and not any(word in text for word in _ITALIC_WORDS):
        if "ital" in axes:
            variation["ital"] = float(axes["ital"]["max"])
        elif "slnt" in axes:
            variation["slnt"] = float(axes["slnt"]["min"])
    return variation or None


def _style_wanted(
    base_font: str, flags: int, italic_angle: float, font_weight: float | None
) -> tuple[bool, bool]:
    name = _normalize(base_font)
    bold = (
        any(word in name for word in _BOLD_WORDS)
        or bool(flags & (1 << 18))
        or (font_weight is not None and font_weight >= 600)
    )
    italic = (
        any(word in name for word in _ITALIC_WORDS)
        or bool(flags & (1 << 6))
        or abs(italic_angle) > 1e-6
    )
    return bold, italic


def _strip_style_words(name: str) -> str:
    for word in (*_BOLD_WORDS, *_ITALIC_WORDS, "regular", "roman", "mt", "ps"):
        if name.endswith(word) and len(name) > len(word):
            name = name[: -len(word)]
    return name


def _style_distance(face: _Face, bold: bool, italic: bool) -> int:
    """Rank a face against the wanted style; lower is closer."""
    text = _normalize(f"{face.subfamily} {face.full} {face.postscript}")
    face_bold = any(word in text for word in _BOLD_WORDS)
    face_italic = any(word in text for word in _ITALIC_WORDS)
    return int(face_bold != bold) + int(face_italic != italic)


def _iter_font_files(directory: Path):
    """Yield font files under *directory*, deepest-last and sorted."""
    try:
        if not directory.is_dir():
            return
        entries = sorted(directory.rglob("*"))
    except OSError:
        return
    for path in entries:
        try:
            if path.suffix.lower() in _FONT_SUFFIXES and path.is_file():
                yield path
        except OSError:
            continue


def _system_font_directories() -> list[Path]:
    """Platform font directories, reusing the public repository's list."""
    from aspose_pdf.font_repository import SystemFontSource

    return [Path(entry) for entry in SystemFontSource._directories()]  # noqa: SLF001


def _read_faces(source: _Source) -> list[_Face]:
    """Index every face in *source* from its directory, names and ``OS/2``."""
    try:
        with _Reader(source) as reader:
            head = reader.at(0, 12)
            if len(head) < 12:
                return []
            if head[:4] in _WOFF_TAGS:
                return _read_wrapped_faces(source)
            offsets: list[int]
            if head[:4] == _TTC_TAG:
                count = struct.unpack_from(">I", head, 8)[0]
                count = min(count, _MAX_FACES_PER_FILE)
                table = reader.at(12, 4 * count)
                offsets = [
                    struct.unpack_from(">I", table, 4 * i)[0]
                    for i in range(len(table) // 4)
                ]
            else:
                if struct.unpack_from(">I", head, 0)[0] not in _SFNT_VERSIONS:
                    return []
                offsets = [0]
            faces = []
            for index, offset in enumerate(offsets):
                face = _read_face(reader, source, index, offset)
                if face is not None:
                    faces.append(face)
            return faces
    except (OSError, struct.error, ValueError):
        return []


def _read_wrapped_faces(source: _Source) -> list[_Face]:
    """Index a WOFF/WOFF2 wrapper by decoding it once (they are small)."""
    from .woff import decode as decode_woff

    data = source.data
    if data is None and source.path is not None:
        try:
            if source.path.stat().st_size > _MAX_FILE_BYTES:
                return []
            data = source.path.read_bytes()
        except OSError:
            return []
    if not data:
        return []
    decoded = decode_woff(data)
    if decoded is None:
        return []
    unwrapped = _Source(
        key=source.key, path=None, data=decoded, label=source.label
    )
    faces = _read_faces(unwrapped)
    for face in faces:
        # Keep reading through the original source so the program loader
        # unwraps the same way this index did.
        face.source = source
    return faces


def _read_face(
    reader: _Reader, source: _Source, face_index: int, offset: int
) -> _Face | None:
    header = reader.at(offset, 12)
    if len(header) < 12:
        return None
    version, num_tables = struct.unpack_from(">IH", header, 0)
    if version not in _SFNT_VERSIONS or not 0 < num_tables <= _MAX_TABLES_PER_FACE:
        return None
    directory = reader.at(offset + 12, num_tables * 16)
    tables: dict[str, tuple[int, int]] = {}
    for i in range(len(directory) // 16):
        tag = directory[i * 16 : i * 16 + 4].decode("latin-1")
        table_offset, length = struct.unpack_from(">II", directory, i * 16 + 8)
        tables[tag] = (table_offset, length)
    names = _read_names(reader, tables.get("name"))
    family = names.get(_NAME_FAMILY, "")
    subfamily = names.get(_NAME_SUBFAMILY, "")
    full = names.get(_NAME_FULL, "")
    postscript = names.get(_NAME_POSTSCRIPT, "")
    indexed = {
        _normalize(text)
        for text in (postscript, full, family, f"{family}{subfamily}", source.label)
        if text
    }
    indexed.discard("")
    if not indexed:
        return None
    return _Face(
        source=source,
        face_index=face_index,
        family=family,
        subfamily=subfamily,
        full=full,
        postscript=postscript,
        is_cff=version == _OTTO or "CFF " in tables or "CFF2" in tables,
        unicode_bits=_read_unicode_ranges(reader, tables.get("OS/2")),
        names=frozenset(indexed),
    )


def _read_names(
    reader: _Reader, location: tuple[int, int] | None
) -> dict[int, str]:
    if location is None:
        return {}
    offset, length = location
    if length <= 0 or length > _MAX_NAME_TABLE_BYTES:
        return {}
    table = reader.at(offset, length)
    if len(table) < 6:
        return {}
    # The parser addresses everything relative to the table start, so handing
    # it the isolated table at offset 0 gives the same result as the whole file.
    return _parse_name_table(table, (0, len(table)))


def _read_unicode_ranges(
    reader: _Reader, location: tuple[int, int] | None
) -> int:
    """Return ``ulUnicodeRange1..4`` as one 128-bit integer, 0 when absent."""
    if location is None:
        return 0
    offset, length = location
    if length < 58:
        return 0
    raw = reader.at(offset + 42, 16)
    if len(raw) < 16:
        return 0
    r1, r2, r3, r4 = struct.unpack(">IIII", raw)
    return r1 | (r2 << 32) | (r3 << 64) | (r4 << 96)


_resolvers: WeakKeyDictionary[Any, FontResolver] = WeakKeyDictionary()


def resolver_for(options: Any) -> FontResolver | None:
    """Return the cached :class:`FontResolver` for *options*, or ``None``.

    The resolver -- and with it the font index -- is built once per options
    object and reused for every page rendered with it.
    """
    if options is None:
        return None
    existing = _resolvers.get(options)
    if existing is not None:
        return existing
    resolver = FontResolver(
        directories=tuple(options.directories),
        programs=tuple(options.fonts.items()),
        use_system_fonts=bool(options.use_system_fonts),
    )
    try:
        _resolvers[options] = resolver
    except TypeError:  # pragma: no cover - options that cannot be weak-referenced
        pass
    return resolver
