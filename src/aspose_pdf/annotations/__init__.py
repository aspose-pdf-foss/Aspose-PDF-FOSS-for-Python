"""Minimal public annotations API for the production-facing package surface."""

from __future__ import annotations

from enum import Enum, IntFlag
from typing import TYPE_CHECKING, Any, Iterator

from aspose_pdf.engine.cos import AnnotationName as Name

if TYPE_CHECKING:
    from aspose_pdf.pages import Page

__all__ = [
    "Annotation",
    "AnnotationCollection",
    "AnnotationFlags",
    "AnnotationType",
    "LinkAnnotation",
    "MarkupAnnotation",
    "Name",
]


class AnnotationFlags(IntFlag):
    """Flags that define annotation behaviour."""

    DEFAULT = 0
    INVISIBLE = 1
    HIDDEN = 2
    PRINT = 4
    NO_ZOOM = 8
    NO_ROTATE = 16
    NO_VIEW = 32
    READ_ONLY = 64
    LOCKED = 128
    TOGGLE_NO_VIEW = 256


class AnnotationType(str, Enum):
    """Known annotation subtype names (PDF 32000-1:2008, Table 169)."""

    TEXT = "Text"
    LINK = "Link"
    FREE_TEXT = "FreeText"
    LINE = "Line"
    SQUARE = "Square"
    CIRCLE = "Circle"
    POLYGON = "Polygon"
    POLY_LINE = "PolyLine"
    HIGHLIGHT = "Highlight"
    UNDERLINE = "Underline"
    SQUIGGLY = "Squiggly"
    STRIKE_OUT = "StrikeOut"
    STAMP = "Stamp"
    CARET = "Caret"
    INK = "Ink"
    POPUP = "Popup"
    FILE_ATTACHMENT = "FileAttachment"
    SOUND = "Sound"
    MOVIE = "Movie"
    WIDGET = "Widget"
    SCREEN = "Screen"
    PRINTER_MARK = "PrinterMark"
    TRAP_NET = "TrapNet"
    WATERMARK = "Watermark"
    REDACT = "Redact"


def _subtype_value(subtype: Any) -> str:
    """Normalise an :class:`AnnotationType` or plain string to its wire value."""
    if isinstance(subtype, AnnotationType):
        return subtype.value
    return str(subtype)


class Annotation:
    """Live view over a single annotation on a page."""

    def __init__(self, page: "Page", index: int, data: dict[str, Any]) -> None:
        self._page = page
        self._index = index
        self._data = dict(data)

    def _sync(self) -> None:
        annotations = self._page._document._engine_pdf.get_annotations(self._page.index)
        self._data = dict(annotations[self._index])

    def _update(self, **changes: Any) -> None:
        self._page._document._engine_pdf.update_annotation(
            self._page.index, self._index, changes
        )
        self._sync()

    @property
    def subtype(self) -> str:
        return str(self._data.get("Subtype", ""))

    @property
    def contents(self) -> str:
        return str(self._data.get("Contents", ""))

    @contents.setter
    def contents(self, value: str) -> None:
        self._update(Contents=value)

    @property
    def rect(self) -> tuple[float, float, float, float]:
        rect = self._data.get("Rect", (0, 0, 0, 0))
        return tuple(float(v) for v in rect)

    @rect.setter
    def rect(self, value: tuple[float, float, float, float]) -> None:
        self._update(Rect=tuple(float(v) for v in value))

    @property
    def title(self) -> str:
        return str(self._data.get("T", ""))

    @title.setter
    def title(self, value: str) -> None:
        self._update(T=value)

    @property
    def author(self) -> str:
        return self.title

    @author.setter
    def author(self, value: str) -> None:
        self.title = value

    @property
    def has_appearance(self) -> bool:
        return bool(self._data.get("has_AP", False))

    @property
    def appearance_normal(self) -> bytes:
        value = self._data.get("AP_N", b"")
        if isinstance(value, bytes):
            return value
        if isinstance(value, bytearray):
            return bytes(value)
        return b""

    @appearance_normal.setter
    def appearance_normal(self, value: bytes | bytearray | None) -> None:
        if value is None:
            self._update(AP=None)
            return
        self._update(AP={"N": bytes(value)})

    @property
    def properties(self) -> dict[str, Any]:
        """Type-specific annotation entries beyond the common fields.

        Returns a copy of the annotation's defining attributes that are not
        already surfaced as :attr:`subtype`, :attr:`rect`, :attr:`contents`,
        :attr:`title`, or appearance -- for example ``"C"`` (colour), ``"IC"``,
        ``"QuadPoints"``, ``"L"``, ``"Vertices"``, ``"InkList"``, or ``"Name"``.
        These survive a save/load round trip for every annotation subtype. PDF
        name values are returned as :class:`Name` instances (a ``str`` subclass),
        so they compare equal to the plain string yet remain distinguishable.
        """
        props = self._data.get("Properties", {})
        if isinstance(props, dict):
            return dict(props)
        return {}

    def get_property(self, name: str, default: Any = None) -> Any:
        """Return a single type-specific property value (or *default*)."""
        return self.properties.get(name, default)

    def set_property(self, name: str, value: Any) -> None:
        """Set a single type-specific property (``value=None`` removes it)."""
        self._update(Properties={name: value})

    def update_properties(self, **values: Any) -> None:
        """Set several type-specific properties at once."""
        if values:
            self._update(Properties=dict(values))

    @property
    def color(self) -> tuple[float, ...]:
        """Annotation colour (``/C``) as a component tuple, empty when unset."""
        value = self.properties.get("C")
        if isinstance(value, (list, tuple)):
            return tuple(float(v) for v in value)
        return ()

    @color.setter
    def color(self, value: "tuple[float, ...] | list[float] | None") -> None:
        if value is None:
            self.set_property("C", None)
        else:
            self.set_property("C", [float(v) for v in value])

    def generate_appearance(self, *, force: bool = False) -> bool:
        """Synthesise a normal appearance stream (``/AP /N``) for this annotation.

        Builds the appearance from the annotation's geometry and colours for the
        supported shape and text-markup subtypes (``Square``, ``Circle``,
        ``Line``, ``Polygon``, ``PolyLine``, ``Ink``, ``Highlight``,
        ``Underline``, ``StrikeOut``, ``Squiggly``). Returns ``True`` when the
        annotation has an appearance after the call, ``False`` for an unsupported
        subtype or missing geometry. An existing appearance is kept unless
        *force* is given, in which case it is regenerated.
        """
        result = self._page._document._engine_pdf.generate_annotation_appearance(
            self._page.index, self._index, force=force
        )
        self._sync()
        return result


