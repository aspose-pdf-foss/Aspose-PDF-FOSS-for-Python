"""Building a bookmark tree from a document's headings.

Two sources, and which one is used matters: a tagged document has been *told*
what its headings are, an untagged one has to be read with the same size-tier
heuristic the Markdown export uses. These tests cover both, the nesting
(including a skipped level), the place each bookmark lands on -- the heading
itself, not the top of its page -- and what happens when there are no headings
to find.
"""

from __future__ import annotations

import io
import re

import pytest

from aspose_pdf import Document, OutlineItem, PageSize, TextBlock
from aspose_pdf.engine.auto_tag import mcid_spans
from aspose_pdf.engine.cos import PdfName
from aspose_pdf.engine.headings import MAX_HEADING_LEVEL, Heading, find_headings
from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.interactive import XYZDestination


def _tagged() -> Document:
    """Two pages whose headings the document names itself."""
    document = Document()
    first = document.pages.add(PageSize.A4)
    first.add_text("Chapter One", 60, 760, font_size=20, tag="H1")
    first.add_text("Body of the first chapter.", 60, 730, tag="P")
    first.add_text("A Section", 60, 700, font_size=15, tag="H2")
    first.add_text("Body of the section.", 60, 670, tag="P")
    second = document.pages.add(PageSize.A4)
    second.add_text("Chapter Two", 60, 760, font_size=20, tag="H1")
    second.add_text("Body of the second chapter.", 60, 730, tag="P")
    return document


def _untagged() -> Document:
    """One page whose headings are only bigger than its body text."""
    document = Document()
    page = document.pages.add(PageSize.A4)
    page.add_text("A Big Heading", 60, 760, font_size=22)
    page.add_text(
        "Body text that follows it and is long enough to read as body.", 60, 720
    )
    page.add_text("Another Heading", 60, 670, font_size=22)
    page.add_text(
        "More body text, also long enough to read as body text.", 60, 630
    )
    return document


def _tree(document: Document) -> list:
    """The outline tree as ``(depth, title, page, (left, top))`` tuples."""
    out: list = []

    def walk(items, depth: int) -> None:
        for item in items:
            target = item.destination
            place = (
                (target.left, target.top)
                if isinstance(target, XYZDestination)
                else None
            )
            out.append((depth, item.title, item.page_index, place))
            walk(item.children, depth + 1)

    walk(list(document.outlines), 0)
    return out


