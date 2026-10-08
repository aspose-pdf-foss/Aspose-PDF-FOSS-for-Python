"""Flowing text blocks: wrapping, alignment, justification, breaking, tagging.

The point of the feature is that none of this needs an optional dependency: the
standard 14 fonts are measured by their own advances, so a measure can be turned
into lines without HarfBuzz and without an embedded font. The tests therefore
work in the default install, and reach for an embedded font only where the
composite path is what is being tested.
"""

from __future__ import annotations

import io
import re

import pytest

from aspose_pdf import Color, Document, PageSize, Paragraph, TextBlock
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName
from aspose_pdf.engine.text_blocks import layout
from aspose_pdf.engine.text_faces import _StandardFace
from aspose_pdf.exceptions import PdfValidationException

LOREM = (
    "Of Man's first disobedience, and the fruit of that forbidden tree whose "
    "mortal taste brought death into the World, and all our woe, with loss of "
    "Eden, till one greater Man restore us, and regain the blissful seat. "
)


def _page(size: PageSize = PageSize.A4):
    document = Document()
    page = document.pages.add(size)
    return document, page


def _font_bytes() -> bytes:
    from aspose_pdf.engine.std_font_data import load_substitute_sfnt

    data = load_substitute_sfnt("sans-regular")
    if not data:
        pytest.skip("no bundled substitute font to embed")
    return data


def _text_positions(content: bytes) -> list[tuple[float, float]]:
    """Every ``Tm`` translation in *content*, in order."""
    pattern = re.compile(rb"1 0 0 1 (-?[\d.]+) (-?[\d.]+) Tm")
    return [
        (float(m.group(1)), float(m.group(2)))
        for m in pattern.finditer(content)
    ]


def _shown_strings(content: bytes) -> list[bytes]:
    """The literal show strings of ``Tj`` operators, in order."""
    return re.findall(rb"\((.*?)\) Tj", content, re.S)


# ---------------------------------------------------------------------------
# The value objects
# ---------------------------------------------------------------------------
class TestTextBlockObject:
    def test_a_string_is_one_paragraph(self):
        block = TextBlock("just the one")
        assert len(block.paragraphs) == 1
        assert block.paragraphs[0].text == "just the one"

    def test_a_blank_line_starts_a_new_paragraph(self):
        block = TextBlock("first\n\nsecond\n\n\nthird")
        assert [p.text for p in block.paragraphs] == ["first", "second", "third"]

    def test_a_single_newline_stays_inside_its_paragraph(self):
        block = TextBlock("first\nstill first")
        assert [p.text for p in block.paragraphs] == ["first\nstill first"]

    def test_crlf_is_normalised(self):
        block = TextBlock("first\r\n\r\nsecond")
        assert [p.text for p in block.paragraphs] == ["first", "second"]

    def test_whitespace_only_text_is_no_paragraph(self):
        assert TextBlock("   \n  \n ").paragraphs == []

    def test_a_sequence_of_strings(self):
        block = TextBlock(["one", "two"])
        assert [p.text for p in block.paragraphs] == ["one", "two"]

    def test_a_sequence_of_paragraphs_keeps_their_options(self):
        block = TextBlock([Paragraph("one", tag="H1"), "two"])
        assert block.paragraphs[0].tag == "H1"
        assert block.paragraphs[1].tag == "P"

    def test_a_single_paragraph_object(self):
        block = TextBlock(Paragraph("solo", alignment="right"))
        assert block.paragraphs[0].alignment == "right"

    def test_paragraphs_given_directly(self):
        block = TextBlock(paragraphs=[Paragraph("one"), "two"])
        assert [p.text for p in block.paragraphs] == ["one", "two"]

    def test_bytes_refused(self):
        with pytest.raises(PdfValidationException, match="not bytes"):
            TextBlock(b"no")

    def test_an_unusable_type_refused(self):
        with pytest.raises(PdfValidationException, match="a string, a Paragraph"):
            TextBlock(42)

    def test_add_paragraph_returns_it(self):
        block = TextBlock()
        paragraph = block.add_paragraph("hello", alignment="center")
        assert block.paragraphs == [paragraph]
        assert paragraph.alignment == "center"

    def test_add_paragraph_with_an_object_and_options_refused(self):
        with pytest.raises(PdfValidationException, match="carries its own options"):
            TextBlock().add_paragraph(Paragraph("x"), tag="H1")

    def test_add_paragraphs_splits_on_blank_lines(self):
        block = TextBlock()
        added = block.add_paragraphs("one\n\ntwo", tag="H2")
        assert len(added) == 2
        assert all(p.tag == "H2" for p in added)

    def test_is_empty(self):
        assert TextBlock().is_empty
        assert TextBlock(paragraphs=[Paragraph("  ")]).is_empty
        assert not TextBlock("something").is_empty

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("width", 0),
            ("width", -1),
            ("font_size", 0),
            ("line_height", 0),
            ("paragraph_gap", -1),
            ("width", "wide"),
            ("font_size", float("inf")),
            ("font_size", True),
        ],
    )
    def test_bad_measurements_refused(self, field, value):
        with pytest.raises(PdfValidationException):
            TextBlock("x", **{field: value})

    def test_a_negative_first_line_indent_is_a_hanging_indent(self):
        assert TextBlock("x", first_line_indent=-18).first_line_indent == -18

    def test_a_negative_left_indent_refused(self):
        with pytest.raises(PdfValidationException, match="must not be negative"):
            Paragraph("x", left_indent=-5)

    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("left", "left"),
            ("CENTER", "center"),
            ("centre", "center"),
            ("Right", "right"),
            ("justify", "justify"),
        ],
    )
    def test_alignments(self, given, expected):
        assert TextBlock("x", alignment=given).alignment == expected

    def test_an_unknown_alignment_refused(self):
        with pytest.raises(PdfValidationException, match="alignment is one of"):
            TextBlock("x", alignment="middle")


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
def _laid_out(block: TextBlock, measure: float = 300.0):
    document, _page_object = _page()
    faces = {
        name: _StandardFace(document._engine_pdf, name)
        for name in {block.font_name}
        | {p.font_name or block.font_name for p in block.paragraphs}
    }
    try:
        return layout(block, faces, measure), faces
    finally:
        document.close()


