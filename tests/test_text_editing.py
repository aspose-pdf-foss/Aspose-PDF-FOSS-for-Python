"""Tests for conservative existing-text replace/redact APIs."""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.exceptions import PdfValidationException


def _doc_with_content(*contents: bytes) -> Document:
    doc = Document()
    doc._engine_pdf = SimplePdf(
        pages=[(0.0, 0.0, 200.0, 200.0) for _ in contents],
        page_contents=list(contents),
    )
    return doc


def test_page_replace_text_updates_literal_tj() -> None:
    doc = Document()
    page = doc.pages.add()
    page.add_text("Hello World", 10, 20)

    assert page.replace_text("World", "PDF") == 1

    assert b"(Hello PDF)" in page.content
    assert "Hello PDF" in doc._engine_pdf.extract_text()


def test_document_replace_text_across_pages_honors_max_count() -> None:
    doc = _doc_with_content(
        b"BT /F1 12 Tf (cat cat) Tj ET",
        b"BT /F1 12 Tf (cat) Tj ET",
    )

    assert doc.replace_text("cat", "dog", max_count=2) == 2

    assert b"(dog dog)" in doc.pages[1].content
    assert b"(cat)" in doc.pages[2].content


def test_replace_text_can_target_one_page() -> None:
    doc = _doc_with_content(
        b"BT /F1 12 Tf (alpha) Tj ET",
        b"BT /F1 12 Tf (alpha) Tj ET",
    )

    assert doc.replace_text("alpha", "beta", page_index=2) == 1

    assert b"(alpha)" in doc.pages[1].content
    assert b"(beta)" in doc.pages[2].content


def test_replace_text_handles_tj_array_elements() -> None:
    doc = _doc_with_content(b"BT /F1 12 Tf [(Hello) 120 (World)] TJ ET")

    assert doc.replace_text("World", "PDF") == 1

    assert b"[(Hello) 120 (PDF)] TJ" in doc.pages[1].content


def test_replace_text_handles_hex_string_operand() -> None:
    doc = _doc_with_content(b"BT /F1 12 Tf <48656C6C6F> Tj ET")

    assert doc.replace_text("Hello", "Hi") == 1

    assert b"<4869>" in doc.pages[1].content


def test_replace_text_case_insensitive() -> None:
    doc = _doc_with_content(b"BT /F1 12 Tf (Hello HELLO hello) Tj ET")

    assert doc.replace_text("hello", "hi", case_sensitive=False) == 3

    assert b"(hi hi hi)" in doc.pages[1].content


def test_redact_text_removes_simple_operand_text() -> None:
    doc = _doc_with_content(b"BT /F1 12 Tf (Public Secret) Tj ET")

    assert doc.redact_text("Secret") == 1

    assert b"Secret" not in doc.pages[1].content
    assert b"(Public )" in doc.pages[1].content


def test_replace_text_persists_after_save_reload() -> None:
    doc = _doc_with_content(b"BT /F1 12 Tf (before) Tj ET")
    doc.replace_text("before", "after")
    buf = io.BytesIO()

    doc.save(buf)
    buf.seek(0)
    reloaded = Document().load_from(buf)

    assert b"(after)" in reloaded.pages[1].content


def test_replace_text_rejects_unencodable_replacement() -> None:
    doc = _doc_with_content(b"BT /F1 12 Tf (hello) Tj ET")

    with pytest.raises(PdfValidationException):
        doc.replace_text("hello", "snowman \u2603")


def test_replace_text_joins_split_tj_fragments() -> None:
    # A phrase split across TJ elements (common with kerning) is matched as one
    # string: the replacement lands in the first element, the rest is removed.
    doc = _doc_with_content(b"BT /F1 12 Tf [(Hel) 0 (lo)] TJ ET")

    assert doc.replace_text("Hello", "Hi") == 1

    content = doc.pages[1].content
    assert b"(Hi)" in content
    assert b"(Hel)" not in content and b"(lo)" not in content


# Tests for Document.replace_texts() ------------------------------


def test_replace_texts_applies_multiple_patterns() -> None:
    """replace_texts replaces all patterns in the mapping."""
    doc = _doc_with_content(
        b"BT /F1 12 Tf (Hello World) Tj ET",
        b"BT /F1 12 Tf (Goodbye World) Tj ET",
    )

    count = doc.replace_texts({"Hello": "Hi", "World": "PDF"})

    # Hello appears once (in first page), World appears twice (both pages)
    assert count == 3
    assert b"(Hi PDF)" in doc.pages[1].content
    assert b"(Goodbye PDF)" in doc.pages[2].content


def test_replace_texts_with_page_index() -> None:
    """replace_texts can target a specific page."""
    doc = _doc_with_content(
        b"BT /F1 12 Tf (alpha) Tj ET",
        b"BT /F1 12 Tf (alpha) Tj ET",
    )

    count = doc.replace_texts({"alpha": "beta"}, page_index=1)

    assert count == 1
    assert b"(beta)" in doc.pages[1].content
    assert b"(alpha)" in doc.pages[2].content


