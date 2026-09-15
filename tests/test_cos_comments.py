"""A comment may stand anywhere white-space may (ISO 32000-1 7.2.3).

The object tokenizer treated ``%`` as a token it did not know. ReportLab writes
``% ReportLab generated PDF document -- digest (opensource)`` into the trailer
dictionary of every file it produces, so the trailer failed to parse, the
reader fell back to scanning the whole file -- and the scan parsed the trailer
with the same tokenizer and lost it again. What goes with the trailer went too:
a ReportLab document opened without its title or author, and an encrypted one,
whose ``/Encrypt`` lives there, could not be opened with either password.
pdfium, MuPDF and qpdf read all of these. Form feed, white-space by 7.2.2, was
not skipped either.
"""

from __future__ import annotations

import io
import logging

import pytest

from aspose_pdf import Document

_RECONSTRUCTING = "reconstruction scan"


def _file(objects: dict[int, bytes], trailer: bytes) -> bytes:
    raw = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for number in sorted(objects):
        offsets[number] = len(raw)
        raw += b"%d 0 obj\n" % number + objects[number] + b"\nendobj\n"
    start = len(raw)
    size = max(objects) + 1
    raw += b"xref\n0 %d\n" % size
    for number in range(size):
        raw += (b"%010d 00000 n \n" % offsets[number]) if number in offsets else b"0000000000 65535 f \n"
    raw += b"trailer\n" + trailer + b"\nstartxref\n%d\n%%%%EOF\n" % start
    return bytes(raw)


def _objects(extra_catalog: bytes = b"") -> dict[int, bytes]:
    content = b"BT /F1 24 Tf 72 700 Td (Body text) Tj ET"
    return {
        1: b"<< /Type /Catalog /Pages 2 0 R" + extra_catalog + b" >>",
        2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> "
        b"/Contents 5 0 R >>",
        4: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        5: b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        6: b"<< /Title (Commented) /Author (Tester) >>",
    }


def _opened(data: bytes, caplog: pytest.LogCaptureFixture, **kwargs) -> Document:
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="aspose_pdf"):
        document = Document(io.BytesIO(data), **kwargs)
    assert _RECONSTRUCTING not in caplog.text, "a well-formed file was rebuilt by scanning"
    return document


@pytest.mark.parametrize(
    "trailer",
    [
        # ReportLab's own, verbatim in layout.
        b"<<\n/ID \n[<8c1d577c590c00164850603622791467><8c1d577c590c00164850603622791467>]\n"
        b"% ReportLab generated PDF document -- digest (opensource)\n\n/Info 6 0 R\n/Root 1 0 R\n/Size 7\n>>",
        # A comment holding what would otherwise end the dictionary or open a string.
        b"<< /Root 1 0 R % not the end >> nor a string (\n/Info 6 0 R /Size 7 >>",
        # A string holding a delimiter.
        b"<< /Root 1 0 R /Note (a \\) >> b) /Info 6 0 R /Size 7 >>",
        b"<<\f/Root 1 0 R\f/Info 6 0 R /Size 7\f>>",
    ],
    ids=["reportlab", "delimiters-in-comment", "delimiters-in-string", "form-feed"],
)
def test_the_trailer_reads_whole(trailer, caplog):
    document = _opened(_file(_objects(), trailer), caplog)
    assert (document.info.get("Title"), document.info.get("Author")) == ("Commented", "Tester")
    assert document.pages[0].extract_text().strip() == "Body text"


def test_comments_inside_objects_are_white_space(caplog):
    objects = _objects()
    objects[3] = (
        b"<< /Type /Page % a page\n/Parent 2 % gen follows\n0 R /MediaBox [0 0 % in an array\n612 792]\n"
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
    )
    content = b"BT /F1 24 Tf 72 700 Td (Body text) Tj ET"
    objects[5] = b"<< /Length %d >> %% before the keyword\nstream\n" % len(content) + content + b"\nendstream"
    document = _opened(_file(objects, b"<< /Root 1 0 R /Info 6 0 R /Size 7 >>"), caplog)
    assert document.pages[0].media_box is not None
    assert document.pages[0].extract_text().strip() == "Body text"


def test_an_encrypted_file_with_a_commented_trailer_opens(caplog):
    source = Document()
    source.pages.add().add_text("Secret body", 72, 700, font_size=14)
    source.info["Title"] = "Encrypted title"
    source.encrypt("user", "owner", algorithm="RC4-128")
    buffer = io.BytesIO()
    source.save(buffer)
    data = buffer.getvalue()
    # The trailer follows the xref table, so no offset moves.
    commented = data.replace(b"trailer\n<<", b"trailer\n<<\n% ReportLab generated PDF document -- digest (opensource)\n")
    assert commented != data

    for password in ("user", "owner"):
        document = _opened(commented, caplog, password=password)
        assert document.info.get("Title") == "Encrypted title"
        assert document.pages[0].extract_text().strip() == "Secret body"
