"""Pure-Python JPEG 2000 decoder (ISO/IEC 15444-1), for ``/JPXDecode``.

Scanners emit JPEG 2000 constantly, so a PDF library that cannot decode it has
a hole where whole pages should be. This module fills that hole without a
third-party codec: it reads the JP2 container (or a bare codestream), walks the
tier-2 packet structure, runs the EBCOT tier-1 block decoder over the MQ
arithmetic coder, dequantises, inverts the wavelet transform and undoes the
component transform.

Layout, in decoding order:

* :class:`MQDecoder` -- the arithmetic decoder of Annex C, shared by every
  code-block.
* :class:`TagTree` and :class:`_HeaderReader` -- the two primitives packet
  headers are written in (Annex B), including the bit-stuffing rule that makes
  a header byte after ``0xFF`` carry only seven bits.
* :class:`Codestream` and friends -- the marker segments (Annex A) and the
  tile / component / resolution / subband / precinct / code-block hierarchy
  they describe.
* :func:`_decode_codeblock` -- the three coding passes of Annex D.
* :func:`_inverse_dwt` -- the 5/3 reversible and 9/7 irreversible synthesis
  filters of Annex F.

What it does not do is listed in :data:`_UNSUPPORTED`: those cases raise rather
than return a plausible-looking image, because a wrong page is worse than a
missing one.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from aspose_pdf.exceptions import PdfResourceLimitException, PdfValidationException
from aspose_pdf.load_limits import PdfLoadLimits, _coerce_limits

__all__ = ["DecodedImage", "decode"]

# --- markers (Annex A, Table A.2) ------------------------------------------
_SOC = 0xFF4F
_SIZ = 0xFF51
_COD = 0xFF52
_COC = 0xFF53
_TLM = 0xFF55
_PLM = 0xFF57
_PLT = 0xFF58
_QCD = 0xFF5C
_QCC = 0xFF5D
_RGN = 0xFF5E
_POC = 0xFF5F
_PPM = 0xFF60
_PPT = 0xFF61
_CRG = 0xFF63
_COM = 0xFF64
_SOT = 0xFF90
_SOP = 0xFF91
_EPH = 0xFF92
_SOD = 0xFF93
_EOC = 0xFFD9

_UNSUPPORTED = {
    _PPM: "packed packet headers in the main header (PPM)",
    _PPT: "packed packet headers in a tile header (PPT)",
    _POC: "progression order changes (POC)",
}

_MAX_DECOMPOSITION_LEVELS = 32
_MAX_COMPONENTS = 16
_MAX_CODING_PASSES = 164


class Jpeg2000Error(PdfValidationException):
    """A JPEG 2000 codestream that cannot be decoded."""


# ---------------------------------------------------------------------------
# MQ arithmetic decoder (Annex C)
# ---------------------------------------------------------------------------
# Context indices shared by the coder and the coding passes.
_RUNLENGTH_CTX = 17
_UNIFORM_CTX = 18

# (Qe, NMPS, NLPS, SWITCH) indexed by the context's state.
_QE: tuple[tuple[int, int, int, int], ...] = (
    (0x5601, 1, 1, 1), (0x3401, 2, 6, 0), (0x1801, 3, 9, 0), (0x0AC1, 4, 12, 0),
    (0x0521, 5, 29, 0), (0x0221, 38, 33, 0), (0x5601, 7, 6, 1), (0x5401, 8, 14, 0),
    (0x4801, 9, 14, 0), (0x3801, 10, 14, 0), (0x3001, 11, 17, 0), (0x2401, 12, 18, 0),
    (0x1C01, 13, 20, 0), (0x1601, 29, 21, 0), (0x5601, 15, 14, 1), (0x5401, 16, 14, 0),
    (0x5101, 17, 15, 0), (0x4801, 18, 16, 0), (0x3801, 19, 17, 0), (0x3401, 20, 18, 0),
    (0x3001, 21, 19, 0), (0x2801, 22, 19, 0), (0x2401, 23, 20, 0), (0x2201, 24, 21, 0),
    (0x1C01, 25, 22, 0), (0x1801, 26, 23, 0), (0x1601, 27, 24, 0), (0x1401, 28, 25, 0),
    (0x1201, 29, 26, 0), (0x1101, 30, 27, 0), (0x0AC1, 31, 28, 0), (0x09C1, 32, 29, 0),
    (0x08A1, 33, 30, 0), (0x0521, 34, 31, 0), (0x0441, 35, 32, 0), (0x02A1, 36, 33, 0),
    (0x0221, 37, 34, 0), (0x0141, 38, 35, 0), (0x0111, 39, 36, 0), (0x0085, 40, 37, 0),
    (0x0049, 41, 38, 0), (0x0025, 42, 39, 0), (0x0015, 43, 40, 0), (0x0009, 44, 41, 0),
    (0x0005, 45, 42, 0), (0x0001, 45, 43, 0), (0x5601, 46, 46, 0),
)


def initial_states(contexts: int = 19) -> list[list[int]]:
    """Initial ``(index, mps)`` per context (Table D.7).

    Three contexts do not start at state 0: the all-insignificant zero-coding
    context, the run-length context and the uniform context each begin part-way
    up the probability ladder, which is what makes the very first symbols of a
    cleanup pass decode correctly.
    """
    states = [[0, 0] for _ in range(contexts)]
    states[0] = [4, 0]
    if contexts > _RUNLENGTH_CTX:
        states[_RUNLENGTH_CTX] = [3, 0]
    if contexts > _UNIFORM_CTX:
        states[_UNIFORM_CTX] = [46, 0]
    return states


class MQDecoder:
    """The MQ arithmetic decoder of Annex C.

    ``states`` holds one ``(index, mps)`` pair per context; the caller owns it
    so a code-block can reset or carry contexts across coding passes as the
    code-block style demands.
    """

    __slots__ = ("_a", "_bp", "_c", "_contexts", "_ct", "_data", "_end", "states")

    def __init__(self, data: bytes, contexts: int = 19) -> None:
        self._data = data
        self._end = len(data)
        self._contexts = contexts
        self.states: list[list[int]] = initial_states(contexts)
        self.restart(0)

    def reset_states(self) -> None:
        """Return every context to the initial state of Table D.7."""
        self.states = initial_states(self._contexts)

    def restart(self, position: int) -> None:
        """(Re)initialise the decoder at *position* (INITDEC)."""
        self._bp = position
        byte = self._byte(self._bp)
        self._c = byte << 16
        self._bytein()
        self._c = (self._c << 7) & 0xFFFFFFFF
        self._ct -= 7
        self._a = 0x8000

    def _byte(self, index: int) -> int:
        # Past the end of a segment the decoder must behave as if fed 0xFF,
        # which is what a terminated codestream implies (C.3.4).
        return self._data[index] if 0 <= index < self._end else 0xFF

    def _bytein(self) -> None:
        if self._byte(self._bp) == 0xFF:
            if self._byte(self._bp + 1) > 0x8F:
                self._c += 0xFF00
                self._ct = 8
            else:
                self._bp += 1
                self._c += self._byte(self._bp) << 9
                self._ct = 7
        else:
            self._bp += 1
            self._c += self._byte(self._bp) << 8
            self._ct = 8

    def decode(self, context: int) -> int:
        """Decode one bit in *context*."""
        state = self.states[context]
        qe, nmps, nlps, switch = _QE[state[0]]
        self._a -= qe
        if ((self._c >> 16) & 0xFFFF) < qe:
            # LPS exchange
            if self._a < qe:
                bit = state[1]
                state[0] = nmps
            else:
                bit = 1 - state[1]
                if switch:
                    state[1] = 1 - state[1]
                state[0] = nlps
            self._a = qe
        else:
            self._c -= qe << 16
            if self._a & 0x8000:
                return state[1]
            # MPS exchange
            if self._a < qe:
                bit = 1 - state[1]
                if switch:
                    state[1] = 1 - state[1]
                state[0] = nlps
            else:
                bit = state[1]
                state[0] = nmps
        # renormalise
        while True:
            if self._ct == 0:
                self._bytein()
            self._a = (self._a << 1) & 0xFFFF
            self._c = (self._c << 1) & 0xFFFFFFFF
            self._ct -= 1
            if self._a & 0x8000:
                break
        return bit


# ---------------------------------------------------------------------------
# Packet-header primitives (Annex B)
# ---------------------------------------------------------------------------
class _HeaderReader:
    """Bit reader for packet headers, with the ``0xFF`` stuffing rule.

    A header byte that follows ``0xFF`` carries only seven bits (B.10.1), so
    the reader tracks the previous byte rather than counting bits blindly.
    """

    __slots__ = ("_bits", "_buffer", "_data", "_last", "pos")

    def __init__(self, data: bytes, pos: int = 0) -> None:
        self._data = data
        self.pos = pos
        self._buffer = 0
        self._bits = 0
        self._last = 0

    def bit(self) -> int:
        if self._bits == 0:
            if self.pos >= len(self._data):
                raise Jpeg2000Error("packet header ended early")
            byte = self._data[self.pos]
            self.pos += 1
            if self._last == 0xFF:
                if byte > 0x8F:
                    raise Jpeg2000Error("invalid stuffing in a packet header")
                self._buffer = byte
                self._bits = 7
            else:
                self._buffer = byte
                self._bits = 8
            self._last = byte
        self._bits -= 1
        return (self._buffer >> self._bits) & 1

    def bits(self, count: int) -> int:
        value = 0
        for _ in range(count):
            value = (value << 1) | self.bit()
        return value

    def align(self) -> None:
        """Finish the header: drop partial bits and skip a stuffed byte."""
        self._bits = 0
        if self._last == 0xFF:
            if self.pos < len(self._data) and self._data[self.pos] == 0x7F:
                self.pos += 1
            self._last = 0


class TagTree:
    """The tag tree of B.10.2, used for inclusion and zero-bitplane signalling."""

    __slots__ = ("_dims", "_levels", "_state", "_value")

    def __init__(self, width: int, height: int) -> None:
        dims: list[tuple[int, int]] = []
        w, h = max(1, width), max(1, height)
        while True:
            dims.append((w, h))
            if w == 1 and h == 1:
                break
            w = (w + 1) // 2
            h = (h + 1) // 2
        self._dims = dims
        self._levels = len(dims)
        self._value = [[0] * (w * h) for w, h in dims]
        self._state = [[0] * (w * h) for w, h in dims]

    def decode(self, reader: _HeaderReader, x: int, y: int, threshold: int) -> int:
        """Return the node value at ``(x, y)``, or *threshold* when still above it.

        A value below *threshold* is exact; a value equal to it means "not yet
        determined", which is how inclusion signalling says "not in this layer".
        """
        lower = 0
        for level in range(self._levels - 1, -1, -1):
            width = self._dims[level][0]
            index = (y >> level) * width + (x >> level)
            values = self._value[level]
            state = self._state[level]
            if values[index] < lower:
                values[index] = lower
            while state[index] == 0 and values[index] < threshold:
                if reader.bit():
                    state[index] = 1
                else:
                    values[index] += 1
            lower = values[index]
            if state[index] == 0:
                return threshold
        return lower


# ---------------------------------------------------------------------------
# Codestream structures (Annex A)
# ---------------------------------------------------------------------------
@dataclass
class _Siz:
    width: int
    height: int
    x0: int
    y0: int
    tile_width: int
    tile_height: int
    tile_x0: int
    tile_y0: int
    depths: list[int]
    signed: list[bool]
    dx: list[int]
    dy: list[int]

    @property
    def components(self) -> int:
        return len(self.depths)


@dataclass
class _Cod:
    """Coding style: ``/COD`` or a component's ``/COC`` override."""

    levels: int = 5
    xcb: int = 6
    ycb: int = 6
    style: int = 0  # code-block style bits (SPcod)
    transform: int = 1  # 1 = 5/3 reversible, 0 = 9/7 irreversible
    precinct_sizes: list[int] | None = None
    layers: int = 1
    progression: int = 0
    multiple_transform: bool = False
    sop: bool = False
    eph: bool = False

    def precinct(self, resolution: int) -> tuple[int, int]:
        """``(PPx, PPy)`` exponents for *resolution*."""
        if not self.precinct_sizes:
            return 15, 15
        index = min(resolution, len(self.precinct_sizes) - 1)
        value = self.precinct_sizes[index]
        return value & 0x0F, (value >> 4) & 0x0F


