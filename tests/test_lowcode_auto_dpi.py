"""Automatic physical image sizing uses per-image resolution and safe fallback."""

from __future__ import annotations

import io
import struct
import sys
import zlib

import pytest

from aspose_pdf import Document, PageSize, PdfLoadLimits
from aspose_pdf.engine.image_export import write_png
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfResourceLimitException,
    PdfValidationException,
)
from aspose_pdf.images import ImagePlacementAbsorber
from aspose_pdf.lowcode import (
    ByteArrayDataSource,
    FileDataSource,
    ImagesToPdf,
    ImagesToPdfOptions,
    StreamDataSource,
)


@pytest.fixture(scope="module")
def jpeg():
    with Document() as document:
        page = document.pages.add(size=(32, 16))
        page.draw_rectangle(0, 0, 32, 16, fill_color=(1, 0, 0))
        return page.render().to_jpeg(quality=100)


@pytest.fixture
def pillow():
    return pytest.importorskip("PIL.Image")


def _convert(data, **options):
    return ImagesToPdf().process(
        ImagesToPdfOptions(**options).add_input(ByteArrayDataSource(data))
    )


def _assert_size(data, expected, **options):
    with Document(_convert(data, **options)[0].to_array()) as document:
        assert document.page_count == 1
        size = document.pages[0].size
        assert (size.width, size.height) == pytest.approx(expected)


def _segment(payload):
    return b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload


def _jfif(jpeg, x=144, y=288, unit=1):
    assert jpeg[2:4] == b"\xff\xe0"
    end = 4 + struct.unpack_from(">H", jpeg, 4)[0]
    payload = b"JFIF\x00\x01\x02" + struct.pack(">BHHBB", unit, x, y, 0, 0)
    return (
        jpeg[:2]
        + b"\xff\xe0"
        + struct.pack(">H", len(payload) + 2)
        + payload
        + jpeg[end:]
    )


def _exif(x=(144, 1), y=(288, 1), unit=2, orientation=None, order="<"):
    entries = []
    for tag, value in ((0x011A, x), (0x011B, y), (0x0128, unit), (0x0112, orientation)):
        if value is not None:
            entries.append((tag, value))
    values_offset = 8 + 2 + len(entries) * 12 + 4
    directory = bytearray(struct.pack(order + "H", len(entries)))
    values = bytearray()
    for tag, value in entries:
        if tag in (0x011A, 0x011B):
            directory.extend(
                struct.pack(order + "HHII", tag, 5, 1, values_offset + len(values))
            )
            values.extend(struct.pack(order + "II", *value))
        else:
            directory.extend(
                struct.pack(order + "HHIH", tag, 3, 1, value) + b"\x00\x00"
            )
    directory.extend(b"\x00" * 4)
    return (
        b"Exif\x00\x00"
        + (b"II" if order == "<" else b"MM")
        + struct.pack(order + "HI", 42, 8)
        + directory
        + values
    )


def _tagged(jpeg, **metadata):
    return jpeg[:2] + _segment(_exif(**metadata)) + jpeg[2:]


def _chunk(kind, payload):
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _png(x=10000, y=20000, unit=1):
    data = write_png(32, 16, "RGB", bytes([255, 0, 0] * 32 * 16))
    return data[:33] + _chunk(b"pHYs", struct.pack(">IIB", x, y, unit)) + data[33:]


@pytest.mark.parametrize("unit,factor", [(1, 1), (2, 2.54)])
def test_jpeg_jfif_converts_units_and_preserves_separate_axes(jpeg, unit, factor):
    _assert_size(_jfif(jpeg, unit=unit), (16 / factor, 4 / factor), dpi="auto")


@pytest.mark.parametrize("order", ["<", ">"])
@pytest.mark.parametrize("unit,factor", [(2, 1), (3, 2.54), (None, 1)])
def test_jpeg_exif_precedes_jfif_and_converts_rationals_and_units(
    jpeg, order, unit, factor
):
    data = _tagged(
        _jfif(jpeg, x=72, y=72), x=(288, 2), y=(576, 2), unit=unit, order=order
    )
    _assert_size(data, (16 / factor, 4 / factor), dpi="auto")


