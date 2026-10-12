"""Writing a page's words as ``<text>`` rather than as curves.

The SVG export draws every glyph as the outline its font gives it, which is
exact and about as selectable as a photograph of a page: no reader can search
it, copy from it, or read it aloud. ``text="text"`` writes ``<text>`` elements
instead, and ``text="both"`` keeps the outlines and puts unfilled ``<text>``
over them -- the page looks exactly as it did and the words are there.

Two things decide whether this is any good. The first is **placement**: a
viewer draws with whatever font it resolves the name to, so the advances will
not match the PDF's -- unless every glyph carries its own position, which is
what the ``x`` list is for. The second is **honesty**: a glyph whose meaning
nothing in the file states gets no ``<text>``, because a guess would put the
wrong word in someone's copy buffer; its outlines are drawn instead, so it is
still on the page.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET

import pytest

from aspose_pdf import Document, PageSize
from aspose_pdf.engine.cos import PdfName
from aspose_pdf.engine.std_font_data import load_substitute_sfnt
from aspose_pdf.exceptions import PdfValidationException

_TEXT_ELEMENT = re.compile(r"<text ([^>]*)>(.*?)</text>", re.DOTALL)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reopen(document: Document) -> Document:
    buffer = io.BytesIO()
    document.save(buffer)
    document.close()
    return Document(buffer.getvalue())


def _page(*lines, size: float = 20.0) -> Document:
    """A document whose page carries *lines* of Standard-14 text."""
    document = Document()
    page = document.pages.add(PageSize.A4)
    for index, line in enumerate(lines):
        page.add_text(line, 50, 760 - index * 40, font_size=size)
    return _reopen(document)


def _elements(svg: str) -> list[tuple[dict[str, str], str]]:
    """Every ``<text>`` in *svg*, as ``(attributes, its text)``."""
    out = []
    for attributes, body in _TEXT_ELEMENT.findall(svg):
        found = dict(re.findall(r'([\w:-]+)="([^"]*)"', attributes))
        out.append((found, re.sub(r"<[^>]+>", "", body)))
    return out


def _strings(svg: str) -> list[str]:
    return [body for _attributes, body in _elements(svg)]


def _matrix(attributes: dict[str, str]) -> list[float]:
    inside = attributes["transform"][len("matrix(") : -1]
    return [float(value) for value in inside.split(",")]


# ---------------------------------------------------------------------------
# The three modes
# ---------------------------------------------------------------------------


def test_outlines_is_the_default_and_writes_no_text():
    with _page("Hello") as document:
        svg = document.pages[0].to_svg()
        assert svg == document.pages[0].to_svg(text="outlines")
        assert "<text " not in svg
        assert "<path" in svg


def test_text_mode_writes_text_and_stops_drawing_the_curves():
    with _page("Hello") as document:
        outlines = document.pages[0].to_svg(text="outlines")
        svg = document.pages[0].to_svg(text="text")
        assert _strings(svg) == ["Hello"]
        assert svg.count("<path") < outlines.count("<path")
        assert len(svg) < len(outlines)


def test_both_keeps_the_curves_and_adds_the_words_over_them():
    with _page("Hello") as document:
        outlines = document.pages[0].to_svg(text="outlines")
        svg = document.pages[0].to_svg(text="both")
        assert _strings(svg) == ["Hello"]
        assert svg.count("<path") == outlines.count("<path")
        (attributes, _body) = _elements(svg)[0]
        assert attributes["fill"] == "none", "the outlines are what is seen"


@pytest.mark.parametrize("mode", ["Outlines", "selectable", "", None, 1])
def test_a_mode_that_is_not_one_of_the_three_is_refused(mode):
    with _page("Hello") as document, pytest.raises(PdfValidationException):
        document.pages[0].to_svg(text=mode)


def test_every_mode_produces_well_formed_xml():
    with _page("Angles < > and ampersands &", "second line") as document:
        for mode in ("outlines", "text", "both"):
            svg = document.pages[0].to_svg(text=mode)
            ET.fromstring(svg)  # raises if it is not well formed


def test_the_characters_that_end_an_element_are_escaped():
    with _page("a < b & c > d") as document:
        svg = document.pages[0].to_svg(text="text")
        assert "&lt;" in svg and "&amp;" in svg and "&gt;" in svg
        assert _strings(svg) == ["a &lt; b &amp; c &gt; d"]


# ---------------------------------------------------------------------------
# What the text says
# ---------------------------------------------------------------------------


def test_the_words_are_the_ones_the_page_extracts():
    with _page("First line here", "Second line there") as document:
        svg = document.pages[0].to_svg(text="text")
        assert _strings(svg) == document.pages[0].extract_text().split("\n")


def test_an_embedded_composite_font_reads_through_its_to_unicode():
    document = Document()
    page = document.pages.add(PageSize.A4)
    page.add_text(
        "Zażółć gęślą jaźń", 50, 700, font=load_substitute_sfnt("sans-regular")
    )
    with _reopen(document) as saved:
        assert _strings(saved.pages[0].to_svg(text="text")) == ["Zażółć gęślą jaźń"]


def test_a_glyph_nothing_explains_keeps_its_outlines_and_gets_no_text():
    """A guess in the copy buffer is worse than no word at all."""
    document = Document()
    page = document.pages.add(PageSize.A4)
    page.add_text("Mappable", 50, 760, font_size=20)
    page.add_text(
        "Unmappable", 50, 700, font=load_substitute_sfnt("sans-regular"), font_size=20
    )
    saved = _reopen(document)
    engine = saved._engine_pdf
    for font, _where in engine._rendering_font_dictionaries():
        if engine._get_name(font.mapping.get(PdfName("Subtype"))) == "Type0":
            font.mapping.pop(PdfName("ToUnicode"), None)

    svg = saved.pages[0].to_svg(text="text")
    assert _strings(svg) == ["Mappable"]
    assert "<path" in svg, "the word with no meaning is still drawn"
    saved.close()


def test_invisible_text_becomes_selectable():
    """Mode 3 over a scan is an OCR layer, and this is what makes it searchable."""
    document = Document()
    page = document.pages.add(PageSize.A4)
    page.add_text("Invisible but searchable", 50, 700, font_size=20)
    saved = _reopen(document)
    engine = saved._engine_pdf
    engine._ensure_page_cache()
    engine.page_contents[0] = engine.get_page_content(0).replace(b"BT", b"BT 3 Tr", 1)

    svg = saved.pages[0].to_svg(text="text")
    assert _strings(svg) == ["Invisible but searchable"]
    (attributes, _body) = _elements(svg)[0]
    assert attributes["fill"] == "none", "invisible text stays invisible"
    saved.close()


# ---------------------------------------------------------------------------
# Where the text sits
# ---------------------------------------------------------------------------


def test_every_glyph_carries_its_own_position():
    """Without that the run would be laid out by the viewer's own advances."""
    with _page("Hello") as document:
        (attributes, body) = _elements(document.pages[0].to_svg(text="text"))[0]
        xs = [float(value) for value in attributes["x"].split()]
        assert len(xs) == len(body)
        assert xs[0] == 0
        assert xs == sorted(xs), "a left-to-right run advances rightwards"


