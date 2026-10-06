"""A pattern is anchored to the stream it is used in, not to the page.

ISO 32000-1 8.7.3.1: the pattern matrix maps pattern space to the **default**
coordinate space of the content stream the pattern is used in -- the page's, or
a form XObject's where the use is inside one. The CTM in force at the moment of
the fill does not move the tiles; the matrix the form was invoked with does.

The second half was missing. A tiling pattern used inside a form drawn at
``1 0 0 1 5 5 cm`` laid its tiles from the *page's* origin and then clipped them
to the form's box, so the first column of every row came out five units narrow
and the rest sat five units off. MuPDF and pdfium both place the tiles from the
form's origin and agree with each other to the pixel; we now agree with both.
Shading patterns are anchored the same way and were wrong the same way.
"""

from __future__ import annotations

import io

from aspose_pdf import Document

SIDE = 120
_WHITE = (255, 255, 255)
#: A cell whose lower-left 10x10 quarter is black, stepped every 20 units.
_CELL = b"0 g 0 0 10 10 re f"


def _pdf(content: bytes, objects: dict[int, bytes], resources: str) -> bytes:
    objs = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {SIDE} {SIDE}]"
            f" /Resources << {resources} >> /Contents 4 0 R >>").encode(),
        4: b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n"
           + content + b"\nendstream",
    }
    objs.update(objects)
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for number in sorted(objs):
        offsets[number] = len(out)
        out += f"{number} 0 obj\n".encode() + objs[number] + b"\nendobj\n"
    start = len(out)
    out += b"xref\n0 1\n0000000000 65535 f \n"
    for number in sorted(objs):
        out += f"{number} 1\n".encode() + f"{offsets[number]:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {max(objs) + 1} /Root 1 0 R >>\n"
            f"startxref\n{start}\n%%EOF\n").encode()
    return bytes(out)


def _tiling(cell: bytes = _CELL, *, matrix: str | None = None) -> bytes:
    mapping = (b"<< /Type /Pattern /PatternType 1 /PaintType 1 /TilingType 1"
               b" /BBox [0 0 20 20] /XStep 20 /YStep 20")
    if matrix:
        mapping += f" /Matrix [{matrix}]".encode()
    mapping += b" /Resources << >> /Length " + str(len(cell)).encode() + b" >>"
    return mapping + b"\nstream\n" + cell + b"\nendstream"


def _form(body: bytes, *, matrix: str | None = None, resources: bytes = b"<< >>") -> bytes:
    mapping = (b"<< /Type /XObject /Subtype /Form /BBox [0 0 120 120]")
    if matrix:
        mapping += f" /Matrix [{matrix}]".encode()
    mapping += (b" /Resources " + resources + b" /Length "
                + str(len(body)).encode() + b" >>")
    return mapping + b"\nstream\n" + body + b"\nendstream"


_FILL_ALL = f"/Pattern cs /P0 scn 0 0 {SIDE} {SIDE} re f".encode()


def _ink_columns(data: bytes, row: int) -> list[int]:
    """The device columns of *row* that carry ink."""
    document = Document()
    document.load_from(io.BytesIO(data))
    try:
        raster = document.pages[0].render(dpi=72, antialias=False)
        return [x for x in range(SIDE) if raster.get_pixel(x, row) != _WHITE]
    finally:
        document.dispose()


def _runs(columns: list[int]) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for x in columns:
        if out and x == out[-1][1] + 1:
            out[-1][1] = x
        else:
            out.append([x, x])
    return [tuple(run) for run in out]


#: The cell's black square occupies the bottom 10 units of each 20-unit tile,
#: which on a 120-unit page is device rows 110..119, 90..99, and so on.
_INKED_ROW = 115


# --- on the page ------------------------------------------------------------


def test_tiles_start_at_the_page_origin():
    data = _pdf(_FILL_ALL, {10: _tiling()}, "/Pattern << /P0 10 0 R >>")
    assert _runs(_ink_columns(data, _INKED_ROW)) == [
        (0, 9), (20, 29), (40, 49), (60, 69), (80, 89), (100, 109)
    ]


def test_the_ctm_at_the_fill_does_not_move_the_tiles():
    data = _pdf(
        b"q 1 0 0 1 7 7 cm " + _FILL_ALL + b" Q",
        {10: _tiling()},
        "/Pattern << /P0 10 0 R >>",
    )
    # The fill's own edges move with the CTM -- its left edge is at 7 now, which
    # clips the first tile -- while every tile after it stays where the page
    # anchors it.
    assert _runs(_ink_columns(data, 95))[:4] == [
        (7, 9), (20, 29), (40, 49), (60, 69)
    ]


