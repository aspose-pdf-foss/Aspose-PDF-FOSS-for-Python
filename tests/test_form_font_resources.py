"""A form XObject's ``/F1`` is its own font, not the page's.

The renderer cached outline fonts by resource *name*, so when a page and a
form it draws both call a font ``/F1`` -- as nearly every generator names its
first font -- whichever was drawn first drew both. GraphicsAbsorber kept a
second name-keyed cache on top, so text boxes were measured in the wrong font
as well. pdfium and MuPDF draw each run in its own font.
"""

from __future__ import annotations

import io

from aspose_pdf import Document
from aspose_pdf.graphics import GraphicsAbsorber

_HELVETICA = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
_COURIER = b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>"


def _stream(body: bytes, dictionary: bytes = b"") -> bytes:
    return b"<< %s /Length %d >>\nstream\n" % (dictionary, len(body)) + body + b"\nendstream"


def _document(page_resources: bytes, content: bytes, form: bytes | None = None) -> Document:
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << "
        + page_resources
        + b" >> /Contents 4 0 R >>",
        4: _stream(content),
        10: _HELVETICA,
        11: _COURIER,
    }
    if form is not None:
        objects[5] = form
    raw = bytearray(b"%PDF-1.7\n")
    offsets = {}
    for number in sorted(objects):
        offsets[number] = len(raw)
        raw += b"%d 0 obj\n" % number + objects[number] + b"\nendobj\n"
    start = len(raw)
    size = max(objects) + 1
    raw += b"xref\n0 %d\n" % size
    for number in range(size):
        raw += (b"%010d 00000 n \n" % offsets[number]) if number in offsets else b"0000000000 65535 f \n"
    raw += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, start)
    return Document(io.BytesIO(bytes(raw)))


_PAGE_TEXT = b"BT /F1 24 Tf 10 150 Td (Wide WWW iii) Tj ET"
_FORM = _stream(
    b"BT /F1 24 Tf 10 60 Td (Wide WWW iii) Tj ET",
    b"/Type /XObject /Subtype /Form /BBox [0 0 300 200] /Resources << /Font << /F1 11 0 R >> >>",
)
_SHARED_NAME = b"/Font << /F1 10 0 R >> /XObject << /Fm 5 0 R >>"


def _pixels(document: Document) -> list:
    raster = document.pages[0].render(dpi=72, antialias=False)
    return [raster.get_pixel(x, y) for y in range(200) for x in range(300)]


def _distinct_names() -> Document:
    return _document(
        b"/Font << /F1 10 0 R /F2 11 0 R >>",
        _PAGE_TEXT + b" BT /F2 24 Tf 10 60 Td (Wide WWW iii) Tj ET",
    )


def test_a_form_s_f1_is_drawn_in_its_own_font_after_the_page_s():
    shared = _document(_SHARED_NAME, _PAGE_TEXT + b" /Fm Do", _FORM)
    assert _pixels(shared) == _pixels(_distinct_names())


def test_the_page_s_f1_is_drawn_in_its_own_font_after_a_form_s():
    shared = _document(_SHARED_NAME, b"/Fm Do " + _PAGE_TEXT, _FORM)
    assert _pixels(shared) == _pixels(_distinct_names())


def test_graphics_absorber_measures_each_run_in_its_own_font():
    # 12 glyphs at 24 pt: Courier is 0.6 em each, Helvetica's run is 152 pt.
    for content in (_PAGE_TEXT + b" /Fm Do", b"/Fm Do " + _PAGE_TEXT):
        absorber = GraphicsAbsorber()
        absorber.visit(_document(_SHARED_NAME, content, _FORM).pages[0])
        widths = {round(e.rectangle.y): round(e.rectangle.width, 1) for e in absorber.elements if e.kind == "text"}
        assert widths == {144: 152.0, 54: 172.8}, content
