"""Type 3 fonts are drawn from their glyph procedures.

A Type 3 font (ISO 32000-1 9.6.5) has no font program: each glyph is a content
stream in ``/CharProcs``, run under ``FontMatrix * text space * Tm * CTM``. The
renderer did not know the font type at all and drew a placeholder box per
character -- every label of a matplotlib chart, whose PDF backend writes Type 3
fonts by default, came out as black boxes.

Two neighbouring rules came with it. A glyph described with ``d1`` is a shape
whose own colour operators are ignored. And invisible text (``Tr 3``) takes up
room like any other: it used to return before advancing, so a visible word
after an invisible one was drawn on top of it -- for every font, not only
Type 3. pdfium and MuPDF agree with every expectation here.
"""

from __future__ import annotations

import io

from aspose_pdf import Document

_SQUARE = b"1000 0 0 0 1000 1000 d1 100 100 800 800 re f"
_TRIANGLE = b"1000 0 0 0 1000 1000 d1 100 100 m 900 100 l 500 900 l f"
_WHITE = (255, 255, 255)
_RED = (255, 0, 0)
_BLUE = (0, 0, 255)


def _stream(body: bytes, dictionary: bytes = b"") -> bytes:
    return b"<< %s /Length %d >>\nstream\n%s\nendstream" % (dictionary, len(body), body)


def _document(content: bytes, procs: dict[str, bytes], *, font_matrix=b"[0.001 0 0 0.001 0 0]",
              widths=b"[1000 1000 1000]", font_extra=b"", extra=None, second_font=b"") -> Document:
    names = list(procs)
    entries = b" ".join(b"/%s %d 0 R" % (n.encode(), 11 + i) for i, n in enumerate(names))
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        3: (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources "
            b"<< /Font << /F1 10 0 R " + second_font + b">> >> /Contents 4 0 R >>"
        ),
        4: _stream(content),
        10: (
            b"<< /Type /Font /Subtype /Type3 /FontBBox [0 0 1000 1000] /FontMatrix "
            + font_matrix
            + b" /CharProcs << " + entries + b" >> /Encoding << /Differences [65 "
            + b" ".join(b"/" + n.encode() for n in names)
            + b"] >> /FirstChar 65 /LastChar 67 /Widths " + widths + font_extra + b" >>"
        ),
    }
    for index, body in enumerate(procs.values()):
        objects[11 + index] = _stream(body)
    objects.update(extra or {})
    size = max(objects) + 1
    raw = bytearray(b"%PDF-1.7\n")
    offsets = {}
    for number in sorted(objects):
        offsets[number] = len(raw)
        raw += b"%d 0 obj\n" % number + objects[number] + b"\nendobj\n"
    start = len(raw)
    raw += b"xref\n0 %d\n" % size
    for number in range(size):
        raw += (b"%010d 00000 n \n" % offsets[number]) if number in offsets else b"0000000000 65535 f \n"
    raw += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, start)
    return Document(io.BytesIO(bytes(raw)))


def _pixels(document: Document):
    raster = document.pages[0].render(dpi=72, antialias=False)
    return lambda x, y: raster.get_pixel(x, y)


def _all(document: Document) -> list:
    raster = document.pages[0].render(dpi=72, antialias=False)
    return [raster.get_pixel(x, y) for y in range(200) for x in range(200)]


# --- glyphs are drawn from their procedures ----------------------------------


def test_a_glyph_is_its_procedure_not_a_box():
    # A triangle leaves the top corners of its cell empty; the placeholder box
    # that used to stand in for it filled them.
    pixel = _pixels(_document(b"BT 1 0 0 rg /F1 100 Tf 50 50 Td (A) Tj ET", {"a": _TRIANGLE}))
    assert pixel(100, 100) == _RED  # the middle of the triangle
    assert pixel(64, 64) == _WHITE  # its top-left corner, device y downward


def test_a_d1_glyph_is_painted_in_the_text_colour():
    blue_inside = b"1000 0 0 0 1000 1000 d1 0 0 1 rg 100 100 800 800 re f"
    pixel = _pixels(_document(b"BT 1 0 0 rg /F1 100 Tf 50 50 Td (A) Tj ET", {"a": blue_inside}))
    assert pixel(100, 100) == _RED


def test_a_d0_glyph_keeps_its_own_colour():
    blue_inside = b"1000 0 d0 0 0 1 rg 100 100 800 800 re f"
    pixel = _pixels(_document(b"BT 1 0 0 rg /F1 100 Tf 50 50 Td (A) Tj ET", {"a": blue_inside}))
    assert pixel(100, 100) == _BLUE


def test_the_text_colour_outlives_a_d0_glyph_that_changed_it():
    # A procedure's colour is its own business: the next glyph starts again
    # from the colour the text was shown in.
    procs = {"a": b"1000 0 d0 0 0 1 rg 100 100 800 800 re f", "b": _SQUARE}
    pixel = _pixels(_document(b"BT 1 0 0 rg /F1 50 Tf 20 50 Td (AB) Tj ET", procs))
    assert pixel(45, 125) == _BLUE  # inside A: x 25..60, y 55..90 in user space
    assert pixel(95, 125) == _RED  # inside B, one 50-unit advance later


# --- where glyphs go ---------------------------------------------------------------


def test_each_glyph_advances_by_its_width():
    shown = _document(b"BT 0 0 0 rg /F1 40 Tf 10 80 Td (ABA) Tj ET", {"a": _SQUARE, "b": _TRIANGLE},
                      widths=b"[500 1500 1000]")
    placed = _document(
        b"BT 0 0 0 rg /F1 40 Tf 1 0 0 1 10 80 Tm (A) Tj 1 0 0 1 30 80 Tm (B) Tj "
        b"1 0 0 1 90 80 Tm (A) Tj ET",
        {"a": _SQUARE, "b": _TRIANGLE},
        widths=b"[500 1500 1000]",
    )
    assert _all(shown) == _all(placed)


