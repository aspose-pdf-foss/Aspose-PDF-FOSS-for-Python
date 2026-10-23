"""Gray and packed bilevel PDF copies preserve the selected page views."""

from __future__ import annotations

import hashlib
import io
import tracemalloc
import weakref

import pytest

from aspose_pdf import (
    Document,
    PdfLoadLimits,
    PdfResourceLimitException,
    RasterizedPage,
)
from aspose_pdf.engine import rasterized_copy
from aspose_pdf.engine.cos import PdfName, PdfStream
from aspose_pdf.exceptions import AsposePdfException, PdfValidationException


def _bytes(document):
    with io.BytesIO() as output:
        document.save(output)
        return output.getvalue()


def _images(document):
    return [
        obj for obj in document._engine_pdf._cos_doc.objects.values()
        if isinstance(obj, PdfStream) and obj.mapping.get(PdfName("Subtype")) == PdfName("Image")
    ]


def _samples(document, image):
    return document._engine_pdf._decode_cos_stream(image)


def _scan(document, width, height, pixels):
    page = document.pages.add(size=(width, height))
    page.add_image(RasterizedPage(width, height, pixels).to_png(), 0, 0, width=width, height=height)
    return page


def test_gray_pdf_contains_one_component_luminance_and_survives_round_trips(tmp_path):
    colors = ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255), (128, 128, 128), (0, 0, 0))
    expected = bytes((76, 149, 29, 255, 128, 0))
    with Document() as source:
        _scan(source, 3, 2, bytes(channel for color in colors for channel in color))
        original = _bytes(source)
        result = source.rasterized_copy(mode="gray", dpi=72, antialias=False)
        assert _bytes(source) == original
    with result:
        (image,) = _images(result)
        assert image.mapping[PdfName("ColorSpace")] == PdfName("DeviceGray")
        assert image.mapping[PdfName("BitsPerComponent")].value == 8
        assert _samples(result, image) == expected
        assert result.pages[0].media_box == (0, 0, 3, 2)
        assert result.pages[0].render(antialias=False).pixels == bytes(v for v in expected for _ in range(3))
        path = tmp_path / "gray.pdf"
        result.save(path)
        for argument in (path, _bytes(result)):
            with Document(argument) as reopened:
                assert reopened.validate()
                (stored,) = _images(reopened)
                assert stored.mapping[PdfName("ColorSpace")] == PdfName("DeviceGray")
                assert _samples(reopened, stored) == expected


def test_bilevel_pdf_has_one_bit_samples_with_correct_polarity_and_row_padding(tmp_path):
    values = (0, 64, 127, 128, 129, 200, 254, 255, 64, 255, 128, 127, 0, 200, 0, 255, 128, 129)
    expected = b"\x1f\x00\xcb\x80"
    with Document() as source:
        _scan(source, 9, 2, bytes(v for v in values for _ in range(3)))
        result = source.rasterized_copy(mode="bilevel", dpi=72, antialias=False)
    with result:
        (image,) = _images(result)
        assert image.mapping[PdfName("ColorSpace")] == PdfName("DeviceGray")
        assert image.mapping[PdfName("BitsPerComponent")].value == 1
        assert image.mapping.get(PdfName("ImageMask")) is None
        assert _samples(result, image) == expected
        assert result.pages[0].render(antialias=False).pixels == bytes(
            255 if v >= 128 else 0 for v in values for _ in range(3)
        )
        path = tmp_path / "bilevel.pdf"
        result.save(path)
        for argument in (path, _bytes(result)):
            with Document(argument) as reopened:
                assert reopened.validate()
                (stored,) = _images(reopened)
                assert stored.mapping[PdfName("BitsPerComponent")].value == 1
                assert _samples(reopened, stored) == expected


@pytest.mark.parametrize("mode,bits,space", [
    ("rgb", 8, "DeviceRGB"), ("COLOR", 8, "DeviceRGB"), (" colour ", 8, "DeviceRGB"),
    ("gray", 8, "DeviceGray"), ("Grey", 8, "DeviceGray"), ("GRAYSCALE", 8, "DeviceGray"),
    ("greyscale", 8, "DeviceGray"), ("L", 8, "DeviceGray"),
    ("bilevel", 1, "DeviceGray"), ("BW", 1, "DeviceGray"), ("1", 1, "DeviceGray"),
])
def test_color_mode_aliases_match_raster_image_exports(mode, bits, space):
    with Document() as source:
        source.pages.add(size=(5, 5))
        with source.rasterized_copy(mode=mode, dpi=72, antialias=False) as result:
            (image,) = _images(result)
            assert image.mapping[PdfName("BitsPerComponent")].value == bits
            assert image.mapping[PdfName("ColorSpace")] == PdfName(space)


