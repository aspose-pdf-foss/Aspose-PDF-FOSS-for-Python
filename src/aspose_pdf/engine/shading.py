"""Dependency-free axial/radial PDF shadings (gradients) for rasterization.

Implements the two common shading types -- **axial** (``ShadingType 2``, linear
gradients) and **radial** (``ShadingType 3``, between two circles) -- backed by
PDF function types 2 (exponential), 3 (stitching) and 0 (sampled).  A shading is
built into a 256-entry RGB colour lookup table once, so per-pixel evaluation is
a cheap projection plus a table lookup.

Used by the page renderer for the ``sh`` operator and for shading-pattern fills
(``PatternType 2``).  Mesh shadings (types 4-7), function-based shadings
(type 1) and PostScript-calculator functions (type 4) are not handled -- the
caller leaves such paints unpainted (best effort).
"""

from __future__ import annotations

import math
from typing import Any, List, Optional, Tuple

from .cos import PdfArray, PdfDictionary, PdfName, PdfNumber, PdfStream

__all__ = ["build_shading", "Shading"]

Color = Tuple[int, int, int]


# ---------------------------------------------------------------------------
# COS readers
# ---------------------------------------------------------------------------


def _num(pdf: Any, obj: Any) -> Optional[float]:
    obj = pdf._resolve(obj)
    if isinstance(obj, PdfNumber):
        return float(obj.value)
    if isinstance(obj, (int, float)):
        return float(obj)
    return None


def _num_array(pdf: Any, obj: Any) -> Optional[List[float]]:
    obj = pdf._resolve(obj)
    if not isinstance(obj, PdfArray):
        return None
    out: List[float] = []
    for item in obj.items:
        value = _num(pdf, item)
        out.append(value if value is not None else 0.0)
    return out


def _bool_array(pdf: Any, obj: Any) -> Optional[List[bool]]:
    obj = pdf._resolve(obj)
    if not isinstance(obj, PdfArray):
        return None
    return [bool(getattr(pdf._resolve(item), "value", False)) for item in obj.items]


def _byte(value: float) -> int:
    return 0 if value < 0 else 255 if value > 255 else int(value + 0.5)


def _components_to_rgb(comps: List[float]) -> Color:
    """Convert a colour by component count (gray/RGB/CMYK)."""
    if len(comps) == 1:
        v = _byte(comps[0] * 255)
        return (v, v, v)
    if len(comps) == 4:
        c, m, y, k = comps[:4]
        return (
            _byte(255 * (1 - c) * (1 - k)),
            _byte(255 * (1 - m) * (1 - k)),
            _byte(255 * (1 - y) * (1 - k)),
        )
    if len(comps) >= 3:
        return (_byte(comps[0] * 255), _byte(comps[1] * 255), _byte(comps[2] * 255))
    return (0, 0, 0)


def _color_converter(pdf: Any, cs_obj: Any):
    """Return a ``components -> Color`` converter for a shading colour space."""
    cs = pdf._resolve(cs_obj)
    name = None
    if isinstance(cs, PdfName):
        name = cs.name.lstrip("/")
    elif isinstance(cs, PdfArray) and cs.items:
        head = pdf._resolve(cs.items[0])
        head_name = head.name.lstrip("/") if isinstance(head, PdfName) else ""
        if head_name == "ICCBased" and len(cs.items) >= 2:
            stream = pdf._resolve(cs.items[1])
            n = _num(pdf, stream.mapping.get(PdfName("N"))) if isinstance(
                stream, PdfStream
            ) else None
            count = int(n or 3)
            return lambda comps, _c=count: _components_to_rgb(
                comps[:_c] if len(comps) >= _c else comps
            )
        name = head_name
    table = {
        "DeviceGray": 1,
        "CalGray": 1,
        "G": 1,
        "DeviceRGB": 3,
        "CalRGB": 3,
        "Lab": 3,
        "RGB": 3,
        "DeviceCMYK": 4,
        "CMYK": 4,
    }
    if name in table:
        return lambda comps: _components_to_rgb(comps)
    return lambda comps: _components_to_rgb(comps)


# ---------------------------------------------------------------------------
# PDF functions
# ---------------------------------------------------------------------------


