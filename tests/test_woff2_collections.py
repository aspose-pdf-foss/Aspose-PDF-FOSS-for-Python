"""WOFF2 font-collection (ttcf) reconstruction and the TTC builder."""

from __future__ import annotations

import io
import struct

import pytest

pytest.importorskip("fontTools")

from fontTools.fontBuilder import FontBuilder  # noqa: E402
from fontTools.pens.ttGlyphPen import TTGlyphPen  # noqa: E402
from fontTools.ttLib import TTFont  # noqa: E402
from fontTools.ttLib.ttCollection import TTCollection  # noqa: E402

from aspose_pdf.engine.sfnt import parse_faces  # noqa: E402
from aspose_pdf.engine.woff import build_ttc  # noqa: E402
from aspose_pdf.engine.woff2 import decode  # noqa: E402

_KNOWN_TAGS = (
    "cmap", "head", "hhea", "hmtx", "maxp", "name", "OS/2", "post", "cvt ",
    "fpgm", "glyf", "loca", "prep", "CFF ", "VORG", "EBDT", "EBLC", "gasp",
    "hdmx", "kern", "LTSH", "PCLT", "VDMX", "vhea", "vmtx", "BASE", "GDEF",
    "GPOS", "GSUB", "EBSC", "JSTF", "MATH", "CBDT", "CBLC", "COLR", "CPAL",
    "SVG ", "sbix", "acnt", "avar", "bdat", "bloc", "bsln", "cvar", "fdsc",
    "feat", "fmtx", "fvar", "gvar", "hsty", "just", "lcar", "mort", "morx",
    "opbd", "prop", "trak", "Zapf", "Silf", "Glat", "Gloc", "Feat", "Sill",
)  # fmt: skip


def _font_tables(family: str) -> dict[str, bytes]:
    order = [".notdef", "space", "A"]
    glyphs = {}
    for name in order:
        pen = TTGlyphPen(None)
        pen.moveTo((0, 0))
        pen.lineTo((500, 0))
        pen.lineTo((500, 700))
        pen.closePath()
        glyphs[name] = pen.glyph()
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap({0x20: "space", 0x41: "A"})
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({n: (500, 0) for n in order})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": family, "styleName": "Regular"})
    builder.setupOS2()
    builder.setupPost()
    builder.setupMaxp()
    builder.font.recalcTimestamp = False
    output = io.BytesIO()
    builder.font.save(output)
    font = TTFont(io.BytesIO(output.getvalue()))
    return {
        tag: font.getTableData(tag)
        for tag in font.keys()
        if len(tag) == 4 and tag != "GlyphOrder"
    }


def test_build_ttc_round_trips() -> None:
    faces = [
        (0x00010000, list(_font_tables("Alpha").items())),
        (0x00010000, list(_font_tables("Beta").items())),
    ]
    ttc = build_ttc(faces)
    assert ttc[:4] == b"ttcf"
    ours = parse_faces(ttc)
    assert [face.best_name for face in ours] == ["Alpha", "Beta"]
    reparsed = TTCollection(io.BytesIO(ttc))
    assert [f["name"].getDebugName(1) for f in reparsed.fonts] == ["Alpha", "Beta"]
    assert all(f["maxp"].numGlyphs == 3 for f in reparsed.fonts)


def _w255(value: int) -> bytes:
    if value < 253:
        return bytes([value])
    if value < 506:
        return bytes([255, value - 253])
    if value < 762:
        return bytes([254, value - 506])
    return bytes([253]) + struct.pack(">H", value)


def _wbase128(value: int) -> bytes:
    if value == 0:
        return b"\x00"
    out: list[int] = []
    while value:
        out.insert(0, value & 0x7F)
        value >>= 7
    for i in range(len(out) - 1):
        out[i] |= 0x80
    return bytes(out)


def test_decode_woff2_collection() -> None:
    """A hand-built (untransformed) WOFF2 collection decodes to a valid TTC."""
    brotli = pytest.importorskip("brotli")
    ta = _font_tables("Alpha")
    tb = _font_tables("Beta")
    tags = sorted(ta)
    unique = [(tag, ta[tag]) for tag in tags]
    name_b_index = len(unique)
    unique.append(("name", tb["name"]))

    def index_of(tag: str, use_b: bool) -> int:
        return name_b_index if (tag == "name" and use_b) else tags.index(tag)

    face_a = [index_of(tag, False) for tag in tags]
    face_b = [index_of(tag, tag == "name") for tag in tags]

    directory = bytearray()
    stream = bytearray()
    for tag, body in unique:
        flags = _KNOWN_TAGS.index(tag) if tag in _KNOWN_TAGS else 0x3F
        transform = 3 if tag in ("glyf", "loca") else 0  # 3 == untransformed
        directory += bytes([flags | (transform << 6)])
        if (flags & 0x3F) == 0x3F:
            directory += tag.encode("latin-1")
        directory += _wbase128(len(body))
        stream += body

    collection = bytearray(struct.pack(">I", 0x00010000)) + _w255(2)
    for face in (face_a, face_b):
        collection += _w255(len(face)) + struct.pack(">I", 0x00010000)
        for i in face:
            collection += _w255(i)

    compressed = brotli.compress(bytes(stream))
    total_sfnt = 12 + 16 * len(unique) + sum((len(b) + 3) & ~3 for _, b in unique)
    header = bytearray(b"wOF2")
    header += struct.pack(">I", 0x74746366)  # ttcf flavor
    header += struct.pack(">I", 0)  # length (patched below)
    header += struct.pack(">HH", len(unique), 0)
    header += struct.pack(">I", total_sfnt)
    header += struct.pack(">I", len(compressed))
    header += struct.pack(">HHIIIII", 1, 0, 0, 0, 0, 0, 0)
    assert len(header) == 48

    woff2 = bytes(header) + bytes(directory) + bytes(collection) + compressed
    woff2 = woff2[:8] + struct.pack(">I", len(woff2)) + woff2[12:]

    out = decode(woff2)
    assert out is not None and out[:4] == b"ttcf"
    faces = TTCollection(io.BytesIO(out))
    assert [f["name"].getDebugName(1) for f in faces.fonts] == ["Alpha", "Beta"]
    assert all(f["maxp"].numGlyphs == 3 for f in faces.fonts)
