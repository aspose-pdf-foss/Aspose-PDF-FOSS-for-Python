"""Dependency-free CFF (``/FontFile3``) glyph outline extraction.

The CFF subsetter (:mod:`.font_subset_cff`) erases unused charstrings without
*interpreting* them; this module runs a Type 2 charstring interpreter so the
page renderer can fill real CFF glyph outlines instead of placeholder boxes.
Both **name-keyed** (``/Type1C``) and **CID-keyed** (``/CIDFontType0C``) CFF
programs are handled, including global/local subroutines, the flex operators,
and a font's ``FontMatrix`` (for the em scale).  A full OpenType (``OTTO``)
wrapper is unwrapped to its ``CFF `` table.  CFF2 (major version 2) is out of
scope.

Outlines are returned as flattened, closed contours in font units (y up),
matching :class:`aspose_pdf.engine.glyph_outlines.TrueTypeOutlines`, so the
renderer can treat the two outline sources interchangeably.

Parsing and interpretation are defensive: malformed input yields an empty
outline (or an inert source) rather than raising.
"""

from __future__ import annotations

import struct
from collections.abc import Mapping
from typing import Any

from .agl import cff_standard_strings
from .font_subset_cff import (
    _OP_CHARSTRINGS,
    _OP_ENCODING,
    _OP_FDARRAY,
    _OP_FDSELECT,
    _OP_PRIVATE,
    _OP_ROS,
    _OP_SUBRS,
    _dict_get,
    _dict_int,
    _dict_ints,
    _parse_dict,
    _read_index,
)

__all__ = ["CffOutlines"]

Point = tuple[float, float]
Contour = list[Point]

_OP_VSTORE = 24  # CFF2 Top DICT: offset to the ItemVariationStore.


def _read_index2(data: bytes, pos: int) -> tuple[list[bytes], int]:
    """Read a CFF2 INDEX (32-bit count), returning items and the next offset."""
    count = struct.unpack_from(">I", data, pos)[0]
    pos += 4
    if count == 0:
        return [], pos
    off_size = data[pos]
    pos += 1
    if off_size < 1 or off_size > 4:
        raise ValueError("bad CFF2 INDEX offSize")
    offsets = []
    for _ in range(count + 1):
        offsets.append(int.from_bytes(data[pos : pos + off_size], "big"))
        pos += off_size
    base = pos - 1
    items = []
    for i in range(count):
        start, end = base + offsets[i], base + offsets[i + 1]
        if not (pos <= start <= end <= len(data)):
            raise ValueError("bad CFF2 INDEX offsets")
        items.append(data[start:end])
    return items, base + offsets[count]

_OP_CHARSET = 15
_OP_FONTMATRIX = (12, 7)

# Segments emitted per cubic bézier span when flattening.
_CURVE_STEPS = 8
_MAX_SUBR_DEPTH = 10


def _subr_bias(count: int) -> int:
    """Return the Type 2 subroutine index bias for *count* subroutines."""
    if count < 1240:
        return 107
    if count < 33900:
        return 1131
    return 32768


def _read_operand(data: bytes, i: int, b0: int) -> tuple[float, int]:
    """Decode one charstring numeric operand starting at *i* (``b0 == data[i]``)."""
    if b0 == 28:
        return float(struct.unpack_from(">h", data, i + 1)[0]), i + 3
    if b0 < 247:  # 32..246
        return float(b0 - 139), i + 1
    if b0 < 251:  # 247..250
        return float((b0 - 247) * 256 + data[i + 1] + 108), i + 2
    if b0 < 255:  # 251..254
        return float(-(b0 - 251) * 256 - data[i + 1] - 108), i + 2
    # 255: 16.16 fixed point.
    return struct.unpack_from(">i", data, i + 1)[0] / 65536.0, i + 5


def _decode_reals(operand_bytes: bytes) -> list[float]:
    """Decode DICT operands as floats, including CFF real (operator 30) numbers."""
    vals: list[float] = []
    i = 0
    n = len(operand_bytes)
    while i < n:
        b0 = operand_bytes[i]
        if b0 == 28:
            vals.append(float(struct.unpack_from(">h", operand_bytes, i + 1)[0]))
            i += 3
        elif b0 == 29:
            vals.append(float(struct.unpack_from(">i", operand_bytes, i + 1)[0]))
            i += 5
        elif b0 == 30:  # real number: nibbles until the 0xf terminator.
            i += 1
            text = ""
            done = False
            while i < n and not done:
                byte = operand_bytes[i]
                i += 1
                for nib in (byte >> 4, byte & 0x0F):
                    if nib <= 9:
                        text += str(nib)
                    elif nib == 0x0A:
                        text += "."
                    elif nib == 0x0B:
                        text += "E"
                    elif nib == 0x0C:
                        text += "E-"
                    elif nib == 0x0E:
                        text += "-"
                    elif nib == 0x0F:
                        done = True
                        break
            try:
                vals.append(float(text))
            except ValueError:
                vals.append(0.0)
        elif 32 <= b0 <= 246:
            vals.append(float(b0 - 139))
            i += 1
        elif 247 <= b0 <= 250:
            vals.append(float((b0 - 247) * 256 + operand_bytes[i + 1] + 108))
            i += 2
        elif 251 <= b0 <= 254:
            vals.append(float(-(b0 - 251) * 256 - operand_bytes[i + 1] - 108))
            i += 2
        else:
            i += 1
    return vals


