"""AUDIT issue #19: Content stream text — Tj/TJ with graphics/color ops, Identity-H fallback.

Operator arity for path/color/text state ops between BT and ET must drain the operand stack so
``(text) Tj`` still binds correctly. CID Identity-H without ToUnicode uses UTF-16BE code units.
"""

from aspose_pdf.engine.content_stream_parser import ContentStreamParser


def test_tj_after_rg_inside_text_object():
    """Device RGB color before Tj must not steal the string operand."""
    stream = b"BT /F1 12 Tf 1 0 0 rg (Hello) Tj ET"
    parser = ContentStreamParser(stream, {"Font": {"F1": {}}})
    assert parser.extract_text() == "Hello"


def test_tj_after_cs_sc_rgb():
    """Non-stroking colorspace + color operands before Tj."""
    stream = b"BT /F1 12 Tf /DeviceRGB cs 0.2 0.4 0.6 sc (Hi) Tj ET"
    parser = ContentStreamParser(stream, {"Font": {"F1": {}}})
    assert parser.extract_text() == "Hi"


def test_tj_after_text_state_tc():
    """Text state operator Tc (char spacing) shares the operand stack with Tj."""
    stream = b"BT /F1 12 Tf 2 Tc (Spaced) Tj ET"
    parser = ContentStreamParser(stream, {"Font": {"F1": {}}})
    assert parser.extract_text() == "Spaced"


def test_tj_after_save_restore_graphics_with_color():
    """q/Q nested with rg — stack and graphics state must stay consistent."""
    stream = b"BT /F1 12 Tf q 0.5 g 0.25 g (Inner) Tj Q 1 0 0 rg (Outer) Tj ET"
    parser = ContentStreamParser(stream, {"Font": {"F1": {}}})
    assert parser.extract_text() == "InnerOuter"


def test_tj_multiple_hex_showing_word_fragments():
    """Multiple hex strings in one text object."""
    stream = b"BT /F1 12 Tf <48656C6C6F> Tj 20 Tl <576F726C64> Tj ET"
    parser = ContentStreamParser(stream, {"Font": {"F1": {}}})
    out = parser.extract_text()
    assert "Hello" in out and "World" in out


def test_tj_array_adjacent_strings():
    """TJ array with back-to-back string operands (no numeric kerning between)."""
    stream = b"BT /F1 12 Tf [(Hello)(World)] TJ ET"
    parser = ContentStreamParser(stream, {"Font": {"F1": {}}})
    assert parser.extract_text() == "HelloWorld"


def test_tj_array_large_negative_triggers_space():
    """TJ kerning gap still inserts a word space when sufficiently negative."""
    stream = b"BT /F1 12 Tf [(Hello) -3500 (World)] TJ ET"
    parser = ContentStreamParser(stream, {"Font": {"F1": {}}})
    assert parser.extract_text() == "Hello World"


def test_identity_h_utf16be_without_to_unicode():
    """Type0 Identity-H / CID string: decode 16-bit code units as Unicode when no ToUnicode."""
    resources = {
        "Font": {
            "F1": {
                "Subtype": "Type0",
                "Encoding": "/Identity-H",
                "DescendantFonts": [
                    {
                        "Subtype": "CIDFontType2",
                        "DW": 1000,
                    }
                ],
            }
        }
    }
    # U+0048 U+0069 = "Hi"
    stream = b"BT /F1 12 Tf <00480069> Tj ET"
    parser = ContentStreamParser(stream, resources)
    assert parser.extract_text() == "Hi"


def test_simple_font_unknown_code_falls_back_to_latin1():
    """Bytes outside /Differences still yield a character via latin-1 fallback (not empty)."""
    resources = {
        "Font": {
            "F1": {
                "Encoding": {
                    "BaseEncoding": "WinAnsiEncoding",
                    "Differences": [0x20],
                },
            }
        },
    }
    # 0xA3 is £ in WinAnsi — with a broken/minimal Differences list, ensure we do not drop bytes silently
    stream = b"BT /F1 12 Tf <A3> Tj ET"
    parser = ContentStreamParser(stream, resources)
    assert parser.extract_text() == "£"


def test_second_extract_text_call_is_idempotent():
    """extract_text clears internal buffer between calls on the same parser."""
    parser = ContentStreamParser(b"BT (Once) Tj ET", {})
    assert parser.extract_text() == "Once"
    assert parser.extract_text() == "Once"
