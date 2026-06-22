"""Dependency-free TrueType ``glyf`` outline extraction for rasterization.

The font subsetter (:mod:`.font_subset`) copies glyph *bytes* without decoding
them; this module decodes a glyph description into filled contours so the page
renderer can draw real text instead of placeholder boxes.  Only TrueType
``glyf``/``loca`` outlines are handled -- OpenType CFF (``/FontFile3``) and
Type 1 (``/FontFile``) programs use other outline formats and are left to later
engine layers (the renderer falls back to glyph boxes for those).

Outlines are returned as already-flattened, closed polygons in font units with
the y-axis pointing up (TrueType's native orientation).  Quadratic B-splines
are flattened with a fixed number of segments -- enough for legible text at the
sizes a best-effort rasterizer targets.

Parsing is deliberately defensive: malformed or unexpected input yields an
empty outline (or an inert source) rather than raising.
"""

from __future__ import annotations

import struct
from typing import Dict, List, Optional, Tuple

__all__ = ["TrueTypeOutlines"]

Point = Tuple[float, float]
Contour = List[Point]

# Simple-glyph point flags (OpenType ``glyf`` table).
_ON_CURVE = 0x01
_X_SHORT = 0x02
_Y_SHORT = 0x04
_REPEAT = 0x08
_X_SAME_OR_POS = 0x10
_Y_SAME_OR_POS = 0x20

# Composite-glyph component flags.
_ARG_1_AND_2_ARE_WORDS = 0x0001
_ARGS_ARE_XY_VALUES = 0x0002
_WE_HAVE_A_SCALE = 0x0008
_MORE_COMPONENTS = 0x0020
_WE_HAVE_AN_X_AND_Y_SCALE = 0x0040
_WE_HAVE_A_TWO_BY_TWO = 0x0080

_F2DOT14 = 1.0 / 16384.0

# Segments emitted per off-curve (quadratic) span when flattening.
_CURVE_STEPS = 8

# Guard against pathological / cyclic composite glyphs.
_MAX_COMPONENT_DEPTH = 6


