"""Reconstruct extracted PDF image XObjects into real, openable image files.

This module turns the *decoded* sample bytes that the stream decoder produces
into proper image files:

* raster codecs (Flate/LZW/CCITT/JBIG2/raw) -> a pure-Python **PNG** built from
  the image's width/height/colour-space/bits-per-component, applying colour
  conversion (CMYK/Indexed/Gray -> RGB) as needed;
* **DCTDecode** payloads are already a JPEG, so they are written as ``.jpg`` by
  default; when a raster (e.g. PNG) is requested without Pillow, the
  dependency-free decoder in ``aspose_pdf.engine.dct`` decodes baseline and
  progressive JPEG (grayscale/RGB/CMYK) to pixels;
* **JPXDecode** to pixels uses **Pillow** when it is installed.

Everything except the Pillow paths is dependency-free (only ``zlib``/``struct``
from the standard library), matching the rest of the engine. Without Pillow the
core still works: DCT images save as ``.jpg`` (or decode to PNG) and JPX keeps
its prior behaviour.
"""

from __future__ import annotations

import struct
import zlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from aspose_pdf.exceptions import PdfResourceLimitException
from aspose_pdf.load_limits import PdfLoadLimits, _coerce_limits, _LoadBudget

try:  # optional, mirrors engine/jpx.py
    import io as _io

    from PIL import Image as _PILImage

    HAS_PILLOW = True
except ImportError:  # pragma: no cover - exercised only without Pillow
    HAS_PILLOW = False


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8"
_GIF_MAGIC = (b"GIF87a", b"GIF89a")
_BMP_MAGIC = b"BM"

# PNG colour types and channel counts per supported mode.
_PNG_COLOR_TYPE = {"L": 0, "RGB": 2, "P": 3, "RGBA": 6}
_PNG_CHANNELS = {"L": 1, "RGB": 3, "P": 1, "RGBA": 4}


# ---------------------------------------------------------------------------
# Encoded-image sniffing
# ---------------------------------------------------------------------------
def ext_from_magic(data: bytes) -> str | None:
    """Return a file extension if *data* already is an encoded image, else None."""
    if not data:
        return None
    if data.startswith(PNG_MAGIC):
        return "png"
    if data[:2] == _JPEG_MAGIC:
        return "jpg"
    if data[:6] in _GIF_MAGIC:
        return "gif"
    if data.startswith(_BMP_MAGIC):
        return "bmp"
    # JPEG 2000: raw codestream (FF 4F FF 51) or JP2 box ("....jP  " / ftypjp2)
    if data[:4] == b"\xff\x4f\xff\x51" or data[4:8] == b"jP  ":
        return "jp2"
    return None


def looks_like_encoded_image(data: bytes) -> bool:
    """True when *data* already carries an image-file header (avoid re-encoding)."""
    return ext_from_magic(data) is not None


_EXT_ALIASES = {"jpg": "jpg", "jpeg": "jpg", "tif": "tiff", "tiff": "tiff"}


def resolve_output_path(path: Any, produced_ext: str) -> Any:
    """Return a ``Path`` whose suffix matches *produced_ext*.

    The caller's suffix is kept when it already matches the produced format
    (``jpg``/``jpeg`` and ``tif``/``tiff`` count as equivalent); otherwise the
    suffix is swapped so the written file is not mislabelled.
    """
    from pathlib import Path

    p = Path(path)
    if not produced_ext:
        return p
    cur = p.suffix.lower().lstrip(".")
    if cur == produced_ext:
        return p
    if cur and _EXT_ALIASES.get(cur) == _EXT_ALIASES.get(produced_ext):
        return p
    return p.with_suffix("." + produced_ext)


