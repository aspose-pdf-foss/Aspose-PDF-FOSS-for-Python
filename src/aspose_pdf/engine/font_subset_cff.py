"""Dependency-free CFF (``/FontFile3``) glyph-erasure subsetter.

This mirrors :mod:`aspose_pdf.engine.font_subset` (the TrueType subsetter) for
bare CFF (Type 2 charstring) font programs. It implements *glyph-erasure*: the
charstrings of unused glyphs are replaced with a one-byte ``endchar`` while the
glyph **numbering** (the CharStrings INDEX count and every glyph id) is left
unchanged. Because glyph ids are preserved, the ``charset`` and the PDF's
``CIDToGIDMap`` stay valid without rewriting -- only the CharStrings INDEX (by
far the largest part of a lightly-used font) shrinks. Global / local subrs and
every other table are copied verbatim, so the kept glyphs keep working.

Both **name-keyed** and **CID-keyed** CFF (a Top DICT with a ``ROS`` operator --
``/CIDFontType0C``) are handled. A CID-keyed font additionally carries an
``FDArray`` (an INDEX of Font DICTs, each with its own ``Private`` DICT and local
subrs), an ``FDSelect`` mapping each glyph to its Font DICT, and a ``charset``
mapping glyph ids to CIDs; all of these are relocated, and every Font DICT's
``Private`` offset is patched to its moved position. CFF2 (major version 2) is
not handled.

Parsing is deliberately defensive: malformed or unexpected input never raises,
:func:`subset_cff` simply returns ``None`` so the caller can leave the original
program untouched.
"""

from __future__ import annotations

import struct

__all__ = ["subset_cff", "cff_charset_cid_to_gid"]

_ENDCHAR = b"\x0e"  # Type 2 (and Type 1) charstring "endchar" operator.

# Top DICT operators that carry an absolute offset into the CFF (need fixups).
_OP_CHARSET = 15
_OP_ENCODING = 16
_OP_CHARSTRINGS = 17
_OP_PRIVATE = 18
_OP_SUBRS = 19  # inside a Private DICT, relative to the Private DICT start.
_OP_ROS = (12, 30)  # marks a CID-keyed font.
_OP_FDARRAY = (12, 36)  # CID: offset to the Font DICT INDEX.
_OP_FDSELECT = (12, 37)  # CID: offset to the glyph -> FD map.


def subset_cff(font_bytes: bytes, keep_gids: set[int]) -> bytes | None:
    """Return a subset of *font_bytes* keeping only *keep_gids* (and glyph 0).

    Charstrings for every other glyph are emptied. Returns ``None`` when the
    font is not a parseable name-keyed CFF, when it is CID-keyed / CFF2, when
    nothing can be erased, or when the result would not be smaller.
    """
    try:
        return _subset_cff(font_bytes, keep_gids)
    except (struct.error, IndexError, ValueError):
        return None


def _subset_cff(font_bytes: bytes, keep_gids: set[int]) -> bytes | None:
    if len(font_bytes) < 4:
        return None
    major, _minor, hdr_size, _off_size = font_bytes[0:4]
    if major != 1 or hdr_size < 4:
        return None  # CFF2 (major 2) and odd headers are out of scope.

    header = font_bytes[:hdr_size]
    name_index, pos = _read_index(font_bytes, hdr_size)
    topdict_index, pos = _read_index(font_bytes, pos)
    string_index, pos = _read_index(font_bytes, pos)
    gsubr_index, pos = _read_index(font_bytes, pos)
    if len(topdict_index) != 1:
        return None

    entries = _parse_dict(topdict_index[0])
    is_cid = _dict_get(entries, _OP_ROS) is not None

    cs_off = _dict_int(entries, _OP_CHARSTRINGS)
    if cs_off is None:
        return None
    charstrings, _end = _read_index(font_bytes, cs_off)
    num_glyphs = len(charstrings)
    if num_glyphs == 0:
        return None

    keep = {g for g in keep_gids if 0 <= g < num_glyphs}
    keep.add(0)  # .notdef must survive.
    new_charstrings = []
    changed = False
    for gid, charstring in enumerate(charstrings):
        if gid in keep:
            new_charstrings.append(charstring)
        else:
            if charstring != _ENDCHAR:
                changed = True
            new_charstrings.append(_ENDCHAR)
    if not changed:
        return None
    new_cs_index = _build_index(new_charstrings)

    common = (header, name_index, entries, string_index, gsubr_index, num_glyphs)
    if is_cid:
        return _assemble_cid(font_bytes, common, new_cs_index)
    return _assemble_name_keyed(font_bytes, common, new_cs_index)


