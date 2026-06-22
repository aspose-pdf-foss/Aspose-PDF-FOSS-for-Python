"""Standard-14 fonts render as real glyphs via bundled OFL substitutes.

The Standard-14 fonts are never embedded, so before substitution the renderer
drew a solid box per glyph. These tests cover the name->substitute resolution,
the bundled-font loading, and that the page renderer now fills real glyph
outlines (sparser than solid boxes) for the Helvetica/Times/Courier families
while Symbol/ZapfDingbats keep the box fallback.
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


def test_resolve_symbol_and_dingbats_have_no_substitute() -> None:
    assert resolve_substitute_key("Symbol") is None
    assert resolve_substitute_key("ZapfDingbats") is None
    assert resolve_substitute_key("ABCDEF+Symbol") is None


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


def test_helvetica_renders_real_glyphs_not_boxes() -> None:
    # Real glyph outlines are far sparser than the solid-box fallback that
    # Symbol (no substitute) still uses for the same string.
    glyphs = _dark_pixels(_render_text("Helvetica", "Helvetica"))
    boxes = _dark_pixels(_render_text("Helvetica", "Symbol"))
    assert glyphs > 0
    assert glyphs < boxes


def test_times_and_courier_render_glyphs() -> None:
    for font in ("Times-Roman", "Courier"):
        glyphs = _dark_pixels(_render_text("Sample", font))
        boxes = _dark_pixels(_render_text("Sample", "Symbol"))
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


def test_symbol_still_falls_back_to_boxes() -> None:
    # Symbol/ZapfDingbats have no metric-compatible OFL source; they keep the
    # solid-box fallback (dense ink), unlike the now-glyph Latin Standard-14.
    symbol = _dark_pixels(_render_text("ABCDE", "Symbol"))
    helv = _dark_pixels(_render_text("ABCDE", "Helvetica"))
    assert symbol > helv
