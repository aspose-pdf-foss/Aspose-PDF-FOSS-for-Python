"""Rasterized copies are independent image-only PDFs of selected page views."""

from __future__ import annotations

import io
import weakref
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
from aspose_pdf.engine import rasterized_copy
from aspose_pdf.engine.cos import PdfDictionary, PdfName, PdfStream
from aspose_pdf.exceptions import AsposePdfException, PdfValidationException


def _bytes(document):
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _page(document, color=None, size=(20, 20)):
    page = document.pages.add(size=size)
    if color:
        page.draw_rectangle(0, 0, *size, fill_color=color, stroke_color=None)
    return page


def _images(document):
    return [
        value
        for value in document._engine_pdf._cos_doc.objects.values()
        if isinstance(value, PdfStream)
        and value.mapping.get(PdfName("Subtype")) == PdfName("Image")
    ]


def test_copy_renders_one_rgb_image_per_page_with_no_searchable_text(tmp_path):
    with Document() as source:
        page = _page(source)
        page.add_text("Visible text", 1, 10, font_size=3)
        page.draw_rectangle(0, 0, 5, 5, fill_color="#f00", stroke_color=None)
        scan = RasterizedPage(2, 1, bytes((0, 255, 0, 0, 0, 255))).to_png()
        _page(source, size=(40, 20)).add_image(scan, 0, 0, width=40, height=20)
        references = [
            page.render(dpi=72, antialias=False).pixels for page in source.pages
        ]
        before = _bytes(source)
        result = source.rasterized_copy(dpi=72, antialias=False)
        assert _bytes(source) == before and source.extract_text() == "Visible text\n"
    with result:
        assert result.page_count == 2 and result.file_name is None
        assert result.extract_text() == "\n"
        assert all(page.extract_text() == "" for page in result.pages)
        assert len(_images(result)) == 2
        assert all(
            image.mapping[PdfName("ColorSpace")] == PdfName("DeviceRGB")
            and image.mapping[PdfName("BitsPerComponent")].value == 8
            for image in _images(result)
        )
        assert [
            page.render(antialias=False).pixels for page in result.pages
        ] == references
        path = tmp_path / "raster.pdf"
        result.save(path)
        for argument in (path, _bytes(result)):
            with Document(argument) as loaded:
                assert loaded.validate() and loaded.page_count == 2
                assert [
                    page.render(antialias=False).pixels for page in loaded.pages
                ] == references
                assert loaded.extract_text() == "\n" and len(_images(loaded)) == 2
        result.pages[0].add_text("OUTPUT ONLY", 1, 10, font_size=3)
        assert "OUTPUT ONLY" in result.extract_text()


def test_selected_pages_keep_order_repeats_and_independent_resources():
    with Document() as source:
        for color in ("#f00", "#0f0", "#00f"):
            _page(source, color)
        with source.rasterized_copy(
            pages=(index for index in (2, 0, 2)), dpi=72, antialias=False
        ) as result:
            assert [
                page.render(antialias=False).get_pixel(10, 10) for page in result.pages
            ] == [(0, 0, 255), (255, 0, 0), (0, 0, 255)]
            assert len(_images(result)) == 3
            result.pages[0].draw_rectangle(
                0, 0, 20, 20, fill_color="#000", stroke_color=None
            )
            assert result.pages[2].render(antialias=False).get_pixel(10, 10) == (
                0,
                0,
                255,
            )
            assert source.pages[2].render(antialias=False).get_pixel(10, 10) == (
                0,
                0,
                255,
            )


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("antialias", [False, True, 2, 8])
def test_visible_crop_rotation_offset_and_antialias_match_source_view(
    rotation, antialias
):
    with Document() as source:
        page = source.pages.add(((-20, 10, 100, 90), b"1 0 0 rg -20 10 50 80 re f"))
        page.crop_box = (-10, 20, 70, 60)
        page.rotation = rotation
        reference = page.render(dpi=72, antialias=antialias, background=(200, 220, 240))
        with source.rasterized_copy(
            dpi=72, antialias=antialias, background=(200, 220, 240)
        ) as result:
            output = result.pages[0]
            size = (40.0, 80.0) if rotation in (90, 270) else (80.0, 40.0)
            assert output.media_box == output.crop_box == (0, 0, *size)
            assert output.rotation == 0
            assert output.render(antialias=False).pixels == reference.pixels
            with Document(_bytes(result)) as loaded:
                assert loaded.pages[0].rotation == 0
                assert (
                    loaded.pages[0].render(antialias=False).pixels == reference.pixels
                )