def _assemble_name_keyed(font_bytes, common, new_cs_index):
    header, name_index, entries, string_index, gsubr_index, num_glyphs = common

    # Trailing blocks that must be relocated, copied verbatim.
    charset_blob = _slice_charset(font_bytes, entries, num_glyphs)
    encoding_blob = _slice_encoding(font_bytes, entries)
    private_blob, private_size = _slice_private(font_bytes, entries)

    relocate = {
        "charset": charset_blob is not None,
        "encoding": encoding_blob is not None,
        "private": private_blob is not None,
    }
    top_dict, patches = _encode_top_dict(entries, relocate, private_size)

    name_index_bytes = _build_index(name_index)
    string_index_bytes = _build_index(string_index)
    gsubr_index_bytes = _build_index(gsubr_index)
    topdict_index_bytes = bytearray(_build_index([top_dict]))
    topdict_header_len = len(topdict_index_bytes) - len(top_dict)

    # Lay everything out and learn each relocated block's absolute offset.
    offset = len(header) + len(name_index_bytes)
    offset += len(topdict_index_bytes)
    offset += len(string_index_bytes) + len(gsubr_index_bytes)

    new_offsets = {"charstrings": offset}
    offset += len(new_cs_index)
    if charset_blob is not None:
        new_offsets["charset"] = offset
        offset += len(charset_blob)
    if encoding_blob is not None:
        new_offsets["encoding"] = offset
        offset += len(encoding_blob)
    if private_blob is not None:
        new_offsets["private"] = offset
        offset += len(private_blob)

    # Patch the (fixed-width) offset operands inside the built Top DICT INDEX.
    for name, rel in patches.items():
        struct.pack_into(
            ">I", topdict_index_bytes, topdict_header_len + rel, new_offsets[name]
        )

    out = bytearray(header)
    out += name_index_bytes
    out += topdict_index_bytes
    out += string_index_bytes
    out += gsubr_index_bytes
    out += new_cs_index
    if charset_blob is not None:
        out += charset_blob
    if encoding_blob is not None:
        out += encoding_blob
    if private_blob is not None:
        out += private_blob

    if len(out) >= len(font_bytes):
        return None
    return bytes(out)


