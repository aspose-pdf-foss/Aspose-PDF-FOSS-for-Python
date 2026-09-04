"""The header, and the limits a conforming reader is held to.

A PDF that holds binary data -- almost every one -- puts a comment of at least
four bytes above 127 immediately after ``%PDF-x.y`` (ISO 32000-1 7.5.2). It is
what tells anything reading the first two lines that the file must be copied
byte for byte: a transfer in text mode, an editor deciding whether to translate
line endings. ISO 19005-1 6.1.2 makes it a requirement, so every file this
library wrote failed PDF/A-1 on its second line -- and its own conformance check
never looked, so a document converted to PDF/A-1b came back with no issues at
all.

Nor did the check look at the implementation limits ISO 32000-1 annex C.1 sets
and 19005-1 6.1.13 adopts: a name of more than 127 bytes, a string of more than
65535, an integer past +/-2,147,483,647. A reader is not obliged to handle any
of them, so a file that needs it to is not archivable.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.simple_pdf import SimplePdf


def _saved(document: Document, **kwargs) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer, **kwargs)
    return buffer.getvalue()


def _second_line(data: bytes) -> bytes:
    return data.split(b"\n")[1]


def _binary(line: bytes) -> bool:
    return line.startswith(b"%") and sum(1 for byte in line if byte > 127) >= 4


def _checked(document: Document, level: str = "1b") -> tuple[list[str], list[str]]:
    return document._engine_pdf.check_pdfa_compliance_detailed(level)


def _pdfa(text: str = "hello") -> Document:
    document = Document()
    document.pages.add().add_text(text, 72, 700)
    document.info["Title"] = "T"
    document.convert_to_pdfa("1b")
    return document


# ---------------------------------------------------------------------------
# Writing it
# ---------------------------------------------------------------------------


def test_a_saved_document_marks_itself_binary_on_its_second_line():
    document = Document()
    document.pages.add().add_text("hello", 72, 700)

    assert _binary(_second_line(_saved(document)))


def test_the_legacy_writer_marks_it_too():
    """It builds the file as lines of text and has its own header."""
    engine = SimplePdf()
    engine.pages = [(0, 0, 612, 792)]
    engine.page_contents = [b""]

    assert _binary(_second_line(engine.to_bytes()))


def test_the_comment_does_not_disturb_the_file():
    document = Document()
    document.pages.add().add_text("hello", 72, 700)
    data = _saved(document)

    reloaded = Document(io.BytesIO(data))

    assert len(reloaded.pages) == 1
    assert reloaded.pages[0].to_markdown().strip() == "hello"
    assert _saved(reloaded) == data


def test_an_incremental_save_keeps_the_header_it_was_given():
    """It preserves the original bytes as its prefix, comment included."""
    document = Document()
    document.pages.add().add_text("hello", 72, 700)
    base = _saved(document)
    again = Document(io.BytesIO(base))
    again.add_attachment("a.txt", b"x")

    appended = _saved(again, incremental=True)

    assert appended.startswith(base)
    assert _binary(_second_line(appended))


# ---------------------------------------------------------------------------
# Checking it
# ---------------------------------------------------------------------------


def test_a_file_without_the_comment_is_reported():
    raw = (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >> endobj\n"
        b"trailer << /Root 1 0 R /Size 4 >>\n%%EOF\n"
    )

    _errors, warnings = _checked(Document(io.BytesIO(raw)))

    assert any("four bytes above 127" in warning for warning in warnings)


def test_a_file_with_the_comment_is_not_reported():
    document = Document()
    document.pages.add()
    reloaded = Document(io.BytesIO(_saved(document)))

    _errors, warnings = _checked(reloaded)

    assert not any("four bytes above 127" in warning for warning in warnings)


def test_it_is_a_warning_because_saving_decides_whether_it_persists():
    """A full save writes a header that has one; only an incremental save,
    which keeps the original bytes, carries the absence forward."""
    raw = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << >>\n%%EOF\n"

    errors, warnings = _checked(Document(io.BytesIO(raw)))

    assert any("four bytes above 127" in warning for warning in warnings)
    assert not any("four bytes above 127" in error for error in errors)


def test_a_comment_of_ordinary_characters_does_not_count():
    """It is the bytes above 127 that make it a binary marker, not the `%`."""
    raw = (
        b"%PDF-1.4\n%abcd\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >> endobj\n"
        b"trailer << /Root 1 0 R /Size 4 >>\n%%EOF\n"
    )

    _errors, warnings = _checked(Document(io.BytesIO(raw)))

    assert any("four bytes above 127" in warning for warning in warnings)


def test_a_document_written_with_object_streams_marks_itself_too():
    """The PDF 1.5+ layout builds its own file from the header up."""
    from aspose_pdf.optimization import OptimizationOptions

    document = Document()
    for _ in range(30):
        page = document.pages.add()
        page.annotations.add("Text", (10, 10, 40, 40), "a body long enough to pack")
    document._engine_pdf.optimize(OptimizationOptions())
    data = document._engine_pdf.to_bytes()

    assert b"/XRef" in data  # the compressed layout really was taken
    assert _binary(_second_line(data))


def test_a_document_built_in_memory_is_not_asked_about_bytes_it_has_none_of():
    document = Document()
    document.pages.add()

    _errors, warnings = _checked(document)

    assert not any("four bytes above 127" in warning for warning in warnings)


# ---------------------------------------------------------------------------
# The implementation limits
# ---------------------------------------------------------------------------


def test_a_name_longer_than_the_limit_is_reported():
    document = _pdfa()
    document.pages[0].annotations.add(
        "Text", (10, 10, 40, 40), "x", properties={"n" * 200: 1}
    )

    errors, _warnings = _checked(Document(io.BytesIO(_saved(document))))

    assert any("limits a name to 127 bytes" in error for error in errors)


def test_a_string_longer_than_the_limit_is_reported():
    document = _pdfa()
    document.pages[0].annotations.add("Text", (10, 10, 40, 40), "y" * 70000)

    errors, _warnings = _checked(Document(io.BytesIO(_saved(document))))

    assert any("limits a string to 65535 bytes" in error for error in errors)


def test_an_integer_past_the_limit_is_reported():
    """Only a file this library did not write can hold one: its own writer
    keeps a whole number that large a *real*, so it stays in range. Inside an
    array, because that is where a coordinate lives."""
    raw = (
        b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R /Zz [ 1 3000000000 ] >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >> endobj\n"
        b"trailer << /Root 1 0 R /Size 4 >>\n%%EOF\n"
    )

    errors, _warnings = _checked(Document(io.BytesIO(raw)))

    assert any("limits an integer" in error for error in errors)


@pytest.mark.parametrize(
    "properties",
    [{"n" * 127: 1}, {"Zz": 2147483647}, {"Zz": -2147483647}],
    ids=["name-at-the-limit", "integer-at-the-limit", "negative-at-the-limit"],
)
def test_a_value_exactly_at_the_limit_is_allowed(properties):
    document = _pdfa()
    document.pages[0].annotations.add(
        "Text", (10, 10, 40, 40), "x", properties=properties
    )

    errors, _warnings = _checked(Document(io.BytesIO(_saved(document))))

    assert not any("limits a" in error or "limits an" in error for error in errors)


def test_a_clean_document_reports_nothing():
    errors, warnings = _checked(Document(io.BytesIO(_saved(_pdfa()))))

    assert errors == []
    assert warnings == []
