"""Image placement utilities for the Aspose PDF library.

This module provides ImagePlacement and ImagePlacementAbsorber classes
for handling image extraction and manipulation in PDF documents.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Tuple, Union, TYPE_CHECKING

if TYPE_CHECKING:
    pass

from aspose_pdf.exceptions import AsposePdfException, PdfValidationException


# PDF default resolution (points per inch)
DEFAULT_IMAGE_DPI = 72.0


class Rectangle:
    """Rectangle representing image placement bounds on a PDF page.

    Attributes
    ----------
    x : float
        Left edge in PDF points.
    y : float
        Bottom edge in PDF points.
    width : float
        Width in PDF points.
    height : float
        Height in PDF points.
    """

    def __init__(
        self, x: float = 0, y: float = 0, width: float = 0, height: float = 0
    ) -> None:
        self.x = float(x)
        self.y = float(y)
        self.width = float(width)
        self.height = float(height)

    def __repr__(self) -> str:
        return f"Rectangle(x={self.x}, y={self.y}, width={self.width}, height={self.height})"


class ImagePlacement:
    """Represent an image placed on a PDF page.

    Parameters
    ----------
    name: str
        Identifier for the image placement.
    image_data: bytes | bytearray
        Raw image bytes (e.g., PNG, JPEG).
    page_index: int, optional
        Index of the page this image is on.
    rect: Rectangle, optional
        Bounding rectangle of the image on the page (x, y, width, height).
    resolution: tuple, optional
        (horizontal_dpi, vertical_dpi).
    rotation: int, optional
        Rotation angle in degrees (0, 90, 180, 270).
    matrix: tuple, optional
        PDF transformation matrix (a, b, c, d, e, f).
    """

    def __init__(
        self,
        name: str,
        image_data: Union[bytes, bytearray],
        page_index: int = 0,
        rect: Optional[Rectangle] = None,
        resolution: Optional[Tuple[float, float]] = None,
        rotation: int = 0,
        matrix: Optional[Tuple[float, float, float, float, float, float]] = None,
        meta: Optional[dict] = None,
    ) -> None:
        if not isinstance(name, str):
            raise TypeError("name must be a string")
        if not isinstance(image_data, (bytes, bytearray)):
            raise TypeError("image_data must be bytes or bytearray")
        if len(image_data) == 0:
            raise PdfValidationException("image_data cannot be empty")

        self.name = name
        self._image_data = bytes(image_data)
        self.page_index = page_index
        self._hidden = False
        self._disposed = False
        self._rect = rect
        self._resolution = resolution
        self._rotation = rotation
        self._matrix = matrix
        # Reconstruction metadata (colour space / bpc / palette / filter / ...)
        # captured at extraction time; enables save() to write a real image file.
        self._meta = dict(meta) if meta else None

    def _ensure_not_disposed(self) -> None:
        if self._disposed:
            raise AsposePdfException("Object has been disposed")

    def replace(self, new_image_data: Union[bytes, bytearray]) -> None:
        """Replace the current image data with new_image_data.

        Parameters
        ----------
        new_image_data: bytes | bytearray
            The new raw image bytes to store.
        """
        self._ensure_not_disposed()
        if not isinstance(new_image_data, (bytes, bytearray)):
            raise TypeError("new_image_data must be bytes or bytearray")
        if len(new_image_data) == 0:
            raise PdfValidationException("new_image_data cannot be empty")
        self._image_data = bytes(new_image_data)

    def save(
        self, path: Union[str, os.PathLike], *, color_space: Optional[str] = None
    ) -> Path:
        """Save the image as a real, openable image file.

        When reconstruction metadata is available (images collected from a parsed
        document), the payload is rebuilt into a proper file: raster codecs become
        PNG with CMYK/Indexed/Gray→RGB colour conversion, DCT/JPEG keeps its JPEG
        bytes, and JPX uses Pillow when installed. Without metadata (or for bytes
        that already are an encoded image) the payload is written verbatim.

        Parameters
        ----------
        path: str or os.PathLike
            Destination file path. Its suffix selects the format when achievable;
            it is adjusted to the produced format otherwise.
        color_space: str, optional
            ``"RGB"`` or ``"Gray"`` to force a colour conversion of reconstructed
            raster output.

        Returns
        -------
        pathlib.Path
            The path actually written.
        """
        self._ensure_not_disposed()
        if self._hidden:
            raise AsposePdfException(
                "ImagePlacement has been hidden and its data is no longer accessible"
            )

        from aspose_pdf.engine.image_export import (
            reconstruct_image_file,
            resolve_output_path,
        )

        out_bytes, produced_ext = reconstruct_image_file(
            self._meta, self._image_data, Path(path).suffix, color_space
        )
        file_path = resolve_output_path(path, produced_ext)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(out_bytes)
        return file_path

    def hide(self) -> None:
        """Hide the image placement.

        After calling this method, attempts to access the image data will
        raise a RuntimeError.
        """
        self._ensure_not_disposed()
        self._hidden = True

    @property
    def image_data(self) -> bytes:
        """Return the current image payload."""
        self._ensure_not_disposed()
        if self._hidden:
            raise AsposePdfException(
                "ImagePlacement has been hidden and its data is no longer accessible"
            )
        return bytes(self._image_data)

    @property
    def rectangle(self) -> Rectangle:
        """Bounding rectangle of the image on the page (x, y, width, height in PDF points)."""
        if self._rect is not None:
            return self._rect
        return Rectangle(0, 0, 0, 0)

    @property
    def resolution(self) -> Tuple[float, float]:
        """Image resolution as (horizontal_dpi, vertical_dpi). Default 72 DPI."""
        if self._resolution is not None:
            return self._resolution
        return (DEFAULT_IMAGE_DPI, DEFAULT_IMAGE_DPI)

    @property
    def rotation(self) -> int:
        """Rotation angle in degrees (0, 90, 180, 270)."""
        return self._rotation if self._rotation is not None else 0

    @property
    def matrix(self) -> Tuple[float, float, float, float, float, float]:
        """PDF transformation matrix (a, b, c, d, e, f). Identity when not set."""
        if self._matrix is not None:
            return self._matrix
        return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

    @property
    def width(self) -> Optional[int]:
        """Pixel width from the image XObject, when known."""
        return self._meta.get("width") if self._meta else None

    @property
    def height(self) -> Optional[int]:
        """Pixel height from the image XObject, when known."""
        return self._meta.get("height") if self._meta else None

    @property
    def bits_per_component(self) -> Optional[int]:
        """Bits per colour component, when known."""
        return self._meta.get("bpc") if self._meta else None

    @property
    def color_space(self) -> Optional[str]:
        """Resolved colour-space kind (``gray``/``rgb``/``cmyk``/``indexed``)."""
        return self._meta.get("cs_kind") if self._meta else None

    def __repr__(self) -> str:
        return f"ImagePlacement(name={self.name!r}, size={len(self._image_data)} bytes)"


class ImagePlacementAbsorber:
    """Absorber to collect image placements from PDF pages.

    The absorber visits page objects and extracts any image resources found.
    Results are stored in the `image_placements` list.
    """

    def __init__(self) -> None:
        self.image_placements: List[ImagePlacement] = []

    def _add_image(
        self,
        name: str,
        data: Union[bytes, bytearray],
        page_index: int = 0,
        rect: Optional[Rectangle] = None,
        resolution: Optional[Tuple[float, float]] = None,
        rotation: int = 0,
        matrix: Optional[Tuple[float, float, float, float, float, float]] = None,
        meta: Optional[dict] = None,
    ) -> None:
        """Create an ImagePlacement and store it."""
        try:
            placement = ImagePlacement(
                name,
                data,
                page_index,
                rect=rect,
                resolution=resolution,
                rotation=rotation,
                matrix=matrix,
                meta=meta,
            )
        except Exception:
            return
        self.image_placements.append(placement)

    def visit(self, page_or_pdf) -> None:
        """Visit a page or PDF and collect all image placements.

        Parameters
        ----------
        page_or_pdf
            A page object or SimplePdf instance to extract images from.
        """
        self.image_placements.clear()

        # 1. Handle SimplePdf directly (Engine level)
        is_engine = hasattr(page_or_pdf, "_is_engine_pdf") or (
            hasattr(page_or_pdf, "images") and hasattr(page_or_pdf, "pages")
        )

        if is_engine:
            # LZY-01 fix: If the document is lazy-loaded and doesn't have image info yet, hydrate it.
            if getattr(page_or_pdf, "_lazy", False) and not getattr(
                page_or_pdf, "_page_image_map", {}
            ):
                if hasattr(page_or_pdf, "_hydrate_image_info"):
                    page_or_pdf._hydrate_image_info()

            images = page_or_pdf.images
            page_map = getattr(page_or_pdf, "_page_image_map", {})
            matrix_map = getattr(page_or_pdf, "_image_matrix_map", {})
            rect_map = getattr(page_or_pdf, "_image_rect_map", {})
            meta_map = getattr(page_or_pdf, "_image_meta", {})

            def _to_rect(val):
                if val is None:
                    return None
                if isinstance(val, Rectangle):
                    return val
                if isinstance(val, (tuple, list)) and len(val) >= 4:
                    return Rectangle(val[0], val[1], val[2], val[3])
                return None

            if page_map:
                for page_idx, img_names in page_map.items():
                    for name in img_names:
                        if name in images:
                            rect = _to_rect(rect_map.get((page_idx, name)))
                            matrix = matrix_map.get((page_idx, name))
                            self._add_image(
                                name,
                                images[name],
                                page_idx,
                                rect=rect,
                                matrix=matrix,
                                resolution=(DEFAULT_IMAGE_DPI, DEFAULT_IMAGE_DPI),
                                meta=meta_map.get(name),
                            )
            else:
                # No page map, assign all to page 0
                for name, data in images.items():
                    rect = _to_rect(rect_map.get((0, name)))
                    matrix = matrix_map.get((0, name))
                    self._add_image(
                        name,
                        data,
                        0,
                        rect=rect,
                        matrix=matrix,
                        resolution=(DEFAULT_IMAGE_DPI, DEFAULT_IMAGE_DPI),
                        meta=meta_map.get(name),
                    )
        else:
            # 2. Handle Page or other objects (if not handled by engine)
            # Multiple if statements allowed here as a page can have images in multiple ways.
            if hasattr(page_or_pdf, "image_placements"):
                placements = getattr(page_or_pdf, "image_placements")
                if isinstance(placements, (list, tuple)):
                    for p in placements:
                        if isinstance(p, ImagePlacement):
                            self.image_placements.append(p)

            if hasattr(page_or_pdf, "images") and isinstance(
                getattr(page_or_pdf, "images"), dict
            ):
                imgs = getattr(page_or_pdf, "images")
                for name, data in imgs.items():
                    if isinstance(data, (bytes, bytearray)):
                        self._add_image(str(name), data)

            if hasattr(page_or_pdf, "resources"):
                res = getattr(page_or_pdf, "resources")
                if isinstance(res, dict):
                    xobj = res.get("XObject")
                    if isinstance(xobj, dict):
                        for name, obj in xobj.items():
                            if isinstance(obj, (bytes, bytearray)):
                                self._add_image(str(name), obj)
                            elif hasattr(obj, "image_data"):
                                data = getattr(obj, "image_data")
                                if isinstance(data, (bytes, bytearray)):
                                    self._add_image(str(name), data)
