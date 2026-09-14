"""Standard-14 fonts render as real glyphs via bundled substitutes.

The Standard-14 fonts are never embedded, so before substitution the renderer
drew a solid box per glyph. These tests cover the name->substitute resolution,
the bundled-font loading, and that the page renderer now fills real glyph
outlines (sparser than solid boxes) for the Helvetica/Times/Courier families
(Liberation) and for Symbol/ZapfDingbats (DejaVu shape subsets indexed by
their built-in encodings).
"""

from __future__ import annotations

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.engine.std_font_data import (
    load_substitute_sfnt,
    resolve_substitute_key,
    strip_subset_prefix,
)

# --- name / descriptor resolution -----------------------------------------


@pytest.mark.parametrize(
    "base_font,expected",
    [
        ("Helvetica", "sans-regular"),
        ("Helvetica-Bold", "sans-bold"),
        ("Helvetica-Oblique", "sans-italic"),
        ("Helvetica-BoldOblique", "sans-bolditalic"),
        ("Times-Roman", "serif-regular"),
        ("Times-BoldItalic", "serif-bolditalic"),
        ("Courier", "mono-regular"),
        ("Courier-BoldOblique", "mono-bolditalic"),
        # real-world aliases / decorations
        ("Arial,Italic", "sans-italic"),
        ("ArialMT", "sans-regular"),
        ("Arial-BoldMT", "sans-bold"),
        ("TimesNewRomanPS-BoldItalicMT", "serif-bolditalic"),
        ("CourierNewPSMT", "mono-regular"),
        # subset prefix is stripped
        ("ABCDEF+Helvetica-Bold", "sans-bold"),
    ],
)
def test_resolve_substitute_key_by_name(base_font: str, expected: str) -> None:
    assert resolve_substitute_key(base_font) == expected


def test_resolve_symbol_and_dingbats_use_shape_substitutes() -> None:
    assert resolve_substitute_key("Symbol") == "symbol"
    assert resolve_substitute_key("ZapfDingbats") == "dingbats"
    assert resolve_substitute_key("ABCDEF+Symbol") == "symbol"


def test_resolve_uses_descriptor_signals() -> None:
    # No usable name -> fall back to FontDescriptor /Flags.
    assert resolve_substitute_key(None, flags=1 << 1) == "serif-regular"  # Serif
    assert resolve_substitute_key(None, flags=1 << 0) == "mono-regular"  # FixedPitch
    assert resolve_substitute_key("Unknown", flags=1 << 6) == "sans-italic"  # Italic
    assert resolve_substitute_key("Unknown", flags=1 << 18) == "sans-bold"  # ForceBold
    # Italic angle and weight refine an otherwise-regular face.
    assert resolve_substitute_key("Whatever", italic_angle=-12.0) == "sans-italic"
    assert resolve_substitute_key("Whatever", font_weight=700) == "sans-bold"


def test_unrecognised_symbolic_font_has_no_substitute() -> None:
    # A symbolic font with no family signal in its name (e.g. a non-embedded
    # Wingdings) must not be substituted with a Latin face -- that would draw
    # the wrong glyphs -- so it keeps the box fallback.
    assert resolve_substitute_key("Wingdings", flags=1 << 2) is None
    assert resolve_substitute_key(None, flags=1 << 2) is None
    # ...but a recognised text family wins even if the symbolic flag is set
    # (producers set it spuriously on subset text fonts).
    assert resolve_substitute_key("Helvetica", flags=1 << 2) == "sans-regular"
    # ...and a symbolic font that is fixed-pitch/serif by flags still resolves.
    assert resolve_substitute_key(None, flags=(1 << 2) | (1 << 0)) == "mono-regular"


def test_strip_subset_prefix() -> None:
    assert strip_subset_prefix("ABCDEF+Helvetica") == "Helvetica"
    assert strip_subset_prefix("Helvetica") == "Helvetica"
    assert strip_subset_prefix("ABCDE+Helvetica") == "ABCDE+Helvetica"  # needs 6


# --- bundled font loading --------------------------------------------------


@pytest.mark.parametrize(
    "key", ["sans-regular", "serif-bold", "mono-italic", "sans-bolditalic"]
)
def test_load_substitute_sfnt_returns_valid_font(key: str) -> None:
    data = load_substitute_sfnt(key)
    assert data is not None
    # A valid SFNT starts with a known sfntVersion tag.
    assert data[:4] in (b"\x00\x01\x00\x00", b"true", b"OTTO")


def test_load_substitute_sfnt_misses_return_none() -> None:
    assert load_substitute_sfnt(None) is None
    assert load_substitute_sfnt("symbol-regular") is None
    assert load_substitute_sfnt("bogus-key") is None


def test_load_symbolic_substitutes() -> None:
    for key in ("symbol", "dingbats"):
        data = load_substitute_sfnt(key)
        assert data is not None
        assert data[:4] in (b"\x00\x01\x00\x00", b"true", b"OTTO")


