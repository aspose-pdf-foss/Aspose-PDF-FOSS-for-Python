"""Acceptance tests for Unicode text authoring with embedded fonts."""

from __future__ import annotations

import io
import re
import struct
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fontTools")

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

from aspose_pdf import (
    Document,
    FontDescriptor,
    FontEmbeddingException,
    MemoryFontSource,
    OptimizationOptions,
    PdfExtractor,
)
from aspose_pdf.engine.content_stream_parser import (
    parse_to_unicode_cmap,
)
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfStream,
)
from aspose_pdf.exceptions import PdfValidationException

_SCRIPT_TEXT = "ASCII Č Привет Ж Ω 中漢字 🙂"
_RENDER_TEXT = "ČЖΩ中"
_FONT_ERRORS = (FontEmbeddingException, PdfValidationException)


def _empty_glyph():
    return TTGlyphPen(None).glyph()


def _box_glyph(index: int):
    """Return a visible, slightly varied rectangular TrueType glyph."""

    inset = 80 + (index % 4) * 25
    top = 620 + (index % 3) * 60
    pen = TTGlyphPen(None)
    pen.moveTo((inset, 0))
    pen.lineTo((820 - inset, 0))
    pen.lineTo((820 - inset, top))
    pen.lineTo((inset, top))
    pen.closePath()
    return pen.glyph()


def _glyph_name(codepoint: int) -> str:
    if codepoint <= 0xFFFF:
        return f"uni{codepoint:04X}"
    return f"u{codepoint:X}"


def _build_unicode_ttf() -> bytes:
    """Build a deterministic TrueType fixture without external font files."""

    codepoints = set(range(0x20, 0x7F))
    codepoints.update(ord(char) for char in "ČЖПриветΩ中漢字🙂")

    cmap: dict[int, str] = {0x20: "space", 0xA0: "space"}
    glyph_order = [".notdef", "space"]
    glyphs = {".notdef": _empty_glyph(), "space": _empty_glyph()}
    metrics = {".notdef": (600, 0), "space": (320, 0)}

    for index, codepoint in enumerate(sorted(codepoints - {0x20}), start=1):
        name = _glyph_name(codepoint)
        cmap[codepoint] = name
        glyph_order.append(name)
        glyphs[name] = _box_glyph(index)
        metrics[name] = (500 + (index % 5) * 70, 0)

    builder = FontBuilder(unitsPerEm=1000, isTTF=True)
    builder.setupGlyphOrder(glyph_order)
    builder.setupCharacterMap(cmap)
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics(metrics)
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable(
        {
            "familyName": "Codex Unicode Fixture",
            "styleName": "Regular",
            "uniqueFontIdentifier": "CodexUnicodeFixture-Regular-1.0",
            "fullName": "Codex Unicode Fixture Regular",
            "psName": "CodexUnicodeFixture-Regular",
            "version": "Version 1.0",
        }
    )
    builder.setupMaxp()
    builder.setupOS2(
        sTypoAscender=800,
        sTypoDescender=-200,
        usWinAscent=800,
        usWinDescent=200,
    )
    builder.setupPost()
    builder.font.recalcTimestamp = False
    builder.font["head"].created = 2082844800
    builder.font["head"].modified = 2082844800

    output = io.BytesIO()
    builder.save(output)
    return output.getvalue()


@pytest.fixture(scope="module")
def unicode_ttf() -> bytes:
    return _build_unicode_ttf()


def _save(document: Document) -> bytes:
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _extract(data: bytes) -> str:
    with PdfExtractor() as extractor:
        extractor.bind_pdf(data)
        extractor.extract_text()
        return extractor.get_text()


def _load(data: bytes) -> Document:
    document = Document()
    document.load_from(data)
    return document


def _font_graph(document: Document, page_index: int = 0) -> dict[str, Any]:
    """Resolve the authored Type0 font graph for structural assertions."""

    engine = document._engine_pdf
    page = engine._get_page_dict(page_index)
    assert isinstance(page, PdfDictionary)
    resources = engine._resolve_resources_cos(page)
    assert isinstance(resources, PdfDictionary)
    fonts = engine._resolve(resources.mapping.get(PdfName("Font")))
    assert isinstance(fonts, PdfDictionary)
    assert fonts.mapping

    font_ref = next(iter(fonts.mapping.values()))
    font = engine._resolve(font_ref)
    assert isinstance(font, PdfDictionary)
    descendants = engine._resolve(font.mapping.get(PdfName("DescendantFonts")))
    assert isinstance(descendants, PdfArray)
    assert len(descendants.items) == 1
    cid_font = engine._resolve(descendants.items[0])
    assert isinstance(cid_font, PdfDictionary)
    descriptor = engine._resolve(cid_font.mapping.get(PdfName("FontDescriptor")))
    assert isinstance(descriptor, PdfDictionary)

    to_unicode_ref = font.mapping.get(PdfName("ToUnicode"))
    to_unicode = engine._resolve(to_unicode_ref)
    assert isinstance(to_unicode, PdfStream)
    cmap_bytes = engine._decode_cos_stream(to_unicode, to_unicode_ref)

    cid_to_gid_ref = cid_font.mapping.get(PdfName("CIDToGIDMap"))
    cid_to_gid = engine._resolve(cid_to_gid_ref)
    assert isinstance(cid_to_gid, PdfStream)
    cid_to_gid_bytes = engine._decode_cos_stream(cid_to_gid, cid_to_gid_ref)

    font_file_ref = descriptor.mapping.get(PdfName("FontFile2"))
    font_file = engine._resolve(font_file_ref)
    assert isinstance(font_file, PdfStream)
    font_bytes = engine._decode_cos_stream(font_file, font_file_ref)

    return {
        "engine": engine,
        "fonts": fonts,
        "font": font,
        "cid_font": cid_font,
        "descriptor": descriptor,
        "to_unicode": parse_to_unicode_cmap(cmap_bytes),
        "cid_to_gid": cid_to_gid_bytes,
        "font_bytes": font_bytes,
    }