class TrueTypeOutlines:
    """Decode glyph outlines from an embedded TrueType (``glyf``) program."""

    def __init__(self, font_bytes: bytes):
        self._data = bytes(font_bytes)
        self.units_per_em = 1000
        self.num_glyphs = 0
        self._loca: Optional[List[int]] = None
        self._glyf_off = 0
        self._glyf_len = 0
        self._num_h_metrics = 0
        self._hmtx_off = 0
        self._hmtx_len = 0
        self._cache: Dict[int, List[Contour]] = {}
        self._ok = False
        try:
            self._parse_header()
        except (struct.error, IndexError, ValueError):
            self._ok = False

    @property
    def ok(self) -> bool:
        """``True`` when a TrueType ``glyf`` program was parsed successfully."""
        return self._ok

    # -- header -----------------------------------------------------------

    def _parse_header(self) -> None:
        data = self._data
        if len(data) < 12:
            return
        sfnt_version, num_tables = struct.unpack_from(">IH", data, 0)
        if sfnt_version == 0x4F54544F:  # 'OTTO' -> CFF outlines, no glyf.
            return

        tables: Dict[str, Tuple[int, int]] = {}
        record = 12
        for _ in range(num_tables):
            if record + 16 > len(data):
                return
            tag = data[record : record + 4].decode("latin-1")
            offset, length = struct.unpack_from(">II", data, record + 8)
            tables[tag] = (offset, length)
            record += 16

        if not {"glyf", "loca", "head", "maxp"} <= tables.keys():
            return

        head_off, _ = tables["head"]
        maxp_off, _ = tables["maxp"]
        if head_off + 54 > len(data) or maxp_off + 6 > len(data):
            return

        self.units_per_em = struct.unpack_from(">H", data, head_off + 18)[0] or 1000
        index_to_loc = struct.unpack_from(">h", data, head_off + 50)[0]
        self.num_glyphs = struct.unpack_from(">H", data, maxp_off + 4)[0]
        if self.num_glyphs == 0:
            return

        self._loca = self._read_loca(tables["loca"], index_to_loc)
        if self._loca is None:
            return
        self._glyf_off, self._glyf_len = tables["glyf"]

        if "hhea" in tables and "hmtx" in tables:
            hhea_off = tables["hhea"][0]
            if hhea_off + 36 <= len(data):
                self._num_h_metrics = struct.unpack_from(">H", data, hhea_off + 34)[0]
                self._hmtx_off, self._hmtx_len = tables["hmtx"]

        self._ok = True

    def _read_loca(
        self, location: Tuple[int, int], index_to_loc: int
    ) -> Optional[List[int]]:
        data = self._data
        off, _ = location
        count = self.num_glyphs + 1
        offsets: List[int] = []
        if index_to_loc == 0:  # short format: uint16 entries scaled by 2.
            if off + count * 2 > len(data):
                return None
            for i in range(count):
                offsets.append(struct.unpack_from(">H", data, off + i * 2)[0] * 2)
        else:  # long format: uint32 byte offsets.
            if off + count * 4 > len(data):
                return None
            for i in range(count):
                offsets.append(struct.unpack_from(">I", data, off + i * 4)[0])
        return offsets

    # -- public outline / metrics access ----------------------------------

    def outline(self, gid: int) -> List[Contour]:
        """Return flattened, closed contours for *gid* in font units (y up).

        An empty list is returned for blank glyphs (e.g. the space) and for any
        glyph that cannot be decoded.  The result is cached and must be treated
        as read-only by callers.
        """
        if not self._ok or self._loca is None or gid < 0 or gid >= self.num_glyphs:
            return []
        cached = self._cache.get(gid)
        if cached is not None:
            return cached
        try:
            contours = self._decode_glyph(gid, 0)
        except (struct.error, IndexError, ValueError):
            contours = []
        self._cache[gid] = contours
        return contours

    def advance_width(self, gid: int) -> Optional[int]:
        """Return *gid*'s advance width in font units from ``hmtx``, or ``None``.

        This is only a fallback: a PDF font's ``/Widths`` (simple) or ``/W``
        (CID) array is authoritative for text positioning when present.
        """
        if not self._ok or self._num_h_metrics == 0:
            return None
        index = gid if gid < self._num_h_metrics else self._num_h_metrics - 1
        off = self._hmtx_off + index * 4
        if off + 2 > self._hmtx_off + self._hmtx_len or off + 2 > len(self._data):
            return None
        return struct.unpack_from(">H", self._data, off)[0]

    # -- glyph decoding ---------------------------------------------------

    def _decode_glyph(self, gid: int, depth: int) -> List[Contour]:
        if depth > _MAX_COMPONENT_DEPTH or self._loca is None:
            return []
        start, end = self._loca[gid], self._loca[gid + 1]
        if end <= start:
            return []  # Empty glyph (e.g. the space character).
        base = self._glyf_off + start
        limit = self._glyf_off + end
        if base + 10 > len(self._data) or end - start < 10:
            return []
        num_contours = struct.unpack_from(">h", self._data, base)[0]
        if num_contours < 0:
            return self._decode_composite(base + 10, limit, depth)
        return self._decode_simple(base, num_contours)

    def _decode_simple(self, base: int, num_contours: int) -> List[Contour]:
        data = self._data
        pos = base + 10
        end_pts: List[int] = []
        for _ in range(num_contours):
            end_pts.append(struct.unpack_from(">H", data, pos)[0])
            pos += 2
        if not end_pts:
            return []
        num_points = end_pts[-1] + 1
        if num_points <= 0:
            return []

        instr_len = struct.unpack_from(">H", data, pos)[0]
        pos += 2 + instr_len

        flags: List[int] = []
        while len(flags) < num_points:
            flag = data[pos]
            pos += 1
            flags.append(flag)
            if flag & _REPEAT:
                repeat = data[pos]
                pos += 1
                flags.extend([flag] * repeat)
        flags = flags[:num_points]

        xs: List[int] = []
        x = 0
        for flag in flags:
            if flag & _X_SHORT:
                dx = data[pos]
                pos += 1
                x += dx if flag & _X_SAME_OR_POS else -dx
            elif not flag & _X_SAME_OR_POS:
                x += struct.unpack_from(">h", data, pos)[0]
                pos += 2
            xs.append(x)

        ys: List[int] = []
        y = 0
        for flag in flags:
            if flag & _Y_SHORT:
                dy = data[pos]
                pos += 1
                y += dy if flag & _Y_SAME_OR_POS else -dy
            elif not flag & _Y_SAME_OR_POS:
                y += struct.unpack_from(">h", data, pos)[0]
                pos += 2
            ys.append(y)

        contours: List[Contour] = []
        start_idx = 0
        for end_idx in end_pts:
            raw = [
                (float(xs[i]), float(ys[i]), bool(flags[i] & _ON_CURVE))
                for i in range(start_idx, end_idx + 1)
            ]
            start_idx = end_idx + 1
            flat = _flatten_contour(raw)
            if len(flat) >= 3:
                contours.append(flat)
        return contours

    def _decode_composite(self, pos: int, end: int, depth: int) -> List[Contour]:
        data = self._data
        contours: List[Contour] = []
        while pos + 4 <= end:
            flags, comp_gid = struct.unpack_from(">HH", data, pos)
            pos += 4
            if flags & _ARG_1_AND_2_ARE_WORDS:
                arg1, arg2 = struct.unpack_from(">hh", data, pos)
                pos += 4
            else:
                arg1, arg2 = struct.unpack_from(">bb", data, pos)
                pos += 2

            a = d = 1.0
            b = c = 0.0
            if flags & _WE_HAVE_A_SCALE:
                a = d = struct.unpack_from(">h", data, pos)[0] * _F2DOT14
                pos += 2
            elif flags & _WE_HAVE_AN_X_AND_Y_SCALE:
                a = struct.unpack_from(">h", data, pos)[0] * _F2DOT14
                d = struct.unpack_from(">h", data, pos + 2)[0] * _F2DOT14
                pos += 4
            elif flags & _WE_HAVE_A_TWO_BY_TWO:
                a = struct.unpack_from(">h", data, pos)[0] * _F2DOT14
                b = struct.unpack_from(">h", data, pos + 2)[0] * _F2DOT14
                c = struct.unpack_from(">h", data, pos + 4)[0] * _F2DOT14
                d = struct.unpack_from(">h", data, pos + 6)[0] * _F2DOT14
                pos += 8

            # Point-matching (flag bit clear) is rare; treat as no offset.
            dx = float(arg1) if flags & _ARGS_ARE_XY_VALUES else 0.0
            dy = float(arg2) if flags & _ARGS_ARE_XY_VALUES else 0.0

            for contour in self._decode_glyph(comp_gid, depth + 1):
                contours.append(
                    [
                        (a * px + c * py + dx, b * px + d * py + dy)
                        for px, py in contour
                    ]
                )

            if not flags & _MORE_COMPONENTS:
                break
        return contours


