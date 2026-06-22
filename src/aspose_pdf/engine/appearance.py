"""Synthesise ``/AP /N`` appearance streams for standard annotation subtypes.

Given an annotation's subtype, ``Rect`` and type-specific properties (``C``,
``IC``, ``L``, ``Vertices``, ``InkList``, ``QuadPoints``, border width…), build
the content stream of a normal-appearance form XObject. Content is emitted in the
form's local coordinate space — origin at the ``Rect`` lower-left, spanning
``(0, 0)`` to ``(width, height)`` — to match the ``BBox [0 0 w h]`` produced by
``SimplePdf._register_annotation_appearance``.

The geometry properties (``L``/``Vertices``/``InkList``/``QuadPoints``) live in
default user space (absolute page coordinates), so each coordinate is translated
by ``-(llx, lly)`` into local space here.

This module is pure (no COS / engine imports) so it stays trivially testable; the
caller wraps the returned bytes in a form XObject and registers it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Subtypes this module can synthesise an appearance for.
SUPPORTED_SUBTYPES = frozenset(
    {
        "Square",
        "Circle",
        "Line",
        "Polygon",
        "PolyLine",
        "Ink",
        "Highlight",
        "Underline",
        "StrikeOut",
        "Squiggly",
    }
)

# Quarter-ellipse Bézier control-point constant (4/3 * (sqrt(2) - 1)).
_KAPPA = 0.5522847498307936


@dataclass
class GeneratedAppearance:
    """A synthesised appearance: content bytes plus any required ExtGState entries.

    *ext_gstates* maps a resource name to a small parameter dict (e.g.
    ``{"GsMul": {"BM": "Multiply"}}``); the caller materialises these into the
    form's ``/Resources /ExtGState``. It is empty for opaque shapes.
    """

    content: bytes
    ext_gstates: Dict[str, Dict[str, Any]] = field(default_factory=dict)


def _fmt(value: float) -> str:
    """Format a coordinate compactly (trim trailing zeros, avoid ``-0``)."""
    text = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return text if text and text != "-0" else "0"


def _as_floats(value: Any) -> Optional[List[float]]:
    """Coerce a sequence of numbers to a ``list[float]`` (or ``None``)."""
    if not isinstance(value, (list, tuple)) or not value:
        return None
    out: List[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return None
        out.append(float(item))
    return out


def _color_op(components: Any, *, stroke: bool) -> Optional[str]:
    """Return a colour-setting operator for 1/3/4-component colours, else ``None``."""
    comps = _as_floats(components)
    if not comps:
        return None
    vals = " ".join(_fmt(c) for c in comps)
    if len(comps) == 1:
        op = "G" if stroke else "g"
    elif len(comps) == 3:
        op = "RG" if stroke else "rg"
    elif len(comps) == 4:
        op = "K" if stroke else "k"
    else:
        return None
    return f"{vals} {op}"


def _border_width(properties: Dict[str, Any]) -> float:
    """Resolve the border width from ``/BS /W`` or the legacy ``/Border`` array."""
    bs = properties.get("BS")
    if isinstance(bs, dict):
        w = bs.get("W")
        if isinstance(w, (int, float)) and not isinstance(w, bool):
            return max(0.0, float(w))
    border = properties.get("Border")
    border_vals = _as_floats(border)
    if border_vals and len(border_vals) >= 3:
        return max(0.0, border_vals[2])
    return 1.0


def _local_points(
    flat: Sequence[float], llx: float, lly: float
) -> List[Tuple[float, float]]:
    """Convert a flat ``[x1 y1 x2 y2 …]`` list to local ``(x, y)`` tuples."""
    pts: List[Tuple[float, float]] = []
    for i in range(0, len(flat) - 1, 2):
        pts.append((flat[i] - llx, flat[i + 1] - lly))
    return pts


def _paint_op(has_fill: bool, has_stroke: bool) -> Optional[str]:
    if has_fill and has_stroke:
        return "B"
    if has_fill:
        return "f"
    if has_stroke:
        return "S"
    return None


def _polyline_path(points: Sequence[Tuple[float, float]]) -> str:
    """Emit ``m``/``l`` operators tracing *points* (no paint operator)."""
    if not points:
        return ""
    segs = [f"{_fmt(points[0][0])} {_fmt(points[0][1])} m"]
    for x, y in points[1:]:
        segs.append(f"{_fmt(x)} {_fmt(y)} l")
    return "\n".join(segs)


def build_appearance(
    subtype: str,
    rect: Tuple[float, float, float, float],
    properties: Dict[str, Any],
) -> Optional[GeneratedAppearance]:
    """Build a normal appearance for *subtype*, or ``None`` when not synthesisable.

    ``None`` is returned for subtypes outside :data:`SUPPORTED_SUBTYPES`, for a
    degenerate ``Rect``, or when a subtype's required geometry (e.g. a ``Line``'s
    ``L``) is missing.
    """
    if subtype not in SUPPORTED_SUBTYPES:
        return None

    llx, lly = float(rect[0]), float(rect[1])
    urx, ury = float(rect[2]), float(rect[3])
    width, height = urx - llx, ury - lly
    if width <= 0 or height <= 0:
        return None

    props = properties or {}
    builder = _BUILDERS[subtype]
    return builder(props, llx, lly, width, height)


# ---------------------------------------------------------------------------
# Per-subtype builders (all coordinates already translated to local space)
# ---------------------------------------------------------------------------


def _build_square(
    props: Dict[str, Any], llx: float, lly: float, w: float, h: float
) -> Optional[GeneratedAppearance]:
    bw = _border_width(props)
    stroke = _color_op(props.get("C"), stroke=True)
    fill = _color_op(props.get("IC"), stroke=False)
    has_stroke = bw > 0
    if stroke is None and has_stroke:
        stroke = "0 G"  # default to a black border so the shape is visible
    inset = bw / 2.0
    x, y = inset, inset
    rw, rh = w - bw, h - bw
    if rw <= 0 or rh <= 0:
        x, y, rw, rh = 0.0, 0.0, w, h
        has_stroke = False
    paint = _paint_op(fill is not None, has_stroke)
    if paint is None:
        return None
    lines = ["q"]
    if has_stroke and stroke:
        lines.append(stroke)
        lines.append(f"{_fmt(bw)} w")
    if fill:
        lines.append(fill)
    lines.append(f"{_fmt(x)} {_fmt(y)} {_fmt(rw)} {_fmt(rh)} re")
    lines.append(paint)
    lines.append("Q")
    return GeneratedAppearance(("\n".join(lines) + "\n").encode("ascii"))


def _build_circle(
    props: Dict[str, Any], llx: float, lly: float, w: float, h: float
) -> Optional[GeneratedAppearance]:
    bw = _border_width(props)
    stroke = _color_op(props.get("C"), stroke=True)
    fill = _color_op(props.get("IC"), stroke=False)
    has_stroke = bw > 0
    if stroke is None and has_stroke:
        stroke = "0 G"
    paint = _paint_op(fill is not None, has_stroke)
    if paint is None:
        return None
    inset = bw / 2.0
    rx, ry = (w - bw) / 2.0, (h - bw) / 2.0
    if rx <= 0 or ry <= 0:
        rx, ry, inset = w / 2.0, h / 2.0, 0.0
        has_stroke = False
        paint = _paint_op(fill is not None, has_stroke) or "f"
    cx, cy = inset + rx, inset + ry
    kx, ky = rx * _KAPPA, ry * _KAPPA
    lines = ["q"]
    if has_stroke and stroke:
        lines.append(stroke)
        lines.append(f"{_fmt(bw)} w")
    if fill:
        lines.append(fill)
    # Four cubic Béziers, counter-clockwise from the right vertex.
    lines.append(f"{_fmt(cx + rx)} {_fmt(cy)} m")
    lines.append(
        f"{_fmt(cx + rx)} {_fmt(cy + ky)} {_fmt(cx + kx)} {_fmt(cy + ry)} "
        f"{_fmt(cx)} {_fmt(cy + ry)} c"
    )
    lines.append(
        f"{_fmt(cx - kx)} {_fmt(cy + ry)} {_fmt(cx - rx)} {_fmt(cy + ky)} "
        f"{_fmt(cx - rx)} {_fmt(cy)} c"
    )
    lines.append(
        f"{_fmt(cx - rx)} {_fmt(cy - ky)} {_fmt(cx - kx)} {_fmt(cy - ry)} "
        f"{_fmt(cx)} {_fmt(cy - ry)} c"
    )
    lines.append(
        f"{_fmt(cx + kx)} {_fmt(cy - ry)} {_fmt(cx + rx)} {_fmt(cy - ky)} "
        f"{_fmt(cx + rx)} {_fmt(cy)} c"
    )
    lines.append(paint)
    lines.append("Q")
    return GeneratedAppearance(("\n".join(lines) + "\n").encode("ascii"))


def _build_line(
    props: Dict[str, Any], llx: float, lly: float, w: float, h: float
) -> Optional[GeneratedAppearance]:
    coords = _as_floats(props.get("L"))
    if not coords or len(coords) < 4:
        return None
    pts = _local_points(coords[:4], llx, lly)
    bw = max(_border_width(props), 0.0) or 1.0
    stroke = _color_op(props.get("C"), stroke=True) or "0 G"
    lines = [
        "q",
        stroke,
        f"{_fmt(bw)} w",
        f"{_fmt(pts[0][0])} {_fmt(pts[0][1])} m",
        f"{_fmt(pts[1][0])} {_fmt(pts[1][1])} l",
        "S",
        "Q",
    ]
    return GeneratedAppearance(("\n".join(lines) + "\n").encode("ascii"))


def _build_polygon(
    props: Dict[str, Any], llx: float, lly: float, w: float, h: float
) -> Optional[GeneratedAppearance]:
    return _build_poly(props, llx, lly, closed=True)


def _build_polyline(
    props: Dict[str, Any], llx: float, lly: float, w: float, h: float
) -> Optional[GeneratedAppearance]:
    return _build_poly(props, llx, lly, closed=False)


def _build_poly(
    props: Dict[str, Any], llx: float, lly: float, *, closed: bool
) -> Optional[GeneratedAppearance]:
    verts = _as_floats(props.get("Vertices"))
    if not verts or len(verts) < 4:
        return None
    pts = _local_points(verts, llx, lly)
    bw = max(_border_width(props), 0.0) or 1.0
    stroke = _color_op(props.get("C"), stroke=True) or "0 G"
    fill = _color_op(props.get("IC"), stroke=False) if closed else None
    paint = _paint_op(fill is not None, True)
    lines = ["q", stroke, f"{_fmt(bw)} w"]
    if fill:
        lines.append(fill)
    lines.append(_polyline_path(pts))
    if closed:
        lines.append("h")
    lines.append(paint or "S")
    lines.append("Q")
    return GeneratedAppearance(("\n".join(lines) + "\n").encode("ascii"))


def _build_ink(
    props: Dict[str, Any], llx: float, lly: float, w: float, h: float
) -> Optional[GeneratedAppearance]:
    ink_list = props.get("InkList")
    if not isinstance(ink_list, (list, tuple)) or not ink_list:
        return None
    bw = max(_border_width(props), 0.0) or 1.0
    stroke = _color_op(props.get("C"), stroke=True) or "0 G"
    lines = ["q", stroke, f"{_fmt(bw)} w", "1 J", "1 j"]  # round caps/joins
    drew = False
    for path in ink_list:
        coords = _as_floats(path)
        if not coords or len(coords) < 4:
            continue
        pts = _local_points(coords, llx, lly)
        lines.append(_polyline_path(pts))
        lines.append("S")
        drew = True
    if not drew:
        return None
    lines.append("Q")
    return GeneratedAppearance(("\n".join(lines) + "\n").encode("ascii"))


def _quads(props: Dict[str, Any], llx: float, lly: float) -> List[List[Tuple[float, float]]]:
    """Split ``QuadPoints`` into a list of 4-corner quads (local coordinates)."""
    flat = _as_floats(props.get("QuadPoints"))
    if not flat or len(flat) < 8:
        return []
    quads: List[List[Tuple[float, float]]] = []
    for i in range(0, len(flat) - 7, 8):
        quads.append(_local_points(flat[i : i + 8], llx, lly))
    return quads


def _build_text_markup(
    props: Dict[str, Any], llx: float, lly: float, kind: str
) -> Optional[GeneratedAppearance]:
    quads = _quads(props, llx, lly)
    if not quads:
        return None
    stroke = _color_op(props.get("C"), stroke=True) or "0 G"
    lines = ["q", stroke]
    drew = False
    for quad in quads:
        xs = [p[0] for p in quad]
        ys = [p[1] for p in quad]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        qh = y1 - y0
        if x1 <= x0 or qh <= 0:
            continue
        lw = max(0.75, qh * 0.05)
        if kind == "StrikeOut":
            y = (y0 + y1) / 2.0
        else:  # Underline / Squiggly sit near the baseline
            y = y0 + qh * 0.10
        if kind == "Squiggly":
            lines.append(f"{_fmt(lw)} w")
            lines.append(_squiggle_path(x0, x1, y, qh * 0.12))
            lines.append("S")
        else:
            lines.append(f"{_fmt(lw)} w")
            lines.append(f"{_fmt(x0)} {_fmt(y)} m {_fmt(x1)} {_fmt(y)} l")
            lines.append("S")
        drew = True
    if not drew:
        return None
    lines.append("Q")
    return GeneratedAppearance(("\n".join(lines) + "\n").encode("ascii"))


def _squiggle_path(x0: float, x1: float, y: float, amp: float) -> str:
    """A zig-zag polyline from *x0* to *x1* centred on *y* with amplitude *amp*."""
    amp = max(amp, 0.5)
    step = amp * 2.0
    segs = [f"{_fmt(x0)} {_fmt(y)} m"]
    x = x0
    up = True
    while x < x1:
        x = min(x + step, x1)
        segs.append(f"{_fmt(x)} {_fmt(y + amp if up else y - amp)} l")
        up = not up
    return "\n".join(segs)


def _build_highlight(
    props: Dict[str, Any], llx: float, lly: float, w: float, h: float
) -> Optional[GeneratedAppearance]:
    quads = _quads(props, llx, lly)
    if not quads:
        return None
    # Default to yellow; multiply blend keeps the underlying text legible.
    fill = _color_op(props.get("C"), stroke=False) or "1 1 0 rg"
    lines = ["q", "/GsMul gs", fill]
    drew = False
    for quad in quads:
        xs = [p[0] for p in quad]
        ys = [p[1] for p in quad]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        if x1 <= x0 or y1 <= y0:
            continue
        lines.append(f"{_fmt(x0)} {_fmt(y0)} {_fmt(x1 - x0)} {_fmt(y1 - y0)} re")
        lines.append("f")
        drew = True
    if not drew:
        return None
    lines.append("Q")
    return GeneratedAppearance(
        ("\n".join(lines) + "\n").encode("ascii"),
        ext_gstates={"GsMul": {"BM": "Multiply"}},
    )


_BUILDERS = {
    "Square": _build_square,
    "Circle": _build_circle,
    "Line": _build_line,
    "Polygon": _build_polygon,
    "PolyLine": _build_polyline,
    "Ink": _build_ink,
    "Highlight": _build_highlight,
    "Underline": lambda p, x, y, w, h: _build_text_markup(p, x, y, "Underline"),
    "StrikeOut": lambda p, x, y, w, h: _build_text_markup(p, x, y, "StrikeOut"),
    "Squiggly": lambda p, x, y, w, h: _build_text_markup(p, x, y, "Squiggly"),
}