# ---------------------------------------------------------------------------
# Finding the headings
# ---------------------------------------------------------------------------
class TestFindHeadings:
    def test_a_tagged_document_says_what_they_are(self):
        with _tagged() as document:
            found = find_headings(document._engine_pdf)
        assert [(h.level, h.text, h.page_index) for h in found] == [
            (1, "Chapter One", 0),
            (2, "A Section", 0),
            (1, "Chapter Two", 1),
        ]
        assert all(h.source == "structure" for h in found)

    def test_each_one_carries_its_place(self):
        with _tagged() as document:
            found = find_headings(document._engine_pdf)
        assert [(h.x, h.y) for h in found] == [(60.0, 760.0), (60.0, 700.0), (60.0, 760.0)]

    def test_an_untagged_document_is_read(self):
        with _untagged() as document:
            found = find_headings(document._engine_pdf)
        assert [h.text for h in found] == ["A Big Heading", "Another Heading"]
        assert all(h.source == "layout" for h in found)
        assert all(h.level == 1 for h in found)

    def test_the_pages_can_be_read_even_when_tagged(self):
        """A document whose tagging is a shell has nothing in the tree to read."""
        with _tagged() as document:
            from_tree = find_headings(document._engine_pdf)
            from_pages = find_headings(document._engine_pdf, prefer_structure=False)
        assert [h.source for h in from_tree] == ["structure"] * 3
        assert all(h.source == "layout" for h in from_pages)

    def test_max_level_leaves_the_deeper_ones_out(self):
        with _tagged() as document:
            found = find_headings(document._engine_pdf, max_level=1)
        assert [h.text for h in found] == ["Chapter One", "Chapter Two"]

    def test_max_level_is_clamped_to_what_table_333_names(self):
        with _tagged() as document:
            assert find_headings(document._engine_pdf, max_level=99) == find_headings(
                document._engine_pdf, max_level=MAX_HEADING_LEVEL
            )
            assert find_headings(document._engine_pdf, max_level=0) == find_headings(
                document._engine_pdf, max_level=1
            )

    def test_actual_text_is_what_a_heading_reads_as(self):
        document = Document()
        page = document.pages.add(PageSize.A4)
        page.add_text(
            "Ch. 1", 60, 760, font_size=20, tag="H1", actual_text="Chapter One"
        )
        found = find_headings(document._engine_pdf)
        document.close()
        assert [h.text for h in found] == ["Chapter One"]

    def test_a_document_with_no_headings_finds_none(self):
        document = Document()
        page = document.pages.add(PageSize.A4)
        page.add_text("Just body text, all of it the same size.", 60, 700)
        found = find_headings(document._engine_pdf)
        document.close()
        assert found == []

    def test_an_empty_document_finds_none(self):
        document = Document()
        document.pages.add(PageSize.A4)
        assert find_headings(document._engine_pdf) == []
        document.close()

    def test_a_bare_h_takes_its_level_from_the_sections_around_it(self):
        """ISO 14289-1 7.4's unnumbered form: one /H per /Sect, nesting the level."""
        document = Document()
        page = document.pages.add(PageSize.A4)
        page.add_text("Outer", 60, 760, font_size=20, tag="H")
        page.add_text("Inner", 60, 700, font_size=16, tag="H")
        engine = document._engine_pdf
        tagged = document.tagged_content
        outer_section = tagged.add_element("Sect")
        inner_section = outer_section.add_child("Sect")
        root = engine._tagged_root_elements()
        headings = [
            element
            for element in root
            if engine._tagged_element_type(element) == "H"
        ]
        assert len(headings) == 2
        # Move the first /H under one section and the second under two.
        tagged._wrap(headings[0]).move_to(outer_section)
        tagged._wrap(headings[1]).move_to(inner_section)
        found = find_headings(engine)
        document.close()
        # By title, not by order: the reading order is the tree's own, and this
        # tree was built section-first.
        assert {h.text: h.level for h in found} == {"Outer": 1, "Inner": 2}

    def test_a_heading_with_no_place_still_has_its_page(self):
        document = Document()
        page = document.pages.add(PageSize.A4)
        page.add_text("Headed", 60, 760, font_size=20, tag="H1")
        engine = document._engine_pdf
        element = engine._tagged_root_elements()[0]
        # An element whose marked content the page does not have: no place to
        # point at, but the page it claims is still a page.
        element.mapping[PdfName("K")] = __import__(
            "aspose_pdf.engine.cos", fromlist=["PdfNumber"]
        ).PdfNumber(99)
        element.mapping[PdfName("ActualText")] = __import__(
            "aspose_pdf.engine.cos", fromlist=["PdfString"]
        ).PdfString("Headed")
        found = find_headings(engine)
        document.close()
        assert len(found) == 1
        assert found[0].page_index == 0
        assert found[0].x is None and found[0].y is None


# ---------------------------------------------------------------------------
# The byte ranges a heading's position comes from
# ---------------------------------------------------------------------------
class TestMcidSpans:
    def test_each_sequence_gets_its_range(self):
        content = b"/H1 << /MCID 0 >> BDC BT (a) Tj ET EMC /P << /MCID 1 >> BDC x EMC"
        spans = mcid_spans(content)
        assert set(spans) == {0, 1}
        assert content[spans[0][0] : spans[0][1]].strip() == b"BT (a) Tj ET"

    def test_nesting_gives_each_its_own(self):
        content = b"/A << /MCID 0 >> BDC x /B << /MCID 1 >> BDC y EMC z EMC"
        spans = mcid_spans(content)
        assert content[spans[1][0] : spans[1][1]].strip() == b"y"
        assert b"y" in content[spans[0][0] : spans[0][1]]

    def test_an_unbalanced_emc_closes_nothing(self):
        spans = mcid_spans(b"EMC /A << /MCID 0 >> BDC q EMC")
        assert set(spans) == {0}

    def test_a_sequence_left_open_runs_to_the_end(self):
        content = b"/A << /MCID 5 >> BDC tail"
        spans = mcid_spans(content)
        assert spans[5][1] == len(content)

    def test_a_sequence_with_no_id_is_not_one(self):
        assert mcid_spans(b"/Artifact BDC x EMC") == {}

    def test_a_string_that_looks_like_an_operator_is_not_one(self):
        assert mcid_spans(b"BT (EMC BDC) Tj ET") == {}


