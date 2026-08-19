"""``Page.add_image`` accepts every PNG bit depth and Adam7 interlacing.

Both were hard rejections before: an ordinary interlaced PNG, or anything that
is not 8 bits per sample, raised instead of embedding. The fixtures build PNGs
byte by byte (including the interlaced passes) so the expected pixels are known
independently of the decoder under test.
"""

from __future__ import annotations

import struct
import zlib

import pytest

from aspose_pdf.engine.content_authoring import prepare_image
from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import _LoadBudget

_ADAM7 = (
    (0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4),
    (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2),
)


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def _pack_row(values: list[int], bit_depth: int) -> bytes:
    """Pack samples MSB-first, padding the row to a byte boundary."""
    if bit_depth == 8:
        return bytes(values)
    if bit_depth == 16:
        return b"".join(struct.pack(">H", v) for v in values)
    out = bytearray()
    acc = bits = 0
    for value in values:
        acc = (acc << bit_depth) | (value & ((1 << bit_depth) - 1))
        bits += bit_depth
        while bits >= 8:
            bits -= 8
            out.append((acc >> bits) & 0xFF)
    if bits:
        out.append((acc << (8 - bits)) & 0xFF)
    return bytes(out)


def _png(
    width: int,
    height: int,
    color_type: int,
    bit_depth: int,
    sample_at,
    *,
    palette: bytes | None = None,
    interlace: int = 0,
) -> bytes:
    """Build a PNG. *sample_at(x, y)* returns that pixel's channel list."""
    raw = bytearray()
    if interlace:
        for x0, y0, dx, dy in _ADAM7:
            pass_w = (width - x0 + dx - 1) // dx if width > x0 else 0
            pass_h = (height - y0 + dy - 1) // dy if height > y0 else 0
            if pass_w <= 0 or pass_h <= 0:
                continue
            for row in range(pass_h):
                values: list[int] = []
                for column in range(pass_w):
                    values.extend(sample_at(x0 + column * dx, y0 + row * dy))
                raw.append(0)  # filter type None
                raw.extend(_pack_row(values, bit_depth))
    else:
        for y in range(height):
            values = []
            for x in range(width):
                values.extend(sample_at(x, y))
            raw.append(0)
            raw.extend(_pack_row(values, bit_depth))

    ihdr = struct.pack(">IIBBBBB", width, height, bit_depth, color_type, 0, 0, interlace)
    out = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
    if palette is not None:
        out += _chunk(b"PLTE", palette)
    return out + _chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + _chunk(b"IEND", b"")


def _decode(data: bytes):
    return prepare_image(data, budget=_LoadBudget())


_W, _H = 9, 7  # deliberately not a multiple of 8, so every Adam7 pass is partial


def _rgb_at(x: int, y: int) -> list[int]:
    return [(x * 23) % 256, (y * 37) % 256, (x * y * 7) % 256]


def _gray_at(x: int, y: int) -> list[int]:
    return [(x * 11 + y * 5) % 256]


# --- interlacing -----------------------------------------------------------
@pytest.mark.parametrize("interlace", [0, 1])
def test_rgb8_round_trips_interlaced_and_not(interlace):
    image = _decode(_png(_W, _H, 2, 8, _rgb_at, interlace=interlace))
    assert (image.width, image.height) == (_W, _H)
    assert image.color_space == "DeviceRGB"
    expected = bytes(
        v for y in range(_H) for x in range(_W) for v in _rgb_at(x, y)
    )
    assert image.decoded_data == expected


def test_interlaced_and_progressive_agree():
    """The two encodings of the same image must decode identically."""
    plain = _decode(_png(_W, _H, 2, 8, _rgb_at, interlace=0))
    woven = _decode(_png(_W, _H, 2, 8, _rgb_at, interlace=1))
    assert plain.decoded_data == woven.decoded_data


def test_single_pixel_interlaced():
    """Only the first Adam7 pass is non-empty for a 1x1 image."""
    image = _decode(_png(1, 1, 2, 8, lambda x, y: [10, 20, 30], interlace=1))
    assert image.decoded_data == bytes([10, 20, 30])


# --- bit depths ------------------------------------------------------------
@pytest.mark.parametrize("depth", [1, 2, 4, 8])
def test_grayscale_sub_byte_depths_are_scaled_to_8_bit(depth):
    maxval = (1 << depth) - 1

    def sample(x, y):
        return [(x + y) % (maxval + 1)]

    image = _decode(_png(_W, _H, 0, depth, sample))
    assert image.color_space == "DeviceGray"
    expected = bytes(
        sample(x, y)[0] * 255 // maxval for y in range(_H) for x in range(_W)
    )
    assert image.decoded_data == expected


