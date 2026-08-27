"""Graphic element absorption from PDF pages.

:class:`GraphicsAbsorber` walks a page's content stream and reports every
painted path and placed image with its bounding box in page (user) space --
the geometry counterpart to :class:`~aspose_pdf.text.TextFragmentAbsorber`.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from aspose_pdf.engine.graphics_absorb import AbsorbedElement, absorb_page_graphics
from aspose_pdf.geometry import Rectangle

__all__ = [
    "GraphicElement",
    "GraphicElementCollection",
    "GraphicsAbsorber",
    "InvalidOperationException",
]


class InvalidOperationException(RuntimeError):
    """Raised when a graphics element is attached to the wrong parent."""


class GraphicElement:
    """A painted path or a placed image, read from a page's content stream.

    The element is a read-only view: it records where a mark lands and how it
    was painted, and changing it does not change the page.

    ``rectangle`` is the bounding box in page (user) space, with curves bounded
    exactly rather than by their control points. It covers the path geometry
    itself -- a stroke's width is reported separately in ``line_width`` (scaled
    into page space) rather than folded into the box.
    """

    __slots__ = ("_collection", "_element")

    def __init__(self, element: AbsorbedElement) -> None:
        self._element = element
        self._collection: GraphicElementCollection | None = None

    @property
    def kind(self) -> str:
        """What put the mark on the page.

        ``"path"`` for a painted path, ``"image"`` for a placed image (an
        XObject or an inline ``BI``/``EI`` one), ``"text"`` for a shown run,
        and ``"shading"`` for an ``sh`` fill.
        """
        return self._element.kind

    @property
    def operation(self) -> str | None:
        """How the mark was made: ``fill``, ``stroke``, ``fill_stroke`` or ``clip``.

        ``None`` for an image.
        """
        return self._element.operation

    @property
    def rectangle(self) -> Rectangle:
        """Bounding box in page (user) space."""
        return Rectangle(
            self._element.llx,
            self._element.lly,
            self._element.urx - self._element.llx,
            self._element.ury - self._element.lly,
        )

    @property
    def page_index(self) -> int:
        """Zero-based index of the page this element was found on."""
        return self._element.page_index

    @property
    def resource_name(self) -> str | None:
        """XObject resource name of a placed image, or ``None`` for a path."""
        return self._element.resource_name

    @property
    def fill_color(self) -> tuple[float, float, float] | None:
        """Fill colour as RGB in 0..1, or ``None``.

        Colours set through a device space (``g``/``rg``/``k`` and ``sc``/
        ``scn`` under DeviceGray/DeviceRGB/DeviceCMYK) are reported; a pattern,
        ICCBased, Separation or Indexed colour is reported as ``None`` rather
        than as an approximation.
        """
        return self._element.fill_color

    @property
    def stroke_color(self) -> tuple[float, float, float] | None:
        """Stroke colour as RGB in 0..1, or ``None``; see :attr:`fill_color`."""
        return self._element.stroke_color

    @property
    def line_width(self) -> float | None:
        """Stroke width in page space, or ``None`` for a fill or an image."""
        return self._element.line_width

    def __repr__(self) -> str:
        rect = self._element
        return (
            f"GraphicElement(kind={rect.kind!r}, operation={rect.operation!r}, "
            f"rect=({rect.llx:.2f}, {rect.lly:.2f}, {rect.urx:.2f}, {rect.ury:.2f}), "
            f"page_index={rect.page_index})"
        )


class GraphicElementCollection:
    """An in-memory list of :class:`GraphicElement` objects.

    The collection is a plain container: adding or removing an element changes
    the list, never the page it came from.
    """

    def __init__(self) -> None:
        """Initialize a new instance of GraphicElementCollection."""
        self._elements: list[Any] = []
        self._parent: Any = None

    def add(self, element: Any) -> None:
        """Add a graphic element to the collection.

        Args:
            element: The graphic element to add.

        Raises:
            InvalidOperationException: If the element has a different parent.
        """
        if self._parent is not None:
            if getattr(element, "parent", None) is not None:
                if element.parent is not self._parent:
                    raise InvalidOperationException(
                        "Cannot add element with different parent to this collection"
                    )
            collection = getattr(element, "_collection", None)
            if collection is not None and collection is not self:
                raise InvalidOperationException(
                    "Cannot add element with different parent to this collection"
                )

        self._elements.append(element)
        if hasattr(element, "_collection"):
            element._collection = self

    def remove(self, element: Any) -> None:
        """Remove a graphic element from the collection.

        Args:
            element: The graphic element to remove.
        """
        if element in self._elements:
            self._elements.remove(element)
            if hasattr(element, "_collection"):
                element._collection = None

    @property
    def elements(self) -> list[Any]:
        """Get the collection of graphic elements."""
        return self._elements

    def __len__(self) -> int:
        return len(self._elements)

    def __iter__(self) -> Iterator[Any]:
        return iter(self._elements)

    def __getitem__(self, index: int) -> Any:
        return self._elements[index]


class GraphicsAbsorber:
    """Absorbs graphic elements from PDF pages.

    Visiting a page (or a whole document) walks the content stream, tracking
    the graphics state through ``q``/``Q``/``cm`` and descending into form
    XObjects, and collects one element per mark it puts on the page: painted
    paths, placed images (XObject and inline alike), shown text runs and ``sh``
    shading fills.

    A text element carries the box the glyphs occupy, measured with the
    renderer's own font metrics, and its resource name -- for the text
    *itself*, use :class:`~aspose_pdf.text.TextFragmentAbsorber`. Text in
    rendering mode 3 or 7 puts nothing on the page and is not collected.

    Example
    -------
    ::

        absorber = GraphicsAbsorber()
        absorber.visit(document.pages[0])
        for element in absorber.elements:
            print(element.kind, element.rectangle.width)
    """

    def __init__(self) -> None:
        """Initialize a new instance of GraphicsAbsorber."""
        self._elements: GraphicElementCollection | None = None
        self._suppressed = False

    @property
    def elements(self) -> GraphicElementCollection:
        """Get the collection of absorbed graphic elements."""
        if self._elements is None:
            self._elements = GraphicElementCollection()
        return self._elements

    def visit(self, page: Any) -> None:
        """Visit a page or a document and absorb its graphic elements.

        Args:
            page: A :class:`~aspose_pdf.pages.Page`, a
                :class:`~aspose_pdf.document.Document` (every page is visited,
                in order), or an engine ``SimplePdf``.

        Raises:
            TypeError: If the object is not a page or document.
        """
        self._elements = GraphicElementCollection()
        if self._suppressed:
            return
        for engine, page_index in _targets(page):
            for element in absorb_page_graphics(engine, page_index):
                self._elements.add(GraphicElement(element))

    def suppress_update(self) -> None:
        """Stop :meth:`visit` from collecting until :meth:`resume_update`."""
        self._suppressed = True

    def resume_update(self) -> None:
        """Resume collecting in :meth:`visit`."""
        self._suppressed = False


def _targets(source: Any) -> list[tuple[Any, int]]:
    """Return the ``(engine, page index)`` pairs *source* stands for."""
    engine = getattr(source, "_engine_pdf", None)
    index = getattr(source, "_index", None)
    if engine is not None and index is not None:  # a Page
        return [(engine, int(index))]
    document = getattr(source, "_document", None)
    if document is not None and index is not None:  # a Page over a Document
        return [(getattr(document, "_engine_pdf", None), int(index))]
    if engine is not None:  # a Document
        return [(engine, i) for i in range(len(getattr(engine, "pages", [])))]
    if hasattr(source, "pages") and hasattr(source, "page_contents"):  # SimplePdf
        return [(source, i) for i in range(len(source.pages))]
    raise TypeError(
        "GraphicsAbsorber.visit() expects a Page or a Document, "
        f"got {type(source).__name__}"
    )
