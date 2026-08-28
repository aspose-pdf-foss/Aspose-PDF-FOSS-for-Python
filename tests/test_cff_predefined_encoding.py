"""CFF charset name->gid, predefined-encoding resolution, and static CFF2."""

from __future__ import annotations

import io

import pytest

pytest.importorskip("fontTools")

from fontTools.cffLib.CFFToCFF2 import convertCFFToCFF2
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.ttLib import TTFont

from aspose_pdf.engine.agl import base_encoding_table
from aspose_pdf.engine.cff_outlines import CffOutlines


def _charstring() -> object:
    pen = T2CharStringPen(600, {})
    pen.moveTo((100, 0))
    pen.lineTo((500, 0))
    pen.lineTo((500, 700))
    pen.lineTo((100, 700))
    pen.closePath()
    return pen.getCharString()


def _build_cff(order: list[str], cmap: dict[int, str]) -> bytes:
    builder = FontBuilder(1000, isTTF=False)
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap(cmap)
    builder.setupCFF("Test", {}, {name: _charstring() for name in order}, {})
    builder.setupHorizontalMetrics({name: (600, 0) for name in order})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "Test", "styleName": "Regular"})
    builder.setupOS2()
    builder.setupPost()
    output = io.BytesIO()
    builder.save(output)
    return TTFont(io.BytesIO(output.getvalue())).getTableData("CFF ")


def test_cff_name_to_gid_from_charset() -> None:
    cff = _build_cff(
        [".notdef", "space", "A", "aacute"],
        {0x20: "space", 0x41: "A", 0xE1: "aacute"},
    )
    outlines = CffOutlines(cff)
    assert outlines.ok
    name_to_gid = outlines.name_to_gid()
    assert name_to_gid["A"] == 2 and name_to_gid["aacute"] == 3


def test_predefined_standard_encoding_resolves_to_gid() -> None:
    """A code routed through StandardEncoding reaches the CFF charset gid."""
    cff = _build_cff([".notdef", "space", "A"], {0x20: "space", 0x41: "A"})
    outlines = CffOutlines(cff)
    standard = base_encoding_table("StandardEncoding")
    # StandardEncoding maps code 65 -> "A"; the charset maps "A" -> gid 2.
    assert standard[65] == "A"
    assert outlines.name_to_gid()[standard[65]] == 2
    # And glyph 2 has a real outline (four corner points), not a box fallback.
    assert len(outlines.outline(2)[0]) == 4


def test_static_cff2_renders_like_cff1() -> None:
    """A CFF2 program renders the same outlines as its CFF1 source."""
    output = io.BytesIO()
    builder = FontBuilder(1000, isTTF=False)
    order = [".notdef", "space", "A"]
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap({0x20: "space", 0x41: "A"})
    builder.setupCFF("Test", {}, {n: _charstring() for n in order}, {})
    builder.setupHorizontalMetrics({n: (600, 0) for n in order})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "Test", "styleName": "Regular"})
    builder.setupOS2()
    builder.setupPost()
    builder.save(output)
    raw = output.getvalue()

    cff1 = CffOutlines(TTFont(io.BytesIO(raw)).getTableData("CFF "))
    converted = TTFont(io.BytesIO(raw))
    convertCFFToCFF2(converted)
    buffer = io.BytesIO()
    converted.save(buffer)
    cff2_data = TTFont(io.BytesIO(buffer.getvalue())).getTableData("CFF2")

    cff2 = CffOutlines(cff2_data)
    assert cff2.ok and cff2._is_cff2
    a_cff1 = cff1.outline(2)
    a_cff2 = cff2.outline(2)
    assert a_cff2 and len(a_cff2[0]) == 4
    # Same rounded corner points -> the default instance matches CFF1.
    assert [(round(x), round(y)) for x, y in a_cff1[0]] == [
        (round(x), round(y)) for x, y in a_cff2[0]
    ]
