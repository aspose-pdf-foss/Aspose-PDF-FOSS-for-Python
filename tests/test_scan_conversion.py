"""A rectangle covers the pixels it covers, and a clip passes exactly those.

The rasteriser had four different rules for turning a span's floating-point
``x`` range into pixel columns. The glyph and stroke paths used the pixel-centre
rule; path fill, the clip mask and shading used ``floor(x_from)`` to
``ceil(x_to)`` *inclusive*, which covers one column too many for any
integer-aligned edge -- while ``_scan_spans`` had always chosen rows by their
centres. So rows came out exact and columns one too wide: a 1-unit vertical rule
rendered two pixels across, a 1-unit horizontal rule one pixel down, and every
clip leaked a column of content past its right edge.

Measured against MuPDF, pdfium and poppler on thirteen rectangles x fill and
clip: on the twenty-three cases where all three references agree with each other
they now all agree with us too, and on none did they before. The clip is
conservative -- it keeps a pixel of which the region covers any area -- which is
what the three references do; a fill keeps a pixel whose centre is inside, which
is what they do and what this renderer already did for glyphs.
"""

from __future__ import annotations

import pytest

from aspose_pdf import Document

_WHITE = (255, 255, 255)
_PAGE = 100


def _pdf(content: bytes, *, extra_resources: str = "", objects: list[bytes] = ()) -> bytes:
    """A one-page PDF of *content* on a 100x100 page, at one point per pixel."""
    body = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_PAGE} {_PAGE}]"
         f" /Resources << {extra_resources} >> /Contents 4 0 R >>").encode(),
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n"
        + content + b"\nendstream",
        *objects,
    ]
    out = bytearray(b"%PDF-1.7\n")
    offsets = []
    for number, obj in enumerate(body, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
    start = len(out)
    out += f"xref\n0 {len(body) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(body) + 1} /Root 1 0 R >>\n"
            f"startxref\n{start}\n%%EOF\n").encode()
    return bytes(out)


def _painted(content: str, **kwargs) -> set[tuple[int, int]]:
    """The device pixels *content* leaves non-white."""
    document = Document()
    document.load_from(_pdf(content.encode(), **kwargs))
    raster = document.pages[0].render(dpi=72, antialias=False)
    pixels = {
        (x, y)
        for y in range(_PAGE)
        for x in range(_PAGE)
        if raster.get_pixel(x, y) != _WHITE
    }
    document.dispose()
    return pixels


def _block(x: int, y: int, w: int, h: int) -> set[tuple[int, int]]:
    """The device pixels a PDF rectangle at *(x, y)* of *w* x *h* covers.

    PDF user space has its origin at the bottom left and the raster's at the top
    left, so a rectangle whose top is at user ``y + h`` starts at device row
    ``page - (y + h)``.
    """
    top = _PAGE - (y + h)
    return {(px, py) for px in range(x, x + w) for py in range(top, top + h)}


_RECTS = [
    (20, 20, 40, 40),
    (30, 30, 10, 10),
    (0, 0, 1, 1),
    (5, 5, 1, 1),
    (10, 10, 3, 7),
    (0, 50, 100, 1),
    (50, 0, 1, 100),
    (0, 0, 100, 100),
]


# --- a fill covers its own pixels and no others -----------------------------


@pytest.mark.parametrize(("x", "y", "w", "h"), _RECTS)
def test_a_filled_rectangle_covers_exactly_its_pixels(x, y, w, h):
    assert _painted(f"q 0 g {x} {y} {w} {h} re f Q") == _block(x, y, w, h)


@pytest.mark.parametrize(("x", "y", "w", "h"), _RECTS)
def test_a_clip_passes_exactly_its_pixels(x, y, w, h):
    content = f"q {x} {y} {w} {h} re W n 0 g 0 0 {_PAGE} {_PAGE} re f Q"
    assert _painted(content) == _block(x, y, w, h)


def test_a_one_point_rule_is_one_pixel_wide_in_both_directions():
    # The asymmetry that gave the defect away: a vertical rule came out two
    # pixels across while a horizontal one came out one pixel down.
    vertical = _painted("q 0 g 50 10 1 80 re f Q")
    horizontal = _painted("q 0 g 10 50 80 1 re f Q")
    assert {x for x, _ in vertical} == {50}
    assert {y for _, y in horizontal} == {_PAGE - 51}
    assert len(vertical) == 80
    assert len(horizontal) == 80


