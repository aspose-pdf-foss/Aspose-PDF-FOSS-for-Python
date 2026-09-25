"""Pixel regressions for PDF stroke state and geometry."""

from __future__ import annotations

import pytest

from aspose_pdf import Document, PdfLoadLimits, PdfResourceLimitException
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName, PdfNumber, PdfStream
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.exceptions import PdfValidationException

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)


def _document(content: bytes, *, extgstates=None, limits=None) -> Document:
    def cos(value):
        if isinstance(value, dict):
            return PdfDictionary({PdfName(k): cos(v) for k, v in value.items()})
        if isinstance(value, list):
            return PdfArray([cos(v) for v in value])
        return PdfNumber(value)

    pdf = SimplePdf(pages=[(0, 0, 100, 100)], page_contents=[content])
    if extgstates:
        page = pdf._get_page_dict(0)
        page.mapping[PdfName("Resources")] = cos({"ExtGState": extgstates})
    return Document(pdf.to_bytes(), limits=limits)


def _render(content: bytes, **kwargs):
    with _document(content, **kwargs) as document:
        return document.pages[0].render(antialias=False)


def _at(raster, x, y):
    return raster.get_pixel(x, 99 - y)


@pytest.mark.parametrize("cap", [0, 1, 2])
def test_open_stroke_caps(cap):
    raster = _render(b"10 w %d J 20 50 m 80 50 l S" % cap)
    assert _at(raster, 30, 50) == BLACK
    assert _at(raster, 18, 50) == (WHITE if cap == 0 else BLACK)
    assert _at(raster, 15, 54) == (BLACK if cap == 2 else WHITE)
    assert _at(raster, 84, 54) == (BLACK if cap == 2 else WHITE)
    assert _at(raster, 14, 50) == WHITE


@pytest.mark.parametrize(
    ("pattern", "phase", "runs"),
    [
        (b"[10 10]", 0, [(10, 20), (30, 40), (50, 60), (70, 80)]),
        (b"[10]", 5, [(10, 15), (25, 35), (45, 55), (65, 75), (85, 90)]),
        (b"[10 10]", 45, [(10, 15), (25, 35), (45, 55), (65, 75), (85, 90)]),
        (b"[]", 0, [(10, 90)]),
    ],
)
def test_dash_phase_and_odd_length_arrays(pattern, phase, runs):
    raster = _render(b"4 w " + pattern + b" %d d 10 50 m 90 50 l S" % phase)
    ink = {x for x in range(100) if _at(raster, x, 50) == BLACK}
    assert ink == {x for start, end in runs for x in range(start, end)}


def test_dash_continues_at_vertices_and_restarts_for_each_subpath():
    raster = _render(
        b"4 w [15 10] 0 d 10 20 m 30 20 l 30 80 l 60 20 m 80 20 l 80 80 l S"
    )
    assert _at(raster, 30, 22) == WHITE
    assert _at(raster, 30, 28) == BLACK
    assert _at(raster, 80, 22) == WHITE
    assert _at(raster, 80, 28) == BLACK


@pytest.mark.parametrize("cap", [0, 1, 2])
def test_zero_length_dashes_use_the_path_direction(cap):
    raster = _render(b"10 w %d J [0 20] 0 d 20 50 m 80 50 l S" % cap)
    assert _at(raster, 20, 50) == (WHITE if cap == 0 else BLACK)
    assert _at(raster, 15, 54) == (BLACK if cap == 2 else WHITE)
    assert _at(raster, 30, 50) == WHITE
    assert _at(raster, 80, 50) == WHITE


@pytest.mark.parametrize("cap", [0, 1, 2])
@pytest.mark.parametrize("path", [b"40 50 m 40 50 l", b"40 50 m h"])
def test_degenerate_subpaths_only_draw_with_round_caps(cap, path):
    raster = _render(b"10 w %d J " % cap + path + b" S")
    assert _at(raster, 40, 50) == (BLACK if cap == 1 else WHITE)
    assert _at(_render(b"10 w %d J 40 50 m S" % cap), 40, 50) == WHITE


