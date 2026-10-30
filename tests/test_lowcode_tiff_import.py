"""TIFF scan import through the public ImagesToPdf plugin."""

from __future__ import annotations

import io
import struct
import sys

import pytest

from aspose_pdf import Document, PageSize, PdfLoadLimits
from aspose_pdf.engine.image_export import write_png
from aspose_pdf.exceptions import (
    AsposePdfException,
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


def _tiff(pillow, specs=None, **kwargs):
    if specs is None:
        specs = [("RGB", (6, 4), (255, 0, 0)), ("L", (3, 5), 100)]
    frames = [pillow.new(mode, size, color) for mode, size, color in specs]
    try:
        stream = io.BytesIO()
        frames[0].save(
            stream, format="TIFF", save_all=True, append_images=frames[1:], **kwargs
        )
        return stream.getvalue()
    finally:
        for frame in frames:
            frame.close()


def _convert(data, **kwargs):
    return ImagesToPdf().process(
        ImagesToPdfOptions(**kwargs).add_input(ByteArrayDataSource(data))
    )


@pytest.mark.parametrize(
    "compression", ["raw", "tiff_lzw", "tiff_adobe_deflate", "packbits"]
)
def test_imports_all_frames_with_their_dimensions_and_colors(pillow, compression):
    if compression != "raw":
        features = pytest.importorskip("PIL.features")
        if not features.check("libtiff"):
            pytest.skip("Pillow was built without libtiff")
    results = _convert(_tiff(pillow, compression=compression))
    assert len(results) == 1
    with Document(io.BytesIO(results[0].to_array())) as document:
        assert document.page_count == 2
        assert [page.size for page in document.pages] == [
            PageSize(6, 4),
            PageSize(3, 5),
        ]
        assert document.pages[0].render().get_pixel(1, 1) == (255, 0, 0)
        assert document.pages[1].render().get_pixel(1, 1) == (100, 100, 100)


def test_single_document_false_emits_one_pdf_per_frame_in_source_order(pillow):
    options = ImagesToPdfOptions(single_document=False)
    options.add_input(
        ByteArrayDataSource(write_png(2, 2, "RGB", bytes([0, 0, 255] * 4)))
    )
    options.add_input(ByteArrayDataSource(_tiff(pillow)))
    options.add_input(ByteArrayDataSource(_tiff(pillow, [("L", (2, 2), 255)])))
    results = ImagesToPdf().process(options)
    assert len(results) == 4
    colors = []
    for result in results:
        with Document(io.BytesIO(result.to_array())) as document:
            assert document.page_count == 1
            colors.append(document.pages[0].render().get_pixel(1, 1))
    assert colors == [(0, 0, 255), (255, 0, 0), (100, 100, 100), (255, 255, 255)]


def test_mixed_inputs_are_combined_in_source_and_frame_order(pillow):
    blue = pillow.new("RGB", (2, 2), (0, 0, 255))
    try:
        stream = io.BytesIO()
        blue.save(stream, format="JPEG", quality=100)
    finally:
        blue.close()
    options = ImagesToPdfOptions()
    options.add_input(ByteArrayDataSource(write_png(2, 2, "L", bytes([0] * 4))))
    options.add_input(ByteArrayDataSource(_tiff(pillow)))
    options.add_input(ByteArrayDataSource(stream.getvalue()))
    results = ImagesToPdf().process(options)
    with Document(io.BytesIO(results[0].to_array())) as document:
        assert document.page_count == 4
        assert [document.pages[i].render().get_pixel(1, 1) for i in range(3)] == [
            (0, 0, 0),
            (255, 0, 0),
            (100, 100, 100),
        ]
        assert document.pages[3].render().get_pixel(1, 1)[2] >= 250


def test_tiff_file_and_stream_sources_and_pdf_file_output(pillow, tmp_path):
    source = tmp_path / "scans.bin"
    source.write_bytes(_tiff(pillow))
    output = tmp_path / "scans.pdf"
    stream = io.BytesIO(_tiff(pillow, [("L", (2, 2), 255)]))
    results = ImagesToPdf().process(
        ImagesToPdfOptions()
        .add_input(FileDataSource(source))
        .add_input(StreamDataSource(stream))
        .add_output(FileDataSource(output))
    )
    assert not stream.closed
    assert output.read_bytes() == results[0].to_array()
    with Document(output) as document:
        assert document.page_count == 3


def test_separate_frame_results_reach_separate_outputs(pillow):
    outputs = [io.BytesIO(), io.BytesIO()]
    options = ImagesToPdfOptions(single_document=False).add_input(
        ByteArrayDataSource(_tiff(pillow))
    )
    for output in outputs:
        options.add_output(StreamDataSource(output))
    results = ImagesToPdf().process(options)
    assert [output.getvalue() for output in outputs] == [r.to_array() for r in results]


def test_dpi_applies_to_every_frame_and_overrides_tiff_metadata(pillow):
    data = _tiff(pillow, dpi=(600, 600))
    for dpi in (72, 300):
        results = _convert(data, dpi=dpi)
        with Document(io.BytesIO(results[0].to_array())) as document:
            assert document.pages[0].size.width == pytest.approx(6 * 72 / dpi)
            assert document.pages[1].size.height == pytest.approx(5 * 72 / dpi)


def test_fixed_page_size_and_margin_fit_each_frame(pillow):
    data = _tiff(pillow, [("RGB", (400, 200), (255, 0, 0))])
    results = _convert(data, page_size=(100, 100), margin=10)
    with Document(io.BytesIO(results[0].to_array())) as document:
        page = document.pages[0]
        assert page.size == PageSize(100, 100)
        absorber = ImagePlacementAbsorber()
        absorber.visit(page)
        rect = absorber.image_placements[0].rectangle
        assert (rect.x, rect.y, rect.width, rect.height) == pytest.approx(
            (10, 30, 80, 40)
        )


@pytest.mark.parametrize("compression", ["raw", "group4"])
def test_bilevel_scan_preserves_black_and_white(pillow, compression):
    if compression == "group4":
        features = pytest.importorskip("PIL.features")
        if not features.check("libtiff"):
            pytest.skip("Pillow was built without libtiff")
    frame = pillow.new("1", (4, 4), 1)
    frame.putpixel((1, 1), 0)
    try:
        stream = io.BytesIO()
        frame.save(stream, format="TIFF", compression=compression)
    finally:
        frame.close()
    results = _convert(stream.getvalue())
    with Document(io.BytesIO(results[0].to_array())) as document:
        raster = document.pages[0].render()
        assert raster.get_pixel(1, 1) == (0, 0, 0)
        assert raster.get_pixel(2, 2) == (255, 255, 255)


def test_palette_tiff_expands_colors_losslessly(pillow):
    frame = pillow.new("P", (4, 4), 1)
    frame.putpalette([0, 0, 0, 20, 40, 60] + [0] * 762)
    try:
        stream = io.BytesIO()
        frame.save(stream, format="TIFF")
    finally:
        frame.close()
    results = _convert(stream.getvalue())
    with Document(io.BytesIO(results[0].to_array())) as document:
        assert document.pages[0].render().get_pixel(1, 1) == (20, 40, 60)


@pytest.mark.parametrize("orientation", [3, 6, 8])
def test_tiff_orientation_is_applied_once(pillow, orientation):
    frame = pillow.new("RGB", (4, 2), (0, 0, 255))
    frame.putpixel((0, 0), (255, 0, 0))
    try:
        stream = io.BytesIO()
        frame.save(stream, format="TIFF", tiffinfo={274: orientation})
    finally:
        frame.close()
    results = _convert(stream.getvalue())
    with Document(io.BytesIO(results[0].to_array())) as document:
        expected_size = (4, 2) if orientation == 3 else (2, 4)
        assert document.pages[0].size == PageSize(*expected_size)
        raster = document.pages[0].render()
        expected_red = {3: (3, 1), 6: (1, 0), 8: (0, 3)}[orientation]
        assert raster.get_pixel(*expected_red) == (255, 0, 0)


def test_bigtiff_frames_are_supported(pillow):
    data = _tiff(pillow, big_tiff=True)
    assert data[:4] == b"II+\x00"
    results = _convert(data)
    with Document(io.BytesIO(results[0].to_array())) as document:
        assert document.page_count == 2


def test_big_endian_tiff_is_recognized(pillow):
    entries = [
        (256, 4, 2),
        (257, 4, 2),
        (258, 3, 8),
        (259, 3, 1),
        (262, 3, 1),
        (273, 4, 122),
        (278, 4, 2),
        (279, 4, 4),
        (284, 3, 1),
    ]
    data = b"MM\x00*" + struct.pack(">IH", 8, len(entries))
    for tag, kind, value in entries:
        encoded = (
            struct.pack(">H", value) + b"\x00\x00"
            if kind == 3
            else struct.pack(">I", value)
        )
        data += struct.pack(">HHI", tag, kind, 1) + encoded
    data += struct.pack(">I", 0) + bytes([50] * 4)
    results = _convert(data)
    with Document(io.BytesIO(results[0].to_array())) as document:
        assert document.pages[0].size == PageSize(2, 2)
        assert document.pages[0].render().get_pixel(1, 1) == (50, 50, 50)


@pytest.mark.parametrize("mode", ["CMYK", "I;16", "F"])
def test_unsupported_tiff_modes_are_refused_explicitly(pillow, mode):
    with pytest.raises(UnsupportedFeatureException, match="unsupported mode"):
        _convert(_tiff(pillow, [(mode, (2, 2), 0)]))


def _alpha_tiff(pillow, mode, *, orientation=1, compression="raw", extra_samples=2):
    alpha = (0, 64, 128, 255)
    pixels = [
        (50 + i * 20, 40 + i * 10, 200 - i * 20, alpha[i % 4])
        if mode == "RGBA" else (30 + i * 25, alpha[i % 4])
        for i in range(8)
    ]
    if extra_samples == 1:
        pixels = [
            (*((channel * pixel[3] + 127) // 255 for channel in pixel[:3]), pixel[3])
            for pixel in pixels
        ]
    with pillow.new(mode, (4, 2)) as image, io.BytesIO() as output:
        image.putdata(pixels)
        image.save(output, format="TIFF", compression=compression,
                   tiffinfo={274: orientation, 338: (extra_samples,)})
        data = output.getvalue()
    if extra_samples == 1:
        # Pillow writes an unassociated-alpha tag even when tiffinfo supplies 1.
        # Mark the premultiplied fixture samples as associated in the saved IFD.
        data = bytearray(data)
        assert data[:2] == b"II"
        first = struct.unpack_from("<I", data, 4)[0]
        count = struct.unpack_from("<H", data, first)[0]
        for index in range(count):
            entry = first + 2 + 12 * index
            if struct.unpack_from("<H", data, entry)[0] == 338:
                struct.pack_into("<H", data, entry + 8, 1)
                return bytes(data)
        raise AssertionError("Fixture has no ExtraSamples tag")
    return data


def _reference_png(pillow, data, frame=0):
    with pillow.open(io.BytesIO(data)) as image:
        image.seek(frame)
        with image.convert("RGBA") as rgba:
            return write_png(*rgba.size, "RGBA", rgba.tobytes())


@pytest.mark.parametrize("mode", ["RGBA", "LA"])
@pytest.mark.parametrize("compression", ["raw", "tiff_lzw", "tiff_adobe_deflate", "packbits"])
@pytest.mark.parametrize("source_kind", ["bytes", "file", "stream"])
def test_transparent_tiff_matches_png_and_survives_pdf_round_trips(
    pillow, tmp_path, mode, compression, source_kind,
):
    if compression != "raw" and not pytest.importorskip("PIL.features").check("libtiff"):
        pytest.skip("Pillow was built without libtiff")
    data = _alpha_tiff(pillow, mode, compression=compression)
    source = tmp_path / "misnamed.jpg"
    source.write_bytes(data)
    with io.BytesIO(data) as stream:
        inputs = {
            "bytes": ByteArrayDataSource(data), "file": FileDataSource(source),
            "stream": StreamDataSource(stream),
        }
        result = ImagesToPdf().process(ImagesToPdfOptions().add_input(inputs[source_kind]))
        assert not stream.closed
    png = _reference_png(pillow, data)
    with Document(result[0].to_array()) as actual:
        with Document(_convert(png)[0].to_array()) as reference:
            assert actual.pages[0].size == PageSize(4, 2)
            for background in ((255, 255, 255), (0, 255, 0)):
                expected = reference.pages[0].render(background=background, antialias=False)
                assert actual.pages[0].render(
                    background=background, antialias=False,
                ).pixels == expected.pixels
            output = tmp_path / "transparent.pdf"
            actual.save(output)
            for value in (output, output.read_bytes()):
                with Document(value) as reopened:
                    assert reopened.pages[0].render(
                        background=(0, 255, 0), antialias=False,
                    ).pixels == expected.pixels
    assert source.read_bytes() == data


@pytest.mark.parametrize("mode", ["RGBA", "LA"])
@pytest.mark.parametrize("orientation", range(1, 9))
def test_alpha_tiff_orientation_preserves_colors_and_transparency(pillow, mode, orientation):
    data = _alpha_tiff(pillow, mode, orientation=orientation)
    with Document(_convert(data)[0].to_array()) as actual:
        with Document(_convert(_reference_png(pillow, data))[0].to_array()) as reference:
            assert actual.pages[0].size == reference.pages[0].size
            assert actual.pages[0].render(
                background=(0, 255, 0), antialias=False,
            ).pixels == reference.pages[0].render(
                background=(0, 255, 0), antialias=False,
            ).pixels


def test_associated_rgba_alpha_is_not_applied_twice(pillow):
    data = _alpha_tiff(pillow, "RGBA", extra_samples=1)
    with pillow.open(io.BytesIO(data)) as image:
        assert image.tag_v2[338] == (1,)
    with Document(_convert(data)[0].to_array()) as actual:
        with Document(_convert(_reference_png(pillow, data))[0].to_array()) as reference:
            assert actual.pages[0].render(
                background=(0, 255, 0), antialias=False,
            ).pixels == reference.pages[0].render(
                background=(0, 255, 0), antialias=False,
            ).pixels


@pytest.mark.parametrize("single", [False, True])
def test_mixed_opaque_and_alpha_frames_keep_their_order_and_dimensions(pillow, single):
    data = _tiff(pillow, [
        ("RGB", (6, 4), (255, 0, 0)), ("LA", (3, 5), (100, 64)),
        ("RGBA", (4, 2), (20, 100, 200, 128)), ("L", (2, 2), 50),
    ])
    results = _convert(data, single_document=single)
    assert len(results) == (1 if single else 4)
    index = 0
    for result in results:
        with Document(result.to_array()) as document:
            for page in document.pages:
                png = _reference_png(pillow, data, index)
                with Document(_convert(png)[0].to_array()) as reference:
                    assert page.size == reference.pages[0].size
                    assert page.render(background=(0, 255, 0), antialias=False).pixels == (
                        reference.pages[0].render(background=(0, 255, 0), antialias=False).pixels
                    )
                index += 1
    assert index == 4


@pytest.mark.parametrize("mode,color", [("RGBA", (20, 40, 60, 255)), ("LA", (100, 255))])
def test_fully_opaque_alpha_frames_do_not_create_soft_masks(pillow, mode, color):
    data = _tiff(pillow, [(mode, (4, 2), color)])
    result = _convert(data)[0].to_array()
    assert b"/SMask" not in result
    with Document(result) as document:
        expected = color[:3] if mode == "RGBA" else (100, 100, 100)
        assert document.pages[0].render(antialias=False).get_pixel(1, 1) == expected


@pytest.mark.parametrize("mode", ["RGBA", "LA"])
@pytest.mark.parametrize("limit_name", ["max_decoded_stream_bytes", "max_total_decoded_bytes",
                                        "max_codec_work_bytes"])
def test_alpha_channels_are_budgeted_before_pixel_decoding(pillow, monkeypatch, mode, limit_name):
    data = _tiff(pillow, [(mode, (6, 4), 0)])
    components = 4 if mode == "RGBA" else 2
    limit = {
        "max_decoded_stream_bytes": 24 * components + 4 - 1,
        "max_total_decoded_bytes": 24 * components - 1,
        "max_codec_work_bytes": len(data) + 24 * (4 + components * 5) + 8 + 1024 - 1,
    }[limit_name]
    plugin = pytest.importorskip("PIL.TiffImagePlugin")

    def forbidden(*args, **kwargs):
        pytest.fail("Alpha-channel allocations must be bounded before pixel decoding")

    monkeypatch.setattr(plugin.TiffImageFile, "load", forbidden)
    with pytest.raises(PdfResourceLimitException, match=limit_name):
        _convert(data, limits=PdfLoadLimits(**{limit_name: limit}))


@pytest.mark.parametrize("mode", ["RGBA", "LA"])
@pytest.mark.parametrize("single", [False, True])
def test_alpha_bytes_count_toward_cumulative_frame_limits(pillow, monkeypatch, mode, single):
    data = _tiff(pillow, [(mode, (6, 4), 0), (mode, (6, 4), 0)])
    plugin = pytest.importorskip("PIL.TiffImagePlugin")
    original = plugin.TiffImageFile.load

    def guarded(image):
        assert image.tell() == 0, "The next alpha frame must fail before decoding"
        return original(image)

    monkeypatch.setattr(plugin.TiffImageFile, "load", guarded)
    limit = 24 * (4 if mode == "RGBA" else 2) * 2 - 1
    with pytest.raises(PdfResourceLimitException, match="max_total_decoded_bytes"):
        _convert(data, single_document=single, limits=PdfLoadLimits(max_total_decoded_bytes=limit))


def test_high_precision_rgba_is_rejected_before_downsampling(pillow):
    data = bytearray(_tiff(pillow, [("RGBA", (6, 4), 0)]))
    first = struct.unpack_from("<I", data, 4)[0]
    count = struct.unpack_from("<H", data, first)[0]
    for index in range(count):
        entry = first + 2 + 12 * index
        if struct.unpack_from("<H", data, entry)[0] == 258:
            bits_offset = struct.unpack_from("<I", data, entry + 8)[0]
            struct.pack_into("<HHHH", data, bits_offset, 16, 16, 16, 16)
            break
    else:
        raise AssertionError("Fixture has no BitsPerSample tag")
    with pytest.raises(UnsupportedFeatureException, match="bit depth"):
        _convert(bytes(data))


def test_high_precision_rgb_is_refused_before_pillow_can_downsample(pillow):
    data = bytearray(_tiff(pillow, [("RGB", (6, 4), 0)]))
    first = struct.unpack_from("<I", data, 4)[0]
    count = struct.unpack_from("<H", data, first)[0]
    for index in range(count):
        entry = first + 2 + 12 * index
        if struct.unpack_from("<H", data, entry)[0] == 258:
            bits_offset = struct.unpack_from("<I", data, entry + 8)[0]
            struct.pack_into("<HHH", data, bits_offset, 16, 16, 16)
            break
    else:
        raise AssertionError("Fixture has no BitsPerSample tag")
    with pytest.raises(UnsupportedFeatureException, match="bit depth"):
        _convert(bytes(data))


@pytest.mark.parametrize("pillow_limit", [10, 15])
def test_pillow_pixel_guards_raise_library_resource_errors(
    pillow, monkeypatch, pillow_limit
):
    data = _tiff(pillow)
    monkeypatch.setattr(pillow, "MAX_IMAGE_PIXELS", pillow_limit)
    with pytest.raises(PdfResourceLimitException, match="Pillow's pixel safety limit"):
        _convert(data, limits=PdfLoadLimits.unlimited())


def test_tiff_requires_optional_pillow(monkeypatch):
    monkeypatch.setitem(sys.modules, "PIL", None)
    with pytest.raises(UnsupportedFeatureException, match="install the 'images' extra"):
        _convert(b"II*\x00" + bytes(16))


def test_png_import_still_works_without_pillow(monkeypatch):
    monkeypatch.setitem(sys.modules, "PIL", None)
    results = _convert(write_png(2, 2, "L", bytes([100] * 4)))
    with Document(io.BytesIO(results[0].to_array())) as document:
        assert document.pages[0].render().get_pixel(1, 1) == (100, 100, 100)


@pytest.mark.parametrize("magic", [b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"])
def test_invalid_tiff_has_a_clear_library_exception(pillow, magic):
    with pytest.raises(PdfValidationException, match="Cannot decode TIFF"):
        _convert(magic + bytes(16))


def test_truncated_pixel_data_is_refused(pillow):
    data = _tiff(pillow, [("RGB", (6, 4), (255, 0, 0))])
    with pillow.open(io.BytesIO(data)) as image:
        pixel_offset = image.tag_v2[273][0]
    with pytest.raises(PdfValidationException, match="Cannot decode TIFF"):
        _convert(data[: pixel_offset + 1])


@pytest.mark.parametrize("cyclic", [False, True])
def test_invalid_frame_chain_is_refused(pillow, cyclic):
    data = bytearray(_tiff(pillow))
    first = struct.unpack_from("<I", data, 4)[0]
    count = struct.unpack_from("<H", data, first)[0]
    struct.pack_into(
        "<I", data, first + 2 + 12 * count, first if cyclic else len(data) + 100
    )
    with pytest.raises(PdfValidationException, match="invalid or cyclic offset"):
        _convert(bytes(data))


def test_later_frame_failure_does_not_write_any_output(pillow, tmp_path):
    data = _tiff(pillow, [("RGB", (6, 4), 0), ("CMYK", (2, 2), 0)])
    outputs = [tmp_path / "first.pdf", tmp_path / "second.pdf"]
    options = ImagesToPdfOptions(single_document=False).add_input(
        ByteArrayDataSource(data)
    )
    for output in outputs:
        output.write_bytes(b"original")
        options.add_output(FileDataSource(output))
    with pytest.raises(UnsupportedFeatureException):
        ImagesToPdf().process(options)
    assert [output.read_bytes() for output in outputs] == [b"original", b"original"]


@pytest.mark.parametrize(
    "name, limit",
    [
        ("max_image_pixels", 10),
        ("max_decoded_stream_bytes", 20),
        ("max_decoded_stream_bytes", 72),
        ("max_total_decoded_bytes", 20),
        ("max_codec_work_bytes", 1000),
    ],
)
def test_frame_limits_are_checked_before_pixel_decoding(
    pillow, monkeypatch, name, limit
):
    data = _tiff(pillow)
    plugin = pytest.importorskip("PIL.TiffImagePlugin")

    def unexpected_load(self):
        raise AssertionError("Pixels were decoded before checking the limit")

    monkeypatch.setattr(plugin.TiffImageFile, "load", unexpected_load)
    with pytest.raises(PdfResourceLimitException, match=name):
        _convert(data, limits=PdfLoadLimits(**{name: limit}))


@pytest.mark.parametrize("single", [False, True])
@pytest.mark.parametrize(
    "name, limit", [("max_pages", 1), ("max_total_decoded_bytes", 80)]
)
def test_cumulative_limits_are_checked_before_loading_the_next_frame(
    pillow, monkeypatch, single, name, limit
):
    data = _tiff(pillow)
    plugin = pytest.importorskip("PIL.TiffImagePlugin")
    original = plugin.TiffImageFile.load

    def guarded_load(self):
        if self.tell() == 1:
            raise AssertionError("Second frame was decoded before checking the limit")
        return original(self)

    monkeypatch.setattr(plugin.TiffImageFile, "load", guarded_load)
    with pytest.raises(PdfResourceLimitException, match=name):
        _convert(data, single_document=single, limits=PdfLoadLimits(**{name: limit}))


def test_rotated_frame_filtered_bytes_are_checked_before_decoding(pillow, monkeypatch):
    data = _tiff(pillow, [("RGB", (6, 4), 0)], tiffinfo={274: 6})
    plugin = pytest.importorskip("PIL.TiffImagePlugin")

    def unexpected_load(self):
        raise AssertionError("Rotated frame was decoded before checking filtered bytes")

    monkeypatch.setattr(plugin.TiffImageFile, "load", unexpected_load)
    with pytest.raises(PdfResourceLimitException, match="max_decoded_stream_bytes"):
        _convert(data, limits=PdfLoadLimits(max_decoded_stream_bytes=77))


@pytest.mark.parametrize(
    "name, limit", [("max_pages", 3), ("max_total_decoded_bytes", 150)]
)
def test_cumulative_limits_span_separate_tiff_inputs_and_results(pillow, name, limit):
    data = _tiff(pillow)
    options = ImagesToPdfOptions(
        single_document=False, limits=PdfLoadLimits(**{name: limit})
    )
    options.add_input(ByteArrayDataSource(data)).add_input(ByteArrayDataSource(data))
    with pytest.raises(PdfResourceLimitException, match=name):
        ImagesToPdf().process(options)


def test_tiff_source_input_limit_is_checked_before_opening(pillow, monkeypatch):
    data = _tiff(pillow)

    def unexpected_open(*args, **kwargs):
        raise AssertionError("TIFF opened before checking input bytes")

    monkeypatch.setattr(pillow, "open", unexpected_open)
    with pytest.raises(PdfResourceLimitException, match="max_input_bytes"):
        _convert(data, limits=PdfLoadLimits(max_input_bytes=len(data) - 1))


@pytest.mark.parametrize("option", ["dpi", "margin"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_geometry_options_are_refused(option, value):
    with pytest.raises(AsposePdfException, match=option):
        _convert(write_png(2, 2, "L", bytes(4)), **{option: value})
