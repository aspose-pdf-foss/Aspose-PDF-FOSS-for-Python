"""Dependency-free JPEG (``DCTDecode``) decoder.

Decodes **baseline sequential** (marker ``SOF0``) and **progressive**
(``SOF2``) DCT JPEG with Huffman entropy coding into raw 8-bit, row-major,
component-interleaved samples:

* 1 component  -> grayscale,
* 3 components -> YCbCr converted to RGB (or kept RGB for an Adobe
  ``transform = 0`` stream),
* 4 components -> CMYK; Adobe YCCK (``transform = 2``) is converted to CMYK and
  Adobe streams are de-inverted to the ``0 = no ink`` convention.

Chroma subsampling (4:4:4 / 4:2:2 / 4:2:0, and other integer sampling factors)
and restart intervals are supported in both modes. Arithmetic-coded and
lossless JPEGs are **not** handled -- :func:`decode` returns ``None`` for those
so the caller can fall back to Pillow.

Only the standard library is used (``math``/``struct``), matching the engine's
other pure-Python codecs. Parsing is defensive: malformed input never raises,
:func:`decode` simply returns ``None``.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass

__all__ = ["decode", "DecodedJpeg"]

# Zig-zag order: coefficient k in the entropy stream maps to natural index
# ``_ZIGZAG[k]`` of the 8x8 block.
_ZIGZAG = (
    0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5,
    12, 19, 26, 33, 40, 48, 41, 34, 27, 20, 13, 6, 7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37, 44, 51,
    58, 59, 52, 45, 38, 31, 39, 46, 53, 60, 61, 54, 47, 55, 62, 63,
)  # fmt: skip

# Start-of-frame markers we cannot decode (extended sequential, lossless,
# differential, and arithmetic-coded variants). ``SOF0``/``SOF2`` (baseline and
# progressive Huffman) are handled.
_UNSUPPORTED_SOF = frozenset(
    {0xC1, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)

# Pre-computed inverse-DCT basis: _IDCT_BASIS[u][x] = C(u)·cos((2x+1)uπ/16).
_IDCT_BASIS = [
    [
        (math.sqrt(0.5) if u == 0 else 1.0) * math.cos((2 * x + 1) * u * math.pi / 16)
        for x in range(8)
    ]
    for u in range(8)
]


@dataclass
class DecodedJpeg:
    """A decoded JPEG image."""

    width: int
    height: int
    components: int  # 1 (gray), 3 (RGB) or 4 (CMYK)
    samples: bytes  # row-major, interleaved, 8 bits per sample

    @property
    def mode(self) -> str:
        return {1: "L", 4: "CMYK"}.get(self.components, "RGB")


def decode(data: bytes) -> DecodedJpeg | None:
    """Decode baseline JPEG *data*; return ``None`` if it is not supported."""
    try:
        return _decode(data)
    except (struct.error, IndexError, ValueError, ZeroDivisionError):
        return None


# ---------------------------------------------------------------------------
# Marker parsing
# ---------------------------------------------------------------------------


@dataclass
class _Component:
    cid: int
    h: int  # horizontal sampling factor
    v: int  # vertical sampling factor
    quant_id: int
    dc_table: int = 0
    ac_table: int = 0


def _decode(data: bytes) -> DecodedJpeg | None:
    if len(data) < 2 or data[0] != 0xFF or data[1] != 0xD8:
        return None  # missing SOI

    pos = 2
    n = len(data)
    quant: dict[int, list[int]] = {}
    huff_dc: dict[int, dict] = {}
    huff_ac: dict[int, dict] = {}
    components: list[_Component] = []
    width = height = 0
    restart_interval = 0
    adobe_transform: int | None = None
    progressive = False
    prog: _ProgState | None = None

    while pos + 1 < n:
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        pos += 2
        if marker == 0xD9:  # EOI
            break
        if marker == 0xD8 or 0xD0 <= marker <= 0xD7 or marker == 0x01:
            continue  # SOI / RSTn / TEM: no payload
        if pos + 2 > n:
            break
        seg_len = struct.unpack_from(">H", data, pos)[0]
        seg = data[pos + 2 : pos + seg_len]
        pos += seg_len

        if marker == 0xC0:  # SOF0 -- baseline
            width, height, components = _parse_sof(seg)
        elif marker == 0xC2:  # SOF2 -- progressive
            width, height, components = _parse_sof(seg)
            progressive = True
        elif marker in _UNSUPPORTED_SOF:
            return None  # extended/arithmetic/lossless: unsupported
        elif marker == 0xDB:  # DQT
            _parse_dqt(seg, quant)
        elif marker == 0xC4:  # DHT
            _parse_dht(seg, huff_dc, huff_ac)
        elif marker == 0xDD:  # DRI
            restart_interval = struct.unpack_from(">H", seg, 0)[0]
        elif marker == 0xEE:  # APP14 (Adobe)
            if seg[:5] == b"Adobe" and len(seg) >= 12:
                adobe_transform = seg[11]
        elif marker == 0xDA:  # SOS -- start of scan; entropy data follows
            if not progressive:
                scan_comps = _parse_sos(seg, components)
                if scan_comps is None:
                    return None
                return _decode_scan(
                    data, pos, width, height, components,
                    quant, huff_dc, huff_ac, restart_interval, adobe_transform,
                )
            scan = _parse_sos_progressive(seg, components)
            if scan is None:
                return None
            if prog is None:
                prog = _ProgState(width, height, components)
            pos = _decode_progressive_scan(
                data, pos, prog, scan, huff_dc, huff_ac, restart_interval
            )

    if progressive and prog is not None:
        return prog.finalize(quant, adobe_transform)
    return None


def _parse_sof(seg: bytes):
    precision = seg[0]
    if precision != 8:
        raise ValueError("only 8-bit precision supported")
    height = struct.unpack_from(">H", seg, 1)[0]
    width = struct.unpack_from(">H", seg, 3)[0]
    count = seg[5]
    comps = []
    for i in range(count):
        base = 6 + i * 3
        cid = seg[base]
        sampling = seg[base + 1]
        comps.append(_Component(cid, sampling >> 4, sampling & 0x0F, seg[base + 2]))
    return width, height, comps


def _parse_dqt(seg: bytes, quant: dict) -> None:
    i = 0
    while i < len(seg):
        pq = seg[i] >> 4  # 0 = 8-bit, 1 = 16-bit
        tq = seg[i] & 0x0F
        i += 1
        table = [0] * 64
        for k in range(64):
            if pq:
                table[k] = struct.unpack_from(">H", seg, i)[0]
                i += 2
            else:
                table[k] = seg[i]
                i += 1
        quant[tq] = table  # kept in zig-zag order


def _parse_dht(seg: bytes, huff_dc: dict, huff_ac: dict) -> None:
    i = 0
    while i < len(seg):
        tc = seg[i] >> 4  # 0 = DC, 1 = AC
        th = seg[i] & 0x0F
        i += 1
        counts = list(seg[i : i + 16])
        i += 16
        total = sum(counts)
        symbols = list(seg[i : i + total])
        i += total
        table = _build_huffman(counts, symbols)
        (huff_dc if tc == 0 else huff_ac)[th] = table


def _build_huffman(counts: list[int], symbols: list[int]) -> dict:
    """Build a ``(length, code) -> symbol`` map from canonical JPEG Huffman data."""
    table: dict[tuple[int, int], int] = {}
    code = 0
    si = 0
    for length in range(1, 17):
        for _ in range(counts[length - 1]):
            table[(length, code)] = symbols[si]
            si += 1
            code += 1
        code <<= 1
    return table


def _parse_sos(seg: bytes, components: list[_Component]):
    count = seg[0]
    by_id = {c.cid: c for c in components}
    scan = []
    for i in range(count):
        cid = seg[1 + i * 2]
        tables = seg[2 + i * 2]
        comp = by_id.get(cid)
        if comp is None:
            return None
        comp.dc_table = tables >> 4
        comp.ac_table = tables & 0x0F
        scan.append(comp)
    return scan


# ---------------------------------------------------------------------------
# Entropy-coded scan decoding
# ---------------------------------------------------------------------------


class _BitReader:
    """MSB-first bit reader over JPEG entropy data with byte de-stuffing."""

    def __init__(self, data: bytes, start: int) -> None:
        self.data = data
        self.pos = start
        self._bits = 0
        self._count = 0

    def _next_byte(self):
        data = self.data
        if self.pos >= len(data):
            return None
        byte = data[self.pos]
        if byte == 0xFF:
            nxt = data[self.pos + 1] if self.pos + 1 < len(data) else 0
            if nxt == 0x00:
                self.pos += 2
                return 0xFF
            return None  # a real marker -- stop feeding bits
        self.pos += 1
        return byte

    def bit(self) -> int:
        if self._count == 0:
            byte = self._next_byte()
            if byte is None:
                return 0  # pad with zeros at a marker boundary
            self._bits = byte
            self._count = 8
        self._count -= 1
        return (self._bits >> self._count) & 1

    def bits(self, n: int) -> int:
        value = 0
        for _ in range(n):
            value = (value << 1) | self.bit()
        return value

    def restart(self) -> None:
        """Byte-align and skip the next ``RSTn`` marker (restart interval)."""
        self._count = 0
        data = self.data
        while self.pos + 1 < len(data):
            if data[self.pos] == 0xFF and 0xD0 <= data[self.pos + 1] <= 0xD7:
                self.pos += 2
                return
            self.pos += 1


def _huffman_decode(reader: _BitReader, table: dict) -> int:
    code = 0
    for length in range(1, 17):
        code = (code << 1) | reader.bit()
        symbol = table.get((length, code))
        if symbol is not None:
            return symbol
    return 0


def _extend(value: int, size: int) -> int:
    """JPEG ``RECEIVE``/``EXTEND``: sign-extend *size*-bit magnitude *value*."""
    if size and value < (1 << (size - 1)):
        return value - (1 << size) + 1
    return value


def _decode_scan(
    data, start, width, height, components, quant, huff_dc, huff_ac,
    restart_interval, adobe_transform,
):  # fmt: skip
    if width <= 0 or height <= 0 or len(components) not in (1, 3, 4):
        return None  # grayscale, 3-component (YCbCr/RGB) and 4-component (CMYK)
    h_max = max(c.h for c in components)
    v_max = max(c.v for c in components)
    mcu_w = 8 * h_max
    mcu_h = 8 * v_max
    mcus_x = (width + mcu_w - 1) // mcu_w
    mcus_y = (height + mcu_h - 1) // mcu_h

    # One full-resolution-for-this-component plane per component.
    planes = []
    comp_dims = []
    for comp in components:
        cw = mcus_x * comp.h * 8
        ch = mcus_y * comp.v * 8
        planes.append(bytearray(cw * ch))
        comp_dims.append((cw, ch))

    reader = _BitReader(data, start)
    preds = [0] * len(components)
    mcu_count = 0
    for my in range(mcus_y):
        for mx in range(mcus_x):
            if restart_interval and mcu_count and mcu_count % restart_interval == 0:
                reader.restart()
                preds = [0] * len(components)
            for ci, comp in enumerate(components):
                cw, _ch = comp_dims[ci]
                qt = quant.get(comp.quant_id)
                dc_tab = huff_dc.get(comp.dc_table)
                ac_tab = huff_ac.get(comp.ac_table)
                if qt is None or dc_tab is None or ac_tab is None:
                    return None
                for by in range(comp.v):
                    for bx in range(comp.h):
                        block, preds[ci] = _decode_block(
                            reader, dc_tab, ac_tab, qt, preds[ci]
                        )
                        px = (mx * comp.h + bx) * 8
                        py = (my * comp.v + by) * 8
                        _place_block(planes[ci], cw, px, py, block)
            mcu_count += 1

    return _assemble(
        width, height, components, comp_dims, planes, h_max, v_max, adobe_transform
    )


def _decode_block(reader, dc_table, ac_table, qt, pred):
    """Decode one 8x8 block into natural-order, dequantised coefficients."""
    coeffs = [0] * 64
    size = _huffman_decode(reader, dc_table)
    diff = _extend(reader.bits(size), size) if size else 0
    dc = pred + diff
    coeffs[0] = dc * qt[0]
    k = 1
    while k < 64:
        rs = _huffman_decode(reader, ac_table)
        run = rs >> 4
        size = rs & 0x0F
        if size == 0:
            if run != 15:  # EOB
                break
            k += 16  # ZRL: 16 zeros
            continue
        k += run
        if k >= 64:
            break
        value = _extend(reader.bits(size), size)
        coeffs[_ZIGZAG[k]] = value * qt[k]
        k += 1
    return _idct(coeffs), dc


def _idct(coeffs) -> list[int]:
    """8x8 inverse DCT (separable) with level shift and clamping to 0..255."""
    basis = _IDCT_BASIS
    # Pass 1: columns (over u) -> tmp[x][v].
    tmp = [[0.0] * 8 for _ in range(8)]
    for v in range(8):
        col = coeffs[v::8]  # coeffs[u*8 + v] for u in 0..7
        if not any(col):
            continue
        for x in range(8):
            bx = basis  # local
            tmp[x][v] = (
                bx[0][x] * col[0] + bx[1][x] * col[1] + bx[2][x] * col[2]
                + bx[3][x] * col[3] + bx[4][x] * col[4] + bx[5][x] * col[5]
                + bx[6][x] * col[6] + bx[7][x] * col[7]
            )
    # Pass 2: rows (over v) -> spatial samples.
    out = [0] * 64
    for x in range(8):
        row = tmp[x]
        for y in range(8):
            s = (
                basis[0][y] * row[0] + basis[1][y] * row[1] + basis[2][y] * row[2]
                + basis[3][y] * row[3] + basis[4][y] * row[4] + basis[5][y] * row[5]
                + basis[6][y] * row[6] + basis[7][y] * row[7]
            )
            value = int(s * 0.25 + 128.5)
            out[x * 8 + y] = 0 if value < 0 else 255 if value > 255 else value
    return out


def _place_block(plane: bytearray, plane_w: int, px: int, py: int, block) -> None:
    plane_h = len(plane) // plane_w
    for r in range(8):
        y = py + r
        if y >= plane_h:
            break
        dst = y * plane_w + px
        src = r * 8
        plane[dst : dst + 8] = bytes(block[src : src + 8])


def _assemble(width, height, components, comp_dims, planes, h_max, v_max, transform):
    n = len(components)
    out = bytearray(width * height * n)
    # Per-component upsampling factors (component plane -> full resolution).
    for ci, comp in enumerate(components):
        cw, _ch = comp_dims[ci]
        plane = planes[ci]
        sx = h_max // comp.h
        sy = v_max // comp.v
        for y in range(height):
            row = (y // sy) * cw
            base = y * width * n + ci
            for x in range(width):
                out[base + x * n] = plane[row + x // sx]

    if n == 3 and transform != 0:
        _ycbcr_to_rgb(out)
    elif n == 4:
        _finish_cmyk(out, transform)
    return DecodedJpeg(width, height, n, bytes(out))


def _finish_cmyk(buf: bytearray, transform: int | None) -> None:
    """Turn 4-component samples into standard (``0 = no ink``) CMYK.

    ``transform == 2`` (Adobe YCCK) carries YCbCr in the first three channels;
    they are converted to RGB first. Adobe streams (any APP14 transform) store
    CMYK inverted, so all four channels are then inverted -- yielding the
    convention :func:`aspose_pdf.engine.image_export.cmyk_to_rgb` expects. With
    no Adobe marker the samples are assumed to already be standard CMYK.
    """
    if transform == 2:
        for i in range(0, len(buf), 4):
            y = buf[i]
            cb = buf[i + 1] - 128
            cr = buf[i + 2] - 128
            r = y + ((91881 * cr) >> 16)
            g = y - ((22554 * cb + 46802 * cr) >> 16)
            b = y + ((116130 * cb) >> 16)
            buf[i] = 0 if r < 0 else 255 if r > 255 else r
            buf[i + 1] = 0 if g < 0 else 255 if g > 255 else g
            buf[i + 2] = 0 if b < 0 else 255 if b > 255 else b
    if transform is not None:  # Adobe: de-invert to standard CMYK
        for i in range(len(buf)):
            buf[i] = 255 - buf[i]


def _ycbcr_to_rgb(buf: bytearray) -> None:
    for i in range(0, len(buf), 3):
        y = buf[i]
        cb = buf[i + 1] - 128
        cr = buf[i + 2] - 128
        r = y + ((91881 * cr) >> 16)
        g = y - ((22554 * cb + 46802 * cr) >> 16)
        b = y + ((116130 * cb) >> 16)
        buf[i] = 0 if r < 0 else 255 if r > 255 else r
        buf[i + 1] = 0 if g < 0 else 255 if g > 255 else g
        buf[i + 2] = 0 if b < 0 else 255 if b > 255 else b


# ---------------------------------------------------------------------------
# Progressive (SOF2) decoding
#
# A progressive JPEG sends its DCT coefficients across several scans: DC and AC
# bands are refined separately, with spectral selection (``Ss``..``Se``) and
# successive approximation (``Ah``/``Al``). We accumulate every coefficient
# (zig-zag order) into per-component block grids over all scans, then run the
# inverse DCT once at the end. The band decoders mirror the classic libjpeg
# logic (DC first/refine, AC first/refine with EOB runs).
# ---------------------------------------------------------------------------


def _ceil_div(a: int, b: int) -> int:
    return (a + b - 1) // b


def _next_marker_pos(data: bytes, pos: int) -> int:
    """Index of the next real marker at/after *pos* (skips stuffing and RSTn)."""
    n = len(data)
    i = pos
    while i + 1 < n:
        if data[i] == 0xFF:
            b = data[i + 1]
            if b == 0x00 or 0xD0 <= b <= 0xD7:
                i += 2  # byte-stuffed 0xFF or restart marker: part of the scan
                continue
            if b == 0xFF:
                i += 1
                continue
            return i
        i += 1
    return n


def _parse_sos_progressive(seg: bytes, components: list[_Component]):
    """Parse an SOS header into ``(scan_components, Ss, Se, Ah, Al)``."""
    count = seg[0]
    by_id = {c.cid: c for c in components}
    scan = []
    for i in range(count):
        cid = seg[1 + i * 2]
        tables = seg[2 + i * 2]
        comp = by_id.get(cid)
        if comp is None:
            return None
        comp.dc_table = tables >> 4
        comp.ac_table = tables & 0x0F
        scan.append(comp)
    base = 1 + count * 2
    ss = seg[base]
    se = seg[base + 1]
    approx = seg[base + 2]
    return scan, ss, se, approx >> 4, approx & 0x0F


class _ProgState:
    """Per-component DCT coefficient grids accumulated across progressive scans."""

    def __init__(self, width: int, height: int, components: list[_Component]):
        self.width = width
        self.height = height
        self.components = components
        self.h_max = max(c.h for c in components)
        self.v_max = max(c.v for c in components)
        self.mcus_x = _ceil_div(width, 8 * self.h_max)
        self.mcus_y = _ceil_div(height, 8 * self.v_max)
        self.blocks_per_line = []  # MCU-aligned stride per component
        self.coef = []  # list[component] -> list[block] -> list[64] (zig-zag)
        for c in components:
            bpl = self.mcus_x * c.h
            bpc = self.mcus_y * c.v
            self.blocks_per_line.append(bpl)
            self.coef.append([[0] * 64 for _ in range(bpl * bpc)])

    def finalize(self, quant: dict, transform: int | None) -> DecodedJpeg | None:
        planes = []
        comp_dims = []
        for ci, comp in enumerate(self.components):
            qt = quant.get(comp.quant_id)
            if qt is None:
                return None
            bpl = self.blocks_per_line[ci]
            bpc = len(self.coef[ci]) // bpl
            cw = bpl * 8
            plane = bytearray(cw * bpc * 8)
            for brow in range(bpc):
                base = brow * bpl
                for bcol in range(bpl):
                    zz = self.coef[ci][base + bcol]
                    natural = [0] * 64
                    for k in range(64):
                        natural[_ZIGZAG[k]] = zz[k] * qt[k]
                    _place_block(plane, cw, bcol * 8, brow * 8, _idct(natural))
            planes.append(plane)
            comp_dims.append((cw, bpc * 8))
        if len(self.components) not in (1, 3, 4):
            return None
        return _assemble(
            self.width, self.height, self.components, comp_dims,
            planes, self.h_max, self.v_max, transform,
        )


class _ScanState:
    """Mutable per-scan state: end-of-band run plus per-component DC predictors."""

    __slots__ = ("eobrun", "preds")

    def __init__(self) -> None:
        self.eobrun = 0
        self.preds = {}


def _decode_progressive_scan(data, pos, prog, scan, huff_dc, huff_ac, restart):
    comps, ss, se, ah, al = scan
    reader = _BitReader(data, pos)
    state = _ScanState()

    if ss == 0:  # DC band
        dc_tabs = [huff_dc.get(c.dc_table) for c in comps]
        if any(t is None for t in dc_tabs):
            return _next_marker_pos(data, reader.pos)
        if len(comps) == 1:
            ci = prog.components.index(comps[0])

            def decode_dc(block, _ci=ci, _t=dc_tabs[0]):
                _dc_block(reader, block, _t, ah, al, _ci, state)

            _scan_component(prog, ci, comps[0], restart, reader, state, decode_dc)
        else:
            _scan_mcus(prog, comps, restart, reader, state, ah, al, dc_tabs)
    else:  # AC band -- always a single component
        comp = comps[0]
        ci = prog.components.index(comp)
        ac_tab = huff_ac.get(comp.ac_table)
        if ac_tab is None:
            return _next_marker_pos(data, reader.pos)
        if ah == 0:
            def decode_ac(block):
                _ac_first(reader, block, ac_tab, ss, se, al, state)
        else:
            def decode_ac(block):
                _ac_refine(reader, block, ac_tab, ss, se, al, state)
        _scan_component(prog, ci, comp, restart, reader, state, decode_ac)

    return _next_marker_pos(data, reader.pos)


def _dc_block(reader, block, dc_tab, ah, al, ci, state):
    if ah == 0:  # first DC scan: high bits of the value
        size = _huffman_decode(reader, dc_tab)
        diff = _extend(reader.bits(size), size) if size else 0
        pred = state.preds.get(ci, 0) + diff
        state.preds[ci] = pred
        block[0] = pred << al
    else:  # refinement scan: one more low bit
        if reader.bit():
            block[0] |= 1 << al


def _scan_component(prog, ci, comp, restart, reader, state, decode_block):
    """Iterate a single component's own (non-interleaved) block grid."""
    bpl = prog.blocks_per_line[ci]
    blocks_x = _ceil_div(prog.width * comp.h, 8 * prog.h_max)
    blocks_y = _ceil_div(prog.height * comp.v, 8 * prog.v_max)
    coef = prog.coef[ci]
    unit = 0
    for row in range(blocks_y):
        base = row * bpl
        for col in range(blocks_x):
            if restart and unit and unit % restart == 0:
                reader.restart()
                state.eobrun = 0
                state.preds.clear()
            decode_block(coef[base + col])
            unit += 1


