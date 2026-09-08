"""The page begins on a transparent backdrop, not on the paper.

ISO 32000-1 11.4.7: a page's contents are a transparency group, and that group
is *isolated* -- it starts with nothing behind it. The paper comes in once, at
the end, when the finished group is composited onto it.

The difference is invisible until a blend mode asks what is underneath. The
canvas was the paper, opaque and white from the first pixel, so every blend
happened against paper that is not there yet: ``Screen`` over bare page came
out white, where it should leave the colour alone, and ``Multiply`` over bare
page came out unchanged where the two agree only by accident. The compositing
formula (11.3.8) already had the term for it -- ``(1 - ab) * Cs`` -- and it was
simply never reached, because the page canvas carried no alpha to be zero.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document

_WHITE = (255, 255, 255)


def _document(content: bytes, extgstate: bytes = b"") -> Document:
    resources = b"/ExtGState << " + extgstate + b" >> " if extgstate else b""
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 60 60] /Resources << "
            + resources
            + b">> /Contents 4 0 R >>"
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


def _pixel(
    content: bytes,
    extgstate: bytes = b"",
    at: tuple[int, int] = (30, 30),
    background: tuple[int, int, int] = _WHITE,
) -> tuple[int, int, int]:
    raster = (
        _document(content, extgstate)
        .pages[0]
        .render(antialias=False, background=background)
    )
    return raster.get_pixel(*at)


_CYAN = b"0 1 1 rg 10 10 40 40 re f"


# --- a blend over bare page has nothing to blend with -----------------------


@pytest.mark.parametrize(
    "mode",
    [
        "Normal",
        "Multiply",
        "Screen",
        "Overlay",
        "Darken",
        "Lighten",
        "ColorDodge",
        "ColorBurn",
        "HardLight",
        "SoftLight",
        "Difference",
        "Exclusion",
        "Hue",
        "Saturation",
        "Color",
        "Luminosity",
    ],
)
def test_any_blend_over_bare_page_paints_the_colour_itself(mode):
    # With nothing behind it there is no backdrop to blend against, so every
    # mode leaves the source colour alone -- which is what makes Screen and
    # Difference (which would otherwise whiten and invert) come out right.
    painted = _pixel(
        b"/GS1 gs " + _CYAN,
        b"/GS1 << /BM /" + mode.encode() + b" >> ",
    )
    assert painted == (0, 255, 255)


def test_a_blend_over_something_painted_still_blends():
    # The backdrop is only absent where nothing has been drawn.
    content = b"1 1 0 rg 0 0 60 60 re f /GS1 gs " + _CYAN
    assert _pixel(content, b"/GS1 << /BM /Multiply >> ") == (0, 255, 0)
    assert _pixel(content, b"/GS1 << /BM /Screen >> ") == (255, 255, 255)


def test_the_page_is_still_the_colour_it_was_given_where_nothing_paints():
    assert _pixel(_CYAN, at=(2, 2)) == _WHITE
    assert _pixel(_CYAN, at=(2, 2), background=(10, 20, 30)) == (10, 20, 30)


def test_a_blend_over_bare_page_still_meets_a_coloured_background():
    # The paper is joined at the end, so it tints what was drawn over nothing
    # only through the alpha -- and an opaque paint hides it entirely.
    assert _pixel(_CYAN, background=(10, 20, 30)) == (0, 255, 255)


# --- partial coverage is where the paper actually shows through -------------


def test_a_half_transparent_paint_meets_the_paper():
    assert _pixel(b"/GS1 gs 0 0 0 rg 10 10 40 40 re f", b"/GS1 << /ca 0.5 >> ") == (
        128,
        128,
        128,
    )


def test_a_half_transparent_paint_meets_a_coloured_paper():
    painted = _pixel(
        b"/GS1 gs 0 0 0 rg 10 10 40 40 re f",
        b"/GS1 << /ca 0.5 >> ",
        background=(0, 0, 255),
    )
    assert painted == (0, 0, 128)


def test_two_half_transparent_paints_see_each_other():
    content = b"/GS1 gs 1 0 0 rg 0 0 60 60 re f 0 0 1 rg 10 10 40 40 re f"
    # Red at half over nothing is red at half; blue at half over that sees it,
    # and both meet the paper at the end.
    assert _pixel(content, b"/GS1 << /ca 0.5 >> ") == (128, 64, 191)
    assert _pixel(content, b"/GS1 << /ca 0.5 >> ", at=(5, 5)) == (255, 128, 128)


def test_a_blend_under_partial_alpha_is_still_taken_against_nothing():
    painted = _pixel(b"/GS1 gs " + _CYAN, b"/GS1 << /ca 0.5 /BM /Multiply >> ")
    # Multiply against no backdrop is the colour; then half of it meets paper.
    assert painted == (128, 255, 255)


# --- the plumbing -----------------------------------------------------------


def test_a_written_pixel_never_reports_no_coverage():
    # Zero coverage is what says "nothing has ever reached here", and the
    # flatten relies on that being exactly true -- so a paint too faint to
    # round up to one is still recorded as one.
    from aspose_pdf.engine.rasterizer import _Canvas

    canvas = _Canvas(4, 4, _WHITE)
    canvas.alpha = bytearray(16)
    canvas.set_pixel(1, 1, (0, 0, 0), alpha=0.0005)
    assert canvas.alpha[5] == 1


def test_the_page_is_opaque_once_it_is_on_the_paper():
    # Annotations are drawn after the flatten and must blend against the page.
    from aspose_pdf.engine.rasterizer import _PageRasterizer

    document = _document(_CYAN)
    page = _PageRasterizer(
        document._engine_pdf,
        0,
        dpi=72.0,
        scale=1.0,
        background=_WHITE,
        antialias=False,
    )
    assert page.canvas.alpha is not None
    page.render()
    assert page.canvas.alpha is None


def test_only_partly_covered_pixels_are_mixed():
    from aspose_pdf.engine.rasterizer import _PARTIAL_COVERAGE

    assert _PARTIAL_COVERAGE[0] == 0
    assert _PARTIAL_COVERAGE[255] == 0
    assert _PARTIAL_COVERAGE[1] == 1
    assert _PARTIAL_COVERAGE[254] == 1