def test_the_pattern_matrix_does_move_the_tiles():
    data = _pdf(_FILL_ALL, {10: _tiling(matrix="1 0 0 1 5 5")},
                "/Pattern << /P0 10 0 R >>")
    assert _runs(_ink_columns(data, _INKED_ROW - 5))[0] == (5, 14)


# --- inside a form XObject --------------------------------------------------


def test_a_pattern_in_a_translated_form_is_anchored_to_the_form():
    data = _pdf(
        b"q 1 0 0 1 5 5 cm /Fx0 Do Q",
        {10: _tiling(),
         11: _form(_FILL_ALL, resources=b"<< /Pattern << /P0 10 0 R >> >>")},
        "/XObject << /Fx0 11 0 R >>",
    )
    # Tiles from the form's origin: 5..14, 25..34, ... and the row the cell
    # paints moves up with the form too.
    assert _runs(_ink_columns(data, _INKED_ROW - 5))[:3] == [
        (5, 14), (25, 34), (45, 54)
    ]


def test_a_pattern_in_a_form_with_its_own_matrix_follows_that_matrix():
    data = _pdf(
        b"/Fx0 Do",
        {10: _tiling(),
         11: _form(_FILL_ALL, matrix="1 0 0 1 8 0",
                   resources=b"<< /Pattern << /P0 10 0 R >> >>")},
        "/XObject << /Fx0 11 0 R >>",
    )
    assert _runs(_ink_columns(data, _INKED_ROW))[:2] == [(8, 17), (28, 37)]


def test_a_pattern_in_an_untranslated_form_matches_the_page_case():
    plain = _pdf(_FILL_ALL, {10: _tiling()}, "/Pattern << /P0 10 0 R >>")
    in_form = _pdf(
        b"/Fx0 Do",
        {10: _tiling(),
         11: _form(_FILL_ALL, resources=b"<< /Pattern << /P0 10 0 R >> >>")},
        "/XObject << /Fx0 11 0 R >>",
    )
    assert _ink_columns(in_form, _INKED_ROW) == _ink_columns(plain, _INKED_ROW)


def test_a_shading_pattern_in_a_form_moves_with_it():
    shading = (
        b"<< /Type /Pattern /PatternType 2 /Shading << /ShadingType 2"
        b" /ColorSpace /DeviceGray /Coords [0 0 40 0] /Function"
        b" << /FunctionType 2 /Domain [0 1] /C0 [0] /C1 [1] /N 1 >>"
        b" /Extend [true true] >> >>"
    )
    body = f"/Pattern cs /P0 scn 0 0 {SIDE} {SIDE} re f".encode()

    def grey_at(shift: int, x: int) -> int:
        data = _pdf(
            f"q 1 0 0 1 {shift} 0 cm /Fx0 Do Q".encode(),
            {10: shading,
             11: _form(body, resources=b"<< /Pattern << /P0 10 0 R >> >>")},
            "/XObject << /Fx0 11 0 R >>",
        )
        document = Document()
        document.load_from(io.BytesIO(data))
        try:
            raster = document.pages[0].render(dpi=72, antialias=False)
            return raster.get_pixel(x, 60)[0]
        finally:
            document.dispose()

    # The gradient runs black to white over x 0..40 in the form's space, so
    # shifting the form 40 units right moves the whole ramp 40 units right: the
    # greys read at 5, 20 and 39 come back at 45, 60 and 79.
    for near, far in ((5, 45), (20, 60), (39, 79)):
        assert grey_at(0, near) == grey_at(40, far), (near, far)
    # And the ramp really is a ramp, not a flat fill.
    assert grey_at(0, 5) < grey_at(0, 20) < grey_at(0, 39)
    # Before the shifted form begins there is nothing of it to see.
    assert grey_at(40, 20) == 255


# --- the order the two matrices compose in ----------------------------------


def test_a_translated_form_with_a_scaling_pattern_matrix():
    """The form's matrix applies *after* the pattern's, not before.

    Two translations commute, so a translated form and a translated pattern
    matrix cannot tell the two orders apart. A **scaling** pattern matrix can:
    composed one way the form's 20-unit shift stays 20, the other way the scale
    doubles it to 40. MuPDF and pdfium both answer 20, giving tiles at 20..39,
    60..79 and 100..119.
    """
    data = _pdf(
        b"q 1 0 0 1 20 0 cm /Fx0 Do Q",
        {10: _tiling(matrix="2 0 0 2 0 0"),
         11: _form(_FILL_ALL, resources=b"<< /Pattern << /P0 10 0 R >> >>")},
        "/XObject << /Fx0 11 0 R >>",
    )
    assert _runs(_ink_columns(data, 110)) == [(20, 39), (60, 79), (100, 119)]


