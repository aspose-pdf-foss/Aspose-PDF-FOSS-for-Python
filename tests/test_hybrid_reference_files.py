"""A hybrid-reference file is one revision indexed in two places.

ISO 32000-1 7.5.8.4 lets a file stay readable by PDF 1.4 software while keeping
objects in object streams: the classic table lists what that software can reach,
and a cross-reference stream named by the trailer's ``/XRefStm`` indexes the
objects in object streams. The ``/XRefStm`` entry was never read, so every
object in an object stream was missing -- here the page tree, the font and the
document information. The document opened with no text and no title, and saving
it wrote a file whose page tree was gone: qpdf and MuPDF cannot open it.

With the objects in object streams left out of the classic table, qpdf, MuPDF
and pdfium all read the file. Listed there as free, only pdfium reads it; that
is also how it reads here, the table's in-use entries answering first.
"""

from __future__ import annotations

import io
import logging
import re
import zlib

import pytest

from aspose_pdf import Document

_CONTENT = b"BT /F1 24 Tf 72 700 Td (Hello hybrid) Tj ET"


def _hybrid(layout: str = "omit") -> bytes:
    members = {
        2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        4: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        6: b"<< /Title (Hybrid Probe) >>",
    }
    header = body = b""
    for number, value in members.items():
        header += b"%d %d " % (number, len(body))
        body += value + b" "
    packed = header + body
    loose = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> "
        b"/Contents 5 0 R >>",
        5: b"<< /Length %d >>\nstream\n" % len(_CONTENT) + _CONTENT + b"\nendstream",
        7: b"<< /Type /ObjStm /N 3 /First %d /Length %d >>\nstream\n" % (len(header), len(packed)) + packed + b"\nendstream",
    }
    raw = bytearray(b"%PDF-1.5\n")
    offsets = {}
    for number in sorted(loose):
        offsets[number] = len(raw)
        raw += b"%d 0 obj\n" % number + loose[number] + b"\nendobj\n"
    offsets[8] = len(raw)
    order = sorted(members)
    rows = b""
    for number in range(9):
        if number in members:
            rows += bytes([2]) + (7).to_bytes(4, "big") + order.index(number).to_bytes(2, "big")
        elif number in offsets:
            rows += bytes([1]) + offsets[number].to_bytes(4, "big") + (0).to_bytes(2, "big")
        else:
            rows += bytes([0]) + (0).to_bytes(4, "big") + (65535).to_bytes(2, "big")
    data = zlib.compress(rows)
    raw += (
        b"8 0 obj\n<< /Type /XRef /Size 9 /W [1 4 2] /Filter /FlateDecode /Length %d >>\nstream\n" % len(data)
        + data
        + b"\nendstream\nendobj\n"
    )
    table_at = len(raw)
    if layout == "omit":
        raw += b"xref\n0 2\n0000000000 65535 f \n%010d 00000 n \n" % offsets[1]
        raw += b"3 1\n%010d 00000 n \n5 1\n%010d 00000 n \n" % (offsets[3], offsets[5])
        raw += b"7 2\n%010d 00000 n \n%010d 00000 n \n" % (offsets[7], offsets[8])
    else:
        raw += b"xref\n0 9\n"
        for number in range(9):
            if number in offsets:
                raw += b"%010d 00000 n \n" % offsets[number]
            else:
                raw += b"0000000000 65535 f \n" if number == 0 else b"0000000000 00000 f \n"
    raw += b"trailer\n<< /Size 9 /Root 1 0 R /Info 6 0 R /XRefStm %d >>\nstartxref\n%d\n%%%%EOF\n" % (
        offsets[8],
        table_at,
    )
    return bytes(raw)


def _read(data: bytes, caplog: pytest.LogCaptureFixture) -> tuple:
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="aspose_pdf"):
        document = Document(io.BytesIO(data))
        seen = (len(document.pages), document.info.get("Title"), document.pages[0].extract_text().strip())
    assert "reconstruction" not in caplog.text
    return seen


@pytest.mark.parametrize("layout", ["omit", "free"])
def test_objects_in_object_streams_are_found_through_xrefstm(layout, caplog):
    assert _read(_hybrid(layout), caplog) == (1, "Hybrid Probe", "Hello hybrid")


def test_an_object_only_the_stream_lists_is_found(caplog):
    # The content stream, left out of the table: qpdf, MuPDF and pdfium find it.
    data = re.sub(rb"5 1\n\d{10} 00000 n \n", b"", _hybrid())
    assert _read(data, caplog) == (1, "Hybrid Probe", "Hello hybrid")


def test_the_table_answers_first_for_an_object_both_list(caplog):
    # A loose object 6 listed in use in the table, and a packed one in the
    # stream: qpdf, MuPDF and pdfium all read the table's.
    base = _hybrid()
    table_at = base.rindex(b"xref\n0 2")
    loose = b"6 0 obj\n<< /Title (Classic Title) >>\nendobj\n"
    table = base[table_at:].replace(b"7 2\n", b"6 1\n%010d 00000 n \n7 2\n" % table_at, 1)
    data = base[:table_at] + loose + table
    data = re.sub(rb"startxref\n\d+\n", b"startxref\n%d\n" % (table_at + len(loose)), data)
    assert _read(data, caplog) == (1, "Classic Title", "Hello hybrid")


def test_a_saved_hybrid_file_keeps_what_its_object_streams_held(caplog):
    buffer = io.BytesIO()
    Document(io.BytesIO(_hybrid())).save(buffer)
    assert _read(buffer.getvalue(), caplog) == (1, "Hybrid Probe", "Hello hybrid")


def test_an_incremental_update_of_a_hybrid_file_reads_back(caplog):
    original = _hybrid()
    document = Document(io.BytesIO(original))
    document.info["Title"] = "Updated"
    buffer = io.BytesIO()
    document.save(buffer, incremental=True)
    updated = buffer.getvalue()

    assert updated.startswith(original)
    assert _read(updated, caplog) == (1, "Updated", "Hello hybrid")


def test_an_xrefstm_that_is_not_a_cross_reference_stream_is_ignored(caplog):
    # A PDF 1.4 reader never looks at it, so the table alone has to stand.
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> "
        b"/Contents 5 0 R >>",
        4: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        5: b"<< /Length %d >>\nstream\n" % len(_CONTENT) + _CONTENT + b"\nendstream",
        6: b"<< /Title (Hybrid Probe) >>",
    }
    raw = bytearray(b"%PDF-1.5\n")
    offsets = {}
    for number in sorted(objects):
        offsets[number] = len(raw)
        raw += b"%d 0 obj\n" % number + objects[number] + b"\nendobj\n"
    table_at = len(raw)
    raw += b"xref\n0 7\n0000000000 65535 f \n" + b"".join(b"%010d 00000 n \n" % offsets[n] for n in range(1, 7))
    for target in (offsets[1], len(raw) + 10_000_000):  # a catalog, and past the end of the file
        data = bytes(raw) + b"trailer\n<< /Size 7 /Root 1 0 R /Info 6 0 R /XRefStm %d >>\nstartxref\n%d\n%%%%EOF\n" % (
            target,
            table_at,
        )
        assert _read(data, caplog) == (1, "Hybrid Probe", "Hello hybrid")
