"""Authoring a page's size, and the five boxes a page is described by.

A new page used to be 612x792 and nothing else: ``pages.add()`` took no size,
``media_box`` was a read-only alias of ``rect``, and of the five boxes ISO
32000-1 Table 30 defines only ``/CropBox`` was reachable at all. These tests
cover the named sizes, the size of a page as a thing that can be set, and the
production boxes ``/BleedBox``, ``/TrimBox`` and ``/ArtBox``.

Two rules are checked here because they are the ones that are easy to get wrong:

* The media box has to have an area, while the other four may be written exactly
  as asked for. A media box of no area is replaced with US Letter when read back
  (a page must have a size), so accepting one on the way out would make the
  assignment look as though it had never happened. The others are *interpreted*
  on the way back in -- intersected with the media box, falling back to their
  default where nothing is left -- so the bytes can be kept as given, which is
  what ``/CropBox`` has always done.
* ``/MediaBox`` and ``/CropBox`` are inheritable; ``/BleedBox``, ``/TrimBox`` and
  ``/ArtBox`` are not. A production box on a page-tree node is not the page's.

Both rules were checked against two independent implementations rather than only
against this reading of the clause. **qpdf 12.4.2** (through pikepdf) reads all
five boxes back from our output unchanged, and **poppler 26.09** (``pdfinfo
-box``) agrees with us on every box of a document qpdf authored -- including an
``/ArtBox`` of ``[-20 -20 700 900]`` on an A4 page, which both poppler and this
reduce to the media box; a page with no production boxes, which both answer with
the *crop* box; and a ``/TrimBox`` on the shared ``/Pages`` node, which both
ignore.
"""

from __future__ import annotations

import io
import struct

import pytest

from aspose_pdf import Document, PageSize
from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.viewer_preferences import PageBoundary

LETTER = (0.0, 0.0, 612.0, 792.0)


def _roundtrip(document: Document) -> Document:
    buffer = io.BytesIO()
    document.save(buffer)
    buffer.seek(0)
    return Document(buffer)


def _saved(document: Document) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _png_size(path) -> tuple[int, int]:
    width, height = struct.unpack(">II", path.read_bytes()[16:24])
    return width, height


def _page(size=None):
    document = Document()
    return document.pages.add(size) if size is not None else document.pages.add()


# ---------------------------------------------------------------------------
# PageSize
# ---------------------------------------------------------------------------


def test_the_iso_sizes_are_exact_conversions_from_millimetres():
    # 210 x 297 mm at 72 points to the inch. Rounding these to a printer's
    # 595.28 x 841.89 would put the error in the library instead of the caller's
    # formatting.
    assert PageSize.A4 == PageSize(210 * 72 / 25.4, 297 * 72 / 25.4)
    # Each A size is the next one turned over and doubled along its long side.
    assert PageSize.A3.width == pytest.approx(PageSize.A4.height)
    assert PageSize.A3.height == pytest.approx(PageSize.A4.width * 2)
    assert PageSize.A5.height == pytest.approx(PageSize.A4.width)
    assert PageSize.A4.scaled(2) == PageSize(PageSize.A4.width * 2, PageSize.A4.height * 2)


def test_the_us_sizes_are_whole_inches():
    assert PageSize.LETTER == PageSize(612, 792)
    assert PageSize.LEGAL == PageSize(612, 1008)
    assert PageSize.TABLOID == PageSize(792, 1224)
    assert PageSize.LEDGER == PageSize(1224, 792)
    assert PageSize.EXECUTIVE == PageSize(522, 756)
    assert PageSize.STATEMENT == PageSize(396, 612)


def test_every_named_size_is_portrait_except_ledger():
    # ISO 216 and the US sizes are both named shorter-side-first; ledger is the
    # one that is conventionally the other way round.
    for name in ("A0", "A1", "A2", "A3", "A4", "A5", "A6", "B4", "B5",
                 "LETTER", "LEGAL", "TABLOID", "EXECUTIVE", "STATEMENT"):
        size = getattr(PageSize, name)
        assert size.width < size.height, name
    assert PageSize.LEDGER.width > PageSize.LEDGER.height


def test_landscape_portrait_and_rotated():
    assert PageSize.A4.landscape() == PageSize(PageSize.A4.height, PageSize.A4.width)
    assert PageSize.A4.landscape().landscape() == PageSize.A4.landscape()
    assert PageSize.A4.landscape().portrait() == PageSize.A4
    assert PageSize.A4.portrait() is PageSize.A4
    assert PageSize.A4.rotated().rotated() == PageSize.A4


