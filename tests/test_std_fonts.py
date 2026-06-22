# Test for std_fonts
from aspose_pdf.engine.std_fonts import StandardFonts


def test_is_standard_font_positive():
    """Standard font names should be recognized as standard."""
    assert StandardFonts.is_standard_font("Courier") is True
    assert StandardFonts.is_standard_font("Helvetica-BoldOblique") is True


def test_is_standard_font_negative():
    """Non‑standard font names should return False."""
    assert StandardFonts.is_standard_font("NonExistingFont") is False
    assert StandardFonts.is_standard_font("") is False


def test_get_glyph_width_default():
    """Glyph width lookup returns default width for known and unknown fonts/char codes."""
    # Known standard font and ASCII character
    width = StandardFonts.get_glyph_width("Helvetica", ord("A"))
    assert width == 600
    # Unknown font should fallback to default width
    unknown_width = StandardFonts.get_glyph_width("FakeFont", ord("A"))
    assert unknown_width == 600
    # Known font but out‑of‑range char code should fallback to default width
    out_of_range = StandardFonts.get_glyph_width("Courier", 200)
    assert out_of_range == 600


def test_font_names_list():
    """ALL() should return the complete list of standard font names."""
    fonts = StandardFonts.ALL()
    # Expect exactly 14 standard fonts
    assert isinstance(fonts, list)
    assert len(fonts) == 14
    # Known font should be present in the list
    assert "Helvetica" in fonts
    # Ensure the list is a copy, not the original mutable reference
    fonts.append("Extra")
    assert "Extra" not in StandardFonts.ALL()