# ---------------------------------------------------------------------------
# Pure-Python PNG encoder
# ---------------------------------------------------------------------------
def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def write_png(
    width: int,
    height: int,
    mode: str,
    data: bytes,
    *,
    bit_depth: int = 8,
    palette: bytes | None = None,
) -> bytes:
    """Encode raw, row-major samples as a PNG file.

    Parameters
    ----------
    mode: one of ``"L"`` (grayscale), ``"RGB"``, ``"RGBA"``, ``"P"`` (indexed).
    bit_depth: bits per sample/channel; ``1`` and ``8`` are supported (``1`` only
        for ``"L"``/``"P"``).
    palette: for ``mode="P"``, the RGB palette bytes (3 per entry).

    ``data`` is the concatenation of scanlines without PNG filter bytes; each
    row must already be padded to a byte boundary (as PDF image samples are).
    Short/long ``data`` is padded/truncated defensively.
    """
    if mode not in _PNG_COLOR_TYPE:
        raise ValueError(f"unsupported PNG mode: {mode!r}")
    if bit_depth not in (1, 8):
        raise ValueError(f"unsupported PNG bit depth: {bit_depth}")
    if width <= 0 or height <= 0:
        raise ValueError("PNG width and height must be positive")

    channels = _PNG_CHANNELS[mode]
    row_bytes = (width * channels * bit_depth + 7) // 8
    expected = row_bytes * height
    if len(data) < expected:
        data = data + b"\x00" * (expected - len(data))

    raw = bytearray()
    mv = memoryview(data)
    for y in range(height):
        start = y * row_bytes
        raw.append(0)  # filter byte: None
        raw.extend(mv[start : start + row_bytes])

    ihdr = struct.pack(
        ">IIBBBBB", width, height, bit_depth, _PNG_COLOR_TYPE[mode], 0, 0, 0
    )
    out = bytearray(PNG_MAGIC)
    out += _png_chunk(b"IHDR", ihdr)
    if mode == "P":
        if not palette:
            raise ValueError("indexed PNG requires a palette")
        out += _png_chunk(b"PLTE", bytes(palette))
    out += _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    out += _png_chunk(b"IEND", b"")
    return bytes(out)


# ---------------------------------------------------------------------------
# Pure-Python TIFF encoder (single page and multi-page)
# ---------------------------------------------------------------------------
# mode -> (samples per pixel, bits per sample, photometric interpretation)
_TIFF_MODES = {
    "RGB": (3, 8, 2),
    "L": (1, 8, 1),
    "1": (1, 1, 1),
}

_TIFF_COMPRESSION = {"none": 1, "deflate": 8}


@dataclass(frozen=True)
class TiffPage:
    """One image in a TIFF file.

    ``data`` holds packed row-major samples with each row padded to a byte
    boundary, matching what :func:`write_png` accepts. ``mode`` is ``"RGB"``,
    ``"L"`` (8-bit grey) or ``"1"`` (1 bit per pixel, 0 = black).
    """

    width: int
    height: int
    mode: str
    data: bytes
    dpi: float = 72.0


def _tiff_entry(tag: int, typ: int, count: int, value: int) -> bytes:
    return struct.pack("<HHII", tag, typ, count, value)