def _assemble_cid(font_bytes, common, new_cs_index):
    """Re-assemble a CID-keyed CFF after charstring erasure.

    Relocates the charset (GID->CID), FDSelect (GID->FD) and FDArray (the Font
    DICT INDEX), and moves each Font DICT's Private DICT + local subrs, patching
    every Private offset to its new position.
    """
    header, name_index, entries, string_index, gsubr_index, num_glyphs = common

    fdselect_off = _dict_int(entries, _OP_FDSELECT)
    fdarray_off = _dict_int(entries, _OP_FDARRAY)
    if fdselect_off is None or fdarray_off is None:
        return None  # not a well-formed CID font.

    charset_blob = _slice_charset(font_bytes, entries, num_glyphs)
    fdselect_blob = _slice_fdselect(font_bytes, fdselect_off, num_glyphs)

    # Re-encode every Font DICT with a relocatable (fixed-width) Private offset
    # and collect the Private DICT (+ local subrs) blobs to lay out at the end.
    fd_items, _end = _read_index(font_bytes, fdarray_off)
    if not fd_items:
        return None
    encoded_fds: list[tuple[bytes, int | None, int | None]] = []
    private_blobs: list[bytes] = []
    for fd_bytes in fd_items:
        fd_entries = _parse_dict(fd_bytes)
        priv = _dict_ints(fd_entries, _OP_PRIVATE)
        if priv and len(priv) == 2:
            blob, _size = _slice_private(font_bytes, fd_entries)
            if blob is None:
                return None
            new_fd, rel = _encode_fd_dict(fd_entries)
            encoded_fds.append((new_fd, rel, len(private_blobs)))
            private_blobs.append(blob)
        else:
            encoded_fds.append((fd_bytes, None, None))
    new_fdarray = bytearray(_build_index([fd[0] for fd in encoded_fds]))
    fdarray_header_len = len(new_fdarray) - sum(len(fd[0]) for fd in encoded_fds)

    relocate = {"charset": charset_blob is not None, "fdselect": True, "fdarray": True}
    top_dict, patches = _encode_cid_top_dict(entries, relocate)

    name_index_bytes = _build_index(name_index)
    string_index_bytes = _build_index(string_index)
    gsubr_index_bytes = _build_index(gsubr_index)
    topdict_index_bytes = bytearray(_build_index([top_dict]))
    topdict_header_len = len(topdict_index_bytes) - len(top_dict)

    # Lay everything out and learn each relocated block's absolute offset.
    offset = len(header) + len(name_index_bytes) + len(topdict_index_bytes)
    offset += len(string_index_bytes) + len(gsubr_index_bytes)
    new_offsets = {"charstrings": offset}
    offset += len(new_cs_index)
    if charset_blob is not None:
        new_offsets["charset"] = offset
        offset += len(charset_blob)
    new_offsets["fdselect"] = offset
    offset += len(fdselect_blob)
    new_offsets["fdarray"] = offset
    offset += len(new_fdarray)
    private_offsets = []
    for blob in private_blobs:
        private_offsets.append(offset)
        offset += len(blob)

    # Patch the fixed-width offsets in the Top DICT and the FDArray Font DICTs.
    for name, rel in patches.items():
        struct.pack_into(
            ">I", topdict_index_bytes, topdict_header_len + rel, new_offsets[name]
        )
    cursor = fdarray_header_len
    for new_fd, rel, blob_idx in encoded_fds:
        if rel is not None:
            struct.pack_into(
                ">I", new_fdarray, cursor + rel, private_offsets[blob_idx]
            )
        cursor += len(new_fd)

    out = bytearray(header)
    out += name_index_bytes
    out += topdict_index_bytes
    out += string_index_bytes
    out += gsubr_index_bytes
    out += new_cs_index
    if charset_blob is not None:
        out += charset_blob
    out += fdselect_blob
    out += new_fdarray
    for blob in private_blobs:
        out += blob

    if len(out) >= len(font_bytes):
        return None
    return bytes(out)


def _slice_fdselect(data: bytes, off: int, num_glyphs: int) -> bytes:
    """Slice the FDSelect structure (format 0 or 3), copied verbatim."""
    fmt = data[off]
    if fmt == 0:
        length = 1 + num_glyphs
    elif fmt == 3:
        n_ranges = struct.unpack_from(">H", data, off + 1)[0]
        length = 3 + n_ranges * 3 + 2  # header + ranges + the sentinel uint16.
    else:
        raise ValueError("unknown FDSelect format")
    return data[off : off + length]


def _encode_cid_top_dict(entries, relocate):
    """Re-encode a CID-keyed Top DICT with fixed-width relocatable offsets.

    ``CharStrings`` is always relocated; ``charset`` / ``FDArray`` / ``FDSelect``
    per *relocate*.  ``ROS``, ``CIDCount`` and everything else are kept verbatim.
    """
    reloc_name = {
        _OP_CHARSTRINGS: "charstrings",
        _OP_CHARSET: "charset",
        _OP_FDARRAY: "fdarray",
        _OP_FDSELECT: "fdselect",
    }
    out = bytearray()
    patches: dict[str, int] = {}
    for key, operand_bytes in entries:
        name = reloc_name.get(key)
        if name == "charstrings" or (name is not None and relocate.get(name)):
            patches[name] = len(out) + 1
            out += _placeholder_offset()
            out += _op_bytes(key)
        else:
            out += operand_bytes + _op_bytes(key)
    return bytes(out), patches


