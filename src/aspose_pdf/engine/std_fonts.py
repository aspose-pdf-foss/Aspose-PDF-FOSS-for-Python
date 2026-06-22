# Standard 14 Fonts Module

# List of the 14 standard font names as defined by the PDF specification
_STANDARD_FONTS = [
    "Courier",
    "Courier-Bold",
    "Courier-Oblique",
    "Courier-BoldOblique",
    "Helvetica",
    "Helvetica-Bold",
    "Helvetica-Oblique",
    "Helvetica-BoldOblique",
    "Times-Roman",
    "Times-Bold",
    "Times-Italic",
    "Times-BoldItalic",
    "Symbol",
    "ZapfDingbats",
]

# Very simple glyph width table: width for ASCII 32‑126 set to 600 units.
_DEFAULT_WIDTH = 600
_ASCII_RANGE = range(32, 127)

# Build a mapping for each font to its widths (identical for simplicity)
_GLYPH_WIDTHS = {
    font: {code: _DEFAULT_WIDTH for code in _ASCII_RANGE} for font in _STANDARD_FONTS
}


class StandardFonts:
    """Utility class for the PDF Standard 14 fonts.

    This class provides:
    * A list of the Standard 14 font names.
    * A simple glyph‑width lookup (all ASCII characters default to 600 units).
    * Helpers to check if a font name is standard and to retrieve default
      encodings.
    """

    @classmethod
    def ALL(cls):
        """Return a list of all standard font names."""
        return list(_STANDARD_FONTS)

    @classmethod
    def is_standard_font(cls, font_name):
        """Return ``True`` if *font_name* is one of the PDF Standard 14 fonts.

        The check is case‑sensitive to match the exact names used in PDFs.
        """
        return font_name in _STANDARD_FONTS

    @classmethod
    def get_glyph_width(cls, font_name, char_code):
        """Return the width of a glyph for *font_name* and *char_code*.

        If the font is not standard or the character code is unknown, a
        default width of 600 units is returned.
        """
        widths = _GLYPH_WIDTHS.get(font_name)
        if widths is None:
            return _DEFAULT_WIDTH
        return widths.get(char_code, _DEFAULT_WIDTH)

    @classmethod
    def get_default_encoding(cls, font_name):
        """Return the default encoding name for a standard font.

        Most standard fonts use *WinAnsiEncoding*; ``Symbol`` and
        ``ZapfDingbats`` historically use *StandardEncoding*.
        """
        if font_name in ("Symbol", "ZapfDingbats"):
            return "StandardEncoding"
        return "WinAnsiEncoding"
