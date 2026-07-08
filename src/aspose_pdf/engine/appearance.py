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

This module is pure (no COS imports) so it stays trivially testable; the
caller wraps the returned bytes in a form XObject and registers it. Text is
measured with the bundled Helvetica-compatible substitute's glyph metrics
(``text_metrics.py``), degrading to a flat estimate if the bundle is missing.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .field_appearance import (
    WidthFn,
    _pdf_literal,
    _quad_x,
    _text_width,
    _wrap_text,
    auto_font_size,
    parse_default_appearance,
)

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
        "FreeText",
        "Stamp",
        "Caret",
    }
)

# Quarter-ellipse Bézier control-point constant (4/3 * (sqrt(2) - 1)).
_KAPPA = 0.5522847498307936

# Resource name and Type1 program for the synthesised annotation text font.
_ANNOT_FONT_NAME = "Helv"
_ANNOT_FONT_SPEC = {
    "Subtype": "Type1",
    "BaseFont": "Helvetica",
    "Encoding": "WinAnsiEncoding",
}


def _annot_width_fn() -> Optional[WidthFn]:
    """Glyph metrics for the synthesised Helvetica (cached by ``text_metrics``)."""
    from .text_metrics import substitute_width_fn

    return substitute_width_fn(_ANNOT_FONT_SPEC["BaseFont"])


@dataclass
class GeneratedAppearance:
    """A synthesised appearance: content bytes plus any required ExtGState entries.

    *ext_gstates* maps a resource name to a small parameter dict (e.g.
    ``{"GsMul": {"BM": "Multiply"}}``); the caller materialises these into the
    form's ``/Resources /ExtGState``. It is empty for opaque shapes.

    *fonts* maps a resource name to a simple Type1 font spec (e.g.
    ``{"Helv": {"Subtype": "Type1", "BaseFont": "Helvetica"}}``) for text-bearing
    subtypes; the caller materialises these into ``/Resources /Font``. It is
    empty for shape-only appearances.
    """

    content: bytes
    ext_gstates: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    fonts: Dict[str, Dict[str, Any]] = field(default_factory=dict)


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


def _name(value: Any) -> str:
    """Coerce a PDF name-ish value (``AnnotationName``/str) to its bare string."""
    return str(value).lstrip("/") if value is not None else ""


# ---------------------------------------------------------------------------
# Border decorations: dash patterns, line endings, and cloud (/BE) borders
# ---------------------------------------------------------------------------


def _dash_array(props: Dict[str, Any]) -> Optional[List[float]]:
    """Resolve a dash pattern from ``/BS`` (style ``D``) or a legacy ``/Border``."""
    bs = props.get("BS")
    if isinstance(bs, dict) and _name(bs.get("S")) == "D":
        dash = _as_floats(bs.get("D"))
        if dash and any(v > 0 for v in dash):
            return [max(0.0, v) for v in dash]
        return [3.0]  # /S /D with no explicit array: a sensible default dash
    border = props.get("Border")
    if isinstance(border, (list, tuple)) and len(border) >= 4:
        dash = _as_floats(border[3])
        if dash and any(v > 0 for v in dash):
            return [max(0.0, v) for v in dash]
    return None


def _dash_op(props: Dict[str, Any]) -> Optional[str]:
    """Return a ``d`` dash operator for the annotation's border, or ``None``."""
    dash = _dash_array(props)
    if not dash:
        return None
    return f"[{' '.join(_fmt(v) for v in dash)}] 0 d"


def _stroke_setup(
    stroke: str, bw: float, props: Dict[str, Any]
) -> List[str]:
    """Stroke colour, width and (optional) dash operators, in order."""
    ops = [stroke, f"{_fmt(bw)} w"]
    dash = _dash_op(props)
    if dash:
        ops.append(dash)
    return ops


def _unit(dx: float, dy: float) -> Optional[Tuple[float, float]]:
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return None
    return (dx / length, dy / length)


def _rot(v: Tuple[float, float], angle: float) -> Tuple[float, float]:
    ca, sa = math.cos(angle), math.sin(angle)
    return (v[0] * ca - v[1] * sa, v[0] * sa + v[1] * ca)


# Line-ending styles this module can draw (others degrade to no decoration).
_LINE_ENDINGS = frozenset(
    {
        "OpenArrow", "ClosedArrow", "ROpenArrow", "RClosedArrow",
        "Circle", "Square", "Diamond", "Butt", "Slash",
    }
)


