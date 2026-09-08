"""A pattern is a colour, so it paints wherever that colour paints.

ISO 32000-1 8.7.3: a pattern is selected with ``scn``/``SCN`` in a Pattern
colour space, and from then on it *is* the fill or the stroke colour. The
renderer honoured that in one place -- filling a path -- and nowhere else:

* ``SCN`` was ignored outright, so a stroke drawn with a pattern came out in
  whatever colour happened to be set, which is black on a fresh page;
* glyphs were filled from ``fill_color``, so text filled with a pattern -- how
  a gradient headline is drawn -- came out solid.

The region differs in each case and the paint does not. A path hands over its
subpaths and a glyph its outlines; a stroke has no such path at all, only the
area the pen covered, so its coverage is collected as a mask and the clip
narrowed to it. After that the same tiler runs for all three.
"""

from __future__ import annotations

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfStream,
)
from aspose_pdf.engine.simple_pdf import SimplePdf

_RED = (255, 0, 0)
_BLUE = (0, 0, 255)
_WHITE = (255, 255, 255)
_BLACK = (0, 0, 0)


def _arr(*values):
    return PdfArray([PdfNumber(v) for v in values])


def _tiling(cell: bytes, *, paint_type: int = 1) -> PdfStream:
    return PdfStream(
        cell,
        {
            PdfName("Type"): PdfName("Pattern"),
            PdfName("PatternType"): PdfNumber(1),
            PdfName("PaintType"): PdfNumber(paint_type),
            PdfName("TilingType"): PdfNumber(1),
            PdfName("BBox"): _arr(0, 0, 10, 10),
            PdfName("XStep"): PdfNumber(10),
            PdfName("YStep"): PdfNumber(10),
            PdfName("Resources"): PdfDictionary({}),
            PdfName("Matrix"): _arr(1, 0, 0, 1, 0, 0),
        },
    )


def _shading() -> PdfDictionary:
    function = PdfDictionary(
        {
            PdfName("FunctionType"): PdfNumber(2),
            PdfName("Domain"): _arr(0, 1),
            PdfName("C0"): _arr(1, 0, 0),
            PdfName("C1"): _arr(1, 0, 0),
            PdfName("N"): PdfNumber(1),
        }
    )
    return PdfDictionary(
        {
            PdfName("PatternType"): PdfNumber(2),
            PdfName("Shading"): PdfDictionary(
                {
                    PdfName("ShadingType"): PdfNumber(2),
                    PdfName("ColorSpace"): PdfName("DeviceRGB"),
                    PdfName("Coords"): _arr(0, 0, 60, 0),
                    PdfName("Function"): function,
                    PdfName("Extend"): PdfArray([]),
                }
            ),
        }
    )


def _document(content: bytes, pattern) -> Document:
    pdf = SimplePdf(pages=[(0, 0, 60, 60)], page_contents=[content])
    pdf._ensure_cos()
    reference = pdf._cos_doc.register_object(pattern)
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
        {
            PdfName("Pattern"): PdfDictionary({PdfName("P0"): reference}),
            PdfName("ColorSpace"): PdfDictionary(
                {PdfName("Cs1"): PdfArray([PdfName("Pattern"), PdfName("DeviceRGB")])}
            ),
        }
    )
    document = Document()
    document._engine_pdf = pdf
    return document


def _pixels(content: bytes, pattern, points):
    raster = _document(content, pattern).pages[0].render(antialias=False)
    return [raster.get_pixel(x, y) for x, y in points]


#: A cell painting a red square in its lower-left quarter and nothing else.
_CELL = b"1 0 0 rg 0 0 5 5 re f"

#: The rows a `10 w` rule along user y=25 covers, on a 60pt-tall page.
_BAND = range(30, 41)


def _band(raster) -> list[tuple[int, int, int]]:
    return [raster.get_pixel(x, y) for y in _BAND for x in range(0, 60)]


# --- stroking with a pattern ------------------------------------------------


