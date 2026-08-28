"""Shaping and bidi on the text edit and substitute-render paths.

A right-to-left or complex-script replacement is shaped (HarfBuzz + Unicode
bidi) instead of encoded code-for-code: reused in the run's own embedded font
when it already carries every shaped glyph, otherwise drawn with a freshly
embedded ``font=``. RTL phrases are also matched in their stored visual order.
Substitute-face rendering joins complex-script runs in place. Everything needs
the optional ``text-layout`` extra, so the whole module skips without it.
"""

from __future__ import annotations

import io

import pytest

pytest.importorskip("uharfbuzz")
pytest.importorskip("bidi")
pytest.importorskip("fontTools")

from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

from aspose_pdf import Document
from aspose_pdf.engine.text_edit import (
    Reshaper,
    _shape_replacement_codes,
    replace_text_in_content,
)
from aspose_pdf.engine.text_layout import (
    needs_shaping,
    shape_join_preserving,
)
from aspose_pdf.engine.text_locate import CompositeFontMetric
from aspose_pdf.exceptions import FontEmbeddingException
from aspose_pdf.facades import PdfExtractor
from aspose_pdf.load_limits import PdfLoadLimits, _LoadBudget
from aspose_pdf.text_layout import TextLayoutOptions

# Arabic letters used across the tests (logical order runs right-to-left).
SEEN, LAM, ALEF, MEEM, AIN = "س", "ل", "ا", "م", "ع"


def _empty_glyph():
    return TTGlyphPen(None).glyph()


def _box_glyph():
    pen = TTGlyphPen(None)
    pen.moveTo((50, 0))
    pen.lineTo((550, 0))
    pen.lineTo((550, 600))
    pen.lineTo((50, 600))
    pen.closePath()
    return pen.glyph()


def _glyph_name(character: str) -> str:
    codepoint = ord(character)
    return f"uni{codepoint:04X}" if codepoint <= 0xFFFF else f"u{codepoint:X}"


def _build_arabic_font(characters: str, *, family: str = "Reshape Test") -> bytes:
    """Build a TrueType font with Arabic init/medi/fina joining substitutions."""
    cmap = {ord(" "): "space"}
    glyph_order = [".notdef", "space"]
    glyphs = {".notdef": _empty_glyph(), "space": _empty_glyph()}
    metrics = {".notdef": (600, 0), "space": (300, 0)}
    for character in dict.fromkeys(characters):
        if character == " ":
            continue
        name = _glyph_name(character)
        cmap[ord(character)] = name
        glyph_order.append(name)
        glyphs[name] = _box_glyph()
        metrics[name] = (600, 0)
    joining = [c for c in (SEEN, LAM, MEEM, AIN) if c in characters]
    for character in [c for c in (SEEN, LAM, ALEF, MEEM, AIN) if c in characters]:
        for suffix in ("init", "medi", "fina"):
            name = f"{_glyph_name(character)}.{suffix}"
            glyph_order.append(name)
            glyphs[name] = _box_glyph()
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
            "uniqueFontIdentifier": f"{family}-1.0",
            "fullName": family,
            "psName": family.replace(" ", ""),
            "version": "1.0",
        }
    )
    builder.setupMaxp()
    builder.setupOS2()
    builder.setupPost()
    init = " ".join(
        f"sub {_glyph_name(c)} by {_glyph_name(c)}.init;" for c in joining
    )
    medi = " ".join(
        f"sub {_glyph_name(c)} by {_glyph_name(c)}.medi;" for c in joining
    )
    fina_chars = [c for c in (SEEN, LAM, ALEF, MEEM, AIN) if c in characters]
    fina = " ".join(
        f"sub {_glyph_name(c)} by {_glyph_name(c)}.fina;" for c in fina_chars
    )
    addOpenTypeFeaturesFromString(
        builder.font,
        f"""
        languagesystem DFLT dflt;
        languagesystem arab dflt;
        feature init {{ {init} }} init;
        feature medi {{ {medi} }} medi;
        feature fina {{ {fina} }} fina;
        """,
    )
    builder.font.recalcTimestamp = False
    builder.font["head"].created = builder.font["head"].modified = 2082844800
    output = io.BytesIO()
    builder.save(output)
    return output.getvalue()


def _glyph_order(font_bytes: bytes) -> list[str]:
    from fontTools.ttLib import TTFont

    return TTFont(io.BytesIO(font_bytes)).getGlyphOrder()


def _save(document: Document) -> bytes:
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _extract(data: bytes) -> str:
    with PdfExtractor() as extractor:
        extractor.bind_pdf(data)
        extractor.extract_text()
        return extractor.get_text()


