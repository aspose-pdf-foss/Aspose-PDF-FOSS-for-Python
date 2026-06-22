"""Tests for the dependency-free baseline JPEG (DCTDecode) decoder.

Fixtures are produced with Pillow, which also acts as the oracle: decoding the
same JPEG with both must agree to within a small tolerance (JPEG decoders are
permitted to differ by a few sample levels). The module is skipped when Pillow
is unavailable.
"""

from __future__ import annotations

import io

import pytest

Image = pytest.importorskip("PIL.Image")

from aspose_pdf.engine import dct  # noqa: E402
from aspose_pdf.engine.dct import decode  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _smooth_rgb(w: int = 96, h: int = 72) -> "Image.Image":
    """A smooth, in-range image (so nearest-neighbour chroma upsampling is close)."""
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = (40 + x, 30 + y, 200 - (x * 2) // 3)
    return img


def _smooth_cmyk(w: int = 64, h: int = 48) -> "Image.Image":
    """A smooth CMYK image for the 4-component path."""
    img = Image.new("CMYK", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = (
                (20 + x * 2) % 200,
                (30 + y) % 200,
                (200 - (x + y)) % 200,
                ((x * y) // 40) % 120,
            )
    return img


def _jpeg(img: "Image.Image", **save_kwargs) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", **save_kwargs)
    return buf.getvalue()


def _diff(a: bytes, b: bytes) -> tuple[float, int]:
    assert len(a) == len(b), (len(a), len(b))
    deltas = [abs(x - y) for x, y in zip(a, b)]
    return sum(deltas) / len(deltas), max(deltas)


def _assert_matches_pillow(data: bytes, mode: str, *, mean_max: float, abs_max: int):
    decoded = decode(data)
    assert decoded is not None
    assert decoded.mode == mode
    reference = Image.open(io.BytesIO(data)).convert(mode)
    assert (decoded.width, decoded.height) == reference.size
    mean, peak = _diff(decoded.samples, reference.tobytes())
    assert mean <= mean_max, f"mean diff {mean} > {mean_max}"
    assert peak <= abs_max, f"max diff {peak} > {abs_max}"


def _assert_cmyk_matches_pillow(data: bytes, *, mean_max: float, abs_max: int):
    """4-component oracle: our standard CMYK must match Pillow's CMYK."""
    decoded = decode(data)
    assert decoded is not None
    assert decoded.mode == "CMYK" and decoded.components == 4
    reference = Image.open(io.BytesIO(data)).convert("CMYK")
    assert (decoded.width, decoded.height) == reference.size
    mean, peak = _diff(decoded.samples, reference.tobytes())
    assert mean <= mean_max, f"mean diff {mean} > {mean_max}"
    assert peak <= abs_max, f"max diff {peak} > {abs_max}"


# ---------------------------------------------------------------------------
# Decoding vs Pillow
# ---------------------------------------------------------------------------


def test_grayscale_matches_pillow():
    data = _jpeg(_smooth_rgb().convert("L"), quality=92)
    _assert_matches_pillow(data, "L", mean_max=1.0, abs_max=4)


def test_rgb_444_matches_pillow():
    data = _jpeg(_smooth_rgb(), quality=92, subsampling=0)
    _assert_matches_pillow(data, "RGB", mean_max=1.5, abs_max=6)


def test_rgb_422_matches_pillow():
    data = _jpeg(_smooth_rgb(), quality=90, subsampling=1)
    _assert_matches_pillow(data, "RGB", mean_max=2.0, abs_max=10)


def test_rgb_420_matches_pillow():
    data = _jpeg(_smooth_rgb(), quality=90, subsampling=2)
    _assert_matches_pillow(data, "RGB", mean_max=2.5, abs_max=12)


def test_restart_markers_are_handled():
    data = _jpeg(_smooth_rgb(), quality=92, subsampling=0, restart_marker_blocks=3)
    assert b"\xff\xdd" in data  # DRI marker present
    _assert_matches_pillow(data, "RGB", mean_max=1.5, abs_max=6)


def test_non_multiple_of_mcu_dimensions():
    # 70x53 is not a whole number of MCUs -> exercises edge cropping.
    data = _jpeg(_smooth_rgb(70, 53), quality=90, subsampling=2)
    decoded = decode(data)
    assert (decoded.width, decoded.height) == (70, 53)
    assert len(decoded.samples) == 70 * 53 * 3


def test_solid_colour_decodes_to_that_colour():
    img = Image.new("RGB", (32, 16), (180, 60, 90))
    decoded = decode(_jpeg(img, quality=95, subsampling=0))
    # Every pixel should be ~the constant colour (only the DC term is non-zero).
    for i in range(0, len(decoded.samples), 3):
        assert abs(decoded.samples[i] - 180) <= 4
        assert abs(decoded.samples[i + 1] - 60) <= 4
        assert abs(decoded.samples[i + 2] - 90) <= 4


# ---------------------------------------------------------------------------
# Progressive (SOF2) decoding vs Pillow
# ---------------------------------------------------------------------------


def test_progressive_rgb_444_matches_pillow():
    data = _jpeg(_smooth_rgb(), quality=92, progressive=True, subsampling=0)
    assert b"\xff\xc2" in data  # SOF2 progressive frame
    _assert_matches_pillow(data, "RGB", mean_max=1.5, abs_max=6)


def test_progressive_rgb_420_matches_pillow():
    data = _jpeg(_smooth_rgb(), quality=90, progressive=True, subsampling=2)
    _assert_matches_pillow(data, "RGB", mean_max=2.5, abs_max=12)


def test_progressive_grayscale_matches_pillow():
    data = _jpeg(_smooth_rgb().convert("L"), quality=92, progressive=True)
    _assert_matches_pillow(data, "L", mean_max=1.0, abs_max=4)


def test_progressive_non_mcu_dimensions():
    data = _jpeg(_smooth_rgb(70, 53), quality=90, progressive=True, subsampling=2)
    _assert_matches_pillow(data, "RGB", mean_max=2.5, abs_max=12)


def test_progressive_with_restart_markers():
    data = _jpeg(
        _smooth_rgb(), quality=92, progressive=True,
        subsampling=0, restart_marker_blocks=5,
    )
    assert b"\xff\xdd" in data  # DRI marker present
    _assert_matches_pillow(data, "RGB", mean_max=1.5, abs_max=6)


# ---------------------------------------------------------------------------
# CMYK / YCCK (4-component) decoding vs Pillow
# ---------------------------------------------------------------------------


def test_cmyk_baseline_matches_pillow():
    data = _jpeg(_smooth_cmyk(), quality=92)
    assert b"\xff\xc0" in data  # baseline frame
    _assert_cmyk_matches_pillow(data, mean_max=1.0, abs_max=6)


def test_cmyk_progressive_matches_pillow():
    data = _jpeg(_smooth_cmyk(), quality=92, progressive=True)
    _assert_cmyk_matches_pillow(data, mean_max=1.5, abs_max=8)


def test_cmyk_to_rgb_matches_pillow_rgb():
    from aspose_pdf.engine.image_export import cmyk_to_rgb

    data = _jpeg(_smooth_cmyk(), quality=92)
    decoded = decode(data)
    rgb = cmyk_to_rgb(decoded.samples)
    reference = Image.open(io.BytesIO(data)).convert("RGB").tobytes()
    mean, peak = _diff(rgb, reference)
    assert mean <= 2.0 and peak <= 10, (mean, peak)


# ---------------------------------------------------------------------------
# Unsupported / malformed -> None (caller falls back)
# ---------------------------------------------------------------------------


def test_defensive_on_garbage_and_truncation():
    assert decode(b"") is None
    assert decode(b"not a jpeg") is None
    assert decode(b"\xff\xd8\xff\xd9") is None  # SOI + EOI, no frame
    truncated = _jpeg(_smooth_rgb(), quality=90)[:40]
    assert decode(truncated) is None or isinstance(decode(truncated), dct.DecodedJpeg)


# ---------------------------------------------------------------------------
# Dependency-free image export (no Pillow) uses the decoder
# ---------------------------------------------------------------------------


def test_image_export_decodes_dct_to_png_without_pillow(monkeypatch):
    from aspose_pdf.engine import image_export

    img = _smooth_rgb(48, 32)
    jpeg = _jpeg(img, quality=92, subsampling=0)
    meta = {"filter": "DCTDecode", "width": 48, "height": 32}

    # Force the no-Pillow path: the pure-Python decoder must produce a PNG.
    monkeypatch.setattr(image_export, "HAS_PILLOW", False)
    out, ext = image_export.reconstruct_image_file(meta, jpeg, target_ext="png")
    assert ext == "png"
    assert out[:8] == b"\x89PNG\r\n\x1a\n"

    # The PNG round-trips back to ~the JPEG's pixels.
    png_pixels = Image.open(io.BytesIO(out)).convert("RGB").tobytes()
    jpeg_pixels = Image.open(io.BytesIO(jpeg)).convert("RGB").tobytes()
    mean, _peak = _diff(png_pixels, jpeg_pixels)
    assert mean <= 1.5


def test_image_export_decodes_progressive_to_png_without_pillow(monkeypatch):
    from aspose_pdf.engine import image_export

    jpeg = _jpeg(_smooth_rgb(), quality=90, progressive=True, subsampling=0)
    meta = {"filter": "DCTDecode", "width": 96, "height": 72}
    monkeypatch.setattr(image_export, "HAS_PILLOW", False)
    # Progressive now decodes with the pure-Python path -> PNG.
    out, ext = image_export.reconstruct_image_file(meta, jpeg, target_ext="png")
    assert ext == "png"
    assert out[:8] == b"\x89PNG\r\n\x1a\n"
    png_pixels = Image.open(io.BytesIO(out)).convert("RGB").tobytes()
    jpeg_pixels = Image.open(io.BytesIO(jpeg)).convert("RGB").tobytes()
    mean, _peak = _diff(png_pixels, jpeg_pixels)
    assert mean <= 1.5


def test_image_export_decodes_cmyk_to_png_without_pillow(monkeypatch):
    from aspose_pdf.engine import image_export

    jpeg = _jpeg(_smooth_cmyk(), quality=92)
    meta = {"filter": "DCTDecode", "width": 64, "height": 48}
    monkeypatch.setattr(image_export, "HAS_PILLOW", False)
    # 4-component JPEG -> CMYK decoded then converted to RGB for the PNG.
    out, ext = image_export.reconstruct_image_file(meta, jpeg, target_ext="png")
    assert ext == "png"
    assert out[:8] == b"\x89PNG\r\n\x1a\n"
    png_pixels = Image.open(io.BytesIO(out)).convert("RGB").tobytes()
    jpeg_pixels = Image.open(io.BytesIO(jpeg)).convert("RGB").tobytes()
    mean, _peak = _diff(png_pixels, jpeg_pixels)
    assert mean <= 3.0
