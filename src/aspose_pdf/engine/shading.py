"""Dependency-free PDF functions and shadings used by the page rasterizer.

The module evaluates sampled, exponential, stitching, and bounded PostScript
calculator functions. It paints function-based and axial/radial gradients plus
Gouraud, Coons, and tensor-product mesh shadings.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from aspose_pdf.exceptions import PdfParseException, PdfResourceLimitException
from aspose_pdf.load_limits import PdfLoadLimits, _coerce_limits, _LoadBudget

from .cos import PdfArray, PdfDictionary, PdfName, PdfNumber, PdfStream, PdfString

__all__ = ["Shading", "build_color_converter", "build_shading"]

Color = tuple[int, int, int]
Point = tuple[float, float]
Matrix = tuple[float, float, float, float, float, float]


# ---------------------------------------------------------------------------
# COS readers
# ---------------------------------------------------------------------------


def _num(pdf: Any, obj: Any) -> float | None:
    obj = pdf._resolve(obj)
    if isinstance(obj, PdfNumber):
        return float(obj.value)
    if isinstance(obj, (int, float)):
        return float(obj)
    return None


def _num_array(
    pdf: Any,
    obj: Any,
    *,
    budget: _LoadBudget | None = None,
    state: _FunctionBuildState | None = None,
    context: str = "shading numeric array items",
) -> list[float] | None:
    obj = pdf._resolve(obj)
    if not isinstance(obj, PdfArray):
        return None
    if budget is not None:
        budget.check(len(obj.items), "max_container_items", context)
    if state is not None:
        state.charge_items(len(obj.items))
    out: list[float] = []
    for item in obj.items:
        value = _num(pdf, item)
        out.append(value if value is not None else 0.0)
    return out


def _bool_array(
    pdf: Any,
    obj: Any,
    *,
    budget: _LoadBudget | None = None,
    context: str = "shading boolean array items",
) -> list[bool] | None:
    obj = pdf._resolve(obj)
    if not isinstance(obj, PdfArray):
        return None
    if budget is not None:
        budget.check(len(obj.items), "max_container_items", context)
    return [bool(getattr(pdf._resolve(item), "value", False)) for item in obj.items]


def _byte(value: float) -> int:
    return 0 if value < 0 else 255 if value > 255 else int(value + 0.5)


def _components_to_rgb(comps: list[float]) -> Color:
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


def _color_converter(
    pdf: Any,
    cs_obj: Any,
    *,
    limits: PdfLoadLimits | None = None,
    budget: _LoadBudget | None = None,
    seen: set[int] | None = None,
):
    """Return a ``components -> Color`` converter for a shading colour space."""
    cs = pdf._resolve(cs_obj)
    visited = set() if seen is None else seen
    if id(cs) in visited:
        return lambda comps: (0, 0, 0)
    nested_seen = visited | {id(cs)}
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
        if head_name in ("Indexed", "I") and len(cs.items) >= 4:
            # Indexed is not a shading space, but it *is* a fill space: an
            # `scn` operand is a palette index, not a colour component, and
            # feeding it to the generic path reads it as a grey level.
            base = _color_converter(
                pdf, cs.items[1], limits=limits, budget=budget, seen=nested_seen
            )
            base_count = _color_component_count(pdf, cs.items[1])
            palette = _indexed_palette(pdf, cs.items[3])
            hival = int(_num(pdf, cs.items[2]) or 0)

            def indexed(comps, _base=base, _n=base_count, _p=palette, _h=hival):
                if not comps or not _p:
                    return (0, 0, 0)
                index = int(max(0, min(_h, round(comps[0]))))
                start = index * _n
                entry = _p[start : start + _n]
                if len(entry) < _n:
                    return (0, 0, 0)
                return _base([value / 255.0 for value in entry])

            return indexed
        if head_name == "Separation" and len(cs.items) >= 4:
            alternate = _color_converter(
                pdf,
                cs.items[2],
                limits=limits,
                budget=budget,
                seen=nested_seen,
            )
            tint = build_function(
                pdf,
                cs.items[3],
                limits=limits,
                budget=budget,
            )
            if tint is None:
                return lambda comps: (0, 0, 0)

            def convert_separation(comps):
                try:
                    value = comps[0] if comps else 0.0
                    return alternate(tint.eval(value))
                except (PdfParseException, ValueError, IndexError, ZeroDivisionError):
                    return (0, 0, 0)

            return convert_separation
        if head_name in ("DeviceN", "NChannel") and len(cs.items) >= 4:
            names = pdf._resolve(cs.items[1])
            component_count = len(names.items) if isinstance(names, PdfArray) else 0
            alternate = _color_converter(
                pdf,
                cs.items[2],
                limits=limits,
                budget=budget,
                seen=nested_seen,
            )
            tint = build_function(
                pdf,
                cs.items[3],
                limits=limits,
                budget=budget,
            )
            if tint is None or component_count <= 0:
                return lambda comps: (0, 0, 0)

            def convert_device_n(comps):
                try:
                    values = list(comps[:component_count])
                    values.extend([0.0] * (component_count - len(values)))
                    inputs: Any = values[0] if component_count == 1 else values
                    return alternate(tint.eval(inputs))
                except (PdfParseException, ValueError, IndexError, ZeroDivisionError):
                    return (0, 0, 0)

            return convert_device_n
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


def build_color_converter(
    pdf: Any,
    cs_obj: Any,
    *,
    limits: PdfLoadLimits | None = None,
    budget: _LoadBudget | None = None,
):
    """Build a bounded converter from PDF colour components to device RGB."""
    return _color_converter(pdf, cs_obj, limits=limits, budget=budget)


def _indexed_palette(pdf: Any, lookup_obj: Any) -> bytes:
    """The lookup table of an ``/Indexed`` space, as raw bytes."""
    lookup = pdf._resolve(lookup_obj)
    if isinstance(lookup, PdfString):
        raw = lookup.value
        return raw if isinstance(raw, bytes) else str(raw).encode("latin-1")
    if isinstance(lookup, PdfStream):
        decoder = getattr(pdf, "_decode_cos_stream", None)
        if callable(decoder):
            try:
                return decoder(lookup, lookup_obj)
            except PdfResourceLimitException:
                raise
            except Exception:
                return lookup.content
        return lookup.content
    return b""


def _color_component_count(pdf: Any, cs_obj: Any) -> int:
    cs = pdf._resolve(cs_obj)
    if isinstance(cs, PdfName):
        name = cs.name.lstrip("/")
    elif isinstance(cs, PdfArray) and cs.items:
        head = pdf._resolve(cs.items[0])
        name = head.name.lstrip("/") if isinstance(head, PdfName) else ""
        if name == "ICCBased" and len(cs.items) >= 2:
            stream = pdf._resolve(cs.items[1])
            if isinstance(stream, PdfStream):
                count = _num(pdf, stream.mapping.get(PdfName("N")))
                return max(1, int(count or 3))
        if name == "Indexed":
            return 1
        if name == "Separation":
            return 1
        if name in ("DeviceN", "NChannel") and len(cs.items) >= 2:
            names = pdf._resolve(cs.items[1])
            if isinstance(names, PdfArray):
                return max(1, len(names.items))
    else:
        name = ""
    return {
        "DeviceGray": 1,
        "CalGray": 1,
        "G": 1,
        "DeviceCMYK": 4,
        "CMYK": 4,
    }.get(name, 3)


def _color_space_kind(pdf: Any, cs_obj: Any) -> str:
    cs = pdf._resolve(cs_obj)
    if isinstance(cs, PdfName):
        return {
            "DeviceGray": "gray",
            "G": "gray",
            "DeviceRGB": "rgb",
            "RGB": "rgb",
            "DeviceCMYK": "cmyk",
            "CMYK": "cmyk",
        }.get(cs.name.lstrip("/"), "other")
    if isinstance(cs, PdfArray) and cs.items:
        head = pdf._resolve(cs.items[0])
        name = head.name.lstrip("/") if isinstance(head, PdfName) else ""
        if name == "Separation":
            return "spot"
        if name in ("DeviceN", "NChannel"):
            return "device_n"
        if name in ("DeviceCMYK", "CMYK"):
            return "cmyk"
    return "other"


def _transform_point(matrix: Matrix, x: float, y: float) -> Point:
    return (
        matrix[0] * x + matrix[2] * y + matrix[4],
        matrix[1] * x + matrix[3] * y + matrix[5],
    )


def _invert_matrix(matrix: Matrix) -> Matrix | None:
    a, b, c, d, e, f = matrix
    determinant = a * d - b * c
    if abs(determinant) < 1e-12:
        return None
    return (
        d / determinant,
        -b / determinant,
        -c / determinant,
        a / determinant,
        (c * f - d * e) / determinant,
        (b * e - a * f) / determinant,
    )


# ---------------------------------------------------------------------------
# PDF functions
# ---------------------------------------------------------------------------


class _FunctionBuildState:
    """Track one bounded traversal of a PDF function graph."""

    def __init__(self, budget: _LoadBudget) -> None:
        self.budget = budget
        self.active: set[int] = set()
        self.memo: dict[tuple[int, bool], Any] = {}
        self.node_count = 0
        self.item_count = 0

    def charge_items(self, count: int) -> None:
        self.item_count += count
        self.budget.check(
            self.item_count,
            "max_container_items",
            "PDF function materialized items",
        )


def _resolve_budget(
    pdf: Any,
    limits: PdfLoadLimits | None,
    budget: _LoadBudget | None,
) -> _LoadBudget:
    if budget is not None:
        if not isinstance(budget, _LoadBudget):
            raise TypeError("budget must be a _LoadBudget instance or None")
        return budget
    existing = getattr(pdf, "_load_budget", None)
    if limits is None and isinstance(existing, _LoadBudget):
        return existing
    if limits is None:
        limits = getattr(pdf, "_load_limits", None)
    return _LoadBudget(_coerce_limits(limits))


def build_function(
    pdf: Any,
    obj: Any,
    *,
    limits: PdfLoadLimits | None = None,
    budget: _LoadBudget | None = None,
):
    """Build an evaluable function (types 0/2/3/4, or an array of them)."""
    state = _FunctionBuildState(_resolve_budget(pdf, limits, budget))
    return _build_function(pdf, obj, state, depth=1, allow_partial_array=True)


def _build_function(
    pdf: Any,
    obj: Any,
    state: _FunctionBuildState,
    *,
    depth: int,
    allow_partial_array: bool,
):
    obj = pdf._resolve(obj)
    if not isinstance(obj, (PdfArray, PdfDictionary, PdfStream)):
        return None

    state.budget.check(
        depth,
        "max_nesting_depth",
        "PDF function graph depth",
    )
    obj_id = id(obj)
    memo_key = (obj_id, allow_partial_array)
    if obj_id in state.active:
        raise PdfParseException("PDF function graph contains a cycle")
    if memo_key in state.memo:
        return state.memo[memo_key]

    state.node_count += 1
    state.budget.check(
        state.node_count,
        "max_container_items",
        "PDF function graph nodes",
    )
    state.active.add(obj_id)
    try:
        result = _build_function_object(
            pdf,
            obj,
            state,
            depth=depth,
            allow_partial_array=allow_partial_array,
        )
        state.memo[memo_key] = result
        return result
    finally:
        state.active.remove(obj_id)


def _build_function_object(
    pdf: Any,
    obj: Any,
    state: _FunctionBuildState,
    *,
    depth: int,
    allow_partial_array: bool,
):
    if isinstance(obj, PdfArray):
        state.budget.check(
            len(obj.items),
            "max_container_items",
            "PDF function array items",
        )
        state.charge_items(len(obj.items))
        funcs = [
            _build_function(
                pdf,
                item,
                state,
                depth=depth + 1,
                allow_partial_array=True,
            )
            for item in obj.items
        ]
        if not allow_partial_array and any(func is None for func in funcs):
            return None
        funcs = [func for func in funcs if func is not None]
        return _ArrayFunction(funcs) if funcs else None

    mapping = obj.mapping
    state.budget.check(
        len(mapping),
        "max_container_items",
        "PDF function dictionary items",
    )
    ftype = _num(pdf, mapping.get(PdfName("FunctionType")))
    if ftype is None:
        return None
    domain = _num_array(
        pdf,
        mapping.get(PdfName("Domain")),
        budget=state.budget,
        state=state,
        context="PDF function Domain items",
    ) or [0.0, 1.0]
    ftype = int(ftype)
    if ftype == 2:
        c0 = _num_array(
            pdf,
            mapping.get(PdfName("C0")),
            budget=state.budget,
            state=state,
            context="PDF function C0 items",
        ) or [0.0]
        c1 = _num_array(
            pdf,
            mapping.get(PdfName("C1")),
            budget=state.budget,
            state=state,
            context="PDF function C1 items",
        ) or [1.0]
        n = _num(pdf, mapping.get(PdfName("N"))) or 1.0
        return _ExpFunction(domain, c0, c1, n)
    if ftype == 3:
        funcs_obj = pdf._resolve(mapping.get(PdfName("Functions")))
        if not isinstance(funcs_obj, PdfArray):
            return None
        funcs_array = _build_function(
            pdf,
            funcs_obj,
            state,
            depth=depth + 1,
            allow_partial_array=False,
        )
        if not isinstance(funcs_array, _ArrayFunction):
            return None
        bounds = _num_array(
            pdf,
            mapping.get(PdfName("Bounds")),
            budget=state.budget,
            state=state,
            context="PDF function Bounds items",
        ) or []
        encode = _num_array(
            pdf,
            mapping.get(PdfName("Encode")),
            budget=state.budget,
            state=state,
            context="PDF function Encode items",
        ) or []
        return _StitchFunction(domain, funcs_array.funcs, bounds, encode)
    if ftype == 0 and isinstance(obj, PdfStream):
        sampled = _SampledFunction(pdf, obj, domain, state)
        return sampled if sampled.ok else None
    if ftype == 4 and isinstance(obj, PdfStream):
        calculator = _CalculatorFunction(pdf, obj, domain, state)
        return calculator if calculator.ok else None
    return None


class _ExpFunction:
    def __init__(self, domain, c0, c1, n):
        self.domain = domain
        self.c0 = c0
        self.c1 = c1
        self.n = n

    def eval(self, t: float) -> list[float]:
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

    def eval(self, t: float) -> list[float]:
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

    def eval(self, t: float) -> list[float]:
        out: list[float] = []
        for func in self.funcs:
            out.extend(func.eval(t))
        return out


class _SampledFunction:
    def __init__(self, pdf, stream, domain, state: _FunctionBuildState):
        mapping = stream.mapping
        budget = state.budget
        self.domain = domain
        dimensions = len(domain) // 2 if len(domain) % 2 == 0 else 0
        size_values = _num_array(
            pdf,
            mapping.get(PdfName("Size")),
            budget=budget,
            state=state,
            context="sampled function Size items",
        ) or []
        self.size = (
            [int(value) for value in size_values]
            if all(math.isfinite(value) for value in size_values)
            else []
        )
        bps_value = _num(pdf, mapping.get(PdfName("BitsPerSample")))
        self.bps = (
            int(bps_value)
            if bps_value is not None and math.isfinite(bps_value)
            else 8 if bps_value is None else 0
        )
        self.range = _num_array(
            pdf,
            mapping.get(PdfName("Range")),
            budget=budget,
            state=state,
            context="sampled function Range items",
        ) or [0.0, 1.0]
        self.n_out = max(1, len(self.range) // 2)
        self.ok = bool(
            dimensions
            and all(math.isfinite(value) for value in domain)
            and len(self.size) == dimensions
            and all(side > 0 for side in self.size)
            and len(self.range) % 2 == 0
            and all(math.isfinite(value) for value in self.range)
            and self.bps in (1, 2, 4, 8, 12, 16, 24, 32)
        )
        if not self.ok:
            self.encode = []
            self.decode = []
            self.samples = None
            return
        sample_count = 1
        for side in self.size:
            sample_count *= side
        total = sample_count * self.n_out
        budget.check(
            total,
            "max_container_items",
            "sampled function entries",
        )
        interpolation_corners = 1 << sum(side > 1 for side in self.size)
        budget.check(
            interpolation_corners,
            "max_container_items",
            "sampled function interpolation corners",
        )
        required_bytes = (total * self.bps + 7) // 8
        budget.check(
            required_bytes,
            "max_decoded_stream_bytes",
            "sampled function bytes",
        )
        budget.check(
            required_bytes + (total * 32),
            "max_codec_work_bytes",
            "sampled function working set",
        )
        self.encode = _num_array(
            pdf,
            mapping.get(PdfName("Encode")),
            budget=budget,
            state=state,
            context="sampled function Encode items",
        ) or [
            value
            for side in self.size
            for value in (0.0, float(side - 1))
        ]
        self.decode = _num_array(
            pdf,
            mapping.get(PdfName("Decode")),
            budget=budget,
            state=state,
            context="sampled function Decode items",
        ) or list(self.range)
        if (
            len(self.encode) < dimensions * 2
            or len(self.decode) < self.n_out * 2
            or not all(
                math.isfinite(value) for value in self.encode + self.decode
            )
        ):
            self.samples = None
            self.ok = False
            return
        try:
            data = pdf._decode_cos_stream(stream, None)
        except PdfResourceLimitException:
            raise
        except Exception:
            data = stream.content
        budget.check(
            len(data) + (total * 32),
            "max_codec_work_bytes",
            "sampled function working set",
        )
        self.samples = self._read_samples(data, total)
        self.ok = self.samples is not None

    def _read_samples(self, data: bytes, total: int) -> list[float] | None:
        reader = _BitReader(data)
        maximum = (1 << self.bps) - 1
        samples: list[float] = []
        for _ in range(total):
            value = reader.read(self.bps)
            if value is None:
                return None
            samples.append(value / maximum)
        return samples

    def eval(self, value: float | Sequence[float]) -> list[float]:
        if not self.ok:
            return [0.0] * self.n_out
        values = (
            list(value)
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes))
            else [value]
        )
        if len(values) != len(self.size):
            raise PdfParseException(
                f"Sampled function expects {len(self.size)} input values"
            )
        bounds: list[tuple[int, int, float]] = []
        for index, item in enumerate(values):
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise PdfParseException("Sampled function numeric type error")
            if not math.isfinite(float(item)):
                raise PdfParseException("Sampled function non-finite input")
            lo, hi = self.domain[index * 2 : index * 2 + 2]
            clipped = min(max(float(item), min(lo, hi)), max(lo, hi))
            e_lo = self.encode[index * 2] if index * 2 < len(self.encode) else 0.0
            e_hi = (
                self.encode[index * 2 + 1]
                if index * 2 + 1 < len(self.encode)
                else float(self.size[index] - 1)
            )
            encoded = (
                e_lo
                if hi == lo
                else e_lo + (clipped - lo) * (e_hi - e_lo) / (hi - lo)
            )
            encoded = min(max(encoded, 0.0), float(self.size[index] - 1))
            lower = math.floor(encoded)
            upper = min(lower + 1, self.size[index] - 1)
            bounds.append((lower, upper, encoded - lower))

        out = [0.0] * self.n_out
        varying = [index for index, (lo, hi, _) in enumerate(bounds) if lo != hi]
        for corner in range(1 << len(varying)):
            coordinates = [bound[0] for bound in bounds]
            weight = 1.0
            for bit, dimension in enumerate(varying):
                lower, upper, fraction = bounds[dimension]
                if corner & (1 << bit):
                    coordinates[dimension] = upper
                    weight *= fraction
                else:
                    coordinates[dimension] = lower
                    weight *= 1.0 - fraction
            sample_index = 0
            stride = 1
            for coordinate, side in zip(coordinates, self.size):
                sample_index += coordinate * stride
                stride *= side
            base = sample_index * self.n_out
            for output in range(self.n_out):
                out[output] += weight * self.samples[base + output]

        for index, sample in enumerate(out):
            d_lo = self.decode[2 * index] if 2 * index < len(self.decode) else 0.0
            d_hi = (
                self.decode[2 * index + 1]
                if 2 * index + 1 < len(self.decode)
                else 1.0
            )
            decoded = d_lo + sample * (d_hi - d_lo)
            r_lo = self.range[2 * index]
            r_hi = self.range[2 * index + 1]
            out[index] = min(max(decoded, min(r_lo, r_hi)), max(r_lo, r_hi))
        return out


_CALCULATOR_NUMBER = re.compile(
    r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$"
)
_CALCULATOR_OPERATORS = {
    "abs",
    "add",
    "and",
    "atan",
    "bitshift",
    "ceiling",
    "copy",
    "cos",
    "cvi",
    "cvr",
    "div",
    "dup",
    "eq",
    "exch",
    "exp",
    "false",
    "floor",
    "ge",
    "gt",
    "idiv",
    "if",
    "ifelse",
    "index",
    "le",
    "ln",
    "log",
    "lt",
    "mod",
    "mul",
    "ne",
    "neg",
    "not",
    "or",
    "pop",
    "roll",
    "round",
    "sin",
    "sqrt",
    "sub",
    "true",
    "truncate",
    "xor",
}


class _CalculatorFunction:
    """Bounded interpreter for the PDF type 4 calculator language."""

    _MAX_STACK = 100

    def __init__(self, pdf, stream, domain, state: _FunctionBuildState):
        self.domain = domain
        self.range = _num_array(
            pdf,
            stream.mapping.get(PdfName("Range")),
            budget=state.budget,
            state=state,
            context="calculator function Range items",
        ) or []
        self.ok = bool(
            self.domain
            and len(self.domain) % 2 == 0
            and self.range
            and len(self.range) % 2 == 0
        )
        if not self.ok:
            self.code: list[Any] = []
            return
        try:
            data = pdf._decode_cos_stream(stream, None)
        except PdfResourceLimitException:
            raise
        except Exception as exc:
            raise PdfParseException(
                "Unable to decode calculator function stream"
            ) from exc
        state.budget.check(
            len(data),
            "max_decoded_stream_bytes",
            "calculator function bytes",
        )
        try:
            source = data.decode("latin-1")
        except UnicodeDecodeError as exc:  # pragma: no cover - latin-1 is total
            raise PdfParseException("Invalid calculator function source") from exc
        tokens = self._tokenize(source, state)
        self.code = self._parse(tokens, state)

    @staticmethod
    def _tokenize(source: str, state: _FunctionBuildState) -> list[str]:
        tokens: list[str] = []
        i = 0
        length = len(source)
        while i < length:
            char = source[i]
            if char.isspace():
                i += 1
                continue
            if char == "%":
                while i < length and source[i] not in "\r\n":
                    i += 1
                continue
            if char in "{}":
                tokens.append(char)
                i += 1
            else:
                start = i
                while (
                    i < length
                    and not source[i].isspace()
                    and source[i] not in "{}%"
                ):
                    i += 1
                if start == i:
                    raise PdfParseException("Invalid calculator function token")
                tokens.append(source[start:i])
            state.budget.check(
                len(tokens),
                "max_content_tokens",
                "calculator function tokens",
            )
            state.budget.check(
                len(tokens),
                "max_container_items",
                "calculator function tokens",
            )
            state.budget.check(
                len(source) + len(tokens) * 64,
                "max_codec_work_bytes",
                "calculator function working set",
            )
        return tokens

    @classmethod
    def _parse(
        cls, tokens: list[str], state: _FunctionBuildState
    ) -> list[Any]:
        if not tokens or tokens[0] != "{":
            raise PdfParseException("Calculator function must be enclosed in braces")

        def parse_proc(index: int, depth: int) -> tuple[list[Any], int]:
            state.budget.check(
                depth,
                "max_nesting_depth",
                "calculator function procedure depth",
            )
            out: list[Any] = []
            while index < len(tokens):
                token = tokens[index]
                index += 1
                if token == "}":
                    return out, index
                if token == "{":
                    proc, index = parse_proc(index, depth + 1)
                    out.append(proc)
                    continue
                if _CALCULATOR_NUMBER.fullmatch(token):
                    try:
                        value: Any = (
                            float(token)
                            if any(char in token for char in ".eE")
                            else int(token)
                        )
                        if isinstance(value, int) and not (
                            -(1 << 31) <= value < (1 << 31)
                        ):
                            value = float(token)
                        if isinstance(value, float) and not math.isfinite(value):
                            raise ValueError
                    except (ValueError, OverflowError) as exc:
                        raise PdfParseException(
                            "Invalid calculator function number"
                        ) from exc
                    out.append(value)
                    continue
                if token not in _CALCULATOR_OPERATORS:
                    raise PdfParseException(
                        f"Unsupported calculator function operator: {token}"
                    )
                out.append(token)
            raise PdfParseException("Unterminated calculator function procedure")

        code, index = parse_proc(1, 1)
        if index != len(tokens):
            raise PdfParseException("Trailing calculator function tokens")
        return code

    def eval(self, value: float | Sequence[float]) -> list[float]:
        if not self.ok:
            return []
        values = (
            list(value)
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes))
            else [value]
        )
        expected = len(self.domain) // 2
        if len(values) != expected:
            raise PdfParseException(
                f"Calculator function expects {expected} input values"
            )
        stack: list[Any] = []
        for index, item in enumerate(values):
            number = self._number(item)
            lo = self.domain[index * 2]
            hi = self.domain[index * 2 + 1]
            stack.append(min(max(number, min(lo, hi)), max(lo, hi)))
        self._execute(self.code, stack)
        output_count = len(self.range) // 2
        if len(stack) != output_count or any(
            isinstance(item, (bool, list)) or not isinstance(item, (int, float))
            for item in stack
        ):
            raise PdfParseException(
                "Calculator function produced an invalid output stack"
            )
        out: list[float] = []
        for index, item in enumerate(stack):
            number = float(item)
            lo = self.range[index * 2]
            hi = self.range[index * 2 + 1]
            out.append(min(max(number, min(lo, hi)), max(lo, hi)))
        return out

    def _execute(self, code: list[Any], stack: list[Any]) -> None:
        for item in code:
            if isinstance(item, list):
                self._push(stack, item)
            elif isinstance(item, (int, float)):
                self._push(stack, item)
            else:
                self._operator(item, stack)

    def _operator(self, op: str, stack: list[Any]) -> None:
        if op in ("true", "false"):
            self._push(stack, op == "true")
            return
        if op == "dup":
            self._require(stack, 1)
            self._push(stack, stack[-1])
            return
        if op == "exch":
            self._require(stack, 2)
            stack[-1], stack[-2] = stack[-2], stack[-1]
            return
        if op == "pop":
            self._pop(stack)
            return
        if op == "copy":
            count = self._integer(self._pop(stack))
            if count < 0 or count > len(stack):
                raise PdfParseException("Calculator function copy range error")
            for item in stack[-count:] if count else []:
                self._push(stack, item)
            return
        if op == "index":
            index = self._integer(self._pop(stack))
            if index < 0 or index >= len(stack):
                raise PdfParseException("Calculator function index range error")
            self._push(stack, stack[-index - 1])
            return
        if op == "roll":
            shift = self._integer(self._pop(stack))
            count = self._integer(self._pop(stack))
            if count < 0 or count > len(stack):
                raise PdfParseException("Calculator function roll range error")
            if count:
                shift %= count
                stack[-count:] = stack[-shift:] + stack[-count:-shift]
            return
        if op in ("if", "ifelse"):
            if op == "if":
                proc = self._procedure(self._pop(stack))
                condition = self._boolean(self._pop(stack))
                if condition:
                    self._execute(proc, stack)
            else:
                false_proc = self._procedure(self._pop(stack))
                true_proc = self._procedure(self._pop(stack))
                condition = self._boolean(self._pop(stack))
                self._execute(true_proc if condition else false_proc, stack)
            return
        if op in ("abs", "ceiling", "cos", "cvi", "cvr", "floor", "ln", "log", "neg", "round", "sin", "sqrt", "truncate"):
            self._unary_numeric(op, stack)
            return
        if op in ("add", "atan", "div", "exp", "idiv", "mod", "mul", "sub"):
            self._binary_numeric(op, stack)
            return
        if op in ("eq", "ne"):
            right = self._pop(stack)
            left = self._pop(stack)
            self._push(stack, (left == right) if op == "eq" else (left != right))
            return
        if op in ("ge", "gt", "le", "lt"):
            right = self._number(self._pop(stack))
            left = self._number(self._pop(stack))
            result = {
                "ge": left >= right,
                "gt": left > right,
                "le": left <= right,
                "lt": left < right,
            }[op]
            self._push(stack, result)
            return
        if op in ("and", "or", "xor"):
            right = self._pop(stack)
            left = self._pop(stack)
            if isinstance(left, bool) and isinstance(right, bool):
                result = {
                    "and": left and right,
                    "or": left or right,
                    "xor": left != right,
                }[op]
            else:
                a = self._integer(left)
                b = self._integer(right)
                result = {"and": a & b, "or": a | b, "xor": a ^ b}[op]
            self._push(stack, result)
            return
        if op == "not":
            value = self._pop(stack)
            if isinstance(value, bool):
                self._push(stack, not value)
            else:
                self._push(stack, ~self._integer(value))
            return
        if op == "bitshift":
            shift = self._integer(self._pop(stack))
            value = self._integer(self._pop(stack))
            if abs(shift) >= 32:
                result = 0
            elif shift >= 0:
                result = (value & 0xFFFFFFFF) << shift
            else:
                result = value >> -shift
            result &= 0xFFFFFFFF
            if result & 0x80000000:
                result -= 0x100000000
            self._push(stack, result)
            return
        raise PdfParseException(f"Unsupported calculator function operator: {op}")

    def _unary_numeric(self, op: str, stack: list[Any]) -> None:
        value = self._number(self._pop(stack))
        if op == "abs":
            result: Any = abs(value)
        elif op == "ceiling":
            result = math.ceil(value)
        elif op == "cos":
            result = math.cos(math.radians(value))
        elif op == "cvi":
            result = math.trunc(value)
        elif op == "cvr":
            result = float(value)
        elif op == "floor":
            result = math.floor(value)
        elif op == "ln":
            if value <= 0:
                raise PdfParseException("Calculator function ln range error")
            result = math.log(value)
        elif op == "log":
            if value <= 0:
                raise PdfParseException("Calculator function log range error")
            result = math.log10(value)
        elif op == "neg":
            result = -value
        elif op == "round":
            result = math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5)
        elif op == "sin":
            result = math.sin(math.radians(value))
        elif op == "sqrt":
            if value < 0:
                raise PdfParseException("Calculator function sqrt range error")
            result = math.sqrt(value)
        else:
            result = math.trunc(value)
        self._push(stack, result)

    def _binary_numeric(self, op: str, stack: list[Any]) -> None:
        right = self._number(self._pop(stack))
        left = self._number(self._pop(stack))
        try:
            if op == "add":
                result: Any = left + right
            elif op == "atan":
                result = math.degrees(math.atan2(left, right)) % 360.0
            elif op == "div":
                result = left / right
            elif op == "exp":
                result = left**right
            elif op == "idiv":
                result = math.trunc(left / right)
            elif op == "mod":
                result = left - math.trunc(left / right) * right
            elif op == "mul":
                result = left * right
            else:
                result = left - right
            if isinstance(result, int) and not (
                -(1 << 31) <= result < (1 << 31)
            ):
                result = float(result)
        except (ArithmeticError, OverflowError, ValueError) as exc:
            raise PdfParseException(
                f"Calculator function {op} numeric error"
            ) from exc
        if isinstance(result, float) and not math.isfinite(result):
            raise PdfParseException(f"Calculator function {op} produced non-finite value")
        self._push(stack, result)

    @classmethod
    def _push(cls, stack: list[Any], value: Any) -> None:
        if len(stack) >= cls._MAX_STACK:
            raise PdfParseException("Calculator function stack overflow")
        stack.append(value)

    @staticmethod
    def _require(stack: list[Any], count: int) -> None:
        if len(stack) < count:
            raise PdfParseException("Calculator function stack underflow")

    @classmethod
    def _pop(cls, stack: list[Any]) -> Any:
        cls._require(stack, 1)
        return stack.pop()

    @staticmethod
    def _number(value: Any) -> int | float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise PdfParseException("Calculator function numeric type error")
        if isinstance(value, float) and not math.isfinite(value):
            raise PdfParseException("Calculator function non-finite number")
        return value

    @staticmethod
    def _integer(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise PdfParseException("Calculator function integer type error")
        return value

    @staticmethod
    def _boolean(value: Any) -> bool:
        if not isinstance(value, bool):
            raise PdfParseException("Calculator function boolean type error")
        return value

    @staticmethod
    def _procedure(value: Any) -> list[Any]:
        if not isinstance(value, list):
            raise PdfParseException("Calculator function procedure type error")
        return value


# ---------------------------------------------------------------------------
# Shadings
# ---------------------------------------------------------------------------


class Shading:
    """Base class for bounded RGB sampling in a shading's target space."""

    def __init__(self, lut: list[Color], extend: list[bool]):
        self.lut = lut
        self.extend = (
            bool(extend[0]) if len(extend) > 0 else False,
            bool(extend[1]) if len(extend) > 1 else False,
        )
        self.bbox: tuple[float, float, float, float] | None = None
        self.background: Color | None = None
        self.color_kind = "other"

    def configure(
        self,
        *,
        bbox: tuple[float, float, float, float] | None,
        background: Color | None,
        color_kind: str,
    ) -> Shading:
        self.bbox = bbox
        self.background = background
        self.color_kind = color_kind
        return self

    def _lookup(self, s: float) -> Color:
        s = 0.0 if s < 0.0 else 1.0 if s > 1.0 else s
        return self.lut[int(s * (len(self.lut) - 1))]

    def _inside_bbox(self, x: float, y: float) -> bool:
        if not math.isfinite(x) or not math.isfinite(y):
            return False
        if self.bbox is None:
            return True
        x0, y0, x1, y1 = self.bbox
        return x0 <= x <= x1 and y0 <= y <= y1

    def color_at(self, x: float, y: float) -> Color | None:
        if not self._inside_bbox(x, y):
            return None
        return self._color_at(x, y)

    def pattern_color_at(self, x: float, y: float) -> Color | None:
        """Sample a shading pattern, applying its optional background colour."""
        if not self._inside_bbox(x, y):
            return None
        return self._color_at(x, y) or self.background

    def _color_at(self, x: float, y: float) -> Color | None:  # pragma: no cover
        raise NotImplementedError