def test_substitute_is_metric_compatible_with_helvetica() -> None:
    # Liberation Sans is metric-compatible with Helvetica/Arial: the advance of
    # 'A' is 667/1000 em. This is what keeps text positioning correct when a
    # Standard-14 font omits its /Widths array.
    from aspose_pdf.engine.font_subset import read_unicode_cmap
    from aspose_pdf.engine.glyph_outlines import TrueTypeOutlines

    sfnt = load_substitute_sfnt("sans-regular")
    outlines = TrueTypeOutlines(sfnt)
    assert outlines.ok
    gid = read_unicode_cmap(sfnt)[ord("A")]
    advance = round(outlines.advance_width(gid) * 1000 / outlines.units_per_em)
    assert advance == 667


# --- rendering -------------------------------------------------------------


def _render_text(text: str, font_name: str, font_size: float = 40.0):
    doc = Document()
    doc._engine_pdf = SimplePdf(pages=[(0.0, 0.0, 400.0, 80.0)], page_contents=[b""])
    doc.pages[0].add_text(text, 10, 30, font_size=font_size, font_name=font_name)
    return doc.pages[0].render(antialias=False)


def _dark_pixels(raster) -> int:
    return sum(
        1
        for y in range(raster.height)
        for x in range(raster.width)
        if raster.get_pixel(x, y) == (0, 0, 0)
    )


def _box_pixels(monkeypatch, text: str, font_name: str) -> int:
    """Ink of the solid-box fallback (substitute bundle simulated missing)."""
    monkeypatch.setattr(
        "aspose_pdf.engine.rasterizer.load_substitute_sfnt", lambda key: None
    )
    try:
        return _dark_pixels(_render_text(text, font_name))
    finally:
        monkeypatch.undo()


def test_helvetica_renders_real_glyphs_not_boxes(monkeypatch) -> None:
    # Real glyph outlines are far sparser than the solid-box fallback drawn
    # when no substitute is available for the same string.
    glyphs = _dark_pixels(_render_text("Helvetica", "Helvetica"))
    boxes = _box_pixels(monkeypatch, "Helvetica", "Helvetica")
    assert glyphs > 0
    assert glyphs < boxes


def test_times_and_courier_render_glyphs(monkeypatch) -> None:
    for font in ("Times-Roman", "Courier"):
        glyphs = _dark_pixels(_render_text("Sample", font))
        boxes = _box_pixels(monkeypatch, "Sample", font)
        assert 0 < glyphs < boxes


def test_distinct_letters_produce_distinct_glyphs() -> None:
    thin = _render_text("lllll", "Helvetica")
    wide = _render_text("MMMMM", "Helvetica")
    # 'M' carries far more ink than 'l', which solid boxes (width-only) would
    # not reflect this strongly, and the rasters must differ.
    assert _dark_pixels(wide) > _dark_pixels(thin)
    differs = any(
        thin.get_pixel(x, y) != wide.get_pixel(x, y)
        for y in range(thin.height)
        for x in range(thin.width)
    )
    assert differs


def test_bold_is_heavier_than_regular() -> None:
    regular = _dark_pixels(_render_text("Mass", "Helvetica"))
    bold = _dark_pixels(_render_text("Mass", "Helvetica-Bold"))
    assert bold > regular


def test_symbol_renders_real_glyphs(monkeypatch) -> None:
    # Symbol code 0x61.. maps to Greek alpha/beta/gamma through the built-in
    # Symbol encoding; real outlines are sparser than the box fallback.
    glyphs = _dark_pixels(_render_text("abg", "Symbol"))
    boxes = _box_pixels(monkeypatch, "abg", "Symbol")
    assert 0 < glyphs < boxes


def test_zapfdingbats_renders_real_glyphs(monkeypatch) -> None:
    # ZapfDingbats codes 0x33/0x34 are the check marks U+2713/U+2714.
    glyphs = _dark_pixels(_render_text("34", "ZapfDingbats"))
    boxes = _box_pixels(monkeypatch, "34", "ZapfDingbats")
    assert 0 < glyphs < boxes


def test_symbol_glyphs_differ_from_latin() -> None:
    # The same codes drawn with Symbol (Greek) and Helvetica (Latin) must
    # produce different rasters -- i.e. the built-in encoding is honoured.
    symbol = _render_text("abg", "Symbol")
    helv = _render_text("abg", "Helvetica")
    differs = any(
        symbol.get_pixel(x, y) != helv.get_pixel(x, y)
        for y in range(symbol.height)
        for x in range(symbol.width)
    )
    assert differs


# --- Symbol and ZapfDingbats keep their built-in encoding -------------------
#
# A named /Encoding on these fonts is a Latin code page they have no glyphs
# for. Overlaid on the built-in encoding, it turned the ZapfDingbats check mark
# MuPDF writes as (3) under /WinAnsiEncoding into the glyph "three" and drew
# nothing -- MuPDF's check boxes rendered empty. pdfium and MuPDF keep the
# built-in encoding whatever base is named; /Differences still apply, naming
# glyphs of the font's own.


