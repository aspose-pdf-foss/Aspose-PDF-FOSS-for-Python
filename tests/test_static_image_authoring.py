"""Static BMP/GIF/WebP assets can be placed and stamped without conversion."""

from __future__ import annotations

import io
import struct
import sys

import pytest

from aspose_pdf import (
    Document,
    HorizontalAlignment,
    ImageStamp,
    PdfLoadLimits,
    VerticalAlignment,
)
from aspose_pdf.engine.image_export import write_png
from aspose_pdf.exceptions import (
    PdfResourceLimitException,
    PdfValidationException,
    UnsupportedFeatureException,
)


@pytest.fixture
def pillow():
    return pytest.importorskip("PIL.Image")


def _image(pillow, kind, *, mode="RGB", color=(20, 40, 60), **options):
    if kind == "WEBP":
        if not pytest.importorskip("PIL.features").check("webp"):
            pytest.skip("Pillow was built without WebP")
        options.setdefault("lossless", True)
    with pillow.new(mode, (6, 4), color) as image, io.BytesIO() as output:
        image.save(output, format=kind, **options)
        return output.getvalue()


def _place(page, source, entrypoint, width=None, height=None):
    if entrypoint == "page":
        return page.add_image(source, 2, 3, width, height)
    page.add_stamp(
        ImageStamp(
            source, width=width, height=height,
            horizontal_alignment=HorizontalAlignment.LEFT,
            vertical_alignment=VerticalAlignment.BOTTOM,
            x_indent=2, y_indent=3,
        )
    )


@pytest.mark.parametrize("entrypoint", ["page", "stamp"])
@pytest.mark.parametrize("kind", ["BMP", "GIF", "WEBP"])
@pytest.mark.parametrize("source", ["bytes", "bytearray", "path"])
@pytest.mark.parametrize("scaled", [False, True])
def test_images_keep_size_colors_and_placement_after_save(
    pillow, entrypoint, kind, source, scaled, tmp_path,
):
    data = _image(pillow, kind)
    asset = tmp_path / "misnamed.jpg"
    asset.write_bytes(data)
    value = {"bytes": data, "bytearray": bytearray(data), "path": asset}[source]
    width, height = (12, 8) if scaled else (6, 4)
    with Document() as document:
        page = document.pages.add(size=(24, 20))
        result = _place(page, value, entrypoint, width if scaled else None,
                        height if scaled else None)
        if entrypoint == "page":
            assert isinstance(result, str) and result
        raster = page.render(antialias=False)
        for y in range(20):
            for x in range(24):
                expected = (
                    (20, 40, 60)
                    if 2 <= x < 2 + width and 17 - height <= y < 17
                    else (255, 255, 255)
                )
                assert raster.get_pixel(x, y) == expected
        with io.BytesIO() as output:
            document.save(output)
            with Document(output.getvalue()) as reopened:
                assert reopened.pages[0].render(antialias=False).pixels == raster.pixels
        output_path = tmp_path / "output.pdf"
        document.save(output_path)
        with Document(output_path) as reopened:
            assert reopened.pages[0].render(antialias=False).pixels == raster.pixels


def _alpha_bmp():
    """Two V4 pixels with explicit alpha: transparent red and half-opaque red."""
    header = bytearray(108)
    struct.pack_into("<IiiHHII", header, 0, 108, 2, 1, 1, 32, 3, 8)
    struct.pack_into("<IIII", header, 40, 0xFF0000, 0xFF00, 0xFF, 0xFF000000)
    pixels = bytes([0, 0, 255, 0, 0, 0, 255, 128])
    return b"BM" + struct.pack("<IHHI", 122 + len(pixels), 0, 0, 122) + header + pixels


@pytest.mark.parametrize("entrypoint", ["page", "stamp"])
@pytest.mark.parametrize("kind", ["BMP", "GIF", "WEBP", "WEBP_LOSSY"])
def test_transparency_matches_png_after_round_trip(pillow, entrypoint, kind):
    if kind == "BMP":
        data = _alpha_bmp()
    elif kind.startswith("WEBP"):
        data = _image(pillow, "WEBP", mode="RGBA", color=(255, 0, 0, 128),
                      lossless=kind == "WEBP")
    else:
        with pillow.new("P", (6, 4), 1) as image, io.BytesIO() as output:
            image.putpalette([0, 0, 0, 255, 0, 0] + [0] * 762)
            image.putpixel((0, 0), 0)
            image.save(output, format="GIF", transparency=0)
            data = output.getvalue()
    with pillow.open(io.BytesIO(data)) as image, image.convert("RGBA") as rgba:
        png = write_png(*rgba.size, "RGBA", rgba.tobytes())
    rendered = []
    for payload in (data, png):
        with Document() as document, io.BytesIO() as output:
            page = document.pages.add(size=(24, 20))
            _place(page, payload, entrypoint, 12, 8)
            document.save(output)
            with Document(output.getvalue()) as reopened:
                rendered.append(reopened.pages[0].render(
                    background=(0, 255, 0), antialias=False,
                ).pixels)
    assert rendered[0] == rendered[1]


