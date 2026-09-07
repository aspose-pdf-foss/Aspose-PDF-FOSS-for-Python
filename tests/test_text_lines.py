"""Getting a page's text out, in the lines it was written in.

Two faults met here. The extractor answered every text-positioning operator
with a space -- ``Td``, ``TD``, ``Tm`` and ``T*`` alike -- although those four
both start a new line and shift along the one in progress. The line structure of
the page was thrown away at exactly the point where it was known, so a page of
prose came out as one very long line.

And ``TextFragmentAbsorber`` -- the documented way to collect text -- looked for
an ``extract_text()`` method that no real ``Page`` had. It collected nothing
from any document. A hundred tests passed on it because every one of them fed it
a stub with a ``.text`` attribute; not one used a ``Document``.

The line breaks come from the vertical displacement now, and pages and documents
have the accessor the absorber was always asking for.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document
from aspose_pdf.text import TextAbsorber, TextFragmentAbsorber


def _reloaded(document: Document) -> Document:
    buffer = io.BytesIO()
    document.save(buffer)
    return Document(io.BytesIO(buffer.getvalue()))


def _lines(*rows: str, gap: float = 17, size: float = 14) -> Document:
    document = Document()
    page = document.pages.add()
    for index, text in enumerate(rows):
        page.add_text(text, 72, 700 - index * gap, font_size=size)
    return _reloaded(document)


# ---------------------------------------------------------------------------
# Lines
# ---------------------------------------------------------------------------


def test_each_line_is_a_line():
    assert _lines("L0", "L1", "L2").pages[0].extract_text() == "L0\nL1\nL2"


@pytest.mark.parametrize("gap", [11, 14, 17, 20, 24, 40])
def test_the_leading_does_not_decide_whether_it_is_a_line(gap):
    """Single-spaced prose is about 1.2x the size; a page of it used to come
    out as one line because the separator was chosen by distance."""
    assert _lines("A", "B", gap=gap).pages[0].extract_text() == "A\nB"


def test_text_moved_along_the_same_line_stays_on_it():
    document = Document()
    page = document.pages.add()
    page.add_text("left", 72, 700, font_size=14)
    page.add_text("right", 300, 700, font_size=14)

    assert _reloaded(document).pages[0].extract_text() == "left right"


def test_a_paragraph_is_still_joined_when_asked_for_markdown():
    """Where the lines go is `to_markdown`'s judgement, not the extractor's:
    a wrapped paragraph is one paragraph."""
    assert _lines("wrapped", "over", "lines").pages[0].to_markdown().strip() == (
        "wrapped over lines"
    )


def test_pages_are_separated_in_the_document_s_text():
    document = Document()
    document.pages.add().add_text("one", 72, 700, font_size=14)
    document.pages.add().add_text("two", 72, 700, font_size=14)

    assert _reloaded(document).extract_text() == "one\ntwo"


def test_a_page_with_nothing_on_it_says_nothing():
    document = Document()
    document.pages.add()

    assert _reloaded(document).pages[0].extract_text() == ""


def test_an_index_outside_the_document_says_nothing():
    engine = _lines("a")._engine_pdf

    assert engine.extract_page_text(9) == ""
    assert engine.extract_page_text(-1) == ""


def _from_content(content: bytes) -> Document:
    """A page whose content stream is written out by hand.

    This library's own writer positions every run with `Tm`. The operators most
    other producers use for lines -- `Td`, `TD`, `T*` and the `TL` that feeds
    them -- only ever arrive in a file someone else wrote.
    """
    raw = (
        b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >> endobj\n"
        b"4 0 obj << /Length "
        + str(len(content)).encode()
        + b" >> stream\n"
        + content
        + b"endstream endobj\n"
        b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >> endobj\n"
        b"trailer << /Root 1 0 R /Size 6 >>\n%%EOF\n"
    )
    return Document(io.BytesIO(raw))


def test_td_with_a_vertical_move_starts_a_line():
    page = _from_content(
        b"BT /F1 12 Tf 72 700 Td (one) Tj 0 -14 Td (two) Tj ET\n"
    ).pages[0]

    assert page.extract_text() == "one\ntwo"


def test_td_along_the_line_does_not():
    page = _from_content(
        b"BT /F1 12 Tf 72 700 Td (one) Tj 40 0 Td (two) Tj ET\n"
    ).pages[0]

    assert page.extract_text() == "one two"


def test_t_star_steps_down_by_the_leading():
    page = _from_content(
        b"BT /F1 12 Tf 14 TL 72 700 Td (one) Tj T* (two) Tj T* (three) Tj ET\n"
    ).pages[0]

    assert page.extract_text() == "one\ntwo\nthree"


def test_t_star_without_a_leading_stays_on_the_line():
    """`TL` defaults to zero, so `T*` moves nowhere until something sets it."""
    page = _from_content(b"BT /F1 12 Tf 72 700 Td (one) Tj T* (two) Tj ET\n").pages[0]

    assert page.extract_text() == "one two"


def test_td_capital_sets_the_leading_as_well_as_moving():
    page = _from_content(
        b"BT /F1 12 Tf 72 700 Td (one) Tj 0 -14 TD (two) Tj T* (three) Tj ET\n"
    ).pages[0]

    assert page.extract_text() == "one\ntwo\nthree"


def test_the_quote_operators_show_on_the_next_line():
    page = _from_content(
        b"BT /F1 12 Tf 14 TL 72 700 Td (one) Tj (two) ' 1 2 (three) \" ET\n"
    ).pages[0]

    assert page.extract_text() == "one\ntwo\nthree"


# ---------------------------------------------------------------------------
# The absorbers, against real pages
# ---------------------------------------------------------------------------


def test_an_absorber_collects_a_real_page_s_lines():
    absorber = TextFragmentAbsorber()

    absorber.visit(_lines("L0", "L1", "L2").pages[0])

    assert [fragment.text for fragment in absorber.text_fragments] == ["L0", "L1", "L2"]


def test_an_absorber_collects_a_real_document():
    document = Document()
    document.pages.add().add_text("first", 72, 700, font_size=14)
    document.pages.add().add_text("second", 72, 700, font_size=14)
    absorber = TextFragmentAbsorber()

    absorber.visit(_reloaded(document))

    assert [(f.text, f.page_index) for f in absorber.text_fragments] == [
        ("first", 0),
        ("second", 1),
    ]


def test_a_document_is_taken_page_by_page_so_a_fragment_knows_where_it_was():
    """Asking the document for all of its text at once would put every
    fragment on page 0, which is the one thing a page index must not do."""
    document = Document()
    for index in range(3):
        document.pages.add().add_text(f"p{index}", 72, 700, font_size=14)
    absorber = TextFragmentAbsorber()

    absorber.visit(_reloaded(document))

    assert [f.page_index for f in absorber.text_fragments] == [0, 1, 2]


def test_a_phrase_search_finds_it_on_a_real_page():
    absorber = TextFragmentAbsorber("L1")

    absorber.visit(_lines("L0", "L1", "L2").pages[0])

    assert [fragment.text for fragment in absorber.text_fragments] == ["L1"]


def test_the_legacy_absorber_reads_a_real_page_too():
    absorber = TextAbsorber()

    absorber.visit(_lines("L0", "L1").pages[0])

    assert absorber.text == "L0\nL1"


def test_the_engine_is_read_as_a_whole_since_its_pages_are_rectangles():
    """`SimplePdf.pages` is a list of media boxes, not of pages, so walking it
    would collect nothing: it answers for its own text instead."""
    absorber = TextFragmentAbsorber()

    absorber.visit(_lines("L0", "L1")._engine_pdf)

    assert [fragment.text for fragment in absorber.text_fragments] == ["L0", "L1"]


def test_something_with_no_text_at_all_is_simply_empty():
    absorber = TextFragmentAbsorber()

    absorber.visit(object())

    assert absorber.text_fragments == []