class MarkupAnnotation(Annotation):
    """Base class for markup annotations."""


class LinkAnnotation(Annotation):
    """Concrete annotation type kept for compatibility with tests/API."""


_ANNOTATION_CLASSES: dict[str, type[Annotation]] = {
    AnnotationType.LINK.value: LinkAnnotation,
    AnnotationType.TEXT.value: MarkupAnnotation,
    AnnotationType.FREE_TEXT.value: MarkupAnnotation,
    AnnotationType.LINE.value: MarkupAnnotation,
    AnnotationType.SQUARE.value: MarkupAnnotation,
    AnnotationType.CIRCLE.value: MarkupAnnotation,
    AnnotationType.POLYGON.value: MarkupAnnotation,
    AnnotationType.POLY_LINE.value: MarkupAnnotation,
    AnnotationType.HIGHLIGHT.value: MarkupAnnotation,
    AnnotationType.UNDERLINE.value: MarkupAnnotation,
    AnnotationType.SQUIGGLY.value: MarkupAnnotation,
    AnnotationType.STRIKE_OUT.value: MarkupAnnotation,
    AnnotationType.STAMP.value: MarkupAnnotation,
    AnnotationType.CARET.value: MarkupAnnotation,
    AnnotationType.INK.value: MarkupAnnotation,
    AnnotationType.FILE_ATTACHMENT.value: MarkupAnnotation,
    AnnotationType.SOUND.value: MarkupAnnotation,
    AnnotationType.REDACT.value: MarkupAnnotation,
}


class AnnotationCollection:
    """Mutable sequence-like wrapper over page annotations."""

    def __init__(self, page: "Page") -> None:
        self._page = page

    def _items(self) -> list[dict[str, Any]]:
        return self._page._document._engine_pdf.get_annotations(self._page.index)

    def __len__(self) -> int:
        return len(self._items())

    def __iter__(self) -> Iterator[Annotation]:
        for index, data in enumerate(self._items()):
            yield self._wrap(index, data)

    def __getitem__(self, index: int) -> Annotation:
        items = self._items()
        if index < 0:
            index += len(items)
        if index < 0 or index >= len(items):
            raise IndexError("Annotation index out of range")
        return self._wrap(index, items[index])

    def _wrap(self, index: int, data: dict[str, Any]) -> Annotation:
        subtype = str(data.get("Subtype", ""))
        annotation_cls = _ANNOTATION_CLASSES.get(subtype, Annotation)
        return annotation_cls(self._page, index, data)

    def add(
        self,
        subtype: str,
        rect: tuple[float, float, float, float],
        contents: str,
        *,
        title: str | None = None,
        appearance_normal: bytes | bytearray | None = None,
        properties: dict[str, Any] | None = None,
    ) -> Annotation:
        payload: dict[str, Any] = {
            "Subtype": _subtype_value(subtype),
            "Rect": tuple(float(v) for v in rect),
            "Contents": contents,
        }
        if title:
            payload["T"] = title
        if appearance_normal is not None:
            payload["AP"] = {"N": bytes(appearance_normal)}
        if properties:
            payload["Properties"] = dict(properties)
        self._page._document._engine_pdf.add_annotation(self._page.index, payload)
        return self[len(self) - 1]

    def insert(
        self,
        index: int,
        subtype: str,
        rect: tuple[float, float, float, float],
        contents: str,
        *,
        title: str | None = None,
        appearance_normal: bytes | bytearray | None = None,
        properties: dict[str, Any] | None = None,
    ) -> Annotation:
        payload: dict[str, Any] = {
            "Subtype": _subtype_value(subtype),
            "Rect": tuple(float(v) for v in rect),
            "Contents": contents,
        }
        if title:
            payload["T"] = title
        if appearance_normal is not None:
            payload["AP"] = {"N": bytes(appearance_normal)}
        if properties:
            payload["Properties"] = dict(properties)
        self._page._document._engine_pdf.insert_annotation(
            self._page.index, index, payload
        )
        if index < 0:
            index = 0
        if index >= len(self):
            index = len(self) - 1
        return self[index]

    def delete(self, index: int) -> None:
        items = self._items()
        if index < 0 or index >= len(items):
            raise IndexError("Annotation index out of range")
        self._page._document._engine_pdf.delete_annotation(self._page.index, index)

    def clear(self) -> None:
        self._page._document._engine_pdf.clear_annotations(self._page.index)

    def generate_appearances(self, *, force: bool = False) -> int:
        """Synthesise missing appearance streams for every annotation on the page.

        Returns the number of appearances created. Annotations that already have
        an appearance are left untouched unless *force* is given. See
        :meth:`Annotation.generate_appearance` for the supported subtypes.
        """
        return self._page._document._engine_pdf.generate_appearances(
            self._page.index, force=force
        )
