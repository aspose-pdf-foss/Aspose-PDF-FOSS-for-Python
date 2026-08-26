"""Export a page as SVG by retargeting the rasterizer's interpreter.

The renderer already walks a content stream correctly: graphics state, the
clipping path, form XObjects, optional content, colour spaces, fonts and glyph
outlines, images, shadings and annotation appearances. Re-implementing that
walk to emit vectors would mean maintaining a second interpreter and a second
set of bugs.

So this module *subclasses* the rasterizer and replaces only the handful of
places where it puts marks on a canvas. Everything above those sinks -- every
operator, every transform, every resource lookup -- is the same code that
renders the page, which is what keeps the two outputs agreeing.

The page is interpreted at 72 dpi with no supersampling, so the rasterizer's
device pixels *are* SVG user units: one unit per point, y already flipped and
the page rotation and crop box already applied. What arrives at a sink is a
polygon in that space, ready to be written out as path data.

What the vector backend cannot express it draws instead of dropping: a shading
that is neither axial nor radial is sampled into an embedded image, and a
soft-masked image becomes an SVG ``<mask>``. Curves are flattened before they
reach a sink -- the rasterizer flattens them while building the path -- so
output is polylines, not Béziers.
"""

from __future__ import annotations

import base64
import math
from typing import Any

from aspose_pdf.exceptions import PDF_OPERATION_ERRORS, PdfResourceLimitException
from aspose_pdf.load_limits import PdfLoadLimits

from .image_export import write_png
from .rasterizer import (
    Color,
    Matrix,
    Point,
    _invert_matrix,
    _multiply,
    _PageRasterizer,
    _transform_point,
)
from .shading import Shading

__all__ = ["page_to_svg"]

# Sampling grid for a shading SVG cannot express as a gradient.
_SHADING_SAMPLE_LIMIT = 512
# Stops emitted for an axial or radial gradient; the shading's own lookup
# table is usually 256 entries, and this many reproduces it invisibly.
_GRADIENT_STOPS = 64


def _fmt(value: float, precision: int = 3) -> str:
    """Format a coordinate compactly: no trailing zeros, no exponent."""
    if not math.isfinite(value):
        return "0"
    text = f"{value:.{precision}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _rgb(color: Color) -> str:
    return f"#{color[0] & 0xFF:02x}{color[1] & 0xFF:02x}{color[2] & 0xFF:02x}"


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


