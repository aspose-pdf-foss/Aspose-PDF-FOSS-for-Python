"""WOFF2 decoder (optional ``brotli`` dependency).

WOFF2 wraps an SFNT font in a single Brotli-compressed block and, for the
``glyf`` / ``loca`` tables, replaces them with a compact *transformed*
representation (variable-length point coordinates, omitted bounding boxes,
split sub-streams). This module reverses both layers, reconstructing a plain
SFNT byte stream so the rest of the font subsystem can treat a ``.woff2`` like
a ``.ttf`` / ``.otf``.

Brotli is **not** in the Python standard library, so it is an optional
dependency (``pip install aspose-pdf-foss-for-python[woff2]``). When it is
unavailable, :func:`decode` returns ``None`` and callers fall back to file-name
metadata -- the rest of the library stays dependency-free.

The transform implementation follows the W3C WOFF2 specification (and matches
fontTools' encoder). Parsing is deliberately defensive: malformed input or a
missing ``brotli`` never raises; :func:`decode` simply returns ``None``.
"""

from __future__ import annotations

import struct

from aspose_pdf.engine.woff import build_sfnt

__all__ = ["decode"]

_WOFF2_SIGNATURE = b"wOF2"
_TTCF_FLAVOR = 0x74746366  # 'ttcf' -- font collections are out of scope.

_HEADER_SIZE = 48
_GLYF_HEADER_SIZE = 36

# The 63 tags addressable by a 6-bit directory flag (index 63 == arbitrary tag).
_KNOWN_TAGS = (
    "cmap", "head", "hhea", "hmtx", "maxp", "name", "OS/2", "post",
    "cvt ", "fpgm", "glyf", "loca", "prep", "CFF ", "VORG", "EBDT",
    "EBLC", "gasp", "hdmx", "kern", "LTSH", "PCLT", "VDMX", "vhea",
    "vmtx", "BASE", "GDEF", "GPOS", "GSUB", "EBSC", "JSTF", "MATH",
    "CBDT", "CBLC", "COLR", "CPAL", "SVG ", "sbix", "acnt", "avar",
    "bdat", "bloc", "bsln", "cvar", "fdsc", "feat", "fmtx", "fvar",
    "gvar", "hsty", "just", "lcar", "mort", "morx", "opbd", "prop",
    "trak", "Zapf", "Silf", "Glat", "Gloc", "Feat", "Sill",
)  # fmt: skip

# Composite-glyph component flags (OpenType ``glyf`` spec).
_ARG_1_AND_2_ARE_WORDS = 0x0001
_WE_HAVE_A_SCALE = 0x0008
_MORE_COMPONENTS = 0x0020
_WE_HAVE_AN_X_AND_Y_SCALE = 0x0040
_WE_HAVE_A_TWO_BY_TWO = 0x0080
_WE_HAVE_INSTRUCTIONS = 0x0100

# Simple-glyph point flags.
_ON_CURVE = 0x01
_X_SHORT = 0x02
_Y_SHORT = 0x04
_X_SAME_OR_POSITIVE = 0x10
_Y_SAME_OR_POSITIVE = 0x20
_OVERLAP_SIMPLE = 0x40

_OVERLAP_SIMPLE_BITMAP = 0x0001  # glyf-transform optionFlags bit 0.


def decode(data: bytes) -> bytes | None:
    """Reconstruct the SFNT font wrapped in WOFF2 *data*.

    Returns the decoded TrueType / OpenType byte stream, or ``None`` when
    *data* is not WOFF2, is malformed, wraps a font collection, or when the
    optional ``brotli`` dependency is not installed.
    """
    if len(data) < _HEADER_SIZE or data[:4] != _WOFF2_SIGNATURE:
        return None
    brotli = _import_brotli()
    if brotli is None:
        return None
    try:
        return _decode(data, brotli)
    except (struct.error, IndexError, ValueError, KeyError):
        return None


def _import_brotli():
    try:
        import brotli  # type: ignore
    except ImportError:
        try:
            import brotlicffi as brotli  # type: ignore
        except ImportError:
            return None
    return brotli


