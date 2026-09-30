"""A file cut off inside a stream opens, with what survived the cut.

Truncation is the commonest damage a transfer does to a PDF, and the parser
already treats a ``/Length`` that no longer reaches ``endstream`` as damage to
recover from rather than a reason to fail. The case where the keyword is not
there *at all* -- the input ends inside the stream -- went the other way and
raised ``endstream not found for stream``, failing the whole document, so a file
cut in its first content stream gave nothing back although every page
dictionary before the cut was intact and readable.

qpdf and MuPDF both read such a file. The stream is now the bytes that are
there, which is the same remedy the wrong-``/Length`` case already gets. Where
the cut lands inside an *object* rather than a stream the document is still
refused -- qpdf and MuPDF refuse that too.

The recovered results here were checked against both: our page counts are
qpdf's, and our per-page text is MuPDF's, character for character, where MuPDF
recovers anything. For a cut inside the first stream MuPDF reports three pages
with no text on any of them, while qpdf and this library report the one page
whose dictionary arrived.
"""

from __future__ import annotations

import io
import logging

import pytest

from aspose_pdf import Document, PdfLoadLimits
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.exceptions import PdfParseException, PdfResourceLimitException

_OPENER = b"\nstream\n"


@pytest.fixture(scope="module")
def three_pages() -> bytes:
    """A three-page document, one line of text per page."""
    document = Document()
    for index in range(3):
        document.pages.add()
        document.pages[index].add_text(f"Page {index}", 50, 700)
    buffer = io.BytesIO()
    document.save(buffer)
    data = buffer.getvalue()
    assert data.count(_OPENER) == 3  # one content stream per page
    return data


def _cut_inside_first_stream(data: bytes) -> bytes:
    return data[: data.index(_OPENER) + len(_OPENER) + 20]


def _cut_inside_last_stream(data: bytes) -> bytes:
    return data[: data.rindex(_OPENER) + len(_OPENER) + 20]


def _texts(document: Document) -> list[str]:
    return [
        document.pages[index].extract_text().strip().replace("\n", " ")
        for index in range(len(document.pages))
    ]


def test_a_cut_inside_the_last_stream_keeps_the_pages_before_it(three_pages):
    document = Document(io.BytesIO(_cut_inside_last_stream(three_pages)))
    # qpdf and MuPDF both read three pages here, and this is MuPDF's text.
    assert len(document.pages) == 3
    assert _texts(document) == ["Page 0", "Page 1", ""]


def test_a_cut_inside_the_first_stream_reports_the_page_that_is_there(three_pages):
    document = Document(io.BytesIO(_cut_inside_first_stream(three_pages)))
    # Two of the three /Kids never arrived. qpdf answers one page; MuPDF keeps
    # the tree's count of three and has no text on any of them.
    assert len(document.pages) == 1
    assert _texts(document) == [""]


def test_the_recovery_says_so_in_the_log(three_pages, caplog):
    with caplog.at_level(logging.WARNING, logger="aspose_pdf"):
        Document(io.BytesIO(_cut_inside_last_stream(three_pages)))
    assert any(
        "not closed by endstream" in record.message for record in caplog.records
    ), [record.message for record in caplog.records]


def test_a_cut_inside_an_object_is_still_refused(three_pages):
    # Not a stream: the page dictionary itself stops mid-key. qpdf and MuPDF
    # both refuse this file, and widening the stream recovery must not reach it.
    cut = three_pages[: three_pages.index(b"/MediaBox") + 5]
    with pytest.raises(PdfParseException):
        Document(io.BytesIO(cut))


def test_an_intact_file_is_untouched_and_says_nothing(three_pages, caplog):
    with caplog.at_level(logging.WARNING, logger="aspose_pdf"):
        document = Document(io.BytesIO(three_pages))
        assert _texts(document) == ["Page 0", "Page 1", "Page 2"]
    assert not [
        record.message
        for record in caplog.records
        if "endstream" in record.message
    ]


def test_the_recovered_stream_is_still_bounded_by_the_load_limits(three_pages):
    # Reading to the end of the input is still an object read, and the budget
    # that caps one applies before the bytes are kept.
    with pytest.raises(PdfResourceLimitException):
        SimplePdf.from_bytes(
            _cut_inside_last_stream(three_pages),
            limits=PdfLoadLimits(max_object_bytes=64),
        )


def test_the_recovered_document_saves_and_reopens(three_pages, tmp_path):
    document = Document(io.BytesIO(_cut_inside_last_stream(three_pages)))
    out = tmp_path / "recovered.pdf"
    document.save(out)
    reopened = Document(out)
    assert len(reopened.pages) == 3
    assert _texts(reopened)[:2] == ["Page 0", "Page 1"]
