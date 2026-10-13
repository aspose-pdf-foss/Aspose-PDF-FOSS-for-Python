"""WebP page export through raster, page, document and converter APIs."""

from __future__ import annotations

import io
import sys

import pytest

from aspose_pdf import Document, PdfLoadLimits, RasterizedPage
from aspose_pdf.exceptions import (
    PdfResourceLimitException,
    PdfValidationException,
    UnsupportedFeatureException,
)
from aspose_pdf.facades import PdfConverter
from aspose_pdf.lowcode import (
    ByteArrayDataSource,
    Converter,
    ConvertOptions,
    FileDataSource,
)


@pytest.fixture
def pillow():
    image = pytest.importorskip("PIL.Image")
    features = pytest.importorskip("PIL.features")
    if not features.check("webp"):
        pytest.skip("Pillow was built without WebP")
    return image


def _raster():
    pixels = bytes(
        channel
        for y in range(24)
        for x in range(32)
        for channel in (
            (x * 37 + y * 11) % 256,
            (x * 7 + y * 29) % 256,
            (x * y * 13) % 256,
        )
    )
    return RasterizedPage(32, 24, pixels)


def _pdf():
    with Document() as document:
        for color in ((1, 0, 0), (0, 1, 0), (0, 0, 1)):
            page = document.pages.add(size=(8, 6))
            page.draw_rectangle(0, 0, 8, 6, fill_color=color, stroke_color=None)
        output = io.BytesIO()
        document.save(output)
        return output.getvalue()


def _decoded(pillow, data):
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    with pillow.open(io.BytesIO(data)) as image:
        assert image.format == "WEBP"
        with image.convert("RGB") as rgb:
            return rgb.size, rgb.tobytes()


@pytest.mark.parametrize("quality", [0, 85.5, 100])
@pytest.mark.parametrize("method", [0, 6])
def test_lossless_webp_preserves_every_rgb_pixel(pillow, quality, method):
    raster = _raster()
    data = raster.to_webp(lossless=True, quality=quality, method=method)
    assert data[12:16] == b"VP8L"
    size, pixels = _decoded(pillow, data)
    assert size == (raster.width, raster.height)
    assert pixels == raster.pixels


def test_default_webp_is_lossy_and_quality_controls_compression(pillow):
    raster = _raster()
    default = raster.to_webp()
    low = raster.to_webp(quality=10)
    high = raster.to_webp(quality=95)
    assert default[12:16] == b"VP8 "
    size, pixels = _decoded(pillow, default)
    assert size == (32, 24)
    assert len(pixels) == len(raster.pixels)
    assert len(low) < len(high)


@pytest.mark.parametrize("mode", ["gray", "grey", "grayscale", "L"])
def test_gray_webp_uses_existing_luminance_conversion(pillow, mode):
    raster = RasterizedPage(3, 1, bytes([255, 0, 0, 0, 255, 0, 0, 0, 255]))
    size, pixels = _decoded(pillow, raster.to_webp(mode=mode, lossless=True))
    assert size == (3, 1)
    assert pixels == bytes([76] * 3 + [149] * 3 + [29] * 3)


def test_bilevel_webp_honors_threshold_and_partial_byte_rows(pillow):
    raster = RasterizedPage(3, 2, bytes([32] * 3 + [96] * 3 + [255] * 3) * 2)
    size, pixels = _decoded(
        pillow, raster.to_webp(mode="bilevel", threshold=64, lossless=True)
    )
    assert size == (3, 2)
    assert pixels == (bytes([0] * 3 + [255] * 6) * 2)


@pytest.mark.parametrize("suffix", [".webp", ".WEBP"])
def test_raster_save_dispatches_webp_and_creates_parent_directories(
    pillow, tmp_path, suffix
):
    raster = _raster()
    path = tmp_path / "nested" / f"page{suffix}"
    assert raster.save(path, lossless=True, quality=100, method=6) == path
    assert _decoded(pillow, path.read_bytes())[1] == raster.pixels


