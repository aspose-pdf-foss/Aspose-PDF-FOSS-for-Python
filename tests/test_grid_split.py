"""Grid tiles keep displayed page geometry and editable vector content."""

from __future__ import annotations

import io
from itertools import repeat

import pytest

from aspose_pdf import (
    Document,
    FitDestination,
    NumberingStyle,
    OutlineItem,
    PageLabel,
    PdfLoadLimits,
    PdfResourceLimitException,
    RasterizedPage,
)
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfValidationException,
    UnsupportedFeatureException,
)
from aspose_pdf.pages import Page


def _bytes(document):
    with io.BytesIO() as stream:
        document.save(stream)
        return stream.getvalue()


def _grid_page(document, *, offset=False, width=120, height=180):
    x, y = (-20, 10) if offset else (0, 0)
    page = document.pages.add(((x - 10, y - 10, x + width + 10, y + height + 10), b""))
    page.crop_box = (x, y, x + width, y + height)
    for index, color in enumerate(("#f00", "#0f0", "#00f", "#ff0", "#f0f", "#0ff")):
        row, column = divmod(index, 3)
        page.draw_rectangle(
            x + column * width / 3,
            y + row * height / 2,
            width / 3,
            height / 2,
            fill_color=color,
            stroke_color=None,
        )
    return page


def _region(raster, left, top, right, bottom):
    return (
        right - left,
        bottom - top,
        b"".join(
            raster.pixels[
                (row * raster.width + left) * 3 : (row * raster.width + right) * 3
            ]
            for row in range(top, bottom)
        ),
    )


def _regions(raster, rows, columns, gutter=0, order="row"):
    width = (raster.width - gutter * (columns - 1)) // columns
    height = (raster.height - gutter * (rows - 1)) // rows
    positions = [(row, column) for row in range(rows) for column in range(columns)]
    if order == "column":
        positions.sort(key=lambda pair: (pair[1], pair[0]))
    return [
        _region(
            raster,
            column * (width + gutter),
            row * (height + gutter),
            column * (width + gutter) + width,
            row * (height + gutter) + height,
        )
        for row, column in positions
    ]


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("order", ["row", "column"])
@pytest.mark.parametrize("offset", [False, True])
def test_two_by_three_tiles_match_displayed_regions_after_round_trip(
    rotation, order, offset
):
    with Document() as source:
        page = _grid_page(source, offset=offset)
        page.rotation = rotation
        before = _bytes(source)
        expected = _regions(page.render(antialias=False), 2, 3, order=order)
        with source.split_grid(2, 3, order=order) as result:
            assert result.page_count == 6 and result.load_limits is source.load_limits
            for tile, region in zip(result.pages, expected, strict=True):
                raster = tile.render(antialias=False)
                assert (raster.width, raster.height, raster.pixels) == region
                assert tile.rotation == rotation
                assert (
                    tile.media_box
                    == tile.crop_box
                    == tile.bleed_box
                    == tile.trim_box
                    == tile.art_box
                )
            with Document(_bytes(result)) as reopened:
                assert reopened.validate()
                for tile, region in zip(reopened.pages, expected, strict=True):
                    raster = tile.render(antialias=False)
                    assert (raster.width, raster.height, raster.pixels) == region
        assert _bytes(source) == before


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("order", ["row", "column"])
def test_equal_gaps_leave_uniform_cells_in_displayed_coordinates(rotation, order):
    with Document() as source:
        page = _grid_page(source, offset=True, width=128, height=122)
        page.rotation = rotation
        expected = _regions(page.render(antialias=False), 2, 3, gutter=4, order=order)
        with source.split_grid(2, 3, order=order, gutter=4) as result:
            for tile, region in zip(result.pages, expected, strict=True):
                raster = tile.render(antialias=False)
                assert (raster.width, raster.height, raster.pixels) == region


@pytest.mark.parametrize("rows,columns", [(1, 1), (1, 3), (2, 1)])
def test_single_row_column_and_one_cell_grids(rows, columns):
    with Document() as source:
        page = _grid_page(source)
        expected = _regions(page.render(antialias=False), rows, columns)
        with source.split_grid(rows, columns) as result:
            assert result.page_count == rows * columns
            for tile, region in zip(result.pages, expected, strict=True):
                raster = tile.render(antialias=False)
                assert (raster.width, raster.height, raster.pixels) == region