@pytest.mark.parametrize("orientation", range(1, 9))
def test_unequal_dpi_axes_follow_jpeg_rotation_without_changing_payload(
    jpeg, orientation
):
    data = _tagged(jpeg, orientation=orientation)
    results = _convert(data, dpi="auto", auto_orient=True)
    with Document(results[0].to_array()) as document:
        page = document.pages[0]
        assert page.size == PageSize(*((4, 16) if orientation >= 5 else (16, 4)))
        absorber = ImagePlacementAbsorber()
        absorber.visit(page)
        assert absorber.image_placements[0].image_data == data
    _assert_size(data, (16, 4), dpi="auto")


@pytest.mark.parametrize(
    "metadata",
    [
        {"x": None},
        {"y": None},
        {"x": (0, 1)},
        {"y": (1, 0)},
        {"unit": 1},
        {"unit": 99},
    ],
)
def test_unusable_exif_pair_falls_back_to_valid_jfif(jpeg, metadata):
    _assert_size(_tagged(_jfif(jpeg), **metadata), (16, 4), dpi="auto")


@pytest.mark.parametrize(
    "x,y,unit", [(144, 288, 0), (0, 288, 1), (144, 0, 1), (1, 1, 7)]
)
def test_missing_absolute_jpeg_density_uses_72_dpi_for_both_axes(jpeg, x, y, unit):
    _assert_size(_jfif(jpeg, x, y, unit), (32, 16), dpi="auto")


@pytest.mark.parametrize(
    "data,expected",
    [
        (_png(), (32 * 72 / 254, 16 * 72 / 508)),
        (_png(unit=0), (32, 16)),
        (_png(x=0), (32, 16)),
    ],
)
def test_png_physical_units_and_relative_or_zero_density(data, expected):
    _assert_size(data, expected, dpi="auto")


@pytest.mark.parametrize("dpi", [72, 144, "144"])
def test_numeric_dpi_overrides_metadata_and_default_stays_72(jpeg, dpi):
    for data in (_tagged(jpeg), _png()):
        _assert_size(data, (32 * 72 / float(dpi), 16 * 72 / float(dpi)), dpi=dpi)
        _assert_size(data, (32, 16))


def test_fixed_page_margin_fitting_uses_physical_size_and_avoids_upscaling(jpeg):
    data = _tagged(jpeg, orientation=6)
    with Document(
        _convert(data, dpi="auto", auto_orient=True, page_size=(40, 40), margin=4)[
            0
        ].to_array()
    ) as document:
        absorber = ImagePlacementAbsorber()
        absorber.visit(document.pages[0])
        rect = absorber.image_placements[0].rectangle
        assert (rect.x, rect.y, rect.width, rect.height) == pytest.approx(
            (18, 12, 4, 16)
        )


@pytest.mark.parametrize("kind", ["bytes", "file", "stream"])
def test_auto_dpi_input_sources_and_pdf_file_round_trip(jpeg, kind, tmp_path):
    data = _jfif(jpeg)
    path = tmp_path / "scan.bin"
    path.write_bytes(data)
    target = tmp_path / "scan.pdf"
    with io.BytesIO(b"prefix" + data) as stream:
        stream.seek(6)
        source = {
            "bytes": ByteArrayDataSource(data),
            "file": FileDataSource(path),
            "stream": StreamDataSource(stream),
        }[kind]
        options = (
            ImagesToPdfOptions().add_input(source).add_output(FileDataSource(target))
        )
        options.dpi = "auto"
        results = ImagesToPdf().process(options)
        assert not stream.closed
    assert target.read_bytes() == results[0].to_array()
    with Document.open_streaming(target) as document:
        assert document.pages[0].size == PageSize(16, 4)
    assert path.read_bytes() == data


@pytest.mark.parametrize("single", [True, False])
def test_each_input_gets_its_own_metadata_or_fallback(jpeg, single):
    options = ImagesToPdfOptions(dpi="auto", single_document=single)
    for data in (_jfif(jpeg), _png(), write_png(32, 16, "L", bytes(512))):
        options.add_input(ByteArrayDataSource(data))
    results = ImagesToPdf().process(options)
    assert len(results) == (1 if single else 3)
    sizes = []
    for result in results:
        with Document(result.to_array()) as document:
            sizes.extend((page.size.width, page.size.height) for page in document.pages)
    for actual, expected in zip(
        sizes, [(16, 4), (32 * 72 / 254, 16 * 72 / 508), (32, 16)], strict=True
    ):
        assert actual == pytest.approx(expected)


