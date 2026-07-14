"""Dependency-free TrueType (``glyf``) font subsetter.

This module implements *glyph-erasure* (a.k.a. retain-GID) subsetting: the
outlines of glyphs that are not used are dropped from the ``glyf`` table while
the glyph **numbering** (``numGlyphs`` and every glyph id) is left unchanged.
Because glyph ids are preserved, the ``cmap``, ``hmtx``, ``post`` and a font's
``CIDToGIDMap`` stay valid without any rewriting -- only ``glyf`` and ``loca``
shrink. ``glyf`` is by far the largest table in a typical embedded TrueType
program, so this still removes the bulk of the bytes for a lightly-used font.

The subsetter only understands TrueType outlines (``glyf``/``loca``). OpenType
CFF (``CFF ``/``OTTO``) and Type 1 programs use a different outline format and
are reported as unsupported (the caller keeps such fonts whole).

Parsing is deliberately defensive: malformed or unexpected input never raises,
:func:`subset_truetype` simply returns ``None`` so the caller can leave the
original font program untouched.
"""

from __future__ import annotations

import struct

__all__ = ["subset_truetype", "read_symbol_code_to_gid", "read_unicode_cmap"]

# Composite-glyph component flags (see the OpenType ``glyf`` table spec).
_ARG_1_AND_2_ARE_WORDS = 0x0001
_WE_HAVE_A_SCALE = 0x0008
_MORE_COMPONENTS = 0x0020
_WE_HAVE_AN_X_AND_Y_SCALE = 0x0040
_WE_HAVE_A_TWO_BY_TWO = 0x0080

# Magic constant used when deriving ``head.checkSumAdjustment``.
_CHECKSUM_MAGIC = 0xB1B0AFBA
_MAX_CMAP_MAPPINGS = 1_000_000
_MAX_CMAP_SUBTABLES = 256
_MAX_SFNT_TABLES = 4096


def subset_truetype(font_bytes: bytes, keep_gids: set[int]) -> bytes | None:
    """Return a subset of *font_bytes* keeping only *keep_gids* (and glyph 0).

    Outlines for every other glyph are emptied. Composite glyphs in the keep
    set pull their component glyphs in automatically. Returns ``None`` when the
    font is not a parseable TrueType ``glyf`` program, when subsetting is not
    possible, or when the result would not be smaller than the input.
    """
    try:
        return _subset_truetype(font_bytes, keep_gids)
    except (struct.error, IndexError, ValueError):
        return None


def _subset_truetype(font_bytes: bytes, keep_gids: set[int]) -> bytes | None:
    if len(font_bytes) < 12:
        return None

    sfnt_version, num_tables = struct.unpack_from(">IH", font_bytes, 0)
    # OpenType/CFF ('OTTO') has no 'glyf' table; nothing to do here.
    if sfnt_version == 0x4F54544F:  # 'OTTO'
        return None

    tables: dict[str, tuple[int, int]] = {}
    record = 12
    for _ in range(num_tables):
        if record + 16 > len(font_bytes):
            return None
        tag = font_bytes[record : record + 4].decode("latin-1")
        offset, length = struct.unpack_from(">II", font_bytes, record + 8)
        tables[tag] = (offset, length)
        record += 16

    # TrueType outlines require all of these tables.
    if not {"glyf", "loca", "head", "maxp"} <= tables.keys():
        return None

    head_off, head_len = tables["head"]
    maxp_off, _maxp_len = tables["maxp"]
    if head_off + 54 > len(font_bytes) or maxp_off + 6 > len(font_bytes):
        return None

    index_to_loc = struct.unpack_from(">h", font_bytes, head_off + 50)[0]
    num_glyphs = struct.unpack_from(">H", font_bytes, maxp_off + 4)[0]
    if num_glyphs == 0:
        return None

    loca = _read_loca(font_bytes, tables["loca"], num_glyphs, index_to_loc)
    if loca is None:
        return None

    glyf_off, glyf_len = tables["glyf"]
    glyf = font_bytes[glyf_off : glyf_off + glyf_len]

    keep = {g for g in keep_gids if 0 <= g < num_glyphs}
    keep.add(0)  # .notdef must always survive.
    keep = _expand_composites(glyf, loca, keep, num_glyphs)

    # Rebuild glyf as the concatenation of kept glyph descriptions, empty
    # otherwise, with a matching long-format loca.
    new_glyf = bytearray()
    new_loca: list[int] = [0]
    for gid in range(num_glyphs):
        if gid in keep:
            start, end = loca[gid], loca[gid + 1]
            if 0 <= start <= end <= len(glyf):
                new_glyf += glyf[start:end]
        new_loca.append(len(new_glyf))

    new_loca_bytes = b"".join(struct.pack(">I", off) for off in new_loca)

    # Assemble new tables: glyf/loca replaced, head switched to long loca.
    new_tables: dict[str, bytes] = {}
    for tag, (off, length) in tables.items():
        new_tables[tag] = font_bytes[off : off + length]
    new_tables["glyf"] = bytes(new_glyf)
    new_tables["loca"] = new_loca_bytes
    new_tables["head"] = _set_long_loca(new_tables["head"])

    result = _build_sfnt(sfnt_version, new_tables)

    if len(result) >= len(font_bytes):
        return None
    return result


