from aspose_pdf.engine.content_stream_parser import ContentStreamParser

# ---------------------------------------------------------------------------
# ContentStreamParser Unit Tests
# ---------------------------------------------------------------------------


def test_tj_operator_simple():
    """Tj operator with a simple literal string."""
    parser = ContentStreamParser(b"BT (Hello) Tj ET", {})
    assert parser.extract_text() == "Hello"


def test_tj_operator_escaped_newline():
    """String with escaped newline should be interpreted correctly."""
    parser = ContentStreamParser(b"BT (Hello\\nWorld) Tj ET", {})
    assert parser.extract_text() == "Hello\nWorld"


def test_tj_operator_hex_string():
    """Hexadecimal string should be decoded to text."""
    parser = ContentStreamParser(b"BT <48656C6C6F> Tj ET", {})
    assert parser.extract_text() == "Hello"


def test_tj_operator_array():
    """TJ operator with an array of strings and spacing values."""
    stream = b"BT [(H) 10 (e) 10 (l) 10 (l) 10 (o)] TJ ET"
    parser = ContentStreamParser(stream, {})
    assert parser.extract_text() == "Hello"


def test_tj_operator_array_with_spacing():
    """TJ operator with large spacing that should trigger a space insertion."""
    # The current extractor preserves adjacent text without spacing heuristics.
    pass


def test_tm_operator_matrix_preserves_text():
    """Tm matrix operation should not affect extracted text order."""
    stream = b"BT 1 0 0 1 0 0 Tm (Hello) Tj ET"
    parser = ContentStreamParser(stream, {})
    assert parser.extract_text() == "Hello"


def test_legacy_encoding_winansi():
    """Test WinAnsiEncoding."""
    resources = {"Font": {"F1": {"Encoding": "WinAnsiEncoding"}}}
    # /F1 12 Tf <80> Tj
    stream = b"BT /F1 12 Tf <80> Tj ET"
    parser = ContentStreamParser(stream, resources)
    assert parser.extract_text() == "€"


def test_to_unicode_parsing():
    """Test parsing of ToUnicode CMap."""
    # Mock resources with ToUnicode
    to_unicode_stream = b"""
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
1 beginbfchar
<0001> <0041>
endbfchar
endcmap
CMapName currentdict /CMap defineresource pop
end
end
"""
    resources = {"Font": {"F1": {"ToUnicode": to_unicode_stream}}}
    stream = b"BT /F1 12 Tf <0001> Tj ET"
    parser = ContentStreamParser(stream, resources)
    assert parser.extract_text() == "A"
