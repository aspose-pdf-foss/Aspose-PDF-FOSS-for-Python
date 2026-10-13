"""Render viewports use PDF coordinates without allocating a full-page canvas."""

import math
import struct
from dataclasses import replace
from io import BytesIO

import pytest

from aspose_pdf import Document, PdfLoadLimits, PdfResourceLimitException
from aspose_pdf.engine import rasterizer
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfBoolean,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfStream,
)
from aspose_pdf.exceptions import PdfValidationException

_MEDIA = (-20, 10, 60, 80)
_CROP = (-10, 20, 50, 70)
_CLIP = (2, 30, 22, 46)
_CONTENT = (
    b"0.1 0.2 0.7 rg -20 10 80 70 re f "
    b"q -5 25 40 30 re W n 1 0 0 rg -8 22 m 40 28 l 5 60 l h f Q "
    b"0 0 0 RG 2 w -10 35 m 50 50 l S"
)


@pytest.fixture
def document():
    with Document() as doc:
        page = doc.pages.add((_MEDIA, _CONTENT))
        page.crop_box = _CROP
        yield doc


def _cropped_pixels(full, left, top, width, height):
    stride = full.width * 3
    return b"".join(
        full.pixels[y * stride + left * 3 : y * stride + (left + width) * 3]
        for y in range(top, top + height)
    )


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("antialias", [False, True, 2])
@pytest.mark.parametrize("dpi,scale", [(72, 1), (144, 0.75)])
def test_clip_pixels_match_the_same_area_of_the_full_render(
    document, rotation, antialias, dpi, scale
):
    page = document.pages[0]
    page.rotation = rotation
    options = dict(dpi=dpi, scale=scale, antialias=antialias)
    full = page.render(**options)
    clipped = document.render_page(0, clip=_CLIP, **options)
    factor = dpi / 72 * scale
    left, top, width, height = {
        0: (12, 24, 20, 16),
        90: (10, 12, 16, 20),
        180: (28, 10, 20, 16),
        270: (24, 28, 16, 20),
    }[rotation]
    left, top, width, height = (
        int(value * factor) for value in (left, top, width, height)
    )
    assert (clipped.width, clipped.height) == (width, height)
    assert clipped.dpi == dpi
    assert clipped.pixels == _cropped_pixels(full, left, top, width, height)
    assert page.render(clip=_CLIP, **options).pixels == clipped.pixels


@pytest.mark.parametrize(
    "clip,expected",
    [
        ((-100, -100, 100, 100), _CROP),
        ((-30, 15, 8, 35), (-10, 20, 8, 35)),
        (list(_CLIP), _CLIP),
    ],
)
def test_clip_is_intersected_with_the_effective_crop_box(document, clip, expected):
    raster = document.pages[0].render(clip=clip, antialias=False)
    assert (raster.width, raster.height) == (
        expected[2] - expected[0],
        expected[3] - expected[1],
    )
    assert (
        raster.pixels == document.render_page(0, clip=expected, antialias=False).pixels
    )


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_fractional_clip_dimensions_and_background(document, rotation):
    page = document.pages[0]
    page.rotation = rotation
    raster = page.render(
        clip=(2.25, 30.5, 4.4, 33.75),
        dpi=96,
        scale=1.25,
        antialias=2,
        background=(10, 20, 30),
    )
    size = (math.ceil(2.15 * 96 / 72 * 1.25), math.ceil(3.25 * 96 / 72 * 1.25))
    assert (raster.width, raster.height) == (size[::-1] if rotation % 180 else size)
    with Document() as blank:
        blank.pages.add(size=(10, 10))
        result = blank.render_page(
            0, clip=(2.25, 2.5, 4.4, 5.75), background=(10, 20, 30)
        )
        assert result.pixels == bytes((10, 20, 30)) * result.width * result.height


def test_tiny_clip_uses_its_actual_span_before_pixel_rounding():
    with Document() as doc:
        doc.pages.add(size=(10, 10))
        raster = doc.render_page(0, clip=(0, 0, 1e-9, 2e-9), dpi=72e9, antialias=False)
        assert (raster.width, raster.height) == (1, 2)


