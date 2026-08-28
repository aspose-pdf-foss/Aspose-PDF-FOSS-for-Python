"""Complex-script shaping, bidi, fallback, and line-layout tests."""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

pytest.importorskip("fontTools")
pytest.importorskip("uharfbuzz")
pytest.importorskip("bidi")

from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

from aspose_pdf import Document, TextLayoutOptions
from aspose_pdf.engine.content_stream_parser import (
    parse_to_unicode_cmap,
)
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfStream,
)
from aspose_pdf.engine.font_authoring import prepare_authored_font
from aspose_pdf.engine.text_layout import layout_text
from aspose_pdf.exceptions import (
    FontEmbeddingException,
    PdfValidationException,
)


def _empty_glyph():
    return TTGlyphPen(None).glyph()


def _box_glyph(index: int):
    pen = TTGlyphPen(None)
    inset = 55 + index % 5 * 18
    height = 560 + index % 4 * 55
    pen.moveTo((inset, 0))
    pen.lineTo((600 - inset, 0))
    pen.lineTo((600 - inset, height))
    pen.lineTo((inset, height))
    pen.closePath()
    return pen.glyph()


def _glyph_name(character: str) -> str:
    codepoint = ord(character)
    return f"uni{codepoint:04X}" if codepoint <= 0xFFFF else f"u{codepoint:X}"


def _build_layout_font(
    characters: str,
    *,
    family: str,
    complex_features: bool = False,
) -> bytes:
    cmap = {ord(" "): "space"}
    glyph_order = [".notdef", "space"]
    glyphs = {".notdef": _empty_glyph(), "space": _empty_glyph()}
    metrics = {".notdef": (600, 0), "space": (300, 0)}
    for index, character in enumerate(dict.fromkeys(characters), start=1):
        if character == " ":
            continue
        name = _glyph_name(character)
        cmap[ord(character)] = name
        glyph_order.append(name)
        glyphs[name] = _box_glyph(index)
        metrics[name] = (600, 0)

    if complex_features:
        glyph_order.append("fi")
        glyphs["fi"] = _box_glyph(50)
        metrics["fi"] = (900, 0)
        glyph_order.append("deva_ligature")
        glyphs["deva_ligature"] = _box_glyph(51)
        metrics["deva_ligature"] = (900, 0)
        for character in "سلام":
            base = _glyph_name(character)
            for suffix in ("init", "medi", "fina"):
                name = f"{base}.{suffix}"
                if name not in glyphs:
                    glyph_order.append(name)
                    glyphs[name] = _box_glyph(len(glyph_order))
                    metrics[name] = (600, 0)

    builder = FontBuilder(unitsPerEm=1000, isTTF=True)
    builder.setupGlyphOrder(glyph_order)
    builder.setupCharacterMap(cmap)
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics(metrics)
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
    if complex_features:
        seen = _glyph_name("س")
        lam = _glyph_name("ل")
        alef = _glyph_name("ا")
        meem = _glyph_name("م")
        latin_f = _glyph_name("f")
        latin_i = _glyph_name("i")
        latin_a = _glyph_name("A")
        latin_v = _glyph_name("V")
        deva_ka = _glyph_name("क")
        deva_ssa = _glyph_name("ष")
        features = f"""
            languagesystem DFLT dflt;
            languagesystem latn dflt;
            languagesystem arab dflt;
            languagesystem deva dflt;
            feature liga {{ sub {latin_f} {latin_i} by fi; }} liga;
            feature kern {{ pos {latin_a} {latin_v} -200; }} kern;
            feature rlig {{
                script deva;
                language dflt;
                sub {deva_ka} {deva_ssa} by deva_ligature;
            }} rlig;
            feature init {{
                sub {seen} by {seen}.init;
                sub {lam} by {lam}.init;
                sub {meem} by {meem}.init;
            }} init;
            feature medi {{
                sub {seen} by {seen}.medi;
                sub {lam} by {lam}.medi;
                sub {meem} by {meem}.medi;
            }} medi;
            feature fina {{
                sub {seen} by {seen}.fina;
                sub {lam} by {lam}.fina;
                sub {alef} by {alef}.fina;
                sub {meem} by {meem}.fina;
            }} fina;
        """
        addOpenTypeFeaturesFromString(builder.font, features)
    builder.font.recalcTimestamp = False
    builder.font["head"].created = 2082844800
    builder.font["head"].modified = 2082844800
    output = io.BytesIO()
    builder.save(output)
    return output.getvalue()


