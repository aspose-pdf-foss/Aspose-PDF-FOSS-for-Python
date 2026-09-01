"""What ``auto_tag`` could not see: sparse tables, nesting, and drawn bullets.

Four shapes fell through the inferencer, each for the same underlying reason —
the rule that recognised the common case was written strictly enough to reject
its variations.

A table had to be a perfect rectangle, so one merged heading or one blank cell
turned the whole grid back into loose paragraphs. A table with generous column
spacing was cut into page columns before anything looked for a grid, and
reading a table column-major turns its rows inside out. A list was a flat run
of items however far they were indented. And a list whose markers were *drawn*
rather than typed had no marker text to recognise at all.
"""

from __future__ import annotations

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.auto_tag import (
    LayoutElement,
    assign_list_depths,
    attach_image_bullets,
    detect_columns,
    detect_tables,
    group_rows,
)
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfStream,
)
from aspose_pdf.engine.simple_pdf import SimplePdf


def _cell(x: float, y: float, chars: int = 4, size: float = 10.0) -> LayoutElement:
    return LayoutElement(
        "text", 0, 0, x=x, y=y, font_size=size, text_length=chars, tag="P"
    )


def _tagged(content: bytes, *, images: dict[str, tuple[int, int]] | None = None):
    """Auto-tag a one-page document and return ``(engine, structure root)``."""
    pdf = SimplePdf(pages=[(0, 0, 612, 792)], page_contents=[content])
    pdf._ensure_cos()
    if images:
        entries = {}
        for name in images:
            stream = PdfStream(
                b"\x00",
                {
                    PdfName("Type"): PdfName("XObject"),
                    PdfName("Subtype"): PdfName("Image"),
                    PdfName("Width"): PdfNumber(1),
                    PdfName("Height"): PdfNumber(1),
                    PdfName("ColorSpace"): PdfName("DeviceGray"),
                    PdfName("BitsPerComponent"): PdfNumber(8),
                    PdfName("Length"): PdfNumber(1),
                },
            )
            entries[PdfName(name)] = pdf._cos_doc.register_object(stream)
        pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
            {PdfName("XObject"): PdfDictionary(entries)}
        )
    document = Document()
    document._engine_pdf = pdf
    document.auto_tag()
    root = pdf._resolve(pdf._cos_doc.trailer.mapping[PdfName("Root")])
    return pdf, pdf._resolve(root.mapping[PdfName("StructTreeRoot")])


def _tree(pdf: SimplePdf, ref) -> tuple:
    """The structure subtree as nested ``(type, children...)`` tuples."""
    element = pdf._resolve(ref)
    kind = element.mapping[PdfName("S")].name.lstrip("/")
    kids = pdf._resolve(element.mapping.get(PdfName("K")))
    items = kids.items if isinstance(kids, PdfArray) else [kids]
    children = []
    for kid in items:
        resolved = pdf._resolve(kid)
        if isinstance(resolved, PdfDictionary) and PdfName("S") in resolved.mapping:
            children.append(_tree(pdf, kid))
    return (kind, *children)


def _roots(pdf: SimplePdf, struct_root) -> list[tuple]:
    kids = pdf._resolve(struct_root.mapping[PdfName("K")])
    return [_tree(pdf, kid) for kid in kids.items]


# ---------------------------------------------------------------------------
# Tables with blanks and merges
# ---------------------------------------------------------------------------


def test_a_row_that_leaves_a_column_blank_stays_in_the_grid():
    rows = [
        [_cell(72, 700), _cell(200, 700), _cell(330, 700)],
        [_cell(72, 686), _cell(330, 686)],  # middle column empty
        [_cell(72, 672), _cell(200, 672), _cell(330, 672)],
    ]
    segments = detect_tables(rows)

    assert [kind for kind, _ in segments] == ["table"]
    assert len(segments[0][1]) == 3


def test_a_blank_cell_keeps_the_columns_after_it_in_place():
    # Closing the gap up would move the third column's value into the second.
    rows = [
        [_cell(72, 700), _cell(200, 700), _cell(330, 700)],
        [_cell(72, 686), _cell(330, 686)],
    ]
    (_, grid), = detect_tables(rows)

    assert [e.column for e in grid[1]] == [0, 2]


