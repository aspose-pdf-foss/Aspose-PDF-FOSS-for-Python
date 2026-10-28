"""Static WebP/BMP/GIF inputs compose with ImagesToPdf without losing alpha."""

from __future__ import annotations

import io
import struct
import sys

import pytest

from aspose_pdf import Document, PageSize, PdfLoadLimits
from aspose_pdf.engine.image_export import write_png
from aspose_pdf.exceptions import (
    PdfResourceLimitException,
    PdfValidationException,
    UnsupportedFeatureException,
)
from aspose_pdf.images import ImagePlacementAbsorber
from aspose_pdf.lowcode import (
    ByteArrayDataSource,
    FileDataSource,
    ImagesToPdf,
    ImagesToPdfOptions,
    StreamDataSource,
)


@pytest.fixture
def pillow():
    return pytest.importorskip("PIL.Image")


def _image(pillow, kind, *, mode="RGB", size=(6, 4), color=(20, 40, 60), **options):
    if kind == "WEBP" and not pytest.importorskip("PIL.features").check("webp"):
        pytest.skip("Pillow was built without WebP")
    with pillow.new(mode, size, color) as image, io.BytesIO() as output:
        if kind == "WEBP":
            options.setdefault("lossless", True)
        image.save(output, format=kind, **options)
        return output.getvalue()


def _convert(data, **options):
    return ImagesToPdf().process(
        ImagesToPdfOptions(**options).add_input(ByteArrayDataSource(data))
    )


@pytest.mark.parametrize("kind", ["BMP", "GIF", "WEBP"])
@pytest.mark.parametrize("source", ["bytes", "file", "stream"])
def test_imports_formats_by_signature_and_preserves_pixels(
    pillow, kind, source, tmp_path
):
    data = _image(pillow, kind)
    path = tmp_path / "misnamed.jpg"
    path.write_bytes(data)
    with io.BytesIO(data) as stream:
        inputs = {
            "bytes": ByteArrayDataSource(data),
            "file": FileDataSource(path),
            "stream": StreamDataSource(stream),
        }
        result = ImagesToPdf().process(ImagesToPdfOptions().add_input(inputs[source]))
        assert not stream.closed
    with Document(result[0].to_array()) as document:
        assert document.page_count == 1
        assert document.pages[0].size == PageSize(6, 4)
        assert document.pages[0].render(antialias=False).get_pixel(1, 1) == (20, 40, 60)
        output = tmp_path / f"{kind}.pdf"
        document.save(output)
        with Document(output) as reopened:
            assert (
                reopened.pages[0].render().pixels == document.pages[0].render().pixels
            )


@pytest.mark.parametrize("kind", ["BMP", "GIF", "WEBP"])
def test_image_sizing_dpi_and_fixed_page_margins(pillow, kind):
    data = _image(pillow, kind, size=(60, 30))
    with Document(_convert(data, dpi=144)[0].to_array()) as document:
        assert document.pages[0].size == PageSize(30, 15)
    with Document(
        _convert(data, page_size=(100, 100), margin=10)[0].to_array()
    ) as document:
        absorber = ImagePlacementAbsorber()
        absorber.visit(document.pages[0])
        rect = absorber.image_placements[0].rectangle
        assert (rect.x, rect.y, rect.width, rect.height) == pytest.approx(
            (20, 35, 60, 30)
        )


@pytest.mark.parametrize("single", [True, False])
def test_mixed_inputs_preserve_order_with_tiff_frames(pillow, single):
    options = ImagesToPdfOptions(single_document=single)
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (0, 0, 0), (255, 255, 255)]
    for kind, color in zip(["BMP", "GIF", "WEBP"], colors[:3], strict=True):
        options.add_input(ByteArrayDataSource(_image(pillow, kind, color=color)))
    with pillow.new("RGB", (6, 4), colors[3]) as first:
        with pillow.new("RGB", (6, 4), colors[4]) as second, io.BytesIO() as output:
            first.save(output, format="TIFF", save_all=True, append_images=[second])
            options.add_input(ByteArrayDataSource(output.getvalue()))
    results = ImagesToPdf().process(options)
    assert len(results) == (1 if single else 5)
    pixels = []
    for result in results:
        with Document(result.to_array()) as document:
            pixels.extend(page.render().get_pixel(1, 1) for page in document.pages)
    assert pixels == colors