def test_a_stroke_takes_the_pattern_scn_gave_it():
    # A thick horizontal rule across the middle. Where the pattern's red square
    # falls under the pen the rule is red; between squares the page shows.
    content = b"/Pattern CS /P0 SCN 10 w 0 25 m 60 25 l S"
    raster = _document(content, _tiling(_CELL)).pages[0].render(antialias=False)
    band = _band(raster)
    assert _RED in band  # the pattern's squares, under the pen
    assert _WHITE in band  # and the gaps between them
    assert _BLACK not in band  # what it used to paint


def test_a_stroke_pattern_does_not_reach_outside_the_pen():
    # A diagonal, so the pen's *bounding box* is the whole page while the pen
    # itself is a line: the corners tell a coverage mask from a bounding box.
    # The cell is solid, so anywhere the pattern reaches is red.
    solid = _tiling(b"1 0 0 rg 0 0 10 10 re f")
    content = b"/Pattern CS /P0 SCN 4 w 0 0 m 60 60 l S"
    raster = _document(content, solid).pages[0].render(antialias=False)
    assert raster.get_pixel(2, 2) == _WHITE  # top-left, off the diagonal
    assert raster.get_pixel(57, 57) == _WHITE  # bottom-right
    assert raster.get_pixel(30, 29) == _RED  # but the diagonal itself


def test_the_clip_goes_back_after_a_patterned_stroke():
    content = b"/Pattern CS /P0 SCN 4 w 0 0 m 60 60 l S 0 0 1 rg 0 50 10 10 re f"
    raster = _document(content, _tiling(_CELL)).pages[0].render(antialias=False)
    # The blue square is nowhere near the pen, and paints all the same.
    assert raster.get_pixel(5, 5) == _BLUE


def test_a_plain_colour_set_after_a_pattern_wins():
    content = b"/Pattern CS /P0 SCN 0 0 1 RG 10 w 0 25 m 60 25 l S"
    raster = _document(content, _tiling(_CELL)).pages[0].render(antialias=False)
    assert raster.get_pixel(30, 33) == _BLUE


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        # `RG` replaces the colour, pattern and all.
        (b"/Pattern CS /P0 SCN 0 0 1 RG 10 w 0 25 m 60 25 l S", _BLUE),
        # So does a numeric `SCN`, even left in the Pattern space by a stream
        # that should have changed it first.
        (b"/Pattern CS /P0 SCN 0 0 1 SCN 10 w 0 25 m 60 25 l S", _BLUE),
        # And so does `CS` on its own: 8.6.8 sets the colour to the new
        # space's initial value, which is black -- not the standing pattern.
        (b"/Pattern CS /P0 SCN /DeviceRGB CS 10 w 0 25 m 60 25 l S", _BLACK),
    ],
)
def test_setting_a_plain_stroke_colour_puts_the_pattern_down(content, expected):
    raster = _document(content, _tiling(_CELL)).pages[0].render(antialias=False)
    assert raster.get_pixel(30, 35) == expected


def test_changing_the_fill_colour_space_puts_the_pattern_down():
    content = b"/Pattern cs /P0 scn /DeviceRGB cs 0 0 60 60 re f"
    raster = _document(content, _tiling(_CELL)).pages[0].render(antialias=False)
    assert raster.get_pixel(30, 30) == _BLACK


def test_a_shading_pattern_strokes_too():
    content = b"/Pattern CS /P0 SCN 10 w 0 25 m 60 25 l S"
    raster = _document(content, _shading()).pages[0].render(antialias=False)
    assert raster.get_pixel(30, 33) == _RED
    assert raster.get_pixel(30, 5) == _WHITE


def test_an_uncoloured_stroke_pattern_takes_the_colour_from_scn():
    content = b"/Cs1 CS 0 0 1 /P0 SCN 10 w 0 25 m 60 25 l S"
    raster = (
        _document(content, _tiling(b"0 0 5 5 re f", paint_type=2))
        .pages[0]
        .render(antialias=False)
    )
    assert _BLUE in _band(raster)