def _scan_mcus(prog, comps, restart, reader, state, ah, al, dc_tabs):
    """Iterate interleaved MCUs for a multi-component DC scan."""
    cis = [prog.components.index(c) for c in comps]
    unit = 0
    for my in range(prog.mcus_y):
        for mx in range(prog.mcus_x):
            if restart and unit and unit % restart == 0:
                reader.restart()
                state.eobrun = 0
                state.preds.clear()
            for comp, ci, dc_tab in zip(comps, cis, dc_tabs):
                bpl = prog.blocks_per_line[ci]
                coef = prog.coef[ci]
                for by in range(comp.v):
                    row = my * comp.v + by
                    for bx in range(comp.h):
                        col = mx * comp.h + bx
                        _dc_block(reader, coef[row * bpl + col], dc_tab, ah, al, ci, state)
            unit += 1


def _ac_first(reader, block, ac_tab, ss, se, al, state):
    """First AC scan for a band: spectral selection with EOB runs."""
    if state.eobrun > 0:
        state.eobrun -= 1
        return
    k = ss
    while k <= se:
        rs = _huffman_decode(reader, ac_tab)
        run = rs >> 4
        size = rs & 0x0F
        if size == 0:
            if run < 15:  # EOBn: this and the next ``2^run + extra - 1`` blocks
                state.eobrun = (1 << run) - 1
                if run:
                    state.eobrun += reader.bits(run)
                break
            k += 16  # ZRL: skip 16 zero coefficients
            continue
        k += run
        if k > se:
            break
        block[k] = _extend(reader.bits(size), size) * (1 << al)
        k += 1