@pytest.mark.parametrize("threshold,expected", [(0, b"\xff"), (64, b"\x7f"), (128, b"\x1f"), (255, b"\x01")])
def test_bilevel_threshold_includes_equality_and_extreme_values(threshold, expected):
    values = (0, 64, 127, 128, 129, 200, 254, 255)
    with Document() as source:
        _scan(source, 8, 1, bytes(v for v in values for _ in range(3)))
        with source.rasterized_copy(mode="bilevel", threshold=threshold, dpi=72, antialias=False) as result:
            assert _samples(result, _images(result)[0]) == expected


def test_bilevel_dithering_uses_error_diffusion_in_the_pdf():
    values = (0, 32, 64, 96, 128, 160, 192, 255)
    with Document() as source:
        _scan(source, 8, 1, bytes(v for v in values for _ in range(3)))
        with source.rasterized_copy(mode="bilevel", dpi=72, antialias=False) as plain:
            assert _samples(plain, _images(plain)[0]) == b"\x0f"
        with source.rasterized_copy(mode="bilevel", dither=True, dpi=72, antialias=False) as dithered:
            assert _samples(dithered, _images(dithered)[0]) == b"\x17"


@pytest.mark.parametrize("mode", ["rgb", "gray"])
def test_threshold_and_dither_do_not_change_other_modes(mode):
    with Document() as source:
        _scan(source, 2, 1, b"\xff\x00\x00\x00\x00\xff")
        with source.rasterized_copy(mode=mode, dpi=72, antialias=False) as plain:
            expected = _samples(plain, _images(plain)[0])
        with source.rasterized_copy(mode=mode, threshold=0, dither=True, dpi=72, antialias=False) as same:
            assert _samples(same, _images(same)[0]) == expected


@pytest.mark.parametrize("options", [
    {"mode": "cmyk"}, {"mode": ""}, {"mode": None}, {"mode": b"gray"}, {"mode": 1},
    {"threshold": -1}, {"threshold": 256}, {"threshold": 128.0},
    {"threshold": "128"}, {"threshold": True}, {"threshold": float("nan")},
    {"dither": 1}, {"dither": None},
])
@pytest.mark.parametrize("empty", [False, True])
def test_invalid_color_options_are_rejected_before_geometry_or_output(options, empty, monkeypatch):
    with Document() as source:
        if not empty:
            source.pages.add(size=(5, 5))

        def forbidden(*args, **kwargs):
            pytest.fail("Color options must be validated before geometry or allocation")

        monkeypatch.setattr(rasterized_copy, "_geometry", forbidden)
        monkeypatch.setattr(source, "render_page", forbidden)
        with monkeypatch.context() as patch:
            patch.setattr(Document, "__init__", forbidden)
            with pytest.raises(PdfValidationException):
                source.rasterized_copy(**options)


@pytest.mark.parametrize("mode,sample_bytes", [("gray", 4096), ("bilevel", 512)])
def test_output_limits_use_converted_sample_sizes(mode, sample_bytes, monkeypatch):
    content = rasterized_copy.build_image_stream("Im1", 0, 0, 128, 32)
    limits = PdfLoadLimits(
        max_object_bytes=sample_bytes,
        max_decoded_stream_bytes=sample_bytes,
        max_total_decoded_bytes=2 * (sample_bytes + len(content)),
    )
    with Document(limits=limits) as source:
        source.pages.add(size=(128, 32))
        with source.rasterized_copy(pages=[0, 0], mode=mode, dpi=72, antialias=False) as result:
            assert result.load_limits is limits and result.page_count == 2
            assert len(_samples(result, _images(result)[0])) == sample_bytes

        def forbidden(*args, **kwargs):
            pytest.fail("RGB sample size must be rejected before rendering")

        monkeypatch.setattr(source, "render_page", forbidden)
        with pytest.raises(PdfResourceLimitException, match="max_object_bytes"):
            source.rasterized_copy(mode="rgb", dpi=72, antialias=False)


