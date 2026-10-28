"""Rounded rectangles are reusable vector paths for painting and clipping."""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document, GraphicsPath
from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.graphics import GraphicsAbsorber


def _bytes(document):
    with io.BytesIO() as output:
        document.save(output)
        return output.getvalue()


def _draw(document, path, **options):
    page = document.pages.add(size=(100, 80))
    page.draw_path(path, fill_color="#f00", stroke_color=None, **options)
    return page


def test_rounded_rect_starts_a_closed_subpath_and_builders_keep_chaining():
    path = GraphicsPath().move_to(1, 2).line_to(3, 4)
    previous = len(path)
    assert path.rounded_rect(10, 20, 80, 40, 10) is path
    assert path.current_point == (20, 20) and len(path) > previous
    assert path.line_to(5, 6).close().current_point == (20, 20)
    standalone = GraphicsPath().rounded_rect(10, 20, 80, 40, 10)
    assert not standalone.is_empty and standalone.current_point == (20, 20)


def test_saved_shape_has_four_curved_corners_and_exact_outer_bounds(tmp_path):
    with Document() as document:
        page = _draw(document, GraphicsPath().rounded_rect(10, 20, 80, 40, 10))
        operators = page.content.decode("ascii").split()
        assert operators.count("c") == 4 and operators.count("h") == 1
        assert "re" not in operators and "Do" not in operators
        assert "20 20 m" in page.content.decode("ascii")
        path = tmp_path / "rounded.pdf"
        document.save(path)
        for source in (_bytes(document), path):
            with Document(source) as reopened:
                assert reopened.validate()
                absorber = GraphicsAbsorber()
                absorber.visit(reopened.pages[0])
                assert len(absorber.elements) == 1
                element = absorber.elements[0]
                assert element.kind == "path" and element.operation == "fill"
                box = element.rectangle
                assert (box.x, box.y, box.width, box.height) == (10, 20, 80, 40)
                raster = reopened.pages[0].render(antialias=False)
                for point in ((10, 20), (89, 20), (10, 59), (89, 59), (11, 21)):
                    assert raster.get_pixel(*point) == (255, 255, 255)
                for point in ((50, 40), (50, 20), (10, 40), (50, 59), (13, 23)):
                    assert raster.get_pixel(*point) == (255, 0, 0)


@pytest.mark.parametrize("radius", [0, -0.0])
def test_zero_radius_is_identical_to_an_existing_rectangle(radius):
    with Document() as actual, Document() as expected:
        shape = GraphicsPath().rounded_rect(10, 20, 80, 40, radius)
        assert shape.current_point == (10, 20)
        rounded = _draw(actual, shape)
        rectangle = _draw(expected, GraphicsPath().rect(10, 20, 80, 40))
        assert rounded.content == rectangle.content
        assert (
            rounded.render(antialias=False).pixels
            == rectangle.render(antialias=False).pixels
        )


@pytest.mark.parametrize("radius", [21, 1000, 1e308])
def test_large_radius_is_clamped_to_half_the_smaller_side(radius):
    with Document() as actual, Document() as expected:
        path = GraphicsPath().rounded_rect(10, 20, 80, 40, radius)
        assert path.current_point == (30, 20)
        rounded = _draw(actual, path)
        capsule = _draw(expected, GraphicsPath().rounded_rect(10, 20, 80, 40, 20))
        assert rounded.content == capsule.content
        assert (
            rounded.render(antialias=False).pixels
            == capsule.render(antialias=False).pixels
        )


def test_square_with_maximum_radius_draws_the_same_circle():
    with Document() as actual, Document() as expected:
        rounded = _draw(actual, GraphicsPath().rounded_rect(20, 20, 40, 40, 20))
        circle = _draw(expected, GraphicsPath().circle(40, 40, 20))
        assert (
            rounded.render(antialias=False).pixels
            == circle.render(antialias=False).pixels
        )


