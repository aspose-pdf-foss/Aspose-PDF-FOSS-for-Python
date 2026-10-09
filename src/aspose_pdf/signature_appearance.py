"""What a visible digital signature looks like on the page.

A signature field may be *invisible* -- a zero-size widget, covering the whole
document and showing nothing -- or **visible**, drawn on a page as a box saying
who signed, when, and why. ISO 32000-1 12.7.4.5 puts nothing in the signature
dictionary about this: the appearance is an ordinary annotation appearance
stream (``/AP /N``), and what goes in it is entirely the producer's business.
Which is why it has to be said somewhere, and :class:`SignatureAppearance` is
where.

It carries no PDF structure of its own. It is a description handed to
:meth:`aspose_pdf.Document.sign`, which draws it into the signed revision when
the document is saved -- so the appearance is covered by the signature that
made it, and cannot be swapped for another without breaking it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aspose_pdf.exceptions import PdfValidationException

__all__ = ["SignatureAppearance"]

#: Where the image sits relative to the text (see :class:`SignatureAppearance`).
IMAGE_POSITIONS = ("left", "right", "top", "background", "only")

_DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S %z"


class SignatureAppearance:
    """How a visible signature is drawn: its text, its image, and its frame.

    Pass one to :meth:`aspose_pdf.Document.sign`::

        document.sign(
            "Signature1",
            certificate=cert, private_key=key,
            reason="I approve this document", location="Prague",
            appearance=SignatureAppearance(image=logo_png),
        )

    By default it writes four lines -- who signed, when, why, and where --
    each labelled, taking the name from ``signer_name`` or, failing that, the
    common name in the signing certificate. Turn a line off with its
    ``show_*`` argument, or replace the lot with *text* of your own.

    Parameters
    ----------
    image : bytes, str or Path, optional
        A scanned signature, a seal or a logo, as image bytes or a path. PNG,
        JPEG, GIF, BMP and TIFF are read; it is scaled to fit its share of the
        box, keeping its aspect ratio.
    image_position : str
        ``"left"`` (the default), ``"right"``, ``"top"``, ``"background"`` --
        the image fills the box with the text over it -- or ``"only"``, which
        draws no text at all.
    image_fraction : float
        How much of the box the image gets when it sits beside or above the
        text, between 0.1 and 0.9. Default 0.4.
    text : str, optional
        The whole text, replacing the composed lines. Newlines break lines;
        a line too long for the box is wrapped.
    show_name, show_date, show_reason, show_location, show_contact : bool
        Which composed lines to include. A line whose value is empty is left
        out whatever this says -- a signature with no reason given shows no
        empty ``Reason:``.
    labels : bool
        Prefix each composed line with what it is (``Date:``, ``Reason:``).
        The name line reads ``Digitally signed by ...``. With ``False`` the
        values are drawn on their own.
    date_format : str
        :meth:`~datetime.datetime.strftime` format for the date line.
    font : bytes, str, Path or FontDescriptor, optional
        A font program to draw the text with, embedded in the document. Needed
        for text outside Latin-1 -- a Cyrillic or CJK name has no code in the
        standard fonts' encodings, and signing refuses rather than drawing the
        wrong letters. Without one the text is drawn in Helvetica.
    font_size : float
        Point size, or ``0`` (the default) to fit the text to the box.
    text_color : optional
        Any colour this library accepts; black by default.
    background : optional
        Fill colour for the box. ``None`` leaves it unpainted, so whatever the
        page already draws there shows through.
    border_color : optional
        Colour of a frame around the box. ``None`` draws no frame.
    border_width : float
        Frame line width in points. Default 1.
    page : int, optional
        Zero-based page for the field :meth:`~aspose_pdf.Document.sign` adds
        when it is given no field name. Ignored when signing a field that
        exists -- that field's widget says where it is.
    rect : sequence of four floats, optional
        ``(llx, lly, urx, ury)`` for that same added field. Signing with an
        appearance and neither a field nor a rectangle is refused: there would
        be nowhere to draw it.

    Raises
    ------
    PdfValidationException
        If an argument is out of range or the two placement arguments disagree.
    """

    # Declared, so a misspelled name raises instead of being taken and quietly
    # lost -- an appearance that silently ignored `image_positon` would be
    # found only by looking at the signed document.
    __slots__ = (
        "background",
        "border_color",
        "border_width",
        "date_format",
        "font",
        "font_size",
        "image",
        "image_fraction",
        "image_position",
        "labels",
        "page",
        "rect",
        "show_contact",
        "show_date",
        "show_location",
        "show_name",
        "show_reason",
        "text",
        "text_color",
    )

    def __init__(
        self,
        *,
        image: Any = None,
        image_position: str = "left",
        image_fraction: float = 0.4,
        text: str | None = None,
        show_name: bool = True,
        show_date: bool = True,
        show_reason: bool = True,
        show_location: bool = True,
        show_contact: bool = False,
        labels: bool = True,
        date_format: str = _DEFAULT_DATE_FORMAT,
        font: Any = None,
        font_size: float = 0.0,
        text_color: Any = None,
        background: Any = None,
        border_color: Any = None,
        border_width: float = 1.0,
        page: int | None = None,
        rect: Any = None,
    ) -> None:
        position = str(image_position or "left").lower()
        if position not in IMAGE_POSITIONS:
            allowed = ", ".join(IMAGE_POSITIONS)
            raise PdfValidationException(
                f"image_position is one of {allowed}, not {image_position!r}."
            )
        if image is None and position == "only":
            raise PdfValidationException(
                "image_position='only' draws the image and nothing else, so "
                "there has to be an image."
            )
        fraction = _number("image_fraction", image_fraction)
        if not 0.1 <= fraction <= 0.9:
            raise PdfValidationException(
                f"image_fraction is between 0.1 and 0.9, not {fraction}."
            )
        size = _number("font_size", font_size)
        if size < 0:
            raise PdfValidationException(f"font_size is not negative: {size}.")
        width = _number("border_width", border_width)
        if width < 0:
            raise PdfValidationException(f"border_width is not negative: {width}.")
        if text is not None and not isinstance(text, str):
            raise PdfValidationException("text is a string, or None.")
        if not isinstance(date_format, str) or not date_format:
            raise PdfValidationException("date_format is a non-empty string.")
        if isinstance(image, (str, Path)):
            image = Path(image)

        self.image = image
        self.image_position = position
        self.image_fraction = fraction
        self.text = text
        self.show_name = bool(show_name)
        self.show_date = bool(show_date)
        self.show_reason = bool(show_reason)
        self.show_location = bool(show_location)
        self.show_contact = bool(show_contact)
        self.labels = bool(labels)
        self.date_format = date_format
        self.font = font
        self.font_size = size
        self.text_color = text_color
        self.background = background
        self.border_color = border_color
        self.border_width = width
        self.page, self.rect = _placement(page, rect)

    @property
    def draws_text(self) -> bool:
        """Whether anything but the image is drawn."""
        return self.image_position != "only"

    @property
    def places_its_own_field(self) -> bool:
        """Whether this says where a field should be added for it."""
        return self.rect is not None

    def __repr__(self) -> str:
        where = "" if self.rect is None else f", page={self.page}, rect={self.rect}"
        return (
            f"SignatureAppearance(image={'yes' if self.image is not None else 'no'}"
            f", image_position={self.image_position!r}{where})"
        )


def _number(label: str, value: Any) -> float:
    """*value* as a finite float, or a clear complaint about what it is."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PdfValidationException(
            f"{label} is a number, not {type(value).__name__}."
        )
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise PdfValidationException(f"{label} is a finite number, not {value!r}.")
    return number


