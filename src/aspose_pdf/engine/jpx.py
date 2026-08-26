"""``/JPXDecode`` support: the bundled JPEG 2000 decoder, or Pillow when present.

The pure-Python decoder in :mod:`aspose_pdf.engine.jpeg2000` makes JPEG 2000 work
in a default install, which matters because scanners emit it constantly. Pillow
(OpenJPEG) is still preferred when it is installed: it is the same picture
several hundred times faster, which is the difference between a page and a
coffee break on a full-size scan.
"""

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

__all__ = ["Decoder", "decode_to_rgb"]


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
        """Decode JPEG 2000 data to raw interleaved samples.

        Uses Pillow when it is installed and falls back to the bundled
        pure-Python decoder otherwise, so a default install still reads the
        image. Both paths honour *limits*.
        """
        resolved_limits = _coerce_limits(limits)
        if not HAS_PILLOW:
            return _decode_builtin(data, limits=resolved_limits).samples

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


def _decode_builtin(data: bytes, *, limits: PdfLoadLimits):
    from .jpeg2000 import decode as decode_jpeg2000

    return decode_jpeg2000(data, limits=limits)


def decode_to_rgb(
    data: bytes,
    meta: dict[str, Any] | None = None,
    *,
    limits: PdfLoadLimits | None = None,
) -> tuple[int, int, bytes] | None:
    """Decode to ``(width, height, rgb_bytes)`` for the renderer, or ``None``.

    Returning ``None`` rather than raising lets the rasterizer skip an image it
    cannot decode. That matters more than it sounds: the stream decoder hands
    undecodable filters back as their *raw* bytes, and a JPEG 2000 codestream
    painted as if it were samples is a page of noise.
    """
    resolved = _coerce_limits(limits)
    meta = meta or {}
    try:
        if HAS_PILLOW:
            width, height, components, samples = _pillow_samples(data, resolved)
        else:
            image = _decode_builtin(data, limits=resolved)
            width, height = image.width, image.height
            components, samples = image.components, image.samples
    except PdfResourceLimitException:
        raise
    except Exception:
        return None
    if width <= 0 or height <= 0 or not samples:
        return None
    return width, height, _to_rgb(samples, width, height, components, meta)


def _pillow_samples(
    data: bytes, limits: PdfLoadLimits
) -> tuple[int, int, int, bytes]:
    with io.BytesIO(data) as bio:
        with Image.open(bio) as img:
            _validate_layout(img, limits)
            if img.mode not in ("L", "RGB", "RGBA", "CMYK"):
                img = img.convert("RGB")
            return img.width, img.height, len(img.getbands()), img.tobytes()


def _to_rgb(
    samples: bytes,
    width: int,
    height: int,
    components: int,
    meta: dict[str, Any],
) -> bytes:
    """Turn interleaved samples into packed RGB, honouring the PDF colour space."""
    from .image_export import cmyk_to_rgb, gray_to_rgb

    expected = width * height * components
    if len(samples) < expected:
        samples = samples + bytes(expected - len(samples))
    if components == 1:
        return gray_to_rgb(samples[:expected])
    if components == 3:
        return bytes(samples[:expected])
    if components == 4:
        # A four-component JPEG 2000 is CMYK when the PDF says so, and RGB with
        # an alpha channel otherwise -- the common scanner/camera case.
        if str(meta.get("cs_kind") or "").lower() == "cmyk":
            return cmyk_to_rgb(samples[:expected])
        out = bytearray(width * height * 3)
        for pixel in range(width * height):
            source = pixel * 4
            target = pixel * 3
            out[target : target + 3] = samples[source : source + 3]
        return bytes(out)
    if components == 2:  # grey plus alpha
        out = bytearray(width * height)
        for pixel in range(width * height):
            out[pixel] = samples[pixel * 2]
        return gray_to_rgb(bytes(out))
    return gray_to_rgb(samples[: width * height])
