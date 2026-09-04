"""A text string is written in an encoding other readers understand.

ISO 32000-1 7.9.2.2 gives a text string two encodings: PDFDocEncoding, or
UTF-16BE behind a ``FEFF`` byte order mark. This library wrote raw UTF-8, which
is neither -- a conforming reader takes those bytes for PDFDocEncoding, so every
non-Latin title, bookmark, annotation and attachment name it produced came out
as mojibake in Acrobat and in every other tool. Reading its own files back hid
it completely, because the reader made the same wrong assumption.

Not everywhere, though, and that is what made it visible from inside. Field
names went through the one encoder that was right, while the value setter
matched them with a hand-rolled UTF-8 decode -- so ``form["Ф"].value = "V"``
found no field, wrote nothing, and reported success. The value was simply gone
after a save.

One encoder and one decoder now, and both are checked against pikepdf reading
the same bytes.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import (
    PdfString,
    decode_pdf_text_string,
    encode_pdf_text_string,
)
from aspose_pdf.outlines import OutlineItem

NON_ASCII = ["Привет", "café", "日本語", "مرحبا", "Ω", "x\U0001f600"]


def _reloaded(document: Document) -> Document:
    buffer = io.BytesIO()
    document.save(buffer)
    return Document(io.BytesIO(buffer.getvalue()))


def _saved(document: Document) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# The encoding itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["plain", "a(b)c", "a\\b", "", "0123"])
def test_an_ascii_string_is_written_as_it_stands(text):
    """PDFDocEncoding's first 128 characters are ASCII's, so nothing to do."""
    assert encode_pdf_text_string(text) == text.encode("ascii")


@pytest.mark.parametrize("text", NON_ASCII)
def test_anything_else_goes_to_utf_16be_behind_the_mark(text):
    encoded = encode_pdf_text_string(text)

    assert encoded[:2] == b"\xfe\xff"
    assert encoded[2:].decode("utf-16-be") == text


@pytest.mark.parametrize("text", [*NON_ASCII, "plain", "a(b)c", ""])
def test_the_decoder_is_the_encoder_s_inverse(text):
    assert decode_pdf_text_string(PdfString(text)) == text


@pytest.mark.parametrize(
    ("octets", "expected"),
    [
        (b"\xfe\xff\x04\x1f", "П"),
        (b"\xff\xfe\x1f\x04", "П"),  # some producers write the other order
        (b"\xef\xbb\xbf\xd0\x9f", "П"),  # ISO 32000-2 admits marked UTF-8
        (b"\xd0\x9f", "П"),  # and bare UTF-8 is written in the wild
        (b"caf\xe9", "café"),  # PDFDocEncoding's upper half, as Latin-1
        (b"", ""),
    ],
    ids=["utf16be", "utf16le", "utf8-bom", "utf8-bare", "pdfdoc", "empty"],
)
def test_the_decoder_reads_what_files_actually_hold(octets, expected):
    assert decode_pdf_text_string(PdfString(octets)) == expected


def test_a_byte_string_is_not_re_encoded():
    """`/ID`, `/O`, `/U` and a signature's `/Contents` are bytes, not text."""
    raw = bytes(range(256))

    assert PdfString(raw).value == raw


# ---------------------------------------------------------------------------
# Through the document
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", NON_ASCII)
def test_every_place_text_lives_keeps_it(text):
    document = Document()
    document.pages.add()
    document.info["Title"] = text
    document.outlines.add(OutlineItem(text, 0))
    document.pages[0].annotations.add("Text", (10, 10, 40, 40), text, title=text)
    document.add_attachment(f"{text}.txt", b"x", description=text)

    again = _reloaded(document)

    assert again.info.get("Title") == text
    assert again.outlines[0].title == text
    assert again.pages[0].annotations[0].contents == text
    assert again.pages[0].annotations[0].title == text
    assert sorted(again.attachments) == [f"{text}.txt"]
    assert again.get_embedded_file(f"{text}.txt").description == text


@pytest.mark.parametrize("name", ["Ф", "café", "名前", "plain", "a b", "a.b"])
def test_a_field_keeps_the_value_set_on_it_whatever_it_is_called(name):
    """The name is written UTF-16BE and was matched with a UTF-8 decode, so
    setting the value found no field, wrote nothing, and said nothing."""
    document = Document()
    document.pages.add()
    document.form.add_text_field(name, 0, (10, 10, 200, 30))
    document.form[name].value = "V"

    assert [(f.name, f.value) for f in _reloaded(document).form] == [(name, "V")]


def test_nothing_is_written_as_bare_utf_8():
    """The bytes themselves, because "it reads back" proves only that the
    reader shares the writer's mistake. One assertion over the whole file, so
    a text string written anywhere else is caught by the same rule."""
    text = "Привет"
    document = Document()
    document.pages.add()
    document.info["Title"] = text
    document.outlines.add(OutlineItem(text, 0))
    document.pages[0].annotations.add("Text", (10, 10, 40, 40), text, title=text)
    document.form.add_text_field(text, 0, (10, 10, 200, 30))
    document.add_attachment(f"{text}.txt", b"x", description=text)

    data = _saved(document)

    # In either notation: non-printable bytes go out as a hex string, so the
    # UTF-8 would be there as its own hex rather than as itself.
    assert text.encode("utf-8") not in data
    assert text.encode("utf-8").hex().encode("ascii") not in data.lower()
    assert b"<feff041f04400438043204350442" in data.lower()


def test_a_generic_annotation_property_keeps_its_text():
    """`/Contents` and `/T` are handled by name; every other entry travels
    through the generic property channel, which decodes for itself."""
    document = Document()
    document.pages.add()
    document.pages[0].annotations.add(
        "Text", (10, 10, 40, 40), "x", properties={"Subj": "Тема"}
    )

    assert _reloaded(document).pages[0].annotations[0].properties["Subj"] == "Тема"


def test_a_conformance_message_names_the_attachment_it_is_about():
    """The label is a file specification's `/F`, a text string like any other.

    Only a file this library did not write can be missing `/AFRelationship`,
    so the specification is stripped of it the way a foreign producer would
    leave it.
    """
    from aspose_pdf.engine import conformance
    from aspose_pdf.engine.cos import PdfName

    document = Document()
    document.pages.add()
    document.add_attachment("вложение.txt", b"x")
    reloaded = _reloaded(document)
    engine = reloaded._engine_pdf
    for obj in engine._cos_doc.objects.values():
        mapping = getattr(obj, "mapping", None)
        if mapping and PdfName("AFRelationship") in mapping:
            del mapping[PdfName("AFRelationship")]

    errors = conformance.pdfa_extended(engine, "3b")
    errors = errors[0] if isinstance(errors, tuple) else errors

    assert any("вложение.txt" in problem for problem in errors)


def test_a_string_a_foreign_producer_wrote_in_utf_16_is_read():
    raw = (
        b"%PDF-1.7\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >> endobj\n"
        b"4 0 obj << /Title <FEFF041F04400438043204350442> >> endobj\n"
        b"trailer << /Root 1 0 R /Info 4 0 R /Size 5 >>\n%%EOF\n"
    )

    assert Document(io.BytesIO(raw)).info.get("Title") == "Привет"


def test_an_unchanged_document_with_non_ascii_text_still_saves_byte_for_byte():
    document = Document()
    document.pages.add()
    document.info["Title"] = "Привет"
    document.outlines.add(OutlineItem("日本語", 0))
    base = _saved(document)

    assert _saved(Document(io.BytesIO(base))) == base
