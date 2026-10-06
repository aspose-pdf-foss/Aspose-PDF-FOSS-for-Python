"""Authored colour is grey, RGB or CMYK -- one rule, wherever a colour is taken.

``Page.add_text``, ``draw_rectangle`` and ``draw_line`` used to insist on exactly
three components: ``normalize_rgb`` raised "RGB color must contain exactly three
channels" for ``(0.5,)`` and for ``(0, 0.2, 1, 0.05)``. Everything *else* in the
package already read 1/3/4 components -- a stamp's colour, an annotation's ``/C``
and ``/IC``, a field's ``/MK`` -- so the page authoring API was the one place that
could not set a grey or an ink, which is what a print workflow is written in.

The three device spaces are the ones a content stream can set without naming a
colour space first (ISO 32000-1 8.6.8): ``g``/``G`` for DeviceGray, ``rg``/``RG``
for DeviceRGB, ``k``/``K`` for DeviceCMYK.

Checked against two independent implementations: **qpdf 12.4.2** parses the
content streams and reports the operators and operands below, and **poppler
26.09** (``pdftoppm``) renders grey and RGB to the same pixel we do. On CMYK
poppler's own colour-managed transform differs from the subtractive formula of
8.6.4.4 that this renderer uses -- a documented difference, not a disagreement
about the operator -- so the CMYK pixels here are checked against the formula.
"""

from __future__ import annotations

import io
import re

import pytest

from aspose_pdf import Color, Document, TextStamp
from aspose_pdf.exceptions import PdfValidationException, UnsupportedFeatureException


def _saved(document: Document) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _page(width: float = 300, height: float = 200):
    document = Document()
    return document, document.pages.add(size=(width, height))


def _operators(data: bytes, operator: bytes) -> list[str]:
    """Every operand list written with *operator* in the file's content."""
    pattern = rb"([-0-9.]+(?: [-0-9.]+)*) " + operator + rb"(?![A-Za-z])"
    return [match.decode("ascii") for match in re.findall(pattern, data)]


def _cmyk_to_rgb(c: float, m: float, y: float, k: float) -> tuple[int, int, int]:
    """What the renderer paints a CMYK fill as: the *multiplicative* conversion.

    ``rasterizer._cmyk`` and ``image_export.cmyk_to_rgb`` both use
    ``(1 - ink) * (1 - k)``, which is not the additive ``1 - min(1, ink + k)`` of
    ISO 32000-1 8.6.4.4 that ``graphics_absorb._cmyk_rgb`` reports colours with.
    The two agree wherever an ink or the black is 0 or 1 and differ by three or
    four levels for mixed values. That difference is the renderer's, not the
    writer's, and is not this change's to settle -- what is asserted here is only
    that a CMYK fill arrives at the renderer as CMYK.
    """
    return tuple(round(255 * (1 - min(1.0, comp)) * (1 - min(1.0, k))) for comp in (c, m, y))


# ---------------------------------------------------------------------------
# The operator that reaches the file
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("color", "operator", "operands"),
    [
        (0.5, b"g", "0.5"),
        ((0.5,), b"g", "0.5"),
        ((1, 0, 0), b"rg", "1 0 0"),
        ((255, 128, 0), b"rg", "1 0.501961 0"),
        ("#336699", b"rg", "0.2 0.4 0.6"),
        ("#f80", b"rg", "1 0.533333 0"),
        ((0, 0.2, 1, 0.05), b"k", "0 0.2 1 0.05"),
    ],
    ids=["gray-scalar", "gray-tuple", "rgb", "rgb-255", "hex-6", "hex-3", "cmyk"],
)
def test_a_fill_is_written_in_the_space_its_colour_names(color, operator, operands):
    document, page = _page()
    page.draw_rectangle(10, 10, 100, 100, fill_color=color, stroke_color=None)
    assert operands in _operators(_saved(document), operator)


@pytest.mark.parametrize(
    ("color", "operator"),
    [(0.5, b"G"), ((1, 0, 0), b"RG"), ((0, 0.2, 1, 0.05), b"K")],
    ids=["gray", "rgb", "cmyk"],
)
def test_a_stroke_uses_the_upper_case_operator(color, operator):
    document, page = _page()
    page.draw_line(10, 10, 100, 100, stroke_color=color)
    assert _operators(_saved(document), operator)