def test_kerning_and_character_spacing_apply_to_type3_text():
    # A 30 + Tc 5 = 35, then -600 kerning moves on 18: B starts at 10+35+18.
    kerned = _document(b"BT 0 0 1 rg /F1 30 Tf 5 Tc 10 80 Td [(A) -600 (B)] TJ ET",
                       {"a": _SQUARE, "b": _TRIANGLE})
    placed = _document(
        b"BT 0 0 1 rg /F1 30 Tf 5 Tc 1 0 0 1 10 80 Tm (A) Tj 1 0 0 1 63 80 Tm (B) Tj ET",
        {"a": _SQUARE, "b": _TRIANGLE},
    )
    assert _all(kerned) == _all(placed)


def test_a_flipped_font_matrix_draws_the_glyph_upside_down():
    upright = _pixels(_document(b"BT 0 0 0 rg /F1 100 Tf 50 50 Td (A) Tj ET", {"a": _TRIANGLE}))
    flipped = _pixels(
        _document(b"BT 0 0 0 rg /F1 100 Tf 50 150 Td (A) Tj ET", {"a": _TRIANGLE},
                  font_matrix=b"[0.001 0 0 -0.001 0 0]")
    )
    # The apex region: near the top for the upright glyph, the bottom when flipped.
    assert upright(100, 62) != _WHITE and upright(100, 138) != _WHITE
    assert flipped(100, 62) != _WHITE
    assert flipped(64, 64) != _WHITE  # the flipped base's corner is now at the top
    assert upright(64, 64) == _WHITE


def test_a_glyph_uses_the_font_s_own_resources():
    image = _stream(bytes([255, 0, 0] * 4),
                    b"/Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace /DeviceRGB /BitsPerComponent 8")
    pixel = _pixels(
        _document(b"BT 0 0 0 rg /F1 100 Tf 50 50 Td (A) Tj ET",
                  {"a": b"1000 0 d0 q 800 0 0 800 100 100 cm /Im Do Q"},
                  font_extra=b" /Resources << /XObject << /Im 20 0 R >> >>", extra={20: image})
    )
    assert pixel(100, 100) == _RED


def test_a_glyph_may_show_text_in_another_type3_font():
    nested = (
        b"<< /Type /Font /Subtype /Type3 /FontBBox [0 0 1000 1000] /FontMatrix [0.001 0 0 0.001 0 0] "
        b"/CharProcs << /b 31 0 R >> /Encoding << /Differences [66 /b] >> /FirstChar 66 /LastChar 66 "
        b"/Widths [1000] >>"
    )
    pixel = _pixels(
        _document(b"BT 1 0 0 rg /F1 100 Tf 50 50 Td (A) Tj ET",
                  {"a": b"1000 0 d0 BT /G1 1000 Tf 0 0 Td (B) Tj ET"},
                  font_extra=b" /Resources << /Font << /G1 30 0 R >> >>",
                  extra={30: nested, 31: _stream(_SQUARE)})
    )
    assert pixel(100, 100) == _RED


def test_a_glyph_that_shows_itself_stops():
    document = _document(b"BT 0 0 0 rg /F1 100 Tf 50 50 Td (A) Tj ET",
                         {"a": b"1000 0 d0 0 0 500 500 re f BT /F1 1000 Tf 0 0 Td (A) Tj ET"},
                         font_extra=b" /Resources << /Font << /F1 10 0 R >> >>")
    assert any(p != _WHITE for p in _all(document))


# --- invisible text still takes up room -------------------------------------------------


def test_invisible_type3_text_advances():
    hidden_first = _document(b"BT 0 0 0 rg /F1 40 Tf 10 80 Td 3 Tr (AA) Tj 0 Tr (B) Tj ET",
                             {"a": _SQUARE, "b": _TRIANGLE})
    placed = _document(b"BT 0 0 0 rg /F1 40 Tf 1 0 0 1 90 80 Tm (B) Tj ET",
                       {"a": _SQUARE, "b": _TRIANGLE})
    assert _all(hidden_first) == _all(placed)


def test_invisible_text_in_an_ordinary_font_advances():
    helvetica = b"/F2 40 0 R "
    extra = {40: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"}
    hidden_first = _document(b"BT 0 0 0 rg /F2 30 Tf 10 90 Td 3 Tr (HIDDEN) Tj 0 Tr (SEEN) Tj ET",
                             {"a": _SQUARE}, extra=extra, second_font=helvetica)
    alone = _document(b"BT 0 0 0 rg /F2 30 Tf 10 90 Td (SEEN) Tj ET",
                      {"a": _SQUARE}, extra=extra, second_font=helvetica)
    # Drawn after the hidden word, SEEN is not where it would be on its own.
    assert _all(hidden_first) != _all(alone)
    hidden_pixels = _all(hidden_first)
    assert all(hidden_pixels[y * 200 + x] == _WHITE for y in range(200) for x in range(0, 60))


def test_colour_set_after_a_d1_glyph_is_honoured():
    # The lock that ignores a d1 glyph's colour operators ends with the glyph.
    pixel = _pixels(
        _document(b"BT 1 0 0 rg /F1 50 Tf 20 20 Td (A) Tj ET 0 0 1 rg 120 120 60 60 re f",
                  {"a": _SQUARE})
    )
    assert pixel(150, 50) == _BLUE