class TestLayout:
    def test_text_is_wrapped_to_the_measure(self):
        boxes, faces = _laid_out(TextBlock(LOREM, font_size=11))
        face = faces["Helvetica"]
        lines = boxes[0].lines
        assert len(lines) > 1
        for line in lines:
            assert face.width(line.text, 11) <= line.measure + 1e-6

    def test_a_hard_break_starts_a_line(self):
        boxes, _ = _laid_out(TextBlock("one\ntwo"))
        assert [line.text for line in boxes[0].lines] == ["one", "two"]

    def test_a_blank_line_inside_a_paragraph_takes_its_room(self):
        boxes, _ = _laid_out(TextBlock(paragraphs=[Paragraph("one\n\ntwo")]))
        texts = [line.text for line in boxes[0].lines]
        assert texts == ["one", "", "two"]

    def test_the_first_line_is_the_one_the_indent_moves(self):
        boxes, _ = _laid_out(TextBlock(LOREM, first_line_indent=18))
        lines = boxes[0].lines
        assert lines[0].x == 18
        assert all(line.x == 0 for line in lines[1:])

    def test_a_hanging_indent_widens_the_first_line(self):
        boxes, _ = _laid_out(TextBlock(LOREM, first_line_indent=-18))
        lines = boxes[0].lines
        assert lines[0].x == -18
        assert lines[0].measure == lines[1].measure

    def test_indents_narrow_the_measure(self):
        boxes, _ = _laid_out(
            TextBlock(paragraphs=[Paragraph(LOREM, left_indent=20, right_indent=30)])
        )
        assert boxes[0].lines[0].measure == pytest.approx(250)
        assert boxes[0].lines[0].x == 20

    def test_indents_with_no_room_left_refused(self):
        with pytest.raises(PdfValidationException, match="leave no room"):
            _laid_out(TextBlock(paragraphs=[Paragraph("x", left_indent=200)]), 150)

    def test_a_measure_of_nothing_refused(self):
        with pytest.raises(PdfValidationException, match="needs a width"):
            _laid_out(TextBlock("x"), 0)

    def test_a_word_too_long_is_broken_rather_than_overflowing(self):
        boxes, faces = _laid_out(TextBlock("x" * 400, font_size=11), 100)
        for line in boxes[0].lines:
            assert faces["Helvetica"].width(line.text, 11) <= 100 + 1e-6
        assert len(boxes[0].lines) > 1

    def test_line_height_is_honoured(self):
        boxes, _ = _laid_out(TextBlock(LOREM, line_height=20))
        assert all(line.height == 20 for line in boxes[0].lines)

    def test_a_paragraph_may_set_its_own_line_height(self):
        block = TextBlock(line_height=20)
        block.add_paragraph(LOREM)
        block.add_paragraph(LOREM, line_height=30)
        boxes, _ = _laid_out(block)
        assert boxes[0].lines[0].height == 20
        assert boxes[1].lines[0].height == 30

    def test_the_gap_falls_between_paragraphs_only(self):
        block = TextBlock(paragraph_gap=8)
        block.add_paragraph("one")
        block.add_paragraph("two")
        boxes, _ = _laid_out(block)
        assert boxes[0].space_before == 0
        assert boxes[1].space_before == 8

    def test_space_before_adds_to_the_gap(self):
        block = TextBlock(paragraph_gap=8)
        block.add_paragraph("one")
        block.add_paragraph("two", space_before=4)
        boxes, _ = _laid_out(block)
        assert boxes[1].space_before == 12

    def test_a_paragraph_falls_back_to_the_block_s_style(self):
        block = TextBlock(font_size=9, alignment="right")
        block.add_paragraph("one")
        block.add_paragraph("two", font_size=14)
        boxes, _ = _laid_out(block)
        assert boxes[0].lines[0].style["font_size"] == 9
        assert boxes[1].lines[0].style["font_size"] == 14
        assert boxes[1].lines[0].style["alignment"] == "right"


