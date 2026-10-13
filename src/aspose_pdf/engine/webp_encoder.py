"""Optional Pillow-backed WebP encoding for rendered page samples."""

from __future__ import annotations

import io
import math

from aspose_pdf.exceptions import PdfValidationException, UnsupportedFeatureException
from aspose_pdf.load_limits import PdfLoadLimits, _LoadBudget


def encode(
    width: int,
    height: int,
    samples: bytes,
    *,
    mode: str = "RGB",
    quality: int | float = 85,
    lossless: bool = False,
    method: int = 4,
    limits: PdfLoadLimits | None = None,
) -> bytes:
    """Encode RGB, grayscale or packed bilevel samples as opaque WebP.

    WebP stores grayscale/bilevel content as RGB. The working-set estimate
    includes sample storage, Pillow's images, codec buffers and encoded output.
    Native codec allocations depend on the installed libwebp implementation.
    """
    if isinstance(quality, bool):
        raise PdfValidationException("WebP quality must be a number from 0 to 100.")
    try:
        quality_value = float(quality)
    except (TypeError, ValueError, OverflowError):
        raise PdfValidationException(
            "WebP quality must be a number from 0 to 100."
        ) from None
    if not math.isfinite(quality_value) or not 0 <= quality_value <= 100:
        raise PdfValidationException("WebP quality must be a number from 0 to 100.")
    if not isinstance(lossless, bool):
        raise PdfValidationException("WebP lossless must be a bool.")
    if isinstance(method, bool) or not isinstance(method, int) or not 0 <= method <= 6:
        raise PdfValidationException("WebP method must be an integer from 0 to 6.")
    if (
        isinstance(width, bool)
        or not isinstance(width, int)
        or isinstance(height, bool)
        or not isinstance(height, int)
        or not 1 <= width <= 16383
        or not 1 <= height <= 16383
    ):
        raise PdfValidationException(
            "WebP dimensions must be integers from 1 to 16383."
        )
    budget = _LoadBudget(limits)
    budget.check_raster_pixels(width, height, "WebP output")
    if mode == "RGB":
        expected = width * height * 3
    elif mode == "L":
        expected = width * height
    elif mode == "1":
        expected = ((width + 7) // 8) * height
    else:
        raise PdfValidationException(f"Unsupported WebP sample mode {mode!r}.")
    if len(samples) != expected:
        raise PdfValidationException(
            f"WebP sample buffer must contain {expected} bytes."
        )
    budget.check(
        width * height * 32 + len(samples),
        "max_codec_work_bytes",
        "WebP working buffers",
    )
    try:
        from PIL import Image, features
    except ImportError:
        raise UnsupportedFeatureException(
            "WebP export requires Pillow; install the 'images' extra."
        ) from None
    if not features.check("webp"):
        raise UnsupportedFeatureException(
            "WebP export requires a Pillow build with WebP support."
        )

    try:
        output = io.BytesIO()
        with Image.frombytes(mode, (width, height), samples) as image:
            if mode == "RGB":
                image.save(
                    output,
                    format="WEBP",
                    quality=quality_value,
                    lossless=lossless,
                    method=method,
                )
            else:
                with image.convert("RGB") as rgb:
                    rgb.save(
                        output,
                        format="WEBP",
                        quality=quality_value,
                        lossless=lossless,
                        method=method,
                    )
        budget.check(output.tell(), "max_codec_work_bytes", "WebP encoded output")
        return output.getvalue()
    except (OSError, ValueError) as exc:
        raise PdfValidationException(f"Cannot encode WebP: {exc}") from exc
