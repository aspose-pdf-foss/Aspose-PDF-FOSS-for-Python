"""The images a page draws, and changing them.

:class:`ImagePlacementAbsorber` collects an :class:`ImagePlacement` for every
image a page draws, each carrying the picture's bytes and where on the page it
is put. A placement collected that way is *bound* to the document it came from,
so :meth:`ImagePlacement.replace` and :meth:`ImagePlacement.hide` change the
document -- they used to change only the object in hand, and a save afterwards
still wrote the picture that was there.

Both work by redefining the image XObject rather than by editing the page's
content, which is what MuPDF's ``Page.replace_image`` does: the drawing
operation stays, so the new picture lands exactly where the old one was, at the
same size and under the same rotation. Hiding redefines it as a single fully
transparent pixel, which is MuPDF's ``Page.delete_image``. An image XObject is
one object however many times it is drawn, so both reach every place that draws
it.

A placement built by hand, with no document behind it, still holds and replaces
its own bytes -- there is nothing else it could do.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from aspose_pdf.exceptions import AsposePdfException, PdfValidationException
from aspose_pdf.load_limits import PdfLoadLimits, _coerce_limits

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
        image_data: bytes | bytearray,
        page_index: int = 0,
        rect: Rectangle | None = None,
        resolution: tuple[float, float] | None = None,
        rotation: int = 0,
        matrix: tuple[float, float, float, float, float, float] | None = None,
        meta: dict | None = None,
        limits: PdfLoadLimits | None = None,
        engine: object | None = None,
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
        self._load_limits = _coerce_limits(limits)
        # The document this placement came out of, when it came out of one.
        # Without it there is nothing to write a replacement into.
        self._engine = engine
        # Reconstruction metadata (colour space / bpc / palette / filter / ...)
        # captured at extraction time; enables save() to write a real image file.
        self._meta = dict(meta) if meta else None

    def _ensure_not_disposed(self) -> None:
        if self._disposed:
            raise AsposePdfException("Object has been disposed")

    def dispose(self) -> None:
        """Release the stored image payload and reconstruction metadata."""
        if self._disposed:
            return
        self._image_data = b""
        self._meta = None
        self._disposed = True

    def close(self) -> None:
        """Alias for :meth:`dispose`."""
        self.dispose()

    def __enter__(self) -> ImagePlacement:
        self._ensure_not_disposed()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.dispose()

    def replace(self, new_image_data: bytes | bytearray) -> bool:
        """Put *new_image_data* in this image's place in the document.

        The image XObject is redefined and the page's drawing operations are
        left alone, so the new picture appears where the old one was, at the
        same size and under the same rotation -- as MuPDF's
        ``Page.replace_image`` does it. The XObject is one object however many
        times it is drawn, so every page drawing this image shows the new one.

        Returns whether the document was changed: ``False`` for a placement with
        no document behind it, or one whose image the document no longer draws.
        Either way the placement itself now holds the new bytes.

        Parameters
        ----------
        new_image_data: bytes | bytearray
            The replacement picture, in any format
            :meth:`~aspose_pdf.pages.Page.add_image` accepts.
        """
        self._ensure_not_disposed()
        if not isinstance(new_image_data, (bytes, bytearray)):
            raise TypeError("new_image_data must be bytes or bytearray")
        if len(new_image_data) == 0:
            raise PdfValidationException("new_image_data cannot be empty")
        written = self._write_to_document(bytes(new_image_data))
        if written:
            # A placement's data is the image's *samples*, with :attr:`_meta`
            # saying how to read them -- which is what it was when the document
            # was read, and what :meth:`save` rebuilds a file from. Keeping the
            # encoded bytes the caller passed would quietly change what
            # ``image_data`` means for this one placement.
            self._adopt_from_document()
        else:
            self._image_data = bytes(new_image_data)
        self._hidden = False
        return written

    def _adopt_from_document(self) -> None:
        """Take the samples and metadata the document now holds for this image."""
        engine = self._engine
        data = getattr(engine, "images", {}).get(self.name)
        if isinstance(data, (bytes, bytearray)):
            self._image_data = bytes(data)
        meta = getattr(engine, "_image_meta", {}).get(self.name)
        self._meta = dict(meta) if meta else None

    def _write_to_document(self, data: bytes | None) -> bool:
        """Redefine this image in the document; ``None`` makes it invisible."""
        engine = self._engine
        if engine is None:
            return False
        if data is None:
            return bool(engine.hide_page_image(self.page_index, self.name))
        return bool(engine.replace_page_image(self.page_index, self.name, data))

    def save(
        self, path: str | os.PathLike, *, color_space: str | None = None
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
            self._meta,
            self._image_data,
            Path(path).suffix,
            color_space,
            limits=self._load_limits,
        )
        from aspose_pdf.engine.file_output import write_file_atomically

        file_path = resolve_output_path(path, produced_ext)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        write_file_atomically(file_path, out_bytes)
        return file_path

    def hide(self) -> bool:
        """Stop the document drawing this image.

        It is redefined as a single fully transparent pixel -- MuPDF's
        ``Page.delete_image`` -- so the drawing operation stays and there is
        nothing left for it to draw. The samples that were there are replaced
        rather than covered, so a full save does not carry them. As with
        :meth:`replace`, the XObject is one object, so this reaches every page
        that draws it.

        Returns whether the document was changed. Afterwards this placement's
        own data is no longer readable: :attr:`image_data` and :meth:`save`
        raise, because what it described is not in the document any more.
        """
        self._ensure_not_disposed()
        written = self._write_to_document(None)
        self._hidden = True
        return written

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
        self._ensure_not_disposed()
        if self._rect is not None:
            return self._rect
        return Rectangle(0, 0, 0, 0)

    @property
    def resolution(self) -> tuple[float, float]:
        """Image resolution as (horizontal_dpi, vertical_dpi). Default 72 DPI."""
        self._ensure_not_disposed()
        if self._resolution is not None:
            return self._resolution
        return (DEFAULT_IMAGE_DPI, DEFAULT_IMAGE_DPI)

    @property
    def rotation(self) -> int:
        """Rotation angle in degrees (0, 90, 180, 270)."""
        self._ensure_not_disposed()
        return self._rotation if self._rotation is not None else 0

    @property
    def matrix(self) -> tuple[float, float, float, float, float, float]:
        """PDF transformation matrix (a, b, c, d, e, f). Identity when not set."""
        self._ensure_not_disposed()
        if self._matrix is not None:
            return self._matrix
        return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

    @property
    def width(self) -> int | None:
        """Pixel width from the image XObject, when known."""
        self._ensure_not_disposed()
        return self._meta.get("width") if self._meta else None

    @property
    def height(self) -> int | None:
        """Pixel height from the image XObject, when known."""
        self._ensure_not_disposed()
        return self._meta.get("height") if self._meta else None

    @property
    def bits_per_component(self) -> int | None:
        """Bits per colour component, when known."""
        self._ensure_not_disposed()
        return self._meta.get("bpc") if self._meta else None

    @property
    def color_space(self) -> str | None:
        """Resolved colour-space kind (``gray``/``rgb``/``cmyk``/``indexed``)."""
        self._ensure_not_disposed()
        return self._meta.get("cs_kind") if self._meta else None

    def __repr__(self) -> str:
        if self._disposed:
            return f"ImagePlacement(name={self.name!r}, disposed=True)"
        return f"ImagePlacement(name={self.name!r}, size={len(self._image_data)} bytes)"


class ImagePlacementAbsorber:
    """Absorber to collect image placements from PDF pages.

    The absorber visits page objects and extracts any image resources found.
    Results are stored in the `image_placements` list.
    """

    def __init__(self) -> None:
        self.image_placements: list[ImagePlacement] = []
        self._load_limits = PdfLoadLimits()
        self._engine: object | None = None

    def _add_image(
        self,
        name: str,
        data: bytes | bytearray,
        page_index: int = 0,
        rect: Rectangle | None = None,
        resolution: tuple[float, float] | None = None,
        rotation: int = 0,
        matrix: tuple[float, float, float, float, float, float] | None = None,
        meta: dict | None = None,
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
                limits=self._load_limits,
                # Bound to the document it was read out of, so replacing or
                # hiding it changes that document rather than only this object.
                engine=self._engine,
            )
        except Exception:
            return
        self.image_placements.append(placement)

    def visit(self, page_or_pdf) -> None:
        """Visit a page, a document or an engine PDF and collect image placements.

        Parameters
        ----------
        page_or_pdf
            A :class:`~aspose_pdf.pages.Page` (only that page's images), a
            :class:`~aspose_pdf.document.Document` or engine ``SimplePdf``
            (every page), or any object exposing ``image_placements``,
            ``images`` or ``resources`` directly.
        """
        self.image_placements.clear()
        limits = getattr(page_or_pdf, "load_limits", None)
        if limits is None and hasattr(page_or_pdf, "_document"):
            limits = getattr(page_or_pdf._document, "load_limits", None)
        self._load_limits = _coerce_limits(limits)

        engine, page_filter = _resolve_engine(page_or_pdf)
        self._engine = engine
        if engine is not None:
            self._absorb_engine(engine, page_filter)
            return

        # Objects that carry image data directly (test doubles and simple
        # page-like objects) are still supported.
        if hasattr(page_or_pdf, "image_placements"):
            placements = page_or_pdf.image_placements
            if isinstance(placements, (list, tuple)):
                for placement in placements:
                    if isinstance(placement, ImagePlacement):
                        self.image_placements.append(placement)

        if isinstance(getattr(page_or_pdf, "images", None), dict):
            for name, data in page_or_pdf.images.items():
                if isinstance(data, (bytes, bytearray)):
                    self._add_image(str(name), data)

        resources = getattr(page_or_pdf, "resources", None)
        if isinstance(resources, dict):
            xobjects = resources.get("XObject")
            if isinstance(xobjects, dict):
                for name, obj in xobjects.items():
                    if isinstance(obj, (bytes, bytearray)):
                        self._add_image(str(name), obj)
                    else:
                        data = getattr(obj, "image_data", None)
                        if isinstance(data, (bytes, bytearray)):
                            self._add_image(str(name), data)

    def _absorb_engine(self, engine, page_filter: int | None) -> None:
        """Collect placements recorded by the engine for one page or all pages."""
        # A lazily loaded document has not scanned its images yet.
        if getattr(engine, "_lazy", False) and not getattr(
            engine, "_page_image_map", {}
        ):
            hydrate = getattr(engine, "_hydrate_image_info", None)
            if callable(hydrate):
                hydrate()

        images = engine.images
        page_map = getattr(engine, "_page_image_map", {})
        matrix_map = getattr(engine, "_image_matrix_map", {})
        rect_map = getattr(engine, "_image_rect_map", {})
        meta_map = getattr(engine, "_image_meta", {})

        sizes = getattr(engine, "_image_sizes", {})

        def to_rect(value):
            if isinstance(value, Rectangle):
                return value
            if isinstance(value, (tuple, list)) and len(value) >= 4:
                return Rectangle(value[0], value[1], value[2], value[3])
            return None

        def resolution_for(name: str, rect: Rectangle | None):
            """Effective DPI: raster pixels over the size drawn on the page."""
            pixels = sizes.get(name)
            if rect is None or not pixels:
                return (DEFAULT_IMAGE_DPI, DEFAULT_IMAGE_DPI)
            pixel_width, pixel_height = pixels
            if rect.width <= 0 or rect.height <= 0:
                return (DEFAULT_IMAGE_DPI, DEFAULT_IMAGE_DPI)
            return (
                pixel_width * DEFAULT_IMAGE_DPI / rect.width,
                pixel_height * DEFAULT_IMAGE_DPI / rect.height,
            )

        if not page_map:
            # No per-page mapping: everything belongs to the first page.
            if page_filter not in (None, 0):
                return
            for name, data in images.items():
                rect = to_rect(rect_map.get((0, name)))
                self._add_image(
                    name,
                    data,
                    0,
                    rect=rect,
                    matrix=matrix_map.get((0, name)),
                    resolution=resolution_for(name, rect),
                    meta=meta_map.get(name),
                )
            return

        for page_index, names in page_map.items():
            if page_filter is not None and page_index != page_filter:
                continue
            for name in names:
                if name not in images:
                    continue
                rect = to_rect(rect_map.get((page_index, name)))
                self._add_image(
                    name,
                    images[name],
                    page_index,
                    rect=rect,
                    matrix=matrix_map.get((page_index, name)),
                    resolution=resolution_for(name, rect),
                    meta=meta_map.get(name),
                )



def _resolve_engine(source) -> tuple[object | None, int | None]:
    """Return ``(engine, page index)`` for *source*, or ``(None, None)``.

    A :class:`~aspose_pdf.pages.Page` narrows the result to its own page; a
    :class:`~aspose_pdf.document.Document` or an engine ``SimplePdf`` covers
    every page. Anything else is left to the caller's direct-attribute path.
    """
    index = getattr(source, "_index", None)
    document = getattr(source, "_document", None)
    if index is not None and document is not None:  # Page
        return getattr(document, "_engine_pdf", None), int(index)
    engine = getattr(source, "_engine_pdf", None)
    if engine is not None:  # Document
        return engine, None
    if hasattr(source, "_is_engine_pdf") or (
        hasattr(source, "images") and hasattr(source, "pages")
    ):  # SimplePdf
        return source, None
    return None, None
