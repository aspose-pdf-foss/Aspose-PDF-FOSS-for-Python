"""Small helpers for authoring page content streams."""

from __future__ import annotations

import math
import re
import struct
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import PdfLoadLimits, _coerce_limits, _LoadBudget

from .agl import encode_with_base_encoding, unencodable_characters
from .cos import format_pdf_number
from .filters import StreamDecoder
from .image_resample import sample_to_byte

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8"
_SAFE_RESOURCE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class AuthoredImage:
    """Prepared image data and PDF image XObject metadata."""

    stream_data: bytes
    decoded_data: bytes
    width: int
    height: int
    bits_per_component: int
    color_space: str
    components: int
    filter_name: str | None = None
    #: One 8-bit opacity sample per pixel, for a ``/SMask``; ``None`` when the
    #: image is opaque throughout.
    alpha: bytes | None = None

    @property
    def meta(self) -> dict:
        kind = {
            "DeviceGray": "gray",
            "DeviceRGB": "rgb",
            "DeviceCMYK": "cmyk",
        }.get(self.color_space, "rgb")
        meta = {
            "width": self.width,
            "height": self.height,
            "bpc": self.bits_per_component,
            "cs_kind": kind,
            "n_comps": self.components,
        }
        if self.filter_name:
            meta["filter"] = self.filter_name
        return meta


#: A content operand and a COS number follow the same rule, so they are the
#: same function; the name is kept because that is what this module's callers
#: read it as.
format_number = format_pdf_number


def safe_resource_name(name: str | None, prefix: str) -> str | None:
    """Return *name* when it is safe for content streams, otherwise ``None``."""

    if not name:
        return None
    candidate = str(name).lstrip("/")
    if _SAFE_RESOURCE_RE.match(candidate):
        return candidate
    if _SAFE_RESOURCE_RE.match(prefix + candidate):
        return prefix + candidate
    return None


def pdf_literal(text: str | bytes) -> str:
    """Wrap already-encoded *text* as a PDF literal string.

    The bytes are the string's own: for a simple font they are codes in its
    encoding, and for a CID font they are the two-byte codes. Encoding is the
    caller's decision, because only the caller knows the font.
    """

    raw = text if isinstance(text, bytes) else str(text).encode("utf-8")
    out = bytearray()
    for b in raw:
        if b == 0x5C:
            out.extend(b"\\\\")
        elif b == 0x28:
            out.extend(b"\\(")
        elif b == 0x29:
            out.extend(b"\\)")
        elif b == 0x0A:
            out.extend(b"\\n")
        elif b == 0x0D:
            out.extend(b"\\r")
        elif b == 0x09:
            out.extend(b"\\t")
        elif b < 0x20:
            out.extend(f"\\{b:03o}".encode("ascii"))
        else:
            out.append(b)
    return "(" + out.decode("latin-1") + ")"


