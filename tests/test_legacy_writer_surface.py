"""One writer, and the engine methods that only ever reached the other one.

``SimplePdf`` had two writers. The COS writer serialised a document that had an
object graph; a second, older one served a document assembled in memory, which
is what a bare ``SimplePdf()`` is. Nothing the public API does produces such a
document -- every load builds an object graph, malformed input included -- so a
handful of methods whose effect existed only in that writer did nothing at all
through ``Document``: ``set_watermark`` set a field nobody read and saved a file
with no watermark in it, and ``sign``/``add_signature`` fabricated a signature,
an orphan ``/Sig`` holding ``/Contents <0000>``, over no byte range, referenced
by no form field, in a document with no ``/AcroForm``.

Those methods are gone, and so is the writer: a document with no object graph is
given one and serialised like any other. The tests below are here so that none
of it comes back -- a method that quietly does nothing is worse than no method,
because the caller finds out from a blank page rather than from the call.
"""

from __future__ import annotations

import io
import re

import pytest

from aspose_pdf import Document, TextStamp
from aspose_pdf.engine.simple_pdf import SimplePdf


def _in_memory() -> SimplePdf:
    """A document assembled in memory: no object graph until it is saved."""
    pdf = SimplePdf()
    pdf.pages.append((0, 0, 200, 200))
    pdf.page_contents.append(b"BT /F1 12 Tf 10 100 Td (Body) Tj ET")
    assert pdf._cos_doc is None
    return pdf


def _objects(data: bytes) -> dict[int, bytes]:
    return {
        int(match.group(1)): b" ".join(match.group(2).split())
        for match in re.finditer(rb"(\d+) 0 obj\s*(.*?)\s*endobj", data, re.S)
    }


def test_a_document_with_no_object_graph_is_given_one_and_written():
    pdf = _in_memory()
    data = pdf.to_bytes()
    assert pdf._cos_doc is not None  # built on the way out

    objects = _objects(data)
    catalog = next(body for body in objects.values() if b"/Type /Catalog" in body)
    assert b"/Pages" in catalog
    assert any(b"(Body) Tj" in body for body in objects.values())
    # And it reads back as the page it was.
    assert Document(io.BytesIO(data)).extract_text().strip() == "Body"


def test_a_signature_read_from_a_file_is_not_written_back_as_one():
    # The details of a signature this document was loaded with are a read model.
    # They used to be turned back into a /Sig of their own, which said the
    # document was signed while covering nothing and being referenced by nothing.
    pdf = _in_memory()
    pdf.signature = {"Reason": "R", "ContactInfo": "C", "Location": "L"}
    data = pdf.to_bytes()

    assert b"/Type /Sig" not in data
    assert b"/Contents <0000>" not in data
    assert b"/AcroForm" not in data
    assert _objects(data) == _objects(_in_memory().to_bytes())


@pytest.mark.parametrize(
    "name",
    ["set_watermark", "sign", "add_signature", "hide_image", "replace_image",
     "extract_attachment", "save_incremental", "supports_incremental_update"],
)
def test_the_methods_that_only_reached_the_other_writer_are_gone(name):
    assert not hasattr(SimplePdf, name), (
        f"SimplePdf.{name} is back; through Document it cannot take effect"
    )


def test_there_is_one_writer():
    import aspose_pdf.engine.simple_pdf as module

    assert not hasattr(module, "PdfWriterV0")
    assert not hasattr(SimplePdf(), "watermark_text")


def test_a_watermark_is_what_a_stamp_does():
    # What set_watermark was reached for, on a document that has an object graph
    # -- which is every document the public API makes.
    document = Document()
    document.pages.add()
    document.pages[0].add_stamp(TextStamp("CONFIDENTIAL", font_size=36))
    assert "CONFIDENTIAL" in document.extract_text()


def test_a_document_with_no_object_graph_can_still_be_encrypted():
    # The retired writer derived a file key from a bare password of its own
    # accord. The one writer asks the security handler, which is what `encrypt`
    # sets up -- so the supported call still works on a document assembled in
    # memory, and qpdf and MuPDF both need the password to open the result.
    pdf = _in_memory()
    pdf.encrypt("pw")
    data = pdf.to_bytes()

    assert b"/Encrypt" in data
    assert b"(Body) Tj" not in data  # the content stream is no longer in clear
    assert Document(io.BytesIO(data), password="pw").extract_text().strip() == "Body"


def test_saying_a_document_is_encrypted_does_not_encrypt_it():
    # `encrypted` says how a document *arrived*; it is not a request. Without a
    # key there is nothing to encrypt with, and the file is written in clear
    # rather than with something invented.
    pdf = _in_memory()
    pdf.encrypted = True
    pdf.password = "pw"
    data = pdf.to_bytes()
    assert b"/Encrypt" not in data
    assert Document(io.BytesIO(data)).extract_text().strip() == "Body"