# --- Branch B: reuse the run's own embedded font ----------------------------


def test_shape_replacement_codes_applies_joining() -> None:
    """The reuse branch returns joined (init/medi/fina) glyphs, not isolated."""
    font_bytes = _build_arabic_font(SEEN + LAM + ALEF + MEEM)
    order = _glyph_order(font_bytes)
    metric = CompositeFontMetric(
        width_of=lambda cid: 600.0,
        code_to_text={},
        shaping_program=font_bytes,
        gid_to_cid=lambda gid: gid,
    )
    budget = _LoadBudget(PdfLoadLimits())

    codes = _shape_replacement_codes(SEEN + LAM + ALEF + MEEM, metric, budget)

    assert codes is not None
    gids = [int.from_bytes(codes[i : i + 2], "big") for i in range(0, len(codes), 2)]
    names = [order[gid] for gid in gids]
    # سلام shapes to visual order: meem (isolated, alef breaks the join),
    # alef.fina, lam.medi, seen.init.
    assert names == [
        _glyph_name(MEEM),
        f"{_glyph_name(ALEF)}.fina",
        f"{_glyph_name(LAM)}.medi",
        f"{_glyph_name(SEEN)}.init",
    ]


def test_shape_replacement_codes_none_when_glyph_missing() -> None:
    """A glyph absent from the embedded font falls through (returns None)."""
    font_bytes = _build_arabic_font(SEEN + LAM)  # no meem/alef
    metric = CompositeFontMetric(
        width_of=lambda cid: 600.0,
        code_to_text={},
        shaping_program=font_bytes,
        gid_to_cid=lambda gid: gid,
    )
    budget = _LoadBudget(PdfLoadLimits())
    assert _shape_replacement_codes(SEEN + MEEM + ALEF, metric, budget) is None


def test_shape_replacement_codes_none_without_program() -> None:
    """A font with no shaping program is never reused."""
    metric = CompositeFontMetric(width_of=lambda cid: 600.0, code_to_text={})
    budget = _LoadBudget(PdfLoadLimits())
    assert _shape_replacement_codes(SEEN + LAM, metric, budget) is None


def test_content_editor_reuses_embedded_font_for_rtl_replacement() -> None:
    """replace_text_in_content shapes an RTL replacement into the run's font."""
    font_bytes = _build_arabic_font(SEEN + LAM + ALEF + MEEM + AIN)
    order = _glyph_order(font_bytes)
    name_to_gid = {name: index for index, name in enumerate(order)}
    # The run shows "سلام" as one isolated glyph per letter, in logical order.
    search = SEEN + LAM + ALEF + MEEM
    codes = "".join(
        f"{name_to_gid[_glyph_name(ch)]:04X}" for ch in search
    )
    content = f"BT /F1 20 Tf 100 700 Td <{codes}> Tj ET".encode()
    code_to_text = {
        name_to_gid[_glyph_name(ch)].to_bytes(2, "big"): ch for ch in search
    }
    from aspose_pdf.engine.text_edit import CidTextCodec

    codec = CidTextCodec(code_to_text)
    metric = CompositeFontMetric(
        width_of=lambda cid: 600.0,
        code_to_text=code_to_text,
        shaping_program=font_bytes,
        gid_to_cid=lambda gid: gid,
    )
    reshaper = Reshaper(can_author=False, budget=_LoadBudget(PdfLoadLimits()))

    updated, count = replace_text_in_content(
        content,
        search,
        LAM + ALEF + MEEM,  # replace with "لام"
        codec_for_name=lambda name: codec if name == "F1" else None,
        metric_for_name=lambda name: metric if name == "F1" else None,
        reshaper=reshaper,
    )

    assert count == 1
    assert not reshaper.author_requests  # reuse, not author
    # Extract the new operand and confirm it holds shaped (joined) glyphs.
    hex_codes = updated.split(b"<")[1].split(b">")[0]
    new_gids = [
        int(hex_codes[i : i + 4], 16) for i in range(0, len(hex_codes), 4)
    ]
    names = [order[gid] for gid in new_gids]
    assert any(name.endswith((".init", ".medi", ".fina")) for name in names)
    # The original isolated seen/lam/alef/meem sequence is gone.
    assert names != [_glyph_name(ch) for ch in (LAM, ALEF, MEEM)]


# --- Branch A: author a freshly embedded font -------------------------------


def _authored_arabic_pdf(word: str, font_bytes: bytes) -> bytes:
    document = Document()
    page = document.pages.add()
    page.add_text(word, x=100, y=700, font_size=20, font=font_bytes,
                  layout=TextLayoutOptions())
    return _save(document)