def _decode(data: bytes, brotli) -> bytes | None:
    flavor = struct.unpack_from(">I", data, 4)[0]
    if flavor == _TTCF_FLAVOR:
        return None  # WOFF2 font collections are not supported.
    num_tables = struct.unpack_from(">H", data, 12)[0]
    total_compressed = struct.unpack_from(">I", data, 20)[0]
    if num_tables == 0:
        return None

    entries, pos = _read_directory(data, num_tables)

    block = data[pos : pos + total_compressed]
    try:
        decompressed = brotli.decompress(block)
    except Exception as exc:
        # brotli and brotlicffi raise different error types; normalise so the
        # caller's defensive handler turns any failure into a clean None.
        raise ValueError("brotli decompression failed") from exc

    # Slice each table out of the decompressed stream, in directory order.
    cursor = 0
    raw: dict[str, bytes] = {}
    transformed: dict[str, bool] = {}
    order: list[str] = []
    for tag, is_transformed, in_stream_size in entries:
        chunk = decompressed[cursor : cursor + in_stream_size]
        if len(chunk) != in_stream_size:
            return None
        cursor += in_stream_size
        raw[tag] = chunk
        transformed[tag] = is_transformed
        order.append(tag)

    tables = dict(raw)
    if transformed.get("glyf"):
        glyf_bytes, loca_bytes = _reconstruct_glyf(raw["glyf"])
        tables["glyf"] = glyf_bytes
        tables["loca"] = loca_bytes  # transformed loca is reconstructed here.
        if "loca" not in order:
            order.append("loca")
        if "head" in tables:
            tables["head"] = _force_long_loca(tables["head"])

    return build_sfnt(flavor, [(tag, tables[tag]) for tag in order])


def _read_directory(
    data: bytes, num_tables: int
) -> tuple[list[tuple[str, bool, int]], int]:
    """Parse the WOFF2 table directory; return entries and the post-dir offset."""
    entries: list[tuple[str, bool, int]] = []
    pos = _HEADER_SIZE
    for _ in range(num_tables):
        flags = data[pos]
        pos += 1
        tag_index = flags & 0x3F
        transform_version = flags >> 6
        if tag_index == 0x3F:
            tag = data[pos : pos + 4].decode("latin-1")
            pos += 4
        else:
            tag = _KNOWN_TAGS[tag_index]

        if tag in ("glyf", "loca"):
            is_transformed = transform_version != 3
        else:
            is_transformed = transform_version != 0
            if is_transformed:
                # Only glyf/loca transforms are defined/supported; bail on any
                # other (e.g. a transformed hmtx) so the caller falls back.
                raise ValueError(f"unsupported transform on {tag!r}")

        orig_length, pos = _read_base128(data, pos)
        if is_transformed:
            transform_length, pos = _read_base128(data, pos)
            if tag == "loca" and transform_length != 0:
                raise ValueError("transformed loca must have transformLength 0")
            in_stream_size = transform_length
        else:
            in_stream_size = orig_length
        entries.append((tag, is_transformed, in_stream_size))
    return entries, pos


# ---------------------------------------------------------------------------
# Transformed glyf -> standard glyf + loca
# ---------------------------------------------------------------------------


def _reconstruct_glyf(blob: bytes) -> tuple[bytes, bytes]:
    """Decode a transformed ``glyf`` blob into standard glyf + (long) loca."""
    (
        _version,
        option_flags,
        num_glyphs,
        _index_format,
        n_contour_size,
        n_points_size,
        flag_size,
        glyph_size,
        composite_size,
        bbox_size,
        instruction_size,
    ) = struct.unpack_from(">HHHHLLLLLLL", blob, 0)

    pos = _GLYF_HEADER_SIZE
    streams = {}
    for name, size in (
        ("n_contour", n_contour_size),
        ("n_points", n_points_size),
        ("flag", flag_size),
        ("glyph", glyph_size),
        ("composite", composite_size),
        ("bbox", bbox_size),
        ("instruction", instruction_size),
    ):
        streams[name] = blob[pos : pos + size]
        if len(streams[name]) != size:
            raise ValueError("truncated glyf sub-stream")
        pos += size

    overlap_bitmap = None
    if option_flags & _OVERLAP_SIMPLE_BITMAP:
        overlap_size = (num_glyphs + 7) >> 3
        overlap_bitmap = blob[pos : pos + overlap_size]
        pos += overlap_size

    if len(streams["n_contour"]) != num_glyphs * 2:
        raise ValueError("bad nContour stream")
    n_contours = struct.unpack(f">{num_glyphs}h", streams["n_contour"])

    # The bbox stream begins with a presence bitmap (padded to a 4-byte word).
    bbox_bitmap_size = ((num_glyphs + 31) >> 5) << 2
    bbox_bitmap = streams["bbox"][:bbox_bitmap_size]
    if len(bbox_bitmap) != bbox_bitmap_size:
        raise ValueError("bad bbox bitmap")
    bbox_values = streams["bbox"][bbox_bitmap_size:]

    cur = {"n_points": 0, "flag": 0, "glyph": 0, "composite": 0,
           "bbox": 0, "instruction": 0}  # fmt: skip
    flag_stream = streams["flag"]
    glyph_stream = streams["glyph"]
    composite_stream = streams["composite"]
    instruction_stream = streams["instruction"]

    glyf = bytearray()
    offsets = [0]
    for gid in range(num_glyphs):
        nc = n_contours[gid]
        has_bbox = bool(bbox_bitmap[gid >> 3] & (0x80 >> (gid & 7)))
        if nc == 0:
            glyph_bytes = b""
        elif nc > 0:
            glyph_bytes = _decode_simple_glyph(
                nc, has_bbox, bbox_values, gid, overlap_bitmap,
                streams["n_points"], flag_stream, glyph_stream,
                instruction_stream, cur,
            )
        else:
            if not has_bbox:
                raise ValueError("composite glyph without bbox")
            glyph_bytes = _decode_composite_glyph(
                bbox_values, composite_stream, glyph_stream,
                instruction_stream, cur,
            )
        glyf += glyph_bytes
        if len(glyph_bytes) % 2:
            glyf += b"\x00"  # keep glyph offsets even
        offsets.append(len(glyf))

    loca = b"".join(struct.pack(">I", off) for off in offsets)
    return bytes(glyf), loca


