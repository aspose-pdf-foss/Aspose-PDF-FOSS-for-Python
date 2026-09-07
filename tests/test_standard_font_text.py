"""Text drawn with a standard font, in the encoding that font actually uses.

The string in a ``Tj`` is a sequence of *codes*, and a simple font gives each
code a glyph through its encoding. ``add_text`` wrote the text's **UTF-8** into
one and declared no encoding at all, so the font fell back to its built-in
StandardEncoding and drew whatever glyphs those bytes named: ``café`` came out
as two wrong letters where the accent was, ``100€`` as three, and Cyrillic as a
row of Latin nonsense. Nothing failed and nothing warned -- the page simply said
something else. Reading it back through this library hid it, because the
extractor made the same wrong assumption; pdfminer and Acrobat did not.

Only ASCII was safe, which is a narrow definition of working.

The font now declares ``WinAnsiEncoding`` and the text is encoded into it,
using the inverse of the very table the reader resolves codes through. A
character that encoding has no code for is refused, naming it, rather than
drawn as a different one.
"""

from __future__ import annotations

import io
import re

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.agl import (
    base_encoding_table,
    encode_with_base_encoding,
    glyph_name_to_scalar,
    glyph_name_to_unicode,
)
from aspose_pdf.exceptions import PdfValidationException

# Everything here is in WinAnsiEncoding, and none of it is ASCII.
WINANSI = ["café", "Grüße", "“quoted”", "en–dash", "100€", "• item", "£1", "½"]


def _saved(document: Document) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _drawn(text: str, font_name: str | None = None) -> bytes:
    document = Document()
    document.pages.add().add_text(text, 72, 700, font_size=14, font_name=font_name)
    return _saved(document)


def _shown(data: bytes) -> bytes:
    match = re.search(rb"\((?:[^()\\]|\\.)*\)\s*Tj", data)
    assert match is not None, "no text-showing operator in the content stream"
    return match.group(0)[:-3].strip()


def _read_back(data: bytes) -> str:
    return Document(io.BytesIO(data)).pages[0].to_markdown().strip()


# ---------------------------------------------------------------------------
# What the file holds
# ---------------------------------------------------------------------------


def test_the_font_says_which_encoding_its_codes_are_in():
    """Without it a reader falls back to the font's built-in encoding, which
    for the text faces is StandardEncoding and has no accented letters."""
    assert b"/Encoding /WinAnsiEncoding" in _drawn("hello")


@pytest.mark.parametrize("font_name", ["Symbol", "ZapfDingbats"])
def test_a_symbolic_face_is_left_its_built_in_encoding(font_name):
    """Declaring a base encoding replaces the font's own (ISO 32000-1 9.6.6.2).
    Symbol would then take `a` for Latin *a*, which it has no glyph for, and the
    page would draw nothing."""
    data = _drawn("abg", font_name)

    assert b"/Encoding" not in data
    assert _shown(data) == b"(abg)"


def test_text_is_written_as_codes_not_as_utf_8():
    data = _drawn("café")

    assert _shown(data) == b"(caf\xe9)"
    assert "café".encode() not in data


@pytest.mark.parametrize("text", WINANSI)
def test_a_winansi_character_survives_the_round_trip(text):
    assert _read_back(_drawn(text)) == text.replace("• ", "- ")


def test_ascii_is_unchanged():
    """The common case must still be byte-for-byte what it was."""
    assert _shown(_drawn("Hello world")) == b"(Hello world)"


# ---------------------------------------------------------------------------
# What it refuses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["Привет", "αβγ", "日本", "🙂", "a b", "soft­hyphen"])
def test_a_character_the_encoding_has_no_code_for_is_refused(text):
    """Including the two that look like they should work: WinAnsiEncoding puts
    *space* at 0xA0 and *hyphen* at 0xAD, so a non-breaking space and a soft
    hyphen have no code of their own and would be drawn as the plain ones."""
    with pytest.raises(PdfValidationException, match="has no code for"):
        _drawn(text)


def test_the_refusal_names_the_characters_and_the_way_out():
    with pytest.raises(PdfValidationException) as raised:
        _drawn("Grüße Привет")

    message = str(raised.value)
    assert "Привет" in message
    assert "ü" not in message  # only what it could not encode
    assert "font=" in message


def test_nothing_is_written_when_the_text_is_refused():
    document = Document()
    document.pages.add()

    with pytest.raises(PdfValidationException):
        document.pages[0].add_text("Привет", 72, 700)

    assert document.pages[0].content == b""


# ---------------------------------------------------------------------------
# The encoder and the reader agree by construction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["WinAnsiEncoding", "StandardEncoding", "MacRomanEncoding"]
)
def test_every_code_the_table_defines_encodes_back_to_itself(name):
    """The encoder inverts the table the reader resolves codes through, so a
    string written and read back agrees by construction rather than by two
    tables happening to match."""
    table = base_encoding_table(name)
    assert table is not None

    for code, glyph in enumerate(table):
        if not glyph:
            continue
        mapped = glyph_name_to_unicode(glyph)
        if mapped is None or len(mapped) != 1:
            continue
        encoded = encode_with_base_encoding(mapped, name)
        assert encoded is not None, f"{name} lost {glyph!r}"
        # A scalar two codes share encodes to the lower one, and both name the
        # same glyph, so the glyph drawn is the glyph asked for.
        assert table[encoded[0]] == glyph


@pytest.mark.parametrize(
    ("glyph", "scalar"),
    [
        ("a", ord("a")),
        ("Euro", 0x20AC),
        ("fi", 0xFB01),  # a *precomposed* ligature is one scalar
        ("f_i", None),  # the two-glyph spelling of it is two
        ("uni00410042", None),
        ("notaglyphname", None),
    ],
)
def test_a_name_stands_for_one_scalar_or_for_none(glyph, scalar):
    """One byte code, one glyph: a name the AGL expands to a *sequence* has no
    place in a table that maps codes either way, and answering with the first
    of them would silently drop the rest."""
    assert glyph_name_to_scalar(glyph) == scalar


@pytest.mark.parametrize("text", ["abc", ""], ids=["text", "nothing"])
def test_an_encoding_that_does_not_exist_encodes_nothing(text):
    """Not even the empty string: an unrecognised encoding name is a mistake,
    and answering it with success is how a mistake becomes a file."""
    assert encode_with_base_encoding(text, "NotAnEncoding") is None
