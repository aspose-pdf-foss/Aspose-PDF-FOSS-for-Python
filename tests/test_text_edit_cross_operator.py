"""Cross-show-operator phrase matching for replace/redact.

A phrase split across several *consecutive* text-showing operators (including
line-moving operators ``'``/``"``, e.g. two adjacent ``Tj``, or a ``Tj`` followed
by a ``'`` or ``TJ``) is matched as one logical string when the operators are
separated only by positionally-neutral operators. The replacement lands in the
element holding the match start and the remaining matched characters are removed
from the others; any positioning/font/CTM change starts a new run.
"""

from __future__ import annotations

from aspose_pdf import Document
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.engine.text_edit import (
    _decode_operand,
    _group_show_runs,
    _lex,
    redact_text_in_content,
    replace_text_in_content,
)
from aspose_pdf.engine.text_locate import SimpleFontMetric, locate_matches


def _content(text: str) -> bytes:
    return text.encode("latin-1")


def _joined(content: bytes) -> str:
    return "".join(
        _decode_operand(t.value)[0] for t in _lex(content) if t.kind == "string"
    )


# --- replace / redact across adjacent show operators -----------------------


def test_replace_phrase_across_two_adjacent_tj() -> None:
    out, count = replace_text_in_content(
        _content("BT (Hello ) Tj (World) Tj ET"), "Hello World", "Hi"
    )
    assert count == 1
    # Replacement in the first operator; matched chars removed from the second.
    assert out == _content("BT (Hi) Tj () Tj ET")


def test_replace_phrase_across_tj_then_tj_partial() -> None:
    # "loWor" spans the end of the first Tj and the start of the second.
    out, count = replace_text_in_content(
        _content("BT (Hello) Tj (World) Tj ET"), "loWor", "X"
    )
    assert count == 1
    assert out == _content("BT (HelX) Tj (ld) Tj ET")


def test_replace_phrase_across_tj_and_tj_array() -> None:
    out, count = replace_text_in_content(
        _content("BT (Hel) Tj [(lo )-30(Wor)] TJ (ld) Tj ET"),
        "Hello World",
        "Hi",
    )
    assert count == 1
    assert _joined(out) == "Hi"


def test_redact_phrase_across_two_tj_removes_all() -> None:
    out, count = redact_text_in_content(
        _content("BT (Secret ) Tj (Data) Tj ET"), "Secret Data"
    )
    assert count == 1
    assert b"Secret" not in out and b"Data" not in out


def test_neutral_color_operator_does_not_break_run() -> None:
    out, count = replace_text_in_content(
        _content("BT (Hel) Tj 1 0 0 rg (lo) Tj ET"), "Hello", "Hi"
    )
    assert count == 1
    assert out == _content("BT (Hi) Tj 1 0 0 rg () Tj ET")


def test_marked_content_does_not_break_run() -> None:
    out, count = replace_text_in_content(
        _content("BT /Span <</MCID 0>> BDC (Hel) Tj EMC (lo) Tj ET"),
        "Hello",
        "Hi",
    )
    assert count == 1
    assert _joined(out) == "Hi"


# --- run boundaries --------------------------------------------------------


def test_positioning_operator_breaks_run() -> None:
    # A Td between the two Tj starts a new run, so "Hello" does not match across.
    content = _content("BT (Hel) Tj 5 0 Td (lo) Tj ET")
    out, count = replace_text_in_content(content, "Hello", "Hi")
    assert count == 0
    assert out == content


def test_font_change_breaks_run() -> None:
    content = _content("BT /F1 10 Tf (Hel) Tj /F2 10 Tf (lo) Tj ET")
    out, count = replace_text_in_content(content, "Hello", "Hi")
    assert count == 0
    assert out == content


def test_line_show_operator_does_not_break_run() -> None:
    # The quote operator shows text but doesn't break the run; phrases can cross.
    content = _content("BT (Hel) Tj (lo) ' ET")
    out, count = replace_text_in_content(content, "Hello", "Hi")
    assert count == 1
    # Replacement in the first operator; matched chars removed from the second.
    assert out == _content("BT (Hi) Tj () ' ET")


def test_phrase_across_double_quote_operator() -> None:
    # The double-quote operator also allows phrase matching across its boundary.
    content = _content("BT (Hel) Tj 0 0.1 (lo) \" ET")
    out, count = replace_text_in_content(content, "Hello", "Hi")
    assert count == 1
    assert out == _content("BT (Hi) Tj 0 0.1 () \" ET")


def test_bt_et_boundary_breaks_run() -> None:
    content = _content("BT (Hel) Tj ET BT (lo) Tj ET")
    out, count = replace_text_in_content(content, "Hello", "Hi")
    assert count == 0
    assert out == content


# --- counting / grouping ---------------------------------------------------


def test_cross_operator_match_counts_once() -> None:
    out, count = replace_text_in_content(
        _content("BT (foo) Tj (bar) Tj (foo) Tj (bar) Tj ET"),
        "foobar",
        "X",
        max_count=1,
    )
    assert count == 1
    # Only the first spanning occurrence is replaced.
    assert out == _content("BT (X) Tj () Tj (foo) Tj (bar) Tj ET")


def test_group_show_runs_joins_adjacent_and_splits_on_position() -> None:
    tokens = _lex(_content("BT (a) Tj (b) Tj 5 0 Td (c) Tj ET"))
    groups = _group_show_runs(tokens)
    joined = ["".join(_decode_operand(t.value)[0] for t in g) for g in groups]
    assert joined == ["ab", "c"]


# --- public API + overlay alignment ----------------------------------------


def test_public_api_replace_across_operators() -> None:
    doc = Document()
    doc._engine_pdf = SimplePdf(
        pages=[(0.0, 0.0, 200.0, 200.0)],
        page_contents=[_content("BT /F1 12 Tf (Hello ) Tj (World) Tj ET")],
    )
    assert doc.replace_text("Hello World", "Hi all") == 1
    content = doc.pages[0].content
    assert b"(Hi all)" in content
    assert b"(World)" not in content


def test_overlay_locates_match_spanning_two_tj() -> None:
    font = SimpleFontMetric(width_of=lambda code: 500.0)
    fonts = lambda name: font if name == "F1" else None  # noqa: E731
    quads = locate_matches(
        b"BT /F1 10 Tf 100 200 Td (Hello ) Tj (World) Tj ET", "Hello World", fonts
    )
    # One contiguous box spanning both operators (11 glyphs * 5 units).
    assert len(quads) == 1
    (x0, _y0), (x1, _y1), _tr, _tl = quads[0]
    assert round(x0, 1) == 100.0
    assert round(x1, 1) == 155.0
