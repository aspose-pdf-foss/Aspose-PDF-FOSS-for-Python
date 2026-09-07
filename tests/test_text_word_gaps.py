"""Telling a word gap from kerning, with the font's real metrics.

A ``TJ`` array separates its strings with displacements in thousandths of an em:
a small negative one is kerning inside a word, a large one is the space between
words. Which is which depends on how wide the glyphs actually are, and the
extractor asked the font -- but a Standard 14 font is normally written *without*
a ``/Widths`` array, its metrics being the reader's to know. Missing them, the
extractor assumed 1000 units for every glyph, about four times a typical
lowercase letter, and put the word-gap threshold four times too high. Ordinary
word spacing was read as kerning and the words ran together: ``Hello world``
came out ``Helloworld``.

The bundled metric-compatible substitutes already answer this question for the
appearance builders, which measure text with them to wrap and centre it. The
extractor asks them too now.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.content_stream_parser import ContentStreamParser

_STANDARD_14 = {
    "Subtype": "Type1",
    "BaseFont": "/Helvetica",
    "Encoding": "/WinAnsiEncoding",
}


def _text(content: bytes, font: dict | None = None) -> str:
    resources = {"Font": {"F1": font if font is not None else dict(_STANDARD_14)}}
    return ContentStreamParser(content, resources).extract_text()


def _tj(adjustment: int, size: int = 12) -> str:
    return _text(
        b"BT /F1 "
        + str(size).encode()
        + b" Tf 72 700 Td [(Hello) "
        + str(adjustment).encode()
        + b" (world)] TJ ET"
    )


# ---------------------------------------------------------------------------
# The threshold
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("adjustment", [-200, -250, -300, -400, -1000])
def test_a_word_gap_becomes_a_space(adjustment):
    """A gap of a fifth of an em upwards is between words, not inside one."""
    assert _tj(adjustment) == "Hello world"


@pytest.mark.parametrize("adjustment", [-10, -30, -80, -150, 0, 50])
def test_kerning_inside_a_word_does_not(adjustment):
    assert _tj(adjustment) == "Helloworld"


def test_the_threshold_follows_the_glyph_that_precedes_it():
    """Helvetica's `o` is 556 units and Courier's is 600, so the same
    displacement can be a word gap in one face and kerning in the other."""
    narrow = _text(
        b"BT /F1 12 Tf 72 700 Td [(i) -170 (i)] TJ ET",
        {"Subtype": "Type1", "BaseFont": "/Helvetica", "Encoding": "/WinAnsiEncoding"},
    )

    assert narrow == "i i"


def test_a_font_that_declares_its_widths_is_believed():
    """`/Widths` is the font's own answer and comes first."""
    font = {
        "Subtype": "Type1",
        "BaseFont": "/Helvetica",
        "Encoding": "/WinAnsiEncoding",
        "FirstChar": 32,
        "Widths": [1000] * 96,
    }

    assert _tj(-250) == "Hello world"
    assert (
        _text(
            b"BT /F1 12 Tf 72 700 Td [(Hello) -250 (world)] TJ ET",
            font,
        )
        == "Helloworld"
    )


def test_an_unknown_face_is_measured_with_a_substitute():
    """A font nobody has heard of is still routed to a substitute by its
    descriptor, so its words separate like anyone else's."""
    font = {"Subtype": "Type1", "BaseFont": "/NoSuchFaceAtAll"}

    assert _text(b"BT /F1 12 Tf 72 700 Td [(Hello) -250 (world)] TJ ET", font) == (
        "Hello world"
    )


def test_a_font_the_stream_never_names_reads_on_the_flat_estimate():
    """No `Tf`, so no font and no metrics: the 1000-unit assumption is all
    there is, and it is the one this fixes everywhere it can be fixed."""
    assert _text(b"BT 72 700 Td [(Hello) -250 (world)] TJ ET") == "Helloworld"


# ---------------------------------------------------------------------------
# Through a document
# ---------------------------------------------------------------------------


def _document(base: bytes) -> Document:
    content = (
        b"BT /F1 11 Tf 72 700 Td "
        b"[(The) -250 (quick) -30 (,) -250 (brown) -250 (fox)] TJ ET\n"
    )
    raw = (
        b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >> endobj\n"
        b"4 0 obj << /Length "
        + str(len(content)).encode()
        + b" >> stream\n"
        + content
        + b"endstream endobj\n"
        b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /" + base + b" "
        b"/Encoding /WinAnsiEncoding >> endobj\n"
        b"trailer << /Root 1 0 R /Size 6 >>\n%%EOF\n"
    )
    return Document(io.BytesIO(raw))


@pytest.mark.parametrize(
    "base", [b"Helvetica", b"Times-Roman", b"Courier", b"Helvetica-Bold"]
)
def test_a_kerned_sentence_reads_as_a_sentence(base):
    """The same words and the same displacements in four faces, each of which
    has different metrics -- and pdfminer reads all four the same way."""
    assert _document(base).pages[0].extract_text() == "The quick, brown fox"