def _parse_fvar_axes(data: bytes) -> list[dict[str, Any]]:
    """Axis records from an SFNT's ``fvar`` table, or ``[]``."""
    table = _sfnt_table(data, b"fvar")
    if table is None or len(table) < 16:
        return []
    try:
        axes_offset, _, axis_count, axis_size = struct.unpack_from(">HHHH", table, 4)
    except struct.error:
        return []
    axes: list[dict[str, Any]] = []
    for index in range(axis_count):
        start = axes_offset + index * axis_size
        if start + 20 > len(table):
            break
        tag = table[start : start + 4].decode("latin-1")
        minimum, default, maximum = struct.unpack_from(">lll", table, start + 4)
        axes.append(
            {
                "tag": tag,
                "min": minimum / 65536.0,
                "default": default / 65536.0,
                "max": maximum / 65536.0,
            }
        )
    return axes


def _sfnt_table(data: bytes, tag: bytes) -> bytes | None:
    """The named table of an SFNT wrapper, or ``None`` for a bare CFF."""
    if len(data) < 12 or data[:4] not in (b"OTTO", b"\x00\x01\x00\x00", b"true"):
        return None
    try:
        count = struct.unpack_from(">H", data, 4)[0]
        for index in range(count):
            record = 12 + index * 16
            if data[record : record + 4] != tag:
                continue
            offset, length = struct.unpack_from(">II", data, record + 8)
            if offset + length <= len(data):
                return data[offset : offset + length]
    except struct.error:
        return None
    return None


def _normalize_coordinates(
    axes: list[dict[str, Any]],
    variation: Mapping[str, float] | None,
    raw: bytes,
) -> tuple[float, ...]:
    """User axis values -> normalised [-1, 1] coordinates, ``avar`` applied."""
    if not axes:
        return ()
    coords: list[float] = []
    for axis in axes:
        value = None if variation is None else variation.get(axis["tag"])
        if value is None:
            coords.append(0.0)
            continue
        value = max(axis["min"], min(axis["max"], float(value)))
        default = axis["default"]
        if value == default:
            coords.append(0.0)
        elif value < default:
            span = default - axis["min"]
            coords.append((value - default) / span if span else 0.0)
        else:
            span = axis["max"] - default
            coords.append((value - default) / span if span else 0.0)
    return tuple(_apply_avar(raw, coords))


def _apply_avar(raw: bytes, coords: list[float]) -> list[float]:
    """Apply the ``avar`` segment maps, which reshape an axis's response."""
    table = _sfnt_table(raw, b"avar")
    if table is None or len(table) < 8:
        return coords
    try:
        axis_count = struct.unpack_from(">H", table, 6)[0]
    except struct.error:
        return coords
    cursor = 8
    out = list(coords)
    for axis in range(min(axis_count, len(out))):
        try:
            pair_count = struct.unpack_from(">H", table, cursor)[0]
        except struct.error:
            return out
        cursor += 2
        pairs: list[tuple[float, float]] = []
        for _ in range(pair_count):
            try:
                from_value, to_value = struct.unpack_from(">hh", table, cursor)
            except struct.error:
                return out
            pairs.append((from_value / 16384.0, to_value / 16384.0))
            cursor += 4
        out[axis] = _piecewise(pairs, out[axis])
    return out


def _piecewise(pairs: list[tuple[float, float]], value: float) -> float:
    if len(pairs) < 2:
        return value
    for index in range(1, len(pairs)):
        left_from, left_to = pairs[index - 1]
        right_from, right_to = pairs[index]
        if left_from <= value <= right_from:
            span = right_from - left_from
            if span <= 0:
                return left_to
            ratio = (value - left_from) / span
            return left_to + ratio * (right_to - left_to)
    return value


