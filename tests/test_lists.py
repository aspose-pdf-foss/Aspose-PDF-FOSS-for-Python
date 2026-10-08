"""Authoring bulleted and numbered lists.

The nested structure a list needs -- ``/L`` of ``/LI`` of ``/Lbl`` and
``/LBody`` -- had been in the engine since ``auto_tag`` learned to recognise
one, but only for re-tagging content that was already on the page. These tests
cover authoring: the markers and the numbering that restarts at each sub-list,
the gutter the markers sit in, the wrap, the break onto the next page, and the
structure the result is tagged with -- which stays one list across a break.
"""

from __future__ import annotations

import io
import re

import pytest

from aspose_pdf import Document, ListItem, PageSize, TextList
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName, PdfNumber
from aspose_pdf.engine.lists import flatten, label_column, layout
from aspose_pdf.engine.text_faces import _StandardFace
from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.lists import alphabetic, number_label, roman


def _page(size: PageSize = PageSize.A4):
    document = Document()
    return document, document.pages.add(size)


def _positions(content: bytes) -> list[tuple[float, float]]:
    return [
        (float(m.group(1)), float(m.group(2)))
        for m in re.finditer(rb"1 0 0 1 (-?[\d.]+) (-?[\d.]+) Tm", content)
    ]


def _marks(content: bytes) -> list[str]:
    return [m.group(1).decode("ascii") for m in re.finditer(rb"/(\w+) << /MCID", content)]


def _laid_out(text_list: TextList, measure: float = 400.0):
    document, _page_object = _page()
    faces = {
        name: _StandardFace(document._engine_pdf, name)
        for name in {text_list.font_name}
        | {item.font_name or text_list.font_name for _d, item, _l in flatten(text_list)}
    }
    try:
        return layout(text_list, faces, measure), faces
    finally:
        document.close()


# ---------------------------------------------------------------------------
# Numbering
# ---------------------------------------------------------------------------
class TestNumbering:
    @pytest.mark.parametrize(
        ("number", "expected"),
        [(1, "i"), (4, "iv"), (9, "ix"), (14, "xiv"), (40, "xl"), (1990, "mcmxc")],
    )
    def test_roman(self, number, expected):
        assert roman(number) == expected

    @pytest.mark.parametrize(
        ("number", "expected"),
        [(1, "a"), (26, "z"), (27, "aa"), (52, "az"), (53, "ba")],
    )
    def test_alphabetic(self, number, expected):
        assert alphabetic(number) == expected

    @pytest.mark.parametrize("number", [0, -3])
    def test_a_number_with_no_numeral_falls_back_to_decimal(self, number):
        assert roman(number) == str(number)
        assert alphabetic(number) == str(number)

    @pytest.mark.parametrize(
        ("style", "expected"),
        [
            ("decimal", "3"),
            ("lower-alpha", "c"),
            ("upper-alpha", "C"),
            ("lower-roman", "iii"),
            ("upper-roman", "III"),
            ("none", ""),
        ],
    )
    def test_every_style(self, style, expected):
        assert number_label(style, 3) == expected

    def test_a_bulleted_list_cycles_its_markers(self):
        items = TextList(["x"])
        assert [items.marker_for(d) for d in range(4)] == [
            "\u2022",
            "\u2013",
            "\u00b7",
            "\u2022",
        ]

    def test_an_ordered_list_cycles_its_styles(self):
        items = TextList(["x"], ordered=True)
        assert [items.numbering_for(d) for d in range(4)] == [
            "decimal",
            "lower-alpha",
            "lower-roman",
            "decimal",
        ]

    def test_the_label_format_is_applied(self):
        items = TextList(["x"], ordered=True, number_format="({number})")
        assert items.label_for(0, 2) == "(2)"

    def test_the_start_is_honoured(self):
        items = TextList(["x"], ordered=True, start=5)
        assert [items.label_for(0, p) for p in (1, 2)] == ["5.", "6."]

    def test_list_numbering_says_what_the_markers_mean(self):
        assert TextList(["x"], ordered=True).numbering_attribute(0) == "Decimal"
        assert TextList(["x"], ordered=True).numbering_attribute(1) == "LowerAlpha"
        assert TextList(["x"]).numbering_attribute(0) == "Disc"
        assert TextList(["x"], markers="\u25aa").numbering_attribute(0) == "Square"

    def test_a_marker_with_no_standard_value_says_nothing(self):
        """An en dash is a good bullet and is none of Disc/Circle/Square."""
        assert TextList(["x"], markers="\u2013").numbering_attribute(0) is None

    def test_an_unnumbered_ordered_list_says_so(self):
        """``/ListNumbering /None`` is a value of its own: no autonumbering."""
        items = TextList(["x"], ordered=True, numbering="none")
        assert items.numbering_attribute(0) == "None"


