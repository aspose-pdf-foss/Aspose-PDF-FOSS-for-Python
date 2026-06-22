"""Tests for the dependency-free WOFF 1.0 decoder and its font-subsystem wiring."""

from __future__ import annotations

import struct
import zlib

from aspose_pdf.engine.font_subset import subset_truetype
from aspose_pdf.engine.sfnt import is_sfnt, parse_faces
from aspose_pdf.engine.woff import decode, is_woff, is_woff2
from aspose_pdf.font_repository import FileFontSource, MemoryFontSource

_TRUETYPE = 0x00010000
_OTTO = 0x4F54544F
_CHECKSUM_MAGIC = 0xB1B0AFBA


# ---------------------------------------------------------------------------
# Minimal SFNT + WOFF 1.0 builders (no external font files required).
# ---------------------------------------------------------------------------


def _name_table(family: str, subfamily: str = "Regular") -> bytes:
    full = f"{family} {subfamily}"
    postscript = family.replace(" ", "")
    records = [
        (3, 1, 0x0409, 1, family),
        (3, 1, 0x0409, 2, subfamily),
        (3, 1, 0x0409, 4, full),
        (3, 1, 0x0409, 6, postscript),
    ]
    body = b""
    storage = b""
    pos = 0
    for platform, encoding, language, name_id, value in records:
        encoded = value.encode("utf-16-be")
        body += struct.pack(
            ">HHHHHH", platform, encoding, language, name_id, len(encoded), pos
        )
        storage += encoded
        pos += len(encoded)
    header = struct.pack(">HHH", 0, len(records), 6 + 12 * len(records))
    return header + body + storage


def _pad4(data: bytes) -> bytes:
    return data + b"\x00" * ((4 - len(data) % 4) % 4)


def _sfnt(tables: list[tuple[str, bytes]], version: int = _TRUETYPE) -> bytes:
    """Assemble an SFNT (checksums left zero; offsets are real)."""
    num = len(tables)
    header = struct.pack(">IHHHH", version, num, 0, 0, 0)
    directory = b""
    body = b""
    offset = 12 + 16 * num
    for tag, payload in tables:
        directory += tag.encode("ascii") + struct.pack(">III", 0, offset, len(payload))
        padded = _pad4(payload)
        body += padded
        offset += len(padded)
    return header + directory + body


def _simple_glyph(payload_len: int) -> bytes:
    return struct.pack(">hHHHH", 1, 0, 0, 100, 100) + b"\xAB" * payload_len


def _ttf(family: str, glyphs: list[bytes] | None = None) -> bytes:
    """A minimal but real TrueType font with a name table and glyf/loca."""
    glyphs = glyphs or [b"", _simple_glyph(40), _simple_glyph(120)]
    glyf = b"".join(glyphs)
    offsets = [0]
    for g in glyphs:
        offsets.append(offsets[-1] + len(g))
    loca = b"".join(struct.pack(">I", o) for o in offsets)  # long loca
    head = (
        struct.pack(">III", 0x00010000, 0, 0)  # version, fontRevision, checkSumAdj
        + struct.pack(">I", 0x5F0F3CF5)  # magicNumber
        + struct.pack(">HH", 0, 1000)  # flags, unitsPerEm
        + struct.pack(">qq", 0, 0)  # created, modified
        + struct.pack(">hhhh", 0, 0, 100, 100)  # bbox
        + struct.pack(">HHh", 0, 8, 0)  # macStyle, lowestRecPPEM, fontDirectionHint
        + struct.pack(">hh", 1, 0)  # indexToLocFormat (long), glyphDataFormat
    )
    maxp = struct.pack(">IH", 0x00010000, len(glyphs))
    return _sfnt(
        [
            ("glyf", glyf),
            ("head", head),
            ("loca", loca),
            ("maxp", maxp),
            ("name", _name_table(family)),
        ]
    )


def _otf(family: str) -> bytes:
    return _sfnt([("CFF ", b"\x01\x00"), ("name", _name_table(family))], version=_OTTO)


def _woff1(sfnt: bytes, *, compress: bool = True) -> bytes:
    """Wrap *sfnt* into a WOFF 1.0 container (zlib per table when it helps)."""
    flavor, num_tables = struct.unpack_from(">IH", sfnt, 0)
    entries = []
    for i in range(num_tables):
        rec = 12 + 16 * i
        tag = sfnt[rec : rec + 4]
        checksum, offset, length = struct.unpack_from(">III", sfnt, rec + 4)
        entries.append((tag, checksum, sfnt[offset : offset + length]))

    header_size = 44
    dir_size = 20 * num_tables
    data_base = header_size + dir_size

    directory = b""
    region = bytearray()
    total_sfnt_size = 12 + 16 * num_tables
    for tag, checksum, orig in entries:
        comp = zlib.compress(orig) if compress else orig
        if len(comp) >= len(orig):  # store uncompressed when not smaller
            comp = orig
        woff_off = data_base + len(region)
        directory += tag + struct.pack(
            ">IIII", woff_off, len(comp), len(orig), checksum
        )
        region += comp
        region += b"\x00" * ((4 - len(region) % 4) % 4)  # pad to 4 bytes
        total_sfnt_size += len(_pad4(orig))

    length = data_base + len(region)
    header = b"wOFF" + struct.pack(
        ">IIHHIHHIIIII",
        flavor,
        length,
        num_tables,
        0,  # reserved
        total_sfnt_size,
        0,
        0,  # major/minor version
        0,
        0,
        0,  # meta offset/length/origLength
        0,
        0,  # priv offset/length
    )
    return header + directory + bytes(region)