def test_selection_keeps_covers_page_order_labels_destinations_and_editable_fields(
    tmp_path,
):
    with Document() as source:
        source.pages.add(size=(20, 20)).add_text("Cover", 1, 10, font_size=2)
        _grid_page(source)
        _grid_page(source)
        source.pages.add(size=(20, 20)).add_text("Back", 1, 10, font_size=2)
        source.form.add_text_field("cover", 0, (1, 1, 19, 9), value="Keep")
        source.page_labels[0] = PageLabel(NumberingStyle.NUMERALS_ROMAN_LOWERCASE)
        source.info["Title"] = "Grid source"
        source.add_attachment("guide.txt", b"Original attachment")
        source.outlines.add(OutlineItem("Cover", 0))
        source.outlines.add(OutlineItem("First grid", destination=FitDestination(1)))
        source.outlines.add(OutlineItem("Second grid", 2))
        source.outlines.add(OutlineItem("Back", 3))
        source.pages[1].add_link((1, 1, 5, 5), FitDestination(2))
        original = [
            (item.title, item.page_index, item.destination) for item in source.outlines
        ]
        with source.split_grid(2, 3, pages=(index for index in (2, 1, 2))) as result:
            assert result.page_count == 14
            assert (
                "Cover" in result.pages[0].extract_text()
                and "Back" in result.pages[-1].extract_text()
            )
            assert result.info["Title"] == "Grid source"
            assert result.attachments["guide.txt"] == b"Original attachment"
            assert [page.label for page in result.pages] == [
                "i",
                *["ii"] * 6,
                *["iii"] * 6,
                "iv",
            ]
            assert [(item.title, item.page_index) for item in result.outlines] == [
                ("Cover", 0),
                ("First grid", 1),
                ("Second grid", 7),
                ("Back", 13),
            ]
            assert (
                result.form["cover"].value == "Keep" and result.validate_links() == []
            )
            for page in result.pages[1:7]:
                assert page.annotations[0].get_property("Dest") == FitDestination(7)
            path = tmp_path / "tiles.pdf"
            result.save(path)
            with Document(path) as reopened:
                assert reopened.validate() and reopened.validate_links() == []
                assert [
                    (item.title, item.page_index) for item in reopened.outlines
                ] == [("Cover", 0), ("First grid", 1), ("Second grid", 7), ("Back", 13)]
                assert reopened.form["cover"].value == "Keep"
        assert [
            (item.title, item.page_index, item.destination) for item in source.outlines
        ] == original


def test_empty_selection_returns_an_independent_copy_with_interactive_fields():
    with Document() as source:
        page = _grid_page(source)
        source.form.add_text_field("keep", page, (1, 1, 30, 15), value="Original")
        with source.split_grid(3, 4, pages=[]) as result:
            assert result.page_count == 1 and result.pages[0].crop_box == page.crop_box
            result.form["keep"].value = "Changed"
            assert source.form["keep"].value == "Original"


def test_content_stays_vector_searchable_and_hidden_outside_each_tile(monkeypatch):
    with Document() as source:
        page = source.pages.add(size=(120, 80))
        for x, y, text in (
            (5, 60, "TL"),
            (65, 60, "TR"),
            (5, 20, "BL"),
            (65, 20, "BR"),
        ):
            page.add_text(text, x, y, font_size=8)
        with monkeypatch.context() as patch:
            patch.setattr(
                Page, "render", lambda *a, **k: pytest.fail("No rasterization")
            )
            result = source.split_grid()
    with result:
        assert result.file_name is None
        for page, expected in zip(result.pages, ("TL", "TR", "BL", "BR"), strict=True):
            assert all(word in page.extract_text() for word in ("TL", "TR", "BL", "BR"))
            assert page.extract_text(clip=page.crop_box).strip() == expected
            assert "figure" not in {block.kind for block in page.extract_blocks()}


def test_annotation_threads_are_independent_on_every_tile():
    with Document() as source:
        page = _grid_page(source)
        note = page.annotations.add_text((1, 1, 5, 5), "Note")
        note.reply("Reply")
        note.add_popup((5, 1, 15, 8), is_open=True)
        with source.split_grid(2, 3) as result:
            result.pages[4].annotations[0].contents = "Tile four"
            for index, page in enumerate(result.pages):
                annotations = page.annotations
                text = "Tile four" if index == 4 else "Note"
                assert len(annotations) == 3
                assert annotations[0].contents == text
                assert annotations[1].in_reply_to.contents == text
                assert annotations[0].popup.parent_annotation.contents == text
            assert source.pages[0].annotations[0].contents == "Note"
            with Document(_bytes(result)) as reopened:
                assert (
                    reopened.pages[4].annotations[1].in_reply_to.contents == "Tile four"
                )
                assert reopened.pages[5].annotations[0].contents == "Note"


