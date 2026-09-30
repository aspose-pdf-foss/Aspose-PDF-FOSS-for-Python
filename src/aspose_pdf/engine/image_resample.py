"""Dependency-free image downscaling (box / area averaging) and depth rescaling.

Used to shrink the pixel dimensions of an embedded image before it is
re-encoded, the resampling half of the ``image_compression_quality`` /
``image_max_dimension`` optimization.  Only downscaling is offered — enlarging a
raster never reduces file size and would only invent detail.

Pixels are 8-bit, interleaved (``components`` per pixel), row-major.

``sample_to_byte`` is the other half: bringing a sample of some other depth onto
0..255.  It lives here so that every path that has to do it does it the same
way, which they did not — a 16-bit sample was shifted down on the way in from a
PNG, divided down on the way out to one, and rounded down in a JPEG 2000
codestream, three answers to one question differing by a step.
"""

from __future__ import annotations

__all__ = ["downscale", "fit_within", "sample_to_byte", "samples_to_bytes"]

_BYTE_MAX = 255


def sample_to_byte(value: int, maximum: int) -> int:
    """Return *value*, whose full-scale is *maximum*, rescaled onto 0..255.

    The linear rescale, rounded: ISO 15948 (PNG) 10.4 calls this the most
    accurate way to reduce a sample's depth, and the alternatives -- shifting
    the low bits away, or truncating the division -- "slightly less accurate"
    but faster.  Speed is not what is being bought here: these samples are
    stored in a document or written to a file the caller keeps, not pushed at a
    screen once, and at Python's arithmetic the two cost the same.  So the
    accurate rule is used, and used everywhere.

    Exact whenever 255 is a whole multiple of *maximum* (1, 3, 15 and 255, so
    every sub-byte depth and 8 bits itself).  Half-way values cannot arise: they
    would need ``510 * value == maximum * odd``, and ``2**n - 1`` is always odd,
    so which way they would round never comes up.

    pdfium truncates and MuPDF shifts, so a 16-bit image can differ from either
    by 1/255 -- less than this rounds away from the true value.

    There is no 8-bit short cut: every caller returns the byte untouched before
    it would need one, and the rescale is the identity at that depth anyway.
    """
    return ((value * _BYTE_MAX + maximum // 2) // maximum) & 0xFF


def samples_to_bytes(values, maximum: int) -> bytes:
    """``sample_to_byte`` over an iterable of samples."""
    half = maximum // 2
    return bytes(
        ((value * _BYTE_MAX + half) // maximum) & 0xFF for value in values
    )


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
