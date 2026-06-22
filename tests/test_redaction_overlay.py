"""Text position tracking and cosmetic redaction-overlay boxes.

``locate_matches`` tracks the text matrix and resolves simple-font advance
widths to return user-space quads for each match; ``redact_text(overlay=True)``
removes the text and draws a filled bar over each located run, skipping runs it
cannot position (multi-byte/unresolved fonts) since the text is already gone.
"""

from __future__ import annotations

from aspose_pdf import Document
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.engine.text_locate import SimpleFontMetric, locate_matches

# A constant-width (500/1000) font so expected coordinates are exact.
_FONT = SimpleFontMetric(width_of=lambda code: 500.0, ascent=800.0, descent=-200.0)
_FONTS = lambda name: _FONT if name == "F1" else None  # noqa: E731


def _render(doc, antialias=False):
    return doc.pages[0].render(antialias=antialias)


# --- locator ---------------------------------------------------------------


def test_locate_simple_tj_rectangle():
    quads = locate_matches(b"BT /F1 10 Tf 100 200 Td (Hello World) Tj ET", "World", _FONTS)
    assert len(quads) == 1
    (x0, y0), (x1, _y1), _tr, _tl = quads[0]
    # "World" starts after "Hello " (6 glyphs * 5 units) at x=100; 5 glyphs wide.
    assert round(x0, 1) == 130.0
    assert round(x1, 1) == 155.0
    assert round(y0, 1) == 198.0  # baseline 200 + descent (-200/1000*10)


def test_locate_match_split_across_tj_elements_single_quad():
    quads = locate_matches(b"BT /F1 10 Tf 0 0 Td [(Wor)-50(ld)] TJ ET", "World", _FONTS)
    assert len(quads) == 1
    (x0, _y0), (x1, _y1), _a, _b = quads[0]
    # 5 glyphs (25 units) minus the -50 kern (+0.5 unit) = 25.5 wide from x=0.
    assert round(x0, 1) == 0.0
    assert round(x1, 1) == 25.5


def test_locate_unresolved_font_yields_no_quads():
    quads = locate_matches(b"BT /F9 10 Tf 0 0 Td (World) Tj ET", "World", _FONTS)
    assert quads == []


def test_locate_honours_cm_scaling():
    quads = locate_matches(
        b"q 2 0 0 2 0 0 cm BT /F1 10 Tf 10 10 Td (World) Tj ET Q", "World", _FONTS
    )
    assert len(quads) == 1
    (x0, y0), *_ = quads[0]
    assert round(x0, 1) == 20.0  # 10 * 2
    assert round(y0, 1) == 16.0  # (10 + descent -2) * 2


# --- redaction overlay -----------------------------------------------------


def _authored_doc():
    doc = Document()
    doc._engine_pdf = SimplePdf(pages=[(0, 0, 300, 80)], page_contents=[b""])
    doc.pages[0].add_text("Hello World", 20, 40, font_size=24, font_name="Helvetica")
    return doc


def test_redact_overlay_removes_text_and_draws_bar():
    doc = _authored_doc()
    assert doc.pages[0].redact_text("World", overlay=True) == 1
    content = doc.pages[0].content
    assert b"World" not in content  # text removed from the stream
    assert b"h f" in content  # a filled overlay path was appended
    assert b"0 0 0 rg" in content  # default black bar


def test_redact_overlay_default_off_draws_no_bar():
    doc = _authored_doc()
    assert doc.pages[0].redact_text("World") == 1
    content = doc.pages[0].content
    assert b"World" not in content
    assert b"h f" not in content  # no overlay path when overlay is off


def test_redact_overlay_color_is_honoured():
    doc = _authored_doc()
    doc.pages[0].redact_text("World", overlay=True, overlay_color=(1, 0, 0))
    assert b"1 0 0 rg" in doc.pages[0].content


def test_redact_overlay_bar_covers_removed_region():
    doc = _authored_doc()
    before = _render(doc)
    doc.pages[0].redact_text("World", overlay=True)
    after = _render(doc)

    def dark(raster, x0, x1):
        return sum(
            1
            for y in range(raster.height)
            for x in range(x0, x1)
            if raster.get_pixel(x, y) == (0, 0, 0)
        )

    # The "World" region (right of ~x=80) gains ink (a solid bar); the "Hello"
    # region on the left is untouched.
    assert dark(after, 80, 220) > dark(before, 80, 220)
    assert dark(after, 0, 70) == dark(before, 0, 70)


def test_redact_overlay_skips_untrackable_font_but_still_removes_text():
    # No font resource is registered for /FX, so the run cannot be positioned:
    # the text is still removed, just without a cosmetic bar (safe degradation).
    doc = Document()
    doc._engine_pdf = SimplePdf(
        pages=[(0, 0, 200, 80)],
        page_contents=[b"BT /FX 12 Tf 10 20 Td (Secret) Tj ET"],
    )
    assert doc.pages[0].redact_text("Secret", overlay=True) == 1
    content = doc.pages[0].content
    assert b"Secret" not in content
    assert b"h f" not in content  # no bar drawn for the untrackable run
