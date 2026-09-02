"""Rich-text (/RC, /RV) parsing and appearance rendering."""

from __future__ import annotations

import pytest

from aspose_pdf.engine.appearance import build_appearance
from aspose_pdf.engine.rich_text import (
    RichStyle,
    _parse_color,
    build_rich_text_content,
    parse_rich_text,
)

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parse_bold_italic_colour_size():
    rc = (
        '<body><p><span style="font-size:14pt;color:#ff0000;font-weight:bold">A'
        "</span><i>B</i></p></body>"
    )
    (runs,) = parse_rich_text(rc, RichStyle())
    assert runs[0].text == "A"
    assert runs[0].style.size == 14.0
    assert runs[0].style.bold and not runs[0].style.italic
    assert runs[0].style.color == "1 0 0 rg"
    assert runs[1].text == "B" and runs[1].style.italic


def test_parse_paragraphs_and_alignment():
    rc = (
        '<body><p style="text-align:center">one</p>'
        '<p style="text-align:right">two</p></body>'
    )
    paras = parse_rich_text(rc, RichStyle())
    assert len(paras) == 2
    assert paras[0][0].style.align == 1
    assert paras[1][0].style.align == 2


def test_parse_br_breaks_into_lines():
    paras = parse_rich_text("<body><p>a<br/>b</p></body>", RichStyle())
    assert [r.text for p in paras for r in p] == ["a", "b"]
    assert len(paras) == 2


def test_parse_nested_styles_pop_correctly():
    rc = "<body><p><b>bold <i>both</i></b> plain</p></body>"
    (runs,) = parse_rich_text(rc, RichStyle())
    assert runs[0].style.bold and not runs[0].style.italic  # "bold "
    assert runs[1].style.bold and runs[1].style.italic      # "both"
    assert not runs[2].style.bold and not runs[2].style.italic  # " plain"


def test_colour_parsing():
    assert _parse_color("#00FF00") == "0 1 0 rg"
    assert _parse_color("#0f0") == "0 1 0 rg"
    assert _parse_color("rgb(255, 0, 0)") == "1 0 0 rg"
    assert _parse_color("blue") == "0 0 1 rg"
    assert _parse_color("not-a-colour") is None


# ---------------------------------------------------------------------------
# Layout / rendering
# ---------------------------------------------------------------------------


def test_build_uses_bold_font_and_colour():
    rc = '<body><p><span style="font-weight:bold;color:#0000ff">Hi</span></p></body>'
    body, fonts = build_rich_text_content(rc, 200, 60, default_style=RichStyle())
    assert "HeBo" in fonts and fonts["HeBo"]["BaseFont"] == "Helvetica-Bold"
    assert "0 0 1 rg" in body
    assert any("(Hi) Tj" in ln for ln in body)


def test_build_wraps_to_width():
    rc = "<body><p>" + " ".join(["word"] * 30) + "</p></body>"
    body, _fonts = build_rich_text_content(rc, 120, 300, default_style=RichStyle(size=12))
    baselines = {ln.split()[5] for ln in body if ln.endswith(" Tm")}
    assert len(baselines) >= 2  # wrapped across several lines


def test_build_default_style_seeds_plain_text():
    body, _fonts = build_rich_text_content(
        "<body><p>plain</p></body>", 200, 50, default_style=RichStyle(size=10, color="0 0 1 rg")
    )
    assert "/Helv 10 Tf" in body
    assert "0 0 1 rg" in body


def test_build_empty_returns_none():
    assert build_rich_text_content("<body></body>", 100, 50, default_style=RichStyle()) is None
    assert build_rich_text_content("", 100, 50, default_style=RichStyle()) is None


# ---------------------------------------------------------------------------
# Font families
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("css", "resource", "base"),
    [
        ("serif", "TiRo", "Times-Roman"),
        ("monospace", "Cour", "Courier"),
        ("sans-serif", "Helv", "Helvetica"),
        ("Times New Roman", "TiRo", "Times-Roman"),
        ("Courier New", "Cour", "Courier"),
        ("Arial", "Helv", "Helvetica"),
    ],
)
def test_font_family_selects_a_standard_14_face(css, resource, base):
    rc = f'<body><p style="font-family:{css}">Text</p></body>'
    _body, fonts = build_rich_text_content(rc, 300, 60, default_style=RichStyle())

    assert list(fonts) == [resource]
    assert fonts[resource]["BaseFont"] == base


