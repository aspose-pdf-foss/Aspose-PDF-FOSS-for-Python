"""Multi-element ``TJ`` phrase matching for replace/redact.

A phrase split across several ``TJ`` string elements (common with kerning) is
matched as one logical string: the replacement is placed in the element holding
the match start and the remaining matched characters are removed from the other
elements, leaving the kerning adjustments and unmatched elements intact.
"""

from __future__ import annotations

from aspose_pdf import Document
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.engine.text_edit import (
    _decode_operand,
    _lex,
    redact_text_in_content,
    replace_text_in_content,
)


def _joined_strings(content: bytes) -> str:
    """Decode and concatenate every string operand in *content*."""
    return "".join(
        _decode_operand(t.value)[0] for t in _lex(content) if t.kind == "string"
    )


def _content(text: str) -> bytes:
    return text.encode("latin-1")


def test_replace_phrase_split_across_two_elements() -> None:
    out, count = replace_text_in_content(
        _content("BT [(Hel)-30(lo)] TJ ET"), "Hello", "Hi"
    )
    assert count == 1
    # Replacement lands in the first element; the rest of the match is removed.
    assert out == _content("BT [(Hi)-30()] TJ ET")


def test_replace_phrase_split_across_three_elements_preserves_kerning() -> None:
    out, count = replace_text_in_content(
        _content("BT [(Hel)-30(lo )100(Wor)20(ld)] TJ ET"), "Hello World", "Hi all"
    )
    assert count == 1
    assert out == _content("BT [(Hi all)-30()100()20()] TJ ET")


def test_redact_phrase_split_across_elements_removes_all_matched() -> None:
    out, count = redact_text_in_content(
        _content("BT [(Hel)-30(lo)] TJ ET"), "Hello"
    )
    assert count == 1
    assert b"Hel" not in out and b"lo" not in out
    assert out == _content("BT [()-30()] TJ ET")


def test_match_spanning_hex_and_literal_elements() -> None:
    # <48656C> = "Hel" (hex) + (lo) literal == "Hello"; each element keeps its
    # own style when rewritten.
    out, count = replace_text_in_content(
        _content("BT [<48656C>(lo)] TJ ET"), "Hello", "Hi"
    )
    assert count == 1
    assert out == _content("BT [<4869>()] TJ ET")  # "Hi" re-encoded as hex


def test_match_within_single_element_unchanged_behavior() -> None:
    out, count = replace_text_in_content(_content("BT (hello world) Tj ET"), "world", "PDF")
    assert count == 1
    assert out == _content("BT (hello PDF) Tj ET")


def test_unmatched_elements_left_byte_for_byte() -> None:
    # Only the matched span is touched; the trailing element is untouched.
    out, count = replace_text_in_content(
        _content("BT [(Hel)-30(lo)-50(!)] TJ ET"), "Hello", "Hi"
    )
    assert count == 1
    assert out == _content("BT [(Hi)-30()-50(!)] TJ ET")


def test_utf16be_segments_join_and_replace() -> None:
    # Two UTF-16BE elements "Hel" + "lo" -> match "Hello".
    seg1 = b"\xfe\xff" + "Hel".encode("utf-16-be")
    seg2 = b"\xfe\xff" + "lo".encode("utf-16-be")
    content = b"BT [(" + seg1 + b")(" + seg2 + b")] TJ ET"
    out, count = replace_text_in_content(content, "Hello", "Hi")
    assert count == 1
    assert _joined_strings(out) == "Hi"  # decodes back through the UTF-16BE BOM


def test_max_count_counts_spanning_match_once() -> None:
    content = _content("BT [(Hel)0(lo)] TJ (Hello) Tj ET")
    out, count = replace_text_in_content(content, "Hello", "Hi", max_count=1)
    assert count == 1
    # Only the first (spanning) occurrence is replaced; the later one remains.
    assert out == _content("BT [(Hi)0()] TJ (Hello) Tj ET")


def test_case_insensitive_spanning_match() -> None:
    out, count = replace_text_in_content(
        _content("BT [(HEL)0(LO)] TJ ET"), "hello", "hi", case_sensitive=False
    )
    assert count == 1
    assert out == _content("BT [(hi)0()] TJ ET")


def test_no_match_leaves_content_untouched() -> None:
    content = _content("BT [(Hel)0(lo)] TJ ET")
    out, count = replace_text_in_content(content, "Goodbye", "Hi")
    assert count == 0
    assert out == content


def test_public_api_replace_split_phrase() -> None:
    doc = Document()
    doc._engine_pdf = SimplePdf(
        pages=[(0.0, 0.0, 200.0, 200.0)],
        page_contents=[_content("BT /F1 12 Tf [(Wor)-20(ld) -40 (Peace)] TJ ET")],
    )
    assert doc.replace_text("World", "Earth") == 1
    content = doc.pages[0].content
    assert b"(Earth)" in content
    assert b"(Wor)" not in content and b"(ld)" not in content
    assert b"(Peace)" in content  # untouched element preserved