def test_selected_widgets_are_flattened_and_the_source_remains_interactive():
    with Document() as source:
        page = source.pages.add(size=(120, 80))
        source.form.add_text_field("name", page, (5, 20, 115, 60), value="Alice")
        reference = _regions(page.render(antialias=False), 2, 3)
        with source.split_grid(2, 3) as result:
            assert len(result.form) == 0
            for tile, expected in zip(result.pages, reference, strict=True):
                raster = tile.render(antialias=False)
                assert (raster.width, raster.height, raster.pixels) == expected
        assert source.form["name"].value == "Alice"
        with pytest.raises(UnsupportedFeatureException, match="flatten_fields"):
            source.split_grid(flatten_fields=False)
        page.annotations[0].set_property("F", 2)
        with pytest.raises(UnsupportedFeatureException, match="could not be flattened"):
            source.split_grid()
        assert source.form["name"].value == "Alice"


def test_image_resources_and_layers_survive_on_every_tile():
    with Document() as source:
        page = source.pages.add(size=(30, 20))
        layer = source.layers.add("Artwork")
        with page.layer(layer):
            page.add_image(
                RasterizedPage(
                    3, 2, bytes.fromhex("ff000000ff000000ffffff00ff00ff00ffff")
                ).to_png(),
                0,
                0,
                width=30,
                height=20,
            )
        expected = _regions(page.render(antialias=False), 2, 3)
        result = source.split_grid(2, 3)
    with result:
        for page, region in zip(result.pages, expected, strict=True):
            raster = page.render(antialias=False)
            assert (raster.width, raster.height, raster.pixels) == region
        result.layers["Artwork"].visible = False
        assert all(
            page.render(antialias=False).get_pixel(5, 5) == (255, 255, 255)
            for page in result.pages
        )
        result.layers["Artwork"].visible = True
        with Document(_bytes(result)) as reopened:
            for page, region in zip(reopened.pages, expected, strict=True):
                raster = page.render(antialias=False)
                assert (raster.width, raster.height, raster.pixels) == region


@pytest.mark.parametrize(
    "options",
    [
        {"rows": 0},
        {"rows": -1},
        {"rows": True},
        {"rows": 1.5},
        {"rows": "2"},
        {"columns": 0},
        {"columns": False},
        {"columns": 2.0},
        {"columns": None},
        {"order": "other"},
        {"order": 1},
        {"gutter": -1},
        {"gutter": True},
        {"gutter": float("nan")},
        {"gutter": float("inf")},
        {"gutter": "1"},
        {"flatten_fields": 1},
        {"pages": 0},
        {"pages": "0"},
        {"pages": [True]},
        {"pages": [-1]},
        {"pages": [0, 5]},
    ],
)
@pytest.mark.parametrize("empty", [False, True])
def test_invalid_options_fail_before_creating_output(monkeypatch, options, empty):
    from aspose_pdf.engine import grid_split

    with Document() as source:
        if not empty:
            _grid_page(source)
        monkeypatch.setattr(
            grid_split, "_split_pages", lambda *a, **k: pytest.fail("Preflight first")
        )
        with pytest.raises(PdfValidationException):
            source.split_grid(**options)


def test_empty_document_and_disposal():
    with Document() as source:
        with source.split_grid() as result:
            assert result.page_count == 0
    with pytest.raises(AsposePdfException, match="disposed"):
        source.split_grid()


def test_all_geometry_and_numeric_precision_are_checked_before_import(monkeypatch):
    from aspose_pdf.engine import grid_split

    with Document() as source:
        _grid_page(source)
        source.pages.add(size=(2, 2))
        monkeypatch.setattr(
            grid_split,
            "_split_pages",
            lambda *a, **k: pytest.fail("Preflight all geometry"),
        )
        with pytest.raises(PdfValidationException, match="no room"):
            source.split_grid(gutter=3)
    with Document() as source:
        source.pages.add(size=(0.000001, 1))
        with pytest.raises(PdfValidationException, match="precision"):
            source.split_grid(1, 3)


