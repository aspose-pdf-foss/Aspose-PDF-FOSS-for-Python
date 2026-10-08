"""Stamps: a mark put over or under what a page already draws.

A stamp is placed, not authored at a coordinate: say which corner it belongs in
and how far off it, and it lands there on any page size::

    document.pages[0].add_stamp(TextStamp("DRAFT", font_size=64, rotate=45,
                                          opacity=0.2, background=True))
    document.add_stamp(PageNumberStamp("{page} of {total}", font_size=9,
                                       vertical_alignment=VerticalAlignment.BOTTOM,
                                       y_indent=24))
    document.pages[0].add_stamp(ImageStamp(logo_png, width=80, height=40,
                                           horizontal_alignment=HorizontalAlignment.RIGHT,
                                           vertical_alignment=VerticalAlignment.TOP,
                                           x_indent=-24, y_indent=-24))

A stamp is positioned inside the part of the page a reader shows -- the crop
box -- and the page's rotation is undone, so it stands upright on screen
whatever ``/Rotate`` says. ``background`` puts it under the page's own content
instead of over it. One stamp added to many pages is one object in the file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from aspose_pdf.exceptions import PdfValidationException

__all__ = [
    "HorizontalAlignment",
    "ImageStamp",
    "PageNumberStamp",
    "PageStamp",
    "Stamp",
    "TextStamp",
    "VerticalAlignment",
]


class HorizontalAlignment(str, Enum):  # noqa: UP042
    """Which way a stamp is placed across the page."""

    LEFT = "Left"
    CENTER = "Center"
    RIGHT = "Right"


class VerticalAlignment(str, Enum):  # noqa: UP042
    """Which way a stamp is placed up the page."""

    BOTTOM = "Bottom"
    CENTER = "Center"
    TOP = "Top"


@dataclass(kw_only=True)
class Stamp:
    """What every stamp says about where it goes and how it is drawn."""

    opacity: float = 1.0
    """How opaque the stamp is, ``0.0`` to ``1.0`` (``/ca`` and ``/CA``)."""

    rotate: float = 0.0
    """Degrees to turn the stamp counter-clockwise, about its own centre."""

    background: bool = False
    """Draw under the page's own content rather than over it."""

    horizontal_alignment: HorizontalAlignment = HorizontalAlignment.CENTER
    vertical_alignment: VerticalAlignment = VerticalAlignment.CENTER

    x_indent: float = 0.0
    """Points to move the stamp right of where the alignment puts it."""

    y_indent: float = 0.0
    """Points to move the stamp up from where the alignment puts it."""

    zoom: float = 1.0
    """Scale the stamp by this much, after it is measured."""

    artifact: bool = True
    """Mark the stamp as an ``/Artifact``: decoration, not what the page says.

    A stamp is not part of the document's content -- that is what stamping
    means -- and ISO 14289-1 7.1 requires every mark on a page of a *tagged*
    document to be either tagged as real content or marked as an artifact. So a
    stamp says which it is, and the answer is always the same one. On an
    untagged document the marking is inert: a reader that is not looking for
    structure ignores it.

    Set it to ``False`` to put the stamp on as bare content, which is what a
    stamp that *is* part of the document -- an imposed page, a form being filled
    in -- would want.
    """

    artifact_type: str | None = None
    """``Pagination`` (the default), ``Layout`` or ``Page``; see 14.8.2.2."""

    artifact_subtype: str | None = None
    """``Header``, ``Footer`` or ``Watermark`` for a ``Pagination`` artifact.

    ``None`` takes the subclass's own answer: a page number placed at the top or
    bottom of the sheet is a header or a footer, and anything else a stamp puts
    on a page is a watermark.
    """

    def _validate(self) -> None:
        if not 0.0 <= float(self.opacity) <= 1.0:
            raise PdfValidationException("opacity must be between 0 and 1")
        if float(self.zoom) <= 0.0:
            raise PdfValidationException("zoom must be above zero")
        HorizontalAlignment(self.horizontal_alignment)
        VerticalAlignment(self.vertical_alignment)
        if self.artifact:
            # Through the writer, so one rule decides what a valid artifact is.
            from aspose_pdf.engine.content_authoring import wrap_artifact

            wrap_artifact(
                b"",
                artifact_type=self.artifact_type or "Pagination",
                subtype=self._artifact_subtype(),
            )

    def _artifact_subtype(self) -> str | None:
        """Which kind of running matter this stamp is, when it is Pagination."""
        if self.artifact_subtype is not None:
            return self.artifact_subtype
        if (self.artifact_type or "Pagination") != "Pagination":
            return None
        return "Watermark"

    def _artifact(self) -> dict[str, Any] | None:
        """What to say about the stamp in its ``/Artifact`` property list.

        ``None`` when the stamp is to go on as bare content. ``/Attached`` is
        written only for a header or a footer, which is the only case where a
        stamp really is fixed to an edge of the sheet: a watermark sits wherever
        it was aligned and is attached to nothing.
        """
        if not self.artifact:
            return None
        artifact_type = self.artifact_type or "Pagination"
        subtype = self._artifact_subtype()
        attached: list[str] | None = None
        if subtype in ("Header", "Footer"):
            vertical = VerticalAlignment(self.vertical_alignment).value
            if vertical in ("Top", "Bottom"):
                attached = [vertical]
        return {
            "artifact_type": artifact_type,
            "subtype": subtype,
            "attached": attached,
        }

    def _placement(self) -> dict[str, Any]:
        return {
            "zoom": (float(self.zoom), float(self.zoom)),
            "rotate": float(self.rotate),
            "alignment": (
                HorizontalAlignment(self.horizontal_alignment).value,
                VerticalAlignment(self.vertical_alignment).value,
            ),
            "indent": (float(self.x_indent), float(self.y_indent)),
        }

    def _content(self, pdf: Any, page_index: int) -> tuple[bytes, tuple[float, float], Any]:
        """The stamp's drawing, the box it needs, and the form's resources."""
        raise NotImplementedError

    def _reusable(self) -> bool:
        """Whether one form serves every page, or each page needs its own."""
        return True