@dataclass
class _Qcd:
    style: int = 0  # 0 = no quantisation, 1 = scalar derived, 2 = scalar expounded
    guard_bits: int = 2
    exponents: list[int] = field(default_factory=list)
    mantissas: list[int] = field(default_factory=list)


@dataclass
class _CodeBlock:
    x0: int
    y0: int
    x1: int
    y1: int
    cbx: int
    cby: int
    included: bool = False
    lblock: int = 3
    zero_bitplanes: int = 0
    passes: int = 0
    data: list[bytes] = field(default_factory=list)


@dataclass
class _Precinct:
    index: int
    inclusion: TagTree
    imsb: TagTree
    blocks: list[_CodeBlock]


@dataclass
class _Subband:
    kind: int  # 0 = LL, 1 = HL, 2 = LH, 3 = HH
    level: int  # decomposition level nb
    x0: int
    y0: int
    x1: int
    y1: int
    precincts: dict[int, _Precinct] = field(default_factory=dict)

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0


@dataclass
class _Resolution:
    index: int
    x0: int
    y0: int
    x1: int
    y1: int
    ppx: int
    ppy: int
    precincts_wide: int
    precincts_high: int
    subbands: list[_Subband]

    @property
    def precinct_count(self) -> int:
        return self.precincts_wide * self.precincts_high


@dataclass
class _TileComponent:
    index: int
    x0: int
    y0: int
    x1: int
    y1: int
    cod: _Cod
    qcd: _Qcd
    resolutions: list[_Resolution]


@dataclass
class _Tile:
    index: int
    x0: int
    y0: int
    x1: int
    y1: int
    components: list[_TileComponent]
    parts: list[bytes] = field(default_factory=list)


@dataclass
class DecodedImage:
    """A decoded JPEG 2000 image as interleaved 8-bit samples."""

    width: int
    height: int
    components: int
    samples: bytes

    @property
    def mode(self) -> str:
        return {1: "L", 3: "RGB", 4: "CMYK"}.get(self.components, "RGB")


# ---------------------------------------------------------------------------
# Tier-1: EBCOT code-block decoding (Annex D)
# ---------------------------------------------------------------------------
def _zero_coding_tables() -> tuple[list[int], list[int], list[int]]:
    """Zero-coding contexts (Table D.1), one table per band orientation.

    Each table is indexed by ``(h * 3 + v) * 5 + d`` with the neighbour sums
    clamped, which turns the standard's decision tree into a lookup.
    """
    ll_lh = [0] * 45
    hl = [0] * 45
    hh = [0] * 45
    for h in range(3):
        for v in range(3):
            for d in range(5):
                index = (h * 3 + v) * 5 + d
                # LL and LH: horizontal neighbours dominate.
                if h == 2:
                    ll_lh[index] = 8
                elif h == 1:
                    ll_lh[index] = 7 if v >= 1 else (6 if d >= 1 else 5)
                elif v == 2:
                    ll_lh[index] = 4
                elif v == 1:
                    ll_lh[index] = 3
                else:
                    ll_lh[index] = 2 if d >= 2 else d
                # HL: the same table with the axes swapped.
                if v == 2:
                    hl[index] = 8
                elif v == 1:
                    hl[index] = 7 if h >= 1 else (6 if d >= 1 else 5)
                elif h == 2:
                    hl[index] = 4
                elif h == 1:
                    hl[index] = 3
                else:
                    hl[index] = 2 if d >= 2 else d
                # HH: diagonals dominate.
                hv = h + v
                if d >= 3:
                    hh[index] = 8
                elif d == 2:
                    hh[index] = 7 if hv >= 1 else 6
                elif d == 1:
                    hh[index] = 5 if hv >= 2 else (4 if hv == 1 else 3)
                else:
                    hh[index] = 2 if hv >= 2 else hv
    return ll_lh, hl, hh


_ZC_LL_LH, _ZC_HL, _ZC_HH = _zero_coding_tables()