def test_as_rect_takes_an_origin():
    assert PageSize(100, 200).as_rect() == (0.0, 0.0, 100.0, 200.0)
    assert PageSize(100, 200).as_rect(10, 20) == (10.0, 20.0, 110.0, 220.0)


def test_by_name_ignores_case_and_separators():
    assert PageSize.by_name("a4") is PageSize.A4
    assert PageSize.by_name("Letter") is PageSize.LETTER
    assert PageSize.by_name("half letter") is PageSize.STATEMENT
    assert PageSize.by_name("HALF-LETTER") is PageSize.STATEMENT
    assert PageSize.by_name("11x17") is PageSize.TABLOID


def test_a_size_has_to_be_two_positive_finite_numbers():
    for bad in [(0, 10), (10, 0), (-1, 10), (10, float("nan")), (float("inf"), 10)]:
        with pytest.raises(ValueError):
            PageSize(*bad)
    with pytest.raises(TypeError):
        PageSize("wide", 10)
    with pytest.raises(ValueError):
        PageSize.by_name("A9")
    with pytest.raises(TypeError):
        PageSize.by_name(4)


def test_two_sizes_are_equal_when_they_write_the_same_numbers():
    # A PDF holds six decimals, so an A4 page that has been saved and reloaded is
    # half a nanometre from PageSize.A4 in floating point. Equality compares the
    # pair as a file would hold it, which is the only answer that is any use.
    document = Document()
    document.pages.add(PageSize.A4)
    buffer = io.BytesIO()
    document.save(buffer)
    buffer.seek(0)
    reloaded = Document(buffer).pages[0]

    assert reloaded.size != PageSize.A4.rotated()
    assert reloaded.size == PageSize.A4
    assert reloaded.size.width != PageSize.A4.width  # the raw floats do differ
    assert len({reloaded.size, PageSize.A4}) == 1
    assert PageSize.A4 != "A4"


def test_a_size_is_immutable():
    with pytest.raises(AttributeError):
        PageSize.A4.width = 1


# ---------------------------------------------------------------------------
# Creating a page of a given size
# ---------------------------------------------------------------------------


def test_a_page_with_no_size_asked_for_is_still_us_letter():
    assert tuple(_page().rect) == LETTER


def test_a_size_may_be_the_argument_or_the_keyword():
    document = Document()
    assert tuple(document.pages.add(PageSize.A4).rect) == PageSize.A4.as_rect()
    assert tuple(document.pages.add(size=PageSize.A5).rect) == PageSize.A5.as_rect()
    assert tuple(document.pages.add(size="legal").rect) == PageSize.LEGAL.as_rect()
    assert tuple(document.pages.add(size=(300, 400)).rect) == (0.0, 0.0, 300.0, 400.0)


def test_insert_takes_a_size_too():
    document = Document()
    document.pages.add()
    first = document.pages.insert(0, size=PageSize.A4.landscape())
    assert tuple(first.rect) == PageSize.A4.landscape().as_rect()
    assert tuple(document.pages[1].rect) == LETTER


def test_a_size_cannot_be_given_twice_or_alongside_a_page_to_copy():
    document = Document()
    page = document.pages.add()
    with pytest.raises(PdfValidationException):
        document.pages.add(PageSize.A4, size=PageSize.A5)
    with pytest.raises(PdfValidationException):
        document.pages.add(page, size=PageSize.A5)


def test_a_bad_size_is_refused_as_a_validation_error():
    document = Document()
    for bad in [(0, 0), (10,), "A9", object()]:
        with pytest.raises(PdfValidationException):
            document.pages.add(size=bad)


def test_pages_of_different_sizes_live_in_one_document():
    document = Document()
    document.pages.add(PageSize.A4)
    document.pages.add(PageSize.A5)
    document.pages.add()
    sizes = [tuple(page.rect) for page in _roundtrip(document).pages]
    assert sizes[1][3] == pytest.approx(PageSize.A5.height)
    assert sizes[2] == LETTER
    assert sizes[0][3] == pytest.approx(PageSize.A4.height)


