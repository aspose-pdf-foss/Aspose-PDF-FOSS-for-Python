"""Dependency-free baseline (sequential) JPEG encoder.

The counterpart to :mod:`aspose_pdf.engine.dct` (the decoder): it turns 8-bit
interleaved pixels into a baseline ``DCTDecode`` stream so embedded images can be
recompressed at a chosen quality without any third-party dependency.

Supported inputs: 1 component (grayscale) and 3 components (RGB, encoded as
YCbCr with 4:2:0 chroma subsampling).  Output is a standard JFIF baseline JPEG
that round-trips through :func:`aspose_pdf.engine.dct.decode` and mainstream
decoders (Pillow, libjpeg).

Only the standard Annex K quantization and Huffman tables are used; quality
scales the quantization tables exactly as libjpeg does.
"""

from __future__ import annotations

import math

__all__ = ["encode"]

# --- Annex K.1 quantization tables (natural, row-major order) --------------
_STD_LUMA_QT = [
    16, 11, 10, 16, 24, 40, 51, 61,
    12, 12, 14, 19, 26, 58, 60, 55,
    14, 13, 16, 24, 40, 57, 69, 56,
    14, 17, 22, 29, 51, 87, 80, 62,
    18, 22, 37, 56, 68, 109, 103, 77,
    24, 35, 55, 64, 81, 104, 113, 92,
    49, 64, 78, 87, 103, 121, 120, 101,
    72, 92, 95, 98, 112, 100, 103, 99,
]
_STD_CHROMA_QT = [
    17, 18, 24, 47, 99, 99, 99, 99,
    18, 21, 26, 66, 99, 99, 99, 99,
    24, 26, 56, 99, 99, 99, 99, 99,
    47, 66, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
]

# Natural position for each entry of the zig-zag sequence.
_ZIGZAG = [
    0, 1, 8, 16, 9, 2, 3, 10,
    17, 24, 32, 25, 18, 11, 4, 5,
    12, 19, 26, 33, 40, 48, 41, 34,
    27, 20, 13, 6, 7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36,
    29, 22, 15, 23, 30, 37, 44, 51,
    58, 59, 52, 45, 38, 31, 39, 46,
    53, 60, 61, 54, 47, 55, 62, 63,
]

# --- Annex K.3 standard Huffman tables (BITS counts + symbol values) -------
_BITS_DC_LUMA = [0, 1, 5, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0]
_VALS_DC_LUMA = list(range(12))
_BITS_DC_CHROMA = [0, 3, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0]
_VALS_DC_CHROMA = list(range(12))