class _FunctionShading(Shading):
    def __init__(self, function, convert, domain, inverse_matrix):
        super().__init__([(0, 0, 0)], [False, False])
        self.function = function
        self.convert = convert
        self.x0, self.x1, self.y0, self.y1 = domain[:4]
        self.inverse_matrix: Matrix = inverse_matrix

    def _color_at(self, x: float, y: float) -> Color | None:
        domain_x, domain_y = _transform_point(self.inverse_matrix, x, y)
        if not (
            min(self.x0, self.x1) <= domain_x <= max(self.x0, self.x1)
            and min(self.y0, self.y1) <= domain_y <= max(self.y0, self.y1)
        ):
            return None
        try:
            return self.convert(self.function.eval([domain_x, domain_y]))
        except (PdfParseException, ValueError, IndexError, ZeroDivisionError):
            return None


class _AxialShading(Shading):
    def __init__(self, coords, lut, extend):
        super().__init__(lut, extend)
        self.x0, self.y0, self.x1, self.y1 = coords[:4]
        dx, dy = self.x1 - self.x0, self.y1 - self.y0
        self._dx, self._dy = dx, dy
        self._dd = dx * dx + dy * dy

    def _color_at(self, x: float, y: float) -> Color | None:
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

    def _color_at(self, x: float, y: float) -> Color | None:
        dx, dy, dr = self.x1 - self.x0, self.y1 - self.y0, self.r1 - self.r0
        px, py = x - self.x0, y - self.y0
        a = dx * dx + dy * dy - dr * dr
        b = -2.0 * (px * dx + py * dy + self.r0 * dr)
        c = px * px + py * py - self.r0 * self.r0
        best: float | None = None
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

    def _accept(self, s: float, dr: float, best: float | None) -> float | None:
        if self.r0 + s * dr < 0.0:
            return best  # the interpolated radius must be non-negative
        if s < 0.0 and not self.extend[0]:
            return best
        if s > 1.0 and not self.extend[1]:
            return best
        if best is None or s > best:
            return s  # prefer the largest s (its circle paints on top)
        return best


