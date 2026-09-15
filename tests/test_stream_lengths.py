"""A stream ends where its ``/Length`` says -- or, when that is wrong, at ``endstream``.

Two ways a readable file failed to open at all:

* The object was taken to end at the first ``endobj`` after its header, so a
  content stream that merely *says* ``endobj`` -- in a string, in a comment --
  made a correct ``/Length`` "extend past the object".
* A wrong ``/Length`` -- too long, too short, negative, the usual damage from a
  transfer that rewrites line endings -- raised, and one bad stream failed the
  whole document.

pdfium, MuPDF and qpdf read every file here. An indirect ``/Length`` is now
followed too, so a stream measured by one is exact -- the bytes qpdf reads --
rather than carrying the end-of-line before ``endstream``.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.pdf_parser_cos import PdfCosParser

_TEXT = b"BT /F1 24 Tf 72 700 Td (Hello streams) Tj ET"


def _file(content_object: bytes, extra: dict[int, bytes] | None = None) -> bytes:
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> "
        b"/Contents 5 0 R >>",
        4: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        5: content_object,
        **(extra or {}),
    }
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
    raw += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, start)
    return bytes(raw)


def _stream(data: bytes, length: bytes) -> bytes:
    return b"<< /Length " + length + b" >>\nstream\n" + data + b"\nendstream"


def _text(data: bytes) -> str:
    return Document(io.BytesIO(data)).pages[0].extract_text().strip()


def test_a_content_stream_that_says_endobj_opens():
    data = _TEXT + b"\n% endstream endobj mentioned in passing\n/Span << /ActualText (endobj) >> BDC EMC"
    assert _text(_file(_stream(data, b"%d" % len(data)))) == "Hello streams"


@pytest.mark.parametrize("length", [len(_TEXT) + 30, len(_TEXT) - 10, -5, 10**9], ids=["long", "short", "negative", "huge"])
def test_a_wrong_length_does_not_fail_the_document(length):
    assert _text(_file(_stream(_TEXT, b"%d" % length))) == "Hello streams"


def test_no_length_and_endstream_in_the_data_still_finds_the_end():
    data = _TEXT + b"\n% the word endstream, then more data\n"
    assert _text(_file(b"<< >>\nstream\n" + data + b"\nendstream")) == "Hello streams"


def test_an_indirect_length_is_followed_exactly():
    # The data ends in a line feed of its own, with none added before endstream:
    # only the number can tell them apart.
    payload = b"AB\n"
    data = _file(b"<< /Length 6 0 R >>\nstream\n" + payload + b"endstream", {6: b"3"})
    assert PdfCosParser(data).parse().objects[5].content == payload


def test_a_length_referring_to_its_own_stream_reads_to_the_keyword():
    data = _file(b"<< /Length 5 0 R >>\nstream\n" + _TEXT + b"\nendstream")
    assert _text(data) == "Hello streams"


def _content(data: bytes) -> bytes:
    return PdfCosParser(data).parse().objects[5].content


@pytest.mark.parametrize(("eol", "name"), [(b"\n", "lf"), (b"\r\n", "crlf"), (b"\r", "cr")])
def test_the_end_of_line_before_endstream_is_not_data(eol, name):
    # ISO 32000-1 7.3.8.1; only when the end is found by the keyword.
    data = _file(b"<< >>\nstream" + eol + _TEXT + eol + b"endstream")
    assert _content(data) == _TEXT, name


def test_endstream_glued_to_its_neighbours_in_the_data_is_not_the_end():
    # Each would close the stream but for the character glued to the keyword.
    data = _TEXT + b"\n% fooendstream endobj\n% endstreamendobj\n"
    assert _content(_file(b"<< >>\nstream\n" + data + b"\nendstream")) == data


@pytest.mark.parametrize("stream_last", [False, True], ids=["then-an-object", "then-xref"])
def test_a_stream_object_without_endobj_ends_at_what_follows(stream_last):
    rest = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> "
        b"/Contents 5 0 R >>",
        4: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    raw = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for number in [1, 2, 3, 4, 5] if stream_last else [1, 2, 3, 5, 4]:
        offsets[number] = len(raw)
        if number == 5:
            raw += b"5 0 obj\n<< >>\nstream\n" + _TEXT + b"\nendstream\n"
        else:
            raw += b"%d 0 obj\n" % number + rest[number] + b"\nendobj\n"
    start = len(raw)
    raw += b"xref\n0 6\n0000000000 65535 f \n" + b"".join(b"%010d 00000 n \n" % offsets[n] for n in range(1, 6))
    raw += b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % start
    assert _content(bytes(raw)) == _TEXT
    assert _text(bytes(raw)) == "Hello streams"