@pytest.mark.parametrize("mode", ["gray", "bilevel"])
def test_no_intermediate_png_and_one_source_raster_at_a_time(mode, monkeypatch):
    with Document() as source:
        for color in ("#ff0000", "#0000ff"):
            source.pages.add(size=(20, 20)).draw_rectangle(0, 0, 20, 20, fill_color=color)
        render = source.render_page
        references = []

        def tracked(index, **options):
            assert all(ref() is None for ref in references)
            result = render(index, **options)
            references.append(weakref.ref(result))
            return result

        def no_png(*args, **kwargs):
            pytest.fail("PDF copies must not encode an intermediate PNG")

        monkeypatch.setattr(source, "render_page", tracked)
        monkeypatch.setattr(RasterizedPage, "to_png", no_png)
        with source.rasterized_copy(mode=mode, dpi=72, antialias=False) as result:
            assert result.page_count == 2 and all(ref() is None for ref in references)


def test_conversion_failure_disposes_partial_result_and_preserves_source(monkeypatch):
    with Document() as source:
        source.pages.add(size=(10, 10))
        source.pages.add(size=(10, 10))
        before = _bytes(source)
        append = rasterized_copy._append_image
        partial = []

        def tracked(pdf, *args):
            partial.append(pdf)
            return append(pdf, *args)

        convert = RasterizedPage.to_gray
        calls = 0

        def failing(raster):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise PdfValidationException("Unable to convert page")
            return convert(raster)

        monkeypatch.setattr(rasterized_copy, "_append_image", tracked)
        monkeypatch.setattr(RasterizedPage, "to_gray", failing)
        with pytest.raises(PdfValidationException, match="convert"):
            source.rasterized_copy(mode="gray", dpi=72, antialias=False)
        with pytest.raises(AsposePdfException, match="disposed"):
            partial[0]._ensure_not_disposed()
        assert _bytes(source) == before


@pytest.mark.parametrize("threshold,digest", [
    (0, "9f6d87c632aebf8d0f1967e2055837d7f4a7c9b47862865a75056af7f584dae3"),
    (64, "a00e70f926e22396eb74c321cc56085b6184088759660c6195201aef92b8d20e"),
    (128, "5ee225ab470f32519ec1db3f7db0dc74917b32de117ac4ace4aa462e200aabad"),
    (255, "7291bfb879c8b4ea72b484bf116c8a3b74d2493808f2b6fa29a5bf2b60becfc0"),
])
def test_row_dithering_matches_preexisting_full_image_results(threshold, digest):
    width, height = 31, 17
    values = bytes((x * 37 + y * 91 + x * y * 11) % 256 for y in range(height) for x in range(width))
    raster = RasterizedPage(width, height, bytes(v for v in values for _ in range(3)))
    assert hashlib.sha256(raster.to_bilevel(threshold, dither=True)).hexdigest() == digest


