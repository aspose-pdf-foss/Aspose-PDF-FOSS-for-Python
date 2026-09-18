"""A signed document's signatures are reachable from the public ``Document``.

``Document`` had no ``signatures``, so everything built on it saw an unsigned
file. ``SignaturesCompromiseDetector(Document(...))`` reported "unsigned
document" -- including for a file whose page was replaced after signing -- and
``UnsignedContentAbsorber`` read ``is_signed`` flags nothing carries, reporting
every page, field and annotation of a fully signed document as unsigned, the
signature field included.

Unsigned content is now what is new or different since the newest signature
that verifies: a page, its contents or its resources; an annotation or its
appearance; a field or its widgets.
"""

from __future__ import annotations

import io
import re

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.sign_field import sign_field
from aspose_pdf.engine.signing import SigningUtils
from aspose_pdf.forms import UnsignedContentAbsorber
from aspose_pdf.security import SignaturesCompromiseDetector


@pytest.fixture(scope="module")
def signed() -> bytes:
    document = Document()
    page = document.pages.add()
    page.add_text("Signed body", 72, 700, font_size=18)
    document.form.add_text_field("Name", page, (72, 600, 272, 624))
    document.form.add_signature_field("Sig1", page, (300, 600, 500, 680))
    buffer = io.BytesIO()
    document.save(buffer)
    cert, key = SigningUtils.create_self_signed_cert()
    return sign_field(buffer.getvalue(), "Sig1", cert, key)


def _unsigned(data: bytes, **kwargs) -> tuple[int, list[str], list[str]]:
    content = UnsignedContentAbsorber(Document(io.BytesIO(data), **kwargs)).extract()
    return (
        len(content.pages),
        sorted(field.name for field in content.form_fields),
        sorted(annotation.subtype for annotation in content.annotations),
    )


def _incremental(data: bytes, edit) -> bytes:
    document = Document(io.BytesIO(data))
    edit(document)
    buffer = io.BytesIO()
    document.save(buffer, incremental=True)
    assert buffer.getvalue().startswith(data)
    return buffer.getvalue()


def _replace_page_content(data: bytes, text: bytes) -> bytes:
    """Append a revision that swaps the first page's content stream for another."""
    return _replace_page(data, Document(io.BytesIO(data))._engine_pdf._page_obj_ids[0], text)


def _replace_page(data: bytes, page_number: int, text: bytes) -> bytes:
    """Append a revision that swaps page object *page_number*'s content stream."""
    trailer = data[data.rindex(b"trailer") :]
    size = int(re.search(rb"/Size (\d+)", trailer).group(1))
    root = int(re.search(rb"/Root (\d+) 0 R", trailer).group(1))
    previous = int(re.search(rb"startxref\s+(\d+)", trailer).group(1))
    page = re.search(rb"\n%d 0 obj\n(.*?)\nendobj" % page_number, data, re.S).group(1)
    page = re.sub(rb"/Contents (\d+ 0 R|\[[^\]]*\])", b"/Contents %d 0 R" % size, page)
    content = b"BT /F1 18 Tf 72 700 Td (" + text + b") Tj ET"
    at = len(data)
    revision = b"%d 0 obj\n" % page_number + page + b"\nendobj\n"
    stream_at = at + len(revision)
    revision += b"%d 0 obj\n<< /Length %d >>\nstream\n" % (size, len(content)) + content + b"\nendstream\nendobj\n"
    table_at = at + len(revision)
    revision += b"xref\n0 1\n0000000000 65535 f \n%d 1\n%010d 00000 n \n%d 1\n%010d 00000 n \n" % (
        page_number,
        at,
        size,
        stream_at,
    )
    revision += b"trailer\n<< /Size %d /Root %d 0 R /Prev %d >>\nstartxref\n%d\n%%%%EOF\n" % (
        size + 1,
        root,
        previous,
        table_at,
    )
    return data + revision


# --- the signatures themselves ---------------------------------------------------------


def test_a_document_lists_its_signatures(signed):
    signatures = Document(io.BytesIO(signed)).signatures
    assert [signature.name for signature in signatures] == ["Sig1"]
    assert signatures[0].valid


def test_a_document_without_signatures_lists_none():
    assert Document().signatures == []


def test_the_compromise_detector_reads_a_document(signed):
    clean = SignaturesCompromiseDetector(Document(io.BytesIO(signed))).check()
    assert (clean.compromised, clean.signatures_coverage) == (False, 1)
    replaced = SignaturesCompromiseDetector(
        Document(io.BytesIO(_replace_page_content(signed, b"Swapped body")))
    ).check()
    assert replaced.compromised


# --- what the signatures cover --------------------------------------------------------------


def test_a_signed_document_has_no_unsigned_content(signed):
    assert _unsigned(signed) == (0, [], [])


def test_an_unsigned_document_is_unsigned_throughout():
    document = Document()
    page = document.pages.add()
    document.form.add_text_field("Name", page, (72, 600, 272, 624))
    buffer = io.BytesIO()
    document.save(buffer)
    assert _unsigned(buffer.getvalue()) == (1, ["Name"], ["Widget"])


def test_a_page_whose_content_was_replaced_is_unsigned(signed):
    assert _unsigned(_replace_page_content(signed, b"Swapped body")) == (1, [], [])