def _line_ending_styles(props: Dict[str, Any]) -> Tuple[str, str]:
    """Return ``(start, end)`` line-ending style names from ``/LE``."""
    le = props.get("LE")
    if isinstance(le, (list, tuple)) and le:
        start = _name(le[0])
        end = _name(le[1]) if len(le) >= 2 else "None"
        return start, end
    if le is not None:  # a bare name applies to the line's end
        return "None", _name(le)
    return "None", "None"


def _ending_ops(
    end: Tuple[float, float],
    outward: Tuple[float, float],
    style: str,
    size: float,
    fill_op: str,
) -> List[str]:
    """Draw a *style* line ending at *end*, opening along *outward* (unit)."""
    if style not in _LINE_ENDINGS or size <= 0:
        return []
    ex, ey = end
    ox, oy = outward
    px, py = -oy, ox  # perpendicular
    half = size / 2.0

    def pt(along: float, across: float) -> str:
        return f"{_fmt(ex + ox * along + px * across)} {_fmt(ey + oy * along + py * across)}"

    if style in ("OpenArrow", "ClosedArrow", "ROpenArrow", "RClosedArrow"):
        # Reversed arrows point back along the line instead of outward.
        inward = (-ox, -oy) if style in ("OpenArrow", "ClosedArrow") else (ox, oy)
        theta = math.radians(30.0)
        w1 = _rot(inward, theta)
        w2 = _rot(inward, -theta)
        p1 = f"{_fmt(ex + w1[0] * size)} {_fmt(ey + w1[1] * size)}"
        p2 = f"{_fmt(ex + w2[0] * size)} {_fmt(ey + w2[1] * size)}"
        tip = f"{_fmt(ex)} {_fmt(ey)}"
        if style in ("OpenArrow", "ROpenArrow"):
            return [f"{p1} m", f"{tip} l", f"{p2} l", "S"]
        return [fill_op, f"{tip} m", f"{p1} l", f"{p2} l", "h", "b"]
    if style == "Butt":
        return [f"{pt(0.0, half)} m", f"{pt(0.0, -half)} l", "S"]
    if style == "Slash":
        d = _rot(outward, math.radians(60.0))
        a = f"{_fmt(ex + d[0] * half)} {_fmt(ey + d[1] * half)}"
        b = f"{_fmt(ex - d[0] * half)} {_fmt(ey - d[1] * half)}"
        return [f"{a} m", f"{b} l", "S"]
    if style == "Square":
        return [
            fill_op,
            f"{_fmt(ex - half)} {_fmt(ey - half)} {_fmt(size)} {_fmt(size)} re",
            "b",
        ]
    if style == "Diamond":
        return [
            fill_op,
            f"{pt(half, 0.0)} m",
            f"{pt(0.0, half)} l",
            f"{pt(-half, 0.0)} l",
            f"{pt(0.0, -half)} l",
            "h",
            "b",
        ]
    # Circle: four Béziers about *end*.
    r = half
    k = r * _KAPPA
    return [
        fill_op,
        f"{_fmt(ex + r)} {_fmt(ey)} m",
        f"{_fmt(ex + r)} {_fmt(ey + k)} {_fmt(ex + k)} {_fmt(ey + r)} {_fmt(ex)} {_fmt(ey + r)} c",
        f"{_fmt(ex - k)} {_fmt(ey + r)} {_fmt(ex - r)} {_fmt(ey + k)} {_fmt(ex - r)} {_fmt(ey)} c",
        f"{_fmt(ex - r)} {_fmt(ey - k)} {_fmt(ex - k)} {_fmt(ey - r)} {_fmt(ex)} {_fmt(ey - r)} c",
        f"{_fmt(ex + k)} {_fmt(ey - r)} {_fmt(ex + r)} {_fmt(ey - k)} {_fmt(ex + r)} {_fmt(ey)} c",
        "b",
    ]


def _line_ending_size(bw: float, span: float) -> float:
    """A line-ending marker size that scales with width but fits the segment."""
    return min(max(bw * 4.0, 8.0), span * 0.4)


