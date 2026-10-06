"""What goes between two shown strings: a line break, a space, or nothing.

Extraction decided this from the baseline alone -- different ``y`` meant a new
line, the same ``y`` with a move before it meant a space -- which left two runs
glued when they should not have been:

* **Text from a form XObject ran into the page's own text with no separator at
  all.** A form's text is placed by a matrix of its own, so after reading one
  the extractor set "nothing has been shown yet"; the next string on the page
  then got no separator, and ``inside a form`` + ``on the page`` came out as
  ``inside a formon the page``.
* **A move that did not advance along the line produced a space.** Two strings
  at the same position are stacked, not side by side, so ``under`` over ``OVER``
  read as ``under OVER``; ``T*`` with the default zero leading, and a plain
  ``0 0 Td``, did the same.

Measured against MuPDF (asked for draw order, not its own reading order) and
poppler on twenty hand-built layouts: both now agree with MuPDF on sixteen of
them and with ``pdftotext -layout`` on the rest. See
``supported-features.md`` for the one divergence that remains -- the *size* of a
horizontal gap, where plain ``pdftotext`` and MuPDF start a new line and we keep
one line, as ``pdftotext -layout`` does.
"""

from __future__ import annotations

import io

from aspose_pdf import Document

W = H = 400


def _document(content: str, *, extra_objects=(), resources_extra: str = "") -> Document:
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {W} {H}]"
            f" /Resources << /Font << /F1 5 0 R >> {resources_extra} >>"
            f" /Contents 4 0 R >>").encode(),
        5: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    body = content.encode()
    objects[4] = (
        b"<< /Length " + str(len(body)).encode() + b" >>\nstream\n" + body + b"\nendstream"
    )
    for number, value in extra_objects:
        objects[number] = value
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += f"{number} 0 obj\n".encode() + objects[number] + b"\nendobj\n"
    start = len(out)
    out += b"xref\n0 1\n0000000000 65535 f \n"
    for number in sorted(objects):
        out += f"{number} 1\n".encode() + f"{offsets[number]:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {max(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{start}\n%%EOF\n").encode()
    document = Document()
    document.load_from(io.BytesIO(bytes(out)))
    return document


def _text(content: str, **kwargs) -> str:
    document = _document(content, **kwargs)
    try:
        return document.pages[0].extract_text()
    finally:
        document.dispose()


def _line(text: str, x: float, y: float, size: float = 14) -> str:
    return f"BT /F1 {size} Tf 0 g 1 0 0 1 {x:g} {y:g} Tm ({text}) Tj ET\n"


def _form(body: bytes) -> tuple[int, bytes]:
    return (
        7,
        b"<< /Type /XObject /Subtype /Form /BBox [0 0 200 40]"
        b" /Resources << /Font << /F1 5 0 R >> >> /Length "
        + str(len(body)).encode() + b" >>\nstream\n" + body + b"\nendstream",
    )


_FORM_TEXT = b"BT /F1 14 Tf 0 g 1 0 0 1 0 0 Tm (inside a form) Tj ET"


# --- a form XObject's text is on a line of its own --------------------------


def test_a_form_drawn_before_the_page_text_is_separated_from_it():
    text = _text(
        "q 1 0 0 1 60 200 cm /Fx0 Do Q\n" + _line("on the page", 40, 300),
        resources_extra="/XObject << /Fx0 7 0 R >>",
        extra_objects=[_form(_FORM_TEXT)],
    )
    assert text == "inside a form\non the page"


def test_a_form_drawn_after_the_page_text_is_separated_from_it():
    text = _text(
        _line("on the page", 40, 300) + "q 1 0 0 1 60 200 cm /Fx0 Do Q\n",
        resources_extra="/XObject << /Fx0 7 0 R >>",
        extra_objects=[_form(_FORM_TEXT)],
    )
    assert text == "on the page\ninside a form"


