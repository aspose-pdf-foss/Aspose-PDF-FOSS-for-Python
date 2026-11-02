"""Content-based column sizing uses rendered font advances and available space."""

from __future__ import annotations

import io
import math
from itertools import pairwise

import pytest

from aspose_pdf import Cell, Document, Table, URIAction
from aspose_pdf.engine.tables import _EmbeddedFace, _StandardFace, column_edges, layout
from aspose_pdf.exceptions import PdfValidationException


def _saved(document):
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _faces(document, *names):
    return {
        name: _StandardFace(document._engine_pdf, name)
        for name in names or ("Helvetica",)
    }


def _widths(table, document, available=400, faces=None):
    edges = column_edges(table, available, faces or _faces(document))
    return [right - left for left, right in pairwise(edges)]


@pytest.fixture
def document():
    with Document() as doc:
        doc.pages.add(size=(300, 200))
        yield doc


def test_default_equal_columns_and_explicit_widths_are_preserved(document):
    table = Table(width=200)
    table.add_row(["MMMM", "1"])
    assert _widths(table, document) == [100, 100]
    table.auto_column_widths = True
    table.column_widths = [70, 130]
    assert _widths(table, document) == [70, 130]


@pytest.mark.parametrize("on_disk", [False, True])
def test_auto_widths_use_glyph_advances_and_survive_save(document, tmp_path, on_disk):
    table = Table(width=38.88, padding=0, auto_column_widths=True)
    table.add_row(
        [
            Cell("MMMM", link=URIAction("https://example.test/description")),
            Cell("1", link=URIAction("https://example.test/quantity")),
        ]
    )
    document.pages[0].add_table(table, 20, 160)
    assert table.column_widths is None
    if on_disk:
        source = tmp_path / "auto.pdf"
        document.save(source)
    else:
        source = _saved(document)
    with Document(source) as reopened:
        first, second = reopened.extract_links()
        assert first.rect[0:3:2] == pytest.approx((20, 53.32))
        assert second.rect[0:3:2] == pytest.approx((53.32, 58.88))
        assert "MMMM" in reopened.pages[0].extract_text()
        assert "1" in reopened.pages[0].extract_text()


def test_same_character_count_does_not_imply_equal_widths(document):
    table = Table(width=200, padding=0, auto_column_widths=True)
    table.add_row(["iiii", "WWWW"])
    narrow, wide = _widths(table, document)
    assert wide > narrow * 4
    assert narrow + wide == pytest.approx(200)


@pytest.mark.parametrize("scope", ["table", "row", "cell", "header"])
def test_font_sizes_and_padding_use_effective_cell_styles(document, scope):
    table = Table(width=100, padding=0, auto_column_widths=True)
    row = table.add_row(["M", "M"], header=scope == "header")
    if scope == "table":
        table.font_size = 20
        row.cells[1].font_size = 10
    elif scope == "row":
        row.font_size = 20
        row.cells[1].font_size = 10
    elif scope == "cell":
        row.cells[0].font_size = 20
    else:
        table.header_font_size = 20
        row.cells[1].font_size = 10
    assert _widths(table, document) == pytest.approx([200 / 3, 100 / 3])
    row.cells[1].padding = 10
    first, second = _widths(table, document)
    assert second > first


def test_effective_font_names_are_measured(document):
    table = Table(width=100, padding=0, auto_column_widths=True)
    table.add_row([Cell("iiii", font_name="Courier"), Cell("iiii")])
    widths = _widths(table, document, faces=_faces(document, "Helvetica", "Courier"))
    assert widths[0] > widths[1] * 2


@pytest.mark.parametrize("text", ["MMMM\nM", "MMMM\r\nM", "MMMM\rM"])
def test_explicit_line_breaks_use_the_widest_line(document, text):
    table = Table(width=100, padding=0, auto_column_widths=True)
    table.add_row([text, "MMMM"])
    assert _widths(table, document) == pytest.approx([50, 50])


def test_whitespace_is_measured_as_the_table_wrapper_renders_it(document):
    table = Table(width=100, padding=0, auto_column_widths=True)
    table.add_row(["A   B\tC", "A B C"])
    assert _widths(table, document) == pytest.approx([50, 50])


def test_long_content_shrinks_and_wraps_within_the_available_width(document):
    table = Table(width=110, padding=4, auto_column_widths=True)
    table.add_row(["A description with several words and a verylongidentifier", "42"])
    faces = _faces(document)
    row = layout(table, faces, 400)[0]
    assert row.cells[0].width > row.cells[1].width
    assert sum(cell.width for cell in row.cells) == pytest.approx(110)
    assert len(row.cells[0].lines) > 1
    for cell in row.cells:
        for line in cell.lines:
            assert faces["Helvetica"].width(line, 10) <= cell.width - 8 + 1e-6


def test_spanning_cells_affect_their_columns_without_changing_relative_proportions(
    document,
):
    table = Table(width=200, padding=0, auto_column_widths=True)
    table.add_row(["MMMM", "M", "M"])
    table.add_row([Cell("M" * 20, column_span=2), "M"])
    first, second, third = _widths(table, document)
    assert first + second > 180
    assert first > second * 3
    assert third < 20


def test_overlapping_span_measurements_do_not_depend_on_row_order(document):
    rows = [
        [Cell("A long merged heading", column_span=2), "A"],
        ["A", Cell("Another merged heading", column_span=2)],
        ["Name", "Quantity", "Price"],
    ]
    tables = [
        Table(rows=items, width=250, auto_column_widths=True)
        for items in (rows, list(reversed(rows)))
    ]
    assert _widths(tables[0], document) == pytest.approx(_widths(tables[1], document))