def _draw_endings(
    pts: Sequence[Tuple[float, float]],
    styles: Tuple[str, str],
    bw: float,
    fill_op: str,
) -> List[str]:
    """Ops for the start/end line endings of a (poly)line, oriented outward.

    The start ending points back along the first edge and the end ending along
    the last edge; each marker's size scales with the border width but is capped
    to its edge length, so short segments do not overshoot.
    """
    if len(pts) < 2:
        return []
    start_style, end_style = styles
    ops: List[str] = []
    if start_style != "None":
        first_len = math.hypot(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1])
        out = _unit(pts[0][0] - pts[1][0], pts[0][1] - pts[1][1])
        if out is not None:
            ops += _ending_ops(
                pts[0], out, start_style, _line_ending_size(bw, first_len), fill_op
            )
    if end_style != "None":
        last_len = math.hypot(pts[-1][0] - pts[-2][0], pts[-1][1] - pts[-2][1])
        out = _unit(pts[-1][0] - pts[-2][0], pts[-1][1] - pts[-2][1])
        if out is not None:
            ops += _ending_ops(
                pts[-1], out, end_style, _line_ending_size(bw, last_len), fill_op
            )
    return ops


def _cloud_intensity(props: Dict[str, Any]) -> float:
    """Return the ``/BE`` cloud intensity (``>0`` enables a cloudy border)."""
    be = props.get("BE")
    if not isinstance(be, dict) or _name(be.get("S")) != "C":
        return 0.0
    i = be.get("I")
    intensity = float(i) if isinstance(i, (int, float)) and not isinstance(i, bool) else 1.0
    return max(0.0, intensity)


def _cloud_bulge(intensity: float) -> float:
    """Half the scallop diameter for a cloud of *intensity* (1 or 2)."""
    return (8.0 + 6.0 * max(1.0, min(intensity, 2.0))) / 2.0


def _cloud_path(points: Sequence[Tuple[float, float]], intensity: float) -> Optional[str]:
    """Trace a closed cloud (outward convex scallops) around *points*."""
    if len(points) < 3:
        return None
    bulge = _cloud_bulge(intensity)
    diameter = bulge * 2.0
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    segs: List[str] = []
    started = False
    n = len(points)
    for i in range(n):
        ax, ay = points[i]
        bx, by = points[(i + 1) % n]
        edge = _unit(bx - ax, by - ay)
        elen = math.hypot(bx - ax, by - ay)
        if edge is None or elen < 1e-6:
            continue
        ux, uy = edge
        px, py = -uy, ux  # perpendicular; flip to point away from the centroid
        midx, midy = (ax + bx) / 2.0 - cx, (ay + by) / 2.0 - cy
        if px * midx + py * midy < 0:
            px, py = -px, -py
        steps = max(1, round(elen / diameter))
        seg = elen / steps
        for s in range(steps):
            sx, sy = ax + ux * seg * s, ay + uy * seg * s
            ex, ey = ax + ux * seg * (s + 1), ay + uy * seg * (s + 1)
            if not started:
                segs.append(f"{_fmt(sx)} {_fmt(sy)} m")
                started = True
            c1x = sx + ux * seg * 0.25 + px * bulge
            c1y = sy + uy * seg * 0.25 + py * bulge
            c2x = ex - ux * seg * 0.25 + px * bulge
            c2y = ey - uy * seg * 0.25 + py * bulge
            segs.append(
                f"{_fmt(c1x)} {_fmt(c1y)} {_fmt(c2x)} {_fmt(c2y)} {_fmt(ex)} {_fmt(ey)} c"
            )
    if not segs:
        return None
    segs.append("h")
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

    # A /BE cloud border replaces the straight edges with outward scallops.
    intensity = _cloud_intensity(props)
    if intensity > 0 and has_stroke and stroke:
        inset = bw / 2.0 + _cloud_bulge(intensity)
        rw, rh = w - 2.0 * inset, h - 2.0 * inset
        if rw > 0 and rh > 0:
            corners = [
                (inset, inset), (inset + rw, inset),
                (inset + rw, inset + rh), (inset, inset + rh),
            ]
            path = _cloud_path(corners, intensity)
            if path:
                lines = ["q", *_stroke_setup(stroke, bw, props)]
                if fill:
                    lines.append(fill)
                lines.append(path)
                lines.append(_paint_op(fill is not None, True) or "S")
                lines.append("Q")
                return GeneratedAppearance(("\n".join(lines) + "\n").encode("ascii"))

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
        lines += _stroke_setup(stroke, bw, props)
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
        lines += _stroke_setup(stroke, bw, props)
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
    fill_op = _color_op(props.get("IC"), stroke=False) or _color_op(
        props.get("C"), stroke=False
    ) or "0 g"
    lines = ["q", *_stroke_setup(stroke, bw, props)]
    lines.append(f"{_fmt(pts[0][0])} {_fmt(pts[0][1])} m")
    lines.append(f"{_fmt(pts[1][0])} {_fmt(pts[1][1])} l")
    lines.append("S")
    lines += _draw_endings(pts, _line_ending_styles(props), bw, fill_op)
    lines.append("Q")
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
    lines = ["q", *_stroke_setup(stroke, bw, props)]
    if fill:
        lines.append(fill)

    # A closed polygon with a /BE cloud border draws scalloped edges.
    intensity = _cloud_intensity(props) if closed else 0.0
    cloud = _cloud_path(pts, intensity) if intensity > 0 else None
    if cloud is not None:
        lines.append(cloud)
        lines.append(paint or "S")
    else:
        lines.append(_polyline_path(pts))
        if closed:
            lines.append("h")
        lines.append(paint or "S")
        if not closed:  # PolyLine: draw its /LE start/end line endings
            fill_op = _color_op(props.get("IC"), stroke=False) or _color_op(
                props.get("C"), stroke=False
            ) or "0 g"
            lines += _draw_endings(pts, _line_ending_styles(props), bw, fill_op)
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


