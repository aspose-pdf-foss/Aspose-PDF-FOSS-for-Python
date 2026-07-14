"""Acceptance tests for embedded font containers used by text authoring."""

from __future__ import annotations

import io
from typing import Any

import pytest

pytest.importorskip("fontTools")

from fontTools.fontBuilder import FontBuilder  # noqa: E402
from fontTools.pens.t2CharStringPen import T2CharStringPen  # noqa: E402
from fontTools.pens.ttGlyphPen import TTGlyphPen  # noqa: E402
from fontTools.ttLib import TTCollection, TTFont  # noqa: E402

from aspose_pdf import Document, FontDescriptor, PdfExtractor  # noqa: E402
from aspose_pdf.engine.cos import (  # noqa: E402
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfStream,
)
from aspose_pdf.exceptions import FontEmbeddingException  # noqa: E402


def _save(document: Document) -> bytes:
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _load(data: bytes) -> Document:
    document = Document()
    document.load_from(data)
    return document


def _extract(data: bytes) -> str:
    with PdfExtractor() as extractor:
        extractor.bind_pdf(data)
        extractor.extract_text()
        return extractor.get_text()


def _box_ttf_glyph(inset: int = 80):
    pen = TTGlyphPen(None)
    pen.moveTo((inset, 0))
    pen.lineTo((800 - inset, 0))
    pen.lineTo((800 - inset, 700))
    pen.lineTo((inset, 700))
    pen.closePath()
    return pen.glyph()


