"""Primitive value objects for prerelease compatibility."""

from __future__ import annotations

__all__ = ["ColorPrimitive"]


class ColorPrimitive:
    """Very small color primitive with transparency support."""

    def __init__(self) -> None:
        self._transparency = 0

    @property
    def transparency(self) -> int:
        return self._transparency

    @transparency.setter
    def transparency(self, value: int) -> None:
        if not 0 <= int(value) <= 100:
            raise ValueError("transparency must be between 0 and 100")
        self._transparency = int(value)