def _read_loca(
    data: bytes, location: tuple[int, int], num_glyphs: int, index_to_loc: int
) -> list[int] | None:
    """Parse the ``loca`` table into ``num_glyphs + 1`` byte offsets."""
    off, _length = location
    count = num_glyphs + 1
    offsets: list[int] = []
    if index_to_loc == 0:  # short format: uint16 entries, scaled by 2
        if off + count * 2 > len(data):
            return None
        for i in range(count):
            offsets.append(struct.unpack_from(">H", data, off + i * 2)[0] * 2)
    else:  # long format: uint32 byte offsets
        if off + count * 4 > len(data):
            return None
        for i in range(count):
            offsets.append(struct.unpack_from(">I", data, off + i * 4)[0])
    return offsets


def _expand_composites(
    glyf: bytes, loca: list[int], keep: set[int], num_glyphs: int
) -> set[int]:
    """Grow *keep* with the components referenced by kept composite glyphs."""
    result = set(keep)
    pending = list(keep)
    while pending:
        gid = pending.pop()
        if not (0 <= gid < num_glyphs):
            continue
        start, end = loca[gid], loca[gid + 1]
        if end <= start or end > len(glyf) or end - start < 10:
            continue
        num_contours = struct.unpack_from(">h", glyf, start)[0]
        if num_contours >= 0:
            continue  # simple glyph: no components
        for comp in _composite_components(glyf, start + 10, end):
            if comp not in result:
                result.add(comp)
                pending.append(comp)
    return result


def _composite_components(glyf: bytes, pos: int, end: int):
    """Yield the component glyph ids of a composite glyph description."""
    while pos + 4 <= end:
        flags, glyph_index = struct.unpack_from(">HH", glyf, pos)
        yield glyph_index
        pos += 4
        pos += 4 if flags & _ARG_1_AND_2_ARE_WORDS else 2
        if flags & _WE_HAVE_A_SCALE:
            pos += 2
        elif flags & _WE_HAVE_AN_X_AND_Y_SCALE:
            pos += 4
        elif flags & _WE_HAVE_A_TWO_BY_TWO:
            pos += 8
        if not flags & _MORE_COMPONENTS:
            break


def _set_long_loca(head: bytes) -> bytes:
    """Return *head* with ``indexToLocFormat`` forced to long (1)."""
    if len(head) < 52:
        return head
    return head[:50] + struct.pack(">h", 1) + head[52:]