def _region_scalar(
    region: list[tuple[float, float, float]], coords: tuple[float, ...]
) -> float:
    """The region's weight at *coords* (OpenType variation scalar)."""
    scalar = 1.0
    for axis, (start, peak, end) in enumerate(region):
        if peak == 0.0:
            continue  # this axis does not participate in the region
        value = coords[axis] if axis < len(coords) else 0.0
        if value == peak:
            continue
        if value <= start or value >= end:
            return 0.0
        if value < peak:
            scalar *= (value - start) / (peak - start) if peak != start else 0.0
        else:
            scalar *= (end - value) / (end - peak) if end != peak else 0.0
    return scalar


def _maybe_extract_cff(data: bytes) -> bytes:
    """Return the ``CFF ``/``CFF2`` table when *data* is an SFNT/OpenType wrapper."""
    if len(data) >= 12 and data[:4] in (b"OTTO", b"\x00\x01\x00\x00", b"true"):
        try:
            num_tables = struct.unpack_from(">H", data, 4)[0]
            found: dict[bytes, bytes] = {}
            record = 12
            for _ in range(num_tables):
                if record + 16 > len(data):
                    break
                tag = data[record : record + 4]
                if tag in (b"CFF ", b"CFF2"):
                    off, length = struct.unpack_from(">II", data, record + 8)
                    found[tag] = data[off : off + length]
                record += 16
            # Prefer the classic outline table; fall back to CFF2 when only it exists.
            if b"CFF " in found:
                return found[b"CFF "]
            if b"CFF2" in found:
                return found[b"CFF2"]
        except struct.error:
            pass
    return data