@dataclass(frozen=True)
class _MeshVertex:
    x: float
    y: float
    components: tuple[float, ...]


@dataclass(frozen=True)
class _MeshTriangle:
    a: _MeshVertex
    b: _MeshVertex
    c: _MeshVertex

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (
            min(self.a.x, self.b.x, self.c.x),
            min(self.a.y, self.b.y, self.c.y),
            max(self.a.x, self.b.x, self.c.x),
            max(self.a.y, self.b.y, self.c.y),
        )


class _MeshShading(Shading):
    """Triangle mesh with a bounded uniform spatial index."""

    def __init__(self, triangles, convert, function):
        super().__init__([(0, 0, 0)], [False, False])
        self.triangles: list[_MeshTriangle] = triangles
        self.convert = convert
        self.function = function
        boxes = [triangle.bbox for triangle in triangles]
        self.boxes = boxes
        self._cells: dict[tuple[int, int], list[int]] = {}
        self._wide: list[int] = []
        if not boxes:
            self._bounds = (0.0, 0.0, 0.0, 0.0)
            self._grid = 1
            return
        self._bounds = (
            min(box[0] for box in boxes),
            min(box[1] for box in boxes),
            max(box[2] for box in boxes),
            max(box[3] for box in boxes),
        )
        self._grid = min(32, max(1, int(math.sqrt(len(boxes)))))
        for index, box in enumerate(boxes):
            x0, y0 = self._cell(box[0], box[1])
            x1, y1 = self._cell(box[2], box[3])
            cell_count = (x1 - x0 + 1) * (y1 - y0 + 1)
            if cell_count > 64:
                self._wide.append(index)
                continue
            for gy in range(y0, y1 + 1):
                for gx in range(x0, x1 + 1):
                    self._cells.setdefault((gx, gy), []).append(index)

    def _cell(self, x: float, y: float) -> tuple[int, int]:
        x0, y0, x1, y1 = self._bounds
        gx = 0 if x1 == x0 else int((x - x0) * self._grid / (x1 - x0))
        gy = 0 if y1 == y0 else int((y - y0) * self._grid / (y1 - y0))
        return (
            min(self._grid - 1, max(0, gx)),
            min(self._grid - 1, max(0, gy)),
        )

    def _color_at(self, x: float, y: float) -> Color | None:
        x0, y0, x1, y1 = self._bounds
        if x < x0 or x > x1 or y < y0 or y > y1:
            return None
        cell = self._cells.get(self._cell(x, y), [])
        left = len(cell) - 1
        right = len(self._wide) - 1
        while left >= 0 or right >= 0:
            cell_index = cell[left] if left >= 0 else -1
            wide_index = self._wide[right] if right >= 0 else -1
            if cell_index >= wide_index:
                index = cell_index
                left -= 1
            else:
                index = wide_index
                right -= 1
            weights = self._weights(self.triangles[index], x, y)
            if weights is None:
                continue
            wa, wb, wc = weights
            triangle = self.triangles[index]
            count = len(triangle.a.components)
            components = [
                wa * triangle.a.components[item]
                + wb * triangle.b.components[item]
                + wc * triangle.c.components[item]
                for item in range(count)
            ]
            if self.function is not None:
                components = self.function.eval(components[0])
            return self.convert(components)
        return None

    @staticmethod
    def _weights(
        triangle: _MeshTriangle, x: float, y: float
    ) -> tuple[float, float, float] | None:
        ax, ay = triangle.a.x, triangle.a.y
        bx, by = triangle.b.x, triangle.b.y
        cx, cy = triangle.c.x, triangle.c.y
        denominator = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(denominator) < 1e-14:
            return None
        wa = ((by - cy) * (x - cx) + (cx - bx) * (y - cy)) / denominator
        wb = ((cy - ay) * (x - cx) + (ax - cx) * (y - cy)) / denominator
        wc = 1.0 - wa - wb
        epsilon = -1e-9
        if wa < epsilon or wb < epsilon or wc < epsilon:
            return None
        return wa, wb, wc