def _build_sfnt(sfnt_version: int, table_data: dict[str, bytes]) -> bytes:
    """Reassemble an sfnt from *table_data*, fixing checksums and offsets."""
    tags = sorted(table_data)
    num_tables = len(tags)

    # head's checksum is computed with checkSumAdjustment temporarily zeroed.
    entries: dict[str, tuple[bytes, int]] = {}
    for tag in tags:
        data = table_data[tag]
        if tag == "head" and len(data) >= 12:
            data = data[:8] + b"\x00\x00\x00\x00" + data[12:]
        entries[tag] = (data, _checksum(data))

    # Lay tables out after the header + directory, each 4-byte aligned.
    offset = 12 + 16 * num_tables
    layout: dict[str, int] = {}
    for tag in tags:
        layout[tag] = offset
        offset += _aligned4(len(entries[tag][0]))

    entry_selector = max(num_tables.bit_length() - 1, 0)
    search_range = (1 << entry_selector) * 16
    range_shift = num_tables * 16 - search_range

    out = bytearray()
    out += struct.pack(
        ">IHHHH", sfnt_version, num_tables, search_range, entry_selector, range_shift
    )
    for tag in tags:
        data, checksum = entries[tag]
        out += tag.encode("latin-1")
        out += struct.pack(">III", checksum, layout[tag], len(data))
    for tag in tags:
        data = entries[tag][0]
        out += data
        out += b"\x00" * (_aligned4(len(data)) - len(data))

    _apply_checksum_adjustment(out, layout.get("head"))
    return bytes(out)


def _apply_checksum_adjustment(out: bytearray, head_offset: int | None) -> None:
    """Write ``head.checkSumAdjustment`` so the whole file checksums correctly."""
    if head_offset is None or head_offset + 12 > len(out):
        return
    adjustment = (_CHECKSUM_MAGIC - _checksum(out)) & 0xFFFFFFFF
    struct.pack_into(">I", out, head_offset + 8, adjustment)


def _checksum(data: bytes) -> int:
    """Sum *data* as big-endian uint32 words (zero-padded to a multiple of 4)."""
    if len(data) % 4:
        data = data + b"\x00" * (4 - len(data) % 4)
    total = 0
    for i in range(0, len(data), 4):
        total += struct.unpack_from(">I", data, i)[0]
    return total & 0xFFFFFFFF


def _aligned4(n: int) -> int:
    return (n + 3) & ~3


# ---------------------------------------------------------------------------
# cmap reading (for best-effort subsetting of simple /TrueType fonts)
# ---------------------------------------------------------------------------


def read_symbol_code_to_gid(font_bytes: bytes) -> dict[int, int]:
    """Return a single-byte ``code -> glyph id`` map from a symbol ``cmap``.

    Looks only at the ``(3, 0)`` (Windows Symbol) and ``(1, 0)`` (Mac Roman)
    subtables, which map a font's own byte codes directly to glyph ids -- the
    case where a simple ``/TrueType`` font can be subset without resolving the
    PDF ``/Encoding``. Returns ``{}`` when no usable subtable is present or the
    font cannot be parsed (the caller then leaves the font whole).
    """
    try:
        return _read_symbol_code_to_gid(font_bytes)
    except (struct.error, IndexError, ValueError):
        return {}


def _read_symbol_code_to_gid(font_bytes: bytes) -> dict[int, int]:
    chosen = None  # (priority, subtable_offset, cmap_end)
    for plat, enc, off, cmap_end in _cmap_subtables(font_bytes):
        if plat == 3 and enc == 0:
            priority = 0
        elif plat == 1 and enc == 0:
            priority = 1
        else:
            continue
        if chosen is None or priority < chosen[0]:
            chosen = (priority, off, cmap_end)
    if chosen is None:
        return {}
    return _read_cmap_subtable(font_bytes, chosen[1], table_end=chosen[2])


