"""Collect the vector paths and placed images of a page's content stream.

This is the geometry counterpart to text extraction: it walks the content
stream the same way the rasterizer does -- tracking the graphics state through
``q``/``Q``/``cm``, building paths, descending into form XObjects -- but paints
nothing. Each painted path and each placed image comes back as one element with
its bounding box in page (user) space, so callers can find where a rule, a box
or a logo sits on the page.

Text is deliberately not reported: :class:`~aspose_pdf.text.TextFragmentAbsorber`
covers it, with the encoding handling that needs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from aspose_pdf.exceptions import PDF_OPERATION_ERRORS, PdfResourceLimitException
from aspose_pdf.load_limits import _coerce_limits, _LoadBudget

from .content_stream_parser import ContentStreamParser
from .cos import (
    PdfArray,
    PdfDictionary,
    PdfIndirectReference,
    PdfName,
    PdfNumber,
    PdfStream,
)

Matrix = tuple[float, float, float, float, float, float]
Point = tuple[float, float]

IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

# A form XObject may nest, and a malformed one may nest into itself; the
# rasterizer stops at the same depth.
_MAX_FORM_DEPTH = 8

# Painting operators mapped to what they do with the current path.
_PAINT_OPERATORS = {
    "S": "stroke",
    "s": "stroke",
    "f": "fill",
    "F": "fill",
    "f*": "fill",
    "B": "fill_stroke",
    "B*": "fill_stroke",
    "b": "fill_stroke",
    "b*": "fill_stroke",
    "n": None,
}

_DEVICE_SPACES = {"DeviceGray", "DeviceRGB", "DeviceCMYK", "G", "RGB", "CMYK"}


@dataclass(frozen=True)
class AbsorbedElement:
    """One painted path or placed image, in page (user) space."""

    kind: str  # "path" or "image"
    llx: float
    lly: float
    urx: float
    ury: float
    page_index: int
    operation: str | None = None  # fill / stroke / fill_stroke / clip
    resource_name: str | None = None
    fill_color: tuple[float, float, float] | None = None
    stroke_color: tuple[float, float, float] | None = None
    line_width: float | None = None


def absorb_page_graphics(pdf: Any, page_index: int) -> list[AbsorbedElement]:
    """Return the graphic elements of ``page_index`` in ``pdf`` (a ``SimplePdf``)."""
    if pdf is None:
        return []
    pages = getattr(pdf, "pages", [])
    if page_index < 0 or page_index >= len(pages):
        raise IndexError("Page index out of range.")
    return _GraphicsWalker(pdf, page_index).run()


def _multiply(a: Matrix, b: Matrix) -> Matrix:
    """Return ``a * b`` for PDF affine matrices."""
    return (
        a[0] * b[0] + a[1] * b[2],
        a[0] * b[1] + a[1] * b[3],
        a[2] * b[0] + a[3] * b[2],
        a[2] * b[1] + a[3] * b[3],
        a[4] * b[0] + a[5] * b[2] + b[4],
        a[4] * b[1] + a[5] * b[3] + b[5],
    )


def _apply(m: Matrix, x: float, y: float) -> Point:
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


def _cubic_bounds(
    p0: Point, p1: Point, p2: Point, p3: Point
) -> tuple[float, float, float, float]:
    """Exact bounding box of a cubic Bezier, not just its control hull."""
    xs = [p0[0], p3[0]]
    ys = [p0[1], p3[1]]
    for axis, values in ((0, xs), (1, ys)):
        a = -p0[axis] + 3.0 * p1[axis] - 3.0 * p2[axis] + p3[axis]
        b = 2.0 * (p0[axis] - 2.0 * p1[axis] + p2[axis])
        c = p1[axis] - p0[axis]
        for t in _quadratic_roots(3.0 * a, 3.0 * b, 3.0 * c):
            if 0.0 < t < 1.0:
                u = 1.0 - t
                values.append(
                    u * u * u * p0[axis]
                    + 3.0 * u * u * t * p1[axis]
                    + 3.0 * u * t * t * p2[axis]
                    + t * t * t * p3[axis]
                )
    return (min(xs), min(ys), max(xs), max(ys))


def _quadratic_roots(a: float, b: float, c: float) -> list[float]:
    if abs(a) < 1e-12:
        if abs(b) < 1e-12:
            return []
        return [-c / b]
    disc = b * b - 4.0 * a * c
    if disc < 0.0:
        return []
    root = math.sqrt(disc)
    return [(-b + root) / (2.0 * a), (-b - root) / (2.0 * a)]


class _GraphicsWalker:
    """Interprets a content stream far enough to know where its marks land."""

    def __init__(self, pdf: Any, page_index: int) -> None:
        self.pdf = pdf
        self.page_index = page_index
        budget = getattr(pdf, "_load_budget", None)
        if isinstance(budget, _LoadBudget):
            self._budget = budget
            self._limits = budget.limits
        else:
            self._limits = _coerce_limits(getattr(pdf, "_load_limits", None))
            self._budget = _LoadBudget(self._limits)
        self.elements: list[AbsorbedElement] = []
        self.ctm: Matrix = IDENTITY
        self.stack: list[tuple[Matrix, Any, Any, float, str, str]] = []
        self.fill_color: tuple[float, float, float] | None = (0.0, 0.0, 0.0)
        self.stroke_color: tuple[float, float, float] | None = (0.0, 0.0, 0.0)
        self.fill_space = "DeviceGray"
        self.stroke_space = "DeviceGray"
        self.line_width = 1.0
        self.points: list[Point] = []
        self.current: Point | None = None
        self.subpath_start: Point | None = None
        self.bbox: list[float] | None = None
        self.pending_clip = False
        self._active_forms: set[int] = set()

    # -- entry point ------------------------------------------------------
    def run(self) -> list[AbsorbedElement]:
        content = self._page_content()
        if content:
            self._interpret(content, self._page_resources(), depth=0)
        return self.elements

    def _page_content(self) -> bytes:
        if hasattr(self.pdf, "get_page_content"):
            return self.pdf.get_page_content(self.page_index)
        contents = getattr(self.pdf, "page_contents", [])
        if self.page_index < len(contents):
            return contents[self.page_index]
        return b""

    def _page_resources(self) -> PdfDictionary | None:
        if not hasattr(self.pdf, "_get_page_dict") or not hasattr(
            self.pdf, "_resolve_resources_cos"
        ):
            return None
        page = self.pdf._get_page_dict(self.page_index)
        if page is None:
            return None
        resources = self.pdf._resolve_resources_cos(page)
        return resources if isinstance(resources, PdfDictionary) else None

    # -- COS helpers ------------------------------------------------------
    def _resolve(self, obj: Any) -> Any:
        if hasattr(self.pdf, "_resolve"):
            return self.pdf._resolve(obj)
        if isinstance(obj, PdfIndirectReference) and getattr(self.pdf, "_cos_doc", None):
            return self.pdf._cos_doc.objects.get(obj.object_number)
        return obj

    def _name(self, obj: Any) -> str | None:
        """Return a name operand without its slash.

        Content-stream tokens arrive as plain strings (``"/Im1"``); names that
        come from the COS graph arrive as :class:`PdfName`.
        """
        obj = self._resolve(obj)
        if isinstance(obj, PdfName):
            return obj.name.lstrip("/")
        if isinstance(obj, str) and obj.startswith("/"):
            return obj.lstrip("/")
        return None

    def _number(self, obj: Any) -> float | None:
        obj = self._resolve(obj)
        if isinstance(obj, PdfNumber):
            return float(obj.value)
        if isinstance(obj, (int, float)) and not isinstance(obj, bool):
            return float(obj)
        return None

    # -- interpretation ---------------------------------------------------
    def _interpret(
        self, content: bytes, resources: PdfDictionary | None, *, depth: int
    ) -> None:
        resources_plain: dict = {}
        if hasattr(self.pdf, "_convert_cos_to_dict") and isinstance(
            resources, PdfDictionary
        ):
            try:
                resources_plain = self.pdf._convert_cos_to_dict(resources) or {}
            except PdfResourceLimitException:
                raise
            except PDF_OPERATION_ERRORS:
                resources_plain = {}
        try:
            tokens = list(
                ContentStreamParser(
                    content,
                    resources_plain,
                    limits=self._limits,
                    budget=self._budget,
                )._tokenize()
            )
        except PdfResourceLimitException:
            raise
        except PDF_OPERATION_ERRORS:
            return

        operands: list[Any] = []
        for token in tokens:
            if not _is_operator(token):
                operands.append(token)
                continue
            try:
                self._operator(str(token), operands, resources, depth)
            except PdfResourceLimitException:
                raise
            except PDF_OPERATION_ERRORS:
                pass
            finally:
                operands.clear()

    def _operator(
        self,
        op: str,
        operands: list[Any],
        resources: PdfDictionary | None,
        depth: int,
    ) -> None:
        numbers = [n for n in (_as_float(v) for v in operands) if n is not None]

        if op == "q":
            self.stack.append(
                (
                    self.ctm,
                    self.fill_color,
                    self.stroke_color,
                    self.line_width,
                    self.fill_space,
                    self.stroke_space,
                )
            )
            return
        if op == "Q":
            if self.stack:
                (
                    self.ctm,
                    self.fill_color,
                    self.stroke_color,
                    self.line_width,
                    self.fill_space,
                    self.stroke_space,
                ) = self.stack.pop()
            return
        if op == "cm" and len(numbers) >= 6:
            self.ctm = _multiply(tuple(numbers[-6:]), self.ctm)  # type: ignore[arg-type]
            return
        if op == "w" and numbers:
            self.line_width = numbers[-1]
            return

        if op in ("m", "l") and len(numbers) >= 2:
            point = _apply(self.ctm, numbers[-2], numbers[-1])
            if op == "m":
                self.subpath_start = point
            self._add_point(point)
            self.current = point
            return
        if op in ("c", "v", "y") and self.current is not None:
            self._curve(op, numbers)
            return
        if op == "h":
            if self.subpath_start is not None:
                self._add_point(self.subpath_start)
                self.current = self.subpath_start
            return
        if op == "re" and len(numbers) >= 4:
            x, y, width, height = numbers[-4:]
            for corner in (
                (x, y),
                (x + width, y),
                (x + width, y + height),
                (x, y + height),
            ):
                self._add_point(_apply(self.ctm, *corner))
            self.subpath_start = _apply(self.ctm, x, y)
            self.current = self.subpath_start
            return

        if op in ("W", "W*"):
            self.pending_clip = True
            return
        if op in _PAINT_OPERATORS:
            self._paint(_PAINT_OPERATORS[op])
            return

        if op in ("g", "rg", "k", "G", "RG", "K"):
            self._set_device_color(op, numbers)
            return
        if op in ("cs", "CS"):
            space = self._name(operands[-1]) if operands else None
            self._set_space(op == "cs", space)
            return
        if op in ("sc", "scn", "SC", "SCN"):
            self._set_components(op.islower(), numbers)
            return

        if op == "Do" and operands:
            name = self._name(operands[-1])
            if name:
                self._do_xobject(name, resources, depth)
            return

    # -- path building ----------------------------------------------------
    def _add_point(self, point: Point) -> None:
        if self.bbox is None:
            self.bbox = [point[0], point[1], point[0], point[1]]
        else:
            self.bbox[0] = min(self.bbox[0], point[0])
            self.bbox[1] = min(self.bbox[1], point[1])
            self.bbox[2] = max(self.bbox[2], point[0])
            self.bbox[3] = max(self.bbox[3], point[1])

    def _curve(self, op: str, numbers: list[float]) -> None:
        start = self.current
        if start is None:
            return
        if op == "c" and len(numbers) >= 6:
            c1 = _apply(self.ctm, numbers[-6], numbers[-5])
            c2 = _apply(self.ctm, numbers[-4], numbers[-3])
            end = _apply(self.ctm, numbers[-2], numbers[-1])
        elif op == "v" and len(numbers) >= 4:
            c1 = start
            c2 = _apply(self.ctm, numbers[-4], numbers[-3])
            end = _apply(self.ctm, numbers[-2], numbers[-1])
        elif op == "y" and len(numbers) >= 4:
            c1 = _apply(self.ctm, numbers[-4], numbers[-3])
            end = _apply(self.ctm, numbers[-2], numbers[-1])
            c2 = end
        else:
            return
        llx, lly, urx, ury = _cubic_bounds(start, c1, c2, end)
        self._add_point((llx, lly))
        self._add_point((urx, ury))
        self.current = end

    def _paint(self, operation: str | None) -> None:
        bbox = self.bbox
        clipping = self.pending_clip
        self.bbox = None
        self.current = None
        self.subpath_start = None
        self.pending_clip = False
        if bbox is None:
            return
        if operation is None:
            if not clipping:
                return
            operation = "clip"
        scale = math.sqrt(abs(self.ctm[0] * self.ctm[3] - self.ctm[1] * self.ctm[2]))
        self.elements.append(
            AbsorbedElement(
                kind="path",
                llx=bbox[0],
                lly=bbox[1],
                urx=bbox[2],
                ury=bbox[3],
                page_index=self.page_index,
                operation=operation,
                fill_color=self.fill_color if operation != "stroke" else None,
                stroke_color=(
                    self.stroke_color if operation in ("stroke", "fill_stroke") else None
                ),
                line_width=(
                    self.line_width * scale
                    if operation in ("stroke", "fill_stroke")
                    else None
                ),
            )
        )

    # -- colour -----------------------------------------------------------
    def _set_device_color(self, op: str, numbers: list[float]) -> None:
        nonstroking = op.islower()
        lowered = op.lower()
        if lowered == "g" and numbers:
            color = _gray_rgb(numbers[-1])
        elif lowered == "rg" and len(numbers) >= 3:
            color = _clamp_rgb(numbers[-3], numbers[-2], numbers[-1])
        elif lowered == "k" and len(numbers) >= 4:
            color = _cmyk_rgb(*numbers[-4:])
        else:
            return
        if nonstroking:
            self.fill_color = color
            self.fill_space = {"g": "DeviceGray", "rg": "DeviceRGB", "k": "DeviceCMYK"}[
                lowered
            ]
        else:
            self.stroke_color = color
            self.stroke_space = {
                "g": "DeviceGray",
                "rg": "DeviceRGB",
                "k": "DeviceCMYK",
            }[lowered]

    def _set_space(self, nonstroking: bool, space: str | None) -> None:
        name = space or ""
        if nonstroking:
            self.fill_space = name
            self.fill_color = (0.0, 0.0, 0.0) if name in _DEVICE_SPACES else None
        else:
            self.stroke_space = name
            self.stroke_color = (0.0, 0.0, 0.0) if name in _DEVICE_SPACES else None

    def _set_components(self, nonstroking: bool, numbers: list[float]) -> None:
        space = self.fill_space if nonstroking else self.stroke_space
        if space in ("DeviceGray", "G") and numbers:
            color = _gray_rgb(numbers[-1])
        elif space in ("DeviceRGB", "RGB") and len(numbers) >= 3:
            color = _clamp_rgb(numbers[-3], numbers[-2], numbers[-1])
        elif space in ("DeviceCMYK", "CMYK") and len(numbers) >= 4:
            color = _cmyk_rgb(*numbers[-4:])
        else:
            # A pattern, ICCBased, Separation or Indexed value: the element
            # reports no colour rather than an invented one.
            color = None
        if nonstroking:
            self.fill_color = color
        else:
            self.stroke_color = color

    # -- XObjects ---------------------------------------------------------
    def _do_xobject(
        self, name: str, resources: PdfDictionary | None, depth: int
    ) -> None:
        if not isinstance(resources, PdfDictionary):
            return
        xobjects = self._resolve(resources.mapping.get(PdfName("XObject")))
        if not isinstance(xobjects, PdfDictionary):
            return
        ref = xobjects.mapping.get(PdfName(name))
        stream = self._resolve(ref)
        if not isinstance(stream, PdfStream):
            return
        subtype = self._name(stream.mapping.get(PdfName("Subtype")))
        if subtype == "Image":
            self._image_element(name)
            return
        if subtype != "Form" or depth >= _MAX_FORM_DEPTH:
            return

        obj_number = ref.object_number if isinstance(ref, PdfIndirectReference) else None
        if obj_number is not None:
            if obj_number in self._active_forms:
                return  # a form that draws itself
            self._active_forms.add(obj_number)
        try:
            self._run_form(stream, ref, resources, depth)
        finally:
            if obj_number is not None:
                self._active_forms.discard(obj_number)

    def _run_form(
        self,
        stream: PdfStream,
        ref: Any,
        parent_resources: PdfDictionary | None,
        depth: int,
    ) -> None:
        matrix = _cos_matrix(self._resolve(stream.mapping.get(PdfName("Matrix"))))
        form_resources = self._resolve(stream.mapping.get(PdfName("Resources")))
        if not isinstance(form_resources, PdfDictionary):
            form_resources = parent_resources
        try:
            # The reference matters: in an encrypted document the object
            # number is part of the per-object decryption key.
            content = (
                self.pdf._decode_cos_stream(stream, ref)
                if hasattr(self.pdf, "_decode_cos_stream")
                else stream.content
            )
        except PdfResourceLimitException:
            raise
        except PDF_OPERATION_ERRORS:
            content = stream.content
        saved = (
            self.ctm,
            self.fill_color,
            self.stroke_color,
            self.line_width,
            self.fill_space,
            self.stroke_space,
        )
        saved_stack = self.stack
        self.stack = []
        if matrix is not None:
            self.ctm = _multiply(matrix, self.ctm)
        self._interpret(content, form_resources, depth=depth + 1)
        self.stack = saved_stack
        (
            self.ctm,
            self.fill_color,
            self.stroke_color,
            self.line_width,
            self.fill_space,
            self.stroke_space,
        ) = saved

    def _image_element(self, name: str) -> None:
        """An image fills the unit square of its own space (ISO 32000-1 8.9.5.2)."""
        corners = [
            _apply(self.ctm, 0.0, 0.0),
            _apply(self.ctm, 1.0, 0.0),
            _apply(self.ctm, 1.0, 1.0),
            _apply(self.ctm, 0.0, 1.0),
        ]
        xs = [point[0] for point in corners]
        ys = [point[1] for point in corners]
        self.elements.append(
            AbsorbedElement(
                kind="image",
                llx=min(xs),
                lly=min(ys),
                urx=max(xs),
                ury=max(ys),
                page_index=self.page_index,
                resource_name=name,
            )
        )


def _is_operator(token: Any) -> bool:
    return isinstance(token, str) and not token.startswith("/")


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and not value.startswith("/"):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _cos_matrix(obj: Any) -> Matrix | None:
    if not isinstance(obj, PdfArray) or len(obj.items) < 6:
        return None
    values = []
    for item in obj.items[:6]:
        if isinstance(item, PdfNumber):
            values.append(float(item.value))
        elif isinstance(item, (int, float)):
            values.append(float(item))
        else:
            return None
    return tuple(values)  # type: ignore[return-value]


def _clamp(value: float) -> float:
    return 0.0 if value < 0.0 else (1.0 if value > 1.0 else value)


def _clamp_rgb(r: float, g: float, b: float) -> tuple[float, float, float]:
    return (_clamp(r), _clamp(g), _clamp(b))


def _gray_rgb(value: float) -> tuple[float, float, float]:
    level = _clamp(value)
    return (level, level, level)


def _cmyk_rgb(c: float, m: float, y: float, k: float) -> tuple[float, float, float]:
    return (
        _clamp(1.0 - min(1.0, c + k)),
        _clamp(1.0 - min(1.0, m + k)),
        _clamp(1.0 - min(1.0, y + k)),
    )
