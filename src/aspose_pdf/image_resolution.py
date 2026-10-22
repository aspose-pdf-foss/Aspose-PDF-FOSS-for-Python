"""Detached findings from an effective image-resolution inspection."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ImageResolutionIssue"]


@dataclass(frozen=True, slots=True)
class ImageResolutionIssue:
    """One image paint below the requested effective DPI on either image axis.

    Indices are zero-based. ``placement_index`` counts all image paints on the
    page in content order, including good and degenerate placements, descending
    into invoked forms. Repeated paints produce separate findings.

    ``resource_path`` contains original resource names, without their PDF name
    prefix, from outer forms to the image. For an inline image it contains only
    enclosing forms and ``is_inline`` is true. ``bbox`` is ``(x, y, width,
    height)`` in unrotated page user coordinates, before cropping or clipping.
    DPI uses transformed image-axis lengths and the page's ``/UserUnit``;
    ``dpi_x``/``dpi_y`` refer to image columns/rows even for rotated images.
    This immutable report remains usable after the document is closed.
    """

    page_index: int
    placement_index: int
    resource_path: tuple[str, ...]
    pixel_width: int
    pixel_height: int
    dpi_x: float
    dpi_y: float
    bbox: tuple[float, float, float, float]
    is_inline: bool = False

    @property
    def image_name(self) -> str | None:
        """Return the image resource name, or None for an inline image."""
        return None if self.is_inline else self.resource_path[-1]

    @property
    def effective_dpi(self) -> float:
        """Return the lower of the image's two effective axis resolutions."""
        return min(self.dpi_x, self.dpi_y)