def _decode_simple_glyph(
    num_contours, has_bbox, bbox_values, gid, overlap_bitmap,
    n_points_stream, flag_stream, glyph_stream, instruction_stream, cur,
):  # fmt: skip
    end_pts = []
    end_point = -1
    for _ in range(num_contours):
        n, cur["n_points"] = _read_255ushort(n_points_stream, cur["n_points"])
        end_point += n
        end_pts.append(end_point)
    n_points = end_pts[-1] + 1 if end_pts else 0

    flags = flag_stream[cur["flag"] : cur["flag"] + n_points]
    if len(flags) != n_points:
        raise ValueError("not enough flag data")
    cur["flag"] += n_points

    coords, on_curve, consumed = _decode_triplets(
        flags, glyph_stream, cur["glyph"], n_points
    )
    cur["glyph"] += consumed

    instr_len, cur["glyph"] = _read_255ushort(glyph_stream, cur["glyph"])
    instructions = instruction_stream[
        cur["instruction"] : cur["instruction"] + instr_len
    ]
    if len(instructions) != instr_len:
        raise ValueError("not enough instruction data")
    cur["instruction"] += instr_len

    if has_bbox:
        bbox = struct.unpack_from(">4h", bbox_values, cur["bbox"])
        cur["bbox"] += 8
    else:
        xs = [x for x, _ in coords]
        ys = [y for _, y in coords]
        bbox = (min(xs), min(ys), max(xs), max(ys))

    overlap_first = bool(
        overlap_bitmap is not None
        and overlap_bitmap[gid >> 3] & (0x80 >> (gid & 7))
    )
    return _encode_simple_glyph(
        num_contours, end_pts, instructions, coords, on_curve, bbox, overlap_first
    )


def _decode_composite_glyph(
    bbox_values, composite_stream, glyph_stream, instruction_stream, cur
):
    component_bytes, cur["composite"], has_instructions = _read_components(
        composite_stream, cur["composite"]
    )
    bbox = struct.unpack_from(">4h", bbox_values, cur["bbox"])
    cur["bbox"] += 8

    out = struct.pack(">h", -1) + struct.pack(">4h", *bbox) + component_bytes
    if has_instructions:
        instr_len, cur["glyph"] = _read_255ushort(glyph_stream, cur["glyph"])
        instructions = instruction_stream[
            cur["instruction"] : cur["instruction"] + instr_len
        ]
        if len(instructions) != instr_len:
            raise ValueError("not enough instruction data")
        cur["instruction"] += instr_len
        out += struct.pack(">H", instr_len) + instructions
    return out


def _read_components(buf: bytes, pos: int) -> tuple[bytes, int, bool]:
    """Copy a composite glyph's component records (standard layout) verbatim."""
    start = pos
    has_instructions = False
    while True:
        flags, _glyph_index = struct.unpack_from(">HH", buf, pos)
        pos += 4
        pos += 4 if flags & _ARG_1_AND_2_ARE_WORDS else 2
        if flags & _WE_HAVE_A_SCALE:
            pos += 2
        elif flags & _WE_HAVE_AN_X_AND_Y_SCALE:
            pos += 4
        elif flags & _WE_HAVE_A_TWO_BY_TWO:
            pos += 8
        if flags & _WE_HAVE_INSTRUCTIONS:
            has_instructions = True
        if not flags & _MORE_COMPONENTS:
            break
    if pos > len(buf):
        raise ValueError("truncated composite stream")
    return buf[start:pos], pos, has_instructions


