"""Presentation primitives for Aspose.PDF Python SDK."""

from __future__ import annotations
from typing import Optional


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
    """Interface for path operations."""
    
    def __init__(self):
        """Initialize a path."""
        self._current_x = 0.0
        self._current_y = 0.0
        self._transform_matrix: Optional[IMatrix] = None
        self._fill_mode = FillMode.ALTERNATE
    
    @property
    def current_x(self) -> float:
        """Get current X position."""
        return self._current_x
    
    @property
    def current_y(self) -> float:
        """Get current Y position."""
        return self._current_y
    
    @property
    def transform(self) -> Optional[IMatrix]:
        """Get the transformation matrix."""
        return self._transform_matrix
    
    @transform.setter
    def transform(self, matrix: Optional[IMatrix]) -> None:
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
    
    def append_cubic_bezier_curve(self, x1: float, y1: float, x2: float, y2: float, x: float, y: float) -> None:
        """Append a cubic Bezier curve to the path."""
        self._current_x = x
        self._current_y = y