# ---------------------------------------------------------------------------
# Drawing and alignment
# ---------------------------------------------------------------------------
class TestDrawing:
    def test_a_block_puts_its_text_on_the_page(self):
        document, page = _page()
        result = page.add_text_block(TextBlock(LOREM, width=400), 72, 700)
        assert result["pages"] == [0]
        assert result["lines"] >= 2
        assert result["bottom"] < 700
        assert "disobedience" in page.extract_text()
        document.close()

    def test_without_a_width_the_room_to_the_right_edge_is_used(self):
        document, page = _page()
        page.add_text_block(TextBlock(LOREM), 72, 700)
        face = _StandardFace(document._engine_pdf, "Helvetica")
        widest = max(
            face.width(line, 11)
            for line in page.extract_text().split("\n")
            if line
        )
        # A4 is 595.28 wide, so the measure is 595.28 - 72 = 523.28.
        assert 400 < widest <= 523.28
        document.close()

    @pytest.mark.parametrize(
        ("alignment", "expected"),
        [("left", 72.0), ("center", None), ("right", None)],
    )
    def test_alignment_places_the_line(self, alignment, expected):
        document, page = _page()
        page.add_text_block(
            TextBlock("short line", width=400, alignment=alignment), 72, 700
        )
        x = _text_positions(page.content)[0][0]
        if expected is None:
            assert x > 72
        else:
            assert x == pytest.approx(expected)
        document.close()

    def test_centre_and_right_differ(self):
        positions = {}
        for alignment in ("left", "center", "right"):
            document, page = _page()
            page.add_text_block(
                TextBlock("short line", width=400, alignment=alignment), 72, 700
            )
            positions[alignment] = _text_positions(page.content)[0][0]
            document.close()
        assert positions["left"] < positions["center"] < positions["right"]

    def test_a_block_with_no_paragraphs_refused(self):
        document, page = _page()
        with pytest.raises(PdfValidationException, match="nothing to draw"):
            page.add_text_block(TextBlock(), 72, 700)
        document.close()

    def test_colour_follows_the_package_s_rule(self):
        document, page = _page()
        page.add_text_block(TextBlock("grey", width=200, text_color=0.5), 72, 700)
        page.add_text_block(TextBlock("hex", width=200, text_color="#336699"), 72, 650)
        page.add_text_block(
            TextBlock("cmyk", width=200, text_color=Color.cmyk(0, 0, 0, 1)), 72, 600
        )
        content = page.content
        assert b" g\n" in content or b" g " in content
        assert b"rg" in content
        assert b" k " in content
        document.close()

    def test_a_character_the_font_cannot_show_is_refused(self):
        document, page = _page()
        with pytest.raises(PdfValidationException, match="no code for"):
            page.add_text_block(TextBlock("Zażółć gęślą", width=300), 72, 700)
        document.close()

    def test_an_unknown_standard_font_refused(self):
        document, page = _page()
        with pytest.raises(PdfValidationException, match="standard 14"):
            page.add_text_block(TextBlock("x", width=300, font_name="Comic Sans"), 72, 700)
        document.close()

    def test_a_paragraph_may_use_another_standard_font(self):
        document, page = _page()
        block = TextBlock(width=300, font_name="Helvetica")
        block.add_paragraph("sans")
        block.add_paragraph("serif", font_name="Times-Roman")
        page.add_text_block(block, 72, 700)
        fonts = page._document._engine_pdf._ensure_resource_subdict(0, "Font")
        assert len(fonts.mapping) == 2
        document.close()

    def test_an_embedded_font_sets_the_whole_block(self):
        document, page = _page()
        page.add_text_block(
            TextBlock("Zażółć gęślą jaźń", width=300, font=_font_bytes()), 72, 700
        )
        assert "Zażółć" in page.extract_text()
        document.close()