def test_page_and_document_save_webp_with_render_options(pillow, tmp_path):
    with Document(io.BytesIO(_pdf())) as document:
        page = document.pages[0].save_as_image(
            tmp_path / "red.webp",
            dpi=144,
            antialias=False,
            mode="gray",
            lossless=True,
            quality=0,
            method=0,
        )
        whole = document.save_page_as_image(
            2,
            tmp_path / "blue.webp",
            dpi=144,
            antialias=False,
            lossless=True,
            quality=100,
            method=6,
        )
    assert _decoded(pillow, page.read_bytes()) == (
        (16, 12),
        bytes([76] * (16 * 12 * 3)),
    )
    assert _decoded(pillow, whole.read_bytes()) == (
        (16, 12),
        bytes([0, 0, 255] * (16 * 12)),
    )


def test_lossless_rendered_page_matches_png_pixels(pillow):
    with Document() as document:
        page = document.pages.add(size=(40, 30))
        page.add_text("WebP", 2, 10, font_size=8)
        page.draw_rectangle(1, 1, 8, 6, fill_color=(0, 0, 1))
        raster = page.render()
        assert _decoded(pillow, raster.to_webp(lossless=True))[1] == raster.pixels


def test_lowcode_converter_preserves_selection_source_order_and_outputs(
    pillow, tmp_path
):
    options = ConvertOptions(
        ".WebP", pages=[2, 0], dpi=144, lossless=True, quality=100, method=6
    )
    options.add_input(ByteArrayDataSource(_pdf())).add_input(
        ByteArrayDataSource(_pdf())
    )
    paths = [tmp_path / f"page{index}.webp" for index in range(4)]
    for path in paths:
        options.add_output(FileDataSource(path))
    results = Converter().process(options)
    assert len(results) == 4
    for index, result in enumerate(results):
        assert not result.is_string()
        assert paths[index].read_bytes() == result.to_array()
        size, pixels = _decoded(pillow, result.to_array())
        assert size == (16, 12)
        color = [0, 0, 255] if index % 2 == 0 else [255, 0, 0]
        assert pixels == bytes(color * (16 * 12))


@pytest.mark.parametrize("spelling", ["webp", ".WEBP", " WebP "])
def test_convert_options_accept_webp(spelling):
    assert ConvertOptions(spelling).output_format == "webp"


def test_facade_webp_uses_one_based_page_selection_and_resets_results(pillow):
    facade = PdfConverter()
    try:
        facade.bind_pdf(io.BytesIO(_pdf()))
        facade.resolution = 144
        assert facade.do_convert(
            [3, 1], image_format=".WEBP", lossless=True, quality=100, method=6
        )
        for color in ([0, 0, 255], [255, 0, 0]):
            assert facade.has_next_image()
            assert _decoded(pillow, facade.get_next_image()) == (
                (16, 12),
                bytes(color * (16 * 12)),
            )
        assert not facade.has_next_image()
        assert facade.get_next_image() is None
        assert facade.do_convert(
            [2], image_format="webp", mode="gray", lossless=True, method=0
        )
        assert _decoded(pillow, facade.get_next_image())[1] == bytes(
            [149] * (16 * 12 * 3)
        )
    finally:
        facade.dispose()


@pytest.mark.parametrize(
    "quality", [-1, 101, float("nan"), float("inf"), True, None, "bad"]
)
def test_invalid_webp_quality_is_rejected(quality):
    with pytest.raises(PdfValidationException, match="WebP quality"):
        _raster().to_webp(quality=quality)


@pytest.mark.parametrize("method", [-1, 7, 3.5, True, "4", None])
def test_invalid_webp_method_is_rejected(method):
    with pytest.raises(PdfValidationException, match="WebP method"):
        _raster().to_webp(method=method)


@pytest.mark.parametrize("lossless", [None, 0, "yes"])
def test_invalid_lossless_flag_is_rejected(lossless):
    with pytest.raises(PdfValidationException, match="WebP lossless"):
        _raster().to_webp(lossless=lossless)


def test_unknown_webp_color_mode_is_rejected():
    with pytest.raises(PdfValidationException, match="Unsupported raster colour mode"):
        _raster().to_webp(mode="cmyk")


