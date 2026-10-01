"""The crop box is intersected with the media box before anything uses it.

ISO 32000-1 Table 30: the crop box "shall be intersected with the media box" to
determine the page's visible region. The entry itself may reach outside -- qpdf,
pdfium and MuPDF all keep whatever bytes the file holds -- so the intersection
belongs where the value is used, which is what was missing: a page whose crop box
was larger than its media box rendered at the crop box's size, inventing sheet
the document does not describe, and one reaching off an edge placed its content
against a corner that is not on the page.

Measured on a 612x792 page, after the fix our rendered size is pdfium's and
MuPDF's on every shape:

====================================  ========  ========  ========
crop box                              ours      MuPDF     pdfium
====================================  ========  ========  ========
``(0, 0, 2000, 2000)`` over-large     612x792   612x792   612x792
``(-100, -100, 300, 300)`` overhangs  300x300   300x300   300x300
``(0, 0, 0, 0)`` empty                612x792   612x792   612x792
``(1000, 1000, 2000, 2000)`` off page 612x792   612x792   0x0
``(300, 400, 100, 200)`` inverted     200x200   200x200   200x200
``(100, 100, 300, 400)`` inside       200x300   200x300   200x300
====================================  ========  ========  ========

The one row where the references part is a crop box lying entirely off the page:
pdfium takes the empty intersection literally and reports a 0x0 page it then
refuses to render, while MuPDF falls back to the media box. A page of no size is
not a usable answer, so MuPDF's is the one adopted.
"""

from __future__ import annotations

import io
import struct

import pytest

from aspose_pdf import Document, TextStamp

CASES = {
    "over-large": ((0, 0, 2000, 2000), (0.0, 0.0, 612.0, 792.0), (612, 792)),
    "overhangs an edge": ((-100, -100, 300, 300), (0.0, 0.0, 300.0, 300.0), (300, 300)),
    "empty": ((0, 0, 0, 0), (0, 0, 612, 792), (612, 792)),
    "entirely off the page": (
        (1000, 1000, 2000, 2000), (0, 0, 612, 792), (612, 792),
    ),
    "inverted corners": ((300, 400, 100, 200), (100.0, 200.0, 300.0, 400.0), (200, 200)),
    "inside the media box": (
        (100, 100, 300, 400), (100.0, 100.0, 300.0, 400.0), (200, 300),
    ),
}


def _document(crop=None) -> Document:
    document = Document()
    document.pages.add()
    document.pages[0].add_text("X", 120, 150)
    if crop is not None:
        document.pages[0].crop_box = crop
    return document


def _png_size(path) -> tuple[int, int]:
    width, height = struct.unpack(">II", path.read_bytes()[16:24])
    return width, height


@pytest.mark.parametrize("shape", sorted(CASES))
def test_the_effective_crop_box_is_the_intersection(shape):
    crop, effective, _ = CASES[shape]
    assert tuple(_document(crop).pages[0].crop_box) == tuple(effective)


@pytest.mark.parametrize("shape", sorted(CASES))
def test_the_page_renders_at_the_size_the_references_render_it(shape, tmp_path):
    crop, _, size = CASES[shape]
    out = _document(crop).pages[0].save_as_image(tmp_path / "page.png", dpi=72)
    assert _png_size(out) == size


@pytest.mark.parametrize("shape", sorted(CASES))
def test_a_loaded_document_is_treated_the_same(shape, tmp_path, request):
    # The fix has to hold for a file that arrives from outside, not only for one
    # assembled here -- that is where such a crop box actually comes from.
    crop, effective, size = CASES[shape]
    path = tmp_path / "in.pdf"
    _document(crop).save(path)
    reloaded = Document(path)
    assert tuple(reloaded.pages[0].crop_box) == tuple(effective)
    out = reloaded.pages[0].save_as_image(tmp_path / "page.png", dpi=72)
    assert _png_size(out) == size


def test_the_entry_in_the_file_is_still_what_was_asked_for():
    # Only the interpretation is clipped. The bytes stay as they were, which is
    # what qpdf, pdfium and MuPDF all keep, so nothing is lost on a round trip.
    buffer = io.BytesIO()
    _document((0, 0, 2000, 2000)).save(buffer)
    assert b"/CropBox [ 0 0 2000 2000 ]" in buffer.getvalue()


def test_an_empty_crop_box_no_longer_makes_the_page_unrenderable():
    # It used to raise "page box must have positive area" where both references
    # show the whole media box.
    document = _document((0, 0, 0, 0))
    assert document.pages[0].extract_text().strip() == "X"
    assert tuple(document.pages[0].crop_box) == (0, 0, 612, 792)


def test_a_stamp_lands_inside_the_page_not_inside_an_over_large_crop_box():
    # Stamp placement measures the crop box too, so an over-large one used to put
    # a centred stamp outside the sheet entirely.
    document = _document((0, 0, 2000, 2000))
    document.pages[0].add_stamp(TextStamp("MARK", font_size=24))
    assert "MARK" in document.pages[0].extract_text()


def test_a_crop_box_inside_the_media_box_is_left_alone():
    # The common case must not move: no clipping happens when none is called for.
    document = _document((100, 100, 300, 400))
    assert tuple(document.pages[0].crop_box) == (100.0, 100.0, 300.0, 400.0)
    assert tuple(document.pages[0].rect) == (0, 0, 612, 792)  # the media box