def test_replace_texts_with_max_count() -> None:
    """replace_texts respects max_count across all patterns."""
    doc = _doc_with_content(b"BT /F1 12 Tf (cat dog) Tj ET")

    count = doc.replace_texts({"cat": "mouse", "dog": "bird"}, max_count=2)

    # max_count=2 means only 2 total replacements, shared across patterns
    assert count == 2


def test_replace_texts_with_regex() -> None:
    """replace_texts supports regex patterns in the mapping."""
    doc = _doc_with_content(b"BT /F1 12 Tf (Phone: 123-456) Tj ET")

    count = doc.replace_texts(
        {r"\d{3}-\d{3}": "XXX-XXX"},
        regex=True,
    )

    assert count == 1
    assert b"(Phone: XXX-XXX)" in doc.pages[1].content


def test_replace_texts_compiled_regex() -> None:
    """replace_texts accepts compiled regex patterns as keys."""
    import re

    doc = _doc_with_content(b"BT /F1 12 Tf (Name: John) Tj ET")

    count = doc.replace_texts(
        {re.compile(r"John"): "Jane", re.compile(r"Name"): "Subject"},
        regex=True,
    )

    assert count == 2
    assert b"(Subject: Jane)" in doc.pages[1].content


def test_replace_texts_case_insensitive() -> None:
    """replace_texts supports case-insensitive matching."""
    doc = _doc_with_content(b"BT /F1 12 Tf (HELLO hello HeLLo) Tj ET")

    count = doc.replace_texts({"hello": "hi"}, case_sensitive=False)

    assert count == 3
    assert b"(hi hi hi)" in doc.pages[1].content


def test_replace_texts_returns_total_count() -> None:
    """replace_texts returns the sum of all pattern replacements."""
    doc = _doc_with_content(b"BT /F1 12 Tf (a b a b a b) Tj ET")

    count = doc.replace_texts({"a": "x", "b": "y"})

    assert count == 6  # 3 'a's + 3 'b's


def test_replace_texts_order_doesnt_matter() -> None:
    """replace_texts produces the same result regardless of dict order."""
    doc1 = _doc_with_content(b"BT /F1 12 Tf (foo bar) Tj ET")
    doc2 = _doc_with_content(b"BT /F1 12 Tf (foo bar) Tj ET")

    count1 = doc1.replace_texts({"foo": "x", "bar": "y"})
    count2 = doc2.replace_texts({"bar": "y", "foo": "x"})

    assert count1 == count2 == 2
    assert doc1.pages[1].content == doc2.pages[1].content


def test_replace_texts_empty_mapping() -> None:
    """replace_texts with empty mapping returns 0."""
    doc = _doc_with_content(b"BT /F1 12 Tf (nothing) Tj ET")

    count = doc.replace_texts({})

    assert count == 0
    assert b"(nothing)" in doc.pages[1].content


# Tests for Page.replace_texts() ------------------------------


def test_page_replace_texts_applies_multiple_patterns() -> None:
    """Page.replace_texts replaces all patterns in the mapping."""
    doc = Document()
    page = doc.pages.add()
    page.add_text("Hello World", 10, 20)
    page.add_text("Goodbye World", 10, 40)

    count = page.replace_texts({"Hello": "Hi", "World": "PDF"})

    # Hello appears once, World appears twice
    assert count == 3
    assert b"(Hi PDF)" in page.content


def test_page_replace_texts_with_max_count() -> None:
    """Page.replace_texts respects max_count."""
    doc = Document()
    page = doc.pages.add()
    page.add_text("cat cat cat cat", 10, 20)

    count = page.replace_texts({"cat": "dog"}, max_count=2)

    assert count == 2


def test_page_replace_texts_with_regex() -> None:
    """Page.replace_texts supports regex patterns."""
    import re

    doc = Document()
    page = doc.pages.add()
    page.add_text("ID: 12345", 10, 20)

    count = page.replace_texts({re.compile(r"\d+"): "XXXXX"})

    assert count == 1
    assert b"(ID: XXXXX)" in page.content


# Tests for Document.redact_texts() ------------------------------


def test_redact_texts_removes_multiple_patterns() -> None:
    """redact_texts removes all patterns in the mapping."""
    doc = _doc_with_content(
        b"BT /F1 12 Tf (Hello World) Tj ET",
        b"BT /F1 12 Tf (Hello Secret) Tj ET",
    )

    count = doc.redact_texts({"World": None, "Secret": None})

    # World appears once, Secret appears once
    assert count == 2
    # The text should be removed (no longer in content)
    assert b"(World)" not in doc.pages[1].content
    assert b"(Secret)" not in doc.pages[2].content


