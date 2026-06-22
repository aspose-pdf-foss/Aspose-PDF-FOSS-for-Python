"""Data types for Aspose.PDF Python SDK."""

from __future__ import annotations


class Color:
    """Represents a color in PDF documents.
    
    This class provides predefined color constants and basic color functionality.
    """
    
    def __init__(self, r: float = 0.0, g: float = 0.0, b: float = 0.0, 
                 a: float = 1.0):
        """Initialize a color.
        
        Args:
            r: Red component (0.0-1.0)
            g: Green component (0.0-1.0)
            b: Blue component (0.0-1.0)
            a: Alpha component (0.0-1.0)
        """
        self._r = max(0.0, min(1.0, r))
        self._g = max(0.0, min(1.0, g))
        self._b = max(0.0, min(1.0, b))
        self._a = max(0.0, min(1.0, a))
    
    @property
    def r(self) -> float:
        """Get the red component."""
        return self._r
    
    @property
    def g(self) -> float:
        """Get the green component."""
        return self._g
    
    @property
    def b(self) -> float:
        """Get the blue component."""
        return self._b
    
    @property
    def a(self) -> float:
        """Get the alpha component."""
        return self._a
    
    # Predefined colors
    @staticmethod
    def aqua() -> "Color":
        """Get the aqua color (0.0, 1.0, 1.0)."""
        return Color(0.0, 1.0, 1.0)
    
    @staticmethod
    def blue() -> "Color":
        """Get the blue color (0.0, 0.0, 1.0)."""
        return Color(0.0, 0.0, 1.0)
    
    @staticmethod
    def azure() -> "Color":
        """Get the azure color (0.94, 0.97, 1.0)."""
        return Color(0.94, 0.97, 1.0)
    
    @staticmethod
    def red() -> "Color":
        """Get the red color (1.0, 0.0, 0.0)."""
        return Color(1.0, 0.0, 0.0)
    
    @staticmethod
    def green() -> "Color":
        """Get the green color (0.0, 1.0, 0.0)."""
        return Color(0.0, 1.0, 0.0)
    
    @staticmethod
    def yellow() -> "Color":
        """Get the yellow color (1.0, 1.0, 0.0)."""
        return Color(1.0, 1.0, 0.0)
    
    @staticmethod
    def black() -> "Color":
        """Get the black color (0.0, 0.0, 0.0)."""
        return Color(0.0, 0.0, 0.0)
    
    @staticmethod
    def white() -> "Color":
        """Get the white color (1.0, 1.0, 1.0)."""
        return Color(1.0, 1.0, 1.0)
    
    @staticmethod
    def gray() -> "Color":
        """Get the gray color (0.5, 0.5, 0.5)."""
        return Color(0.5, 0.5, 0.5)
    
    # Aliases for the test
    @staticmethod
    def Aqua() -> "Color":
        """Get the aqua color (alias for aqua)."""
        return Color.aqua()
    
    @staticmethod
    def Blue() -> "Color":
        """Get the blue color (alias for blue)."""
        return Color.blue()
    
    @staticmethod
    def Azure() -> "Color":
        """Get the azure color (alias for azure)."""
        return Color.azure()
    
    @staticmethod
    def Red() -> "Color":
        """Get the red color (alias for red)."""
        return Color.red()
    
    @staticmethod
    def Green() -> "Color":
        """Get the green color (alias for green)."""
        return Color.green()
    
    @staticmethod
    def Yellow() -> "Color":
        """Get the yellow color (alias for yellow)."""
        return Color.yellow()
    
    @staticmethod
    def Black() -> "Color":
        """Get the black color (alias for black)."""
        return Color.black()
    
    @staticmethod
    def White() -> "Color":
        """Get the white color (alias for white)."""
        return Color.white()
    
    @staticmethod
    def Gray() -> "Color":
        """Get the gray color (alias for gray)."""
        return Color.gray()


class PdfName:
    """Represents a PDF name object."""
    
    def __init__(self, name: str):
        """Initialize a PDF name.
        
        Args:
            name: The name string (without the leading slash)
        """
        self._name = name
    
    @property
    def name(self) -> str:
        """Get the name value."""
        return self._name