def write_tiff(
    pages: Sequence[TiffPage] | Iterable[TiffPage],
    *,
    compression: str = "deflate",
) -> bytes:
    """Encode one or more rasters as a little-endian baseline TIFF.

    Every page becomes one strip in its own IFD, chained through the next-IFD
    pointer, so a multi-page file is simply a longer chain. ``compression`` is
    ``"deflate"`` (tag 8, Adobe Deflate -- what a rendered page wants, since an
    uncompressed 300 dpi page runs to tens of megabytes) or ``"none"``.

    Pages are consumed lazily: only the compressed bytes accumulate, not every
    raster at once.
    """
    codec = _TIFF_COMPRESSION.get(compression)
    if codec is None:
        raise ValueError(
            f"unsupported TIFF compression {compression!r}; use 'deflate' or 'none'"
        )

    out = bytearray(b"II")
    out += struct.pack("<HI", 42, 0)  # magic, first-IFD offset (patched below)
    next_ifd_field = 4
    page_list = list(pages)
    if not page_list:
        raise ValueError("a TIFF file needs at least one page")
    total = len(page_list)

    for index, page in enumerate(page_list):
        spec = _TIFF_MODES.get(page.mode)
        if spec is None:
            raise ValueError(f"unsupported TIFF mode: {page.mode!r}")
        channels, bits, photometric = spec
        if page.width <= 0 or page.height <= 0:
            raise ValueError("TIFF page dimensions must be positive")
        row_bytes = (page.width * channels * bits + 7) // 8
        expected = row_bytes * page.height
        data = page.data
        if len(data) < expected:
            data = data + b"\x00" * (expected - len(data))
        elif len(data) > expected:
            data = data[:expected]
        payload = zlib.compress(data, 6) if codec == 8 else data

        if len(out) % 2:
            out.append(0)
        data_offset = len(out)
        out += payload

        def add_extra(blob: bytes) -> int:
            if len(out) % 2:
                out.append(0)
            offset = len(out)
            out.extend(blob)
            return offset

        bits_value = bits
        bits_offset = None
        if channels > 1:
            bits_offset = add_extra(
                struct.pack(f"<{channels}H", *([bits] * channels))
            )
        resolution = max(1, round(page.dpi))
        xres_offset = add_extra(struct.pack("<II", resolution, 1))
        yres_offset = add_extra(struct.pack("<II", resolution, 1))

        entries = [
            _tiff_entry(254, 4, 1, 2 if total > 1 else 0),  # NewSubfileType
            _tiff_entry(256, 4, 1, page.width),
            _tiff_entry(257, 4, 1, page.height),
            _tiff_entry(
                258, 3, channels, bits_offset if bits_offset is not None else bits_value
            ),
            _tiff_entry(259, 3, 1, codec),
            _tiff_entry(262, 3, 1, photometric),
            _tiff_entry(273, 4, 1, data_offset),  # StripOffsets
            _tiff_entry(277, 3, 1, channels),  # SamplesPerPixel
            _tiff_entry(278, 4, 1, page.height),  # RowsPerStrip
            _tiff_entry(279, 4, 1, len(payload)),  # StripByteCounts
            _tiff_entry(282, 5, 1, xres_offset),
            _tiff_entry(283, 5, 1, yres_offset),
            _tiff_entry(284, 3, 1, 1),  # PlanarConfiguration: chunky
            _tiff_entry(296, 3, 1, 2),  # ResolutionUnit: inch
            _tiff_entry(297, 3, 2, index + 1 | (total << 16)),  # PageNumber
        ]

        if len(out) % 2:
            out.append(0)
        ifd_offset = len(out)
        struct.pack_into("<I", out, next_ifd_field, ifd_offset)
        out += struct.pack("<H", len(entries))
        out += b"".join(entries)
        next_ifd_field = len(out)
        out += struct.pack("<I", 0)

    return bytes(out)


# ---------------------------------------------------------------------------
# Sample unpacking & colour conversion
# ---------------------------------------------------------------------------
def unpack_samples(
    data: bytes, bpc: int, width: int, height: int, comps: int
) -> list[int]:
    """Return ``width*height*comps`` integer samples, removing per-row padding.

    Handles sub-byte bit depths (1/2/4), 8-bit (fast path), and 16-bit (keeps the
    high byte). Values are the raw component values (not scaled).
    """
    samples_per_row = width * comps
    if bpc == 8:
        out: list[int] = []
        for y in range(height):
            row = data[y * samples_per_row : (y + 1) * samples_per_row]
            out.extend(row)
            if len(row) < samples_per_row:
                out.extend([0] * (samples_per_row - len(row)))
        return out
    if bpc == 16:
        row_bytes = samples_per_row * 2
        out = []
        for y in range(height):
            row = data[y * row_bytes : (y + 1) * row_bytes]
            for i in range(samples_per_row):
                j = i * 2
                hi = row[j] if j < len(row) else 0
                lo = row[j + 1] if j + 1 < len(row) else 0
                out.append((hi << 8) | lo)
        return out

    # Sub-byte depths: rows are padded to a byte boundary.
    mask = (1 << bpc) - 1
    row_bytes = (samples_per_row * bpc + 7) // 8
    out = []
    for y in range(height):
        row = data[y * row_bytes : (y + 1) * row_bytes]
        bit = 0
        for _ in range(samples_per_row):
            val = 0
            for b in range(bpc):
                idx = bit + b
                byi = idx // 8
                bii = 7 - (idx % 8)
                cur = row[byi] if byi < len(row) else 0
                val = (val << 1) | ((cur >> bii) & 1)
            out.append(val & mask)
            bit += bpc
    return out


