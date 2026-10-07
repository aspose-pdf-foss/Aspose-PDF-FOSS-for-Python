"""The public annotations API: one class per subtype, over one property channel.

Every annotation is a live view over the page's ``/Annots`` entry. The generic
:class:`Annotation` reads and writes any entry through
:attr:`~Annotation.properties`, which is what preserves subtypes this API has no
class for; the typed classes below put names and types on the entries their own
subtype is defined by -- ``/IC``, ``/QuadPoints``, ``/Vertices``, ``/InkList``,
``/L``, ``/LE``, ``/CA``, ``/BS`` -- so a caller does not have to remember which
spelling the standard uses.

Replies and popups are the one thing the property channel cannot carry: they are
*references* between annotations rather than values. :meth:`Annotation.reply`,
:attr:`Annotation.replies`, :attr:`Annotation.in_reply_to` and
:meth:`Annotation.add_popup` are those references.
"""

from __future__ import annotations

from collections.abc import Iterator
from enum import Enum, IntFlag
from typing import TYPE_CHECKING, Any

from aspose_pdf.engine.cos import AnnotationName as Name

if TYPE_CHECKING:
    from aspose_pdf.pages import Page

__all__ = [
    "Annotation",
    "AnnotationCollection",
    "AnnotationFlags",
    "AnnotationType",
    "CircleAnnotation",
    "FileAttachmentAnnotation",
    "FreeTextAnnotation",
    "HighlightAnnotation",
    "InkAnnotation",
    "LineAnnotation",
    "LinkAnnotation",
    "MarkupAnnotation",
    "Name",
    "PolyLineAnnotation",
    "PolygonAnnotation",
    "PopupAnnotation",
    "RedactAnnotation",
    "SquareAnnotation",
    "SquigglyAnnotation",
    "StampAnnotation",
    "StrikeOutAnnotation",
    "TextAnnotation",
    "UnderlineAnnotation",
    "quad_from_rect",
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


class AnnotationType(str, Enum):  # noqa: UP042
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


def _numbers(values: Any, what: str) -> list[float]:
    """*values* flattened to numbers: pairs, nested groups, or a flat sequence."""
    from aspose_pdf.exceptions import PdfValidationException

    flat: list[float] = []

    def walk(value: Any) -> None:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            flat.append(float(value))
            return
        if isinstance(value, (str, bytes, bytearray)) or value is None:
            raise PdfValidationException(f"{what} is made of numbers.")
        try:
            items = list(value)
        except TypeError:
            raise PdfValidationException(f"{what} is made of numbers.") from None
        for item in items:
            walk(item)

    walk(values)
    return flat


def _pairs(values: list[float], what: str, group: int) -> list[Any]:
    """*values* grouped into points, and those into groups of *group* points."""
    from aspose_pdf.exceptions import PdfValidationException

    if len(values) % (2 * group) != 0 or not values:
        raise PdfValidationException(
            f"{what} needs {2 * group} numbers per entry, not {len(values)} in all."
        )
    points = [(values[i], values[i + 1]) for i in range(0, len(values), 2)]
    if group == 1:
        return points
    return [tuple(points[i : i + group]) for i in range(0, len(points), group)]


def _number_property(key: str, doc: str) -> property:
    """A property over one numeric annotation entry."""

    def getter(self: Annotation) -> float | None:
        value = self.get_property(key)
        return float(value) if isinstance(value, (int, float)) else None

    def setter(self: Annotation, value: float | None) -> None:
        self.set_property(key, None if value is None else float(value))

    return property(getter, setter, doc=doc)


def _text_property(key: str, doc: str) -> property:
    """A property over one text-string annotation entry."""

    def getter(self: Annotation) -> str | None:
        value = self.get_property(key)
        if isinstance(value, bytes):
            return value.decode("latin-1")
        return str(value) if value is not None else None

    def setter(self: Annotation, value: str | None) -> None:
        self.set_property(key, None if value is None else str(value))

    return property(getter, setter, doc=doc)


def _bool_property(key: str, doc: str) -> property:
    """A property over one boolean annotation entry."""

    def getter(self: Annotation) -> bool:
        return bool(self.get_property(key))

    def setter(self: Annotation, value: bool | None) -> None:
        self.set_property(key, None if value is None else bool(value))

    return property(getter, setter, doc=doc)


def _name_property(key: str, doc: str) -> property:
    """A property over one PDF-name annotation entry."""

    def getter(self: Annotation) -> str | None:
        value = self.get_property(key)
        return str(value) if value is not None else None

    def setter(self: Annotation, value: str | None) -> None:
        self.set_property(key, None if value is None else Name(str(value).lstrip("/")))

    return property(getter, setter, doc=doc)


def _color_property(key: str, doc: str) -> property:
    """A property over one colour-array annotation entry (grey, RGB or CMYK)."""

    def getter(self: Annotation) -> tuple[float, ...]:
        value = self.get_property(key)
        if isinstance(value, (list, tuple)):
            return tuple(float(item) for item in value)
        return ()

    def setter(self: Annotation, value: Any) -> None:
        if value is None:
            self.set_property(key, None)
            return
        from aspose_pdf.engine.content_authoring import normalize_color

        self.set_property(key, [float(item) for item in normalize_color(value)])

    return property(getter, setter, doc=doc)


def _points_property(key: str, doc: str, *, group: int = 1) -> property:
    """A property over a coordinate array: points, or groups of *group* points."""

    def getter(self: Annotation) -> list[Any]:
        value = self.get_property(key)
        if not isinstance(value, (list, tuple)) or not value:
            return []
        try:
            return _pairs(_numbers(value, key), key, group)
        except Exception:
            # What the file holds is not this shape. The raw entry is still
            # readable through ``properties``; this view simply has nothing to
            # show, which is better than half a quadrilateral.
            return []

    def setter(self: Annotation, value: Any) -> None:
        if value is None:
            self.set_property(key, None)
            return
        flat = _numbers(value, key)
        _pairs(flat, key, group)  # checked before anything is written
        self.set_property(key, flat)

    return property(getter, setter, doc=doc)


def _stroke_list_property(key: str, doc: str) -> property:
    """A property over an array of coordinate arrays, which is ``/InkList``."""

    def getter(self: Annotation) -> list[list[tuple[float, float]]]:
        value = self.get_property(key)
        if not isinstance(value, (list, tuple)):
            return []
        strokes = []
        for stroke in value:
            try:
                strokes.append(_pairs(_numbers(stroke, key), key, 1))
            except Exception:
                continue
        return strokes

    def setter(self: Annotation, value: Any) -> None:
        if value is None:
            self.set_property(key, None)
            return
        strokes = []
        for stroke in value:
            flat = _numbers(stroke, key)
            _pairs(flat, key, 1)
            strokes.append(flat)
        self.set_property(key, strokes)

    return property(getter, setter, doc=doc)


class Annotation:
    """Live view over a single annotation on a page."""

    # Declared, so a misspelled name raises instead of being taken and
    # quietly lost; see ``Page`` in ``pages.py``.
    __slots__ = ("_data", "_index", "_page")


    def __init__(self, page: Page, index: int, data: dict[str, Any]) -> None:
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

    color = _color_property(
        "C",
        """The annotation's colour (``/C``) as a component tuple, empty when unset.

        What it colours depends on the subtype (Table 164): the border of a shape,
        the mark of a text markup, the icon of a note, the title bar of a popup.
        One, three or four components -- grey, RGB or CMYK -- and it is read the
        way every other colour in the package is: a ``"#rrggbb"`` string, 0..1 or
        0..255 components, or an :class:`~aspose_pdf.color.Color`. ``None`` removes
        the entry, which leaves a viewer to choose.
        """,
    )

    def _links(self) -> dict[str, Any]:
        """What this annotation says about the others around it, by index."""
        return self._page._document._engine_pdf.annotation_links(
            self._page.index, self._index
        )

    @property
    def in_reply_to(self) -> Annotation | None:
        """The annotation this one replies to (``/IRT``), or ``None``."""
        index = self._links()["in_reply_to"]
        return None if index is None else self._page.annotations[index]

    @property
    def replies(self) -> list[Annotation]:
        """Every annotation on this page that replies to this one (``/IRT``).

        In the order the page lists them, which is the order they were added.
        """
        annotations = self._page.annotations
        return [annotations[index] for index in self._links()["replies"]]

    @property
    def popup(self) -> Annotation | None:
        """The popup window this annotation owns (``/Popup``), or ``None``."""
        index = self._links()["popup"]
        return None if index is None else self._page.annotations[index]

    @property
    def parent_annotation(self) -> Annotation | None:
        """The annotation a popup belongs to (``/Parent``), or ``None``.

        Named apart from ``parent`` so that nothing reads it as the page.
        """
        index = self._links()["parent"]
        return None if index is None else self._page.annotations[index]

    def reply(
        self,
        contents: str,
        *,
        title: str | None = None,
        rect: tuple[float, float, float, float] | None = None,
        subtype: str = "Text",
        properties: dict[str, Any] | None = None,
    ) -> Annotation:
        """Add an annotation replying to this one, and return it.

        ISO 32000-1 12.5.6.2: the reply carries ``/IRT``, the annotation it is in
        reply to, and ``/RT /Reply``; a viewer then shows the two as one thread.
        *rect* defaults to this annotation's own, which is where a viewer puts a
        reply that does not ask for a place of its own.
        """
        payload: dict[str, Any] = {
            "Subtype": _subtype_value(subtype),
            "Rect": tuple(float(value) for value in (rect or self.rect)),
            "Contents": contents,
        }
        if title:
            payload["T"] = title
        if properties:
            payload["Properties"] = dict(properties)
        index = self._page._document._engine_pdf.add_annotation_reply(
            self._page.index, self._index, payload
        )
        return self._page.annotations[index]

    def add_popup(
        self,
        rect: tuple[float, float, float, float] | None = None,
        *,
        is_open: bool = False,
    ) -> Annotation:
        """Add this annotation's popup window, linked both ways, and return it.

        12.5.6.14: the popup's ``/Parent`` names this annotation and this
        annotation's ``/Popup`` names the popup -- one without the other leaves a
        window a viewer cannot associate with anything. *rect* defaults to a box
        beside the annotation, which is where a viewer would put one.
        """
        if rect is None:
            x0, y0, x1, y1 = self.rect
            rect = (x1, y0, x1 + max(144.0, x1 - x0), y0 + max(72.0, y1 - y0))
        index = self._page._document._engine_pdf.add_annotation_popup(
            self._page.index,
            self._index,
            tuple(float(value) for value in rect),
            is_open=is_open,
        )
        return self._page.annotations[index]

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


def _color_components(color: Any) -> list[float] | None:
    """*color* as the component list a colour entry holds, or ``None``."""
    if color is None:
        return None
    from aspose_pdf.engine.content_authoring import normalize_color

    return [float(value) for value in normalize_color(color)]


def _bounding_rect(flat: list[float], border_width: float | None) -> tuple[float, ...]:
    """The box around a coordinate list, grown enough to hold what is drawn.

    12.5.2 requires the annotation rectangle to contain the annotation, so a
    stroke of some width needs room for half of it on each side -- plus a little,
    because a join or a line ending reaches past the stroke itself.
    """
    from aspose_pdf.exceptions import PdfValidationException

    if not flat:
        raise PdfValidationException("There are no points to put a rectangle around.")
    xs = flat[0::2]
    ys = flat[1::2]
    margin = float(border_width if border_width is not None else 1.0) + 2.0
    return (min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin)


def quad_from_rect(
    x0: float, y0: float, x1: float, y1: float
) -> tuple[tuple[float, float], ...]:
    """A rectangle as the four corners a ``/QuadPoints`` quad is written with.

    ISO 32000-1 12.5.6.10 lists the corners of each quadrilateral, and the order
    every viewer actually expects -- the one Acrobat writes -- is upper-left,
    upper-right, lower-left, lower-right. Getting that order wrong is the classic
    text-markup bug: the highlight comes out as a bow tie.
    """
    left, right = min(x0, x1), max(x0, x1)
    bottom, top = min(y0, y1), max(y0, y1)
    return ((left, top), (right, top), (left, bottom), (right, bottom))


class MarkupAnnotation(Annotation):
    """An annotation a person made, with the entries every one of them shares.

    ISO 32000-1 12.5.6.2: a markup annotation has an author (:attr:`title`), what
    they said (:attr:`contents`), when they said it (:attr:`creation_date`), what
    it is about (:attr:`subject`), how solid it is drawn (:attr:`opacity`), and it
    can be replied to and carry a popup window.
    """

    # Empty: a subclass without its own would hand back a __dict__.
    __slots__ = ()

    opacity = _number_property(
        "CA",
        """How opaque the annotation is drawn, 0 to 1 (``/CA``).

        ``None`` when the annotation does not say, which means fully opaque. This
        is the whole annotation's alpha, applied to its appearance as a group
        (12.5.6.2); the colours inside the appearance are not touched.
        """,
    )
    subject = _text_property(
        "Subj", "What the annotation is about, as a short line (``/Subj``)."
    )
    creation_date = _text_property(
        "CreationDate",
        """When the annotation was made, as a PDF date string (``/CreationDate``).

        Written in the ``D:YYYYMMDDHHmmSSOHH'mm'`` form of 7.9.4, which is what
        ``datetime.strftime("D:%Y%m%d%H%M%S+00'00'")`` produces. :attr:`modified`
        (``/M``) is the one the standard puts on every annotation, not just these.
        """,
    )
    modified = _text_property(
        "M", "When the annotation was last changed, as a PDF date string (``/M``)."
    )
    @property
    def _border(self) -> dict[str, Any]:
        """The ``/BS`` border-style dictionary as it stands."""
        value = self.get_property("BS")
        return dict(value) if isinstance(value, dict) else {}

    def _set_border(self, key: str, value: Any) -> None:
        """Change one ``/BS`` entry, leaving the others as they were."""
        border = self._border
        if value is None:
            border.pop(key, None)
        else:
            border[key] = value
        self.set_property("BS", border or None)

    @property
    def border_width(self) -> float | None:
        """The border's width in points (``/BS /W``), or ``None`` when unset.

        Unset means 1 point, which is the entry's default (Table 166). A width of
        0 is a border that is not drawn at all. The legacy ``/Border`` array is
        read as well where there is no ``/BS``, by whatever draws the annotation.
        """
        value = self._border.get("W")
        return float(value) if isinstance(value, (int, float)) else None

    @border_width.setter
    def border_width(self, value: float | None) -> None:
        self._set_border("W", None if value is None else float(value))

    @property
    def border_style(self) -> str | None:
        """The border's style (``/BS /S``): ``S``, ``D``, ``B``, ``I`` or ``U``.

        Solid, Dashed, Beveled, Inset or Underline (Table 166). ``D`` is drawn
        with :attr:`border_dash`.
        """
        value = self._border.get("S")
        return str(value) if value is not None else None

    @border_style.setter
    def border_style(self, value: str | None) -> None:
        self._set_border(
            "S", None if value is None else Name(str(value).lstrip("/"))
        )

    @property
    def border_dash(self) -> tuple[float, ...]:
        """The dash pattern of a dashed border (``/BS /D``), empty when unset."""
        value = self._border.get("D")
        if isinstance(value, (list, tuple)):
            return tuple(float(item) for item in value)
        return ()

    @border_dash.setter
    def border_dash(self, value: Any) -> None:
        if value is None:
            self._set_border("D", None)
            return
        lengths = _numbers(value, "A border dash pattern")
        self._set_border("D", lengths)



class LinkAnnotation(Annotation):
    """A clickable area that goes somewhere (``Link``).

    Authored through :meth:`aspose_pdf.pages.Page.add_link`, which takes an
    action, a destination, or the name of one.
    """

    # Empty: a subclass without its own would hand back a __dict__.
    __slots__ = ()


class _ShapeAnnotation(MarkupAnnotation):
    """A markup annotation with an inside as well as an outline."""

    __slots__ = ()

    interior_color = _color_property(
        "IC",
        """The colour inside the shape (``/IC``), empty when it is not filled.

        :attr:`color` (``/C``) is the outline; this is the fill. Grey, RGB and
        CMYK are all read, since Table 173 types the entry by its length.
        """,
    )


class SquareAnnotation(_ShapeAnnotation):
    """A rectangle drawn inside the annotation's rectangle (``Square``)."""

    __slots__ = ()


class CircleAnnotation(_ShapeAnnotation):
    """An ellipse drawn inside the annotation's rectangle (``Circle``)."""

    __slots__ = ()


class LineAnnotation(_ShapeAnnotation):
    """A single line between two points (``Line``)."""

    __slots__ = ()

    line_endings = property(
        lambda self: tuple(
            str(item) for item in (self.get_property("LE") or ()) if item is not None
        ),
        lambda self, value: self.set_property(
            "LE",
            None
            if value is None
            else [Name(str(item).lstrip("/")) for item in value],
        ),
        doc="""What is drawn at each end of the line (``/LE``).

        A pair of names from Table 176 -- ``OpenArrow``, ``ClosedArrow``,
        ``ROpenArrow``, ``RClosedArrow``, ``Circle``, ``Square``, ``Diamond``,
        ``Butt``, ``Slash``, ``None`` -- for the start and the end in that order.
        """,
    )

    @property
    def start(self) -> tuple[float, float] | None:
        """Where the line starts, from ``/L``."""
        points = self._line_points()
        return points[0] if points else None

    @property
    def end(self) -> tuple[float, float] | None:
        """Where the line ends, from ``/L``."""
        points = self._line_points()
        return points[1] if len(points) > 1 else None

    def _line_points(self) -> list[tuple[float, float]]:
        value = self.get_property("L")
        if not isinstance(value, (list, tuple)):
            return []
        try:
            return _pairs(_numbers(value, "L"), "L", 1)
        except Exception:
            return []

    def set_line(
        self, start: tuple[float, float], end: tuple[float, float]
    ) -> LineAnnotation:
        """Put the line between *start* and *end* (``/L``)."""
        flat = _numbers([start, end], "A line")
        if len(flat) != 4:
            from aspose_pdf.exceptions import PdfValidationException

            raise PdfValidationException("A line is two points (``/L``).")
        self.set_property("L", flat)
        return self


class PolygonAnnotation(_ShapeAnnotation):
    """A closed shape through a list of points (``Polygon``)."""

    __slots__ = ()

    vertices = _points_property(
        "Vertices",
        """The points the shape runs through (``/Vertices``), as ``(x, y)`` pairs.

        Assigning takes pairs or a flat sequence of numbers; a count that is not
        even is refused rather than written with a coordinate missing.
        """,
    )


class PolyLineAnnotation(PolygonAnnotation):
    """An open shape through a list of points (``PolyLine``)."""

    __slots__ = ()

    line_endings = LineAnnotation.line_endings


class InkAnnotation(MarkupAnnotation):
    """Freehand strokes (``Ink``)."""

    __slots__ = ()

    ink_list = _stroke_list_property(
        "InkList",
        """The strokes, each a list of ``(x, y)`` points (``/InkList``).

        One entry per stroke of the pen: an array of arrays, which is what makes
        two separate marks two strokes rather than one with a jump in it.
        """,
    )


class _TextMarkupAnnotation(MarkupAnnotation):
    """A markup annotation over a run of text, which it names by quadrilaterals."""

    __slots__ = ()

    quad_points = _points_property(
        "QuadPoints",
        """The quadrilaterals the markup covers (``/QuadPoints``).

        Each entry is four ``(x, y)`` corners -- upper-left, upper-right,
        lower-left, lower-right, which is the order viewers expect (12.5.6.10);
        :func:`quad_from_rect` writes a rectangle in it. Assigning takes those
        groups, or a flat sequence of numbers, and a count that is not a multiple
        of eight is refused.
        """,
        group=4,
    )


class HighlightAnnotation(_TextMarkupAnnotation):
    """Text marked with a highlighter (``Highlight``)."""

    __slots__ = ()


class UnderlineAnnotation(_TextMarkupAnnotation):
    """Text with a line under it (``Underline``)."""

    __slots__ = ()


class StrikeOutAnnotation(_TextMarkupAnnotation):
    """Text with a line through it (``StrikeOut``)."""

    __slots__ = ()


class SquigglyAnnotation(_TextMarkupAnnotation):
    """Text with a wavy line under it (``Squiggly``)."""

    __slots__ = ()


class RedactAnnotation(_TextMarkupAnnotation):
    """An area marked for removal (``Redact``).

    The annotation marks; it does not remove. ``Document.redact_text`` is what
    takes text out of the content stream.
    """

    __slots__ = ()

    overlay_text = _text_property(
        "OverlayText", "What to draw over the area once it is applied (``/OverlayText``)."
    )
    interior_color = _ShapeAnnotation.interior_color


class FreeTextAnnotation(MarkupAnnotation):
    """Text drawn straight onto the page (``FreeText``)."""

    __slots__ = ()

    default_appearance = _text_property(
        "DA",
        """The appearance string the text is set with (``/DA``).

        A content-stream fragment naming the font, size and colour, as in
        ``"/Helv 12 Tf 0 g"``. The appearance generator reads it.
        """,
    )
    rich_text = _text_property(
        "RC", "The rich-text form of the contents (``/RC``), an XHTML fragment."
    )

    @property
    def alignment(self) -> int | None:
        """How the lines are set (``/Q``): 0 left, 1 centred, 2 right."""
        value = self.get_property("Q")
        return int(value) if isinstance(value, (int, float)) else None

    @alignment.setter
    def alignment(self, value: int | str | None) -> None:
        if value is None:
            self.set_property("Q", None)
            return
        if isinstance(value, str):
            names = {"left": 0, "center": 1, "centre": 1, "right": 2}
            key = value.strip().lower()
            if key not in names:
                from aspose_pdf.exceptions import PdfValidationException

                raise PdfValidationException(
                    "Alignment is 'left', 'center' or 'right' (``/Q`` 0, 1, 2)."
                )
            value = names[key]
        if int(value) not in (0, 1, 2):
            from aspose_pdf.exceptions import PdfValidationException

            raise PdfValidationException("``/Q`` is 0 (left), 1 (centre) or 2 (right).")
        self.set_property("Q", int(value))


class TextAnnotation(MarkupAnnotation):
    """A sticky note: an icon that opens a window (``Text``)."""

    __slots__ = ()

    icon = _name_property(
        "Name",
        """Which icon a viewer draws (``/Name``).

        ``Comment``, ``Key``, ``Note``, ``Help``, ``NewParagraph``, ``Paragraph``
        and ``Insert`` are the ones Table 172 names and the appearance generator
        draws; an unknown name falls back to the subtype's default, as a viewer
        does.
        """,
    )
    is_open = _bool_property(
        "Open", "Whether the note's window starts open (``/Open``)."
    )
    state = _text_property("State", "The review state of the annotation (``/State``).")
    state_model = _text_property(
        "StateModel", "Which set of states ``/State`` is from (``/StateModel``)."
    )


class StampAnnotation(MarkupAnnotation):
    """A rubber stamp (``Stamp``)."""

    __slots__ = ()

    icon = _name_property(
        "Name", "Which stamp it is (``/Name``), e.g. ``Approved`` or ``Draft``."
    )


class FileAttachmentAnnotation(MarkupAnnotation):
    """A file attached at a point on the page (``FileAttachment``)."""

    __slots__ = ()

    icon = _name_property(
        "Name",
        "Which icon a viewer draws (``/Name``): ``PushPin``, ``Graph``, "
        "``Paperclip`` or ``Tag``.",
    )


class PopupAnnotation(Annotation):
    """The window that shows another annotation's contents (``Popup``).

    It belongs to the annotation that owns it, which is :attr:`parent`; a popup is
    not a markup annotation of its own and carries no author or date.
    """

    __slots__ = ()

    is_open = _bool_property(
        "Open", "Whether the window starts open (``/Open``)."
    )



#: Which class reads which subtype. A subtype with no class of its own is still
#: read -- as :class:`MarkupAnnotation` where it is one, and as
#: :class:`Annotation` otherwise -- so nothing in a file becomes unreadable for
#: want of a class.
_ANNOTATION_CLASSES: dict[str, type[Annotation]] = {
    AnnotationType.LINK.value: LinkAnnotation,
    AnnotationType.TEXT.value: TextAnnotation,
    AnnotationType.FREE_TEXT.value: FreeTextAnnotation,
    AnnotationType.LINE.value: LineAnnotation,
    AnnotationType.SQUARE.value: SquareAnnotation,
    AnnotationType.CIRCLE.value: CircleAnnotation,
    AnnotationType.POLYGON.value: PolygonAnnotation,
    AnnotationType.POLY_LINE.value: PolyLineAnnotation,
    AnnotationType.HIGHLIGHT.value: HighlightAnnotation,
    AnnotationType.UNDERLINE.value: UnderlineAnnotation,
    AnnotationType.SQUIGGLY.value: SquigglyAnnotation,
    AnnotationType.STRIKE_OUT.value: StrikeOutAnnotation,
    AnnotationType.STAMP.value: StampAnnotation,
    AnnotationType.CARET.value: MarkupAnnotation,
    AnnotationType.INK.value: InkAnnotation,
    AnnotationType.FILE_ATTACHMENT.value: FileAttachmentAnnotation,
    AnnotationType.SOUND.value: MarkupAnnotation,
    AnnotationType.POPUP.value: PopupAnnotation,
    AnnotationType.REDACT.value: RedactAnnotation,
}


class AnnotationCollection:
    """Mutable sequence-like wrapper over page annotations."""

    # Declared, so a misspelled name raises instead of being taken and
    # quietly lost; see ``Page`` in ``pages.py``.
    __slots__ = ("_page",)


    def __init__(self, page: Page) -> None:
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

    # -- one factory per subtype ------------------------------------------
    #
    # Each of these is ``add`` with the entries that subtype is defined by
    # already in the right place, under the names the standard gives them. The
    # generic ``add`` still takes any subtype, including one this API has no
    # class for.

    def _add(
        self,
        subtype: str,
        rect: tuple[float, float, float, float],
        contents: str,
        title: str | None,
        properties: dict[str, Any],
        extra: dict[str, Any] | None,
    ) -> Annotation:
        """One factory's work: fold its named entries into the generic add."""
        merged = {key: value for key, value in properties.items() if value is not None}
        if extra:
            merged.update(extra)
        return self.add(
            subtype, rect, contents, title=title, properties=merged or None
        )

    def add_text(
        self,
        rect: tuple[float, float, float, float],
        contents: str = "",
        *,
        title: str | None = None,
        icon: str | None = None,
        is_open: bool = False,
        color: Any = None,
        opacity: float | None = None,
        properties: dict[str, Any] | None = None,
    ) -> TextAnnotation:
        """Add a sticky note (``Text``) -- an icon that opens a window."""
        annotation = self._add(
            AnnotationType.TEXT.value,
            rect,
            contents,
            title,
            {
                "Name": Name(icon.lstrip("/")) if icon else None,
                "Open": True if is_open else None,
                "C": _color_components(color),
                "CA": opacity,
            },
            properties,
        )
        return annotation  # type: ignore[return-value]

    def add_free_text(
        self,
        rect: tuple[float, float, float, float],
        contents: str = "",
        *,
        title: str | None = None,
        default_appearance: str | None = None,
        alignment: int | str | None = None,
        color: Any = None,
        opacity: float | None = None,
        properties: dict[str, Any] | None = None,
    ) -> FreeTextAnnotation:
        """Add text drawn straight onto the page (``FreeText``)."""
        annotation = self._add(
            AnnotationType.FREE_TEXT.value,
            rect,
            contents,
            title,
            {
                "DA": default_appearance,
                "C": _color_components(color),
                "CA": opacity,
            },
            properties,
        )
        if alignment is not None:
            annotation.alignment = alignment  # type: ignore[attr-defined]
        return annotation  # type: ignore[return-value]

    def add_square(
        self,
        rect: tuple[float, float, float, float],
        contents: str = "",
        *,
        title: str | None = None,
        color: Any = (0.0, 0.0, 0.0),
        interior_color: Any = None,
        border_width: float | None = None,
        opacity: float | None = None,
        properties: dict[str, Any] | None = None,
    ) -> SquareAnnotation:
        """Add a rectangle (``Square``) drawn inside *rect*."""
        return self._shape(
            AnnotationType.SQUARE.value,
            rect,
            contents,
            title,
            color,
            interior_color,
            border_width,
            opacity,
            properties,
        )

    def add_circle(
        self,
        rect: tuple[float, float, float, float],
        contents: str = "",
        *,
        title: str | None = None,
        color: Any = (0.0, 0.0, 0.0),
        interior_color: Any = None,
        border_width: float | None = None,
        opacity: float | None = None,
        properties: dict[str, Any] | None = None,
    ) -> CircleAnnotation:
        """Add an ellipse (``Circle``) drawn inside *rect*."""
        return self._shape(
            AnnotationType.CIRCLE.value,
            rect,
            contents,
            title,
            color,
            interior_color,
            border_width,
            opacity,
            properties,
        )

    def _shape(
        self,
        subtype: str,
        rect: Any,
        contents: str,
        title: str | None,
        color: Any,
        interior_color: Any,
        border_width: float | None,
        opacity: float | None,
        properties: dict[str, Any] | None,
        extra: dict[str, Any] | None = None,
    ) -> Any:
        """The shared part of every shape factory."""
        entries = {
            "C": _color_components(color),
            "IC": _color_components(interior_color),
            "CA": opacity,
        }
        if border_width is not None:
            entries["BS"] = {"W": float(border_width), "S": Name("S")}
        if extra:
            entries.update(extra)
        return self._add(subtype, rect, contents, title, entries, properties)

    def add_line(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        contents: str = "",
        *,
        title: str | None = None,
        color: Any = (0.0, 0.0, 0.0),
        interior_color: Any = None,
        line_endings: Any = None,
        border_width: float | None = None,
        opacity: float | None = None,
        rect: tuple[float, float, float, float] | None = None,
        properties: dict[str, Any] | None = None,
    ) -> LineAnnotation:
        """Add a line (``Line``) from *start* to *end*.

        *rect* defaults to the line's own bounding box grown by the border width,
        since 12.5.2 requires the rectangle to contain what the annotation draws.
        """
        points = _numbers([start, end], "A line")
        if len(points) != 4:
            from aspose_pdf.exceptions import PdfValidationException

            raise PdfValidationException("A line is two points.")
        if rect is None:
            margin = float(border_width if border_width is not None else 1.0) + 2.0
            rect = (
                min(points[0], points[2]) - margin,
                min(points[1], points[3]) - margin,
                max(points[0], points[2]) + margin,
                max(points[1], points[3]) + margin,
            )
        extra: dict[str, Any] = {"L": points}
        if line_endings is not None:
            extra["LE"] = [Name(str(item).lstrip("/")) for item in line_endings]
        return self._shape(
            AnnotationType.LINE.value,
            rect,
            contents,
            title,
            color,
            interior_color,
            border_width,
            opacity,
            properties,
            extra,
        )

    def add_polygon(
        self,
        vertices: Any,
        contents: str = "",
        *,
        title: str | None = None,
        color: Any = (0.0, 0.0, 0.0),
        interior_color: Any = None,
        border_width: float | None = None,
        opacity: float | None = None,
        rect: tuple[float, float, float, float] | None = None,
        properties: dict[str, Any] | None = None,
    ) -> PolygonAnnotation:
        """Add a closed shape (``Polygon``) through *vertices*."""
        return self._poly(
            AnnotationType.POLYGON.value,
            vertices,
            contents,
            title,
            color,
            interior_color,
            None,
            border_width,
            opacity,
            rect,
            properties,
        )

    def add_polyline(
        self,
        vertices: Any,
        contents: str = "",
        *,
        title: str | None = None,
        color: Any = (0.0, 0.0, 0.0),
        interior_color: Any = None,
        line_endings: Any = None,
        border_width: float | None = None,
        opacity: float | None = None,
        rect: tuple[float, float, float, float] | None = None,
        properties: dict[str, Any] | None = None,
    ) -> PolyLineAnnotation:
        """Add an open shape (``PolyLine``) through *vertices*."""
        return self._poly(
            AnnotationType.POLY_LINE.value,
            vertices,
            contents,
            title,
            color,
            interior_color,
            line_endings,
            border_width,
            opacity,
            rect,
            properties,
        )

    def _poly(
        self,
        subtype: str,
        vertices: Any,
        contents: str,
        title: str | None,
        color: Any,
        interior_color: Any,
        line_endings: Any,
        border_width: float | None,
        opacity: float | None,
        rect: Any,
        properties: dict[str, Any] | None,
    ) -> Any:
        flat = _numbers(vertices, "Vertices")
        _pairs(flat, "Vertices", 1)
        if rect is None:
            rect = _bounding_rect(flat, border_width)
        extra: dict[str, Any] = {"Vertices": flat}
        if line_endings is not None:
            extra["LE"] = [Name(str(item).lstrip("/")) for item in line_endings]
        return self._shape(
            subtype,
            rect,
            contents,
            title,
            color,
            interior_color,
            border_width,
            opacity,
            properties,
            extra,
        )

    def add_ink(
        self,
        strokes: Any,
        contents: str = "",
        *,
        title: str | None = None,
        color: Any = (0.0, 0.0, 0.0),
        border_width: float | None = None,
        opacity: float | None = None,
        rect: tuple[float, float, float, float] | None = None,
        properties: dict[str, Any] | None = None,
    ) -> InkAnnotation:
        """Add freehand strokes (``Ink``): a list of lists of ``(x, y)`` points."""
        prepared = []
        everything: list[float] = []
        for stroke in strokes:
            flat = _numbers(stroke, "InkList")
            _pairs(flat, "InkList", 1)
            prepared.append(flat)
            everything.extend(flat)
        if not prepared:
            from aspose_pdf.exceptions import PdfValidationException

            raise PdfValidationException("Ink needs at least one stroke.")
        if rect is None:
            rect = _bounding_rect(everything, border_width)
        entries: dict[str, Any] = {
            "InkList": prepared,
            "C": _color_components(color),
            "CA": opacity,
        }
        if border_width is not None:
            entries["BS"] = {"W": float(border_width), "S": Name("S")}
        return self._add(  # type: ignore[return-value]
            AnnotationType.INK.value, rect, contents, title, entries, properties
        )

    def add_highlight(self, quads: Any, contents: str = "", **options: Any) -> Any:
        """Add a highlight (``Highlight``) over *quads*."""
        return self._markup(AnnotationType.HIGHLIGHT.value, quads, contents, options)

    def add_underline(self, quads: Any, contents: str = "", **options: Any) -> Any:
        """Add an underline (``Underline``) under *quads*."""
        return self._markup(AnnotationType.UNDERLINE.value, quads, contents, options)

    def add_strike_out(self, quads: Any, contents: str = "", **options: Any) -> Any:
        """Add a strike-through (``StrikeOut``) over *quads*."""
        return self._markup(AnnotationType.STRIKE_OUT.value, quads, contents, options)

    def add_squiggly(self, quads: Any, contents: str = "", **options: Any) -> Any:
        """Add a wavy underline (``Squiggly``) under *quads*."""
        return self._markup(AnnotationType.SQUIGGLY.value, quads, contents, options)

    def add_redact(self, quads: Any, contents: str = "", **options: Any) -> Any:
        """Mark an area for removal (``Redact``).

        This marks only. ``Document.redact_text`` is what takes text out.
        """
        overlay = options.pop("overlay_text", None)
        annotation = self._markup(AnnotationType.REDACT.value, quads, contents, options)
        if overlay is not None:
            annotation.overlay_text = overlay
        return annotation

    def _markup(
        self, subtype: str, quads: Any, contents: str, options: dict[str, Any]
    ) -> Any:
        """The shared part of every text-markup factory.

        *quads* is what :attr:`_TextMarkupAnnotation.quad_points` takes -- groups
        of four corners, or a flat sequence -- and a single rectangle is accepted
        as the common case of marking one box, through :func:`quad_from_rect`.
        """
        title = options.pop("title", None)
        color = options.pop("color", (1.0, 1.0, 0.0))
        opacity = options.pop("opacity", None)
        rect = options.pop("rect", None)
        interior_color = options.pop("interior_color", None)
        properties = options.pop("properties", None)
        if options:
            raise TypeError(
                "Unexpected options: " + ", ".join(sorted(options))
            )
        flat = _numbers(quads, "QuadPoints")
        if len(flat) == 4:
            # One rectangle, which is what marking a single box looks like.
            flat = _numbers(quad_from_rect(*flat), "QuadPoints")
        _pairs(flat, "QuadPoints", 4)
        if rect is None:
            rect = _bounding_rect(flat, None)
        entries: dict[str, Any] = {
            "QuadPoints": flat,
            "C": _color_components(color),
            "IC": _color_components(interior_color),
            "CA": opacity,
        }
        return self._add(subtype, rect, contents, title, entries, properties)

    def add_stamp(
        self,
        rect: tuple[float, float, float, float],
        icon: str = "Draft",
        contents: str = "",
        *,
        title: str | None = None,
        opacity: float | None = None,
        properties: dict[str, Any] | None = None,
    ) -> StampAnnotation:
        """Add a rubber stamp (``Stamp``) named *icon*."""
        return self._add(  # type: ignore[return-value]
            AnnotationType.STAMP.value,
            rect,
            contents,
            title,
            {"Name": Name(str(icon).lstrip("/")), "CA": opacity},
            properties,
        )

    def generate_appearances(self, *, force: bool = False) -> int:
        """Synthesise missing appearance streams for every annotation on the page.

        Returns the number of appearances created. Annotations that already have
        an appearance are left untouched unless *force* is given. See
        :meth:`Annotation.generate_appearance` for the supported subtypes.
        """
        return self._page._document._engine_pdf.generate_appearances(
            self._page.index, force=force
        )