def _encode_fd_dict(entries):
    """Re-encode a Font DICT, forcing its Private offset to a 5-byte placeholder.

    Returns ``(bytes, rel)`` where *rel* is the offset of the 4-byte value inside
    the returned bytes (``None`` when there is no Private to relocate).
    """
    out = bytearray()
    rel = None
    for key, operand_bytes in entries:
        if key == _OP_PRIVATE:
            ops = _decode_operands(operand_bytes)
            size = ops[0] if ops else 0
            out += _encode_int(size)
            rel = len(out) + 1
            out += _placeholder_offset()
            out += _op_bytes(key)
        else:
            out += operand_bytes + _op_bytes(key)
    return bytes(out), rel


# ---------------------------------------------------------------------------
# CID charset -> CID->GID map (for the caller's used-CID resolution)
# ---------------------------------------------------------------------------


def cff_charset_cid_to_gid(font_bytes: bytes) -> dict[int, int] | None:
    """Return a ``CID -> glyph id`` map for a CID-keyed CFF, else ``None``.

    ``None`` means "treat the CID as the glyph id directly" — the right
    behaviour for a name-keyed CFF used as a CIDFontType0, or a CID font whose
    charset is the identity / a predefined one.
    """
    try:
        return _cff_charset_cid_to_gid(font_bytes)
    except (struct.error, IndexError, ValueError):
        return None


def _cff_charset_cid_to_gid(font_bytes: bytes) -> dict[int, int] | None:
    if len(font_bytes) < 4:
        return None
    major, _minor, hdr_size, _off_size = font_bytes[0:4]
    if major != 1 or hdr_size < 4:
        return None
    _name_index, pos = _read_index(font_bytes, hdr_size)
    topdict_index, pos = _read_index(font_bytes, pos)
    if len(topdict_index) != 1:
        return None
    entries = _parse_dict(topdict_index[0])
    if _dict_get(entries, _OP_ROS) is None:
        return None  # name-keyed: CID is used as the glyph id directly.
    cs_off = _dict_int(entries, _OP_CHARSTRINGS)
    charset_off = _dict_int(entries, _OP_CHARSET)
    if cs_off is None or charset_off is None or charset_off <= 2:
        return None  # identity / predefined charset -> CID == GID.
    charstrings, _end = _read_index(font_bytes, cs_off)
    cids = _read_charset_cids(font_bytes, charset_off, len(charstrings))
    return {cid: gid for gid, cid in enumerate(cids)}


def _read_charset_cids(data: bytes, off: int, num_glyphs: int) -> list[int]:
    """Return the CID for each glyph id from a charset (format 0/1/2)."""
    fmt = data[off]
    cids = [0]  # glyph 0 (.notdef) maps to CID 0 implicitly.
    pos = off + 1
    if fmt == 0:
        for _ in range(num_glyphs - 1):
            cids.append(struct.unpack_from(">H", data, pos)[0])
            pos += 2
    elif fmt in (1, 2):
        while len(cids) < num_glyphs:
            first = struct.unpack_from(">H", data, pos)[0]
            pos += 2
            if fmt == 1:
                nleft = data[pos]
                pos += 1
            else:
                nleft = struct.unpack_from(">H", data, pos)[0]
                pos += 2
            for c in range(nleft + 1):
                if len(cids) >= num_glyphs:
                    break
                cids.append(first + c)
    else:
        raise ValueError("unknown charset format")
    return cids


# ---------------------------------------------------------------------------
# Trailing-block slicing (copied verbatim, then relocated)
# ---------------------------------------------------------------------------


def _slice_charset(data: bytes, entries, num_glyphs: int) -> bytes | None:
    off = _dict_int(entries, _OP_CHARSET)
    if off is None or off <= 2:  # 0/1/2 are predefined charsets (no data).
        return None
    fmt = data[off]
    if fmt == 0:
        length = 1 + (num_glyphs - 1) * 2
    elif fmt in (1, 2):
        pos = off + 1
        covered = 1  # glyph 0 (.notdef) is implicit.
        step_sid = 2
        nleft_size = 1 if fmt == 1 else 2
        while covered < num_glyphs:
            if fmt == 1:
                nleft = data[pos + step_sid]
            else:
                nleft = struct.unpack_from(">H", data, pos + step_sid)[0]
            covered += nleft + 1
            pos += step_sid + nleft_size
        length = pos - off
    else:
        raise ValueError("unknown charset format")
    return data[off : off + length]