# ---------------------------------------------------------------------------
# Generating
# ---------------------------------------------------------------------------
class TestGenerate:
    def test_a_tagged_document_gets_its_tree(self):
        with _tagged() as document:
            assert document.generate_outlines() == 3
            assert _tree(document) == [
                (0, "Chapter One", 0, (60.0, 760.0)),
                (1, "A Section", 0, (60.0, 700.0)),
                (0, "Chapter Two", 1, (60.0, 760.0)),
            ]

    def test_an_untagged_document_gets_one_too(self):
        with _untagged() as document:
            assert document.generate_outlines() == 2
            assert [row[1] for row in _tree(document)] == [
                "A Big Heading",
                "Another Heading",
            ]

    def test_a_skipped_level_still_nests(self):
        document = Document()
        page = document.pages.add(PageSize.A4)
        page.add_text("Top", 60, 760, font_size=20, tag="H1")
        page.add_text("Deep", 60, 700, font_size=12, tag="H4")
        page.add_text("Also top", 60, 640, font_size=20, tag="H1")
        assert document.generate_outlines() == 3
        assert [(row[0], row[1]) for row in _tree(document)] == [
            (0, "Top"),
            (1, "Deep"),
            (0, "Also top"),
        ]
        document.close()

    def test_coming_back_up_a_level(self):
        document = Document()
        page = document.pages.add(PageSize.A4)
        for title, level, y in (
            ("One", 1, 760),
            ("One.a", 2, 730),
            ("One.a.i", 3, 700),
            ("One.b", 2, 670),
            ("Two", 1, 640),
        ):
            page.add_text(title, 60, y, font_size=22 - level * 2, tag=f"H{level}")
        assert document.generate_outlines() == 5
        assert [(row[0], row[1]) for row in _tree(document)] == [
            (0, "One"),
            (1, "One.a"),
            (2, "One.a.i"),
            (1, "One.b"),
            (0, "Two"),
        ]
        document.close()

    def test_the_bookmark_lands_on_the_heading_not_the_page(self):
        with _tagged() as document:
            document.generate_outlines()
            section = next(iter(document.outlines)).children[0]
            assert isinstance(section.destination, XYZDestination)
            assert section.destination.top == 700.0
            assert section.destination.page == 0

    def test_zoom_is_the_reader_s_unless_asked(self):
        with _tagged() as document:
            document.generate_outlines()
            assert next(iter(document.outlines)).destination.zoom is None
            document.generate_outlines(zoom=1.5)
            assert next(iter(document.outlines)).destination.zoom == 1.5

    def test_replace_is_the_default(self):
        with _tagged() as document:
            document.outlines.add(OutlineItem("Hand-made", 0))
            assert document.generate_outlines() == 3
            assert "Hand-made" not in [row[1] for row in _tree(document)]

    def test_appending_keeps_what_was_there(self):
        with _tagged() as document:
            document.outlines.add(OutlineItem("Hand-made", 0))
            document.generate_outlines(replace=False)
            titles = [row[1] for row in _tree(document)]
            assert titles[0] == "Hand-made"
            assert "Chapter One" in titles

    def test_open_to_level(self):
        with _tagged() as document:
            document.generate_outlines(open_to_level=1)
            top = list(document.outlines)
            assert all(item.open for item in top)
            assert not top[0].children[0].open

    def test_nothing_open_by_default(self):
        with _tagged() as document:
            document.generate_outlines()
            assert not any(item.open for item in document.outlines)

    def test_max_level(self):
        with _tagged() as document:
            assert document.generate_outlines(max_level=1) == 2
            assert all(not row[0] for row in _tree(document))

    def test_a_document_with_no_headings_clears_nothing_it_did_not_make(self):
        document = Document()
        page = document.pages.add(PageSize.A4)
        page.add_text("All body text, every line of it the same.", 60, 700)
        document.outlines.add(OutlineItem("Kept", 0))
        assert document.generate_outlines(replace=False) == 0
        assert [row[1] for row in _tree(document)] == ["Kept"]
        document.close()

    def test_replace_with_no_headings_leaves_an_empty_tree(self):
        document = Document()
        page = document.pages.add(PageSize.A4)
        page.add_text("All body text, every line of it the same.", 60, 700)
        document.outlines.add(OutlineItem("Dropped", 0))
        assert document.generate_outlines() == 0
        assert list(document.outlines) == []
        document.close()

    def test_a_bad_max_level_is_refused(self):
        with _tagged() as document:
            with pytest.raises(PdfValidationException, match="whole number from one"):
                document.generate_outlines(max_level=0)

    def test_a_document_with_no_pages_finds_nothing(self):
        """As ``validate_pdfa`` does: there is nothing to read until there is."""
        document = Document()
        assert document.generate_outlines() == 0
        assert list(document.outlines) == []
        document.close()


