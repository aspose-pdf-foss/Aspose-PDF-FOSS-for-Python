"""Drawing paths on a page, and the graphics state they are drawn under.

Authoring could put exactly two things on a page: an axis-aligned rectangle and a
straight line. No curve, no circle, no polygon, no dash, no line cap, no join, no
transparency, and no way to turn, scale or clip anything -- while the *renderer*
had always drawn all of it, since that is what reading a PDF means. The hole was
on the writing side only, and `IPath` was in the "Known Unsupported" table for
exactly that reason: there was nothing for a path to be drawn with.

``GraphicsPath`` collects the subpaths of ISO 32000-1 8.5.2, ``Page.draw_path``
paints one, and ``Page.graphics`` holds a state -- a matrix, a clip, an alpha, a
blend mode -- over everything appended inside it, text and images included.

Verified against **poppler 26.09**: at 288 dpi, where a one-point line is four
pixels wide, every case here renders pixel-identical (max difference 0-4 levels
of 255). At 72 dpi a hairline's antialiasing differs between any two
rasterizers -- the *undashed* reference case differs there too -- so the
comparison is made where the geometry rather than the sampling is being read.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document, GraphicsPath
from aspose_pdf.exceptions import PdfValidationException


def _page(width: float = 300, height: float = 300):
    document = Document()
    return document, document.pages.add(size=(width, height))


def _content(page) -> str:
    return page.content.decode("latin-1")


def _saved(document: Document) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Building a path
# ---------------------------------------------------------------------------


def test_a_path_starts_empty():
    path = GraphicsPath()
    assert path.is_empty
    assert len(path) == 0
    assert not path
    assert path.current_point is None


def test_the_builders_chain_and_track_the_current_point():
    path = GraphicsPath().move_to(10, 20).line_to(30, 40)
    assert path.current_point == (30.0, 40.0)
    path.curve_to(50, 60, 70, 80, 90, 100)
    assert path.current_point == (90.0, 100.0)
    assert len(path) == 3
    assert bool(path)


def test_a_path_that_continues_from_nowhere_is_refused():
    # ``l`` and ``c`` need a current point: a content stream whose first path
    # operator has nothing to draw from is not a path at all.
    for build in (
        lambda p: p.line_to(1, 1),
        lambda p: p.curve_to(1, 1, 2, 2, 3, 3),
        lambda p: p.quadratic_to(1, 1, 2, 2),
        lambda p: p.close(),
    ):
        with pytest.raises(PdfValidationException, match="start"):
            build(GraphicsPath())


def test_a_shape_starts_its_own_subpath():
    for build in (
        lambda p: p.rect(0, 0, 10, 10),
        lambda p: p.ellipse(0, 0, 10, 10),
        lambda p: p.circle(5, 5, 5),
        lambda p: p.polygon([(0, 0), (1, 1), (2, 0)]),
        lambda p: p.polyline([(0, 0), (1, 1)]),
    ):
        path = GraphicsPath()
        build(path)
        assert not path.is_empty


def test_close_returns_to_where_the_subpath_started():
    path = GraphicsPath().move_to(10, 10).line_to(50, 10).line_to(50, 50).close()
    assert path.current_point == (10.0, 10.0)


def test_a_rectangle_leaves_the_current_point_at_its_origin():
    # 8.5.2.1: ``re`` is ``m`` plus three ``l`` plus ``h``, so the point ends up
    # back where the rectangle began.
    assert GraphicsPath().rect(10, 20, 30, 40).current_point == (10.0, 20.0)


def test_a_coordinate_has_to_be_a_finite_number():
    for bad in ("x", None, float("inf"), float("nan"), True):
        with pytest.raises(PdfValidationException):
            GraphicsPath().move_to(bad, 0)


def test_a_circle_and_an_ellipse_need_a_size():
    with pytest.raises(PdfValidationException, match="radius above zero"):
        GraphicsPath().circle(0, 0, 0)
    with pytest.raises(PdfValidationException, match="above zero"):
        GraphicsPath().ellipse(0, 0, 10, 0)


def test_a_polygon_needs_at_least_two_points():
    with pytest.raises(PdfValidationException, match="at least two"):
        GraphicsPath().polygon([(0, 0)])
    with pytest.raises(PdfValidationException, match="\\(x, y\\) pairs"):
        GraphicsPath().polygon([(0, 0), (1, 2, 3)])


def test_an_ellipse_is_four_curves_closed():
    # PDF has no arc operator; every producer draws one this way.
    path = GraphicsPath().ellipse(0, 0, 100, 50)
    assert len(path) == 6  # move, four curves, close


def test_a_circle_is_the_ellipse_in_its_bounding_box():
    assert len(GraphicsPath().circle(50, 50, 25)) == len(
        GraphicsPath().ellipse(25, 25, 50, 50)
    )


def test_an_empty_path_cannot_be_drawn():
    _, page = _page()
    with pytest.raises(PdfValidationException, match="no segments"):
        page.draw_path(GraphicsPath(), fill_color=(0, 0, 0))


def test_draw_path_takes_a_path():
    _, page = _page()
    with pytest.raises(PdfValidationException, match="GraphicsPath"):
        page.draw_path("not a path", fill_color=(0, 0, 0))


# ---------------------------------------------------------------------------
# What reaches the content stream
# ---------------------------------------------------------------------------


def test_the_segment_operators_are_the_ones_the_standard_names():
    _, page = _page()
    path = (
        GraphicsPath()
        .move_to(10, 10)
        .line_to(50, 10)
        .curve_to(60, 20, 70, 30, 80, 40)
        .close()
    )
    page.draw_path(path, stroke_color=(0, 0, 0))
    content = _content(page)
    assert "10 10 m" in content
    assert "50 10 l" in content
    assert "60 20 70 30 80 40 c" in content
    assert " h " in content


def test_a_quadratic_curve_is_written_as_the_cubic_that_draws_it():
    # PDF has no quadratic operator. The control points two thirds of the way
    # along make the cubic that draws the same curve, not an approximation of it.
    _, page = _page()
    page.draw_path(
        GraphicsPath().move_to(0, 0).quadratic_to(30, 60, 60, 0),
        stroke_color=(0, 0, 0),
    )
    assert "20 40 40 40 60 0 c" in _content(page)


@pytest.mark.parametrize(
    ("fill", "stroke", "even_odd", "operator"),
    [
        ((0, 0, 0), None, False, " f"),
        ((0, 0, 0), None, True, " f*"),
        (None, (0, 0, 0), False, " S"),
        ((0, 0, 0), (0, 0, 0), False, " B"),
        ((0, 0, 0), (0, 0, 0), True, " B*"),
    ],
)
def test_the_painting_operator_says_what_to_do_with_the_path(
    fill, stroke, even_odd, operator
):
    _, page = _page()
    page.draw_path(
        GraphicsPath().rect(10, 10, 50, 50),
        fill_color=fill,
        stroke_color=stroke,
        even_odd=even_odd,
    )
    assert operator + " Q" in _content(page)


def test_a_path_with_no_colour_at_all_is_refused():
    _, page = _page()
    with pytest.raises(PdfValidationException, match="stroke colour, a fill colour"):
        page.draw_path(
            GraphicsPath().rect(0, 0, 10, 10), fill_color=None, stroke_color=None
        )


def test_the_line_state_operators_are_written():
    _, page = _page()
    page.draw_path(
        GraphicsPath().move_to(0, 0).line_to(10, 10),
        stroke_color=(0, 0, 0),
        line_width=3,
        line_cap="round",
        line_join="bevel",
        miter_limit=4,
        dash=([6, 3], 1),
    )
    content = _content(page)
    assert "3 w" in content
    assert "1 J" in content
    assert "2 j" in content
    assert "4 M" in content
    assert "[6 3] 1 d" in content


@pytest.mark.parametrize(
    ("cap", "number"), [("butt", 0), ("round", 1), ("square", 2), (2, 2)]
)
def test_every_line_cap(cap, number):
    _, page = _page()
    page.draw_path(
        GraphicsPath().move_to(0, 0).line_to(10, 0),
        stroke_color=(0, 0, 0),
        line_cap=cap,
    )
    assert f"{number} J" in _content(page)


@pytest.mark.parametrize(
    ("join", "number"), [("miter", 0), ("round", 1), ("bevel", 2), (1, 1)]
)
def test_every_line_join(join, number):
    _, page = _page()
    page.draw_path(
        GraphicsPath().polygon([(0, 0), (10, 0), (5, 10)]),
        stroke_color=(0, 0, 0),
        line_join=join,
    )
    assert f"{number} j" in _content(page)


def test_a_cap_or_join_that_is_not_one_is_refused():
    _, page = _page()
    path = GraphicsPath().move_to(0, 0).line_to(1, 1)
    with pytest.raises(PdfValidationException, match="line cap"):
        page.draw_path(path, stroke_color=(0, 0, 0), line_cap="flat")
    with pytest.raises(PdfValidationException, match="line join"):
        page.draw_path(path, stroke_color=(0, 0, 0), line_join="sharp")
    with pytest.raises(PdfValidationException, match="miter limit"):
        page.draw_path(path, stroke_color=(0, 0, 0), miter_limit=0.5)


@pytest.mark.parametrize(
    ("dash", "written"),
    [
        (None, None),
        ([], "[] 0 d"),
        (3, "[3] 0 d"),
        ([4, 2], "[4 2] 0 d"),
        (([4, 2], 1.5), "[4 2] 1.5 d"),
    ],
)
def test_a_dash_pattern_is_written_the_way_it_was_given(dash, written):
    _, page = _page()
    page.draw_path(
        GraphicsPath().move_to(0, 0).line_to(100, 0),
        stroke_color=(0, 0, 0),
        dash=dash,
    )
    content = _content(page)
    if written is None:
        assert " d " not in content
    else:
        assert written in content


def test_a_dash_pattern_that_would_draw_nothing_is_refused():
    _, page = _page()
    path = GraphicsPath().move_to(0, 0).line_to(10, 0)
    with pytest.raises(PdfValidationException, match="draw nothing"):
        page.draw_path(path, stroke_color=(0, 0, 0), dash=[0, 0])
    with pytest.raises(PdfValidationException, match="not negative"):
        page.draw_path(path, stroke_color=(0, 0, 0), dash=[-2, 2])
    with pytest.raises(PdfValidationException, match="not negative"):
        page.draw_path(path, stroke_color=(0, 0, 0), dash=([2, 2], -1))


def test_a_transform_applies_to_the_drawing_that_follows_it():
    _, page = _page()
    page.draw_path(
        GraphicsPath().rect(0, 0, 10, 10),
        fill_color=(0, 0, 0),
        transform=(2, 0, 0, 2, 5, 5),
    )
    content = _content(page)
    assert content.index("2 0 0 2 5 5 cm") < content.index("0 0 10 10 re")


def test_a_transform_may_be_an_imatrix():
    from aspose_pdf.presentation import IMatrix

    _, page = _page()
    page.draw_path(
        GraphicsPath().rect(0, 0, 10, 10),
        fill_color=(0, 0, 0),
        transform=IMatrix(1, 0, 0, 1, 3, 4),
    )
    assert "1 0 0 1 3 4 cm" in _content(page)


def test_a_transform_is_six_numbers():
    _, page = _page()
    path = GraphicsPath().rect(0, 0, 10, 10)
    with pytest.raises(PdfValidationException, match="six numbers"):
        page.draw_path(path, fill_color=(0, 0, 0), transform=(1, 0, 0, 1))
    with pytest.raises(PdfValidationException, match="must be a number"):
        page.draw_path(path, fill_color=(0, 0, 0), transform=(1, 0, 0, 1, 0, "x"))


# ---------------------------------------------------------------------------
# Transparency and blending
# ---------------------------------------------------------------------------


def test_opacity_is_written_as_an_ext_gstate():
    document, page = _page()
    page.draw_path(GraphicsPath().rect(0, 0, 10, 10), fill_color=(0, 0, 0), opacity=0.5)
    content = _content(page)
    assert "/GS1 gs" in content
    data = _saved(document)
    assert b"/ca 0.5" in data
    assert b"/CA 0.5" in data
    assert b"/ExtGState" in data


def test_each_half_of_the_paint_can_have_its_own_opacity():
    document, page = _page()
    page.draw_path(
        GraphicsPath().rect(0, 0, 10, 10),
        fill_color=(0, 0, 0),
        stroke_color=(0, 0, 0),
        fill_opacity=0.25,
        stroke_opacity=0.75,
    )
    data = _saved(document)
    assert b"/ca 0.25" in data
    assert b"/CA 0.75" in data


def test_the_same_state_is_reused_rather_than_written_again():
    # A page drawing a hundred half-transparent paths used to be a hundred
    # dictionaries saying the same thing.
    _, page = _page()
    for index in range(5):
        page.draw_path(
            GraphicsPath().rect(index * 10, 0, 5, 5),
            fill_color=(0, 0, 0),
            opacity=0.5,
        )
    content = _content(page)
    assert content.count("/GS1 gs") == 5
    assert "/GS2" not in content


def test_a_different_state_gets_its_own_resource():
    _, page = _page()
    page.draw_path(GraphicsPath().rect(0, 0, 10, 10), fill_color=(0, 0, 0), opacity=0.5)
    page.draw_path(
        GraphicsPath().rect(20, 0, 10, 10), fill_color=(0, 0, 0), opacity=0.25
    )
    content = _content(page)
    assert "/GS1 gs" in content
    assert "/GS2 gs" in content


def test_a_blend_mode_is_written_and_checked():
    document, page = _page()
    page.draw_path(
        GraphicsPath().rect(0, 0, 10, 10), fill_color=(0, 0, 0), blend_mode="Multiply"
    )
    assert b"/BM /Multiply" in _saved(document)
    with pytest.raises(PdfValidationException, match="blend mode"):
        page.draw_path(
            GraphicsPath().rect(0, 0, 10, 10),
            fill_color=(0, 0, 0),
            blend_mode="Sparkle",
        )


def test_opacity_has_to_be_between_zero_and_one():
    _, page = _page()
    with pytest.raises(PdfValidationException, match="between 0 and 1"):
        page.draw_path(
            GraphicsPath().rect(0, 0, 10, 10), fill_color=(0, 0, 0), opacity=1.5
        )


# ---------------------------------------------------------------------------
# The graphics section
# ---------------------------------------------------------------------------


def test_a_section_wraps_what_is_drawn_inside_it():
    _, page = _page()
    with page.graphics(transform=(0, 1, -1, 0, 200, 0)):
        page.add_text("sideways", 0, 0, font_size=12)
    content = _content(page)
    # One balanced q ... Q holding the text, rather than a q left open for the
    # next fragment to trip over.
    assert content.count("q") >= 2
    assert content.strip().endswith("Q")
    opening = content.index("0 1 -1 0 200 0 cm")
    assert content.index("(sideways)") > opening
    assert content.count("q") == content.count("Q")


def test_a_section_is_balanced_whatever_is_drawn_in_it():
    _, page = _page()
    with page.graphics(opacity=0.5):
        page.draw_rectangle(0, 0, 10, 10, fill_color=(0, 0, 0))
        page.add_text("x", 20, 20)
        page.draw_path(GraphicsPath().circle(50, 50, 10), fill_color=(1, 0, 0))
    content = _content(page)
    assert content.count("q") == content.count("Q")


def test_sections_nest():
    _, page = _page()
    with page.graphics(opacity=0.5):
        with page.graphics(transform=(2, 0, 0, 2, 0, 0)):
            page.draw_path(GraphicsPath().rect(0, 0, 5, 5), fill_color=(0, 0, 0))
    content = _content(page)
    assert content.count("q") == content.count("Q")
    assert content.index("/GS1 gs") < content.index("2 0 0 2 0 0 cm")


def test_a_clip_confines_the_section_and_paints_nothing_itself():
    _, page = _page()
    with page.graphics(clip=GraphicsPath().circle(150, 150, 50)):
        page.draw_rectangle(100, 100, 100, 100, fill_color=(0, 0.6, 0.6))
    content = _content(page)
    assert " W n" in content  # 8.5.4: take the path as the clip, paint nothing
    assert content.count("q") == content.count("Q")


def test_a_clip_can_use_the_even_odd_rule():
    _, page = _page()
    with page.graphics(clip=GraphicsPath().circle(50, 50, 20), clip_even_odd=True):
        page.draw_rectangle(0, 0, 100, 100, fill_color=(0, 0, 0))
    assert " W* n" in _content(page)


def test_the_state_is_undone_when_the_section_ends():
    _, page = _page()
    with page.graphics(opacity=0.25):
        page.draw_rectangle(0, 0, 10, 10, fill_color=(0, 0, 0))
    page.draw_rectangle(50, 50, 10, 10, fill_color=(0, 0, 0))
    content = _content(page)
    # The second rectangle is drawn after the section's Q, so the alpha the
    # section set cannot reach it.
    assert content.index("Q") < content.index("50 50 10 10 re")


def test_the_sections_content_reaches_the_page_when_it_closes():
    _, page = _page()
    section = page.graphics(opacity=0.5)
    with section:
        page.draw_rectangle(0, 0, 10, 10, fill_color=(0, 0, 0))
        # Still buffered: the whole section is appended as one balanced fragment.
        assert "0 0 10 10 re" not in _content(page)
    assert "0 0 10 10 re" in _content(page)


def test_closing_a_section_that_is_not_open_is_refused():
    document, _ = _page()
    with pytest.raises(PdfValidationException, match="No graphics section"):
        document._engine_pdf.end_page_graphics(0)


# ---------------------------------------------------------------------------
# The two shortcuts take the same options
# ---------------------------------------------------------------------------


def test_a_rectangle_is_written_exactly_as_it_always_was():
    # draw_rectangle goes through draw_path now; the bytes a plain one writes are
    # the bytes it wrote before, so nothing that reads them has to change.
    _, page = _page()
    page.draw_rectangle(10, 20, 100, 50, stroke_color=(0, 0, 0), fill_color=(1, 0, 0))
    assert _content(page).strip() == "q 0 0 0 RG 1 w 1 0 0 rg 10 20 100 50 re B Q"


def test_a_line_is_written_exactly_as_it_always_was():
    _, page = _page()
    page.draw_line(1, 2, 3, 4)
    assert _content(page).strip() == "q 0 0 0 RG 1 w 1 2 m 3 4 l S Q"


def test_a_rectangle_can_be_dashed_and_turned_now():
    _, page = _page()
    page.draw_rectangle(
        10,
        10,
        50,
        20,
        stroke_color=(0, 0, 0),
        dash=[2, 2],
        opacity=0.5,
        transform=(1, 0, 0, 1, 5, 5),
    )
    content = _content(page)
    assert "[2 2] 0 d" in content
    assert "1 0 0 1 5 5 cm" in content
    assert "/GS1 gs" in content


def test_a_line_can_be_dashed_and_capped_now():
    _, page = _page()
    page.draw_line(0, 0, 100, 0, dash=([5, 5], 2), line_cap="round")
    content = _content(page)
    assert "[5 5] 2 d" in content
    assert "1 J" in content


# ---------------------------------------------------------------------------
# It renders, and it survives a round trip
# ---------------------------------------------------------------------------


def test_a_drawn_path_renders():
    document, page = _page(100, 100)
    page.draw_path(
        GraphicsPath().circle(50, 50, 40), fill_color=(1, 0, 0), stroke_color=None
    )
    raster = Document(io.BytesIO(_saved(document))).pages[0].render(dpi=72)
    assert raster.get_pixel(50, 50) == (255, 0, 0)  # the middle of the circle
    assert raster.get_pixel(2, 2) == (255, 255, 255)  # outside it


def test_the_even_odd_rule_leaves_the_hole_a_hole():
    document, page = _page(100, 100)
    # Two concentric circles in one path: even-odd makes the inner one a hole,
    # nonzero winding fills straight over it (8.5.3.3).
    rings = GraphicsPath().circle(50, 50, 40).circle(50, 50, 20)
    page.draw_path(rings, fill_color=(1, 0, 0), stroke_color=None, even_odd=True)
    raster = Document(io.BytesIO(_saved(document))).pages[0].render(dpi=72)
    assert raster.get_pixel(50, 50) == (255, 255, 255)  # the hole
    assert raster.get_pixel(50, 20) == (255, 0, 0)  # the ring

    document, page = _page(100, 100)
    page.draw_path(
        GraphicsPath().circle(50, 50, 40).circle(50, 50, 20),
        fill_color=(1, 0, 0),
        stroke_color=None,
        even_odd=False,
    )
    raster = Document(io.BytesIO(_saved(document))).pages[0].render(dpi=72)
    assert raster.get_pixel(50, 50) == (255, 0, 0)  # filled through


def test_opacity_renders_as_transparency():
    document, page = _page(100, 100)
    page.draw_path(
        GraphicsPath().rect(0, 0, 100, 100),
        fill_color=(0, 0, 0),
        stroke_color=None,
        opacity=0.5,
    )
    raster = Document(io.BytesIO(_saved(document))).pages[0].render(dpi=72)
    red, green, blue = raster.get_pixel(50, 50)
    assert 120 <= red <= 135 and red == green == blue  # half way to white


def test_a_clip_keeps_the_drawing_inside_it():
    document, page = _page(100, 100)
    with page.graphics(clip=GraphicsPath().rect(0, 0, 50, 100)):
        page.draw_rectangle(0, 0, 100, 100, fill_color=(1, 0, 0), stroke_color=None)
    raster = Document(io.BytesIO(_saved(document))).pages[0].render(dpi=72)
    assert raster.get_pixel(25, 50) == (255, 0, 0)  # inside the clip
    assert raster.get_pixel(75, 50) == (255, 255, 255)  # outside it


def test_a_transform_moves_what_is_drawn_under_it():
    document, page = _page(100, 100)
    with page.graphics(transform=(1, 0, 0, 1, 50, 0)):
        page.draw_rectangle(0, 0, 40, 40, fill_color=(1, 0, 0), stroke_color=None)
    raster = Document(io.BytesIO(_saved(document))).pages[0].render(dpi=72)
    assert raster.get_pixel(70, 80) == (255, 0, 0)  # where the matrix put it
    assert raster.get_pixel(20, 80) == (255, 255, 255)  # where it was authored


def test_a_path_survives_a_round_trip_as_the_same_content():
    document, page = _page()
    page.draw_path(
        GraphicsPath().move_to(10, 10).curve_to(20, 30, 40, 30, 50, 10),
        stroke_color="#336699",
        line_width=2,
        dash=[4, 2],
    )
    before = page.content
    reloaded = Document(io.BytesIO(_saved(document)))
    assert reloaded.pages[0].content == before


def test_a_drawing_can_be_tagged_like_any_other_authored_content():
    document, page = _page()
    page.draw_path(
        GraphicsPath().circle(50, 50, 20),
        fill_color=(0, 0, 0),
        tag="Figure",
        alt="a dot",
    )
    data = _saved(document)
    assert b"/Figure" in data
    assert b"a dot" in data
    assert b"/StructTreeRoot" in data