class CffOutlines:
    """Decode glyph outlines from a CFF (Type 2 charstring) font program."""

    def __init__(
        self,
        font_bytes: bytes,
        *,
        variation: Mapping[str, float] | None = None,
    ):
        """Decode a CFF program, optionally at a variable-font *instance*.

        *variation* names axis coordinates in user units -- ``{"wght": 700}``
        -- for a CFF2 program that carries an ``fvar`` table. Without it (or
        for any non-variable font) the default master is drawn, exactly as
        before.
        """
        self.units_per_em = 1000
        self.num_glyphs = 0
        self._charstrings: list[bytes] = []
        self._gsubrs: list[bytes] = []
        self._fd_lsubrs: list[list[bytes]] = []
        self._fdselect: list[int] | None = None
        self._is_cid = False
        self._is_cff2 = False
        self._region_counts: dict[int, int] = {}
        # Variation regions from the ItemVariationStore, the region set each
        # ``vsindex`` selects, and the blend scalars for the chosen instance.
        self._regions: list[list[tuple[float, float, float]]] = []
        self._vsindex_regions: dict[int, list[int]] = {}
        self._scalars: dict[int, list[float]] = {}
        self.axes: list[dict[str, Any]] = []
        self.coordinates: tuple[float, ...] = ()
        self._encoding_off: int | None = None
        self._charset_off: int | None = None
        self._strings: list[bytes] = []
        self._name_to_gid: dict[str, int] | None = None
        self._data = b""
        self._cache: dict[int, list[Contour]] = {}
        self._ok = False
        raw = bytes(font_bytes)
        try:
            self.axes = _parse_fvar_axes(raw)
            self._parse(_maybe_extract_cff(raw))
            if self._ok and self._is_cff2 and self._regions:
                self._select_instance(raw, variation)
        except (struct.error, IndexError, ValueError):
            self._ok = False

    @property
    def ok(self) -> bool:
        """``True`` when a CFF program was parsed successfully."""
        return self._ok

    # -- parsing ----------------------------------------------------------

    def _parse(self, data: bytes) -> None:
        self._data = data
        if len(data) < 4:
            return
        major = data[0]
        if major == 2:
            self._parse_cff2(data)
            return
        hdr_size = data[2]
        if major != 1 or hdr_size < 4:
            return  # unknown major version / odd header.

        _name_index, pos = _read_index(data, hdr_size)
        topdict_index, pos = _read_index(data, pos)
        self._strings, pos = _read_index(data, pos)
        gsubr_index, pos = _read_index(data, pos)
        if len(topdict_index) != 1:
            return
        self._gsubrs = gsubr_index

        entries = _parse_dict(topdict_index[0])
        cs_off = _dict_int(entries, _OP_CHARSTRINGS)
        if cs_off is None:
            return
        self._charstrings, _ = _read_index(data, cs_off)
        self.num_glyphs = len(self._charstrings)
        if self.num_glyphs == 0:
            return

        self.units_per_em = self._units_per_em(entries)
        self._encoding_off = _dict_int(entries, _OP_ENCODING)
        self._charset_off = _dict_int(entries, _OP_CHARSET)
        self._is_cid = _dict_get(entries, _OP_ROS) is not None
        if self._is_cid:
            self._parse_cid(data, entries)
        else:
            self._fd_lsubrs = [self._read_local_subrs(data, entries)]
        self._ok = True

    def _parse_cff2(self, data: bytes) -> None:
        """Parse a static (default-instance) CFF2 program.

        CFF2 replaces the name/Top-DICT/String INDEXes with a fixed-length Top
        DICT, uses 32-bit INDEXes, and is always FD-based (FDArray/FDSelect).
        Variable-font ``blend``/``vsindex`` operators are honored only for the
        default instance -- region deltas are dropped -- so a variable CFF2 draws
        its default master rather than an interpolated instance.
        """
        if len(data) < 5:
            return
        hdr_size = data[2]
        top_dict_length = struct.unpack_from(">H", data, 3)[0]
        top_start = hdr_size
        top_end = top_start + top_dict_length
        if hdr_size < 5 or top_end > len(data):
            return
        entries = _parse_dict(data[top_start:top_end])
        self._gsubrs, _ = _read_index2(data, top_end)
        cs_off = _dict_int(entries, _OP_CHARSTRINGS)
        if cs_off is None:
            return
        self._charstrings, _ = _read_index2(data, cs_off)
        self.num_glyphs = len(self._charstrings)
        if self.num_glyphs == 0:
            return
        self.units_per_em = self._units_per_em(entries)
        self._is_cid = True  # CFF2 always selects a Font DICT per glyph.
        fdarray_off = _dict_int(entries, _OP_FDARRAY)
        fdselect_off = _dict_int(entries, _OP_FDSELECT)
        if fdarray_off is not None and fdselect_off is not None:
            fd_items, _ = _read_index2(data, fdarray_off)
            self._fd_lsubrs = [
                self._read_local_subrs(data, _parse_dict(fd), cff2=True)
                for fd in fd_items
            ] or [[]]
            self._fdselect = self._read_fdselect(data, fdselect_off)
        else:
            self._fd_lsubrs = [[]]
        vstore_off = _dict_int(entries, _OP_VSTORE)
        if vstore_off is not None:
            # The CFF2 ``vstore`` offset points at a 2-byte length, *then* the
            # ItemVariationStore. Reading from the length itself finds no store
            # at all -- and with no region counts the ``blend`` operator cannot
            # tell deltas from coordinates, so a variable font drew garbage
            # rather than its default master.
            self._region_counts = self._parse_variation_regions(data, vstore_off + 2)
        self._is_cff2 = True
        self._ok = True

    def _parse_variation_regions(self, data: bytes, off: int) -> dict[int, int]:
        """Read the ItemVariationStore: region counts, axes and per-vsindex sets.

        CFF2 splits variation data in two: the *deltas* travel inside the
        charstring as ``blend`` operands, and this store says how many there
        are and what each one means -- one region per delta, each a triple of
        axis coordinates. Reading only the counts is enough to *skip* the
        deltas; reading the regions is what lets them be applied.
        """
        try:
            if struct.unpack_from(">H", data, off)[0] != 1:
                return {}
            region_list_off = struct.unpack_from(">I", data, off + 2)[0]
            self._regions = self._parse_region_list(data, off + region_list_off)
            data_count = struct.unpack_from(">H", data, off + 6)[0]
            counts: dict[int, int] = {}
            for index in range(data_count):
                ivd_offset = struct.unpack_from(">I", data, off + 8 + 4 * index)[0]
                base = off + ivd_offset
                region_count = struct.unpack_from(">H", data, base + 4)[0]
                counts[index] = region_count
                self._vsindex_regions[index] = [
                    struct.unpack_from(">H", data, base + 6 + 2 * i)[0]
                    for i in range(region_count)
                ]
            return counts
        except struct.error:
            return {}

    @staticmethod
    def _parse_region_list(data: bytes, off: int) -> list[list[tuple[float, float, float]]]:
        """``[[(start, peak, end) per axis] per region]`` from a VarRegionList."""
        try:
            axis_count, region_count = struct.unpack_from(">HH", data, off)
        except struct.error:
            return []
        regions: list[list[tuple[float, float, float]]] = []
        cursor = off + 4
        for _ in range(region_count):
            axes: list[tuple[float, float, float]] = []
            for _axis in range(axis_count):
                try:
                    start, peak, end = struct.unpack_from(">hhh", data, cursor)
                except struct.error:
                    return regions
                axes.append((start / 16384.0, peak / 16384.0, end / 16384.0))
                cursor += 6
            regions.append(axes)
        return regions

    def _select_instance(
        self, raw: bytes, variation: Mapping[str, float] | None
    ) -> None:
        """Pick a variable-font instance and precompute its blend scalars."""
        coords = _normalize_coordinates(self.axes, variation, raw)
        self.coordinates = coords
        if not coords or not any(coords):
            return  # the default master: every scalar is 0, nothing to blend.
        scalars = [_region_scalar(region, coords) for region in self._regions]
        self._scalars = {
            vsindex: [
                scalars[index] if index < len(scalars) else 0.0
                for index in indices
            ]
            for vsindex, indices in self._vsindex_regions.items()
        }

    def _units_per_em(self, entries) -> int:
        raw = _dict_get(entries, _OP_FONTMATRIX)
        if raw is not None:
            vals = _decode_reals(raw)
            if vals and vals[0]:
                return max(1, round(1.0 / vals[0]))
        return 1000

    def _read_local_subrs(self, data: bytes, entries, cff2: bool = False) -> list[bytes]:
        priv = _dict_ints(entries, _OP_PRIVATE)
        if not priv or len(priv) != 2:
            return []
        size, off = priv
        if off < 0 or off + size > len(data):
            return []
        priv_entries = _parse_dict(data[off : off + size])
        subrs_rel = _dict_int(priv_entries, _OP_SUBRS)
        if subrs_rel is None:
            return []
        reader = _read_index2 if cff2 else _read_index
        subrs, _ = reader(data, off + subrs_rel)
        return subrs

    def _parse_cid(self, data: bytes, entries) -> None:
        fdarray_off = _dict_int(entries, _OP_FDARRAY)
        fdselect_off = _dict_int(entries, _OP_FDSELECT)
        if fdarray_off is None or fdselect_off is None:
            self._fd_lsubrs = [[]]
            return
        fd_items, _ = _read_index(data, fdarray_off)
        self._fd_lsubrs = [
            self._read_local_subrs(data, _parse_dict(fd)) for fd in fd_items
        ] or [[]]
        self._fdselect = self._read_fdselect(data, fdselect_off)

    def _read_fdselect(self, data: bytes, off: int) -> list[int] | None:
        fmt = data[off]
        result = [0] * self.num_glyphs
        if fmt == 0:
            for gid in range(self.num_glyphs):
                if off + 1 + gid < len(data):
                    result[gid] = data[off + 1 + gid]
        elif fmt == 3:
            n_ranges = struct.unpack_from(">H", data, off + 1)[0]
            pos = off + 3
            ranges = []
            for _ in range(n_ranges):
                first = struct.unpack_from(">H", data, pos)[0]
                fd = data[pos + 2]
                ranges.append((first, fd))
                pos += 3
            sentinel = struct.unpack_from(">H", data, pos)[0]
            for idx, (first, fd) in enumerate(ranges):
                end = ranges[idx + 1][0] if idx + 1 < len(ranges) else sentinel
                for gid in range(first, min(end, self.num_glyphs)):
                    result[gid] = fd
        else:
            return None
        return result

    # -- public outline access --------------------------------------------

    def outline(self, gid: int) -> list[Contour]:
        """Return flattened, closed contours for *gid* in font units (y up)."""
        if not self._ok or gid < 0 or gid >= self.num_glyphs:
            return []
        cached = self._cache.get(gid)
        if cached is not None:
            return cached
        try:
            interp = _T2Glyph(
                self._gsubrs,
                self._local_subrs(gid),
                is_cff2=self._is_cff2,
                region_counts=self._region_counts,
                scalars=self._scalars,
            )
            contours = interp.run(self._charstrings[gid])
        except (struct.error, IndexError, ValueError):
            contours = []
        self._cache[gid] = contours
        return contours

    def advance_width(self, gid: int) -> int | None:
        """CFF advance widths are not surfaced (PDF ``/Widths`` is authoritative)."""
        return None

    def _local_subrs(self, gid: int) -> list[bytes]:
        if self._is_cid and self._fdselect is not None and gid < len(self._fdselect):
            fd = self._fdselect[gid]
            if 0 <= fd < len(self._fd_lsubrs):
                return self._fd_lsubrs[fd]
            return []
        return self._fd_lsubrs[0] if self._fd_lsubrs else []

    def encoding_code_to_gid(self) -> dict[int, int]:
        """Return the CFF's built-in custom Encoding as ``code -> gid``, or ``{}``.

        Predefined Standard/Expert encodings (offset 0/1, or absent) return
        ``{}`` -- resolving those needs the CFF standard-strings and standard
        encoding name tables, so the caller falls back to glyph boxes.
        """
        off = self._encoding_off
        if not self._ok or off is None or off <= 1 or off >= len(self._data):
            return {}
        data = self._data
        fmt = data[off]
        base = fmt & 0x7F
        mapping: dict[int, int] = {}
        try:
            if base == 0:
                n = data[off + 1]
                for gid_minus_1 in range(n):
                    mapping[data[off + 2 + gid_minus_1]] = gid_minus_1 + 1
            elif base == 1:
                n_ranges = data[off + 1]
                pos = off + 2
                gid = 1
                for _ in range(n_ranges):
                    first, nleft = data[pos], data[pos + 1]
                    pos += 2
                    for code in range(first, first + nleft + 1):
                        mapping[code] = gid
                        gid += 1
        except IndexError:
            return {}
        return mapping

    def name_to_gid(self) -> dict[str, int]:
        """Return ``{glyph name: gid}`` from the CFF charset, or ``{}``.

        Resolves the charset (formats 0/1/2, or the ISOAdobe identity charset for
        offset 0) to per-glyph SIDs, then each SID to a name via the predefined
        CFF strings and the font's own String INDEX. CID-keyed fonts and the
        Expert/ExpertSubset predefined charsets have no glyph names here and
        return ``{}``.
        """
        if self._name_to_gid is not None:
            return self._name_to_gid
        result: dict[str, int] = {}
        sids = self._charset_sids()
        if sids is not None:
            for gid, sid in enumerate(sids):
                name = self._sid_name(sid)
                if name is not None:
                    result.setdefault(name, gid)
        self._name_to_gid = result
        return result

    def _charset_sids(self) -> list[int] | None:
        """Return ``gid -> SID`` for a name-keyed font, or ``None``."""
        if not self._ok or self._is_cid or self.num_glyphs == 0:
            return None
        off = self._charset_off
        n = self.num_glyphs
        sids = [0] * n  # gid 0 is always .notdef (SID 0)
        if off is None or off == 0:  # ISOAdobe / absent: gid i -> SID i
            for gid in range(1, n):
                sids[gid] = gid
            return sids
        if off in (1, 2):
            return None  # Expert / ExpertSubset predefined charsets unsupported
        data = self._data
        if off >= len(data):
            return None
        try:
            fmt = data[off]
            pos = off + 1
            gid = 1
            if fmt == 0:
                while gid < n:
                    sids[gid] = struct.unpack_from(">H", data, pos)[0]
                    pos += 2
                    gid += 1
            elif fmt in (1, 2):
                while gid < n:
                    first = struct.unpack_from(">H", data, pos)[0]
                    pos += 2
                    if fmt == 1:
                        n_left = data[pos]
                        pos += 1
                    else:
                        n_left = struct.unpack_from(">H", data, pos)[0]
                        pos += 2
                    for offset in range(n_left + 1):
                        if gid >= n:
                            break
                        sids[gid] = first + offset
                        gid += 1
            else:
                return None
        except (struct.error, IndexError):
            return None
        return sids

    def _sid_name(self, sid: int) -> str | None:
        standard = cff_standard_strings()
        if 0 <= sid < len(standard):
            return standard[sid]
        index = sid - len(standard)
        if 0 <= index < len(self._strings):
            try:
                return self._strings[index].decode("latin-1")
            except (UnicodeDecodeError, AttributeError):
                return None
        return None