def _slice_encoding(data: bytes, entries) -> bytes | None:
    off = _dict_int(entries, _OP_ENCODING)
    if off is None or off <= 1:  # 0/1 are predefined encodings (no data).
        return None
    fmt = data[off]
    base = fmt & 0x7F
    if base == 0:
        n_codes = data[off + 1]
        pos = off + 2 + n_codes
    elif base == 1:
        n_ranges = data[off + 1]
        pos = off + 2 + n_ranges * 2
    else:
        raise ValueError("unknown encoding format")
    if fmt & 0x80:  # supplements
        n_sups = data[pos]
        pos += 1 + n_sups * 3
    return data[off : off + (pos - off)]


def _slice_private(data: bytes, entries) -> tuple[bytes | None, int]:
    operands = _dict_ints(entries, _OP_PRIVATE)
    if not operands or len(operands) != 2:
        return None, 0
    size, off = operands
    end = off + size
    # A Private DICT may point to a Local Subr INDEX (offset relative to itself);
    # copy through to its end so the relative offset stays valid after moving.
    rel = _dict_int(_parse_dict(data[off : off + size]), _OP_SUBRS)
    if rel is not None:
        _subrs, subrs_end = _read_index(data, off + rel)
        end = max(end, subrs_end)
    return data[off:end], size


# ---------------------------------------------------------------------------
# Top DICT re-encoding (offsets forced to fixed 5-byte form for single-pass)
# ---------------------------------------------------------------------------


def _encode_top_dict(entries, relocate, private_size: int):
    """Re-encode the Top DICT, returning bytes and per-block patch positions.

    Relocated offset operands are written as a 5-byte (operator 29) integer so
    the encoded Top DICT length is independent of the final offset values -- the
    layout can therefore be computed in a single pass and the actual offsets
    patched in afterwards. Every other operator keeps its original operand bytes
    verbatim (preserving reals such as the FontMatrix exactly).
    """
    out = bytearray()
    patches: dict[str, int] = {}
    for key, operand_bytes in entries:
        if key == _OP_CHARSTRINGS:
            patches["charstrings"] = len(out) + 1
            out += _placeholder_offset()
            out += _op_bytes(key)
        elif key == _OP_CHARSET and relocate["charset"]:
            patches["charset"] = len(out) + 1
            out += _placeholder_offset()
            out += _op_bytes(key)
        elif key == _OP_ENCODING and relocate["encoding"]:
            patches["encoding"] = len(out) + 1
            out += _placeholder_offset()
            out += _op_bytes(key)
        elif key == _OP_PRIVATE and relocate["private"]:
            out += _encode_int(private_size)
            patches["private"] = len(out) + 1
            out += _placeholder_offset()
            out += _op_bytes(key)
        else:
            out += operand_bytes + _op_bytes(key)
    return bytes(out), patches


def _placeholder_offset() -> bytes:
    return b"\x1d\x00\x00\x00\x00"  # operator 29 (5-byte int) + zero value.


def _op_bytes(key) -> bytes:
    if isinstance(key, tuple):
        return bytes(key)
    return bytes([key])


def _encode_int(value: int) -> bytes:
    if -107 <= value <= 107:
        return bytes([value + 139])
    if 108 <= value <= 1131:
        value -= 108
        return bytes([(value >> 8) + 247, value & 0xFF])
    if -1131 <= value <= -108:
        value = -108 - value
        return bytes([(value >> 8) + 251, value & 0xFF])
    if -32768 <= value <= 32767:
        return b"\x1c" + struct.pack(">h", value)
    return b"\x1d" + struct.pack(">i", value)


# ---------------------------------------------------------------------------
# CFF INDEX structures
# ---------------------------------------------------------------------------