@pytest.mark.parametrize(
    "dpi,pixels",
    [(36, (6, 4)), (72, (11, 8)), (96, (14, 10)), (150, (22, 16)), (144, (21, 15))],
)
def test_dpi_controls_pixels_without_enlarging_fractional_physical_page_size(
    dpi, pixels
):
    with Document() as source:
        _page(source, "#f00", size=(10.25, 7.5))
        with source.rasterized_copy(dpi=dpi, antialias=False) as result:
            assert result.pages[0].media_box == (0, 0, 10.25, 7.5)
            (image,) = _images(result)
            assert (
                image.mapping[PdfName("Width")].value,
                image.mapping[PdfName("Height")].value,
            ) == pixels


def test_default_dpi_is_150_and_default_antialias_factor_is_three(monkeypatch):
    with Document() as source:
        _page(source, size=(12, 12))
        original = source.render_page
        calls = []

        def render(index, **options):
            calls.append((index, options))
            return original(index, **options)

        monkeypatch.setattr(source, "render_page", render)
        with source.rasterized_copy() as result:
            assert calls == [
                (
                    0,
                    dict(
                        dpi=150.0,
                        background=(255, 255, 255),
                        antialias=True,
                        draw_annotations=True,
                    ),
                )
            ]
            assert _images(result)[0].mapping[PdfName("Width")].value == 25


def test_annotations_and_filled_form_appearances_are_baked_or_excluded():
    with Document() as source:
        page = _page(source, size=(80, 40))
        page.annotations.add(
            "Square", (1, 1, 10, 10), "Comment", properties={"IC": [1, 0, 0]}
        )
        source.form.add_text_field("Name", 0, (20, 5, 75, 25), value="Visible")
        for include in (False, True):
            reference = page.render(antialias=False, draw_annotations=include)
            with source.rasterized_copy(
                dpi=72, antialias=False, include_annotations=include
            ) as result:
                assert (
                    result.pages[0].render(antialias=False).pixels == reference.pixels
                )
                assert (
                    list(result.pages[0].annotations) == [] and list(result.form) == []
                )
        assert (
            page.render(antialias=False).pixels
            != page.render(antialias=False, draw_annotations=False).pixels
        )


def test_nonvisual_source_objects_and_hidden_text_are_not_transferred():
    with Document() as source:
        page = _page(source)
        source._engine_pdf._set_page_content(
            0, b"BT /F1 5 Tf 3 Tr 1 10 Td (Hidden OCR) Tj ET"
        )
        hidden = source.layers.add("Hidden", visible=False)
        with page.layer(hidden):
            page.draw_rectangle(0, 0, 20, 20, fill_color="#f00", stroke_color=None)
        page.add_link((1, 1, 10, 10), FitDestination(0))
        source.outlines.add(OutlineItem("Private title", 0))
        source.destinations["Private"] = FitDestination(0)
        source.page_labels[0] = PageLabel(NumberingStyle.NUMERALS_ARABIC, "Private-", 7)
        source.info["Title"] = "Private metadata"
        source.xmp_metadata.set_value(
            "xmp", "Label", "Private XMP", uri="http://ns.adobe.com/xap/1.0/"
        )
        source.add_attachment("secret.txt", b"Private attachment")
        source.javascript.add("startup", "app.alert('Private script')")
        with source.rasterized_copy(dpi=72, antialias=False) as result:
            assert result.pages[0].render(antialias=False).get_pixel(10, 10) == (
                255,
                255,
                255,
            )
            assert result.extract_text() == "" and result.attachments == {}
            assert result.info.get("Title") is None and len(result.outlines) == 0
            assert list(result.form) == [] and list(result.layers) == []
            assert list(result.destinations) == [] and list(result.javascript) == []
            assert (
                result.pages[0].label is None
                and list(result.pages[0].annotations) == []
            )
            data = _bytes(result)
            assert b"Private" not in data and b"Hidden OCR" not in data
            catalog = result._engine_pdf._resolve(
                result._engine_pdf._cos_doc.trailer.mapping[PdfName("Root")]
            )
            assert isinstance(catalog, PdfDictionary)
            assert all(
                PdfName(name) not in catalog.mapping
                for name in (
                    "AcroForm",
                    "Outlines",
                    "Names",
                    "OCProperties",
                    "Metadata",
                    "StructTreeRoot",
                    "OpenAction",
                )
            )


def test_empty_selection_returns_independent_empty_document():
    with Document() as source:
        for pages in (None, []):
            with source.rasterized_copy(pages=pages) as result:
                assert result is not source and result.page_count == 0
                with Document(_bytes(result)) as loaded:
                    assert loaded.page_count == 0
        _page(source)
        with source.rasterized_copy(pages=[]) as result:
            assert result.page_count == 0 and source.page_count == 1