# ---------------------------------------------------------------------------
# Justification
# ---------------------------------------------------------------------------
class TestJustification:
    def test_a_standard_font_is_spread_with_word_spacing(self):
        """``Tw`` leaves no displacement for an extractor to read as a space."""
        document, page = _page()
        page.add_text_block(
            TextBlock(LOREM, width=440, alignment="justify"), 72, 700
        )
        content = page.content
        assert b" Tw" in content
        assert b"] TJ" not in content
        assert "  " not in page.extract_text()
        document.close()

    def test_an_embedded_font_is_spread_with_positioning(self):
        """``Tw`` cannot reach a two-byte code, so ``TJ`` does the spreading."""
        document, page = _page()
        page.add_text_block(
            TextBlock(LOREM, width=440, alignment="justify", font=_font_bytes()),
            72,
            700,
        )
        content = page.content
        assert b"] TJ" in content
        assert b" Tw" not in content
        assert "  " not in page.extract_text()
        document.close()

    def test_a_justified_line_fills_the_measure(self):
        document, page = _page()
        page.add_text_block(
            TextBlock(LOREM, width=400, alignment="justify"), 72, 700
        )
        face = _StandardFace(document._engine_pdf, "Helvetica")
        content = page.content
        spacings = [float(m) for m in re.findall(rb"([\d.]+) Tw", content)]
        strings = _shown_strings(content)
        # Every line but the last carries a spacing, and they come in order, so
        # each spacing pairs with the line it was computed for.
        assert spacings and len(spacings) == len(strings) - 1
        for extra, raw in zip(spacings, strings):
            text = raw.decode("latin-1")
            natural = face.width(text, 11)
            assert natural + extra * text.count(" ") == pytest.approx(400, abs=0.05)
        document.close()

    def test_the_last_line_of_a_paragraph_is_left_alone(self):
        document, page = _page()
        page.add_text_block(
            TextBlock(LOREM, width=440, alignment="justify"), 72, 700
        )
        content = page.content
        # Every line but the last carries a word spacing; the last is a plain Tj.
        lines = content.count(b" Tj")
        spread = content.count(b" Tw")
        assert spread == lines - 1
        document.close()

    def test_the_line_before_a_hard_break_is_left_alone(self):
        document, page = _page()
        page.add_text_block(
            TextBlock(f"{LOREM}\nand a short tail", width=440, alignment="justify"),
            72,
            700,
        )
        content = page.content
        assert content.count(b" Tw") == content.count(b" Tj") - 2
        document.close()

    def test_a_single_word_line_is_not_stretched(self):
        document, page = _page()
        page.add_text_block(
            TextBlock("solitary", width=440, alignment="justify"), 72, 700
        )
        assert b" Tw" not in page.content
        document.close()

    def test_justified_text_reads_back_as_its_words(self):
        document, page = _page()
        page.add_text_block(
            TextBlock(LOREM, width=440, alignment="justify"), 72, 700
        )
        extracted = page.extract_text().replace("\n", " ")
        assert "Of Man's first disobedience" in extracted
        assert "blissful seat" in extracted
        document.close()


