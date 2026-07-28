"""Options for shaped, bidirectional text authoring."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

__all__ = ["TextLayoutOptions"]


@dataclass(frozen=True, slots=True)
class TextLayoutOptions:
    """Configure complex-text shaping and line layout for ``Page.add_text``.

    The primary font is supplied through ``Page.add_text(font=...)``.
    ``fallback_fonts`` are checked in order for text clusters the primary
    font cannot cover. Widths and line heights are expressed in PDF points.
    """

    direction: str = "auto"
    language: str | None = None
    script: str | None = None
    features: Mapping[str, int | bool] = field(default_factory=dict)
    fallback_fonts: Sequence[Any] = ()
    max_width: float | None = None
    line_height: float | None = None
    alignment: str = "start"

    def __post_init__(self) -> None:
        direction = str(self.direction).strip().lower()
        if direction not in {"auto", "ltr", "rtl"}:
            raise ValueError("direction must be 'auto', 'ltr', or 'rtl'")
        alignment = str(self.alignment).strip().lower()
        if alignment not in {"start", "end", "left", "center", "right"}:
            raise ValueError(
                "alignment must be 'start', 'end', 'left', 'center', or 'right'"
            )
        language = self._optional_label(self.language, "language")
        script = self._optional_label(self.script, "script")

        if not isinstance(self.features, Mapping):
            raise TypeError("features must be a mapping")
        features: dict[str, int | bool] = {}
        for name, value in self.features.items():
            if not isinstance(name, str) or not name.strip():
                raise TypeError("OpenType feature names must be non-empty strings")
            if isinstance(value, bool):
                features[name.strip()] = value
            elif isinstance(value, int):
                features[name.strip()] = value
            else:
                raise TypeError("OpenType feature values must be bool or int")

        fallbacks = self.fallback_fonts
        if isinstance(fallbacks, (str, bytes, bytearray, Path)):
            raise TypeError("fallback_fonts must be a sequence of font sources")
        try:
            fallback_tuple = tuple(fallbacks)
        except TypeError as exc:
            raise TypeError("fallback_fonts must be a sequence of font sources") from exc

        max_width = self._positive_number(self.max_width, "max_width")
        line_height = self._positive_number(self.line_height, "line_height")
        object.__setattr__(self, "direction", direction)
        object.__setattr__(self, "alignment", alignment)
        object.__setattr__(self, "language", language)
        object.__setattr__(self, "script", script)
        object.__setattr__(self, "features", MappingProxyType(features))
        object.__setattr__(self, "fallback_fonts", fallback_tuple)
        object.__setattr__(self, "max_width", max_width)
        object.__setattr__(self, "line_height", line_height)

    @staticmethod
    def _optional_label(value: str | None, name: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError(f"{name} must be a string or None")
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{name} must not be empty")
        return normalized

    @staticmethod
    def _positive_number(value: float | None, name: str) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool):
            raise TypeError(f"{name} must be a positive number or None")
        try:
            normalized = float(value)
        except (TypeError, ValueError) as exc:
            raise TypeError(f"{name} must be a positive number or None") from exc
        if not math.isfinite(normalized) or normalized <= 0:
            raise ValueError(f"{name} must be finite and positive")
        return normalized