# ---------------------------------------------------------------------------
# The value objects
# ---------------------------------------------------------------------------
class TestTextListObject:
    def test_strings_become_items(self):
        items = TextList(["one", "two"])
        assert [item.text for item in items.items] == ["one", "two"]

    def test_nested_strings_become_items(self):
        items = TextList([ListItem("one", items=["a", "b"])])
        assert [item.text for item in items.items[0].items] == ["a", "b"]

    def test_a_single_item_object(self):
        items = TextList(ListItem("solo"))
        assert items.items[0].text == "solo"

    def test_a_bare_string_is_refused(self):
        with pytest.raises(PdfValidationException, match="a single string is one item"):
            TextList("one")

    def test_add_item_returns_it(self):
        items = TextList()
        item = items.add_item("hello", font_size=14)
        assert items.items == [item]
        assert item.font_size == 14

    def test_add_item_on_an_item_nests(self):
        parent = ListItem("parent")
        child = parent.add_item("child")
        assert parent.items == [child]

    def test_an_object_with_options_is_refused(self):
        with pytest.raises(PdfValidationException, match="carries its own options"):
            TextList().add_item(ListItem("x"), font_size=9)

    def test_is_empty(self):
        assert TextList().is_empty
        assert TextList(["  "]).is_empty
        assert not TextList([ListItem("", items=["deep"])]).is_empty
        assert not TextList(["x"]).is_empty

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("indent", -1),
            ("label_gap", -1),
            ("label_width", -1),
            ("width", 0),
            ("font_size", 0),
            ("line_height", 0),
            ("item_gap", -1),
            ("start", 1.5),
            ("number_format", "no placeholder"),
            ("numbering", "sideways"),
            ("label_alignment", "middle"),
            ("markers", ()),
        ],
    )
    def test_bad_settings_refused(self, field, value):
        with pytest.raises(PdfValidationException):
            TextList(["x"], **{field: value})

    def test_a_marker_may_be_one_string_or_several(self):
        assert TextList(["x"], markers="-").markers == ["-"]
        assert TextList(["x"], markers=["-", "*"]).markers == ["-", "*"]


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
class TestLayout:
    def test_reading_order_is_depth_first(self):
        items = TextList(
            [ListItem("one", items=["a", ListItem("b", items=["deep"])]), "two"]
        )
        assert [(d, i.text) for d, i, _l in flatten(items)] == [
            (0, "one"),
            (1, "a"),
            (1, "b"),
            (2, "deep"),
            (0, "two"),
        ]

    def test_numbering_restarts_at_each_sub_list(self):
        items = TextList(
            [ListItem("one", items=["a", "b"]), ListItem("two", items=["a"])],
            ordered=True,
        )
        assert [label for _d, _i, label in flatten(items)] == [
            "1.",
            "a.",
            "b.",
            "2.",
            "a.",
        ]

    def test_an_explicit_label_wins(self):
        items = TextList([ListItem("odd", label="\u2014"), "even"], ordered=True)
        assert [label for _d, _i, label in flatten(items)] == ["\u2014", "2."]

    def test_an_empty_label_is_honoured(self):
        items = TextList([ListItem("no marker", label="")], ordered=True)
        assert [label for _d, _i, label in flatten(items)] == [""]

    def test_the_gutter_fits_the_widest_marker(self):
        items = TextList([f"item {i}" for i in range(1, 12)], ordered=True)
        boxes, faces = _laid_out(items)
        face = faces["Helvetica"]
        widest = max(face.width(label, 11) for _d, _i, label in flatten(items))
        assert label_column(items, faces, flatten(items)) == pytest.approx(
            widest + items.label_gap
        )
        assert boxes[0].lines[0].x == pytest.approx(widest + items.label_gap)

    def test_an_explicit_gutter_is_used(self):
        items = TextList(["x"], label_width=30, label_gap=4)
        boxes, _faces = _laid_out(items)
        assert boxes[0].lines[0].x == pytest.approx(34)

    def test_each_depth_steps_in_by_the_indent(self):
        items = TextList([ListItem("one", items=[ListItem("a", items=["deep"])])],
                         indent=20, label_width=10, label_gap=5)
        boxes, _faces = _laid_out(items)
        assert [box.lines[0].x for box in boxes] == pytest.approx([15, 35, 55])

    def test_markers_are_right_aligned_against_the_body(self):
        """So that "9." and "10." line up on their dots."""
        items = TextList([f"item {i}" for i in range(1, 12)], ordered=True)
        boxes, faces = _laid_out(items)
        face = faces["Helvetica"]
        for box in boxes:
            right = box.label_x + face.width(box.label, 11)
            assert right == pytest.approx(
                box.lines[0].x - items.label_gap, abs=1e-6
            )

    def test_markers_can_be_left_aligned(self):
        items = TextList(["one", "two"], ordered=True, label_alignment="left",
                         indent=20)
        boxes, _faces = _laid_out(items)
        assert all(box.label_x == 0 for box in boxes)

    def test_the_body_wraps_to_what_is_left(self):
        items = TextList(["word " * 60], width=300)
        boxes, faces = _laid_out(items, 300)
        face = faces["Helvetica"]
        lines = boxes[0].lines
        assert len(lines) > 1
        for line in lines:
            assert face.width(line.text, 11) <= line.measure + 1e-6
            assert line.measure == pytest.approx(300 - lines[0].x)

    def test_a_hard_break_starts_a_line(self):
        boxes, _faces = _laid_out(TextList(["one\ntwo"]))
        assert [line.text for line in boxes[0].lines] == ["one", "two"]

    def test_indents_with_no_room_left_refused(self):
        """A sub-item indented past the measure has nowhere to put its text."""
        deep = TextList([ListItem("x", items=["y"])], indent=200, label_width=100)
        with pytest.raises(PdfValidationException, match="leave no room"):
            _laid_out(deep, 150)

    def test_a_measure_of_nothing_refused(self):
        with pytest.raises(PdfValidationException, match="needs a width"):
            _laid_out(TextList(["x"]), 0)

    def test_the_item_gap_falls_between_items_only(self):
        items = TextList(["one", "two"], item_gap=8)
        boxes, _faces = _laid_out(items)
        assert boxes[0].space_before == 0
        assert boxes[1].space_before == 8

    def test_an_item_may_set_its_own_style(self):
        items = TextList(["plain", ListItem("big", font_size=20)])
        boxes, _faces = _laid_out(items)
        assert boxes[0].style["font_size"] == 11
        assert boxes[1].style["font_size"] == 20


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------
class TestDrawing:
    def test_a_bulleted_list_reads_back(self):
        document, page = _page()
        result = page.add_list(TextList(["first", "second"], width=400), 72, 700)
        assert result == {"pages": [0], "bottom": pytest.approx(result["bottom"]),
                          "items": 2}
        assert page.extract_text() == "• first\n• second"
        document.close()

    def test_an_ordered_nested_list_reads_back(self):
        document, page = _page()
        items = TextList(ordered=True, width=420)
        items.add_item("First", items=["sub one", "sub two"])
        items.add_item("Second", items=[ListItem("deep", items=["deeper"])])
        page.add_list(items, 72, 740)
        assert page.extract_text().split("\n") == [
            "1. First",
            "a. sub one",
            "b. sub two",
            "2. Second",
            "a. deep",
            "i. deeper",
        ]
        document.close()

    def test_the_marker_is_the_font_s_own_code(self):
        """A bullet is 0x95 in WinAnsiEncoding, not the text's UTF-8."""
        document, page = _page()
        page.add_list(TextList(["x"], width=300), 72, 700)
        assert b"(\x95) Tj" in page.content
        document.close()

    def test_a_marker_the_font_cannot_show_is_refused(self):
        document, page = _page()
        with pytest.raises(PdfValidationException, match="no code for"):
            page.add_list(TextList(["x"], markers="\u2713", width=300), 72, 700)
        document.close()

    def test_an_item_with_no_marker_draws_none(self):
        document, page = _page()
        page.add_list(TextList([ListItem("quiet", label="")], width=300), 72, 700)
        assert page.extract_text() == "quiet"
        document.close()

    def test_a_list_with_no_items_refused(self):
        document, page = _page()
        with pytest.raises(PdfValidationException, match="nothing to draw"):
            page.add_list(TextList(), 72, 700)
        document.close()

    def test_without_a_width_the_room_to_the_right_edge_is_used(self):
        document, page = _page()
        page.add_list(TextList(["word " * 80]), 72, 700)
        face = _StandardFace(document._engine_pdf, "Helvetica")
        widest = max(
            face.width(line.lstrip("\u2022 "), 11)
            for line in page.extract_text().split("\n")
            if line
        )
        assert 380 < widest <= 523.28
        document.close()

    def test_an_embedded_font_sets_the_whole_list(self):
        from aspose_pdf.engine.std_font_data import load_substitute_sfnt

        data = load_substitute_sfnt("sans-regular")
        if not data:
            pytest.skip("no bundled substitute font to embed")
        document, page = _page()
        page.add_list(
            TextList(["Zażółć gęślą"],
                     width=300, font=data),
            72,
            700,
        )
        assert "Zażółć" in page.extract_text()
        document.close()

    def test_a_list_and_a_block_can_share_a_page(self):
        from aspose_pdf import TextBlock

        document, page = _page()
        end = page.add_text_block(TextBlock("An introduction.", width=420), 72, 740)
        below = page.add_list(TextList(["first", "second"], width=420), 72,
                              end["bottom"] - 10)
        assert below["bottom"] < end["bottom"]
        text = page.extract_text()
        assert "introduction" in text and "first" in text
        document.close()