# ---------------------------------------------------------------------------
# Through a save
# ---------------------------------------------------------------------------
class TestRoundTrip:
    def test_the_tree_survives_a_save(self, tmp_path):
        target = tmp_path / "outlined.pdf"
        with _tagged() as document:
            document.generate_outlines()
            document.save(target)
        with Document(target) as reopened:
            assert _tree(reopened) == [
                (0, "Chapter One", 0, (60.0, 760.0)),
                (1, "A Section", 0, (60.0, 700.0)),
                (0, "Chapter Two", 1, (60.0, 760.0)),
            ]

    def test_the_destinations_are_written_as_xyz(self, tmp_path):
        target = tmp_path / "outlined.pdf"
        with _tagged() as document:
            document.generate_outlines()
            document.save(target)
        raw = target.read_bytes()
        assert raw.count(b"/XYZ") >= 3
        assert re.search(rb"/XYZ 60(\.0+)? 760(\.0+)? null", raw)

    def test_a_block_of_headings_is_found_and_outlined(self):
        """What a caller actually writes: a text block with tagged paragraphs."""
        document = Document()
        page = document.pages.add(PageSize.A4)
        block = TextBlock(width=440)
        block.add_paragraph("The Report", tag="H1", font_size=20)
        block.add_paragraph("Its body, at length. " * 6)
        block.add_paragraph("Findings", tag="H2", font_size=15)
        block.add_paragraph("More body, also at length. " * 6)
        page.add_text_block(block, 72, 740)
        created = document.generate_outlines()
        tree = _tree(document)
        document.close()
        assert created == 2
        assert [(row[0], row[1]) for row in tree] == [
            (0, "The Report"),
            (1, "Findings"),
        ]

    def test_a_heading_split_across_pages_is_one_bookmark(self):
        """Its far marked ids are references; the place comes from the first page."""
        document = Document()
        page = document.pages.add(PageSize.A4)
        block = TextBlock(width=440)
        block.add_paragraph(
            "A heading so long that it has to run over the foot of the page and "
            "carry on at the top of the next one, which is unusual but legal",
            tag="H1",
            font_size=20,
        )
        page.add_text_block(block, 72, 100)
        assert document.page_count > 1
        created = document.generate_outlines()
        tree = _tree(document)
        document.close()
        assert created == 1
        assert tree[0][2] == 0  # the page it starts on


def test_generated_outlines_do_not_disturb_pdfua():
    document = Document()
    page = document.pages.add(PageSize.A4)
    page.add_text("Chapter One", 60, 760, font_size=20, tag="H1")
    page.add_text("Body of it.", 60, 730, tag="P")
    document.generate_outlines()
    document.convert_to_pdfua(title="Outlined")
    buffer = io.BytesIO()
    document.save(buffer)
    document.close()
    with Document(buffer.getvalue()) as reopened:
        result = reopened.validate_pdfua()
        assert result.is_valid, result.errors
        assert len(reopened.outlines) == 1


def test_a_heading_is_a_plain_value_object():
    heading = Heading(2, "Title", 3, 10.0, 20.0, "structure")
    assert heading.level == 2 and heading.page_index == 3
    assert heading == Heading(2, "Title", 3, 10.0, 20.0, "structure")