def _refine_band(reader, block, lo, hi, bit):
    """Read one correction bit for each already-nonzero coefficient in a band."""
    for j in range(lo, hi + 1):
        coef = block[j]
        if coef != 0 and reader.bit() and (coef & bit) == 0:
            block[j] = coef + (bit if coef > 0 else -bit)


def _ac_refine(reader, block, ac_tab, ss, se, al, state):
    """AC refinement scan: correction bits + newly significant coefficients."""
    bit = 1 << al
    if state.eobrun > 0:
        state.eobrun -= 1
        _refine_band(reader, block, ss, se, bit)
        return
    k = ss
    while k <= se:
        rs = _huffman_decode(reader, ac_tab)
        run = rs >> 4
        size = rs & 0x0F
        newval = 0
        if size == 0:
            if run < 15:  # EOB run starts; refine this block's tail and stop
                state.eobrun = (1 << run) - 1
                if run:
                    state.eobrun += reader.bits(run)
                _refine_band(reader, block, k, se, bit)
                return
            # run == 15 (ZRL): advance past 16 zero-history coefficients
        else:
            newval = bit if reader.bit() else -bit
        # Walk forward: refine nonzero coefficients we pass, count down ``run``
        # zero-history coefficients; the next zero takes the new value.
        while k <= se:
            coef = block[k]
            if coef != 0:
                if reader.bit() and (coef & bit) == 0:
                    block[k] = coef + (bit if coef > 0 else -bit)
            else:
                if run == 0:
                    break
                run -= 1
            k += 1
        if newval and k <= se:
            block[k] = newval
        k += 1