def _render_font(font_dict: bytes, shown: bytes):
    import io

    content = b"BT /F1 40 Tf 10 20 Td (" + shown + b") Tj ET"
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        3: (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 80] /Resources "
            b"<< /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        4: b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
        5: font_dict,
    }
    raw = bytearray(b"%PDF-1.7\n")
    offsets = {}
    for number in sorted(objects):
        offsets[number] = len(raw)
        raw += b"%d 0 obj\n" % number + objects[number] + b"\nendobj\n"
    start = len(raw)
    raw += b"xref\n0 6\n0000000000 65535 f \n"
    raw += b"".join(b"%010d 00000 n \n" % offsets[n] for n in sorted(objects))
    raw += b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % start
    raster = Document(io.BytesIO(bytes(raw))).pages[0].render(antialias=False)
    return [raster.get_pixel(x, y) for y in range(raster.height) for x in range(raster.width)]


def _font(base: bytes, encoding: bytes = b"") -> bytes:
    return b"<< /Type /Font /Subtype /Type1 /BaseFont /" + base + encoding + b" >>"


@pytest.mark.parametrize(
    "encoding", [b"WinAnsiEncoding", b"StandardEncoding", b"MacRomanEncoding"]
)
@pytest.mark.parametrize(("base", "shown"), [(b"ZapfDingbats", b"34n"), (b"Symbol", b"abp")])
def test_a_named_encoding_does_not_replace_the_built_in_one(base, shown, encoding):
    named = _render_font(_font(base, b" /Encoding /" + encoding), shown)
    builtin = _render_font(_font(base), shown)
    assert any(p != (255, 255, 255) for p in builtin)
    assert named == builtin


def test_a_difference_naming_a_dingbat_draws_that_dingbat():
    # a20 is the heavy check mark, code 52 ("4") in the built-in encoding; no
    # Adobe Glyph List name, so it resolves through the font's own names.
    remapped = _render_font(
        _font(b"ZapfDingbats", b" /Encoding << /Differences [51 /a20] >>"), b"3"
    )
    direct = _render_font(_font(b"ZapfDingbats"), b"4")
    assert remapped == direct


def test_a_difference_on_top_of_a_named_base_still_applies():
    remapped = _render_font(
        _font(
            b"ZapfDingbats",
            b" /Encoding << /BaseEncoding /WinAnsiEncoding /Differences [51 /a20] >>",
        ),
        b"3",
    )
    assert remapped == _render_font(_font(b"ZapfDingbats"), b"4")


def test_a_difference_naming_a_symbol_glyph_draws_that_glyph():
    remapped = _render_font(_font(b"Symbol", b" /Encoding << /Differences [97 /beta] >>"), b"a")
    assert remapped == _render_font(_font(b"Symbol"), b"b")


@pytest.mark.parametrize("glyph", [b"Lslash", b"checkmark", b"uni2714", b"foobar"])
def test_a_difference_naming_a_glyph_the_font_lacks_draws_nothing(glyph):
    # As pdfium does. Even "checkmark" and "uni2714", which the Adobe Glyph List
    # maps to check marks: ZapfDingbats has no glyphs by those names.
    missing = _render_font(
        _font(b"ZapfDingbats", b" /Encoding << /Differences [51 /" + glyph + b"] >>"), b"3"
    )
    assert all(p == (255, 255, 255) for p in missing)


def test_the_glyph_name_tables_cover_exactly_the_encoded_codes():
    from aspose_pdf.engine.symbol_encodings import (
        SYMBOL_GLYPH_NAMES,
        SYMBOL_TO_UNICODE,
        ZAPF_DINGBATS_GLYPH_NAMES,
        ZAPF_DINGBATS_TO_UNICODE,
    )

    for names, codes in (
        (SYMBOL_GLYPH_NAMES, SYMBOL_TO_UNICODE),
        (ZAPF_DINGBATS_GLYPH_NAMES, ZAPF_DINGBATS_TO_UNICODE),
    ):
        assert set(names) == set(codes)
        assert len(set(names.values())) == len(names)  # a name names one code


@pytest.mark.parametrize(
    ("font", "glyph", "codepoint"),
    [
        ("dingbats", "a1", 0x2701),
        ("dingbats", "a20", 0x2714),
        ("dingbats", "a191", 0x27BE),
        ("dingbats", "three", None),
        ("symbol", "alpha", 0x03B1),
        ("symbol", "Delta", 0x0394),  # the table's alias, not the AGL's U+2206
        ("helvetica", "A", None),
    ],
)
def test_builtin_glyph_to_unicode(font, glyph, codepoint):
    from aspose_pdf.engine.symbol_encodings import builtin_glyph_to_unicode

    assert builtin_glyph_to_unicode(font, glyph) == codepoint
