"""Minimal logical-structure helpers for tagged PDF compatibility."""

from __future__ import annotations

from enum import Enum
from typing import Any

__all__ = ["StructureTypeStandard", "WarichuWPElement"]


class StructureTypeStandard(str, Enum):
    DOCUMENT = "Document"
    PART = "Part"
    SECT = "Sect"
    DIV = "Div"
    P = "P"
    SPAN = "Span"
    TABLE = "Table"
    TR = "TR"
    TH = "TH"
    TD = "TD"
    WARICHU = "Warichu"
    WP = "WP"
    RB = "RB"
    RT = "RT"
    RP = "RP"


class WarichuWPElement:
    """Minimal tagged-element type for API compatibility."""

    def __init__(self, tagged_context: Any, parent: Any | None = None) -> None:
        self.tagged_context = tagged_context
        self.parent = parent
