"""Page collection implementation for Aspose.PDF Python SDK."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from typing import Iterator, List, Optional, Sequence, Tuple, Union, TYPE_CHECKING

from aspose_pdf.annotations import AnnotationCollection
from aspose_pdf.exceptions import AsposePdfException, PdfValidationException

if TYPE_CHECKING:
    from aspose_pdf.document import Document
    from aspose_pdf.engine.rasterizer import RasterizedPage


class Page:
    """A page of a PDF document."""

    def __init__(self, document: "Document", index: int):
        self._document = document
        self._index = index

    @property
    def index(self) -> int:
        """The zero-based index of the page."""
        return self._index

    @index.setter
    def index(self, value: int):
        # Internal use only or for legacy compatibility
        self._index = value

    @property
    def rect(self) -> tuple[float, float, float, float]:
        """Get the page rectangle (MediaBox)."""
        if self._document._engine_pdf and self._index < len(
            self._document._engine_pdf.pages
        ):
            return self._document._engine_pdf.pages[self._index]
        return (0, 0, 0, 0)

    @property
    def annotations(self) -> AnnotationCollection:
        """Get the collection of annotations on the page."""
        if not hasattr(self, "_annotations"):
            self._annotations = AnnotationCollection(self)
        return self._annotations

    @property
    def media_box(self) -> Tuple[float, float, float, float]:
        """Alias for rect."""
        return self.rect

    @property
    def rotation(self) -> int:
        """The page rotation in degrees, clockwise (one of 0, 90, 180, 270).

        Inherited from parent page-tree nodes when not set on the page itself.
        """
        eng = self._document._engine_pdf
        if eng is None or not hasattr(eng, "get_page_rotation"):
            return 0
        return eng.get_page_rotation(self._index)

    @rotation.setter
    def rotation(self, value: int) -> None:
        try:
            degrees = int(value)
        except (TypeError, ValueError):
            raise PdfValidationException("Rotation must be an integer number of degrees.")
        if degrees % 90 != 0:
            raise PdfValidationException("Rotation must be a multiple of 90 degrees.")
        eng = self._document._engine_pdf
        if eng is None or not hasattr(eng, "set_page_rotation"):
            raise AsposePdfException("Cannot set rotation: no underlying document.")
        eng.set_page_rotation(self._index, degrees)

    @property
    def crop_box(self) -> Tuple[float, float, float, float]:
        """The page CropBox ``(x0, y0, x1, y1)``; falls back to the MediaBox when unset."""
        eng = self._document._engine_pdf
        if eng is not None and hasattr(eng, "get_page_crop_box"):
            box = eng.get_page_crop_box(self._index)
            if box is not None:
                return box
        return self.rect

    @crop_box.setter
    def crop_box(self, value: Tuple[float, float, float, float]) -> None:
        try:
            rect = tuple(float(v) for v in value)
        except (TypeError, ValueError):
            raise PdfValidationException("CropBox must be four numbers (x0, y0, x1, y1).")
        if len(rect) != 4:
            raise PdfValidationException("CropBox must be four numbers (x0, y0, x1, y1).")
        eng = self._document._engine_pdf
        if eng is None or not hasattr(eng, "set_page_crop_box"):
            raise AsposePdfException("Cannot set CropBox: no underlying document.")
        eng.set_page_crop_box(self._index, rect)

    @property
    def content(self) -> bytes:
        """Get the page content stream.

        In streaming/lazy mode (opened via :meth:`~aspose_pdf.document.Document.open_streaming`)
        the content is decoded from the underlying COS document on demand.
        In normal mode the pre-loaded ``page_contents`` list is used.
        """
        eng = self._document._engine_pdf
        if eng is None:
            return b""
        if hasattr(eng, "get_page_content"):
            return eng.get_page_content(self._index)
        if self._index < len(eng.page_contents):
            return eng.page_contents[self._index]
        return b""

    def add_text(
        self,
        text: str,
        x: float,
        y: float,
        *,
        font_size: float = 12.0,
        font_name: str = "Helvetica",
        color: Sequence[float] = (0.0, 0.0, 0.0),
        tag: Optional[str] = None,
        actual_text: Optional[str] = None,
    ) -> "Page":
        """Append positioned text to this page."""
        self._document._ensure_not_disposed()
        eng = self._document._engine_pdf
        if eng is None:
            raise AsposePdfException("No document loaded")
        eng.add_text_to_page(
            self._index,
            text,
            x,
            y,
            font_size=font_size,
            font_name=font_name,
            color=color,
            tag=tag,
            actual_text=actual_text,
        )
        return self

    def add_image(
        self,
        image: Union[bytes, bytearray, str, Path],
        x: float,
        y: float,
        width: Optional[float] = None,
        height: Optional[float] = None,
        *,
        pixel_width: Optional[int] = None,
        pixel_height: Optional[int] = None,
        color_space: str = "DeviceRGB",
        bits_per_component: int = 8,
        name: Optional[str] = None,
        tag: Optional[str] = None,
        alt: Optional[str] = None,
        actual_text: Optional[str] = None,
    ) -> str:
        """Place an image on this page and return its resource name."""
        self._document._ensure_not_disposed()
        eng = self._document._engine_pdf
        if eng is None:
            raise AsposePdfException("No document loaded")
        if isinstance(image, (str, Path)):
            data = Path(image).read_bytes()
        elif isinstance(image, (bytes, bytearray)):
            data = bytes(image)
        else:
            raise TypeError("image must be bytes, bytearray, str, or Path")
        return eng.add_image_to_page(
            self._index,
            data,
            x,
            y,
            width,
            height,
            pixel_width=pixel_width,
            pixel_height=pixel_height,
            color_space=color_space,
            bits_per_component=bits_per_component,
            name=name,
            tag=tag,
            alt=alt,
            actual_text=actual_text,
        )

    def draw_rectangle(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        *,
        stroke_color: Optional[Sequence[float]] = (0.0, 0.0, 0.0),
        fill_color: Optional[Sequence[float]] = None,
        line_width: float = 1.0,
        tag: Optional[str] = None,
        alt: Optional[str] = None,
        actual_text: Optional[str] = None,
    ) -> "Page":
        """Append a stroked and/or filled rectangle to this page."""
        self._document._ensure_not_disposed()
        eng = self._document._engine_pdf
        if eng is None:
            raise AsposePdfException("No document loaded")
        eng.draw_rectangle_on_page(
            self._index,
            x,
            y,
            width,
            height,
            stroke_color=stroke_color,
            fill_color=fill_color,
            line_width=line_width,
            tag=tag,
            alt=alt,
            actual_text=actual_text,
        )
        return self

    def draw_line(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        *,
        stroke_color: Sequence[float] = (0.0, 0.0, 0.0),
        line_width: float = 1.0,
        tag: Optional[str] = None,
        alt: Optional[str] = None,
        actual_text: Optional[str] = None,
    ) -> "Page":
        """Append a stroked line segment to this page."""
        self._document._ensure_not_disposed()
        eng = self._document._engine_pdf
        if eng is None:
            raise AsposePdfException("No document loaded")
        eng.draw_line_on_page(
            self._index,
            x1,
            y1,
            x2,
            y2,
            stroke_color=stroke_color,
            line_width=line_width,
            tag=tag,
            alt=alt,
            actual_text=actual_text,
        )
        return self

    def __repr__(self) -> str:
        return f"Page({self._index})"

    def accept(self, visitor: Any) -> None:
        """Accept a visitor for page processing.

        Args:
            visitor: The visitor object to accept.
        """
        # Default implementation - do nothing
        # This method should be overridden by subclasses or used with visitors
        pass

    def render(
        self,
        *,
        dpi: float = 72.0,
        scale: float = 1.0,
        background: Tuple[int, int, int] = (255, 255, 255),
        antialias: Union[bool, int] = True,
    ) -> "RasterizedPage":
        """Render this page to an RGB raster image.

        The renderer is dependency-free and supports common page content:
        paths, fills/strokes, image XObjects, form XObjects, and embedded-font
        text. The returned object can be encoded to PNG/TIFF or saved directly.

        ``antialias`` smooths edges by supersampling (``True`` = 3x, an integer
        1-8 sets the factor, ``False`` disables it for a hard-edged raster).
        """
        self._document._ensure_not_disposed()
        eng = self._document._engine_pdf
        if eng is None:
            raise AsposePdfException("No document loaded")
        from aspose_pdf.engine.rasterizer import render_page

        return render_page(
            eng,
            self._index,
            dpi=dpi,
            scale=scale,
            background=background,
            antialias=antialias,
        )

    def save_as_image(
        self,
        path: Union[str, Path],
        *,
        dpi: float = 72.0,
        scale: float = 1.0,
        background: Tuple[int, int, int] = (255, 255, 255),
        antialias: Union[bool, int] = True,
    ) -> Path:
        """Render this page and save it as ``.png`` or ``.tif/.tiff``."""
        return self.render(
            dpi=dpi, scale=scale, background=background, antialias=antialias
        ).save(path)

    def replace_text(
        self,
        search: str,
        replacement: str,
        *,
        case_sensitive: bool = True,
        max_count: int = 0,
    ) -> int:
        """Replace existing text in simple text-showing operands on this page.

        ``max_count=0`` means unlimited. This is a conservative content-stream
        edit: it handles simple ``Tj``/``TJ`` operands and does not reflow layout.
        Returns the number of replacements made.
        """
        self._document._ensure_not_disposed()
        eng = self._document._engine_pdf
        if eng is None:
            raise AsposePdfException("No document loaded")
        return eng.replace_text(
            search,
            replacement,
            page_index=self._index,
            case_sensitive=case_sensitive,
            max_count=max_count,
        )

    def redact_text(
        self,
        search: str,
        *,
        case_sensitive: bool = True,
        max_count: int = 0,
        overlay: bool = False,
        overlay_color: Sequence[float] = (0.0, 0.0, 0.0),
    ) -> int:
        """Remove existing text from simple text-showing operands on this page.

        With ``overlay=True`` a filled rectangle (``overlay_color``, a DeviceRGB
        triple of 0..1, default black) is drawn over each removed run -- the
        classic redaction bar. The bar is cosmetic (the text is already removed);
        runs whose position cannot be tracked are left unmarked.
        """
        self._document._ensure_not_disposed()
        eng = self._document._engine_pdf
        if eng is None:
            raise AsposePdfException("No document loaded")
        return eng.redact_text(
            search,
            page_index=self._index,
            case_sensitive=case_sensitive,
            max_count=max_count,
            overlay=overlay,
            overlay_color=tuple(overlay_color),
        )


class PageCollection:
    """A collection to manage PDF pages within a Document."""

    def __init__(self, document: "Document"):
        """Create a new collection.

        Parameters
        ----------
        document:
            Parent Document.
        """
        self._document = document

    def _ensure_not_disposed(self) -> None:
        """Raise AsposePdfException if the collection or its document is disposed."""
        if getattr(self._document, "_disposed", False):
            raise AsposePdfException("Cannot operate on a disposed document.")

    def __len__(self) -> int:
        self._ensure_not_disposed()
        return len(self._document._engine_pdf.pages)

    def __iter__(self) -> Iterator[Page]:
        self._ensure_not_disposed()
        for i in range(len(self)):
            yield Page(self._document, i)

    def __getitem__(self, index: Union[int, slice]) -> Union[Page, List[Page]]:
        """Return the page at index or a slice of pages."""
        self._ensure_not_disposed()
        count = len(self)
        if isinstance(index, slice):
            start, stop, step = index.indices(count)
            return [Page(self._document, i) for i in range(start, stop, step)]

        if index < 0:
            index += count
        if index < 0 or index >= count:
            raise IndexError("Page index out of range.")
        return Page(self._document, index)

    def item(self, index: int) -> Page:
        """Legacy accessor mirroring the original Item method."""
        return self.__getitem__(index)

    def get_enumerator(self) -> Iterator[Page]:
        """Legacy iterator name – returns an iterator over the pages."""
        return self.__iter__()

    def add(self, page: Optional[Union[Page, Any]] = None) -> Page:
        """Append a page to the collection."""
        self._ensure_not_disposed()
        idx = len(self)
        if page is None:
            self._document._engine_pdf.add_page_break()
        else:
            self._document._engine_pdf.add(page)
        return Page(self._document, idx)

    def insert(self, index: int, page: Optional[Union[Page, Any]] = None) -> Page:
        """Insert a page at *index*."""
        self._ensure_not_disposed()
        count = len(self)
        if index < 0:
            index = 0
        if index > count:
            index = count

        if page is None:
            # Insert a blank page
            self._document._engine_pdf.insert(index, ((0, 0, 612, 792), b""))
        else:
            self._document._engine_pdf.insert(index, page)

        return Page(self._document, index)

    def delete(self, index: int) -> None:
        """Delete the page at index."""
        self._ensure_not_disposed()
        count = len(self)
        if index < 0:
            index += count
        if index < 0 or index >= count:
            raise IndexError("Page index out of range.")

        self._document._engine_pdf.delete(index)

    def Delete(self, index: int) -> None:
        """Public API alias for :meth:`delete`."""
        self.delete(index)

    def clear(self) -> None:
        """Remove all pages from the collection."""
        self._ensure_not_disposed()
        # Delete from last to first to avoid index shifting issues if engine didn't handle it,
        # but our engine's delete_pages handles it now.
        self._document._engine_pdf.delete_pages(0, len(self))

    def contains(self, page: Page) -> bool:
        """Return True if page is present in the collection."""
        self._ensure_not_disposed()
        if not isinstance(page, Page):
            return False
        return page._document == self._document and 0 <= page.index < len(self)

    def index_of(self, page: Page) -> int:
        """Return the zero‑based index of *page* in the collection."""
        self._ensure_not_disposed()
        if not isinstance(page, Page):
            raise TypeError("Argument must be a Page instance.")
        if page._document != self._document:
            raise PdfValidationException("The page belongs to a different document.")
        if 0 <= page.index < len(self):
            return page.index
        raise PdfValidationException("The page is not in the collection.")

    def _dispose(self) -> None:
        """Internal use only."""
        pass