def test_redact_texts_with_page_index() -> None:
    """redact_texts can target a specific page."""
    doc = _doc_with_content(
        b"BT /F1 12 Tf (alpha) Tj ET",
        b"BT /F1 12 Tf (alpha) Tj ET",
    )

    count = doc.redact_texts({"alpha": None}, page_index=1)

    assert count == 1
    # alpha should be removed from page 1 but not page 2
    assert b"(alpha)" not in doc.pages[1].content
    assert b"(alpha)" in doc.pages[2].content


def test_redact_texts_with_max_count() -> None:
    """redact_texts respects max_count across all patterns."""
    doc = _doc_with_content(b"BT /F1 12 Tf (cat dog) Tj ET")

    count = doc.redact_texts({"cat": None, "dog": None}, max_count=2)

    # max_count=2 means only 2 total redactions, shared across patterns
    assert count == 2


def test_redact_texts_with_regex() -> None:
    """redact_texts supports regex patterns in the mapping."""
    doc = _doc_with_content(b"BT /F1 12 Tf (Phone: 123-456) Tj ET")

    count = doc.redact_texts(
        {r"\d{3}-\d{3}": None},
        regex=True,
    )

    assert count == 1
    assert b"(Phone: )" in doc.pages[1].content
    assert b"123-456" not in doc.pages[1].content


def test_redact_texts_compiled_regex() -> None:
    """redact_texts accepts compiled regex patterns as keys."""
    import re

    doc = _doc_with_content(b"BT /F1 12 Tf (Name: John) Tj ET")

    count = doc.redact_texts(
        {re.compile(r"John"): None, re.compile(r"Name"): None},
        regex=True,
    )

    assert count == 2
    # When both patterns are removed, we're left with ": "
    assert b": " in doc.pages[1].content
    assert b"Name" not in doc.pages[1].content
    assert b"John" not in doc.pages[1].content


def test_redact_texts_case_insensitive() -> None:
    """redact_texts supports case-insensitive matching."""
    doc = _doc_with_content(b"BT /F1 12 Tf (HELLO hello HeLLo) Tj ET")

    count = doc.redact_texts({"hello": None}, case_sensitive=False)

    assert count == 3
    assert b"(HELLO)" not in doc.pages[1].content
    assert b"(hello)" not in doc.pages[1].content
    assert b"(HeLLo)" not in doc.pages[1].content


def test_redact_texts_returns_total_count() -> None:
    """redact_texts returns the sum of all pattern redactions."""
    doc = _doc_with_content(b"BT /F1 12 Tf (a b a b a b) Tj ET")

    count = doc.redact_texts({"a": None, "b": None})

    assert count == 6  # 3 'a's + 3 'b's


def test_redact_texts_with_overlay() -> None:
    """redact_texts removes text even with overlay=True."""
    doc = _doc_with_content(b"BT /F1 12 Tf (Secret) Tj ET")

    count = doc.redact_texts({"Secret": None}, overlay=True)

    assert count == 1
    # The text should be removed
    assert b"(Secret)" not in doc.pages[1].content


def test_redact_texts_empty_mapping() -> None:
    """redact_texts with empty mapping returns 0."""
    doc = _doc_with_content(b"BT /F1 12 Tf (nothing) Tj ET")

    count = doc.redact_texts({})

    assert count == 0
    assert b"(nothing)" in doc.pages[1].content


# Tests for Page.redact_texts() ------------------------------


def test_page_redact_texts_removes_multiple_patterns() -> None:
    """Page.redact_texts removes all patterns in the mapping."""
    doc = Document()
    page = doc.pages.add()
    page.add_text("Hello World", 10, 20)
    page.add_text("Hello Secret", 10, 40)

    count = page.redact_texts({"World": None, "Secret": None})

    # World appears once, Secret appears once
    assert count == 2
    assert b"(World)" not in page.content
    assert b"(Secret)" not in page.content


def test_page_redact_texts_with_max_count() -> None:
    """Page.redact_texts respects max_count."""
    doc = Document()
    page = doc.pages.add()
    page.add_text("cat cat cat cat", 10, 20)

    count = page.redact_texts({"cat": None}, max_count=2)

    assert count == 2


def test_page_redact_texts_with_regex() -> None:
    """Page.redact_texts supports regex patterns."""
    import re

    doc = Document()
    page = doc.pages.add()
    page.add_text("ID: 12345", 10, 20)

    count = page.redact_texts({re.compile(r"\d+"): None})

    assert count == 1
    assert b"(ID: )" in page.content
    assert b"12345" not in page.content


def test_page_redact_texts_with_overlay() -> None:
    """Page.redact_texts draws overlay bars when overlay=True."""
    doc = Document()
    page = doc.pages.add()
    page.add_text("Confidential", 10, 20)

    count = page.redact_texts({"Confidential": None}, overlay=True)

    assert count == 1
    assert b"(Confidential)" not in page.content