# Sign coding (Table D.3): (horizontal, vertical) contribution -> (context, xor).
_SC_TABLE = {
    (1, 1): (13, 0), (1, 0): (12, 0), (1, -1): (11, 0),
    (0, 1): (10, 0), (0, 0): (9, 0), (0, -1): (10, 1),
    (-1, 1): (11, 1), (-1, 0): (12, 1), (-1, -1): (13, 1),
}


class _RawReader:
    """Raw (bypass) bit reader for the lazy coding passes of D.6."""

    __slots__ = ("_bits", "_buffer", "_data", "_last", "pos")

    def __init__(self, data: bytes, pos: int = 0) -> None:
        self._data = data
        self.pos = pos
        self._buffer = 0
        self._bits = 0
        self._last = 0

    def bit(self) -> int:
        if self._bits == 0:
            byte = self._data[self.pos] if self.pos < len(self._data) else 0xFF
            self.pos += 1
            if self._last == 0xFF:
                self._buffer = byte & 0x7F
                self._bits = 7
            else:
                self._buffer = byte
                self._bits = 8
            self._last = byte
        self._bits -= 1
        return (self._buffer >> self._bits) & 1


class _BlockState:
    """Coefficient state for one code-block during the three coding passes."""

    __slots__ = ("first_refine", "height", "magnitude", "sig", "sign", "visited", "width")

    def __init__(self, width: int, height: int) -> None:
        size = width * height
        self.width = width
        self.height = height
        self.sig = bytearray(size)
        self.visited = bytearray(size)
        self.first_refine = bytearray(size)
        self.sign = bytearray(size)
        self.magnitude = [0] * size


def _neighbour_sums(state: _BlockState, x: int, y: int) -> tuple[int, int, int]:
    """Return ``(horizontal, vertical, diagonal)`` significant-neighbour counts."""
    sig = state.sig
    width, height = state.width, state.height
    index = y * width + x
    h = 0
    if x > 0:
        h += sig[index - 1]
    if x + 1 < width:
        h += sig[index + 1]
    v = 0
    if y > 0:
        v += sig[index - width]
    if y + 1 < height:
        v += sig[index + width]
    d = 0
    if x > 0 and y > 0:
        d += sig[index - width - 1]
    if x + 1 < width and y > 0:
        d += sig[index - width + 1]
    if x > 0 and y + 1 < height:
        d += sig[index + width - 1]
    if x + 1 < width and y + 1 < height:
        d += sig[index + width + 1]
    return h, v, d


def _zero_context(state: _BlockState, x: int, y: int, table: list[int]) -> int:
    h, v, d = _neighbour_sums(state, x, y)
    return table[(min(h, 2) * 3 + min(v, 2)) * 5 + min(d, 4)]


def _sign_context(state: _BlockState, x: int, y: int) -> tuple[int, int]:
    sig, sign = state.sig, state.sign
    width, height = state.width, state.height
    index = y * width + x
    horizontal = 0
    if x > 0 and sig[index - 1]:
        horizontal += -1 if sign[index - 1] else 1
    if x + 1 < width and sig[index + 1]:
        horizontal += -1 if sign[index + 1] else 1
    vertical = 0
    if y > 0 and sig[index - width]:
        vertical += -1 if sign[index - width] else 1
    if y + 1 < height and sig[index + width]:
        vertical += -1 if sign[index + width] else 1
    return _SC_TABLE[(max(-1, min(1, horizontal)), max(-1, min(1, vertical)))]


def _decode_codeblock(
    block: _CodeBlock,
    band_kind: int,
    style: int,
    max_bitplanes: int,
) -> tuple[list[int], list[int]]:
    """Decode one code-block into ``(magnitudes, signs)`` in raster order.

    ``style`` carries the SPcod code-block style bits: selective arithmetic
    bypass (0x01), context reset (0x02), termination on each pass (0x04),
    vertically causal context (0x08), predictable termination (0x10) and
    segmentation symbols (0x20).
    """
    width = block.x1 - block.x0
    height = block.y1 - block.y0
    state = _BlockState(width, height)
    if width <= 0 or height <= 0 or not block.data:
        return state.magnitude, list(state.sign)

    table = _ZC_HH if band_kind == 3 else (_ZC_HL if band_kind == 1 else _ZC_LL_LH)
    bypass = bool(style & 0x01)
    reset_contexts = bool(style & 0x02)
    terminate_all = bool(style & 0x04)
    vertically_causal = bool(style & 0x08)
    segmentation = bool(style & 0x20)

    segments = block.data
    # Without per-pass or bypass termination the whole block is one segment.
    stream = b"".join(segments) if not (terminate_all or bypass) else None
    decoder = MQDecoder(stream if stream is not None else segments[0])
    raw: _RawReader | None = None
    segment_index = 0

    bitplanes = max_bitplanes - block.zero_bitplanes
    if bitplanes <= 0:
        return state.magnitude, list(state.sign)

    passes = block.passes
    pass_index = 0
    plane = bitplanes - 1
    kind = 2  # the first pass of a block is always a cleanup pass

    def next_segment() -> None:
        nonlocal segment_index, decoder, raw
        segment_index += 1
        if segment_index < len(segments):
            decoder = MQDecoder(segments[segment_index])
            raw = _RawReader(segments[segment_index])

    while pass_index < passes and plane >= 0:
        use_raw = bypass and pass_index >= 10 and kind in (0, 1)
        if use_raw and raw is None:
            raw = _RawReader(segments[min(segment_index, len(segments) - 1)])
        if kind == 0:
            _significance_pass(
                state, decoder, raw if use_raw else None, table, plane,
                vertically_causal,
            )
        elif kind == 1:
            _refinement_pass(
                state, decoder, raw if use_raw else None, plane, vertically_causal
            )
        else:
            _cleanup_pass(
                state, decoder, table, plane, vertically_causal, segmentation
            )
            state.visited = bytearray(len(state.visited))
        pass_index += 1
        if reset_contexts:
            decoder.reset_states()
        if terminate_all or (bypass and pass_index >= 10 and kind == 2):
            next_segment()
        elif bypass and pass_index >= 10 and kind == 1:
            next_segment()
        if kind == 2:
            plane -= 1
            kind = 0
        else:
            kind += 1
    if plane >= 0:
        # Decoding stopped above the least significant plane, so a significant
        # coefficient is only known to lie somewhere in an interval; putting it
        # in the middle of that interval rather than at its floor is what keeps
        # a truncated (lossy) image close to what the encoder meant (E.1.1.2).
        # How wide the interval is depends on where decoding stopped: after a
        # cleanup pass the whole plane above is finished, while stopping
        # part-way through a plane leaves half as much uncertainty.
        half = 1 << plane if kind == 0 else 1 << max(plane - 1, 0)
        magnitude = state.magnitude
        for index, value in enumerate(magnitude):
            if value:
                magnitude[index] = value + half
    return state.magnitude, list(state.sign)


def _stripe_rows(height: int):
    """Yield ``(stripe_start, stripe_end)`` for the four-row stripes of D.2."""
    for start in range(0, height, 4):
        yield start, min(start + 4, height)


def _significance_pass(
    state: _BlockState,
    decoder: MQDecoder,
    raw: _RawReader | None,
    table: list[int],
    plane: int,
    vertically_causal: bool,
) -> None:
    """Significance propagation pass (D.3.1)."""
    width = state.width
    sig, visited, magnitude, sign = state.sig, state.visited, state.magnitude, state.sign
    bit_value = 1 << plane
    for stripe_start, stripe_end in _stripe_rows(state.height):
        for x in range(width):
            for y in range(stripe_start, stripe_end):
                index = y * width + x
                if sig[index]:
                    continue
                h, v, d = _neighbour_sums(state, x, y)
                if vertically_causal and y == stripe_end - 1:
                    pass
                if h + v + d == 0:
                    continue
                if raw is not None:
                    bit = raw.bit()
                else:
                    context = table[
                        (min(h, 2) * 3 + min(v, 2)) * 5 + min(d, 4)
                    ]
                    bit = decoder.decode(context)
                visited[index] = 1
                if not bit:
                    continue
                if raw is not None:
                    sign[index] = raw.bit()
                else:
                    context, xor = _sign_context(state, x, y)
                    sign[index] = decoder.decode(context) ^ xor
                sig[index] = 1
                magnitude[index] |= bit_value