def test_a_cell_reaching_past_the_next_column_is_a_merge():
    wide = _cell(72, 700, chars=40)  # 200 units: past the 200 anchor, not 330
    rows = [
        [wide, _cell(330, 700)],
        [_cell(72, 686), _cell(200, 686), _cell(330, 686)],
        [_cell(72, 672), _cell(200, 672), _cell(330, 672)],
    ]
    (_, grid), = detect_tables(rows)

    assert wide.span == 2
    assert grid[1][1].span == 1


def test_two_cells_on_one_anchor_break_the_grid():
    # Both cells of the second row sit on the first column: the row does not
    # fit the grid, and guessing which column the second belongs to would put
    # a value under the wrong heading.
    rows = [
        [_cell(72, 700), _cell(200, 700)],
        [_cell(72, 686), _cell(74, 686)],
        [_cell(72, 672), _cell(200, 672)],
    ]
    # No row can anchor a grid the others fit, so none of it is a table.
    assert [kind for kind, _ in detect_tables(rows)] == ["flow"]


def test_a_run_of_single_cell_lines_is_not_a_one_column_table():
    # Allowing blanks must not turn ordinary left-aligned prose into a table.
    rows = [[_cell(72, 700 - 14 * i)] for i in range(5)]
    assert [kind for kind, _ in detect_tables(rows)] == ["flow"]


def test_a_table_needs_most_of_its_rows_to_show_the_grid():
    rows = [
        [_cell(72, 700), _cell(200, 700)],
        [_cell(72, 686)],
        [_cell(72, 672)],
        [_cell(72, 658)],
        [_cell(72, 644), _cell(200, 644)],
    ]
    # Two full rows out of five: mostly prose that happens to align.
    assert [kind for kind, _ in detect_tables(rows)] == ["flow"]


def test_a_table_does_not_swallow_the_sentence_above_it():
    rows = [
        [_cell(72, 720, chars=50)],  # "The table below shows..."
        [_cell(72, 700), _cell(200, 700)],
        [_cell(72, 686), _cell(200, 686)],
    ]
    assert [(kind, len(v)) for kind, v in detect_tables(rows)] == [
        ("flow", 1),
        ("table", 2),
    ]


def test_a_table_does_not_swallow_the_sentence_below_it():
    rows = [
        [_cell(72, 700), _cell(200, 700)],
        [_cell(72, 686), _cell(200, 686)],
        [_cell(72, 660, chars=50)],
    ]
    assert [(kind, len(v)) for kind, v in detect_tables(rows)] == [
        ("table", 2),
        ("flow", 1),
    ]


def test_a_blank_cell_becomes_an_empty_td_in_the_tag_tree():
    pdf, struct_root = _tagged(
        b"BT /F1 10 Tf 1 0 0 1 72 700 Tm (a) Tj ET\n"
        b"BT /F1 10 Tf 1 0 0 1 200 700 Tm (b) Tj ET\n"
        b"BT /F1 10 Tf 1 0 0 1 330 700 Tm (c) Tj ET\n"
        b"BT /F1 10 Tf 1 0 0 1 72 686 Tm (d) Tj ET\n"
        b"BT /F1 10 Tf 1 0 0 1 330 686 Tm (f) Tj ET\n"
        b"BT /F1 10 Tf 1 0 0 1 72 672 Tm (g) Tj ET\n"
        b"BT /F1 10 Tf 1 0 0 1 200 672 Tm (h) Tj ET\n"
        b"BT /F1 10 Tf 1 0 0 1 330 672 Tm (i) Tj ET"
    )
    tables = [root for root in _roots(pdf, struct_root) if root[0] == "Table"]

    assert len(tables) == 1
    # Every row has a cell in all three columns; the middle one of the second
    # row is an empty /TD rather than a missing one.
    assert [len(row) - 1 for row in tables[0][1:]] == [3, 3, 3]