def test_rounding_can_be_used_as_a_clip_without_painting_the_clip():
    shape = GraphicsPath().rounded_rect(10, 20, 80, 40, 10)
    with Document() as clipped:
        page = clipped.pages.add(size=(100, 80))
        with page.graphics(clip=shape):
            page.draw_rectangle(0, 0, 100, 80, fill_color="#f00", stroke_color=None)
        assert b"W n" in page.content
        assert shape.current_point == (20, 20)
        reference = page.render(antialias=False)
        with Document(_bytes(clipped)) as reopened:
            raster = reopened.pages[0].render(antialias=False)
            assert raster.pixels == reference.pixels
            for point in ((10, 20), (89, 20), (10, 59), (89, 59), (11, 21)):
                assert raster.get_pixel(*point) == (255, 255, 255)
            for point in ((50, 40), (50, 20), (10, 40), (50, 59), (13, 23)):
                assert raster.get_pixel(*point) == (255, 0, 0)


@pytest.mark.parametrize("even_odd", [False, True])
def test_winding_matches_positive_rectangles_and_even_odd_can_make_a_hole(even_odd):
    with Document() as document:
        path = GraphicsPath().rect(5, 5, 90, 70).rounded_rect(20, 20, 60, 40, 8)
        page = _draw(document, path, even_odd=even_odd)
        raster = page.render(antialias=False)
        assert raster.get_pixel(50, 40) == (
            (255, 255, 255) if even_odd else (255, 0, 0)
        )
        assert raster.get_pixel(7, 7) == (255, 0, 0)


def test_existing_stroke_transparency_and_transform_options_apply_after_round_trip():
    with Document() as document:
        page = document.pages.add(size=(110, 90))
        path = GraphicsPath().rounded_rect(10, 20, 80, 40, 10)
        page.draw_path(
            path,
            fill_color="#f00",
            stroke_color="#00f",
            line_width=4,
            fill_opacity=0.5,
            stroke_opacity=0.75,
            transform=(1, 0, 0, 1, 5, 5),
        )
        before = page.render(antialias=False)
        with Document(_bytes(document)) as reopened:
            raster = reopened.pages[0].render(antialias=False)
            assert raster.pixels == before.pixels
            center = raster.get_pixel(55, 45)
            border = raster.get_pixel(55, 25)
            assert center[0] > center[1] and center[1] == center[2]
            assert border[2] > border[0]
            assert raster.get_pixel(15, 25) == (255, 255, 255)


@pytest.mark.parametrize(
    "changes",
    [
        {"width": 0},
        {"width": -1},
        {"height": 0},
        {"height": -1},
        {"radius": -1},
        {"radius": True},
        {"radius": float("inf")},
        {"radius": float("nan")},
        {"x": float("nan")},
        {"y": float("inf")},
        {"x": True},
        {"width": "wide"},
        {"height": 10**1000},
        {"x": 1e308, "width": 1e308},
        {"y": 1e308, "height": 1e308},
        {"x": 1e308, "width": 1},
        {"y": 1e308, "height": 1},
    ],
)
def test_invalid_geometry_leaves_existing_segments_and_subpath_state_unchanged(changes):
    options = dict(x=10, y=20, width=80, height=40, radius=10)
    options.update(changes)
    path = GraphicsPath().move_to(1, 2).line_to(3, 4)
    with pytest.raises(PdfValidationException):
        path.rounded_rect(**options)
    assert len(path) == 2 and path.current_point == (3, 4)
    assert path.close().current_point == (1, 2)
    empty = GraphicsPath()
    with pytest.raises(PdfValidationException):
        empty.rounded_rect(**options)
    assert empty.is_empty and empty.current_point is None


def test_overflowing_numeric_inputs_use_the_existing_coordinate_validation_error():
    path = GraphicsPath()
    with pytest.raises(PdfValidationException, match="x"):
        path.move_to(10**1000, 0)
    assert path.is_empty and path.current_point is None


def test_negative_origins_are_valid_and_keep_the_requested_bounds():
    with Document() as document:
        page = _draw(document, GraphicsPath().rounded_rect(-10, -20, 80, 40, 5))
        absorber = GraphicsAbsorber()
        absorber.visit(page)
        box = absorber.elements[0].rectangle
        assert (box.x, box.y, box.width, box.height) == (-10, -20, 80, 40)
