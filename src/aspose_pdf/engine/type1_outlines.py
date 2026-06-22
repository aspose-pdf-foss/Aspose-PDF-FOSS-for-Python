"""Dependency-free Type 1 (``/FontFile``) glyph outline extraction.

Type 1 font programs store their glyphs as *encrypted* Type 1 charstrings: the
program has a cleartext PostScript header, an ``eexec``-encrypted binary section
(the Private dict with ``/Subrs`` and ``/CharStrings``), and a zero-padded
trailer.  Each charstring is additionally charstring-encrypted.  This module
decrypts both layers and interprets the Type 1 charstrings into filled contours
so the page renderer can draw real Type 1 text instead of placeholder boxes.

Unlike TrueType/CFF, Type 1 glyphs are keyed by **name**, not glyph id, so the
caller resolves a character code to a glyph name (through the PDF ``/Encoding``
or the font's own built-in encoding, both surfaced here) and looks the outline
up by the synthetic glyph id assigned in charstring order.

The flex and hint-replacement OtherSubrs (0-3) are emulated; ``seac`` accent
composition is skipped (best effort).  Parsing is defensive: malformed input
yields an inert source rather than raising.
"""

from __future__ import annotations

import re
import struct
from typing import Dict, List, Optional, Tuple

__all__ = ["Type1Outlines"]

Point = Tuple[float, float]
Contour = List[Point]

_CURVE_STEPS = 8
_MAX_SUBR_DEPTH = 30

_EEXEC_R = 55665
_CHARSTRING_R = 4330
_C1 = 52845
_C2 = 22719

_RD = re.compile(rb"(?:RD|-\|)[ ]")


def _decrypt(data: bytes, r: int, skip: int) -> bytes:
    """Decrypt *data* with the Type 1 eexec/charstring cipher, dropping *skip*."""
    c1, c2 = _C1, _C2
    out = bytearray(len(data))
    for i, cipher in enumerate(data):
        out[i] = cipher ^ (r >> 8)
        r = ((cipher + r) * c1 + c2) & 0xFFFF
    return bytes(out[skip:])


def _is_hex(data: bytes) -> bool:
    sample = data[:4]
    return len(sample) == 4 and all(
        b in b"0123456789abcdefABCDEF \t\r\n" for b in sample
    )