@pytest.mark.parametrize(
    ("join", "limit", "corner"),
    [(0, 10, BLACK), (0, 1, WHITE), (1, 10, WHITE), (2, 10, WHITE)],
)
def test_join_style_and_miter_limit(join, limit, corner):
    raster = _render(b"10 w %d j %d M 20 20 m 60 20 l 60 70 l S" % (join, limit))
    assert _at(raster, 64, 15) == corner
    assert _at(raster, 60, 19) == BLACK


def test_closed_subpaths_join_while_an_explicit_return_keeps_caps():
    closed = _render(b"10 w 0 J 0 j 20 20 m 60 20 l 60 60 l 20 60 l h S")
    opened = _render(b"10 w 0 J 0 j 20 20 m 60 20 l 60 60 l 20 60 l 20 20 l S")
    assert _at(closed, 15, 15) == BLACK
    assert _at(opened, 15, 15) == WHITE


def test_stroke_state_is_restored_by_q_Q():
    raster = _render(b"4 w q 1 J [10 10] 0 d 20 70 m 80 70 l S Q 20 30 m 80 30 l S")
    assert _at(raster, 18, 70) == BLACK
    assert _at(raster, 35, 70) == WHITE
    assert _at(raster, 18, 30) == WHITE
    assert _at(raster, 35, 30) == BLACK


def test_extgstate_sets_stroke_parameters():
    operators = _render(b"8 w 2 J 2 j 2 M [10 5] 3 d 20 20 m 70 20 l 70 70 l S")
    resource = _render(
        b"/GS gs 20 20 m 70 20 l 70 70 l S",
        extgstates={"GS": {"LW": 8, "LC": 2, "LJ": 2, "ML": 2, "D": [[10, 5], 3]}},
    )
    assert resource.pixels == operators.pixels


def test_ctm_scales_dash_lengths_and_pen_width():
    raster = _render(b"2 0 0 3 0 0 cm 4 w [10 5] 0 d 10 20 m 40 20 l S")
    assert _at(raster, 25, 65) == BLACK
    assert _at(raster, 25, 66) == WHITE
    assert _at(raster, 45, 60) == WHITE
    assert _at(raster, 55, 60) == BLACK


def test_a_stroke_composites_overlapping_segments_once():
    raster = _render(
        b"/GS gs 10 w 1 j 20 20 m 60 20 l 60 70 l S",
        extgstates={"GS": {"CA": 0.5}},
    )
    assert _at(raster, 59, 21) == _at(raster, 30, 20) == (128, 128, 128)


@pytest.mark.parametrize("dash", [b"[0 0] 0 d", b"[-1 2] 0 d", b"[/Bad 2] 0 d"])
def test_invalid_dash_patterns_raise(dash):
    with pytest.raises(PdfValidationException, match="dash"):
        _render(dash + b" 10 50 m 90 50 l S")


def test_tiny_dash_pattern_obeys_work_limit():
    with pytest.raises(PdfResourceLimitException, match=r"stroke|dash"):
        _render(
            b"[0.00001 0.00001] 0 d 10 50 m 90 50 l S",
            limits=PdfLoadLimits(max_container_items=1000),
        )


@pytest.mark.parametrize(
    "content",
    [
        b"[1 1] 0 d 10000000000000000 50 m 10000000000000100 50 l S",
        b"1 0 0 1 10000000000000000 0 cm 0 w [1 1] 0 d 20 50 m 80 50 l S",
    ],
)
def test_dash_points_that_round_to_one_coordinate_do_not_crash(content):
    raster = _render(content)
    assert raster.pixels == bytes([255]) * (100 * 100 * 3)


def test_negative_dash_phase_wraps_the_full_odd_pattern():
    negative = _render(b"4 w [10 3 5] -2 d 10 50 m 90 50 l S")
    positive = _render(b"4 w [10 3 5] 34 d 10 50 m 90 50 l S")
    assert negative.pixels == positive.pixels


def test_a_dash_ending_at_a_corner_gets_a_cap_before_the_turn():
    raster = _render(b"10 w 2 J 0 j [40 20] 0 d 20 20 m 60 20 l 60 80 l S")
    assert _at(raster, 64, 15) == BLACK
    assert _at(raster, 60, 28) == WHITE
    assert _at(raster, 60, 48) == BLACK


