"""Bounded optional image decoding for authoring and sequential frame import."""

from __future__ import annotations

import io
import struct
import warnings
from collections.abc import Iterator
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


def _gif_info(
    data: bytes,
    budget: _LoadBudget,
    *,
    allow_animation: bool = False,
    preceding_pages: int = 0,
) -> tuple[int, int, int]:
    """Check every block and frame extent without decoding or retaining pixels."""
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
            return width, height, frames
        if marker == 0x21:
            position = subblocks(position + 1)
        elif marker == 0x2C:
            frames += 1
            if frames > 1 and not allow_animation:
                raise UnsupportedFeatureException(
                    "Animated GIF requires ImagesToPdf frame import"
                )
            if position + 9 > len(data):
                _invalid("GIF")
            left, top, frame_width, frame_height = struct.unpack_from(
                "<HHHH", data, position
            )
            if not frame_width or not frame_height:
                _invalid("GIF")
            budget.check_pages(preceding_pages + frames)
            budget.check_image_pixels(
                max(width, left + frame_width),
                max(height, top + frame_height),
                "GIF frame extent",
            )
            if frames > 1 and (
                left + frame_width > width or top + frame_height > height
            ):
                raise PdfValidationException("GIF frame extends beyond its canvas")
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


def _webp_bitstream_size(
    data: bytes, position: int, size: int, kind: bytes
) -> tuple[int, int]:
    """Read dimensions without creating a native WebP decoder."""
    if kind == b"VP8 ":
        if size < 10 or data[position + 3 : position + 6] != b"\x9d\x01\x2a":
            _invalid("WebP")
        width, height = struct.unpack_from("<HH", data, position + 6)
        return width & 0x3FFF, height & 0x3FFF
    if size < 5 or data[position] != 0x2F:
        _invalid("WebP")
    bits = int.from_bytes(data[position + 1 : position + 5], "little")
    return 1 + (bits & 0x3FFF), 1 + ((bits >> 14) & 0x3FFF)


def _webp_info(
    data: bytes,
    budget: _LoadBudget,
    *,
    allow_animation: bool = False,
    preceding_pages: int = 0,
) -> tuple[int, int, int]:
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
    animation = False
    animation_header = False
    frames = 0

    def chunk() -> None:
        nonlocal count
        count += 1
        budget.check(count, "max_container_items", "WebP chunks")

    while position < limit:
        chunk()
        if position + 8 > limit:
            _invalid("WebP")
        kind = data[position : position + 4]
        size = int.from_bytes(data[position + 4 : position + 8], "little")
        position += 8
        end = position + size
        if end + (size & 1) > limit:
            _invalid("WebP")
        if kind in (b"ANIM", b"ANMF") and not allow_animation:
            raise UnsupportedFeatureException(
                "Animated WebP requires ImagesToPdf frame import"
            )
        if kind == b"VP8X":
            if size != 10 or canvas is not None:
                _invalid("WebP")
            animation = bool(data[position] & 2)
            if animation and not allow_animation:
                raise UnsupportedFeatureException(
                    "Animated WebP requires ImagesToPdf frame import"
                )
            canvas = (
                1 + int.from_bytes(data[position + 4 : position + 7], "little"),
                1 + int.from_bytes(data[position + 7 : position + 10], "little"),
            )
            budget.check_image_pixels(*canvas, "WebP canvas")
        elif kind == b"ANIM":
            if (
                not animation
                or canvas is None
                or animation_header
                or frames
                or size != 6
            ):
                _invalid("WebP")
            animation_header = True
        elif kind == b"ANMF":
            if not animation_header or canvas is None or size < 16:
                _invalid("WebP")
            frames += 1
            budget.check_pages(preceding_pages + frames)
            left = 2 * int.from_bytes(data[position : position + 3], "little")
            top = 2 * int.from_bytes(data[position + 3 : position + 6], "little")
            width = 1 + int.from_bytes(data[position + 6 : position + 9], "little")
            height = 1 + int.from_bytes(data[position + 9 : position + 12], "little")
            if left + width > canvas[0] or top + height > canvas[1]:
                _invalid("WebP")
            inner = position + 16
            frame_size = None
            while inner < end:
                chunk()
                if inner + 8 > end:
                    _invalid("WebP")
                subkind = data[inner : inner + 4]
                subsize = int.from_bytes(data[inner + 4 : inner + 8], "little")
                inner += 8
                subend = inner + subsize + (subsize & 1)
                if subend > end:
                    _invalid("WebP")
                if subkind in (b"VP8 ", b"VP8L"):
                    if frame_size is not None:
                        _invalid("WebP")
                    frame_size = _webp_bitstream_size(data, inner, subsize, subkind)
                inner = subend
            if frame_size != (width, height):
                _invalid("WebP")
        elif kind in (b"VP8 ", b"VP8L"):
            if image_size is not None:
                _invalid("WebP")
            image_size = _webp_bitstream_size(data, position, size, kind)
        position = end + (size & 1)
    if animation:
        if not animation_header or not frames or image_size is not None:
            _invalid("WebP")
        return *canvas, frames
    if image_size is None or (canvas is not None and canvas != image_size):
        _invalid("WebP")
    return *image_size, 1