def _opacity_resources(pdf: Any, opacity: float, **rest: Any) -> Any:
    """The form's ``/Resources``, with an alpha state when one is called for."""
    states = {}
    if opacity < 1.0:
        states["GS0"] = {"ca": opacity, "CA": opacity}
    return pdf._build_appearance_resources(states, **rest)


def _with_opacity(content: bytes, opacity: float) -> bytes:
    return b"/GS0 gs\n" + content if opacity < 1.0 else content


def _colour_operator(color: Any) -> bytes:
    """The stamp's fill colour, read by the one rule every authored colour is.

    A stamp has taken grey, RGB and CMYK since it was written; the page
    authoring API took RGB only. Both go through
    ``content_authoring.color_operator`` now, so a colour means the same thing
    whichever of them it is handed to -- including a single number for grey and a
    ``"#rrggbb"`` string.
    """
    from aspose_pdf.engine.content_authoring import color_operator

    return color_operator(color, stroking=False).encode("ascii")


@dataclass
class TextStamp(Stamp):
    """A line of text, set in one of the 14 standard fonts.

    The stamp is as wide as the text and as tall as the face's own bounding
    box, so nothing it draws is clipped, and the box is what the alignment
    places. The text is written in the encoding the face uses
    (``WinAnsiEncoding``, or the font's own for ``Symbol`` and
    ``ZapfDingbats``); a character that encoding has no code for is refused
    rather than drawn as something else.
    """

    value: str
    font_size: float = 14.0
    font_name: str = "Helvetica"
    color: Any = field(default=(0.0, 0.0, 0.0))
    font: Any = None
    """A font to embed instead: bytes, a path, or a ``FontDescriptor``.

    With one, the text is written through a subset Type0/CID font, so a stamp can
    say something the standard fonts' encodings have no codes for -- a Cyrillic or
    a CJK watermark. Without one, the stamp is set in one of the 14 standard
    fonts, which is what :attr:`font_name` picks.
    """

    def _text(self, page_index: int, page_count: int) -> str:
        return str(self.value)

    def _validate(self) -> None:
        super()._validate()
        if float(self.font_size) <= 0.0:
            raise PdfValidationException("font_size must be above zero")
        if self.font is not None:
            # The face comes from the font itself; ``font_name`` names a standard
            # one and has nothing to say about an embedded program.
            return
        from aspose_pdf.engine.std_metrics import WIDTHS

        if str(self.font_name).lstrip("/") not in WIDTHS:
            allowed = ", ".join(sorted(WIDTHS))
            raise PdfValidationException(
                f"font_name must be one of the standard 14 fonts: {allowed}"
            )

    def _content(self, pdf: Any, page_index: int) -> tuple[bytes, tuple[float, float], Any]:
        if self.font is not None:
            return self._embedded_content(pdf, page_index)
        return self._standard_content(pdf, page_index)

    def _embedded_content(
        self, pdf: Any, page_index: int
    ) -> tuple[bytes, tuple[float, float], Any]:
        """The stamp set in an embedded Type0 font, written in CID codes."""
        from aspose_pdf.engine.content_authoring import format_number
        from aspose_pdf.engine.font_authoring import prepare_authored_font

        self._validate()
        size = float(self.font_size)
        text = self._text(page_index, len(pdf.pages))
        authored = prepare_authored_font(self.font, limits=pdf._load_limits)
        encoded = authored.encode(text)
        widths = authored.cid_widths()
        total = sum(
            widths.get((encoded[i] << 8) | encoded[i + 1], 0)
            for i in range(0, len(encoded) - 1, 2)
        )
        width = total / 1000.0 * size
        metrics = dict(authored.descriptor_metrics)
        box = metrics.get("FontBBox")
        if isinstance(box, (list, tuple)) and len(box) == 4:
            top, bottom = float(box[3]), float(box[1])
        else:
            top = float(metrics.get("Ascent", 750) or 750)
            bottom = float(metrics.get("Descent", -250) or -250)
        height = (top - bottom) / 1000.0 * size
        baseline = -bottom / 1000.0 * size

        type0_ref, _parts = pdf._build_type0_font_graph(authored)
        opacity = float(self.opacity)
        content = _with_opacity(
            b"BT "
            + _colour_operator(self.color)
            + b" /F1 "
            + format_number(size).encode("ascii")
            + b" Tf 1 0 0 1 0 "
            + format_number(baseline).encode("ascii")
            + b" Tm <"
            + encoded.hex().encode("ascii")
            + b"> Tj ET\n",
            opacity,
        )
        resources = _opacity_resources(pdf, opacity)
        from aspose_pdf.engine.cos import PdfDictionary, PdfName

        if not isinstance(resources, PdfDictionary):
            resources = PdfDictionary()
        resources.mapping[PdfName("Font")] = PdfDictionary({PdfName("F1"): type0_ref})
        return bytes(content), (max(width, 1e-6), max(height, 1e-6)), resources

    def _standard_content(
        self, pdf: Any, page_index: int
    ) -> tuple[bytes, tuple[float, float], Any]:
        from aspose_pdf.engine.agl import (
            encode_with_base_encoding,
            unencodable_characters,
        )
        from aspose_pdf.engine.content_authoring import format_number
        from aspose_pdf.engine.std_fonts import StandardFonts
        from aspose_pdf.engine.std_metrics import bounds, text_width

        self._validate()
        face = str(self.font_name).lstrip("/")
        size = float(self.font_size)
        text = self._text(page_index, len(pdf.pages))
        encoding = StandardFonts.get_default_encoding(face)
        codes = encode_with_base_encoding(text, encoding)
        if codes is None:
            missing = "".join(unencodable_characters(text, encoding))
            raise PdfValidationException(
                f"{face} cannot write these characters: {missing!r}"
            )

        width = text_width(codes, face, size) or 0.0
        top, bottom = bounds(face)
        height = (top - bottom) / 1000.0 * size
        baseline = -bottom / 1000.0 * size

        literal = bytearray(b"(")
        for code in codes:
            if code in b"()\\":
                literal.append(0x5C)
            literal.append(code)
        literal.append(0x29)
        opacity = float(self.opacity)
        content = _with_opacity(
            b"BT "
            + _colour_operator(self.color)
            + b" /F1 "
            + format_number(size).encode("ascii")
            + b" Tf 1 0 0 1 0 "
            + format_number(baseline).encode("ascii")
            + b" Tm "
            + bytes(literal)
            + b" Tj ET\n",
            opacity,
        )
        declared = StandardFonts.declared_encoding(face)
        spec = {"Subtype": "Type1", "BaseFont": face}
        if declared:
            spec["Encoding"] = declared
        resources = _opacity_resources(pdf, opacity, fonts={"F1": spec})
        return bytes(content), (max(width, 1e-6), max(height, 1e-6)), resources