def _read_index(data: bytes, pos: int) -> tuple[list[bytes], int]:
    count = struct.unpack_from(">H", data, pos)[0]
    pos += 2
    if count == 0:
        return [], pos
    off_size = data[pos]
    pos += 1
    if off_size < 1 or off_size > 4:
        raise ValueError("bad INDEX offSize")
    offsets = []
    for _ in range(count + 1):
        offsets.append(int.from_bytes(data[pos : pos + off_size], "big"))
        pos += off_size
    base = pos - 1  # offsets are relative to the byte before the object data.
    items = []
    for i in range(count):
        start, end = base + offsets[i], base + offsets[i + 1]
        if not (pos <= start <= end <= len(data)):
            raise ValueError("bad INDEX offsets")
        items.append(data[start:end])
    return items, base + offsets[count]


def _build_index(items: list[bytes]) -> bytes:
    if not items:
        return b"\x00\x00"
    data = b"".join(items)
    offsets = [1]
    for item in items:
        offsets.append(offsets[-1] + len(item))
    last = offsets[-1]
    off_size = 1 if last < 0x100 else 2 if last < 0x10000 else 3 if last < 0x1000000 else 4
    out = bytearray(struct.pack(">H", len(items)))
    out.append(off_size)
    for off in offsets:
        out += off.to_bytes(off_size, "big")
    out += data
    return bytes(out)


# ---------------------------------------------------------------------------
# CFF DICT parsing
# ---------------------------------------------------------------------------


def _parse_dict(data: bytes) -> list[tuple]:
    """Parse a CFF DICT into ``[(key, operand_bytes), ...]`` preserving order."""
    entries: list[tuple] = []
    operands_start = 0
    i = 0
    n = len(data)
    while i < n:
        b0 = data[i]
        if b0 <= 21:  # operator
            op_pos = i
            if b0 == 12:
                key = (12, data[i + 1])
                i += 2
            else:
                key = b0
                i += 1
            entries.append((key, data[operands_start:op_pos]))
            operands_start = i
        elif b0 == 28:
            i += 3
        elif b0 == 29:
            i += 5
        elif b0 == 30:  # real number: nibbles until 0xf terminator
            i += 1
            while i < n:
                byte = data[i]
                i += 1
                if (byte & 0x0F) == 0x0F or (byte & 0xF0) == 0xF0:
                    break
        elif 32 <= b0 <= 246:
            i += 1
        elif 247 <= b0 <= 254:
            i += 2
        else:
            i += 1  # 22-27, 31, 255: reserved -- skip defensively.
    return entries


def _decode_operands(operand_bytes: bytes) -> list[int]:
    """Decode the integer operands in *operand_bytes* (reals are skipped)."""
    values: list[int] = []
    i = 0
    n = len(operand_bytes)
    while i < n:
        b0 = operand_bytes[i]
        if b0 == 28:
            values.append(struct.unpack_from(">h", operand_bytes, i + 1)[0])
            i += 3
        elif b0 == 29:
            values.append(struct.unpack_from(">i", operand_bytes, i + 1)[0])
            i += 5
        elif b0 == 30:  # real -- not needed for offsets; skip it
            i += 1
            while i < n:
                byte = operand_bytes[i]
                i += 1
                if (byte & 0x0F) == 0x0F or (byte & 0xF0) == 0xF0:
                    break
        elif 32 <= b0 <= 246:
            values.append(b0 - 139)
            i += 1
        elif 247 <= b0 <= 250:
            values.append((b0 - 247) * 256 + operand_bytes[i + 1] + 108)
            i += 2
        elif 251 <= b0 <= 254:
            values.append(-(b0 - 251) * 256 - operand_bytes[i + 1] - 108)
            i += 2
        else:
            i += 1
    return values


def _dict_get(entries, key):
    for entry_key, operand_bytes in entries:
        if entry_key == key:
            return operand_bytes
    return None


def _dict_ints(entries, key) -> list[int] | None:
    operand_bytes = _dict_get(entries, key)
    if operand_bytes is None:
        return None
    return _decode_operands(operand_bytes)


def _dict_int(entries, key) -> int | None:
    values = _dict_ints(entries, key)
    if not values:
        return None
    return values[-1]
