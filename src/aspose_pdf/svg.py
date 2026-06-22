"""SVG compatibility helpers."""

from __future__ import annotations

from dataclasses import dataclass, field

from aspose_pdf.load_options import SvgLoadOptions as _BaseSvgLoadOptions

__all__ = ["Margin", "PageInfo", "SvgLoadOptions"]


@dataclass(slots=True)
class Margin:
    top: float = 0.0
    left: float = 0.0
    bottom: float = 0.0
    right: float = 0.0


@dataclass(slots=True)
class PageInfo:
    margin: Margin = field(default_factory=Margin)


class SvgLoadOptions(_BaseSvgLoadOptions):
    def __init__(self, page_size: tuple[float, float] | None = None) -> None:
        super().__init__(page_size=page_size)
        self._conversion_engine = "Default"
        self._page_info = PageInfo()

    @property
    def conversion_engine(self) -> str:
        return self._conversion_engine

    @conversion_engine.setter
    def conversion_engine(self, value: str) -> None:
        self._conversion_engine = str(value)

    @property
    def page_info(self) -> PageInfo:
        return self._page_info
