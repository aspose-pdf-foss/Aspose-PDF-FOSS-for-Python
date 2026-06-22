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
from typing import Dict, List, Optional, Tuple

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

Point = Tuple[float, float]
Contour = List[Point]

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


def _read_operand(data: bytes, i: int, b0: int) -> Tuple[float, int]:
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


def _decode_reals(operand_bytes: bytes) -> List[float]:
    """Decode DICT operands as floats, including CFF real (operator 30) numbers."""
    vals: List[float] = []
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


def _maybe_extract_cff(data: bytes) -> bytes:
    """Return the ``CFF `` table when *data* is an SFNT/OpenType wrapper."""
    if len(data) >= 12 and data[:4] in (b"OTTO", b"\x00\x01\x00\x00", b"true"):
        try:
            num_tables = struct.unpack_from(">H", data, 4)[0]
            record = 12
            for _ in range(num_tables):
                if record + 16 > len(data):
                    break
                if data[record : record + 4] == b"CFF ":
                    off, length = struct.unpack_from(">II", data, record + 8)
                    return data[off : off + length]
                record += 16
        except struct.error:
            pass
    return data


class CffOutlines:
    """Decode glyph outlines from a CFF (Type 2 charstring) font program."""

    def __init__(self, font_bytes: bytes):
        self.units_per_em = 1000
        self.num_glyphs = 0
        self._charstrings: List[bytes] = []
        self._gsubrs: List[bytes] = []
        self._fd_lsubrs: List[List[bytes]] = []
        self._fdselect: Optional[List[int]] = None
        self._is_cid = False
        self._encoding_off: Optional[int] = None
        self._data = b""
        self._cache: Dict[int, List[Contour]] = {}
        self._ok = False
        try:
            self._parse(_maybe_extract_cff(bytes(font_bytes)))
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
        major, _minor, hdr_size, _off_size = data[0:4]
        if major != 1 or hdr_size < 4:
            return  # CFF2 (major 2) and odd headers are out of scope.

        _name_index, pos = _read_index(data, hdr_size)
        topdict_index, pos = _read_index(data, pos)
        _string_index, pos = _read_index(data, pos)
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
        self._is_cid = _dict_get(entries, _OP_ROS) is not None
        if self._is_cid:
            self._parse_cid(data, entries)
        else:
            self._fd_lsubrs = [self._read_local_subrs(data, entries)]
        self._ok = True

    def _units_per_em(self, entries) -> int:
        raw = _dict_get(entries, _OP_FONTMATRIX)
        if raw is not None:
            vals = _decode_reals(raw)
            if vals and vals[0]:
                return max(1, int(round(1.0 / vals[0])))
        return 1000

    def _read_local_subrs(self, data: bytes, entries) -> List[bytes]:
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
        subrs, _ = _read_index(data, off + subrs_rel)
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

    def _read_fdselect(self, data: bytes, off: int) -> Optional[List[int]]:
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

    def outline(self, gid: int) -> List[Contour]:
        """Return flattened, closed contours for *gid* in font units (y up)."""
        if not self._ok or gid < 0 or gid >= self.num_glyphs:
            return []
        cached = self._cache.get(gid)
        if cached is not None:
            return cached
        try:
            interp = _T2Glyph(self._gsubrs, self._local_subrs(gid))
            contours = interp.run(self._charstrings[gid])
        except (struct.error, IndexError, ValueError):
            contours = []
        self._cache[gid] = contours
        return contours

    def advance_width(self, gid: int) -> Optional[int]:
        """CFF advance widths are not surfaced (PDF ``/Widths`` is authoritative)."""
        return None

    def _local_subrs(self, gid: int) -> List[bytes]:
        if self._is_cid and self._fdselect is not None and gid < len(self._fdselect):
            fd = self._fdselect[gid]
            if 0 <= fd < len(self._fd_lsubrs):
                return self._fd_lsubrs[fd]
            return []
        return self._fd_lsubrs[0] if self._fd_lsubrs else []

    def encoding_code_to_gid(self) -> Dict[int, int]:
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
        mapping: Dict[int, int] = {}
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


class _T2Glyph:
    """Type 2 charstring interpreter producing flattened, filled contours."""

    def __init__(self, gsubrs: List[bytes], lsubrs: List[bytes]):
        self._gsubrs = gsubrs
        self._lsubrs = lsubrs
        self._gbias = _subr_bias(len(gsubrs))
        self._lbias = _subr_bias(len(lsubrs))
        self.stack: List[float] = []
        self.x = 0.0
        self.y = 0.0
        self.contours: List[Contour] = []
        self._current: Optional[Contour] = None
        self._nstems = 0
        self._have_width = False
        self._done = False

    def run(self, charstring: bytes) -> List[Contour]:
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