@pytest.mark.parametrize(
    "width,height", [(0, 1), (1, 0), (16384, 1), (1, 16384), (True, 1)]
)
def test_webp_dimension_limits_are_checked_before_native_allocation(width, height):
    with pytest.raises(PdfValidationException, match="WebP dimensions"):
        RasterizedPage(width, height, b"").to_webp()


def test_manually_constructed_raster_obeys_default_pixel_limit():
    with pytest.raises(PdfResourceLimitException, match="max_raster_pixels"):
        RasterizedPage(16383, 16383, b"").to_webp()


@pytest.mark.parametrize("pixels", [b"", bytes(4)])
def test_webp_rejects_a_malformed_sample_buffer(pixels):
    with pytest.raises(PdfValidationException, match="sample buffer"):
        RasterizedPage(1, 1, pixels).to_webp()


def test_webp_inherits_document_work_limit_before_pillow_allocates(pillow, monkeypatch):
    with Document(limits=PdfLoadLimits(max_codec_work_bytes=1000)) as document:
        raster = document.pages.add(size=(10, 8)).render(antialias=False)

    def unexpected_allocation(*args, **kwargs):
        raise AssertionError("Pillow allocated before the work-limit check")

    monkeypatch.setattr(pillow, "frombytes", unexpected_allocation)
    with pytest.raises(PdfResourceLimitException, match="max_codec_work_bytes"):
        raster.to_webp()


def test_webp_requires_optional_pillow_and_keeps_core_encoders_working(monkeypatch):
    monkeypatch.setitem(sys.modules, "PIL", None)
    with pytest.raises(UnsupportedFeatureException, match="install the 'images' extra"):
        _raster().to_webp()
    assert _raster().to_png().startswith(b"\x89PNG")
    assert _raster().to_jpeg().startswith(b"\xff\xd8")
    assert _raster().to_tiff().startswith(b"II")


def test_pillow_without_webp_reports_the_missing_codec(monkeypatch):
    pytest.importorskip("PIL.Image")
    features = pytest.importorskip("PIL.features")
    monkeypatch.setattr(features, "check", lambda feature: False)
    with pytest.raises(
        UnsupportedFeatureException, match="Pillow build with WebP support"
    ):
        _raster().to_webp()


def test_failed_webp_export_preserves_the_existing_file(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "PIL", None)
    path = tmp_path / "page.webp"
    path.write_bytes(b"original")
    with pytest.raises(UnsupportedFeatureException):
        _raster().save(path)
    assert path.read_bytes() == b"original"


def test_lowcode_dependency_failure_does_not_write_outputs(monkeypatch, tmp_path):
    data = _pdf()
    path = tmp_path / "page.webp"
    path.write_bytes(b"original")
    monkeypatch.setitem(sys.modules, "PIL", None)
    with pytest.raises(UnsupportedFeatureException):
        Converter().process(
            ConvertOptions("webp", pages=[0])
            .add_input(ByteArrayDataSource(data))
            .add_output(FileDataSource(path))
        )
    assert path.read_bytes() == b"original"


def test_facade_records_webp_dependency_failure_and_can_convert_again(monkeypatch):
    facade = PdfConverter()
    try:
        facade.bind_pdf(io.BytesIO(_pdf()))
        monkeypatch.setitem(sys.modules, "PIL", None)
        assert facade.do_convert(image_format="webp") is False
        assert isinstance(facade.last_exception, UnsupportedFeatureException)
        assert not facade.has_next_image()
        assert facade.do_convert(image_format="png") is True
        assert facade.last_exception is None
    finally:
        facade.dispose()


def test_native_webp_encoder_failure_has_a_library_exception(pillow, monkeypatch):
    def failed_save(*args, **kwargs):
        raise OSError("synthetic encoder failure")

    monkeypatch.setattr(pillow.Image, "save", failed_save)
    with pytest.raises(
        PdfValidationException, match=r"Cannot encode WebP.*synthetic encoder failure"
    ):
        _raster().to_webp()