def _font_input(kind: str, font_bytes: bytes, tmp_path: Path):
    if kind == "bytes":
        return font_bytes
    if kind == "bytearray":
        return bytearray(font_bytes)
    if kind in ("path", "string-path"):
        path = tmp_path / "unicode-fixture.ttf"
        path.write_bytes(font_bytes)
        return path if kind == "path" else str(path)
    if kind == "descriptor":
        definitions = MemoryFontSource(font_bytes).get_font_definitions()
        assert len(definitions) == 1
        return definitions[0]
    raise AssertionError(f"Unknown font input kind: {kind}")


@pytest.mark.parametrize(
    "kind",
    ["bytes", "bytearray", "path", "string-path", "descriptor"],
)
def test_add_text_accepts_supported_embedded_font_inputs(
    kind: str,
    unicode_ttf: bytes,
    tmp_path: Path,
) -> None:
    document = Document()
    page = document.pages.add()
    page.add_text(
        _SCRIPT_TEXT,
        40,
        700,
        font_size=18,
        font=_font_input(kind, unicode_ttf, tmp_path),
    )

    assert _extract(_save(document)) == _SCRIPT_TEXT


def test_unicode_font_bytes_write_complete_type0_font_graph(
    unicode_ttf: bytes,
) -> None:
    document = Document()
    page = document.pages.add()
    page.add_text(_SCRIPT_TEXT, 40, 700, font_size=18, font=unicode_ttf)

    loaded = _load(_save(document))
    graph = _font_graph(loaded)
    font = graph["font"]
    cid_font = graph["cid_font"]

    assert graph["engine"]._get_name(font.mapping.get(PdfName("Subtype"))) == "Type0"
    assert graph["engine"]._get_name(font.mapping.get(PdfName("Encoding"))) == "Identity-H"
    assert (
        graph["engine"]._get_name(cid_font.mapping.get(PdfName("Subtype")))
        == "CIDFontType2"
    )
    assert isinstance(
        graph["engine"]._resolve(cid_font.mapping.get(PdfName("W"))), PdfArray
    )
    assert graph["font_bytes"][:4] in (b"\x00\x01\x00\x00", b"true")

    mapped_text = set(graph["to_unicode"].values())
    assert set(_SCRIPT_TEXT) <= mapped_text
    assert graph["cid_to_gid"]
    assert len(graph["cid_to_gid"]) % 2 == 0

    content = loaded.pages[0].content
    operands = re.findall(rb"<([0-9A-Fa-f]+)>\s*Tj", content)
    assert operands
    assert all(len(operand) % 4 == 0 for operand in operands)
    assert _SCRIPT_TEXT.encode("utf-8") not in content


def test_unicode_embedded_font_renders_after_reload(unicode_ttf: bytes) -> None:
    document = Document()
    page = document.pages.add()
    baselines = (700, 640, 580, 520)
    for text, baseline in zip(_RENDER_TEXT, baselines):
        page.add_text(text, 72, baseline, font_size=30, font=unicode_ttf)

    loaded = _load(_save(document))
    assert _extract(_save(document)) == _RENDER_TEXT
    raster = loaded.pages[0].render(antialias=False)

    for baseline in baselines:
        x0, x1 = 68, 105
        y0 = max(0, int(raster.height - baseline - 35))
        y1 = min(raster.height, int(raster.height - baseline + 8))
        ink = sum(
            raster.get_pixel(x, y) != (255, 255, 255)
            for y in range(y0, y1)
            for x in range(x0, x1)
        )
        assert ink > 20


def test_repeated_calls_reuse_font_and_extend_to_unicode(unicode_ttf: bytes) -> None:
    document = Document()
    page = document.pages.add()
    page.add_text("Č", 40, 700, font=unicode_ttf)
    page.add_text("ЖΩ中", 40, 660, font=unicode_ttf)

    data = _save(document)
    loaded = _load(data)
    graph = _font_graph(loaded)

    assert len(graph["fonts"].mapping) == 1
    assert set(_RENDER_TEXT) <= set(graph["to_unicode"].values())
    assert _extract(data) == _RENDER_TEXT