def _alpha_bmp():
    """A minimal V4 bitmap with an explicit alpha bitfield."""
    header = bytearray(108)
    struct.pack_into("<IiiHHII", header, 0, 108, 2, 1, 1, 32, 3, 8)
    struct.pack_into("<IIII", header, 40, 0xFF0000, 0xFF00, 0xFF, 0xFF000000)
    pixels = bytes([0, 0, 255, 0, 0, 0, 255, 128])
    return (
        b"BM"
        + struct.pack("<IHHI", 14 + len(header) + len(pixels), 0, 0, 122)
        + header
        + pixels
    )


@pytest.mark.parametrize("kind", ["BMP", "GIF", "WEBP", "WEBP_LOSSY"])
def test_transparency_matches_png_authoring(pillow, kind):
    if kind == "BMP":
        data = _alpha_bmp()
    elif kind.startswith("WEBP"):
        data = _image(
            pillow, "WEBP", mode="RGBA", color=(255, 0, 0, 128), lossless=kind == "WEBP"
        )
    else:
        with pillow.new("P", (6, 4), 1) as image, io.BytesIO() as output:
            image.putpalette([0, 0, 0, 255, 0, 0] + [0] * 762)
            image.putpixel((0, 0), 0)
            image.save(output, format="GIF", transparency=0)
            data = output.getvalue()
    with pillow.open(io.BytesIO(data)) as image, image.convert("RGBA") as converted:
        width, height = converted.size
        png = write_png(width, height, "RGBA", converted.tobytes())
    with Document(_convert(data)[0].to_array()) as actual:
        with Document(_convert(png)[0].to_array()) as reference:
            raster = actual.pages[0].render(background=(0, 255, 0), antialias=False)
            expected = reference.pages[0].render(
                background=(0, 255, 0), antialias=False
            )
            assert raster.pixels == expected.pixels
            if kind in ("BMP", "GIF"):
                assert raster.get_pixel(0, 0) == (0, 255, 0)


def test_lossy_webp_preserves_decoded_colors(pillow):
    data = _image(pillow, "WEBP", color=(20, 100, 200), lossless=False, quality=50)
    with pillow.open(io.BytesIO(data)) as image, image.convert("RGB") as reference:
        expected = reference.getpixel((1, 1))
    with Document(_convert(data)[0].to_array()) as document:
        assert document.pages[0].render().get_pixel(1, 1) == expected


@pytest.mark.parametrize("kind", ["GIF", "WEBP"])
def test_animation_is_refused_before_pillow_opens_it(pillow, kind, monkeypatch):
    if kind == "WEBP" and not pytest.importorskip("PIL.features").check("webp"):
        pytest.skip("Pillow was built without WebP")
    with pillow.new("RGB", (6, 4), "red") as first:
        with pillow.new("RGB", (6, 4), "blue") as second, io.BytesIO() as output:
            first.save(
                output, format=kind, save_all=True, append_images=[second], duration=100
            )
            data = output.getvalue()

    def forbidden(*args, **kwargs):
        pytest.fail("Animations must be refused before opening a decoder")

    monkeypatch.setattr(pillow, "open", forbidden)
    with pytest.raises(UnsupportedFeatureException, match="Animated"):
        _convert(data)


@pytest.mark.parametrize("data", [b"BM", b"GIF89a", b"RIFF\x00\x00\x00\x00WEBP"])
def test_new_formats_require_optional_pillow(monkeypatch, data):
    monkeypatch.setitem(sys.modules, "PIL", None)
    with pytest.raises(UnsupportedFeatureException, match="install the 'images' extra"):
        _convert(data)