def build_function(pdf: Any, obj: Any):
    """Build an evaluable function (types 0/2/3, or an array of them)."""
    obj = pdf._resolve(obj)
    if isinstance(obj, PdfArray):
        funcs = [build_function(pdf, item) for item in obj.items]
        funcs = [f for f in funcs if f is not None]
        return _ArrayFunction(funcs) if funcs else None
    if not isinstance(obj, (PdfDictionary, PdfStream)):
        return None
    mapping = obj.mapping
    ftype = _num(pdf, mapping.get(PdfName("FunctionType")))
    if ftype is None:
        return None
    domain = _num_array(pdf, mapping.get(PdfName("Domain"))) or [0.0, 1.0]
    ftype = int(ftype)
    if ftype == 2:
        c0 = _num_array(pdf, mapping.get(PdfName("C0"))) or [0.0]
        c1 = _num_array(pdf, mapping.get(PdfName("C1"))) or [1.0]
        n = _num(pdf, mapping.get(PdfName("N"))) or 1.0
        return _ExpFunction(domain, c0, c1, n)
    if ftype == 3:
        funcs_obj = pdf._resolve(mapping.get(PdfName("Functions")))
        if not isinstance(funcs_obj, PdfArray):
            return None
        funcs = [build_function(pdf, item) for item in funcs_obj.items]
        if any(f is None for f in funcs):
            return None
        bounds = _num_array(pdf, mapping.get(PdfName("Bounds"))) or []
        encode = _num_array(pdf, mapping.get(PdfName("Encode"))) or []
        return _StitchFunction(domain, funcs, bounds, encode)
    if ftype == 0 and isinstance(obj, PdfStream):
        sampled = _SampledFunction(pdf, obj, domain)
        return sampled if sampled.ok else None
    return None  # type 4 (PostScript calculator) is unsupported.


class _ExpFunction:
    def __init__(self, domain, c0, c1, n):
        self.domain = domain
        self.c0 = c0
        self.c1 = c1
        self.n = n

    def eval(self, t: float) -> List[float]:
        lo, hi = self.domain[0], self.domain[1]
        t = min(max(t, min(lo, hi)), max(lo, hi))
        tn = t**self.n if self.n != 1.0 else t
        return [a + tn * (b - a) for a, b in zip(self.c0, self.c1)]


class _StitchFunction:
    def __init__(self, domain, funcs, bounds, encode):
        self.domain = domain
        self.funcs = funcs
        self.bounds = bounds
        self.encode = encode

    def eval(self, t: float) -> List[float]:
        lo, hi = self.domain[0], self.domain[1]
        t = min(max(t, lo), hi)
        k = 0
        while k < len(self.bounds) and t >= self.bounds[k]:
            k += 1
        k = min(k, len(self.funcs) - 1)
        d_lo = self.bounds[k - 1] if k > 0 else lo
        d_hi = self.bounds[k] if k < len(self.bounds) else hi
        e_lo = self.encode[2 * k] if 2 * k < len(self.encode) else 0.0
        e_hi = self.encode[2 * k + 1] if 2 * k + 1 < len(self.encode) else 1.0
        if d_hi != d_lo:
            t = e_lo + (t - d_lo) * (e_hi - e_lo) / (d_hi - d_lo)
        else:
            t = e_lo
        return self.funcs[k].eval(t)


class _ArrayFunction:
    def __init__(self, funcs):
        self.funcs = funcs

    def eval(self, t: float) -> List[float]:
        out: List[float] = []
        for func in self.funcs:
            out.extend(func.eval(t))
        return out