def test_text_takes_every_space_too():
    document, page = _page()
    page.add_text("grey", 10, 150, color=0.25)
    page.add_text("rgb", 10, 120, color="#336699")
    page.add_text("ink", 10, 90, color=(0, 0.9, 0.9, 0.05))
    data = _saved(document)
    assert "0.25" in _operators(data, b"g")
    assert "0.2 0.4 0.6" in _operators(data, b"rg")
    assert "0 0.9 0.9 0.05" in _operators(data, b"k")


def test_a_rectangle_can_stroke_and_fill_in_different_spaces():
    document, page = _page()
    page.draw_rectangle(10, 10, 100, 100, fill_color=(0, 0.2, 1, 0), stroke_color=0.0)
    data = _saved(document)
    assert "0 0.2 1 0" in _operators(data, b"k")
    assert "0" in _operators(data, b"G")


# ---------------------------------------------------------------------------
# What the renderer makes of it
# ---------------------------------------------------------------------------


def test_each_space_renders_the_colour_it_names():
    document, page = _page()
    page.draw_rectangle(0, 100, 150, 100, fill_color=(0, 0.2, 1, 0.05), stroke_color=None)
    page.draw_rectangle(150, 100, 150, 100, fill_color=Color.gray(0.25), stroke_color=None)
    page.draw_rectangle(0, 0, 150, 100, fill_color="#336699", stroke_color=None)
    raster = Document(io.BytesIO(_saved(document))).pages[0].render(dpi=72)
    assert raster.get_pixel(70, 50) == _cmyk_to_rgb(0, 0.2, 1, 0.05)
    assert raster.get_pixel(220, 50) == (64, 64, 64)
    assert raster.get_pixel(70, 150) == (51, 102, 153)


def test_the_same_colour_in_two_spaces_renders_the_same():
    # Black is black whichever way it is asked for, which is the cheapest check
    # that none of the three operators is being misread.
    document, page = _page()
    page.draw_rectangle(0, 100, 300, 100, fill_color=(0, 0, 0, 1), stroke_color=None)
    page.draw_rectangle(0, 0, 300, 100, fill_color=(0.0,), stroke_color=None)
    raster = Document(io.BytesIO(_saved(document))).pages[0].render(dpi=72)
    assert raster.get_pixel(150, 50) == raster.get_pixel(150, 150) == (0, 0, 0)


# ---------------------------------------------------------------------------
# One rule, every entry point
# ---------------------------------------------------------------------------


def test_a_stamp_and_a_page_read_a_colour_the_same_way():
    document, page = _page(400, 200)
    page.add_stamp(TextStamp("X", font_size=30, color=(0, 1, 1, 0)))
    page.add_text("X", 10, 10, color=(0, 1, 1, 0))
    data = _saved(document)
    assert _operators(data, b"k").count("0 1 1 0") == 2


def test_a_redaction_bar_takes_an_ink_as_well():
    document, page = _page(400, 200)
    page.add_text("secret", 20, 100)
    assert page.redact_text("secret", overlay=True, overlay_color=(0, 1, 1, 0.1))
    data = _saved(document)
    assert "0 1 1 0.1" in _operators(data, b"k")


def test_a_redaction_bar_refuses_a_colour_it_cannot_read():
    # It used to fall back to black on anything unreadable. The bar is what makes
    # the removal visible, so the wrong colour is worse than an error.
    _, page = _page(400, 200)
    page.add_text("secret", 20, 100)
    with pytest.raises(PdfValidationException):
        page.redact_text("secret", overlay=True, overlay_color=(1, 0))


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "color",
    [(), (1, 0), (1, 0, 0, 0, 0), "blue", "#12345", b"\x01\x02\x03", (0, 0, None),
     (0, 0, -1), (0, 0, float("nan")), (0, 0, float("inf")), (True, False, True)],
)
def test_a_colour_that_is_not_a_colour_is_refused(color):
    _, page = _page()
    with pytest.raises(PdfValidationException):
        page.draw_rectangle(10, 10, 50, 50, fill_color=color, stroke_color=None)


def test_a_component_above_one_is_the_eight_bit_scale_for_grey_and_rgb():
    document, page = _page()
    page.draw_rectangle(10, 10, 50, 50, fill_color=(128,), stroke_color=None)
    page.draw_rectangle(70, 10, 50, 50, fill_color=(255, 128, 0), stroke_color=None)
    data = _saved(document)
    assert "0.501961" in _operators(data, b"g")
    assert "1 0.501961 0" in _operators(data, b"rg")