# ---------------------------------------------------------------------------
# Breaking onto the next page
# ---------------------------------------------------------------------------
class TestPageBreaking:
    def _long(self, count: int = 60) -> TextList:
        return TextList(
            [f"Item {i}: " + "text that has to wrap because it is long " * 3
             for i in range(1, count + 1)],
            width=420,
        )

    def test_a_long_list_adds_a_page(self):
        document, page = _page()
        result = page.add_list(self._long(), 72, 740)
        assert document.page_count > 1
        assert result["pages"] == list(range(document.page_count))
        document.close()

    def test_an_existing_page_is_used(self):
        document, page = _page()
        document.pages.add(PageSize.A4)
        result = page.add_list(self._long(12), 72, 300)
        assert document.page_count == 2  # the one that was already there
        assert result["pages"] == [0, 1]
        assert document.pages[1].extract_text().strip()
        document.close()

    def test_a_continuation_keeps_the_height_it_started_at(self):
        """As a table's and a text block's do: the top margin is the caller's."""
        document, page = _page()
        page.add_list(self._long(12), 72, 300)
        top_of_second = max(y for _x, y in _positions(document.pages[1].content))
        top_of_first = max(y for _x, y in _positions(document.pages[0].content))
        assert top_of_second == pytest.approx(top_of_first, abs=0.01)
        document.close()

    def test_the_continuation_starts_at_the_same_height(self):
        document, page = _page()
        page.add_list(self._long(), 72, 700)
        first = _positions(document.pages[0].content)[0][1]
        second = _positions(document.pages[1].content)[0][1]
        assert second == pytest.approx(first, abs=0.01)
        document.close()

    def test_a_marker_never_ends_up_alone(self):
        """It rides its item's first line, so it goes where that line goes."""
        document, page = _page()
        page.add_list(self._long(), 72, 740)
        for index in range(document.page_count):
            marks = _marks(document.pages[index].content)
            # Every /Lbl is followed by the /LBody of the item it belongs to.
            for position, mark in enumerate(marks):
                if mark == "Lbl":
                    assert marks[position + 1 : position + 2] == ["LBody"]
        document.close()

    def test_a_first_line_that_cannot_fit_is_refused(self):
        document, page = _page()
        with pytest.raises(PdfValidationException, match="does not fit"):
            page.add_list(TextList(["x"], width=300), 72, 40)
        document.close()

    def test_the_bottom_margin_is_honoured(self):
        document, page = _page()
        page.add_list(self._long(), 72, 700, bottom_margin=300)
        lowest = min(y for _x, y in _positions(document.pages[0].content))
        assert lowest >= 300
        document.close()


