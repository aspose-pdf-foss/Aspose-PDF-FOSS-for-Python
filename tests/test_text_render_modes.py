"""A text object's state, and the four rendering modes that clip.

Two halves of one rule.

**The text state is graphics state.** ISO 32000-1 9.4.1 gives ``BT`` two jobs:
the text matrix and the text *line* matrix. Everything else a text object uses
-- the font and its size, ``Tc``, ``Tw``, ``Tz``, ``TL``, ``Tr``, ``Ts`` -- is
listed in 9.3.1 as part of the **graphics** state, so it outlives a text object
and is saved and restored by ``q``/``Q``. Both readers cleared all of it at
``BT``: the renderer then drew a page that selects its font once at the 12pt
fallback in the wrong face, and the extractor read that page's text with no
font at all, dropping every character its encoding was needed for.

**Modes 4 to 7 clip.** Table 106: they add what they show to the clipping path,
which is applied once, at ``ET``. The renderer stored ``Tr`` and never read it
past mode 3, so text-shaped windows -- the usual way to put a picture inside
letters -- did not clip anything and the picture covered the page.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.content_stream_parser import ContentStreamParser

_WHITE = (255, 255, 255)
_GREEN = (0, 255, 0)
_BLUE = (0, 0, 255)

_FONT = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>"


def _document(content: bytes) -> Document:
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources "
            b"<< /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        4: b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        5: _FONT,
    }
    raw = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for number in sorted(objects):
        offsets[number] = len(raw)
        raw += b"%d 0 obj\n" % number + objects[number] + b"\nendobj\n"
    start = len(raw)
    raw += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for number in sorted(objects):
        raw += b"%010d 00000 n \n" % offsets[number]
    raw += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        start,
    )
    return Document(io.BytesIO(bytes(raw)))


def _ink(content: bytes) -> int:
    """How many sampled pixels are not the bare page."""
    raster = _document(content).pages[0].render(antialias=False)
    return sum(
        1
        for y in range(0, 200, 4)
        for x in range(0, 200, 4)
        if raster.get_pixel(x, y) != _WHITE
    )


#: A green wash painted after the text object, to show where the clip lets it
#: through. Without a text clip it covers the page.
_WASH = b"0 1 0 rg 0 0 200 200 re f\n"


def _shown(mode: int, *, after: bytes = b"") -> bytes:
    return (
        b"BT /F1 60 Tf 0 0 1 rg 1 0 0 RG 1.5 w %d Tr 20 80 Td (OBI) Tj ET\n" % mode
    ) + after


def _colours(content: bytes) -> tuple[int, int]:
    """Sampled pixels of the fill colour and of the stroke colour."""
    raster = _document(content).pages[0].render(antialias=False)
    blue = red = 0
    for y in range(0, 200, 2):
        for x in range(0, 200, 2):
            pixel = raster.get_pixel(x, y)
            if pixel == _BLUE:
                blue += 1
            elif pixel == (255, 0, 0):
                red += 1
    return blue, red


# --- the four painting modes ------------------------------------------------


@pytest.mark.parametrize("mode", [0, 4])
def test_a_filling_mode_paints_the_letter_in_the_fill_colour(mode):
    blue, red = _colours(_shown(mode))
    assert blue > 0
    assert red == 0


@pytest.mark.parametrize("mode", [1, 5])
def test_a_stroking_mode_draws_the_outline_in_the_stroke_colour(mode):
    """Table 106 mode 1 strokes and does *not* fill.

    Every stroking mode used to fall through to the fill, so an outlined
    headline came out solid, in the fill colour -- the one colour mode 1 says
    not to use.
    """
    blue, red = _colours(_shown(mode))
    assert red > 0
    assert blue == 0


@pytest.mark.parametrize("mode", [2, 6])
def test_fill_then_stroke_uses_both_colours(mode):
    blue, red = _colours(_shown(mode))
    assert blue > 0
    assert red > 0


def test_an_outline_is_lighter_than_the_letter_and_both_together_are_heavier():
    """The relationship pdfium and MuPDF both draw: 1 < 0 < 2."""
    outline = _ink(_shown(1))
    filled = _ink(_shown(0))
    both = _ink(_shown(2))
    assert outline < filled < both


def test_the_pen_that_strokes_a_glyph_is_the_graphics_state_pen():
    thin = _ink(b"BT /F1 60 Tf 1 0 0 RG 0.4 w 1 Tr 20 80 Td (OBI) Tj ET\n")
    thick = _ink(b"BT /F1 60 Tf 1 0 0 RG 6 w 1 Tr 20 80 Td (OBI) Tj ET\n")
    assert thick > thin


def test_a_clipping_variant_paints_exactly_like_its_counterpart():
    assert _colours(_shown(4)) == _colours(_shown(0))
    assert _colours(_shown(5)) == _colours(_shown(1))
    assert _colours(_shown(6)) == _colours(_shown(2))


# --- the four clipping modes ------------------------------------------------


@pytest.mark.parametrize("mode", [4, 5, 6, 7])
def test_a_clipping_mode_lets_the_page_through_only_where_its_glyphs_are(mode):
    full = _ink(_WASH)
    clipped = _ink(_shown(mode, after=_WASH))
    assert full == 2500  # every sample: the wash covers the page
    assert 0 < clipped < full // 3  # only the letters


@pytest.mark.parametrize("mode", [0, 1, 2, 3])
def test_a_non_clipping_mode_leaves_the_page_alone(mode):
    assert _ink(_shown(mode, after=_WASH)) == _ink(_WASH)


def test_mode_seven_clips_without_painting():
    assert _ink(_shown(7)) == 0
    assert _ink(_shown(4)) > 0


def test_the_clip_arrives_at_et_and_not_before():
    # The wash *inside* the text object is not clipped: the outlines only reach
    # the clipping path when the object ends.
    inside = b"BT /F1 60 Tf 7 Tr 20 80 Td (OBI) Tj " + _WASH + b"ET\n"
    assert _ink(inside) == _ink(_WASH)


def test_the_clip_is_graphics_state_and_comes_back_at_q_restore():
    content = b"q " + _shown(7, after=_WASH) + b"Q 0 0 1 rg 0 0 200 20 re f\n"
    raster = _document(content).pages[0].render(antialias=False)
    # The strip painted after Q is outside the text, and shows all the same.
    assert raster.get_pixel(100, 190) == _BLUE


def test_two_text_objects_intersect_their_clips():
    twice = _shown(7) + _shown(7, after=_WASH)
    once = _shown(7, after=_WASH)
    assert _ink(twice) == _ink(once)  # the same letters, twice over


def test_a_second_clip_that_shares_nothing_leaves_nothing():
    elsewhere = b"BT /F1 60 Tf 7 Tr 20 20 Td (X) Tj ET\n"
    assert _ink(_shown(7) + elsewhere + _WASH) == 0


def test_a_glyph_with_no_outline_still_clips():
    # A space is a glyph; it contributes no outline, so the window it opens is
    # empty and nothing after it reaches the page.
    assert _ink(b"BT /F1 60 Tf 7 Tr 20 80 Td ( ) Tj ET\n" + _WASH) == 0


def test_showing_nothing_clips_nothing():
    # An empty string shows no glyphs, so no clipping path is built at all --
    # which is not the same as building an empty one.
    assert _ink(b"BT /F1 60 Tf 7 Tr 20 80 Td () Tj ET\n" + _WASH) == _ink(_WASH)
    assert _ink(b"BT /F1 60 Tf 7 Tr ET\n" + _WASH) == _ink(_WASH)


def test_an_unterminated_text_object_does_not_leak_its_outlines():
    # A stream can be missing its ET. A new BT starts a fresh object, and the
    # outlines the abandoned one collected are not part of the new one's clip.
    leaked = (
        b"BT /F1 60 Tf 7 Tr 20 130 Td (O) Tj BT /F1 60 Tf 20 30 Td (O) Tj ET\n" + _WASH
    )
    lower_only = b"BT /F1 60 Tf 7 Tr 20 30 Td (O) Tj ET\n" + _WASH
    assert _ink(leaked) == _ink(lower_only)


def test_a_mode_set_after_the_text_clips_nothing():
    content = b"BT /F1 60 Tf 0 Tr 20 80 Td (OBI) Tj 7 Tr ET\n" + _WASH
    assert _ink(content) == _ink(_WASH)


# --- the text state outlives the text object --------------------------------


def test_the_font_and_its_size_survive_bt():
    across = b"BT /F1 60 Tf ET BT 0 0 1 rg 20 80 Td (OBI) Tj ET\n"
    inside = b"BT /F1 60 Tf 0 0 1 rg 20 80 Td (OBI) Tj ET\n"
    assert _ink(across) == _ink(inside) > 0


def test_the_rendering_mode_survives_bt():
    # Mode 3 draws nothing, and a following text object is still in mode 3.
    assert _ink(b"BT /F1 60 Tf 3 Tr ET BT 20 80 Td (OBI) Tj ET\n") == 0


def test_a_clipping_mode_survives_bt_and_the_next_object_clips_too():
    # Two objects, the mode set once: the second inherits it, so its own ET
    # intersects again -- with a glyph that shares nothing, leaving nothing.
    content = b"BT /F1 60 Tf 7 Tr 20 80 Td (OBI) Tj ET BT 20 20 Td (X) Tj ET\n" + _WASH
    assert _ink(content) == 0


def test_character_spacing_survives_bt():
    across = b"BT /F1 30 Tf 20 Tc ET BT 0 0 1 rg 20 80 Td (OBI) Tj ET\n"
    inside = b"BT /F1 30 Tf 20 Tc 0 0 1 rg 20 80 Td (OBI) Tj ET\n"
    assert _ink(across) == _ink(inside) > 0


def test_the_text_matrix_does_not_survive_bt():
    # The one thing BT *does* reset: the second object starts at the origin
    # rather than where the first one left off.
    after_bt = b"BT /F1 30 Tf 0 0 1 rg 1 0 0 1 20 80 Tm (OBI) Tj ET BT (OBI) Tj ET\n"
    spelled_out = (
        b"BT /F1 30 Tf 0 0 1 rg 1 0 0 1 20 80 Tm (OBI) Tj ET "
        b"BT 1 0 0 1 0 0 Tm (OBI) Tj ET\n"
    )
    assert _ink(after_bt) == _ink(spelled_out)
    assert _ink(after_bt) > _ink(
        b"BT /F1 30 Tf 0 0 1 rg 1 0 0 1 20 80 Tm (OBI) Tj ET\n"
    )


def test_q_restore_puts_the_text_state_back():
    content = b"BT /F1 12 Tf ET q BT /F1 60 Tf ET Q BT 0 0 1 rg 20 80 Td (OBI) Tj ET\n"
    small = b"BT /F1 12 Tf 0 0 1 rg 20 80 Td (OBI) Tj ET\n"
    assert _ink(content) == _ink(small)


# --- and the extractor reads the same state ---------------------------------

_WINANSI = {
    "Font": {
        "F1": {
            "Subtype": "Type1",
            "BaseFont": "/Helvetica",
            "Encoding": "/WinAnsiEncoding",
        }
    }
}


def _text(content: bytes) -> str:
    return ContentStreamParser(content, _WINANSI).extract_text()


def test_the_extractor_keeps_the_font_across_bt():
    # Without the font the code 0xE9 has no encoding to be read through, and
    # the character it stands for is simply lost.
    assert _text(b"BT /F1 12 Tf ET BT 20 100 Td (caf\xe9) Tj ET") == "café"


def test_the_extractor_keeps_the_leading_across_bt():
    across = b"BT /F1 12 Tf 14 TL ET BT 20 100 Td (one) Tj T* (two) Tj ET"
    inside = b"BT /F1 12 Tf 14 TL 20 100 Td (one) Tj T* (two) Tj ET"
    assert _text(across) == _text(inside) == "one\ntwo"


def test_the_extractor_still_starts_each_text_object_at_the_origin():
    # BT does reset the matrix, so a second object with no move of its own
    # draws at the origin rather than continuing where the first left off.
    same_line = b"BT /F1 12 Tf 20 100 Td (one) Tj ET BT (two) Tj ET"
    assert _text(same_line) == "one\ntwo"


# --- the SVG export says the same thing -------------------------------------


def test_the_svg_export_turns_a_text_clip_into_a_clip_path():
    svg = _document(_shown(7, after=_WASH)).pages[0].to_svg()
    assert "<clipPath" in svg
    plain = _document(_shown(0, after=_WASH)).pages[0].to_svg()
    assert "<clipPath" not in plain