def read_unicode_cmap(font_bytes: bytes) -> dict[int, int]:
    """Return a ``unicode codepoint -> glyph id`` map from a Unicode ``cmap``.

    Prefers full-repertoire Windows/Unicode subtables (normally format 12),
    then the Windows BMP subtable. Compatible subtables are merged in priority
    order so a BMP-only fallback can fill gaps without replacing mappings from
    a full-repertoire table. Used to subset a simple ``/TrueType`` font whose
    code -> glyph mapping is resolved through the PDF ``/Encoding`` (code ->
    unicode) and then this map (unicode -> glyph id). Returns ``{}`` when none
    is usable.
    """
    try:
        candidates: list[tuple[int, int, int]] = []
        for plat, enc, off, cmap_end in _cmap_subtables(font_bytes):
            if off + 2 > len(font_bytes):
                continue
            fmt = struct.unpack_from(">H", font_bytes, off)[0]
            if plat == 3 and enc == 10 and fmt in (12, 13):
                priority = 0
            elif plat == 0 and fmt in (12, 13):
                priority = 1
            elif plat == 3 and enc == 1:
                priority = 2
            elif plat == 0 and enc in (3, 4, 6):
                priority = 3
            elif plat == 0:
                priority = 4
            else:
                continue
            candidates.append((priority, off, cmap_end))
        mapping: dict[int, int] = {}
        work_budget = [_MAX_CMAP_MAPPINGS]
        for _priority, off, cmap_end in sorted(candidates):
            subtable = _read_cmap_subtable(
                font_bytes,
                off,
                work_budget,
                table_end=cmap_end,
            )
            for codepoint, gid in subtable.items():
                mapping.setdefault(codepoint, gid)
        return mapping
    except (struct.error, IndexError, ValueError):
        return {}


def _cmap_subtables(font_bytes: bytes) -> list[tuple[int, int, int, int]]:
    """Return bounded ``cmap`` encoding records and their table end offset."""
    if len(font_bytes) < 12:
        return []
    num_tables = struct.unpack_from(">H", font_bytes, 4)[0]
    if (
        num_tables == 0
        or num_tables > _MAX_SFNT_TABLES
        or 12 + num_tables * 16 > len(font_bytes)
    ):
        return []
    cmap_off = None
    cmap_length = None
    record = 12
    for _ in range(num_tables):
        if font_bytes[record : record + 4] == b"cmap":
            if cmap_off is not None:
                return []
            cmap_off, cmap_length = struct.unpack_from(">II", font_bytes, record + 8)
        record += 16
    if cmap_off is None or cmap_length is None or cmap_length < 4:
        return []
    if cmap_off > len(font_bytes) or cmap_length > len(font_bytes) - cmap_off:
        return []
    cmap_end = cmap_off + cmap_length
    num_sub = struct.unpack_from(">H", font_bytes, cmap_off + 2)[0]
    directory_size = 4 + num_sub * 8
    available_records = (cmap_length - 4) // 8
    if num_sub > _MAX_CMAP_SUBTABLES or num_sub > available_records:
        return []
    subs: list[tuple[int, int, int, int]] = []
    for i in range(num_sub):
        rec = cmap_off + 4 + i * 8
        plat, enc, sub_off = struct.unpack_from(">HHI", font_bytes, rec)
        absolute_off = cmap_off + sub_off
        if sub_off < directory_size or absolute_off + 2 > cmap_end:
            return []
        subs.append((plat, enc, absolute_off, cmap_end))
    return subs