_BITS_AC_LUMA = [0, 2, 1, 3, 3, 2, 4, 3, 5, 5, 4, 4, 0, 0, 1, 0x7D]
_VALS_AC_LUMA = [
    0x01, 0x02, 0x03, 0x00, 0x04, 0x11, 0x05, 0x12, 0x21, 0x31, 0x41, 0x06,
    0x13, 0x51, 0x61, 0x07, 0x22, 0x71, 0x14, 0x32, 0x81, 0x91, 0xA1, 0x08,
    0x23, 0x42, 0xB1, 0xC1, 0x15, 0x52, 0xD1, 0xF0, 0x24, 0x33, 0x62, 0x72,
    0x82, 0x09, 0x0A, 0x16, 0x17, 0x18, 0x19, 0x1A, 0x25, 0x26, 0x27, 0x28,
    0x29, 0x2A, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3A, 0x43, 0x44, 0x45,
    0x46, 0x47, 0x48, 0x49, 0x4A, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59,
    0x5A, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69, 0x6A, 0x73, 0x74, 0x75,
    0x76, 0x77, 0x78, 0x79, 0x7A, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89,
    0x8A, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9A, 0xA2, 0xA3,
    0xA4, 0xA5, 0xA6, 0xA7, 0xA8, 0xA9, 0xAA, 0xB2, 0xB3, 0xB4, 0xB5, 0xB6,
    0xB7, 0xB8, 0xB9, 0xBA, 0xC2, 0xC3, 0xC4, 0xC5, 0xC6, 0xC7, 0xC8, 0xC9,
    0xCA, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8, 0xD9, 0xDA, 0xE1, 0xE2,
    0xE3, 0xE4, 0xE5, 0xE6, 0xE7, 0xE8, 0xE9, 0xEA, 0xF1, 0xF2, 0xF3, 0xF4,
    0xF5, 0xF6, 0xF7, 0xF8, 0xF9, 0xFA,
]
_BITS_AC_CHROMA = [0, 2, 1, 2, 4, 4, 3, 4, 7, 5, 4, 4, 0, 1, 2, 0x77]
_VALS_AC_CHROMA = [
    0x00, 0x01, 0x02, 0x03, 0x11, 0x04, 0x05, 0x21, 0x31, 0x06, 0x12, 0x41,
    0x51, 0x07, 0x61, 0x71, 0x13, 0x22, 0x32, 0x81, 0x08, 0x14, 0x42, 0x91,
    0xA1, 0xB1, 0xC1, 0x09, 0x23, 0x33, 0x52, 0xF0, 0x15, 0x62, 0x72, 0xD1,
    0x0A, 0x16, 0x24, 0x34, 0xE1, 0x25, 0xF1, 0x17, 0x18, 0x19, 0x1A, 0x26,
    0x27, 0x28, 0x29, 0x2A, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3A, 0x43, 0x44,
    0x45, 0x46, 0x47, 0x48, 0x49, 0x4A, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58,
    0x59, 0x5A, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69, 0x6A, 0x73, 0x74,
    0x75, 0x76, 0x77, 0x78, 0x79, 0x7A, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87,
    0x88, 0x89, 0x8A, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9A,
    0xA2, 0xA3, 0xA4, 0xA5, 0xA6, 0xA7, 0xA8, 0xA9, 0xAA, 0xB2, 0xB3, 0xB4,
    0xB5, 0xB6, 0xB7, 0xB8, 0xB9, 0xBA, 0xC2, 0xC3, 0xC4, 0xC5, 0xC6, 0xC7,
    0xC8, 0xC9, 0xCA, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8, 0xD9, 0xDA,
    0xE2, 0xE3, 0xE4, 0xE5, 0xE6, 0xE7, 0xE8, 0xE9, 0xEA, 0xF2, 0xF3, 0xF4,
    0xF5, 0xF6, 0xF7, 0xF8, 0xF9, 0xFA,
]

# Forward-DCT basis: _COS[u][x] = 0.5 * C(u) * cos((2x+1)uπ/16).
_COS = [[0.0] * 8 for _ in range(8)]
for _u in range(8):
    _cu = (1.0 / math.sqrt(2.0)) if _u == 0 else 1.0
    for _x in range(8):
        _COS[_u][_x] = 0.5 * _cu * math.cos((2 * _x + 1) * _u * math.pi / 16.0)


def _build_huffman(bits, vals) -> dict[int, tuple[int, int]]:
    """Return ``symbol -> (code, bit_length)`` from a BITS/HUFFVAL spec."""
    table: dict[int, tuple[int, int]] = {}
    code = 0
    k = 0
    for length in range(1, 17):
        for _ in range(bits[length - 1]):
            table[vals[k]] = (code, length)
            code += 1
            k += 1
        code <<= 1
    return table


_HUFF_DC_LUMA = _build_huffman(_BITS_DC_LUMA, _VALS_DC_LUMA)
_HUFF_AC_LUMA = _build_huffman(_BITS_AC_LUMA, _VALS_AC_LUMA)
_HUFF_DC_CHROMA = _build_huffman(_BITS_DC_CHROMA, _VALS_DC_CHROMA)
_HUFF_AC_CHROMA = _build_huffman(_BITS_AC_CHROMA, _VALS_AC_CHROMA)


