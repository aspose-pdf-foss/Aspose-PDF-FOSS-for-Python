"""An embedded file is an *associated* file: the catalog's /AF points at it.

ISO 19005-3 clause 6.8 and ISO 19005-4 clause 6.9 require every embedded file to
be associated with the document or a part of it, which is what an ``/AF`` array
expresses -- veraPDF checks it as ``isAssociatedFile == true``. We stamped
``/AFRelationship`` on each file specification, which says *what* the
relationship is, but never put the specification in an ``/AF`` array, which is
what establishes that there is one. So **veraPDF failed every PDF/A-3 document
carrying an attachment** even though its relationship was stated.

The catalog's array is the one that means "associated with the document as a
whole", which is what a plain attachment is. It is written for every document,
PDF/A or not, on the same footing as ``/AFRelationship`` and ``/Subtype``: a
valid optional catalog key that nothing else reads.
"""

from __future__ import annotations

import io
import re

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import PdfName

AF_ARRAY = re.compile(rb"/AF \[([^\]]*)\]")


def _saved(level: str | None = None, attachments=("data.bin",)) -> bytes:
    document = Document()
    document.pages.add()
    document.pages[0].add_text("Heading", 50, 720)
    for name in attachments:
        document.add_attachment(name, b"payload-" + name.encode())
    document.info["Title"] = "A Title"
    if level:
        document.convert_to_pdfa(level)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _af_refs(data: bytes) -> list[bytes]:
    match = AF_ARRAY.search(data)
    return re.findall(rb"\d+ 0 R", match.group(1)) if match else []


@pytest.mark.parametrize("level", [None, "PDF/A-3B", "PDF/A-4F"])
def test_the_catalog_points_at_the_embedded_file(level):
    data = _saved(level)
    assert len(_af_refs(data)) == 1


def test_every_attachment_is_listed():
    data = _saved(attachments=("one.bin", "two.bin", "three.bin"))
    assert len(_af_refs(data)) == 3


def test_the_array_names_the_file_specifications_themselves():
    # /AF holds file specifications, the same objects the name tree holds -- not
    # the embedded file streams, and not copies of them.
    data = _saved()
    engine = Document(io.BytesIO(data))._engine_pdf
    root = engine._resolve(engine._cos_doc.trailer.mapping.get(PdfName("Root")))
    af = engine._resolve(root.mapping.get(PdfName("AF")))
    assert af is not None and len(af.items) == 1

    spec = engine._resolve(af.items[0])
    assert spec.mapping.get(PdfName("Type")) == PdfName("Filespec")
    assert PdfName("EF") in spec.mapping

    names = engine._resolve(root.mapping.get(PdfName("Names")))
    tree = engine._resolve(names.mapping.get(PdfName("EmbeddedFiles")))
    listed = engine._resolve(tree.mapping.get(PdfName("Names")))
    # The name tree's value for the attachment is that same object number.
    assert listed.items[1].object_number == af.items[0].object_number


def test_removing_the_last_attachment_takes_the_array_with_it():
    # An /AF pointing at a file specification that is no longer there would be a
    # dangling reference, so the removal path drops both.
    document = Document(io.BytesIO(_saved()))
    document.remove_attachment("data.bin")
    buffer = io.BytesIO()
    document.save(buffer)
    data = buffer.getvalue()

    assert b"/EmbeddedFiles" not in data
    assert _af_refs(data) == []


def test_a_document_with_no_attachments_gets_no_array():
    document = Document()
    document.pages.add()
    document.pages[0].add_text("Heading", 50, 720)
    buffer = io.BytesIO()
    document.save(buffer)
    assert _af_refs(buffer.getvalue()) == []


def test_the_relationship_is_still_stated():
    # /AF says there is an association; /AFRelationship says what it is. Both
    # are needed, and the second was already there.
    data = _saved("PDF/A-3B")
    assert b"/AFRelationship" in data
    assert len(_af_refs(data)) == 1


def test_the_attachment_still_round_trips():
    reopened = Document(io.BytesIO(_saved("PDF/A-3B")))
    assert list(reopened.attachments) == ["data.bin"]
    assert reopened.get_embedded_file("data.bin").contents == b"payload-data.bin"


def test_pdfa_1_reports_the_attachment_it_cannot_carry():
    """PDF/A-1 forbids embedded files, and an in-memory one is reported.

    ``convert_to_pdfa`` drops the ``/EmbeddedFiles`` tree it finds in the object
    graph, but an attachment added in this session is not in the graph yet -- it
    is written at save time -- so the conversion cannot remove it and says so
    instead, and ``validate_pdfa`` rejects the result. The ``/AF`` array follows
    the attachment it points at; both are forbidden here, and the warning is
    about both.
    """
    document = Document()
    document.pages.add()
    document.pages[0].add_text("Heading", 50, 720)
    document.add_attachment("data.bin", b"payload")
    warnings = document.convert_to_pdfa("PDF/A-1B")

    assert any("prohibit embedded files" in w for w in warnings), warnings
    assert document.validate_pdfa("PDF/A-1B").is_valid is False


def test_an_attachment_already_in_the_graph_is_dropped_for_pdfa_1():
    # The case the conversion *can* handle: the tree is there to remove.
    document = Document(io.BytesIO(_saved()))
    assert list(document.attachments) == ["data.bin"]
    document.convert_to_pdfa("PDF/A-1B")

    buffer = io.BytesIO()
    document.save(buffer)
    data = buffer.getvalue()
    assert b"/EmbeddedFiles" not in data
    assert _af_refs(data) == []
    assert list(Document(io.BytesIO(data)).attachments) == []