def _read_cmap_subtable(
    data: bytes,
    off: int,
    work_budget: list[int] | None = None,
    *,
    table_end: int | None = None,
) -> dict[int, int]:
    """Parse a bounded format 0/4/6/12/13 ``cmap`` subtable."""
    data_end = len(data) if table_end is None else table_end
    if data_end < 0 or data_end > len(data) or off < 0 or off + 2 > data_end:
        return {}
    if work_budget is None:
        work_budget = [_MAX_CMAP_MAPPINGS]

    def reserve_work(count: int) -> bool:
        if count < 0 or count > work_budget[0]:
            work_budget[0] = 0
            return False
        work_budget[0] -= count
        return True

    fmt = struct.unpack_from(">H", data, off)[0]
    mapping: dict[int, int] = {}

    if fmt == 0:  # byte encoding table: 256 glyph ids
        if off + 6 > data_end:
            return {}
        length = struct.unpack_from(">H", data, off + 2)[0]
        if length < 262 or off + length > data_end or not reserve_work(256):
            return {}
        for code in range(256):
            gid = data[off + 6 + code]
            if gid:
                mapping[code] = gid
        return mapping

    if fmt == 6:  # trimmed table mapping
        if off + 10 > data_end:
            return {}
        length = struct.unpack_from(">H", data, off + 2)[0]
        first, count = struct.unpack_from(">HH", data, off + 6)
        base = off + 10
        if (
            length < 10 + count * 2
            or off + length > data_end
            or first + count > 0x10000
            or not reserve_work(count)
        ):
            return {}
        for i in range(count):
            gid = struct.unpack_from(">H", data, base + i * 2)[0]
            if gid:
                mapping[first + i] = gid
        return mapping

    if fmt in (12, 13):  # segmented coverage for the full Unicode repertoire
        if off + 16 > data_end:
            return {}
        length = struct.unpack_from(">I", data, off + 4)[0]
        num_groups = struct.unpack_from(">I", data, off + 12)[0]
        if length < 16 or off + length > data_end:
            return {}
        group_base = off + 16
        if num_groups > (length - 16) // 12:
            return {}
        for index in range(num_groups):
            start, end, start_gid = struct.unpack_from(
                ">III", data, group_base + index * 12
            )
            if end < start or end > 0x10FFFF or start_gid > 0xFFFF:
                return {}
            range_size = end - start + 1
            if not reserve_work(range_size):
                return {}
            if fmt == 12 and start_gid + range_size - 1 > 0xFFFF:
                return {}
            for codepoint in range(start, end + 1):
                gid = start_gid if fmt == 13 else start_gid + codepoint - start
                if gid:
                    mapping[codepoint] = gid
        return mapping

    if fmt == 4:  # segment mapping to delta values
        if off + 14 > data_end:
            return {}
        length = struct.unpack_from(">H", data, off + 2)[0]
        if length < 16 or off + length > data_end:
            return {}
        subtable_end = off + length
        seg_x2 = struct.unpack_from(">H", data, off + 6)[0]
        if seg_x2 == 0 or seg_x2 % 2:
            return {}
        seg_count = seg_x2 // 2
        end_base = off + 14
        start_base = end_base + seg_x2 + 2  # skip endCodes + reservedPad
        delta_base = start_base + seg_x2
        range_base = delta_base + seg_x2
        if range_base + seg_x2 > subtable_end:
            return {}
        for s in range(seg_count):
            end_code = struct.unpack_from(">H", data, end_base + s * 2)[0]
            start_code = struct.unpack_from(">H", data, start_base + s * 2)[0]
            id_delta = struct.unpack_from(">h", data, delta_base + s * 2)[0]
            id_range = struct.unpack_from(">H", data, range_base + s * 2)[0]
            if end_code < start_code:
                return {}
            range_size = end_code - start_code + 1
            if not reserve_work(range_size):
                return {}
            glyph_base = range_base + s * 2 + id_range
            if id_range and glyph_base + range_size * 2 > subtable_end:
                return {}
            for code in range(start_code, end_code + 1):
                if code == 0xFFFF:
                    continue
                if id_range == 0:
                    gid = (code + id_delta) & 0xFFFF
                else:
                    gi_off = glyph_base + (code - start_code) * 2
                    gid = struct.unpack_from(">H", data, gi_off)[0]
                    if gid:
                        gid = (gid + id_delta) & 0xFFFF
                if gid:
                    mapping[code] = gid
        return mapping

    return {}