def _scaled_qt(base: list[int], quality: int) -> list[int]:
    """Scale a base quantization table for *quality* (libjpeg convention)."""
    q = max(1, min(100, quality))
    scale = 5000 // q if q < 50 else 200 - 2 * q
    out = []
    for value in base:
        scaled = (value * scale + 50) // 100
        out.append(max(1, min(255, scaled)))
    return out


class _BitWriter:
    """MSB-first bit accumulator with JPEG ``0xFF`` byte stuffing."""

    def __init__(self) -> None:
        self.out = bytearray()
        self._acc = 0
        self._nbits = 0

    def write(self, value: int, length: int) -> None:
        self._acc = (self._acc << length) | (value & ((1 << length) - 1))
        self._nbits += length
        while self._nbits >= 8:
            self._nbits -= 8
            byte = (self._acc >> self._nbits) & 0xFF
            self.out.append(byte)
            if byte == 0xFF:
                self.out.append(0x00)

    def flush(self) -> None:
        if self._nbits > 0:
            # Pad the final partial byte with 1-bits, per the standard.
            pad = 8 - self._nbits
            self.write((1 << pad) - 1, pad)


def _category(value: int) -> int:
    return value.bit_length() if value >= 0 else (-value).bit_length()


def _amplitude_bits(value: int, size: int) -> int:
    return value + ((1 << size) - 1) if value < 0 else value


def _fdct_quantize(block: list[int], qt: list[int]) -> list[int]:
    """Level-shifted forward DCT of an 8x8 *block* followed by quantization.

    Returns the 64 quantized coefficients in natural (row-major) order.
    """
    # Rows.
    tmp = [0.0] * 64
    for y in range(8):
        base = y * 8
        s0, s1, s2, s3 = block[base], block[base + 1], block[base + 2], block[base + 3]
        s4, s5, s6, s7 = block[base + 4], block[base + 5], block[base + 6], block[base + 7]
        for u in range(8):
            c = _COS[u]
            tmp[base + u] = (
                s0 * c[0] + s1 * c[1] + s2 * c[2] + s3 * c[3]
                + s4 * c[4] + s5 * c[5] + s6 * c[6] + s7 * c[7]
            )
    # Columns + quantization.
    out = [0] * 64
    for u in range(8):
        col0, col1, col2, col3 = tmp[u], tmp[8 + u], tmp[16 + u], tmp[24 + u]
        col4, col5, col6, col7 = tmp[32 + u], tmp[40 + u], tmp[48 + u], tmp[56 + u]
        for v in range(8):
            c = _COS[v]
            coeff = (
                col0 * c[0] + col1 * c[1] + col2 * c[2] + col3 * c[3]
                + col4 * c[4] + col5 * c[5] + col6 * c[6] + col7 * c[7]
            )
            pos = v * 8 + u
            q = qt[pos]
            out[pos] = int(math.floor(coeff / q + 0.5))
    return out


def _encode_block(coeffs, prev_dc, dc_table, ac_table, writer) -> int:
    """Huffman-encode one quantized block; return its DC value for prediction."""
    dc = coeffs[0]
    diff = dc - prev_dc
    size = _category(diff)
    code, length = dc_table[size]
    writer.write(code, length)
    if size:
        writer.write(_amplitude_bits(diff, size), size)

    run = 0
    for k in range(1, 64):
        value = coeffs[_ZIGZAG[k]]
        if value == 0:
            run += 1
            continue
        while run > 15:
            zrl_code, zrl_len = ac_table[0xF0]
            writer.write(zrl_code, zrl_len)
            run -= 16
        size = _category(value)
        symbol = (run << 4) | size
        code, length = ac_table[symbol]
        writer.write(code, length)
        writer.write(_amplitude_bits(value, size), size)
        run = 0
    if run > 0:  # trailing zeros -> end-of-block.
        eob_code, eob_len = ac_table[0x00]
        writer.write(eob_code, eob_len)
    return dc


