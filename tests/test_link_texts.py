"""Tests for batch text linking with link_texts API."""

from __future__ import annotations

import pytest

from aspose_pdf import AutoLinkResult, Document
from aspose_pdf.interactive import FitDestination


@pytest.fixture
def document():
    """Create a document with test text."""
    with Document() as doc:
        first = doc.pages.add()
        first.add_text("Please review this document and review it again", 72, 700, font_size=12)
        first.add_text("Contact us at support@example.com for help", 72, 650, font_size=12)
        second = doc.pages.add()
        second.add_text("IMPORTANT: Review required for Review", 72, 700, font_size=12)
        second.add_text("Call 123-456-7890 for support", 72, 650, font_size=12)
        yield doc


def test_link_texts_multiple_patterns_to_uri(document):
    """link_texts links multiple patterns to the same URI."""
    result = document.link_texts(
        {"review": {}, "Review": {}},
        uri="https://example.com/docs",
    )
    
    assert isinstance(result, AutoLinkResult)
    assert result.linked_count == 4  # 2 "review" + 2 "Review" matches
    assert result.skipped_count == 0
    assert result.existing_count == 0


def test_link_texts_page_selection(document):
    """link_texts can target a specific page."""
    result = document.link_texts(
        {"review": {}},
        uri="https://example.com",
        pages=[1],
    )
    
    assert result.linked_count == 2  # Only page 1 has "review"
    # Check annotations are on page 1
    for ann in result.annotations:
        # The annotation's parent page should be page 1
        # Check by looking at the annotation's rect (page 1 content starts at y=700)
        assert ann.rect[3] >= 700  # y1 should be near 700


def test_link_texts_regex(document):
    """link_texts supports regex patterns."""
    result = document.link_texts(
        {r"\d{3}-\d{3}-\d{4}": {}},
        uri="https://example.com",
        regex=True,
    )
    
    assert result.linked_count == 1  # One phone number match


def test_link_texts_case_sensitivity():
    """link_texts respects case sensitivity."""
    with Document() as doc:
        first = doc.pages.add()
        first.add_text("Please review this document and review it again", 72, 700, font_size=12)
        second = doc.pages.add()
        second.add_text("IMPORTANT: Review required for Review", 72, 700, font_size=12)
        
        # Case sensitive - "review" won't match "Review"
        result = doc.link_texts(
            {"review": {}},
            uri="https://example.com",
            case_sensitive=True,
        )
        assert result.linked_count == 2  # 2 "review" matches (both lowercase) on page 1
        
        # Case insensitive - "review" will match "Review" and "review"
        with Document() as doc2:
            doc2.pages.add().add_text("Please review this document and review it again", 72, 700, font_size=12)
            doc2.pages.add().add_text("IMPORTANT: Review required for Review", 72, 700, font_size=12)
            result = doc2.link_texts(
                {"review": {}},
                uri="https://example.com",
                case_sensitive=False,
            )
            # 4 matches total (2 "review" + 2 "Review"), and since it's a fresh doc,
            # all 4 can be linked
            assert result.linked_count == 4


def test_link_texts_with_destination(document):
    """link_texts can link to a Destination."""
    result = document.link_texts(
        {"chapter": {}},
        target=FitDestination(1),
        uri=None,
    )
    
    assert result.linked_count == 0  # No "chapter" matches in the document
    
    # Add text that matches
    document.pages[1].add_text("See chapter 1 for details", 72, 600, font_size=12)
    result = document.link_texts(
        {"chapter": {}},
        target=FitDestination(1),
        uri=None,
    )
    
    assert result.linked_count == 1


def test_link_texts_empty_dict(document):
    """link_texts rejects an empty dict."""
    with pytest.raises(Exception):  # PdfValidationException or similar
        document.link_texts({}, uri="https://example.com")


def test_link_texts_invalid_options(document):
    """link_texts validates pattern options."""
    with pytest.raises(Exception):
        document.link_texts({"test": "not a dict"}, uri="https://example.com")


def test_link_texts_max_count(document):
    """link_texts respects max_count."""
    result = document.link_texts(
        {"review": {}, "Review": {}},
        uri="https://example.com",
        max_count=2,
    )
    
    assert result.linked_count == 2  # Limited by max_count


def test_link_texts_page_method():
    """Page.link_texts works like Document.link_texts but for one page."""
    with Document() as doc:
        first = doc.pages.add()
        first.add_text("Please review this document and review it again", 72, 700, font_size=12)
        first.add_text("IMPORTANT: Review required for Review", 72, 650, font_size=12)
        
        result = doc.pages[1].link_texts(
            {"review": {}, "Review": {}},
            uri="https://example.com",
        )
        
        assert isinstance(result, AutoLinkResult)
        # 4 matches on page 1 (2 "review" + 2 "Review")
        assert result.linked_count == 4


def test_link_texts_combined_regex(document):
    """link_texts can mix regex and literal patterns."""
    result = document.link_texts(
        {r"\d+": {}, "review": {}},
        uri="https://example.com",
        regex=True,
    )
    
    # Should match "123-456-7890" and "review" occurrences
    assert result.linked_count >= 3  # At least one number + two reviews


def test_link_texts_no_duplicate_annotations(document):
    """link_texts doesn't create overlapping annotations."""
    # Link "review" first
    result1 = document.link_texts(
        {"review": {}},
        uri="https://example.com",
    )
    
    # Try to link "review" again - should not create duplicates
    result2 = document.link_texts(
        {"review": {}},
        uri="https://example.com",
    )
    
    # The second call should find existing links and skip
    assert result2.linked_count == 0
    assert result2.existing_count >= result1.linked_count


def test_link_texts_disposed_document():
    """link_texts raises on a disposed document."""
    doc = Document()
    doc.dispose()
    with pytest.raises(Exception):
        doc.link_texts({"test": {}}, uri="https://example.com")