def test_fractional_cell_boxes_match_saved_geometry():
    with Document() as source:
        source.pages.add(size=(10, 10))
        with source.split_grid(3, 3) as result:
            with Document(_bytes(result)) as reopened:
                assert [page.media_box for page in result.pages] == [
                    page.media_box for page in reopened.pages
                ]


@pytest.mark.parametrize(
    "limits,options,limit",
    [
        (PdfLoadLimits(max_pages=3), {}, "max_pages"),
        (PdfLoadLimits(max_container_items=3), {}, "max_container_items"),
        (PdfLoadLimits(max_objects=5), {}, "max_objects"),
        (PdfLoadLimits(), {"rows": 10**100}, "max_pages"),
    ],
)
def test_output_budgets_are_checked_before_geometry_and_import(
    monkeypatch, limits, options, limit
):
    from aspose_pdf.engine import grid_split

    with Document() as source:
        _grid_page(source)
        source._load_limits = limits
        monkeypatch.setattr(
            grid_split, "_cells", lambda *a, **k: pytest.fail("Budget before cells")
        )
        monkeypatch.setattr(
            grid_split,
            "_split_pages",
            lambda *a, **k: pytest.fail("Budget before import"),
        )
        with pytest.raises(PdfResourceLimitException, match=limit):
            source.split_grid(**options)


def test_unbounded_selection_and_copied_content_are_bounded(monkeypatch):
    from aspose_pdf.engine import spread_split

    with Document() as source:
        _grid_page(source)
        source._load_limits = PdfLoadLimits(max_container_items=3)
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            source.split_grid(pages=repeat(0))
        size = len(source._engine_pdf.get_page_content(0))
        source._load_limits = PdfLoadLimits(max_total_decoded_bytes=2 * size - 1)
        monkeypatch.setattr(
            spread_split,
            "_copy_annotations",
            lambda *a: pytest.fail("Bound content before copying"),
        )
        with pytest.raises(PdfResourceLimitException, match="max_total_decoded_bytes"):
            source.split_grid()


@pytest.mark.parametrize("kind", ["bytes", "file", "streaming"])
@pytest.mark.parametrize("encrypted", [False, True])
def test_loaded_sources_are_independent_and_streaming_caches_stay_lazy(
    tmp_path, kind, encrypted
):
    path = tmp_path / "source.pdf"
    with Document() as source:
        _grid_page(source)
        _grid_page(source, offset=True).rotation = 90
        source.info["Title"] = "Original"
        source.add_attachment("original.txt", b"Keep")
        if encrypted:
            source.encrypt("user", "owner")
        source.save(path)
    original = path.read_bytes()
    opener = Document.open_streaming if kind == "streaming" else Document
    with opener(
        original if kind == "bytes" else path, password="user" if encrypted else None
    ) as source:
        protected = source.is_encrypted
        result = source.split_grid(2, 3, pages=[1])
        assert source.page_count == 2 and source.is_encrypted == protected
        if kind == "streaming":
            assert (
                source._engine_pdf.page_contents == []
                and source._engine_pdf._extracted_text is None
            )
    with result:
        assert result.page_count == 7 and not result.is_encrypted
        assert (
            result.info["Title"] == "Original"
            and result.attachments["original.txt"] == b"Keep"
        )
        with Document(_bytes(result)) as reopened:
            assert reopened.validate() and reopened.page_count == 7
    assert path.read_bytes() == original


@pytest.mark.parametrize("interrupt", [False, True])
def test_late_copy_failure_discards_partial_output_and_preserves_source(
    monkeypatch, interrupt
):
    from aspose_pdf.engine import spread_split

    with Document() as source:
        _grid_page(source)
        before = _bytes(source)
        created, calls = [], []
        original_import = spread_split._import_source
        original_copy = spread_split._copy_annotations

        def remember(result, *args, **kwargs):
            created.append(result)
            return original_import(result, *args, **kwargs)

        def fail(*args):
            calls.append(True)
            if len(calls) == 3:
                if interrupt:
                    raise KeyboardInterrupt()
                raise PdfValidationException("Later tile failed")
            return original_copy(*args)

        monkeypatch.setattr(spread_split, "_import_source", remember)
        monkeypatch.setattr(spread_split, "_copy_annotations", fail)
        with pytest.raises(KeyboardInterrupt if interrupt else PdfValidationException):
            source.split_grid(2, 3)
        assert len(created) == 1 and created[0]._disposed
        assert not source._engine_pdf._disposed and _bytes(source) == before