@pytest.mark.parametrize(
    ("css", "resource"),
    [("Book Antiqua", "TiRo"), ("Gill Sans", "Helv"), ("Courier New", "Cour")],
)
def test_a_family_written_with_spaces_is_still_recognised(css, resource):
    """CSS writes font names with spaces; the keyword table has none.

    ``Book Antiqua`` and ``Gill Sans`` are the families this turns on: their
    keywords exist only in the run-together form a PDF ``/BaseFont`` uses, so
    matching without dropping separators first misses them entirely.
    """
    rc = f'<body><p style="font-family:{css}">Text</p></body>'
    _body, fonts = build_rich_text_content(rc, 300, 60, default_style=RichStyle())

    assert list(fonts) == [resource]


def test_a_font_stack_takes_the_first_name_it_recognises():
    """The rest of a stack is the fallback list it was written to avoid."""
    rc = (
        "<body><p style=\"font-family:'Nonesuch Display', Georgia, monospace\">"
        "Text</p></body>"
    )
    _body, fonts = build_rich_text_content(rc, 300, 60, default_style=RichStyle())

    assert fonts["TiRo"]["BaseFont"] == "Times-Roman"


def test_an_unrecognised_family_keeps_the_one_it_inherited():
    """Falling back to the default would override a deliberate choice."""
    rc = (
        '<body><p style="font-family:serif">outer '
        '<span style="font-family:Wingdings">inner</span></p></body>'
    )
    _body, fonts = build_rich_text_content(rc, 300, 60, default_style=RichStyle())

    assert list(fonts) == ["TiRo"]


@pytest.mark.parametrize("family", ["serif", "monospace"])
def test_bold_and_italic_pick_that_family_s_own_faces(family):
    rc = (
        f'<body><p style="font-family:{family}">'
        "<b>b</b><i>i</i><b><i>bi</i></b></p></body>"
    )
    _body, fonts = build_rich_text_content(rc, 300, 60, default_style=RichStyle())

    bases = sorted(spec["BaseFont"] for spec in fonts.values())
    if family == "serif":
        assert bases == ["Times-Bold", "Times-BoldItalic", "Times-Italic"]
    else:
        assert bases == ["Courier-Bold", "Courier-BoldOblique", "Courier-Oblique"]


@pytest.mark.parametrize("tag", ["tt", "code", "kbd", "samp"])
def test_the_html_monospace_tags_are_honoured(tag):
    rc = f"<body><p>a <{tag}>x=1</{tag}> b</p></body>"
    _body, fonts = build_rich_text_content(rc, 300, 60, default_style=RichStyle())

    assert fonts["Cour"]["BaseFont"] == "Courier"
    assert fonts["Helv"]["BaseFont"] == "Helvetica"  # the surrounding text


def test_each_family_is_measured_with_its_own_advances():
    """Laying Times or Courier out on Helvetica's widths misplaces every word.

    Courier is fixed-pitch, so four characters occupy exactly four times the
    advance whatever they are -- which is the sharpest evidence that the
    family's own metrics were used.
    """
    from aspose_pdf.engine.rich_text import _measure

    narrow, wide = "iiii", "WWWW"
    assert _measure(narrow, RichStyle(size=10, family="mono")) == pytest.approx(
        _measure(wide, RichStyle(size=10, family="mono"))
    )
    for family in ("sans", "serif"):
        style = RichStyle(size=10, family=family)
        assert _measure(narrow, style) < _measure(wide, style)

    sans = _measure("Wiii", RichStyle(size=10, family="sans"))
    serif = _measure("Wiii", RichStyle(size=10, family="serif"))
    assert sans != serif


def test_the_default_family_seeds_markup_that_names_none():
    rc = "<body><p>plain</p></body>"
    _body, fonts = build_rich_text_content(
        rc, 300, 60, default_style=RichStyle(family="serif")
    )

    assert fonts["TiRo"]["BaseFont"] == "Times-Roman"


# ---------------------------------------------------------------------------
# FreeText /RC integration
# ---------------------------------------------------------------------------


def test_freetext_rc_renders_rich_text():
    rc = '<body><p><span style="font-weight:bold">Bold</span> plain</p></body>'
    gen = build_appearance("FreeText", (0, 0, 200, 60), {"RC": rc, "DA": "/Helv 12 Tf 0 g"})
    assert gen is not None
    assert "HeBo" in gen.fonts and "Helv" in gen.fonts
    assert b"(Bold) Tj" in gen.content and b"(plain) Tj" in gen.content


def test_freetext_falls_back_to_contents_without_rc():
    gen = build_appearance(
        "FreeText", (0, 0, 200, 60), {"Contents": "hello", "DA": "/Helv 12 Tf 0 g"}
    )
    assert b"(hello) Tj" in gen.content
    assert set(gen.fonts) == {"Helv"}
