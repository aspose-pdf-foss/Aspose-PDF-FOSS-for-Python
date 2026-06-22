"""PdfNumber class for PDF number primitives."""

from __future__ import annotations

from typing import Union


class PdfNumber:
    """Represents a PDF number primitive (integer or real).
    
    This class wraps numeric values for PDF document manipulation.
    """
    
    def __init__(self, value: Union[int, float, str] = 0) -> None:
        """Initialize a PdfNumber with a numeric value.
        
        Args:
            value: The numeric value. Can be int, float, or string representation.
                   Defaults to 0 if not provided.
        """
        if isinstance(value, str):
            try:
                if '.' in value:
                    self._value = float(value)
                else:
                    self._value = int(value)
            except ValueError:
                self._value = 0
        else:
            self._value = float(value) if not isinstance(value, (int, float)) else value
    
    def __float__(self) -> float:
        """Convert to float."""
        return self._value
    
    def __int__(self) -> int:
        """Convert to int (truncates)."""
        return int(self._value)
    
    def to_double(self) -> float:
        """Get the numeric value as a double-precision float.
        
        Returns:
            The numeric value as a float.
        """
        return self._value
    
    def to_int(self) -> int:
        """Get the numeric value as an integer (truncates).
        
        Returns:
            The numeric value truncated to an integer.
        """
        return int(self._value)
    
    def __repr__(self) -> str:
        """Return string representation."""
        if self._value == int(self._value):
            return f"PdfNumber({int(self._value)})"
        return f"PdfNumber({self._value})"
    
    def __eq__(self, other: object) -> bool:
        """Check equality with another PdfNumber or numeric value."""
        if isinstance(other, PdfNumber):
            return self._value == other._value
        if isinstance(other, (int, float)):
            return self._value == other
        return NotImplemented
    
    def __lt__(self, other: 'PdfNumber') -> bool:
        """Compare less than."""
        if isinstance(other, PdfNumber):
            return self._value < other._value
        return NotImplemented
    
    def __le__(self, other: 'PdfNumber') -> bool:
        """Compare less than or equal."""
        if isinstance(other, PdfNumber):
            return self._value <= other._value
        return NotImplemented