class _BitReader:
    def __init__(self, data: bytes):
        self.data = data
        self.bit = 0

    @property
    def remaining(self) -> int:
        return len(self.data) * 8 - self.bit

    def read(self, count: int) -> int | None:
        if count < 0 or self.remaining < count:
            return None
        value = 0
        for _ in range(count):
            byte = self.data[self.bit >> 3]
            value = (value << 1) | ((byte >> (7 - (self.bit & 7))) & 1)
            self.bit += 1
        return value

    def align(self) -> None:
        self.bit = min(len(self.data) * 8, (self.bit + 7) & ~7)


def _decode_mesh_value(raw: int, bits: int, lo: float, hi: float) -> float:
    maximum = (1 << bits) - 1
    return lo if maximum == 0 else lo + raw * (hi - lo) / maximum


def _read_mesh_vertex(
    reader: _BitReader,
    *,
    coordinate_bits: int,
    component_bits: int,
    component_count: int,
    decode: list[float],
    flag_bits: int = 0,
) -> tuple[int, _MeshVertex] | None:
    flag = reader.read(flag_bits) if flag_bits else 0
    x = reader.read(coordinate_bits)
    y = reader.read(coordinate_bits)
    raw_components = [reader.read(component_bits) for _ in range(component_count)]
    if flag is None or x is None or y is None or any(
        item is None for item in raw_components
    ):
        return None
    components = tuple(
        _decode_mesh_value(
            int(value),
            component_bits,
            decode[4 + index * 2],
            decode[5 + index * 2],
        )
        for index, value in enumerate(raw_components)
    )
    vertex = _MeshVertex(
        _decode_mesh_value(x, coordinate_bits, decode[0], decode[1]),
        _decode_mesh_value(y, coordinate_bits, decode[2], decode[3]),
        components,
    )
    reader.align()
    return int(flag), vertex


