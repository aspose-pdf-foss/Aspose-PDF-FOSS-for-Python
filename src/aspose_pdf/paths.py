"""Paths to draw on a page: lines, curves and the shapes built from them.

A :class:`GraphicsPath` collects the subpaths of ISO 32000-1 8.5.2 -- moves,
lines, cubic Bézier curves, rectangles -- and :meth:`aspose_pdf.pages.Page.draw_path`
paints it. Building is fluent, so a shape reads as one statement::

    from aspose_pdf import GraphicsPath

    arrow = (
        GraphicsPath()
        .move_to(100, 100)
        .line_to(200, 160)
        .curve_to(230, 170, 250, 150, 260, 120)
        .close()
    )
    page.draw_path(arrow, fill_color="#336699", stroke_color=None)

Coordinates are PDF points in the page's own space, y upward from the bottom
left -- the same space :meth:`~aspose_pdf.pages.Page.add_text` and
:meth:`~aspose_pdf.pages.Page.draw_rectangle` place things in.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from aspose_pdf.exceptions import PdfValidationException

__all__ = ["GraphicsPath"]

#: How closely four Bézier curves have to bulge to pass for a circle. The
#: constant is 4/3 * (sqrt(2) - 1): with the control points that far along each
#: side, the curve meets the circle at both ends and at the midpoint, and the
#: largest error between is about 0.027% of the radius -- a fifth of a point on a
#: page-sized circle, which is below what any renderer resolves.
_KAPPA = 4.0 / 3.0 * (math.sqrt(2.0) - 1.0)


def _number(value: object, name: str) -> float:
    """*value* as a finite float, or a refusal naming the coordinate."""
    if isinstance(value, bool):
        raise PdfValidationException(f"{name} must be a number.")
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise PdfValidationException(f"{name} must be a number.") from None
    if not math.isfinite(number):
        raise PdfValidationException(f"{name} must be finite.")
    return number


class GraphicsPath:
    """A path made of subpaths, to be filled, stroked or clipped with.

    The builders return the path, so calls chain. Every one of them needs a
    current point except :meth:`move_to`, :meth:`rect`, :meth:`ellipse`,
    :meth:`circle`, :meth:`polygon` and :meth:`polyline`, which start a subpath
    of their own: a path that begins with :meth:`line_to` is refused rather than
    written as a content stream whose first operator has nothing to draw from.
    """

    __slots__ = ("_current", "_ops", "_start")

    def __init__(self) -> None:
        self._ops: list[tuple[str, tuple[float, ...]]] = []
        self._current: tuple[float, float] | None = None
        self._start: tuple[float, float] | None = None

    # -- state ---------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._ops)

    def __bool__(self) -> bool:
        return bool(self._ops)

    @property
    def current_point(self) -> tuple[float, float] | None:
        """Where the last segment left off, or ``None`` for an empty path."""
        return self._current

    @property
    def is_empty(self) -> bool:
        """Whether the path has no segments at all."""
        return not self._ops

    def _require_current(self, what: str) -> tuple[float, float]:
        if self._current is None:
            raise PdfValidationException(
                f"{what} continues a subpath, so the path has to start somewhere: "
                "call move_to (or rect, ellipse, circle, polygon, polyline) first."
            )
        return self._current

    # -- segments ------------------------------------------------------------

    def move_to(self, x: float, y: float) -> GraphicsPath:
        """Start a new subpath at ``(x, y)`` (the ``m`` operator)."""
        point = (_number(x, "x"), _number(y, "y"))
        self._ops.append(("m", point))
        self._current = point
        self._start = point
        return self

    def line_to(self, x: float, y: float) -> GraphicsPath:
        """Add a straight segment to ``(x, y)`` (the ``l`` operator)."""
        self._require_current("line_to")
        point = (_number(x, "x"), _number(y, "y"))
        self._ops.append(("l", point))
        self._current = point
        return self

    def curve_to(
        self, x1: float, y1: float, x2: float, y2: float, x: float, y: float
    ) -> GraphicsPath:
        """Add a cubic Bézier curve to ``(x, y)`` (the ``c`` operator).

        ``(x1, y1)`` and ``(x2, y2)`` are the control points at this end and at
        the far end of the curve.
        """
        self._require_current("curve_to")
        values = (
            _number(x1, "x1"),
            _number(y1, "y1"),
            _number(x2, "x2"),
            _number(y2, "y2"),
            _number(x, "x"),
            _number(y, "y"),
        )
        self._ops.append(("c", values))
        self._current = (values[4], values[5])
        return self

    def quadratic_to(self, x1: float, y1: float, x: float, y: float) -> GraphicsPath:
        """Add a quadratic curve, written as the cubic that draws it exactly.

        PDF has no quadratic operator; a quadratic is the cubic whose control
        points sit two thirds of the way from each end to the quadratic's own, so
        this is a conversion and not an approximation.
        """
        x0, y0 = self._require_current("quadratic_to")
        cx, cy = _number(x1, "x1"), _number(y1, "y1")
        ex, ey = _number(x, "x"), _number(y, "y")
        return self.curve_to(
            x0 + 2.0 / 3.0 * (cx - x0),
            y0 + 2.0 / 3.0 * (cy - y0),
            ex + 2.0 / 3.0 * (cx - ex),
            ey + 2.0 / 3.0 * (cy - ey),
            ex,
            ey,
        )

    def close(self) -> GraphicsPath:
        """Close the current subpath back to where it started (``h``).

        A closed subpath is joined at the start point rather than capped, which
        is what makes a stroked corner there look like the others.
        """
        self._require_current("close")
        self._ops.append(("h", ()))
        if self._start is not None:
            self._current = self._start
        return self

    # -- shapes --------------------------------------------------------------

    def rect(self, x: float, y: float, width: float, height: float) -> GraphicsPath:
        """Add a closed rectangle as its own subpath (the ``re`` operator)."""
        values = (
            _number(x, "x"),
            _number(y, "y"),
            _number(width, "width"),
            _number(height, "height"),
        )
        self._ops.append(("re", values))
        # ``re`` leaves the current point at the rectangle's origin, as 8.5.2.1
        # says: it is `m` plus three `l` plus `h`.
        self._current = (values[0], values[1])
        self._start = self._current
        return self

    def ellipse(self, x: float, y: float, width: float, height: float) -> GraphicsPath:
        """Add the ellipse inscribed in the box ``(x, y, width, height)``.

        Four Bézier curves, which is how every PDF producer draws one: PDF has no
        arc operator.
        """
        left = _number(x, "x")
        bottom = _number(y, "y")
        w = _number(width, "width")
        h = _number(height, "height")
        if w <= 0 or h <= 0:
            raise PdfValidationException("An ellipse needs a width and a height above zero.")
        rx, ry = w / 2.0, h / 2.0
        cx, cy = left + rx, bottom + ry
        ox, oy = rx * _KAPPA, ry * _KAPPA
        self.move_to(cx - rx, cy)
        self.curve_to(cx - rx, cy + oy, cx - ox, cy + ry, cx, cy + ry)
        self.curve_to(cx + ox, cy + ry, cx + rx, cy + oy, cx + rx, cy)
        self.curve_to(cx + rx, cy - oy, cx + ox, cy - ry, cx, cy - ry)
        self.curve_to(cx - ox, cy - ry, cx - rx, cy - oy, cx - rx, cy)
        return self.close()

    def circle(self, center_x: float, center_y: float, radius: float) -> GraphicsPath:
        """Add a circle of *radius* about ``(center_x, center_y)``."""
        r = _number(radius, "radius")
        if r <= 0:
            raise PdfValidationException("A circle needs a radius above zero.")
        cx = _number(center_x, "center_x")
        cy = _number(center_y, "center_y")
        return self.ellipse(cx - r, cy - r, 2 * r, 2 * r)

    def polyline(self, points: Iterable[Sequence[float]]) -> GraphicsPath:
        """Add an open subpath through *points*, a sequence of ``(x, y)`` pairs."""
        return self._points(points, close=False, what="A polyline")

    def polygon(self, points: Iterable[Sequence[float]]) -> GraphicsPath:
        """Add a closed subpath through *points*, a sequence of ``(x, y)`` pairs."""
        return self._points(points, close=True, what="A polygon")

    def _points(
        self, points: Iterable[Sequence[float]], *, close: bool, what: str
    ) -> GraphicsPath:
        collected = []
        for point in points:
            try:
                px, py = point
            except (TypeError, ValueError):
                raise PdfValidationException(
                    f"{what} is made of (x, y) pairs."
                ) from None
            collected.append((px, py))
        if len(collected) < 2:
            raise PdfValidationException(f"{what} needs at least two points.")
        self.move_to(*collected[0])
        for px, py in collected[1:]:
            self.line_to(px, py)
        return self.close() if close else self

    # -- output --------------------------------------------------------------

    def _operators(self) -> list[tuple[str, tuple[float, ...]]]:
        """The path's operators, for the content-stream writer."""
        if not self._ops:
            raise PdfValidationException("The path has no segments to draw.")
        return list(self._ops)

    def __repr__(self) -> str:
        return f"GraphicsPath({len(self._ops)} segments, current={self._current})"