class _SvgWriter(_PageRasterizer):
    """A rasterizer whose paint sinks write SVG elements."""

    def __init__(
        self,
        pdf: Any,
        page_index: int,
        *,
        background: tuple[int, int, int] | None = (255, 255, 255),
        draw_annotations: bool = True,
        font_substitution: Any = None,
        precision: int = 3,
    ) -> None:
        super().__init__(
            pdf,
            page_index,
            dpi=72.0,
            scale=1.0,
            background=background or (255, 255, 255),
            antialias=False,
            draw_annotations=draw_annotations,
            font_substitution=font_substitution,
        )
        self._svg_background = background
        self._precision = max(0, min(9, int(precision)))
        self._body: list[str] = []
        self._defs: list[str] = []
        self._next_id = 0
        # The clip in force, as an SVG id, tracked alongside the interpreter's
        # own q/Q stack.
        self._clip_id: str | None = None
        self._clip_id_stack: list[str | None] = []
        # Stroke properties the rasterizer does not model (it draws round
        # segments), tracked here because SVG can express them exactly.
        self._dash: tuple[list[float], float] | None = None
        self._line_cap = 0
        self._line_join = 0
        self._miter_limit = 10.0
        self._stroke_stack: list[tuple[Any, int, int, float]] = []
        self._fill_rule = "nonzero"

    # -- output ------------------------------------------------------------

    def to_svg(self) -> str:
        content = self._page_content()
        if content:
            self._interpret(
                content, self.resources_cos, self.resources_plain, depth=0
            )
        if self.draw_annotations:
            self._paint_annotations()
        width = _fmt(self.target_width, self._precision)
        height = _fmt(self.target_height, self._precision)
        parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            (
                '<svg xmlns="http://www.w3.org/2000/svg" '
                'xmlns:xlink="http://www.w3.org/1999/xlink" '
                f'width="{width}pt" height="{height}pt" '
                f'viewBox="0 0 {width} {height}">'
            ),
        ]
        if self._defs:
            parts.append("<defs>")
            parts.extend(self._defs)
            parts.append("</defs>")
        if self._svg_background is not None:
            parts.append(
                f'<rect width="{width}" height="{height}" '
                f'fill="{_rgb(self.background)}"/>'
            )
        parts.extend(self._body)
        parts.append("</svg>")
        return "\n".join(parts) + "\n"

    def _identifier(self, prefix: str) -> str:
        self._next_id += 1
        return f"{prefix}{self._next_id}"

    def _emit(self, element: str) -> None:
        self._body.append(element)

    def _clip_attribute(self) -> str:
        return f' clip-path="url(#{self._clip_id})"' if self._clip_id else ""

    def _path_data(self, polygons: list[list[Point]], *, close: bool) -> str:
        precision = self._precision
        parts: list[str] = []
        for polygon in polygons:
            if len(polygon) < 2:
                continue
            first = polygon[0]
            parts.append(f"M{_fmt(first[0], precision)} {_fmt(first[1], precision)}")
            for x, y in polygon[1:]:
                parts.append(f"L{_fmt(x, precision)} {_fmt(y, precision)}")
            if close:
                parts.append("Z")
        return "".join(parts)

    # -- graphics state ----------------------------------------------------

    def _handle_operator(
        self,
        op: str,
        operands: list[Any],
        resources_cos: Any,
        resources_plain: dict,
        depth: int,
    ) -> None:
        # The clip id and the stroke properties follow q/Q like everything
        # else in the graphics state.
        if op == "q":
            self._clip_id_stack.append(self._clip_id)
            self._stroke_stack.append(
                (self._dash, self._line_cap, self._line_join, self._miter_limit)
            )
        elif op == "Q":
            if self._clip_id_stack:
                self._clip_id = self._clip_id_stack.pop()
            if self._stroke_stack:
                (
                    self._dash,
                    self._line_cap,
                    self._line_join,
                    self._miter_limit,
                ) = self._stroke_stack.pop()
        elif op == "d" and len(operands) >= 2:
            self._set_dash(operands[-2], operands[-1])
            return
        elif op == "J" and operands:
            self._line_cap = _int_operand(operands[-1], self._line_cap)
            return
        elif op == "j" and operands:
            self._line_join = _int_operand(operands[-1], self._line_join)
            return
        elif op == "M" and operands:
            value = _float_operand(operands[-1])
            if value is not None:
                self._miter_limit = value
            return
        super()._handle_operator(op, operands, resources_cos, resources_plain, depth)

    def _set_dash(self, array: Any, phase: Any) -> None:
        items = getattr(array, "items", array)
        if not isinstance(items, (list, tuple)):
            self._dash = None
            return
        pattern = [
            value
            for value in (_float_operand(item) for item in items)
            if value is not None and value >= 0
        ]
        if not pattern or not any(pattern):
            self._dash = None
            return
        self._dash = (pattern, _float_operand(phase) or 0.0)

    def _stroke_attributes(self, color: Color, alpha: float) -> str:
        width = max(
            self.state.line_width * self.point_scale, 0.1 if self.state.line_width else 0.0
        )
        caps = {0: "butt", 1: "round", 2: "square"}
        joins = {0: "miter", 1: "round", 2: "bevel"}
        parts = [
            f'fill="none" stroke="{_rgb(color)}"',
            f'stroke-width="{_fmt(width, self._precision)}"',
        ]
        if self._line_cap:
            parts.append(f'stroke-linecap="{caps.get(self._line_cap, "butt")}"')
        if self._line_join:
            parts.append(f'stroke-linejoin="{joins.get(self._line_join, "miter")}"')
        elif self._miter_limit != 10.0:
            parts.append(f'stroke-miterlimit="{_fmt(self._miter_limit, 2)}"')
        if self._dash is not None:
            pattern, phase = self._dash
            scaled = " ".join(
                _fmt(value * self.point_scale, self._precision) for value in pattern
            )
            parts.append(f'stroke-dasharray="{scaled}"')
            if phase:
                parts.append(
                    f'stroke-dashoffset="{_fmt(phase * self.point_scale, self._precision)}"'
                )
        if alpha < 1.0:
            parts.append(f'stroke-opacity="{_fmt(alpha, 3)}"')
        return " ".join(parts)

    # -- paint sinks -------------------------------------------------------

    def _paint_path(self, op: str, depth: int = 0) -> None:
        # The fill rule lives in the operator, and the rasterizer drops it
        # (it fills each subpath on its own). SVG can say it exactly, so it is
        # captured here for ``_fill_subpaths`` to use.
        self._fill_rule = "evenodd" if op in ("f*", "B*", "b*") else "nonzero"
        super()._paint_path(op, depth)

    def _fill_subpaths(self, subpaths: Any, color: Color, alpha: float) -> None:
        """One ``<path>`` for the whole path, so holes and the rule survive."""
        polygons = [
            [self._user_to_pixel(x, y) for x, y in subpath]
            for subpath in subpaths
            if len(subpath) >= 3
        ]
        self._emit_fill(polygons, color, alpha, rule=self._fill_rule)

    def _fill_polygon_pixels(
        self, polygon: list[Point], color: Color, alpha: float
    ) -> None:
        self._emit_fill([polygon], color, alpha, rule=None)

    def _fill_contours_nonzero(
        self, contours: list[list[Point]], color: Color, alpha: float
    ) -> None:
        self._emit_fill(contours, color, alpha, rule="nonzero")

    def _emit_fill(
        self,
        polygons: list[list[Point]],
        color: Color,
        alpha: float,
        *,
        rule: str | None,
        paint: str | None = None,
    ) -> None:
        data = self._path_data(polygons, close=True)
        if not data:
            return
        attributes = [f'd="{data}"', f'fill="{paint or _rgb(color)}"']
        if rule:
            attributes.append(f'fill-rule="{rule}"')
        if alpha < 1.0:
            attributes.append(f'fill-opacity="{_fmt(alpha, 3)}"')
        self._emit(f"<path {' '.join(attributes)}{self._clip_attribute()}/>")

    def _stroke_subpaths(
        self, subpaths: Any, color: Color, alpha: float
    ) -> None:
        polygons = [
            [self._user_to_pixel(x, y) for x, y in subpath]
            for subpath in subpaths
            if len(subpath) >= 2
        ]
        data = self._path_data(polygons, close=False)
        if not data:
            return
        self._emit(
            f'<path d="{data}" {self._stroke_attributes(color, alpha)}'
            f"{self._clip_attribute()}/>"
        )

    def _apply_clip(self, subpaths: list[list[Point]]) -> None:
        """Register the clip as a ``<clipPath>``; no raster mask is needed."""
        polygons = [
            [self._user_to_pixel(x, y) for x, y in subpath]
            for subpath in subpaths
            if len(subpath) >= 3
        ]
        data = self._path_data(polygons, close=True)
        if not data:
            return
        identifier = self._identifier("clip")
        inherited = (
            f' clip-path="url(#{self._clip_id})"' if self._clip_id else ""
        )
        self._defs.append(
            f'<clipPath id="{identifier}"{inherited}>'
            f'<path d="{data}"/></clipPath>'
        )
        self._clip_id = identifier

    # -- images ------------------------------------------------------------

    def _paint_image_pixels(
        self,
        meta: dict,
        data: bytes,
        matrix: Matrix,
        smask: tuple[int, int, bytes] | None = None,
    ) -> None:
        from .rasterizer import _decode_image_to_rgb

        image = _decode_image_to_rgb(meta, data, limits=self._load_limits)
        if image is None:
            return
        width, height, pixels = image
        href = _png_data_uri(width, height, "RGB", pixels)
        mask_attribute = ""
        if smask is not None and smask[2]:
            mask_attribute = f' mask="url(#{self._mask_from_smask(smask)})"'
        transform = self._image_transform(matrix)
        attributes = [
            'preserveAspectRatio="none"',
            # PDF samples are not interpolated unless /Interpolate says so, and
            # the renderer never interpolates; smoothing here would make the
            # SVG disagree with the rendered page.
            'image-rendering="pixelated"',
            'x="0" y="0" width="1" height="1"',
            f'transform="{transform}"',
            f'xlink:href="{href}"',
        ]
        if self.state.fill_alpha < 1.0:
            attributes.append(f'opacity="{_fmt(self.state.fill_alpha, 3)}"')
        self._emit(
            f"<image {' '.join(attributes)}{mask_attribute}"
            f"{self._clip_attribute()}/>"
        )

    def _image_transform(self, matrix: Matrix) -> str:
        """Map the SVG unit square onto the image's place on the page.

        A PDF image occupies the unit square with its first row at ``v = 1``;
        SVG draws the first row at ``y = 0``. The extra flip is what puts the
        picture the right way up.
        """
        flip: Matrix = (1.0, 0.0, 0.0, -1.0, 0.0, 1.0)
        # ``_multiply(outer, inner)`` applies *inner* first, so this reads
        # right-to-left: flip the row order, place the unit square on the page,
        # then map user space to SVG space.
        chain = _multiply(self._device_matrix(), _multiply(matrix, flip))
        return _matrix_attribute(chain, self._precision)

    def _device_matrix(self) -> Matrix:
        """The user-space to SVG-space affine, recovered from three points."""
        origin = self._user_to_pixel(0.0, 0.0)
        unit_x = self._user_to_pixel(1.0, 0.0)
        unit_y = self._user_to_pixel(0.0, 1.0)
        return (
            unit_x[0] - origin[0],
            unit_x[1] - origin[1],
            unit_y[0] - origin[0],
            unit_y[1] - origin[1],
            origin[0],
            origin[1],
        )

    def _mask_from_smask(self, smask: tuple[int, int, bytes]) -> str:
        width, height, alpha = smask
        href = _png_data_uri(width, height, "L", alpha)
        identifier = self._identifier("mask")
        self._defs.append(
            f'<mask id="{identifier}" maskUnits="objectBoundingBox" '
            'maskContentUnits="objectBoundingBox">'
            f'<image preserveAspectRatio="none" x="0" y="0" width="1" '
            f'height="1" xlink:href="{href}"/></mask>'
        )
        return identifier

    # -- shadings ----------------------------------------------------------

    def _fill_subpaths_shading(
        self,
        subpaths: Any,
        fill_shading: tuple[Shading, Matrix],
        alpha: float,
    ) -> None:
        shading, matrix = fill_shading
        polygons = [
            [self._user_to_pixel(x, y) for x, y in subpath]
            for subpath in subpaths
            if len(subpath) >= 3
        ]
        if not polygons:
            return
        paint = self._shading_paint(shading, matrix)
        if paint is not None:
            self._emit_fill(polygons, (0, 0, 0), alpha, rule="nonzero", paint=paint)
            return
        self._emit_sampled_shading(polygons, shading, matrix, alpha)

    def _paint_sh(self, name: str, resources_cos: Any) -> None:
        """``sh`` paints the shading over the whole clip region."""
        shading = self._shading_for_name(name, resources_cos)
        if shading is None:
            return
        box = [
            [
                (0.0, 0.0),
                (float(self.target_width), 0.0),
                (float(self.target_width), float(self.target_height)),
                (0.0, float(self.target_height)),
            ]
        ]
        paint = self._shading_paint(shading, self.state.ctm)
        if paint is not None:
            self._emit_fill(box, (0, 0, 0), self.state.fill_alpha, rule=None, paint=paint)
            return
        self._emit_sampled_shading(box, shading, self.state.ctm, self.state.fill_alpha)

    def _shading_for_name(self, name: str, resources_cos: Any) -> Shading | None:
        from .cos import PdfName

        shadings = self._resource_dict(resources_cos, "Shading")
        if shadings is None:
            return None
        entry = self._resolve(shadings.mapping.get(PdfName(name)))
        if entry is None:
            return None
        try:
            return self._build_shading_object(entry)
        except PdfResourceLimitException:
            raise
        except PDF_OPERATION_ERRORS:
            return None

    def _build_shading_object(self, entry: Any) -> Shading | None:
        from .shading import build_shading

        return build_shading(self.pdf, entry, budget=self._load_budget)

    def _shading_paint(self, shading: Shading, matrix: Matrix) -> str | None:
        """A ``url(#…)`` gradient for an axial or radial shading, else ``None``."""
        from .shading import _AxialShading, _RadialShading

        transform = _matrix_attribute(
            _multiply(self._device_matrix(), matrix), self._precision
        )
        stops = _gradient_stops(shading)
        # PDF /Extend continues the end colours; SVG "pad" is the same rule.
        spread = "pad"
        if isinstance(shading, _AxialShading):
            x0, y0 = float(shading.x0), float(shading.y0)
            x1, y1 = float(shading.x1), float(shading.y1)
            identifier = self._identifier("grad")
            self._defs.append(
                f'<linearGradient id="{identifier}" gradientUnits="userSpaceOnUse" '
                f'gradientTransform="{transform}" spreadMethod="{spread}" '
                f'x1="{_fmt(x0)}" y1="{_fmt(y0)}" x2="{_fmt(x1)}" y2="{_fmt(y1)}">'
                f"{stops}</linearGradient>"
            )
            return f"url(#{identifier})"
        if isinstance(shading, _RadialShading):
            x0, y0, r0 = float(shading.x0), float(shading.y0), float(shading.r0)
            x1, y1, r1 = float(shading.x1), float(shading.y1), float(shading.r1)
            if r1 <= 0:
                return None
            identifier = self._identifier("grad")
            self._defs.append(
                f'<radialGradient id="{identifier}" gradientUnits="userSpaceOnUse" '
                f'gradientTransform="{transform}" spreadMethod="{spread}" '
                f'cx="{_fmt(x1)}" cy="{_fmt(y1)}" r="{_fmt(r1)}" '
                f'fx="{_fmt(x0)}" fy="{_fmt(y0)}" fr="{_fmt(max(r0, 0.0))}">'
                f"{stops}</radialGradient>"
            )
            return f"url(#{identifier})"
        return None

    def _emit_sampled_shading(
        self,
        polygons: list[list[Point]],
        shading: Shading,
        matrix: Matrix,
        alpha: float,
    ) -> None:
        """Draw a shading SVG has no gradient for by sampling it into an image.

        Function-based and mesh shadings have no SVG equivalent. Sampling the
        shading's own colour lookup over the area it covers keeps the page
        looking right, at the cost of a raster in an otherwise vector file.
        """
        xs = [p[0] for polygon in polygons for p in polygon]
        ys = [p[1] for polygon in polygons for p in polygon]
        if not xs or not ys:
            return
        min_x, max_x = max(0.0, min(xs)), min(float(self.target_width), max(xs))
        min_y, max_y = max(0.0, min(ys)), min(float(self.target_height), max(ys))
        span_x = max_x - min_x
        span_y = max_y - min_y
        if span_x <= 0 or span_y <= 0:
            return
        width = max(1, min(_SHADING_SAMPLE_LIMIT, math.ceil(span_x)))
        height = max(1, min(_SHADING_SAMPLE_LIMIT, math.ceil(span_y)))
        inverse_device = _invert_matrix(self._device_matrix())
        inverse_pattern = _invert_matrix(matrix)
        if inverse_device is None or inverse_pattern is None:
            return
        samples = bytearray(width * height * 3)
        background = shading.background or (255, 255, 255)
        for row in range(height):
            device_y = min_y + (row + 0.5) * span_y / height
            base = row * width * 3
            for column in range(width):
                device_x = min_x + (column + 0.5) * span_x / width
                ux, uy = _transform_point(inverse_device, device_x, device_y)
                sx, sy = _transform_point(inverse_pattern, ux, uy)
                color = shading.color_at(sx, sy) or background
                offset = base + column * 3
                samples[offset : offset + 3] = bytes(color)
        href = _png_data_uri(width, height, "RGB", bytes(samples))
        identifier = self._identifier("clip")
        inherited = f' clip-path="url(#{self._clip_id})"' if self._clip_id else ""
        self._defs.append(
            f'<clipPath id="{identifier}"{inherited}>'
            f'<path d="{self._path_data(polygons, close=True)}"/></clipPath>'
        )
        opacity = f' opacity="{_fmt(alpha, 3)}"' if alpha < 1.0 else ""
        self._emit(
            f'<image preserveAspectRatio="none" x="{_fmt(min_x)}" y="{_fmt(min_y)}" '
            f'width="{_fmt(span_x)}" height="{_fmt(span_y)}" '
            f'xlink:href="{href}"{opacity} clip-path="url(#{identifier})"/>'
        )


