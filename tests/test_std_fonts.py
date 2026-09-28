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


def test_get_glyph_width_is_the_fonts_own():
    """A standard font's glyph widths are the published ones, not a flat guess."""
    # The metrics every reader knows: see engine/std_metrics.py.
    assert StandardFonts.get_glyph_width("Helvetica", ord("A")) == 667
    assert StandardFonts.get_glyph_width("Helvetica", ord("i")) == 222
    assert StandardFonts.get_glyph_width("Times-Roman", ord(" ")) == 250
    # Courier is fixed-pitch, so 600 there is the real width, not a fallback.
    assert StandardFonts.get_glyph_width("Courier", ord("i")) == 600
    # No width to give: not one of the fourteen, or a code WinAnsi leaves out.
    assert StandardFonts.get_glyph_width("FakeFont", ord("A")) == 600
    assert StandardFonts.get_glyph_width("Helvetica", 127) == 600


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
