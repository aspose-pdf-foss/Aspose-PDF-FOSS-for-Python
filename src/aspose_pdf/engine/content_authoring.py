"""Small helpers for authoring page content streams."""

from __future__ import annotations

import math
import re
import struct
import zlib
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from aspose_pdf.exceptions import PdfValidationException


_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8"
_SAFE_RESOURCE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class AuthoredImage:
    """Prepared image data and PDF image XObject metadata."""

    stream_data: bytes
    decoded_data: bytes
    width: int
    height: int
    bits_per_component: int
    color_space: str
    components: int
    filter_name: Optional[str] = None

    @property
    def meta(self) -> dict:
        kind = {
            "DeviceGray": "gray",
            "DeviceRGB": "rgb",
            "DeviceCMYK": "cmyk",
        }.get(self.color_space, "rgb")
        meta = {
            "width": self.width,
            "height": self.height,
            "bpc": self.bits_per_component,
            "cs_kind": kind,
            "n_comps": self.components,
        }
        if self.filter_name:
            meta["filter"] = self.filter_name
        return meta


def format_number(value: float) -> str:
    """Format a numeric operand for compact, deterministic PDF content."""

    if isinstance(value, bool):
        raise PdfValidationException("PDF numeric operands must be numbers.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise PdfValidationException("PDF numeric operands must be numbers.")
    if not math.isfinite(number):
        raise PdfValidationException("PDF numeric operands must be finite.")
    if abs(number) < 0.0000005:
        number = 0.0
    if number.is_integer():
        return str(int(number))
    return f"{number:.6f}".rstrip("0").rstrip(".")


def safe_resource_name(name: Optional[str], prefix: str) -> Optional[str]:
    """Return *name* when it is safe for content streams, otherwise ``None``."""

    if not name:
        return None
    candidate = str(name).lstrip("/")
    if _SAFE_RESOURCE_RE.match(candidate):
        return candidate
    if _SAFE_RESOURCE_RE.match(prefix + candidate):
        return prefix + candidate
    return None


def pdf_literal(text: str) -> str:
    """Encode text as a PDF literal string preserving UTF-8 bytes."""

    raw = str(text).encode("utf-8")
    out = bytearray()
    for b in raw:
        if b == 0x5C:
            out.extend(b"\\\\")
        elif b == 0x28:
            out.extend(b"\\(")
        elif b == 0x29:
            out.extend(b"\\)")
        elif b == 0x0A:
            out.extend(b"\\n")
        elif b == 0x0D:
            out.extend(b"\\r")
        elif b == 0x09:
            out.extend(b"\\t")
        elif b < 0x20:
            out.extend(f"\\{b:03o}".encode("ascii"))
        else:
            out.append(b)
    return "(" + out.decode("latin-1") + ")"


def normalize_rgb(color: Sequence[float]) -> Tuple[float, float, float]:
    """Normalize an RGB color from 0..1 or 0..255 channels to PDF 0..1 values."""

    if len(color) != 3:
        raise PdfValidationException("RGB color must contain exactly three channels.")
    values = []
    for channel in color:
        if isinstance(channel, bool):
            raise PdfValidationException("RGB color channels must be numbers.")
        try:
            values.append(float(channel))
        except (TypeError, ValueError):
            raise PdfValidationException("RGB color channels must be numbers.")
    if any(v < 0 for v in values):
        raise PdfValidationException("RGB color channels cannot be negative.")
    if any(v > 1.0 for v in values):
        values = [v / 255.0 for v in values]
    if any(v > 1.0 for v in values):
        raise PdfValidationException("RGB color channels must be in 0..1 or 0..255.")
    return (values[0], values[1], values[2])


def color_operator(color: Sequence[float], *, stroking: bool) -> str:
    r, g, b = normalize_rgb(color)
    op = "RG" if stroking else "rg"
    return (
        f"{format_number(r)} {format_number(g)} {format_number(b)} {op}"
    )


def build_text_stream(
    text: str,
    x: float,
    y: float,
    font_resource: str,
    font_size: float,
    color: Sequence[float],
) -> bytes:
    parts = [
        "q",
        color_operator(color, stroking=False),
        "BT",
        f"/{font_resource} {format_number(font_size)} Tf",
        f"1 0 0 1 {format_number(x)} {format_number(y)} Tm",
        f"{pdf_literal(text)} Tj",
        "ET",
        "Q",
    ]
    return (" ".join(parts) + "\n").encode("latin-1")


def build_image_stream(
    image_resource: str,
    x: float,
    y: float,
    width: float,
    height: float,
) -> bytes:
    parts = [
        "q",
        (
            f"{format_number(width)} 0 0 {format_number(height)} "
            f"{format_number(x)} {format_number(y)} cm"
        ),
        f"/{image_resource} Do",
        "Q",
    ]
    return (" ".join(parts) + "\n").encode("ascii")


def build_rectangle_stream(
    x: float,
    y: float,
    width: float,
    height: float,
    *,
    stroke_color: Optional[Sequence[float]],
    fill_color: Optional[Sequence[float]],
    line_width: float,
) -> bytes:
    if stroke_color is None and fill_color is None:
        raise PdfValidationException("A rectangle needs a stroke or fill color.")
    op = "B" if stroke_color is not None and fill_color is not None else "S"
    if fill_color is not None and stroke_color is None:
        op = "f"
    parts = ["q"]
    if stroke_color is not None:
        parts.append(color_operator(stroke_color, stroking=True))
        parts.append(f"{format_number(line_width)} w")
    if fill_color is not None:
        parts.append(color_operator(fill_color, stroking=False))
    parts.append(
        (
            f"{format_number(x)} {format_number(y)} "
            f"{format_number(width)} {format_number(height)} re {op}"
        )
    )
    parts.append("Q")
    return (" ".join(parts) + "\n").encode("ascii")


def build_line_stream(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    stroke_color: Sequence[float],
    line_width: float,
) -> bytes:
    parts = [
        "q",
        color_operator(stroke_color, stroking=True),
        f"{format_number(line_width)} w",
        (
            f"{format_number(x1)} {format_number(y1)} m "
            f"{format_number(x2)} {format_number(y2)} l S"
        ),
        "Q",
    ]
    return (" ".join(parts) + "\n").encode("ascii")


def wrap_marked_content(content: bytes, tag: str, mcid: int) -> bytes:
    """Wrap a content stream fragment in a tagged marked-content sequence."""

    prefix = f"/{tag} << /MCID {int(mcid)} >> BDC\n".encode("ascii")
    suffix = b"EMC\n"
    body = bytes(content)
    if body and not body.endswith((b"\n", b"\r")):
        body += b"\n"
    return prefix + body + suffix


def prepare_image(
    data: bytes,
    *,
    pixel_width: Optional[int] = None,
    pixel_height: Optional[int] = None,
    color_space: str = "DeviceRGB",
    bits_per_component: int = 8,
) -> AuthoredImage:
    """Prepare raw/JPEG/PNG bytes for a PDF image XObject."""

    payload = bytes(data)
    if not payload:
        raise PdfValidationException("Image data cannot be empty.")
    if payload.startswith(_JPEG_MAGIC):
        return _prepare_jpeg(payload)
    if payload.startswith(_PNG_MAGIC):
        return _prepare_png(payload)
    return _prepare_raw(
        payload,
        pixel_width=pixel_width,
        pixel_height=pixel_height,
        color_space=color_space,
        bits_per_component=bits_per_component,
    )


def _prepare_raw(
    data: bytes,
    *,
    pixel_width: Optional[int],
    pixel_height: Optional[int],
    color_space: str,
    bits_per_component: int,
) -> AuthoredImage:
    if pixel_width is None or pixel_height is None:
        raise PdfValidationException(
            "Raw image data requires pixel_width and pixel_height."
        )
    width = _positive_int(pixel_width, "pixel_width")
    height = _positive_int(pixel_height, "pixel_height")
    if bits_per_component != 8:
        raise PdfValidationException("Only 8-bit raw image data is supported.")
    cs = _normalize_color_space(color_space)
    components = {"DeviceGray": 1, "DeviceRGB": 3, "DeviceCMYK": 4}[cs]
    expected = width * height * components
    if len(data) != expected:
        raise PdfValidationException(
            f"Raw image data length must be {expected} bytes for this geometry."
        )
    return AuthoredImage(
        stream_data=data,
        decoded_data=data,
        width=width,
        height=height,
        bits_per_component=8,
        color_space=cs,
        components=components,
    )


def _prepare_jpeg(data: bytes) -> AuthoredImage:
    width, height, components, precision = _jpeg_geometry(data)
    cs = {1: "DeviceGray", 3: "DeviceRGB", 4: "DeviceCMYK"}.get(
        components, "DeviceRGB"
    )
    return AuthoredImage(
        stream_data=data,
        decoded_data=data,
        width=width,
        height=height,
        bits_per_component=precision,
        color_space=cs,
        components={"DeviceGray": 1, "DeviceRGB": 3, "DeviceCMYK": 4}[cs],
        filter_name="DCTDecode",
    )


def _prepare_png(data: bytes) -> AuthoredImage:
    width, height, bit_depth, color_type, pixels = _decode_png(data)
    if bit_depth != 8:
        raise PdfValidationException("Only 8-bit PNG images are supported.")
    if color_type == 0:
        decoded = pixels
        cs = "DeviceGray"
        components = 1
    elif color_type == 2:
        decoded = pixels
        cs = "DeviceRGB"
        components = 3
    elif color_type == 3:
        palette, indices = pixels
        decoded = _indexed_png_to_rgb(indices, palette)
        cs = "DeviceRGB"
        components = 3
    elif color_type == 4:
        decoded = pixels[0::2]
        cs = "DeviceGray"
        components = 1
    elif color_type == 6:
        decoded = _strip_rgba_alpha(pixels)
        cs = "DeviceRGB"
        components = 3
    else:
        raise PdfValidationException(f"Unsupported PNG color type: {color_type}.")
    compressed = zlib.compress(decoded, 9)
    return AuthoredImage(
        stream_data=compressed,
        decoded_data=decoded,
        width=width,
        height=height,
        bits_per_component=8,
        color_space=cs,
        components=components,
        filter_name="FlateDecode",
    )


def _positive_int(value: int, name: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise PdfValidationException(f"{name} must be a positive integer.")
    if result <= 0:
        raise PdfValidationException(f"{name} must be a positive integer.")
    return result


def _normalize_color_space(value: str) -> str:
    name = str(value).lstrip("/")
    aliases = {
        "G": "DeviceGray",
        "Gray": "DeviceGray",
        "DeviceGray": "DeviceGray",
        "RGB": "DeviceRGB",
        "DeviceRGB": "DeviceRGB",
        "CMYK": "DeviceCMYK",
        "DeviceCMYK": "DeviceCMYK",
    }
    if name not in aliases:
        raise PdfValidationException(
            "color_space must be DeviceGray, DeviceRGB, or DeviceCMYK."
        )
    return aliases[name]


def _jpeg_geometry(data: bytes) -> Tuple[int, int, int, int]:
    pos = 2
    sof_markers = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    while pos + 4 <= len(data):
        while pos < len(data) and data[pos] != 0xFF:
            pos += 1
        while pos < len(data) and data[pos] == 0xFF:
            pos += 1
        if pos >= len(data):
            break
        marker = data[pos]
        pos += 1
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            continue
        if pos + 2 > len(data):
            break
        segment_len = struct.unpack(">H", data[pos : pos + 2])[0]
        if segment_len < 2 or pos + segment_len > len(data):
            break
        if marker in sof_markers:
            if segment_len < 8:
                break
            precision = data[pos + 2]
            height = struct.unpack(">H", data[pos + 3 : pos + 5])[0]
            width = struct.unpack(">H", data[pos + 5 : pos + 7])[0]
            components = data[pos + 7]
            if width <= 0 or height <= 0:
                break
            return width, height, components, precision
        pos += segment_len
    raise PdfValidationException("Could not read JPEG dimensions.")


def _decode_png(data: bytes):
    pos = len(_PNG_MAGIC)
    width = height = bit_depth = color_type = None
    interlace = 0
    palette = None
    idat = bytearray()
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        tag = data[pos + 4 : pos + 8]
        payload_start = pos + 8
        payload_end = payload_start + length
        if payload_end + 4 > len(data):
            raise PdfValidationException("PNG chunk extends past end of data.")
        payload = data[payload_start:payload_end]
        pos = payload_end + 4
        if tag == b"IHDR":
            if length != 13:
                raise PdfValidationException("Invalid PNG IHDR chunk.")
            (
                width,
                height,
                bit_depth,
                color_type,
                compression,
                filter_method,
                interlace,
            ) = struct.unpack(">IIBBBBB", payload)
            if compression != 0 or filter_method != 0:
                raise PdfValidationException("Unsupported PNG compression/filter.")
        elif tag == b"PLTE":
            palette = payload
        elif tag == b"IDAT":
            idat.extend(payload)
        elif tag == b"IEND":
            break
    if width is None or height is None or bit_depth is None or color_type is None:
        raise PdfValidationException("PNG image is missing IHDR.")
    if interlace:
        raise PdfValidationException("Interlaced PNG images are not supported.")
    if width <= 0 or height <= 0:
        raise PdfValidationException("PNG width and height must be positive.")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    if channels is None:
        raise PdfValidationException(f"Unsupported PNG color type: {color_type}.")
    if bit_depth != 8:
        raise PdfValidationException("Only 8-bit PNG images are supported.")
    row_len = width * channels
    bpp = max(1, channels)
    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise PdfValidationException("PNG image data cannot be decompressed.") from exc
    pixels = _png_unfilter(raw, width, height, row_len, bpp)
    if color_type == 3:
        if palette is None:
            raise PdfValidationException("Indexed PNG image is missing a palette.")
        return width, height, bit_depth, color_type, (palette, pixels)
    return width, height, bit_depth, color_type, pixels


def _png_unfilter(
    raw: bytes, width: int, height: int, row_len: int, bpp: int
) -> bytes:
    expected = height * (row_len + 1)
    if len(raw) < expected:
        raise PdfValidationException("PNG image data is truncated.")
    rows = []
    prev = bytearray(row_len)
    offset = 0
    for _y in range(height):
        filt = raw[offset]
        offset += 1
        cur = bytearray(raw[offset : offset + row_len])
        offset += row_len
        for i in range(row_len):
            left = cur[i - bpp] if i >= bpp else 0
            up = prev[i]
            up_left = prev[i - bpp] if i >= bpp else 0
            if filt == 1:
                cur[i] = (cur[i] + left) & 0xFF
            elif filt == 2:
                cur[i] = (cur[i] + up) & 0xFF
            elif filt == 3:
                cur[i] = (cur[i] + ((left + up) // 2)) & 0xFF
            elif filt == 4:
                cur[i] = (cur[i] + _paeth(left, up, up_left)) & 0xFF
            elif filt != 0:
                raise PdfValidationException(f"Unsupported PNG filter type: {filt}.")
        rows.append(bytes(cur))
        prev = cur
    return b"".join(rows)


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _indexed_png_to_rgb(indices: bytes, palette: bytes) -> bytes:
    out = bytearray(len(indices) * 3)
    for i, idx in enumerate(indices):
        p = idx * 3
        if p + 2 >= len(palette):
            continue
        out[i * 3] = palette[p]
        out[i * 3 + 1] = palette[p + 1]
        out[i * 3 + 2] = palette[p + 2]
    return bytes(out)


def _strip_rgba_alpha(data: bytes) -> bytes:
    out = bytearray((len(data) // 4) * 3)
    j = 0
    for i in range(0, len(data) - 3, 4):
        out[j] = data[i]
        out[j + 1] = data[i + 1]
        out[j + 2] = data[i + 2]
        j += 3
    return bytes(out)