def test_a_form_between_two_page_strings_separates_both_sides():
    text = _text(
        _line("before", 40, 340)
        + "q 1 0 0 1 60 200 cm /Fx0 Do Q\n"
        + _line("after", 40, 300),
        resources_extra="/XObject << /Fx0 7 0 R >>",
        extra_objects=[_form(_FORM_TEXT)],
    )
    assert text == "before\ninside a form\nafter"


def test_a_form_at_the_same_baseline_is_still_its_own_line():
    # The form's own text matrix puts its text at y 0 in *its* space; what the
    # page did last says nothing about where that lands.
    text = _text(
        _line("on the page", 40, 300) + "q 1 0 0 1 60 300 cm /Fx0 Do Q\n",
        resources_extra="/XObject << /Fx0 7 0 R >>",
        extra_objects=[_form(_FORM_TEXT)],
    )
    assert text == "on the page\ninside a form"


def test_an_empty_form_adds_no_separator():
    blank = b"q 1 0 0 1 0 0 cm Q"
    text = _text(
        _line("before", 40, 340) + "q 1 0 0 1 60 200 cm /Fx0 Do Q\n"
        + _line("after", 40, 300),
        resources_extra="/XObject << /Fx0 7 0 R >>",
        extra_objects=[_form(blank)],
    )
    assert text == "before\nafter"


# --- a move that does not advance along the line ----------------------------


def test_two_strings_at_the_same_place_are_not_two_words():
    assert _text(_line("under", 40, 300) + _line("OVER", 40, 300)) == "under\nOVER"


def test_a_move_back_along_the_line_starts_a_new_line():
    assert _text(_line("second", 40, 300) + _line("first", 20, 300)) == "second\nfirst"


def test_t_star_with_no_leading_does_not_separate_words():
    text = _text("BT /F1 14 Tf 0 g 1 0 0 1 40 300 Tm (one) Tj T* (two) Tj ET\n")
    assert text == "one\ntwo"


def test_a_zero_td_does_not_separate_words():
    text = _text("BT /F1 14 Tf 0 g 1 0 0 1 40 300 Tm (one) Tj 0 0 Td (two) Tj ET\n")
    assert text == "one\ntwo"


def test_a_move_forward_along_the_line_is_still_a_space():
    text = _text(
        "BT /F1 14 Tf 0 g 1 0 0 1 40 300 Tm (Hello) Tj"
        " 1 0 0 1 80 300 Tm (world) Tj ET\n"
    )
    assert text == "Hello world"


def test_td_offsets_the_line_matrix_and_so_accumulates():
    # ISO 32000-1 9.4.2: `Td` offsets the *current line* matrix, so successive
    # `Td 30 0`s walk along -- 40, 70, 100 -- rather than landing in one place.
    # MuPDF and poppler both read this as three words on one line.
    text = _text(
        "BT /F1 14 Tf 0 g 1 0 0 1 40 300 Tm (one) Tj 30 0 Td (two) Tj 30 0 Td (three) Tj ET\n"
    )
    assert text == "one two three"


def test_a_new_baseline_is_still_a_new_line():
    assert _text(_line("first", 40, 324) + _line("second", 40, 300)) == "first\nsecond"


def test_a_backward_move_on_a_new_baseline_is_one_break_not_two():
    assert _text(_line("first", 240, 324) + _line("second", 40, 300)) == "first\nsecond"


def test_bt_starts_the_text_matrix_at_the_identity():
    # ISO 32000-1 9.4.1: BT sets both the text and the text *line* matrix to the
    # identity, so the horizontal position a previous text object left behind is
    # not where the next `Td` counts from. Leaving it there put the second
    # string to the *right* of the first instead of to its left, and so read the
    # two as one line. MuPDF answers `right\nleft` here.
    text = _text(
        "BT /F1 14 Tf 0 g 1 0 0 1 200 300 Tm (right) Tj ET\n"
        "BT /F1 14 Tf 0 g 100 300 Td (left) Tj ET\n"
    )
    assert text == "right\nleft"
