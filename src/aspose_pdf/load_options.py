"""Load option containers for format-specific import helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "CdrLoadOptions",
    "CgmLoadOptions",
    "OfdLoadOptions",
    "SvgLoadOptions",
]


def _normalize_page_size(
    page_size: tuple[float, float] | list[float] | None,
) -> tuple[float, float] | None:
    if page_size is None:
        return None
    if len(page_size) != 2:
        raise ValueError("page_size must contain exactly two numbers")
    return (float(page_size[0]), float(page_size[1]))


@dataclass(slots=True)
class _BaseLoadOptions:
    page_size: tuple[float, float] | None = None
    xps_intermediate_file_if_any: str | Path | None = None
    aps_intermediate_file_if_any: str | Path | None = None

    def __post_init__(self) -> None:
        self.page_size = _normalize_page_size(self.page_size)

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {}
        if self.page_size is not None:
            result["page_size"] = self.page_size
        if self.xps_intermediate_file_if_any is not None:
            result["xps_intermediate_file"] = str(self.xps_intermediate_file_if_any)
        if self.aps_intermediate_file_if_any is not None:
            result["aps_intermediate_file"] = str(self.aps_intermediate_file_if_any)
        return result


@dataclass(slots=True)
class CdrLoadOptions(_BaseLoadOptions):
    """Options for loading CDR files."""


@dataclass(slots=True)
class SvgLoadOptions(_BaseLoadOptions):
    """Options for loading SVG files."""


@dataclass(slots=True)
class CgmLoadOptions(_BaseLoadOptions):
    """Options for loading CGM files."""


@dataclass(slots=True)
class OfdLoadOptions(_BaseLoadOptions):
    """Options for loading OFD files."""
