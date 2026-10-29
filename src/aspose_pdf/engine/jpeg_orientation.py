"""Read primary-image JPEG EXIF orientation without decoding image samples."""

from __future__ import annotations

import struct

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import _LoadBudget


def jpeg_orientation(data: bytes, budget: _LoadBudget) -> int:
    """Read IFD0's Orientation, ignoring thumbnails and non-EXIF APP1 segments.

    Missing orientation means upright. Malformed or ambiguous EXIF is refused
    rather than silently choosing how to turn the image. Only pre-scan JPEG
    headers are inspected; compressed samples and linked TIFF IFDs are unread.
    """
    if not data.startswith(b"\xff\xd8"):
        return 1
    position = 2
    segments = 0
    orientation = 1
    found_exif = False
    while position < len(data):
        if data[position] != 0xFF:
            raise PdfValidationException("Invalid JPEG marker before image scan")
        while position < len(data) and data[position] == 0xFF:
            position += 1
            budget.check(position, "max_codec_work_bytes", "JPEG orientation header scan")
        if position >= len(data):
            raise PdfValidationException("Truncated JPEG marker")
        marker = data[position]
        position += 1
        segments += 1
        budget.check(segments, "max_container_items", "JPEG orientation header count")
        budget.check(position, "max_codec_work_bytes", "JPEG orientation header scan")
        if marker == 0xD9:
            return orientation
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            continue
        if marker in (0, 0xD8) or position + 2 > len(data):
            raise PdfValidationException("Invalid or truncated JPEG segment")
        length = struct.unpack_from(">H", data, position)[0]
        end = position + length
        if length < 2 or end > len(data):
            raise PdfValidationException("Invalid or truncated JPEG segment length")
        budget.check(end, "max_codec_work_bytes", "JPEG orientation header scan")
        start = position + 2
        if marker == 0xE1 and data.startswith(b"Exif\x00\x00", start, end):
            if found_exif:
                raise PdfValidationException(
                    "Multiple JPEG EXIF segments are ambiguous"
                )
            found_exif = True
            budget.check(end - start, "max_object_bytes", "JPEG EXIF metadata bytes")
            orientation = _tiff_orientation(memoryview(data)[start + 6 : end], budget)
        if marker == 0xDA:
            return orientation
        position = end
    raise PdfValidationException("JPEG headers end before an image scan or end marker")


def _tiff_orientation(data: memoryview, budget: _LoadBudget) -> int:
    """Read the inline SHORT in classic TIFF's primary image directory only."""
    if len(data) < 8 or bytes(data[:2]) not in (b"II", b"MM"):
        raise PdfValidationException("Invalid JPEG EXIF TIFF header")
    byte_order = "<" if data[:2] == b"II" else ">"
    if struct.unpack_from(byte_order + "H", data, 2)[0] != 42:
        raise PdfValidationException("Invalid JPEG EXIF TIFF header")
    offset = struct.unpack_from(byte_order + "I", data, 4)[0]
    if offset == 0:
        return 1
    if offset < 8 or offset + 2 > len(data):
        raise PdfValidationException("Invalid JPEG EXIF primary directory offset")
    count = struct.unpack_from(byte_order + "H", data, offset)[0]
    budget.check(count, "max_container_items", "JPEG EXIF directory entries")
    if offset + 2 + count * 12 + 4 > len(data):
        raise PdfValidationException("Truncated JPEG EXIF primary directory")
    orientation = None
    for index in range(count):
        entry = offset + 2 + index * 12
        tag, kind, size = struct.unpack_from(byte_order + "HHI", data, entry)
        if tag != 0x0112:
            continue
        if orientation is not None:
            raise PdfValidationException("Duplicate JPEG EXIF Orientation tags")
        if kind != 3 or size != 1:
            raise PdfValidationException("JPEG EXIF Orientation must be one SHORT")
        orientation = struct.unpack_from(byte_order + "H", data, entry + 8)[0]
        if not 1 <= orientation <= 8:
            raise PdfValidationException(
                "JPEG EXIF Orientation must be between 1 and 8"
            )
    return 1 if orientation is None else orientation


def orientation_matrix(
    orientation: int, x: float, y: float, width: float, height: float
) -> tuple[float, ...]:
    """Map a JPEG's unit square into oriented bounds in PDF coordinates."""
    a, b, c, d, e, f = {
        2: (-1, 0, 0, 1, 1, 0),
        3: (-1, 0, 0, -1, 1, 1),
        4: (1, 0, 0, -1, 0, 1),
        5: (0, -1, -1, 0, 1, 1),
        6: (0, -1, 1, 0, 0, 1),
        7: (0, 1, 1, 0, 0, 0),
        8: (0, 1, -1, 0, 1, 0),
    }[orientation]
    return a * width, b * height, c * width, d * height, x + e * width, y + f * height
