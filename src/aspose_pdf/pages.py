"""Page collection implementation for Aspose.PDF Python SDK."""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aspose_pdf.actions import PAGE_TRIGGERS, ActionCollection
from aspose_pdf.annotations import AnnotationCollection
from aspose_pdf.color import ColorValue
from aspose_pdf.exceptions import AsposePdfException, PdfValidationException
from aspose_pdf.page_size import PageSize
from aspose_pdf.paths import GraphicsPath
from aspose_pdf.viewer_preferences import PageBoundary

if TYPE_CHECKING:
    from aspose_pdf.document import Document
    from aspose_pdf.engine.rasterizer import RasterizedPage
    from aspose_pdf.font_registry import FontDescriptor
    from aspose_pdf.stamps import Stamp
    from aspose_pdf.tables import Table
    from aspose_pdf.text_block import TextBlock
    from aspose_pdf.text_layout import TextLayoutOptions


def _box_name(boundary: PageBoundary | str) -> str:
    """The ``/`` entry name *boundary* stands for, however it was spelled."""
    try:
        return str(PageBoundary(boundary).value)
    except ValueError:
        allowed = ", ".join(member.value for member in PageBoundary)
        raise PdfValidationException(f"A page box is one of: {allowed}") from None


def _split_page_argument(
    page: Any, size: PageSize | Sequence[float] | str | None
) -> tuple[Any, PageSize | None]:
    """Tell a page to copy from a size to create, however the two arrived.

    ``add`` and ``insert`` took one positional argument long before there were
    page sizes, so a size may be given there as well as by keyword -- but not
    both at once, which would be two answers to one question.
    """
    if isinstance(page, PageSize):
        if size is not None:
            raise PdfValidationException(
                "Pass a page size once: as the argument or as size=, not both."
            )
        return None, page
    if size is None:
        return page, None
    if page is not None:
        raise PdfValidationException(
            "size= makes a blank page of that size; it cannot be combined with "
            "a page to copy."
        )
    return None, _coerce_page_size(size)


def _coerce_page_size(value: PageSize | Sequence[float] | str) -> PageSize:
    """*value* as a :class:`PageSize`: one already, a name, or a ``(w, h)`` pair."""
    if isinstance(value, PageSize):
        return value
    if isinstance(value, str):
        try:
            return PageSize.by_name(value)
        except (TypeError, ValueError) as error:
            raise PdfValidationException(str(error)) from None
    try:
        width, height = tuple(value)
    except (TypeError, ValueError):
        raise PdfValidationException(
            "A page size is a PageSize, its name, or a (width, height) pair."
        ) from None
    try:
        return PageSize(width, height)
    except (TypeError, ValueError) as error:
        raise PdfValidationException(f"Invalid page size: {error}") from None


class _LayerSection:
    """The open optional content section of :meth:`Page.layer`."""

    __slots__ = ("_engine", "_object_number", "_page_index")

    def __init__(self, engine: Any, page_index: int, object_number: int) -> None:
        self._engine = engine
        self._page_index = page_index
        self._object_number = object_number

    def __enter__(self) -> _LayerSection:
        self._engine.begin_page_layer(self._page_index, self._object_number)
        return self

    def __exit__(self, *exc: object) -> None:
        self._engine.end_page_layer(self._page_index)


def _resolve_path(
    path: Any, even_odd: bool | None, transform: Any
) -> tuple[Any, bool, Any]:
    """The path to draw, its fill rule and its matrix, whichever kind it is.

    An :class:`~aspose_pdf.presentation.IPath` carries the fill rule and the
    matrix on itself, where a :class:`~aspose_pdf.paths.GraphicsPath` leaves both
    to the drawing call. An argument given here wins over what the path says,
    since the caller said it last.
    """
    from aspose_pdf.presentation import FillMode

    converted = getattr(path, "to_graphics_path", None)
    if callable(converted):
        if even_odd is None:
            even_odd = getattr(path, "fill_mode", None) == FillMode.ALTERNATE
        if transform is None:
            transform = getattr(path, "transform", None)
        path = converted()
    if not isinstance(path, GraphicsPath):
        raise PdfValidationException(
            "draw_path takes a GraphicsPath (or an IPath), not "
            f"{type(path).__name__}."
        )
    return path, bool(even_odd), transform


class _GraphicsSection:
    """The open graphics state of :meth:`Page.graphics`."""

    __slots__ = ("_engine", "_page_index", "_state")

    def __init__(self, engine: Any, page_index: int, state: dict[str, Any]) -> None:
        self._engine = engine
        self._page_index = page_index
        self._state = state

    def __enter__(self) -> _GraphicsSection:
        self._engine.begin_page_graphics(self._page_index, **self._state)
        return self

    def __exit__(self, *exc: object) -> None:
        self._engine.end_page_graphics(self._page_index)


