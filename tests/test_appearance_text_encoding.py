"""Generated WinAnsi appearances draw and measure the requested characters."""

from io import BytesIO

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.appearance import build_button_appearance
from aspose_pdf.engine.field_appearance import _pdf_literal, _quad_x, _wrap_text
from aspose_pdf.engine.rich_text import DocumentFont, RichStyle, build_rich_text_content

TEXT = 'Price 10 € — “café” … • ™ Œ œ Š š Ž ž Ÿ'


def _save(document, tmp_path, on_disk):
    if on_disk:
        path = tmp_path / "appearance.pdf"
        document.save(path)
        return path
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def _flattened_text(document):
    document.flatten()
    return " ".join(document.pages[0].extract_text().split())


@pytest.mark.parametrize("subtype", ["FreeText", "Stamp"])
@pytest.mark.parametrize("on_disk", [False, True])
def test_annotation_winansi_text_survives_flattening(tmp_path, subtype, on_disk):
    with Document() as document:
        page = document.pages.add()
        annotation = page.annotations.add(subtype, (20, 600, 590, 750), TEXT)
        assert annotation.generate_appearance()
        with Document(_save(document, tmp_path, on_disk)) as reopened:
            assert reopened.pages[0].annotations[0].contents == TEXT
            assert _flattened_text(reopened) == TEXT


@pytest.mark.parametrize("family", ["sans-serif", "serif", "monospace"])
def test_rich_text_annotation_uses_winansi_for_every_family(tmp_path, family):
    with Document() as document:
        page = document.pages.add()
        annotation = page.annotations.add(
            "FreeText",
            (20, 500, 590, 750),
            "plain fallback",
            properties={
                "RC": f'<body><p style="font-family:{family}">{TEXT}</p></body>'
            },
        )
        assert annotation.generate_appearance()
        with Document(_save(document, tmp_path, False)) as reopened:
            assert _flattened_text(reopened) == TEXT


@pytest.mark.parametrize("kind", ["text", "combobox", "listbox", "pushbutton"])
@pytest.mark.parametrize("on_disk", [False, True])
def test_field_winansi_text_survives_flattening(tmp_path, kind, on_disk):
    with Document() as document:
        document.pages.add()
        form = document.form
        rect = (20, 600, 590, 750)
        if kind == "text":
            field = form.add_text_field("field", 0, rect, value="before")
            field.value = TEXT
        elif kind == "combobox":
            form.add_combo_box("field", 0, rect, [("export", TEXT)], value="export")
        elif kind == "listbox":
            form.add_list_box("field", 0, rect, [("export", TEXT)], value="export")
        else:
            form.add_push_button("field", 0, rect, caption=TEXT)
        with Document(_save(document, tmp_path, on_disk)) as reopened:
            assert _flattened_text(reopened) == TEXT


@pytest.mark.parametrize(
    ("text", "literal"),
    [
        ("€", r"(\200)"),
        ("—", r"(\227)"),
        ("“”", r"(\223\224)"),
        ("Œœ", r"(\214\234)"),
        ("café", r"(caf\351)"),
        ("•", r"(\225)"),
        ("a\xa0b\xadc", r"(a\240b\255c)"),
        ("A (B) \\ C\t", r"(A \(B\) \\ C\t)"),
    ],
)
def test_literals_use_winansi_codes_and_escape_pdf_delimiters(text, literal):
    assert _pdf_literal(text) == literal


def _width(code):
    return {0x97: 1000.0, 0x20: 250.0, 0x3F: 250.0}.get(code, 500.0)


def test_plain_text_wrap_and_alignment_use_em_dash_widths():
    assert _wrap_text("—— ——", 30, 10, _width) == ["——", "——"]
    assert _quad_x("——", 100, 10, 1, 2, _width) == 40


def test_rich_text_wrap_and_alignment_use_em_dash_widths():
    body, _fonts = build_rich_text_content(
        '<body><p style="text-align:center">—— ——</p></body>',
        34,
        100,
        default_style=RichStyle(size=10),
        document_font=DocumentFont("Custom", width_of=_width),
    )
    positions = [line.split() for line in body if line.endswith(" Tm")]
    assert len(positions) == 2
    assert [float(position[4]) for position in positions] == [7.0, 7.0]
    assert positions[0][5] != positions[1][5]


def test_unencodable_text_keeps_the_question_mark_fallback():
    assert _pdf_literal("a🙂b") == "(a?b)"


@pytest.mark.parametrize(("caption", "literal"), [("\xa0", rb"(\240)"), ("€", b"(?)")])
def test_checkbox_caption_keeps_its_zapf_glyph_code(caption, literal):
    appearance = build_button_appearance(20, 20, on=True, radio=False, caption=caption)
    assert literal + b" Tj" in appearance.content
    assert any(font["BaseFont"] == "ZapfDingbats" for font in appearance.fonts.values())