# ---------------------------------------------------------------------------
# Breaking onto the next page
# ---------------------------------------------------------------------------
class TestPageBreaking:
    def test_a_long_block_adds_a_page(self):
        document, page = _page()
        result = page.add_text_block(TextBlock(LOREM * 30, width=440), 72, 760)
        assert document.page_count > 1
        assert result["pages"] == list(range(document.page_count))
        document.close()

    def test_an_existing_page_is_used_rather_than_a_new_one(self):
        document, page = _page()
        document.pages.add(PageSize.A4)
        result = page.add_text_block(TextBlock(LOREM * 8, width=440), 72, 300)
        assert document.page_count == 2  # the one that was already there
        assert result["pages"] == [0, 1]
        assert document.pages[1].extract_text().strip()
        document.close()

    def test_a_new_page_matches_the_one_it_continues_from(self):
        document, page = _page(PageSize.A5)
        page.add_text_block(TextBlock(LOREM * 16, width=300), 40, 560)
        assert document.pages[1].size == document.pages[0].size
        document.close()

    def test_text_continues_at_the_same_height_on_the_next_page(self):
        document, page = _page()
        page.add_text_block(TextBlock(LOREM * 30, width=440), 72, 700)
        first = _text_positions(document.pages[0].content)[0][1]
        second = _text_positions(document.pages[1].content)[0][1]
        assert second == pytest.approx(first, abs=0.01)
        document.close()

    def test_keep_together_moves_a_whole_paragraph(self):
        document, page = _page()
        block = TextBlock(width=440)
        block.add_paragraph("A heading that stays with its text", tag="H2")
        block.add_paragraph(LOREM * 5, keep_together=True)
        page.add_text_block(block, 72, 200)
        # The paragraph did not fit below y=200, so none of it is on page one.
        assert "disobedience" not in document.pages[0].extract_text()
        assert "disobedience" in document.pages[1].extract_text()
        document.close()

    def test_without_keep_together_a_paragraph_splits(self):
        document, page = _page()
        block = TextBlock(width=440)
        block.add_paragraph(LOREM * 5)
        page.add_text_block(block, 72, 200)
        assert "disobedience" in document.pages[0].extract_text()
        assert document.page_count == 2
        document.close()

    def test_a_first_line_that_cannot_fit_is_refused(self):
        document, page = _page()
        with pytest.raises(PdfValidationException, match="does not fit"):
            page.add_text_block(TextBlock(LOREM, width=440), 72, 40)
        document.close()

    def test_the_bottom_margin_is_honoured(self):
        document, page = _page()
        page.add_text_block(
            TextBlock(LOREM * 6, width=440), 72, 700, bottom_margin=300
        )
        lowest = min(y for _x, y in _text_positions(document.pages[0].content))
        assert lowest >= 300
        document.close()

    def test_the_line_count_covers_every_page(self):
        document, page = _page()
        result = page.add_text_block(TextBlock(LOREM * 30, width=440), 72, 760)
        placed = sum(
            len(_text_positions(document.pages[i].content)) for i in result["pages"]
        )
        assert placed == result["lines"]
        document.close()


# ---------------------------------------------------------------------------
# Tagging
# ---------------------------------------------------------------------------
def _struct_kids(document: Document) -> list[PdfDictionary]:
    engine = document._engine_pdf
    root = engine._resolve(engine._cos_doc.trailer.get(PdfName("Root")))
    struct_root = engine._resolve(root.get(PdfName("StructTreeRoot")))
    kids = engine._resolve(struct_root.get(PdfName("K")))
    items = kids.items if isinstance(kids, PdfArray) else [kids]
    return [engine._resolve(item) for item in items]


