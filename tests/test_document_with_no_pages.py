"""A document with no pages does not have one.

Six shapes of page-less input -- a bare header, a header and ``%%EOF``, a header
followed by garbage, a header with one object that is not a page, a catalog with
no ``/Pages``, and a well-formed but empty page tree -- were all answered with
``len(doc.pages) == 1``: a blank Letter sheet invented on load. A caller that
checked the page count was told the file had a page, rendered it, and got a
blank sheet the file never described.

The references agree on the principle and split on the remedy. qpdf and pdfium
refuse all six. MuPDF refuses the three with no PDF object in them and reports
0 pages for the three that have a structure to read. That split is the rule
adopted here, because this library also repairs documents: there is nothing to
work with in a file with no objects, while a file whose page tree is empty is a
document -- one that ``validate()`` rejects and ``repair()`` fixes, which is
where asking for a page to be invented belongs.

The file we now write for such a document is opened by both qpdf and MuPDF, and
both read 0 pages from it.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.exceptions import PdfParseException, PdfValidationException

# No PDF object anywhere in the input: qpdf, pdfium and MuPDF all refuse these.
NO_OBJECTS = {
    "bare header": b"%PDF-1.7\n",
    "header and EOF": b"%PDF-1.7\n%%EOF\n",
    "header and garbage": b"%PDF-1.7\nnot a pdf at all\n%%EOF\n",
}

# Parseable, with no page in it: MuPDF reads these and reports 0 pages.
NO_PAGES = {
    "one object, not a page": (
        b"%PDF-1.7\n1 0 obj\n<< /Type /Font /BaseFont /Helvetica >>\nendobj\n"
        b"trailer\n<< /Size 2 >>\n%%EOF\n"
    ),
    "catalog without /Pages": (
        b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"
        b"trailer\n<< /Size 2 /Root 1 0 R >>\n%%EOF\n"
    ),
    "empty page tree": (
        b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Count 0 /Kids [] >>\nendobj\n"
        b"trailer\n<< /Size 3 /Root 1 0 R >>\n%%EOF\n"
    ),
}

EMPTY_TREE = NO_PAGES["empty page tree"]


@pytest.mark.parametrize("shape", sorted(NO_OBJECTS))
def test_input_with_no_pdf_object_is_refused(shape):
    with pytest.raises(PdfParseException, match="No PDF objects"):
        Document(io.BytesIO(NO_OBJECTS[shape]))


@pytest.mark.parametrize("shape", sorted(NO_PAGES))
def test_a_page_less_document_reports_no_pages(shape):
    document = Document(io.BytesIO(NO_PAGES[shape]))
    assert len(document.pages) == 0
    assert list(document.pages) == []
    assert document.page_count == 0


@pytest.mark.parametrize("shape", sorted(NO_PAGES))
def test_no_blank_page_is_invented_to_extract_from(shape):
    # The invented sheet made this an empty string for the same reason a real
    # blank page would, so the count is what the caller has to go by.
    document = Document(io.BytesIO(NO_PAGES[shape]))
    assert document.extract_text() == ""
    with pytest.raises(IndexError):
        _ = document.pages[0]


def test_the_page_tree_is_still_the_one_the_file_had():
    # Nothing is grafted on: the page tree that is written back is the empty one
    # that was read, which qpdf and MuPDF both open and read as 0 pages.
    document = Document(io.BytesIO(EMPTY_TREE))
    buffer = io.BytesIO()
    document.save(buffer)
    data = buffer.getvalue()

    assert b"/Type /Pages" in data
    assert b"/Count 0" in data
    assert b"/MediaBox" not in data  # no Letter sheet of our making
    assert len(Document(io.BytesIO(data)).pages) == 0


def test_a_page_less_document_is_invalid_and_repair_is_what_adds_the_page():
    document = Document(io.BytesIO(EMPTY_TREE))
    assert document.validate() is False

    document._engine_pdf.repair()
    assert len(document.pages) == 1
    assert document.validate() is True


def test_a_page_less_document_can_be_authored_into():
    document = Document(io.BytesIO(EMPTY_TREE))
    document.pages.add()
    document.pages[0].add_text("Hello", 50, 700)

    buffer = io.BytesIO()
    document.save(buffer)
    reopened = Document(io.BytesIO(buffer.getvalue()))
    assert len(reopened.pages) == 1
    assert "Hello" in reopened.extract_text()


def test_a_page_less_document_survives_encryption():
    document = Document(io.BytesIO(EMPTY_TREE))
    document.encrypt("user", "owner")
    buffer = io.BytesIO()
    document.save(buffer)
    data = buffer.getvalue()

    assert b"/Encrypt" in data
    assert len(Document(io.BytesIO(data), password="user").pages) == 0


def test_the_operations_that_need_a_page_say_so():
    document = Document(io.BytesIO(EMPTY_TREE))
    with pytest.raises(PdfValidationException, match="at least one page"):
        document.save_as_svg(io.BytesIO())
    # Signing is guarded in the engine, where the field would be authored onto
    # a page that is not there.
    with pytest.raises(PdfValidationException, match="no pages"):
        document._engine_pdf._ensure_signature_field("Signature1")


def test_a_page_less_document_loaded_lazily_also_reports_none(tmp_path):
    path = tmp_path / "empty.pdf"
    path.write_bytes(EMPTY_TREE)
    with Document.open_streaming(path) as document:
        assert len(document.pages) == 0


def test_the_cos_loader_reports_no_pages_either():
    # load_cos keeps the object graph and nothing else; it used to add the blank
    # sheet too, so a caller preserving a file byte for byte still saw a page.
    pdf = SimplePdf.load_cos(EMPTY_TREE)
    assert len(pdf.pages) == 0
    assert pdf.page_contents == []


def test_from_bytes_safe_still_hands_back_a_usable_document():
    # The tolerant loader is the opt-in for "give me something to work with":
    # it repairs, and repairing a document with no pages adds one.
    pdf = SimplePdf.from_bytes_safe(b"%PDF-1.7\n%%EOF\n")
    assert len(pdf.pages) == 1