def _decode_mesh_stream(pdf: Any, stream: PdfStream) -> bytes:
    try:
        return pdf._decode_cos_stream(stream, None)
    except PdfResourceLimitException:
        raise
    except Exception as exc:
        raise PdfParseException("Unable to decode mesh shading stream") from exc


def _mesh_parameters(pdf, stream, budget):
    mapping = stream.mapping
    coordinate_bits = int(_num(pdf, mapping.get(PdfName("BitsPerCoordinate"))) or 0)
    component_bits = int(_num(pdf, mapping.get(PdfName("BitsPerComponent"))) or 0)
    if coordinate_bits not in (1, 2, 4, 8, 12, 16, 24, 32):
        return None
    if component_bits not in (1, 2, 4, 8, 12, 16):
        return None
    function_obj = mapping.get(PdfName("Function"))
    function = (
        build_function(pdf, function_obj, limits=budget.limits, budget=budget)
        if function_obj is not None
        else None
    )
    if function_obj is not None and function is None:
        return None
    component_count = (
        1
        if function is not None
        else _color_component_count(pdf, mapping.get(PdfName("ColorSpace")))
    )
    decode = _num_array(
        pdf,
        mapping.get(PdfName("Decode")),
        budget=budget,
        context="mesh shading Decode items",
    )
    if decode is None or len(decode) < 4 + component_count * 2:
        return None
    data = _decode_mesh_stream(pdf, stream)
    budget.check(
        len(data), "max_decoded_stream_bytes", "mesh shading decoded bytes"
    )
    budget.check(
        len(data) * 16,
        "max_codec_work_bytes",
        "mesh shading decode working set",
    )
    return coordinate_bits, component_bits, component_count, decode, function, data