@pytest.mark.parametrize(
    "name,value",
    [
        ("dpi", 0),
        ("dpi", -1),
        ("dpi", True),
        ("dpi", "72"),
        ("dpi", float("nan")),
        ("dpi", float("inf")),
        ("dpi", 10**1000),
        ("dpi", 5e-324),
        ("antialias", 0),
        ("antialias", 9),
        ("antialias", None),
        ("antialias", "2"),
        ("antialias", 2.0),
        ("background", None),
        ("background", "fff"),
        ("background", b"abc"),
        ("background", [0, 0]),
        ("background", [0, 0, 256]),
        ("background", [True, 0, 0]),
        ("background", [0, 0, 1.5]),
        ("include_annotations", 0),
        ("include_annotations", None),
    ],
)
@pytest.mark.parametrize("empty", [False, True])
def test_invalid_options_fail_before_geometry_output_creation_or_render(
    monkeypatch, name, value, empty
):
    with Document() as source:
        if not empty:
            _page(source)

        def forbidden(*args, **options):
            pytest.fail("validate before geometry, output allocation or rendering")

        monkeypatch.setattr(rasterized_copy, "_geometry", forbidden)
        monkeypatch.setattr(source, "render_page", forbidden)
        with monkeypatch.context() as patch:
            patch.setattr(Document, "__init__", forbidden)
            with pytest.raises(PdfValidationException):
                source.rasterized_copy(**{name: value})


@pytest.mark.parametrize("pages", [0, "0", b"0", [True], [0.5], [-1], [1], [0, 1]])
def test_full_selection_is_validated_before_geometry_or_render(monkeypatch, pages):
    with Document() as source:
        _page(source)
        monkeypatch.setattr(
            rasterized_copy, "_geometry", lambda *a, **kw: pytest.fail("validate first")
        )
        with pytest.raises(PdfValidationException):
            source.rasterized_copy(pages=pages)


def test_disposed_source_is_rejected():
    source = Document()
    source.dispose()
    with pytest.raises(AsposePdfException, match="disposed"):
        source.rasterized_copy()


@pytest.mark.parametrize("limit", ["max_pages", "max_container_items"])
def test_page_count_limits_apply_to_all_pages_and_repeated_selection(
    monkeypatch, limit
):
    with Document(limits=PdfLoadLimits(**{limit: 1})) as source:
        _page(source)
        _page(source)
        monkeypatch.setattr(
            source, "render_page", lambda *a, **kw: pytest.fail("limit first")
        )
        for options in ({}, {"pages": [0, 0]}):
            with pytest.raises(PdfResourceLimitException, match=limit):
                source.rasterized_copy(**options)


def test_unbounded_page_selection_is_stopped():
    with Document(limits=PdfLoadLimits(max_container_items=2)) as source:
        _page(source)
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            source.rasterized_copy(pages=repeat(0))


@pytest.mark.parametrize(
    "limits,options,match",
    [
        (
            PdfLoadLimits(max_raster_pixels=99),
            {"antialias": False},
            "max_raster_pixels",
        ),
        (PdfLoadLimits(max_raster_pixels=899), {}, "max_raster_pixels"),
        (PdfLoadLimits(max_image_pixels=99), {}, "max_image_pixels"),
        (PdfLoadLimits(max_object_bytes=299), {}, "max_object_bytes"),
        (PdfLoadLimits(max_decoded_stream_bytes=299), {}, "max_decoded_stream_bytes"),
        (
            PdfLoadLimits(max_codec_work_bytes=512 * 1024 + 599),
            {},
            "max_codec_work_bytes",
        ),
        (PdfLoadLimits(max_content_stream_bytes=4), {}, "max_content_stream_bytes"),
        (
            PdfLoadLimits(max_total_decoded_bytes=599),
            {"pages": [0, 0]},
            "max_total_decoded_bytes",
        ),
        (PdfLoadLimits(max_objects=5), {}, "max_objects"),
    ],
)
def test_all_output_limits_are_checked_before_rendering(
    monkeypatch, limits, options, match
):
    with Document(limits=limits) as source:
        _page(source, size=(10, 10))
        monkeypatch.setattr(
            source, "render_page", lambda *a, **kw: pytest.fail("preflight first")
        )
        with pytest.raises(PdfResourceLimitException, match=match):
            source.rasterized_copy(dpi=72, **options)