class Type1Outlines:
    """Decode glyph outlines from a Type 1 (``/FontFile``) font program."""

    def __init__(
        self,
        font_bytes: bytes,
        length1: Optional[int] = None,
        length2: Optional[int] = None,
    ):
        self.units_per_em = 1000
        self.num_glyphs = 0
        self.glyph_names: List[str] = []
        self.name_to_gid: Dict[str, int] = {}
        self.builtin_encoding: Dict[int, str] = {}
        self._charstrings: List[bytes] = []
        self._subrs: Dict[int, bytes] = {}
        self._cache: Dict[int, List[Contour]] = {}
        self._ok = False
        try:
            self._parse(bytes(font_bytes), length1, length2)
        except (struct.error, IndexError, ValueError):
            self._ok = False

    @property
    def ok(self) -> bool:
        """``True`` when a Type 1 program with charstrings was parsed."""
        return self._ok

    # -- parsing ----------------------------------------------------------

    def _parse(
        self, data: bytes, length1: Optional[int], length2: Optional[int]
    ) -> None:
        clear, encrypted = self._split(data, length1, length2)
        if not encrypted:
            return
        if _is_hex(encrypted):
            encrypted = bytes.fromhex(
                re.sub(rb"[^0-9a-fA-F]", b"", encrypted).decode("ascii")
            )
        private = _decrypt(encrypted, _EEXEC_R, 4)

        self._read_font_matrix(clear)
        self._read_builtin_encoding(clear)

        len_iv = 4
        m = re.search(rb"/lenIV\s+(\d+)", private)
        if m:
            len_iv = int(m.group(1))

        self._subrs = self._read_subrs(private, len_iv)
        charstrings = self._read_charstrings(private, len_iv)
        if not charstrings:
            return
        self.glyph_names = list(charstrings.keys())
        self.name_to_gid = {name: gid for gid, name in enumerate(self.glyph_names)}
        self._charstrings = [charstrings[name] for name in self.glyph_names]
        self.num_glyphs = len(self._charstrings)
        self._ok = True

    def _split(
        self, data: bytes, length1: Optional[int], length2: Optional[int]
    ) -> Tuple[bytes, bytes]:
        if length1 and length2 and length1 + length2 <= len(data):
            return data[:length1], data[length1 : length1 + length2]
        idx = data.find(b"eexec")
        if idx < 0:
            return data, b""
        pos = idx + 5
        while pos < len(data) and data[pos] in b" \t\r\n":
            pos += 1
        return data[:idx], data[pos:]

    def _read_font_matrix(self, clear: bytes) -> None:
        m = re.search(rb"/FontMatrix\s*\[\s*([0-9eE.+\-]+)", clear)
        if m:
            try:
                a = float(m.group(1))
                if a:
                    self.units_per_em = max(1, int(round(1.0 / a)))
            except ValueError:
                pass

    def _read_builtin_encoding(self, clear: bytes) -> None:
        if re.search(rb"/Encoding\s+StandardEncoding\s+def", clear):
            return  # Standard encoding names are resolved via the PDF /Encoding.
        for m in re.finditer(rb"dup\s+(\d+)\s*/([^ \t\r\n/]+)\s+put", clear):
            self.builtin_encoding[int(m.group(1))] = m.group(2).decode("latin-1")

    def _read_subrs(self, private: bytes, len_iv: int) -> Dict[int, bytes]:
        start = private.find(b"/Subrs")
        if start < 0:
            return {}
        stop = private.find(b"/CharStrings")
        end = stop if stop > start else len(private)
        subrs: Dict[int, bytes] = {}
        pattern = re.compile(rb"dup\s+(\d+)\s+(\d+)\s+(?:RD|-\|)[ ]")
        pos = start
        while True:
            m = pattern.search(private, pos, end)
            if not m:
                break
            index = int(m.group(1))
            length = int(m.group(2))
            blob_start = m.end()
            subrs[index] = _decrypt(
                private[blob_start : blob_start + length], _CHARSTRING_R, len_iv
            )
            pos = blob_start + length
        return subrs

    def _read_charstrings(self, private: bytes, len_iv: int) -> Dict[str, bytes]:
        start = private.find(b"/CharStrings")
        if start < 0:
            return {}
        charstrings: Dict[str, bytes] = {}
        pattern = re.compile(rb"/([^ \t\r\n/{}\[\]()]+)\s+(\d+)\s+(?:RD|-\|)[ ]")
        pos = start
        # Skip the "/CharStrings N dict dup begin" header to the first glyph.
        header = re.search(rb"begin", private[start : start + 200])
        if header:
            pos = start + header.end()
        while True:
            m = pattern.search(private, pos)
            if not m:
                break
            name = m.group(1).decode("latin-1")
            length = int(m.group(2))
            blob_start = m.end()
            charstrings[name] = _decrypt(
                private[blob_start : blob_start + length], _CHARSTRING_R, len_iv
            )
            pos = blob_start + length
        return charstrings

    # -- outline access ---------------------------------------------------

    def outline(self, gid: int) -> List[Contour]:
        """Return flattened, closed contours for *gid* in font units (y up)."""
        if not self._ok or gid < 0 or gid >= self.num_glyphs:
            return []
        cached = self._cache.get(gid)
        if cached is not None:
            return cached
        try:
            interp = _T1Glyph(self._subrs)
            contours = interp.run(self._charstrings[gid])
        except (struct.error, IndexError, ValueError):
            contours = []
        self._cache[gid] = contours
        return contours

    def advance_width(self, gid: int) -> Optional[int]:
        """Type 1 advance widths are not surfaced (PDF ``/Widths`` is used)."""
        return None


def _read_operand(data: bytes, i: int, b0: int) -> Tuple[float, int]:
    """Decode one Type 1 charstring numeric operand (255 is a 32-bit integer)."""
    if b0 < 247:  # 32..246
        return float(b0 - 139), i + 1
    if b0 < 251:  # 247..250
        return float((b0 - 247) * 256 + data[i + 1] + 108), i + 2
    if b0 < 255:  # 251..254
        return float(-(b0 - 251) * 256 - data[i + 1] - 108), i + 2
    return float(struct.unpack_from(">i", data, i + 1)[0]), i + 5