def test_the_text_is_the_right_way_up():
    """SVG's y grows downward and PDF text space grows upward."""
    with _page("Upright") as document:
        (attributes, _body) = _elements(document.pages[0].to_svg(text="text"))[0]
        a, b, c, d, _e, _f = _matrix(attributes)
        assert a > 0 and d > 0, "an unrotated page draws text upright"
        assert b == 0 and c == 0


def test_the_text_lands_where_the_page_puts_it():
    with _page("Placed") as document:
        page = document.pages[0]
        (attributes, _body) = _elements(page.to_svg(text="text"))[0]
        _a, _b, _c, _d, e, f = _matrix(attributes)
        assert e == pytest.approx(50, abs=0.5)
        # 760 up from the bottom of an A4 page is 841.89 - 760 down from the top.
        _llx, _lly, _urx, ury = page.rect
        assert f == pytest.approx(ury - 760, abs=0.5)


def test_rotated_text_carries_its_rotation():
    document = Document()
    page = document.pages.add(PageSize.A4)
    with page.graphics(transform=(0, 1, -1, 0, 400, 300)):
        page.add_text("Sideways", 0, 0, font_size=16)
    with _reopen(document) as saved:
        (attributes, body) = _elements(saved.pages[0].to_svg(text="text"))[0]
        assert body == "Sideways"
        a, b, c, d, _e, _f = _matrix(attributes)
        assert (a, d) == pytest.approx((0.0, 0.0), abs=1e-6)
        assert b and c, "the rotation is in the matrix, not lost"


# ---------------------------------------------------------------------------
# What makes one element, and what makes two
# ---------------------------------------------------------------------------


