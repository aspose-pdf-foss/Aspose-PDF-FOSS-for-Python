"""Read JPEG orientation and physical resolution without decoding samples."""

from __future__ import annotations

import struct

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import _LoadBudget

from .image_import_resolution import exif_metadata, resolution_pair


def jpeg_orientation(data: bytes, budget: _LoadBudget) -> int:
    """Read IFD0's Orientation, ignoring thumbnails and non-EXIF APP1 segments.

    Missing orientation means upright. Malformed or ambiguous EXIF is refused
    rather than silently choosing how to turn the image. Only pre-scan JPEG
    headers are inspected; compressed samples and linked TIFF IFDs are unread.
    """
    return jpeg_metadata(data, budget, read_orientation=True)[0]


def jpeg_metadata(
    data: bytes,
    budget: _LoadBudget,
    *,
    read_orientation: bool = False,
    read_resolution: bool = False,
) -> tuple[int, tuple[float, float] | None]:
    """Read requested EXIF tags and JFIF density in a single bounded header scan."""
    if not data.startswith(b"\xff\xd8"):
        return 1, None
    position = 2
    segments = 0
    orientation = 1
    found_exif = False
    exif_resolution = jfif_resolution = None
    found_jfif = False
    while position < len(data):
        if data[position] != 0xFF:
            raise PdfValidationException("Invalid JPEG marker before image scan")
        while position < len(data) and data[position] == 0xFF:
            position += 1
            budget.check(
                position, "max_codec_work_bytes", "JPEG orientation header scan"
            )
        if position >= len(data):
            raise PdfValidationException("Truncated JPEG marker")
        marker = data[position]
        position += 1
        segments += 1
        budget.check(segments, "max_container_items", "JPEG orientation header count")
        budget.check(position, "max_codec_work_bytes", "JPEG orientation header scan")
        if marker == 0xD9:
            return orientation, exif_resolution or jfif_resolution
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
            orientation, exif_resolution = exif_metadata(
                memoryview(data)[start + 6 : end],
                budget,
                read_orientation=read_orientation,
                read_resolution=read_resolution,
            )
        if (
            read_resolution
            and marker == 0xE0
            and data.startswith(b"JFIF\x00", start, end)
        ):
            if found_jfif or end - start < 14:
                raise PdfValidationException(
                    "Invalid or duplicate JPEG JFIF density header"
                )
            found_jfif = True
            unit, x, y = struct.unpack_from(">BHH", data, start + 7)
            jfif_resolution = (
                resolution_pair(x, y, unit + 1) if unit in (1, 2) else None
            )
        if marker == 0xDA:
            return orientation, exif_resolution or jfif_resolution
        position = end
    raise PdfValidationException("JPEG headers end before an image scan or end marker")


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