def test_missing_webp_codec_has_a_clear_error(pillow, monkeypatch):
    features = pytest.importorskip("PIL.features")
    monkeypatch.setattr(features, "check", lambda name: False)
    with pytest.raises(UnsupportedFeatureException, match="build with WebP support"):
        _convert(b"RIFF\x00\x00\x00\x00WEBP")


@pytest.mark.parametrize("kind", ["BMP", "GIF", "WEBP"])
@pytest.mark.parametrize(
    "name,value",
    [
        ("max_image_pixels", 20),
        ("max_decoded_stream_bytes", 99),
        ("max_codec_work_bytes", 1000),
        ("max_total_decoded_bytes", 95),
    ],
)
def test_limits_are_checked_before_opening_pillow(
    pillow, monkeypatch, kind, name, value
):
    data = _image(pillow, kind)

    def forbidden(*args, **kwargs):
        pytest.fail("Limits must be checked before opening a decoder")

    monkeypatch.setattr(pillow, "open", forbidden)
    with pytest.raises(PdfResourceLimitException, match=name):
        _convert(data, limits=PdfLoadLimits(**{name: value}))


@pytest.mark.parametrize("single", [True, False])
@pytest.mark.parametrize(
    "name,value", [("max_pages", 1), ("max_total_decoded_bytes", 150)]
)
def test_limits_accumulate_across_separate_inputs(
    pillow, monkeypatch, single, name, value
):
    options = ImagesToPdfOptions(
        single_document=single, limits=PdfLoadLimits(**{name: value})
    )
    options.add_input(ByteArrayDataSource(_image(pillow, "BMP")))
    options.add_input(ByteArrayDataSource(_image(pillow, "GIF")))
    original = pillow.open

    def guarded(*args, **kwargs):
        assert kwargs.get("formats") != ("GIF",), "Second image must not be opened"
        return original(*args, **kwargs)

    monkeypatch.setattr(pillow, "open", guarded)
    with pytest.raises(PdfResourceLimitException, match=name):
        ImagesToPdf().process(options)


@pytest.mark.parametrize("kind", ["BMP", "GIF", "WEBP"])
@pytest.mark.parametrize("limit", [10, 20])
def test_pillow_pixel_limits_are_reported_as_resource_errors(
    pillow, monkeypatch, kind, limit
):
    data = _image(pillow, kind)
    monkeypatch.setattr(pillow, "MAX_IMAGE_PIXELS", limit)
    with pytest.raises(PdfResourceLimitException, match="Pillow's pixel safety limit"):
        _convert(data, limits=PdfLoadLimits.unlimited())


@pytest.mark.parametrize("data", [b"BM", b"GIF89a", b"RIFF\x00\x00\x00\x00WEBP"])
def test_truncated_headers_raise_library_errors(pillow, data):
    if data[8:12] == b"WEBP" and not pytest.importorskip("PIL.features").check("webp"):
        pytest.skip("Pillow was built without WebP")
    with pytest.raises(PdfValidationException, match="Cannot decode"):
        _convert(data)


def test_failed_later_image_does_not_write_partial_results(pillow, tmp_path):
    path = tmp_path / "output.pdf"
    path.write_bytes(b"original")
    output = ByteArrayDataSource(b"original")
    options = ImagesToPdfOptions(single_document=False)
    options.add_input(ByteArrayDataSource(_image(pillow, "BMP")))
    options.add_input(ByteArrayDataSource(b"GIF89a"))
    options.add_output(FileDataSource(path)).add_output(output)
    with pytest.raises(PdfValidationException):
        ImagesToPdf().process(options)
    assert path.read_bytes() == output.read_bytes() == b"original"


def test_png_and_jpeg_still_import_without_pillow(monkeypatch):
    from aspose_pdf.engine.jpeg_encoder import encode

    png = write_png(2, 2, "RGB", bytes([0, 0, 255] * 4))
    jpeg = encode(2, 2, 3, bytes([255, 0, 0] * 4))
    monkeypatch.setitem(sys.modules, "PIL", None)
    options = ImagesToPdfOptions()
    options.add_input(ByteArrayDataSource(png)).add_input(ByteArrayDataSource(jpeg))
    with Document(ImagesToPdf().process(options)[0].to_array()) as document:
        assert document.page_count == 2


