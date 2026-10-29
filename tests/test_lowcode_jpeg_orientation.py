"""Opt-in JPEG EXIF orientation preserves compressed images and layout rules."""

from __future__ import annotations

import io
import struct
import sys

import pytest

from aspose_pdf import Document, PageSize, PdfLoadLimits
from aspose_pdf.engine.image_export import write_png
from aspose_pdf.exceptions import PdfResourceLimitException, PdfValidationException
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
        for x, y, color in (
            (0, 8, (1, 0, 0)),
            (16, 8, (0, 1, 0)),
            (0, 0, (0, 0, 1)),
            (16, 0, (1, 1, 0)),
        ):
            page.draw_rectangle(x, y, 16, 8, fill_color=color)
        return page.render(antialias=False).to_jpeg(quality=100)


def _exif(orientation=1, *, byte_order="<", entries=None, next_ifd=0):
    if entries is None:
        entries = [(0x0112, 3, 1, orientation)]
    directory = bytearray(struct.pack(byte_order + "H", len(entries)))
    for tag, kind, count, value in entries:
        directory.extend(struct.pack(byte_order + "HHI", tag, kind, count))
        directory.extend(struct.pack(byte_order + "H", value) + b"\x00\x00")
    directory.extend(struct.pack(byte_order + "I", next_ifd))
    return (
        b"Exif\x00\x00"
        + (b"II" if byte_order == "<" else b"MM")
        + struct.pack(byte_order + "HI", 42, 8)
        + directory
    )


def _segment(payload, marker=0xE1):
    return b"\xff" + bytes([marker]) + struct.pack(">H", len(payload) + 2) + payload


def _tagged(jpeg, orientation=1, **options):
    return jpeg[:2] + _segment(_exif(orientation, **options)) + jpeg[2:]


def _convert(data, **options):
    return ImagesToPdf().process(
        ImagesToPdfOptions(**options).add_input(ByteArrayDataSource(data))
    )


def _corners(page):
    raster = page.render(antialias=False)
    return [
        raster.get_pixel(x, y)
        for x, y in (
            (4, 4),
            (raster.width - 5, 4),
            (4, raster.height - 5),
            (raster.width - 5, raster.height - 5),
        )
    ]


@pytest.mark.parametrize("byte_order", ["<", ">"])
@pytest.mark.parametrize(
    "orientation,order",
    [
        (1, [0, 1, 2, 3]),
        (2, [1, 0, 3, 2]),
        (3, [3, 2, 1, 0]),
        (4, [2, 3, 0, 1]),
        (5, [0, 2, 1, 3]),
        (6, [2, 0, 3, 1]),
        (7, [3, 1, 2, 0]),
        (8, [1, 3, 0, 2]),
    ],
)
def test_all_orientations_render_correctly_without_recompressing_jpeg(
    jpeg, orientation, order, byte_order, tmp_path
):
    data = _tagged(jpeg, orientation, byte_order=byte_order)
    with Document(_convert(jpeg)[0].to_array()) as reference:
        expected = [_corners(reference.pages[0])[index] for index in order]
    with Document(_convert(data, auto_orient=True)[0].to_array()) as document:
        page = document.pages[0]
        assert page.size == PageSize(*((16, 32) if orientation >= 5 else (32, 16)))
        assert page.rotation == 0
        assert _corners(page) == expected
        absorber = ImagePlacementAbsorber()
        absorber.visit(page)
        assert len(absorber.image_placements) == 1
        assert absorber.image_placements[0].image_data == data
        path = tmp_path / "oriented.pdf"
        document.save(path)
        with Document.open_streaming(path) as reopened:
            assert _corners(reopened.pages[0]) == expected


@pytest.mark.parametrize("orientation", range(2, 9))
def test_default_and_explicit_opt_out_keep_original_orientation(jpeg, orientation):
    data = _tagged(jpeg, orientation)
    with Document(_convert(jpeg)[0].to_array()) as reference:
        expected = _corners(reference.pages[0])
    for options in ({}, {"auto_orient": False}):
        with Document(_convert(data, **options)[0].to_array()) as document:
            assert document.pages[0].size == PageSize(32, 16)
            assert _corners(document.pages[0]) == expected