_HEX_COLOR_RE = re.compile(r"^#?(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

#: The operators that set a device colour, by how many components it has
#: (stroking, non-stroking). ISO 32000-1 8.6.8: these three spaces are the ones
#: a content stream can set without naming a colour space first.
_COLOR_OPERATORS = {1: ("G", "g"), 3: ("RG", "rg"), 4: ("K", "k")}


def normalize_color(color: Any) -> tuple[float, ...]:
    """A colour a caller asked for, as 1, 3 or 4 components in 0..1.

    One component is DeviceGray, three DeviceRGB, four DeviceCMYK. A single
    number is grey, a ``"#rgb"``/``"#rrggbb"`` string is parsed as 8-bit RGB, and
    anything carrying a ``components`` sequence -- :class:`aspose_pdf.Color` --
    hands that over.

    Grey and RGB channels may be given in 0..255 as well as 0..1, since 8-bit is
    what a colour picker and a stylesheet both speak; a value above 1 means the
    whole colour is on that scale. **CMYK is 0..1 only**: ink is quoted as a
    percentage as often as a byte, and dividing 20 by 255 when 20% was meant
    would be wrong rather than merely imprecise, so it is refused and says so.

    This is for a colour a *caller* supplied, which is why it refuses what it
    cannot read. A colour read from a **file** is tolerated instead and the paint
    skipped -- ``appearance._color_op`` and ``SimplePdf._field_color_operator``
    do that for annotation and field colours -- because a document that is merely
    opened and saved must not be refused over an entry it already holds.
    """
    components = getattr(color, "components", None)
    if components is not None:
        color = components
    elif isinstance(color, str):
        color = _hex_components(color)
    elif isinstance(color, (int, float)) and not isinstance(color, bool):
        color = (color,)

    if isinstance(color, (bytes, bytearray)):
        raise PdfValidationException(
            "A colour is 1 (grey), 3 (RGB) or 4 (CMYK) numbers, a '#rrggbb' "
            "string, or an aspose_pdf.Color."
        )
    try:
        values = [_color_channel(channel) for channel in tuple(color)]
    except TypeError:
        raise PdfValidationException(
            "A colour is 1 (grey), 3 (RGB) or 4 (CMYK) numbers, a '#rrggbb' "
            "string, or an aspose_pdf.Color."
        ) from None
    if len(values) not in _COLOR_OPERATORS:
        raise PdfValidationException(
            f"A colour has 1 (grey), 3 (RGB) or 4 (CMYK) components, not "
            f"{len(values)}."
        )
    if any(value > 1.0 for value in values):
        if len(values) == 4:
            raise PdfValidationException(
                "CMYK components must be between 0 and 1; a percentage or an "
                "8-bit value cannot be told apart from a fraction here."
            )
        values = [value / 255.0 for value in values]
        if any(value > 1.0 for value in values):
            raise PdfValidationException(
                "Colour components must be in 0..1 or 0..255."
            )
    return tuple(values)


def _color_channel(channel: Any) -> float:
    """One colour component as a non-negative finite float."""
    if isinstance(channel, bool):
        raise PdfValidationException("Colour components must be numbers.")
    try:
        value = float(channel)
    except (TypeError, ValueError):
        raise PdfValidationException("Colour components must be numbers.") from None
    if not math.isfinite(value):
        raise PdfValidationException("Colour components must be finite.")
    if value < 0:
        raise PdfValidationException("Colour components cannot be negative.")
    return value


def _hex_components(text: str) -> tuple[float, float, float]:
    """``"#rgb"`` or ``"#rrggbb"`` as three 0..1 channels."""
    if not _HEX_COLOR_RE.match(text.strip()):
        raise PdfValidationException(
            f"{text!r} is not a hex colour; write '#rgb' or '#rrggbb'."
        )
    digits = text.strip().lstrip("#")
    if len(digits) == 3:
        digits = "".join(digit * 2 for digit in digits)
    return tuple(int(digits[i : i + 2], 16) / 255.0 for i in (0, 2, 4))


def color_operator(color: Any, *, stroking: bool) -> str:
    """The operator that sets *color*, in whichever device space it names."""
    values = normalize_color(color)
    stroke_op, fill_op = _COLOR_OPERATORS[len(values)]
    numbers = " ".join(format_number(value) for value in values)
    return f"{numbers} {stroke_op if stroking else fill_op}"


def build_text_stream(
    text: str,
    x: float,
    y: float,
    font_resource: str,
    font_size: float,
    color: Any,
    encoding: str = "WinAnsiEncoding",
) -> bytes:
    """Draw *text* with a simple font, in that font's own encoding.

    The string in a ``Tj`` is a sequence of *codes*, and a simple font gives
    each code a glyph through its encoding. Writing the text's UTF-8 into one
    therefore draws whatever glyphs those bytes name -- two wrong letters for
    every accented one, and nothing recognisable for a script the encoding does
    not hold. A character the font cannot show is refused rather than drawn as
    something else.
    """
    parts = [
        "q",
        color_operator(color, stroking=False),
        "BT",
        f"/{font_resource} {format_number(font_size)} Tf",
        f"1 0 0 1 {format_number(x)} {format_number(y)} Tm",
        f"{pdf_literal(encode_simple_text(text, encoding))} Tj",
        "ET",
        "Q",
    ]
    return (" ".join(parts) + "\n").encode("latin-1")


def encode_simple_text(text: str, encoding: str) -> bytes:
    """The codes *text* has in *encoding*, or a refusal naming what it lacks."""
    encoded = encode_with_base_encoding(str(text), encoding)
    if encoded is not None:
        return encoded
    missing = "".join(unencodable_characters(str(text), encoding))
    raise PdfValidationException(
        f"{encoding} has no code for {missing!r}. A standard font can only "
        "draw the characters its encoding names; pass font= to embed a font "
        "that covers this text."
    )


def build_cid_text_stream(
    encoded_text: bytes,
    x: float,
    y: float,
    font_resource: str,
    font_size: float,
    color: Any,
) -> bytes:
    """Build a text fragment whose show string contains two-byte CID codes."""

    raw = bytes(encoded_text)
    if len(raw) % 2:
        raise PdfValidationException("CID text must contain complete two-byte codes.")
    parts = [
        "q",
        color_operator(color, stroking=False),
        f"1 0 0 1 {format_number(x)} {format_number(y)} cm",
        "BT",
        f"/{font_resource} {format_number(font_size)} Tf",
        f"<{raw.hex().upper()}> Tj",
        "ET",
        "Q",
    ]
    return (" ".join(parts) + "\n").encode("ascii")


def build_word_spaced_text_stream(
    text: str,
    extra: float,
    x: float,
    y: float,
    font_resource: str,
    font_size: float,
    color: Any,
    encoding: str = "WinAnsiEncoding",
) -> bytes:
    """Draw *text* with *extra* points added to every space in it.

    This is how a **simple** font's line is justified. ``Tw`` (ISO 32000-1
    9.3.3) widens the advance of the single-byte code 32 itself, so the line
    carries no positioning adjustments at all: the string is the text, the
    spaces are spaces, and every reader -- including one whose extractor
    synthesises a space from a wide gap -- reads back exactly the words that
    were written. A ``TJ`` of inter-word adjustments places the same glyphs in
    the same places, but a gap then holds both a space *and* a displacement,
    and an extractor that counts the displacement as well reads two spaces
    where there is one. pdfminer.six does exactly that; pdfium does not.

    ``Tw`` is only an option here because a simple font's space really is byte
    32. A composite font has no single-byte code (9.3.3: word spacing "shall
    not apply" to a two-byte code), so :func:`build_adjusted_text_stream` is
    what justifies that.
    """
    parts = [
        "q",
        color_operator(color, stroking=False),
        "BT",
        f"/{font_resource} {format_number(font_size)} Tf",
        f"{format_number(extra)} Tw",
        f"1 0 0 1 {format_number(x)} {format_number(y)} Tm",
        f"{pdf_literal(encode_simple_text(text, encoding))} Tj",
        "ET",
        "Q",
    ]
    return (" ".join(parts) + "\n").encode("latin-1")


def build_adjusted_text_stream(
    segments: Sequence[bytes],
    adjustments: Sequence[float],
    x: float,
    y: float,
    font_resource: str,
    font_size: float,
    color: Any,
    *,
    hex_strings: bool,
) -> bytes:
    """Show *segments* in one line, inserting *adjustments* points between them.

    This is how a justified line is drawn. A number inside a ``TJ`` array moves
    the next glyph by ``-number / 1000`` of the font size (ISO 32000-1 9.4.3),
    so the extra space a gap needs is written as a negative number -- the slack
    in points scaled into thousandths of an em.

    ``Tw`` would be shorter, but it only ever reaches **byte 32**: in a
    composite font a single-byte code 32 does not exist (9.3.3 says word
    spacing "shall not apply" to a two-byte code), so an Identity-encoded
    Type0 font ignores it entirely and the line comes out unjustified. One
    ``TJ`` works for both kinds of font, which is why there is one path here.
    """
    if len(adjustments) != max(len(segments) - 1, 0):
        raise PdfValidationException(
            "A justified line needs one adjustment for each gap between its "
            "segments."
        )
    if font_size <= 0:
        raise PdfValidationException("font_size must be a positive number.")

    def show(raw: bytes) -> str:
        return f"<{bytes(raw).hex().upper()}>" if hex_strings else pdf_literal(raw)

    items: list[str] = []
    for index, segment in enumerate(segments):
        items.append(show(segment))
        if index < len(adjustments):
            # Negative moves the next glyph to the right, which is what opening
            # the gap means.
            items.append(format_number(-adjustments[index] * 1000.0 / font_size))
    parts = [
        "q",
        color_operator(color, stroking=False),
        "BT",
        f"/{font_resource} {format_number(font_size)} Tf",
        f"1 0 0 1 {format_number(x)} {format_number(y)} Tm",
        "[" + " ".join(items) + "] TJ",
        "ET",
        "Q",
    ]
    return (" ".join(parts) + "\n").encode("latin-1")


def build_positioned_cid_text_stream(
    lines: Sequence[
        tuple[str, Sequence[tuple[bytes, str, float, float]]]
    ],
    font_size: float,
    color: Any,
) -> bytes:
    """Build shaped lines from absolute glyph positions and named ActualText."""
    parts = ["q", color_operator(color, stroking=False)]
    for property_name, glyphs in lines:
        if safe_resource_name(property_name, "AT") != property_name:
            raise PdfValidationException("ActualText property name is invalid.")
        parts.extend([f"/Span /{property_name} BDC", "BT"])
        current_font: str | None = None
        for encoded, font_resource, glyph_x, glyph_y in glyphs:
            raw = bytes(encoded)
            if len(raw) != 2:
                raise PdfValidationException(
                    "Each positioned CID glyph must contain one two-byte code."
                )
            if safe_resource_name(font_resource, "F") != font_resource:
                raise PdfValidationException("Font resource name is invalid.")
            if font_resource != current_font:
                parts.append(f"/{font_resource} {format_number(font_size)} Tf")
                current_font = font_resource
            parts.extend(
                [
                    (
                        "1 0 0 1 "
                        f"{format_number(glyph_x)} {format_number(glyph_y)} Tm"
                    ),
                    f"<{raw.hex().upper()}> Tj",
                ]
            )
        parts.extend(["ET", "EMC"])
    parts.append("Q")
    return (" ".join(parts) + "\n").encode("ascii")


def build_image_stream(
    image_resource: str,
    x: float,
    y: float,
    width: float,
    height: float,
) -> bytes:
    parts = [
        "q",
        (
            f"{format_number(width)} 0 0 {format_number(height)} "
            f"{format_number(x)} {format_number(y)} cm"
        ),
        f"/{image_resource} Do",
        "Q",
    ]
    return (" ".join(parts) + "\n").encode("ascii")


def build_rectangle_stream(
    x: float,
    y: float,
    width: float,
    height: float,
    *,
    stroke_color: Any | None,
    fill_color: Any | None,
    line_width: float,
) -> bytes:
    if stroke_color is None and fill_color is None:
        raise PdfValidationException("A rectangle needs a stroke or fill color.")
    op = "B" if stroke_color is not None and fill_color is not None else "S"
    if fill_color is not None and stroke_color is None:
        op = "f"
    parts = ["q"]
    if stroke_color is not None:
        parts.append(color_operator(stroke_color, stroking=True))
        parts.append(f"{format_number(line_width)} w")
    if fill_color is not None:
        parts.append(color_operator(fill_color, stroking=False))
    parts.append(
        f"{format_number(x)} {format_number(y)} "
        f"{format_number(width)} {format_number(height)} re {op}"
    )
    parts.append("Q")
    return (" ".join(parts) + "\n").encode("ascii")


#: The line caps and joins of ISO 32000-1 8.4.3.3 and 8.4.3.4, by name.
LINE_CAPS = {"butt": 0, "round": 1, "square": 2}
LINE_JOINS = {"miter": 0, "round": 1, "bevel": 2}

#: The blend modes of 11.3.5, which an authored ``/ExtGState`` may name.
BLEND_MODES = (
    "Normal", "Multiply", "Screen", "Overlay", "Darken", "Lighten",
    "ColorDodge", "ColorBurn", "HardLight", "SoftLight", "Difference",
    "Exclusion", "Hue", "Saturation", "Color", "Luminosity",
)


def _enumerated(value: Any, table: dict[str, int], what: str) -> int:
    """*value* as the number PDF writes for it, by name or by number."""
    if isinstance(value, bool):
        raise PdfValidationException(f"{what} must be a name or a number.")
    if isinstance(value, str):
        key = value.strip().lower()
        if key not in table:
            raise PdfValidationException(
                f"{what} is one of: " + ", ".join(sorted(table)) + f"; not {value!r}."
            )
        return table[key]
    if isinstance(value, int):
        if value not in table.values():
            raise PdfValidationException(
                f"{what} is {sorted(set(table.values()))} as a number; not {value!r}."
            )
        return value
    raise PdfValidationException(f"{what} must be a name or a number.")


def dash_operator(dash: Any) -> str:
    """The ``d`` operator for *dash*: a pattern, or a ``(pattern, phase)`` pair.

    ISO 32000-1 8.4.3.6: the pattern alternates on and off lengths in user space
    and the phase says how far into it the line starts. ``None`` and an empty
    pattern are both the solid line, which is what ``[] 0 d`` means. A pattern of
    all zeros would make a line that is nowhere drawn and is refused, since the
    caller asked for a dashed line and would get an invisible one.
    """
    phase: float = 0.0
    pattern: Any = dash
    if isinstance(dash, tuple) and len(dash) == 2 and not _is_number(dash[0]):
        pattern, phase = dash
    if pattern is None:
        return "[] 0 d"
    if _is_number(pattern):
        pattern = [pattern]
    try:
        lengths = [float(value) for value in pattern]
    except (TypeError, ValueError):
        raise PdfValidationException(
            "A dash pattern is a sequence of numbers, or (pattern, phase)."
        ) from None
    if any(not math.isfinite(value) or value < 0 for value in lengths):
        raise PdfValidationException("Dash lengths must be finite and not negative.")
    if lengths and not any(value > 0 for value in lengths):
        raise PdfValidationException(
            "A dash pattern of zeros would draw nothing; leave it out for a solid line."
        )
    try:
        offset = float(phase)
    except (TypeError, ValueError):
        raise PdfValidationException("A dash phase is a number.") from None
    if not math.isfinite(offset) or offset < 0:
        raise PdfValidationException("A dash phase must be finite and not negative.")
    numbers = " ".join(format_number(value) for value in lengths)
    return f"[{numbers}] {format_number(offset)} d"


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def transform_operator(transform: Any) -> str:
    """The ``cm`` operator for a six-number matrix ``(a, b, c, d, e, f)``.

    Also accepts anything carrying those six as attributes, which is what
    ``aspose_pdf.presentation.IMatrix`` is.
    """
    if hasattr(transform, "a") and hasattr(transform, "f"):
        values = [getattr(transform, name) for name in "abcdef"]
    else:
        try:
            values = list(transform)
        except TypeError:
            raise PdfValidationException(
                "A transform is six numbers (a, b, c, d, e, f) or an IMatrix."
            ) from None
    if len(values) != 6:
        raise PdfValidationException(
            f"A transform is six numbers (a, b, c, d, e, f), not {len(values)}."
        )
    numbers = []
    for name, value in zip("abcdef", values):
        if isinstance(value, bool):
            raise PdfValidationException(f"Transform {name} must be a number.")
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise PdfValidationException(f"Transform {name} must be a number.") from None
        if not math.isfinite(number):
            raise PdfValidationException(f"Transform {name} must be finite.")
        numbers.append(format_number(number))
    return " ".join(numbers) + " cm"


def path_operators(path: Any) -> str:
    """The segment operators of a :class:`~aspose_pdf.paths.GraphicsPath`."""
    parts = []
    for operator, values in path._operators():
        numbers = " ".join(format_number(value) for value in values)
        parts.append(f"{numbers} {operator}" if numbers else operator)
    return " ".join(parts)


def paint_operator(
    *, has_fill: bool, has_stroke: bool, even_odd: bool, clip: bool = False
) -> str:
    """The operator that paints a path the way it is meant to be painted.

    ISO 32000-1 8.5.3: ``f``/``f*`` fills by the nonzero or even-odd rule,
    ``S`` strokes, ``B``/``B*`` does both, and ``n`` paints nothing -- which is
    what a path used only to clip with ends in, after the ``W`` that takes it as
    the clipping path.
    """
    suffix = "*" if even_odd else ""
    if clip:
        operator = "W" + suffix + " "
    else:
        operator = ""
    if has_fill and has_stroke:
        return operator + "B" + suffix
    if has_fill:
        return operator + "f" + suffix
    if has_stroke:
        return operator + "S"
    if clip:
        return operator + "n"
    raise PdfValidationException("A path needs a stroke colour, a fill colour, or both.")


def build_graphics_state(
    *,
    stroke_color: Any = None,
    fill_color: Any = None,
    line_width: float | None = None,
    line_cap: Any = None,
    line_join: Any = None,
    miter_limit: float | None = None,
    dash: Any = None,
    transform: Any = None,
    ext_gstate: str | None = None,
) -> list[str]:
    """The state operators a drawing sets before its path, in writing order.

    The transform comes first, because it applies to the coordinates that follow
    it, and the colour and line state after -- their order among themselves does
    not matter to a reader, and this one keeps the bytes a rectangle has always
    been written with.
    """
    parts: list[str] = []
    if transform is not None:
        parts.append(transform_operator(transform))
    if ext_gstate is not None:
        parts.append(f"/{ext_gstate} gs")
    if stroke_color is not None:
        parts.append(color_operator(stroke_color, stroking=True))
        if line_width is not None:
            parts.append(f"{format_number(line_width)} w")
    if fill_color is not None:
        parts.append(color_operator(fill_color, stroking=False))
    if line_cap is not None:
        parts.append(f"{_enumerated(line_cap, LINE_CAPS, 'A line cap')} J")
    if line_join is not None:
        parts.append(f"{_enumerated(line_join, LINE_JOINS, 'A line join')} j")
    if miter_limit is not None:
        limit = float(miter_limit)
        if not math.isfinite(limit) or limit < 1:
            raise PdfValidationException("A miter limit is at least 1 (8.4.3.5).")
        parts.append(f"{format_number(limit)} M")
    if dash is not None:
        parts.append(dash_operator(dash))
    return parts


def build_path_stream(
    path: Any,
    *,
    stroke_color: Any = None,
    fill_color: Any = None,
    line_width: float = 1.0,
    even_odd: bool = False,
    line_cap: Any = None,
    line_join: Any = None,
    miter_limit: float | None = None,
    dash: Any = None,
    transform: Any = None,
    ext_gstate: str | None = None,
) -> bytes:
    """A complete ``q ... Q`` drawing of *path*, state and painting included."""
    if stroke_color is None and fill_color is None:
        raise PdfValidationException(
            "A path needs a stroke colour, a fill colour, or both."
        )
    parts = ["q"]
    parts.extend(
        build_graphics_state(
            stroke_color=stroke_color,
            fill_color=fill_color,
            line_width=line_width,
            line_cap=line_cap,
            line_join=line_join,
            miter_limit=miter_limit,
            dash=dash,
            transform=transform,
            ext_gstate=ext_gstate,
        )
    )
    parts.append(path_operators(path))
    parts.append(
        paint_operator(
            has_fill=fill_color is not None,
            has_stroke=stroke_color is not None,
            even_odd=even_odd,
        )
    )
    parts.append("Q")
    return (" ".join(parts) + "\n").encode("ascii")


def build_line_stream(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    stroke_color: Any,
    line_width: float,
) -> bytes:
    parts = [
        "q",
        color_operator(stroke_color, stroking=True),
        f"{format_number(line_width)} w",
        (
            f"{format_number(x1)} {format_number(y1)} m "
            f"{format_number(x2)} {format_number(y2)} l S"
        ),
        "Q",
    ]
    return (" ".join(parts) + "\n").encode("ascii")


def wrap_marked_content(content: bytes, tag: str, mcid: int) -> bytes:
    """Wrap a content stream fragment in a tagged marked-content sequence."""

    prefix = f"/{tag} << /MCID {int(mcid)} >> BDC\n".encode("ascii")
    suffix = b"EMC\n"
    body = bytes(content)
    if body and not body.endswith((b"\n", b"\r")):
        body += b"\n"
    return prefix + body + suffix


#: The artifact types ISO 32000-1 14.8.2.2 defines. ``Pagination`` is running
#: matter -- a header, a footer, a page number, a watermark; ``Layout`` is a
#: visual device such as a rule or a box; ``Page`` is production furniture such
#: as a cut mark or a colour bar.
ARTIFACT_TYPES = ("Pagination", "Layout", "Page")

#: ``/Subtype`` values Table 330 gives a ``Pagination`` artifact.
ARTIFACT_SUBTYPES = ("Header", "Footer", "Watermark")

#: Page edges an artifact may be attached to.
ARTIFACT_EDGES = ("Top", "Bottom", "Left", "Right")


def wrap_artifact(
    content: bytes,
    *,
    artifact_type: str | None = None,
    subtype: str | None = None,
    bbox: Sequence[float] | None = None,
    attached: Sequence[str] | None = None,
) -> bytes:
    """Wrap a fragment in an ``/Artifact`` marked-content sequence.

    An artifact is content that is *not* part of what the document says: a
    watermark, a page number, a rule. ISO 14289-1 7.1 requires every mark on a
    page of a tagged document to be either tagged as real content or marked as
    an artifact, so a stamp on an otherwise conformant document makes it
    non-conformant until it says it is decoration. This is that statement.

    The sequence goes *outside* any ``q``/``Q`` the fragment carries, which is
    what 14.6 means by properly nested: a marked-content sequence may contain
    whole graphics-state pairs but may not straddle one.

    ``/BBox`` is the artifact's box in default user space and ``/Attached`` the
    page edges it is attached to -- both of which only mean something for
    running matter, so ``/Attached`` is refused on a ``Layout`` artifact, as
    Table 330 requires.
    """
    properties: list[str] = []
    if artifact_type is not None:
        name = str(artifact_type).strip().lstrip("/")
        if name not in ARTIFACT_TYPES:
            raise PdfValidationException(
                "An artifact type is one of: " + ", ".join(ARTIFACT_TYPES)
            )
        properties.append(f"/Type /{name}")
    else:
        name = None
    if subtype is not None:
        sub = str(subtype).strip().lstrip("/")
        if sub not in ARTIFACT_SUBTYPES:
            raise PdfValidationException(
                "An artifact subtype is one of: " + ", ".join(ARTIFACT_SUBTYPES)
            )
        if name not in (None, "Pagination"):
            raise PdfValidationException(
                f"/Subtype belongs to a Pagination artifact, not to /{name}"
            )
        properties.append(f"/Subtype /{sub}")
    if bbox is not None:
        values = [float(value) for value in bbox]
        if len(values) != 4 or not all(math.isfinite(value) for value in values):
            raise PdfValidationException(
                "An artifact's /BBox is four finite numbers"
            )
        properties.append(
            "/BBox [" + " ".join(format_number(value) for value in values) + "]"
        )
    if attached:
        edges = [str(edge).strip().lstrip("/").title() for edge in attached]
        unknown = [edge for edge in edges if edge not in ARTIFACT_EDGES]
        if unknown:
            raise PdfValidationException(
                "An artifact is attached to one or more of: "
                + ", ".join(ARTIFACT_EDGES)
            )
        if name == "Layout":
            raise PdfValidationException(
                "/Attached belongs to a Pagination or Page artifact, not to a "
                "Layout one"
            )
        properties.append("/Attached [" + " ".join(f"/{e}" for e in edges) + "]")

    head = "/Artifact"
    if properties:
        head += " << " + " ".join(properties) + " >>"
    prefix = (head + " BDC\n").encode("ascii")
    body = bytes(content)
    if body and not body.endswith((b"\n", b"\r")):
        body += b"\n"
    return prefix + body + b"EMC\n"


def image_pixel_size(data: bytes) -> tuple[int, int]:
    """The pixel width and height of JPEG or PNG *data*, from its header alone.

    Something that needs the size *before* it places the image -- a page sized to
    fit the picture on it -- would otherwise have to call :func:`prepare_image`
    first and then place it, decoding a PNG twice over. Only the two formats that
    carry their own geometry are read here; raw samples arrive with their
    dimensions supplied by the caller, so there is nothing to find out.
    """
    payload = bytes(data)
    if payload.startswith(_JPEG_MAGIC):
        width, height, _components, _precision = _jpeg_geometry(payload)
        return width, height
    if payload.startswith(_PNG_MAGIC):
        if len(payload) < 24 or payload[12:16] != b"IHDR":
            raise PdfValidationException("PNG data has no image header.")
        width, height = struct.unpack(">II", payload[16:24])
        if width <= 0 or height <= 0:
            raise PdfValidationException("PNG dimensions must be above zero.")
        return width, height
    raise PdfValidationException(
        "Only JPEG and PNG data carries its own size; raw samples need "
        "pixel_width and pixel_height."
    )


def prepare_image(
    data: bytes,
    *,
    pixel_width: int | None = None,
    pixel_height: int | None = None,
    color_space: str = "DeviceRGB",
    bits_per_component: int = 8,
    limits: PdfLoadLimits | None = None,
    budget: _LoadBudget | None = None,
) -> AuthoredImage:
    """Prepare raw/JPEG/PNG bytes for a PDF image XObject."""

    resolved_limits = _coerce_limits(limits)
    if budget is None:
        active_budget = _LoadBudget(resolved_limits)
    else:
        if not isinstance(budget, _LoadBudget):
            raise TypeError("budget must be a _LoadBudget instance or None")
        if limits is not None and limits != budget.limits:
            raise ValueError("limits must match budget.limits")
        active_budget = budget
    payload = bytes(data)
    active_budget.check(
        len(payload), "max_input_bytes", "authored image input bytes"
    )
    if not payload:
        raise PdfValidationException("Image data cannot be empty.")
    if payload.startswith(_JPEG_MAGIC):
        return _prepare_jpeg(payload, active_budget)
    if payload.startswith(_PNG_MAGIC):
        return _prepare_png(payload, active_budget)
    return _prepare_raw(
        payload,
        pixel_width=pixel_width,
        pixel_height=pixel_height,
        color_space=color_space,
        bits_per_component=bits_per_component,
        budget=active_budget,
    )


def _prepare_raw(
    data: bytes,
    *,
    pixel_width: int | None,
    pixel_height: int | None,
    color_space: str,
    bits_per_component: int,
    budget: _LoadBudget,
) -> AuthoredImage:
    if pixel_width is None or pixel_height is None:
        raise PdfValidationException(
            "Raw image data requires pixel_width and pixel_height."
        )
    width = _positive_int(pixel_width, "pixel_width")
    height = _positive_int(pixel_height, "pixel_height")
    if bits_per_component != 8:
        raise PdfValidationException("Only 8-bit raw image data is supported.")
    cs = _normalize_color_space(color_space)
    components = {"DeviceGray": 1, "DeviceRGB": 3, "DeviceCMYK": 4}[cs]
    expected = width * height * components
    budget.check_image_pixels(width, height, "authored raw image")
    budget.check(expected, "max_codec_work_bytes", "authored raw image samples")
    if len(data) != expected:
        raise PdfValidationException(
            f"Raw image data length must be {expected} bytes for this geometry."
        )
    return AuthoredImage(
        stream_data=data,
        decoded_data=data,
        width=width,
        height=height,
        bits_per_component=8,
        color_space=cs,
        components=components,
    )


def _prepare_jpeg(data: bytes, budget: _LoadBudget) -> AuthoredImage:
    width, height, components, precision = _jpeg_geometry(data)
    budget.check_image_pixels(width, height, "authored JPEG image")
    cs = {1: "DeviceGray", 3: "DeviceRGB", 4: "DeviceCMYK"}.get(
        components, "DeviceRGB"
    )
    return AuthoredImage(
        stream_data=data,
        decoded_data=data,
        width=width,
        height=height,
        bits_per_component=precision,
        color_space=cs,
        components={"DeviceGray": 1, "DeviceRGB": 3, "DeviceCMYK": 4}[cs],
        filter_name="DCTDecode",
    )


def _prepare_png(data: bytes, budget: _LoadBudget) -> AuthoredImage:
    """Decode a PNG to 8-bit samples for embedding.

    ``_decode_png`` already normalises every allowed bit depth to 8 bits per
    sample (rescaled by ``image_resample.sample_to_byte``, the one rule the
    library uses for this; palette indices are left as indices) and reassembles
    Adam7 passes, so the colour type is all that is left to map.
    """
    width, height, _bit_depth, color_type, pixels, alpha = _decode_png(data, budget)
    if color_type == 0:
        decoded = pixels
        cs = "DeviceGray"
        components = 1
    elif color_type == 2:
        decoded = pixels
        cs = "DeviceRGB"
        components = 3
    elif color_type == 3:
        palette, indices = pixels
        decoded = _indexed_png_to_rgb(indices, palette)
        cs = "DeviceRGB"
        components = 3
    elif color_type == 4:
        decoded = pixels[0::2]
        cs = "DeviceGray"
        components = 1
    elif color_type == 6:
        decoded = _strip_rgba_alpha(pixels)
        cs = "DeviceRGB"
        components = 3
    else:
        raise PdfValidationException(f"Unsupported PNG color type: {color_type}.")
    compressed = zlib.compress(decoded, 9)
    return AuthoredImage(
        stream_data=compressed,
        decoded_data=decoded,
        width=width,
        height=height,
        bits_per_component=8,
        color_space=cs,
        components=components,
        filter_name="FlateDecode",
        alpha=alpha if alpha is not None and alpha.count(255) != len(alpha) else None,
    )


def _positive_int(value: int, name: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise PdfValidationException(f"{name} must be a positive integer.")
    if result <= 0:
        raise PdfValidationException(f"{name} must be a positive integer.")
    return result


def _normalize_color_space(value: str) -> str:
    name = str(value).lstrip("/")
    aliases = {
        "G": "DeviceGray",
        "Gray": "DeviceGray",
        "DeviceGray": "DeviceGray",
        "RGB": "DeviceRGB",
        "DeviceRGB": "DeviceRGB",
        "CMYK": "DeviceCMYK",
        "DeviceCMYK": "DeviceCMYK",
    }
    if name not in aliases:
        raise PdfValidationException(
            "color_space must be DeviceGray, DeviceRGB, or DeviceCMYK."
        )
    return aliases[name]


def _jpeg_geometry(data: bytes) -> tuple[int, int, int, int]:
    pos = 2
    sof_markers = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    while pos + 4 <= len(data):
        while pos < len(data) and data[pos] != 0xFF:
            pos += 1
        while pos < len(data) and data[pos] == 0xFF:
            pos += 1
        if pos >= len(data):
            break
        marker = data[pos]
        pos += 1
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            continue
        if pos + 2 > len(data):
            break
        segment_len = struct.unpack(">H", data[pos : pos + 2])[0]
        if segment_len < 2 or pos + segment_len > len(data):
            break
        if marker in sof_markers:
            if segment_len < 8:
                break
            precision = data[pos + 2]
            height = struct.unpack(">H", data[pos + 3 : pos + 5])[0]
            width = struct.unpack(">H", data[pos + 5 : pos + 7])[0]
            components = data[pos + 7]
            if width <= 0 or height <= 0:
                break
            return width, height, components, precision
        pos += segment_len
    raise PdfValidationException("Could not read JPEG dimensions.")


# Adam7 interlacing: (x_start, y_start, x_step, y_step) per pass.
_ADAM7_PASSES = (
    (0, 0, 8, 8),
    (4, 0, 8, 8),
    (0, 4, 4, 8),
    (2, 0, 4, 4),
    (0, 2, 2, 4),
    (1, 0, 2, 2),
    (0, 1, 1, 2),
)
_PNG_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
_PNG_OUTPUT_COMPONENTS = {0: 1, 2: 3, 3: 3, 4: 1, 6: 3}
# ISO/IEC 15948 table 11: which bit depths each colour type allows.
_PNG_ALLOWED_DEPTHS = {
    0: (1, 2, 4, 8, 16),
    2: (8, 16),
    3: (1, 2, 4, 8),
    4: (8, 16),
    6: (8, 16),
}


def _adam7_geometry(width: int, height: int):
    """Yield ``(x0, y0, dx, dy, pass_width, pass_height)`` for non-empty passes."""
    for x0, y0, dx, dy in _ADAM7_PASSES:
        pass_w = (width - x0 + dx - 1) // dx if width > x0 else 0
        pass_h = (height - y0 + dy - 1) // dy if height > y0 else 0
        if pass_w > 0 and pass_h > 0:
            yield x0, y0, dx, dy, pass_w, pass_h


def _png_row_bytes(pixels_per_row: int, channels: int, bit_depth: int) -> int:
    return (pixels_per_row * channels * bit_depth + 7) // 8


def _unpack_png_row(
    row: bytes, pixels: int, channels: int, bit_depth: int, scale: bool
) -> list[int]:
    """Return one row's samples as 8-bit values (indices are left unscaled)."""
    count = pixels * channels
    if bit_depth == 8:
        return list(row[:count])
    mask = (1 << bit_depth) - 1
    if bit_depth == 16:
        # PDF images here are 8 bits per component, so a 16-bit sample is
        # rescaled -- the whole value, not its high byte, which is the faster
        # approximation ISO 15948 10.4 offers for display and which gave this
        # path a different answer from the one the image exporter gave.
        values = [row[i * 2] << 8 | row[i * 2 + 1] for i in range(count)]
        return [sample_to_byte(v, mask) for v in values] if scale else values
    out: list[int] = []
    for index in range(count):
        bit = index * bit_depth
        byte = row[bit >> 3]
        shift = 8 - bit_depth - (bit & 7)
        value = (byte >> shift) & mask
        out.append(sample_to_byte(value, mask) if scale else value)
    return out


def _raw_png_values(row: bytes, pixels: int, channels: int, bit_depth: int) -> list[int]:
    """One row's samples at the image's own depth, 16-bit ones kept whole."""
    if bit_depth == 16:
        return [row[i * 2] << 8 | row[i * 2 + 1] for i in range(pixels * channels)]
    return _unpack_png_row(row, pixels, channels, bit_depth, scale=False)


def _decode_png(data: bytes, budget: _LoadBudget):
    """Decode a PNG to 8-bit samples and, where it has transparency, opacity.

    Returns ``(width, height, bit_depth, color_type, pixels, alpha)``. *alpha*
    holds one opacity byte per pixel, read from the alpha channel (colour types
    4 and 6) or from a ``tRNS`` chunk -- a per-entry opacity for a palette, or a
    single colour that is fully transparent for grey and RGB, compared at the
    image's own bit depth -- and is ``None`` for an image with neither.
    """
    pos = len(_PNG_MAGIC)
    width = height = bit_depth = color_type = None
    interlace = 0
    palette = None
    transparency = None
    idat = bytearray()
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        tag = data[pos + 4 : pos + 8]
        payload_start = pos + 8
        payload_end = payload_start + length
        if payload_end + 4 > len(data):
            raise PdfValidationException("PNG chunk extends past end of data.")
        payload = data[payload_start:payload_end]
        pos = payload_end + 4
        if tag == b"IHDR":
            if length != 13:
                raise PdfValidationException("Invalid PNG IHDR chunk.")
            (
                width,
                height,
                bit_depth,
                color_type,
                compression,
                filter_method,
                interlace,
            ) = struct.unpack(">IIBBBBB", payload)
            if compression != 0 or filter_method != 0:
                raise PdfValidationException("Unsupported PNG compression/filter.")
            if interlace not in (0, 1):
                raise PdfValidationException("Unsupported PNG interlace method.")
        elif tag == b"PLTE":
            palette = payload
        elif tag == b"tRNS":
            transparency = payload
        elif tag == b"IDAT":
            idat.extend(payload)
        elif tag == b"IEND":
            break
    if width is None or height is None or bit_depth is None or color_type is None:
        raise PdfValidationException("PNG image is missing IHDR.")
    if width <= 0 or height <= 0:
        raise PdfValidationException("PNG width and height must be positive.")
    channels = _PNG_CHANNELS.get(color_type)
    if channels is None:
        raise PdfValidationException(f"Unsupported PNG color type: {color_type}.")
    if bit_depth not in _PNG_ALLOWED_DEPTHS[color_type]:
        raise PdfValidationException(
            f"PNG bit depth {bit_depth} is not allowed for color type "
            f"{color_type}."
        )
    budget.check_image_pixels(width, height, "authored PNG image")

    if interlace:
        geometry = list(_adam7_geometry(width, height))
        filtered_size = sum(
            pass_h * (_png_row_bytes(pass_w, channels, bit_depth) + 1)
            for _x0, _y0, _dx, _dy, pass_w, pass_h in geometry
        )
    else:
        geometry = [(0, 0, 1, 1, width, height)]
        filtered_size = height * (_png_row_bytes(width, channels, bit_depth) + 1)

    output_components = _PNG_OUTPUT_COMPONENTS[color_type]
    output_size = width * height * output_components
    samples_size = width * height * channels
    work_size = len(data) + filtered_size + samples_size + output_size * 2
    budget.check(
        filtered_size,
        "max_decoded_stream_bytes",
        "authored PNG filtered samples",
    )
    budget.check(work_size, "max_codec_work_bytes", "authored PNG working set")
    budget.reserve_decoded(output_size, "authored PNG decoded samples")
    try:
        raw = StreamDecoder.decode(
            bytes(idat),
            "FlateDecode",
            None,
            limits=budget.limits,
            max_output_bytes=filtered_size,
        )
    except zlib.error as exc:
        raise PdfValidationException("PNG image data cannot be decompressed.") from exc

    # Filtering works on whole bytes; sub-byte depths use a 1-byte step.
    bpp = max(1, channels * bit_depth // 8)
    scale = color_type != 3  # palette indices must not be rescaled.
    samples = bytearray(width * height * channels)
    # A grey or RGB tRNS names one colour, at the image's own depth, that is
    # fully transparent; each pixel is compared before scaling to 8 bits.
    key = None
    if transparency is not None and color_type in (0, 2):
        count = 1 if color_type == 0 else 3
        if len(transparency) >= 2 * count:
            key = tuple(
                struct.unpack(">H", transparency[2 * i : 2 * i + 2])[0] for i in range(count)
            )
    keyed = bytearray(b"\xff" * (width * height)) if key is not None else None
    offset = 0
    for x0, y0, dx, dy, pass_w, pass_h in geometry:
        row_len = _png_row_bytes(pass_w, channels, bit_depth)
        chunk = raw[offset : offset + pass_h * (row_len + 1)]
        offset += pass_h * (row_len + 1)
        unfiltered = _png_unfilter(chunk, pass_w, pass_h, row_len, bpp)
        for row_index in range(pass_h):
            row = unfiltered[row_index * row_len : (row_index + 1) * row_len]
            values = _unpack_png_row(row, pass_w, channels, bit_depth, scale)
            y = y0 + row_index * dy
            base = y * width * channels
            for column in range(pass_w):
                x = x0 + column * dx
                target = base + x * channels
                source = column * channels
                samples[target : target + channels] = bytes(
                    values[source : source + channels]
                )
            if keyed is not None:
                raw_values = _raw_png_values(row, pass_w, channels, bit_depth)
                for column in range(pass_w):
                    if tuple(raw_values[column * channels : (column + 1) * channels]) == key:
                        keyed[y * width + x0 + column * dx] = 0

    pixels = bytes(samples)
    alpha: bytes | None = None
    if color_type in (4, 6):
        alpha = pixels[channels - 1 :: channels]
    elif keyed is not None:
        alpha = bytes(keyed)
    if color_type == 3:
        if palette is None:
            raise PdfValidationException("Indexed PNG image is missing a palette.")
        if transparency:
            # One opacity per palette entry; entries past the list are opaque.
            opacity = bytes(transparency) + b"\xff" * (256 - min(256, len(transparency)))
            alpha = pixels.translate(opacity[:256])
        return width, height, bit_depth, color_type, (palette, pixels), alpha
    return width, height, bit_depth, color_type, pixels, alpha


def _png_unfilter(
    raw: bytes, width: int, height: int, row_len: int, bpp: int
) -> bytes:
    expected = height * (row_len + 1)
    if len(raw) < expected:
        raise PdfValidationException("PNG image data is truncated.")
    rows = []
    prev = bytearray(row_len)
    offset = 0
    for _y in range(height):
        filt = raw[offset]
        offset += 1
        cur = bytearray(raw[offset : offset + row_len])
        offset += row_len
        for i in range(row_len):
            left = cur[i - bpp] if i >= bpp else 0
            up = prev[i]
            up_left = prev[i - bpp] if i >= bpp else 0
            if filt == 1:
                cur[i] = (cur[i] + left) & 0xFF
            elif filt == 2:
                cur[i] = (cur[i] + up) & 0xFF
            elif filt == 3:
                cur[i] = (cur[i] + ((left + up) // 2)) & 0xFF
            elif filt == 4:
                cur[i] = (cur[i] + _paeth(left, up, up_left)) & 0xFF
            elif filt != 0:
                raise PdfValidationException(f"Unsupported PNG filter type: {filt}.")
        rows.append(bytes(cur))
        prev = cur
    return b"".join(rows)


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _indexed_png_to_rgb(indices: bytes, palette: bytes) -> bytes:
    out = bytearray(len(indices) * 3)
    for i, idx in enumerate(indices):
        p = idx * 3
        if p + 2 >= len(palette):
            continue
        out[i * 3] = palette[p]
        out[i * 3 + 1] = palette[p + 1]
        out[i * 3 + 2] = palette[p + 2]
    return bytes(out)


def _strip_rgba_alpha(data: bytes) -> bytes:
    out = bytearray((len(data) // 4) * 3)
    j = 0
    for i in range(0, len(data) - 3, 4):
        out[j] = data[i]
        out[j + 1] = data[i + 1]
        out[j + 2] = data[i + 2]
        j += 3
    return bytes(out)