@pytest.mark.parametrize("depth", [1, 2, 4])
def test_sub_byte_depths_work_interlaced(depth):
    maxval = (1 << depth) - 1

    def sample(x, y):
        return [(x * 3 + y) % (maxval + 1)]

    plain = _decode(_png(_W, _H, 0, depth, sample, interlace=0))
    woven = _decode(_png(_W, _H, 0, depth, sample, interlace=1))
    assert plain.decoded_data == woven.decoded_data


def test_16_bit_keeps_the_high_byte():
    def sample(x, y):
        return [(x * 4097) % 65536, (y * 8193) % 65536, 0x1234]

    image = _decode(_png(_W, _H, 2, 16, sample))
    expected = bytes(
        (v >> 8) & 0xFF for y in range(_H) for x in range(_W) for v in sample(x, y)
    )
    assert image.decoded_data == expected


def test_16_bit_grayscale_interlaced():
    def sample(x, y):
        return [(x * 2571 + y * 511) % 65536]

    plain = _decode(_png(_W, _H, 0, 16, sample, interlace=0))
    woven = _decode(_png(_W, _H, 0, 16, sample, interlace=1))
    assert plain.decoded_data == woven.decoded_data


# --- palette ---------------------------------------------------------------
@pytest.mark.parametrize("depth", [1, 2, 4, 8])
def test_palette_indices_are_not_rescaled(depth):
    """An index is a lookup key, not an intensity — scaling it corrupts colour."""
    entries = min(1 << depth, 8)
    palette = bytes(
        v for i in range(entries) for v in (i * 30 % 256, 255 - i * 20, i * 11)
    )

    def sample(x, y):
        return [(x + y) % entries]

    image = _decode(_png(_W, _H, 3, depth, sample, palette=palette))
    assert image.color_space == "DeviceRGB"
    expected = bytearray()
    for y in range(_H):
        for x in range(_W):
            index = sample(x, y)[0]
            expected += palette[index * 3 : index * 3 + 3]
    assert image.decoded_data == bytes(expected)


def test_palette_interlaced():
    palette = bytes([0, 0, 0, 255, 0, 0, 0, 255, 0, 0, 0, 255])

    def sample(x, y):
        return [(x * y) % 4]

    plain = _decode(_png(_W, _H, 3, 4, sample, palette=palette, interlace=0))
    woven = _decode(_png(_W, _H, 3, 4, sample, palette=palette, interlace=1))
    assert plain.decoded_data == woven.decoded_data


# --- alpha channels --------------------------------------------------------
def test_gray_alpha_keeps_the_gray_channel():
    image = _decode(_png(_W, _H, 4, 8, lambda x, y: [(x * 9) % 256, 128]))
    assert image.color_space == "DeviceGray"
    assert image.decoded_data == bytes((x * 9) % 256 for _y in range(_H)
                                       for x in range(_W))


def test_rgba_drops_the_alpha_channel():
    image = _decode(_png(_W, _H, 6, 8, lambda x, y: [*_rgb_at(x, y), 64]))
    assert image.color_space == "DeviceRGB"
    expected = bytes(v for y in range(_H) for x in range(_W) for v in _rgb_at(x, y))
    assert image.decoded_data == expected


# --- rejections ------------------------------------------------------------
def test_bit_depth_illegal_for_the_colour_type_is_rejected():
    """ISO 15948 table 11: truecolour has no 1/2/4-bit form."""
    with pytest.raises(PdfValidationException, match="bit depth"):
        _decode(_png(4, 4, 2, 4, _rgb_at))


def test_unknown_interlace_method_is_rejected():
    data = bytearray(_png(4, 4, 2, 8, _rgb_at))
    ihdr = data.index(b"IHDR")
    data[ihdr + 4 + 12] = 2  # interlace byte
    payload = bytes(data[ihdr : ihdr + 4 + 13])
    data[ihdr + 4 + 13 : ihdr + 4 + 17] = struct.pack(
        ">I", zlib.crc32(payload) & 0xFFFFFFFF
    )
    with pytest.raises(PdfValidationException, match="interlace"):
        _decode(bytes(data))


def test_truncated_interlaced_data_is_rejected():
    data = _png(_W, _H, 2, 8, _rgb_at, interlace=1)
    idat = data.index(b"IDAT")
    length = struct.unpack(">I", data[idat - 4 : idat])[0]
    payload = data[idat + 4 : idat + 4 + length]
    short = zlib.compress(zlib.decompress(payload)[:10], 9)
    rebuilt = data[: idat - 4] + _chunk(b"IDAT", short) + _chunk(b"IEND", b"")
    with pytest.raises(PdfValidationException, match="truncated"):
        _decode(rebuilt)
