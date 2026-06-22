"""LaTeX fragment compatibility helpers."""

from __future__ import annotations


class LatexFragment:
    """Small value object that stores LaTeX source text."""

    def __init__(self, text: str, *args: object) -> None:
        self.text = str(text)
        self.args = args

    def __str__(self) -> str:
        return self.text
