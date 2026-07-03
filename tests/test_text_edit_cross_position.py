"""Phrase matching across positioning operators and font changes.

With advance widths available, a positioning operator (``Td``/``TD``/``Tm``/
``T*``) that continues the same baseline within a small horizontal gap keeps
the logical run open: a word-sized gap is matched as a single synthetic space,
a kerning-sized gap contributes nothing. Font and text-state changes
(``Tf``/``Tc``/``Tw``/``Tz``/``TL``) never move the pen and never break runs.
Without metrics the pen cannot be tracked, so positioning operators break runs
exactly as before.
"""

from __future__ import annotations

from aspose_pdf import Document
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName, PdfNumber
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.engine.text_edit import (
    redact_text_in_content,
    replace_text_in_content,
)
from aspose_pdf.engine.text_locate import SimpleFontMetric, locate_matches

# A constant-width (500/1000) font so gaps and coordinates are exact.
_FONT = SimpleFontMetric(width_of=lambda code: 500.0)
_METRICS = lambda name: _FONT if name in ("F1", "F2") else None  # noqa: E731


def _content(text: str) -> bytes:
    return text.encode("latin-1")


# --- joining across positioning operators (editor) ---------------------------


def test_word_gap_td_joins_with_synthetic_space() -> None:
    # "Hello" is 25 units wide at size 10; Td 28 leaves a 3-unit (0.3 em) gap.
    content = _content("BT /F1 10 Tf (Hello) Tj 28 0 Td (World) Tj ET")
    out, count = replace_text_in_content(
        content, "Hello World", "Hi", metric_for_name=_METRICS
    )
    assert count == 1
    assert out == _content("BT /F1 10 Tf (Hi) Tj 28 0 Td () Tj ET")


def test_kerning_gap_td_joins_without_space() -> None:
    # "Hel" is 15 units wide; Td 15.5 leaves a 0.05 em gap -> no space.
    content = _content("BT /F1 10 Tf (Hel) Tj 15.5 0 Td (lo) Tj ET")
    out, count = replace_text_in_content(
        content, "Hello", "Hi", metric_for_name=_METRICS
    )
    assert count == 1
    assert out == _content("BT /F1 10 Tf (Hi) Tj 15.5 0 Td () Tj ET")


def test_tm_continuation_joins() -> None:
    content = _content(
        "BT /F1 10 Tf 1 0 0 1 100 200 Tm (Hello) Tj 1 0 0 1 128 200 Tm (World) Tj ET"
    )
    out, count = redact_text_in_content(
        content, "Hello World", metric_for_name=_METRICS
    )
    assert count == 1
    assert b"(Hello)" not in out and b"(World)" not in out


def test_baseline_change_breaks_run() -> None:
    content = _content("BT /F1 10 Tf (Hel) Tj 15 -5 Td (lo) Tj ET")
    out, count = replace_text_in_content(
        content, "Hello", "Hi", metric_for_name=_METRICS
    )
    assert count == 0
    assert out == content


def test_wide_gap_breaks_run() -> None:
    # Td 40 leaves a 15-unit (1.5 em) gap -> too far to be one phrase.
    content = _content("BT /F1 10 Tf (Hello) Tj 40 0 Td (World) Tj ET")
    out, count = replace_text_in_content(
        content, "Hello World", "Hi", metric_for_name=_METRICS
    )
    assert count == 0
    assert out == content


def test_backwards_jump_breaks_run() -> None:
    # Td 0 0 returns the pen to the line start (25 units backwards).
    content = _content("BT /F1 10 Tf (Hello) Tj 0 0 Td (World) Tj ET")
    out, count = replace_text_in_content(
        content, "HelloWorld", "X", metric_for_name=_METRICS
    )
    assert count == 0
    assert out == content


def test_without_metrics_positioning_still_breaks() -> None:
    content = _content("BT /F1 10 Tf (Hello) Tj 28 0 Td (World) Tj ET")
    out, count = replace_text_in_content(content, "Hello World", "Hi")
    assert count == 0
    assert out == content


def test_search_matching_only_the_synthetic_gap_is_not_counted() -> None:
    content = _content("BT /F1 10 Tf (Hello) Tj 28 0 Td (World) Tj ET")
    out, count = redact_text_in_content(content, " ", metric_for_name=_METRICS)
    assert count == 0
    assert out == content


def test_unknown_font_glyphs_disable_position_join() -> None:
    # /FX has no metrics: the pen is unknown after showing its text, so the
    # following Td cannot prove continuation and must break the run.
    content = _content("BT /FX 10 Tf (Hello) Tj 28 0 Td (World) Tj ET")
    out, count = replace_text_in_content(
        content, "Hello World", "Hi", metric_for_name=_METRICS
    )
    assert count == 0
    assert out == content


# --- locator ------------------------------------------------------------------


def test_locate_box_spans_positioning_gap() -> None:
    quads = locate_matches(
        b"BT /F1 10 Tf 100 200 Td (Hello) Tj 28 0 Td (World) Tj ET",
        "Hello World",
        _METRICS,
    )
    assert len(quads) == 1
    (x0, _y0), (x1, _y1), _tr, _tl = quads[0]
    # Box covers "Hello" (25), the 3-unit gap, and "World" (25).
    assert round(x0, 1) == 100.0
    assert round(x1, 1) == 153.0


def test_locate_match_across_line_break_yields_one_quad_per_line() -> None:
    quads = locate_matches(
        b"BT /F1 10 Tf 100 200 Td 14 TL (Hel) Tj (lo) ' ET", "Hello", _METRICS
    )
    assert len(quads) == 2
    (ax0, ay0), (ax1, _), _atr, _atl = quads[0]
    (bx0, by0), (bx1, _), _btr, _btl = quads[1]
    # First line: "Hel" at y=200; second line: "lo" one leading (14) below.
    assert (round(ax0, 1), round(ax1, 1)) == (100.0, 115.0)
    assert (round(bx0, 1), round(bx1, 1)) == (100.0, 110.0)
    assert round(ay0 - by0, 1) == 14.0


# --- public API (COS-backed document) ----------------------------------------


def _doc_with_widths_font() -> Document:
    # "Hello" is 30 units wide at size 12; Td 33.6 leaves a 3.6-unit gap.
    content = _content("BT /F1 12 Tf 20 40 Td (Hello) Tj 33.6 0 Td (World) Tj ET")
    pdf = SimplePdf(pages=[(0.0, 0.0, 300.0, 80.0)], page_contents=[content])
    pdf._ensure_cos()
    font = PdfDictionary(
        {
            PdfName("Type"): PdfName("Font"),
            PdfName("Subtype"): PdfName("Type1"),
            PdfName("BaseFont"): PdfName("TestFlat"),
            PdfName("FirstChar"): PdfNumber(32),
            PdfName("Widths"): PdfArray([PdfNumber(500) for _ in range(95)]),
        }
    )
    fonts = pdf._ensure_resource_subdict(0, "Font")
    fonts.mapping[PdfName("F1")] = pdf._cos_doc.register_object(font)
    doc = Document()
    doc._engine_pdf = pdf
    return doc


def test_public_redact_overlay_across_positioning_gap() -> None:
    doc = _doc_with_widths_font()
    assert doc.pages[0].redact_text("Hello World", overlay=True) == 1
    content = doc.pages[0].content
    assert b"(Hello)" not in content and b"(World)" not in content
    assert b"h f" in content  # a filled overlay path was appended
    # One bar from x=20 to 20 + 33.6 + 30 = 83.6 on the shared baseline.
    assert b"20.000" in content
    assert b"83.600" in content