def test_a_merged_cell_declares_its_column_span():
    pdf, struct_root = _tagged(
        b"BT /F1 10 Tf 1 0 0 1 72 700 Tm (a heading across two) Tj ET\n"
        b"BT /F1 10 Tf 1 0 0 1 260 700 Tm (c) Tj ET\n"
        b"BT /F1 10 Tf 1 0 0 1 72 686 Tm (d) Tj ET\n"
        b"BT /F1 10 Tf 1 0 0 1 160 686 Tm (e) Tj ET\n"
        b"BT /F1 10 Tf 1 0 0 1 260 686 Tm (f) Tj ET\n"
        b"BT /F1 10 Tf 1 0 0 1 72 672 Tm (g) Tj ET\n"
        b"BT /F1 10 Tf 1 0 0 1 160 672 Tm (h) Tj ET\n"
        b"BT /F1 10 Tf 1 0 0 1 260 672 Tm (i) Tj ET"
    )
    spans = []
    kids = pdf._resolve(struct_root.mapping[PdfName("K")])
    table = pdf._resolve(kids.items[0])
    for tr_ref in pdf._resolve(table.mapping[PdfName("K")]).items:
        tr = pdf._resolve(tr_ref)
        for td_ref in pdf._resolve(tr.mapping[PdfName("K")]).items:
            td = pdf._resolve(td_ref)
            value = td.mapping.get(PdfName("ColSpan"))
            spans.append(int(value.value) if value is not None else 1)

    assert 2 in spans


def test_the_html_export_gives_a_merged_cell_a_colspan():
    from aspose_pdf.engine.text_export import Block, _html_block

    block = Block(
        kind="table",
        rows=[["wide", "", "right"], ["a", "b", "c"]],
        spans=[[2, 1, 1], [1, 1, 1]],
    )
    html = _html_block(block, embed_images=False)

    assert '<th colspan="2">wide</th>' in html
    # The column the merge covers is not emitted a second time.
    assert html.count("<th") == 2


# ---------------------------------------------------------------------------
# A wide gutter between a table's columns is not a gutter between the page's
# ---------------------------------------------------------------------------


def test_narrow_cells_across_a_wide_gap_are_a_table():
    left = [_cell(72, y, chars=2) for y in (700, 686, 672)]
    right = [_cell(340, y, chars=2) for y in (700, 686, 672)]

    assert len(detect_columns(left + right)) == 1


def test_prose_running_up_to_the_gap_is_still_two_columns():
    # 24 bytes at 10pt is about 120 units, filling most of the band.
    left = [_cell(72, y, chars=24) for y in (700, 686, 672)]
    right = [_cell(340, y, chars=24) for y in (700, 686, 672)]

    assert len(detect_columns(left + right)) == 2


def test_columns_whose_lines_do_not_pair_up_are_still_columns():
    # A short left column beside a long right one: the sides are independent,
    # which is what a page's columns look like and a table's never do.
    left = [_cell(72, y, chars=2) for y in (700, 686)]
    right = [_cell(340, y, chars=2) for y in (700, 686, 672, 658, 644, 630)]

    assert len(detect_columns(left + right)) == 2


def test_a_page_with_no_measurable_widths_keeps_the_column_reading():
    # Zero-length elements carry no evidence either way, and no evidence is a
    # reason to keep the previous answer rather than overturn it.
    left = [LayoutElement("text", 0, 0, x=72, y=y, font_size=10, tag="P") for y in (700, 686, 672)]
    right = [LayoutElement("text", 0, 0, x=340, y=y, font_size=10, tag="P") for y in (700, 686, 672)]

    assert len(detect_columns(left + right)) == 2


def test_a_wide_gutter_grid_is_tagged_as_a_table():
    pdf, struct_root = _tagged(
        b"BT /F1 12 Tf 1 0 0 1 72 700 Tm (a) Tj ET\n"
        b"BT /F1 12 Tf 1 0 0 1 400 700 Tm (b) Tj ET\n"
        b"BT /F1 12 Tf 1 0 0 1 72 686 Tm (c) Tj ET\n"
        b"BT /F1 12 Tf 1 0 0 1 400 686 Tm (d) Tj ET"
    )
    assert [root[0] for root in _roots(pdf, struct_root)] == ["Table"]