@dataclass
class PageNumberStamp(TextStamp):
    """A text stamp whose text is the page's number.

    *value* is a format string: ``{page}`` is the page's number counting from
    :attr:`starting_number`, and ``{total}`` is how many pages the document
    has. Each page gets its own form, because each says something different.
    """

    value: str = "{page}"
    starting_number: int = 1

    def _validate(self) -> None:
        super()._validate()
        if int(self.starting_number) != self.starting_number:
            raise PdfValidationException("starting_number must be a whole number")

    def _artifact_subtype(self) -> str | None:
        """A page number at the top of the sheet is a header, at the foot a footer.

        Not a guess: the caller has already said where the stamp goes, and 14.8.2.2
        names exactly these two for running matter at an edge. A page number
        placed in the middle of the page is left as a watermark, which is the
        honest answer for something that is not at an edge at all.
        """
        if self.artifact_subtype is not None:
            return self.artifact_subtype
        if (self.artifact_type or "Pagination") != "Pagination":
            return None
        return {
            "Top": "Header",
            "Bottom": "Footer",
        }.get(VerticalAlignment(self.vertical_alignment).value, "Watermark")

    def _text(self, page_index: int, page_count: int) -> str:
        number = int(self.starting_number) + page_index
        try:
            return str(self.value).format(page=number, total=page_count)
        except (IndexError, KeyError) as error:
            raise PdfValidationException(
                "a page number format may only name {page} and {total}"
            ) from error

    def _reusable(self) -> bool:
        return False


