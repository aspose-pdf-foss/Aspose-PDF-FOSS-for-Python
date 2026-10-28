"""Optional static image decoding for the low-code image-to-PDF plugin."""

from __future__ import annotations

import io
import struct
import warnings
from typing import NoReturn

from aspose_pdf.exceptions import (
    PdfResourceLimitException,
    PdfValidationException,
    UnsupportedFeatureException,
)
from aspose_pdf.load_limits import _LoadBudget

from .image_export import write_png


def pillow_image_format(data: bytes) -> str | None:
    """Recognize the additional import formats by content, without Pillow."""
    if data.startswith(b"BM"):
        return "BMP"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "GIF"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    return None


def _invalid(kind: str) -> NoReturn:
    raise PdfValidationException(f"Cannot decode {kind}: invalid or truncated image")


def _bmp_size(data: bytes) -> tuple[int, int]:
    if len(data) < 26:
        _invalid("BMP")
    size = int.from_bytes(data[14:18], "little")
    if size not in (12, 40, 52, 56, 64, 108, 124):
        raise UnsupportedFeatureException(f"Unsupported BMP header type: {size}")
    if 14 + size > len(data):
        _invalid("BMP")
    if size == 12:
        return struct.unpack_from("<HH", data, 18)
    width, height = struct.unpack_from("<ii", data, 18)
    return width, abs(height)


def _gif_size(data: bytes, budget: _LoadBudget) -> tuple[int, int]:
    """Bound the canvas and reject a second frame without decoding pixels."""
    if len(data) < 13:
        _invalid("GIF")
    width, height = struct.unpack_from("<HH", data, 6)
    if not width or not height:
        _invalid("GIF")
    position = 13 + ((3 << ((data[10] & 7) + 1)) if data[10] & 128 else 0)
    frames = 0
    work = 0

    def step() -> None:
        nonlocal work
        work += 1
        budget.check(work, "max_container_items", "GIF blocks")

    def subblocks(position: int) -> int:
        while position < len(data):
            step()
            size = data[position]
            position += size + 1
            if position > len(data):
                _invalid("GIF")
            if size == 0:
                return position
        _invalid("GIF")

    while position < len(data):
        step()
        marker = data[position]
        position += 1
        if marker == 0x3B and frames:
            return width, height
        if marker == 0x21:
            position = subblocks(position + 1)
        elif marker == 0x2C:
            frames += 1
            if frames > 1:
                raise UnsupportedFeatureException(
                    "Animated GIF import is not supported"
                )
            if position + 9 > len(data):
                _invalid("GIF")
            left, top, frame_width, frame_height = struct.unpack_from(
                "<HHHH", data, position
            )
            if not frame_width or not frame_height:
                _invalid("GIF")
            width, height = (
                max(width, left + frame_width),
                max(height, top + frame_height),
            )
            flags = data[position + 8]
            position += 9 + ((3 << ((flags & 7) + 1)) if flags & 128 else 0)
            position = subblocks(position + 1)
        else:
            _invalid("GIF")
    _invalid("GIF")


def _webp_size(data: bytes, budget: _LoadBudget) -> tuple[int, int]:
    """Check container and bitstream dimensions before native decoder creation."""
    if len(data) < 20:
        _invalid("WebP")
    limit = int.from_bytes(data[4:8], "little") + 8
    if limit > len(data) or limit < 20:
        _invalid("WebP")
    position = 12
    canvas = None
    image_size = None
    count = 0
    while position < limit:
        count += 1
        budget.check(count, "max_container_items", "WebP chunks")
        if position + 8 > limit:
            _invalid("WebP")
        kind = data[position : position + 4]
        size = int.from_bytes(data[position + 4 : position + 8], "little")
        position += 8
        end = position + size
        if end + (size & 1) > limit:
            _invalid("WebP")
        if kind in (b"ANIM", b"ANMF"):
            raise UnsupportedFeatureException("Animated WebP import is not supported")
        if kind == b"VP8X":
            if size != 10 or canvas is not None:
                _invalid("WebP")
            if data[position] & 2:
                raise UnsupportedFeatureException(
                    "Animated WebP import is not supported"
                )
            canvas = (
                1 + int.from_bytes(data[position + 4 : position + 7], "little"),
                1 + int.from_bytes(data[position + 7 : position + 10], "little"),
            )
        elif kind in (b"VP8 ", b"VP8L"):
            if image_size is not None:
                _invalid("WebP")
            if kind == b"VP8 ":
                if size < 10 or data[position + 3 : position + 6] != b"\x9d\x01\x2a":
                    _invalid("WebP")
                width, height = struct.unpack_from("<HH", data, position + 6)
                image_size = width & 0x3FFF, height & 0x3FFF
            else:
                if size < 5 or data[position] != 0x2F:
                    _invalid("WebP")
                bits = int.from_bytes(data[position + 1 : position + 5], "little")
                image_size = 1 + (bits & 0x3FFF), 1 + ((bits >> 14) & 0x3FFF)
        position = end + (size & 1)
    if image_size is None or (canvas is not None and canvas != image_size):
        _invalid("WebP")
    return image_size


def static_image(
    data: bytes, budget: _LoadBudget, *, preceding_pages: int = 0
) -> tuple[bytes, int, int]:
    """Decode one BMP/GIF/WebP as lossless RGBA PNG under the shared budget."""
    kind = pillow_image_format(data)
    if kind is None:
        raise UnsupportedFeatureException("Expected a BMP, GIF or WebP image")
    try:
        from PIL import Image, features
    except ImportError:
        raise UnsupportedFeatureException(
            f"{kind} import requires Pillow; install the 'images' extra."
        ) from None
    if kind == "WEBP" and not features.check("webp"):
        raise UnsupportedFeatureException(
            "WebP import requires a Pillow build with WebP support."
        )

    budget.check_input(len(data))
    budget.check_pages(preceding_pages + 1)
    if kind == "BMP":
        width, height = _bmp_size(data)
    elif kind == "GIF":
        width, height = _gif_size(data, budget)
    else:
        width, height = _webp_size(data, budget)
    if width <= 0 or height <= 0:
        _invalid(kind)
    budget.check_image_pixels(width, height, f"{kind} import")
    decoded_size = width * height * 4
    budget.check(
        decoded_size + height, "max_decoded_stream_bytes", f"{kind} PNG samples"
    )
    budget.check(
        len(data) * 2 + width * height * 32 + height * 2 + 1024,
        "max_codec_work_bytes",
        f"{kind} import working buffers",
    )
    budget.reserve_decoded(decoded_size, f"{kind} import decoded samples")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data), formats=(kind,)) as image:
                if image.size != (width, height):
                    _invalid(kind)
                with image.convert("RGBA") as converted:
                    samples = converted.tobytes()
                if len(samples) != decoded_size:
                    _invalid(kind)
                encoded = write_png(width, height, "RGBA", samples)
                return encoded, width, height
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise PdfResourceLimitException(
            f"{kind} exceeds Pillow's pixel safety limit: {exc}"
        ) from exc
    except (OSError, ValueError, SyntaxError, EOFError, UserWarning) as exc:
        raise PdfValidationException(f"Cannot decode {kind}: {exc}") from exc