def _rgb_to_ycbcr_planes(width, height, samples):
    """Split interleaved RGB into full-resolution Y, Cb, Cr byte planes."""
    n = width * height
    y_plane = bytearray(n)
    cb_plane = bytearray(n)
    cr_plane = bytearray(n)
    for i in range(n):
        r = samples[3 * i]
        g = samples[3 * i + 1]
        b = samples[3 * i + 2]
        y_plane[i] = min(255, max(0, (19595 * r + 38470 * g + 7471 * b + 32768) >> 16))
        cb_plane[i] = min(
            255, max(0, ((-11059 * r - 21709 * g + 32768 * b + 8388608) >> 16))
        )
        cr_plane[i] = min(
            255, max(0, ((32768 * r - 27439 * g - 5329 * b + 8388608) >> 16))
        )
    return y_plane, cb_plane, cr_plane


def _downsample_2x(plane, width, height):
    """2x2 box average of a byte plane (for 4:2:0 chroma)."""
    cw = (width + 1) // 2
    ch = (height + 1) // 2
    out = bytearray(cw * ch)
    for cy in range(ch):
        y0 = 2 * cy
        y1 = min(y0 + 1, height - 1)
        for cx in range(cw):
            x0 = 2 * cx
            x1 = min(x0 + 1, width - 1)
            total = (
                plane[y0 * width + x0]
                + plane[y0 * width + x1]
                + plane[y1 * width + x0]
                + plane[y1 * width + x1]
            )
            out[cy * cw + cx] = (total + 2) >> 2
    return out, cw, ch


def _extract_block(plane, pw, ph, bx, by):
    """Read an 8x8 block at (bx, by), clamping past the plane edges (padding)."""
    block = [0] * 64
    for yy in range(8):
        sy = by + yy
        if sy >= ph:
            sy = ph - 1
        row = sy * pw
        for xx in range(8):
            sx = bx + xx
            if sx >= pw:
                sx = pw - 1
            block[yy * 8 + xx] = plane[row + sx] - 128
    return block