def _decode_triplets(flags, triplets, start, n_points):
    """Decode WOFF2 triplet-encoded point deltas into absolute coordinates."""
    x = y = 0
    coords = []
    on_curve = []
    ti = start
    for i in range(n_points):
        flag = flags[i]
        on = not (flag >> 7)
        flag &= 0x7F
        if flag < 84:
            n_bytes = 1
        elif flag < 120:
            n_bytes = 2
        elif flag < 124:
            n_bytes = 3
        else:
            n_bytes = 4
        if ti + n_bytes > len(triplets):
            raise ValueError("not enough triplet data")
        if flag < 10:
            dx = 0
            dy = _with_sign(flag, ((flag & 14) << 7) + triplets[ti])
        elif flag < 20:
            dx = _with_sign(flag, (((flag - 10) & 14) << 7) + triplets[ti])
            dy = 0
        elif flag < 84:
            b0 = flag - 20
            b1 = triplets[ti]
            dx = _with_sign(flag, 1 + (b0 & 0x30) + (b1 >> 4))
            dy = _with_sign(flag >> 1, 1 + ((b0 & 0x0C) << 2) + (b1 & 0x0F))
        elif flag < 120:
            b0 = flag - 84
            dx = _with_sign(flag, 1 + ((b0 // 12) << 8) + triplets[ti])
            dy = _with_sign(flag >> 1, 1 + (((b0 % 12) >> 2) << 8) + triplets[ti + 1])
        elif flag < 124:
            b2 = triplets[ti + 1]
            dx = _with_sign(flag, (triplets[ti] << 4) + (b2 >> 4))
            dy = _with_sign(flag >> 1, ((b2 & 0x0F) << 8) + triplets[ti + 2])
        else:
            dx = _with_sign(flag, (triplets[ti] << 8) + triplets[ti + 1])
            dy = _with_sign(flag >> 1, (triplets[ti + 2] << 8) + triplets[ti + 3])
        ti += n_bytes
        x += dx
        y += dy
        coords.append((x, y))
        on_curve.append(on)
    return coords, on_curve, ti - start


def _with_sign(flag: int, value: int) -> int:
    return value if flag & 1 else -value


def _encode_simple_glyph(
    num_contours, end_pts, instructions, coords, on_curve, bbox, overlap_first
) -> bytes:
    """Re-encode decoded points as a standard TrueType simple-glyph description."""
    out = bytearray(struct.pack(">h", num_contours))
    out += struct.pack(">4h", *bbox)
    for end in end_pts:
        out += struct.pack(">H", end)
    out += struct.pack(">H", len(instructions))
    out += instructions

    flag_bytes = bytearray()
    xs = bytearray()
    ys = bytearray()
    prev_x = prev_y = 0
    for i, (x, y) in enumerate(coords):
        flag = _ON_CURVE if on_curve[i] else 0
        if overlap_first and i == 0:
            flag |= _OVERLAP_SIMPLE
        dx = x - prev_x
        dy = y - prev_y
        prev_x, prev_y = x, y
        if dx == 0:
            flag |= _X_SAME_OR_POSITIVE
        elif -255 <= dx <= 255:
            flag |= _X_SHORT
            if dx > 0:
                flag |= _X_SAME_OR_POSITIVE
            xs += struct.pack("B", abs(dx))
        else:
            xs += struct.pack(">h", dx)
        if dy == 0:
            flag |= _Y_SAME_OR_POSITIVE
        elif -255 <= dy <= 255:
            flag |= _Y_SHORT
            if dy > 0:
                flag |= _Y_SAME_OR_POSITIVE
            ys += struct.pack("B", abs(dy))
        else:
            ys += struct.pack(">h", dy)
        flag_bytes.append(flag)

    out += flag_bytes
    out += xs
    out += ys
    return bytes(out)


def _force_long_loca(head: bytes) -> bytes:
    """Return *head* with ``indexToLocFormat`` (offset 50) forced to long (1)."""
    if len(head) < 52:
        return head
    return head[:50] + struct.pack(">h", 1) + head[52:]


# ---------------------------------------------------------------------------
# Variable-length integer readers (index-based; never copy the buffer).
# ---------------------------------------------------------------------------


def _read_base128(buf: bytes, pos: int) -> tuple[int, int]:
    if buf[pos] == 0x80:
        raise ValueError("UIntBase128 must not start with 0x80")
    result = 0
    for _ in range(5):
        code = buf[pos]
        pos += 1
        if result & 0xFE000000:
            raise ValueError("UIntBase128 exceeds 2**32-1")
        result = (result << 7) | (code & 0x7F)
        if not code & 0x80:
            return result, pos
    raise ValueError("UIntBase128 longer than 5 bytes")


def _read_255ushort(buf: bytes, pos: int) -> tuple[int, int]:
    code = buf[pos]
    pos += 1
    if code == 253:
        value = struct.unpack_from(">H", buf, pos)[0]
        pos += 2
    elif code == 254:
        value = buf[pos] + 506
        pos += 1
    elif code == 255:
        value = buf[pos] + 253
        pos += 1
    else:
        value = code
    return value, pos
