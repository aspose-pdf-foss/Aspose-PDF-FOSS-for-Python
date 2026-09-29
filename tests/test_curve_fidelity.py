"""A Bézier stays a Bézier, and is flattened to the size it is drawn at.

The interpreter turned every curve into twelve line segments and threw the
control points away. Two things followed. The raster kept a fixed flat on each
segment -- a 250 pt circle was off by half a point at *any* resolution, where
pdfium and MuPDF have no error at all -- and the SVG export, whose whole point
is a resolution-independent facsimile, wrote those segments out as ``L``
commands: a circle became fifty lines.

Now the step count comes from the curve's length in device pixels, so the error
is under :data:`_BEZIER_TOLERANCE` of a pixel at every scale, and each subpath
remembers which of its points stand in for a cubic so the exporter can write the
cubic instead.

Checked outside the suite with MuPDF, which reads SVG as well as PDF: on a page
of 24 stroked circles it renders the exported SVG and the original PDF with *no*
pixel differing by more than antialiasing (max 11 of 255), where the flattened
export differed on 0.56% of the page by up to 86 -- and the file went from
20 760 bytes to 7 138.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ElementTree
from itertools import pairwise

import pytest

from aspose_pdf.engine.cos import PdfDictionary, PdfName
from aspose_pdf.engine.rasterizer import (
    _BEZIER_MAX_STEPS,
    _BEZIER_TOLERANCE,
    _bezier_steps,
    render_page,
)
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.engine.stroke import Subpath
from aspose_pdf.engine.svg_export import page_to_svg

_SVG_NS = "{http://www.w3.org/2000/svg}"


class _StubWriter:
    """Just enough of the writer for :meth:`_path_data`."""

    _precision = 3

#: The constant that turns a quarter circle into a cubic.
KAPPA = 0.5522847498


def _page(content: bytes, size=(200, 200)) -> SimplePdf:
    pdf = SimplePdf()
    pdf.pages = [(0, 0, *size)]
    pdf.page_contents = [content]
    pdf._ensure_cos()
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary({})
    return pdf


def _circle_content(cx: float, cy: float, r: float, *, paint: bytes = b"f") -> bytes:
    k = KAPPA * r
    return (
        f"{cx + r} {cy} m "
        f"{cx + r} {cy + k} {cx + k} {cy + r} {cx} {cy + r} c "
        f"{cx - k} {cy + r} {cx - r} {cy + k} {cx - r} {cy} c "
        f"{cx - r} {cy - k} {cx - k} {cy - r} {cx} {cy - r} c "
        f"{cx + k} {cy - r} {cx + r} {cy - k} {cx + r} {cy} c "
    ).encode("ascii") + paint


def _path_commands(svg: str) -> list[str]:
    root = ElementTree.fromstring(svg)
    return [
        element.get("d", "") for element in root.iter(f"{_SVG_NS}path")
    ]


def _numbers(text: str) -> list[float]:
    return [float(value) for value in re.findall(r"-?\d+(?:\.\d+)?", text)]


def _painted_subpaths(pdf: SimplePdf, page: int = 0, **kwargs) -> list[Subpath]:
    """Every subpath the interpreter painted, as it painted it."""
    from aspose_pdf.engine import rasterizer as module

    captured: list[Subpath] = []
    original = module._PageRasterizer._paint_path

    def spy(self, op, depth=0):
        captured.extend(self.path.subpaths)
        return original(self, op, depth)

    module._PageRasterizer._paint_path = spy
    try:
        render_page(pdf, page, **kwargs)
    finally:
        module._PageRasterizer._paint_path = original
    return captured


# --- the exported curve -------------------------------------------------------


def test_a_curve_is_exported_as_a_curve():
    svg = page_to_svg(_page(b"0 0 0 rg " + _circle_content(100, 100, 60)), 0)
    [data] = [d for d in _path_commands(svg) if d]
    # Four cubics and nothing else: the circle the page describes.
    assert data.count("C") == 4
    assert "L" not in data
    assert data.endswith("Z")


def test_the_exported_control_points_are_the_page_s_own():
    r, cx, cy = 60.0, 100.0, 100.0
    svg = page_to_svg(_page(b"0 0 0 rg " + _circle_content(cx, cy, r)), 0)
    [data] = [d for d in _path_commands(svg) if d]
    # The first cubic: from (cx+r, cy) with controls (cx+r, cy+k) and (cx+k, cy+r)
    # to (cx, cy+r). SVG y grows downward on a 200 pt page, so each y is 200 - y.
    k = KAPPA * r
    start = _numbers(data.split("C")[0])
    first = _numbers(data.split("C")[1].split("C")[0])
    assert start == pytest.approx([cx + r, 200 - cy], abs=0.01)
    assert first[:6] == pytest.approx(
        [cx + r, 200 - (cy + k), cx + k, 200 - (cy + r), cx, 200 - (cy + r)], abs=0.01
    )


@pytest.mark.parametrize("operator", ["c", "v", "y"])
def test_every_curve_operator_is_exported_as_a_cubic(operator):
    # v and y leave one control point implied (ISO 32000-1 table 59); the cubic
    # written out has to be the one they mean, not the one they spell.
    bodies = {
        "c": b"20 80 60 80 80 20 c",
        "v": b"60 80 80 20 v",
        "y": b"20 80 80 20 y",
    }
    svg = page_to_svg(_page(b"0 0 0 RG 20 20 m " + bodies[operator] + b" S"), 0)
    [data] = [d for d in _path_commands(svg) if d]
    assert data.count("C") == 1 and "L" not in data
    numbers = _numbers(data.split("C")[1])
    expected = {
        "c": [20, 120, 60, 120, 80, 180],
        "v": [20, 180, 60, 120, 80, 180],  # first control is the current point
        "y": [20, 120, 80, 180, 80, 180],  # second control is the end point
    }[operator]
    assert numbers[:6] == pytest.approx(expected, abs=0.01)


def test_lines_and_curves_in_one_subpath_keep_their_order():
    svg = page_to_svg(
        _page(b"0 0 0 RG 20 20 m 20 100 l 60 140 100 140 140 100 c 140 20 l S"), 0
    )
    [data] = [d for d in _path_commands(svg) if d]
    assert re.findall(r"[MLCZ]", data) == ["M", "L", "C", "L"]


def test_a_shape_with_no_curves_in_it_is_still_a_polyline():
    svg = page_to_svg(_page(b"1 0 0 rg 20 20 80 60 re f"), 0)
    [data] = [d for d in _path_commands(svg) if d]
    assert "C" not in data and data.count("L") == 4


def test_glyph_outlines_are_unaffected():
    # Text is exported as contours built from points, with no cubics recorded;
    # they must still come out as the polylines they are.
    pdf = _page(b"BT /F1 24 Tf 20 100 Td (Og) Tj ET")
    font = pdf._cos_doc.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("Font"),
                PdfName("Subtype"): PdfName("Type1"),
                PdfName("BaseFont"): PdfName("Helvetica"),
            }
        )
    )
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("Font"): PdfDictionary({PdfName("F1"): font})}
    )
    svg = page_to_svg(pdf, 0)
    assert any("L" in data for data in _path_commands(svg) if data)


# --- how finely a curve is flattened -----------------------------------------


def test_a_curve_is_flattened_to_the_size_it_is_drawn_at():
    pdf = _page(b"0 0 0 rg " + _circle_content(100, 100, 80), size=(200, 200))
    counts = {}
    for scale in (1.0, 4.0):
        [subpath] = [sp for sp in _painted_subpaths(pdf, scale=scale) if len(sp) > 4]
        counts[scale] = len(subpath)
    # Four times the size, more segments -- a fixed count would give the same.
    assert counts[4.0] > counts[1.0]


@pytest.mark.parametrize("radius", [8.0, 80.0])
@pytest.mark.parametrize("scale", [1.0, 4.0])
def test_the_flattening_stays_within_its_tolerance(radius, scale):
    centre = 100.0
    pdf = _page(b"0 0 0 rg " + _circle_content(centre, centre, radius))
    [subpath] = [sp for sp in _painted_subpaths(pdf, scale=scale) if len(sp) > 4]
    # The chord midpoints are where a polyline is furthest inside the arc.
    worst = max(
        radius - math.hypot((x0 + x1) / 2 - centre, (y0 + y1) / 2 - centre)
        for (x0, y0), (x1, y1) in pairwise(subpath)
    )
    assert worst * scale <= _BEZIER_TOLERANCE


def test_a_tiny_curve_does_not_get_a_fine_flattening():
    pdf = _page(b"0 0 0 rg " + _circle_content(100, 100, 1.0))
    [subpath] = [sp for sp in _painted_subpaths(pdf, scale=1.0) if len(sp) > 2]
    assert len(subpath) <= 4 * 5  # four cubics, a handful of steps each


def test_however_long_the_curve_the_segments_are_bounded():
    huge = ((0.0, 0.0), (1e7, 0.0), (1e7, 1e7), (0.0, 1e7))
    assert _bezier_steps(*huge, scale=1000.0) == _BEZIER_MAX_STEPS


@pytest.mark.parametrize(
    "points",
    [
        ((10.0, 10.0),) * 4,  # a curve that goes nowhere
        ((0.0, 0.0), (float("nan"), 0.0), (1.0, 1.0), (2.0, 2.0)),
    ],
    ids=["zero length", "not a number"],
)
def test_a_curve_with_no_length_needs_one_segment(points):
    assert _bezier_steps(*points) == 1


def test_a_degenerate_curve_does_not_stop_the_page():
    # A curve with no current point, and one whose operands are missing: the
    # page still renders, and nothing is recorded for them.
    pdf = _page(b"0 0 0 RG 20 80 60 80 80 20 c 20 20 m 40 40 l S")
    raster = render_page(pdf, 0, antialias=False)
    assert raster.width == 200
    svg = page_to_svg(pdf, 0)
    assert "L" in "".join(_path_commands(svg))


# --- what the record must not disturb -----------------------------------------


def test_a_curve_inside_a_clipping_path_still_clips():
    # A clip is taken from the flattened points, which is what a clip is; the
    # record on the subpath must not change that.
    pdf = _page(
        b"q " + _circle_content(100, 100, 40, paint=b"W n")
        + b" 1 0 0 rg 0 0 200 200 re f Q"
    )
    raster = render_page(pdf, 0, antialias=False)
    inside = raster.get_pixel(100, 100)
    outside = raster.get_pixel(5, 5)
    assert inside[0] > 200 and inside[1] < 90
    assert outside == (255, 255, 255)


def test_a_subpath_built_without_curves_reads_as_a_polyline():
    plain = Subpath([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)])
    assert plain.curves == ()
    # And the class default is not shared between instances.
    other = Subpath([(0.0, 0.0)])
    plain.curves = ((1, 2, (0.5, 0.0), (1.0, 0.5)),)
    assert other.curves == ()


def test_a_curve_is_never_drawn_as_a_single_chord():
    # However short, a curve gets enough segments to still be a curve: one
    # chord would turn a small arc into the straight line between its ends.
    for length in (0.01, 0.5, 2.0):
        assert (
            _bezier_steps(
                (0.0, 0.0),
                (length / 3, 0.0),
                (2 * length / 3, 0.0),
                (length, 0.0),
            )
            >= 4
        )


def test_a_curve_straight_after_a_closepath_keeps_its_control_points():
    # `h` closes the subpath, so the curve's own first point opens a new one --
    # its points are not where the subpath it was drawn from ends. Looked for
    # there, the record was dropped and the curve went out as its polyline.
    pdf = _page(b"0 0 0 RG 20 20 m 40 40 l h 60 100 100 140 140 140 c S")
    subpaths = _painted_subpaths(pdf, scale=1.0)
    closed = [sp for sp in subpaths if getattr(sp, "closed", False)]
    assert closed and all(sp.curves == () for sp in closed)
    [curved] = [sp for sp in subpaths if sp.curves]
    first, last, c1, c2 = curved.curves[0]
    assert 0 < first <= last < len(curved)
    assert (c1, c2) == ((60.0, 100.0), (100.0, 140.0))

    data = "".join(_path_commands(page_to_svg(pdf, 0)))
    assert data.count("C") == 1


def test_a_curve_record_that_does_not_fit_its_points_is_ignored():
    # Defensive: a record naming points a subpath does not have describes
    # nothing, and the points are still a polyline that can be written.
    from aspose_pdf.engine.svg_export import _SvgWriter

    subpath = Subpath([(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)])
    subpath.curves = ((1, 99, (1.0, 1.0), (2.0, 2.0)), (0, 2, (1.0, 1.0), (2.0, 2.0)))
    data = _SvgWriter._path_data(
        _StubWriter(), [subpath], close=False
    )
    assert "C" not in data and data.count("L") == 2