def _build_triangle_mesh(pdf, stream, shading_type, budget, convert):
    params = _mesh_parameters(pdf, stream, budget)
    if params is None:
        return None
    coordinate_bits, component_bits, component_count, decode, function, data = params
    reader = _BitReader(data)
    vertices: list[_MeshVertex] = []
    flags: list[int] = []
    flag_bits = 0
    if shading_type == 4:
        flag_bits = int(
            _num(pdf, stream.mapping.get(PdfName("BitsPerFlag"))) or 0
        )
        if flag_bits not in (2, 4, 8):
            return None
    record_bits = flag_bits + coordinate_bits * 2 + component_bits * component_count
    record_bytes = (record_bits + 7) // 8
    if record_bytes == 0 or len(data) % record_bytes:
        return None
    count = len(data) // record_bytes
    budget.check(count, "max_container_items", "mesh shading vertices")
    budget.check(
        count * 160,
        "max_codec_work_bytes",
        "mesh shading vertex working set",
    )
    for _ in range(count):
        result = _read_mesh_vertex(
            reader,
            coordinate_bits=coordinate_bits,
            component_bits=component_bits,
            component_count=component_count,
            decode=decode,
            flag_bits=flag_bits,
        )
        if result is None:
            return None
        flag, vertex = result
        flags.append(flag & 3)
        vertices.append(vertex)
    triangles: list[_MeshTriangle] = []
    if shading_type == 4:
        index = 0
        previous: tuple[_MeshVertex, _MeshVertex, _MeshVertex] | None = None
        while index < len(vertices):
            flag = flags[index]
            if flag == 0:
                if index + 2 >= len(vertices):
                    return None
                current = (vertices[index], vertices[index + 1], vertices[index + 2])
                index += 3
            elif flag in (1, 2) and previous is not None:
                new = vertices[index]
                current = (
                    (previous[1], previous[2], new)
                    if flag == 1
                    else (previous[0], previous[2], new)
                )
                index += 1
            else:
                return None
            triangles.append(_MeshTriangle(*current))
            previous = current
    else:
        per_row = int(_num(pdf, stream.mapping.get(PdfName("VerticesPerRow"))) or 0)
        if per_row < 2 or len(vertices) < per_row * 2 or len(vertices) % per_row:
            return None
        rows = len(vertices) // per_row
        for row in range(rows - 1):
            start = row * per_row
            next_start = start + per_row
            for column in range(per_row - 1):
                top_left = vertices[start + column]
                top_right = vertices[start + column + 1]
                bottom_left = vertices[next_start + column]
                bottom_right = vertices[next_start + column + 1]
                triangles.append(_MeshTriangle(top_left, top_right, bottom_left))
                triangles.append(_MeshTriangle(top_right, bottom_left, bottom_right))
    budget.check(len(triangles), "max_container_items", "mesh shading triangles")
    budget.check(
        len(triangles) * 1024,
        "max_codec_work_bytes",
        "mesh shading spatial index working set",
    )
    return _MeshShading(triangles, convert, function)