class _SampledFunction:
    def __init__(self, pdf, stream, domain):
        mapping = stream.mapping
        self.domain = domain
        size = _num_array(pdf, mapping.get(PdfName("Size"))) or [2.0]
        self.size = max(2, int(size[0]))
        self.bps = int(_num(pdf, mapping.get(PdfName("BitsPerSample"))) or 8)
        self.range = _num_array(pdf, mapping.get(PdfName("Range"))) or [0.0, 1.0]
        self.n_out = max(1, len(self.range) // 2)
        self.encode = _num_array(pdf, mapping.get(PdfName("Encode"))) or [
            0.0,
            float(self.size - 1),
        ]
        self.decode = _num_array(pdf, mapping.get(PdfName("Decode"))) or list(self.range)
        try:
            data = pdf._decode_cos_stream(stream, None)
        except Exception:
            data = stream.content
        self.samples = self._read_samples(data)
        self.ok = self.samples is not None

    def _read_samples(self, data: bytes) -> Optional[List[float]]:
        total = self.size * self.n_out
        if self.bps == 8:
            if len(data) < total:
                return None
            return [data[i] / 255.0 for i in range(total)]
        if self.bps == 16:
            if len(data) < total * 2:
                return None
            return [
                ((data[2 * i] << 8) | data[2 * i + 1]) / 65535.0
                for i in range(total)
            ]
        return None  # 1/2/4/32-bit samples unsupported.

    def eval(self, t: float) -> List[float]:
        if not self.ok:
            return [0.0] * self.n_out
        lo, hi = self.domain[0], self.domain[1]
        t = min(max(t, min(lo, hi)), max(lo, hi))
        e_lo, e_hi = self.encode[0], self.encode[1]
        x = e_lo if hi == lo else e_lo + (t - lo) * (e_hi - e_lo) / (hi - lo)
        x = min(max(x, 0.0), float(self.size - 1))
        i0 = int(math.floor(x))
        i1 = min(i0 + 1, self.size - 1)
        frac = x - i0
        out: List[float] = []
        for j in range(self.n_out):
            s0 = self.samples[i0 * self.n_out + j]
            s1 = self.samples[i1 * self.n_out + j]
            sv = s0 + frac * (s1 - s0)
            d_lo = self.decode[2 * j] if 2 * j < len(self.decode) else 0.0
            d_hi = self.decode[2 * j + 1] if 2 * j + 1 < len(self.decode) else 1.0
            out.append(d_lo + sv * (d_hi - d_lo))
        return out


# ---------------------------------------------------------------------------
# Shadings
# ---------------------------------------------------------------------------


class Shading:
    """Base class: holds an RGB lookup table over the parametric range."""

    def __init__(self, lut: List[Color], extend: List[bool]):
        self.lut = lut
        self.extend = (
            bool(extend[0]) if len(extend) > 0 else False,
            bool(extend[1]) if len(extend) > 1 else False,
        )

    def _lookup(self, s: float) -> Color:
        s = 0.0 if s < 0.0 else 1.0 if s > 1.0 else s
        return self.lut[int(s * (len(self.lut) - 1))]

    def color_at(self, x: float, y: float) -> Optional[Color]:  # pragma: no cover
        raise NotImplementedError


class _AxialShading(Shading):
    def __init__(self, coords, lut, extend):
        super().__init__(lut, extend)
        self.x0, self.y0, self.x1, self.y1 = coords[:4]
        dx, dy = self.x1 - self.x0, self.y1 - self.y0
        self._dx, self._dy = dx, dy
        self._dd = dx * dx + dy * dy

    def color_at(self, x: float, y: float) -> Optional[Color]:
        if self._dd == 0:
            s = 0.0
        else:
            s = ((x - self.x0) * self._dx + (y - self.y0) * self._dy) / self._dd
        if s < 0.0:
            if not self.extend[0]:
                return None
        elif s > 1.0:
            if not self.extend[1]:
                return None
        return self._lookup(s)


class _RadialShading(Shading):
    def __init__(self, coords, lut, extend):
        super().__init__(lut, extend)
        self.x0, self.y0, self.r0, self.x1, self.y1, self.r1 = coords[:6]

    def color_at(self, x: float, y: float) -> Optional[Color]:
        dx, dy, dr = self.x1 - self.x0, self.y1 - self.y0, self.r1 - self.r0
        px, py = x - self.x0, y - self.y0
        a = dx * dx + dy * dy - dr * dr
        b = -2.0 * (px * dx + py * dy + self.r0 * dr)
        c = px * px + py * py - self.r0 * self.r0
        best: Optional[float] = None
        if abs(a) < 1e-9:
            if abs(b) > 1e-12:
                best = self._accept(-c / b, dr, best)
        else:
            disc = b * b - 4 * a * c
            if disc >= 0:
                sq = math.sqrt(disc)
                best = self._accept((-b + sq) / (2 * a), dr, best)
                best = self._accept((-b - sq) / (2 * a), dr, best)
        if best is None:
            return None
        return self._lookup(best)

    def _accept(self, s: float, dr: float, best: Optional[float]) -> Optional[float]:
        if self.r0 + s * dr < 0.0:
            return best  # the interpolated radius must be non-negative
        if s < 0.0 and not self.extend[0]:
            return best
        if s > 1.0 and not self.extend[1]:
            return best
        if best is None or s > best:
            return s  # prefer the largest s (its circle paints on top)
        return best


def build_shading(pdf: Any, obj: Any, lut_size: int = 256) -> Optional[Shading]:
    """Build an axial/radial :class:`Shading` from a COS shading dict, or ``None``."""
    obj = pdf._resolve(obj)
    if not isinstance(obj, (PdfDictionary, PdfStream)):
        return None
    mapping = obj.mapping
    stype = _num(pdf, mapping.get(PdfName("ShadingType")))
    if stype is None or int(stype) not in (2, 3):
        return None
    coords = _num_array(pdf, mapping.get(PdfName("Coords")))
    needed = 4 if int(stype) == 2 else 6
    if not coords or len(coords) < needed:
        return None
    func = build_function(pdf, mapping.get(PdfName("Function")))
    if func is None:
        return None
    convert = _color_converter(pdf, mapping.get(PdfName("ColorSpace")))
    domain = _num_array(pdf, mapping.get(PdfName("Domain"))) or [0.0, 1.0]
    extend = _bool_array(pdf, mapping.get(PdfName("Extend"))) or [False, False]

    lut: List[Color] = []
    d_lo, d_hi = domain[0], domain[1]
    span = d_hi - d_lo
    for i in range(lut_size):
        t = d_lo + span * (i / (lut_size - 1))
        try:
            lut.append(convert(func.eval(t)))
        except (ValueError, IndexError, ZeroDivisionError):
            lut.append((0, 0, 0))
    if int(stype) == 2:
        return _AxialShading(coords, lut, extend)
    return _RadialShading(coords, lut, extend)
