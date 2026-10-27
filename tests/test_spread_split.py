"""Visible PDF spreads become independent, cropped pages without rasterization."""

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
    URIAction,
)
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfValidationException,
    UnsupportedFeatureException,
)


def _bytes(document):
    with io.BytesIO() as output:
        document.save(output)
        return output.getvalue()


def _spread(document, *, offset=False):
    page = (
        document.pages.add(((-20, 0, 80, 50), b""))
        if offset
        else document.pages.add(size=(40, 20))
    )
    x, y, width, height = (-10, 5, 80, 40) if offset else (0, 0, 40, 20)
    for dx, dy, color in (
        (0, 0, "#f00"),
        (0.5, 0, "#0f0"),
        (0, 0.5, "#00f"),
        (0.5, 0.5, "#ff0"),
    ):
        page.draw_rectangle(
            x + dx * width,
            y + dy * height,
            width / 2,
            height / 2,
            fill_color=color,
            stroke_color=None,
        )
    if offset:
        page.crop_box = (x, y, x + width, y + height)
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


@pytest.mark.parametrize("layout", ["horizontal", "vertical"])
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("reverse", [False, True])
def test_visible_halves_match_full_render_for_rotated_offset_crops(
    layout, rotation, reverse
):
    with Document() as source:
        page = _spread(source, offset=True)
        page.rotation = rotation
        before = _bytes(source)
        raster = page.render(antialias=False)
        if layout == "horizontal":
            expected = [
                _region(raster, 0, 0, raster.width // 2, raster.height),
                _region(raster, raster.width // 2, 0, raster.width, raster.height),
            ]
        else:
            expected = [
                _region(raster, 0, 0, raster.width, raster.height // 2),
                _region(raster, 0, raster.height // 2, raster.width, raster.height),
            ]
        if reverse:
            expected.reverse()
        with source.split_spreads(layout=layout, reverse=reverse) as result:
            assert result.page_count == 2 and result.load_limits is source.load_limits
            for actual_page, pixels in zip(result.pages, expected, strict=True):
                actual = actual_page.render(antialias=False)
                assert (actual.width, actual.height, actual.pixels) == pixels
                assert actual_page.rotation == rotation
                assert (
                    actual_page.media_box
                    == actual_page.crop_box
                    == actual_page.trim_box
                    == actual_page.bleed_box
                    == actual_page.art_box
                )
            with Document(_bytes(result)) as reopened:
                assert reopened.validate()
                for actual_page, pixels in zip(reopened.pages, expected, strict=True):
                    actual = actual_page.render(antialias=False)
                    assert (actual.width, actual.height, actual.pixels) == pixels
        assert _bytes(source) == before


@pytest.mark.parametrize(
    "layout,ratio,gutter,regions",
    [
        ("horizontal", 0.3, 4, [(0, 0, 10, 20), (14, 0, 40, 20)]),
        ("vertical", 0.4, 2, [(0, 0, 40, 7), (0, 9, 40, 20)]),
    ],
)
def test_asymmetric_split_and_centered_gutter(layout, ratio, gutter, regions):
    with Document() as source:
        raster = _spread(source).render(antialias=False)
        with source.split_spreads(
            layout=layout, split_ratio=ratio, gutter=gutter
        ) as result:
            for page, region in zip(result.pages, regions, strict=True):
                actual = page.render(antialias=False)
                assert (actual.width, actual.height, actual.pixels) == _region(
                    raster, *region
                )


def test_selected_spreads_stay_in_source_order_with_unselected_covers():
    with Document() as source:
        source.pages.add(size=(10, 20)).add_text("Cover", 1, 10, font_size=2)
        _spread(source)
        source.pages.add(size=(10, 20)).add_text("Back", 1, 10, font_size=2)
        with source.split_spreads(pages=(index for index in (1, 1))) as result:
            assert result.page_count == 4
            assert (
                "Cover" in result.pages[0].extract_text()
                and "Back" in result.pages[3].extract_text()
            )
            assert [page.size.width for page in result.pages] == [10, 20, 20, 10]
        with source.split_spreads(pages=[]) as result:
            assert result.page_count == 3
            assert [page.crop_box for page in result.pages] == [
                page.crop_box for page in source.pages
            ]
            result.pages[0].add_text("Output only", 1, 15, font_size=2)
            assert "Output only" not in source.extract_text()


def test_content_remains_vector_and_searchable_and_cropping_does_not_redact(
    monkeypatch,
):
    with Document() as source:
        page = source.pages.add(size=(100, 50))
        page.add_text("LEFT", 5, 20, font_size=7, font_name="Courier-Bold")
        page.add_text("RIGHT", 55, 20, font_size=7, font_name="Courier-Bold")
        monkeypatch.setattr(
            source, "render_page", lambda *a, **k: pytest.fail("no rasterization")
        )
        result = source.split_spreads()
    with result:
        assert result.file_name is None
        for page in result.pages:
            assert "LEFT" in page.extract_text() and "RIGHT" in page.extract_text()
            assert "figure" not in {block.kind for block in page.extract_blocks()}
        assert "LEFT" in result.pages[0].extract_text(clip=result.pages[0].crop_box)
        assert "RIGHT" not in result.pages[0].extract_text(
            clip=result.pages[0].crop_box
        )
        assert "RIGHT" in result.pages[1].extract_text(clip=result.pages[1].crop_box)


def test_metadata_attachments_bookmarks_labels_links_and_fields_on_covers_survive(
    tmp_path,
):
    with Document() as source:
        cover = source.pages.add(size=(20, 20))
        source.form.add_text_field("cover", cover, (1, 10, 19, 19), value="Keep")
        _spread(source)
        _spread(source)
        source.info["Title"] = "Book"
        source.add_attachment("guide.txt", b"Keep")
        source.page_labels[0] = PageLabel(style=NumberingStyle.NUMERALS_ROMAN_LOWERCASE)
        source.outlines.add(OutlineItem("Cover", 0))
        source.outlines.add(OutlineItem("Spread", destination=FitDestination(1)))
        source.outlines.add(OutlineItem("Last", 2))
        source.pages[1].add_link((1, 1, 5, 5), FitDestination(2))
        source.pages[2].add_link((1, 1, 5, 5), URIAction("https://example.test"))
        labels = [source.pages[index].label for index in range(3)]
        original_outlines = [
            (item.title, item.page_index, item.destination) for item in source.outlines
        ]
        with source.split_spreads(pages=[2, 1]) as result:
            assert result.page_count == 5
            assert (
                result.info["Title"] == "Book"
                and result.attachments["guide.txt"] == b"Keep"
            )
            assert [page.label for page in result.pages] == [
                labels[0],
                labels[1],
                labels[1],
                labels[2],
                labels[2],
            ]
            assert [(item.title, item.page_index) for item in result.outlines] == [
                ("Cover", 0),
                ("Spread", 1),
                ("Last", 3),
            ]
            assert result.form["cover"].value == "Keep"
            assert result.validate_links() == []
            path = tmp_path / "split.pdf"
            result.save(path)
            with Document(path) as reopened:
                assert reopened.validate() and reopened.validate_links() == []
                assert [
                    (item.title, item.page_index) for item in reopened.outlines
                ] == [("Cover", 0), ("Spread", 1), ("Last", 3)]
                assert reopened.form["cover"].value == "Keep"
        assert [
            (item.title, item.page_index, item.destination) for item in source.outlines
        ] == original_outlines


def test_page_annotations_have_independent_threads_on_both_halves():
    with Document() as source:
        page = _spread(source)
        note = page.annotations.add_text((1, 1, 5, 5), "Note")
        note.reply("Reply")
        note.add_popup((5, 1, 15, 8), is_open=True)
        with source.split_spreads() as result:
            first, second = [page.annotations for page in result.pages]
            assert len(first) == len(second) == 3
            second[0].contents = "Changed"
            assert (
                first[0].contents == "Note"
                and source.pages[0].annotations[0].contents == "Note"
            )
            for annotations in (first, second):
                assert annotations[1].in_reply_to.contents == annotations[0].contents
                assert (
                    annotations[0].popup.parent_annotation.contents
                    == annotations[0].contents
                )
            assert len(result.export_annotations_xfdf()) > 0
            with Document(_bytes(result)) as reopened:
                assert (
                    reopened.pages[1].annotations[1].in_reply_to.contents == "Changed"
                )


def test_selected_form_widgets_are_flattened_without_changing_source():
    with Document() as source:
        page = source.pages.add(size=(100, 50))
        source.form.add_text_field("name", page, (5, 20, 95, 40), value="Alice")
        reference = page.render(antialias=False)
        with source.split_spreads() as result:
            assert len(result.form) == 0
            for index, page in enumerate(result.pages):
                actual = page.render(antialias=False)
                assert (actual.width, actual.height, actual.pixels) == _region(
                    reference, 50 * index, 0, 50 * (index + 1), 50
                )
        assert source.form["name"].value == "Alice"
        with pytest.raises(UnsupportedFeatureException, match="flatten_fields"):
            source.split_spreads(flatten_fields=False)


def test_image_resources_stay_available_after_source_disposal_and_round_trip():
    with Document() as source:
        page = source.pages.add(size=(20, 10))
        page.add_image(
            RasterizedPage(2, 1, b"\xff\x00\x00\x00\x00\xff").to_png(),
            0,
            0,
            width=20,
            height=10,
        )
        result = source.split_spreads()
    with result:
        assert [
            page.render(antialias=False).get_pixel(5, 5) for page in result.pages
        ] == [(255, 0, 0), (0, 0, 255)]
        with Document(_bytes(result)) as reopened:
            assert [
                page.render(antialias=False).get_pixel(5, 5) for page in reopened.pages
            ] == [(255, 0, 0), (0, 0, 255)]


@pytest.mark.parametrize(
    "options",
    [
        {"layout": "other"},
        {"layout": 1},
        {"reverse": 1},
        {"flatten_fields": 1},
        {"split_ratio": True},
        {"split_ratio": "0.5"},
        {"split_ratio": 0},
        {"split_ratio": 1},
        {"split_ratio": float("nan")},
        {"gutter": True},
        {"gutter": -1},
        {"gutter": float("inf")},
        {"pages": 0},
        {"pages": "0"},
        {"pages": [True]},
        {"pages": [-1]},
        {"pages": [0, 3]},
    ],
)
@pytest.mark.parametrize("empty", [False, True])
def test_invalid_options_fail_before_import(monkeypatch, options, empty):
    from aspose_pdf.engine import spread_split

    with Document() as source:
        if not empty:
            _spread(source)
        monkeypatch.setattr(
            spread_split,
            "_import_source",
            lambda *a, **k: pytest.fail("validate first"),
        )
        with pytest.raises(PdfValidationException):
            source.split_spreads(**options)


def test_empty_document_and_disposed_source():
    with Document() as source:
        with source.split_spreads() as result:
            assert result.page_count == 0
    with pytest.raises(AsposePdfException, match="disposed"):
        source.split_spreads()


def test_invalid_later_geometry_and_gutter_fail_before_import(monkeypatch):
    from aspose_pdf.engine import spread_split

    with Document() as source:
        _spread(source)
        source.pages.add(size=(2, 2))
        monkeypatch.setattr(
            spread_split,
            "_import_source",
            lambda *a, **k: pytest.fail("preflight all pages"),
        )
        with pytest.raises(PdfValidationException, match="positive page regions"):
            source.split_spreads(gutter=3)


def test_output_count_and_unending_selection_obey_resource_limits(monkeypatch):
    from aspose_pdf.engine import spread_split

    with Document(limits=PdfLoadLimits(max_pages=1, max_container_items=3)) as source:
        _spread(source)
        monkeypatch.setattr(
            spread_split, "_import_source", lambda *a, **k: pytest.fail("budget first")
        )
        with pytest.raises(PdfResourceLimitException, match="max_pages"):
            source.split_spreads()
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            source.split_spreads(pages=repeat(0))


def test_unrepresentable_half_box_is_rejected_before_import(monkeypatch):
    from aspose_pdf.engine import spread_split

    with Document() as source:
        source.pages.add(size=(0.000001, 1))
        monkeypatch.setattr(
            spread_split,
            "_import_source",
            lambda *a, **k: pytest.fail("validate serialized geometry first"),
        )
        with pytest.raises(PdfValidationException, match="precision"):
            source.split_spreads()


def test_live_half_boxes_match_the_geometry_saved_to_pdf():
    with Document() as source:
        source.pages.add(size=(10, 10))
        with source.split_spreads(split_ratio=1 / 3) as result:
            with Document(_bytes(result)) as reopened:
                assert [page.media_box for page in result.pages] == [
                    page.media_box for page in reopened.pages
                ]


def test_generated_form_appearances_obey_decoded_stream_limits():
    with Document() as source:
        page = source.pages.add(size=(100, 50))
        source.form.add_text_field("name", page, (1, 10, 90, 40), value="A" * 300)
        source._load_limits = PdfLoadLimits(max_decoded_stream_bytes=128)
        with pytest.raises(PdfResourceLimitException, match="max_decoded_stream_bytes"):
            source.split_spreads()


@pytest.mark.parametrize("damage", ["subtype", "rect", "array", "entry", "repeat"])
def test_malformed_annotations_are_rejected_without_changing_source(damage):
    from aspose_pdf.engine.cos import PdfName, PdfNumber

    with Document() as source:
        page = _spread(source)
        page.annotations.add_text((1, 1, 4, 4), "Note")
        pdf = source._engine_pdf
        dictionary = pdf._get_page_dict(0)
        array = pdf._resolve(dictionary.mapping[PdfName("Annots")])
        annotation = pdf._resolve(array.items[0])
        if damage == "subtype":
            annotation.mapping.pop(PdfName("Subtype"))
        elif damage == "rect":
            annotation.mapping.pop(PdfName("Rect"))
        elif damage == "array":
            dictionary.mapping[PdfName("Annots")] = PdfNumber(42)
        elif damage == "entry":
            array.items[0] = PdfNumber(42)
        else:
            array.items.append(array.items[0])
        with pytest.raises(PdfValidationException):
            source.split_spreads()
        assert not pdf._disposed and source.page_count == 1


@pytest.mark.parametrize("kind", ["bytes", "file", "streaming"])
@pytest.mark.parametrize("encrypted", [False, True])
def test_loaded_sources_survive_independently_and_keep_streaming_caches_lazy(
    tmp_path, kind, encrypted
):
    path = tmp_path / "spreads.pdf"
    with Document() as document:
        _spread(document)
        _spread(document, offset=True).rotation = 90
        document.info["Title"] = "Original"
        document.add_attachment("original.txt", b"Keep")
        if encrypted:
            document.encrypt("user", "owner")
        document.save(path)
    raw = path.read_bytes()
    opener = Document.open_streaming if kind == "streaming" else Document
    with opener(
        raw if kind == "bytes" else path, password="user" if encrypted else None
    ) as source:
        protected = source.is_encrypted
        result = source.split_spreads(pages=[1], reverse=True)
        assert source.is_encrypted is protected and source.page_count == 2
        assert source.info["Title"] == "Original"
        if kind == "streaming":
            assert (
                source._engine_pdf.page_contents == []
                and source._engine_pdf._extracted_text is None
            )
    with result:
        assert result.page_count == 3 and not result.is_encrypted
        assert (
            result.info["Title"] == "Original"
            and result.attachments["original.txt"] == b"Keep"
        )
        with Document(_bytes(result)) as reopened:
            assert reopened.validate() and not reopened.is_encrypted
            assert reopened.page_count == 3
            assert all(
                page.render(dpi=36, antialias=False).width > 0
                for page in reopened.pages
            )
    assert path.read_bytes() == raw


@pytest.mark.parametrize("interrupt", [False, True])
def test_late_failure_disposes_partial_output_and_preserves_source(
    monkeypatch, interrupt
):
    from aspose_pdf.engine import spread_split

    with Document() as source:
        _spread(source)
        _spread(source)
        before = _bytes(source)
        imported = spread_split._import_source
        copy = spread_split._copy_annotations
        created = []
        calls = []

        def remember(result, *args, **kwargs):
            created.append(result)
            return imported(result, *args, **kwargs)

        def fail_late(*args):
            calls.append(True)
            if len(calls) == 2:
                if interrupt:
                    raise KeyboardInterrupt()
                raise PdfValidationException("Later split failed")
            return copy(*args)

        monkeypatch.setattr(spread_split, "_import_source", remember)
        monkeypatch.setattr(spread_split, "_copy_annotations", fail_late)
        with pytest.raises(KeyboardInterrupt if interrupt else PdfValidationException):
            source.split_spreads()
        assert len(created) == 1 and created[0]._disposed
        assert not source._engine_pdf._disposed and _bytes(source) == before


def test_hidden_widgets_are_refused_without_removing_source_field():
    with Document() as source:
        page = _spread(source)
        source.form.add_text_field("hidden", page, (1, 1, 5, 5), value="Keep")
        source.pages[0].annotations[0].set_property("F", 2)
        with pytest.raises(UnsupportedFeatureException, match="could not be flattened"):
            source.split_spreads()
        assert source.form["hidden"].value == "Keep"


def test_layer_visibility_is_preserved_on_both_halves():
    with Document() as source:
        page = source.pages.add(size=(40, 20))
        layer = source.layers.add("Content")
        with page.layer(layer):
            page.draw_rectangle(0, 0, 40, 20, fill_color="#f00", stroke_color=None)
        with source.split_spreads() as result:
            assert [layer.name for layer in result.layers] == ["Content"]
            assert [
                page.render(antialias=False).get_pixel(10, 10) for page in result.pages
            ] == [(255, 0, 0)] * 2
            result.layers[0].visible = False
            assert [
                page.render(antialias=False).get_pixel(10, 10) for page in result.pages
            ] == [(255, 255, 255)] * 2
        assert source.layers[0].visible


def test_total_copied_content_is_bounded_before_allocating_the_second_half(monkeypatch):
    from aspose_pdf.engine import spread_split

    with Document() as source:
        _spread(source)
        size = len(source._engine_pdf.get_page_content(0))
        source._load_limits = PdfLoadLimits(max_total_decoded_bytes=2 * size - 1)
        monkeypatch.setattr(
            spread_split,
            "_copy_annotations",
            lambda *a: pytest.fail("bound copied content first"),
        )
        with pytest.raises(PdfResourceLimitException, match="max_total_decoded_bytes"):
            source.split_spreads()
        assert source.page_count == 1 and not source._engine_pdf._disposed