def _pillow_decoder(kind: str):
    """Require only the optional decoder needed for the recognized format."""
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
    return Image


def _frame_budget(
    data: bytes,
    width: int,
    height: int,
    budget: _LoadBudget,
    kind: str,
    *,
    animated: bool = False,
) -> int:
    """Bound decoder/compositor buffers and reserve this frame before decoding."""
    if width <= 0 or height <= 0:
        _invalid(kind)
    budget.check_image_pixels(width, height, f"{kind} import")
    decoded_size = width * height * 4
    budget.check(
        decoded_size + height, "max_decoded_stream_bytes", f"{kind} PNG samples"
    )
    budget.check(
        len(data) * 2 + width * height * (48 if animated else 32) + height * 2 + 1024,
        "max_codec_work_bytes",
        f"{kind} import working buffers",
    )
    budget.reserve_decoded(decoded_size, f"{kind} import decoded samples")
    return decoded_size


def static_image(
    data: bytes, budget: _LoadBudget, *, preceding_pages: int = 0
) -> tuple[bytes, int, int]:
    """Decode one BMP/GIF/WebP as lossless RGBA PNG under the shared budget."""
    kind = pillow_image_format(data)
    if kind is None:
        raise UnsupportedFeatureException("Expected a BMP, GIF or WebP image")
    Image = _pillow_decoder(kind)

    budget.check_input(len(data))
    budget.check_pages(preceding_pages + 1)
    if kind == "BMP":
        width, height = _bmp_size(data)
    elif kind == "GIF":
        width, height, _ = _gif_info(data, budget)
    else:
        width, height, _ = _webp_info(data, budget)
    decoded_size = _frame_budget(data, width, height, budget, kind)
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


def image_frames(
    data: bytes,
    budget: _LoadBudget,
    *,
    preceding_pages: int = 0,
    resolution: tuple[float, float] = (72.0, 72.0),
) -> Iterator[tuple[bytes, int, int, tuple[float, float]]]:
    """Yield each composited GIF/WebP canvas once, or one static BMP frame.

    Frame timing and loop counts do not duplicate pages. The caller consumes
    the PNG before requesting the next frame; Pillow retains compositor state.
    """
    kind = pillow_image_format(data)
    if kind == "BMP":
        yield *static_image(data, budget, preceding_pages=preceding_pages), resolution
        return
    if kind not in ("GIF", "WEBP"):
        raise UnsupportedFeatureException("Expected a BMP, GIF or WebP image")
    Image = _pillow_decoder(kind)
    budget.check_input(len(data))
    budget.check_pages(preceding_pages + 1)
    inspect = _gif_info if kind == "GIF" else _webp_info
    width, height, count = inspect(
        data, budget, allow_animation=True, preceding_pages=preceding_pages
    )
    budget.check_pages(preceding_pages + count)
    decoded_size = _frame_budget(data, width, height, budget, kind, animated=count > 1)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data), formats=(kind,)) as image:
                if kind == "WEBP" and image.n_frames != count:
                    _invalid(kind)
                for index in range(count):
                    if index:
                        _frame_budget(data, width, height, budget, kind, animated=True)
                        image.seek(index)
                    if image.size != (width, height):
                        _invalid(kind)
                    with image.convert("RGBA") as converted:
                        samples = converted.tobytes()
                    if len(samples) != decoded_size:
                        _invalid(kind)
                    encoded = write_png(width, height, "RGBA", samples)
                    del samples
                    yield encoded, width, height, resolution
                    del encoded
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise PdfResourceLimitException(
            f"{kind} exceeds Pillow's pixel safety limit: {exc}"
        ) from exc
    except (OSError, ValueError, SyntaxError, EOFError, UserWarning) as exc:
        raise PdfValidationException(f"Cannot decode {kind}: {exc}") from exc
