"""Text drawn through a form XObject is the page's text.

A form XObject is a piece of content drawn on a page by ``Do`` (ISO 32000-1
8.10). Headers, footers, stamps, and anything a layout tool reuses live in one.
The renderer walks into a form -- it has to, or the page draws blank -- and so
does the graphics absorber, but the *text* reader did not: it consumed the
``Do`` operand and moved on, so a page whose words were all inside forms
extracted as empty, and `replace_text` found nothing to replace.

The form's own ``/Resources`` govern inside it (8.10.1), falling back to the
page's when it declares none.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.content_stream_parser import (
    _MAX_FORM_DEPTH,
    ContentStreamParser,
)

_FONT = (
    b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
)


def _document(page_content: bytes, forms: dict[int, bytes]) -> Document:
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 200] /Resources << "
            b"/Font << /F1 5 0 R >> /XObject << /Fm1 6 0 R >> >> /Contents 4 0 R >>"
        ),
        4: b"<< /Length %d >>\nstream\n" % len(page_content)
        + page_content
        + b"\nendstream",
        5: _FONT,
        **forms,
    }
    raw = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for number in sorted(objects):
        offsets[number] = len(raw)
        raw += b"%d 0 obj\n" % number + objects[number] + b"\nendobj\n"
    start = len(raw)
    raw += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for number in sorted(objects):
        raw += b"%010d 00000 n \n" % offsets[number]
    raw += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        start,
    )
    return Document(io.BytesIO(bytes(raw)))


def _form(body: bytes, resources: bytes | None = b"") -> bytes:
    """A form XObject. *resources* of ``None`` omits ``/Resources`` entirely."""
    declared = b"" if resources is None else b"/Resources << " + resources + b">> "
    return (
        b"<< /Type /XObject /Subtype /Form /BBox [0 0 400 200] "
        + declared
        + b"/Length %d >>\nstream\n" % len(body)
        + body
        + b"\nendstream"
    )


def _shows(text: bytes) -> bytes:
    return b"BT /F1 14 Tf 40 150 Td (" + text + b") Tj ET"


_OWN_FONT = b"/Font << /F1 5 0 R >> "


def _text(document: Document) -> str:
    return " ".join(document.pages[0].extract_text().split())


# --- the text inside a form is read -----------------------------------------


def test_a_form_s_text_is_the_page_s_text():
    document = _document(b"/Fm1 Do\n", {6: _form(_shows(b"inner text"), _OWN_FONT)})
    assert _text(document) == "inner text"


@pytest.mark.parametrize("resources", [None, b""])
def test_a_form_with_no_resources_of_its_own_uses_the_page_s(resources):
    # No /Resources at all, and an empty one -- older writers use both to mean
    # "the page's will do".
    document = _document(
        b"/Fm1 Do\n",
        {6: _form(b"BT /F1 14 Tf 40 150 Td (caf\xe9) Tj ET", resources)},
    )
    # Read through the page's WinAnsi /F1, so the accented byte survives.
    assert _text(document) == "café"


def test_the_page_s_own_text_comes_first():
    document = _document(
        b"BT /F1 14 Tf 40 180 Td (page text) Tj ET\n/Fm1 Do\n",
        {6: _form(_shows(b"inner text"), _OWN_FONT)},
    )
    assert _text(document) == "page text inner text"


def test_a_form_inside_a_form_is_read_too():
    document = _document(
        b"/Fm1 Do\n",
        {
            6: _form(b"q 1 0 0 1 0 0 cm /Fm2 Do Q", b"/XObject << /Fm2 7 0 R >> "),
            7: _form(_shows(b"inner text"), _OWN_FONT),
        },
    )
    assert _text(document) == "inner text"


def test_the_same_form_drawn_twice_is_read_twice():
    document = _document(b"/Fm1 Do\n/Fm1 Do\n", {6: _form(_shows(b"stamp"), _OWN_FONT)})
    assert _text(document) == "stamp stamp"


def test_a_form_that_shows_nothing_adds_nothing():
    document = _document(
        b"BT /F1 14 Tf 40 180 Td (page text) Tj ET\n/Fm1 Do\n",
        {6: _form(b"0 0 1 rg 10 10 50 50 re f")},
    )
    assert _text(document) == "page text"


def test_an_image_xobject_is_not_read_as_text():
    # `Do` on an image must not try to read its samples as a content stream --
    # and its bytes must not be inlined into the page's resources to find out.
    image = (
        b"<< /Type /XObject /Subtype /Image /Width 2 /Height 2 "
        b"/ColorSpace /DeviceGray /BitsPerComponent 8 /Length 4 >>\nstream\n"
        b"(Tj)\x00\nendstream"
    )
    document = _document(
        b"BT /F1 14 Tf 40 180 Td (page text) Tj ET\nq 10 0 0 10 0 0 cm /Fm1 Do Q\n",
        {6: image},
    )
    assert _text(document) == "page text"
    resources = document._engine_pdf._get_page_resources(0)
    assert "content" not in resources["XObject"]["Fm1"]


def test_a_form_drawing_itself_stops():
    # The form declares no resources, so it inherits the page's -- which name
    # the form itself. Nothing in the object graph is circular; only the
    # drawing is, and the depth bound is the only thing that ends it.
    document = _document(b"/Fm1 Do\n", {6: _form(_shows(b"loop") + b" /Fm1 Do", None)})
    read = _text(document)
    assert read.startswith("loop")
    assert read.count("loop") <= _MAX_FORM_DEPTH + 1


# --- and the readers built on it --------------------------------------------


def test_the_absorber_finds_a_form_s_text():
    from aspose_pdf.text import TextAbsorber

    document = _document(b"/Fm1 Do\n", {6: _form(_shows(b"inner text"), _OWN_FONT)})
    absorber = TextAbsorber()
    document.pages[0].accept(absorber)
    assert "inner text" in absorber.text


def test_the_whole_document_s_text_carries_it_too():
    document = _document(b"/Fm1 Do\n", {6: _form(_shows(b"inner text"), _OWN_FONT)})
    assert "inner text" in document.extract_text()


def test_the_layout_exports_read_inside_a_form():
    # The export works from byte ranges in the page's stream, where a form is
    # one `Do`; it used to drop every word inside one while `extract_text`
    # kept them. A form's text objects now join the page's own.
    document = _document(b"/Fm1 Do\n", {6: _form(_shows(b"inner text"), _OWN_FONT)})
    page = document.pages[0]
    assert "inner text" in page.to_html(embed_images=False)
    assert "inner text" in page.to_markdown(embed_images=False)


def test_replace_text_does_not_reach_inside_a_form_yet():
    # `replace_text` splices a replacement into the stream at a byte offset,
    # and a form is a different stream -- often one shared by every page that
    # shows the same header, so editing it "on this page" would edit all of
    # them. That is a decision to make, not plumbing to add. Stated rather
    # than silent: see supported-features.md.
    document = _document(b"/Fm1 Do\n", {6: _form(_shows(b"inner text"), _OWN_FONT)})
    assert document.pages[0].replace_text("inner", "outer") == 0


def test_a_form_s_font_governs_inside_it():
    # The form names /F1 as a *different* font from the page's. Reading its
    # text through the page's font would decode the bytes with the wrong
    # encoding; here that shows as a lost character.
    document = _document(
        b"/Fm1 Do\n",
        {
            6: _form(
                b"BT /F1 14 Tf 40 150 Td (caf\xe9) Tj ET",
                b"/Font << /F1 7 0 R >> ",
            ),
            7: _FONT,
        },
    )
    assert _text(document) == "café"


def test_only_a_form_is_followed_even_when_a_caller_hands_over_content():
    # The parser takes a plain resources dictionary, so a caller may hand it
    # anything. An XObject that is not a form is not a content stream, whatever
    # bytes are attached to it.
    resources = {
        "Font": {"F1": {"Subtype": "Type1", "BaseFont": "/Helvetica"}},
        "XObject": {
            "Im1": {
                "Subtype": "Image",
                "content": b"BT /F1 12 Tf (should not be read) Tj ET",
            }
        },
    }
    parser = ContentStreamParser(b"BT /F1 12 Tf (page) Tj ET /Im1 Do", resources)
    assert parser.extract_text() == "page"


# --- the recursion is bounded -----------------------------------------------


def test_a_chain_of_forms_is_followed_to_a_bound():
    assert _MAX_FORM_DEPTH >= 4  # deep enough for real documents
    forms = {}
    depth = _MAX_FORM_DEPTH + 4
    for level in range(depth):
        number = 6 + level
        if level == depth - 1:
            forms[number] = _form(_shows(b"bottom"), _OWN_FONT)
        else:
            forms[number] = _form(
                b"/Fm%d Do" % (level + 2),
                b"/XObject << /Fm%d %d 0 R >> " % (level + 2, number + 1),
            )
    document = _document(b"/Fm1 Do\n", forms)
    # It stops rather than running away; whether it reaches the bottom of a
    # chain deeper than the bound is not promised.
    assert isinstance(_text(document), str)


# --- the layout exports (to_html / to_markdown) -----------------------------
#
# The export infers structure from positions, so a form's text has to land
# where the form puts it on the page: the form's /Matrix, then the CTM its
# `Do` ran under. Reading order is the check -- MuPDF's, on every case here.


def _lines(document: Document) -> list[str]:
    markdown = document.pages[0].to_markdown(embed_images=False)
    return [line for line in markdown.splitlines() if line.strip()]


_BODY = b"BT /F1 12 Tf 40 100 Td (body text) Tj ET\n"


def test_a_header_form_placed_by_cm_sorts_above_the_body():
    document = _document(
        _BODY + b"q 1 0 0 1 0 180 cm /Fm1 Do Q\n",
        {6: _form(b"BT /F1 12 Tf 40 0 Td (header) Tj ET", _OWN_FONT)},
    )
    assert _lines(document) == ["header", "body text"]


def test_a_form_placed_by_its_own_matrix_sorts_where_it_lands():
    placed = (
        b"<< /Type /XObject /Subtype /Form /BBox [0 0 400 200] "
        b"/Matrix [1 0 0 1 0 180] /Resources << /Font << /F1 5 0 R >> >> "
    )
    body = b"BT /F1 12 Tf 40 0 Td (header) Tj ET"
    form = placed + b"/Length %d >>\nstream\n" % len(body) + body + b"\nendstream"
    document = _document(_BODY + b"/Fm1 Do\n", {6: form})
    assert _lines(document) == ["header", "body text"]


def test_a_footer_form_sorts_below_the_body_whatever_the_stream_order():
    document = _document(
        b"/Fm1 Do\n" + _BODY,
        {6: _form(b"BT /F1 12 Tf 40 20 Td (footer) Tj ET", _OWN_FONT)},
    )
    assert _lines(document) == ["body text", "footer"]


def test_the_export_reads_a_form_inside_a_form():
    document = _document(
        _BODY + b"/Fm1 Do\n",
        {
            6: _form(b"/Fm2 Do", b"/XObject << /Fm2 7 0 R >> "),
            7: _form(b"BT /F1 12 Tf 40 180 Td (nested) Tj ET", _OWN_FONT),
        },
    )
    assert _lines(document) == ["nested", "body text"]


@pytest.mark.parametrize("resources", [None, b""])
def test_the_export_lets_the_page_s_resources_stand_in(resources):
    document = _document(
        b"/Fm1 Do\n",
        {6: _form(b"BT /F1 14 Tf 40 150 Td (caf\xe9) Tj ET", resources)},
    )
    assert _lines(document) == ["café"]


def test_the_export_decodes_a_form_s_text_with_the_form_s_font():
    document = _document(
        b"/Fm1 Do\n",
        {
            6: _form(
                b"BT /F1 14 Tf 40 150 Td (caf\xe9) Tj ET",
                b"/Font << /F1 7 0 R >> ",
            ),
            7: _FONT,
        },
    )
    assert _lines(document) == ["café"]


def test_the_export_keeps_a_form_s_text_apart_from_the_page_s():
    # Both text objects start at the same byte offset in their own streams --
    # the page's and the form's. Keyed by offset, one overwrote the other.
    same_offset = b"BT /F1 12 Tf 40 %d Td (%s) Tj ET"
    document = _document(
        same_offset % (100, b"page words") + b"\n/Fm1 Do\n",
        {6: _form(same_offset % (180, b"form words"), _OWN_FONT)},
    )
    assert _lines(document) == ["form words", "page words"]


def test_a_form_drawing_itself_stops_in_the_export_too():
    document = _document(b"/Fm1 Do\n", {6: _form(_shows(b"loop") + b" /Fm1 Do", None)})
    text = " ".join(_lines(document))
    assert "loop" in text
    assert text.count("loop") <= _MAX_FORM_DEPTH + 1


def test_a_form_s_matrix_applies_before_the_ctm_it_is_drawn_under():
    # 8.10.1: form space -> /Matrix -> the CTM at `Do`. Translations alone
    # commute and cannot tell the order apart; a scale can. Under a CTM that
    # doubles, a /Matrix lift of 80 lands the text at y 160, above the body at
    # 100 -- applied the other way round it would land at 80, below it.
    placed = (
        b"<< /Type /XObject /Subtype /Form /BBox [0 0 400 200] "
        b"/Matrix [1 0 0 1 0 80] /Resources << /Font << /F1 5 0 R >> >> "
    )
    body = b"BT /F1 12 Tf 20 0 Td (lifted) Tj ET"
    form = placed + b"/Length %d >>\nstream\n" % len(body) + body + b"\nendstream"
    document = _document(_BODY + b"q 2 0 0 2 0 0 cm /Fm1 Do Q\n", {6: form})
    assert _lines(document) == ["lifted", "body text"]


def test_a_malformed_form_matrix_is_read_as_identity():
    placed = (
        b"<< /Type /XObject /Subtype /Form /BBox [0 0 400 200] "
        b"/Matrix [1 0 0 1] /Resources << /Font << /F1 5 0 R >> >> "
    )
    body = b"BT /F1 12 Tf 40 180 Td (still read) Tj ET"
    form = placed + b"/Length %d >>\nstream\n" % len(body) + body + b"\nendstream"
    document = _document(_BODY + b"/Fm1 Do\n", {6: form})
    assert _lines(document) == ["still read", "body text"]


def test_the_export_follows_only_a_form_even_when_handed_content():
    # The same rule the parser keeps: an XObject that is not a form is not a
    # content stream, whatever bytes a caller attaches to it.
    from aspose_pdf.engine.auto_tag import find_layout_elements
    from aspose_pdf.engine.text_export import _expand_forms

    resources = {
        "XObject": {
            "Im1": {
                "Subtype": "Image",
                "content": b"BT /F1 12 Tf (should not be read) Tj ET",
            }
        }
    }
    elements = find_layout_elements(b"q 10 0 0 10 0 0 cm /Im1 Do Q")
    expanded, sources = _expand_forms(elements, resources, limits=None, budget=None)
    assert [e.kind for e in expanded] == ["xobject"]
    assert sources == {}