@pytest.mark.parametrize("kind", ["bytes", "file", "stream"])
def test_orientation_uses_signatures_and_supports_all_input_sources(
    jpeg, kind, tmp_path
):
    data = _tagged(jpeg, 6)
    path = tmp_path / "photo.png"
    path.write_bytes(data)
    with io.BytesIO(data) as stream:
        inputs = {
            "bytes": ByteArrayDataSource(data),
            "file": FileDataSource(path),
            "stream": StreamDataSource(stream),
        }
        results = ImagesToPdf().process(
            ImagesToPdfOptions(auto_orient=True).add_input(inputs[kind])
        )
        assert not stream.closed
    with Document(results[0].to_array()) as document:
        assert document.pages[0].size == PageSize(16, 32)
    assert path.read_bytes() == data


@pytest.mark.parametrize("orientation", range(2, 9))
@pytest.mark.parametrize("dpi", [72, 144])
def test_oriented_bounds_are_fitted_and_centred_inside_fixed_page(
    jpeg, orientation, dpi
):
    data = _tagged(jpeg, orientation)
    with Document(
        _convert(data, auto_orient=True, dpi=dpi, page_size=(20, 40), margin=4)[
            0
        ].to_array()
    ) as document:
        page = document.pages[0]
        assert page.size == PageSize(20, 40)
        width, height = (16, 32) if orientation >= 5 else (32, 16)
        width, height = width * 72 / dpi, height * 72 / dpi
        scale = min(12 / width, 32 / height, 1)
        width, height = width * scale, height * scale
        absorber = ImagePlacementAbsorber()
        absorber.visit(page)
        rect = absorber.image_placements[0].rectangle
        assert (rect.x, rect.y, rect.width, rect.height) == pytest.approx(
            ((20 - width) / 2, (40 - height) / 2, width, height)
        )


def test_oriented_image_sized_page_respects_dpi(jpeg):
    with Document(
        _convert(_tagged(jpeg, 6), auto_orient=True, dpi=144)[0].to_array()
    ) as document:
        assert document.pages[0].size == PageSize(8, 16)


@pytest.mark.parametrize("single", [True, False])
def test_mixed_inputs_preserve_order_and_per_image_orientation(jpeg, single):
    options = ImagesToPdfOptions(auto_orient=True, single_document=single)
    for data in (
        _tagged(jpeg, 6),
        write_png(3, 2, "RGB", bytes([0, 255, 0] * 6)),
        _tagged(jpeg, 2),
        _tagged(jpeg, 8),
    ):
        options.add_input(ByteArrayDataSource(data))
    results = ImagesToPdf().process(options)
    assert len(results) == (1 if single else 4)
    sizes = []
    for result in results:
        with Document(result.to_array()) as document:
            sizes.extend(page.size for page in document.pages)
    assert sizes == [
        PageSize(16, 32),
        PageSize(3, 2),
        PageSize(32, 16),
        PageSize(16, 32),
    ]


def test_orientation_without_pillow_and_without_pixel_decoding(jpeg, monkeypatch):
    monkeypatch.setitem(sys.modules, "PIL", None)
    from aspose_pdf.engine import dct

    def forbidden(*args, **kwargs):
        pytest.fail("JPEG orientation must not decode or re-encode pixels")

    monkeypatch.setattr(dct, "decode", forbidden)
    data = _tagged(jpeg, 7)
    with Document(_convert(data, auto_orient=True)[0].to_array()) as document:
        assert document.pages[0].size == PageSize(16, 32)
        absorber = ImagePlacementAbsorber()
        absorber.visit(document.pages[0])
        assert absorber.image_placements[0].image_data == data


def test_missing_orientation_non_exif_app1_and_thumbnail_tags_are_ignored(jpeg):
    no_orientation = _exif(entries=[], next_ifd=14)
    thumbnail = struct.pack("<HHHIH", 1, 0x0112, 3, 1, 6) + b"\x00" * 6
    payloads = [
        b"http://ns.adobe.com/xap/1.0/\x00<metadata/>",
        _exif(entries=[]),
        no_orientation + thumbnail,
        b"Exif\x00\x00II" + struct.pack("<HI", 42, 0),
    ]
    with Document(_convert(jpeg)[0].to_array()) as reference:
        expected = _corners(reference.pages[0])
    for payload in payloads:
        data = jpeg[:2] + _segment(payload) + jpeg[2:]
        with Document(_convert(data, auto_orient=True)[0].to_array()) as document:
            assert document.pages[0].size == PageSize(32, 16)
            assert _corners(document.pages[0]) == expected