def _read_patch_values(
    reader: _BitReader,
    count: int,
    bits: int,
    decode: list[float],
    offset: int,
) -> list[tuple[float, float]] | None:
    points: list[tuple[float, float]] = []
    for _ in range(count):
        raw_x = reader.read(bits)
        raw_y = reader.read(bits)
        if raw_x is None or raw_y is None:
            return None
        points.append(
            (
                _decode_mesh_value(raw_x, bits, decode[offset], decode[offset + 1]),
                _decode_mesh_value(
                    raw_y, bits, decode[offset + 2], decode[offset + 3]
                ),
            )
        )
    return points


def _read_patch_colors(
    reader: _BitReader,
    count: int,
    bits: int,
    component_count: int,
    decode: list[float],
) -> list[tuple[float, ...]] | None:
    colors: list[tuple[float, ...]] = []
    for _ in range(count):
        color: list[float] = []
        for component in range(component_count):
            raw = reader.read(bits)
            if raw is None:
                return None
            color.append(
                _decode_mesh_value(
                    raw,
                    bits,
                    decode[4 + component * 2],
                    decode[5 + component * 2],
                )
            )
        colors.append(tuple(color))
    return colors


def _shared_patch_edge(previous_points, previous_colors, flag):
    point_indices = {
        1: (3, 4, 5, 6),
        2: (6, 7, 8, 9),
        3: (9, 10, 11, 0),
    }[flag]
    color_indices = {1: (1, 2), 2: (2, 3), 3: (3, 0)}[flag]
    return (
        [previous_points[index] for index in point_indices],
        [previous_colors[index] for index in color_indices],
    )


def _coons_tensor(points: list[tuple[float, float]]) -> list[list[Point]]:
    p00, p01, p02, p03, p13, p23, p33, p32, p31, p30, p20, p10 = points
    grid: list[list[Point]] = [
        [p00, p01, p02, p03],
        [p10, (0.0, 0.0), (0.0, 0.0), p13],
        [p20, (0.0, 0.0), (0.0, 0.0), p23],
        [p30, p31, p32, p33],
    ]

    def combine(terms: Sequence[tuple[float, Point]]) -> Point:
        return (
            sum(weight * point[0] for weight, point in terms) / 9.0,
            sum(weight * point[1] for weight, point in terms) / 9.0,
        )

    grid[1][1] = combine(
        [
            (-4, p00),
            (6, p01),
            (6, p10),
            (-2, p03),
            (-2, p30),
            (3, p31),
            (3, p13),
            (-1, p33),
        ]
    )
    grid[1][2] = combine(
        [
            (-4, p03),
            (6, p02),
            (6, p13),
            (-2, p00),
            (-2, p33),
            (3, p32),
            (3, p10),
            (-1, p30),
        ]
    )
    grid[2][1] = combine(
        [
            (-4, p30),
            (6, p31),
            (6, p20),
            (-2, p33),
            (-2, p00),
            (3, p01),
            (3, p23),
            (-1, p03),
        ]
    )
    grid[2][2] = combine(
        [
            (-4, p33),
            (6, p32),
            (6, p23),
            (-2, p30),
            (-2, p03),
            (3, p02),
            (3, p20),
            (-1, p00),
        ]
    )
    return grid


def _tensor_grid(points: list[tuple[float, float]]) -> list[list[Point]]:
    (
        p00,
        p01,
        p02,
        p03,
        p13,
        p23,
        p33,
        p32,
        p31,
        p30,
        p20,
        p10,
        p11,
        p12,
        p22,
        p21,
    ) = points
    return [
        [p00, p01, p02, p03],
        [p10, p11, p12, p13],
        [p20, p21, p22, p23],
        [p30, p31, p32, p33],
    ]


def _bernstein(t: float) -> tuple[float, float, float, float]:
    inverse = 1.0 - t
    return (
        inverse**3,
        3.0 * t * inverse * inverse,
        3.0 * t * t * inverse,
        t**3,
    )


def _tensor_point(grid: list[list[Point]], u: float, v: float) -> Point:
    bu = _bernstein(u)
    bv = _bernstein(v)
    return (
        sum(grid[i][j][0] * bu[i] * bv[j] for i in range(4) for j in range(4)),
        sum(grid[i][j][1] * bu[i] * bv[j] for i in range(4) for j in range(4)),
    )


def _patch_components(colors, u: float, v: float) -> tuple[float, ...]:
    c00, c03, c33, c30 = colors
    return tuple(
        (1.0 - u) * (1.0 - v) * c00[index]
        + (1.0 - u) * v * c03[index]
        + u * v * c33[index]
        + u * (1.0 - v) * c30[index]
        for index in range(len(c00))
    )


def _triangle_interpolation(corners, u: float, v: float) -> tuple[float, ...]:
    a, _b, _c, _d = corners
    if u + v <= 1.0:
        weights = (1.0 - u - v, u, v, 0.0)
    else:
        weights = (0.0, 1.0 - v, 1.0 - u, u + v - 1.0)
    return tuple(
        sum(weight * corner[index] for weight, corner in zip(weights, corners))
        for index in range(len(a))
    )


def _adaptive_patch_steps(
    grid,
    colors,
    device_scale: float,
    budget: _LoadBudget,
    existing_triangles: int,
) -> int:
    scale = abs(float(device_scale))
    if not math.isfinite(scale):
        scale = 1.0
    scale = max(1e-6, min(scale, 1e9))
    samples = (0.25, 0.5, 0.75)
    for depth in range(7):
        steps = 1 << depth
        triangle_count = existing_triangles + steps * steps * 2
        budget.check(
            triangle_count,
            "max_container_items",
            "mesh shading triangles",
        )
        budget.check(
            triangle_count * 1024,
            "max_codec_work_bytes",
            "mesh shading tessellation working set",
        )
        refine = False
        for row in range(steps):
            v0, v1 = row / steps, (row + 1) / steps
            for column in range(steps):
                u0, u1 = column / steps, (column + 1) / steps
                point_corners = (
                    _tensor_point(grid, u0, v0),
                    _tensor_point(grid, u1, v0),
                    _tensor_point(grid, u0, v1),
                    _tensor_point(grid, u1, v1),
                )
                color_corners = (
                    _patch_components(colors, u0, v0),
                    _patch_components(colors, u1, v0),
                    _patch_components(colors, u0, v1),
                    _patch_components(colors, u1, v1),
                )
                for local_v in samples:
                    v = v0 + (v1 - v0) * local_v
                    for local_u in samples:
                        u = u0 + (u1 - u0) * local_u
                        expected_point = _triangle_interpolation(
                            point_corners, local_u, local_v
                        )
                        actual_point = _tensor_point(grid, u, v)
                        point_error = math.hypot(
                            actual_point[0] - expected_point[0],
                            actual_point[1] - expected_point[1],
                        )
                        expected_color = _triangle_interpolation(
                            color_corners, local_u, local_v
                        )
                        actual_color = _patch_components(colors, u, v)
                        color_error = max(
                            abs(actual - expected)
                            for actual, expected in zip(
                                actual_color, expected_color
                            )
                        )
                        if point_error * scale > 0.35 or color_error > 2 / 255:
                            refine = True
                            break
                    if refine:
                        break
                if refine:
                    break
            if refine:
                break
        if not refine or depth == 6:
            return steps
    return 64  # pragma: no cover - the bounded loop always returns


