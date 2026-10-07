"""A page placed on a page: letterheads, watermarks from a PDF, N-up and booklets.

Stamps could draw a line of text, an image or a page number. The one thing nobody
could stamp was **a page** -- a letterhead, a background form, a watermark drawn
by a designer in a PDF -- although the two pieces it takes were both there: the
deep object copy a merge makes (``_import_object``) and the form-XObject placement
every stamp already goes through. Without it there was no imposition either: no
way to put four pages on a sheet, or to lay a document out as a folded booklet.

``PageStamp`` places a page as a reader shows it -- its crop box, with its
``/Rotate`` applied -- and everything it draws with is imported, so the document it
came from can be closed afterwards. ``Document.n_up`` and ``Document.booklet``
build on the same import, sharing one memo across the run so a font several pages
have in common is copied once.

Checked with **qpdf 12.4.2**: the sheet of a 4-up carries four form XObjects whose
BBox is the source page's size, each invoked under a scale-and-translate matrix
into its own cell; a booklet of six pages is four sheets of two A5 pages side by
side, two of them half empty, as padding to a multiple of four requires. poppler
renders both.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document, GraphicsPath, PageSize, PageStamp, PdfFileEditor
from aspose_pdf.engine.imposition import booklet_order
from aspose_pdf.exceptions import PdfValidationException


def _source(pages: int = 1, size=PageSize.A5) -> Document:
    """A document whose pages are each marked with their own number."""
    document = Document()
    for index in range(pages):
        page = document.pages.add(size)
        page.add_text(f"PAGE {index + 1}", 100, 300, font_size=30)
        page.draw_rectangle(
            10, 10, size.width - 20, size.height - 20, stroke_color=(0.6, 0.6, 0.6)
        )
    return document


def _letterhead() -> bytes:
    document = Document()
    page = document.pages.add(PageSize.A5)
    page.draw_rectangle(0, 520, 420, 75, fill_color="#003366", stroke_color=None)
    page.add_text("ACME CORP", 30, 545, font_size=24, color=(1, 1, 1))
    page.draw_path(
        GraphicsPath().circle(380, 557, 20), fill_color=(1, 0.8, 0), stroke_color=None
    )
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _roundtrip(document: Document) -> Document:
    buffer = io.BytesIO()
    document.save(buffer)
    buffer.seek(0)
    return Document(buffer)


def _saved(document: Document) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# A page as a stamp
# ---------------------------------------------------------------------------


def test_a_page_of_another_document_is_stamped_onto_a_page():
    document = _source(2)
    letterhead = Document(io.BytesIO(_letterhead()))
    document.add_stamp(PageStamp(letterhead.pages[0]))
    reloaded = _roundtrip(document)
    for page in reloaded.pages:
        text = page.extract_text()
        assert "ACME CORP" in text
        assert "PAGE" in text


def test_everything_the_stamped_page_draws_with_comes_across():
    # The source document is closed before the result is saved: if the import had
    # left a reference behind rather than copying, there would be nothing to write.
    document = _source(1)
    letterhead = Document(io.BytesIO(_letterhead()))
    document.add_stamp(PageStamp(letterhead.pages[0], background=True))
    letterhead.dispose()

    reloaded = _roundtrip(document)
    assert "ACME CORP" in reloaded.pages[0].extract_text()
    raster = reloaded.pages[0].render(dpi=72)
    assert raster.get_pixel(100, 40) == (0, 51, 102)  # the letterhead bar
    assert raster.get_pixel(380, 595 - 557) == (255, 204, 0)  # its circle


def test_a_background_page_stamp_goes_under_the_pages_own_content():
    document = _source(1)
    source = Document(io.BytesIO(_letterhead()))
    document.add_stamp(PageStamp(source.pages[0], background=True))
    data = _saved(document)
    # A background stamp is put in front of the page's content, which makes the
    # page's /Contents an array with the stamp first.
    assert b"/Contents [" in data


def test_one_form_serves_every_page_it_is_stamped_on():
    document = _source(4)
    source = Document(io.BytesIO(_letterhead()))
    document.add_stamp(PageStamp(source.pages[0]))
    data = _saved(document)
    # One form, invoked from each page: the import happens once.
    assert data.count(b"/Subtype /Form") == 1
    assert data.count(b"Do Q") == 4


def test_a_page_stamp_takes_the_placement_every_stamp_takes():
    document = _source(1)
    source = Document(io.BytesIO(_letterhead()))
    document.add_stamp(
        PageStamp(
            source.pages[0],
            zoom=0.5,
            rotate=90,
            opacity=0.5,
            horizontal_alignment="Left",
            vertical_alignment="Bottom",
            x_indent=10,
            y_indent=10,
        )
    )
    data = _saved(document)
    assert b"/ca 0.5" in data
    assert b"Do Q" in data
    assert "ACME CORP" in _roundtrip(document).pages[0].extract_text()


def test_a_page_of_the_same_document_can_be_stamped_on_another():
    document = _source(2)
    document.add_stamp(PageStamp(document.pages[0]), pages=[1])
    reloaded = _roundtrip(document)
    assert reloaded.pages[1].extract_text().count("PAGE 1") == 1
    assert "PAGE 2" in reloaded.pages[1].extract_text()


def test_a_document_and_a_page_index_are_accepted_as_well_as_a_page():
    document = _source(1)
    source = Document(io.BytesIO(_letterhead()))
    document.add_stamp(PageStamp(source, page_index=0))
    assert "ACME CORP" in _roundtrip(document).pages[0].extract_text()


def test_the_rotation_of_the_stamped_page_is_applied():
    # A page with /Rotate 90 is *shown* turned, so what is placed is the turned
    # page -- and for a quarter turn the form's width and height swap.
    source = _source(1)
    source.pages[0].rotation = 90
    buffer = io.BytesIO()
    source.save(buffer)
    turned = Document(io.BytesIO(buffer.getvalue()))

    document = _source(1)
    document.add_stamp(PageStamp(turned.pages[0]))
    data = _saved(document)
    # The form's box is the page's displayed size: A5 turned is landscape.
    assert b"/BBox [ 0 0 595.275591 419.527559 ]" in data


def test_a_page_stamp_needs_a_page():
    document = _source(1)
    with pytest.raises(PdfValidationException, match="needs a page"):
        document.add_stamp(PageStamp())
    with pytest.raises(PdfValidationException, match="places a Page or a Document"):
        document.add_stamp(PageStamp("letterhead.pdf"))
    with pytest.raises(PdfValidationException, match="page number from zero"):
        document.add_stamp(PageStamp(document, page_index=-1))


def test_a_page_the_source_does_not_have_is_refused():
    document = _source(1)
    source = Document(io.BytesIO(_letterhead()))
    with pytest.raises(Exception, match=r"(?i)page index|out of range"):
        document.add_stamp(PageStamp(source, page_index=7))


# ---------------------------------------------------------------------------
# N-up
# ---------------------------------------------------------------------------


def test_four_pages_go_on_a_sheet_the_size_of_one():
    document = _source(6)
    imposed = document.n_up(2, 2)
    assert imposed.page_count == 2  # six pages, four to a sheet
    assert imposed.pages[0].size == PageSize.A5


def test_every_page_lands_on_a_sheet():
    document = _source(5)
    imposed = _roundtrip(document.n_up(2, 2))
    text = " ".join(page.extract_text() for page in imposed.pages)
    for number in range(1, 6):
        assert f"PAGE {number}" in text


def _placements(document: Document) -> list[tuple[float, float]]:
    """Where each form was invoked on the first sheet, in invocation order.

    Read from the matrices rather than from the text: each imposed page is a form
    of its own, and the extractor reads one form after another in the order they
    are invoked, so extracted text says nothing about where on the sheet they are.
    """
    import re

    data = _saved(document)
    found = []
    for matrix in re.findall(rb"q ([-0-9.e ]+) cm /\w+ Do Q", data):
        numbers = [float(value) for value in matrix.split()]
        found.append((numbers[4], numbers[5]))
    return found


def test_the_pages_are_placed_in_reading_order():
    document = _source(4)
    first, second, third, fourth = _placements(document.n_up(2, 2))
    # Top row first, left to right: page two is to the right of page one at the
    # same height, and page three starts the row below.
    assert second[0] > first[0]
    assert second[1] == pytest.approx(first[1])
    assert third[1] < first[1]
    assert third[0] == pytest.approx(first[0])
    assert fourth[0] == pytest.approx(second[0])
    assert fourth[1] == pytest.approx(third[1])


def test_column_order_goes_down_each_column():
    document = _source(4)
    first, second, third, _fourth = _placements(document.n_up(2, 2, order="column"))
    # Down the first column, then the second: page two is *below* page one, and
    # page three starts the next column across.
    assert second[1] < first[1]
    assert second[0] == pytest.approx(first[0])
    assert third[0] > first[0]
    assert third[1] == pytest.approx(first[1])


def test_a_sheet_size_is_taken_as_given():
    document = _source(2)
    imposed = document.n_up(1, 2, page_size=PageSize.A4.landscape())
    assert imposed.pages[0].size == PageSize.A4.landscape()
    assert imposed.page_count == 1


def test_a_sheet_size_may_be_a_plain_pair():
    document = _source(2)
    imposed = document.n_up(1, 2, page_size=(800, 400))
    assert imposed.pages[0].size.width == 800.0


def test_each_page_is_scaled_proportionally_and_centred():
    document = _source(1)
    # A wide sheet for one page: the page keeps its proportions and sits in the
    # middle of its cell rather than being stretched across it.
    imposed = document.n_up(1, 1, page_size=(840, 595))
    import re

    matrix = re.findall(rb"q ([-0-9.e ]+) cm /\w+ Do Q", _saved(imposed))[0]
    numbers = [float(value) for value in matrix.split()]
    assert numbers[0] == pytest.approx(numbers[3])  # the same scale both ways
    assert numbers[4] > 0  # moved right, to centre it on the wider sheet


def test_margins_and_gutters_make_room():
    document = _source(4)
    plain = document.n_up(2, 2)
    spaced = document.n_up(2, 2, margin=20, gutter=10)
    import re

    def scale(doc: Document) -> float:
        data = _saved(doc)
        first = re.findall(rb"q ([-0-9.e ]+) cm /\w+ Do Q", data)[0]
        return float(first.split()[0])

    assert scale(spaced) < scale(plain)


def test_a_margin_that_leaves_no_room_is_refused():
    document = _source(4)
    with pytest.raises(PdfValidationException, match="no room"):
        document.n_up(2, 2, page_size=(100, 100), margin=60)


def test_the_grid_has_to_be_a_grid():
    document = _source(2)
    for rows, columns in ((0, 2), (2, 0), (-1, 1)):
        with pytest.raises(PdfValidationException, match="at least one"):
            document.n_up(rows, columns)
    with pytest.raises(PdfValidationException, match="negative"):
        document.n_up(2, 2, margin=-1)
    with pytest.raises(PdfValidationException, match="'row' or 'column'"):
        document.n_up(2, 2, order="diagonal")


def test_imposing_a_document_with_no_pages_is_refused():
    with pytest.raises(PdfValidationException, match="no pages"):
        Document().n_up(2, 2)
    with pytest.raises(PdfValidationException, match="no pages"):
        Document().booklet()


def test_the_imposed_document_is_independent_of_the_one_it_came_from():
    document = _source(4)
    imposed = document.n_up(2, 2)
    document.dispose()
    assert "PAGE 1" in _roundtrip(imposed).pages[0].extract_text()


# ---------------------------------------------------------------------------
# Booklets
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("count", "order"),
    [
        (4, [3, 0, 1, 2]),
        (8, [7, 0, 1, 6, 5, 2, 3, 4]),
        (2, [None, 0, 1, None]),
        (6, [None, 0, 1, None, 5, 2, 3, 4]),
    ],
)
def test_the_saddle_stitch_order(count, order):
    # The last page beside the first, the second beside the second-last, each
    # pair starting on the other side -- and padded to a multiple of four,
    # because a folded sheet carries four pages whether they are used or not.
    assert booklet_order(count) == order


def test_a_booklet_is_two_pages_to_a_sheet():
    document = _source(4)
    imposed = document.booklet()
    assert imposed.page_count == 2
    assert imposed.pages[0].size.width == pytest.approx(PageSize.A5.width * 2)
    assert imposed.pages[0].size.height == pytest.approx(PageSize.A5.height)


def test_a_booklet_pads_to_a_multiple_of_four():
    document = _source(6)
    imposed = _roundtrip(document.booklet())
    assert imposed.page_count == 4
    sheets = [page.extract_text().split() for page in imposed.pages]
    assert sheets[0] == ["PAGE", "1"]  # the blank is the other half
    assert sheets[1] == ["PAGE", "2"]
    assert sheets[2] == ["PAGE", "6", "PAGE", "3"]
    assert sheets[3] == ["PAGE", "4", "PAGE", "5"]


def test_the_two_pages_of_a_booklet_sheet_are_side_by_side():
    document = _source(4)
    imposed = document.booklet()
    left, right = _placements(imposed)[:2]
    assert right[0] > left[0]
    assert right[1] == pytest.approx(left[1])


def test_a_booklet_sheet_size_can_be_given():
    document = _source(4)
    imposed = document.booklet(page_size=PageSize.A4.landscape(), margin=10)
    assert imposed.pages[0].size == PageSize.A4.landscape()


def test_a_booklet_of_one_page_is_one_sheet_of_four_places():
    document = _source(1)
    imposed = document.booklet()
    assert imposed.page_count == 2  # four places, two sheets
    assert "PAGE 1" in _roundtrip(imposed).pages[0].extract_text()


# ---------------------------------------------------------------------------
# The facade
# ---------------------------------------------------------------------------


def test_the_facade_imposes_files(tmp_path):
    source = tmp_path / "source.pdf"
    _source(5).save(source)
    editor = PdfFileEditor()
    n_up = tmp_path / "nup.pdf"
    booklet = tmp_path / "booklet.pdf"
    assert editor.make_n_up(str(source), str(n_up), 2, 2, margin=8) is True
    assert editor.make_booklet(str(source), str(booklet)) is True
    assert Document(n_up).page_count == 2
    assert Document(booklet).page_count == 4


def test_the_facade_reports_a_failure_rather_than_raising(tmp_path):
    editor = PdfFileEditor()
    assert editor.make_n_up("no-such-file.pdf", str(tmp_path / "out.pdf")) is False
    assert isinstance(editor.last_exception, Exception)
    assert editor.make_booklet("no-such-file.pdf", str(tmp_path / "out2.pdf")) is False


# ---------------------------------------------------------------------------
# A text stamp in an embedded font
# ---------------------------------------------------------------------------


def _font_bytes() -> bytes:
    """A real font to embed, from the bundled substitutes."""
    from aspose_pdf.engine.std_font_data import load_substitute_sfnt

    data = load_substitute_sfnt("sans-regular")
    if not data:
        pytest.skip("no bundled substitute font to embed")
    return data


def test_a_text_stamp_can_be_set_in_an_embedded_font():
    from aspose_pdf import TextStamp

    document = _source(1)
    document.add_stamp(TextStamp("Hello", font_size=24, font=_font_bytes()))
    data = _saved(document)
    assert b"/Type0" in data
    assert b"/FontFile2" in data or b"/FontFile3" in data


def test_an_embedded_stamp_font_writes_what_a_standard_font_cannot():
    # WinAnsiEncoding has no code for "ł" or "ę", which is the case the embedded
    # path exists for: the same text is refused without a font to embed, below.
    from aspose_pdf import TextStamp

    document = _source(1)
    document.add_stamp(TextStamp("Przegląd łąki", font_size=20, font=_font_bytes()))
    assert b"/Type0" in _saved(document)


def test_a_standard_font_stamp_still_refuses_what_it_cannot_write():
    from aspose_pdf import TextStamp

    document = _source(1)
    with pytest.raises(PdfValidationException, match="cannot write"):
        document.add_stamp(TextStamp("Przegląd łąki"))


def test_an_embedded_stamp_font_ignores_the_standard_font_name():
    from aspose_pdf import TextStamp

    document = _source(1)
    # font_name names one of the 14; with a font to embed it has nothing to say,
    # and must not be validated against that list.
    document.add_stamp(
        TextStamp("x", font_name="Whatever Sans", font=_font_bytes())
    )
    assert b"/Type0" in _saved(document)