@pytest.mark.parametrize("value", [None, 0, 1, "true", [], object()])
def test_invalid_option_is_refused_before_reading_inputs_or_writing_outputs(value):
    class UnreadableSource(ByteArrayDataSource):
        def read_bytes(self, **options):
            pytest.fail("Invalid options must fail before reading inputs")

    output = ByteArrayDataSource(b"original")
    options = ImagesToPdfOptions().add_input(UnreadableSource(b""))
    options.auto_orient = value
    options.add_output(output)
    with pytest.raises(PdfValidationException, match="auto_orient"):
        ImagesToPdf().process(options)
    assert output.data == b"original"


@pytest.mark.parametrize(
    "payload",
    [
        b"Exif\x00\x00II",
        b"Exif\x00\x00ZZ" + struct.pack("<HI", 42, 8),
        b"Exif\x00\x00II" + struct.pack("<HI", 43, 8),
        b"Exif\x00\x00II" + struct.pack("<HI", 42, 4),
        b"Exif\x00\x00II" + struct.pack("<HI", 42, 0xFFFFFFFF),
        b"Exif\x00\x00II" + struct.pack("<HIH", 42, 8, 65535),
        _exif(entries=[(0x0112, 4, 1, 6)]),
        _exif(entries=[(0x0112, 3, 2, 6)]),
        _exif(0),
        _exif(9),
        _exif(entries=[(0x0112, 3, 1, 1), (0x0112, 3, 1, 6)]),
    ],
)
def test_malformed_exif_raises_and_opt_out_keeps_legacy_import(jpeg, payload):
    data = jpeg[:2] + _segment(payload) + jpeg[2:]
    with pytest.raises(PdfValidationException, match="EXIF"):
        _convert(data, auto_orient=True)
    with Document(_convert(data)[0].to_array()) as document:
        assert document.pages[0].size == PageSize(32, 16)


@pytest.mark.parametrize("single", [True, False])
def test_later_bad_metadata_preserves_all_configured_outputs(jpeg, single, tmp_path):
    path = tmp_path / "photos.pdf"
    path.write_bytes(b"original")
    options = ImagesToPdfOptions(auto_orient=True, single_document=single)
    options.add_input(ByteArrayDataSource(_tagged(jpeg, 6)))
    options.add_input(
        ByteArrayDataSource(jpeg[:2] + _segment(b"Exif\x00\x00II") + jpeg[2:])
    )
    options.add_output(FileDataSource(path))
    output = ByteArrayDataSource(b"other")
    if not single:
        options.add_output(output)
    with pytest.raises(PdfValidationException, match="EXIF"):
        ImagesToPdf().process(options)
    assert path.read_bytes() == b"original"
    assert output.data == b"other"


@pytest.mark.parametrize(
    "suffix",
    [
        _segment(_exif(6)) + _segment(_exif(6)),
        b"\xff\xe1\xff\xffshort",
        b"\xff\xe1\x00\x01",
        b"\xff\xd8",
    ],
)
def test_ambiguous_metadata_or_truncated_headers_are_refused(jpeg, suffix):
    data = jpeg[:2] + suffix + jpeg[2:]
    with pytest.raises(PdfValidationException):
        _convert(data, auto_orient=True)


@pytest.mark.parametrize(
    "limits,entries,context",
    [
        (PdfLoadLimits(max_object_bytes=31), None, "JPEG EXIF metadata bytes"),
        (PdfLoadLimits(max_codec_work_bytes=30), None, "JPEG orientation header scan"),
        (PdfLoadLimits(max_container_items=1), None, "JPEG orientation header count"),
        (
            PdfLoadLimits(max_container_items=1),
            [(0x0100, 3, 1, 32), (0x0112, 3, 1, 6)],
            "JPEG EXIF directory entries",
        ),
        (PdfLoadLimits(max_image_pixels=511), None, "authored JPEG image pixels"),
    ],
)
def test_orientation_metadata_obeys_resource_limits(jpeg, limits, entries, context):
    with pytest.raises(PdfResourceLimitException, match=context):
        _convert(_tagged(jpeg, 6, entries=entries), auto_orient=True, limits=limits)


def test_marker_fill_bytes_and_xmp_before_exif_are_supported(jpeg):
    data = (
        jpeg[:2]
        + b"\xff"
        + _segment(b"http://ns.adobe.com/xap/1.0/\x00<metadata/>")
        + _segment(_exif(6))
        + jpeg[2:]
    )
    with Document(_convert(data, auto_orient=True)[0].to_array()) as document:
        assert document.pages[0].size == PageSize(16, 32)