@pytest.mark.parametrize("kind", ["nonfinite", "overflow", "overbudget"])
def test_later_invalid_geometry_is_rejected_before_any_output_or_render(
    monkeypatch, kind
):
    with Document() as source:
        _page(source)
        _page(source)
        source._engine_pdf.pages[1] = {
            "nonfinite": (0, 0, float("nan"), 1),
            "overflow": (-1e308, 0, 1e308, 1),
            "overbudget": (0, 0, 10**12, 10**12),
        }[kind]

        def forbidden(*a, **kw):
            pytest.fail("preflight every page first")

        monkeypatch.setattr(source, "render_page", forbidden)
        with monkeypatch.context() as patch:
            patch.setattr(Document, "__init__", forbidden)
            expected = (
                PdfResourceLimitException
                if kind == "overbudget"
                else PdfValidationException
            )
            with pytest.raises(expected):
                source.rasterized_copy()


@pytest.mark.parametrize("compression_ratio", [1, 2000, None])
def test_lossless_storage_obeys_compression_ratio_policy(compression_ratio):
    with Document(
        limits=PdfLoadLimits(max_compression_ratio=compression_ratio)
    ) as source:
        _page(source, "#f00")
        with source.rasterized_copy(dpi=72, antialias=False) as result:
            assert result.load_limits is source.load_limits
            (image,) = _images(result)
            assert image.mapping.get(PdfName("Filter")) == (
                None if compression_ratio == 1 else PdfName("FlateDecode")
            )
            with Document(_bytes(result), limits=source.load_limits) as loaded:
                assert loaded.pages[0].render(antialias=False).get_pixel(10, 10) == (
                    255,
                    0,
                    0,
                )


@pytest.mark.parametrize("kind", ["bytes", "file", "streaming"])
@pytest.mark.parametrize("encrypted", [False, True])
def test_saved_sources_only_read_selected_pages_and_remain_unchanged(
    tmp_path, monkeypatch, kind, encrypted
):
    path = tmp_path / "source.pdf"
    with Document() as source:
        for color in ("#f00", "#0f0", "#00f"):
            _page(source, color)
        source.info["Title"] = "Source metadata"
        source.add_attachment("source.txt", b"Keep source attachment")
        if encrypted:
            source.encrypt("secret", "owner")
        source.save(path)
    data = path.read_bytes()
    opener = Document.open_streaming if kind == "streaming" else Document
    argument = data if kind == "bytes" else path
    with opener(argument, password="secret" if encrypted else None) as source:
        protection = source.is_encrypted
        visited = []
        original = source._engine_pdf.get_page_content

        def content(index):
            assert index in (0, 2)
            visited.append(index)
            return original(index)

        monkeypatch.setattr(source._engine_pdf, "get_page_content", content)
        result = source.rasterized_copy(pages=[2, 0, 2], dpi=72, antialias=False)
        assert set(visited) == {0, 2}
        assert source.is_encrypted is protection
        assert source.info["Title"] == "Source metadata"
        assert source.attachments == {"source.txt": b"Keep source attachment"}
        if kind == "streaming":
            assert source._engine_pdf.page_contents == []
            assert source._engine_pdf._extracted_text is None
    with result:
        assert [
            page.render(antialias=False).get_pixel(10, 10) for page in result.pages
        ] == [(0, 0, 255), (255, 0, 0), (0, 0, 255)]
        assert result.attachments == {} and not result.is_encrypted
        assert result.info.get("Title") is None
        output = tmp_path / "result.pdf"
        result.save(output)
        with Document(output) as loaded:
            assert loaded.validate() and not loaded.is_encrypted
            assert loaded.page_count == 3 and len(_images(loaded)) == 3
    assert path.read_bytes() == data


def test_streaming_selected_content_limits_propagate(tmp_path):
    path = tmp_path / "source.pdf"
    with Document() as source:
        _page(source).add_text("Some source text", 1, 10, font_size=3)
        source.save(path)
    with Document.open_streaming(
        path, limits=PdfLoadLimits(max_content_stream_bytes=40)
    ) as source:
        with pytest.raises(PdfResourceLimitException, match="max_content_stream_bytes"):
            source.rasterized_copy(dpi=72, antialias=False)


def test_previous_raster_wrappers_are_released_before_rendering_next_page(monkeypatch):
    with Document() as source:
        for _ in range(3):
            _page(source, "#f00")
        original = source.render_page
        references = []

        def render(index, **options):
            assert all(reference() is None for reference in references)
            raster = original(index, **options)
            references.append(weakref.ref(raster))
            return raster

        monkeypatch.setattr(source, "render_page", render)
        with source.rasterized_copy(dpi=72, antialias=False) as result:
            assert result.page_count == 3
            assert all(reference() is None for reference in references)