# ---------------------------------------------------------------------------
# Nested lists
# ---------------------------------------------------------------------------


def _item(x: float, y: float) -> list[LayoutElement]:
    return [
        LayoutElement(
            "text", 0, 0, x=x, y=y, font_size=10, tag="P", text_head="- text"
        )
    ]


def test_indentation_becomes_nesting_depth():
    items = [_item(72, 700), _item(90, 686), _item(90, 672), _item(108, 658)]
    assign_list_depths(items)
    assert [item[0].depth for item in items] == [0, 1, 1, 2]


def test_returning_to_an_outer_indent_closes_the_inner_levels():
    items = [_item(72, 700), _item(90, 686), _item(108, 672), _item(72, 658)]
    assign_list_depths(items)
    assert [item[0].depth for item in items] == [0, 1, 2, 0]


def test_a_new_indent_after_returning_is_one_level_deep_not_two():
    # The level opened at 90 is closed by returning to 72, so the later indent
    # at 100 opens a fresh first level rather than stacking on the old one.
    items = [_item(72, 700), _item(90, 686), _item(72, 672), _item(100, 658)]
    assign_list_depths(items)
    assert [item[0].depth for item in items] == [0, 1, 0, 1]


def test_a_flat_list_stays_at_one_level():
    items = [_item(72, 700 - 14 * i) for i in range(4)]
    assign_list_depths(items)
    assert [item[0].depth for item in items] == [0, 0, 0, 0]


def test_a_hair_of_drift_is_not_a_new_level():
    items = [_item(72, 700), _item(73.5, 686), _item(71, 672)]
    assign_list_depths(items)
    assert [item[0].depth for item in items] == [0, 0, 0]


def test_nesting_depth_is_capped():
    items = [_item(72 + 20 * i, 700 - 14 * i) for i in range(8)]
    assign_list_depths(items)
    assert max(item[0].depth for item in items) == 4


def test_a_sub_list_is_nested_inside_the_item_it_belongs_to():
    pdf, struct_root = _tagged(
        b"BT /F1 10 Tf 1 0 0 1 72 700 Tm (- top one) Tj ET\n"
        b"BT /F1 10 Tf 1 0 0 1 92 686 Tm (- nested a) Tj ET\n"
        b"BT /F1 10 Tf 1 0 0 1 92 672 Tm (- nested b) Tj ET\n"
        b"BT /F1 10 Tf 1 0 0 1 72 658 Tm (- top two) Tj ET"
    )
    (tree,) = _roots(pdf, struct_root)

    # /L -> /LI -> /LBody -> /L, which is where ISO 32000-1 puts a sub-list.
    assert tree == (
        "L",
        ("LI", ("LBody", ("L", ("LI", ("LBody",)), ("LI", ("LBody",))))),
        ("LI", ("LBody",)),
    )


def test_an_unnested_list_is_unchanged():
    pdf, struct_root = _tagged(
        b"BT /F1 10 Tf 1 0 0 1 72 700 Tm (- one) Tj ET\n"
        b"BT /F1 10 Tf 1 0 0 1 72 686 Tm (- two) Tj ET"
    )
    (tree,) = _roots(pdf, struct_root)
    assert tree == ("L", ("LI", ("LBody",)), ("LI", ("LBody",)))


# ---------------------------------------------------------------------------
# Lists whose markers are drawn, not typed
# ---------------------------------------------------------------------------


def _bulleted(size: int = 8, gap: int = 14) -> bytes:
    lines = []
    for index, y in enumerate((700, 686, 672)):
        lines.append(b"q %d 0 0 %d 60 %d cm /Bul Do Q" % (size, size, y))
        lines.append(
            b"BT /F1 10 Tf 1 0 0 1 %d %d Tm (item %d) Tj ET"
            % (60 + gap, y, index)
        )
    return b"\n".join(lines)


def test_a_drawn_bullet_becomes_the_items_label():
    pdf, struct_root = _tagged(_bulleted(), images={"Bul": (1, 1)})
    (tree,) = _roots(pdf, struct_root)

    assert tree == (
        "L",
        ("LI", ("Lbl",), ("LBody",)),
        ("LI", ("Lbl",), ("LBody",)),
        ("LI", ("Lbl",), ("LBody",)),
    )


