"""Fitted page previews preserve appearance, pixel bounds and lazy rendering."""

from __future__ import annotations

import io
import sys

import pytest

from aspose_pdf import Document, PdfLoadLimits, RasterizedPage
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfResourceLimitException,
    PdfValidationException,
)


def _bytes(document):
    with io.BytesIO() as stream:
        document.save(stream)
        return stream.getvalue()


@pytest.mark.parametrize("antialias", [False, True, 2, 8])
@pytest.mark.parametrize(
    "page_size,bounds,expected,scale",
    [
        ((400, 200), (100, 100), (100, 50), 0.25),
        ((200, 400), (100, 100), (50, 100), 0.25),
        ((200, 200), (100, 60), (60, 60), 0.3),
        ((20, 10), (80, 80), (80, 40), 4),
        ((40, 20), (1, 1), (1, 1), 0.025),
    ],
)
def test_fitted_dimensions_and_pixels_match_direct_render(
    antialias, page_size, bounds, expected, scale
):
    with Document() as document:
        page = document.pages.add(size=page_size)
        page.draw_rectangle(
            0, 0, page_size[0] / 2, page_size[1], fill_color="#f00", stroke_color=None
        )
        result = page.render_thumbnail(size=bounds, antialias=antialias)
        reference = page.render(scale=scale, antialias=antialias)
        assert isinstance(result, RasterizedPage)
        assert (result.width, result.height, result.dpi) == (*expected, 72)
        assert result.pixels == reference.pixels
        assert 1 <= result.width <= bounds[0] and 1 <= result.height <= bounds[1]


def test_default_size_and_one_based_document_page_selection():
    with Document() as document:
        document.pages.add(size=(440, 320))
        page = document.pages.add(size=(320, 440))
        page.draw_rectangle(0, 0, 320, 440, fill_color="#00f", stroke_color=None)
        raster = document.render_thumbnail(2, antialias=False)
        assert (raster.width, raster.height) == (160, 220)
        assert raster.pixels == bytes((0, 0, 255)) * (160 * 220)
        assert page.render_thumbnail(antialias=False) == raster


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_offset_crop_and_rotation_match_renderer_without_changing_pdf(rotation):
    with Document() as document:
        page = document.pages.add(((-20, 10, 100, 90), b"1 0 0 rg -20 10 50 80 re f"))
        page.crop_box = (-10, 20, 70, 60)
        page.rotation = rotation
        before = _bytes(document)
        raster = page.render_thumbnail(
            size=(40, 40), background=(200, 220, 240), antialias=False
        )
        reference = page.render(scale=0.5, background=(200, 220, 240), antialias=False)
        assert (raster.width, raster.height) == (
            (20, 40) if rotation in (90, 270) else (40, 20)
        )
        assert raster.pixels == reference.pixels
        assert page.crop_box == (-10, 20, 70, 60) and page.rotation == rotation
        assert _bytes(document) == before


@pytest.mark.parametrize(
    "page_size,bounds",
    [
        ((0.3, 0.7), (7, 11)),
        ((595.28, 841.89), (160, 220)),
        ((1e-6, 1e6), (20, 30)),
        ((1e300, 2e300), (10, 10)),
    ],
)
def test_fractional_and_extreme_geometry_remains_inside_requested_bounds(
    page_size, bounds
):
    with Document() as document:
        page = document.pages.add(size=page_size)
        raster = page.render_thumbnail(size=bounds, antialias=False)
        assert 1 <= raster.width <= bounds[0] and 1 <= raster.height <= bounds[1]
        assert len(raster.pixels) == raster.width * raster.height * 3


@pytest.mark.parametrize("hidden", [False, True])
def test_annotation_switch_and_hidden_layers_follow_page_rendering(hidden):
    with Document() as document:
        page = document.pages.add(size=(40, 40))
        layer = document.layers.add("Hidden", visible=False)
        with page.layer(layer):
            page.draw_rectangle(0, 0, 40, 40, fill_color="#00f", stroke_color=None)
        page.annotations.add(
            "Square",
            (5, 5, 35, 35),
            "Review",
            properties={"IC": [1, 0, 0], "F": 2 if hidden else 0},
        )
        raster = page.render_thumbnail(size=(20, 20), antialias=False)
        assert raster.get_pixel(10, 10) == ((255, 255, 255) if hidden else (255, 0, 0))
        plain = page.render_thumbnail(
            size=(20, 20), include_annotations=False, antialias=False
        )
        assert plain.get_pixel(10, 10) == (255, 255, 255)
        assert (
            plain.pixels
            == page.render(scale=0.5, draw_annotations=False, antialias=False).pixels
        )


def test_png_tiff_and_jpeg_export_need_no_pillow_and_preserve_pixels(
    tmp_path, monkeypatch
):
    with Document() as document:
        page = document.pages.add(size=(80, 40))
        scan = RasterizedPage(2, 1, bytes((255, 0, 0, 0, 0, 255))).to_png()
        page.add_image(scan, 0, 0, width=80, height=40)
        page.add_text("Preview", 2, 15, font_size=10)
        monkeypatch.setitem(sys.modules, "PIL", None)
        raster = page.render_thumbnail(size=(40, 40), antialias=False)
        png = raster.save(tmp_path / "new" / "preview.png")
        with Document() as output:
            output.pages.add(size=(raster.width, raster.height)).add_image(
                png.read_bytes(), 0, 0, width=raster.width, height=raster.height
            )
            assert output.render_page(1, antialias=False).pixels == raster.pixels
        assert (
            raster.save(tmp_path / "preview.tiff").read_bytes().startswith(b"II*\x00")
        )
        assert (
            raster.save(tmp_path / "preview.jpg").read_bytes().startswith(b"\xff\xd8")
        )


