"""Resource limits for authored images and web-font wrappers."""

from __future__ import annotations

import struct
import zlib
from dataclasses import replace

import pytest

from aspose_pdf import Document, PdfLoadLimits, PdfResourceLimitException
from aspose_pdf.engine import woff2
from aspose_pdf.engine.image_export import write_png
from aspose_pdf.engine.woff import decode as decode_woff


def _limits(**changes: int | None) -> PdfLoadLimits:
    return replace(PdfLoadLimits.unlimited(), **changes)


def _wrapper_header(signature: bytes, total_sfnt_size: int) -> bytes:
    header_size = 48 if signature == b"wOF2" else 44
    data = bytearray(header_size)
    data[:4] = signature
    struct.pack_into(">I", data, 4, 0x00010000)
    struct.pack_into(">I", data, 8, header_size)
    struct.pack_into(">H", data, 12, 1)
    struct.pack_into(">I", data, 16, total_sfnt_size)
    return bytes(data)


def test_authored_png_rejects_pixel_count_from_ihdr() -> None:
    png = write_png(10, 10, "RGB", b"\x00" * 300)
    document = Document(limits=_limits(max_image_pixels=99))
    page = document.pages.add()

    with pytest.raises(PdfResourceLimitException, match="max_image_pixels=99"):
        page.add_image(png, 0, 0)


def test_authored_png_rejects_filtered_output_before_inflate() -> None:
    png = write_png(10, 10, "RGB", b"\x00" * 300)
    document = Document(limits=_limits(max_decoded_stream_bytes=309))
    page = document.pages.add()

    with pytest.raises(
        PdfResourceLimitException, match="max_decoded_stream_bytes=309"
    ):
        page.add_image(png, 0, 0)


def test_authored_png_applies_compression_ratio_limit() -> None:
    png = write_png(10, 10, "RGB", b"\x00" * 300)
    document = Document(limits=_limits(max_compression_ratio=2))
    page = document.pages.add()

    with pytest.raises(PdfResourceLimitException, match="max_compression_ratio=2"):
        page.add_image(png, 0, 0)


def test_authored_png_rejects_codec_working_set() -> None:
    png = write_png(10, 10, "RGB", b"\x00" * 300)
    document = Document(limits=_limits(max_codec_work_bytes=1_000))
    page = document.pages.add()

    with pytest.raises(PdfResourceLimitException, match="max_codec_work_bytes=1000"):
        page.add_image(png, 0, 0)


def test_authored_image_path_checks_size_before_read(tmp_path) -> None:
    image_path = tmp_path / "oversized.raw"
    image_path.write_bytes(b"12345678901")
    document = Document(limits=_limits(max_input_bytes=10))
    page = document.pages.add()

    with pytest.raises(PdfResourceLimitException, match="max_input_bytes=10"):
        page.add_image(
            image_path,
            0,
            0,
            pixel_width=1,
            pixel_height=1,
            color_space="DeviceGray",
        )


@pytest.mark.parametrize("signature", [b"wOFF", b"wOF2"])
def test_woff_wrappers_reject_declared_sfnt_before_decode(signature: bytes) -> None:
    wrapper = _wrapper_header(signature, total_sfnt_size=101)

    with pytest.raises(
        PdfResourceLimitException, match="max_decoded_stream_bytes=100"
    ):
        decode_woff(wrapper, limits=_limits(max_decoded_stream_bytes=100))


def test_authored_woff_uses_document_limits() -> None:
    wrapper = _wrapper_header(b"wOFF", total_sfnt_size=101)
    document = Document(limits=_limits(max_decoded_stream_bytes=100))
    page = document.pages.add()

    with pytest.raises(
        PdfResourceLimitException, match="max_decoded_stream_bytes=100"
    ):
        page.add_text("A", 10, 10, font=wrapper)


def test_woff1_stops_table_expansion_at_declared_size() -> None:
    compressed = zlib.compress(b"A" * 1_000)
    assert len(compressed) < 20
    total_sfnt_size = 12 + 16 + 20
    offset = 44 + 20
    length = offset + len(compressed)
    header = struct.pack(
        ">4sIIHHIHHIIIII",
        b"wOFF",
        0x00010000,
        length,
        1,
        0,
        total_sfnt_size,
        1,
        0,
        0,
        0,
        0,
        0,
        0,
    )
    entry = struct.pack(">4sIIII", b"name", offset, len(compressed), 20, 0)

    with pytest.raises(PdfResourceLimitException, match="declared uncompressed"):
        decode_woff(header + entry + compressed, limits=PdfLoadLimits.unlimited())


def test_woff2_stops_streaming_output_at_declared_size(monkeypatch) -> None:
    class FakeDecoder:
        def process(self, _data: bytes) -> bytes:
            return b"A" * 100

        def is_finished(self) -> bool:
            return True

    class FakeBrotli:
        Decompressor = FakeDecoder

    header = bytearray(_wrapper_header(b"wOF2", total_sfnt_size=32))
    struct.pack_into(">I", header, 8, 51)
    struct.pack_into(">I", header, 20, 1)
    wrapper = bytes(header) + b"\x00\x01" + b"x"
    monkeypatch.setattr(woff2, "_import_brotli", lambda: FakeBrotli)

    with pytest.raises(PdfResourceLimitException, match="declared size"):
        woff2.decode(wrapper, limits=PdfLoadLimits.unlimited())
