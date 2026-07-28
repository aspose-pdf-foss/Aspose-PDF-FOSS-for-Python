# JPEG 2000 (JPX) Decoder using Pillow
from __future__ import annotations

import io
from typing import Any

from aspose_pdf.exceptions import (
    PDF_STREAM_DECODE_ERRORS,
    PdfResourceLimitException,
    PdfValidationException,
)
from aspose_pdf.load_limits import PdfLoadLimits, _coerce_limits

try:
    from PIL import Image

    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False


def _raise_limit(context: str, value: int, name: str, limit: int) -> None:
    raise PdfResourceLimitException(
        f"Resource limit exceeded for {context}: {value} exceeds {name}={limit}"
    )


def _target_mode_and_components(img) -> tuple[str | None, int]:
    if img.mode in ("RGB", "RGBA", "L", "CMYK"):
        return None, len(img.getbands())
    if "A" in img.mode:
        return "RGBA", 4
    return "RGB", 3


def _validate_layout(img, limits: PdfLoadLimits) -> tuple[str | None, int]:
    """Validate codec-header dimensions before Pillow decodes pixel planes."""
    width, height = img.size
    source_components = len(img.getbands())
    if width <= 0 or height <= 0 or source_components <= 0:
        raise PdfValidationException(
            f"JPXDecode has invalid image layout: {width}x{height}, "
            f"{source_components} components"
        )

    pixels = width * height
    pixel_limit = limits.max_image_pixels
    if pixel_limit is not None and pixels > pixel_limit:
        _raise_limit("JPX image pixels", pixels, "max_image_pixels", pixel_limit)

    target_mode, target_components = _target_mode_and_components(img)
    output_bytes = pixels * target_components
    byte_limit = limits.max_decoded_stream_bytes
    if byte_limit is not None and output_bytes > byte_limit:
        _raise_limit(
            "JPX decoded samples",
            output_bytes,
            "max_decoded_stream_bytes",
            byte_limit,
        )

    # Pillow may keep up to four bytes per source component while converting.
    source_work_bytes = pixels * max(4, source_components * 4)
    conversion_bytes = output_bytes if target_mode is not None else 0
    work_bytes = source_work_bytes + conversion_bytes + output_bytes
    work_limit = limits.max_codec_work_bytes
    if work_limit is not None and work_bytes > work_limit:
        _raise_limit(
            "JPX decoder working set",
            work_bytes,
            "max_codec_work_bytes",
            work_limit,
        )
    return target_mode, target_components


class Decoder:
    """JPEG 2000 (JPX) Stream Decoder."""

    @staticmethod
    def decode(
        data: bytes,
        parms: dict[str, Any] | None = None,
        *,
        limits: PdfLoadLimits | None = None,
    ) -> bytes:
        """Decode JPEG 2000 data using Pillow.

        Args:
            data: JPX encoded bytes
            parms: Optional DecodeParms (usually ignored for JPX in PDF)

        Returns:
            Decoded raw pixel data
        """
        resolved_limits = _coerce_limits(limits)
        if not HAS_PILLOW:
            raise PdfValidationException(
                "JPXDecode requires Pillow (JPEG 2000 decode is not available)"
            )

        try:
            with io.BytesIO(data) as bio:
                with Image.open(bio) as img:
                    target_mode, _components = _validate_layout(img, resolved_limits)
                    if target_mode is None:
                        return img.tobytes()
                    converted = img.convert(target_mode)
                    try:
                        return converted.tobytes()
                    finally:
                        converted.close()
        except PdfResourceLimitException:
            raise
        except Image.DecompressionBombError as exc:
            raise PdfResourceLimitException(
                "Resource limit exceeded for JPX image pixels"
            ) from exc
        except PDF_STREAM_DECODE_ERRORS as exc:
            raise PdfValidationException(
                "JPXDecode failed while decoding the image stream"
            ) from exc