def _scale_to_byte(values: list[int], bpc: int) -> bytes:
    if bpc == 8:
        return bytes(v & 0xFF for v in values)
    maxv = (1 << bpc) - 1
    return bytes(((v * 255) // maxv) & 0xFF for v in values)


def to_8bpc_bytes(
    data: bytes, bpc: int, width: int, height: int, comps: int
) -> bytes:
    """Decoded samples -> tightly packed 8-bit-per-component bytes (no padding)."""
    if bpc == 8:
        need = width * height * comps
        if len(data) >= need:
            return data[:need]
        return data + b"\x00" * (need - len(data))
    return _scale_to_byte(unpack_samples(data, bpc, width, height, comps), bpc)


def cmyk_to_rgb(data: bytes) -> bytes:
    """Convert tightly packed 8-bit CMYK samples to RGB (multiplicative)."""
    n = (len(data) // 4) * 4
    out = bytearray((n // 4) * 3)
    j = 0
    for i in range(0, n, 4):
        c, m, y, k = data[i], data[i + 1], data[i + 2], data[i + 3]
        ik = 255 - k
        out[j] = ((255 - c) * ik) // 255
        out[j + 1] = ((255 - m) * ik) // 255
        out[j + 2] = ((255 - y) * ik) // 255
        j += 3
    return bytes(out)


def gray_to_rgb(data: bytes) -> bytes:
    out = bytearray(len(data) * 3)
    for i, g in enumerate(data):
        out[3 * i] = out[3 * i + 1] = out[3 * i + 2] = g
    return bytes(out)


def rgb_to_gray(data: bytes) -> bytes:
    n = (len(data) // 3) * 3
    out = bytearray(n // 3)
    for j, i in enumerate(range(0, n, 3)):
        # Rec. 601 luma
        out[j] = (data[i] * 299 + data[i + 1] * 587 + data[i + 2] * 114) // 1000
    return bytes(out)


def indexed_to_rgb(
    data: bytes,
    palette: bytes,
    bpc: int,
    width: int,
    height: int,
    base_comps: int = 3,
) -> bytes:
    """Expand indexed samples to RGB by looking up the ``/Indexed`` palette."""
    indices = unpack_samples(data, bpc, width, height, comps=1)
    out = bytearray(len(indices) * 3)
    plen = len(palette)
    for j, idx in enumerate(indices):
        off = idx * base_comps
        o = 3 * j
        if base_comps == 1:
            g = palette[off] if off < plen else 0
            out[o] = out[o + 1] = out[o + 2] = g
        elif base_comps == 4:
            if off + 3 < plen:
                c, m, y, k = palette[off : off + 4]
            else:
                c = m = y = k = 0
            ik = 255 - k
            out[o] = ((255 - c) * ik) // 255
            out[o + 1] = ((255 - m) * ik) // 255
            out[o + 2] = ((255 - y) * ik) // 255
        else:  # RGB base (default)
            if off + 2 < plen:
                out[o] = palette[off]
                out[o + 1] = palette[off + 1]
                out[o + 2] = palette[off + 2]
    return bytes(out)


def _invert_bytes(data: bytes) -> bytes:
    return bytes(b ^ 0xFF for b in data)


# ---------------------------------------------------------------------------
# Reconstruction orchestrator
# ---------------------------------------------------------------------------
def _pillow_transcode(
    data: bytes,
    ext: str,
    limits: PdfLoadLimits | None = None,
) -> bytes:
    resolved_limits = _coerce_limits(limits)
    budget = _LoadBudget(resolved_limits)
    fmt = {
        "jpg": "JPEG",
        "jpeg": "JPEG",
        "png": "PNG",
        "bmp": "BMP",
        "tif": "TIFF",
        "tiff": "TIFF",
        "gif": "GIF",
    }.get(ext, "PNG")
    try:
        with _io.BytesIO(data) as bio:
            with _PILImage.open(bio) as source:
                width, height = source.size
                budget.check_image_pixels(width, height, "Pillow image transcode")
                components = max(1, len(source.getbands()))
                budget.check(
                    width * height * components,
                    "max_decoded_stream_bytes",
                    "Pillow decoded image samples",
                )
                budget.check(
                    len(data) + (width * height * (components + 4)),
                    "max_codec_work_bytes",
                    "Pillow image transcode working set",
                )
                converted = None
                image = source
                if fmt in ("PNG", "BMP", "GIF") and source.mode not in (
                    "L",
                    "RGB",
                    "RGBA",
                    "P",
                ):
                    converted = source.convert("RGBA" if "A" in source.mode else "RGB")
                    image = converted
                try:
                    buf = _io.BytesIO()
                    image.save(buf, format=fmt)
                    output = buf.getvalue()
                    budget.check(
                        len(output),
                        "max_codec_work_bytes",
                        "Pillow image transcode output",
                    )
                    return output
                finally:
                    if converted is not None:
                        converted.close()
    except PdfResourceLimitException:
        raise
    except _PILImage.DecompressionBombError as exc:
        raise PdfResourceLimitException(
            "Resource limit exceeded for Pillow image pixels"
        ) from exc


def _decode_is_inverted(decode: Any, comps: int) -> bool:
    """True when a 1-component ``/Decode`` array requests inverted samples."""
    if not isinstance(decode, (list, tuple)) or comps != 1 or len(decode) < 2:
        return False
    try:
        return float(decode[0]) > float(decode[1])
    except (TypeError, ValueError):
        return False


def _build_raster(
    meta: dict[str, Any], decoded: bytes
) -> tuple[str, bytes, int, bytes | None]:
    """Return ``(mode, data, bit_depth, palette)`` for a raster image."""
    width = int(meta.get("width") or 0)
    height = int(meta.get("height") or 0)
    bpc = int(meta.get("bpc") or 8)
    cs_kind = meta.get("cs_kind") or "unknown"
    decode = meta.get("decode")

    if cs_kind == "indexed" and meta.get("palette") is not None:
        rgb = indexed_to_rgb(
            decoded,
            meta["palette"],
            bpc,
            width,
            height,
            int(meta.get("palette_base_comps") or 3),
        )
        return ("RGB", rgb, 8, None)

    if cs_kind == "cmyk" or (cs_kind == "unknown" and meta.get("n_comps") == 4):
        samples = to_8bpc_bytes(decoded, bpc, width, height, 4)
        return ("RGB", cmyk_to_rgb(samples), 8, None)

    if cs_kind == "rgb" or (cs_kind == "unknown" and meta.get("n_comps") == 3):
        samples = to_8bpc_bytes(decoded, bpc, width, height, 3)
        return ("RGB", samples, 8, None)

    # grayscale / image-mask / unknown single component
    if bpc == 1:
        data = decoded
        if _decode_is_inverted(decode, 1):
            data = _invert_bytes(data)
        return ("L", data, 1, None)
    samples = to_8bpc_bytes(decoded, bpc, width, height, 1)
    if _decode_is_inverted(decode, 1):
        samples = _invert_bytes(samples)
    return ("L", samples, 8, None)


def _normalize_cs(force_cs: str | None) -> str | None:
    if not force_cs:
        return None
    fc = str(force_cs).strip().upper()
    if fc in ("L", "GRAY", "GREY", "GRAYSCALE", "DEVICEGRAY"):
        return "L"
    if fc in ("RGB", "DEVICERGB"):
        return "RGB"
    return None


def _apply_force_cs(
    mode: str,
    data: bytes,
    bit_depth: int,
    force_cs: str | None,
    width: int,
    height: int,
) -> tuple[str, bytes, int]:
    target = _normalize_cs(force_cs)
    if target is None or target == mode:
        return mode, data, bit_depth
    if bit_depth == 1 and mode == "L":  # promote to 8-bit grayscale first
        data = to_8bpc_bytes(data, 1, width, height, 1)
        bit_depth = 8
    if target == "RGB" and mode == "L":
        return "RGB", gray_to_rgb(data), 8
    if target == "L" and mode == "RGB":
        return "L", rgb_to_gray(data), 8
    return mode, data, bit_depth


def _samples_to_png(
    meta: dict[str, Any],
    decoded: bytes,
    force_cs: str | None = None,
    limits: PdfLoadLimits | None = None,
) -> bytes:
    resolved_limits = _coerce_limits(limits)
    budget = _LoadBudget(resolved_limits)
    width = int(meta.get("width") or 0)
    height = int(meta.get("height") or 0)
    budget.check_image_pixels(width, height, "exported image")
    budget.check(
        width * height * 4,
        "max_decoded_stream_bytes",
        "exported image samples",
    )
    budget.check(
        len(decoded) + (width * height * 8),
        "max_codec_work_bytes",
        "image export working set",
    )
    mode, data, bit_depth, palette = _build_raster(meta, decoded)
    mode, data, bit_depth = _apply_force_cs(
        mode, data, bit_depth, force_cs, width, height
    )
    return write_png(width, height, mode, data, bit_depth=bit_depth, palette=palette)


def _dct_to_raster(
    jpeg: bytes,
    want: str,
    force_cs: str | None,
    limits: PdfLoadLimits | None = None,
) -> tuple[bytes, str] | None:
    """Decode a JPEG to a raster file without Pillow.

    Handles baseline and progressive Huffman JPEG (grayscale, RGB/YCbCr and
    CMYK/YCCK). Returns ``(bytes, ext)`` or ``None`` when the JPEG is not a
    supported stream (e.g. arithmetic-coded) so the caller can fall back to
    keeping the original ``.jpg``.
    """
    from aspose_pdf.engine.dct import decode as decode_jpeg

    image = decode_jpeg(jpeg, limits=limits)
    if image is None:
        return None
    mode, data = image.mode, image.samples
    if mode == "CMYK":  # PNG has no CMYK; convert to RGB
        mode, data = "RGB", cmyk_to_rgb(data)
    mode, data, _bd = _apply_force_cs(
        mode, data, 8, force_cs, image.width, image.height
    )
    png = write_png(image.width, image.height, mode, data)
    # Without Pillow, PNG is the only raster we can author; honour png/default
    # and approximate other raster requests with it.
    return png, "png"


def reconstruct_image_file(
    meta: dict[str, Any] | None,
    decoded: bytes,
    target_ext: str | None = None,
    force_cs: str | None = None,
    *,
    limits: PdfLoadLimits | None = None,
) -> tuple[bytes, str]:
    """Turn decoded image bytes + metadata into a real image file.

    Returns ``(file_bytes, ext)`` where *ext* is the actual format produced (no
    leading dot). Falls back to writing *decoded* verbatim when no metadata is
    available or the payload already is an encoded image. ``force_cs`` (``"RGB"``
    or ``"Gray"``) forces a colour-space conversion of reconstructed raster
    images; it does not affect DCT/JPX passthrough.
    """
    want = (target_ext or "").lower().lstrip(".")

    # Already an encoded image (e.g. user-supplied bytes, DCT passthrough that
    # reached here, or a stream stored verbatim) -> do not re-encode.
    sniff = ext_from_magic(decoded)
    if sniff is not None:
        if want == "png" and sniff in ("jpg", "jp2"):
            if HAS_PILLOW:
                return _pillow_transcode(decoded, "png", limits), "png"
            if sniff == "jpg":
                # Dependency-free baseline JPEG -> PNG (Pillow not installed).
                raster = _dct_to_raster(decoded, "png", force_cs, limits)
                if raster is not None:
                    return raster
        return decoded, sniff

    if not meta:
        return decoded, want or "bin"

    filt = str(meta.get("filter") or "").lstrip("/")

    if filt in ("DCTDecode", "DCT"):
        # decoded is the original JPEG (pass-through filter).
        if want in ("png", "bmp", "tif", "tiff", "gif") and HAS_PILLOW:
            return _pillow_transcode(decoded, want, limits), want
        return decoded, "jpg"

    if filt == "JPXDecode":
        if HAS_PILLOW:
            # decoded may be raw pixels (jpx decoder) or, on failure, raw JPX.
            if ext_from_magic(decoded) in ("jp2", None) and decoded[:4] in (
                b"\xff\x4f\xff\x51",
            ):
                return _pillow_transcode(decoded, want or "png", limits), (
                    want or "png"
                )
            # raw pixels already decoded -> fall through to PNG packing below
        else:
            return decoded, "jp2"

    width = int(meta.get("width") or 0)
    height = int(meta.get("height") or 0)
    if width <= 0 or height <= 0:
        return decoded, want or "bin"

    return _samples_to_png(meta, decoded, force_cs, limits), "png"