def test_a_page_created_at_a4_renders_at_a4(tmp_path):
    # The size lives in two places -- the page list the renderer measures and the
    # /MediaBox a save writes -- so a size that reaches only one of them renders
    # at one size and opens at another.
    document = Document()
    page = document.pages.add(PageSize.A4)
    # 595.2755 x 841.8897 pt: the renderer gives a fractional page the whole
    # pixel it needs (``ceil``), so A4 at 72 dpi is 596 x 842.
    out = page.save_as_image(tmp_path / "page.png", dpi=72)
    assert _png_size(out) == (596, 842)
    reloaded = _roundtrip(document)
    out = reloaded.pages[0].save_as_image(tmp_path / "again.png", dpi=72)
    assert _png_size(out) == (596, 842)


# ---------------------------------------------------------------------------
# The media box and the size of an existing page
# ---------------------------------------------------------------------------


def test_the_media_box_can_be_set_and_is_the_rect():
    page = _page()
    page.media_box = (0, 0, 400, 500)
    assert tuple(page.rect) == (0.0, 0.0, 400.0, 500.0)
    assert tuple(page.media_box) == (0.0, 0.0, 400.0, 500.0)
    assert page.size == PageSize(400, 500)


def test_a_resized_page_renders_and_reopens_at_its_new_size(tmp_path):
    document = Document()
    page = document.pages.add()
    page.media_box = (0, 0, 300, 400)
    out = page.save_as_image(tmp_path / "page.png", dpi=72)
    assert _png_size(out) == (300, 400)
    assert tuple(_roundtrip(document).pages[0].rect) == (0.0, 0.0, 300.0, 400.0)


def test_the_media_box_corners_may_be_named_in_either_order():
    page = _page()
    page.media_box = (400, 500, 0, 0)
    assert tuple(page.rect) == (0.0, 0.0, 400.0, 500.0)


def test_a_media_box_at_a_negative_origin_is_kept():
    page = _page()
    page.media_box = (-100, -50, 500, 700)
    assert tuple(page.rect) == (-100.0, -50.0, 500.0, 700.0)
    assert page.size == PageSize(600, 750)


def test_the_media_box_has_to_be_a_sheet_a_page_can_have():
    page = _page()
    for bad in [(0, 0, 0, 0), (10, 10, 10, 400), (0, 0, 10), "nope",
                (0, 0, float("inf"), 5), None]:
        with pytest.raises(PdfValidationException):
            page.media_box = bad
    assert tuple(page.rect) == LETTER


def test_the_size_setter_keeps_the_media_box_origin():
    page = _page()
    page.media_box = (20, 30, 620, 830)
    page.size = PageSize.A5
    assert tuple(page.rect) == PageSize.A5.as_rect(20, 30)


def test_the_size_setter_takes_a_pair_or_a_name():
    page = _page()
    page.size = (250, 350)
    assert tuple(page.rect) == (0.0, 0.0, 250.0, 350.0)
    page.size = "a4"
    assert page.size == PageSize.A4


def test_the_size_of_a_page_is_its_media_box_not_its_crop_box():
    page = _page()
    page.crop_box = (100, 100, 300, 300)
    assert page.size == PageSize(612, 792)


def test_setting_a_media_box_on_one_page_leaves_its_siblings_alone():
    document = Document()
    document.pages.add()
    document.pages.add()
    document.pages[0].media_box = (0, 0, 300, 300)
    assert tuple(document.pages[1].rect) == LETTER
    reloaded = _roundtrip(document)
    assert tuple(reloaded.pages[0].rect) == (0.0, 0.0, 300.0, 300.0)
    assert tuple(reloaded.pages[1].rect) == LETTER


def test_a_page_overrides_a_media_box_it_was_inheriting():
    from aspose_pdf.engine.cos import PdfArray, PdfName, PdfNumber

    document = Document()
    document.pages.add()
    document.pages.add()
    engine = document._engine_pdf
    # Move /MediaBox up to the shared /Pages node, as a file may well hold it.
    parent = engine._resolve(engine._get_page_dict(0).mapping.get(PdfName("Parent")))
    for index in (0, 1):
        engine._get_page_dict(index).mapping.pop(PdfName("MediaBox"), None)
    parent.mapping[PdfName("MediaBox")] = PdfArray(
        [PdfNumber(v) for v in (0, 0, 612, 792)]
    )

    document.pages[0].media_box = (0, 0, 300, 300)
    assert PdfName("MediaBox") in engine._get_page_dict(0).mapping
    assert PdfName("MediaBox") not in engine._get_page_dict(1).mapping
    reloaded = _roundtrip(document)
    assert tuple(reloaded.pages[0].rect) == (0.0, 0.0, 300.0, 300.0)
    assert tuple(reloaded.pages[1].rect) == LETTER


