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

    def _validate(self) -> None:
        if not 0.0 <= float(self.opacity) <= 1.0:
            raise PdfValidationException("opacity must be between 0 and 1")
        if float(self.zoom) <= 0.0:
            raise PdfValidationException("zoom must be above zero")
        HorizontalAlignment(self.horizontal_alignment)
        VerticalAlignment(self.vertical_alignment)

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

    def _text(self, page_index: int, page_count: int) -> str:
        return str(self.value)

    def _validate(self) -> None:
        super()._validate()
        if float(self.font_size) <= 0.0:
            raise PdfValidationException("font_size must be above zero")
        from aspose_pdf.engine.std_metrics import WIDTHS

        if str(self.font_name).lstrip("/") not in WIDTHS:
            allowed = ", ".join(sorted(WIDTHS))
            raise PdfValidationException(
                f"font_name must be one of the standard 14 fonts: {allowed}"
            )

    def _content(self, pdf: Any, page_index: int) -> tuple[bytes, tuple[float, float], Any]:
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
