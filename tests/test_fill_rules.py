"""Which points a path encloses: the nonzero and even-odd rules.

ISO 32000-1 8.5.3.3 gives two answers and the operator picks one -- ``f`` and
``f*``, ``B`` and ``B*``, ``W`` and ``W*``. The rule belongs to the **path**,
not to any one subpath, because that is the whole point of it: a shape with a
hole is two subpaths, and whether the hole is open depends on counting the
crossings of *both*.

The renderer filled each subpath on its own, which is neither rule. So no path
ever had a hole -- not a donut, not a logo counter, not a letter drawn as
vectors, and not a region clipped out with ``W*`` -- and the star in ``f*`` was
dropped on the floor. Glyph outlines had their own correct nonzero filler,
which is why type looked right while artwork did not.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document

_BLUE = (0, 0, 255)
_RED = (255, 0, 0)
_WHITE = (255, 255, 255)

#: An outer square, then an inner one wound the *opposite* way. Nonzero cancels
#: to zero inside the inner square, so both rules leave a hole.
_OPPOSED = (
    b"40 40 m 160 40 l 160 160 l 40 160 l h 70 70 m 70 130 l 130 130 l 130 70 l h "
)
#: The same two squares wound the *same* way. Nonzero counts two and fills
#: solid; even-odd counts two and opens the hole. This is the pair that tells
#: the rules apart -- and `re` builds squares this way, so it is the common one.
_ALIGNED = (
    b"40 40 m 160 40 l 160 160 l 40 160 l h 70 70 m 130 70 l 130 130 l 70 130 l h "
)


def _document(content: bytes) -> Document:
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
            b"/Resources << >> /Contents 4 0 R >>"
        ),
        4: b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
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


def _probe(content: bytes) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """``(inside the hole, inside the ring)`` after rendering *content*."""
    raster = _document(content).pages[0].render(antialias=False)
    return raster.get_pixel(100, 100), raster.get_pixel(50, 100)


# --- filling ----------------------------------------------------------------


def test_opposed_winding_leaves_a_hole_under_the_nonzero_rule():
    hole, ring = _probe(b"0 0 1 rg " + _OPPOSED + b"f\n")
    assert hole == _WHITE
    assert ring == _BLUE


def test_aligned_winding_fills_solid_under_the_nonzero_rule():
    hole, ring = _probe(b"0 0 1 rg " + _ALIGNED + b"f\n")
    assert hole == _BLUE
    assert ring == _BLUE


@pytest.mark.parametrize("path", [_OPPOSED, _ALIGNED])
def test_even_odd_opens_the_hole_whichever_way_the_subpaths_are_wound(path):
    hole, ring = _probe(b"0 0 1 rg " + path + b"f*\n")
    assert hole == _WHITE
    assert ring == _BLUE


def test_two_rectangles_are_the_ordinary_way_to_write_this():
    # `re` always winds the same way, so a donut written with rectangles needs
    # the even-odd rule -- which is why `f*` is so common in real artwork.
    assert _probe(b"0 0 1 rg 40 40 120 120 re 70 70 60 60 re f*\n")[0] == _WHITE
    assert _probe(b"0 0 1 rg 40 40 120 120 re 70 70 60 60 re f\n")[0] == _BLUE


def test_fill_and_stroke_together_takes_the_rule_too():
    hole, ring = _probe(b"0 0 1 rg 1 0 0 RG 3 w 40 40 120 120 re 70 70 60 60 re B*\n")
    assert hole == _WHITE
    assert ring == _BLUE


def test_close_fill_and_stroke_takes_the_rule_too():
    assert _probe(b"0 0 1 rg 1 0 0 RG 3 w " + _ALIGNED + b"b*\n")[0] == _WHITE


def test_a_single_subpath_is_the_same_under_either_rule():
    solid = b"0 0 1 rg 40 40 120 120 re "
    assert _probe(solid + b"f\n") == _probe(solid + b"f*\n")


# --- clipping ---------------------------------------------------------------


def test_a_clip_path_has_the_same_two_rules():
    inside_hole, inside_ring = _probe(
        b"q " + _ALIGNED + b"W* n 1 0 0 rg 0 0 200 200 re f Q\n"
    )
    assert inside_hole == _WHITE  # clipped away
    assert inside_ring == _RED

    inside_hole, inside_ring = _probe(
        b"q " + _ALIGNED + b"W n 1 0 0 rg 0 0 200 200 re f Q\n"
    )
    assert inside_hole == _RED  # nonzero counts two, so nothing is clipped away
    assert inside_ring == _RED


def test_a_nonzero_clip_still_honours_opposed_winding():
    hole, ring = _probe(b"q " + _OPPOSED + b"W n 1 0 0 rg 0 0 200 200 re f Q\n")
    assert hole == _WHITE
    assert ring == _RED


def test_the_clip_rule_survives_to_the_painting_operator():
    # `W*` only marks the clip; the path operator that follows is what applies
    # it, so the rule has to travel that far -- here through a stroke rather
    # than the usual `n`.
    hole, ring = _probe(
        b"q 0 g 1 w " + _ALIGNED + b"W* S 1 0 0 rg 0 0 200 200 re f Q\n"
    )
    assert hole == _WHITE
    assert ring == _RED


# --- shadings and patterns fill a region too --------------------------------


_AXIAL = (
    b"<< /ShadingType 2 /ColorSpace /DeviceRGB /Coords [40 0 160 0] "
    b"/Function << /FunctionType 2 /Domain [0 1] /C0 [0 0 1] /C1 [0 0 1] /N 1 >> "
    b"/Extend [true true] >>"
)


def _shaded(operator: bytes) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    content = b"/Pattern cs /P1 scn " + _ALIGNED + operator + b"\n"
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources "
            b"<< /Pattern << /P1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        4: b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        5: b"<< /PatternType 2 /Shading " + _AXIAL + b" >>",
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
    raster = Document(io.BytesIO(bytes(raw))).pages[0].render(antialias=False)
    return raster.get_pixel(100, 100), raster.get_pixel(50, 100)


def test_a_shading_pattern_fills_the_region_the_rule_describes():
    hole, ring = _shaded(b"f*")
    assert hole == _WHITE
    assert ring == _BLUE
    assert _shaded(b"f")[0] == _BLUE


# --- and the SVG export says the same thing ---------------------------------


def _svg(content: bytes) -> str:
    return _document(content).pages[0].to_svg()


def test_the_svg_export_names_the_fill_rule():
    assert 'fill-rule="evenodd"' in _svg(b"0 0 1 rg " + _ALIGNED + b"f*\n")
    assert "evenodd" not in _svg(b"0 0 1 rg " + _ALIGNED + b"f\n")


def test_the_svg_export_names_the_clip_rule():
    marked_up = _svg(b"q " + _ALIGNED + b"W* n 1 0 0 rg 0 0 200 200 re f Q\n")
    assert 'clip-rule="evenodd"' in marked_up
    plain = _svg(b"q " + _ALIGNED + b"W n 1 0 0 rg 0 0 200 200 re f Q\n")
    assert "clip-rule" not in plain


def test_the_svg_export_writes_the_whole_path_as_one_element():
    # Two subpaths in one `d`, or the rule has nothing to apply to.
    body = _svg(b"0 0 1 rg " + _ALIGNED + b"f*\n")
    (fill,) = [line for line in body.splitlines() if 'fill-rule="evenodd"' in line]
    assert fill.count("M") == 2


# --- the rule itself --------------------------------------------------------


def test_scan_spans_counts_crossings_by_direction():
    from aspose_pdf.engine.rasterizer import _scan_spans

    outer = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    inner_same = [(3.0, 3.0), (7.0, 3.0), (7.0, 7.0), (3.0, 7.0)]
    inner_opposed = [(3.0, 3.0), (3.0, 7.0), (7.0, 7.0), (7.0, 3.0)]

    def covered(contours, even_odd):
        """The row-5 spans, adjacent ones merged into what actually gets painted."""
        merged: list[list[float]] = []
        for y, a, b in _scan_spans(contours, even_odd=even_odd, height=20):
            if y != 5:
                continue
            if merged and abs(merged[-1][1] - a) < 1e-9:
                merged[-1][1] = b
            else:
                merged.append([a, b])
        return [(round(a, 3), round(b, 3)) for a, b in merged]

    # Wound the same way, nonzero counts two and never reaches zero: solid.
    assert covered([outer, inner_same], False) == [(0.0, 10.0)]
    # Even-odd counts them unsigned, so the middle is outside: a hole.
    assert covered([outer, inner_same], True) == [(0.0, 3.0), (7.0, 10.0)]
    # Wound against each other, the windings cancel and nonzero agrees.
    assert covered([outer, inner_opposed], False) == [(0.0, 3.0), (7.0, 10.0)]
    # One subpath has no counting to do, so the rules coincide.
    assert covered([outer], True) == covered([outer], False) == [(0.0, 10.0)]


def test_a_glyph_is_filled_nonzero_however_its_contours_are_wound():
    # TrueType's convention winds a counter against its outer contour, so the
    # two rules usually agree on type -- but a glyph may also be built from
    # *overlapping* contours wound the same way, and there even-odd would punch
    # a hole through the overlap. Glyphs are nonzero, always.
    from aspose_pdf.engine.rasterizer import _PageRasterizer

    document = _document(b"")
    page = _PageRasterizer(
        document._engine_pdf,
        0,
        dpi=72.0,
        scale=1.0,
        background=(255, 255, 255),
        antialias=False,
    )
    left = [(20.0, 20.0), (60.0, 20.0), (60.0, 60.0), (20.0, 60.0)]
    right = [(40.0, 20.0), (80.0, 20.0), (80.0, 60.0), (40.0, 60.0)]
    page._fill_contours_nonzero([left, right], _BLUE, 1.0)

    def pixel(x: int, y: int) -> tuple[int, int, int]:
        at = (y * page.canvas.width + x) * 3
        return tuple(page.canvas.pixels[at : at + 3])

    assert pixel(50, 40) == _BLUE  # the overlap stays filled
    assert pixel(30, 40) == _BLUE
    assert pixel(70, 40) == _BLUE


def test_scan_spans_ignores_degenerate_contours():
    from aspose_pdf.engine.rasterizer import _scan_spans

    assert list(_scan_spans([], even_odd=False, height=10)) == []
    assert (
        list(_scan_spans([[(0.0, 0.0), (5.0, 5.0)]], even_odd=False, height=10)) == []
    )
