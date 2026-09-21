"""A found text fragment says where it is and what it is set in.

``TextFragment`` carried a page index, the text and its offsets in the page's
text -- nothing about the page. It now also carries ``rect`` and ``quads``
(default user space), ``font_name``, ``font_size`` and ``color``, taken from
the page's content stream by the locator the redactor uses.

The boxes were compared with MuPDF (``Page.search_for``) and pdfium
(``FPDFText_GetRect``) on the fixture below. Horizontally the three agree to
hundredths of a point; vertically a box is the font's ascent to its descent,
which sits inside MuPDF's line box and around pdfium's glyph ink -- the numbers
each reference gave are asserted against directly.

A fragment is placed only when the content holds exactly as many matches of its
text as the page's text did, and every one of them can be tracked: text the
extractor assembled from several runs, or drawn with a font the page does not
declare, is left unplaced rather than given someone else's box.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document
from aspose_pdf.text import TextFragment, TextFragmentAbsorber, TextSearchOptions


def _reloaded(document: Document) -> Document:
    buffer = io.BytesIO()
    document.save(buffer)
    return Document(io.BytesIO(buffer.getvalue()))


@pytest.fixture(scope="module")
def invoice() -> Document:
    document = Document()
    first, second = document.pages.add(), document.pages.add()
    first.add_text("Invoice total", 72, 700, font_size=18, color=(1, 0, 0))
    first.add_text("paid in full, paid twice", 72, 660, font_size=10)
    second.add_text("Invoice total", 100, 500, font_size=12)
    return _reloaded(document)


def _fragments(target, phrase=None, **kwargs):
    absorber = TextFragmentAbsorber(phrase, **kwargs)
    absorber.visit(target)
    return absorber.text_fragments


# What MuPDF's search_for and pdfium's FPDFText_GetRect report for the fixture.
MUPDF = {
    ("total", 0): (72.0, 694.618, 168.048, 719.35),
    ("paid", 0): [(72.0, 657.01, 90.9, 670.75), (122.58, 657.01, 141.48, 670.75)],
    ("total", 1): (100.0, 496.412, 164.032, 512.9),
}
PDFIUM = {
    ("total", 0): (73.674, 699.802, 166.788, 712.888),
    ("paid", 0): [(72.66, 658.02, 90.18, 667.16), (123.24, 658.02, 140.76, 667.16)],
    ("total", 1): (101.116, 499.868, 163.192, 508.592),
}


def _agrees(rect, mupdf, pdfium) -> bool:
    """The box is MuPDF's horizontally, and between pdfium's ink and MuPDF's line."""
    return (
        abs(rect[0] - mupdf[0]) <= 0.05
        and abs(rect[2] - mupdf[2]) <= 0.05
        and mupdf[1] - 0.01 <= rect[1] <= pdfium[1] + 0.01
        and pdfium[3] - 0.01 <= rect[3] <= mupdf[3] + 0.01
    )


def test_a_found_phrase_knows_its_place(invoice):
    [fragment] = _fragments(invoice.pages[0], "Invoice total")
    assert _agrees(fragment.rect, MUPDF[("total", 0)], PDFIUM[("total", 0)])
    assert (fragment.font_name, fragment.font_size, fragment.color) == ("F1", 18.0, (1.0, 0.0, 0.0))
    assert fragment.quads == [(
        (fragment.rect[0], fragment.rect[1]),
        (fragment.rect[2], fragment.rect[1]),
        (fragment.rect[2], fragment.rect[3]),
        (fragment.rect[0], fragment.rect[3]),
    )]
    assert "rect=" in repr(fragment)


def test_each_occurrence_gets_its_own_box(invoice):
    fragments = _fragments(invoice.pages[0], "paid")
    assert len(fragments) == 2
    for fragment, mupdf, pdfium in zip(fragments, MUPDF[("paid", 0)], PDFIUM[("paid", 0)]):
        assert _agrees(fragment.rect, mupdf, pdfium)
        assert (fragment.font_size, fragment.color) == (10.0, (0.0, 0.0, 0.0))