# ---------------------------------------------------------------------------
# Tagging
# ---------------------------------------------------------------------------
def _struct_root(document: Document) -> PdfDictionary:
    engine = document._engine_pdf
    root = engine._resolve(engine._cos_doc.trailer.get(PdfName("Root")))
    return engine._resolve(root.get(PdfName("StructTreeRoot")))


def _kids(document: Document, element: PdfDictionary) -> list:
    engine = document._engine_pdf
    kids = engine._resolve(element.get(PdfName("K")))
    if isinstance(kids, PdfArray):
        return [engine._resolve(item) for item in kids.items]
    return [] if kids is None else [engine._resolve(kids)]


def _kind(document: Document, element) -> str | None:
    if not isinstance(element, PdfDictionary):
        return None
    return document._engine_pdf._get_name(element.get(PdfName("S")))


class TestTagging:
    def test_the_shape_is_l_of_li_of_lbl_and_lbody(self):
        document, page = _page()
        page.add_list(TextList(["one", "two"], width=400), 72, 700)
        (top,) = _kids(document, _struct_root(document))
        assert _kind(document, top) == "L"
        items = _kids(document, top)
        assert [_kind(document, item) for item in items] == ["LI", "LI"]
        assert [_kind(document, k) for k in _kids(document, items[0])] == [
            "Lbl",
            "LBody",
        ]
        document.close()

    def test_a_sub_list_nests_inside_its_item_s_body(self):
        document, page = _page()
        page.add_list(
            TextList([ListItem("one", items=["a"])], width=400), 72, 700
        )
        (top,) = _kids(document, _struct_root(document))
        (item,) = _kids(document, top)
        label, body = _kids(document, item)
        assert _kind(document, label) == "Lbl"
        nested = [k for k in _kids(document, body) if _kind(document, k) == "L"]
        assert len(nested) == 1
        (sub_item,) = _kids(document, nested[0])
        assert _kind(document, sub_item) == "LI"
        document.close()

    def test_the_list_says_what_its_markers_mean(self):
        document, page = _page()
        page.add_list(TextList(["one"], ordered=True, width=400), 72, 700)
        (top,) = _kids(document, _struct_root(document))
        attributes = document._engine_pdf._resolve(top.get(PdfName("A")))
        assert document._engine_pdf._get_name(attributes.get(PdfName("O"))) == "List"
        assert (
            document._engine_pdf._get_name(attributes.get(PdfName("ListNumbering")))
            == "Decimal"
        )
        document.close()

    def test_a_sub_list_says_its_own(self):
        document, page = _page()
        page.add_list(
            TextList([ListItem("one", items=["a"])], ordered=True, width=400), 72, 700
        )
        (top,) = _kids(document, _struct_root(document))
        (item,) = _kids(document, top)
        body = _kids(document, item)[1]
        (nested,) = [k for k in _kids(document, body) if _kind(document, k) == "L"]
        attributes = document._engine_pdf._resolve(nested.get(PdfName("A")))
        assert (
            document._engine_pdf._get_name(attributes.get(PdfName("ListNumbering")))
            == "LowerAlpha"
        )
        document.close()

    def test_a_marker_with_no_standard_name_writes_no_attribute(self):
        document, page = _page()
        page.add_list(TextList(["one"], markers="\u2013", width=400), 72, 700)
        (top,) = _kids(document, _struct_root(document))
        assert PdfName("A") not in top.mapping
        document.close()

    def test_every_line_of_a_wrapped_item_is_in_its_body(self):
        document, page = _page()
        page.add_list(TextList(["word " * 40], width=300), 72, 700)
        (top,) = _kids(document, _struct_root(document))
        (item,) = _kids(document, top)
        body = _kids(document, item)[1]
        kids = document._engine_pdf._resolve(body.get(PdfName("K")))
        assert isinstance(kids, PdfArray)
        assert len(kids.items) == page.content.count(b"/LBody")
        document.close()

    def test_tagging_can_be_turned_off(self):
        document, page = _page()
        page.add_list(TextList(["one"], width=400), 72, 700, tag=False)
        assert b"BDC" not in page.content
        document.close()

    def test_a_list_across_pages_stays_one_list(self):
        document, page = _page()
        page.add_list(
            TextList([f"item {i}" for i in range(1, 80)], width=400), 72, 740
        )
        assert document.page_count > 1
        tops = _kids(document, _struct_root(document))
        assert [_kind(document, top) for top in tops] == ["L"]
        assert len(_kids(document, tops[0])) == 79
        document.close()

    def test_an_item_straddling_a_page_names_the_far_ids_with_mcrs(self):
        document, page = _page()
        page.add_list(
            TextList(
                ["short", "long " + "and it keeps going " * 40],
                width=420,
            ),
            72,
            130,
        )
        assert document.page_count > 1
        engine = document._engine_pdf
        (top,) = _kids(document, _struct_root(document))
        bodies = [
            k
            for item in _kids(document, top)
            for k in _kids(document, item)
            if _kind(document, k) == "LBody"
        ]
        references = []
        for body in bodies:
            kids = engine._resolve(body.get(PdfName("K")))
            items = kids.items if isinstance(kids, PdfArray) else [kids]
            for entry in items:
                resolved = engine._resolve(entry)
                if isinstance(resolved, PdfDictionary):
                    references.append(resolved)
        assert references, "a straddling item needs marked-content references"
        for reference in references:
            assert engine._get_name(reference.get(PdfName("Type"))) == "MCR"
            assert PdfName("Pg") in reference.mapping
            assert isinstance(
                engine._resolve(reference.get(PdfName("MCID"))), PdfNumber
            )
        document.close()

    def test_every_marked_id_resolves_through_the_parent_tree(self):
        document, page = _page()
        page.add_list(
            TextList([f"item {i}" for i in range(1, 80)], width=400), 72, 740
        )
        engine = document._engine_pdf
        for index in range(document.page_count):
            content = document.pages[index].content
            ids = [int(m) for m in re.findall(rb"/MCID (\d+)", content)]
            parents = engine._parent_tree_array_for_page(
                _struct_root(document), engine._get_page_dict(index)
            )
            assert ids
            for mcid in ids:
                assert mcid < len(parents.items)
                assert engine._resolve(parents.items[mcid]) is not None
        document.close()

    def test_a_tagged_list_passes_pdfua(self):
        document, page = _page()
        items = TextList(ordered=True, width=420)
        items.add_item("First", items=["sub one", "sub two"])
        items.add_item("Second")
        page.add_list(items, 72, 700)
        document.convert_to_pdfua(title="A list")
        buffer = io.BytesIO()
        document.save(buffer)
        document.close()
        with Document(buffer.getvalue()) as reopened:
            result = reopened.validate_pdfua()
        assert result.is_valid, result.errors
        assert result.warnings == []

    def test_a_tagged_list_passes_pdfa_2a(self):
        document, page = _page()
        page.add_list(TextList(["one", "two"], width=420), 72, 700)
        document.convert_to_pdfua(title="A list")
        assert document.convert_to_pdfa("2a") == []
        buffer = io.BytesIO()
        document.save(buffer)
        document.close()
        with Document(buffer.getvalue()) as reopened:
            assert reopened.validate_pdfa("2a").is_valid

    def test_a_list_across_pages_passes_pdfua(self):
        document, page = _page()
        page.add_list(
            TextList(
                [f"Item {i}: " + "text that wraps because it is long " * 3
                 for i in range(1, 40)],
                ordered=True,
                width=420,
            ),
            72,
            740,
        )
        assert document.page_count > 1
        document.convert_to_pdfua(title="A long list")
        buffer = io.BytesIO()
        document.save(buffer)
        document.close()
        with Document(buffer.getvalue()) as reopened:
            result = reopened.validate_pdfua()
        assert result.is_valid, result.errors
        assert result.warnings == []


def test_a_saved_list_reads_back_as_its_items(tmp_path):
    target = tmp_path / "list.pdf"
    document, page = _page()
    items = TextList(ordered=True, width=420, item_gap=4)
    items.add_item("Measure the room", items=["with the font's own advances"])
    items.add_item("Wrap the text to it")
    page.add_list(items, 72, 740)
    document.save(target)
    document.close()

    with Document(target) as reopened:
        text = reopened.pages[0].extract_text()
    assert text.split("\n") == [
        "1. Measure the room",
        "a. with the font's own advances",
        "2. Wrap the text to it",
    ]