class _T1Glyph:
    """Type 1 charstring interpreter producing flattened, filled contours."""

    def __init__(self, subrs: Dict[int, bytes]):
        self._subrs = subrs
        self.stack: List[float] = []
        self.ps_stack: List[float] = []
        self.x = 0.0
        self.y = 0.0
        self.contours: List[Contour] = []
        self._current: Optional[Contour] = None
        self._done = False
        self._flex = False
        self._flex_pts: List[Point] = []
        self._flex_start: Point = (0.0, 0.0)

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
            if b0 >= 32:
                val, i = _read_operand(cs, i, b0)
                self.stack.append(val)
                continue
            i += 1
            if b0 == 13:  # hsbw: sbx wx
                self.x = self.stack[0] if self.stack else 0.0
                self.y = 0.0
                self.stack.clear()
            elif b0 == 21:  # rmoveto
                self._moveto(self._a(0), self._a(1))
            elif b0 == 22:  # hmoveto
                self._moveto(self._a(0), 0.0)
            elif b0 == 4:  # vmoveto
                self._moveto(0.0, self._a(0))
            elif b0 == 5:  # rlineto
                self._line(self._a(0), self._a(1))
                self.stack.clear()
            elif b0 == 6:  # hlineto
                self._line(self._a(0), 0.0)
                self.stack.clear()
            elif b0 == 7:  # vlineto
                self._line(0.0, self._a(0))
                self.stack.clear()
            elif b0 == 8:  # rrcurveto
                self._rel_curve(*self.stack[:6])
                self.stack.clear()
            elif b0 == 30:  # vhcurveto
                self._rel_curve(0.0, self._a(0), self._a(1), self._a(2), self._a(3), 0.0)
                self.stack.clear()
            elif b0 == 31:  # hvcurveto
                self._rel_curve(self._a(0), 0.0, self._a(1), self._a(2), 0.0, self._a(3))
                self.stack.clear()
            elif b0 == 9:  # closepath
                self.stack.clear()
            elif b0 in (1, 3):  # hstem / vstem
                self.stack.clear()
            elif b0 == 10:  # callsubr
                if self.stack:
                    idx = int(self.stack.pop())
                    sub = self._subrs.get(idx)
                    if sub is not None:
                        self._exec(sub, depth + 1)
            elif b0 == 11:  # return
                return
            elif b0 == 14:  # endchar
                self._done = True
                return
            elif b0 == 12:  # escape
                if i < n:
                    i = self._escape(cs, i)
            else:
                self.stack.clear()

    # -- helpers ----------------------------------------------------------

    def _a(self, index: int) -> float:
        return self.stack[index] if index < len(self.stack) else 0.0

    def _moveto(self, dx: float, dy: float) -> None:
        self.x += dx
        self.y += dy
        if self._flex:
            self._flex_pts.append((self.x, self.y))
        else:
            self._close()
            self._current = [(self.x, self.y)]
        self.stack.clear()

    def _line(self, dx: float, dy: float) -> None:
        self.x += dx
        self.y += dy
        if self._current is not None:
            self._current.append((self.x, self.y))

    def _rel_curve(
        self, dx1: float, dy1: float, dx2: float, dy2: float, dx3: float, dy3: float
    ) -> None:
        x1, y1 = self.x + dx1, self.y + dy1
        x2, y2 = x1 + dx2, y1 + dy2
        x3, y3 = x2 + dx3, y2 + dy3
        self._abs_curve((self.x, self.y), (x1, y1), (x2, y2), (x3, y3))
        self.x, self.y = x3, y3

    def _abs_curve(self, p0: Point, p1: Point, p2: Point, p3: Point) -> None:
        if self._current is None:
            self._current = [p0]
        for step in range(1, _CURVE_STEPS + 1):
            t = step / _CURVE_STEPS
            mt = 1.0 - t
            a, b, c, d = mt * mt * mt, 3 * mt * mt * t, 3 * mt * t * t, t * t * t
            self._current.append(
                (
                    a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
                    a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1],
                )
            )

    def _close(self) -> None:
        if self._current and len(self._current) >= 3:
            self.contours.append(self._current)
        self._current = None

    def _escape(self, cs: bytes, i: int) -> int:
        b1 = cs[i]
        i += 1
        if b1 == 12:  # div
            if len(self.stack) >= 2:
                b = self.stack.pop()
                a = self.stack.pop()
                self.stack.append(a / b if b else 0.0)
        elif b1 == 16:  # callothersubr
            self._callothersubr()
        elif b1 == 17:  # pop
            self.stack.append(self.ps_stack.pop() if self.ps_stack else 0.0)
        elif b1 == 6:  # seac (accent composition) -- not rendered
            self.stack.clear()
            self._done = True
        elif b1 == 7:  # sbw
            self.x = self._a(0)
            self.y = self._a(1)
            self.stack.clear()
        elif b1 == 33:  # setcurrentpoint
            if len(self.stack) >= 2:
                self.x, self.y = self.stack[-2], self.stack[-1]
            self.stack.clear()
        else:  # dotsection / vstem3 / hstem3 / reserved
            self.stack.clear()
        return i

    def _callothersubr(self) -> None:
        if len(self.stack) < 2:
            self.stack.clear()
            return
        othersubr = int(self.stack.pop())
        count = int(self.stack.pop())
        count = max(0, min(count, len(self.stack)))
        args = [self.stack.pop() for _ in range(count)]
        args.reverse()
        if othersubr == 1:  # start flex
            self._flex = True
            self._flex_pts = []
            self._flex_start = (self.x, self.y)
        elif othersubr == 2:  # flex point collected via the intervening rmoveto
            pass
        elif othersubr == 0:  # end flex
            self._flex = False
            self._emit_flex()
            if len(args) >= 3:
                self.x, self.y = args[1], args[2]
            self.ps_stack = [self.y, self.x]
        elif othersubr == 3:  # hint replacement
            self.ps_stack = [args[0] if args else 3.0]
        else:  # unknown: make args retrievable through subsequent pops
            self.ps_stack = list(reversed(args))

    def _emit_flex(self) -> None:
        pts = self._flex_pts
        if len(pts) < 7:
            return
        # pts[0] is the flex reference point; the outline is two cubics through
        # pts[1..3] and pts[3..6], starting from the pre-flex current point.
        self._abs_curve(self._flex_start, pts[1], pts[2], pts[3])
        self._abs_curve(pts[3], pts[4], pts[5], pts[6])
        self.x, self.y = pts[6]
