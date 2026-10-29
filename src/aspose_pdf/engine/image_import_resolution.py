"""Bounded image-resolution metadata readers for physical PDF placement."""

from __future__ import annotations

import math
import struct
import zlib

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import _LoadBudget

DEFAULT_RESOLUTION = (72.0, 72.0)


def resolution_pair(x, y, unit=2) -> tuple[float, float] | None:
    """Convert an absolute inch/cm pair; incomplete or unusable pairs are absent."""
    if unit not in (2, 3):
        return None
    try:
        x, y = float(x), float(y)
        factor = 2.54 if unit == 3 else 1.0
        x, y = x * factor, y * factor
    except (TypeError, ValueError, OverflowError, ZeroDivisionError):
        return None
    return (x, y) if all(math.isfinite(v) and v > 0 for v in (x, y)) else None


def exif_metadata(
    data: memoryview | bytes,
    budget: _LoadBudget,
    *,
    read_orientation: bool = False,
    read_resolution: bool = False,
    context: str = "JPEG EXIF",
) -> tuple[int, tuple[float, float] | None]:
    """Read selected tags in classic TIFF IFD0, without following linked IFDs."""
    if len(data) < 8 or bytes(data[:2]) not in (b"II", b"MM"):
        raise PdfValidationException(f"Invalid {context} TIFF header")
    order = "<" if data[:2] == b"II" else ">"
    if struct.unpack_from(order + "H", data, 2)[0] != 42:
        raise PdfValidationException(f"Invalid {context} TIFF header")
    offset = struct.unpack_from(order + "I", data, 4)[0]
    if offset == 0:
        return 1, None
    if offset < 8 or offset + 2 > len(data):
        raise PdfValidationException(f"Invalid {context} primary directory offset")
    count = struct.unpack_from(order + "H", data, offset)[0]
    budget.check(count, "max_container_items", f"{context} directory entries")
    if offset + 2 + count * 12 + 4 > len(data):
        raise PdfValidationException(f"Truncated {context} primary directory")
    values = {}
    for index in range(count):
        entry = offset + 2 + index * 12
        tag, kind, size = struct.unpack_from(order + "HHI", data, entry)
        if not (
            (read_orientation and tag == 0x0112)
            or (read_resolution and tag in (0x011A, 0x011B, 0x0128))
        ):
            continue
        label = "Orientation" if tag == 0x0112 else "resolution"
        if tag in values:
            raise PdfValidationException(f"Duplicate {context} {label} tags")
        expected = 5 if tag in (0x011A, 0x011B) else 3
        if kind != expected or size != 1:
            kind_name = "RATIONAL" if expected == 5 else "SHORT"
            raise PdfValidationException(f"{context} {label} must be one {kind_name}")
        if expected == 3:
            value = struct.unpack_from(order + "H", data, entry + 8)[0]
            if tag == 0x0112 and not 1 <= value <= 8:
                raise PdfValidationException(
                    f"{context} Orientation must be between 1 and 8"
                )
        else:
            position = struct.unpack_from(order + "I", data, entry + 8)[0]
            if position < 8 or position + 8 > len(data):
                raise PdfValidationException(
                    f"Invalid {context} resolution value offset"
                )
            numerator, denominator = struct.unpack_from(order + "II", data, position)
            value = numerator / denominator if denominator else 0.0
        values[tag] = value
    resolution = resolution_pair(
        values.get(0x011A), values.get(0x011B), values.get(0x0128, 2)
    )
    return values.get(0x0112, 1), resolution


def image_resolution(data: bytes, budget: _LoadBudget) -> tuple[float, float]:
    """Read PNG pHYs, Windows BMP density or WebP EXIF; otherwise use 72 DPI."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return _png_resolution(data, budget) or DEFAULT_RESOLUTION
    if data.startswith(b"BM"):
        if len(data) < 18:
            raise PdfValidationException("Truncated BMP resolution header")
        size = struct.unpack_from("<I", data, 14)[0]
        if size in (40, 52, 56, 108, 124):
            if len(data) < 14 + size:
                raise PdfValidationException("Truncated BMP resolution header")
            x, y = struct.unpack_from("<ii", data, 38)
            return resolution_pair(x * 0.0254, y * 0.0254) or DEFAULT_RESOLUTION
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return _webp_resolution(data, budget) or DEFAULT_RESOLUTION
    return DEFAULT_RESOLUTION


def _png_resolution(data: bytes, budget: _LoadBudget) -> tuple[float, float] | None:
    position = 8
    chunks = 0
    result = None
    found = False
    while position + 12 <= len(data):
        chunks += 1
        budget.check(chunks, "max_container_items", "PNG resolution chunk count")
        budget.check(position + 8, "max_codec_work_bytes", "PNG resolution header scan")
        size = struct.unpack_from(">I", data, position)[0]
        kind = data[position + 4 : position + 8]
        end = position + 12 + size
        if end > len(data):
            raise PdfValidationException("Truncated PNG resolution header")
        if kind in (b"IDAT", b"IEND"):
            return result
        budget.check(end, "max_codec_work_bytes", "PNG resolution header scan")
        if kind == b"pHYs":
            if found or size != 9:
                raise PdfValidationException("Invalid or duplicate PNG pHYs chunk")
            found = True
            budget.check(size, "max_object_bytes", "PNG resolution metadata bytes")
            actual_crc = zlib.crc32(data[position + 4 : end - 4]) & 0xFFFFFFFF
            if actual_crc != struct.unpack_from(">I", data, end - 4)[0]:
                raise PdfValidationException("Invalid PNG pHYs checksum")
            x, y, unit = struct.unpack_from(">IIB", data, position + 8)
            if unit == 1:
                result = resolution_pair(x * 0.0254, y * 0.0254)
        position = end
    raise PdfValidationException("Truncated PNG resolution header")


def _webp_resolution(data: bytes, budget: _LoadBudget) -> tuple[float, float] | None:
    if len(data) < 12:
        raise PdfValidationException("Truncated WebP resolution header")
    limit = struct.unpack_from("<I", data, 4)[0] + 8
    if limit > len(data) or limit < 12:
        raise PdfValidationException("Invalid WebP resolution container size")
    position = 12
    chunks = 0
    found = False
    result = None
    while position < limit:
        chunks += 1
        budget.check(chunks, "max_container_items", "WebP resolution chunk count")
        budget.check(
            position + 8, "max_codec_work_bytes", "WebP resolution header scan"
        )
        if position + 8 > limit:
            raise PdfValidationException("Truncated WebP resolution chunk")
        kind = data[position : position + 4]
        size = struct.unpack_from("<I", data, position + 4)[0]
        start = position + 8
        end = start + size
        if end + (size & 1) > limit:
            raise PdfValidationException("Truncated WebP resolution chunk")
        if kind == b"EXIF":
            if found:
                raise PdfValidationException("Multiple WebP EXIF chunks are ambiguous")
            found = True
            budget.check(size, "max_object_bytes", "WebP EXIF metadata bytes")
            budget.check(size, "max_codec_work_bytes", "WebP EXIF metadata scan")
            if data.startswith(b"Exif\x00\x00", start, end):
                start += 6
            _, result = exif_metadata(
                memoryview(data)[start:end],
                budget,
                read_resolution=True,
                context="WebP EXIF",
            )
        position = end + (size & 1)
    return result