def test_a_whole_document_places_every_page(invoice):
    fragments = _fragments(invoice, "Invoice total")
    assert [fragment.page_index for fragment in fragments] == [0, 1]
    assert _agrees(fragments[0].rect, MUPDF[("total", 0)], PDFIUM[("total", 0)])
    assert _agrees(fragments[1].rect, MUPDF[("total", 1)], PDFIUM[("total", 1)])
    assert [fragment.font_size for fragment in fragments] == [18.0, 12.0]


def test_lines_and_regular_expressions_are_placed_too(invoice):
    lines = _fragments(invoice.pages[0])
    assert [fragment.text for fragment in lines] == ["Invoice total", "paid in full, paid twice"]
    assert _agrees(lines[0].rect, MUPDF[("total", 0)], PDFIUM[("total", 0)])
    assert lines[1].rect[0] == pytest.approx(72.0) and lines[1].font_size == 10.0

    options = TextSearchOptions(is_regular_expression=True)
    [fragment] = _fragments(invoice.pages[1], r"Invoice\s+total", text_search_options=options)
    assert _agrees(fragment.rect, MUPDF[("total", 1)], PDFIUM[("total", 1)])


def test_a_case_insensitive_match_is_placed_by_the_text_it_found(invoice):
    options = TextSearchOptions(case_sensitive=False)
    [fragment] = _fragments(invoice.pages[1], "INVOICE TOTAL", text_search_options=options)
    assert fragment.text == "Invoice total"
    assert _agrees(fragment.rect, MUPDF[("total", 1)], PDFIUM[("total", 1)])


# --- colour -------------------------------------------------------------------------------


def _with_content(content: bytes) -> Document:
    """A one-page document whose content stream is *content*, with /F1 declared."""
    document = Document()
    document.pages.add().add_text("seed", 72, 740, font_size=12)
    document = _reloaded(document)
    document._engine_pdf._set_page_content(0, content)
    return _reloaded(document)


@pytest.mark.parametrize(
    ("painting", "expected"),
    [
        (b"0 0 1 rg", (0.0, 0.0, 1.0)),
        (b"0.5 g", (0.5, 0.5, 0.5)),
        (b"0 1 1 0 k", (1.0, 0.0, 0.0)),
    ],
    ids=["rgb", "gray", "cmyk"],
)
def test_the_colour_is_the_one_the_glyphs_are_painted_in(painting, expected):
    # pdfium renders these pages' glyphs as (0, 0, 255) and (128, 128, 128);
    # the CMYK red it paints is (238, 29, 35), its own conversion table against
    # the plain formula used here.
    document = _with_content(b"BT " + painting + b" /F1 12 Tf 72 700 Td (Coloured) Tj ET")
    [fragment] = _fragments(document.pages[0], "Coloured")
    assert fragment.color == pytest.approx(expected, abs=0.01)


def test_outlined_text_reports_its_stroke_colour():
    # Rendering mode 1 paints the outline only: pdfium draws it (255, 0, 0).
    document = _with_content(b"BT 1 0 0 RG 1 Tr /F1 12 Tf 72 700 Td (Outline) Tj ET")
    [fragment] = _fragments(document.pages[0], "Outline")
    assert fragment.color == pytest.approx((1.0, 0.0, 0.0), abs=0.01)


def test_the_font_is_the_one_the_match_starts_in():
    # Two show operators on one baseline are one run; each keeps its own /Tf.
    document = _with_content(b"BT /F1 20 Tf 72 700 Td (Big) Tj /F1 10 Tf (small) Tj ET")
    assert _fragments(document.pages[0], "Big")[0].font_size == 20.0
    assert _fragments(document.pages[0], "small")[0].font_size == 10.0


def test_the_colour_comes_from_the_smallest_box_around_the_match():
    document = _with_content(
        b"BT 0 0 1 rg /F1 30 Tf 72 700 Td (WIDE TEXT HERE) Tj ET "
        b"BT 1 0 0 rg /F1 8 Tf 220 710 Td (tiny) Tj ET"
    )
    assert _fragments(document.pages[0], "tiny")[0].color == pytest.approx((1.0, 0.0, 0.0))
    assert _fragments(document.pages[0], "WIDE")[0].color == pytest.approx((0.0, 0.0, 1.0))


