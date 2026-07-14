"""Resource-limit regression tests for CCITT and JBIG2 decoding."""

from __future__ import annotations

import struct

import pytest

from aspose_pdf import PdfLoadLimits, PdfResourceLimitException
from aspose_pdf.engine.ccitt import Decoder as CCITTDecoder
from aspose_pdf.engine.ccitt import decode_group4
from aspose_pdf.engine.jbig2 import Decoder as JBIG2Decoder
from aspose_pdf.exceptions import PdfParseException


def _jbig2_region(width: int, height: int, payload: bytes = b"", *, mmr=True) -> bytes:
    region = bytearray(13)
    region[0] = 1 if mmr else 0
    region[5:9] = struct.pack(">I", width)
    region[9:13] = struct.pack(">I", height)
    segment = bytes(region) + payload
    return (
        b"\x97JBIG2\x00\r"
        + b"\x00\x00\x00\x00"
        + bytes([0, 0x08])
        + struct.pack(">I", len(segment))
        + segment
    )


def test_ccitt_rejects_declared_pixel_count_before_row_allocation():
    limits = PdfLoadLimits(max_image_pixels=16)

    with pytest.raises(PdfResourceLimitException, match="max_image_pixels=16"):
        CCITTDecoder.decode(
            b"",
            {"K": -1, "Columns": 17, "Rows": 0},
            limits=limits,
        )


def test_ccitt_rejects_declared_decoded_size():
    limits = PdfLoadLimits(max_decoded_stream_bytes=2)

    with pytest.raises(
        PdfResourceLimitException, match="max_decoded_stream_bytes=2"
    ):
        decode_group4(b"", 8, 3, limits=limits)


def test_ccitt_run_cannot_expand_beyond_declared_row():
    row = bytearray()

    with pytest.raises(PdfParseException, match="declared row width"):
        CCITTDecoder._append_run(row, 9, 0, 8)

    assert row == b""


def test_jbig2_rejects_hostile_region_dimensions():
    limits = PdfLoadLimits(max_image_pixels=20)

    with pytest.raises(PdfResourceLimitException, match="max_image_pixels=20"):
        JBIG2Decoder.decode(_jbig2_region(5, 5), limits=limits)


def test_jbig2_rejects_raw_region_payload_over_decoded_limit():
    limits = PdfLoadLimits(max_decoded_stream_bytes=2)

    with pytest.raises(
        PdfResourceLimitException, match="max_decoded_stream_bytes=2"
    ):
        JBIG2Decoder.decode(
            _jbig2_region(1, 1, b"abc", mmr=False),
            limits=limits,
        )


def test_jbig2_does_not_swallow_nested_resource_limit(monkeypatch):
    def fail_decode(payload, width, height, *, limits=None):
        raise PdfResourceLimitException("nested CCITT limit")

    monkeypatch.setattr("aspose_pdf.engine.jbig2.decode_group4", fail_decode)

    with pytest.raises(PdfResourceLimitException, match="nested CCITT limit"):
        JBIG2Decoder.decode(
            _jbig2_region(1, 1),
            limits=PdfLoadLimits(max_image_pixels=10),
        )
