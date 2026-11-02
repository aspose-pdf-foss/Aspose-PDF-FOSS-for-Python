"""Independent table-cell borders, inherited from table and row styles."""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Cell, Color, Document, Row, Table
from aspose_pdf.exceptions import PdfValidationException

_MIDPOINTS = {
    "top": (70, 30),
    "right": (120, 50),
    "bottom": (70, 70),
    "left": (20, 50),
}


def _save(document):
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _table(scope, **options):
    table = Table(
        column_widths=[100],
        row_height=40,
        border_width=0,
        **(options if scope == "table" else {}),
    )
    row = table.add_row([], **(options if scope == "row" else {}))
    row.add_cell("", **(options if scope == "cell" else {}))
    return table


@pytest.mark.parametrize("on_disk", [False, True])
@pytest.mark.parametrize("scope", ["table", "row", "cell"])
@pytest.mark.parametrize("side", _MIDPOINTS)
def test_one_visible_side_survives_round_trip(tmp_path, on_disk, scope, side):
    table = _table(scope, border_widths={side: 4}, border_colors={side: "#ff0000"})
    with Document() as document:
        page = document.pages.add(size=(160, 120))
        placement = page.add_table(table, 20, 90, bottom_margin=0)
        assert placement == {"pages": [0], "bottom": 50, "rows": 1}
        if on_disk:
            source = tmp_path / "border.pdf"
            document.save(source)
        else:
            source = _save(document)
        with Document(source) as reopened:
            raster = reopened.pages[0].render(dpi=72, antialias=False)
            for edge, (x, y) in _MIDPOINTS.items():
                assert raster.get_pixel(x, y) == (
                    (255, 0, 0) if edge == side else (255, 255, 255)
                )


def test_widths_and_colors_inherit_independently_and_none_hides_a_side():
    table = Table(
        column_widths=[100],
        row_height=40,
        border_width=4,
        border_widths={"top": 6, "left": 0},
        border_colors={"right": "#f00", "bottom": "#0f0"},
    )
    row = table.add_row([], border_widths={"bottom": 8}, border_colors={"top": "#00f"})
    row.add_cell("", border_widths={"right": 2}, border_colors={"bottom": None})
    with Document() as document:
        page = document.pages.add(size=(160, 120))
        page.add_table(table, 20, 90, bottom_margin=0)
        raster = page.render(dpi=72, antialias=False)
        assert raster.get_pixel(70, 28) == (0, 0, 255)
        assert raster.get_pixel(120, 50) == (255, 0, 0)
        assert raster.get_pixel(118, 50) == (255, 255, 255)
        assert raster.get_pixel(20, 50) == (255, 255, 255)
        assert raster.get_pixel(70, 70) == (255, 255, 255)


@pytest.mark.parametrize("scope", ["row", "cell"])
def test_nearer_uniform_styles_override_inherited_side_styles(scope):
    table = Table(
        column_widths=[100],
        row_height=40,
        border_width=4,
        border_widths={"top": 8, "bottom": 6},
        border_colors={"top": "#f00"},
    )
    options = {"border_width": 0, "border_color": "#00f", "border_widths": {"top": 2}}
    row = table.add_row([], **(options if scope == "row" else {}))
    row.add_cell("", **(options if scope == "cell" else {}))
    with Document() as document:
        page = document.pages.add(size=(160, 120))
        page.add_table(table, 20, 90, bottom_margin=0)
        raster = page.render(dpi=72, antialias=False)
        assert raster.get_pixel(70, 30) == (0, 0, 255)
        assert raster.get_pixel(70, 27) == (255, 255, 255)
        for side in ("right", "bottom", "left"):
            assert raster.get_pixel(*_MIDPOINTS[side]) == (255, 255, 255)


def test_spanning_cell_has_one_continuous_border():
    table = Table(column_widths=[50, 50], row_height=40, border_width=0)
    row = table.add_row([])
    row.add_cell("", column_span=2, border_widths={"bottom": 4})
    with Document() as document:
        page = document.pages.add(size=(160, 120))
        page.add_table(table, 20, 90, bottom_margin=0)
        raster = page.render(dpi=72, antialias=False)
        for x in (25, 70, 115):
            assert raster.get_pixel(x, 70) == (0, 0, 0)
        assert raster.get_pixel(70, 50) == (255, 255, 255)