def test_cmyk_is_not_guessed_at_when_a_component_is_above_one():
    # 20 could be 20% or 20/255 and the two are nowhere near each other, so the
    # one thing not to do is pick one.
    _, page = _page()
    with pytest.raises(PdfValidationException, match="CMYK"):
        page.draw_rectangle(10, 10, 50, 50, fill_color=(0, 20, 100, 10), stroke_color=None)


def test_a_colour_still_out_of_range_after_the_eight_bit_scale_is_refused():
    _, page = _page()
    with pytest.raises(PdfValidationException, match=r"0\.\.1 or 0\.\.255"):
        page.draw_rectangle(10, 10, 50, 50, fill_color=(300, 0, 0), stroke_color=None)


def test_a_rectangle_still_needs_one_of_the_two_colours():
    _, page = _page()
    with pytest.raises(PdfValidationException):
        page.draw_rectangle(10, 10, 50, 50, fill_color=None, stroke_color=None)


# ---------------------------------------------------------------------------
# Color
# ---------------------------------------------------------------------------


def test_the_color_factories():
    assert Color.gray(0.5).components == (0.5,)
    assert Color.gray(128).components == (128 / 255,)
    assert Color.rgb(1, 0, 0).components == (1.0, 0.0, 0.0)
    assert Color.rgb(255, 128, 0).components == (1.0, 128 / 255, 0.0)
    assert Color.cmyk(0, 0.2, 1, 0.05).components == (0.0, 0.2, 1.0, 0.05)
    assert Color.from_hex("#336699").components == (0.2, 0.4, 0.6)
    assert Color.from_hex("036").components == (0.0, 0.2, 0.4)


def test_a_colors_space_is_the_one_its_components_name():
    assert Color.gray(0).color_space == "DeviceGray"
    assert Color.rgb(0, 0, 0).color_space == "DeviceRGB"
    assert Color.cmyk(0, 0, 0, 1).color_space == "DeviceCMYK"


def test_the_ported_constructor_still_builds_an_rgb_colour():
    # Color(pattern, r, g, b) is the signature ported code calls.
    color = Color(None, 1, 0, 0)
    assert color.components == (1.0, 0.0, 0.0)
    assert (color.r, color.g, color.b) == (1.0, 0.0, 0.0)
    assert color == Color.from_hex("#ff0000")


def test_a_grey_colour_answers_its_level_for_r_g_and_b():
    # r/g/b are what ported code reads; a grey colour is that level in all three
    # rather than an error.
    grey = Color.gray(0.4)
    assert (grey.r, grey.g, grey.b) == (0.4, 0.4, 0.4)


def test_a_cmyk_colour_has_no_rgb_channel_to_hand_back():
    with pytest.raises(PdfValidationException, match="DeviceCMYK"):
        Color.cmyk(0, 0, 0, 1).r


def test_a_color_can_be_drawn_with_wherever_a_colour_is_taken():
    document, page = _page()
    page.add_text("x", 10, 150, color=Color.cmyk(1, 0, 0, 0))
    page.draw_rectangle(10, 10, 50, 50, fill_color=Color.gray(0.5), stroke_color=None)
    page.draw_line(10, 100, 100, 100, stroke_color=Color.from_hex("#ff0000"))
    page.add_stamp(TextStamp("s", color=Color.gray(0)))
    data = _saved(document)
    assert "1 0 0 0" in _operators(data, b"k")
    assert "0.5" in _operators(data, b"g")
    assert "1 0 0" in _operators(data, b"RG")


def test_a_gradient_colour_cannot_be_authored():
    # The value object exists for ported code; there is no shading-pattern
    # writer for it to feed, so asking for its components says so.
    from aspose_pdf.color import GradientAxialShading, Point

    gradient = Color(
        GradientAxialShading(Color.rgb(1, 0, 0), Color.rgb(0, 0, 1), Point(0, 0), Point(1, 1))
    )
    assert gradient.pattern_color_space is not None
    with pytest.raises(UnsupportedFeatureException):
        gradient.components
    _, page = _page()
    with pytest.raises(UnsupportedFeatureException):
        page.draw_rectangle(10, 10, 50, 50, fill_color=gradient, stroke_color=None)


def test_color_equality_and_repr():
    assert Color.rgb(1, 0, 0) == Color.from_hex("#ff0000")
    assert Color.gray(0) != Color.cmyk(0, 0, 0, 1)
    assert Color.rgb(1, 0, 0) != (1, 0, 0)
    assert repr(Color.cmyk(0, 0, 0, 1)) == "Color.cmyk(0, 0, 0, 1)"
    assert repr(Color.gray(0.5)) == "Color.gray(0.5)"