@pytest.mark.parametrize(
    "clip",
    [
        (),
        (1, 2, 3),
        (1, 2, 3, 4, 5),
        "1234",
        b"1234",
        7,
        {"x0": 2, "y0": 30, "x1": 22, "y1": 46},
        (True, 30, 22, 46),
        (None, 30, 22, 46),
        (object(), 30, 22, 46),
        (float("nan"), 30, 22, 46),
        (2, 30, float("inf"), 46),
        (2, float("-inf"), 22, 46),
        (2, 30, 2, 46),
        (2, 30, 22, 30),
        (22, 30, 2, 46),
        (2, 46, 22, 30),
        (50, 30, 60, 46),
        (51, 30, 60, 46),
        (2, 0, 22, 20),
    ],
)
def test_invalid_or_empty_clip_is_rejected_before_allocation(
    document, clip, monkeypatch
):
    def unexpected_canvas(*args, **kwargs):
        pytest.fail("An invalid clip must be rejected before allocating a canvas")

    monkeypatch.setattr(rasterizer, "_Canvas", unexpected_canvas)
    with pytest.raises(PdfValidationException, match="clip"):
        document.render_page(0, clip=clip)


def test_clip_does_not_modify_page_boxes_content_or_subsequent_renders(
    document, tmp_path
):
    page = document.pages[0]
    page.rotation = 90
    before = page.render(antialias=False)
    page.render(clip=_CLIP, antialias=False)
    assert page.media_box == _MEDIA
    assert page.crop_box == _CROP
    assert page.rotation == 90
    assert page.render(clip=None, antialias=False) == before
    buffer = BytesIO()
    document.save(buffer)
    path = tmp_path / "unchanged.pdf"
    document.save(path)
    for source in (buffer.getvalue(), path):
        with Document(source) as loaded:
            assert loaded.pages[0].media_box == _MEDIA
            assert loaded.pages[0].crop_box == _CROP
            assert loaded.render_page(0, antialias=False) == before
            assert loaded.render_page(0, clip=_CLIP, antialias=False).pixels == (
                page.render(clip=_CLIP, antialias=False).pixels
            )


@pytest.mark.parametrize("use_document", [False, True])
def test_save_as_image_forwards_clip_and_preserves_existing_files_on_error(
    document, use_document, tmp_path
):
    page = document.pages[0]
    if use_document:

        def save(path, **kwargs):
            return document.save_page_as_image(0, path, **kwargs)
    else:
        save = page.save_as_image
    path = tmp_path / "detail.png"
    assert save(path, clip=_CLIP, scale=2, antialias=False) == path
    data = path.read_bytes()
    assert struct.unpack(">II", data[16:24]) == (40, 32)
    with pytest.raises(PdfValidationException, match="clip"):
        save(path, clip=(100, 100, 200, 200))
    assert path.read_bytes() == data


@pytest.mark.parametrize("antialias,factor", [(False, 1), (True, 3), (2, 2)])
def test_small_clip_of_large_page_allocates_only_the_region(
    monkeypatch, antialias, factor
):
    limits = replace(PdfLoadLimits.unlimited(), max_raster_pixels=900)
    allocations = []
    canvas = rasterizer._Canvas

    def tracked_canvas(width, height, *args, **kwargs):
        allocations.append((width, height))
        return canvas(width, height, *args, **kwargs)

    monkeypatch.setattr(rasterizer, "_Canvas", tracked_canvas)
    with Document(limits=limits) as doc:
        doc.pages.add(size=(100_000, 100_000))
        with pytest.raises(PdfResourceLimitException, match="max_raster_pixels"):
            doc.render_page(0, antialias=antialias)
        assert allocations == []
        result = doc.render_page(
            0, clip=(50_000, 50_000, 50_010, 50_010), antialias=antialias
        )
        assert (result.width, result.height) == (10, 10)
        assert allocations == [(10 * factor, 10 * factor)]
        with pytest.raises(PdfResourceLimitException, match="max_raster_pixels"):
            doc.render_page(0, clip=(0, 0, 40, 40), antialias=antialias)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("draw_annotations", [False, True])