def test_dithering_does_not_allocate_a_full_page_of_python_floats():
    width, height = 31, 256
    raster = RasterizedPage(width, height, bytes((128, 128, 128)) * (width * height))
    tracemalloc.start()
    try:
        data = raster.to_bilevel(dither=True)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(data) == ((width + 7) // 8) * height
    assert peak < width * height * 8


@pytest.mark.parametrize("mode,expected", [("gray", (255, 76, 255)), ("bilevel", (255, 0, 255))])
def test_streaming_encrypted_color_copies_keep_selection_and_source_unchanged(mode, expected, tmp_path, monkeypatch):
    path = tmp_path / "protected.pdf"
    with Document() as source:
        for color in ("#ff0000", "#ffffff", "#0000ff"):
            source.pages.add(size=(13, 7)).draw_rectangle(0, 0, 13, 7, stroke_color=None, fill_color=color)
        source.encrypt("user", "owner")
        source.save(path)
    before = path.read_bytes()
    with Document.open_streaming(path, password="user") as source:
        read = source._engine_pdf.get_page_content
        visited = []

        def selected(index):
            assert index in (0, 1)
            visited.append(index)
            return read(index)

        monkeypatch.setattr(source._engine_pdf, "get_page_content", selected)
        result = source.rasterized_copy(
            mode=mode, pages=[1, 0, 1], threshold=100, dpi=72, antialias=False,
        )
        assert set(visited) == {0, 1}
        assert source._engine_pdf.page_contents == []
        assert source.is_encrypted
    with result:
        assert not result.is_encrypted
        assert [page.render(antialias=False).get_pixel(5, 3) for page in result.pages] == [
            (value, value, value) for value in expected
        ]
        result.pages[0].draw_rectangle(0, 0, 13, 7, stroke_color=None, fill_color="#000000")
        assert result.pages[2].render(antialias=False).get_pixel(5, 3) == (255, 255, 255)
    assert path.read_bytes() == before


@pytest.mark.parametrize("mode", ["gray", "bilevel"])
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_color_conversion_retains_cropped_rotated_page_size_and_view(mode, rotation):
    with Document() as source:
        page = source.pages.add(((-20, 10, 100, 90), b"1 0 0 rg -20 10 50 80 re f"))
        page.crop_box = (-10, 20, 70, 60)
        page.rotation = rotation
        reference = page.render(dpi=72, antialias=False, background=(200, 220, 240))
        expected = reference.to_gray() if mode == "gray" else reference.to_bilevel()
        with source.rasterized_copy(
            mode=mode, dpi=72, antialias=False, background=(200, 220, 240),
        ) as result:
            output = result.pages[0]
            size = (40, 80) if rotation in (90, 270) else (80, 40)
            assert output.media_box == (0, 0, *size) and output.rotation == 0
            assert _samples(result, _images(result)[0]) == expected
            with Document(_bytes(result)) as reopened:
                assert reopened.pages[0].media_box == (0, 0, *size)
                assert _samples(reopened, _images(reopened)[0]) == expected


@pytest.mark.parametrize("mode,dither,work", [("gray", False, 500), ("bilevel", True, 860)])
def test_conversion_work_buffers_are_preflighted_before_render(mode, dither, work, monkeypatch):
    with Document(limits=PdfLoadLimits(max_codec_work_bytes=512 * 1024 + work - 1)) as source:
        source.pages.add(size=(10, 10))
        monkeypatch.setattr(source, "render_page", lambda *a, **kw: pytest.fail("preflight work first"))
        with pytest.raises(PdfResourceLimitException, match="max_codec_work_bytes"):
            source.rasterized_copy(mode=mode, dither=dither, dpi=72, antialias=False)
    with Document(limits=PdfLoadLimits(max_codec_work_bytes=512 * 1024 + work)) as source:
        source.pages.add(size=(10, 10))
        with source.rasterized_copy(mode=mode, dither=dither, dpi=72, antialias=False) as result:
            assert result.page_count == 1


def test_bilevel_cumulative_limits_include_padding_on_every_row(monkeypatch):
    content = rasterized_copy.build_image_stream("Im1", 0, 0, 9, 128)
    total = 2 * (256 + len(content))
    with Document(limits=PdfLoadLimits(max_total_decoded_bytes=total - 1)) as source:
        source.pages.add(size=(9, 128))
        monkeypatch.setattr(source, "render_page", lambda *a, **kw: pytest.fail("preflight padding first"))
        with pytest.raises(PdfResourceLimitException, match="max_total_decoded_bytes"):
            source.rasterized_copy(mode="bilevel", pages=[0, 0], dpi=72, antialias=False)
    with Document(limits=PdfLoadLimits(max_total_decoded_bytes=total)) as source:
        source.pages.add(size=(9, 128))
        with source.rasterized_copy(mode="bilevel", pages=[0, 0], dpi=72, antialias=False) as result:
            assert [len(_samples(result, image)) for image in _images(result)] == [256, 256]


@pytest.mark.parametrize("mode", ["gray", "bilevel"])
def test_converted_raw_samples_that_look_like_encoded_files_remain_raw(mode):
    samples = b"\x89PNG\r\n\x1a\n"
    values = samples if mode == "gray" else bytes(
        255 if byte & (0x80 >> bit) else 0 for byte in samples for bit in range(8)
    )
    with Document() as source:
        _scan(source, len(values), 1, bytes(v for v in values for _ in range(3)))
        with source.rasterized_copy(mode=mode, dpi=72, antialias=False) as result:
            (image,) = _images(result)
            assert image.mapping.get(PdfName("Filter")) is None
            assert _samples(result, image) == samples
            assert result.pages[0].render(antialias=False).pixels == bytes(v for v in values for _ in range(3))