def _marker_segment(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + ((len(payload) + 2).to_bytes(2, "big")) + payload


def _dqt(table_id: int, qt: list[int]) -> bytes:
    body = bytes([table_id]) + bytes(qt[_ZIGZAG[i]] for i in range(64))
    return _marker_segment(0xDB, body)


def _dht(table_class: int, table_id: int, bits, vals) -> bytes:
    body = bytes([(table_class << 4) | table_id]) + bytes(bits) + bytes(vals)
    return _marker_segment(0xC4, body)


def encode(
    width: int,
    height: int,
    components: int,
    samples: bytes,
    quality: int = 75,
) -> bytes:
    """Encode interleaved 8-bit *samples* as a baseline JPEG.

    *components* must be 1 (grayscale) or 3 (RGB).  Raises ``ValueError`` for
    other component counts or a sample buffer of the wrong length.
    """
    if components not in (1, 3):
        raise ValueError("jpeg_encoder supports 1 or 3 components only")
    if width <= 0 or height <= 0:
        raise ValueError("invalid image dimensions")
    if len(samples) < width * height * components:
        raise ValueError("sample buffer too small for the given dimensions")

    luma_qt = _scaled_qt(_STD_LUMA_QT, quality)
    chroma_qt = _scaled_qt(_STD_CHROMA_QT, quality)

    writer = _BitWriter()
    if components == 1:
        _encode_gray(width, height, samples, luma_qt, writer)
        sof = _sof0(width, height, [(1, 1, 1, 0)])
        dqt = _dqt(0, luma_qt)
        dht = (
            _dht(0, 0, _BITS_DC_LUMA, _VALS_DC_LUMA)
            + _dht(1, 0, _BITS_AC_LUMA, _VALS_AC_LUMA)
        )
        sos = _sos([(1, 0, 0)])
    else:
        _encode_rgb_420(width, height, samples, luma_qt, chroma_qt, writer)
        sof = _sof0(width, height, [(1, 2, 2, 0), (2, 1, 1, 1), (3, 1, 1, 1)])
        dqt = _dqt(0, luma_qt) + _dqt(1, chroma_qt)
        dht = (
            _dht(0, 0, _BITS_DC_LUMA, _VALS_DC_LUMA)
            + _dht(1, 0, _BITS_AC_LUMA, _VALS_AC_LUMA)
            + _dht(0, 1, _BITS_DC_CHROMA, _VALS_DC_CHROMA)
            + _dht(1, 1, _BITS_AC_CHROMA, _VALS_AC_CHROMA)
        )
        sos = _sos([(1, 0, 0), (2, 1, 1), (3, 1, 1)])
    writer.flush()

    jfif = _marker_segment(
        0xE0, b"JFIF\x00" + bytes([1, 1, 0, 0, 1, 0, 1, 0, 0])
    )
    return (
        b"\xFF\xD8" + jfif + dqt + sof + dht + sos + bytes(writer.out) + b"\xFF\xD9"
    )


def _sof0(width, height, comps) -> bytes:
    body = bytearray([8])
    body += height.to_bytes(2, "big")
    body += width.to_bytes(2, "big")
    body.append(len(comps))
    for cid, h, v, qid in comps:
        body += bytes([cid, (h << 4) | v, qid])
    return _marker_segment(0xC0, bytes(body))


def _sos(comps) -> bytes:
    body = bytearray([len(comps)])
    for cid, dc_id, ac_id in comps:
        body += bytes([cid, (dc_id << 4) | ac_id])
    body += bytes([0, 63, 0])  # Ss, Se, Ah/Al
    return _marker_segment(0xDA, bytes(body))


def _encode_gray(width, height, samples, luma_qt, writer) -> None:
    prev_dc = 0
    blocks_x = (width + 7) // 8
    blocks_y = (height + 7) // 8
    for by in range(blocks_y):
        for bx in range(blocks_x):
            block = _extract_block(samples, width, height, bx * 8, by * 8)
            coeffs = _fdct_quantize(block, luma_qt)
            prev_dc = _encode_block(
                coeffs, prev_dc, _HUFF_DC_LUMA, _HUFF_AC_LUMA, writer
            )


def _encode_rgb_420(width, height, samples, luma_qt, chroma_qt, writer) -> None:
    y_plane, cb_full, cr_full = _rgb_to_ycbcr_planes(width, height, samples)
    cb_plane, cw, ch = _downsample_2x(cb_full, width, height)
    cr_plane, _cw, _ch = _downsample_2x(cr_full, width, height)

    mcus_x = (width + 15) // 16
    mcus_y = (height + 15) // 16
    dc_y = dc_cb = dc_cr = 0
    for my in range(mcus_y):
        for mx in range(mcus_x):
            for dy, dx in ((0, 0), (0, 8), (8, 0), (8, 8)):
                block = _extract_block(
                    y_plane, width, height, mx * 16 + dx, my * 16 + dy
                )
                coeffs = _fdct_quantize(block, luma_qt)
                dc_y = _encode_block(
                    coeffs, dc_y, _HUFF_DC_LUMA, _HUFF_AC_LUMA, writer
                )
            cb_block = _extract_block(cb_plane, cw, ch, mx * 8, my * 8)
            dc_cb = _encode_block(
                _fdct_quantize(cb_block, chroma_qt),
                dc_cb, _HUFF_DC_CHROMA, _HUFF_AC_CHROMA, writer,
            )
            cr_block = _extract_block(cr_plane, cw, ch, mx * 8, my * 8)
            dc_cr = _encode_block(
                _fdct_quantize(cr_block, chroma_qt),
                dc_cr, _HUFF_DC_CHROMA, _HUFF_AC_CHROMA, writer,
            )
