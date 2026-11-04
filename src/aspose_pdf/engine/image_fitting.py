"""Align an image within a frame while preserving its pixel aspect ratio."""

from __future__ import annotations

import math

from aspose_pdf.exceptions import PdfValidationException

from .content_authoring import format_number

_ANCHORS = {
    "top_left": (0.0, 1.0),
    "top": (0.5, 1.0),
    "top_right": (1.0, 1.0),
    "left": (0.0, 0.5),
    "center": (0.5, 0.5),
    "right": (1.0, 0.5),
    "bottom_left": (0.0, 0.0),
    "bottom": (0.5, 0.0),
    "bottom_right": (1.0, 0.0),
}


def validate_anchor(anchor: str) -> tuple[float, float]:
    """Resolve an anchor before reading image input or registering resources."""
    if not isinstance(anchor, str) or anchor not in _ANCHORS:
        raise PdfValidationException("anchor must be one of: " + ", ".join(_ANCHORS))
    return _ANCHORS[anchor]


def validate_fit_box(
    x: float, y: float, width: float | None, height: float | None, fit: str,
) -> tuple[float, float, float, float] | None:
    """Validate new fitting modes before decoding or registering an image."""
    if not isinstance(fit, str) or fit not in ("stretch", "contain", "cover"):
        raise PdfValidationException("fit must be 'stretch', 'contain' or 'cover'")
    if fit == "stretch":
        return None
    if width is None or height is None:
        raise PdfValidationException("contain and cover require both width and height")
    values = []
    for name, value in (("x", x), ("y", y), ("width", width), ("height", height)):
        if isinstance(value, bool):
            raise PdfValidationException(f"{name} must be a finite number")
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            raise PdfValidationException(f"{name} must be a finite number") from None
        if not math.isfinite(number):
            raise PdfValidationException(f"{name} must be a finite number")
        if name in ("width", "height") and float(format_number(number)) <= 0:
            raise PdfValidationException(
                f"{name} must be positive at PDF coordinate precision"
            )
        values.append(number)
    return values[0], values[1], values[2], values[3]


def fit_image_geometry(
    pixel_width: int,
    pixel_height: int,
    box: tuple[float, float, float, float],
    fit: str,
    alignment: tuple[float, float] = (0.5, 0.5),
) -> tuple[float, float, float, float]:
    """Return the full image rectangle, before any cover clipping."""
    x, y, width, height = box
    by_width = width / pixel_width <= height / pixel_height
    if by_width == (fit == "contain"):
        image_width = width
        image_height = width * (pixel_height / pixel_width)
    else:
        image_height = height
        image_width = height * (pixel_width / pixel_height)
    placed_x = x + (width - image_width) * alignment[0]
    placed_y = y + (height - image_height) * alignment[1]
    geometry = placed_x, placed_y, image_width, image_height
    if not all(math.isfinite(value) for value in geometry):
        raise PdfValidationException("Fitted image geometry must be finite")
    if any(float(format_number(value)) <= 0 for value in geometry[2:]):
        raise PdfValidationException(
            "Fitted image dimensions must be positive at PDF coordinate precision"
        )
    return geometry
