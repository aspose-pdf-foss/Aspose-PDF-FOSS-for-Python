"""Resource-limit regression tests for the DCT and JPX image codecs."""

from __future__ import annotations

import struct

import pytest

from aspose_pdf.engine import dct, jpx
from aspose_pdf.exceptions import PdfResourceLimitException
from aspose_pdf.images import ImagePlacement
from aspose_pdf.load_limits import PdfLoadLimits


def _jpeg_sof(width: int, height: int, components: int, *, progressive=False) -> bytes:
    segment = bytearray([8])
    segment.extend(struct.pack(">HHB", height, width, components))
    for component in range(components):
        segment.extend((component + 1, 0x11, 0))
    marker = 0xC2 if progressive else 0xC0
    return (
        b"\xff\xd8\xff"
        + bytes([marker])
        + struct.pack(">H", len(segment) + 2)
        + bytes(segment)
        + b"\xff\xd9"
    )


def test_dct_rejects_sof_pixel_count_before_scan_decode():
    data = _jpeg_sof(10, 10, 1)

    with pytest.raises(PdfResourceLimitException, match="max_image_pixels=99"):
        dct.decode(data, limits=PdfLoadLimits(max_image_pixels=99))


def test_dct_rejects_decoded_sample_count_from_sof():
    data = _jpeg_sof(10, 10, 3)

    with pytest.raises(
        PdfResourceLimitException, match="max_decoded_stream_bytes=299"
    ):
        dct.decode(
            data,
            limits=PdfLoadLimits(
                max_image_pixels=1_000,
                max_decoded_stream_bytes=299,
            ),
        )


def test_dct_rejects_progressive_coefficient_working_set():
    data = _jpeg_sof(8, 8, 1, progressive=True)

    with pytest.raises(PdfResourceLimitException, match="working set"):
        dct.decode(
            data,
            limits=PdfLoadLimits(
                max_image_pixels=1_000,
                max_decoded_stream_bytes=1_000,
                max_codec_work_bytes=500,
            ),
        )


class _FakePillowImage:
    mode = "RGB"

    def __init__(self, size: tuple[int, int]) -> None:
        self.size = size
        self.tobytes_called = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def getbands(self):
        return ("R", "G", "B")

    def tobytes(self):
        self.tobytes_called = True
        return b"\x00" * (self.size[0] * self.size[1] * 3)


def test_jpx_rejects_header_dimensions_before_tobytes(monkeypatch):
    if not jpx.HAS_PILLOW:
        pytest.skip("Pillow not installed")
    image = _FakePillowImage((10, 10))
    monkeypatch.setattr(jpx.Image, "open", lambda _stream: image)

    with pytest.raises(PdfResourceLimitException, match="max_image_pixels=99"):
        jpx.Decoder.decode(b"header", limits=PdfLoadLimits(max_image_pixels=99))

    assert not image.tobytes_called


def test_jpx_rejects_working_set_before_tobytes(monkeypatch):
    if not jpx.HAS_PILLOW:
        pytest.skip("Pillow not installed")
    image = _FakePillowImage((5, 5))
    monkeypatch.setattr(jpx.Image, "open", lambda _stream: image)

    with pytest.raises(PdfResourceLimitException, match="working set"):
        jpx.Decoder.decode(
            b"header",
            {},
            limits=PdfLoadLimits(
                max_image_pixels=1_000,
                max_decoded_stream_bytes=100,
                max_codec_work_bytes=100,
            ),
        )

    assert not image.tobytes_called


def test_image_placement_save_preserves_document_limits(tmp_path):
    placement = ImagePlacement(
        "Im1",
        b"\x00" * 100,
        meta={
            "width": 10,
            "height": 10,
            "color_space": "DeviceGray",
            "bits_per_component": 8,
        },
        limits=PdfLoadLimits(max_image_pixels=99),
    )

    with pytest.raises(PdfResourceLimitException, match="max_image_pixels=99"):
        placement.save(tmp_path / "image.png")
