"""Lay out and draw a visible signature's appearance stream.

The appearance of a signature is an ordinary annotation appearance stream
(ISO 32000-1 12.5.5): a form XObject in the widget's own coordinate space,
origin at the lower-left of its ``/Rect``. Nothing in the signature dictionary
describes it, so everything here is layout, not conformance -- what a reader
sees where the signature sits.

Like :mod:`~aspose_pdf.engine.field_appearance`, this module touches no
document objects. It is given a *face* to measure and encode text with (see
:mod:`~aspose_pdf.engine.text_faces`), the box to fill, and the pieces to put
in it; the caller registers the fonts, the image and the resulting stream.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "Placement",
    "compose_lines",
    "draw",
    "fit",
    "image_box",
]

#: Inset from the widget's edge to its content, in points. Wider than a form
#: field's, because a signature box is usually framed and text hard against
#: the frame reads as a mistake.
PADDING = 4.0
#: Gap between the image and the text beside it.
GUTTER = 4.0
#: Line spacing as a multiple of the font size.
LINE_HEIGHT = 1.16
#: Largest and smallest point size :func:`fit` will choose on its own.
MAX_AUTO_SIZE = 12.0
MIN_AUTO_SIZE = 4.0
#: :func:`fit` will not shrink below this merely to keep a line from wrapping.
NO_WRAP_FLOOR = 7.0


@dataclass(frozen=True)
class Placement:
    """A laid-out appearance: the size chosen, and where each line goes."""

    size: float
    #: ``(text, x, baseline)`` in the widget's coordinate space.
    lines: tuple[tuple[str, float, float], ...]
    #: ``(x, y, width, height)`` for the image, when there is one.
    image: tuple[float, float, float, float] | None = None
    #: Lines that did not fit the box at the smallest size tried.
    overflowed: int = 0
    #: Whether an entry had to be broken across lines to fit the width.
    wrapped: bool = False


def compose_lines(
    *,
    name: str | None,
    date: str | None,
    reason: str | None,
    location: str | None,
    contact: str | None,
    labels: bool,
) -> list[str]:
    """The default text of a signature appearance, one entry per line.

    An entry with no value is left out entirely rather than drawn as an empty
    label: a signature given no reason should not show the word ``Reason:``
    with nothing after it.
    """
    pairs = (
        ("Digitally signed by {}", name),
        ("Date: {}", date),
        ("Reason: {}", reason),
        ("Location: {}", location),
        ("Contact: {}", contact),
    )
    out: list[str] = []
    for template, value in pairs:
        text = (value or "").strip()
        if not text:
            continue
        out.append(template.format(text) if labels else text)
    return out


def image_box(
    width: float,
    height: float,
    *,
    position: str,
    fraction: float,
    aspect: float,
) -> tuple[tuple[float, float, float, float], tuple[float, float, float, float]]:
    """Split the box into ``(image area, text area)`` for *position*.

    *aspect* is the image's width over its height; the image is centred in its
    area at the largest size that fits, so it is never stretched. Both areas
    are ``(x, y, width, height)``, and the text area is empty (zero width) when
    the image takes the whole box.
    """
    inner_x = PADDING
    inner_y = PADDING
    inner_w = max(0.0, width - 2.0 * PADDING)
    inner_h = max(0.0, height - 2.0 * PADDING)
    whole = (inner_x, inner_y, inner_w, inner_h)
    empty = (0.0, 0.0, 0.0, 0.0)

    if position in ("only", "background"):
        return _fitted(whole, aspect), (empty if position == "only" else whole)
    if position == "top":
        band = inner_h * fraction
        image_area = (inner_x, inner_y + inner_h - band, inner_w, band)
        text_area = (inner_x, inner_y, inner_w, max(0.0, inner_h - band - GUTTER))
        return _fitted(image_area, aspect), text_area
    band = inner_w * fraction
    if position == "right":
        image_area = (inner_x + inner_w - band, inner_y, band, inner_h)
        text_area = (inner_x, inner_y, max(0.0, inner_w - band - GUTTER), inner_h)
    else:  # left
        image_area = (inner_x, inner_y, band, inner_h)
        text_area = (
            inner_x + band + GUTTER,
            inner_y,
            max(0.0, inner_w - band - GUTTER),
            inner_h,
        )
    return _fitted(image_area, aspect), text_area


def _fitted(
    area: tuple[float, float, float, float], aspect: float
) -> tuple[float, float, float, float]:
    """*area*'s largest centred sub-rectangle with the given width/height ratio."""
    x, y, w, h = area
    if w <= 0 or h <= 0 or aspect <= 0:
        return (x, y, 0.0, 0.0)
    width = min(w, h * aspect)
    height = width / aspect
    return (x + (w - width) / 2.0, y + (h - height) / 2.0, width, height)


