"""A form that lists itself costs one edge, not the page.

A page's resource graph can point back at itself -- a form XObject whose own
``/Resources`` name that same form is the usual way it happens, and writers do
produce it. The graph is malformed, but it is *decoration hanging off a page*,
not the page.

Refusing the whole conversion meant the page lost its fonts, its images and its
text; through the renderer it meant `Page.render` and `Page.to_svg` **raised**,
so one bad edge in one form made the page undrawable. pdfminer and MuPDF both
drop the offending reference and carry on, and so do we now. A cyclic **page
tree** is still refused: there is no document behind that one.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import PdfName
from aspose_pdf.exceptions import PdfParseException

_FONT = (
    b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
)


def _document(page_content: bytes, form_body: bytes, form_resources: bytes) -> Document:
    form = (
        b"<< /Type /XObject /Subtype /Form /BBox [0 0 400 200] /Resources << "
        + form_resources
        + b">> /Length %d >>\nstream\n" % len(form_body)
        + form_body
        + b"\nendstream"
    )
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 200] /Resources << "
            b"/Font << /F1 5 0 R >> /XObject << /Fm1 6 0 R >> >> /Contents 4 0 R >>"
        ),
        4: b"<< /Length %d >>\nstream\n" % len(page_content)
        + page_content
        + b"\nendstream",
        5: _FONT,
        6: form,
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


#: A form that draws some text and names itself among its own resources.
_SELF_NAMING = (
    b"BT /F1 14 Tf 40 150 Td (inner) Tj ET",
    b"/Font << /F1 5 0 R >> /XObject << /Fm1 6 0 R >> ",
)


def _ink(document: Document) -> int:
    raster = document.pages[0].render(antialias=False)
    return sum(
        1
        for y in range(0, 200, 2)
        for x in range(0, 400, 2)
        if raster.get_pixel(x, y) != (255, 255, 255)
    )


def test_the_page_still_has_its_text():
    document = _document(
        b"BT /F1 14 Tf 40 180 Td (page) Tj ET\n/Fm1 Do\n", *_SELF_NAMING
    )
    assert "page" in document.pages[0].extract_text()


def test_the_form_s_own_text_survives_as_well():
    document = _document(b"/Fm1 Do\n", *_SELF_NAMING)
    assert document.pages[0].extract_text().strip() == "inner"


def test_the_page_can_still_be_drawn():
    document = _document(
        b"BT /F1 14 Tf 40 180 Td (page) Tj ET\n/Fm1 Do\n", *_SELF_NAMING
    )
    assert _ink(document) > 0  # it used to raise


def test_the_page_can_still_be_exported_to_svg():
    document = _document(b"/Fm1 Do\n", *_SELF_NAMING)
    assert "<svg" in document.pages[0].to_svg()


def test_a_resource_beside_the_cycle_is_untouched():
    # The page's own font is not collateral: the drop takes the edge, not the
    # dictionary it was found in.
    document = _document(
        b"BT /F1 14 Tf 40 180 Td (caf\xe9) Tj ET\n/Fm1 Do\n", *_SELF_NAMING
    )
    assert "café" in document.pages[0].extract_text()


def test_two_forms_that_name_each_other_also_stop():
    document = _document(
        b"/Fm1 Do\n",
        b"BT /F1 14 Tf 40 150 Td (inner) Tj ET",
        b"/Font << /F1 5 0 R >> /XObject << /Fm1 6 0 R /Fm2 6 0 R >> ",
    )
    assert isinstance(document.pages[0].extract_text(), str)
    assert _ink(document) >= 0


def test_a_page_with_no_cycle_is_unaffected():
    document = _document(
        b"BT /F1 14 Tf 40 180 Td (page) Tj ET\n/Fm1 Do\n",
        b"BT /F1 14 Tf 40 150 Td (inner) Tj ET",
        b"/Font << /F1 5 0 R >> ",
    )
    assert " ".join(document.pages[0].extract_text().split()) == "page inner"


# --- what is still refused --------------------------------------------------


def test_a_cyclic_page_tree_is_still_refused():
    from aspose_pdf.engine.simple_pdf import SimplePdf

    pdf = SimplePdf()
    pdf.pages = [(0, 0, 612, 792)]
    pdf.page_contents = [b""]
    pdf._ensure_cos()
    page = pdf._get_page_dict(0)
    page.mapping[PdfName("Parent")] = page
    with pytest.raises(PdfParseException, match="cycle"):
        pdf._get_inherited_attr(page, "Resources")


def _messages(action) -> list[str]:
    import logging

    logger = logging.getLogger("aspose_pdf")
    records: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = _Capture()
    logger.addHandler(handler)
    try:
        action()
    finally:
        logger.removeHandler(handler)
    return records


def test_a_dropped_dictionary_edge_is_reported():
    # Silence would make a page quietly different from what its file says.
    messages = _messages(
        lambda: _document(b"/Fm1 Do\n", *_SELF_NAMING).pages[0].extract_text()
    )
    assert any("circular reference" in message for message in messages)


def test_a_dropped_array_edge_is_reported_too():
    from aspose_pdf.engine.cos import PdfArray, PdfString
    from aspose_pdf.engine.simple_pdf import SimplePdf

    pdf = SimplePdf()
    array = PdfArray([PdfString(b"first")])
    array.items.append(array)
    messages = _messages(lambda: pdf._convert_cos_to_dict(array))
    assert any("circular reference" in message for message in messages)