def test_the_bullet_image_is_not_also_a_standalone_figure():
    pdf, struct_root = _tagged(_bulleted(), images={"Bul": (1, 1)})
    assert [root[0] for root in _roots(pdf, struct_root)] == ["L"]


def test_a_picture_beside_text_is_not_a_bullet():
    # Too big to be a marker: it stays a figure and the line stays a paragraph.
    pdf, struct_root = _tagged(_bulleted(size=60), images={"Bul": (1, 1)})
    kinds = [root[0] for root in _roots(pdf, struct_root)]
    assert "L" not in kinds
    assert "Figure" in kinds


def test_an_image_too_far_from_the_text_is_not_a_bullet():
    text = _cell(200, 700)
    bullet = LayoutElement(
        "xobject", 0, 0, x=64, y=703, width=8, height=8, tag="Figure"
    )
    attach_image_bullets([bullet, text])
    assert text.bullet is None


def test_a_wide_flat_image_is_not_a_bullet():
    # A rule that only looked at height would take a banner for a marker.
    text = _cell(80, 700)
    banner = LayoutElement(
        "xobject", 0, 0, x=40, y=702, width=70, height=6, tag="Figure"
    )
    attach_image_bullets([banner, text])
    assert text.bullet is None


def test_a_tall_narrow_image_is_not_a_bullet():
    text = _cell(80, 700)
    rule = LayoutElement(
        "xobject", 0, 0, x=70, y=702, width=6, height=70, tag="Figure"
    )
    attach_image_bullets([rule, text])
    assert text.bullet is None


def test_one_bullet_does_not_mark_two_runs_of_the_same_line():
    # A bulleted line is often drawn as several text runs; marking each of them
    # would turn one item into several.
    first = _cell(74, 700)
    second = _cell(90, 700)
    bullet = LayoutElement(
        "xobject", 0, 0, x=66, y=703, width=8, height=8, tag="Figure"
    )
    attach_image_bullets([bullet, first, second])
    assert first.bullet is bullet
    assert second.bullet is None


def test_an_image_bullet_needs_the_baseline_of_the_line_it_marks():
    from aspose_pdf.engine.auto_tag import attach_image_bullets as attach

    text = _cell(80, 700)
    far_above = LayoutElement(
        "xobject", 0, 0, x=64, y=760, width=8, height=8, tag="Figure"
    )
    attach([far_above, text])
    assert text.bullet is None


def test_one_image_cannot_bullet_two_lines():
    text_a = _cell(80, 700)
    text_b = _cell(80, 686)
    bullet = LayoutElement(
        "xobject", 0, 0, x=64, y=703, width=8, height=8, tag="Figure"
    )
    attach_image_bullets([bullet, text_a, text_b])
    assert text_a.bullet is bullet
    assert text_b.bullet is None


@pytest.mark.parametrize("rows_in_page", [1])
def test_a_single_drawn_bullet_is_not_a_list(rows_in_page):
    content = (
        b"q 8 0 0 8 60 700 cm /Bul Do Q\n"
        b"BT /F1 10 Tf 1 0 0 1 74 700 Tm (only one) Tj ET"
    )
    pdf, struct_root = _tagged(content, images={"Bul": (1, 1)})
    assert "L" not in [root[0] for root in _roots(pdf, struct_root)]


def test_group_rows_is_not_what_pairs_a_bullet_with_its_line():
    """The anchor recorded for an image is its centre, not its baseline.

    An 8-unit bullet on a 10pt line sits 4 units above the baseline — outside
    the row tolerance — so pairing has to compare the image's *extent* with the
    baseline rather than lean on row grouping.
    """
    text = _cell(80, 700)
    bullet = LayoutElement(
        "xobject", 0, 0, x=64, y=704, width=8, height=8, tag="Figure"
    )
    assert len(group_rows([bullet, text])) == 2  # different rows...

    attach_image_bullets([bullet, text])
    assert text.bullet is bullet  # ...and still the same item