@pytest.mark.parametrize("mode,color", [("1", 1), ("L", 100), ("P", 1)])
def test_bmp_bilevel_grayscale_and_palette_modes(pillow, mode, color):
    with pillow.new(mode, (6, 4), color) as image, io.BytesIO() as output:
        if mode == "P":
            image.putpalette([0, 0, 0, 20, 40, 60] + [0] * 762)
        with image.convert("RGB") as converted:
            expected = converted.getpixel((1, 1))
        image.save(output, format="BMP")
        data = output.getvalue()
    with Document(_convert(data)[0].to_array()) as document:
        assert document.pages[0].render().get_pixel(1, 1) == expected


def test_bmp_top_down_and_core_headers(pillow):
    header = bytearray(_image(pillow, "BMP"))
    struct.pack_into("<i", header, 22, -4)
    core = b"BM" + struct.pack("<IHHI", 30, 0, 0, 26)
    core += struct.pack("<IHHHH", 12, 1, 1, 1, 24) + bytes([60, 40, 20, 0])
    for data in (header, core):
        with Document(_convert(bytes(data))[0].to_array()) as document:
            assert document.pages[0].render().get_pixel(0, 0) == (20, 40, 60)


@pytest.mark.parametrize("kind", ["BMP", "GIF", "WEBP"])
def test_input_limit_is_checked_before_opening_a_decoder(pillow, monkeypatch, kind):
    data = _image(pillow, kind)

    def forbidden(*args, **kwargs):
        pytest.fail("Input bytes must be bounded before a decoder is opened")

    monkeypatch.setattr(pillow, "open", forbidden)
    with pytest.raises(PdfResourceLimitException, match="max_input_bytes"):
        _convert(data, limits=PdfLoadLimits(max_input_bytes=len(data) - 1))


@pytest.mark.parametrize("kind", ["BMP", "GIF", "WEBP"])
def test_pillow_decode_errors_are_library_exceptions(pillow, monkeypatch, kind):
    data = _image(pillow, kind)

    def broken(*args, **kwargs):
        raise OSError("Damaged pixels")

    monkeypatch.setattr(pillow, "open", broken)
    with pytest.raises(PdfValidationException, match=r"Cannot decode.*Damaged pixels"):
        _convert(data)


def test_large_gif_frame_extent_is_bounded_before_opening_pillow(pillow, monkeypatch):
    data = bytearray(_image(pillow, "GIF"))
    position = 13 + (3 << ((data[10] & 7) + 1))
    assert data[position] == 0x2C
    struct.pack_into("<HH", data, position + 5, 60000, 60000)

    def forbidden(*args, **kwargs):
        pytest.fail("GIF frame extents must be bounded before decoder construction")

    monkeypatch.setattr(pillow, "open", forbidden)
    with pytest.raises(PdfResourceLimitException, match="max_image_pixels"):
        _convert(bytes(data))


def test_webp_canvas_and_bitstream_must_agree_before_opening_pillow(
    pillow, monkeypatch
):
    data = _image(pillow, "WEBP")
    canvas = b"VP8X" + struct.pack("<I", 10) + bytes(10)
    data = (
        data[:4]
        + struct.pack("<I", len(data) - 8 + len(canvas))
        + data[8:12]
        + canvas
        + data[12:]
    )

    def forbidden(*args, **kwargs):
        pytest.fail("Conflicting WebP dimensions must fail before decoder construction")

    monkeypatch.setattr(pillow, "open", forbidden)
    with pytest.raises(PdfValidationException, match="Cannot decode WebP"):
        _convert(data)


@pytest.mark.parametrize("kind", ["GIF", "WEBP"])
def test_truncated_image_blocks_are_refused_before_decoding(pillow, kind):
    data = _image(pillow, kind)
    with pytest.raises(PdfValidationException, match="Cannot decode"):
        _convert(data[:-1])