def test_an_annotation_added_after_signing_is_unsigned_but_not_its_page(signed):
    def annotate(document):
        document.pages[0].annotations.add("Text", (10, 10, 30, 30), "added later")

    assert _unsigned(_incremental(signed, annotate)) == (0, [], ["Text"])


def test_a_field_filled_after_signing_is_unsigned_with_its_widget(signed):
    def fill(document):
        next(field for field in document.form.fields if field.name == "Name").value = "Alice"

    assert _unsigned(_incremental(signed, fill)) == (0, ["Name"], ["Widget"])


def test_an_edit_in_memory_is_unsigned_before_it_is_saved(signed):
    document = Document(io.BytesIO(signed))
    document.pages[0].annotations.add("Text", (10, 10, 30, 30), "not saved yet")
    content = UnsignedContentAbsorber(document).extract()
    assert [annotation.subtype for annotation in content.annotations] == ["Text"]
    assert content.pages == [] and content.form_fields == []


def test_a_signature_that_does_not_verify_covers_nothing(signed):
    at = signed.index(b"Signed body")
    broken = signed[:at] + b"X" + signed[at + 1 :]
    pages, fields, annotations = _unsigned(broken)
    assert pages == 1 and fields == ["Name", "Sig1"] and annotations == ["Widget", "Widget"]


def test_an_encrypted_signed_document_compares_in_the_clear():
    cert, key = SigningUtils.create_self_signed_cert()
    document = Document()
    document.pages.add().add_text("Secret body", 72, 700, font_size=18)
    document.encrypt("user", "owner", algorithm="AES-128")
    document._engine_pdf.signing_creds = (cert, key)
    document._engine_pdf.signature = {"Name": "Signature1"}
    buffer = io.BytesIO()
    document.save(buffer)
    data = buffer.getvalue()

    reopened = Document(io.BytesIO(data), password="user")
    assert [signature.valid for signature in reopened.signatures] == [True]
    # Compared as ciphertext, every string and stream would differ.
    assert _unsigned(data, password="user") == (0, [], [])

    reopened.pages[0].annotations.add("Text", (10, 10, 30, 30), "added later")
    buffer = io.BytesIO()
    reopened.save(buffer, incremental=True)
    assert _unsigned(buffer.getvalue(), password="user") == (0, [], ["Text"])


# --- where the comparison has to look --------------------------------------------------------


@pytest.fixture(scope="module")
def twice_signed() -> bytes:
    """Two pages, a hierarchical field, and two signatures, one after the other."""
    document = Document()
    first, second = document.pages.add(), document.pages.add()
    first.add_text("Page one", 72, 700, font_size=18)
    second.add_text("Page two", 72, 700, font_size=18)
    document.form.add_text_field("person.name", first, (72, 600, 272, 624))
    document.form.add_signature_field("Sig1", first, (300, 600, 500, 680))
    document.form.add_signature_field("Sig2", second, (300, 600, 500, 680))
    buffer = io.BytesIO()
    document.save(buffer)
    cert, key = SigningUtils.create_self_signed_cert()
    once = sign_field(buffer.getvalue(), "Sig1", cert, key)
    return sign_field(once, "Sig2", cert, key)


def test_the_newest_signature_is_the_one_that_counts(twice_signed):
    # Measured from the first signature, the second one's field would be new.
    assert len(Document(io.BytesIO(twice_signed)).signatures) == 2
    assert _unsigned(twice_signed) == (0, [], [])


def test_a_page_is_judged_by_its_own_content_not_its_neighbours(twice_signed):
    page_two = Document(io.BytesIO(twice_signed))._engine_pdf._page_obj_ids[1]
    replaced = _replace_page(twice_signed, page_two, b"Swapped two")
    document = Document(io.BytesIO(replaced))
    content = UnsignedContentAbsorber(document).extract()
    assert [page.index for page in content.pages] == [document.pages[1].index]


def test_a_widget_changed_alone_leaves_its_field_unsigned(twice_signed):
    def recolour(document):
        widget = next(a for a in document.pages[0].annotations if a.subtype == "Widget")
        widget.color = (1.0, 0.0, 0.0)

    pages, fields, annotations = _unsigned(_incremental(twice_signed, recolour))
    assert pages == 0 and "person.name" in fields and "Widget" in annotations


def test_an_annotation_edited_in_place_is_unsigned(signed):
    annotated = _incremental(signed, lambda d: d.pages[0].annotations.add("Text", (10, 10, 30, 30), "first"))
    # Sign again so the note is covered, then edit it without adding an object.
    cert, key = SigningUtils.create_self_signed_cert()
    document = Document(io.BytesIO(annotated))
    document.form.add_signature_field("Sig2", document.pages[0], (300, 500, 500, 580))
    buffer = io.BytesIO()
    document.save(buffer, incremental=True)
    covered = sign_field(buffer.getvalue(), "Sig2", cert, key)
    assert _unsigned(covered) == (0, [], [])

    def edit(document):
        next(a for a in document.pages[0].annotations if a.subtype == "Text").contents = "second"

    assert _unsigned(_incremental(covered, edit)) == (0, [], ["Text"])
