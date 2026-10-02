"""A subset drops the tables an embedded font program is never read for.

Erasing glyph outlines left every other table in place, and on a large face
those outweighed what was kept: one line of text in Arial Unicode (50377 glyphs)
embedded 869768 bytes, of which 12 glyphs' outlines were 2636. ``hdmx`` alone was
302288 bytes, with ``LTSH`` 50381, ``GSUB`` 66612 and ``DSIG`` 9648 behind it.

The set dropped is the one fontTools' own subsetter drops by default, less the
colour and bitmap tables, which are kept because a consumer may draw from them.
``DSIG`` is the clearest case: it signs the *unmodified* font, so after erasure
it signs nothing.

Measured on that font: 869768 -> 428568 bytes raw, 142651 -> 65364 deflated, with
the page rendering byte-identically before and after and the same agreement with
MuPDF and pdfium as before.
"""

from __future__ import annotations

import struct

import pytest

from aspose_pdf.engine.font_subset import DROPPED_TABLES, subset_truetype


def _tables(program: bytes) -> dict[str, bytes]:
    _version, count = struct.unpack_from(">IH", program, 0)
    out = {}
    for index in range(count):
        tag, _checksum, offset, length = struct.unpack_from(
            ">4sIII", program, 12 + index * 16
        )
        out[tag.decode("latin-1")] = program[offset : offset + length]
    return out


def _synthetic(extra: dict[str, bytes]) -> bytes:
    """A minimal two-glyph TrueType with whatever extra tables are asked for."""
    glyf = b"\x00" * 16
    loca = struct.pack(">III", 0, 16, 16)          # glyph 0 has 16 bytes, 1 empty
    head = bytearray(54)
    head[50:52] = struct.pack(">h", 1)             # long loca
    maxp = struct.pack(">IH", 0x00010000, 2)
    tables = {"glyf": glyf, "loca": bytes(loca), "head": bytes(head), "maxp": maxp}
    tables.update(extra)

    names = sorted(tables)
    offset = 12 + 16 * len(names)
    directory, body = bytearray(), bytearray()
    for name in names:
        data = tables[name]
        directory += struct.pack(">4sIII", name.encode("latin-1"), 0, offset, len(data))
        padded = data + b"\x00" * (-len(data) % 4)
        body += padded
        offset += len(padded)
    return (
        struct.pack(">IHHHH", 0x00010000, len(names), 0, 0, 0)
        + bytes(directory)
        + bytes(body)
    )


def test_the_dropped_set_names_what_it_should():
    # Layout, hinting aids, device metrics and the stale signature.
    assert {"GSUB", "GPOS", "GDEF", "kern"} <= DROPPED_TABLES
    assert {"hdmx", "LTSH", "VDMX", "gasp"} <= DROPPED_TABLES
    assert "DSIG" in DROPPED_TABLES
    assert "PCLT" in DROPPED_TABLES


@pytest.mark.parametrize(
    "kept",
    ["cmap", "hhea", "hmtx", "COLR", "CPAL", "CBDT", "CBLC", "sbix", "EBDT"],
)
def test_colour_bitmap_and_required_tables_are_kept(kept):
    # A consumer may draw from the colour and bitmap tables, so dropping them
    # could change the page; the required ones are required.
    assert kept not in DROPPED_TABLES


@pytest.mark.parametrize("dropped", sorted(DROPPED_TABLES))
def test_each_dropped_table_really_goes(dropped):
    program = _synthetic({dropped: b"payload-" + dropped.encode("latin-1")})
    assert dropped in _tables(program)

    result = subset_truetype(program, {0})
    assert result is not None
    assert dropped not in _tables(result)


def test_what_the_font_needs_survives():
    program = _synthetic(
        {"hdmx": b"x" * 400, "cmap": b"c" * 40, "hhea": b"h" * 36, "name": b"n" * 20}
    )
    result = subset_truetype(program, {0})
    assert result is not None
    tables = _tables(result)
    for required in ("glyf", "loca", "head", "maxp"):
        assert required in tables
    for kept in ("cmap", "hhea", "name"):
        assert tables[kept] == _tables(program)[kept]
    assert "hdmx" not in tables


def test_dropping_them_is_most_of_the_saving():
    # The outlines are tiny next to the tables that used to come along.
    program = _synthetic({"hdmx": b"x" * 4000, "LTSH": b"y" * 2000})
    result = subset_truetype(program, {0})
    assert result is not None
    assert len(result) < len(program) - 5000


def test_a_font_without_any_of_them_is_unaffected():
    program = _synthetic({"cmap": b"c" * 40})
    result = subset_truetype(program, {0})
    # Nothing to drop and nothing to erase: the subsetter declines rather than
    # returning a copy that is no smaller.
    assert result is None or len(result) <= len(program)