def test_a_dash_crossing_a_closed_path_seam_gets_a_join():
    raster = _render(b"10 w 0 J 0 j [50 10] 0 d 20 20 40 40 re S")
    assert _at(raster, 15, 15) == BLACK
    assert _at(raster, 60, 35) == WHITE


def test_a_dash_ending_exactly_at_the_closed_path_seam_keeps_its_cap():
    raster = _render(b"10 w 0 J 0 j [160 10] 0 d 20 20 40 40 re S")
    assert _at(raster, 15, 15) == WHITE
    assert _at(raster, 64, 15) == BLACK


def test_stroking_uses_the_ctm_at_paint_time():
    built_first = _render(b"20 50 m 80 50 l 2 0 0 2 0 0 cm 4 w [10 5] 0 d S")
    built_scaled = _render(b"2 0 0 2 0 0 cm 4 w [10 5] 0 d 10 25 m 40 25 l S")
    assert built_first.pixels == built_scaled.pixels


def test_dash_geometry_is_stable_under_scale_and_antialias():
    with _document(b"4 w [10 10] 0 d 10 50 m 90 50 l S") as document:
        for antialias in (False, 3):
            raster = document.pages[0].render(scale=2, antialias=antialias)
            assert raster.get_pixel(25, 99) == BLACK
            assert raster.get_pixel(45, 99) == WHITE


def test_zero_width_stroke_stays_a_device_hairline():
    raster = _render(b"2 0 0 3 0 0 cm 0 w [10 10] 0 d 10 20 m 45 20 l S")
    assert sum(raster.get_pixel(25, y) == BLACK for y in range(100)) == 1
    assert all(raster.get_pixel(45, y) == WHITE for y in range(100))


def test_a_form_inherits_stroke_state_and_restores_its_own_changes():
    with _document(b"4 w [10 10] 0 d /Fm Do 20 30 m 80 30 l S") as document:
        pdf = document._engine_pdf
        form = PdfStream(
            b"20 70 m 80 70 l S [] 0 d 10 w 2 J 20 50 m 80 50 l S",
            {
                PdfName("Subtype"): PdfName("Form"),
                PdfName("BBox"): PdfArray([PdfNumber(v) for v in (0, 0, 100, 100)]),
            },
        )
        pdf._cos_page_resources(0).mapping[PdfName("XObject")] = PdfDictionary(
            {PdfName("Fm"): pdf._cos_doc.register_object(form)}
        )
        raster = document.pages[0].render(antialias=False)
    assert _at(raster, 35, 70) == WHITE
    assert _at(raster, 18, 50) == BLACK
    assert _at(raster, 35, 50) == BLACK
    assert _at(raster, 18, 30) == WHITE
    assert _at(raster, 35, 30) == WHITE


def test_stroke_clipping_keeps_the_dash_phase():
    raster = _render(b"35 0 65 100 re W n 4 w [10 10] 0 d 20 50 m 80 50 l S")
    assert _at(raster, 25, 50) == WHITE
    assert _at(raster, 36, 50) == WHITE
    assert _at(raster, 42, 50) == BLACK


def test_curved_dashes_have_the_same_caps_as_straight_dashes():
    dashed = _render(b"6 w 1 J [10 12] 0 d 10 20 m 10 90 80 90 90 20 c S")
    solid = _render(b"6 w 1 J 10 20 m 10 90 80 90 90 20 c S")
    dashed_ink = {i for i in range(0, len(dashed.pixels), 3) if dashed.pixels[i] == 0}
    solid_ink = {i for i in range(0, len(solid.pixels), 3) if solid.pixels[i] == 0}
    assert len(dashed_ink) < len(solid_ink)
    assert _at(dashed, 10, 20) == BLACK
    assert _at(dashed, 12, 36) == WHITE


def test_round_trip_and_streaming_render_the_same_strokes(tmp_path):
    content = b"8 w 2 J 2 j [10 3 5] 7 d 20 20 m 60 20 l 60 70 l S"
    path = tmp_path / "stroke.pdf"
    with _document(content) as document:
        expected = document.pages[0].render(antialias=False)
        document.save(path)
    for opener in (Document, Document.open_streaming):
        with opener(path) as document:
            assert document.pages[0].render(antialias=False).pixels == expected.pixels
