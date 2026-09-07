"""What an inline image is, in one place.

An inline image is the only construct in a content stream whose bytes are not
tokens. Between ``ID`` and ``EI`` lie the image's samples, and samples spell
whatever they happen to spell: an operator, a name, an opening ``(`` that
closes nowhere. A reader that lexes them reads noise, and because a literal
string runs until its closing paren, it usually loses the whole rest of the
page along with the image.

ISO 32000-1 8.9.7 (table 93) also writes the image's dictionary short -- ``/W``
for ``/Width``, ``/Fl`` for ``/FlateDecode`` -- and gives the rule that makes
the end of the data findable rather than guessable: unfiltered samples occupy
exactly ``ceil(Width x BitsPerComponent x components / 8) x Height`` bytes.
Expanding the names once here lets the rest of the library treat an inline
image as the image XObject it is a short spelling of.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aspose_pdf.exceptions import PdfParseException
from aspose_pdf.load_limits import PdfLoadLimits

from .cos import PdfArray, PdfBoolean, PdfDictionary, PdfName, PdfNumber, PdfStream

__all__ = [
    "COLOURSPACE_ABBREVIATIONS",
    "FILTER_ABBREVIATIONS",
    "KEY_ABBREVIATIONS",
    "InlineImage",
    "inline_image_end",
    "scan_inline_image",
]

#: Table 93: the dictionary keys an inline image abbreviates.
KEY_ABBREVIATIONS = {
    "BPC": "BitsPerComponent",
    "CS": "ColorSpace",
    "D": "Decode",
    "DP": "DecodeParms",
    "F": "Filter",
    "H": "Height",
    "I": "Interpolate",
    "IM": "ImageMask",
    "L": "Length",
    "W": "Width",
}

#: Table 93: the filter names it abbreviates. ``/JPXDecode`` has no short form.
FILTER_ABBREVIATIONS = {
    "A85": "ASCII85Decode",
    "AHx": "ASCIIHexDecode",
    "CCF": "CCITTFaxDecode",
    "DCT": "DCTDecode",
    "Fl": "FlateDecode",
    "LZW": "LZWDecode",
    "RL": "RunLengthDecode",
}

#: Table 93: the colour space names it abbreviates. ``/I`` is *Indexed* as a
#: value and *Interpolate* as a key, which is why the two tables stay apart.
COLOURSPACE_ABBREVIATIONS = {
    "CMYK": "DeviceCMYK",
    "G": "DeviceGray",
    "I": "Indexed",
    "RGB": "DeviceRGB",
}

#: Samples per pixel, for the colour spaces an inline image can name without
#: reaching into the page's resources.
_COMPONENTS = {
    "DeviceGray": 1,
    "CalGray": 1,
    "DeviceRGB": 3,
    "CalRGB": 3,
    "DeviceCMYK": 4,
    "Indexed": 1,
}

_WHITESPACE = " \t\r\n\x0c\x00"
_DELIMITERS = "()<>[]{}/%"
_ENDERS = _WHITESPACE + _DELIMITERS


@dataclass(frozen=True)
class InlineImage:
    """A ``BI ... ID ... EI`` image: its dictionary, expanded, and its samples.

    The dictionary's keys and its filter and colour-space names are the long
    ones, so it reads like the ``/Subtype /Image`` XObject dictionary it stands
    for; :meth:`to_stream` completes that.
    """

    entries: PdfDictionary
    data: bytes

    def to_stream(self) -> PdfStream:
        """The image XObject this inline image is a short spelling of.

        ``content_decrypted`` is left true: the samples came out of a content
        stream that the security handler has already been through, so they are
        plaintext by the time they reach here.
        """
        stream = PdfStream(self.data, dict(self.entries.mapping))
        stream.mapping[PdfName("Type")] = PdfName("XObject")
        stream.mapping[PdfName("Subtype")] = PdfName("Image")
        stream.mapping[PdfName("Length")] = PdfNumber(len(self.data))
        return stream


def scan_inline_image(
    text: str,
    start: int,
    *,
    limits: PdfLoadLimits | None = None,
) -> tuple[InlineImage | None, int]:
    """Read the inline image whose dictionary begins at *start*, just after ``BI``.

    *text* is the content stream decoded with latin-1, so an offset into it is
    an offset into the stream's bytes.

    Returns the image and the offset just past its ``EI``. A dictionary that
    does not parse yields ``(None, offset)`` pointing at where it stopped
    making sense, so a caller can carry on lexing from there rather than
    abandon the page.
    """
    entries, data_start, data_end, end = _scan(text, start, limits)
    if entries is None:
        return None, end
    return InlineImage(entries, _raw(text[data_start:data_end])), end


def inline_image_end(
    text: str,
    start: int,
    *,
    limits: PdfLoadLimits | None = None,
) -> int:
    """Offset just past the ``EI`` of the image whose dictionary begins at *start*.

    The same reading as :func:`scan_inline_image`, for a caller that only needs
    to step over the image and would rather not copy its samples to do so.
    """
    return _scan(text, start, limits)[3]


def inline_image_data_end(text: str, data_start: int) -> int:
    """Offset past an ``EI`` when there is no dictionary to consult.

    For a stray ``ID`` -- one whose ``BI`` never arrived -- where the search for
    a free-standing ``EI`` is all that is left.
    """
    return _find_ei(text, data_start, len(text))[1]


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _raw(chunk: str) -> bytes:
    return chunk.encode("latin-1")


def _scan(
    text: str, start: int, limits: PdfLoadLimits | None
) -> tuple[PdfDictionary | None, int, int, int]:
    """``(entries, data_start, data_end, image_end)`` for the image at *start*."""
    from .pdf_parser_cos import _Tokenizer

    n = len(text)
    entries: dict[PdfName, Any] = {}
    tokenizer = _Tokenizer(text, limits, start=start)
    pos = start
    while True:
        pos = _skip_ws_and_comments(text, pos, n)
        if pos >= n:
            return None, n, n, n
        if text.startswith("ID", pos) and (pos + 2 >= n or text[pos + 2] in _ENDERS):
            pos += 2
            break
        if text[pos] != "/":
            return None, pos, pos, pos
        tokenizer.pos = pos
        try:
            key = tokenizer.read()
            value = tokenizer.read()
        except (PdfParseException, IndexError, ValueError):
            return None, pos, pos, pos
        pos = tokenizer.pos
        if isinstance(key, PdfName):
            name = _expand(key.name, KEY_ABBREVIATIONS)
            entries[PdfName(name)] = _expand_value(name, value)

    # Exactly one whitespace byte separates ID from the samples (8.9.7).
    if pos < n and text[pos] in _WHITESPACE:
        pos += 1

    dictionary = PdfDictionary(entries)
    declared = _declared_data_length(dictionary)
    if declared is not None and pos + declared <= n:
        after = _accept_ei(text, pos + declared, n)
        if after is not None:
            return dictionary, pos, pos + declared, after
        # The dictionary says one length and the bytes end somewhere else. The
        # bytes are what is actually on the page, so go and find their end.
    data_end, after = _find_ei(text, pos, n)
    return dictionary, pos, data_end, after


def _skip_ws_and_comments(text: str, pos: int, n: int) -> int:
    while pos < n:
        ch = text[pos]
        if ch in _WHITESPACE:
            pos += 1
        elif ch == "%":
            while pos < n and text[pos] not in "\r\n":
                pos += 1
        else:
            break
    return pos


def _expand(name: str, table: dict[str, str]) -> str:
    """Return *name* -- a PdfName's ``/x`` spelling -- with its long form."""
    bare = name.lstrip("/")
    return f"/{table.get(bare, bare)}"


