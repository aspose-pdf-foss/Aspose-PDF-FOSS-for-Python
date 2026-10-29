"""Decode TIFF frames lazily for the low-code image-to-PDF plugin."""

from __future__ import annotations

import io
import warnings
from collections.abc import Iterator

from aspose_pdf.exceptions import (
    PdfResourceLimitException,
    PdfValidationException,
    UnsupportedFeatureException,
)
from aspose_pdf.load_limits import _LoadBudget

from .image_export import write_png
from .image_import_resolution import DEFAULT_RESOLUTION, resolution_pair

_TIFF_MAGIC = (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")


def is_tiff(data: bytes) -> bool:
    """Recognize classic TIFF and BigTIFF regardless of a source's filename."""
    return data[:4] in _TIFF_MAGIC


def tiff_frames(
    data: bytes,
    budget: _LoadBudget,
    *,
    preceding_pages: int = 0,
    read_resolution: bool = False,
) -> Iterator[tuple[bytes, int, int, tuple[float, float]]]:
    """Yield lossless PNG frames, checking allocations before decoding pixels.

    Pillow is needed only for TIFF. Bilevel and palette images are expanded to
    eight-bit grayscale and RGB respectively; other supported modes are L/RGB.
    The caller places each yielded frame before requesting the next one.
    The final tuple item is the oriented X/Y DPI pair, or 72 DPI when metadata
    is not requested or cannot specify an absolute physical size.
    """
    try:
        from PIL import Image
    except ImportError:
        raise UnsupportedFeatureException(
            "TIFF import requires Pillow; install the 'images' extra."
        ) from None

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            warnings.filterwarnings("error", message="Corrupt EXIF data.*")
            with Image.open(io.BytesIO(data), formats=("TIFF",)) as image:
                seen_offsets: set[int] = set()
                index = 0
                while True:
                    budget.check_pages(preceding_pages + index + 1)
                    offset = image.tag_v2.offset
                    seen_offsets.add(offset)
                    next_offset = image.tag_v2.next
                    if next_offset and (
                        next_offset in seen_offsets or next_offset >= len(data)
                    ):
                        raise PdfValidationException(
                            "TIFF frame directories contain an invalid or cyclic offset."
                        )

                    mode = image.mode
                    bits = image.tag_v2.get(258, (1,))
                    if isinstance(bits, int):
                        bits = (bits,)
                    allowed_bits = (1, 2, 4, 8) if mode == "P" else (1, 8)
                    if mode not in ("1", "L", "RGB", "P") or any(
                        value not in allowed_bits for value in bits
                    ):
                        raise UnsupportedFeatureException(
                            f"TIFF frame {index + 1} has unsupported mode {mode!r} "
                            f"or bit depth {bits!r}; "
                            "supported modes are bilevel, 8-bit grayscale, RGB and palette."
                        )
                    width, height = image.size
                    resolution = DEFAULT_RESOLUTION
                    if read_resolution:
                        resolution = (
                            resolution_pair(
                                image.tag_v2.get(282),
                                image.tag_v2.get(283),
                                image.tag_v2.get(296, 2),
                            )
                            or DEFAULT_RESOLUTION
                        )
                        if image.tag_v2.get(274, 1) in (5, 6, 7, 8):
                            resolution = resolution[::-1]
                    if width <= 0 or height <= 0:
                        raise PdfValidationException(
                            "TIFF dimensions must be positive."
                        )
                    context = f"TIFF frame {index + 1}"
                    budget.check_image_pixels(width, height, context)
                    target = "L" if mode in ("1", "L") else "RGB"
                    components = 1 if target == "L" else 3
                    decoded_size = width * height * components
                    budget.check(
                        decoded_size + height,
                        "max_decoded_stream_bytes",
                        f"{context} PNG filtered samples",
                    )
                    # Account for Pillow's pixels, conversion, PNG encoding and
                    # the authoring decoder's buffers without retaining frames.
                    budget.check(
                        len(data)
                        + width * height * (4 + components * 5)
                        + height * 2
                        + 1024,
                        "max_codec_work_bytes",
                        f"{context} working buffers",
                    )
                    budget.reserve_decoded(decoded_size, f"{context} decoded samples")
                    image.load()
                    width, height = image.size  # Pillow applies TIFF orientation.
                    if image.mode == target:
                        samples = image.tobytes()
                    else:
                        with image.convert(target) as converted:
                            samples = converted.tobytes()
                    if len(samples) != decoded_size:
                        raise PdfValidationException(
                            "TIFF decoded sample size is invalid."
                        )
                    encoded = write_png(width, height, target, samples)
                    del samples
                    yield encoded, width, height, resolution
                    del encoded
                    if not next_offset:
                        return
                    index += 1
                    budget.check_pages(preceding_pages + index + 1)
                    try:
                        image.seek(index)
                    except EOFError:
                        raise PdfValidationException(
                            "TIFF ended before its declared next frame."
                        ) from None
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise PdfResourceLimitException(
            f"TIFF exceeds Pillow's pixel safety limit: {exc}"
        ) from exc
    except (OSError, ValueError, SyntaxError, EOFError, UserWarning) as exc:
        raise PdfValidationException(f"Cannot decode TIFF: {exc}") from exc