class TestTagging:
    def test_each_paragraph_is_one_element(self):
        document, page = _page()
        block = TextBlock(width=440)
        block.add_paragraph("A heading", tag="H1")
        block.add_paragraph(LOREM)
        page.add_text_block(block, 72, 700)
        kinds = [
            document._engine_pdf._get_name(elem.get(PdfName("S")))
            for elem in _struct_kids(document)
        ]
        assert kinds == ["H1", "P"]
        document.close()

    def test_a_paragraph_s_lines_all_belong_to_it(self):
        document, page = _page()
        page.add_text_block(TextBlock(LOREM, width=300), 72, 700)
        (element,) = _struct_kids(document)
        kids = document._engine_pdf._resolve(element.get(PdfName("K")))
        assert isinstance(kids, PdfArray)
        assert len(kids.items) == page.content.count(b"BDC")
        document.close()

    def test_a_paragraph_may_be_left_untagged(self):
        document, page = _page()
        block = TextBlock(width=440)
        block.add_paragraph("tagged")
        block.add_paragraph("not tagged", tag=None)
        page.add_text_block(block, 72, 700)
        assert len(_struct_kids(document)) == 1
        assert page.content.count(b"BDC") == 1
        document.close()

    def test_tagging_can_be_turned_off_for_the_call(self):
        document, page = _page()
        page.add_text_block(TextBlock(LOREM, width=440), 72, 700, tag=False)
        assert b"BDC" not in page.content
        document.close()

    def test_a_paragraph_across_pages_stays_one_element(self):
        """Its ids are on different pages, so the far ones are /MCR kids."""
        document, page = _page()
        page.add_text_block(TextBlock(LOREM * 5, width=440), 72, 200)
        assert document.page_count > 1
        engine = document._engine_pdf
        (element,) = _struct_kids(document)
        assert engine._get_name(element.get(PdfName("S"))) == "P"
        kids = engine._resolve(element.get(PdfName("K")))
        references = [
            engine._resolve(item)
            for item in kids.items
            if isinstance(engine._resolve(item), PdfDictionary)
        ]
        assert references, "a continued paragraph needs marked-content references"
        for reference in references:
            assert engine._get_name(reference.get(PdfName("Type"))) == "MCR"
            assert PdfName("Pg") in reference.mapping
            assert PdfName("MCID") in reference.mapping
        document.close()

    def test_every_marked_id_resolves_through_the_parent_tree(self):
        document, page = _page()
        page.add_text_block(TextBlock(LOREM * 5, width=440), 72, 200)
        engine = document._engine_pdf
        for index in range(document.page_count):
            content = document.pages[index].content
            ids = [int(m) for m in re.findall(rb"/MCID (\d+)", content)]
            page_dict = engine._get_page_dict(index)
            struct_root = engine._resolve(
                engine._resolve(
                    engine._cos_doc.trailer.get(PdfName("Root"))
                ).get(PdfName("StructTreeRoot"))
            )
            parents = engine._parent_tree_array_for_page(struct_root, page_dict)
            for mcid in ids:
                assert mcid < len(parents.items)
                assert engine._resolve(parents.items[mcid]) is not None
        document.close()

    def test_a_tagged_block_passes_pdfua(self):
        document, page = _page()
        page.add_text_block(TextBlock(LOREM * 2, width=440), 72, 700)
        document.convert_to_pdfua(title="Flow")
        buffer = io.BytesIO()
        document.save(buffer)
        document.close()
        with Document(buffer.getvalue()) as reopened:
            result = reopened.validate_pdfua()
            assert result.is_valid, result.errors

    def test_a_tagged_block_passes_pdfa_2a(self):
        document, page = _page()
        page.add_text_block(TextBlock(LOREM * 2, width=440), 72, 700)
        document.convert_to_pdfua(title="Flow")
        assert document.convert_to_pdfa("2a") == []
        buffer = io.BytesIO()
        document.save(buffer)
        document.close()
        with Document(buffer.getvalue()) as reopened:
            assert reopened.validate_pdfa("2a").is_valid