def test_clipped_images_text_and_widget_appearances(rotation, draw_annotations):
    with Document() as doc:
        page = doc.pages.add(size=(60, 40))
        page.rotation = rotation
        page.add_image(
            bytes((255, 0, 0, 0, 0, 255)),
            8,
            8,
            24,
            20,
            pixel_width=2,
            pixel_height=1,
        )
        page.add_text("Hi", 10, 22, font_size=8)
        doc.form.add_text_field(
            "name",
            page,
            (10, 10, 30, 20),
            value="A",
            font_size=8,
            background_color=(0, 1, 0),
        )
        options = dict(antialias=2, draw_annotations=draw_annotations)
        full = page.render(**options)
        clipped = page.render(clip=(8, 8, 32, 28), **options)
        left, top, width, height = {
            0: (8, 12, 24, 20),
            90: (8, 8, 20, 24),
            180: (28, 8, 24, 20),
            270: (12, 28, 20, 24),
        }[rotation]
        assert clipped.pixels == _cropped_pixels(full, left, top, width, height)
        assert len(set(clipped.pixels)) > 1


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("transparency_group", [False, True])
def test_clipped_form_shading_keeps_user_coordinates_and_small_buffers(
    rotation, transparency_group, monkeypatch
):
    def numbers(*values):
        return PdfArray([PdfNumber(value) for value in values])

    shading = PdfDictionary(
        {
            PdfName("ShadingType"): PdfNumber(2),
            PdfName("ColorSpace"): PdfName("DeviceRGB"),
            PdfName("Coords"): numbers(-10, 30, 50, 50),
            PdfName("Function"): PdfDictionary(
                {
                    PdfName("FunctionType"): PdfNumber(2),
                    PdfName("Domain"): numbers(0, 1),
                    PdfName("C0"): numbers(1, 0, 0),
                    PdfName("C1"): numbers(0, 0, 1),
                    PdfName("N"): PdfNumber(1),
                }
            ),
            PdfName("Extend"): PdfArray([PdfBoolean(True), PdfBoolean(True)]),
        }
    )
    with Document() as doc:
        page = doc.pages.add((_MEDIA, b"/Detail Do"))
        page.crop_box = _CROP
        page.rotation = rotation
        pdf = doc._engine_pdf
        mapping = {
            PdfName("Type"): PdfName("XObject"),
            PdfName("Subtype"): PdfName("Form"),
            PdfName("BBox"): numbers(*_MEDIA),
            PdfName("Resources"): PdfDictionary(
                {
                    PdfName("Shading"): PdfDictionary({PdfName("Gradient"): shading}),
                }
            ),
        }
        if transparency_group:
            mapping[PdfName("Group")] = PdfDictionary(
                {
                    PdfName("S"): PdfName("Transparency"),
                    PdfName("I"): PdfBoolean(True),
                    PdfName("CS"): PdfName("DeviceRGB"),
                }
            )
        form = pdf._cos_doc.register_object(PdfStream(b"/Gradient sh", mapping))
        pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
            {
                PdfName("XObject"): PdfDictionary({PdfName("Detail"): form}),
            }
        )
        full = page.render(antialias=2)
        allocations = []
        canvas = rasterizer._Canvas

        def tracked_canvas(width, height, *args, **kwargs):
            allocations.append((width, height))
            return canvas(width, height, *args, **kwargs)

        monkeypatch.setattr(rasterizer, "_Canvas", tracked_canvas)
        clipped = page.render(clip=_CLIP, antialias=2)
        left, top, width, height = {
            0: (12, 24, 20, 16),
            90: (10, 12, 16, 20),
            180: (28, 10, 20, 16),
            270: (24, 28, 16, 20),
        }[rotation]
        assert clipped.pixels == _cropped_pixels(full, left, top, width, height)
        assert len(set(clipped.pixels)) > 2
        assert allocations
        assert all(size == (width * 2, height * 2) for size in allocations)


@pytest.mark.parametrize("suffix", ["tiff", "jpg", "webp"])
@pytest.mark.parametrize("use_document", [False, True])
def test_clipped_region_exports_to_other_image_formats(
    document, tmp_path, suffix, use_document
):
    image = pytest.importorskip("PIL.Image")
    if suffix == "webp":
        features = pytest.importorskip("PIL.features")
        if not features.check("webp"):
            pytest.skip("Pillow has no WebP codec")
    path = tmp_path / f"detail.{suffix}"
    if use_document:
        document.save_page_as_image(0, path, clip=_CLIP, antialias=False)
    else:
        document.pages[0].save_as_image(path, clip=_CLIP, antialias=False)
    with image.open(path) as result:
        assert result.size == (20, 16)
