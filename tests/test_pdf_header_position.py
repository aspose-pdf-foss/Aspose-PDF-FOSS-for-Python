"""A PDF may have bytes in front of its header.

Acrobat accepts the header anywhere in the first 1024 bytes (PDF Reference 1.7,
implementation note 13), and pdfium stops looking there; MuPDF and qpdf look
further still. The reader refused anything that did not start with ``%PDF-``,
so a file with a mail or HTTP header left in front of it -- a saved attachment,
a captured download -- would not open at all.

Such a prefix usually arrives *after* the file was written, so every offset in
it is short by the prefix's length: ``startxref``, the table's entries,
``/Prev`` and ``/XRefStm``. When ``startxref`` does not land on a
cross-reference section but does once moved by the header's position, every
offset is moved by it. qpdf, MuPDF and pdfium read all of these files.
"""

from __future__ import annotations

import importlib.util
import io
import logging
from pathlib import Path

import pytest

from aspose_pdf import Document

_PREFIX = b"Content-Type: application/pdf\r\nContent-Disposition: attachment\r\n\r\n"


def _hybrid() -> bytes:
    """The hybrid-reference file its own tests build, with a /XRefStm entry."""
    spec = importlib.util.spec_from_file_location(
        "_hybrid_fixture", Path(__file__).with_name("test_hybrid_reference_files.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._hybrid()


def _written() -> bytes:
    document = Document()
    document.pages.add().add_text("Behind a prefix", 72, 700, font_size=18)
    document.info["Title"] = "First"
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _updated() -> bytes:
    """Two revisions: the second's trailer chains to the first with /Prev."""
    document = Document(io.BytesIO(_written()))
    document.info["Title"] = "Second"
    buffer = io.BytesIO()
    document.save(buffer, incremental=True)
    return buffer.getvalue()


def _open(data: bytes, caplog: pytest.LogCaptureFixture) -> Document:
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="aspose_pdf"):
        document = Document(io.BytesIO(data))
        document.pages[0].extract_text()
    assert "reconstruction" not in caplog.text, "offsets were not followed"
    return document


@pytest.mark.parametrize(
    ("make", "title", "text"),
    [
        (_written, "First", "Behind a prefix"),
        (_updated, "Second", "Behind a prefix"),  # startxref, the table and /Prev
        (_hybrid, "Hybrid Probe", "Hello hybrid"),  # /XRefStm too
    ],
    ids=["one-revision", "prev-chain", "hybrid"],
)
def test_offsets_short_by_a_prepended_prefix_are_moved(make, title, text, caplog):
    document = _open(_PREFIX + make(), caplog)
    assert document._engine_pdf._cos_doc.offset_shift == len(_PREFIX)
    assert document.info.get("Title") == title
    assert document.pages[0].extract_text().strip() == text
    assert document._engine_pdf.pdf_version in {"1.4", "1.5", "1.7"}


def test_a_file_whose_offsets_count_the_prefix_needs_no_shift(caplog):
    # Build the file with the prefix present from the start: offsets are absolute.
    body = _written()
    assert b"\nxref\n" in body, "the rewrite below needs a classic table"
    prefixed = bytearray(_PREFIX + body)
    start = bytes(prefixed).rindex(b"startxref") + len(b"startxref\n")
    end = bytes(prefixed).index(b"\n", start)
    old = int(prefixed[start:end])
    table = old + len(_PREFIX)
    prefixed[start:end] = str(table).encode()
    # Move the table's in-use entries as well.
    head, _, tail = bytes(prefixed).partition(b"xref\n")
    rows = []
    lines = tail.split(b"\n")
    for line in lines:
        parts = line.split()
        if len(parts) == 3 and parts[2] == b"n":
            line = b"%010d %s n " % (int(parts[0]) + len(_PREFIX), parts[1])
        rows.append(line)
    data = head + b"xref\n" + b"\n".join(rows)

    document = _open(data, caplog)
    assert document._engine_pdf._cos_doc.offset_shift == 0
    assert document.info.get("Title") == "First"


def test_a_header_further_than_1024_bytes_in_is_not_a_pdf():
    with pytest.raises(Exception, match="PDF header"):
        Document(io.BytesIO(b"x" * 1100 + b"\n" + _written()))


def test_saving_a_shifted_file_incrementally_writes_it_whole(caplog):
    document = Document(io.BytesIO(_PREFIX + _written()))
    document.info["Title"] = "Saved"
    buffer = io.BytesIO()
    document.save(buffer, incremental=True)
    saved = buffer.getvalue()

    assert saved.startswith(b"%PDF-"), "offsets are made right again, prefix gone"
    reopened = _open(saved, caplog)
    assert reopened._engine_pdf._cos_doc.offset_shift == 0
    assert reopened.info.get("Title") == "Saved"


def test_a_shifted_file_opens_in_streaming_mode(tmp_path):
    path = tmp_path / "prefixed.pdf"
    path.write_bytes(_PREFIX + _written())
    with Document.open_streaming(path) as document:
        assert document.info.get("Title") == "First"
        assert document.pages[0].extract_text().strip() == "Behind a prefix"