def test_shrinking_the_media_box_clips_the_crop_box_that_was_inside_it():
    # Table 30 intersects the crop box with the media box, so a resize that
    # leaves the crop box hanging over the edge needs no second assignment.
    page = _page()
    page.crop_box = (0, 0, 500, 700)
    page.media_box = (0, 0, 300, 300)
    assert tuple(page.crop_box) == (0.0, 0.0, 300.0, 300.0)


# ---------------------------------------------------------------------------
# The production boxes
# ---------------------------------------------------------------------------

PRODUCTION = ("bleed_box", "trim_box", "art_box")


@pytest.mark.parametrize("name", PRODUCTION)
def test_a_production_box_defaults_to_the_crop_box(name):
    page = _page()
    assert tuple(getattr(page, name)) == tuple(page.crop_box) == LETTER
    page.crop_box = (100, 100, 300, 400)
    assert tuple(getattr(page, name)) == (100.0, 100.0, 300.0, 400.0)


@pytest.mark.parametrize("name", PRODUCTION)
def test_a_production_box_is_set_read_and_round_tripped(name):
    document = Document()
    page = document.pages.add()
    setattr(page, name, (10, 20, 500, 700))
    assert tuple(getattr(page, name)) == (10.0, 20.0, 500.0, 700.0)
    assert tuple(getattr(_roundtrip(document).pages[0], name)) == (10.0, 20.0, 500.0, 700.0)


@pytest.mark.parametrize("name", PRODUCTION)
def test_a_production_box_may_name_its_corners_in_either_order(name):
    page = _page()
    setattr(page, name, (500, 700, 10, 20))
    assert tuple(getattr(page, name)) == (10.0, 20.0, 500.0, 700.0)


@pytest.mark.parametrize("name", PRODUCTION)
def test_a_production_box_is_reduced_to_its_intersection_with_the_media_box(name):
    # 14.11.2: the bleed, trim and art boxes shall not ordinarily extend beyond
    # the media box, and where they do they are effectively reduced to it.
    page = _page()
    setattr(page, name, (-100, -100, 2000, 2000))
    assert tuple(getattr(page, name)) == LETTER


@pytest.mark.parametrize("name", PRODUCTION)
def test_a_box_over_the_edge_is_reduced_for_a_loaded_file_too(name):
    # Which is where such a box actually comes from: the reduction has to hold
    # for a file that arrives from outside, not only for one assembled here.
    document = Document()
    setattr(document.pages.add(), name, (-100, -100, 2000, 2000))
    assert tuple(getattr(_roundtrip(document).pages[0], name)) == LETTER


@pytest.mark.parametrize("name", PRODUCTION)
def test_a_production_box_survives_optimisation(name):
    # optimize() rewrites the object graph; the page's own entries must come
    # through it, as they do through a plain save.
    document = Document()
    page = document.pages.add()
    page.add_text("x", 50, 50)
    setattr(page, name, (10, 20, 500, 700))
    document.optimize()
    assert tuple(getattr(_roundtrip(document).pages[0], name)) == (10.0, 20.0, 500.0, 700.0)


@pytest.mark.parametrize("name", PRODUCTION)
def test_a_production_box_off_the_page_falls_back_to_the_default(name):
    page = _page()
    setattr(page, name, (1000, 1000, 2000, 2000))
    assert tuple(getattr(page, name)) == tuple(page.crop_box)


@pytest.mark.parametrize("name", PRODUCTION)
def test_an_empty_production_box_falls_back_rather_than_being_refused(name):
    # The same reading the crop box takes: what a file may hold, this may write.
    page = _page()
    setattr(page, name, (0, 0, 0, 0))
    assert tuple(getattr(page, name)) == tuple(page.crop_box)


@pytest.mark.parametrize("name", PRODUCTION)
def test_the_entry_in_the_file_is_what_was_asked_for(name):
    document = Document()
    setattr(document.pages.add(), name, (0, 0, 2000, 2000))
    entry = {"bleed_box": b"BleedBox", "trim_box": b"TrimBox", "art_box": b"ArtBox"}[name]
    assert b"/" + entry + b" [ 0 0 2000 2000 ]" in _saved(document)