def _tables(font: bytes) -> dict[str, bytes]:
    """Map each table tag to its (unpadded) bytes."""
    num = struct.unpack_from(">H", font, 4)[0]
    out = {}
    for i in range(num):
        rec = 12 + 16 * i
        tag = font[rec : rec + 4].decode("latin-1")
        _checksum, off, length = struct.unpack_from(">III", font, rec + 4)
        out[tag] = font[off : off + length]
    return out


def _whole_file_checksum(data: bytes) -> int:
    if len(data) % 4:
        data = data + b"\x00" * (4 - len(data) % 4)
    total = 0
    for i in range(0, len(data), 4):
        total = (total + struct.unpack_from(">I", data, i)[0]) & 0xFFFFFFFF
    return total


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_signature_detection():
    assert is_woff(_woff1(_ttf("Acme")))
    assert not is_woff(b"wOF2" + b"\x00" * 40)
    assert not is_woff(_ttf("Acme"))
    assert not is_woff(b"")

    assert is_woff2(b"wOF2" + b"\x00" * 40)
    assert not is_woff2(_woff1(_ttf("Acme")))


# ---------------------------------------------------------------------------
# decode()
# ---------------------------------------------------------------------------


def test_decode_reconstructs_sfnt_round_trip():
    sfnt = _ttf("Acme Sans")
    out = decode(_woff1(sfnt))
    assert out is not None
    assert is_sfnt(out)

    original, got = _tables(sfnt), _tables(out)
    assert set(got) == set(original)
    for tag in original:
        if tag == "head":
            # checkSumAdjustment (bytes 8:12) is intentionally rewritten.
            assert got[tag][:8] + b"\x00\x00\x00\x00" + got[tag][12:] == original[tag]
        else:
            assert got[tag] == original[tag]


def test_decode_uncompressed_tables_round_trip():
    # compress=False forces every table to be stored verbatim (comp == orig).
    sfnt = _ttf("Plain")
    out = decode(_woff1(sfnt, compress=False))
    assert out is not None
    assert _tables(out)["name"] == _tables(sfnt)["name"]


def test_decoded_font_has_valid_checksum_adjustment():
    out = decode(_woff1(_ttf("Acme")))
    assert out is not None
    assert _whole_file_checksum(out) == _CHECKSUM_MAGIC


def test_decode_rejects_non_woff_and_woff2():
    assert decode(_ttf("Acme")) is None  # already an SFNT, not WOFF
    assert decode(b"wOF2" + b"\x00" * 60) is None  # WOFF2 needs Brotli
    assert decode(b"junk") is None
    assert decode(b"") is None


def test_decode_is_defensive_against_corruption():
    woff = bytearray(_woff1(_ttf("Acme")))
    # Point the first table directory entry's offset past EOF.
    struct.pack_into(">I", woff, 44 + 4, len(woff) + 1000)
    assert decode(bytes(woff)) is None

    assert decode(_woff1(_ttf("Acme"))[:30]) is None  # truncated container


# ---------------------------------------------------------------------------
# parse_faces() now sees through WOFF 1.0
# ---------------------------------------------------------------------------


def test_parse_faces_unwraps_woff_truetype():
    faces = parse_faces(_woff1(_ttf("Web Sans")))
    assert len(faces) == 1
    assert faces[0].family_name == "Web Sans"
    assert faces[0].font_type == "TrueType"
    assert "glyf" in faces[0].table_tags


def test_parse_faces_unwraps_woff_opentype():
    faces = parse_faces(_woff1(_otf("Web Serif")))
    assert len(faces) == 1
    assert faces[0].family_name == "Web Serif"
    assert faces[0].font_type == "OpenType"


def test_parse_faces_still_rejects_woff2_and_garbage():
    assert parse_faces(b"wOF2" + b"\x00" * 40) == []
    assert parse_faces(b"wOFF" + b"\x00" * 40) == []  # malformed WOFF1


# ---------------------------------------------------------------------------
# Embedding + subsetting through the font subsystem
# ---------------------------------------------------------------------------


def test_memory_source_woff_is_discovered_with_real_name():
    source = MemoryFontSource(_woff1(_ttf("Mem Web")), name="ignored-fallback")
    defs = source.get_font_definitions()
    assert len(defs) == 1
    assert defs[0].family_name == "Mem Web"
    assert defs[0].font_type == "TrueType"


def test_get_font_bytes_unwraps_woff_for_embedding():
    source = MemoryFontSource(_woff1(_ttf("Embed Me")))
    program = source.get_font_definitions()[0].get_font_bytes()
    # The embeddable program is a plain SFNT, not the WOFF wrapper.
    assert not is_woff(program)
    assert is_sfnt(program)
    assert parse_faces(program)[0].family_name == "Embed Me"


def test_file_source_woff_recovers_real_name(tmp_path):
    path = tmp_path / "WebFont.woff"
    path.write_bytes(_woff1(_ttf("Real Family")))
    defs = FileFontSource(path).get_font_definitions()
    assert len(defs) == 1
    # Real family name, not the "WebFont" file-stem fallback.
    assert defs[0].family_name == "Real Family"
    assert defs[0].font_type == "TrueType"
    assert is_sfnt(defs[0].get_font_bytes())


def test_decoded_woff_truetype_can_be_subset():
    # A WOFF web font, once unwrapped, subsets exactly like a .ttf.
    glyphs = [b"", _simple_glyph(40), _simple_glyph(80), _simple_glyph(120)]
    program = decode(_woff1(_ttf("Subsettable", glyphs)))
    assert program is not None
    subset = subset_truetype(program, {1})
    assert subset is not None
    assert len(subset) < len(program)
    assert parse_faces(subset)[0].font_type == "TrueType"
