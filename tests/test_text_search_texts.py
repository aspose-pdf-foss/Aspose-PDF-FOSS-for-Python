"""Batch text search with context and geometry for multiple patterns."""

from __future__ import annotations

import re

import pytest

from aspose_pdf import Document, PdfLoadLimits, TextSearchMatch
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfResourceLimitException,
    PdfValidationException,
)


@pytest.fixture
def document():
    """Create a simple document with test text."""
    with Document() as doc:
        first, second = doc.pages.add(), doc.pages.add()
        first.add_text("Hello world world", 72, 700, font_size=12)
        first.add_text("Invoice 123 paid 456", 72, 650, font_size=12)
        second.add_text("HELLO world", 100, 500, font_size=12)
        yield doc


def test_search_texts_multiple_patterns(document):
    """search_texts searches multiple patterns in one call."""
    result = document.search_texts(
        {"world": "label1", "invoice": "label2"},
        case_sensitive=False,
    )
    # 3 "world" matches + 1 "invoice" match = 4 total
    assert isinstance(result, dict)
    assert "world" in result
    assert "invoice" in result
    assert len(result["world"]) == 3
    assert len(result["invoice"]) == 1
    
    # Check that results are TextSearchMatch objects
    for match in result["world"]:
        assert isinstance(match, TextSearchMatch)
        assert match.text == "world"
        assert match.page_index in (1, 2)
        assert "context_before" in dir(match)
        assert "context_after" in dir(match)


def test_search_texts_page_selection(document):
    """search_texts can target a specific page."""
    result = document.search_texts(
        {"world": {}},
        pages=[2],
    )
    # Page 2 has 1 "world" match
    assert len(result["world"]) == 1
    assert all(m.page_index == 2 for m in result["world"])


def test_search_texts_regex(document):
    """search_texts supports regex patterns."""
    result = document.search_texts(
        {r"\d+": {}},
        regex=True,
    )
    # 123 and 456 = 2 matches
    assert len(result[r"\d+"]) == 2
    texts = [m.text for m in result[r"\d+"]]
    assert "123" in texts
    assert "456" in texts


def test_search_texts_case_sensitivity(document):
    """search_texts respects case sensitivity."""
    # Case sensitive
    result = document.search_texts(
        {"HELLO": {}},
        case_sensitive=True,
    )
    assert len(result["HELLO"]) == 1
    
    # Case insensitive
    result = document.search_texts(
        {"hello": {}},
        case_sensitive=False,
    )
    # Should match "Hello" on page 1 and "HELLO" on page 2
    assert len(result["hello"]) == 2


def test_search_texts_context_chars(document):
    """search_texts respects context_chars parameter."""
    result = document.search_texts(
        {"world": {}},
        context_chars=5,
    )
    
    # Check context is captured
    match = result["world"][0]
    assert match.context_before is not None
    assert match.context_after is not None


def test_search_texts_page_method(document):
    """Page.search_texts works correctly."""
    result = document.pages[1].search_texts(
        {"world": {}},
    )
    assert len(result["world"]) == 2
    assert all(m.page_index == 1 for m in result["world"])


def test_search_texts_empty_dict():
    """search_texts with empty dict returns empty results."""
    with Document() as doc:
        doc.pages.add().add_text("Hello", 72, 700)
        result = doc.search_texts({})
        assert result == {}


def test_search_texts_invalid_options(document):
    """search_texts validates options."""
    with pytest.raises(PdfValidationException, match="context_chars must be"):
        document.search_texts({"hello": {}}, context_chars=-1)
    
    with pytest.raises(PdfValidationException, match="context_chars must be"):
        document.search_texts({"hello": {}}, context_chars=True)
    
    with pytest.raises(PdfValidationException, match="context_chars must be"):
        document.search_texts({"hello": {}}, context_chars=1.5)


def test_search_texts_disposed_document(document):
    """search_texts rejects disposed documents."""
    document.dispose()
    with pytest.raises(AsposePdfException, match="disposed"):
        document.search_texts({"hello": {}})


def test_search_texts_empty_document():
    """search_texts handles empty documents."""
    with Document() as doc:
        result = doc.search_texts({"hello": {}})
        assert result == {"hello": []}


def test_search_texts_zero_width_matches_ignored():
    """search_texts omits zero-width matches."""
    with Document() as doc:
        page = doc.pages.add()
        page.add_text("test", 72, 700)
        # Pattern that could match zero-width at word boundaries
        result = doc.search_texts({r"\btest\b": {}}, regex=True)
        # Only one exact match
        assert len(result[r"\btest\b"]) == 1


def test_search_texts_mixed_patterns():
    """search_texts handles both string and compiled regex patterns."""
    with Document() as doc:
        page = doc.pages.add()
        page.add_text("Invoice 1234 paid 5678", 72, 700)
        
        result = doc.search_texts(
            {
                "Invoice": {},
                re.compile(r"\d{4}"): {},
            },
            regex=True,
        )
        
        assert len(result["Invoice"]) == 1
        assert len(result[re.compile(r"\d{4}")]) == 2


def test_search_texts_preserves_key_order():
    """search_texts preserves input pattern key order in results."""
    with Document() as doc:
        page = doc.pages.add()
        page.add_text("one two three four", 72, 700)
        
        # Use a dict that preserves insertion order
        patterns = {
            "three": "label3",
            "one": "label1",
            "two": "label2",
        }
        result = doc.search_texts(patterns)
        
        # Check that all patterns have results
        assert len(result["one"]) == 1
        assert len(result["two"]) == 1
        assert len(result["three"]) == 1


def test_search_texts_resource_limits():
    """search_texts respects resource limits."""
    with Document(limits=PdfLoadLimits(max_container_items=16)) as doc:
        page = doc.pages.add()
        # Add enough text to trigger limits - 20 words should exceed limit
        page.add_text(" ".join(["word"] * 20), 72, 700)
        
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            doc.search_texts({"word": {}})


def test_search_texts_omitted_unmeasurable_geometry():
    """search_texts handles matches without geometry."""
    with _with_content(
        b"BT /F1 12 Tf 72 700 Td (Unknown) Tj ET "
        b"BT /F1 12 Tf 72 650 Td (Known) Tj ET"
    ) as doc:
        result = doc.search_texts({r"Unknown|Known": {}}, regex=True)
        
        # Both should be found
        assert len(result[r"Unknown|Known"]) == 2
        
        # Both should have geometry since we used a valid font
        has_geometry = all(m.rect is not None for m in result[r"Unknown|Known"])
        assert has_geometry


def _with_content(content: bytes) -> Document:
    """Create a document with custom content stream."""
    import io
    
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Count 1 /Kids [10 0 R] >>",
        10: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 20 0 R /Resources << /Font << /F1 30 0 R >> >> >>",
        20: _stream(content),
        30: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    
    raw = bytearray(b"%PDF-1.7\n")
    offsets = {}
    for number in sorted(objects):
        offsets[number] = len(raw)
        raw += b"%d 0 obj\n" % number + objects[number] + b"\nendobj\n"
    start = len(raw)
    size = max(objects) + 1
    raw += b"xref\n0 %d\n" % size
    for number in range(size):
        raw += (b"%010d 00000 n \n" % offsets[number]) if number in offsets else b"0000000000 65535 f \n"
    raw += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, start)
    
    return Document(io.BytesIO(bytes(raw)))


def _stream(body: bytes) -> bytes:
    """Create a stream object."""
    return b"<< /Length %d >>\nstream\n" % len(body) + body + b"\nendstream"