def fit(
    face: Any,
    text: list[str],
    area: tuple[float, float, float, float],
    *,
    size: float = 0.0,
) -> Placement:
    """Place *text* in *area*, choosing a size that fits when none is given.

    Each entry of *text* is a line of its own; one too wide for the area is
    wrapped onto further lines. An explicit *size* is honoured even when the
    text then overflows -- the caller asked for that size, and
    :attr:`Placement.overflowed` says how many lines had to be dropped.

    With *size* ``0`` the size is chosen, because a signature box is small and
    its text varies in length: a fixed size either overflows it or wastes it.
    The first choice is the largest size at which every entry stays on a line
    of its own and the whole lot fits the height -- ``Date: ...`` broken after
    the time, with the zone offset alone underneath, reads worse than the same
    line a point smaller. That is not worth shrinking indefinitely for, so
    below :data:`NO_WRAP_FLOOR` the search gives up on it and takes the largest
    size that fits the height, wrapping as it must.
    """
    x, y, width, height = area
    if width <= 0 or height <= 0 or not text:
        return Placement(size=size or MIN_AUTO_SIZE, lines=())

    if size > 0:
        return _place(face, text, x, y, width, height, size)

    trial = MAX_AUTO_SIZE
    while trial >= NO_WRAP_FLOOR:
        candidate = _place(face, text, x, y, width, height, trial)
        if not candidate.overflowed and not candidate.wrapped:
            return candidate
        trial -= 0.5

    chosen = _place(face, text, x, y, width, height, MAX_AUTO_SIZE)
    trial = MAX_AUTO_SIZE
    while chosen.overflowed and trial > MIN_AUTO_SIZE:
        trial = max(MIN_AUTO_SIZE, trial - 0.5)
        chosen = _place(face, text, x, y, width, height, trial)
    return chosen


def _place(
    face: Any,
    text: list[str],
    x: float,
    y: float,
    width: float,
    height: float,
    size: float,
) -> Placement:
    """Wrap *text* at *size* and stack the lines down from the top of the area."""
    entries = [part for entry in text for part in entry.split("\n")]
    lines: list[str] = []
    for part in entries:
        lines.extend(face.wrap(part, width, size) or [""])

    leading = size * LINE_HEIGHT
    fits = max(0, int((height + (leading - size) * 0.5) // leading))
    placed = tuple(
        (line, x, y + height - size - index * leading)
        for index, line in enumerate(lines[:fits])
    )
    return Placement(
        size=size,
        lines=placed,
        overflowed=len(lines) - len(placed),
        wrapped=len(lines) > len(entries),
    )


def draw(
    placement: Placement,
    *,
    width: float,
    height: float,
    face: Any,
    font_resource: str,
    text_color_op: str,
    background_op: str | None,
    border_op: str | None,
    border_width: float,
    image_resource: str | None,
) -> bytes:
    """The appearance content stream for a laid-out signature.

    The box is painted first, then the image, then the text. That order is the
    whole of what ``image_position="background"`` means: operators paint over
    what came before them (ISO 32000-1 8.1), so an image emitted after the
    text would hide it rather than sit behind it. Beside the text the two do
    not overlap and the order makes no difference, so there is one order here.

    Every part is inside its own ``q``/``Q``, so nothing it sets escapes into
    the page that draws this form.
    """
    out: list[str] = []
    if background_op:
        out += ["q", background_op, f"0 0 {_n(width)} {_n(height)} re", "f", "Q"]
    if border_op and border_width > 0:
        inset = border_width / 2.0
        out += [
            "q",
            border_op,
            f"{_n(border_width)} w",
            f"{_n(inset)} {_n(inset)} {_n(width - border_width)} "
            f"{_n(height - border_width)} re",
            "S",
            "Q",
        ]

    if image_resource and placement.image:
        ix, iy, iw, ih = placement.image
        if iw > 0 and ih > 0:
            out.append(
                f"q\n{_n(iw)} 0 0 {_n(ih)} {_n(ix)} {_n(iy)} cm\n"
                f"/{image_resource} Do\nQ"
            )

    if placement.lines:
        out += ["q", "BT", f"/{font_resource} {_n(placement.size)} Tf", text_color_op]
        previous: tuple[float, float] | None = None
        for text, x, y in placement.lines:
            if previous is None:
                out.append(f"1 0 0 1 {_n(x)} {_n(y)} Tm")
            else:
                out.append(f"{_n(x - previous[0])} {_n(y - previous[1])} Td")
            previous = (x, y)
            out.append(_show(face, text))
        out += ["ET", "Q"]
    return ("\n".join(out) + "\n").encode("latin-1")


def _show(face: Any, text: str) -> str:
    """A show operator for *text* in whichever string form the face needs."""
    encoded = face.show_segments(text)
    if face.hex_show_strings:
        return f"<{encoded.hex().upper()}> Tj"
    return f"{_literal(encoded)} Tj"


def _literal(encoded: bytes) -> str:
    """*encoded* as a PDF literal string, escaping what would end it early."""
    out = bytearray(b"(")
    for byte in encoded:
        if byte in (0x28, 0x29, 0x5C):  # ( ) \
            out.append(0x5C)
        out.append(byte)
    out.append(0x29)
    return out.decode("latin-1")


def _n(value: float) -> str:
    """Format a coordinate compactly, as the other appearance builders do."""
    text = f"{float(value):.3f}".rstrip("0").rstrip(".")
    return text if text and text != "-0" else "0"