# --- filling text with a pattern --------------------------------------------

_TEXT = b"/Pattern cs /P0 scn BT /F1 40 Tf 2 10 Td (H) Tj ET"


def _with_font(content: bytes, pattern) -> Document:
    document = _document(content, pattern)
    resources = document._engine_pdf._get_page_dict(0).mapping[PdfName("Resources")]
    resources.mapping[PdfName("Font")] = PdfDictionary(
        {
            PdfName("F1"): PdfDictionary(
                {
                    PdfName("Type"): PdfName("Font"),
                    PdfName("Subtype"): PdfName("Type1"),
                    PdfName("BaseFont"): PdfName("Helvetica-Bold"),
                }
            )
        }
    )
    return document


def _glyph_pixels(pattern) -> list[tuple[int, int, int]]:
    raster = _with_font(_TEXT, pattern).pages[0].render(antialias=False)
    return [raster.get_pixel(x, y) for y in range(0, 60) for x in range(0, 60)]


def test_text_filled_with_a_tiling_pattern_shows_the_pattern():
    painted = _glyph_pixels(_tiling(_CELL))
    assert _RED in painted
    assert _BLACK not in painted  # what it used to paint


def test_text_filled_with_a_shading_pattern_shows_the_shading():
    painted = _glyph_pixels(_shading())
    assert _RED in painted
    assert _BLACK not in painted


def test_a_pattern_does_not_paint_outside_the_glyphs():
    raster = _with_font(_TEXT, _tiling(_CELL)).pages[0].render(antialias=False)
    assert raster.get_pixel(58, 58) == _WHITE
    assert raster.get_pixel(1, 1) == _WHITE


def test_text_in_a_clipping_mode_still_clips_with_a_pattern_set():
    # Mode 7 paints nothing, pattern or no pattern, but still clips.
    content = (
        b"/Pattern cs /P0 scn BT /F1 40 Tf 7 Tr 2 10 Td (H) Tj ET"
        b" 0 0 1 rg 0 0 60 60 re f"
    )
    raster = _with_font(content, _tiling(_CELL)).pages[0].render(antialias=False)
    painted = [raster.get_pixel(x, y) for y in range(0, 60) for x in range(0, 60)]
    assert _BLUE in painted  # through the letter
    assert painted.count(_BLUE) < 60 * 60 // 3
    assert _RED not in painted  # mode 7 paints nothing itself


# --- the state ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("select", "attribute"),
    [
        (b"/Pattern cs /P0 scn", "fill_tiling"),
        (b"/Pattern CS /P0 SCN", "stroke_tiling"),
    ],
)
def test_the_pattern_is_recorded_on_the_side_that_selected_it(select, attribute):
    from aspose_pdf.engine.rasterizer import _PageRasterizer

    document = _document(select, _tiling(_CELL))
    rasterizer = _PageRasterizer(
        document._engine_pdf,
        0,
        dpi=72.0,
        scale=1.0,
        background=(255, 255, 255),
        antialias=False,
    )
    rasterizer._interpret(
        select, rasterizer.resources_cos, rasterizer.resources_plain, depth=0
    )
    other = "stroke_tiling" if attribute == "fill_tiling" else "fill_tiling"
    assert getattr(rasterizer.state, attribute) is not None
    assert getattr(rasterizer.state, other) is None


def test_a_pattern_cell_starts_with_no_pattern_of_its_own():
    # The cell here *strokes*, and names no colour of its own, and the pattern
    # painting it is a stroke pattern. Without clearing it the cell would try
    # to stroke with the very pattern that is drawing the cell.
    stroking_cell = b"3 w 0 0 m 10 10 l S"
    content = b"/Pattern CS /P0 SCN 10 w 0 25 m 60 25 l S"
    raster = _document(content, _tiling(stroking_cell)).pages[0].render(antialias=False)
    assert _BLACK in _band(raster)  # the cell's own stroke, in its own colour