def _tiff(pillow, specs):
    tiff_plugin = pytest.importorskip("PIL.TiffImagePlugin")
    frames = []
    try:
        for size, x, y, unit, orientation in specs:
            image = pillow.new("RGB", size, "red")
            frames.append(image)
            image.tag_v2 = tiff_plugin.ImageFileDirectory_v2()
            for tag, value in ((282, x), (283, y), (296, unit), (274, orientation)):
                if value is not None:
                    image.tag_v2[tag] = value
        with io.BytesIO() as output:
            frames[0].save(
                output,
                format="TIFF",
                save_all=True,
                append_images=frames[1:],
                tiffinfo={274: 1},
            )
            data = bytearray(output.getvalue())
        # Pillow copies resolution tags from tag_v2 but drops Orientation on save.
        offset = struct.unpack_from("<I", data, 4)[0]
        for _, _, _, _, orientation in specs:
            count = struct.unpack_from("<H", data, offset)[0]
            for index in range(count):
                entry = offset + 2 + index * 12
                if struct.unpack_from("<H", data, entry)[0] == 274:
                    struct.pack_into("<H", data, entry + 8, orientation or 1)
            offset = struct.unpack_from("<I", data, offset + 2 + count * 12)[0]
        return bytes(data)
    finally:
        for image in frames:
            image.close()


@pytest.mark.parametrize("single", [True, False])
def test_tiff_frames_keep_individual_resolution_and_oriented_axes(pillow, single):
    data = _tiff(pillow, [((32, 16), 144, 288, 2, 6), ((16, 32), 72, 144, 2, 1)])
    results = _convert(data, dpi="auto", single_document=single)
    sizes = []
    for result in results:
        with Document(result.to_array()) as document:
            sizes.extend(page.size for page in document.pages)
    assert sizes == [PageSize(4, 16), PageSize(16, 16)]


@pytest.mark.parametrize(
    "unit,expected",
    [(2, (16, 4)), (3, (16 / 2.54, 4 / 2.54)), (1, (32, 16)), (None, (16, 4))],
)
def test_tiff_units_and_fallback(pillow, unit, expected):
    _assert_size(_tiff(pillow, [((32, 16), 144, 288, unit, 1)]), expected, dpi="auto")


def test_tiff_without_resolution_uses_fallback(pillow):
    _assert_size(_tiff(pillow, [((32, 16), None, None, None, 1)]), (32, 16), dpi="auto")


@pytest.mark.parametrize("x,y", [(0, 288), (144, None)])
def test_tiff_incomplete_or_zero_resolution_pair_uses_fallback(pillow, x, y):
    _assert_size(_tiff(pillow, [((32, 16), x, y, 2, 1)]), (32, 16), dpi="auto")


def test_bigtiff_auto_resolution(pillow):
    with pillow.new("RGB", (32, 16), "red") as image, io.BytesIO() as output:
        image.save(output, format="TIFF", dpi=(144, 288), big_tiff=True)
        _assert_size(output.getvalue(), (16, 4), dpi="auto")


@pytest.mark.parametrize("x,y", [(10000, 20000), (0, 20000), (-1, 20000)])
def test_bmp_pixels_per_metre_and_unusable_density(pillow, x, y):
    with pillow.new("RGB", (32, 16), "red") as image, io.BytesIO() as output:
        image.save(output, format="BMP")
        data = bytearray(output.getvalue())
    struct.pack_into("<ii", data, 38, x, y)
    expected = (32 * 72 / 254, 16 * 72 / 508) if x > 0 else (32, 16)
    _assert_size(bytes(data), expected, dpi="auto")


def test_gif_pixel_aspect_ratio_does_not_supply_absolute_dpi(pillow):
    with pillow.new("RGB", (32, 16), "red") as image, io.BytesIO() as output:
        image.save(output, format="GIF")
        data = bytearray(output.getvalue())
    data[12] = 49
    _assert_size(bytes(data), (32, 16), dpi="auto")