@pytest.mark.parametrize("source", ["bytes", "file", "streaming"])
@pytest.mark.parametrize("encrypted", [False, True])
def test_selected_page_only_is_decoded_after_pdf_round_trip(
    tmp_path, monkeypatch, source, encrypted
):
    path = tmp_path / "source.pdf"
    with Document() as document:
        for color in ("#f00", "#0f0", "#00f"):
            page = document.pages.add(size=(80, 40))
            page.draw_rectangle(0, 0, 80, 40, fill_color=color, stroke_color=None)
        if encrypted:
            document.encrypt(
                user_password="secret", owner_password="owner", algorithm="AES-256"
            )
        document.save(path)
    before = path.read_bytes()
    opener = Document.open_streaming if source == "streaming" else Document
    with opener(
        before if source == "bytes" else path, password="secret" if encrypted else None
    ) as document:
        engine = document._engine_pdf
        protected = document.is_encrypted
        original = engine.get_page_content
        reads = []

        def content(index):
            assert index == 1
            reads.append(index)
            return original(index)

        monkeypatch.setattr(engine, "get_page_content", content)
        raster = document.render_thumbnail(2, size=(20, 20), antialias=False)
        assert (raster.width, raster.height) == (20, 10)
        assert raster.pixels == bytes((0, 255, 0)) * 200
        assert reads and document.is_encrypted is protected
        if source == "streaming":
            assert engine.page_contents == [] and engine._extracted_text is None
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "options",
    [
        {"size": None},
        {"size": "12"},
        {"size": b"12"},
        {"size": (1,)},
        {"size": (1, 2, 3)},
        {"size": (0, 1)},
        {"size": (1, -1)},
        {"size": (True, 1)},
        {"size": (1, 2.5)},
        {"size": (1, float("inf"))},
        {"size": (10**1000, 1)},
        {"background": None},
        {"background": "fff"},
        {"background": (0, 0)},
        {"background": (True, 0, 0)},
        {"background": (1.0, 0, 0)},
        {"background": (0, 0, 256)},
        {"background": (-1, 0, 0)},
        {"include_annotations": 1},
        {"antialias": None},
        {"antialias": 0},
        {"antialias": 9},
        {"antialias": 1.5},
        {"antialias": "2"},
        {"antialias": float("nan")},
    ],
)
def test_invalid_options_fail_before_rendering_or_content_reads(monkeypatch, options):
    with Document() as document:
        page = document.pages.add()

        def unexpected(*args, **kwargs):
            raise AssertionError("Invalid thumbnail options must not render")

        monkeypatch.setattr(document, "_api_render_page", unexpected)
        monkeypatch.setattr(document._engine_pdf, "get_page_content", unexpected)
        with pytest.raises(PdfValidationException):
            page.render_thumbnail(**options)


@pytest.mark.parametrize(
    "page,error",
    [
        (0, IndexError),
        (-1, IndexError),
        (2, IndexError),
        (True, TypeError),
        (1.5, TypeError),
    ],
)
def test_invalid_one_based_page_numbers_are_rejected(page, error):
    with Document() as document:
        document.pages.add()
        with pytest.raises(error):
            document.render_thumbnail(page)


def test_empty_and_disposed_documents_are_rejected():
    with Document() as document:
        with pytest.raises(IndexError):
            document.render_thumbnail(1)
        page = document.pages.add()
        document.dispose()
        with pytest.raises(AsposePdfException, match="disposed"):
            document.render_thumbnail(1)
        with pytest.raises(AsposePdfException, match="disposed"):
            page.render_thumbnail()


@pytest.mark.parametrize("antialias,factor", [(False, 1), (True, 3), (2, 2)])
def test_supersampling_budget_is_checked_before_renderer_starts(
    monkeypatch, antialias, factor
):
    with Document(
        limits=PdfLoadLimits(max_raster_pixels=800 * factor * factor - 1)
    ) as document:
        document.pages.add(size=(80, 40))

        def unexpected(*args, **kwargs):
            raise AssertionError("Pixel limits must be checked before rendering")

        monkeypatch.setattr(document, "_api_render_page", unexpected)
        with pytest.raises(PdfResourceLimitException, match="max_raster_pixels"):
            document.render_thumbnail(1, size=(40, 40), antialias=antialias)


def test_large_pdf_geometry_is_rendered_directly_at_thumbnail_scale():
    with Document(limits=PdfLoadLimits(max_raster_pixels=1000)) as document:
        page = document.pages.add(size=(100_000, 50_000))
        with pytest.raises(PdfResourceLimitException, match="max_raster_pixels"):
            page.render(antialias=False)
        raster = page.render_thumbnail(size=(40, 40), antialias=False)
        assert (raster.width, raster.height) == (40, 20)


def test_content_limits_still_apply_to_thumbnail_rendering():
    with Document(limits=PdfLoadLimits(max_content_tokens=3)) as document:
        document.pages.add(((0, 0, 40, 20), b"0 0 0 rg 0 0 40 20 re f"))
        with pytest.raises(PdfResourceLimitException, match="max_content_tokens"):
            document.render_thumbnail(1, size=(20, 20), antialias=False)
