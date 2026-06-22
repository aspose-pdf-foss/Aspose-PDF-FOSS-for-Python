"""Small PDF constant helpers kept for compatibility."""

from __future__ import annotations

__all__ = ["PdfConsts"]


class PdfConsts:
    @staticmethod
    def decimal_to_octal(value: int) -> str:
        if value < 0:
            raise ValueError("value must be non-negative")
        return f"{value:o}"

    @staticmethod
    def octal_to_decimal(octal_str: str) -> int:
        if any(char not in "01234567" for char in octal_str):
            raise ValueError("octal_str must contain only octal digits")
        return int(octal_str, 8)