def _refinement_pass(
    state: _BlockState,
    decoder: MQDecoder,
    raw: _RawReader | None,
    plane: int,
    vertically_causal: bool,
) -> None:
    """Magnitude refinement pass (D.3.3)."""
    width = state.width
    sig, visited, magnitude = state.sig, state.visited, state.magnitude
    first_refine = state.first_refine
    bit_value = 1 << plane
    for stripe_start, stripe_end in _stripe_rows(state.height):
        for x in range(width):
            for y in range(stripe_start, stripe_end):
                index = y * width + x
                if not sig[index] or visited[index]:
                    continue
                if raw is not None:
                    bit = raw.bit()
                elif first_refine[index]:
                    bit = decoder.decode(16)
                else:
                    h, v, d = _neighbour_sums(state, x, y)
                    bit = decoder.decode(15 if h + v + d else 14)
                first_refine[index] = 1
                visited[index] = 1
                if bit:
                    magnitude[index] |= bit_value


def _cleanup_pass(
    state: _BlockState,
    decoder: MQDecoder,
    table: list[int],
    plane: int,
    vertically_causal: bool,
    segmentation: bool,
) -> None:
    """Cleanup pass (D.3.4), including the run-length mode."""
    width, height = state.width, state.height
    sig, visited, magnitude, sign = state.sig, state.visited, state.magnitude, state.sign
    bit_value = 1 << plane
    for stripe_start, stripe_end in _stripe_rows(height):
        full_stripe = stripe_end - stripe_start == 4
        for x in range(width):
            y = stripe_start
            while y < stripe_end:
                # Run-length mode: a full stripe column with nothing significant
                # and no neighbours, coded as one symbol.
                if (
                    full_stripe
                    and y == stripe_start
                    and all(
                        not sig[(row * width) + x]
                        and not visited[(row * width) + x]
                        and sum(_neighbour_sums(state, x, row)) == 0
                        for row in range(stripe_start, stripe_end)
                    )
                ):
                    if not decoder.decode(_RUNLENGTH_CTX):
                        for row in range(stripe_start, stripe_end):
                            visited[row * width + x] = 0
                        y = stripe_end
                        continue
                    offset = (decoder.decode(_UNIFORM_CTX) << 1) | decoder.decode(
                        _UNIFORM_CTX
                    )
                    y = stripe_start + offset
                    index = y * width + x
                    context, xor = _sign_context(state, x, y)
                    sign[index] = decoder.decode(context) ^ xor
                    sig[index] = 1
                    magnitude[index] |= bit_value
                    y += 1
                    continue
                index = y * width + x
                if sig[index] or visited[index]:
                    visited[index] = 0
                    y += 1
                    continue
                context = _zero_context(state, x, y, table)
                if decoder.decode(context):
                    sign_context, xor = _sign_context(state, x, y)
                    sign[index] = decoder.decode(sign_context) ^ xor
                    sig[index] = 1
                    magnitude[index] |= bit_value
                y += 1
    if segmentation:
        symbol = 0
        for _ in range(4):
            symbol = (symbol << 1) | decoder.decode(_UNIFORM_CTX)
        if symbol != 0xA:
            raise Jpeg2000Error("code-block segmentation symbol did not verify")


# ---------------------------------------------------------------------------
# Inverse discrete wavelet transform (Annex F)
# ---------------------------------------------------------------------------
_ALPHA = -1.586134342059924
_BETA = -0.052980118572961
_GAMMA = 0.882911075530934
_DELTA = 0.443506852043971
_K = 1.230174104914001


def _extend(values: list[float], i0: int, i1: int) -> tuple[list[float], int]:
    """Symmetrically extend a 1D signal by four samples on each side (F.3.7)."""
    length = i1 - i0
    if length <= 0:
        return [], 0
    pad = 4
    if length == 1:
        return [values[0]] * (2 * pad + 1), pad
    extended = [0.0] * (length + 2 * pad)
    extended[pad : pad + length] = values
    for k in range(1, pad + 1):
        extended[pad - k] = values[_mirror(-k, length)]
        extended[pad + length - 1 + k] = values[_mirror(length - 1 + k, length)]
    return extended, pad


def _mirror(index: int, length: int) -> int:
    period = 2 * (length - 1) if length > 1 else 1
    index = abs(index) % period
    return index if index < length else period - index


