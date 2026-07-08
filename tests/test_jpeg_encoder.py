"""Tests for the dependency-free baseline JPEG encoder.

The encoder is validated two ways: its output is decoded back by the package's
own pure-Python decoder (:mod:`aspose_pdf.engine.dct`) and, when available, by
Pillow as an independent oracle. Pixel error must stay small for smooth images.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf.engine import dct
from aspose_pdf.engine import jpeg_encoder as je


def _gradient_rgb(w: int, h: int) -> bytes:
    s = bytearray(w * h * 3)
    for y in range(h):
        for x in range(w):
            i = (y * w + x) * 3
            s[i] = (x * 255) // max(1, w - 1)
            s[i + 1] = (y * 255) // max(1, h - 1)
            s[i + 2] = ((x + y) * 255) // max(1, w + h - 2)
    return bytes(s)


def _gradient_gray(w: int, h: int) -> bytes:
    return bytes((x * 255) // max(1, w - 1) for y in range(h) for x in range(w))


def _mae(a: bytes, b: bytes) -> float:
    return sum(abs(p - q) for p, q in zip(a, b)) / len(a)


def test_rgb_roundtrip_through_own_decoder():
    w, h = 64, 48
    src = _gradient_rgb(w, h)
    jpg = je.encode(w, h, 3, src, quality=85)
    assert jpg.startswith(b"\xFF\xD8") and jpg.endswith(b"\xFF\xD9")

    decoded = dct.decode(jpg)
    assert decoded is not None
    assert (decoded.width, decoded.height, decoded.components) == (w, h, 3)
    assert _mae(decoded.samples, src) < 6.0  # smooth gradient -> small error


def test_grayscale_roundtrip():
    w, h = 50, 40
    src = _gradient_gray(w, h)
    jpg = je.encode(w, h, 1, src, quality=85)
    decoded = dct.decode(jpg)
    assert decoded is not None
    assert decoded.components == 1
    assert (decoded.width, decoded.height) == (w, h)
    assert _mae(decoded.samples, src) < 3.0


def test_odd_dimensions_preserved():
    # Dimensions that are not multiples of the 16x16 4:2:0 MCU must still decode
    # back to the exact width/height (internal padding is cropped).
    for w, h in [(37, 21), (17, 17), (1, 1), (16, 1), (1, 16)]:
        jpg = je.encode(w, h, 3, _gradient_rgb(w, h), quality=80)
        decoded = dct.decode(jpg)
        assert decoded is not None
        assert (decoded.width, decoded.height) == (w, h)


def test_lower_quality_is_smaller():
    w, h = 96, 96
    src = _gradient_rgb(w, h)
    big = je.encode(w, h, 3, src, quality=90)
    small = je.encode(w, h, 3, src, quality=30)
    assert len(small) < len(big)


def test_quality_is_clamped():
    src = _gradient_gray(16, 16)
    # Out-of-range quality is clamped, not rejected, so encoding still succeeds.
    assert dct.decode(je.encode(16, 16, 1, src, quality=0)) is not None
    assert dct.decode(je.encode(16, 16, 1, src, quality=1000)) is not None


def test_invalid_inputs_raise():
    with pytest.raises(ValueError):
        je.encode(8, 8, 2, b"\x00" * (8 * 8 * 2))  # unsupported component count
    with pytest.raises(ValueError):
        je.encode(0, 8, 1, b"")  # invalid dimensions
    with pytest.raises(ValueError):
        je.encode(8, 8, 3, b"\x00" * 10)  # buffer too small


def test_pillow_decodes_our_output():
    Image = pytest.importorskip("PIL.Image")
    w, h = 80, 64
    src = _gradient_rgb(w, h)
    jpg = je.encode(w, h, 3, src, quality=85)
    im = Image.open(io.BytesIO(jpg))
    assert im.size == (w, h)
    pixels = im.convert("RGB").tobytes()
    assert _mae(pixels, src) < 6.0


# ---------------------------------------------------------------------------
# Optimized Huffman tables (Annex K.2)
# ---------------------------------------------------------------------------


def test_optimized_is_smaller_and_roundtrips():
    w, h = 48, 32
    src = _gradient_rgb(w, h)
    opt = je.encode(w, h, 3, src, quality=80)
    fixed = je.encode(w, h, 3, src, quality=80, optimize=False)
    assert len(opt) < len(fixed)  # per-image tables beat the fixed Annex K ones
    for jpg in (opt, fixed):
        decoded = dct.decode(jpg)
        assert decoded is not None
        assert (decoded.width, decoded.height, decoded.components) == (w, h, 3)


def test_optimal_table_is_a_valid_prefix_code():
    from aspose_pdf.engine.jpeg_encoder import _optimal_table

    freq = [0] * 256
    for symbol, count in [(0, 100), (1, 50), (2, 25), (0x11, 10), (0xF0, 5)]:
        freq[symbol] = count
    bits, vals = _optimal_table(freq)
    assert len(bits) == 16  # code lengths capped at 16 bits
    assert sum(bits) == len(vals)  # one code per symbol
    assert set(vals) == {0, 1, 2, 0x11, 0xF0}  # exactly the counted symbols
    # A valid prefix code (Kraft sum <= 1); the reserved all-ones codeword is
    # deliberately left free, so the sum is just under 1.
    kraft = sum(n / (1 << length) for length, n in enumerate(bits, 1))
    assert 0.0 < kraft < 1.0


def test_optimal_table_single_symbol():
    from aspose_pdf.engine.jpeg_encoder import _optimal_table

    freq = [0] * 256
    freq[7] = 1
    bits, vals = _optimal_table(freq)
    assert vals == [7] and sum(bits) == 1


def test_solid_colour_image_roundtrips():
    w, h = 32, 24
    src = bytes([120, 60, 200]) * (w * h)
    decoded = dct.decode(je.encode(w, h, 3, src, quality=90))
    assert decoded is not None
    assert _mae(decoded.samples, src) < 4.0  # near-constant output


def test_optimize_false_embeds_standard_tables():
    jpg = je.encode(16, 16, 1, _gradient_gray(16, 16), optimize=False)
    # The standard DC luma BITS (Annex K.3) appear verbatim in a DHT segment.
    assert bytes([0, 1, 5, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0]) in jpg