@pytest.mark.parametrize("prefix", [True, False])
def test_webp_exif_resolution_with_both_metadata_prefix_forms(pillow, prefix):
    if not pytest.importorskip("PIL.features").check("webp"):
        pytest.skip("Pillow was built without WebP")
    payload = _exif()
    with pillow.new("RGB", (32, 16), "red") as image, io.BytesIO() as output:
        image.save(
            output,
            format="WEBP",
            lossless=True,
            exif=payload if prefix else payload[6:],
        )
        _assert_size(output.getvalue(), (16, 4), dpi="auto")


def test_jpeg_png_auto_dpi_does_not_require_pillow(jpeg, monkeypatch):
    monkeypatch.setitem(sys.modules, "PIL", None)
    _assert_size(_jfif(jpeg), (16, 4), dpi="auto")
    _assert_size(_png(), (32 * 72 / 254, 16 * 72 / 508), dpi="auto")


def test_duplicate_png_resolution_and_truncated_jfif_are_refused(jpeg):
    png = _png()
    duplicate = png[:54] + png[33:54] + png[54:]
    with pytest.raises(PdfValidationException, match="duplicate PNG"):
        _convert(duplicate, dpi="auto")
    end = 4 + struct.unpack_from(">H", jpeg, 4)[0]
    truncated = jpeg[:2] + b"\xff\xe0\x00\x07JFIF\x00" + jpeg[end:]
    with pytest.raises(PdfValidationException, match="JFIF"):
        _convert(truncated, dpi="auto")


@pytest.mark.parametrize(
    "value", ["AUTO", "auto ", "invalid", None, 0, float("nan"), float("inf")]
)
def test_invalid_dpi_is_refused_before_source_reads(value):
    class UnreadableSource(ByteArrayDataSource):
        def read_bytes(self, **options):
            pytest.fail("Invalid dpi must fail before reading inputs")

    with pytest.raises(AsposePdfException, match="dpi"):
        ImagesToPdf().process(
            ImagesToPdfOptions(dpi=value).add_input(UnreadableSource(b""))
        )


@pytest.mark.parametrize("mutation", ["offset", "type", "count"])
def test_bad_exif_resolution_structure_fails_but_numeric_mode_ignores_it(
    jpeg, mutation
):
    exif = bytearray(_exif())
    if mutation == "offset":
        struct.pack_into("<I", exif, 6 + 8 + 2 + 8, 0xFFFFFFFF)
    elif mutation == "type":
        struct.pack_into("<H", exif, 6 + 8 + 2 + 2, 3)
    else:
        struct.pack_into("<I", exif, 6 + 8 + 2 + 4, 2)
    data = jpeg[:2] + _segment(exif) + jpeg[2:]
    with pytest.raises(PdfValidationException, match="resolution"):
        _convert(data, dpi="auto")
    _assert_size(data, (32, 16))


@pytest.mark.parametrize("single", [True, False])
def test_late_metadata_failure_leaves_output_files_unchanged(jpeg, single, tmp_path):
    options = ImagesToPdfOptions(dpi="auto", single_document=single)
    options.add_input(ByteArrayDataSource(_jfif(jpeg)))
    bad = _png()
    bad = bad[:50] + bytes([bad[50] ^ 1]) + bad[51:]
    options.add_input(ByteArrayDataSource(bad))
    outputs = (
        [tmp_path / "one.pdf"]
        if single
        else [tmp_path / "one.pdf", tmp_path / "two.pdf"]
    )
    for path in outputs:
        path.write_bytes(b"original")
        options.add_output(FileDataSource(path))
    with pytest.raises(PdfValidationException, match="checksum"):
        ImagesToPdf().process(options)
    assert all(path.read_bytes() == b"original" for path in outputs)


@pytest.mark.parametrize(
    "kind,limits,context",
    [
        ("jpeg", PdfLoadLimits(max_object_bytes=71), "JPEG EXIF metadata bytes"),
        ("jpeg", PdfLoadLimits(max_container_items=2), "JPEG EXIF directory entries"),
        ("png", PdfLoadLimits(max_container_items=1), "PNG resolution chunk count"),
        ("png", PdfLoadLimits(max_codec_work_bytes=40), "PNG resolution header scan"),
    ],
)
def test_resolution_metadata_respects_resource_limits(jpeg, kind, limits, context):
    data = _tagged(jpeg) if kind == "jpeg" else _png()
    with pytest.raises(PdfResourceLimitException, match=context):
        _convert(data, dpi="auto", limits=limits)
