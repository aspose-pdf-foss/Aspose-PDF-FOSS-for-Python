"""Dependency-free WOFF 1.0 decoder.

WOFF 1.0 (the W3C Web Open Font Format) is a thin wrapper around an SFNT
(TrueType / OpenType) font: it carries the very same tables, each optionally
compressed with zlib (RFC 1950). This module reverses that wrapper,
reconstructing the original SFNT byte stream so the rest of the font subsystem
-- name recovery (:mod:`aspose_pdf.engine.sfnt`), embedding, and TrueType
subsetting -- can treat a ``.woff`` exactly like a ``.ttf`` / ``.otf``.

Only :mod:`zlib` from the standard library is used, so WOFF 1.0 decoding is
dependency-free. WOFF2 (``wOF2``) is handled by :mod:`aspose_pdf.engine.woff2`
(to which :func:`decode` delegates); it needs the optional ``brotli`` package
and, when that is absent, simply reports the font as undecodable so callers fall
back to file-name metadata.

Parsing is deliberately defensive: malformed or truncated input never raises;
:func:`decode` simply returns ``None`` so the caller can fall back to the
original bytes.
"""

from __future__ import annotations

import struct
import zlib

__all__ = ["decode", "is_woff", "is_woff2", "build_sfnt"]

_WOFF1_SIGNATURE = b"wOFF"
_WOFF2_SIGNATURE = b"wOF2"

# WOFF 1.0 header is 44 bytes; each table directory entry is 20 bytes.
_HEADER_SIZE = 44
_DIR_ENTRY_SIZE = 20

# Magic constant used when deriving ``head.checkSumAdjustment``.
_CHECKSUM_MAGIC = 0xB1B0AFBA


def is_woff(data: bytes) -> bool:
    """Return ``True`` if *data* starts with the WOFF 1.0 signature."""
    return len(data) >= 4 and data[:4] == _WOFF1_SIGNATURE


def is_woff2(data: bytes) -> bool:
    """Return ``True`` if *data* starts with the WOFF2 signature."""
    return len(data) >= 4 and data[:4] == _WOFF2_SIGNATURE


def decode(data: bytes) -> bytes | None:
    """Reconstruct the SFNT font wrapped in WOFF *data*.

    WOFF 1.0 is decoded here (dependency-free); WOFF2 is delegated to
    :mod:`aspose_pdf.engine.woff2`, which needs the optional ``brotli`` package.
    Returns the decoded TrueType / OpenType byte stream, or ``None`` when *data*
    is not a WOFF wrapper or cannot be decoded.
    """
    if is_woff(data):
        try:
            return _decode_woff1(data)
        except (struct.error, IndexError, ValueError, zlib.error):
            return None
    if is_woff2(data):
        # Imported lazily to break the woff <-> woff2 import cycle (woff2 reuses
        # build_sfnt from this module).
        from aspose_pdf.engine import woff2

        return woff2.decode(data)
    return None


def _decode_woff1(data: bytes) -> bytes | None:
    if len(data) < _HEADER_SIZE:
        return None

    flavor = struct.unpack_from(">I", data, 4)[0]
    num_tables = struct.unpack_from(">H", data, 12)[0]
    if num_tables == 0:
        return None

    tables: list[tuple[str, bytes]] = []
    record = _HEADER_SIZE
    for _ in range(num_tables):
        if record + _DIR_ENTRY_SIZE > len(data):
            return None
        tag, offset, comp_len, orig_len, _checksum = struct.unpack_from(
            ">4sIIII", data, record
        )
        record += _DIR_ENTRY_SIZE

        if offset + comp_len > len(data) or comp_len > orig_len:
            return None
        raw = data[offset : offset + comp_len]
        if comp_len == orig_len:
            table = raw  # stored uncompressed
        else:
            table = zlib.decompress(raw)
            if len(table) != orig_len:
                return None
        tables.append((tag.decode("latin-1"), table))

    return build_sfnt(flavor, tables)


def build_sfnt(flavor: int, tables: list[tuple[str, bytes]]) -> bytes:
    """Assemble an SFNT from decompressed *tables*, fixing offsets/checksums.

    The table directory is written in ascending tag order (as the SFNT spec
    requires), every table is padded to a 4-byte boundary, per-table checksums
    are recomputed, and ``head.checkSumAdjustment`` is rewritten so the whole
    file checksums correctly -- the original WOFF layout (and any stale
    adjustment) is not trusted. Shared with :mod:`aspose_pdf.engine.woff2`.
    """
    ordered = sorted(tables, key=lambda item: item[0])
    num_tables = len(ordered)

    # Per-table checksum; head is summed with checkSumAdjustment zeroed.
    prepared: list[tuple[str, bytes, int]] = []
    for tag, body in ordered:
        checksum_body = body
        if tag == "head" and len(body) >= 12:
            checksum_body = body[:8] + b"\x00\x00\x00\x00" + body[12:]
        prepared.append((tag, body, _checksum(checksum_body)))

    # Lay tables out after the header + directory, each 4-byte aligned.
    offsets: list[int] = []
    cursor = 12 + 16 * num_tables
    for _tag, body, _cs in prepared:
        offsets.append(cursor)
        cursor += _aligned4(len(body))

    entry_selector = max(num_tables.bit_length() - 1, 0)
    search_range = (1 << entry_selector) * 16
    range_shift = num_tables * 16 - search_range

    out = bytearray()
    out += struct.pack(
        ">IHHHH", flavor, num_tables, search_range, entry_selector, range_shift
    )
    head_offset: int | None = None
    for (tag, body, checksum), table_offset in zip(prepared, offsets):
        if tag == "head":
            head_offset = table_offset
        out += tag.encode("latin-1")
        out += struct.pack(">III", checksum, table_offset, len(body))
    for _tag, body, _cs in prepared:
        out += body
        out += b"\x00" * (_aligned4(len(body)) - len(body))

    _apply_checksum_adjustment(out, head_offset)
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
