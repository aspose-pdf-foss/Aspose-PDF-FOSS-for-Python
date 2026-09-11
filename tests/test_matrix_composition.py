"""Transforms compose in the order ISO 32000-1 writes them.

``cm`` is ``CTM' = M * CTM`` (8.4.4), ``Td`` is ``Tlm = [1 0 0 1 tx ty] * Tlm``
(9.4.2), a glyph advance is ``Tm = [1 0 0 1 tx 0] * Tm`` (9.4.4), and a form is
painted under ``Matrix * CTM`` (8.10.1): in each, the newer matrix applies
*first*. The renderer applied it last. Nothing showed while the two transforms
commuted -- a single ``cm``, text with an unscaled ``Tm`` -- and everything
went wrong when they did not: a ``cm`` inside a scaled ``cm`` landed off the
page, a word set with ``20 0 0 20 ... Tm /F1 1 Tf`` piled its letters into one
place, and a form with a scaling or rotating ``/Matrix`` vanished. The image
placement scanner had the same inversion in its own helper.

Each test states the rule as an equivalence the specification guarantees --
two ways of writing the same transform must render the same pixels -- or as a
position worked out by hand. pdfium and MuPDF agree with every expectation.
"""

from __future__ import annotations

import io
import zlib
from decimal import Decimal

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.pdf_matrix import multiply_pdf_affine
from aspose_pdf.engine.rasterizer import _concat, _transform_point
from aspose_pdf.images import ImagePlacementAbsorber

_IMAGE = zlib.compress(bytes([200, 30, 30] * 4))  # 2x2, one flat colour


def _document(content: bytes, form: bytes = b"", form_body: bytes = b"") -> Document:
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        3: (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Resources "
            b"<< /Font << /F1 5 0 R >> /XObject << /Fm 6 0 R /Im 7 0 R >> >> "
            b"/Contents 4 0 R >>"
        ),
        4: b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
        5: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        6: (
            b"<< /Type /XObject /Subtype /Form /BBox [0 0 200 100] %s "
            b"/Resources << /Font << /F1 5 0 R >> >> /Length %d >>\nstream\n%s\nendstream"
            % (form, len(form_body), form_body)
        ),
        7: (
            b"<< /Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace "
            b"/DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode /Length %d >>\n"
            b"stream\n" % len(_IMAGE)
            + _IMAGE
            + b"\nendstream"
        ),
    }
    raw = bytearray(b"%PDF-1.7\n")
    offsets = {}
    for number in sorted(objects):
        offsets[number] = len(raw)
        raw += b"%d 0 obj\n" % number + objects[number] + b"\nendobj\n"
    start = len(raw)
    raw += b"xref\n0 8\n0000000000 65535 f \n"
    raw += b"".join(b"%010d 00000 n \n" % offsets[n] for n in sorted(objects))
    raw += b"trailer\n<< /Size 8 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % start
    return Document(io.BytesIO(bytes(raw)))


def _ink_box(content: bytes, **form) -> tuple[int, int, int, int] | None:
    """The device-space box of everything painted, at 72 dpi (1 px = 1 pt)."""
    raster = _document(content, **form).pages[0].render(dpi=72, antialias=False)
    painted = [
        (x, y)
        for y in range(300)
        for x in range(400)
        if raster.get_pixel(x, y) != (255, 255, 255)
    ]
    if not painted:
        return None
    xs = [p[0] for p in painted]
    ys = [p[1] for p in painted]
    return min(xs), min(ys), max(xs), max(ys)


def _near(box, expected, slack: int = 2) -> bool:
    return box is not None and all(abs(a - b) <= slack for a, b in zip(box, expected))


_RECT = b"0 0 1 rg 10 5 60 20 re f"
_TRANSLATE = (1, 0, 0, 1, 200, 50)
_SCALE = (2, 0, 0, 2, 0, 0)


# --- the helpers, stated against the specification's own notation -----------


def test_concat_applies_its_first_matrix_first():
    # "M * CTM": scale first, then translate -- (10, 5) -> (20, 10) -> (220, 60).
    assert _transform_point(_concat(_SCALE, _TRANSLATE), 10, 5) == (220, 60)


def test_the_image_placement_product_applies_its_first_matrix_first():
    s = tuple(Decimal(v) for v in _SCALE)
    t = tuple(Decimal(v) for v in _TRANSLATE)
    m = multiply_pdf_affine(s, t)
    assert (m[0] * 10 + m[2] * 5 + m[4], m[1] * 10 + m[3] * 5 + m[5]) == (220, 60)


# --- cm ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "nested, combined",
    [
        pytest.param(b"1 0 0 1 100 50 cm 2 0 0 2 0 0 cm", b"2 0 0 2 100 50 cm", id="translate-then-scale"),
        pytest.param(b"2 0 0 2 0 0 cm 1 0 0 1 50 30 cm", b"2 0 0 2 100 60 cm", id="scale-then-translate"),
        pytest.param(b"1 0 0 1 200 50 cm 0 1 -1 0 0 0 cm", b"0 1 -1 0 200 50 cm", id="translate-then-rotate"),
    ],
)
def test_nested_cm_renders_exactly_like_the_product_it_stands_for(nested, combined):
    as_nested = _ink_box(b"q " + nested + b" " + _RECT + b" Q")
    as_one = _ink_box(b"q " + combined + b" " + _RECT + b" Q")
    assert as_one is not None
    assert as_nested == as_one


