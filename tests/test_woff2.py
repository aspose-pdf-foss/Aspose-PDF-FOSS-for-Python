"""Tests for the WOFF2 decoder (optional brotli dependency).

Fixtures are produced with fontTools (which uses the same brotli encoder), so
the whole module is skipped when either optional package is unavailable. The
decoder is verified against fontTools acting as an oracle: the reconstructed
glyph outlines must match the original font exactly.
"""

from __future__ import annotations

import io

import pytest

pytest.importorskip("brotli")
fontBuilder = pytest.importorskip("fontTools.fontBuilder")
ttGlyphPen = pytest.importorskip("fontTools.pens.ttGlyphPen")
ttLib = pytest.importorskip("fontTools.ttLib")

from fontTools.ttLib.tables import ttProgram  # noqa: E402
from fontTools.ttLib.tables._g_l_y_f import flagOverlapSimple  # noqa: E402

from aspose_pdf.engine import woff2  # noqa: E402
from aspose_pdf.engine.sfnt import parse_faces  # noqa: E402
from aspose_pdf.engine.woff import decode as decode_woff  # noqa: E402
from aspose_pdf.engine.woff import is_woff, is_woff2  # noqa: E402
from aspose_pdf.font_repository import FileFontSource, MemoryFontSource  # noqa: E402