class _T2Glyph:
    """Type 2 charstring interpreter producing flattened, filled contours."""

    def __init__(
        self,
        gsubrs: list[bytes],
        lsubrs: list[bytes],
        *,
        is_cff2: bool = False,
        region_counts: dict[int, int] | None = None,
        scalars: dict[int, list[float]] | None = None,
    ):
        self._gsubrs = gsubrs
        self._lsubrs = lsubrs
        self._gbias = _subr_bias(len(gsubrs))
        self._lbias = _subr_bias(len(lsubrs))
        self.stack: list[float] = []
        self.x = 0.0
        self.y = 0.0
        self.contours: list[Contour] = []
        self._current: Contour | None = None
        self._nstems = 0
        self._is_cff2 = is_cff2
        self._region_counts = region_counts or {}
        self._scalars = scalars or {}
        self._vsindex = 0
        # CFF2 charstrings carry no leading width, so none is ever consumed.
        self._have_width = is_cff2
        self._done = False

    def run(self, charstring: bytes) -> list[Contour]:
        self._exec(charstring, 0)
        self._close()
        return self.contours

    def _exec(self, cs: bytes, depth: int) -> None:
        if depth > _MAX_SUBR_DEPTH:
            return
        i = 0
        n = len(cs)
        while i < n and not self._done:
            b0 = cs[i]
            if b0 >= 32 or b0 == 28:
                val, i = _read_operand(cs, i, b0)
                self.stack.append(val)
                continue
            i += 1
            if b0 in (1, 3, 18, 23):  # h/v stem(hm)
                self._stems()
            elif b0 in (19, 20):  # hintmask / cntrmask
                self._stems()
                i += (self._nstems + 7) // 8
            elif b0 == 21:  # rmoveto
                self._take_width(2)
                self._moveto(self._a(0), self._a(1))
            elif b0 == 22:  # hmoveto
                self._take_width(1)
                self._moveto(self._a(0), 0.0)
            elif b0 == 4:  # vmoveto
                self._take_width(1)
                self._moveto(0.0, self._a(0))
            elif b0 == 5:  # rlineto
                self._rlineto()
            elif b0 == 6:  # hlineto
                self._hvlineto(True)
            elif b0 == 7:  # vlineto
                self._hvlineto(False)
            elif b0 == 8:  # rrcurveto
                self._rrcurveto()
            elif b0 == 24:  # rcurveline
                self._rcurveline()
            elif b0 == 25:  # rlinecurve
                self._rlinecurve()
            elif b0 == 26:  # vvcurveto
                self._vvcurveto()
            elif b0 == 27:  # hhcurveto
                self._hhcurveto()
            elif b0 == 30:  # vhcurveto
                self._alt_curveto(False)
            elif b0 == 31:  # hvcurveto
                self._alt_curveto(True)
            elif b0 == 10:  # callsubr
                if self.stack:
                    idx = int(self.stack.pop()) + self._lbias
                    if 0 <= idx < len(self._lsubrs):
                        self._exec(self._lsubrs[idx], depth + 1)
            elif b0 == 29:  # callgsubr
                if self.stack:
                    idx = int(self.stack.pop()) + self._gbias
                    if 0 <= idx < len(self._gsubrs):
                        self._exec(self._gsubrs[idx], depth + 1)
            elif b0 == 11:  # return
                return
            elif b0 == 14:  # endchar
                self._have_width = True
                self._done = True
                return
            elif b0 == 15:  # vsindex (CFF2)
                if self._is_cff2 and self.stack:
                    self._vsindex = int(self.stack.pop())
                else:
                    self.stack.clear()
            elif b0 == 16:  # blend (CFF2)
                if self._is_cff2:
                    self._blend()
                else:
                    self.stack.clear()
            elif b0 == 12:  # escape
                if i < n:
                    self._escape(cs[i])
                    i += 1
            else:
                self.stack.clear()  # reserved operator: drop operands defensively

    # -- operand / width helpers -----------------------------------------

    def _a(self, index: int) -> float:
        return self.stack[index] if index < len(self.stack) else 0.0

    def _take_width(self, expected: int) -> None:
        if not self._have_width and len(self.stack) > expected:
            self.stack.pop(0)
        self._have_width = True

    def _blend(self) -> None:
        """Resolve a CFF2 ``blend`` for the selected instance.

        Stack layout is ``base(n), deltas(n*k), n``. The deltas are laid out
        value-major -- all *k* regions for value 0, then for value 1 -- so each
        default value takes its own slice. With no instance selected the
        deltas are simply dropped, which leaves the default master; with one,
        each value gains the region deltas weighted by that region's scalar.
        """
        if not self.stack:
            return
        n = int(self.stack.pop())
        if n <= 0:
            return
        regions = self._region_counts.get(self._vsindex, 0)
        drop = n * regions
        if drop <= 0 or drop > len(self.stack) - n:
            return
        scalars = self._scalars.get(self._vsindex)
        base = len(self.stack) - drop
        if scalars and len(scalars) == regions:
            values_at = base - n
            if values_at >= 0:
                for value in range(n):
                    delta = 0.0
                    start = base + value * regions
                    for index, scalar in enumerate(scalars):
                        if scalar:
                            delta += scalar * self.stack[start + index]
                    self.stack[values_at + value] += delta
        del self.stack[base:]

    def _stems(self) -> None:
        if not self._have_width and len(self.stack) % 2 == 1:
            self.stack.pop(0)
        self._have_width = True
        self._nstems += len(self.stack) // 2
        self.stack.clear()

    # -- path construction ------------------------------------------------

    def _moveto(self, dx: float, dy: float) -> None:
        self._close()
        self.x += dx
        self.y += dy
        self._current = [(self.x, self.y)]
        self.stack.clear()

    def _line(self, dx: float, dy: float) -> None:
        self.x += dx
        self.y += dy
        if self._current is not None:
            self._current.append((self.x, self.y))

    def _curve(
        self, dx1: float, dy1: float, dx2: float, dy2: float, dx3: float, dy3: float
    ) -> None:
        if self._current is None:
            self._current = [(self.x, self.y)]
        x0, y0 = self.x, self.y
        x1, y1 = x0 + dx1, y0 + dy1
        x2, y2 = x1 + dx2, y1 + dy2
        x3, y3 = x2 + dx3, y2 + dy3
        for step in range(1, _CURVE_STEPS + 1):
            t = step / _CURVE_STEPS
            mt = 1.0 - t
            a, b, c, d = mt * mt * mt, 3 * mt * mt * t, 3 * mt * t * t, t * t * t
            self._current.append(
                (a * x0 + b * x1 + c * x2 + d * x3, a * y0 + b * y1 + c * y2 + d * y3)
            )
        self.x, self.y = x3, y3

    def _close(self) -> None:
        if self._current and len(self._current) >= 3:
            self.contours.append(self._current)
        self._current = None

    # -- line operators ---------------------------------------------------

    def _rlineto(self) -> None:
        args = self.stack
        i = 0
        while i + 2 <= len(args):
            self._line(args[i], args[i + 1])
            i += 2
        self.stack.clear()

    def _hvlineto(self, horizontal: bool) -> None:
        for arg in self.stack:
            self._line(arg, 0.0) if horizontal else self._line(0.0, arg)
            horizontal = not horizontal
        self.stack.clear()

    # -- curve operators --------------------------------------------------

    def _rrcurveto(self) -> None:
        args = self.stack
        i = 0
        while i + 6 <= len(args):
            self._curve(*args[i : i + 6])
            i += 6
        self.stack.clear()

    def _rcurveline(self) -> None:
        args = self.stack
        i = 0
        while i + 6 <= len(args) - 2:
            self._curve(*args[i : i + 6])
            i += 6
        if i + 2 <= len(args):
            self._line(args[i], args[i + 1])
        self.stack.clear()

    def _rlinecurve(self) -> None:
        args = self.stack
        i = 0
        while i + 2 <= len(args) - 6:
            self._line(args[i], args[i + 1])
            i += 2
        if i + 6 <= len(args):
            self._curve(*args[i : i + 6])
        self.stack.clear()

    def _vvcurveto(self) -> None:
        args = list(self.stack)
        dx1 = 0.0
        if len(args) % 4 == 1:
            dx1 = args[0]
            args = args[1:]
        i = 0
        while i + 4 <= len(args):
            dya, dxb, dyb, dyc = args[i : i + 4]
            self._curve(dx1, dya, dxb, dyb, 0.0, dyc)
            dx1 = 0.0
            i += 4
        self.stack.clear()

    def _hhcurveto(self) -> None:
        args = list(self.stack)
        dy1 = 0.0
        if len(args) % 4 == 1:
            dy1 = args[0]
            args = args[1:]
        i = 0
        while i + 4 <= len(args):
            dxa, dxb, dyb, dxc = args[i : i + 4]
            self._curve(dxa, dy1, dxb, dyb, dxc, 0.0)
            dy1 = 0.0
            i += 4
        self.stack.clear()

    def _alt_curveto(self, horizontal: bool) -> None:
        args = self.stack
        n = len(args)
        i = 0
        while i + 4 <= n:
            last = (n - i) == 5
            if horizontal:
                dxc = args[i + 4] if last else 0.0
                self._curve(args[i], 0.0, args[i + 1], args[i + 2], dxc, args[i + 3])
            else:
                dyc = args[i + 4] if last else 0.0
                self._curve(0.0, args[i], args[i + 1], args[i + 2], args[i + 3], dyc)
            horizontal = not horizontal
            i += 4
        self.stack.clear()

    def _escape(self, b1: int) -> None:
        args = self.stack
        if b1 == 35 and len(args) >= 12:  # flex
            self._curve(*args[0:6])
            self._curve(*args[6:12])
        elif b1 == 34 and len(args) >= 7:  # hflex
            dx1, dx2, dy2, dx3, dx4, dx5, dx6 = args[0:7]
            self._curve(dx1, 0.0, dx2, dy2, dx3, 0.0)
            self._curve(dx4, 0.0, dx5, -dy2, dx6, 0.0)
        elif b1 == 36 and len(args) >= 9:  # hflex1
            dx1, dy1, dx2, dy2, dx3, dx4, dx5, dy5, dx6 = args[0:9]
            self._curve(dx1, dy1, dx2, dy2, dx3, 0.0)
            self._curve(dx4, 0.0, dx5, dy5, dx6, -(dy1 + dy2 + dy5))
        elif b1 == 37 and len(args) >= 11:  # flex1
            dx1, dy1, dx2, dy2, dx3, dy3, dx4, dy4, dx5, dy5, d6 = args[0:11]
            dx = dx1 + dx2 + dx3 + dx4 + dx5
            dy = dy1 + dy2 + dy3 + dy4 + dy5
            self._curve(dx1, dy1, dx2, dy2, dx3, dy3)
            if abs(dx) > abs(dy):
                self._curve(dx4, dy4, dx5, dy5, d6, -dy)
            else:
                self._curve(dx4, dy4, dx5, dy5, -dx, d6)
        self.stack.clear()
