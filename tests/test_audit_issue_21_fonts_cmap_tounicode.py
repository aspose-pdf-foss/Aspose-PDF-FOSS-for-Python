"""AUDIT issue #21: CMap / ToUnicode / CIDFonts — international text mapping.

Covers multi-entry bfchar lines, linear bfrange triplets, UTF-16-based Type0 encodings,
and AGL ``uni*`` / ``u*`` glyph names in Encoding differences.
"""

from aspose_pdf.engine.content_stream_parser import ContentStreamParser


def _cmap_template(bfchar_body: bytes) -> bytes:
    return (
        b"""
/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
/CIDSystemInfo
<< /Registry (Adobe)
/Ordering (UCS)
/Supplement 0
>> def
/CMapName /Adobe-Identity-UCS def
1 begincodespacerange
<0000> <FFFF>
endcodespacerange
"""
        + bfchar_body
        + b"""
endcmap
CMapName currentdict /CMap defineresource pop
end
end
"""
    )


def test_to_unicode_bfchar_multiple_pairs_on_one_line():
    """Real CMaps often pack several <src> <dst> pairs on a single line."""
    body = b"""1 beginbfchar
<0001> <0041> <0002> <0042> <0003> <03B1>
endbfchar
"""
    resources = {"Font": {"F1": {"ToUnicode": _cmap_template(body)}}}
    stream = b"BT /F1 12 Tf <0001> Tj <0002> Tj <0003> Tj ET"
    parser = ContentStreamParser(stream, resources)
    assert parser.extract_text() == "ABα"


def test_to_unicode_bfchar_line_with_percent_comment():
    """Ignore PostScript-style comments on CMap lines."""
    body = b"""1 beginbfchar
<0004> <4E2D> % CJK unified ideograph
endbfchar
"""
    resources = {"Font": {"F1": {"ToUnicode": _cmap_template(body)}}}
    stream = b"BT /F1 12 Tf <0004> Tj ET"
    parser = ContentStreamParser(stream, resources)
    assert parser.extract_text() == "中"


def test_to_unicode_bfrange_multiple_linear_ranges_one_line():
    body = b"""1 beginbfrange
<0010> <0011> <0040> <0012> <0013> <0050>
endbfrange
"""
    resources = {"Font": {"F1": {"ToUnicode": _cmap_template(body)}}}
    stream = b"BT /F1 12 Tf <0010> Tj <0011> Tj <0012> Tj <0013> Tj ET"
    parser = ContentStreamParser(stream, resources)
    assert parser.extract_text() == "@APQ"


def test_type0_unijis_utf16_h_without_to_unicode():
    """Encoding name *UTF16* uses 2-byte big-endian code units like Identity-H."""
    resources = {
        "Font": {
            "F1": {
                "Subtype": "Type0",
                "Encoding": "/UniJIS-UTF16-H",
                "DescendantFonts": [
                    {
                        "Subtype": "CIDFontType2",
                        "DW": 1000,
                    }
                ],
            }
        }
    }
    # U+4E2D U+6587 ("中文") as UTF-16BE code units
    stream = b"BT /F1 12 Tf <4E2D6587> Tj ET"
    parser = ContentStreamParser(stream, resources)
    assert parser.extract_text() == "中文"


def test_simple_font_uni_glyph_name_in_differences():
    """AGL ``uni`` + groups of four hex digits map to Unicode (Adobe synthesised names)."""
    resources = {
        "Font": {
            "F1": {
                "Encoding": {
                    "BaseEncoding": "WinAnsiEncoding",
                    "Differences": [0x20, "uni4E2D6587"],
                },
            }
        }
    }
    stream = b"BT /F1 12 Tf <20> Tj ET"
    parser = ContentStreamParser(stream, resources)
    assert parser.extract_text() == "中文"


def test_simple_font_u_prefix_glyph_name():
    resources = {
        "Font": {
            "F1": {
                "Encoding": {
                    "BaseEncoding": "WinAnsiEncoding",
                    "Differences": [0x41, "u00E9"],
                },
            }
        }
    }
    stream = b"BT /F1 12 Tf <41> Tj ET"
    parser = ContentStreamParser(stream, resources)
    assert parser.extract_text() == "é"
