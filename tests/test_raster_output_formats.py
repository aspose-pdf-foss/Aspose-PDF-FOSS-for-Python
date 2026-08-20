"""Output formats for a rendered page: PNG, TIFF and JPEG, colour and grey.

A rendered page used to be encodable only as PNG or as an *uncompressed* RGB
TIFF, which for a 300 dpi A4 page is tens of megabytes. These tests pin the
compressed, multi-page and greyscale/bilevel forms, and read the encoded bytes
back through the file format rather than through the encoder that wrote them.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.dct import decode as decode_jpeg
from aspose_pdf.engine.image_export import PNG_MAGIC, TiffPage, write_tiff
from aspose_pdf.engine.rasterizer import RasterizedPage
from aspose_pdf.exceptions import PdfValidationException


def _page_raster(dpi: float = 72.0, *, width: int = 64, height: int = 48):
    """A small synthetic raster: a black bar on white, at *dpi*.

    Encoder behaviour does not depend on how the pixels were produced, and a
    real page render at a useful resolution costs seconds; the one test that
    needs a genuinely rendered page renders a page.
    """
    pixels = bytearray(b"\xff" * (width * height * 3))
    for y in range(height // 3, 2 * height // 3):
        start = (y * width + width // 4) * 3
        end = (y * width + 3 * width // 4) * 3
        pixels[start:end] = b"\x00" * (end - start)
    return RasterizedPage(
        width=width, height=height, pixels=bytes(pixels), dpi=dpi
    )


def _read_ifds(data: bytes) -> list[dict[int, tuple[int, int, int]]]:
    """Walk the IFD chain of a little-endian TIFF and return the tag maps."""
    assert data[:4] == b"II*\x00"
    offset = struct.unpack_from("<I", data, 4)[0]
    ifds = []
    while offset:
        count = struct.unpack_from("<H", data, offset)[0]
        tags = {}
        for i in range(count):
            tag, typ, cnt, value = struct.unpack_from("<HHII", data, offset + 2 + i * 12)
            tags[tag] = (typ, cnt, value)
        ifds.append(tags)
        offset = struct.unpack_from("<I", data, offset + 2 + count * 12)[0]
        assert len(ifds) < 64, "IFD chain does not terminate"
    return ifds


def _strip_bytes(data: bytes, tags: dict[int, tuple[int, int, int]]) -> bytes:
    strip = data[tags[273][2] : tags[273][2] + tags[279][2]]
    return zlib.decompress(strip) if tags[259][2] == 8 else strip


def test_tiff_defaults_to_deflate_and_round_trips_pixels() -> None:
    raster = _page_raster()
    compressed = raster.to_tiff()
    uncompressed = raster.to_tiff(compression="none")

    assert len(compressed) * 4 < len(uncompressed), "deflate must actually compress"
    tags = _read_ifds(compressed)[0]
    assert tags[259][2] == 8  # Compression: Adobe Deflate
    assert tags[262][2] == 2  # PhotometricInterpretation: RGB
    assert tags[256][2] == raster.width
    assert tags[257][2] == raster.height
    assert _strip_bytes(compressed, tags) == raster.pixels
    assert _strip_bytes(uncompressed, _read_ifds(uncompressed)[0]) == raster.pixels


def test_tiff_records_the_render_resolution() -> None:
    raster = _page_raster(dpi=150.0)
    data = raster.to_tiff()
    tags = _read_ifds(data)[0]
    numerator, denominator = struct.unpack_from("<II", data, tags[282][2])
    assert (numerator, denominator) == (150, 1)
    assert tags[296][2] == 2  # ResolutionUnit: inch


def test_grey_and_bilevel_tiff_shrink_and_declare_their_form() -> None:
    raster = _page_raster()
    grey = raster.to_tiff(mode="gray")
    bilevel = raster.to_tiff(mode="bilevel")

    grey_tags = _read_ifds(grey)[0]
    assert grey_tags[277][2] == 1  # SamplesPerPixel
    assert grey_tags[258][2] == 8  # BitsPerSample
    assert _strip_bytes(grey, grey_tags) == raster.to_gray()

    bilevel_tags = _read_ifds(bilevel)[0]
    assert bilevel_tags[258][2] == 1
    assert len(bilevel) < len(grey)


def test_multi_page_tiff_chains_one_ifd_per_page(tmp_path: Path) -> None:
    document = Document()
    for index in range(3):
        document.pages.add().add_text(f"page {index}", 72, 700)
    out = document.save_as_tiff(tmp_path / "all.tif", dpi=72)
    document.dispose()

    ifds = _read_ifds(out.read_bytes())
    assert len(ifds) == 3
    for index, tags in enumerate(ifds):
        assert tags[254][2] == 2  # NewSubfileType: page of a multi-page file
        assert tags[297][2] & 0xFFFF == index + 1  # PageNumber
        assert tags[297][2] >> 16 == 3


def test_multi_page_tiff_honours_the_page_selection(tmp_path: Path) -> None:
    document = Document()
    for _ in range(4):
        document.pages.add()
    out = document.save_as_tiff(tmp_path / "some.tif", pages=[2, 0])
    document.dispose()
    assert len(_read_ifds(out.read_bytes())) == 2


def test_multi_page_tiff_needs_a_page(tmp_path: Path) -> None:
    document = Document()
    document.pages.add()
    with pytest.raises(PdfValidationException):
        document.save_as_tiff(tmp_path / "none.tif", pages=[])
    document.dispose()


def test_jpeg_output_decodes_back_to_the_page(tmp_path: Path) -> None:
    raster = _page_raster(dpi=96.0)
    data = raster.to_jpeg(quality=90)
    assert data[:2] == b"\xff\xd8"

    decoded = decode_jpeg(data)
    assert decoded is not None
    assert (decoded.width, decoded.height) == (raster.width, raster.height)
    assert decoded.components == 3
    # The top row is white and the middle band is black; lossy round-tripping
    # keeps both well clear of the midpoint.
    row = raster.width * 3
    middle = (raster.height // 2) * row + (raster.width // 2) * 3
    assert sum(decoded.samples[:row]) / row > 240
    assert sum(decoded.samples[middle : middle + 3]) / 3 < 40


def test_jpeg_records_the_render_resolution() -> None:
    data = _page_raster(dpi=150.0).to_jpeg()
    marker = data.index(b"JFIF\x00")
    units, x_density, y_density = struct.unpack_from(">BHH", data, marker + 7)
    assert units == 1  # dots per inch
    assert (x_density, y_density) == (150, 150)


def test_grey_jpeg_is_single_component() -> None:
    decoded = decode_jpeg(_page_raster().to_jpeg(mode="gray"))
    assert decoded is not None
    assert decoded.components == 1


def test_bilevel_jpeg_is_rejected() -> None:
    with pytest.raises(PdfValidationException, match="no bilevel mode"):
        _page_raster().to_jpeg(mode="bilevel")


def test_bilevel_png_is_one_bit_deep() -> None:
    raster = _page_raster()
    data = raster.to_png(mode="bilevel")
    assert data[:8] == PNG_MAGIC
    bit_depth, colour_type = struct.unpack_from(">BB", data, 24)
    assert (bit_depth, colour_type) == (1, 0)
    assert len(data) < len(raster.to_png())


def test_bilevel_threshold_selects_black_and_white() -> None:
    """A grey ramp: every pixel at or above the threshold becomes a set bit."""
    ramp = bytes(b for value in (0, 32, 64, 96, 128, 160, 192, 255) for b in (value,) * 3)
    raster = RasterizedPage(width=8, height=1, pixels=ramp, dpi=72.0)
    assert raster.to_bilevel(threshold=128) == bytes([0b00001111])
    assert raster.to_bilevel(threshold=64) == bytes([0b00111111])
    assert raster.to_bilevel(threshold=1) == bytes([0b01111111])


def test_bilevel_rows_are_padded_to_whole_bytes() -> None:
    raster = _page_raster(width=12, height=3)
    assert len(raster.to_bilevel()) == 2 * 3


def test_rendered_page_saves_every_format(tmp_path: Path) -> None:
    """End to end: a real page render through each public save path."""
    document = Document()
    page = document.pages.add()
    page.add_text("Raster output", 72, 700)
    png = page.save_as_image(tmp_path / "page.png", dpi=18)
    tiff = page.save_as_image(tmp_path / "page.tif", dpi=18, mode="bilevel")
    jpeg = document.save_page_as_image(0, tmp_path / "page.jpg", dpi=18, quality=70)
    document.dispose()

    assert png.read_bytes()[:8] == PNG_MAGIC
    assert _read_ifds(tiff.read_bytes())[0][258][2] == 1
    assert jpeg.read_bytes()[:2] == b"\xff\xd8"


@pytest.mark.parametrize("suffix", [".png", ".tif", ".tiff", ".jpg", ".jpeg"])
def test_save_dispatches_on_the_suffix(tmp_path: Path, suffix: str) -> None:
    out = _page_raster().save(tmp_path / f"page{suffix}")
    assert out.exists() and out.stat().st_size > 0
    head = out.read_bytes()[:4]
    if suffix == ".png":
        assert head == PNG_MAGIC[:4]
    elif suffix in (".tif", ".tiff"):
        assert head == b"II*\x00"
    else:
        assert head[:2] == b"\xff\xd8"


def test_save_rejects_an_unknown_suffix(tmp_path: Path) -> None:
    with pytest.raises(PdfValidationException, match="Unsupported raster output"):
        _page_raster().save(tmp_path / "page.webp")


def test_unknown_colour_mode_is_rejected() -> None:
    with pytest.raises(PdfValidationException, match="Unsupported raster colour mode"):
        _page_raster().to_png(mode="cmyk")


def test_unknown_tiff_compression_is_rejected() -> None:
    with pytest.raises(PdfValidationException, match="unsupported TIFF compression"):
        _page_raster().to_tiff(compression="lzw")


def test_write_tiff_rejects_an_empty_sequence() -> None:
    with pytest.raises(ValueError, match="at least one page"):
        write_tiff([])


def test_write_tiff_pads_short_page_data() -> None:
    """A truncated buffer is padded, never read past its end."""
    data = write_tiff([TiffPage(4, 4, "L", b"\x11\x22", 72.0)], compression="none")
    tags = _read_ifds(data)[0]
    assert _strip_bytes(data, tags) == b"\x11\x22" + b"\x00" * 14
