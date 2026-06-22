"""Dependency-free image downscaling (box / area averaging).

Used to shrink the pixel dimensions of an embedded image before it is
re-encoded, the resampling half of the ``image_compression_quality`` /
``image_max_dimension`` optimization.  Only downscaling is offered — enlarging a
raster never reduces file size and would only invent detail.

Pixels are 8-bit, interleaved (``components`` per pixel), row-major.
"""

from __future__ import annotations

__all__ = ["fit_within", "downscale"]


def fit_within(width: int, height: int, max_dim: int) -> tuple[int, int]:
    """Return the largest ``(w, h)`` <= ``(width, height)`` whose longest side
    is at most *max_dim*, preserving aspect ratio.  Returns the input unchanged
    when it already fits (or *max_dim* is non-positive)."""
    longest = max(width, height)
    if max_dim <= 0 or longest <= max_dim:
        return width, height
    new_w = max(1, (width * max_dim) // longest)
    new_h = max(1, (height * max_dim) // longest)
    return new_w, new_h


def downscale(
    samples: bytes,
    width: int,
    height: int,
    components: int,
    new_width: int,
    new_height: int,
) -> bytes:
    """Box-average *samples* down to ``new_width`` x ``new_height``.

    Each destination pixel is the mean of the source pixels in its footprint, so
    the result is a clean area-resampled downscale.  Returns *samples* unchanged
    when the target is not strictly smaller in at least one axis (no upscaling).
    """
    if new_width <= 0 or new_height <= 0:
        raise ValueError("invalid target dimensions")
    if new_width >= width and new_height >= height:
        return samples
    new_width = min(new_width, width)
    new_height = min(new_height, height)

    # Precompute the source column span for each destination column so the inner
    # loop does not recompute it for every row.
    col_spans = []
    for ox in range(new_width):
        sx0 = (ox * width) // new_width
        sx1 = max(sx0 + 1, ((ox + 1) * width) // new_width)
        col_spans.append((sx0, sx1))

    out = bytearray(new_width * new_height * components)
    for oy in range(new_height):
        sy0 = (oy * height) // new_height
        sy1 = max(sy0 + 1, ((oy + 1) * height) // new_height)
        row_base = oy * new_width * components
        for ox in range(new_width):
            sx0, sx1 = col_spans[ox]
            count = (sy1 - sy0) * (sx1 - sx0)
            half = count >> 1
            dst = row_base + ox * components
            for c in range(components):
                total = 0
                for sy in range(sy0, sy1):
                    pix = (sy * width + sx0) * components + c
                    for _sx in range(sx0, sx1):
                        total += samples[pix]
                        pix += components
                out[dst + c] = (total + half) // count
    return bytes(out)
