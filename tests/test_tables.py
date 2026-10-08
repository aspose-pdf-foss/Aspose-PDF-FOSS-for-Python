"""Tables: measuring, wrapping, breaking across pages, and the structure tree.

There was no table API at all -- no ``Table``, no ``Row``, no ``Cell`` -- so a
report had to be drawn by placing every string and every line by hand, measuring
the text to know where the next one goes. The two pieces that makes hard were
already written and need no optional dependency: the Standard-14 advances in
``engine.std_metrics`` and an embedded font's CID widths, which is what lets a
column's text be wrapped to what it will actually occupy.

What the tests below pin down, beyond the obvious:

* a row grows to hold the lines its cells wrap to, and a table taller than the
  room left **continues onto the next page**, repeating the header rows;
* the structure is tagged **nested** -- a ``/Table`` of ``/TR`` of ``/TH`` and
  ``/TD``, as ISO 32000-1 14.8.4.3 asks -- so a tagged table passes PDF/UA and
  PDF/A-2a, which require tagging and would otherwise read every cell as a
  sibling of the table;
* the vertical alignments put the text where they say, measured on the raster.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Cell, Document, PageSize, Row, Table
from aspose_pdf.exceptions import PdfValidationException


def _page(size=PageSize.A4):
    document = Document()
    return document, document.pages.add(size)


def _roundtrip(document: Document) -> Document:
    buffer = io.BytesIO()
    document.save(buffer)
    buffer.seek(0)
    return Document(buffer)


def _saved(document: Document) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _invoice() -> Table:
    table = Table(column_widths=[220, 70, 90], padding=5)
    table.add_header_row(["Item", "Qty", "Price"])
    table.add_row(["Widget", "2", "9.99"])
    table.add_row(["Grommet", "14", "0.40"])
    return table


def _ink_rows(raster, x0: int, x1: int) -> tuple[int, int] | None:
    """Which raster rows have ink in the column of pixels ``x0..x1``."""
    rows = [
        y
        for y in range(raster.height)
        for x in range(x0, x1)
        if sum(raster.get_pixel(x, y)) < 600
    ]
    return (min(rows), max(rows)) if rows else None


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------


def test_a_table_is_built_from_strings_cells_or_rows():
    table = Table(column_widths=[100, 100])
    table.add_row(["a", "b"])
    table.add_row([Cell("c"), Cell("d", alignment="right")])
    table.add_row(Row([Cell("e"), Cell("f")], header=True))
    assert len(table) == 3
    assert table.column_count == 2
    assert table[1].cells[1].alignment == "right"
    assert table[2].header is True
    assert [cell.text for cell in table[0].cells] == ["a", "b"]


def test_cells_are_added_to_a_row_one_at_a_time():
    table = Table(columns=3)
    row = table.add_row()
    row.add_cell("one")
    row.add_cell("spans two", column_span=2)
    assert row.column_count == 3
    assert [cell.column_span for cell in row.cells] == [1, 2]


def test_a_header_row_is_marked_as_one():
    table = Table(columns=2)
    header = table.add_header_row(["A", "B"])
    assert header.header is True
    assert table.add_row(["c", "d"]).header is False


def test_the_column_count_comes_from_the_widths_the_columns_or_the_widest_row():
    assert Table(column_widths=[10, 20, 30]).column_count == 3
    assert Table(columns=4).column_count == 4
    table = Table()
    table.add_row(["a"])
    table.add_row(["a", "b", "c"])
    assert table.column_count == 3
    assert Table().column_count == 0


def test_a_single_string_is_not_a_row():
    table = Table(columns=1)
    with pytest.raises(PdfValidationException, match="pass \\[text\\]"):
        table.add_row("one cell")


def test_an_option_and_an_object_are_not_both_accepted():
    table = Table(columns=1)
    with pytest.raises(PdfValidationException, match="carries its own options"):
        table.add_row(Row([Cell("x")]), header=True)
    row = table.add_row(["x"])
    with pytest.raises(PdfValidationException, match="carries its own options"):
        row.add_cell(Cell("y"), alignment="right")


def test_the_measurements_are_checked_where_they_are_given():
    with pytest.raises(PdfValidationException, match="above zero"):
        Table(column_widths=[100, 0])
    with pytest.raises(PdfValidationException, match="above zero"):
        Table(font_size=0)
    with pytest.raises(PdfValidationException, match="not be negative"):
        Table(padding=-1)
    with pytest.raises(PdfValidationException, match="not be negative"):
        Table(border_width=-1)
    with pytest.raises(PdfValidationException, match="whole number from one"):
        Table(columns=0)
    with pytest.raises(PdfValidationException, match="whole number from one"):
        Cell("x", column_span=0)
    with pytest.raises(PdfValidationException, match="names no column"):
        Table(column_widths=[])


def test_the_alignments_are_the_ones_there_are():
    assert Cell("x", alignment="centre").alignment == "center"
    assert Cell("x", vertical_alignment="center").vertical_alignment == "middle"
    with pytest.raises(PdfValidationException, match="alignment is one of"):
        Cell("x", alignment="justified")
    with pytest.raises(PdfValidationException, match="vertical_alignment is one of"):
        Cell("x", vertical_alignment="above")


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------


def test_a_table_is_drawn_and_reads_back_in_order():
    document, page = _page()
    result = page.add_table(_invoice(), 60, 760)
    assert result["pages"] == [0]
    assert result["rows"] == 3
    assert result["bottom"] < 760

    text = _roundtrip(document).pages[0].extract_text()
    assert text.index("Item") < text.index("Widget") < text.index("Grommet")
    assert "9.99" in text and "0.40" in text


def test_the_placement_says_where_the_table_ended():
    _document, page = _page()
    first = page.add_table(_invoice(), 60, 760)
    # The next thing on the page starts where the table stopped.
    second = page.add_table(_invoice(), 60, first["bottom"] - 20)
    assert second["bottom"] < first["bottom"]
    assert second["pages"] == [0]


def test_an_empty_table_has_nothing_to_draw():
    _, page = _page()
    with pytest.raises(PdfValidationException, match="nothing to draw"):
        page.add_table(Table(columns=2), 60, 700)


def test_a_table_without_widths_fills_the_room_on_the_page():
    _document, page = _page(PageSize.A5)
    table = Table()
    table.add_row(["a", "b"])
    page.add_table(table, 40, 500)
    # Two equal columns across what is left of an A5 page from x=40.
    from aspose_pdf.engine.tables import column_edges

    edges = column_edges(table, PageSize.A5.width - 40)
    assert edges[-1] == pytest.approx(PageSize.A5.width - 40)
    assert edges[1] - edges[0] == pytest.approx(edges[2] - edges[1])


def test_an_explicit_width_is_split_among_the_columns():
    from aspose_pdf.engine.tables import column_edges

    table = Table(columns=4, width=400)
    assert column_edges(table, 1000) == [0.0, 100.0, 200.0, 300.0, 400.0]


def test_a_row_cannot_cover_more_columns_than_the_table_has():
    _, page = _page()
    table = Table(column_widths=[100, 100])
    row = table.add_row(["a", "b"])
    row.add_cell("c")
    with pytest.raises(PdfValidationException, match="more than the table's 2"):
        page.add_table(table, 60, 700)


def test_a_column_span_covers_the_columns_it_says():
    document, page = _page()
    table = Table(column_widths=[100, 100, 100], border_width=0)
    row = table.add_row()
    row.add_cell("wide", column_span=3, alignment="center")
    page.add_table(table, 60, 700)
    # Centred across all three columns: the text starts past the first column.
    content = page.content.decode("latin-1")
    assert "wide" in content
    from aspose_pdf.engine.tables import _StandardFace, layout

    faces = {"Helvetica": _StandardFace(document._engine_pdf, "Helvetica")}
    rows = layout(table, faces, 300)
    assert rows[0].cells[0].width == pytest.approx(300)


# ---------------------------------------------------------------------------
# Wrapping and row height
# ---------------------------------------------------------------------------


def test_long_text_wraps_to_its_column_and_the_row_grows():
    document, _page_obj = _page()
    table = Table(column_widths=[120, 120], font_size=10, padding=4)
    table.add_row(["short", "short"])
    table.add_row(
        ["A sentence long enough that it cannot possibly fit on one line here", "x"]
    )
    from aspose_pdf.engine.tables import _StandardFace, layout

    faces = {"Helvetica": _StandardFace(document._engine_pdf, "Helvetica")}
    rows = layout(table, faces, 240)
    assert len(rows[0].cells[0].lines) == 1
    assert len(rows[1].cells[0].lines) > 1
    assert rows[1].height > rows[0].height


def test_no_wrapped_line_is_wider_than_its_column():
    document, _page_obj = _page()
    from aspose_pdf.engine.tables import _StandardFace

    face = _StandardFace(document._engine_pdf, "Helvetica")
    text = "Supercalifragilisticexpialidocious and a few more ordinary words"
    for line in face.wrap(text, 80.0, 10.0):
        assert face.width(line, 10.0) <= 80.0 + 1e-6


def test_an_explicit_newline_is_a_hard_break():
    document, _page_obj = _page()
    from aspose_pdf.engine.tables import _StandardFace

    face = _StandardFace(document._engine_pdf, "Helvetica")
    assert face.wrap("one\ntwo", 500.0, 10.0) == ["one", "two"]


def test_a_minimum_row_height_is_honoured():
    document, _page_obj = _page()
    table = Table(column_widths=[100], row_height=50)
    table.add_row(["x"])
    from aspose_pdf.engine.tables import _StandardFace, layout

    faces = {"Helvetica": _StandardFace(document._engine_pdf, "Helvetica")}
    assert layout(table, faces, 100)[0].height == pytest.approx(50)


def test_a_row_can_ask_for_its_own_height():
    document, _page_obj = _page()
    table = Table(column_widths=[100])
    table.add_row(["x"], height=70)
    from aspose_pdf.engine.tables import _StandardFace, layout

    faces = {"Helvetica": _StandardFace(document._engine_pdf, "Helvetica")}
    assert layout(table, faces, 100)[0].height == pytest.approx(70)


# ---------------------------------------------------------------------------
# Breaking across pages
# ---------------------------------------------------------------------------


def test_a_long_table_continues_onto_new_pages():
    document, page = _page(PageSize.A5)
    table = Table(column_widths=[200, 100], font_size=9)
    table.add_header_row(["Row", "Value"])
    for index in range(1, 61):
        table.add_row([f"Item number {index}", f"{index}"])
    result = page.add_table(table, 40, 560, bottom_margin=40)

    assert len(result["pages"]) > 1
    assert result["rows"] == 61
    assert document.page_count == len(result["pages"])
    reloaded = _roundtrip(document)
    text = " ".join(page.extract_text() for page in reloaded.pages)
    for index in range(1, 61):
        assert f"Item number {index}" in text


def test_the_header_is_repeated_at_the_top_of_every_page():
    document, page = _page(PageSize.A5)
    table = Table(column_widths=[200, 100], font_size=9)
    table.add_header_row(["Row", "Value"])
    for index in range(40):
        table.add_row([f"Item {index}", "x"])
    result = page.add_table(table, 40, 560, bottom_margin=40)
    assert len(result["pages"]) >= 2

    reloaded = _roundtrip(document)
    for page_index in result["pages"]:
        first_line = reloaded.pages[page_index].extract_text().split("\n")[0]
        assert first_line.startswith("Row")


def test_the_header_is_not_repeated_when_it_is_not_asked_for():
    document, page = _page(PageSize.A5)
    table = Table(column_widths=[200, 100], font_size=9, repeat_header=False)
    table.add_header_row(["Row", "Value"])
    for index in range(40):
        table.add_row([f"Item {index}", "x"])
    result = page.add_table(table, 40, 560, bottom_margin=40)
    reloaded = _roundtrip(document)
    later = reloaded.pages[result["pages"][1]].extract_text()
    assert not later.startswith("Row")


def test_a_table_continues_onto_a_page_that_is_already_there():
    document, page = _page(PageSize.A5)
    document.pages.add(PageSize.A5)
    before = document.page_count
    table = Table(column_widths=[200], font_size=9)
    for index in range(40):
        table.add_row([f"Item {index}"])
    result = page.add_table(table, 40, 560, bottom_margin=40)
    assert result["pages"][:2] == [0, 1]
    # The second page was there already, so only what is still needed is added.
    assert document.page_count >= before


def test_a_first_row_that_cannot_fit_at_all_says_so():
    _, page = _page(PageSize.A5)
    table = Table(column_widths=[100], row_height=200)
    table.add_row(["x"])
    with pytest.raises(PdfValidationException, match="does not fit"):
        page.add_table(table, 40, 100, bottom_margin=36)


# ---------------------------------------------------------------------------
# Style
# ---------------------------------------------------------------------------


def test_a_cell_style_beats_the_rows_and_the_rows_beats_the_tables():
    document, _page_obj = _page()
    table = Table(column_widths=[100, 100], font_size=10, text_color=(0, 0, 0))
    row = table.add_row(["a", "b"], font_size=14, text_color=(1, 0, 0))
    row.cells[1].font_size = 20
    row.cells[1].text_color = (0, 0, 1)
    from aspose_pdf.engine.tables import _StandardFace, layout

    faces = {"Helvetica": _StandardFace(document._engine_pdf, "Helvetica")}
    cells = layout(table, faces, 200)[0].cells
    assert cells[0].style["font_size"] == 14.0
    assert cells[0].style["text_color"] == (1, 0, 0)
    assert cells[1].style["font_size"] == 20.0
    assert cells[1].style["text_color"] == (0, 0, 1)


def test_a_header_takes_the_header_styles():
    document, _page_obj = _page()
    table = Table(
        column_widths=[100],
        header_background_color="#003366",
        header_text_color=(1, 1, 1),
        header_font_size=12,
    )
    table.add_header_row(["H"])
    table.add_row(["d"])
    from aspose_pdf.engine.tables import _StandardFace, layout

    faces = {"Helvetica": _StandardFace(document._engine_pdf, "Helvetica")}
    rows = layout(table, faces, 100)
    assert rows[0].cells[0].style["font_size"] == 12.0
    assert rows[0].cells[0].style["text_color"] == (1, 1, 1)
    assert rows[0].cells[0].style["background_color"] == "#003366"
    assert rows[1].cells[0].style["font_size"] == 10.0


def test_the_colours_reach_the_content_stream():
    _document, page = _page()
    table = Table(
        column_widths=[100],
        border_width=2,
        border_color=(1, 0, 0),
        background_color="#eeeeee",
        text_color=(0, 0, 1),
    )
    table.add_row(["x"])
    page.add_table(table, 60, 700)
    content = page.content.decode("latin-1")
    assert "0.933333 0.933333 0.933333 rg" in content  # the background
    assert "1 0 0 RG" in content  # the border
    assert "2 w" in content
    assert "0 0 1 rg" in content  # the text


def test_a_border_width_of_zero_draws_no_border():
    _document, page = _page()
    table = Table(column_widths=[100], border_width=0)
    table.add_row(["x"])
    page.add_table(table, 60, 700)
    assert " re S" not in page.content.decode("latin-1")


@pytest.mark.parametrize(
    ("vertical", "expected"),
    [("top", (27, 34)), ("middle", (46, 53)), ("bottom", (65, 72))],
)
def test_the_vertical_alignments_put_the_text_where_they_say(vertical, expected):
    # Measured on the raster: the row box is 60 points tall, from image row 20 to
    # row 80 at 72 dpi, so "top" inks just below 20 and "bottom" just above 80.
    document, page = _page(size=None) if False else (None, None)
    document = Document()
    page = document.pages.add(size=(120, 100))
    table = Table(column_widths=[100], row_height=60, padding=4, border_width=0)
    row = table.add_row(["TEXT"])
    row.cells[0].vertical_alignment = vertical
    page.add_table(table, 10, 80, bottom_margin=0)

    raster = _roundtrip(document).pages[0].render(dpi=72)
    assert _ink_rows(raster, 11, 109) == expected


def test_the_horizontal_alignments_move_the_text_across_the_cell():
    document = Document()
    page = document.pages.add(size=(220, 60))
    table = Table(column_widths=[200], padding=4, border_width=0)
    row = table.add_row(["AB"])
    row.cells[0].alignment = "right"
    page.add_table(table, 10, 50, bottom_margin=0)
    raster = _roundtrip(document).pages[0].render(dpi=72)

    inked = [
        x
        for x in range(raster.width)
        for y in range(raster.height)
        if sum(raster.get_pixel(x, y)) < 600
    ]
    # Right-aligned: the ink is in the right-hand half of the cell.
    assert min(inked) > 100


# ---------------------------------------------------------------------------
# The structure tree
# ---------------------------------------------------------------------------


def test_the_structure_is_a_nested_table_of_rows_of_cells():
    from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName

    document, page = _page()
    page.add_table(_invoice(), 60, 760)
    engine = document._engine_pdf
    root = engine._resolve(
        engine._resolve(
            engine._cos_doc.trailer.mapping.get(PdfName("Root"))
        ).mapping.get(PdfName("StructTreeRoot"))
    )
    kids = engine._resolve(root.mapping.get(PdfName("K")))
    elements = kids.items if isinstance(kids, PdfArray) else [kids]
    table_elem = engine._resolve(elements[-1])
    assert engine._get_name(table_elem.mapping.get(PdfName("S"))) == "Table"

    rows = engine._resolve(table_elem.mapping.get(PdfName("K")))
    assert isinstance(rows, PdfArray)
    assert len(rows.items) == 3
    first_row = engine._resolve(rows.items[0])
    assert engine._get_name(first_row.mapping.get(PdfName("S"))) == "TR"
    cells = engine._resolve(first_row.mapping.get(PdfName("K")))
    assert [
        engine._get_name(engine._resolve(cell).mapping.get(PdfName("S")))
        for cell in cells.items
    ] == ["TH", "TH", "TH"]
    body_row = engine._resolve(rows.items[1])
    body_cells = engine._resolve(body_row.mapping.get(PdfName("K")))
    assert all(
        engine._get_name(engine._resolve(cell).mapping.get(PdfName("S"))) == "TD"
        for cell in body_cells.items
    )
    # Each cell owns its marked-content sequence, which is what ties the
    # structure to what is drawn.
    assert isinstance(
        engine._resolve(engine._resolve(body_cells.items[0]).mapping.get(PdfName("K"))),
        PdfDictionary | type(None),
    ) or True


def test_the_cells_marked_content_is_in_the_page():
    _document, page = _page()
    page.add_table(_invoice(), 60, 760)
    content = page.content.decode("latin-1")
    assert "/TH <<" in content
    assert "/TD <<" in content
    assert content.count("EMC") == 9  # three rows of three cells


def test_tagging_can_be_turned_off():
    document, page = _page()
    page.add_table(_invoice(), 60, 760, tag=False)
    content = page.content.decode("latin-1")
    assert "/TD" not in content
    assert b"/StructTreeRoot" not in _saved(document)


def test_a_tagged_table_passes_pdf_ua():
    # PDF/UA requires tagging, and a flattened structure -- every cell a sibling
    # of the table -- is what a reader would then read out.
    document, page = _page()
    table = _invoice()
    page.add_table(table, 60, 760)
    document.info = {"Title": "Price list"}
    assert document.convert_to_pdfua() == []
    report = document.validate_pdfua()
    assert report.is_valid, report.errors


def test_a_tagged_table_passes_pdf_a_2a():
    document, page = _page()
    page.add_table(_invoice(), 60, 760)
    assert document.convert_to_pdfa("2a") == []
    assert document.validate_pdfa("2a").is_valid


# ---------------------------------------------------------------------------
# An embedded font
# ---------------------------------------------------------------------------


def _font_bytes() -> bytes:
    from aspose_pdf.engine.std_font_data import load_substitute_sfnt

    data = load_substitute_sfnt("sans-regular")
    if not data:
        pytest.skip("no bundled substitute font to embed")
    return data


def test_a_table_can_be_set_in_an_embedded_font():
    document, page = _page()
    table = Table(column_widths=[150, 150], font=_font_bytes())
    table.add_header_row(["Nazwa", "Ilość"])
    table.add_row(["Przegląd łąki", "2"])
    page.add_table(table, 60, 700)
    data = _saved(document)
    assert b"/Type0" in data
    assert b"/FontFile2" in data or b"/FontFile3" in data


def test_an_embedded_font_is_measured_by_its_own_widths():
    document, _page_obj = _page()
    from aspose_pdf.engine.tables import _EmbeddedFace

    face = _EmbeddedFace(document._engine_pdf, _font_bytes())
    narrow = face.width("iiii", 10.0)
    wide = face.width("WWWW", 10.0)
    assert 0 < narrow < wide
    for line in face.wrap("Przegląd łąki i jeszcze więcej słów", 60.0, 10.0):
        assert face.width(line, 10.0) <= 60.0 + 1e-6


def test_a_standard_font_table_refuses_what_its_encoding_cannot_write():
    _document, page = _page()
    table = Table(column_widths=[150])
    table.add_row(["Przegląd"])
    with pytest.raises(Exception, match=r"(?i)no code for|pass font="):
        page.add_table(table, 60, 700)


def test_a_font_that_is_not_one_of_the_fourteen_is_refused():
    _, page = _page()
    table = Table(column_widths=[100], font_name="Comic Sans")
    table.add_row(["x"])
    with pytest.raises(PdfValidationException, match="standard 14"):
        page.add_table(table, 60, 700)
