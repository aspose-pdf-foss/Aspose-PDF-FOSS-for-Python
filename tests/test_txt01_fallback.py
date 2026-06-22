from aspose_pdf.engine.content_stream_parser import ContentStreamParser
from aspose_pdf.engine.simple_pdf import SimplePdf

def test_best_effort_literal_nested_escaped():
    """Test best-effort extraction with nested and escaped parentheses."""
    s = b"(Hello (World\\)!) ) Tj"
    parser = ContentStreamParser(s, {})
    assert parser.best_effort_extract_text() == "Hello (World)!)"

def test_best_effort_hex():
    """Test best-effort extraction with hexadecimal strings."""
    s = b"<48656C6C6F> Tj"
    parser = ContentStreamParser(s, {})
    assert parser.best_effort_extract_text() == "Hello"

def test_best_effort_tj_array():
    """Test best-effort extraction with TJ arrays and spacing."""
    s = b"[(H) 10 (e) -300 (l)] TJ"
    parser = ContentStreamParser(s, {})
    assert parser.best_effort_extract_text() == "He l"

def test_simple_pdf_fallback_trigger():
    """Test that SimplePdf uses best-effort fallback when ContentStreamParser fails."""
    # A 'broken' stream missing BT/ET but containing valid text operators.
    # ContentStreamParser.extract_text() will return empty string because _in_text is False.
    # BUT SimplePdf.extract_text() should now use best_effort_extract_text() as fallback.
    
    pdf = SimplePdf()
    pdf.page_contents = [b"(Broken) Tj"]
    pdf.pages = [(0, 0, 100, 100)]
    
    # Note: SimplePdf.extract_text() might not find it if it doesn't raise Exception.
    # Wait, ContentStreamParser.extract_text() doesn't raise Exception just for missing BT/ET,
    # it just returns empty string.
    # I should check if I need to change SimplePdf logic to trigger fallback on empty string too.
    
    text = pdf.extract_text()
    assert text == "Broken"

if __name__ == "__main__":
    import pytest
    pytest.main([__file__])