# --- what cannot be placed ------------------------------------------------------------------


def test_text_the_locator_cannot_follow_is_left_unplaced():
    # A font the page's resources do not declare: the extractor still reads
    # the bytes, but nothing can measure where the glyphs went.
    document = _with_content(b"BT /Nosuch 12 Tf 72 700 Td (Untracked) Tj ET")
    [fragment] = _fragments(document.pages[0], "Untracked")
    assert fragment.text == "Untracked"
    assert (fragment.rect, fragment.quads, fragment.font_size, fragment.color) == (None, None, None, None)


def test_a_fragment_split_across_runs_is_left_unplaced():
    # Two show operators far apart on one line: the extractor joins them into
    # one line, the content holds no single match of it.
    document = _with_content(
        b"BT /F1 12 Tf 72 700 Td (Left) Tj ET BT /F1 12 Tf 300 700 Td (Right) Tj ET"
    )
    [fragment] = _fragments(document.pages[0])
    assert "Left" in fragment.text and "Right" in fragment.text
    assert fragment.rect is None
    # Each part on its own is placed.
    assert _fragments(document.pages[0], "Right")[0].rect[0] == pytest.approx(300.0)


def test_a_run_whose_pen_is_lost_places_none_of_itself():
    # The second show operator's font is not declared, so the pen stops being
    # trackable; the redactor draws no bar for such a run, and nor is a
    # fragment placed from it -- not even the part before the break.
    document = _with_content(b"BT /F1 12 Tf 72 700 Td (Alpha) Tj /Nosuch 12 Tf (Beta) Tj ET")
    assert _fragments(document.pages[0], "Alpha")[0].rect is None


def test_a_page_that_shows_a_match_fewer_times_than_its_text_places_none():
    from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName, PdfStream

    document = Document()
    document.pages.add().add_text("Repeat", 72, 700, font_size=12)
    document = _reloaded(document)
    engine = document._engine_pdf
    page = engine._get_page_dict(0)
    resources = engine._resolve(page.mapping[PdfName("Resources")])
    # A form XObject drawing the same word: the extractor reads it, the
    # locator walks the page's own content only.
    form = PdfStream(
        b"BT /F1 12 Tf 300 500 Td (Repeat) Tj ET",
        {
            PdfName("Type"): PdfName("XObject"), PdfName("Subtype"): PdfName("Form"),
            PdfName("BBox"): PdfArray([PdfName("0")] * 0 + []),
            PdfName("Resources"): resources,
        },
    )
    form.mapping[PdfName("BBox")] = PdfArray([])
    reference = engine._cos_doc.register_object(form)
    resources.mapping[PdfName("XObject")] = PdfDictionary({PdfName("Fm0"): reference})
    engine._set_page_content(0, engine.get_page_content(0) + b"\nq /Fm0 Do Q")
    document = _reloaded(document)

    fragments = _fragments(document.pages[0], "Repeat")
    assert len(fragments) == 2  # the extractor reads both
    assert [fragment.rect for fragment in fragments] == [None, None]


def test_text_read_from_something_that_is_not_a_page_stays_unplaced(invoice):
    absorber = TextFragmentAbsorber("Invoice")
    absorber.visit(invoice._engine_pdf)  # the engine, which has no pages to place on
    assert [fragment.text for fragment in absorber.text_fragments] == ["Invoice", "Invoice"]
    assert [fragment.rect for fragment in absorber.text_fragments] == [None, None]


def test_the_geometry_does_not_change_what_a_fragment_is():
    plain = TextFragment(page_index=0, text="x", start=1)
    assert (plain.rect, plain.quads, plain.font_name, plain.font_size, plain.color) == (None,) * 5
    assert plain == TextFragment(page_index=0, text="x", start=1, end=2)
    assert repr(plain) == "TextFragment(page=0, text='x', start=1, end=2)"
