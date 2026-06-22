"""Geometry classes for PDF documents."""

from __future__ import annotations


class Point3D:
    """Represents a 3D point."""
    
    def __init__(self, x: float = 0.0, y: float = 0.0, z: float = 0.0):
        """Initialize a 3D point.
        
        Args:
            x: X-coordinate
            y: Y-coordinate
            z: Z-coordinate
        """
        self._x = float(x)
        self._y = float(y)
        self._z = float(z)
    
    @property
    def x(self) -> float:
        """Get X-coordinate."""
        return self._x
    
    @property
    def y(self) -> float:
        """Get Y-coordinate."""
        return self._y
    
    @property
    def z(self) -> float:
        """Get Z-coordinate."""
        return self._z
    
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Point3D):
            return False
        return (self._x == other._x and 
                self._y == other._y and 
                self._z == other._z)
    
    def __repr__(self) -> str:
        return f"Point3D(x={self._x}, y={self._y}, z={self._z})"


class Matrix3D:
    """Represents a 3D transformation matrix."""
    
    def __init__(self, *args):
        """Initialize a 3D matrix.
        
        Args:
            *args: Either 12 float values for the matrix elements or a single list of 12 values
        """
        if len(args) == 1 and isinstance(args[0], (list, tuple)) and len(args[0]) == 12:
            values = args[0]
        elif len(args) == 12:
            values = args
        else:
            raise ValueError("Matrix3D requires 12 float values")
        
        self._values = [float(v) for v in values]
    
    @property
    def m11(self) -> float: return self._values[0]
    @property
    def m12(self) -> float: return self._values[1]
    @property
    def m13(self) -> float: return self._values[2]
    @property
    def m21(self) -> float: return self._values[3]
    @property
    def m22(self) -> float: return self._values[4]
    @property
    def m23(self) -> float: return self._values[5]
    @property
    def m31(self) -> float: return self._values[6]
    @property
    def m32(self) -> float: return self._values[7]
    @property
    def m33(self) -> float: return self._values[8]
    @property
    def dx(self) -> float: return self._values[9]
    @property
    def dy(self) -> float: return self._values[10]
    @property
    def dz(self) -> float: return self._values[11]
    
    def __repr__(self) -> str:
        return f"Matrix3D({', '.join(str(v) for v in self._values)})"


class Rectangle:
    """Represents a rectangle with position and size."""
    
    def __init__(
        self, 
        x: float = 0.0, 
        y: float = 0.0, 
        width: float = 0.0, 
        height: float = 0.0
    ) -> None:
        """Initialize a rectangle.
        
        Args:
            x: X-coordinate of the lower-left corner.
            y: Y-coordinate of the lower-left corner.
            width: Width of the rectangle.
            height: Height of the rectangle.
        """
        self._x = float(x)
        self._y = float(y)
        self._width = float(width)
        self._height = float(height)
    
    @property
    def x(self) -> float:
        """Get X-coordinate of the lower-left corner."""
        return self._x
    
    @property
    def y(self) -> float:
        """Get Y-coordinate of the lower-left corner."""
        return self._y
    
    @property
    def width(self) -> float:
        """Get width."""
        return self._width
    
    @property
    def height(self) -> float:
        """Get height."""
        return self._height
    
    @property
    def left(self) -> float:
        """Get the left edge."""
        return self._x
    
    @property
    def bottom(self) -> float:
        """Get the bottom edge."""
        return self._y
    
    @property
    def right(self) -> float:
        """Get the right edge."""