def _expand_value(key: str, value: Any) -> Any:
    """Expand the abbreviated names a filter or colour-space value may use."""
    if key == "/Filter":
        return _expand_names(value, FILTER_ABBREVIATIONS)
    if key == "/ColorSpace":
        return _expand_names(value, COLOURSPACE_ABBREVIATIONS)
    return value


def _expand_names(value: Any, table: dict[str, str]) -> Any:
    """Expand a name, or the leading name of an array (``[/I /RGB 255 <..>]``)."""
    if isinstance(value, PdfName):
        return PdfName(_expand(value.name, table))
    if isinstance(value, PdfArray) and value.items:
        items = list(value.items)
        if isinstance(items[0], PdfName):
            items[0] = PdfName(_expand(items[0].name, table))
        return PdfArray(items)
    return value


def _number(entries: PdfDictionary, key: str) -> float | None:
    value = entries.mapping.get(PdfName(key))
    if isinstance(value, PdfNumber):
        return float(value.value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _is_mask(entries: PdfDictionary) -> bool:
    value = entries.mapping.get(PdfName("ImageMask"))
    if isinstance(value, PdfBoolean):
        return bool(value.value)
    return value is True


def _components(entries: PdfDictionary) -> int | None:
    """Samples per pixel, or ``None`` when only the page's resources know."""
    space = entries.mapping.get(PdfName("ColorSpace"))
    if isinstance(space, PdfArray) and space.items:
        head = space.items[0]
        name = head.name.lstrip("/") if isinstance(head, PdfName) else ""
        return _COMPONENTS.get(name)
    if isinstance(space, PdfName):
        return _COMPONENTS.get(space.name.lstrip("/"))
    return None


def _declared_data_length(entries: PdfDictionary) -> int | None:
    """The sample count the dictionary settles, or ``None`` if it does not.

    Unfiltered data has the length 8.9.7 gives it, one row at a time and each
    row padded out to a whole byte. A filter makes the length a property of the
    encoded bytes instead, which only ``/L`` -- or the search for ``EI`` --
    can answer.
    """
    length = _number(entries, "Length")
    if length is not None and length >= 0:
        return int(length)
    if entries.mapping.get(PdfName("Filter")) is not None:
        return None
    width = _number(entries, "Width")
    height = _number(entries, "Height")
    if width is None or height is None or width < 0 or height < 0:
        return None
    if _is_mask(entries):
        bits_per_pixel = 1
    else:
        components = _components(entries)
        bpc = _number(entries, "BitsPerComponent")
        if components is None or bpc is None or bpc <= 0:
            return None
        bits_per_pixel = int(bpc) * components
    return ((int(width) * bits_per_pixel + 7) // 8) * int(height)


def _accept_ei(text: str, pos: int, n: int) -> int | None:
    """Offset past an ``EI`` sitting at *pos* (bar whitespace), else ``None``."""
    at = _skip_ws_and_comments(text, pos, n)
    if not text.startswith("EI", at):
        return None
    if at + 2 < n and text[at + 2] not in _ENDERS:
        return None
    return at + 2


def _find_ei(text: str, start: int, n: int) -> tuple[int, int]:
    """``(data_end, image_end)`` for data whose length the dictionary withheld.

    All that is left is to look for an ``EI`` standing on its own -- preceded by
    whitespace, followed by a delimiter -- which samples can imitate. It is the
    only way to end filtered data, and the reason :func:`_declared_data_length`
    is asked first.
    """
    j = start
    while j + 1 < n:
        if (
            text[j] == "E"
            and text[j + 1] == "I"
            and (j == start or text[j - 1] in _WHITESPACE)
            and (j + 2 >= n or text[j + 2] in _ENDERS)
        ):
            return (j - 1 if j > start else j), j + 2
        j += 1
    return n, n