def _tessellate_patch(grid, colors, steps):
    vertices: list[list[_MeshVertex]] = []
    for row in range(steps + 1):
        v = row / steps
        vertex_row: list[_MeshVertex] = []
        for column in range(steps + 1):
            u = column / steps
            x, y = _tensor_point(grid, u, v)
            vertex_row.append(_MeshVertex(x, y, _patch_components(colors, u, v)))
        vertices.append(vertex_row)
    triangles: list[_MeshTriangle] = []
    for row in range(steps):
        for column in range(steps):
            a = vertices[row][column]
            b = vertices[row][column + 1]
            c = vertices[row + 1][column]
            d = vertices[row + 1][column + 1]
            triangles.append(_MeshTriangle(a, b, c))
            triangles.append(_MeshTriangle(b, c, d))
    return triangles


def _build_patch_mesh(pdf, stream, shading_type, budget, device_scale, convert):
    params = _mesh_parameters(pdf, stream, budget)
    if params is None:
        return None
    coordinate_bits, component_bits, component_count, decode, function, data = params
    flag_bits = int(_num(pdf, stream.mapping.get(PdfName("BitsPerFlag"))) or 0)
    if flag_bits not in (2, 4, 8):
        return None
    reader = _BitReader(data)
    triangles: list[_MeshTriangle] = []
    previous_points = None
    previous_colors = None
    patch_count = 0
    full_point_count = 12 if shading_type == 6 else 16
    while reader.remaining >= flag_bits:
        flag = reader.read(flag_bits)
        if flag is None:
            return None
        flag &= 3
        if flag == 0:
            points = _read_patch_values(
                reader, full_point_count, coordinate_bits, decode, 0
            )
            colors = _read_patch_colors(
                reader, 4, component_bits, component_count, decode
            )
        elif previous_points is not None and previous_colors is not None:
            points, colors = _shared_patch_edge(
                previous_points, previous_colors, flag
            )
            new_points = _read_patch_values(
                reader, full_point_count - 4, coordinate_bits, decode, 0
            )
            new_colors = _read_patch_colors(
                reader, 2, component_bits, component_count, decode
            )
            if new_points is None or new_colors is None:
                return None
            points.extend(new_points)
            colors.extend(new_colors)
        else:
            return None
        if points is None or colors is None:
            return None
        reader.align()
        patch_count += 1
        budget.check(patch_count, "max_container_items", "mesh shading patches")
        grid = _coons_tensor(points) if shading_type == 6 else _tensor_grid(points)
        steps = _adaptive_patch_steps(
            grid,
            colors,
            device_scale,
            budget,
            len(triangles),
        )
        triangles.extend(_tessellate_patch(grid, colors, steps))
        previous_points = points
        previous_colors = colors
    if patch_count == 0:
        return None
    return _MeshShading(triangles, convert, function)


def _configure_shading(
    pdf: Any,
    mapping: dict[Any, Any],
    shading: Shading | None,
    convert,
    budget: _LoadBudget,
) -> Shading | None:
    if shading is None:
        return None
    bbox_values = _num_array(
        pdf,
        mapping.get(PdfName("BBox")),
        budget=budget,
        context="shading BBox items",
    )
    bbox = None
    if bbox_values is not None and len(bbox_values) >= 4:
        x0, y0, x1, y1 = bbox_values[:4]
        if all(math.isfinite(value) for value in (x0, y0, x1, y1)):
            bbox = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    background_values = _num_array(
        pdf,
        mapping.get(PdfName("Background")),
        budget=budget,
        context="shading Background items",
    )
    background = None
    if background_values and all(math.isfinite(value) for value in background_values):
        background = convert(background_values)
    return shading.configure(
        bbox=bbox,
        background=background,
        color_kind=_color_space_kind(pdf, mapping.get(PdfName("ColorSpace"))),
    )


def build_shading(
    pdf: Any,
    obj: Any,
    lut_size: int = 256,
    *,
    limits: PdfLoadLimits | None = None,
    budget: _LoadBudget | None = None,
    device_scale: float = 1.0,
) -> Shading | None:
    """Build a supported :class:`Shading` from a COS dictionary or stream."""
    load_budget = _resolve_budget(pdf, limits, budget)
    obj = pdf._resolve(obj)
    if not isinstance(obj, (PdfDictionary, PdfStream)):
        return None
    mapping = obj.mapping
    stype = _num(pdf, mapping.get(PdfName("ShadingType")))
    if stype is None:
        return None
    shading_type = int(stype)
    convert = _color_converter(
        pdf,
        mapping.get(PdfName("ColorSpace")),
        limits=load_budget.limits,
        budget=load_budget,
    )

    if shading_type == 1:
        domain_obj = mapping.get(PdfName("Domain"))
        if domain_obj is None:
            domain = [0.0, 1.0, 0.0, 1.0]
        else:
            domain = _num_array(
                pdf,
                domain_obj,
                budget=load_budget,
                context="function shading Domain items",
            )
            if domain is None or len(domain) < 4:
                return None
        if not all(math.isfinite(value) for value in domain[:4]):
            return None
        matrix_obj = mapping.get(PdfName("Matrix"))
        if matrix_obj is None:
            matrix: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        else:
            matrix_values = _num_array(
                pdf,
                matrix_obj,
                budget=load_budget,
                context="function shading Matrix items",
            )
            if matrix_values is None or len(matrix_values) < 6:
                return None
            matrix = tuple(matrix_values[:6])  # type: ignore[assignment]
        if not all(math.isfinite(value) for value in matrix):
            return None
        inverse_matrix = _invert_matrix(matrix)
        if inverse_matrix is None:
            return None
        function = build_function(
            pdf,
            mapping.get(PdfName("Function")),
            limits=load_budget.limits,
            budget=load_budget,
        )
        if function is None:
            return None
        return _configure_shading(
            pdf,
            mapping,
            _FunctionShading(function, convert, domain, inverse_matrix),
            convert,
            load_budget,
        )

    if shading_type in (4, 5, 6, 7):
        if not isinstance(obj, PdfStream):
            return None
        if shading_type in (4, 5):
            shading = _build_triangle_mesh(
                pdf,
                obj,
                shading_type,
                load_budget,
                convert,
            )
        else:
            shading = _build_patch_mesh(
                pdf,
                obj,
                shading_type,
                load_budget,
                device_scale,
                convert,
            )
        return _configure_shading(pdf, mapping, shading, convert, load_budget)
    if shading_type not in (2, 3):
        return None
    coords = _num_array(
        pdf,
        mapping.get(PdfName("Coords")),
        budget=load_budget,
        context="shading Coords items",
    )
    needed = 4 if shading_type == 2 else 6
    if not coords or len(coords) < needed:
        return None
    func = build_function(
        pdf,
        mapping.get(PdfName("Function")),
        limits=load_budget.limits,
        budget=load_budget,
    )
    if func is None:
        return None
    domain = _num_array(
        pdf,
        mapping.get(PdfName("Domain")),
        budget=load_budget,
        context="shading Domain items",
    ) or [0.0, 1.0]
    extend = _bool_array(
        pdf,
        mapping.get(PdfName("Extend")),
        budget=load_budget,
        context="shading Extend items",
    ) or [False, False]

    lut: list[Color] = []
    d_lo, d_hi = domain[0], domain[1]
    span = d_hi - d_lo
    for i in range(lut_size):
        t = d_lo + span * (i / (lut_size - 1))
        try:
            lut.append(convert(func.eval(t)))
        except (PdfParseException, ValueError, IndexError, ZeroDivisionError):
            lut.append((0, 0, 0))
    if shading_type == 2:
        shading = _AxialShading(coords, lut, extend)
    else:
        shading = _RadialShading(coords, lut, extend)
    return _configure_shading(pdf, mapping, shading, convert, load_budget)
