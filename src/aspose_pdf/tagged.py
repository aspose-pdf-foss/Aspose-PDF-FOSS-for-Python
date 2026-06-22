"""Tagged PDF compatibility helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["TaggedContext"]


@dataclass(slots=True)
class TaggedContext:
    document: Any
    structure_tree_root: Any | None = None
    mark_info: Any | None = None
    language: str | None = None