def test_an_image_inside_a_scaled_page_lands_where_the_scale_puts_it():
    # The shape Microsoft's print-to-PDF writes: everything under one scaling
    # cm, each image placed with its own cm inside it.
    box = _ink_box(b"q 0.5 0 0 0.5 0 0 cm q 200 0 0 100 300 200 cm /Im Do Q Q")
    # user (150, 100)-(250, 150); device y runs down from 300.
    assert _near(box, (150, 150, 249, 199))


def test_the_image_placement_absorber_reports_the_same_place():
    document = _document(b"q 0.5 0 0 0.5 0 0 cm q 200 0 0 100 300 200 cm /Im Do Q Q")
    absorber = ImagePlacementAbsorber()
    document.pages[0].accept(absorber)
    rect = absorber.image_placements[0].rectangle
    assert (rect.x, rect.y, rect.width, rect.height) == (150, 100, 100, 50)


# --- text ------------------------------------------------------------------------


def test_text_scaled_by_tm_is_set_like_text_scaled_by_its_size():
    by_matrix = _ink_box(b"BT 20 0 0 20 40 150 Tm /F1 1 Tf (HELLO) Tj ET")
    by_size = _ink_box(b"BT 1 0 0 1 40 150 Tm /F1 20 Tf (HELLO) Tj ET")
    assert by_size is not None
    assert _near(by_matrix, by_size, slack=1)


def test_kerning_under_a_scaled_tm_is_scaled_too():
    by_matrix = _ink_box(b"BT 15 0 0 15 40 150 Tm /F1 1 Tf [(A) -2000 (B) -2000 (C)] TJ ET")
    by_size = _ink_box(b"BT 1 0 0 1 40 150 Tm /F1 15 Tf [(A) -2000 (B) -2000 (C)] TJ ET")
    assert _near(by_matrix, by_size, slack=1)


def test_the_leading_under_a_scaled_tm_is_in_text_space():
    by_matrix = _ink_box(b"BT 12 0 0 12 40 250 Tm /F1 1 Tf 1.5 TL (AB) Tj T* (CD) Tj T* (EF) Tj ET")
    by_size = _ink_box(b"BT 1 0 0 1 40 250 Tm /F1 12 Tf 18 TL (AB) Tj T* (CD) Tj T* (EF) Tj ET")
    assert _near(by_matrix, by_size, slack=1)


def test_td_under_a_rotated_tm_moves_along_the_rotated_axes():
    # Rotated 90 degrees, "0 -30 Td" moves the next line 30 units to the
    # *right* on the page -- the same page as writing that line's Tm directly.
    with_td = _ink_box(b"BT 0 1 -1 0 200 40 Tm /F1 20 Tf (AB) Tj 0 -30 Td (CD) Tj ET")
    explicit = _ink_box(
        b"BT 0 1 -1 0 200 40 Tm /F1 20 Tf (AB) Tj 0 1 -1 0 230 40 Tm (CD) Tj ET"
    )
    assert _near(with_td, explicit, slack=1)


# --- a form's /Matrix -----------------------------------------------------------------


@pytest.mark.parametrize(
    "matrix, placed",
    [
        pytest.param(b"[2 0 0 2 0 0]", b"2 0 0 2 100 50 cm", id="scale"),
        pytest.param(b"[0 1 -1 0 0 0]", b"0 1 -1 0 100 50 cm", id="rotate"),
    ],
)
def test_a_form_s_matrix_applies_before_the_ctm_it_is_drawn_under(matrix, placed):
    via_form = _ink_box(b"q 1 0 0 1 100 50 cm /Fm Do Q", form=b"/Matrix " + matrix, form_body=_RECT)
    direct = _ink_box(b"q " + placed + b" " + _RECT + b" Q")
    assert direct is not None, "the direct drawing must land on the page"
    assert via_form == direct


def test_a_scaled_form_inside_a_scaled_page():
    box = _ink_box(
        b"q 0.5 0 0 0.5 0 0 cm q 1 0 0 1 300 100 cm /Fm Do Q Q",
        form=b"/Matrix [2 0 0 2 0 0]",
        form_body=_RECT,
    )
    # form (10, 5)-(70, 25) -> x2 -> +(300, 100) -> x0.5 = (160, 55)-(220, 75)
    assert _near(box, (160, 225, 219, 244))


def test_the_box_fallback_advances_in_text_space_too():
    # A font the page does not have is drawn as one box per glyph; the boxes
    # advance by the same rule as outlined glyphs.
    by_matrix = _ink_box(b"BT 20 0 0 20 40 150 Tm /Missing 1 Tf (HE LO) Tj ET")
    by_size = _ink_box(b"BT 1 0 0 1 40 150 Tm /Missing 20 Tf (HE LO) Tj ET")
    assert by_size is not None
    assert _near(by_matrix, by_size, slack=1)


def test_a_transparency_group_form_is_composited_where_it_lands():
    # A group is rendered offscreen into the device box its /Matrix and CTM
    # give it; composed the wrong way round, that box missed the drawing.
    group = _ink_box(
        b"q 1 0 0 1 100 50 cm /Fm Do Q",
        form=b"/Matrix [2 0 0 2 0 0] /Group << /S /Transparency >>",
        form_body=_RECT,
    )
    direct = _ink_box(b"q 2 0 0 2 100 50 cm " + _RECT + b" Q")
    assert group == direct