@pytest.mark.parametrize(("x", "y", "w", "h"), _RECTS)
def test_a_shading_pattern_fill_covers_the_same_pixels_as_a_plain_fill(x, y, w, h):
    # A path filled with a shading pattern goes through a painter of its own,
    # which carried the same off-by-one. No clip here: the path's own extent has
    # to decide the result, or the rule never shows.
    pattern = (
        "<< /Type /Pattern /PatternType 2 /Shading << /ShadingType 2"
        " /ColorSpace /DeviceRGB /Coords [0 0 100 0] /Function"
        " << /FunctionType 2 /Domain [0 1] /C0 [1 0 0] /C1 [0 0 1] /N 1 >>"
        " /Extend [true true] >> >>"
    )
    painted = _painted(
        f"q /Pattern cs /P0 scn {x} {y} {w} {h} re f Q",
        extra_resources="/Pattern << /P0 " + pattern + " >>",
    )
    assert painted == _block(x, y, w, h)


# --- degenerate clips --------------------------------------------------------


@pytest.mark.parametrize("rect", ["20 20 0 40", "20 20 40 0", "20 20 0 0"])
def test_a_clip_with_no_area_passes_nothing(rect):
    # A zero-width rectangle encloses no points, so the clip is empty. The
    # inclusive end used to let a one-pixel column through.
    assert _painted(f"q {rect} re W n 0 g 0 0 {_PAGE} {_PAGE} re f Q") == set()


def test_a_negative_rectangle_is_the_same_region():
    # `60 20 -40 40 re` is the rectangle from x 20 to x 60, written backwards.
    assert _painted("q 0 g 60 20 -40 40 re f Q") == _block(20, 20, 40, 40)


# --- composed clips ----------------------------------------------------------


def test_an_even_odd_clip_leaves_exactly_the_inner_rectangle_out():
    content = (
        f"q 10 10 80 80 re 30 30 40 40 re W* n 0 g 0 0 {_PAGE} {_PAGE} re f Q"
    )
    assert _painted(content) == _block(10, 10, 80, 80) - _block(30, 30, 40, 40)


def test_a_nonzero_clip_of_the_same_two_rectangles_keeps_the_middle():
    content = (
        f"q 10 10 80 80 re 30 30 40 40 re W n 0 g 0 0 {_PAGE} {_PAGE} re f Q"
    )
    assert _painted(content) == _block(10, 10, 80, 80)


def test_nested_clips_intersect():
    content = (
        f"q 10 10 60 60 re W n 40 40 50 50 re W n 0 g 0 0 {_PAGE} {_PAGE} re f Q"
    )
    assert _painted(content) == _block(10, 10, 60, 60) & _block(40, 40, 50, 50)


# --- where the two rules differ ---------------------------------------------


def test_a_clip_keeps_a_pixel_it_only_partly_covers():
    # Conservative, as all three reference renderers are: x 10.9 to 31.1 touches
    # columns 10 through 31, and rows likewise.
    painted = _painted(f"q 10.9 10.9 20.2 20.2 re W n 0 g 0 0 {_PAGE} {_PAGE} re f Q")
    assert {x for x, _ in painted} == set(range(10, 32))
    assert {y for _, y in painted} == set(range(_PAGE - 32, _PAGE - 10))


def test_a_clip_edge_on_a_pixel_boundary_does_not_reach_the_next_pixel():
    # The difference between covering area and touching a line: the region ends
    # exactly at 60, so column 60 and the row beyond are outside it.
    painted = _painted(f"q 20 20 40 40 re W n 0 g 0 0 {_PAGE} {_PAGE} re f Q")
    assert max(x for x, _ in painted) == 59
    assert max(y for _, y in painted) == _PAGE - 21


def test_a_fill_keeps_a_pixel_whose_centre_is_inside():
    # A fill is not conservative: the same fractional rectangle covers only the
    # pixels whose centres it contains, which is what the references fill and
    # what this renderer already did for glyph outlines.
    painted = _painted("q 0 g 10.9 10.9 20.2 20.2 re f Q")
    assert {x for x, _ in painted} == set(range(11, 31))
    assert {y for _, y in painted} == set(range(_PAGE - 31, _PAGE - 11))
