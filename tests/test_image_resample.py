"""Tests for the dependency-free image downscaler."""

from __future__ import annotations

import pytest

from aspose_pdf.engine.image_resample import downscale, fit_within


def test_fit_within_preserves_aspect_ratio():
    assert fit_within(2000, 1000, 800) == (800, 400)
    assert fit_within(1000, 2000, 1000) == (500, 1000)
    assert fit_within(500, 500, 800) == (500, 500)  # already fits
    assert fit_within(640, 480, 0) == (640, 480)  # non-positive cap -> unchanged


def test_downscale_box_average_quadrants():
    # 4x4 split into four solid 2x2 quadrants -> 2x2 of those colours.
    w = h = 4
    px = bytearray(w * h * 3)

    def put(x, y, rgb):
        i = (y * w + x) * 3
        px[i], px[i + 1], px[i + 2] = rgb

    for y in range(4):
        for x in range(4):
            if x < 2 and y < 2:
                put(x, y, (200, 0, 0))
            elif x >= 2 and y < 2:
                put(x, y, (0, 200, 0))
            elif x < 2 and y >= 2:
                put(x, y, (0, 0, 200))
            else:
                put(x, y, (200, 200, 200))

    out = downscale(bytes(px), 4, 4, 3, 2, 2)
    assert list(out) == [200, 0, 0, 0, 200, 0, 0, 0, 200, 200, 200, 200]


def test_downscale_averages_pixels():
    flat = bytes([0, 0, 0, 100, 100, 100])  # 2x1 RGB
    assert list(downscale(flat, 2, 1, 3, 1, 1)) == [50, 50, 50]


def test_downscale_never_upscales():
    px = bytes(range(0, 48))  # 4x4 RGB
    assert downscale(px, 4, 4, 3, 8, 8) == px  # request larger -> unchanged
    assert downscale(px, 4, 4, 3, 4, 4) == px  # same size -> unchanged


def test_downscale_grayscale():
    # 4x1 ramp -> 2x1: round-half-up means of (0,85) and (170,255).
    ramp = bytes([0, 85, 170, 255])
    assert list(downscale(ramp, 4, 1, 1, 2, 1)) == [43, 213]


def test_downscale_rejects_bad_target():
    with pytest.raises(ValueError):
        downscale(b"\x00" * 12, 2, 2, 3, 0, 1)