@pytest.mark.parametrize("stage", ["render", "append", "interrupt"])
def test_late_failure_disposes_partial_result_and_preserves_source(monkeypatch, stage):
    with Document() as source:
        _page(source, "#f00")
        _page(source, "#00f")
        before = _bytes(source)
        original_append, original_render = (
            rasterized_copy._append_image,
            source.render_page,
        )
        partial = []

        def append(pdf, *args):
            partial.append(pdf)
            original_append(pdf, *args)
            if stage == "append" and len(partial) == 2:
                raise PdfValidationException("Unable to append image")

        def render(index, **options):
            if index == 1:
                if stage == "render":
                    raise PdfValidationException("Unable to render page")
                if stage == "interrupt":
                    raise KeyboardInterrupt("Interrupted copy")
            return original_render(index, **options)

        monkeypatch.setattr(rasterized_copy, "_append_image", append)
        monkeypatch.setattr(source, "render_page", render)
        error = KeyboardInterrupt if stage == "interrupt" else PdfValidationException
        with pytest.raises(error):
            source.rasterized_copy(dpi=72, antialias=False)
        assert partial and all(pdf is partial[0] for pdf in partial)
        with pytest.raises(AsposePdfException, match="disposed"):
            partial[0]._ensure_not_disposed()
        assert _bytes(source) == before


def test_cached_text_cursor_and_live_outline_edits_are_unchanged():
    with Document() as source:
        _page(source).add_text("Source text", 1, 10, font_size=3)
        cached = source.extract_text()
        source._engine_pdf._page_text_cursor = 1
        item = source.outlines.add(OutlineItem("Unsaved", 0))
        model = source._engine_pdf._outlines_data
        with source.rasterized_copy(dpi=72, antialias=False) as result:
            assert result.extract_text() == ""
        assert source._engine_pdf._extracted_text is cached
        assert source._engine_pdf._page_text_cursor == 1
        assert source.outlines[0] is item and source._engine_pdf._outlines_data is model


def test_each_call_observes_unsaved_edits_and_current_layer_visibility():
    with Document() as source:
        page = _page(source)
        layer = source.layers.add("Overlay", visible=False)
        with page.layer(layer):
            page.draw_rectangle(0, 0, 20, 20, fill_color="#f00", stroke_color=None)
        with source.rasterized_copy(dpi=72, antialias=False) as first:
            assert first.pages[0].render(antialias=False).get_pixel(10, 10) == (
                255,
                255,
                255,
            )
            layer.visible = True
            with source.rasterized_copy(dpi=72, antialias=False) as second:
                assert second.pages[0].render(antialias=False).get_pixel(10, 10) == (
                    255,
                    0,
                    0,
                )
            assert first.pages[0].render(antialias=False).get_pixel(10, 10) == (
                255,
                255,
                255,
            )


@pytest.mark.parametrize(
    "samples",
    [
        b"\x89PNG\r\n\x1a\n\x63",
        b"\xff\xd8\xff\x00\x11\x22\x33\x44\x55",
        b"\x00\x00\x00\x0cjP  \r\n\x87\n",
    ],
)
def test_rgb_samples_resembling_file_headers_are_not_reparsed_as_image_files(
    monkeypatch, samples
):
    with Document() as source:
        width = len(samples) // 3
        page = _page(source, size=(width, 1))
        page.add_image(
            RasterizedPage(width, 1, samples).to_png(), 0, 0, width=width, height=1
        )
        monkeypatch.setattr(
            RasterizedPage,
            "to_png",
            lambda *a, **kw: pytest.fail("no intermediate PNG encoding"),
        )
        with source.rasterized_copy(dpi=72, antialias=False) as result:
            assert result.pages[0].render(antialias=False).pixels == samples
            with Document(_bytes(result)) as loaded:
                assert loaded.pages[0].render(antialias=False).pixels == samples


def test_exact_output_object_and_decoded_size_boundaries():
    content = rasterized_copy.build_image_stream("Im1", 0, 0, 10, 10)
    limits = PdfLoadLimits(
        max_objects=6, max_total_decoded_bytes=300 + len(content), max_raster_pixels=100
    )
    with Document(limits=limits) as source:
        _page(source, "#f00", size=(10, 10))
        with source.rasterized_copy(dpi=72, antialias=False) as result:
            assert len(result._engine_pdf._cos_doc.objects) == 5
            assert result.pages[0].render(antialias=False).get_pixel(5, 5) == (
                255,
                0,
                0,
            )
