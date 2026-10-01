"""A page whose /MediaBox cannot give it a size gets US Letter.

A page of no area cannot be drawn, and it used to raise
``PdfValidationException("page box must have positive area")`` from the
rasterizer while ``Page.rect`` reported the degenerate rectangle as if it were
the page's size. **pdfium and MuPDF answer US Letter for every such shape**, and
MuPDF puts it in the media box itself rather than only drawing with it, which is
what happens here: after the substitution our ``rect`` equals MuPDF's
``mediabox`` on every shape either of us can parse.

================================  ======================  ======================
``/MediaBox``                     ours (was)              ours (is) = MuPDF
================================  ======================  ======================
absent                            ``(0,0,612,792)``       ``(0,0,612,792)``
``(nonsense)`` not an array       ``(0,0,612,792)``       ``(0,0,612,792)``
``[0 0 612]`` too short           ``(0,0,612,792)``       ``(0,0,612,792)``
``[0 0 0 0]`` no area             ``(0,0,0,0)``, raised   ``(0,0,612,792)``
``[0 0 0 792]`` no width          ``(0,0,0,792)``, raised ``(0,0,612,792)``
``[(a) (b) (c) (d)]`` not numbers ``(0,0,0,0)``, raised   ``(0,0,612,792)``
``[612 792 0 0]`` inverted        ``(612,792,0,0)``       ``(0,0,612,792)``
``[0 792 612 0]`` one axis back   ``(0,792,612,0)``       ``(0,0,612,792)``
``[-612 -792 0 0]`` negative      ``(-612,-792,0,0)``     ``(-612,-792,0,0)``
================================  ======================  ======================

The last row is a real rectangle that happens to sit at negative coordinates;
both references keep it, and so do we. The ``/MediaBox`` written back is always
the one the file held -- MuPDF and qpdf both keep ``[0 0 0 0]`` too, so nothing
is rewritten behind the caller's back.
"""

from __future__ import annotations

import io
import struct

import pytest

from aspose_pdf import Document, TextStamp

LETTER = (0.0, 0.0, 612.0, 792.0)

SHAPES = {
    "absent": (None, LETTER),
    "not an array": (b"(nonsense)", LETTER),
    "too short": (b"[0 0 612]", LETTER),
    "no area": (b"[0 0 0 0]", LETTER),
    "no width": (b"[0 0 0 792]", LETTER),
    "not numbers": (b"[(a) (b) (c) (d)]", LETTER),
    "inverted": (b"[612 792 0 0]", LETTER),
    "one axis backwards": (b"[0 792 612 0]", LETTER),
    "negative coordinates": (b"[-612 -792 0 0]", (-612.0, -792.0, 0.0, 0.0)),
}


def _pdf_with_media_box(media_box: bytes | None) -> bytes:
    """A one-page document whose page carries *media_box* verbatim, or none."""
    entry = b" /MediaBox " + media_box if media_box is not None else b""
    bodies = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        b"<< /Type /Page /Parent 2 0 R" + entry + b" >>",
    ]
    out = bytearray(b"%PDF-1.7\n")
    offsets = []
    for number, body in enumerate(bodies, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    start = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(bodies) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(bodies) + 1,
        start,
    )
    return bytes(out)


def _png_size(path) -> tuple[int, int]:
    width, height = struct.unpack(">II", path.read_bytes()[16:24])
    return width, height


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_the_page_reports_a_size_it_can_have(shape):
    media_box, expected = SHAPES[shape]
    document = Document(io.BytesIO(_pdf_with_media_box(media_box)))
    assert tuple(document.pages[0].rect) == expected
    assert tuple(document.pages[0].media_box) == expected


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_the_page_renders(shape, tmp_path):
    media_box, expected = SHAPES[shape]
    document = Document(io.BytesIO(_pdf_with_media_box(media_box)))
    out = document.pages[0].save_as_image(tmp_path / "page.png", dpi=72)
    width = round(expected[2] - expected[0])
    height = round(expected[3] - expected[1])
    assert _png_size(out) == (width, height)


def test_the_media_box_in_the_file_is_left_as_it_was():
    # MuPDF and qpdf both write [0 0 0 0] straight back, so a degenerate box is
    # substituted for reading and drawing, never rewritten.
    document = Document(io.BytesIO(_pdf_with_media_box(b"[0 0 0 0]")))
    buffer = io.BytesIO()
    document.save(buffer)
    data = buffer.getvalue()

    assert b"/MediaBox [ 0 0 0 0 ]" in data
    assert b"/MediaBox [ 0 0 612 792 ]" not in data
    # And reading it back gives the substitution again.
    assert tuple(Document(io.BytesIO(data)).pages[0].rect) == LETTER


def test_everything_that_needs_a_page_size_works_on_such_a_page(tmp_path):
    data = _pdf_with_media_box(b"[0 0 0 0]")

    assert Document(io.BytesIO(data)).pages[0].extract_text() == ""
    assert Document(io.BytesIO(data)).pages[0].save_as_svg(tmp_path / "p.svg").exists()
    assert Document(io.BytesIO(data)).to_html()

    stamped = Document(io.BytesIO(data))
    stamped.pages[0].add_stamp(TextStamp("MARK", font_size=18))
    assert "MARK" in stamped.pages[0].extract_text()


def test_an_in_memory_page_of_no_area_renders_too():
    # The same substitution the loader makes, for a model assembled directly --
    # which is the only way such a box reaches the rasterizer now.
    from aspose_pdf.engine.rasterizer import render_page
    from aspose_pdf.engine.simple_pdf import SimplePdf

    pdf = SimplePdf([(0.0, 0.0, 0.0, 0.0)], page_contents=[b""])
    raster = render_page(pdf, 0, dpi=72.0)
    assert (raster.width, raster.height) == (612, 792)


def test_a_box_that_is_not_four_finite_numbers_is_still_refused():
    # Substituting here would hide a mistake in the model rather than report it,
    # and the loader never produces such a box.
    from aspose_pdf.engine.rasterizer import render_page
    from aspose_pdf.engine.simple_pdf import SimplePdf
    from aspose_pdf.exceptions import PdfValidationException

    pdf = SimplePdf([(0.0, 0.0, float("inf"), 792.0)], page_contents=[b""])
    with pytest.raises(PdfValidationException, match="finite"):
        render_page(pdf, 0, dpi=72.0)