def test_two_lines_are_two_elements():
    """Gluing them would hand whoever copies the text one word made of two."""
    with _page("First", "Second") as document:
        assert _strings(document.pages[0].to_svg(text="text")) == ["First", "Second"]


def test_two_fonts_on_one_line_are_two_elements():
    document = Document()
    page = document.pages.add(PageSize.A4)
    page.add_text("Helvetica", 50, 700, font_name="Helvetica", font_size=20)
    page.add_text("Courier", 200, 700, font_name="Courier", font_size=20)
    with _reopen(document) as saved:
        elements = _elements(saved.pages[0].to_svg(text="text"))
        assert [body for _a, body in elements] == ["Helvetica", "Courier"]
        assert elements[0][0]["font-family"] != elements[1][0]["font-family"]


def test_two_colours_are_two_elements():
    document = Document()
    page = document.pages.add(PageSize.A4)
    page.add_text("Black", 50, 700, font_size=20)
    page.add_text("Red", 200, 700, font_size=20, color="#ff0000")
    with _reopen(document) as saved:
        elements = _elements(saved.pages[0].to_svg(text="text"))
        fills = {attributes["fill"] for attributes, _body in elements}
        assert fills == {"#000000", "#ff0000"}


def test_a_gap_along_one_line_does_not_split_the_run():
    """The element carries an x per glyph, so a gap is only a larger x."""
    document = Document()
    page = document.pages.add(PageSize.A4)
    page.add_text("Left", 50, 700, font_size=20)
    page.add_text("Right", 400, 700, font_size=20)
    with _reopen(document) as saved:
        assert _strings(saved.pages[0].to_svg(text="text")) == ["LeftRight"]


# ---------------------------------------------------------------------------
# Which face a viewer is asked for
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("font_name", "family", "weight", "style"),
    [
        ("Helvetica", "Helvetica, sans-serif", None, None),
        ("Helvetica-Bold", "Helvetica, sans-serif", "bold", None),
        ("Helvetica-Oblique", "Helvetica, sans-serif", None, "italic"),
        ("Times-Roman", "Times, serif", None, None),
        ("Times-BoldItalic", "Times, serif", "bold", "italic"),
        ("Courier", "Courier, monospace", None, None),
    ],
)
def test_the_face_is_described_for_a_viewer_to_match(font_name, family, weight, style):
    document = Document()
    page = document.pages.add(PageSize.A4)
    page.add_text("Sample", 50, 700, font_name=font_name, font_size=20)
    with _reopen(document) as saved:
        (attributes, _body) = _elements(saved.pages[0].to_svg(text="text"))[0]
        assert attributes["font-family"] == family
        assert attributes.get("font-weight") == weight
        assert attributes.get("font-style") == style


def test_a_font_the_viewer_cannot_have_still_gets_a_generic_family():
    """The program is in the PDF, not the SVG; the shape can still be asked for."""
    document = Document()
    page = document.pages.add(PageSize.A4)
    page.add_text("Embedded", 50, 700, font=load_substitute_sfnt("sans-regular"))
    with _reopen(document) as saved:
        (attributes, _body) = _elements(saved.pages[0].to_svg(text="text"))[0]
        assert attributes["font-family"].endswith("sans-serif")


def test_the_size_is_the_one_the_page_used():
    with _page("Sized", size=17.5) as document:
        (attributes, _body) = _elements(document.pages[0].to_svg(text="text"))[0]
        assert float(attributes["font-size"]) == pytest.approx(17.5)


# ---------------------------------------------------------------------------
# The surface
# ---------------------------------------------------------------------------


def test_save_as_svg_passes_the_mode_through(tmp_path):
    with _page("Written to a file") as document:
        target = tmp_path / "page.svg"
        document.save_as_svg(target, text="text")
        assert "<text " in target.read_text(encoding="utf-8")


def test_page_save_as_svg_passes_the_mode_through(tmp_path):
    with _page("Written to a file") as document:
        target = tmp_path / "page.svg"
        document.pages[0].save_as_svg(target, text="both")
        written = target.read_text(encoding="utf-8")
        assert "<text " in written and "<path" in written


def test_a_page_with_no_text_is_unchanged_by_the_mode():
    document = Document()
    page = document.pages.add(PageSize.A4)
    page.draw_rectangle(50, 600, 200, 80, fill_color="#cccccc")
    with _reopen(document) as saved:
        plain = saved.pages[0].to_svg(text="outlines")
        assert saved.pages[0].to_svg(text="text") == plain
        assert saved.pages[0].to_svg(text="both") == plain