# ---------------------------------------------------------------------------
# The content builders
# ---------------------------------------------------------------------------
class TestBuilders:
    def test_word_spacing_is_written_before_the_show(self):
        from aspose_pdf.engine.content_authoring import build_word_spaced_text_stream

        content = build_word_spaced_text_stream(
            "a b", 2.5, 10, 20, "F1", 12, (0, 0, 0)
        )
        assert b"2.5 Tw" in content
        assert content.index(b"Tw") < content.index(b"Tj")

    def test_an_adjustment_is_the_slack_in_thousandths_of_an_em(self):
        from aspose_pdf.engine.content_authoring import build_adjusted_text_stream

        content = build_adjusted_text_stream(
            [b"a", b"b"], [1.2], 0, 0, "F1", 12, (0, 0, 0), hex_strings=False
        )
        # 1.2 pt at 12 pt is a tenth of an em, written negative to open the gap.
        assert b"-100" in content
        assert b"] TJ" in content

    def test_hex_strings_for_a_composite_font(self):
        from aspose_pdf.engine.content_authoring import build_adjusted_text_stream

        content = build_adjusted_text_stream(
            [b"\x00A", b"\x00B"], [1.0], 0, 0, "F1", 10, (0, 0, 0), hex_strings=True
        )
        assert b"<0041>" in content and b"<0042>" in content

    def test_one_adjustment_per_gap(self):
        from aspose_pdf.engine.content_authoring import build_adjusted_text_stream

        with pytest.raises(PdfValidationException, match="one adjustment"):
            build_adjusted_text_stream(
                [b"a", b"b"], [1.0, 2.0], 0, 0, "F1", 12, (0, 0, 0), hex_strings=False
            )

    def test_a_single_segment_needs_no_adjustment(self):
        from aspose_pdf.engine.content_authoring import build_adjusted_text_stream

        content = build_adjusted_text_stream(
            [b"alone"], [], 0, 0, "F1", 12, (0, 0, 0), hex_strings=False
        )
        assert b"[(alone)] TJ" in content


# ---------------------------------------------------------------------------
# The extractor, which has to read a justified line back
# ---------------------------------------------------------------------------
class TestExtractingAJustifiedLine:
    def _extract(self, content: bytes) -> str:
        document = Document()
        document.pages.add(PageSize.A4)
        document._engine_pdf._append_content_to_page(0, content)
        try:
            return document.pages[0].extract_text()
        finally:
            document.close()

    def test_a_gap_after_a_space_adds_no_second_space(self):
        content = (
            b"BT /F1 12 Tf 1 0 0 1 10 700 Tm [(one ) -200 (two ) -200 (three)] TJ ET\n"
        )
        assert self._extract(content) == "one two three"

    def test_a_gap_with_no_space_before_it_still_separates(self):
        content = b"BT /F1 12 Tf 1 0 0 1 10 700 Tm [(one) -400 (two)] TJ ET\n"
        assert self._extract(content) == "one two"

    def test_a_small_gap_is_not_a_space(self):
        content = b"BT /F1 12 Tf 1 0 0 1 10 700 Tm [(ki) -20 (ck)] TJ ET\n"
        assert self._extract(content) == "kick"


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------
def test_a_saved_block_reads_back_as_its_text(tmp_path):
    target = tmp_path / "flow.pdf"
    document, page = _page()
    block = TextBlock(width=440, alignment="justify", paragraph_gap=6)
    block.add_paragraph("Paradise Lost", tag="H1", font_size=18, alignment="center")
    block.add_paragraph(LOREM * 2, first_line_indent=18)
    page.add_text_block(block, 72, 740)
    document.save(target)
    document.close()

    with Document(target) as reopened:
        text = reopened.pages[0].extract_text()
    assert "Paradise Lost" in text
    assert "Of Man's first disobedience" in text.replace("\n", " ")
    assert "  " not in text


def test_a_block_and_a_table_can_share_a_page():
    from aspose_pdf import Table

    document, page = _page()
    end = page.add_text_block(TextBlock("An introduction. " * 10, width=440), 72, 740)
    table = Table(column_widths=[220, 220])
    table.add_header_row(["Item", "Price"])
    table.add_row(["Widget", "9.99"])
    below = page.add_table(table, 72, end["bottom"] - 12)
    assert below["pages"] == [0]
    assert below["bottom"] < end["bottom"]
    text = page.extract_text()
    assert "introduction" in text and "Widget" in text
    document.close()