@pytest.mark.parametrize("hide_both", [False, True])
def test_shared_edge_is_visible_until_both_adjoining_sides_are_hidden(hide_both):
    table = Table(column_widths=[50, 50], row_height=40, border_width=2)
    table.add_row(
        [
            Cell(border_widths={"right": 0}),
            Cell(border_widths={"left": 0} if hide_both else None),
        ]
    )
    with Document() as document:
        page = document.pages.add(size=(160, 120))
        page.add_table(table, 20, 90, bottom_margin=0)
        raster = page.render(dpi=72, antialias=False)
        assert raster.get_pixel(70, 50) == ((255, 255, 255) if hide_both else (0, 0, 0))


@pytest.mark.parametrize(
    "color,operator",
    [
        (0.5, b"0.5 G"),
        ((0, 1, 0, 0), b"0 1 0 0 K"),
        (Color.rgb(0, 0, 1), b"0 0 1 RG"),
    ],
)
def test_side_colors_accept_standard_drawing_color_inputs(color, operator):
    table = _table("cell", border_widths={"bottom": 2}, border_colors={"bottom": color})
    with Document() as document:
        page = document.pages.add(size=(160, 120))
        page.add_table(table, 20, 90, bottom_margin=0)
        assert operator in page.content


def test_header_border_is_repeated_on_continuation_pages():
    table = Table(column_widths=[100], row_height=30, border_width=0)
    table.add_header_row(
        ["Header"], border_widths={"bottom": 4}, border_colors={"bottom": "#00f"}
    )
    for index in range(4):
        table.add_row([f"Body {index}"])
    with Document() as document:
        page = document.pages.add(size=(160, 120))
        placement = page.add_table(table, 20, 100, bottom_margin=10)
        assert placement["pages"] == [0, 1]
        with Document(_save(document)) as reopened:
            for page in reopened.pages:
                assert "Header" in page.extract_text()
                raster = page.render(dpi=72, antialias=False)
                assert raster.get_pixel(70, 50) == (0, 0, 255)
                assert raster.get_pixel(20, 40) == (255, 255, 255)


@pytest.mark.parametrize("factory", [Table, Row, Cell])
@pytest.mark.parametrize(
    "value", ["bottom", [1, 2, 3, 4], {"Bottom": 1}, {"diagonal": 1}]
)
def test_side_mappings_require_known_side_names(factory, value):
    for name in ("border_widths", "border_colors"):
        with pytest.raises(PdfValidationException, match=name):
            factory(**{name: value})


@pytest.mark.parametrize("factory", [Table, Row, Cell])
@pytest.mark.parametrize(
    "width", [-1, True, None, "bad", float("nan"), float("inf"), 10**1000]
)
def test_invalid_side_widths_are_rejected(factory, width):
    with pytest.raises(PdfValidationException, match="border_widths"):
        factory(border_widths={"top": width})


@pytest.mark.parametrize("factory", [Table, Row, Cell])
@pytest.mark.parametrize("color", ["bad color", (1, 2), (-1,), (float("nan"),), True])
def test_invalid_side_colors_are_rejected(factory, color):
    with pytest.raises(PdfValidationException, match="border_colors"):
        factory(border_colors={"top": color})


@pytest.mark.parametrize("scope", ["table", "row", "cell"])
@pytest.mark.parametrize(
    "name,value",
    [("border_widths", {"bottom": -1}), ("border_colors", {"bottom": "bad color"})],
)
def test_edits_are_revalidated_before_any_page_is_drawn(scope, name, value):
    table = Table(column_widths=[100], row_height=40)
    for index in range(4):
        row = table.add_row([str(index)])
    target = {"table": table, "row": row, "cell": row.cells[0]}[scope]
    setattr(target, name, value)
    with Document() as document:
        page = document.pages.add(size=(160, 120))
        page.add_text("Original", 20, 100)
        before = page.content
        with pytest.raises(PdfValidationException, match=name):
            page.add_table(table, 20, 90, bottom_margin=0)
        assert page.content == before
        assert len(document.pages) == 1


def test_side_mappings_are_copied_and_remain_editable():
    widths = {"top": 2}
    colors = {"top": [1, 0, 0]}
    cell = Cell(border_widths=widths, border_colors=colors)
    widths["top"] = -1
    colors["top"][0] = 0
    assert cell.border_widths == {"top": 2}
    assert cell.border_colors == {"top": (1, 0, 0)}
    cell.border_widths["bottom"] = 0


def test_uniform_border_keeps_the_existing_rectangle_path():
    with Document() as document:
        page = document.pages.add(size=(160, 120))
        table = Table(column_widths=[100], row_height=40, border_width=2)
        table.add_row([""])
        page.add_table(table, 20, 90, bottom_margin=0)
        assert b"20 50 100 40 re S" in page.content