def _quads(
    props: Dict[str, Any], llx: float, lly: float
) -> List[List[Tuple[float, float]]]:
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


# ---------------------------------------------------------------------------
# Text-bearing subtypes (FreeText, Stamp) and the Caret marker
# ---------------------------------------------------------------------------


def _str_prop(value: Any) -> str:
    """Coerce an annotation property to display text (``""`` when absent)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _text_block(
    text: str,
    w: float,
    h: float,
    *,
    font_size: float,
    color_op: str,
    quadding: int,
    padding: float,
) -> List[str]:
    """Emit a ``BT``…``ET`` word-wrapped text block filling ``(w, h)`` from the top."""
    fs = font_size if font_size > 0 else auto_font_size(h, multiline=True)
    leading = fs * 1.15
    width_fn = _annot_width_fn()
    lines = _wrap_text(text, w - 2.0 * padding, fs, width_fn)
    body = ["BT", f"/{_ANNOT_FONT_NAME} {_fmt(fs)} Tf", color_op]
    ly = h - padding - fs
    for line in lines:
        tx = _quad_x(line, w, fs, quadding, padding, width_fn)
        body.append(f"1 0 0 1 {_fmt(tx)} {_fmt(ly)} Tm")
        body.append(f"{_pdf_literal(line)} Tj")
        ly -= leading
    body.append("ET")
    return body


def _build_freetext(
    props: Dict[str, Any], llx: float, lly: float, w: float, h: float
) -> Optional[GeneratedAppearance]:
    text = _str_prop(props.get("Contents"))
    da = props.get("DA")
    _fn, size, color = parse_default_appearance(da if isinstance(da, str) else None)
    q_raw = props.get("Q")
    quadding = (
        int(q_raw) if isinstance(q_raw, (int, float)) and int(q_raw) in (0, 1, 2) else 0
    )
    bw = _border_width(props)
    background = _color_op(props.get("C"), stroke=False)

    lines = ["q"]
    if background:  # /C is the FreeText box background colour
        lines.append(background)
        lines.append(f"0 0 {_fmt(w)} {_fmt(h)} re")
        lines.append("f")
    if bw > 0:
        inset = bw / 2.0
        rw, rh = w - bw, h - bw
        if rw > 0 and rh > 0:
            lines += _stroke_setup("0 G", bw, props)
            lines.append(f"{_fmt(inset)} {_fmt(inset)} {_fmt(rw)} {_fmt(rh)} re")
            lines.append("S")
    fonts: Dict[str, Dict[str, Any]] = {}
    pad = max(2.0, bw + 1.0)
    # Prefer the /RC rich text (styled spans) when present; fall back to the
    # plain /Contents rendered in the /DA font.
    rich = _rich_text_block(props.get("RC"), w, h, size, color, quadding, pad)
    if rich is not None:
        rich_body, fonts = rich
        lines += rich_body
    elif text:
        lines += _text_block(
            text, w, h, font_size=size, color_op=color, quadding=quadding, padding=pad
        )
        fonts = {_ANNOT_FONT_NAME: dict(_ANNOT_FONT_SPEC)}
    lines.append("Q")
    return GeneratedAppearance(
        ("\n".join(lines) + "\n").encode("latin-1", "replace"), fonts=fonts
    )


def _rich_text_block(
    rc: Any,
    w: float,
    h: float,
    size: float,
    color: str,
    quadding: int,
    padding: float,
) -> Optional[Tuple[List[str], Dict[str, Dict[str, Any]]]]:
    """Lay out ``/RC``/``/RV`` rich text, or ``None`` when absent/empty."""
    if not isinstance(rc, str) or not rc.strip():
        return None
    from .rich_text import RichStyle, build_rich_text_content

    default = RichStyle(size=size if size > 0 else 12.0, color=color or "0 g")
    return build_rich_text_content(
        rc, w, h, default_style=default, padding=padding, default_align=quadding
    )


def _stamp_label(props: Dict[str, Any]) -> str:
    """Derive a stamp caption from ``/Name`` (camel-split) or ``/Contents``."""
    name = props.get("Name")
    text = _str_prop(name).strip()
    if text:
        # "NotApproved" / "SBApproved" -> "NOT APPROVED" / "SB APPROVED".
        spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
        return spaced.upper()
    contents = _str_prop(props.get("Contents")).strip()
    if contents:
        return contents.splitlines()[0]
    return "STAMP"


def _build_stamp(
    props: Dict[str, Any], llx: float, lly: float, w: float, h: float
) -> Optional[GeneratedAppearance]:
    label = _stamp_label(props)
    comps = _as_floats(props.get("C"))
    stroke = _color_op(comps, stroke=True) or "1 0 0 RG"  # rubber-stamp red
    fill = _color_op(comps, stroke=False) or "1 0 0 rg"
    bw = max(_border_width(props), 1.0)

    inset = bw / 2.0
    rw, rh = w - bw, h - bw
    lines = ["q", stroke, f"{_fmt(bw)} w"]
    if rw > 0 and rh > 0:
        lines.append(f"{_fmt(inset)} {_fmt(inset)} {_fmt(rw)} {_fmt(rh)} re")
        lines.append("S")

    # Single centred caption sized to fit the box.
    pad = max(4.0, bw * 2.0)
    width_fn = _annot_width_fn()
    unit_w = _text_width(label, 1.0, width_fn)  # caption width per point of size
    fs_w = max(1.0, w - 2.0 * pad) / max(unit_w, 1e-6)
    fs = max(4.0, min(h * 0.5, fs_w))
    tw = _text_width(label, fs, width_fn)
    tx = max(pad, (w - tw) / 2.0)
    ty = (h - fs) / 2.0 + fs * 0.25
    lines += [
        "BT",
        f"/{_ANNOT_FONT_NAME} {_fmt(fs)} Tf",
        fill,
        f"1 0 0 1 {_fmt(tx)} {_fmt(ty)} Tm",
        f"{_pdf_literal(label)} Tj",
        "ET",
        "Q",
    ]
    return GeneratedAppearance(
        ("\n".join(lines) + "\n").encode("latin-1", "replace"),
        fonts={_ANNOT_FONT_NAME: dict(_ANNOT_FONT_SPEC)},
    )


# ---------------------------------------------------------------------------
# Button widgets (check box / radio) — synthesised /AP /N state appearances
# ---------------------------------------------------------------------------

# ZapfDingbats is a Standard-14 font; "4" is its check mark. Radio buttons draw
# a vector dot instead, so they need no font resource.
_ZAPF_FONT_NAME = "ZaDb"
_ZAPF_FONT_SPEC = {"Subtype": "Type1", "BaseFont": "ZapfDingbats"}
_DEFAULT_CHECK = "4"


def _ellipse_path(cx: float, cy: float, rx: float, ry: float) -> List[str]:
    """Four cubic Béziers tracing an ellipse centred at ``(cx, cy)``."""
    kx, ky = rx * _KAPPA, ry * _KAPPA
    return [
        f"{_fmt(cx + rx)} {_fmt(cy)} m",
        f"{_fmt(cx + rx)} {_fmt(cy + ky)} {_fmt(cx + kx)} {_fmt(cy + ry)} {_fmt(cx)} {_fmt(cy + ry)} c",
        f"{_fmt(cx - kx)} {_fmt(cy + ry)} {_fmt(cx - rx)} {_fmt(cy + ky)} {_fmt(cx - rx)} {_fmt(cy)} c",
        f"{_fmt(cx - rx)} {_fmt(cy - ky)} {_fmt(cx - kx)} {_fmt(cy - ry)} {_fmt(cx)} {_fmt(cy - ry)} c",
        f"{_fmt(cx + kx)} {_fmt(cy - ry)} {_fmt(cx + rx)} {_fmt(cy - ky)} {_fmt(cx + rx)} {_fmt(cy)} c",
    ]


def build_button_appearance(
    w: float,
    h: float,
    *,
    on: bool,
    radio: bool,
    caption: Optional[str] = None,
    border_color: Optional[Any] = None,
    bg_color: Optional[Any] = None,
    border_width: float = 1.0,
) -> GeneratedAppearance:
    """Build one check box / radio widget state (``/AP /N`` Off or On).

    Draws the ``/MK`` background (``/BG``) and border (``/BC``) — a rectangle for
    a check box, a circle for a radio — and, for the *on* state, the "checked"
    mark: a ZapfDingbats caption glyph (``/MK /CA``, default ``4``) for a check
    box or a filled vector dot for a radio button. The Off state is background
    and border only.
    """
    if w <= 0 or h <= 0:
        return GeneratedAppearance(b"")
    bw = max(float(border_width), 0.0)
    lines = ["q"]
    fonts: Dict[str, Dict[str, Any]] = {}

    bg = _color_op(bg_color, stroke=False)
    if bg:
        lines.append(bg)
        if radio:
            lines += _ellipse_path(w / 2.0, h / 2.0, w / 2.0, h / 2.0)
            lines.append("f")
        else:
            lines.append(f"0 0 {_fmt(w)} {_fmt(h)} re")
            lines.append("f")

    bc = _color_op(border_color, stroke=True)
    if bc and bw > 0:
        inset = bw / 2.0
        lines.append(bc)
        lines.append(f"{_fmt(bw)} w")
        if radio:
            lines += _ellipse_path(w / 2.0, h / 2.0, w / 2.0 - inset, h / 2.0 - inset)
            lines.append("S")
        else:
            rw, rh = w - bw, h - bw
            if rw > 0 and rh > 0:
                lines.append(f"{_fmt(inset)} {_fmt(inset)} {_fmt(rw)} {_fmt(rh)} re")
                lines.append("S")

    if on:
        mark = _color_op(border_color, stroke=False) or "0 g"
        if radio:
            r = min(w, h) * 0.3
            lines.append(mark)
            lines += _ellipse_path(w / 2.0, h / 2.0, r, r)
            lines.append("f")
        else:
            glyph = (caption or _DEFAULT_CHECK)[:1] or _DEFAULT_CHECK
            fs = min(w, h) * 0.8
            tx = (w - fs * 0.78) / 2.0
            ty = (h - fs * 0.70) / 2.0
            lines += [
                mark,
                "BT",
                f"/{_ZAPF_FONT_NAME} {_fmt(fs)} Tf",
                f"1 0 0 1 {_fmt(tx)} {_fmt(ty)} Tm",
                f"{_pdf_literal(glyph)} Tj",
                "ET",
            ]
            fonts = {_ZAPF_FONT_NAME: dict(_ZAPF_FONT_SPEC)}
    lines.append("Q")
    return GeneratedAppearance(
        ("\n".join(lines) + "\n").encode("latin-1", "replace"), fonts=fonts
    )


def _build_caret(
    props: Dict[str, Any], llx: float, lly: float, w: float, h: float
) -> Optional[GeneratedAppearance]:
    fill = _color_op(props.get("C"), stroke=False) or "0 g"
    # An upward-pointing filled triangle marks the insertion point.
    ix, iy = w * 0.15, h * 0.10
    x0, x1 = ix, w - ix
    y0, y1 = iy, h - iy
    if x1 <= x0 or y1 <= y0:
        x0, y0, x1, y1 = 0.0, 0.0, w, h
    cx = (x0 + x1) / 2.0
    lines = [
        "q",
        fill,
        f"{_fmt(x0)} {_fmt(y0)} m",
        f"{_fmt(cx)} {_fmt(y1)} l",
        f"{_fmt(x1)} {_fmt(y0)} l",
        "h",
        "f",
        "Q",
    ]
    return GeneratedAppearance(("\n".join(lines) + "\n").encode("ascii"))


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
    "FreeText": _build_freetext,
    "Stamp": _build_stamp,
    "Caret": _build_caret,
}
