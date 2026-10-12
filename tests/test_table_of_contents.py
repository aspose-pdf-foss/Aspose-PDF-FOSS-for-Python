"""Putting a contents page in front of a document.

``generate_outlines`` already turns the document's headings into bookmarks.
This writes the same answer onto paper: the headings down a page, indented by
level, each with the page it is on and a link to it.

The thing that makes it more than a list is that **the page numbers move**.
Inserting the contents pushes every page it points at further back, so the
numbers cannot be read off the document as it stands -- and where the document
has page labels, the number a reader sees is the label, not the position. Those
two, and the links landing on the heading rather than on the top of its sheet,
are what most of these tests are about.
"""

from __future__ import annotations

import io
import re

import pytest

from aspose_pdf import Document, NumberingStyle, PageLabel, PageSize
from aspose_pdf.engine.cos import PdfName
from aspose_pdf.engine.text_faces import _StandardFace
from aspose_pdf.engine.toc import _fit, _leader
from aspose_pdf.exceptions import PdfValidationException

CHAPTERS = [
    (1, "Introduction"),
    (2, "Background"),
    (2, "Scope"),
    (1, "Method"),
    (3, "A nested detail"),
    (1, "Results"),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _reopen(document: Document) -> Document:
    buffer = io.BytesIO()
    document.save(buffer)
    document.close()
    return Document(buffer.getvalue())


def _tagged(chapters=CHAPTERS, *, cover: bool = False, labels: bool = False):
    """A document whose headings are tagged, so the levels are its own answer."""
    document = Document()
    if cover:
        document.pages.add(PageSize.A4).add_text("COVER", 60, 400, font_size=40)
    for level, title in chapters:
        page = document.pages.add(PageSize.A4)
        page.add_text(title, 60, 740, font_size=20, tag=f"H{level}")
        page.add_text("Body text.", 60, 700, font_size=11, tag="P")
    if labels:
        document.page_labels[0] = PageLabel(
            style=NumberingStyle.NUMERALS_ROMAN_LOWERCASE
        )
    return _reopen(document)


def _untagged():
    """Headings found by the size tiers, which need something to contrast with."""
    document = Document()
    for index in range(3):
        page = document.pages.add(PageSize.A4)
        page.add_text(f"Chapter {index + 1}", 60, 740, font_size=20)
        page.add_text("Body text at a smaller size, several words.", 60, 700, font_size=11)
    return _reopen(document)


def _lines(document: Document, index: int = 0) -> list[str]:
    """The contents page's lines, without the rows of leader dots."""
    text = document.pages[index].extract_text()
    return [line for line in text.split("\n") if line.strip() and "...." not in line]


def _links(document: Document, index: int = 0) -> list[tuple]:
    """``(page object number, kind, left, top)`` for each link on a page."""
    engine = document._engine_pdf
    annots = engine._resolve(engine._get_page_dict(index).get(PdfName("Annots")))
    out = []
    for reference in getattr(annots, "items", []):
        annotation = engine._resolve(reference)
        destination = engine._resolve(annotation.mapping.get(PdfName("Dest")))
        items = list(getattr(destination, "items", []))
        if not items:
            continue
        out.append(
            (
                items[0].object_number,
                engine._get_name(items[1]),
                *[getattr(value, "value", None) for value in items[2:4]],
            )
        )
    return out


# ---------------------------------------------------------------------------
# What is written
# ---------------------------------------------------------------------------


def test_a_contents_page_lists_the_documents_headings():
    with _tagged() as document:
        assert document.add_table_of_contents() == [0]
        lines = _lines(document)
        assert lines[0] == "Contents"
        assert [line.rsplit(" ", 1)[0] for line in lines[1:]] == [
            title for _level, title in CHAPTERS
        ]


def test_the_pages_it_took_are_what_comes_back():
    with _tagged() as document:
        before = len(document.pages)
        made = document.add_table_of_contents()
        assert made == [0]
        assert len(document.pages) == before + len(made)


def test_a_document_with_no_headings_gets_no_page():
    """A contents listing nothing is not worth a sheet of paper."""
    document = Document()
    document.pages.add(PageSize.A4).add_text("No headings here", 60, 700, font_size=11)
    with _reopen(document) as saved:
        before = len(saved.pages)
        assert saved.add_table_of_contents() == []
        assert len(saved.pages) == before


def test_headings_found_by_reading_the_pages_work_too():
    with _untagged() as document:
        assert document.add_table_of_contents() == [0]
        assert [line.rsplit(" ", 1)[0] for line in _lines(document)[1:]] == [
            "Chapter 1",
            "Chapter 2",
            "Chapter 3",
        ]


def test_a_long_contents_runs_onto_further_pages():
    chapters = [(1, f"Section {index + 1}") for index in range(60)]
    with _tagged(chapters) as document:
        made = document.add_table_of_contents()
        assert len(made) > 1
        first = _lines(document, made[0])
        second = _lines(document, made[1])
        assert first[0] == "Contents"
        assert second[0].startswith("Section ")
        # Every heading is listed once, across the pages.
        listed = [
            line.rsplit(" ", 1)[0]
            for page in made
            for line in _lines(document, page)
            if line != "Contents"
        ]
        assert listed == [title for _level, title in chapters]


# ---------------------------------------------------------------------------
# The page numbers
# ---------------------------------------------------------------------------


def test_the_numbers_count_the_contents_itself():
    """The first chapter is on page 2 once a contents page sits in front of it."""
    with _tagged([(1, "Only chapter")]) as document:
        document.add_table_of_contents()
        assert _lines(document)[1] == "Only chapter 2"


def test_a_contents_after_a_cover_leaves_the_cover_where_it_was():
    with _tagged([(1, "First"), (1, "Second")], cover=True) as document:
        assert document.add_table_of_contents(at=1) == [1]
        assert [page.extract_text().split("\n")[0] for page in document.pages] == [
            "COVER",
            "Contents",
            "First",
            "Second",
        ]
        assert _lines(document, 1)[1:] == ["First 3", "Second 4"]


def test_the_number_shown_is_the_page_label_where_there_is_one():
    """A contents saying 12 for a page the reader sees as xii is wrong."""
    with _tagged([(1, "First"), (1, "Second")], labels=True) as document:
        document.add_table_of_contents()
        shown = [line.rsplit(" ", 1)[1] for line in _lines(document)[1:]]
        assert shown == [document.pages[1].label, document.pages[2].label]
        assert shown == ["i", "ii"]


def test_the_numbers_can_be_left_off():
    with _tagged([(1, "Only chapter")]) as document:
        document.add_table_of_contents(page_numbers=False)
        assert _lines(document)[1] == "Only chapter"


# ---------------------------------------------------------------------------
# The links
# ---------------------------------------------------------------------------


def test_each_line_links_to_its_heading_not_to_the_top_of_the_page():
    with _tagged([(1, "Only chapter")]) as document:
        document.add_table_of_contents()
        (links,) = _links(document)
        _object, kind, left, top = links
        assert kind == "XYZ"
        assert (left, top) == (60.0, 740.0), "the heading's own place"


def test_one_link_per_entry_pointing_at_the_right_pages():
    with _tagged() as document:
        document.add_table_of_contents()
        links = _links(document)
        assert len(links) == len(CHAPTERS)
        engine = document._engine_pdf
        targets = [
            engine._cos_doc.reference(engine._page_refs[index + 1]).object_number
            for index in range(len(CHAPTERS))
        ]
        assert [link[0] for link in links] == targets


def test_a_heading_with_no_place_links_to_its_page():
    with _tagged([(1, "Only chapter")]) as document:
        from aspose_pdf.engine import toc

        original = toc.find_headings

        def without_place(pdf, **kwargs):
            import dataclasses

            return [
                dataclasses.replace(heading, x=None, y=None)
                for heading in original(pdf, **kwargs)
            ]

        toc.find_headings = without_place
        try:
            document.add_table_of_contents()
        finally:
            toc.find_headings = original
        (link,) = _links(document)
        assert link[1] == "FitH"


def test_the_links_can_be_left_off():
    with _tagged() as document:
        document.add_table_of_contents(links=False)
        assert _links(document) == []


# ---------------------------------------------------------------------------
# The look of it
# ---------------------------------------------------------------------------


def test_deeper_headings_are_indented_further():
    with _tagged() as document:
        document.add_table_of_contents(indent=20.0)
        engine = document._engine_pdf
        content = engine.get_page_content(0).decode("latin-1")
        # Each run is placed by a Tm; the x of the one showing a heading's
        # text is where that level starts.
        starts = {
            text: float(x)
            for x, text in re.findall(
                r"1 0 0 1 ([\d.]+) [\d.]+ Tm \((.*?)\) Tj", content
            )
        }
        assert starts["Introduction"] == 72.0, "level 1 starts at the margin"
        assert starts["Background"] == 92.0, "level 2 is one indent in"
        assert starts["A nested detail"] == 112.0, "level 3 is two"


def test_the_sheet_matches_the_document_by_default():
    with _tagged([(1, "Only chapter")]) as document:
        body = document.pages[0].rect
        document.add_table_of_contents()
        assert document.pages[0].rect == body


def test_a_sheet_can_be_asked_for():
    with _tagged([(1, "Only chapter")]) as document:
        document.add_table_of_contents(size=(300, 400))
        _llx, _lly, urx, ury = document.pages[0].rect
        assert (round(urx), round(ury)) == (300, 400)


def test_the_title_can_be_left_off():
    with _tagged([(1, "Only chapter")]) as document:
        document.add_table_of_contents(title=None)
        assert _lines(document)[0] == "Only chapter 2"


def test_the_leader_can_be_changed_or_dropped():
    with _tagged([(1, "Only chapter")]) as document:
        document.add_table_of_contents(leader="")
        assert "...." not in document.pages[0].extract_text()

    with _tagged([(1, "Only chapter")]) as document:
        document.add_table_of_contents(leader="-")
        assert "----" in document.pages[0].extract_text()


# ---------------------------------------------------------------------------
# Measuring: the two pieces that have to fit
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def face():
    document = Document()
    document.pages.add(PageSize.A4)
    saved = _reopen(document)
    yield _StandardFace(saved._engine_pdf, "Helvetica")
    saved.close()


@pytest.mark.parametrize("available", [300, 200, 120, 40, 26])
def test_a_heading_too_long_for_the_line_is_shortened_to_fit(face, available):
    long = "A heading so long that it cannot possibly fit on the contents line"
    fitted = _fit(face, long, available, 11)
    assert face.width(fitted, 11) <= available
    assert fitted.endswith("...")
    assert long.startswith(fitted[:-3].rstrip())


def test_a_heading_that_fits_is_left_alone(face):
    assert _fit(face, "Short", 400, 11) == "Short"


def test_a_line_with_no_room_at_all_shows_nothing(face):
    assert _fit(face, "Anything", 5, 11) == ""
    assert _fit(face, "Anything", 0, 11) == ""


def test_the_leader_ends_against_the_page_number(face):
    """Right-aligned, so the dots line up down the page."""
    run, x = _leader(face, ".", 100.0, 300.0, 11)
    assert run and set(run) == {"."}
    assert x + face.width(run, 11) == pytest.approx(300.0, abs=0.01)
    assert x >= 100.0


def test_no_leader_fits_in_no_gap(face):
    assert _leader(face, ".", 300.0, 300.0, 11) is None
    assert _leader(face, ".", 300.0, 100.0, 11) is None


# ---------------------------------------------------------------------------
# Tagging
# ---------------------------------------------------------------------------


def test_the_entries_can_be_tagged_as_a_table_of_contents():
    """Table 333: a /TOCI is an item *of* a /TOC, not an orphan at the root."""
    with _tagged([(1, "First"), (1, "Second")]) as document:
        document.add_table_of_contents(tag=True)
        with _reopen(document) as saved:
            roots = saved.tagged_content.root_elements
            containers = [e for e in roots if e.structure_type == "TOC"]
            assert len(containers) == 1
            assert [child.structure_type for child in containers[0].children] == [
                "TOCI",
                "TOCI",
            ]
            assert not [e for e in roots if e.structure_type == "TOCI"]


def test_the_title_is_read_before_the_list_it_heads():
    with _tagged([(1, "First")]) as document:
        document.add_table_of_contents(tag=True)
        with _reopen(document) as saved:
            types = [e.structure_type for e in saved.tagged_content.root_elements]
            assert types.index("TOC") == len(types) - 1
            assert types[-2] == "H1", "the Contents title comes first"


def test_tagging_is_off_by_default():
    with _tagged([(1, "First")]) as document:
        document.add_table_of_contents()
        with _reopen(document) as saved:
            types = [e.structure_type for e in saved.tagged_content.root_elements]
            assert "TOC" not in types


# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"margin": -1},
        {"title_size": -1},
        {"entry_size": -1},
        {"entry_size": 0},
        {"indent": -5},
        {"leader": 5},
    ],
)
def test_a_measurement_a_page_cannot_have_is_refused(kwargs):
    with _tagged([(1, "First")]) as document, pytest.raises(PdfValidationException):
        document.add_table_of_contents(**kwargs)


@pytest.mark.parametrize("at", [-5, 99])
def test_a_position_outside_the_document_is_clamped(at):
    with _tagged([(1, "First"), (1, "Second")]) as document:
        made = document.add_table_of_contents(at=at)
        assert made == [0] if at < 0 else made == [2]


def test_only_headings_up_to_the_level_asked_for_are_listed():
    with _tagged() as document:
        document.add_table_of_contents(max_level=1)
        listed = [line.rsplit(" ", 1)[0] for line in _lines(document)[1:]]
        assert listed == ["Introduction", "Method", "Results"]


def test_a_disposed_document_refuses():
    document = _tagged([(1, "First")])
    document.close()
    with pytest.raises(Exception, match=r"(?i)dispos"):
        document.add_table_of_contents()
