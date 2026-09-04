"""A name escapes what it holds, or the file will not parse.

ISO 32000-1 7.3.5: a name is a sequence of bytes, and any byte that is not a
regular character is written ``#`` followed by two hex digits. This library
wrote the bytes as they were. A space, a bracket or a parenthesis does not merely
misrepresent the name -- it *ends* it, so a document with such a name anywhere
came out unparseable, by this library and by everything else. Reading was the
mirror of it: ``#20`` was taken for three characters, so a name another producer
escaped came back wrong.

Reachable from the public surface wherever a caller names something: an
annotation property's key, or a value marked as a name with ``annotations.Name``.

One place had noticed, and only for itself -- an attachment's media type was
escaped by a private pair of functions written for that one field. Those are
gone: a name escapes whatever it holds, so the caller does not have to.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document
from aspose_pdf.annotations import Name
from aspose_pdf.engine.cos import PdfName, decode_pdf_name, encode_pdf_name
from aspose_pdf.engine.pdf_parser_cos import _Tokenizer

AWKWARD = [
    "with space",
    "with#hash",
    "a(b)",
    "a/b",
    "a[b]",
    "a<b>",
    "a{b}",
    "a%b",
    "a\tb",
    "café",
    "日本",
]


def _saved(document: Document) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _reloaded(document: Document) -> Document:
    return Document(io.BytesIO(_saved(document)))


# ---------------------------------------------------------------------------
# The escaping itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["Simple", "F1", "a+b.c", "a-b_c", "", "1"])
def test_a_name_of_regular_characters_is_written_as_it_stands(name):
    assert encode_pdf_name(name) == name


@pytest.mark.parametrize(
    ("name", "token"),
    [
        ("with space", "with#20space"),
        ("with#hash", "with#23hash"),
        ("a(b)", "a#28b#29"),
        ("a/b", "a#2Fb"),
        ("a%b", "a#25b"),
        ("café", "caf#C3#A9"),  # a name's bytes above ASCII are UTF-8
    ],
)
def test_everything_else_becomes_a_hex_escape(name, token):
    assert encode_pdf_name(name) == token


@pytest.mark.parametrize("name", [*AWKWARD, "Simple", ""])
def test_the_decoder_is_the_encoder_s_inverse(name):
    assert decode_pdf_name(encode_pdf_name(name)) == name


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("a#20b", "a b"),
        ("a#2fb", "a/b"),  # lower case hex is a hex digit too
        ("caf#c3#a9", "café"),
        ("a#zz", "a#zz"),  # malformed: taken for itself rather than refused
        ("a#", "a#"),
        ("a#2", "a#2"),
        ("plain", "plain"),
    ],
)
def test_the_decoder_reads_what_files_actually_hold(token, expected):
    assert decode_pdf_name(token) == expected


@pytest.mark.parametrize("name", AWKWARD)
def test_a_name_survives_the_tokenizer(name):
    read = _Tokenizer("/" + encode_pdf_name(name)).read()

    assert isinstance(read, PdfName)
    assert read.name == "/" + name


def test_a_delimiter_still_ends_an_unescaped_name():
    """The escape is what lets a name hold one; a bare delimiter still stops."""
    read = _Tokenizer("[/Alpha/Beta]").read()

    assert [item.name for item in read.items] == ["/Alpha", "/Beta"]


# ---------------------------------------------------------------------------
# Through the document
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", AWKWARD)
def test_an_annotation_property_named_this_way_survives_a_save(name):
    document = Document()
    document.pages.add()
    document.pages[0].annotations.add(
        "Text", (10, 10, 40, 40), "x", properties={name: 1}
    )

    assert _reloaded(document).pages[0].annotations[0].properties[name] == 1


@pytest.mark.parametrize("name", AWKWARD)
def test_a_property_whose_value_is_a_name_survives_a_save(name):
    document = Document()
    document.pages.add()
    document.pages[0].annotations.add(
        "Text", (10, 10, 40, 40), "x", properties={"Zz": Name(name)}
    )

    assert _reloaded(document).pages[0].annotations[0].properties["Zz"] == name


def test_a_media_type_is_escaped_by_the_writer_not_by_its_caller():
    """`/Subtype` on an embedded file stream is a name holding `text/plain`."""
    document = Document()
    document.pages.add()
    document.add_attachment("note.txt", b"x", mime="text/plain")

    data = _saved(document)

    assert b"/Subtype /text#2Fplain" in data
    assert _reloaded(document).get_embedded_file("note.txt").mime_type == "text/plain"


def test_a_name_a_foreign_producer_escaped_is_read_back():
    raw = (
        b"%PDF-1.7\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R "
        b"/Probe << /a#20b /v#28a#29l /caf#c3#a9 /x >> >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >> endobj\n"
        b"trailer << /Root 1 0 R /Size 4 >>\n%%EOF\n"
    )
    engine = Document(io.BytesIO(raw))._engine_pdf
    root = engine._resolve(engine._cos_doc.trailer.mapping.get(PdfName("Root")))
    probe = engine._resolve(root.mapping.get(PdfName("Probe")))

    assert {
        key.name: engine._resolve(value).name for key, value in probe.mapping.items()
    } == {
        "/a b": "/v(a)l",
        "/café": "/x",
    }


def test_an_ordinary_document_is_unchanged_by_the_escaping():
    """Every name this library writes for itself is already regular."""
    document = Document()
    document.pages.add().add_text("hello", 72, 700)
    base = _saved(document)

    assert b"#" not in base.split(b"stream")[0]
    assert _saved(Document(io.BytesIO(base))) == base