@pytest.mark.parametrize("entrypoint", ["page", "stamp"])
@pytest.mark.parametrize("kind", ["GIF", "WEBP"])
def test_animation_is_rejected_before_decoding(pillow, monkeypatch, entrypoint, kind):
    _image(pillow, kind)
    with pillow.new("RGB", (6, 4), "red") as first:
        with pillow.new("RGB", (6, 4), "blue") as second, io.BytesIO() as output:
            first.save(output, format=kind, save_all=True, append_images=[second])
            data = output.getvalue()

    def forbidden(*args, **kwargs):
        pytest.fail("Animated images must be rejected before decoder construction")

    monkeypatch.setattr(pillow, "open", forbidden)
    with Document() as document:
        page = document.pages.add()
        before = page.content
        with pytest.raises(UnsupportedFeatureException, match="Animated"):
            _place(page, data, entrypoint)
        assert page.content == before


@pytest.mark.parametrize("entrypoint", ["page", "stamp"])
@pytest.mark.parametrize("kind", ["BMP", "GIF", "WEBP"])
@pytest.mark.parametrize("name,value", [
    ("max_input_bytes", 10), ("max_image_pixels", 20),
    ("max_decoded_stream_bytes", 99), ("max_codec_work_bytes", 1000),
    ("max_total_decoded_bytes", 95),
])
def test_limits_are_checked_before_decoding(
    pillow, monkeypatch, entrypoint, kind, name, value,
):
    data = _image(pillow, kind)

    def forbidden(*args, **kwargs):
        pytest.fail("Resource limits must be checked before decoder construction")

    monkeypatch.setattr(pillow, "open", forbidden)
    with Document(limits=PdfLoadLimits(**{name: value})) as document:
        page = document.pages.add()
        before = page.content
        with pytest.raises(PdfResourceLimitException, match=name):
            _place(page, data, entrypoint)
        assert page.content == before


@pytest.mark.parametrize("entrypoint", ["page", "stamp"])
def test_decoded_byte_limit_accumulates_across_assets(
    pillow, monkeypatch, entrypoint,
):
    first = _image(pillow, "BMP")
    second = _image(pillow, "GIF")
    original = pillow.open
    opened = []

    def guarded(*args, **kwargs):
        opened.append(kwargs.get("formats"))
        assert len(opened) == 1, "The second asset must be rejected before decoding"
        return original(*args, **kwargs)

    monkeypatch.setattr(pillow, "open", guarded)
    with Document(limits=PdfLoadLimits(max_total_decoded_bytes=250)) as document:
        page = document.pages.add()
        _place(page, first, entrypoint)
        with pytest.raises(PdfResourceLimitException, match="max_total_decoded_bytes"):
            _place(page, second, entrypoint)
    assert opened == [("BMP",)]


@pytest.mark.parametrize("entrypoint", ["page", "stamp"])
@pytest.mark.parametrize("data", [b"BM", b"GIF89a", b"RIFF\x00\x00\x00\x00WEBP"])
def test_missing_pillow_reports_optional_extra(monkeypatch, entrypoint, data):
    monkeypatch.setitem(sys.modules, "PIL", None)
    with Document() as document:
        page = document.pages.add()
        with pytest.raises(UnsupportedFeatureException, match="install the 'images' extra"):
            _place(page, data, entrypoint)


@pytest.mark.parametrize("entrypoint", ["page", "stamp"])
def test_missing_webp_codec_reports_clear_error(pillow, monkeypatch, entrypoint):
    features = pytest.importorskip("PIL.features")
    monkeypatch.setattr(features, "check", lambda name: False)
    with Document() as document:
        page = document.pages.add()
        with pytest.raises(UnsupportedFeatureException, match="build with WebP support"):
            _place(page, b"RIFF\x00\x00\x00\x00WEBP", entrypoint)


@pytest.mark.parametrize("entrypoint", ["page", "stamp"])
@pytest.mark.parametrize("kind", ["BMP", "GIF", "WEBP"])
def test_truncated_images_raise_validation_errors(pillow, entrypoint, kind):
    data = _image(pillow, kind)
    with Document() as document:
        page = document.pages.add()
        with pytest.raises(PdfValidationException, match="Cannot decode"):
            _place(page, data[:20], entrypoint)


@pytest.mark.parametrize("signature", [b"BM", b"GIF89a", b"RIFFxxxxWEBP"])
def test_explicit_raw_samples_keep_signature_like_prefixes(monkeypatch, signature):
    monkeypatch.setitem(sys.modules, "PIL", None)
    samples = signature.ljust(12, b"\x00")
    with Document() as document:
        page = document.pages.add(size=(4, 1))
        page.add_image(samples, 0, 0, pixel_width=4, pixel_height=1)
        assert page.render(antialias=False).pixels == samples


@pytest.mark.parametrize("entrypoint", ["page", "stamp"])
def test_jpeg_and_png_still_work_without_pillow(monkeypatch, entrypoint):
    from aspose_pdf.engine.jpeg_encoder import encode

    pixels = bytes([255, 0, 0] * 4)
    png = write_png(2, 2, "RGB", pixels)
    jpeg = encode(2, 2, 3, pixels)
    monkeypatch.setitem(sys.modules, "PIL", None)
    with Document() as document:
        page = document.pages.add(size=(24, 20))
        for data in (png, jpeg):
            _place(page, data, entrypoint)
        red, green, blue = page.render(antialias=False).get_pixel(2, 16)
        assert red >= 250 and green <= 3 and blue <= 3
