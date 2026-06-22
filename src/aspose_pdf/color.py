"""Color and gradient support for PDF documents."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass
class Point:
    """Represents a point in 2D space."""
    
    x: float = 0.0
    y: float = 0.0
    
    def __repr__(self) -> str:
        return f"Point(x={self.x}, y={self.y})"


class GradientAxialShading:
    """Represents axial (linear) gradient shading.
    
    This class defines a gradient that varies along a line between two points.
    """
    
    def __init__(
        self,
        start_color: "Color",
        end_color: "Color",
        start: Optional[Point] = None,
        end: Optional[Point] = None,
    ) -> None:
        """Initialize axial gradient shading.
        
        Args:
            start_color: The color at the start point.
            end_color: The color at the end point.
            start: The start point of the gradient line. Defaults to (0, 0).
            end: The end point of the gradient line. Defaults to (1, 1).
        """
        self._start_color = start_color
        self._end_color = end_color
        self._start = start if start is not None else Point(0, 0)
        self._end = end if end is not None else Point(1, 1)
    
    @property
    def start_color(self) -> "Color":
        """Get the start color."""
        return self._start_color
    
    @property
    def end_color(self) -> "Color":
        """Get the end color."""
        return self._end_color
    
    @property
    def start(self) -> Point:
        """Get the start point."""
        return self._start
    
    @property
    def end(self) -> Point:
        """Get the end point."""
        return self._end


class Color:
    """Represents a color in PDF documents.
    
    Supports both solid colors and gradient patterns.
    """
    
    def __init__(
        self,
        pattern_color_space: Optional[GradientAxialShading] = None,
        r: float = 0.0,
        g: float = 0.0,
        b: float = 0.0,
    ) -> None:
        """Initialize a color.
        
        Args:
            pattern_color_space: Optional gradient shading pattern.
            r: Red component (0.0-1.0) for solid colors.
            g: Green component (0.0-1.0) for solid colors.
            b: Blue component (0.0-1.0) for solid colors.
        """
        self._pattern_color_space = pattern_color_space
        self._r = r
        self._g = g
        self._b = b
    
    @property
    def pattern_color_space(self) -> Optional[GradientAxialShading]:
        """Get the pattern color space (gradient)."""
        return self._pattern_color_space
    
    @property
    def r(self) -> float:
        """Get the red component."""
        return self._r