def _font_resource_count(document: Document) -> int:
    from aspose_pdf.engine.cos import PdfDictionary, PdfName

    engine = document._engine_pdf
    page = engine._get_page_dict(0)
    resources = engine._resolve_resources_cos(page)
    fonts = engine._resolve(resources.mapping.get(PdfName("Font")))
    if not isinstance(fonts, PdfDictionary):
        return 0
    return len(fonts.mapping)


def test_replace_authors_new_font_when_reuse_impossible() -> None:
    """A subset that lacks the shaped glyphs triggers the author branch."""
    font_bytes = _build_arabic_font(SEEN + LAM + ALEF + MEEM)
    data = _authored_arabic_pdf(SEEN + LAM + ALEF + MEEM, font_bytes)

    document = Document(io.BytesIO(data))
    before = _font_resource_count(document)
    count = document.replace_text(SEEN + LAM + ALEF + MEEM, LAM + ALEF + MEEM,
                                  font=font_bytes)

    assert count == 1
    assert _font_resource_count(document) == before + 1  # a new font was added
    # Round-trips cleanly.
    reloaded = Document(io.BytesIO(_save(document)))
    assert len(reloaded.pages) == 1


def test_replace_refuses_complex_replacement_without_font() -> None:
    """Without a font and unable to reuse, the edit raises instead of mangling."""
    font_bytes = _build_arabic_font(SEEN + LAM + ALEF + MEEM)
    data = _authored_arabic_pdf(SEEN + LAM + ALEF + MEEM, font_bytes)
    document = Document(io.BytesIO(data))
    with pytest.raises(FontEmbeddingException):
        document.replace_text(SEEN + LAM + ALEF + MEEM, LAM + ALEF + MEEM)


def test_replace_matches_rtl_phrase_in_visual_order() -> None:
    """A logical RTL search locates text stored in visual order."""
    font_bytes = _build_arabic_font(SEEN + LAM + ALEF + MEEM)
    data = _authored_arabic_pdf(SEEN + LAM + ALEF + MEEM, font_bytes)
    document = Document(io.BytesIO(data))
    # The logical search string is not the stored (visual) order, yet matches.
    count = document.replace_text(SEEN + LAM + ALEF + MEEM, LAM + ALEF + MEEM,
                                  font=font_bytes)
    assert count == 1


def test_authored_replacement_round_trips_logical_order() -> None:
    """Extraction of the reshaped replacement stays in logical order."""
    font_bytes = _build_arabic_font(SEEN + LAM + ALEF + MEEM + AIN)
    data = _authored_arabic_pdf(SEEN + LAM + ALEF + MEEM, font_bytes)
    document = Document(io.BytesIO(data))
    document.replace_text(SEEN + LAM + ALEF + MEEM, AIN + LAM + MEEM,
                          font=font_bytes)
    text = _extract(_save(document))
    assert AIN + LAM + MEEM in text  # logical order preserved on extraction


# --- LTR fast path is unchanged ---------------------------------------------


def test_ltr_replacement_keeps_fast_path() -> None:
    document = Document()
    page = document.pages.add()
    page.add_text("Hello World", x=72, y=700, font_size=14)
    document = Document(io.BytesIO(_save(document)))
    assert document.replace_text("World", "There") == 1
    assert "There" in _extract(_save(document))


def test_needs_shaping_gate() -> None:
    assert not needs_shaping("Hello")
    assert not needs_shaping("123 abc")
    assert needs_shaping(SEEN + LAM)  # Arabic
    assert needs_shaping("אב")  # Hebrew


# --- Substitute-font render joining -----------------------------------------


def test_shape_join_preserving_maps_indices_in_order() -> None:
    """Order-preserving joining maps each input index to a joined glyph id."""
    font_bytes = _build_arabic_font(SEEN + LAM + ALEF + MEEM)
    order = _glyph_order(font_bytes)
    mapping = shape_join_preserving(font_bytes, SEEN + LAM + ALEF + MEEM)
    assert mapping is not None
    # Keyed by input character index (so the renderer draws each glyph at its
    # stored position); every index resolves, and joined forms are applied.
    assert set(mapping) == {0, 1, 2, 3}
    names = [order[mapping[i]] for i in sorted(mapping)]
    assert any(name.endswith((".init", ".medi", ".fina")) for name in names)


def test_render_substitute_joining_no_crash_latin() -> None:
    """A Latin page renders unchanged with substitute shaping enabled."""
    document = Document()
    page = document.pages.add()
    page.add_text("Hello", x=72, y=700, font_size=24, font_name="Helvetica")
    raster = document.render_page(0, shape_substitute_text=True)
    assert raster.width > 0 and raster.height > 0