def _placement(page: Any, rect: Any) -> tuple[int, tuple[float, float, float, float] | None]:
    """Check ``page``/``rect`` together and return them normalized.

    A page on its own says nothing about where on it to draw, so it is only
    meaningful with a rectangle; saying so here is better than adding a field
    somewhere unintended halfway through a save.
    """
    if rect is None:
        if page is not None:
            raise PdfValidationException(
                "page says which page to add the signature field to, which "
                "needs a rect saying where on it."
            )
        return 0, None
    values = list(rect)
    if len(values) != 4:
        raise PdfValidationException(
            f"rect is four numbers (llx, lly, urx, ury), not {len(values)}."
        )
    llx, lly, urx, ury = (_number("rect", value) for value in values)
    llx, urx = min(llx, urx), max(llx, urx)
    lly, ury = min(lly, ury), max(lly, ury)
    if urx - llx <= 0 or ury - lly <= 0:
        raise PdfValidationException(
            "rect encloses no area, so nothing drawn in it would be visible."
        )
    index = 0 if page is None else page
    if isinstance(index, bool) or not isinstance(index, int):
        raise PdfValidationException("page is a zero-based page index.")
    if index < 0:
        raise PdfValidationException(f"page is not negative: {index}.")
    return index, (llx, lly, urx, ury)
