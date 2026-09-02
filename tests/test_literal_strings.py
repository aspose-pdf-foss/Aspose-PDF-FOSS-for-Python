"""A literal string is worth the bytes it stands for.

``( ... )`` carries most of the text in a PDF -- titles, bookmark labels,
annotation contents, field values, JavaScript -- and inside it a backslash
introduces an escape (ISO 32000-1 7.3.4.2). The reader consumed the backslash
*and* the character after it and kept neither, so every ``\\(``, ``\\)`` and
``\\\\`` was silently deleted from the value. Since those are exactly the three
characters the writer escapes, any text containing a parenthesis or a backslash
came back short of them: a document could be opened and saved with its own
titles quietly edited.

The other escape forms had never been read at all. ``\\101`` is an octal byte,
not the letter n's worth of nothing it decoded to; a backslash before an
end-of-line joins the lines; and an end-of-line *without* one is a single line
feed however the file writes it.

Every expectation below was checked against pikepdf reading the same bytes.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import PdfName, PdfString
from aspose_pdf.engine.pdf_parser_cos import _Tokenizer
from aspose_pdf.exceptions import PdfParseException
from aspose_pdf.load_limits import PdfLoadLimits
from aspose_pdf.outlines import OutlineItem


def _value(source: str) -> bytes:
    read = _Tokenizer(source).read()
    assert isinstance(read, PdfString)
    return read.value


def _reloaded(document: Document) -> Document:
    buffer = io.BytesIO()
    document.save(buffer)
    return Document(io.BytesIO(buffer.getvalue()))


# ---------------------------------------------------------------------------
# What the escapes stand for
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (r"(a\(b\)c)", b"a(b)c"),
        (r"(a(b)c)", b"a(b)c"),
        (r"(a\\b)", b"a\\b"),
        (r"(\101\102\103)", b"ABC"),
        (r"(\376\377)", b"\xfe\xff"),
        (r"(\7a)", b"\x07a"),
        (r"(\400)", b"\x00"),
        (r"(a\nb\tc\rd\be\ff)", b"a\nb\tc\rd\x08e\x0cf"),
        ("(one\\\ntwo)", b"onetwo"),
        ("(one\ntwo)", b"one\ntwo"),
        ("(one\r\ntwo)", b"one\ntwo"),
        ("(one\rtwo)", b"one\ntwo"),
        (r"(a\qb)", b"aqb"),
        ("()", b""),
    ],
)
def test_an_escape_stands_for_what_the_specification_says(source, expected):
    assert _value(source) == expected


def test_a_high_byte_is_the_file_s_byte():
    """Not the UTF-8 of the character it happens to look like in latin-1.

    A UTF-16BE value written as a literal string starts ``\\376\\377``, and
    re-encoding those would double them and destroy the text.
    """
    assert _value("(\xfe\xffH\x00i)") == b"\xfe\xffH\x00i"


def test_an_escaped_parenthesis_does_not_open_a_nesting_level():
    assert _value(r"(a\(b)") == b"a(b"


def test_an_unterminated_string_still_raises():
    with pytest.raises(PdfParseException, match="Unterminated"):
        _Tokenizer("(no end").read()


def test_a_trailing_backslash_does_not_run_past_the_end():
    with pytest.raises(PdfParseException, match="Unterminated"):
        _Tokenizer("(oops\\").read()


def test_real_nesting_is_still_bounded():
    limits = PdfLoadLimits(max_nesting_depth=3)

    with pytest.raises(Exception, match="nesting"):
        _Tokenizer("((((deep))))", limits).read()


# ---------------------------------------------------------------------------
# Through the document
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["a(b)c", "back\\slash", "(unbalanced", "trailing)", "all three ( ) \\"],
    ids=["parens", "backslash", "open", "close", "mixed"],
)
def test_text_with_a_parenthesis_or_backslash_survives_a_save(text):
    document = Document()
    document.pages.add()
    document.info["Title"] = text
    document.outlines.add(OutlineItem(text, 0))
    document.pages[0].annotations.add("Text", (10, 10, 40, 40), text)

    again = _reloaded(document)

    assert again.info.get("Title") == text
    assert again.outlines[0].title == text
    assert again.pages[0].annotations[0].contents == text


def test_a_javascript_action_keeps_its_call_parentheses():
    from aspose_pdf.interactive import JavaScriptAction

    document = Document()
    document.pages.add()
    document.outlines.add(
        OutlineItem("run", destination=JavaScriptAction("app.alert(1)"))
    )

    assert _reloaded(document).outlines[0].destination == JavaScriptAction(
        "app.alert(1)"
    )


def test_a_document_that_only_gets_opened_and_saved_keeps_its_text():
    document = Document()
    document.pages.add()
    document.info["Title"] = "Notes (draft) \\ v2"
    buffer = io.BytesIO()
    document.save(buffer)
    base = buffer.getvalue()

    for _ in range(3):
        again = Document(io.BytesIO(base))
        buffer = io.BytesIO()
        again.save(buffer)
        assert buffer.getvalue() == base
        assert again.info.get("Title") == "Notes (draft) \\ v2"


def test_the_parser_agrees_with_the_bytes_a_foreign_file_holds():
    """A file no writer of ours produced, read for its escaped strings."""
    raw = (
        b"%PDF-1.7\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R "
        b"/Probe << /A (a\\(b\\)c) /B (\\101\\102) /C (one\\\ntwo) >> >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >> endobj\n"
        b"trailer << /Root 1 0 R /Size 4 >>\n%%EOF\n"
    )
    engine = Document(io.BytesIO(raw))._engine_pdf
    root = engine._resolve(engine._cos_doc.trailer.mapping.get(PdfName("Root")))
    probe = engine._resolve(root.mapping.get(PdfName("Probe")))

    values = {
        key.name.lstrip("/"): engine._resolve(value).value
        for key, value in probe.mapping.items()
    }

    assert values == {"A": b"a(b)c", "B": b"AB", "C": b"onetwo"}