class Page:
    """A page of a PDF document."""

    # Declared, so a name that is not one of these raises instead of being
    # taken and quietly lost. These objects are rebuilt on demand -- ``pages[0]``
    # hands back a new one each time -- so an attribute set on a misspelling used
    # to vanish before the next statement, with the document saved unchanged.
    __slots__ = ("_annotations", "_document", "_index")

    def __init__(self, document: Document, index: int):
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
    def label(self) -> str | None:
        """The page's label -- ``"iii"``, ``"12"``, ``"A-1"`` -- as a viewer shows it.

        ``None`` when the document has no page labels, where viewers show the
        page's position instead. Set labels through
        :attr:`Document.page_labels <aspose_pdf.Document.page_labels>`.
        """
        self._document._ensure_not_disposed()
        engine = self._document._engine_pdf
        return None if engine is None else engine.page_label(self._index)

    @property
    def rect(self) -> tuple[float, float, float, float]:
        """Get the page rectangle (MediaBox)."""
        self._document._ensure_not_disposed()
        if self._document._engine_pdf and self._index < len(
            self._document._engine_pdf.pages
        ):
            return self._document._engine_pdf.pages[self._index]
        return (0, 0, 0, 0)

    @property
    def annotations(self) -> AnnotationCollection:
        """Get the collection of annotations on the page."""
        self._document._ensure_not_disposed()
        if not hasattr(self, "_annotations"):
            self._annotations = AnnotationCollection(self)
        return self._annotations

    @property
    def media_box(self) -> tuple[float, float, float, float]:
        """The page's ``/MediaBox`` ``(x0, y0, x1, y1)`` -- the sheet it is on.

        The same rectangle as :attr:`rect`. Setting it changes the page's size;
        the sheet is the one box a page cannot be without, so it cannot be
        removed and has to have a width and a height above zero. A box that
        reaches outside the new sheet is not touched in the file -- it is
        intersected with it when read, as a reader does (ISO 32000-1 Table 30 for
        the crop box, 14.11.2 for the production boxes).
        """
        return self.rect

    @media_box.setter
    def media_box(self, value: tuple[float, float, float, float]) -> None:
        self._set_box("MediaBox", value)

    @property
    def size(self) -> PageSize:
        """The page's size in points, from its media box.

        Setting it resizes the sheet from its existing origin, so content keeps
        the coordinates it was authored at. Accepts a :class:`PageSize`, the name
        of one, or a plain ``(width, height)`` pair in points::

            page.size = PageSize.A4
            page.size = PageSize.A4.landscape()
            page.size = "legal"
            page.size = (300, 400)
        """
        x0, y0, x1, y1 = self.rect
        return PageSize(abs(x1 - x0), abs(y1 - y0))

    @size.setter
    def size(self, value: PageSize | tuple[float, float] | str) -> None:
        size = _coerce_page_size(value)
        x0, y0, _, _ = self.rect
        self._set_box("MediaBox", size.as_rect(x0, y0))

    @property
    def rotation(self) -> int:
        """The page rotation in degrees, clockwise (one of 0, 90, 180, 270).

        Inherited from parent page-tree nodes when not set on the page itself.
        """
        self._document._ensure_not_disposed()
        eng = self._document._engine_pdf
        if eng is None or not hasattr(eng, "get_page_rotation"):
            return 0
        return eng.get_page_rotation(self._index)

    @rotation.setter
    def rotation(self, value: int) -> None:
        self._document._ensure_not_disposed()
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
    def crop_box(self) -> tuple[float, float, float, float]:
        """The page CropBox ``(x0, y0, x1, y1)``; falls back to the MediaBox when unset.

        Assigning ``None`` removes the entry, which is how the page goes back to
        taking its visible region from the media box.
        """
        self._document._ensure_not_disposed()
        eng = self._document._engine_pdf
        if eng is not None and hasattr(eng, "get_page_crop_box"):
            box = eng.get_page_crop_box(self._index)
            if box is not None:
                return box
        return self.rect

    @crop_box.setter
    def crop_box(self, value: tuple[float, float, float, float] | None) -> None:
        self._set_box("CropBox", value)

    @property
    def bleed_box(self) -> tuple[float, float, float, float]:
        """The page's ``/BleedBox`` -- what a production run clips the page to.

        ISO 32000-1 14.11.2. Falls back to the :attr:`crop_box` when the page
        does not state one, as the entry's default is; a stated box is reported
        intersected with the media box, since no production box extends beyond
        the sheet. Assigning ``None`` removes the entry.

        Unlike the media and crop boxes this one is **not inheritable**, so a
        value on a page-tree node is not this page's bleed box.
        """
        return self._production_box("BleedBox")

    @bleed_box.setter
    def bleed_box(self, value: tuple[float, float, float, float] | None) -> None:
        self._set_box("BleedBox", value)

    @property
    def trim_box(self) -> tuple[float, float, float, float]:
        """The page's ``/TrimBox`` -- the finished page's size after trimming.

        Falls back to the :attr:`crop_box`, and behaves in every other way like
        :attr:`bleed_box`.
        """
        return self._production_box("TrimBox")

    @trim_box.setter
    def trim_box(self, value: tuple[float, float, float, float] | None) -> None:
        self._set_box("TrimBox", value)

    @property
    def art_box(self) -> tuple[float, float, float, float]:
        """The page's ``/ArtBox`` -- the extent of its meaningful content.

        Falls back to the :attr:`crop_box`, and behaves in every other way like
        :attr:`bleed_box`.
        """
        return self._production_box("ArtBox")

    @art_box.setter
    def art_box(self, value: tuple[float, float, float, float] | None) -> None:
        self._set_box("ArtBox", value)

    def get_box(self, boundary: PageBoundary | str) -> tuple[float, float, float, float]:
        """The page box *boundary* names, with its default applied.

        Takes a :class:`~aspose_pdf.viewer_preferences.PageBoundary` -- the same
        enumeration the viewer preferences use to say which box to view or print
        -- so code holding one of those can ask for the geometry it names::

            page.get_box(document.viewer_preferences.view_area)
        """
        name = _box_name(boundary)
        if name == "MediaBox":
            return self.media_box
        if name == "CropBox":
            return self.crop_box
        return self._production_box(name)

    def set_box(
        self,
        boundary: PageBoundary | str,
        rect: tuple[float, float, float, float] | None,
    ) -> Page:
        """Set the page box *boundary* names; ``rect=None`` removes the entry.

        The media box cannot be removed, and returns this page so calls chain.
        """
        self._set_box(_box_name(boundary), rect)
        return self

    def _production_box(self, name: str) -> tuple[float, float, float, float]:
        """A bleed/trim/art box as stated, or the crop box it defaults to."""
        self._document._ensure_not_disposed()
        eng = self._document._engine_pdf
        if eng is not None and hasattr(eng, "get_page_box"):
            box = eng.get_page_box(self._index, name)
            if box is not None:
                return box
        return self.crop_box

    def _set_box(
        self, name: str, value: tuple[float, float, float, float] | None
    ) -> None:
        """Write one page box, validating it the way its own entry is read."""
        self._document._ensure_not_disposed()
        if value is not None:
            try:
                rect = tuple(float(v) for v in value)
            except (TypeError, ValueError):
                raise PdfValidationException(
                    f"{name} must be four numbers (x0, y0, x1, y1)."
                ) from None
            if len(rect) != 4:
                raise PdfValidationException(
                    f"{name} must be four numbers (x0, y0, x1, y1)."
                )
        else:
            rect = None
        eng = self._document._engine_pdf
        if eng is None or not hasattr(eng, "set_page_box"):
            raise AsposePdfException(f"Cannot set {name}: no underlying document.")
        eng.set_page_box(self._index, name, rect)

    @property
    def actions(self) -> ActionCollection:
        """What the page does when it is opened or closed (``/AA``).

        ISO 32000-1 12.6.3, Table 194: ``open`` fires when the page becomes the
        one on screen, ``close`` when it stops being it::

            page.actions["open"] = JavaScriptAction("app.alert('here')")
            del page.actions["open"]
        """
        self._document._ensure_not_disposed()
        eng = self._document._engine_pdf
        if eng is None:
            raise AsposePdfException("No document loaded")
        index = self._index
        return ActionCollection(
            PAGE_TRIGGERS,
            lambda: eng.get_page_actions(index),
            lambda key, action: eng.set_page_action(index, key, action),
            "page",
        )

    @property
    def content(self) -> bytes:
        """Get the page content stream.

        In streaming/lazy mode (opened via :meth:`~aspose_pdf.document.Document.open_streaming`)
        the content is decoded from the underlying COS document on demand.
        In normal mode the pre-loaded ``page_contents`` list is used.
        """
        self._document._ensure_not_disposed()
        eng = self._document._engine_pdf
        if eng is None:
            return b""
        if hasattr(eng, "get_page_content"):
            return eng.get_page_content(self._index)
        if self._index < len(eng.page_contents):
            return eng.page_contents[self._index]
        return b""

    def extract_text(self) -> str:
        """The text this page says, with the line breaks it was written in.

        Text-positioning operators are what separate one line from the next, so
        the result keeps them; how those lines are grouped into paragraphs is a
        judgement :meth:`to_markdown` and :meth:`to_html` make and this does
        not.
        """
        self._document._ensure_not_disposed()
        engine = self._document._engine_pdf
        if engine is None:
            return ""
        return engine.extract_page_text(self._index)

    def add_text(
        self,
        text: str,
        x: float,
        y: float,
        *,
        font_size: float = 12.0,
        font_name: str | None = None,
        font: FontDescriptor | bytes | bytearray | str | Path | None = None,
        color: ColorValue = (0.0, 0.0, 0.0),
        tag: str | None = None,
        actual_text: str | None = None,
        layout: TextLayoutOptions | None = None,
    ) -> Page:
        """Append positioned text to this page.

        ``font_name`` selects the Standard-14 path. Pass ``font`` as a
        :class:`FontDescriptor`, bytes, bytearray, or filesystem path to embed
        and subset a Unicode Type0/CID font. Pass ``layout`` to enable
        OpenType shaping, bidirectional text, font fallback, wrapping, and
        alignment through :class:`~aspose_pdf.text_layout.TextLayoutOptions`.

        ``color`` is grey, RGB or CMYK: one, three or four components, a single
        number for grey, a ``"#rrggbb"`` string, or an
        :class:`~aspose_pdf.color.Color`. Grey and RGB channels may be 0..1 or
        0..255; CMYK is 0..1.
        """
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
            font=font,
            color=color,
            tag=tag,
            actual_text=actual_text,
            layout=layout,
        )
        return self

    def add_image(
        self,
        image: bytes | bytearray | str | Path,
        x: float,
        y: float,
        width: float | None = None,
        height: float | None = None,
        *,
        pixel_width: int | None = None,
        pixel_height: int | None = None,
        color_space: str = "DeviceRGB",
        bits_per_component: int = 8,
        name: str | None = None,
        tag: str | None = None,
        alt: str | None = None,
        actual_text: str | None = None,
    ) -> str:
        """Place an image on this page and return its resource name."""
        self._document._ensure_not_disposed()
        eng = self._document._engine_pdf
        if eng is None:
            raise AsposePdfException("No document loaded")
        if isinstance(image, (str, Path)):
            path = Path(image)
            eng._load_budget.check(
                path.stat().st_size,
                "max_input_bytes",
                "authored image input bytes",
            )
            data = path.read_bytes()
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
        stroke_color: ColorValue | None = (0.0, 0.0, 0.0),
        fill_color: ColorValue | None = None,
        line_width: float = 1.0,
        tag: str | None = None,
        alt: str | None = None,
        actual_text: str | None = None,
        **style: Any,
    ) -> Page:
        """Append a stroked and/or filled rectangle to this page.

        Both colours take grey, RGB or CMYK -- see :meth:`add_text` -- and
        ``None`` leaves that half of the paint out: no fill, or no stroke.
        ``**style`` is the rest of :meth:`draw_path`'s painting options -- dash,
        caps, joins, opacity, blend mode, transform -- since a rectangle is one
        path like any other.
        """
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
            **style,
        )
        return self

    def draw_path(
        self,
        path: GraphicsPath | Any,
        *,
        stroke_color: ColorValue | None = (0.0, 0.0, 0.0),
        fill_color: ColorValue | None = None,
        line_width: float = 1.0,
        even_odd: bool | None = None,
        line_cap: str | int | None = None,
        line_join: str | int | None = None,
        miter_limit: float | None = None,
        dash: Any = None,
        opacity: float | None = None,
        fill_opacity: float | None = None,
        stroke_opacity: float | None = None,
        blend_mode: str | None = None,
        transform: Any = None,
        tag: str | None = None,
        alt: str | None = None,
        actual_text: str | None = None,
    ) -> Page:
        """Append a :class:`~aspose_pdf.paths.GraphicsPath` to this page.

        Build the path first -- lines, cubic curves, rectangles, ellipses,
        polygons -- then say how to paint it::

            from aspose_pdf import GraphicsPath

            wedge = GraphicsPath().move_to(300, 400).line_to(400, 460).curve_to(
                430, 470, 450, 440, 460, 410
            ).close()
            page.draw_path(wedge, fill_color="#336699", stroke_color=(0, 0, 0))

        ``stroke_color`` and ``fill_color`` take grey, RGB or CMYK (see
        :meth:`add_text`); ``None`` leaves that half of the paint out, and a path
        with neither is refused rather than written as an invisible operator.
        ``even_odd`` fills by the even-odd rule instead of the nonzero winding
        rule (ISO 32000-1 8.5.3.3), which is what decides whether a hole inside a
        shape is a hole. An :class:`~aspose_pdf.presentation.IPath` is accepted
        too, and its own ``fill_mode`` and ``transform`` are used for whichever of
        the two is not given here.

        The line is drawn with ``line_width``, ``line_cap`` (``"butt"``,
        ``"round"``, ``"square"``), ``line_join`` (``"miter"``, ``"round"``,
        ``"bevel"``), ``miter_limit`` and ``dash`` -- a sequence of on/off lengths,
        or ``(pattern, phase)``.

        ``opacity`` makes the whole drawing transparent, ``fill_opacity`` and
        ``stroke_opacity`` each half of it, and ``blend_mode`` is one of the
        sixteen of 11.3.5; the three are written as an ``/ExtGState``, and a state
        the page already has is reused rather than added again. ``transform`` is
        six numbers ``(a, b, c, d, e, f)`` -- or an
        :class:`~aspose_pdf.presentation.IMatrix` -- applied to this drawing only.
        """
        self._document._ensure_not_disposed()
        eng = self._document._engine_pdf
        if eng is None:
            raise AsposePdfException("No document loaded")
        path, even_odd, transform = _resolve_path(path, even_odd, transform)
        eng.draw_path_on_page(
            self._index,
            path,
            stroke_color=stroke_color,
            fill_color=fill_color,
            line_width=line_width,
            even_odd=even_odd,
            line_cap=line_cap,
            line_join=line_join,
            miter_limit=miter_limit,
            dash=dash,
            opacity=opacity,
            fill_opacity=fill_opacity,
            stroke_opacity=stroke_opacity,
            blend_mode=blend_mode,
            transform=transform,
            tag=tag,
            alt=alt,
            actual_text=actual_text,
        )
        return self

    def graphics(
        self,
        *,
        transform: Any = None,
        clip: GraphicsPath | None = None,
        clip_even_odd: bool = False,
        opacity: float | None = None,
        fill_opacity: float | None = None,
        stroke_opacity: float | None = None,
        blend_mode: str | None = None,
    ) -> Any:
        """A context manager holding a graphics state over what is drawn inside it.

        Everything appended to the page in the block is drawn under that state --
        text and images included, which is how either of those is rotated, scaled
        or made transparent::

            with page.graphics(transform=(0, 1, -1, 0, 500, 100)):
                page.add_text("sideways", 0, 0, font_size=18)

            with page.graphics(clip=GraphicsPath().circle(300, 400, 80)):
                page.add_image("photo.jpg", 220, 320, 160, 160)

        ``clip`` confines the block to the inside of a path (``W n``, ISO 32000-1
        8.5.4), and the state is undone when the block ends, so the rest of the
        page is unaffected. The other arguments are :meth:`draw_path`'s.
        """
        self._document._ensure_not_disposed()
        eng = self._document._engine_pdf
        if eng is None:
            raise AsposePdfException("No document loaded")
        return _GraphicsSection(
            eng,
            self._index,
            {
                "transform": transform,
                "clip": clip,
                "clip_even_odd": clip_even_odd,
                "opacity": opacity,
                "fill_opacity": fill_opacity,
                "stroke_opacity": stroke_opacity,
                "blend_mode": blend_mode,
            },
        )

    def draw_line(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        *,
        stroke_color: ColorValue = (0.0, 0.0, 0.0),
        line_width: float = 1.0,
        tag: str | None = None,
        alt: str | None = None,
        actual_text: str | None = None,
        **style: Any,
    ) -> Page:
        """Append a stroked line segment to this page.

        ``stroke_color`` takes grey, RGB or CMYK -- see :meth:`add_text` --
        and ``**style`` is the rest of :meth:`draw_path`'s painting options, so a
        line can be dashed, capped, transparent or turned.
        """
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
            **style,
        )
        return self

    def add_table(
        self,
        table: Table,
        x: float,
        y: float,
        *,
        bottom_margin: float = 36.0,
        tag: bool = True,
    ) -> dict[str, Any]:
        """Draw *table* with its top-left corner at ``(x, y)`` on this page.

        Cell text is wrapped to its column with the font's own advances, rows
        grow to hold what they are given, and a table taller than the room left
        **continues onto the next page** -- repeating the rows marked as headers,
        adding a page like this one when there is none to continue onto. Pass
        ``bottom_margin`` to say how much to leave at the foot of a page.

        The structure is tagged as it is drawn -- a ``/Table`` of ``/TR`` of
        ``/TH`` and ``/TD``, nested as ISO 32000-1 14.8.4.3 asks -- unless
        ``tag=False``.

        Returns ``{"pages": [...], "bottom": y, "rows": n}``: the pages drawn on,
        where the last row ended, and how many rows were placed -- so the next
        thing on the page knows where to go.
        """
        self._document._ensure_not_disposed()
        eng = self._document._engine_pdf
        if eng is None:
            raise AsposePdfException("No document loaded")
        from aspose_pdf.engine import tables as engine_tables

        return engine_tables.draw(
            eng,
            self._index,
            table,
            float(x),
            float(y),
            bottom_margin=float(bottom_margin),
            tag=bool(tag),
        )

    def add_text_block(
        self,
        block: TextBlock,
        x: float,
        y: float,
        *,
        bottom_margin: float = 36.0,
        tag: bool = True,
    ) -> dict[str, Any]:
        """Draw *block* with the top-left of its measure at ``(x, y)``.

        Text is wrapped to the block's ``width`` -- or to the room between *x*
        and the right edge of the page -- with the font's own advances, lines
        are aligned (``left``, ``center``, ``right`` or ``justify``), and a
        block taller than the room left **continues onto the next page**, adding
        a page like this one when there is none to continue onto. Pass
        ``bottom_margin`` to say how much to leave at the foot of a page.

        Unlike :meth:`add_text` with ``layout=TextLayoutOptions(...)``, this
        needs no embedded font and no optional dependency: the standard 14 fonts
        are measured by their own advances. What it does not do is shape, so
        right-to-left and complex scripts still want that path.

        Each paragraph is tagged with its own structure type -- ``/P`` unless it
        says otherwise -- and a paragraph continued onto the next page stays one
        element. Pass ``tag=False`` to leave the text untagged.

        Returns ``{"pages": [...], "bottom": y, "lines": n}``: the pages drawn
        on, where the text ended, and how many lines were placed -- so the next
        thing on the page knows where to go.
        """
        self._document._ensure_not_disposed()
        eng = self._document._engine_pdf
        if eng is None:
            raise AsposePdfException("No document loaded")
        from aspose_pdf.engine import text_blocks as engine_text_blocks

        return engine_text_blocks.draw(
            eng,
            self._index,
            block,
            float(x),
            float(y),
            bottom_margin=float(bottom_margin),
            tag=bool(tag),
        )

    def add_stamp(self, stamp: Stamp) -> Page:
        """Put *stamp* on this page, over or under what it already draws.

        See :mod:`aspose_pdf.stamps`: the stamp is placed inside the part of
        the page a reader shows, with the page's rotation undone so it stands
        upright.
        """
        self._document._ensure_not_disposed()
        eng = self._document._engine_pdf
        if eng is None:
            raise AsposePdfException("No document loaded")
        from aspose_pdf.engine import stamps as engine_stamps

        engine_stamps.apply(eng, stamp, [self._index])
        return self

    def __repr__(self) -> str:
        return f"Page({self._index})"

    def accept(self, visitor: Any) -> None:
        """Dispatch this page to an object implementing ``visit(page)``.

        Args:
            visitor: The visitor object to accept.

        Raises:
            TypeError: If ``visitor`` does not provide a callable ``visit`` method.
        """
        visit = getattr(visitor, "visit", None)
        if not callable(visit):
            raise TypeError("Page visitor must provide a callable visit(page) method.")
        visit(self)

    def render(
        self,
        *,
        dpi: float = 72.0,
        scale: float = 1.0,
        background: tuple[int, int, int] = (255, 255, 255),
        antialias: bool | int = True,
        shape_substitute_text: bool = True,
        draw_annotations: bool = True,
        font_substitution: Any = None,
        performance: Any = None,
    ) -> RasterizedPage:
        """Render this page to an RGB raster image.

        The renderer is dependency-free and supports common page content:
        paths, fills/strokes, image XObjects, form XObjects, and embedded-font
        text. The returned object can be encoded to PNG/TIFF or saved directly.

        ``antialias`` smooths edges by supersampling (``True`` = 3x, an integer
        1-8 sets the factor, ``False`` disables it for a hard-edged raster).
        ``shape_substitute_text`` (default on) joins complex-script runs drawn
        with a bundled substitute face; it needs the optional ``text-layout``
        extra and only affects non-embedded fonts.
        ``draw_annotations`` (default on) composites each visible annotation's
        normal appearance over the page, the way a viewer shows it.
        ``font_substitution`` overrides the document's
        :attr:`~aspose_pdf.Document.font_substitution` for this call, naming
        the font sources non-embedded fonts may be drawn with.
        ``performance`` is an optional
        :class:`~aspose_pdf.visualization.PerformanceLogger` the render records
        its per-phase timings into; without one nothing is measured.
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
            shape_substitute_text=shape_substitute_text,
            draw_annotations=draw_annotations,
            font_substitution=font_substitution,
            performance=performance,
        )

    def to_svg(
        self,
        *,
        background: tuple[int, int, int] | None = (255, 255, 255),
        draw_annotations: bool = True,
        font_substitution: Any = None,
        precision: int = 3,
    ) -> str:
        """Return this page as an SVG document.

        Paths, clips, text (as glyph outlines), images and axial/radial
        shadings are exported by the shared rendering interpreter. Blend modes
        and isolated groups use CSS compositing; soft masks embed alpha maps.
        Function and mesh shadings are sampled into images. Knockout groups,
        non-isolated groups with group-level alpha/blend/mask, tiling patterns,
        patterned strokes/text and overprint preview fall back to a complete
        embedded RGBA page at 72 dpi, preserving the renderer's limitations.
        Vector blending requires a viewer supporting CSS Compositing Level 1.

        ``background`` paints an opaque page behind the content; pass ``None``
        for a transparent SVG. ``precision`` is the number of decimal places
        coordinates are written with, trading file size against exactness.
        """
        self._document._ensure_not_disposed()
        eng = self._document._engine_pdf
        if eng is None:
            raise AsposePdfException("No document loaded")
        from aspose_pdf.engine.svg_export import page_to_svg

        return page_to_svg(
            eng,
            self._index,
            background=background,
            draw_annotations=draw_annotations,
            font_substitution=font_substitution,
            precision=precision,
        )

    def save_as_svg(
        self,
        path: str | Path,
        *,
        background: tuple[int, int, int] | None = (255, 255, 255),
        draw_annotations: bool = True,
        font_substitution: Any = None,
        precision: int = 3,
    ) -> Path:
        """Write this page to *path* as SVG and return the path."""
        from aspose_pdf.engine.file_output import write_file_atomically

        target = Path(path)
        svg = self.to_svg(
            background=background,
            draw_annotations=draw_annotations,
            font_substitution=font_substitution,
            precision=precision,
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        write_file_atomically(target, svg.encode("utf-8"))
        return target

    def layer(self, layer: Any) -> Any:
        """Author content onto *layer*, as a context manager.

        Everything added inside the block is marked as belonging to the layer,
        so switching the layer off hides it -- in a viewer, in
        :meth:`render`, in text extraction and in every export::

            draft = document.layers.add("Draft", visible=False)
            with page.layer(draft):
                page.add_text("DRAFT", 200, 400, font_size=64)

        Blocks may nest; each closes its own section.
        """
        self._document._ensure_not_disposed()
        eng = self._document._engine_pdf
        if eng is None:
            raise AsposePdfException("No document loaded")
        number = getattr(layer, "object_number", None)
        if not isinstance(number, int):
            raise PdfValidationException(
                "layer must be a Layer from Document.layers"
            )
        return _LayerSection(eng, self._index, number)

    def to_html(self, *, embed_images: bool = True) -> str:
        """Return this page's inferred structure as an HTML fragment document.

        The conversion answers "what does this page *say*", not "what does it
        look like": the same layout analysis :meth:`Document.auto_tag` uses
        infers headings, paragraphs, lists, tables and figures, and the text is
        decoded exactly as :meth:`Document.extract_text` decodes it. Exact
        positioning, colour and fonts are deliberately dropped -- for a
        facsimile, use :meth:`to_svg`.
        """
        from aspose_pdf.engine.text_export import to_html

        return to_html(
            [self._blocks(embed_images)], embed_images=embed_images
        )

    def to_markdown(self, *, embed_images: bool = True) -> str:
        """Return this page's inferred structure as Markdown (GFM).

        See :meth:`to_html` for what the conversion does and does not carry.
        """
        from aspose_pdf.engine.text_export import to_markdown

        return to_markdown(
            [self._blocks(embed_images)], embed_images=embed_images
        )

    def _blocks(self, embed_images: bool) -> list:
        from aspose_pdf.engine.text_export import page_blocks

        self._document._ensure_not_disposed()
        eng = self._document._engine_pdf
        if eng is None:
            raise AsposePdfException("No document loaded")
        return page_blocks(eng, self._index, include_images=embed_images)

    def save_as_image(
        self,
        path: str | Path,
        *,
        dpi: float = 72.0,
        scale: float = 1.0,
        background: tuple[int, int, int] = (255, 255, 255),
        antialias: bool | int = True,
        mode: str = "rgb",
        compression: str = "deflate",
        quality: int = 85,
        threshold: int = 128,
    ) -> Path:
        """Render this page and save it as PNG, TIFF or JPEG.

        The format follows the suffix (``.png``, ``.tif``/``.tiff``,
        ``.jpg``/``.jpeg``). ``mode`` selects ``"rgb"``, ``"gray"`` or
        ``"bilevel"`` output, ``compression`` applies to TIFF (``"deflate"`` by
        default, or ``"none"``) and ``quality`` to JPEG.
        """
        return self.render(
            dpi=dpi, scale=scale, background=background, antialias=antialias
        ).save(
            path,
            mode=mode,
            compression=compression,
            quality=quality,
            threshold=threshold,
        )

    def replace_text(
        self,
        search: str | re.Pattern[str],
        replacement: str,
        *,
        case_sensitive: bool = True,
        max_count: int = 0,
        regex: bool = False,
        font: FontDescriptor | bytes | bytearray | str | Path | None = None,
        layout: TextLayoutOptions | None = None,
    ) -> int:
        """Replace existing text in simple text-showing operands on this page.

        ``max_count=0`` means unlimited. This is a conservative content-stream
        edit: it handles simple ``Tj``/``TJ`` operands and does not reflow layout.
        Returns the number of replacements made.

        A replacement with right-to-left or complex-script characters is shaped
        (HarfBuzz + Unicode bidi): reused in the run's own embedded font when it
        already carries every shaped glyph, otherwise a shaping-capable *font* is
        embedded and the replacement drawn at the match position (see
        :meth:`Document.replace_text`). Reshaping needs the optional
        ``text-layout`` extra.

        With ``regex=True``, or a compiled :class:`re.Pattern` as *search*, the
        search is a regular expression and *replacement* is a template whose
        ``\\1`` and ``\\g<name>`` expand to what each match captured. See
        :meth:`aspose_pdf.document.Document.replace_text` for what a pattern is
        matched against.
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
            regex=regex,
            font=font,
            layout=layout,
        )

    def redact_text(
        self,
        search: str | re.Pattern[str],
        *,
        case_sensitive: bool = True,
        max_count: int = 0,
        regex: bool = False,
        overlay: bool = False,
        overlay_color: ColorValue = (0.0, 0.0, 0.0),
    ) -> int:
        """Remove existing text from simple text-showing operands on this page.

        With ``overlay=True`` a filled rectangle (``overlay_color`` -- grey, RGB
        or CMYK, default black) is drawn over each removed run -- the
        classic redaction bar. The bar is cosmetic (the text is already removed);
        runs whose position cannot be tracked are left unmarked.

        Saving after a match requires a full rewrite and removes obsolete
        content storage. Incremental saves are refused and existing signatures
        are invalidated. Shared content still used by other pages is retained.
        See :meth:`aspose_pdf.document.Document.redact_text` for alternate-text
        handling, unsupported cases and output-stream requirements.

        With ``regex=True``, or a compiled :class:`re.Pattern` as *search*, a
        *shape* is redacted rather than a phrase -- an account number, a date, a
        card -- and the overlay bars come from the same spans the removal used.
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
            regex=regex,
            overlay=overlay,
            overlay_color=overlay_color,
        )

    def add_link(
        self,
        rect: Sequence[float],
        target: Any,
    ) -> None:
        """Add a clickable link over *rect* triggering *target*.

        *rect* is ``(x0, y0, x1, y1)`` in PDF user space. *target* is an
        :class:`~aspose_pdf.interactive.Action` (e.g. ``URIAction``,
        ``GoToAction``) or a :class:`~aspose_pdf.interactive.Destination`
        (a bare destination becomes an implicit go-to within this document).
        """
        self._document._ensure_not_disposed()
        eng = self._document._engine_pdf
        if eng is None:
            raise AsposePdfException("No document loaded")
        eng.add_link(self._index, tuple(rect), target)


class PageCollection:
    """A collection to manage PDF pages within a Document."""

    # Declared, so a misspelled name raises instead of being taken and
    # quietly lost; see ``Page`` in ``pages.py``.
    __slots__ = ("_document",)


    def __init__(self, document: Document):
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

    def __getitem__(self, index: int | slice) -> Page | list[Page]:
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
        """Legacy iterator name - returns an iterator over the pages."""
        return self.__iter__()

    def add(
        self,
        page: Page | PageSize | Any | None = None,
        *,
        size: PageSize | Sequence[float] | str | None = None,
    ) -> Page:
        """Append a page to the collection.

        A blank page is US Letter unless a size is given, either as the first
        argument or as *size* -- a :class:`~aspose_pdf.page_size.PageSize`, one of
        its names, or a ``(width, height)`` pair in points::

            document.pages.add(PageSize.A4)
            document.pages.add(PageSize.A4.landscape())
            document.pages.add(size=(300, 400))

        Passing a :class:`Page` copies it, as :meth:`insert` describes.
        """
        self._ensure_not_disposed()
        idx = len(self)
        page, size = _split_page_argument(page, size)
        if page is None and size is None:
            self._document._engine_pdf.add_page_break()
        elif page is None:
            self._document._engine_pdf.add((size.as_rect(), b""))
        else:
            self._document._engine_pdf.add(page)
        return Page(self._document, idx)

    def insert(
        self,
        index: int,
        page: Page | PageSize | Any | None = None,
        *,
        size: PageSize | Sequence[float] | str | None = None,
    ) -> Page:
        """Insert a page at *index*, blank or a copy of an existing one.

        A blank page takes a *size* exactly as :meth:`add` does, and is US Letter
        without one.

        A :class:`Page` of *this* document is copied whole -- its resources,
        rotation and boxes, and its annotations as fresh objects. Copying a page
        out of a *different* document is refused rather than half-done: the
        objects its content names live in that document's graph, and bringing
        them across is what :meth:`aspose_pdf.Document.merge` is for.
        """
        self._ensure_not_disposed()
        count = len(self)
        if index < 0:
            index = 0
        if index > count:
            index = count
        page, size = _split_page_argument(page, size)

        if page is None:
            # Insert a blank page, of the size asked for or the default sheet.
            rect = size.as_rect() if size is not None else (0, 0, 612, 792)
            self._document._engine_pdf.insert(index, (rect, b""))
        elif isinstance(page, Page):
            if page._document is not self._document:
                raise PdfValidationException(
                    "insert() copies a page within one document; use "
                    "Document.merge to bring pages in from another"
                )
            self._document._engine_pdf.copy_page(page.index, index)
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
        """Return the zero-based index of *page* in the collection."""
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