@pytest.fixture(scope="module")
def layout_font() -> bytes:
    return _build_layout_font(
        " ABCVfioce123سلامकष", family="Codex Layout", complex_features=True
    )


@pytest.fixture(scope="module")
def fallback_font() -> bytes:
    return _build_layout_font(" Ω", family="Codex Fallback")


def _save(document: Document) -> bytes:
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _font_graphs(document: Document) -> list[dict]:
    engine = document._engine_pdf
    page = engine._get_page_dict(0)
    resources = engine._resolve_resources_cos(page)
    fonts = engine._resolve(resources.mapping.get(PdfName("Font")))
    assert isinstance(fonts, PdfDictionary)
    result = []
    for resource_name, font_ref in fonts.mapping.items():
        font = engine._resolve(font_ref)
        descendants = engine._resolve(font.mapping.get(PdfName("DescendantFonts")))
        assert isinstance(descendants, PdfArray)
        cid_font = engine._resolve(descendants.items[0])
        cid_to_gid_ref = cid_font.mapping.get(PdfName("CIDToGIDMap"))
        cid_to_gid = engine._resolve(cid_to_gid_ref)
        assert isinstance(cid_to_gid, PdfStream)
        to_unicode_ref = font.mapping.get(PdfName("ToUnicode"))
        to_unicode = engine._resolve(to_unicode_ref)
        assert isinstance(to_unicode, PdfStream)
        result.append(
            {
                "name": resource_name.name.lstrip("/"),
                "to_unicode": parse_to_unicode_cmap(
                    engine._decode_cos_stream(to_unicode, to_unicode_ref)
                ),
                "cid_to_gid": engine._decode_cos_stream(
                    cid_to_gid, cid_to_gid_ref
                ),
            }
        )
    return result


def test_layout_options_validate_and_normalize() -> None:
    options = TextLayoutOptions(
        direction="RTL",
        alignment="CENTER",
        features={"liga": True},
        fallback_fonts=[b"font"],
        max_width=120,
    )
    assert options.direction == "rtl"
    assert options.alignment == "center"
    assert options.fallback_fonts == (b"font",)
    assert options.max_width == 120.0
    with pytest.raises(ValueError, match="direction"):
        TextLayoutOptions(direction="sideways")
    with pytest.raises(ValueError, match="positive"):
        TextLayoutOptions(max_width=0)
    with pytest.raises(ValueError, match="finite"):
        TextLayoutOptions(line_height=float("nan"))


def test_harfbuzz_applies_ligatures_and_kerning(layout_font: bytes) -> None:
    authored = prepare_authored_font(layout_font)
    default = layout_text(
        "office AV", [authored], TextLayoutOptions(), font_size=20
    ).lines[0]
    no_features = layout_text(
        "office AV",
        [authored],
        TextLayoutOptions(features={"liga": False, "kern": False}),
        font_size=20,
    ).lines[0]

    assert len(default.glyphs) == len("office AV") - 1
    assert len(no_features.glyphs) == len("office AV")
    default_av = layout_text(
        "AV", [authored], TextLayoutOptions(), font_size=20
    ).lines[0]
    plain_av = layout_text(
        "AV",
        [authored],
        TextLayoutOptions(features={"kern": False}),
        font_size=20,
    ).lines[0]
    assert default_av.width < plain_av.width


def test_layout_itemizes_scripts_within_one_bidi_run(layout_font: bytes) -> None:
    authored = prepare_authored_font(layout_font)
    line = layout_text(
        "Aकष", [authored], TextLayoutOptions(), font_size=20
    ).lines[0]
    forced_latin = layout_text(
        "Aकष",
        [authored],
        TextLayoutOptions(script="latn"),
        font_size=20,
    ).lines[0]

    assert len(line.glyphs) == 2
    assert len(forced_latin.glyphs) == 3


def test_bidi_isolate_controls_raise_clear_error(layout_font: bytes) -> None:
    authored = prepare_authored_font(layout_font)
    with pytest.raises(PdfValidationException, match="isolate controls"):
        layout_text(
            "A\u2067سلام\u2069",
            [authored],
            TextLayoutOptions(),
            font_size=20,
        )