def test_unicode_authoring_continues_after_stream_compression(
    unicode_ttf: bytes,
) -> None:
    document = Document()
    page = document.pages.add()
    page.add_text("Č", 40, 700, font_size=18, font=unicode_ttf)

    document.optimize(OptimizationOptions(compress_fonts=True))
    page.add_text("ЖΩ中", 60, 700, font_size=18, font=unicode_ttf)

    data = _save(document)
    loaded = _load(data)
    assert _extract(data) == _RENDER_TEXT

    raster = loaded.pages[0].render(antialias=False)
    y0 = max(0, raster.height - 700 - 25)
    y1 = min(raster.height, raster.height - 700 + 8)
    ink = sum(
        raster.get_pixel(x, y) != (255, 255, 255)
        for y in range(y0, y1)
        for x in range(35, 145)
    )
    assert ink > 20


def test_unicode_aliases_sharing_one_gid_extract_exactly(unicode_ttf: bytes) -> None:
    text = "A \u00a0A"
    document = Document()
    page = document.pages.add()
    page.add_text(text, 40, 700, font=unicode_ttf)

    data = _save(document)
    loaded = _load(data)
    graph = _font_graph(loaded)
    mapping = graph["to_unicode"]

    space_code = next(code for code, value in mapping.items() if value == " ")
    nbsp_code = next(code for code, value in mapping.items() if value == "\u00a0")
    assert space_code != nbsp_code
    assert len(space_code) == len(nbsp_code) == 2

    cid_to_gid = graph["cid_to_gid"]

    def gid_for(code: bytes) -> int:
        cid = int.from_bytes(code, "big")
        offset = cid * 2
        assert offset + 2 <= len(cid_to_gid)
        return int.from_bytes(cid_to_gid[offset : offset + 2], "big")

    assert gid_for(space_code) == gid_for(nbsp_code)
    assert _extract(data) == text


def test_missing_glyph_raises_without_appending_content(unicode_ttf: bytes) -> None:
    document = Document()
    page = document.pages.add()
    before = page.content

    with pytest.raises(_FONT_ERRORS, match=r"(?i)(glyph|U\+1F9EA|character)"):
        page.add_text("🧪", 40, 700, font=unicode_ttf)

    assert page.content == before


def test_malformed_font_raises_domain_error() -> None:
    document = Document()
    page = document.pages.add()

    with pytest.raises(_FONT_ERRORS, match=r"(?i)font"):
        page.add_text("Text", 40, 700, font=b"not a font")


def test_cmap_records_cannot_overlap_subtable_data(unicode_ttf: bytes) -> None:
    malformed = bytearray(unicode_ttf)
    num_tables = struct.unpack_from(">H", malformed, 4)[0]
    cmap_offset = None
    for index in range(num_tables):
        record = 12 + index * 16
        if malformed[record : record + 4] == b"cmap":
            cmap_offset = struct.unpack_from(">I", malformed, record + 8)[0]
            break
    assert cmap_offset is not None
    record_count = struct.unpack_from(">H", malformed, cmap_offset + 2)[0]
    first_subtable = struct.unpack_from(">I", malformed, cmap_offset + 8)[0]
    assert first_subtable == 4 + record_count * 8
    struct.pack_into(">H", malformed, cmap_offset + 2, record_count + 1)

    document = Document()
    page = document.pages.add()
    before = page.content
    with pytest.raises(FontEmbeddingException, match=r"(?i)cmap"):
        page.add_text("A", 40, 700, font=bytes(malformed))
    assert page.content == before


def test_descriptor_without_font_data_raises_domain_error() -> None:
    document = Document()
    page = document.pages.add()
    descriptor = FontDescriptor("Unavailable", is_standard=False)

    with pytest.raises(_FONT_ERRORS, match=r"(?i)font"):
        page.add_text("Text", 40, 700, font=descriptor)


def test_legacy_standard14_ascii_authoring_is_unchanged() -> None:
    text = "Legacy ASCII"
    document = Document()
    page = document.pages.add()
    page.add_text(text, 40, 700, font_size=18, font_name="Helvetica")

    data = _save(document)
    loaded = _load(data)
    engine = loaded._engine_pdf
    page_dict = engine._get_page_dict(0)
    assert isinstance(page_dict, PdfDictionary)
    resources = engine._resolve_resources_cos(page_dict)
    assert isinstance(resources, PdfDictionary)
    fonts = engine._resolve(resources.mapping.get(PdfName("Font")))
    assert isinstance(fonts, PdfDictionary)
    assert len(fonts.mapping) == 1
    font = engine._resolve(next(iter(fonts.mapping.values())))
    assert isinstance(font, PdfDictionary)

    assert engine._get_name(font.mapping.get(PdfName("Subtype"))) == "Type1"
    assert engine._get_name(font.mapping.get(PdfName("BaseFont"))) == "Helvetica"
    assert PdfName("FontDescriptor") not in font
    assert _extract(data) == text
