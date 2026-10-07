"""Presentation drawing primitives.

``FillMode`` and ``IMatrix`` are value objects: the two fill rules PDF defines,
and an affine matrix that really multiplies. ``IPath`` **collects a path and can
be drawn**: it used to raise, because nothing in this package could draw one, and
accepting segments nobody would ever paint would have been worse. Now
:meth:`aspose_pdf.pages.Page.draw_path` takes either it or the native
:class:`aspose_pdf.paths.GraphicsPath`, which is the fuller of the two --
ellipses, polygons, quadratic curves, and painting options per drawing.
"""

from __future__ import annotations

from aspose_pdf.paths import GraphicsPath


class FillMode:
    """Fill mode enumeration for path operations."""
    
    ALTERNATE = "Alternate"
    WINDING = "Winding"


class IMatrix:
    """Interface for matrix operations."""
    
    def __init__(self, a: float = 1.0, b: float = 0.0, c: float = 0.0, 
                 d: float = 1.0, e: float = 0.0, f: float = 0.0):
        """Initialize a matrix with transformation values.
        
        Args:
            a: X scale
            b: Y skew
            c: X skew
            d: Y scale
            e: X translation
            f: Y translation
        """
        self._a = a
        self._b = b
        self._c = c
        self._d = d
        self._e = e
        self._f = f
    
    @property
    def a(self) -> float: return self._a
    @property
    def b(self) -> float: return self._b
    @property
    def c(self) -> float: return self._c
    @property
    def d(self) -> float: return self._d
    @property
    def e(self) -> float: return self._e
    @property
    def f(self) -> float: return self._f
    
    def translate(self, x: float, y: float) -> None:
        """Apply translation to the matrix."""
        self._e += x
        self._f += y


class IPath:
    """A path built segment by segment, in the shape ported code expects.

    Hand it to :meth:`aspose_pdf.pages.Page.draw_path`, which reads
    :attr:`fill_mode` for the fill rule and :attr:`transform` for a matrix to draw
    it under -- both of which a :class:`~aspose_pdf.paths.GraphicsPath` leaves to
    the drawing call instead.
    """

    def __init__(self):
        """Initialize a path."""
        self._current_x = 0.0
        self._current_y = 0.0
        self._transform_matrix: IMatrix | None = None
        self._fill_mode = FillMode.ALTERNATE
        self._path = GraphicsPath()
    
    @property
    def current_x(self) -> float:
        """Get current X position."""
        return self._current_x
    
    @property
    def current_y(self) -> float:
        """Get current Y position."""
        return self._current_y
    
    @property
    def transform(self) -> IMatrix | None:
        """Get the transformation matrix."""
        return self._transform_matrix
    
    @transform.setter
    def transform(self, matrix: IMatrix | None) -> None:
        """Set the transformation matrix."""
        self._transform_matrix = matrix
    
    @property
    def fill_mode(self) -> str:
        """Get the fill mode."""
        return self._fill_mode
    
    @fill_mode.setter
    def fill_mode(self, mode: str) -> None:
        """Set the fill mode."""
        if mode not in (FillMode.ALTERNATE, FillMode.WINDING):
            raise ValueError("Invalid fill mode")
        self._fill_mode = mode
    
    def move_to(self, x: float, y: float) -> None:
        """Start a new subpath at ``(x, y)``."""
        self._path.move_to(x, y)
        self._track()

    def append_line(self, x: float, y: float) -> None:
        """Add a straight segment to ``(x, y)``.

        A path that has not been started yet begins here, rather than refusing: a
        caller who appends a line first means to start one.
        """
        if self._path.is_empty:
            self._path.move_to(x, y)
        else:
            self._path.line_to(x, y)
        self._track()

    def append_cubic_bezier_curve(
        self, x1: float, y1: float, x2: float, y2: float, x: float, y: float
    ) -> None:
        """Add a cubic Bézier curve to ``(x, y)`` through two control points."""
        if self._path.is_empty:
            self._path.move_to(x1, y1)
        self._path.curve_to(x1, y1, x2, y2, x, y)
        self._track()

    def append_rectangle(
        self, x: float, y: float, width: float, height: float
    ) -> None:
        """Add a closed rectangle as its own subpath."""
        self._path.rect(x, y, width, height)
        self._track()

    def close_all_figures(self) -> None:
        """Close the subpath being built."""
        if not self._path.is_empty:
            self._path.close()
            self._track()

    def _track(self) -> None:
        """Keep ``current_x``/``current_y`` on the point the path reached."""
        point = self._path.current_point
        if point is not None:
            self._current_x, self._current_y = point

    def to_graphics_path(self) -> GraphicsPath:
        """This path as the native :class:`~aspose_pdf.paths.GraphicsPath`."""
        return self._path

    def __len__(self) -> int:
        return len(self._path)

    def __repr__(self) -> str:
        return (
            f"IPath({len(self._path)} segments, fill_mode={self._fill_mode!r}, "
            f"current=({self._current_x}, {self._current_y}))"
        )