def _int_operand(value: Any, default: int) -> int:
    number = _float_operand(value)
    return int(number) if number is not None else default


def _float_operand(value: Any) -> float | None:
    raw = getattr(value, "value", value)
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _matrix_attribute(matrix: Matrix, precision: int = 3) -> str:
    values = " ".join(_fmt(value, precision) for value in matrix)
    return f"matrix({values})"


def _gradient_stops(shading: Shading) -> str:
    lut = shading.lut or [(0, 0, 0)]
    count = min(_GRADIENT_STOPS, len(lut))
    stops = []
    for index in range(count):
        position = index / (count - 1) if count > 1 else 0.0
        color = lut[int(position * (len(lut) - 1))]
        stops.append(
            f'<stop offset="{_fmt(position, 4)}" stop-color="{_rgb(color)}"/>'
        )
    return "".join(stops)


def _png_data_uri(width: int, height: int, mode: str, data: bytes) -> str:
    png = write_png(width, height, mode, data)
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def page_to_svg(
    pdf: Any,
    page_index: int,
    *,
    background: tuple[int, int, int] | None = (255, 255, 255),
    draw_annotations: bool = True,
    font_substitution: Any = None,
    precision: int = 3,
    limits: PdfLoadLimits | None = None,
) -> str:
    """Return *page_index* of *pdf* as an SVG document."""
    writer = _SvgWriter(
        pdf,
        page_index,
        background=background,
        draw_annotations=draw_annotations,
        font_substitution=font_substitution,
        precision=precision,
    )
    return writer.to_svg()