def _build_ttf(family: str, codepoint: int, glyph_name: str, inset: int) -> bytes:
    builder = FontBuilder(unitsPerEm=1000, isTTF=True)
    builder.setupGlyphOrder([".notdef", glyph_name])
    builder.setupCharacterMap({codepoint: glyph_name})
    builder.setupGlyf(
        {
            ".notdef": TTGlyphPen(None).glyph(),
            glyph_name: _box_ttf_glyph(inset),
        }
    )
    builder.setupHorizontalMetrics({".notdef": (600, 0), glyph_name: (800, 0)})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable(
        {
            "familyName": family,
            "styleName": "Regular",
            "uniqueFontIdentifier": f"{family}-Regular-1.0",
            "fullName": f"{family} Regular",
            "psName": f"{family.replace(' ', '')}-Regular",
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
    output = io.BytesIO()
    builder.save(output)
    return output.getvalue()


def _cff_charstring(*, cff2: bool, inset: int = 80):
    pen = T2CharStringPen(None if cff2 else 700, None, CFF2=cff2)
    pen.moveTo((inset, 0))
    pen.lineTo((700 - inset, 0))
    pen.lineTo((350, 700))
    pen.closePath()
    return pen.getCharString()


def _build_cff_font(*, cff2: bool = False) -> bytes:
    glyph_order = [".notdef", "A", "Omega"]
    charstrings = {
        name: _cff_charstring(cff2=cff2, inset=80 + index * 20)
        for index, name in enumerate(glyph_order)
    }
    builder = FontBuilder(unitsPerEm=1000, isTTF=False)
    builder.setupGlyphOrder(glyph_order)
    builder.setupCharacterMap({0x41: "A", 0x03A9: "Omega"})
    if cff2:
        builder.setupCFF2(charstrings)
    else:
        builder.setupCFF("FormatCFF", {}, charstrings, {})
    builder.setupHorizontalMetrics({name: (700, 0) for name in glyph_order})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable(
        {
            "familyName": "Format CFF",
            "styleName": "Regular",
            "uniqueFontIdentifier": "FormatCFF-Regular-1.0",
            "fullName": "Format CFF Regular",
            "psName": "FormatCFF-Regular",
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
    output = io.BytesIO()
    builder.save(output)
    return output.getvalue()


def _build_ttc() -> bytes:
    first = TTFont(io.BytesIO(_build_ttf("Collection First", 0x41, "A", 80)))
    second = TTFont(
        io.BytesIO(_build_ttf("Collection Second", 0x0416, "uni0416", 180))
    )
    collection = TTCollection()
    collection.fonts = [first, second]
    output = io.BytesIO()
    collection.save(output)
    first.close()
    second.close()
    return output.getvalue()


def _font_graph(
    document: Document,
    font_file_key: str,
) -> tuple[PdfDictionary, PdfDictionary, PdfDictionary, PdfStream, bytes]:
    engine = document._engine_pdf
    page = engine._get_page_dict(0)
    assert isinstance(page, PdfDictionary)
    resources = engine._resolve_resources_cos(page)
    assert isinstance(resources, PdfDictionary)
    fonts = engine._resolve(resources.mapping.get(PdfName("Font")))
    assert isinstance(fonts, PdfDictionary)
    assert len(fonts.mapping) == 1

    font = engine._resolve(next(iter(fonts.mapping.values())))
    assert isinstance(font, PdfDictionary)
    descendants = engine._resolve(font.mapping.get(PdfName("DescendantFonts")))
    assert isinstance(descendants, PdfArray)
    assert len(descendants.items) == 1
    cid_font = engine._resolve(descendants.items[0])
    assert isinstance(cid_font, PdfDictionary)
    descriptor = engine._resolve(cid_font.mapping.get(PdfName("FontDescriptor")))
    assert isinstance(descriptor, PdfDictionary)
    file_ref = descriptor.mapping.get(PdfName(font_file_key))
    font_file = engine._resolve(file_ref)
    assert isinstance(font_file, PdfStream)
    program = engine._decode_cos_stream(font_file, file_ref)
    return font, cid_font, descriptor, font_file, program


def _name(engine: Any, dictionary: PdfDictionary, key: str) -> str | None:
    return engine._get_name(dictionary.mapping.get(PdfName(key)))


def _assert_visible(document: Document, *, x: int = 72, baseline: int = 700) -> None:
    raster = document.pages[0].render(antialias=False)
    y0 = max(0, raster.height - baseline - 45)
    y1 = min(raster.height, raster.height - baseline + 10)
    ink = sum(
        raster.get_pixel(px, py) != (255, 255, 255)
        for py in range(y0, y1)
        for px in range(x - 5, x + 100)
    )
    assert ink > 20


def test_opentype_cff1_authors_extracts_and_renders_after_reload() -> None:
    text = "AΩ"
    document = Document()
    document.pages.add().add_text(
        text,
        72,
        700,
        font_size=36,
        font=_build_cff_font(),
    )

    data = _save(document)
    loaded = _load(data)
    font, cid_font, _descriptor, font_file, program = _font_graph(
        loaded, "FontFile3"
    )
    engine = loaded._engine_pdf

    assert _extract(data) == text
    assert _name(engine, font, "Subtype") == "Type0"
    assert _name(engine, cid_font, "Subtype") == "CIDFontType0"
    assert _name(engine, font_file, "Subtype") == "CIDFontType0C"
    assert PdfName("CIDToGIDMap") not in cid_font
    assert program.startswith(b"\x01")
    _assert_visible(loaded)


def test_ttc_descriptor_selects_requested_face_for_authoring() -> None:
    text = "Ж"
    descriptor = FontDescriptor(
        "Collection Second",
        is_standard=False,
        face_index=1,
        data=_build_ttc(),
    )
    document = Document()
    document.pages.add().add_text(
        text,
        72,
        700,
        font_size=36,
        font=descriptor,
    )

    data = _save(document)
    loaded = _load(data)
    font, cid_font, _descriptor, _font_file, program = _font_graph(
        loaded, "FontFile2"
    )
    engine = loaded._engine_pdf

    assert _extract(data) == text
    assert _name(engine, cid_font, "Subtype") == "CIDFontType2"
    assert "CollectionSecond-Regular" in (
        _name(engine, font, "BaseFont") or ""
    )
    assert program[:4] in (b"\x00\x01\x00\x00", b"true")
    with TTFont(io.BytesIO(program)) as selected:
        cmap = selected.getBestCmap()
        assert 0x0416 in cmap
        assert 0x41 not in cmap
    _assert_visible(loaded)


def test_woff1_bytes_are_unwrapped_before_embedding() -> None:
    raw = _build_ttf("WOFF Fixture", 0x03A9, "Omega", 120)
    font = TTFont(io.BytesIO(raw))
    font.flavor = "woff"
    wrapper = io.BytesIO()
    font.save(wrapper)
    font.close()

    document = Document()
    document.pages.add().add_text("Ω", 72, 700, font_size=36, font=wrapper.getvalue())
    data = _save(document)
    loaded = _load(data)
    _font, cid_font, _descriptor, _font_file, program = _font_graph(
        loaded, "FontFile2"
    )

    assert _extract(data) == "Ω"
    assert _name(loaded._engine_pdf, cid_font, "Subtype") == "CIDFontType2"
    assert program[:4] in (b"\x00\x01\x00\x00", b"true")
    _assert_visible(loaded)


def test_opentype_cff2_is_rejected_explicitly() -> None:
    document = Document()
    page = document.pages.add()

    with pytest.raises(FontEmbeddingException, match="CFF2"):
        page.add_text("A", 72, 700, font=_build_cff_font(cff2=True))