@dataclass
class PageStamp(Stamp):
    """A page of a document, drawn onto a page -- a letterhead, a background form.

    *source* is the page to place: an :class:`aspose_pdf.Page`, or a
    :class:`aspose_pdf.Document` with *page_index* saying which of its pages. The
    page is brought across as a form XObject with everything it draws with -- its
    fonts, its images, the forms nested inside it -- which is the same deep copy a
    merge makes, so the source document can be closed afterwards.

    What is placed is the page as a reader *shows* it: its crop box, with its
    ``/Rotate`` applied. All of :class:`Stamp`'s placement applies on top --
    ``zoom`` to scale it, ``rotate`` to turn it, the alignments and indents to put
    it somewhere, ``opacity`` to see through it, and ``background=True`` to put it
    *under* the page's own content, which is what a letterhead wants::

        letterhead = Document("letterhead.pdf")
        document.add_stamp(PageStamp(letterhead.pages[0], background=True))

    A page of the document being stamped may be placed on another of its pages;
    placing a page on *itself* is refused, since the content being drawn would be
    the content being added to.
    """

    source: Any = None
    page_index: int = 0

    def _validate(self) -> None:
        super()._validate()
        if self.source is None:
            raise PdfValidationException("A page stamp needs a page to place")
        if int(self.page_index) != self.page_index or int(self.page_index) < 0:
            raise PdfValidationException("page_index must be a page number from zero")

    def _resolved(self) -> tuple[Any, int]:
        """The engine document and page index the stamp places, whatever it was given."""
        from aspose_pdf.document import Document
        from aspose_pdf.pages import Page

        self._validate()
        source = self.source
        if isinstance(source, Page):
            document = source._document
            index = source.index
        elif isinstance(source, Document):
            document = source
            index = int(self.page_index)
        else:
            raise PdfValidationException(
                "A page stamp places a Page or a Document, not "
                f"{type(source).__name__}"
            )
        document._ensure_not_disposed()
        engine = document._engine_pdf
        if engine is None:
            raise PdfValidationException("The page to place has no document loaded")
        return engine, index

    def _form(self, pdf: Any) -> tuple[Any, tuple[float, float]]:
        from aspose_pdf.engine.stamps import import_page_form

        engine, index = self._resolved()
        return import_page_form(pdf, engine, index, opacity=float(self.opacity))

    def _content(self, pdf: Any, page_index: int) -> tuple[bytes, tuple[float, float], Any]:
        # Unused: this stamp hands over a form instead of content to wrap, since
        # importing the page *is* the work. Kept so the class still answers the
        # base's contract if something asks.
        raise PdfValidationException(
            "A page stamp is placed as an imported form, not as content"
        )


@dataclass
class ImageStamp(Stamp):
    """An image, scaled to the box it is given.

    Without *width* and *height* the image is one point per pixel, which is
    what :meth:`Page.add_image <aspose_pdf.pages.Page.add_image>` does; giving
    one of the two keeps the image's proportions and derives the other.
    """

    image: bytes | bytearray | str | Path
    width: float | None = None
    height: float | None = None

    def _validate(self) -> None:
        super()._validate()
        for name in ("width", "height"):
            value = getattr(self, name)
            if value is not None and float(value) <= 0.0:
                raise PdfValidationException(f"{name} must be above zero")

    def _content(self, pdf: Any, page_index: int) -> tuple[bytes, tuple[float, float], Any]:
        from aspose_pdf.engine.content_authoring import format_number, prepare_image

        self._validate()
        source = self.image
        if isinstance(source, (str, Path)):
            path = Path(source)
            pdf._load_budget.check(
                path.stat().st_size, "max_input_bytes", "stamp image input bytes"
            )
            data = path.read_bytes()
        elif isinstance(source, (bytes, bytearray)):
            data = bytes(source)
        else:
            raise TypeError("image must be bytes, bytearray, str, or Path")

        prepared = prepare_image(data, limits=pdf._load_limits, budget=pdf._load_budget)
        reference = pdf._register_image_xobject(prepared)
        width, height = self.width, self.height
        if width is None and height is None:
            width, height = float(prepared.width), float(prepared.height)
        elif width is None:
            width = float(height) * prepared.width / prepared.height
        elif height is None:
            height = float(width) * prepared.height / prepared.width
        width, height = float(width), float(height)

        opacity = float(self.opacity)
        # The image is drawn in its own box: the unit square scaled to it.
        content = _with_opacity(
            format_number(width).encode("ascii")
            + b" 0 0 "
            + format_number(height).encode("ascii")
            + b" 0 0 cm /Im0 Do\n",
            opacity,
        )
        resources = _opacity_resources(pdf, opacity, xobjects={"Im0": reference})
        return bytes(content), (width, height), resources
