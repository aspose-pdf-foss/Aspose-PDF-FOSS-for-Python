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

    assert b"(dog dog)" in doc.pages[0].content
    assert b"(cat)" in doc.pages[1].content


def test_replace_text_can_target_one_page() -> None:
    doc = _doc_with_content(
        b"BT /F1 12 Tf (alpha) Tj ET",
        b"BT /F1 12 Tf (alpha) Tj ET",
    )

    assert doc.replace_text("alpha", "beta", page_index=1) == 1

    assert b"(alpha)" in doc.pages[0].content
    assert b"(beta)" in doc.pages[1].content


def test_replace_text_handles_tj_array_elements() -> None:
    doc = _doc_with_content(b"BT /F1 12 Tf [(Hello) 120 (World)] TJ ET")

    assert doc.replace_text("World", "PDF") == 1

    assert b"[(Hello) 120 (PDF)] TJ" in doc.pages[0].content


def test_replace_text_handles_hex_string_operand() -> None:
    doc = _doc_with_content(b"BT /F1 12 Tf <48656C6C6F> Tj ET")

    assert doc.replace_text("Hello", "Hi") == 1

    assert b"<4869>" in doc.pages[0].content


def test_replace_text_case_insensitive() -> None:
    doc = _doc_with_content(b"BT /F1 12 Tf (Hello HELLO hello) Tj ET")

    assert doc.replace_text("hello", "hi", case_sensitive=False) == 3

    assert b"(hi hi hi)" in doc.pages[0].content


def test_redact_text_removes_simple_operand_text() -> None:
    doc = _doc_with_content(b"BT /F1 12 Tf (Public Secret) Tj ET")

    assert doc.redact_text("Secret") == 1

    assert b"Secret" not in doc.pages[0].content
    assert b"(Public )" in doc.pages[0].content


def test_replace_text_persists_after_save_reload() -> None:
    doc = _doc_with_content(b"BT /F1 12 Tf (before) Tj ET")
    doc.replace_text("before", "after")
    buf = io.BytesIO()

    doc.save(buf)
    buf.seek(0)
    reloaded = Document().load_from(buf)

    assert b"(after)" in reloaded.pages[0].content


def test_replace_text_rejects_unencodable_replacement() -> None:
    doc = _doc_with_content(b"BT /F1 12 Tf (hello) Tj ET")

    with pytest.raises(PdfValidationException):
        doc.replace_text("hello", "snowman \u2603")


def test_replace_text_joins_split_tj_fragments() -> None:
    # A phrase split across TJ elements (common with kerning) is matched as one
    # string: the replacement lands in the first element, the rest is removed.
    doc = _doc_with_content(b"BT /F1 12 Tf [(Hel) 0 (lo)] TJ ET")

    assert doc.replace_text("Hello", "Hi") == 1

    content = doc.pages[0].content
    assert b"(Hi)" in content
    assert b"(Hel)" not in content and b"(lo)" not in content