def test_arabic_shaping_roundtrips_and_renders(layout_font: bytes) -> None:
    text = "سلام"
    document = Document()
    page = document.pages.add()
    page.add_text(
        text,
        72,
        700,
        font_size=30,
        font=layout_font,
        layout=TextLayoutOptions(direction="rtl", script="arab", language="ar"),
    )

    data = _save(document)
    loaded = Document().load_from(data)
    content = loaded.pages[0].content
    graph = _font_graphs(loaded)[0]
    base = prepare_authored_font(layout_font)
    base_gids = {base.glyph_id(character) for character in text}
    encoded_cids = [
        int(value, 16) for value in re.findall(rb"<([0-9A-F]{4})> Tj", content)
    ]
    rendered_gids = {
        int.from_bytes(
            graph["cid_to_gid"][cid * 2 : cid * 2 + 2], "big"
        )
        for cid in encoded_cids
    }

    assert b"/Span /AT" in content
    assert rendered_gids - base_gids
    assert loaded._engine_pdf.extract_text() == text
    assert loaded.tagged_content.root_elements[0].actual_text == text
    raster = loaded.pages[0].render(antialias=False)
    ink = sum(
        raster.get_pixel(x, y) != (255, 255, 255)
        for y in range(60, 110)
        for x in range(65, 160)
    )
    assert ink > 50


def test_mixed_bidi_visual_order_keeps_logical_extraction(layout_font: bytes) -> None:
    text = "ABC سلام 123"
    authored = prepare_authored_font(layout_font)
    line = layout_text(
        text, [authored], TextLayoutOptions(), font_size=18
    ).lines[0]
    visual_clusters = "".join(glyph.unicode_text for glyph in line.glyphs)
    assert visual_clusters != text

    document = Document()
    document.pages.add().add_text(
        text,
        40,
        700,
        font=layout_font,
        layout=TextLayoutOptions(),
    )
    assert Document().load_from(_save(document))._engine_pdf.extract_text() == text


def test_font_fallback_switches_resources(layout_font: bytes, fallback_font: bytes) -> None:
    text = "AΩV"
    document = Document()
    page = document.pages.add()
    before = page.content
    with pytest.raises(FontEmbeddingException, match=r"U\+03A9"):
        page.add_text(
            text,
            40,
            700,
            font=layout_font,
            layout=TextLayoutOptions(),
        )
    assert page.content == before

    page.add_text(
        text,
        40,
        700,
        font=layout_font,
        layout=TextLayoutOptions(fallback_fonts=[fallback_font]),
    )
    loaded = Document().load_from(_save(document))
    assert len(_font_graphs(loaded)) == 2
    assert len(set(re.findall(rb"/(F\d+) [0-9.]+ Tf", loaded.pages[0].content))) == 2
    assert loaded._engine_pdf.extract_text() == text


def test_wrapping_line_height_and_center_alignment(layout_font: bytes) -> None:
    text = "AV AV AV"
    document = Document()
    document.pages.add().add_text(
        text,
        50,
        700,
        font_size=20,
        font=layout_font,
        layout=TextLayoutOptions(
            max_width=38,
            line_height=28,
            alignment="center",
        ),
    )
    loaded = Document().load_from(_save(document))
    content = loaded.pages[0].content
    matrices = re.findall(
        rb"1 0 0 1 ([0-9.]+) ([0-9.]+) Tm", content
    )
    baselines = {float(y) for _x, y in matrices}

    assert len(re.findall(rb"/Span /AT\d+ BDC", content)) >= 3
    assert {700.0, 672.0, 644.0} <= baselines
    assert float(matrices[0][0]) > 50.0
    assert loaded._engine_pdf.extract_text() == text


def test_layout_requires_optional_dependency(
    layout_font: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aspose_pdf.engine.text_layout as engine_layout

    def unavailable():
        raise PdfValidationException("requires the optional 'text-layout' extra")

    monkeypatch.setattr(engine_layout, "_load_dependencies", unavailable)
    document = Document()
    page = document.pages.add()
    before = page.content
    with pytest.raises(PdfValidationException, match="text-layout"):
        page.add_text(
            "Text",
            40,
            700,
            font=layout_font,
            layout=TextLayoutOptions(),
        )
    assert page.content == before


def test_layout_accepts_fallback_font_paths(
    layout_font: bytes, fallback_font: bytes, tmp_path: Path
) -> None:
    fallback_path = tmp_path / "fallback.ttf"
    fallback_path.write_bytes(fallback_font)
    document = Document()
    document.pages.add().add_text(
        "AΩ",
        40,
        700,
        font=layout_font,
        layout=TextLayoutOptions(fallback_fonts=[fallback_path]),
    )
    assert Document().load_from(_save(document))._engine_pdf.extract_text() == "AΩ"