@pytest.mark.parametrize("name", PRODUCTION)
def test_assigning_none_removes_a_production_box(name):
    document = Document()
    page = document.pages.add()
    setattr(page, name, (10, 20, 500, 700))
    setattr(page, name, None)
    assert tuple(getattr(page, name)) == tuple(page.crop_box)
    entry = {"bleed_box": b"BleedBox", "trim_box": b"TrimBox", "art_box": b"ArtBox"}[name]
    assert b"/" + entry not in _saved(document)


@pytest.mark.parametrize("name", PRODUCTION)
def test_a_production_box_has_to_be_four_numbers(name):
    page = _page()
    for bad in [(0, 0, 10), "nope", (0, 0, 10, float("nan"))]:
        with pytest.raises(PdfValidationException):
            setattr(page, name, bad)


@pytest.mark.parametrize("entry", ("BleedBox", "TrimBox", "ArtBox"))
def test_a_production_box_is_not_inherited_from_a_page_tree_node(entry):
    # Table 30 marks only /MediaBox, /CropBox, /Resources and /Rotate
    # inheritable. A production box on the parent is not this page's box.
    from aspose_pdf.engine.cos import PdfArray, PdfName, PdfNumber

    document = Document()
    document.pages.add()
    engine = document._engine_pdf
    page_dict = engine._get_page_dict(0)
    parent = engine._resolve(page_dict.mapping.get(PdfName("Parent")))
    parent.mapping[PdfName(entry)] = PdfArray(
        [PdfNumber(v) for v in (100, 100, 300, 400)]
    )
    assert PdfName(entry) not in page_dict.mapping

    page = document.pages[0]
    assert tuple(page.get_box(entry)) == tuple(page.crop_box) == LETTER


def test_assigning_none_to_the_crop_box_removes_it():
    document = Document()
    page = document.pages.add()
    page.crop_box = (10, 20, 300, 400)
    page.crop_box = None
    assert tuple(page.crop_box) == tuple(page.rect)
    assert b"/CropBox" not in _saved(document)


def test_the_boxes_of_a_copied_page_come_with_it():
    document = Document()
    page = document.pages.add(PageSize.A4)
    page.crop_box = (5, 5, 500, 700)
    page.trim_box = (10, 10, 400, 600)
    page.bleed_box = (8, 8, 450, 650)
    page.art_box = (20, 20, 300, 500)
    copy = document.pages.insert(1, page)
    assert tuple(copy.rect) == PageSize.A4.as_rect()
    assert tuple(copy.trim_box) == (10.0, 10.0, 400.0, 600.0)
    assert tuple(copy.bleed_box) == (8.0, 8.0, 450.0, 650.0)
    assert tuple(copy.art_box) == (20.0, 20.0, 300.0, 500.0)


# ---------------------------------------------------------------------------
# The boxes by name
# ---------------------------------------------------------------------------


def test_get_box_and_set_box_take_a_page_boundary():
    page = _page()
    page.set_box(PageBoundary.TRIM_BOX, (10, 10, 400, 500))
    assert tuple(page.get_box(PageBoundary.TRIM_BOX)) == (10.0, 10.0, 400.0, 500.0)
    page.set_box("CropBox", (5, 5, 500, 600))
    assert tuple(page.get_box("CropBox")) == (5.0, 5.0, 500.0, 600.0)
    page.set_box(PageBoundary.MEDIA_BOX, (0, 0, 450, 550))
    assert tuple(page.get_box(PageBoundary.MEDIA_BOX)) == (0.0, 0.0, 450.0, 550.0)


def test_set_box_returns_the_page_so_calls_chain():
    page = _page()
    assert page.set_box("TrimBox", (10, 10, 100, 100)) is page


def test_set_box_will_not_remove_the_media_box():
    page = _page()
    with pytest.raises(PdfValidationException):
        page.set_box(PageBoundary.MEDIA_BOX, None)


def test_a_box_that_is_not_one_of_the_five_is_refused():
    page = _page()
    with pytest.raises(PdfValidationException):
        page.get_box("Margins")
    with pytest.raises(PdfValidationException):
        page.set_box("Margins", (0, 0, 10, 10))


def test_get_box_answers_the_boundary_the_viewer_preferences_name():
    # The enumeration is the one /ViewArea and /PrintArea are written with, so
    # code holding one can ask the page for the geometry it names.
    document = Document()
    page = document.pages.add()
    page.trim_box = (10, 10, 400, 500)
    document.viewer_preferences.view_area = PageBoundary.TRIM_BOX
    assert tuple(page.get_box(document.viewer_preferences.view_area)) == (
        10.0, 10.0, 400.0, 500.0,
    )
