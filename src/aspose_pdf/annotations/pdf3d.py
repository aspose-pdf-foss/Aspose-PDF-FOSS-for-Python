"""Minimal 3D annotation primitives kept for prerelease compatibility."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from aspose_pdf.engine.data.types import Color
from aspose_pdf.geometry import Matrix3D, Rectangle

__all__ = [
    "PDF3DAnnotation",
    "PDF3DArtwork",
    "PDF3DContent",
    "PDF3DLightingScheme",
    "PDF3DRenderMode",
    "PDF3DView",
]


class PDF3DRenderMode(str, Enum):
    SOLID = "Solid"
    WIREFRAME = "Wireframe"
    TRANSPARENT = "Transparent"


class PDF3DLightingScheme(str, Enum):
    HEADLAMP = "Headlamp"
    WHITE = "White"
    GRAY = "Gray"
    DARK = "Dark"
    CUSTOM = "Custom"


@dataclass(slots=True)
class PDF3DView:
    """Lightweight description of a saved 3D view."""

    name: str = ""
    render_mode: PDF3DRenderMode = PDF3DRenderMode.SOLID
    lighting_scheme: PDF3DLightingScheme = PDF3DLightingScheme.HEADLAMP
    ctm: Matrix3D | None = None


@dataclass(slots=True)
class PDF3DContent:
    """Reference to 3D content stored on disk."""

    content_path: str


@dataclass(slots=True)
class PDF3DArtwork:
    """Container for 3D content and named views."""

    content: PDF3DContent
    views: list[PDF3DView] = field(default_factory=list)
    default_view_index: int = 0

    def add_view(self, view: PDF3DView) -> None:
        self.views.append(view)


@dataclass(slots=True)
class PDF3DAnnotation:
    """Minimal annotation wrapper for prerelease imports."""

    rect: Rectangle
    artwork: PDF3DArtwork
    background_color: Color | None = None
