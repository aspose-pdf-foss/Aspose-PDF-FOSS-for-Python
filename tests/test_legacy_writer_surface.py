"""What the engine no longer offers, and why.

``PdfWriterV0`` is written to only when a ``SimplePdf`` has no COS graph -- a
minimal parse of malformed input, or one built in memory and never given one.
Every path through ``Document`` has a COS graph, so a handful of ``SimplePdf``
methods whose effect existed only in that writer did nothing at all through the
public API: ``set_watermark`` set a field nobody read and saved a file with no
watermark in it, and ``sign``/``add_signature`` fabricated a signature -- an
orphan ``/Sig`` holding ``/Contents <0000>``, over no byte range, referenced by
no form field, in a document with no ``/AcroForm``.

They are gone rather than fixed. A watermark is what :mod:`aspose_pdf.stamps`
puts on any document, signing is what ``Document.sign`` does for real, and the
rest never had a caller at all. The tests below are here so that none of them
comes back: a method that quietly does nothing is worse than no method, because
the caller finds out from a blank page rather than from the call.
"""

from __future__ import annotations

import re

import pytest

from aspose_pdf import Document, TextStamp
from aspose_pdf.engine.simple_pdf import SimplePdf


def _legacy_document() -> SimplePdf:
    """A document the legacy writer will serialise: no COS graph."""
    pdf = SimplePdf()
    pdf.pages.append((0, 0, 200, 200))
    pdf.page_contents.append(b"BT /F1 12 Tf 10 100 Td (Body) Tj ET")
    return pdf


def _objects(data: bytes) -> dict[int, bytes]:
    return {
        int(match.group(1)): b" ".join(match.group(2).split())
        for match in re.finditer(rb"(\d+) 0 obj\s*(.*?)\s*endobj", data, re.S)
    }


def test_a_signature_read_from_a_file_is_not_written_back_as_one():
    # The details of a signature this document was loaded with are a read
    # model. They used to be turned back into a /Sig of their own, which said
    # the document was signed while covering nothing and being referenced by
    # nothing.
    pdf = _legacy_document()
    pdf.signature = {"Reason": "R", "ContactInfo": "C", "Location": "L"}
    data = pdf.to_bytes()

    assert b"/Type /Sig" not in data
    assert b"/Contents <0000>" not in data
    assert b"/AcroForm" not in data
    # The page itself is written exactly as it is without the signature: qpdf,
    # MuPDF and pdfium all read the two files as the same one page.
    assert _objects(data) == _objects(_legacy_document().to_bytes())


def test_the_legacy_writer_still_writes_what_it_is_given():
    data = _legacy_document().to_bytes()
    objects = _objects(data)
    assert len(objects) == 4  # catalog, page tree, page, contents
    assert b"/Type /Catalog" in objects[1]
    assert b"(Body) Tj" in objects[4]


@pytest.mark.parametrize(
    "name",
    ["set_watermark", "sign", "add_signature", "hide_image", "replace_image",
     "extract_attachment", "save_incremental", "supports_incremental_update"],
)
def test_the_methods_that_only_reached_the_legacy_writer_are_gone(name):
    assert not hasattr(SimplePdf, name), (
        f"SimplePdf.{name} is back; through Document it cannot take effect"
    )


def test_a_watermark_is_what_a_stamp_does():
    # What set_watermark was reached for, on a document that has a COS graph --
    # which is every document the public API makes.
    document = Document()
    document.pages.add()
    document.pages[0].add_stamp(TextStamp("CONFIDENTIAL", font_size=36))
    assert "CONFIDENTIAL" in document.extract_text()