def test_overlapping_spans_fit_when_their_minimum_constraints_are_feasible(document):
    table = Table(width=60, columns=3, padding=0, auto_column_widths=True)
    table.add_row([Cell("M", column_span=2, font_size=60), ""])
    table.add_row(["", Cell("M", column_span=2, font_size=60)])
    first, middle, last = _widths(table, document)
    assert first + middle >= 49.98 - 1e-8
    assert middle + last >= 49.98 - 1e-8
    assert first + middle + last == pytest.approx(60)


def test_rounding_at_the_minimum_width_does_not_divide_by_zero(document):
    table = Table(width=8.33 - 1e-9, padding=0, auto_column_widths=True)
    table.add_row(["M"])
    assert _widths(table, document) == pytest.approx([8.33 - 1e-9])


def test_auto_widths_and_link_rectangles_stay_consistent_on_continuation_pages(
    document,
):
    page = document.pages[0]
    page.size = (160, 120)
    table = Table(width=140, row_height=30, auto_column_widths=True)
    table.add_header_row(
        [
            Cell("Description", link=URIAction("https://example.test/header")),
            Cell("Qty", link=URIAction("https://example.test/quantity")),
        ]
    )
    for index in range(4):
        table.add_row([f"Item {index}", str(index)])
    page.add_table(table, 10, 100, bottom_margin=10)
    with Document(_saved(document)) as reopened:
        assert len(reopened.pages) == 2
        first = reopened.pages[0].extract_links()
        repeated = reopened.pages[1].extract_links()
        assert [link.rect for link in first] == [link.rect for link in repeated]
        assert first[0].rect[2] - first[0].rect[0] > first[1].rect[2] - first[1].rect[0]
        assert first[-1].rect[2] == 150


def test_edited_auto_width_flag_is_revalidated_before_drawing(document):
    table = Table(auto_column_widths=True)
    table.add_row(["Text"])
    table.auto_column_widths = "yes"
    before = _saved(document)
    with pytest.raises(PdfValidationException, match="auto_column_widths"):
        document.pages[0].add_table(table, 20, 160)
    assert _saved(document) == before


@pytest.mark.parametrize("repeat", [False, True])
def test_overheight_wrapped_rows_are_rejected_without_creating_endless_pages(
    document, repeat
):
    page = document.pages[0]
    page.size = (160, 120)
    table = Table(width=60, auto_column_widths=True, repeat_header=repeat)
    table.add_header_row(["Item", "Qty"])
    table.add_row(["M " * 100, "1"])
    with pytest.raises(PdfValidationException, match="row does not fit"):
        page.add_table(table, 20, 100, bottom_margin=10)
    assert len(document.pages) == 1
    assert page.content == b""


def test_a_tall_row_can_continue_onto_an_existing_larger_page(document):
    page = document.pages[0]
    page.size = (160, 120)
    document.pages.add(size=(160, 300))
    table = Table(width=100, auto_column_widths=True)
    table.add_header_row(["Header"], height=30)
    table.add_row(["Body"], height=180)
    result = page.add_table(table, 20, 100, bottom_margin=10)
    assert result["pages"] == [0, 1]
    assert "Body" in document.pages[1].extract_text()


def test_empty_and_missing_columns_keep_positive_widths(document):
    table = Table(columns=3, width=90, padding=0, auto_column_widths=True)
    table.add_row(["", ""])
    widths = _widths(table, document)
    assert widths == pytest.approx([30, 30, 30])
    assert all(width > 0 for width in widths)


@pytest.mark.parametrize("value", [1, 0, None, "yes", []])
def test_auto_width_flag_requires_a_boolean(value):
    with pytest.raises(PdfValidationException, match="auto_column_widths"):
        Table(auto_column_widths=value)


@pytest.mark.parametrize("width", [1, float("nan"), float("inf"), -1, 0])
def test_impossible_or_invalid_width_fails_before_drawing(document, width):
    before = _saved(document)
    table = Table(auto_column_widths=True)
    table.add_row(["Text", "42"])
    table.width = width
    with pytest.raises(PdfValidationException):
        document.pages[0].add_table(table, 20, 160)
    assert _saved(document) == before


def test_measured_dimensions_must_remain_finite(document):
    table = Table(auto_column_widths=True, font_size=1e308)
    table.add_row(["MMMM", "1"])
    with pytest.raises(PdfValidationException):
        document.pages[0].add_table(table, 20, 160)


def test_page_room_and_reusing_a_table_do_not_freeze_the_column_widths(document):
    table = Table(auto_column_widths=True)
    table.add_row(
        [
            Cell("Description", link=URIAction("https://example.test/description")),
            Cell("1", link=URIAction("https://example.test/quantity")),
        ]
    )
    page = document.pages[0]
    page.add_table(table, 20, 160)
    first = page.extract_links()
    page.add_table(table, 100, 100)
    second = page.extract_links()[2:]
    assert first[-1].rect[2] == second[-1].rect[2] == 300
    assert first[0].rect[2] - first[0].rect[0] > second[0].rect[2] - second[0].rect[0]
    assert table.column_widths is None


def test_embedded_fonts_measure_new_unicode_glyphs(document):
    from aspose_pdf.engine.std_font_data import load_substitute_sfnt

    font = load_substitute_sfnt("sans-regular")
    assert font
    table = Table(width=200, font=font, padding=0, auto_column_widths=True)
    table.add_row(["Żółw", "i"])
    face = _EmbeddedFace(document._engine_pdf, font)
    widths = _widths(table, document, faces={"Helvetica": face})
    assert all(math.isfinite(width) and width > 0 for width in widths)
    assert widths[0] > widths[1] * 2
    document.pages[0].add_table(table, 20, 160)
    with Document(_saved(document)) as reopened:
        assert "Żółw" in reopened.pages[0].extract_text()