TTFont = ttLib.TTFont
TTGlyphPen = ttGlyphPen.TTGlyphPen
FontBuilder = fontBuilder.FontBuilder


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _build_ttf(*, family="WoffTwo Sans", hinted=False, overlap=False) -> bytes:
    """A small TrueType font: empty, simple, big-coordinate, and composite glyphs."""
    glyphs = {}
    pen = TTGlyphPen(None)
    glyphs[".notdef"] = pen.glyph()

    pen = TTGlyphPen(None)
    pen.moveTo((100, 0)); pen.lineTo((300, 0)); pen.lineTo((200, 400)); pen.closePath()
    glyphs["A"] = pen.glyph()

    pen = TTGlyphPen(None)  # off-curve points
    pen.moveTo((50, 50)); pen.qCurveTo((200, 300), (350, 50)); pen.closePath()
    glyphs["B"] = pen.glyph()

    pen = TTGlyphPen(None)  # large coordinates -> multi-byte triplets
    pen.moveTo((0, 0)); pen.lineTo((5000, 0)); pen.lineTo((5000, 9000))
    pen.lineTo((0, 9000)); pen.closePath()
    glyphs["D"] = pen.glyph()

    pen = TTGlyphPen(glyphs)  # composite: translated + scaled components
    pen.addComponent("A", (1, 0, 0, 1, 10, 20))
    pen.addComponent("B", (0.5, 0, 0, 0.5, 200, 100))
    glyphs["C"] = pen.glyph()

    order = [".notdef", "A", "B", "C", "D"]
    fb = FontBuilder(unitsPerEm=10000, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap({0x41: "A", 0x42: "B", 0x43: "C", 0x44: "D"})
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics({g: (600, 0) for g in order})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": family, "styleName": "Regular"})
    fb.setupMaxp()
    fb.setupOS2()
    fb.setupPost()
    buf = io.BytesIO()
    fb.save(buf)

    if hinted or overlap:
        font = TTFont(buf)
        if hinted:
            program = ttProgram.Program()
            program.fromBytecode(b"\xb0\x01\x2e")
            font["glyf"]["A"].program = program
        if overlap:
            font["glyf"]["B"].flags[0] |= flagOverlapSimple
        buf = io.BytesIO()
        font.save(buf)
    return buf.getvalue()


def _to_woff2(ttf: bytes) -> bytes:
    font = TTFont(io.BytesIO(ttf))
    font.flavor = "woff2"
    out = io.BytesIO()
    font.save(out)
    return out.getvalue()


def _component_key(component) -> tuple:
    return tuple(
        sorted(
            (k, tuple(v) if isinstance(v, (list, tuple)) else v)
            for k, v in component.__dict__.items()
        )
    )


def _assert_glyphs_match(ttf_bytes: bytes, sfnt_bytes: bytes) -> None:
    """Every glyph in *sfnt_bytes* must equal the corresponding one in *ttf_bytes*."""
    orig = TTFont(io.BytesIO(ttf_bytes))
    got = TTFont(io.BytesIO(sfnt_bytes))
    og, gg = orig["glyf"], got["glyf"]
    assert og.keys() == gg.keys()
    for name in og.keys():
        a, b = og[name], gg[name]
        a.expand(og)
        b.expand(gg)
        assert a.numberOfContours == b.numberOfContours, name
        if a.numberOfContours > 0:
            ca, cb = a.getCoordinates(og), b.getCoordinates(gg)
            assert list(ca[0]) == list(cb[0]), name  # coordinates
            assert ca[1] == cb[1], name  # endPtsOfContours
            assert list(ca[2]) == list(cb[2]), name  # flags
        elif a.numberOfContours < 0:
            assert [_component_key(c) for c in a.components] == [
                _component_key(c) for c in b.components
            ], name


# ---------------------------------------------------------------------------
# Glyf transform reconstruction
# ---------------------------------------------------------------------------


def test_decode_reconstructs_glyph_outlines():
    ttf = _build_ttf()
    out = woff2.decode(_to_woff2(ttf))
    assert out is not None
    _assert_glyphs_match(ttf, out)


def test_decoded_glyf_is_byte_identical_to_fonttools():
    ttf = _build_ttf()
    out = woff2.decode(_to_woff2(ttf))
    orig = TTFont(io.BytesIO(ttf))
    got = TTFont(io.BytesIO(out))
    # Our reconstructed glyf compiles to the same bytes fontTools produces.
    assert orig["glyf"].compile(orig) == got["glyf"].compile(got)
    assert got["head"].indexToLocFormat == 1  # we always emit long loca


def test_decode_preserves_instructions_and_overlap():
    ttf = _build_ttf(hinted=True, overlap=True)
    out = woff2.decode(_to_woff2(ttf))
    assert out is not None
    orig = TTFont(io.BytesIO(ttf))
    got = TTFont(io.BytesIO(out))
    assert (
        orig["glyf"]["A"].program.getBytecode()
        == got["glyf"]["A"].program.getBytecode()
    )
    b = got["glyf"]["B"]
    b.expand(got["glyf"])
    assert b.flags[0] & flagOverlapSimple


# ---------------------------------------------------------------------------
# Names, type, and the public wiring
# ---------------------------------------------------------------------------


def test_parse_faces_unwraps_woff2():
    faces = parse_faces(_to_woff2(_build_ttf(family="Web Two")))
    assert len(faces) == 1
    assert faces[0].family_name == "Web Two"
    assert faces[0].font_type == "TrueType"


def test_public_woff_decode_dispatches_to_woff2():
    woff2_bytes = _to_woff2(_build_ttf())
    # The unified woff.decode() entry point routes wOF2 to the woff2 decoder.
    out = decode_woff(woff2_bytes)
    assert out is not None
    assert not is_woff(out) and not is_woff2(out)


def test_memory_source_woff2_discovered_and_embeddable():
    source = MemoryFontSource(_to_woff2(_build_ttf(family="Mem Two")))
    defs = source.get_font_definitions()
    assert len(defs) == 1
    assert defs[0].family_name == "Mem Two"
    program = defs[0].get_font_bytes()
    assert not is_woff2(program)
    assert parse_faces(program)[0].family_name == "Mem Two"


def test_file_source_woff2_recovers_real_name(tmp_path):
    path = tmp_path / "WebFont.woff2"
    path.write_bytes(_to_woff2(_build_ttf(family="Real Two")))
    defs = FileFontSource(path).get_font_definitions()
    assert len(defs) == 1
    assert defs[0].family_name == "Real Two"  # not the "WebFont" stem fallback


# ---------------------------------------------------------------------------
# Graceful degradation + defensiveness
# ---------------------------------------------------------------------------


def test_falls_back_when_brotli_missing(monkeypatch):
    monkeypatch.setattr(woff2, "_import_brotli", lambda: None)
    woff2_bytes = _to_woff2(_build_ttf())
    assert woff2.decode(woff2_bytes) is None
    assert decode_woff(woff2_bytes) is None
    assert parse_faces(woff2_bytes) == []  # falls back to no faces


def test_decode_is_defensive():
    assert woff2.decode(b"not woff2 at all") is None
    assert woff2.decode(b"wOF2") is None  # too short for a header
    truncated = _to_woff2(_build_ttf())[:60]
    assert woff2.decode(truncated) is None

    corrupt = bytearray(_to_woff2(_build_ttf()))
    corrupt[12:14] = b"\xff\xff"  # numTables overruns the directory
    assert woff2.decode(bytes(corrupt)) is None