def _inverse_1d(low: list, high: list, i0: int, reversible: bool) -> list:
    """Synthesise one 1D signal from its low- and high-pass halves."""
    total = len(low) + len(high)
    if total == 0:
        return []
    if total == 1:
        # A single sample: an odd-length start keeps the high-pass halved.
        return list(low) if i0 % 2 == 0 else [high[0] / 2 if not reversible else high[0] // 2]

    interleaved = [0.0] * total
    # i0 even means the first sample is low-pass (F.3.3).
    if i0 % 2 == 0:
        interleaved[0::2] = low
        interleaved[1::2] = high
    else:
        interleaved[1::2] = low
        interleaved[0::2] = high

    extended, pad = _extend(interleaved, i0, i0 + total)
    if reversible:
        return _inverse_53(extended, pad, total, i0)
    return _inverse_97(extended, pad, total, i0)


def _inverse_53(extended: list, pad: int, total: int, i0: int) -> list:
    """5/3 reversible synthesis (F.3.8.2.1)."""
    values = [int(v) for v in extended]
    start = pad - (i0 % 2)
    # Even (low-pass) samples first, then odd, both with integer lifting.
    for n in range(start - 2, start + total + 2, 2):
        if 1 <= n < len(values) - 1:
            values[n] -= (values[n - 1] + values[n + 1] + 2) >> 2
    for n in range(start - 1, start + total + 1, 2):
        if 1 <= n < len(values) - 1:
            values[n] += (values[n - 1] + values[n + 1]) >> 1
    return values[pad : pad + total]


def _inverse_97(extended: list, pad: int, total: int, i0: int) -> list:
    """9/7 irreversible synthesis (F.3.8.2.2)."""
    values = list(extended)
    start = pad - (i0 % 2)
    limit = len(values)
    # Every step runs over the *whole* extended signal, padding included: the
    # symmetric extension only reproduces the correct boundary behaviour when
    # the mirrored samples go through the same lifting as the real ones. Anchor
    # each parity at the start of the array rather than at the first real
    # sample, or the outermost columns come out visibly wrong.
    low = start % 2
    high = (start + 1) % 2
    for n in range(low, limit, 2):
        values[n] *= _K
    for n in range(high, limit, 2):
        values[n] *= 1.0 / _K
    # A lifting step needs both neighbours, so it starts at the first index of
    # its own parity that has one -- stepping to 1 would silently swap the
    # parities and interleave the two halves wrongly.
    low_lift = low if low >= 1 else low + 2
    high_lift = high if high >= 1 else high + 2
    for n in range(low_lift, limit - 1, 2):
        values[n] -= _DELTA * (values[n - 1] + values[n + 1])
    for n in range(high_lift, limit - 1, 2):
        values[n] -= _GAMMA * (values[n - 1] + values[n + 1])
    for n in range(low_lift, limit - 1, 2):
        values[n] -= _BETA * (values[n - 1] + values[n + 1])
    for n in range(high_lift, limit - 1, 2):
        values[n] -= _ALPHA * (values[n - 1] + values[n + 1])
    return values[pad : pad + total]


def _inverse_dwt(
    ll: list,
    ll_box: tuple[int, int, int, int],
    levels: list[tuple[dict[int, list], tuple[int, int, int, int]]],
    reversible: bool,
) -> tuple[list, tuple[int, int, int, int]]:
    """Rebuild a tile-component from its LL band and each resolution's HL/LH/HH.

    *levels* is ordered from the coarsest resolution up; each entry carries the
    three high-pass bands and the coordinates of the resolution they produce.
    """
    current = ll
    box = ll_box
    for bands, target in levels:
        current = _synthesise_level(current, box, bands, target, reversible)
        box = target
    return current, box


def _synthesise_level(
    ll: list,
    ll_box: tuple[int, int, int, int],
    bands: dict[int, list],
    target: tuple[int, int, int, int],
    reversible: bool,
) -> list:
    """One 2D synthesis step: LL + HL/LH/HH -> the next resolution (F.3.4)."""
    u0, v0, u1, v1 = target
    width = u1 - u0
    height = v1 - v0
    if width <= 0 or height <= 0:
        return []

    hl, hl_box = bands.get(1, ([], (0, 0, 0, 0)))
    lh, lh_box = bands.get(2, ([], (0, 0, 0, 0)))
    hh, hh_box = bands.get(3, ([], (0, 0, 0, 0)))

    # Columns first: interleave LL with HL horizontally, LH with HH.
    low_rows = _hstack(ll, ll_box, hl, hl_box, u0, u1, reversible)
    high_rows = _hstack(lh, lh_box, hh, hh_box, u0, u1, reversible)

    result = [0] * (width * height) if reversible else [0.0] * (width * height)
    low_height = len(low_rows)
    high_height = len(high_rows)
    for x in range(width):
        column_low = [low_rows[r][x] for r in range(low_height)]
        column_high = [high_rows[r][x] for r in range(high_height)]
        column = _inverse_1d(column_low, column_high, v0, reversible)
        for y, value in enumerate(column):
            result[y * width + x] = value
    return result


def _hstack(
    low: list,
    low_box: tuple[int, int, int, int],
    high: list,
    high_box: tuple[int, int, int, int],
    u0: int,
    u1: int,
    reversible: bool,
) -> list[list]:
    """Interleave two bands along rows, yielding one synthesised row per line."""
    low_width = low_box[2] - low_box[0]
    high_width = high_box[2] - high_box[0]
    rows = max(low_box[3] - low_box[1], high_box[3] - high_box[1])
    output: list[list] = []
    for r in range(rows):
        low_row = low[r * low_width : (r + 1) * low_width] if low_width else []
        high_row = high[r * high_width : (r + 1) * high_width] if high_width else []
        output.append(_inverse_1d(list(low_row), list(high_row), u0, reversible))
    return output


# ---------------------------------------------------------------------------
# Codestream parsing
# ---------------------------------------------------------------------------
def _ceil_div(numerator: int, denominator: int) -> int:
    return -((-numerator) // denominator)


def _u16(data: bytes, offset: int) -> int:
    if offset + 2 > len(data):
        raise Jpeg2000Error("codestream ended inside a marker segment")
    return struct.unpack_from(">H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    if offset + 4 > len(data):
        raise Jpeg2000Error("codestream ended inside a marker segment")
    return struct.unpack_from(">I", data, offset)[0]


def extract_codestream(data: bytes) -> bytes:
    """Return the raw codestream, unwrapping a JP2 container when present."""
    if data[:2] == b"\xff\x4f":
        return data
    if data[4:8] != b"jP  ":
        raise Jpeg2000Error("not a JPEG 2000 codestream or JP2 file")
    offset = 0
    while offset + 8 <= len(data):
        length = _u32(data, offset)
        box_type = data[offset + 4 : offset + 8]
        header = 8
        if length == 1:  # extended (64-bit) length
            if offset + 16 > len(data):
                break
            length = struct.unpack_from(">Q", data, offset + 8)[0]
            header = 16
        elif length == 0:  # runs to the end of the file
            length = len(data) - offset
        if length < header:
            raise Jpeg2000Error("malformed JP2 box length")
        if box_type == b"jp2c":
            return data[offset + header : offset + length]
        offset += length
    raise Jpeg2000Error("the JP2 file carries no contiguous codestream box")


@dataclass
class Codestream:
    siz: _Siz
    cod: _Cod
    coc: dict[int, _Cod]
    qcd: _Qcd
    qcc: dict[int, _Qcd]
    tile_parts: dict[int, list[bytes]]
    tile_cod: dict[tuple[int, int], _Cod]
    tile_qcd: dict[tuple[int, int], _Qcd]

    def coding_style(self, tile: int, component: int) -> _Cod:
        return (
            self.tile_cod.get((tile, component))
            or self.tile_cod.get((tile, -1))
            or self.coc.get(component)
            or self.cod
        )

    def quantisation(self, tile: int, component: int) -> _Qcd:
        return (
            self.tile_qcd.get((tile, component))
            or self.tile_qcd.get((tile, -1))
            or self.qcc.get(component)
            or self.qcd
        )


def _parse_cod(segment: bytes, *, with_layers: bool = True) -> _Cod:
    scod = segment[0]
    cod = _Cod()
    cod.sop = bool(scod & 0x02)
    cod.eph = bool(scod & 0x04)
    cod.progression = segment[1]
    cod.layers = _u16(segment, 2)
    cod.multiple_transform = bool(segment[4])
    cod.levels = segment[5]
    cod.xcb = (segment[6] & 0x0F) + 2
    cod.ycb = (segment[7] & 0x0F) + 2
    cod.style = segment[8]
    cod.transform = segment[9]
    if scod & 0x01:
        cod.precinct_sizes = list(segment[10:])
    if not with_layers:
        cod.layers = 1
    return cod


def _parse_coc(segment: bytes, components: int, base: _Cod) -> tuple[int, _Cod]:
    if components < 257:
        component = segment[0]
        offset = 1
    else:
        component = _u16(segment, 0)
        offset = 2
    scoc = segment[offset]
    cod = _Cod(
        levels=segment[offset + 1],
        xcb=(segment[offset + 2] & 0x0F) + 2,
        ycb=(segment[offset + 3] & 0x0F) + 2,
        style=segment[offset + 4],
        transform=segment[offset + 5],
        layers=base.layers,
        progression=base.progression,
        multiple_transform=base.multiple_transform,
        sop=base.sop,
        eph=base.eph,
    )
    if scoc & 0x01:
        cod.precinct_sizes = list(segment[offset + 6 :])
    return component, cod


def _parse_qcd(segment: bytes) -> _Qcd:
    sqcd = segment[0]
    qcd = _Qcd(style=sqcd & 0x1F, guard_bits=(sqcd >> 5) & 0x07)
    body = segment[1:]
    if qcd.style == 0:
        qcd.exponents = [byte >> 3 for byte in body]
        qcd.mantissas = [0] * len(qcd.exponents)
    else:
        for index in range(0, len(body) - 1, 2):
            value = struct.unpack_from(">H", body, index)[0]
            qcd.exponents.append(value >> 11)
            qcd.mantissas.append(value & 0x7FF)
    return qcd


def _parse_qcc(segment: bytes, components: int) -> tuple[int, _Qcd]:
    if components < 257:
        return segment[0], _parse_qcd(segment[1:])
    return _u16(segment, 0), _parse_qcd(segment[2:])


def parse_codestream(data: bytes) -> Codestream:
    """Parse the main header and every tile-part of *data*."""
    if _u16(data, 0) != _SOC:
        raise Jpeg2000Error("codestream does not start with SOC")
    offset = 2
    siz: _Siz | None = None
    cod: _Cod | None = None
    coc: dict[int, _Cod] = {}
    qcd: _Qcd | None = None
    qcc: dict[int, _Qcd] = {}
    tile_parts: dict[int, list[bytes]] = {}
    tile_cod: dict[tuple[int, int], _Cod] = {}
    tile_qcd: dict[tuple[int, int], _Qcd] = {}

    while offset + 2 <= len(data):
        marker = _u16(data, offset)
        offset += 2
        if marker == _EOC:
            break
        if marker in (_SOC,):
            continue
        if marker in _UNSUPPORTED:
            raise Jpeg2000Error(
                f"JPEG 2000 codestream uses {_UNSUPPORTED[marker]}, "
                "which this decoder does not implement"
            )
        length = _u16(data, offset)
        if length < 2:
            raise Jpeg2000Error("marker segment shorter than its length field")
        segment = data[offset + 2 : offset + length]
        if marker == _SIZ:
            siz = _parse_siz(segment)
        elif marker == _COD:
            cod = _parse_cod(segment)
        elif marker == _COC:
            if cod is None or siz is None:
                raise Jpeg2000Error("COC before COD/SIZ")
            component, style = _parse_coc(segment, siz.components, cod)
            coc[component] = style
        elif marker == _QCD:
            qcd = _parse_qcd(segment)
        elif marker == _QCC:
            if siz is None:
                raise Jpeg2000Error("QCC before SIZ")
            component, quant = _parse_qcc(segment, siz.components)
            qcc[component] = quant
        elif marker == _RGN:
            raise Jpeg2000Error(
                "JPEG 2000 codestream uses a region of interest (RGN), "
                "which this decoder does not implement"
            )
        elif marker == _SOT:
            if siz is None or cod is None or qcd is None:
                raise Jpeg2000Error("a tile-part precedes the main header")
            offset = _read_tile_part(
                data, offset, segment, siz, cod, tile_parts, tile_cod, tile_qcd
            )
            continue
        offset += length

    if siz is None or cod is None or qcd is None:
        raise Jpeg2000Error("codestream is missing SIZ, COD or QCD")
    return Codestream(siz, cod, coc, qcd, qcc, tile_parts, tile_cod, tile_qcd)


def _parse_siz(segment: bytes) -> _Siz:
    width = _u32(segment, 2)
    height = _u32(segment, 6)
    x0 = _u32(segment, 10)
    y0 = _u32(segment, 14)
    tile_width = _u32(segment, 18)
    tile_height = _u32(segment, 22)
    tile_x0 = _u32(segment, 26)
    tile_y0 = _u32(segment, 30)
    components = _u16(segment, 34)
    if not 0 < components <= _MAX_COMPONENTS:
        raise Jpeg2000Error(f"unsupported component count {components}")
    depths: list[int] = []
    signed: list[bool] = []
    dx: list[int] = []
    dy: list[int] = []
    for index in range(components):
        base = 36 + index * 3
        ssiz = segment[base]
        depths.append((ssiz & 0x7F) + 1)
        signed.append(bool(ssiz & 0x80))
        dx.append(segment[base + 1])
        dy.append(segment[base + 2])
    if width <= x0 or height <= y0:
        raise Jpeg2000Error("SIZ declares an empty image")
    return _Siz(
        width, height, x0, y0, tile_width, tile_height, tile_x0, tile_y0,
        depths, signed, dx, dy,
    )


def _read_tile_part(
    data: bytes,
    offset: int,
    segment: bytes,
    siz: _Siz,
    cod: _Cod,
    tile_parts: dict[int, list[bytes]],
    tile_cod: dict[tuple[int, int], _Cod],
    tile_qcd: dict[tuple[int, int], _Qcd],
) -> int:
    """Consume one SOT..SOD tile-part; return the offset just past its data."""
    tile_index = _u16(segment, 0)
    psot = _u32(segment, 2)
    start = offset - 2  # the SOT marker itself
    cursor = offset + _u16(data, offset)
    while cursor + 2 <= len(data):
        marker = _u16(data, cursor)
        if marker == _SOD:
            cursor += 2
            break
        if marker in _UNSUPPORTED:
            raise Jpeg2000Error(
                f"JPEG 2000 tile header uses {_UNSUPPORTED[marker]}, "
                "which this decoder does not implement"
            )
        length = _u16(data, cursor + 2)
        body = data[cursor + 4 : cursor + 2 + length]
        if marker == _COD:
            tile_cod[(tile_index, -1)] = _parse_cod(body)
        elif marker == _COC:
            component, style = _parse_coc(body, siz.components, cod)
            tile_cod[(tile_index, component)] = style
        elif marker == _QCD:
            tile_qcd[(tile_index, -1)] = _parse_qcd(body)
        elif marker == _QCC:
            component, quant = _parse_qcc(body, siz.components)
            tile_qcd[(tile_index, component)] = quant
        cursor += 2 + length
    end = start + psot if psot else len(data)
    end = min(end, len(data))
    tile_parts.setdefault(tile_index, []).append(data[cursor:end])
    return end


# ---------------------------------------------------------------------------
# Tile structure (Annex B.5-B.7)
# ---------------------------------------------------------------------------
_BAND_OFFSETS = {0: (0, 0), 1: (1, 0), 2: (0, 1), 3: (1, 1)}
_BAND_GAIN = {0: 0, 1: 1, 2: 1, 3: 2}


def _band_box(
    tcx0: int, tcy0: int, tcx1: int, tcy1: int, nb: int, kind: int
) -> tuple[int, int, int, int]:
    xob, yob = _BAND_OFFSETS[kind]
    denominator = 1 << nb
    offset = (1 << (nb - 1)) if nb > 0 else 0
    return (
        _ceil_div(tcx0 - offset * xob, denominator),
        _ceil_div(tcy0 - offset * yob, denominator),
        _ceil_div(tcx1 - offset * xob, denominator),
        _ceil_div(tcy1 - offset * yob, denominator),
    )


def _build_tile(cs: Codestream, tile_index: int) -> _Tile:
    siz = cs.siz
    tiles_wide = _ceil_div(siz.width - siz.tile_x0, siz.tile_width)
    p = tile_index % tiles_wide
    q = tile_index // tiles_wide
    tx0 = max(siz.tile_x0 + p * siz.tile_width, siz.x0)
    ty0 = max(siz.tile_y0 + q * siz.tile_height, siz.y0)
    tx1 = min(siz.tile_x0 + (p + 1) * siz.tile_width, siz.width)
    ty1 = min(siz.tile_y0 + (q + 1) * siz.tile_height, siz.height)

    components: list[_TileComponent] = []
    for index in range(siz.components):
        cod = cs.coding_style(tile_index, index)
        qcd = cs.quantisation(tile_index, index)
        if cod.levels > _MAX_DECOMPOSITION_LEVELS:
            raise Jpeg2000Error(f"unsupported decomposition depth {cod.levels}")
        tcx0 = _ceil_div(tx0, siz.dx[index])
        tcy0 = _ceil_div(ty0, siz.dy[index])
        tcx1 = _ceil_div(tx1, siz.dx[index])
        tcy1 = _ceil_div(ty1, siz.dy[index])
        resolutions = [
            _build_resolution(cod, r, tcx0, tcy0, tcx1, tcy1)
            for r in range(cod.levels + 1)
        ]
        components.append(
            _TileComponent(index, tcx0, tcy0, tcx1, tcy1, cod, qcd, resolutions)
        )
    return _Tile(tile_index, tx0, ty0, tx1, ty1, components)


def _build_resolution(
    cod: _Cod, r: int, tcx0: int, tcy0: int, tcx1: int, tcy1: int
) -> _Resolution:
    shift = cod.levels - r
    rx0 = _ceil_div(tcx0, 1 << shift)
    ry0 = _ceil_div(tcy0, 1 << shift)
    rx1 = _ceil_div(tcx1, 1 << shift)
    ry1 = _ceil_div(tcy1, 1 << shift)
    ppx, ppy = cod.precinct(r)
    wide = 0 if rx1 <= rx0 else _ceil_div(rx1, 1 << ppx) - (rx0 >> ppx)
    high = 0 if ry1 <= ry0 else _ceil_div(ry1, 1 << ppy) - (ry0 >> ppy)

    kinds = [0] if r == 0 else [1, 2, 3]
    nb = cod.levels if r == 0 else cod.levels - r + 1
    # Inside a band the precinct and code-block partitions are halved for
    # every resolution above the lowest (B.7).
    bppx = ppx if r == 0 else ppx - 1
    bppy = ppy if r == 0 else ppy - 1
    xcb = min(cod.xcb, bppx)
    ycb = min(cod.ycb, bppy)

    subbands: list[_Subband] = []
    for kind in kinds:
        bx0, by0, bx1, by1 = _band_box(tcx0, tcy0, tcx1, tcy1, nb, kind)
        band = _Subband(kind, nb, bx0, by0, bx1, by1)
        for pj in range(high):
            for pi in range(wide):
                pbx0 = max(bx0, ((bx0 >> bppx) + pi) << bppx)
                pbx1 = min(bx1, (((bx0 >> bppx) + pi + 1) << bppx))
                pby0 = max(by0, ((by0 >> bppy) + pj) << bppy)
                pby1 = min(by1, (((by0 >> bppy) + pj + 1) << bppy))
                blocks: list[_CodeBlock] = []
                if pbx1 > pbx0 and pby1 > pby0:
                    first_cx = pbx0 >> xcb
                    first_cy = pby0 >> ycb
                    for cy in range(first_cy, ((pby1 - 1) >> ycb) + 1):
                        for cx in range(first_cx, ((pbx1 - 1) >> xcb) + 1):
                            blocks.append(
                                _CodeBlock(
                                    x0=max(pbx0, cx << xcb),
                                    y0=max(pby0, cy << ycb),
                                    x1=min(pbx1, (cx + 1) << xcb),
                                    y1=min(pby1, (cy + 1) << ycb),
                                    cbx=cx - first_cx,
                                    cby=cy - first_cy,
                                )
                            )
                    wide_cb = ((pbx1 - 1) >> xcb) - first_cx + 1
                    high_cb = ((pby1 - 1) >> ycb) - first_cy + 1
                else:
                    wide_cb = high_cb = 0
                band.precincts[pj * wide + pi] = _Precinct(
                    index=pj * wide + pi,
                    inclusion=TagTree(wide_cb, high_cb),
                    imsb=TagTree(wide_cb, high_cb),
                    blocks=blocks,
                )
        subbands.append(band)
    return _Resolution(r, rx0, ry0, rx1, ry1, ppx, ppy, wide, high, subbands)


# ---------------------------------------------------------------------------
# Tier-2: packet sequencing and headers (Annex B.9-B.12)
# ---------------------------------------------------------------------------
def _packet_sequence(tile: _Tile, cod: _Cod) -> list[tuple[int, int, int, int]]:
    """Every ``(layer, resolution, component, precinct)`` in progression order."""
    layers = cod.layers
    entries: list[tuple[int, int, int, int]] = []
    for component in tile.components:
        for r, resolution in enumerate(component.resolutions):
            for precinct in range(resolution.precinct_count):
                for layer in range(layers):
                    entries.append((layer, r, component.index, precinct))

    order = cod.progression
    if order == 0:  # LRCP
        key = lambda e: (e[0], e[1], e[2], e[3])  # noqa: E731
    elif order == 1:  # RLCP
        key = lambda e: (e[1], e[0], e[2], e[3])  # noqa: E731
    else:
        positions = _precinct_positions(tile)
        if order == 2:  # RPCL
            key = lambda e: (e[1], positions[(e[2], e[1], e[3])], e[2], e[0])  # noqa: E731
        elif order == 3:  # PCRL
            key = lambda e: (positions[(e[2], e[1], e[3])], e[2], e[1], e[0])  # noqa: E731
        elif order == 4:  # CPRL
            key = lambda e: (e[2], positions[(e[2], e[1], e[3])], e[1], e[0])  # noqa: E731
        else:
            raise Jpeg2000Error(f"unsupported progression order {order}")
    entries.sort(key=key)
    return entries


def _precinct_positions(tile: _Tile) -> dict[tuple[int, int, int], tuple[int, int]]:
    """Map each precinct to its ``(y, x)`` origin on the reference grid.

    The position-driven progressions (RPCL, PCRL, CPRL) order packets by where
    a precinct sits in the image, not by its index, so the origins have to be
    compared across components with different subsampling.
    """
    positions: dict[tuple[int, int, int], tuple[int, int]] = {}
    for component in tile.components:
        levels = component.cod.levels
        for r, resolution in enumerate(component.resolutions):
            scale = 1 << (levels - r)
            for pj in range(resolution.precincts_high):
                for pi in range(resolution.precincts_wide):
                    px = (((resolution.x0 >> resolution.ppx) + pi) << resolution.ppx)
                    py = (((resolution.y0 >> resolution.ppy) + pj) << resolution.ppy)
                    index = pj * resolution.precincts_wide + pi
                    positions[(component.index, r, index)] = (py * scale, px * scale)
    return positions


def _read_pass_count(reader: _HeaderReader) -> int:
    """Decode the number of coding passes in this packet (Table B.4)."""
    if not reader.bit():
        return 1
    if not reader.bit():
        return 2
    value = reader.bits(2)
    if value < 3:
        return 3 + value
    value = reader.bits(5)
    if value < 31:
        return 6 + value
    return 37 + reader.bits(7)


def _segment_lengths(
    reader: _HeaderReader, block: _CodeBlock, passes: int, style: int
) -> list[tuple[int, int]]:
    """Return ``(passes, length)`` per codeword segment for this packet.

    A code-block is normally one segment, but "terminate on each coding pass"
    ends every pass and selective arithmetic bypass ends the raw passes and the
    cleanup pass separately (D.6), each with its own length field.
    """
    terminate_all = bool(style & 0x04)
    bypass = bool(style & 0x01)
    if terminate_all:
        groups = [1] * passes
    elif bypass:
        groups = []
        remaining = passes
        index = block.passes
        while remaining:
            if index < 9:
                take = min(remaining, 9 - index)
            elif (index - 9) % 3 == 0:
                take = min(remaining, 2)  # the two raw passes
            else:
                take = min(remaining, 1)  # the cleanup pass
            groups.append(take)
            remaining -= take
            index += take
    else:
        groups = [passes]

    lengths: list[tuple[int, int]] = []
    for group in groups:
        while reader.bit():
            block.lblock += 1
        bits = block.lblock + group.bit_length() - 1
        lengths.append((group, reader.bits(bits)))
    return lengths


def _decode_packets(tile: _Tile, cod: _Cod, data: bytes) -> None:
    """Walk every packet of *data*, filling each code-block's codeword segments."""
    pos = 0
    for layer, r, component_index, precinct_index in _packet_sequence(tile, cod):
        component = tile.components[component_index]
        if r >= len(component.resolutions):
            continue
        resolution = component.resolutions[r]
        if precinct_index >= resolution.precinct_count:
            continue
        if pos >= len(data):
            return
        pos = _decode_packet(
            data, pos, resolution, precinct_index, layer, component.cod
        )


def _decode_packet(
    data: bytes,
    pos: int,
    resolution: _Resolution,
    precinct_index: int,
    layer: int,
    cod: _Cod,
) -> int:
    if cod.sop and data[pos : pos + 2] == b"\xff\x91":
        pos += 6
    reader = _HeaderReader(data, pos)
    pending: list[tuple[_CodeBlock, int, int]] = []
    if reader.bit():
        for band in resolution.subbands:
            precinct = band.precincts.get(precinct_index)
            if precinct is None:
                continue
            for block in precinct.blocks:
                if block.included:
                    included = bool(reader.bit())
                else:
                    value = precinct.inclusion.decode(
                        reader, block.cbx, block.cby, layer + 1
                    )
                    included = value <= layer
                if not included:
                    continue
                if not block.included:
                    block.included = True
                    threshold = 1
                    while (
                        precinct.imsb.decode(
                            reader, block.cbx, block.cby, threshold
                        )
                        >= threshold
                    ):
                        threshold += 1
                    block.zero_bitplanes = threshold - 1
                passes = _read_pass_count(reader)
                if passes > _MAX_CODING_PASSES:
                    raise Jpeg2000Error("a code-block declares too many passes")
                for group, length in _segment_lengths(
                    reader, block, passes, cod.style
                ):
                    pending.append((block, group, length))
    reader.align()
    pos = reader.pos
    if cod.eph and data[pos : pos + 2] == b"\xff\x92":
        pos += 2
    for block, group, length in pending:
        if pos + length > len(data):
            raise Jpeg2000Error("a codeword segment runs past the tile data")
        block.data.append(data[pos : pos + length])
        block.passes += group
        pos += length
    return pos


# ---------------------------------------------------------------------------
# Dequantisation and tile reconstruction (Annex E)
# ---------------------------------------------------------------------------
def _band_quantisation(
    qcd: _Qcd, levels: int, kind: int, nb: int, depth: int, reversible: bool
) -> tuple[int, float]:
    """Return ``(bitplanes, step)`` for one subband.

    ``bitplanes`` is Mb (E.1), the number of magnitude bits the encoder could
    have coded; ``step`` is the quantisation step, 1 for the reversible path
    where the coefficients are already integers.
    """
    if kind == 0:
        index = 0
    else:
        r = levels - nb + 1
        index = 3 * (r - 1) + kind
    if qcd.style == 1 and qcd.exponents:  # scalar derived: one value for all
        exponent = qcd.exponents[0] - levels + nb
        mantissa = qcd.mantissas[0]
    else:
        if index >= len(qcd.exponents):
            index = len(qcd.exponents) - 1
        if index < 0:
            raise Jpeg2000Error("quantisation table is empty")
        exponent = qcd.exponents[index]
        mantissa = qcd.mantissas[index] if index < len(qcd.mantissas) else 0
    bitplanes = qcd.guard_bits + exponent - 1
    if reversible:
        return max(1, bitplanes), 1.0
    gain = _BAND_GAIN[kind]
    step = (2.0 ** (depth + gain - exponent)) * (1.0 + mantissa / 2048.0)
    return max(1, bitplanes), step


def _band_coefficients(
    band: _Subband,
    style: int,
    bitplanes: int,
    step: float,
    reversible: bool,
) -> list:
    """Run tier-1 over every code-block and place the coefficients in the band."""
    width = band.width
    height = band.height
    if width <= 0 or height <= 0:
        return []
    coefficients: list = [0] * (width * height) if reversible else [0.0] * (
        width * height
    )
    for precinct in band.precincts.values():
        for block in precinct.blocks:
            if not block.data:
                continue
            magnitudes, signs = _decode_codeblock(block, band.kind, style, bitplanes)
            block_width = block.x1 - block.x0
            for y in range(block.y1 - block.y0):
                row = (block.y0 - band.y0 + y) * width + (block.x0 - band.x0)
                base = y * block_width
                for x in range(block_width):
                    magnitude = magnitudes[base + x]
                    if not magnitude:
                        continue
                    value = -magnitude if signs[base + x] else magnitude
                    coefficients[row + x] = value if reversible else value * step
    return coefficients


def _reconstruct_component(component: _TileComponent, depth: int) -> list:
    """Tier-1 + dequantise + inverse DWT for one tile-component."""
    cod = component.cod
    reversible = cod.transform == 1
    resolutions = component.resolutions

    ll_band = resolutions[0].subbands[0]
    bitplanes, step = _band_quantisation(
        component.qcd, cod.levels, 0, ll_band.level, depth, reversible
    )
    current = _band_coefficients(ll_band, cod.style, bitplanes, step, reversible)
    box = (ll_band.x0, ll_band.y0, ll_band.x1, ll_band.y1)

    for r in range(1, len(resolutions)):
        resolution = resolutions[r]
        bands: dict[int, tuple[list, tuple[int, int, int, int]]] = {}
        for band in resolution.subbands:
            band_planes, band_step = _band_quantisation(
                component.qcd, cod.levels, band.kind, band.level, depth, reversible
            )
            bands[band.kind] = (
                _band_coefficients(
                    band, cod.style, band_planes, band_step, reversible
                ),
                (band.x0, band.y0, band.x1, band.y1),
            )
        target = (resolution.x0, resolution.y0, resolution.x1, resolution.y1)
        current = _synthesise_level(current, box, bands, target, reversible)
        box = target
    return current


def _component_transform(
    planes: list[list], cod: _Cod, count: int
) -> list[list]:
    """Undo the RCT or ICT applied across the first three components (G.2, G.3)."""
    if not cod.multiple_transform or count < 3:
        return planes
    y0, y1, y2 = planes[0], planes[1], planes[2]
    size = len(y0)
    red = [0.0] * size
    green = [0.0] * size
    blue = [0.0] * size
    if cod.transform == 1:  # RCT, reversible
        for i in range(size):
            g = int(y0[i]) - ((int(y1[i]) + int(y2[i])) >> 2)
            green[i] = g
            red[i] = int(y2[i]) + g
            blue[i] = int(y1[i]) + g
    else:  # ICT, irreversible
        for i in range(size):
            luma = y0[i]
            cb = y1[i]
            cr = y2[i]
            red[i] = luma + 1.402 * cr
            green[i] = luma - 0.34413 * cb - 0.71414 * cr
            blue[i] = luma + 1.772 * cb
    return [red, green, blue, *planes[3:]]


# ---------------------------------------------------------------------------
# Top level
# ---------------------------------------------------------------------------
def decode(data: bytes, *, limits: PdfLoadLimits | None = None) -> DecodedImage:
    """Decode a JPEG 2000 image to interleaved 8-bit samples.

    Raises :class:`Jpeg2000Error` (a
    :class:`~aspose_pdf.exceptions.PdfValidationException`) for a codestream
    this decoder does not implement, rather than returning an approximation.
    """
    resolved = _coerce_limits(limits)
    codestream = extract_codestream(data)
    cs = parse_codestream(codestream)
    siz = cs.siz

    width = siz.width - siz.x0
    height = siz.height - siz.y0
    _check_limits(width, height, siz.components, resolved)

    tiles_wide = _ceil_div(siz.width - siz.tile_x0, siz.tile_width)
    tiles_high = _ceil_div(siz.height - siz.tile_y0, siz.tile_height)
    count = siz.components
    planes: list[list[float]] = [[0.0] * (width * height) for _ in range(count)]

    for tile_index in range(tiles_wide * tiles_high):
        parts = cs.tile_parts.get(tile_index)
        if not parts:
            continue
        tile = _build_tile(cs, tile_index)
        cod = cs.coding_style(tile_index, 0)
        _decode_packets(tile, cod, b"".join(parts))
        samples = [
            _reconstruct_component(component, siz.depths[component.index])
            for component in tile.components
        ]
        samples = _component_transform(samples, cod, count)
        for component, plane in zip(tile.components, samples):
            _place_component(
                planes[component.index], plane, component, siz, width, height
            )

    return _to_samples(planes, siz, width, height)


def _check_limits(
    width: int, height: int, components: int, limits: PdfLoadLimits
) -> None:
    if width <= 0 or height <= 0:
        raise Jpeg2000Error("JPEG 2000 image has no area")
    pixels = width * height
    if limits.max_image_pixels is not None and pixels > limits.max_image_pixels:
        raise PdfResourceLimitException(
            f"Resource limit exceeded for JPX image pixels: {pixels} exceeds "
            f"max_image_pixels={limits.max_image_pixels}"
        )
    output = pixels * components
    if (
        limits.max_decoded_stream_bytes is not None
        and output > limits.max_decoded_stream_bytes
    ):
        raise PdfResourceLimitException(
            f"Resource limit exceeded for JPX decoded samples: {output} exceeds "
            f"max_decoded_stream_bytes={limits.max_decoded_stream_bytes}"
        )
    # Coefficients are held as Python numbers during synthesis, which is the
    # real working set rather than the byte count of the result.
    work = pixels * components * 8
    if limits.max_codec_work_bytes is not None and work > limits.max_codec_work_bytes:
        raise PdfResourceLimitException(
            f"Resource limit exceeded for JPX decoder working set: {work} exceeds "
            f"max_codec_work_bytes={limits.max_codec_work_bytes}"
        )


def _place_component(
    target: list[float],
    values: list,
    component: _TileComponent,
    siz: _Siz,
    width: int,
    height: int,
) -> None:
    """Copy a tile-component into the full image plane, undoing subsampling."""
    dx = siz.dx[component.index]
    dy = siz.dy[component.index]
    tile_width = component.x1 - component.x0
    if tile_width <= 0 or not values:
        return
    rows = (component.y1 - component.y0)
    for y in range(rows):
        source = y * tile_width
        for x in range(tile_width):
            value = values[source + x]
            # Component coordinates are on a grid scaled by dx/dy; a subsampled
            # component covers a dx x dy block of the reference grid.
            base_x = (component.x0 + x) * dx - siz.x0
            base_y = (component.y0 + y) * dy - siz.y0
            for oy in range(dy):
                py = base_y + oy
                if not 0 <= py < height:
                    continue
                row = py * width
                for ox in range(dx):
                    px = base_x + ox
                    if 0 <= px < width:
                        target[row + px] = value


def _to_samples(
    planes: list[list[float]], siz: _Siz, width: int, height: int
) -> DecodedImage:
    """DC level shift, clamp, scale to 8 bits and interleave."""
    count = len(planes)
    out = bytearray(width * height * count)
    for index, plane in enumerate(planes):
        depth = siz.depths[index]
        shift = 0 if siz.signed[index] else 1 << (depth - 1)
        peak = (1 << depth) - 1
        scale = 255.0 / peak if depth != 8 else 1.0
        for position, value in enumerate(plane):
            sample = value + shift
            if sample < 0:
                sample = 0
            elif sample > peak:
                sample = peak
            if scale != 1.0:
                sample *= scale
            out[position * count + index] = int(sample + 0.5)
    return DecodedImage(width, height, count, bytes(out))