# --- the base is given back --------------------------------------------------


def test_a_pattern_on_the_page_after_a_form_is_anchored_to_the_page():
    # The form is kept clear of the row that is checked, so what is read there
    # is the page's own fill and nothing of the form's.
    form = (b"<< /Type /XObject /Subtype /Form /BBox [0 0 60 60]"
            b" /Resources << /Pattern << /P0 10 0 R >> >> /Length "
            + str(len(_FILL_ALL)).encode() + b" >>\nstream\n" + _FILL_ALL
            + b"\nendstream")
    # The form's shift is 7, which the pattern's 20-unit step does not divide:
    # a shift the step *does* divide would leave the tiles in the same places
    # and the page fill could not tell whether the base had been given back.
    data = _pdf(
        b"q 1 0 0 1 7 70 cm /Fx0 Do Q\n/Pattern cs /P0 scn 0 0 120 40 re f",
        {10: _tiling(), 11: form},
        "/XObject << /Fx0 11 0 R >> /Pattern << /P0 10 0 R >>",
    )
    assert _runs(_ink_columns(data, _INKED_ROW)) == [
        (0, 9), (20, 29), (40, 49), (60, 69), (80, 89), (100, 109)
    ]


def test_two_pattern_fills_in_one_stream_line_up_with_each_other():
    data = _pdf(
        b"/Pattern cs /P0 scn 0 0 120 40 re f 0 80 120 40 re f",
        {10: _tiling()},
        "/Pattern << /P0 10 0 R >>",
    )
    lower = _runs(_ink_columns(data, _INKED_ROW))
    upper = _runs(_ink_columns(data, _INKED_ROW - 80))
    assert lower == upper == [
        (0, 9), (20, 29), (40, 49), (60, 69), (80, 89), (100, 109)
    ]


def test_a_pattern_inside_a_cell_steps_from_the_cell_not_the_page():
    # The outer cell sits at 3..23 of every 40, and the inner pattern's 5-unit
    # squares step by 10 *within it*: 3..7 and 13..17, then the cell repeats.
    # Anchored to the page instead they would start at 0 and be clipped to 3..7
    # and 10..17 -- a different answer, and one neither reference gives.
    inner = _tiling(b"0 g 0 0 5 5 re f")
    inner = inner.replace(b"/XStep 20 /YStep 20", b"/XStep 10 /YStep 10")
    cell = b"/Pattern cs /P1 scn 0 0 20 20 re f"
    outer = (b"<< /Type /Pattern /PatternType 1 /PaintType 1 /TilingType 1"
             b" /BBox [0 0 20 20] /XStep 40 /YStep 40 /Matrix [1 0 0 1 3 3]"
             b" /Resources << /Pattern << /P1 12 0 R >> >> /Length "
             + str(len(cell)).encode() + b" >>\nstream\n" + cell + b"\nendstream")
    data = _pdf(_FILL_ALL, {10: outer, 12: inner}, "/Pattern << /P0 10 0 R >>")
    assert _runs(_ink_columns(data, 116))[:6] == [
        (3, 7), (13, 17), (43, 47), (53, 57), (83, 87), (93, 97)
    ]


def test_a_pattern_after_a_nested_pattern_is_anchored_to_the_page():
    # The inner fill leaves the base at one of the outer pattern's tiles; the
    # fill that follows it on the page must not inherit that.
    inner = _tiling(b"0 g 0 0 5 5 re f")
    cell = b"/Pattern cs /P1 scn 0 0 20 20 re f"
    outer = (b"<< /Type /Pattern /PatternType 1 /PaintType 1 /TilingType 1"
             b" /BBox [0 0 20 20] /XStep 40 /YStep 40 /Matrix [1 0 0 1 3 3]"
             b" /Resources << /Pattern << /P1 12 0 R >> >> /Length "
             + str(len(cell)).encode() + b" >>\nstream\n" + cell + b"\nendstream")
    data = _pdf(
        b"/Pattern cs /P0 scn 0 0 120 50 re f\n/Pattern cs /P2 scn 0 70 120 50 re f",
        {10: outer, 12: inner, 13: _tiling()},
        "/Pattern << /P0 10 0 R /P2 13 0 R >>",
    )
    # The second fill's tiles start at the page origin, 20 apart.
    assert _runs(_ink_columns(data, 10))[:3] == [(0, 9), (20, 29), (40, 49)]
