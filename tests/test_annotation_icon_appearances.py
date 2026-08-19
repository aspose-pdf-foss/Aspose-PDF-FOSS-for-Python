"""Standard icons for `Text` (sticky note) and `FileAttachment` annotations.

Both are among the most common annotations in a reviewed document, and neither
could synthesise an appearance before: `generate_appearances()` simply declined,
leaving the annotation with no `/AP` at all.
"""

from __future__ import annotations

from io import BytesIO

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.appearance import build_appearance
from aspose_pdf.engine.cos import PdfDictionary, PdfName, PdfStream

TEXT_ICONS = ("Comment", "Key", "Note", "Help", "NewParagraph", "Paragraph", "Insert")
ATTACHMENT_ICONS = ("PushPin", "Graph", "Paperclip", "Tag")


def _content(subtype: str, name: str | None = None, **props) -> bytes:
    if name is not None:
        props["Name"] = name
    generated = build_appearance(subtype, (0, 0, 20, 20), props)
    assert generated is not None, f"{subtype}/{name} produced no appearance"
    return generated.content


# --- every standard name draws something distinct --------------------------
@pytest.mark.parametrize("name", TEXT_ICONS)
def test_text_icon_is_drawn(name):
    content = _content("Text", name)
    assert content.startswith(b"q") and content.rstrip().endswith(b"Q")


@pytest.mark.parametrize("name", ATTACHMENT_ICONS)
def test_file_attachment_icon_is_drawn(name):
    content = _content("FileAttachment", name)
    assert content.startswith(b"q") and content.rstrip().endswith(b"Q")


def test_each_text_icon_differs():
    """A viewer must be able to tell a Key from a Help from a Note."""
    drawn = {name: _content("Text", name) for name in TEXT_ICONS}
    # Paragraph and NewParagraph share a shape but not their glyph.
    assert len(set(drawn.values())) == len(TEXT_ICONS)


def test_each_attachment_icon_differs():
    drawn = {name: _content("FileAttachment", name) for name in ATTACHMENT_ICONS}
    assert len(set(drawn.values())) == len(ATTACHMENT_ICONS)


# --- defaults and fallbacks ------------------------------------------------
def test_missing_name_uses_the_subtype_default():
    assert _content("Text") == _content("Text", "Comment")
    assert _content("FileAttachment") == _content("FileAttachment", "PushPin")


def test_unknown_name_falls_back_to_the_default():
    """An unknown icon name is what a viewer replaces with its default."""
    assert _content("Text", "NoSuchIcon") == _content("Text", "Comment")
    assert _content("FileAttachment", "NoSuchIcon") == _content(
        "FileAttachment", "PushPin"
    )


def test_leading_slash_on_the_name_is_accepted():
    assert _content("Text", "/Key") == _content("Text", "Key")


# --- colour and geometry ---------------------------------------------------
def test_colour_is_honoured():
    default = _content("Text", "Comment")
    red = _content("Text", "Comment", C=[1, 0, 0])
    assert red != default
    assert b"1 0 0 rg" in red


def test_icon_is_square_and_centred_in_a_wide_box():
    """A wide rect must not stretch the icon; it is centred instead."""
    generated = build_appearance("Text", (0, 0, 100, 20), {"Name": "Insert"})
    assert generated is not None
    xs = []
    for line in generated.content.decode("latin-1").splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[2] in ("m", "l"):
            xs.append(float(parts[0]))
    assert xs, "no path points found"
    # The 20pt-tall icon sits in the middle of the 100pt-wide box.
    assert min(xs) >= 38.0 and max(xs) <= 62.0


def test_degenerate_rect_yields_no_appearance():
    assert build_appearance("Text", (10, 10, 10, 10), {"Name": "Note"}) is None


def test_only_glyph_icons_request_a_font():
    for name in ("Help", "Paragraph", "NewParagraph"):
        generated = build_appearance("Text", (0, 0, 20, 20), {"Name": name})
        assert generated.fonts, name
    for name in ("Comment", "Key", "Note", "Insert"):
        generated = build_appearance("Text", (0, 0, 20, 20), {"Name": name})
        assert not generated.fonts, name


# --- through the document API ----------------------------------------------
def _document(subtype: str, name: str) -> Document:
    document = Document()
    page = document.pages.add()
    page.annotations.add(subtype, (50, 700, 90, 740), "note", properties={"Name": name})
    return document


def _annotation_ap(document: Document) -> PdfStream:
    engine = document._engine_pdf
    page = engine._get_page_dict(0)
    annots = engine._resolve(page.mapping[PdfName("Annots")])
    annot = engine._resolve(annots.items[0])
    ap = engine._resolve(annot.mapping[PdfName("AP")])
    assert isinstance(ap, PdfDictionary)
    return engine._resolve(ap.mapping[PdfName("N")])


@pytest.mark.parametrize(
    "subtype,name", [("Text", "Note"), ("FileAttachment", "Paperclip")]
)
def test_generate_appearances_fills_the_annotation(subtype, name):
    document = _document(subtype, name)
    assert document._engine_pdf.generate_appearances() == 1
    stream = _annotation_ap(document)
    assert isinstance(stream, PdfStream)
    assert stream.content.startswith(b"q")


def test_appearance_survives_save_and_reload():
    document = _document("Text", "Key")
    document._engine_pdf.generate_appearances()
    before = _annotation_ap(document).content

    output = BytesIO()
    document.save(output)
    reloaded = Document().load_from(output.getvalue())
    assert _annotation_ap(reloaded).content == before


def test_an_existing_appearance_is_not_replaced():
    document = Document()
    page = document.pages.add()
    page.annotations.add(
        "Text", (50, 700, 90, 740), "note",
        appearance_normal=b"q 1 0 0 rg 0 0 5 5 re f Q\n",
        properties={"Name": "Note"},
    )
    document._engine_pdf.generate_appearances()
    assert b"0 0 5 5 re" in _annotation_ap(document).content