def _flatten_contour(points: List[Tuple[float, float, bool]]) -> Contour:
    """Flatten a TrueType point sequence into a closed on-curve polygon.

    Consecutive off-curve control points imply an on-curve midpoint, and a
    contour that starts off-curve is normalised so it begins on-curve.  Each
    quadratic span ``on -> off -> on`` is subdivided into ``_CURVE_STEPS``
    straight segments.
    """
    if not points:
        return []

    normalised = _normalise_points(points)
    if not normalised:
        return []

    # ``normalised`` starts on-curve; append the first point to close the loop.
    pts = normalised + [normalised[0]]
    out: Contour = [(pts[0][0], pts[0][1])]
    i = 0
    while i < len(pts) - 1:
        cur = pts[i]
        nxt = pts[i + 1]
        if nxt[2]:  # On-curve: straight segment.
            out.append((nxt[0], nxt[1]))
            i += 1
            continue
        # ``nxt`` is a control point; the following point is on-curve by
        # construction (midpoints were inserted between off-curve pairs).
        ctrl = nxt
        end = pts[i + 2]
        for step in range(1, _CURVE_STEPS + 1):
            t = step / _CURVE_STEPS
            mt = 1.0 - t
            qx = mt * mt * cur[0] + 2.0 * mt * t * ctrl[0] + t * t * end[0]
            qy = mt * mt * cur[1] + 2.0 * mt * t * ctrl[1] + t * t * end[1]
            out.append((qx, qy))
        i += 2
    return out


def _normalise_points(
    points: List[Tuple[float, float, bool]],
) -> List[Tuple[float, float, bool]]:
    """Rotate to start on-curve and insert implied on-curve midpoints."""
    n = len(points)
    start = next((i for i in range(n) if points[i][2]), None)
    if start is None:
        # No on-curve points at all: synthesise a start at the midpoint of the
        # wrap-around pair so the whole contour is describable as quadratics.
        mid = (
            (points[0][0] + points[-1][0]) / 2.0,
            (points[0][1] + points[-1][1]) / 2.0,
            True,
        )
        rotated = [mid] + list(points)
    else:
        rotated = [points[(start + k) % n] for k in range(n)]

    result: List[Tuple[float, float, bool]] = []
    m = len(rotated)
    for k in range(m):
        cur = rotated[k]
        result.append(cur)
        nxt = rotated[(k + 1) % m]
        if not cur[2] and not nxt[2]:
            result.append(
                ((cur[0] + nxt[0]) / 2.0, (cur[1] + nxt[1]) / 2.0, True)
            )
    return result
