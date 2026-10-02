"""PDF/UA-2 wants one Document element at the structure tree root.

ISO 14289-2 8.2.5.2, by way of ISO 32000-2 Annex L and ISO/TS 32005: "the
structure tree root shall contain a single Document structure element as its only
child". veraPDF states it three ways -- exactly one ``Document``, no ``Hn``
directly under the root, no ``P`` directly under the root -- and **failed every
PDF/UA-2 document this converter produced**, because the authored headings and
paragraphs were attached to ``/StructTreeRoot`` itself, while
``validate_pdfua(part=2)`` reported it valid.

Part 1 has no such rule, which is why its output passed all along and still does
without a wrapper. veraPDF now passes both.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName


def _tagged(part: int) -> Document:
    document = Document()
    document.pages.add()
    document.pages[0].add_text("Heading", 50, 720, tag="H1")
    document.pages[0].add_text("Body text", 50, 690, tag="P")
    document.info["Title"] = "A Title"
    document.convert_to_pdfua(part=part, title="A Title")
    return document


def _struct_root(document: Document):
    engine = document._engine_pdf
    root = engine._resolve(engine._cos_doc.trailer.mapping.get(PdfName("Root")))
    return engine, engine._resolve(root.mapping.get(PdfName("StructTreeRoot")))


def _root_kids(document: Document) -> list:
    engine, struct_root = _struct_root(document)
    kids = engine._resolve(struct_root.mapping.get(PdfName("K")))
    return list(kids.items) if isinstance(kids, PdfArray) else []


def test_part_two_puts_one_document_at_the_root():
    document = _tagged(2)
    engine, _ = _struct_root(document)
    kids = _root_kids(document)
    assert len(kids) == 1

    wrapper = engine._resolve(kids[0])
    assert wrapper.mapping.get(PdfName("S")) == PdfName("Document")
    assert wrapper.mapping.get(PdfName("Type")) == PdfName("StructElem")
    # It names its namespace, which part 2 requires of every element.
    assert PdfName("NS") in wrapper.mapping


def test_the_authored_elements_become_its_children_in_order():
    document = _tagged(2)
    engine, _ = _struct_root(document)
    wrapper = engine._resolve(_root_kids(document)[0])
    inner = engine._resolve(wrapper.mapping.get(PdfName("K")))
    types = [
        engine._get_name(engine._resolve(item).mapping.get(PdfName("S")))
        for item in inner.items
    ]
    assert types == ["H1", "P"]


def test_the_children_are_re_parented_to_the_document():
    document = _tagged(2)
    engine, _ = _struct_root(document)
    wrapper_ref = _root_kids(document)[0]
    wrapper = engine._resolve(wrapper_ref)
    inner = engine._resolve(wrapper.mapping.get(PdfName("K")))
    for item in inner.items:
        child = engine._resolve(item)
        parent = child.mapping.get(PdfName("P"))
        assert parent.object_number == wrapper_ref.object_number


def test_part_one_gets_no_wrapper():
    document = _tagged(1)
    engine, _ = _struct_root(document)
    types = [
        engine._get_name(engine._resolve(item).mapping.get(PdfName("S")))
        for item in _root_kids(document)
    ]
    assert "Document" not in types
    assert document.validate_pdfua(part=1).is_valid


def test_converting_twice_adds_one_wrapper():
    document = _tagged(2)
    document.convert_to_pdfua(part=2, title="A Title")
    kids = _root_kids(document)
    assert len(kids) == 1
    engine, _ = _struct_root(document)
    assert engine._resolve(kids[0]).mapping.get(PdfName("S")) == PdfName("Document")


@pytest.mark.parametrize("part", [1, 2])
def test_our_validator_accepts_what_we_convert(part):
    assert _tagged(part).validate_pdfua(part=part).is_valid


def test_the_validator_rejects_a_root_without_the_wrapper():
    # The other half: this shape used to pass.
    document = _tagged(2)
    engine, struct_root = _struct_root(document)
    wrapper = engine._resolve(_root_kids(document)[0])
    struct_root.mapping[PdfName("K")] = wrapper.mapping.get(PdfName("K"))

    result = document.validate_pdfua(part=2)
    assert result.is_valid is False
    assert any("8.2.5.2" in str(e) for e in result.errors), result.errors


def test_the_validator_rejects_an_empty_structure_tree_for_part_two():
    document = _tagged(2)
    _, struct_root = _struct_root(document)
    struct_root.mapping[PdfName("K")] = PdfArray([])

    result = document.validate_pdfua(part=2)
    assert result.is_valid is False
    assert any("has none" in str(e) for e in result.errors), result.errors


def test_the_validator_rejects_two_documents_at_the_root():
    document = _tagged(2)
    engine, struct_root = _struct_root(document)
    existing = _root_kids(document)[0]
    second = engine._cos_doc.register_object(
        PdfDictionary(
            {PdfName("Type"): PdfName("StructElem"), PdfName("S"): PdfName("Document")}
        )
    )
    struct_root.mapping[PdfName("K")] = PdfArray([existing, second])

    result = document.validate_pdfua(part=2)
    assert result.is_valid is False
    assert any("only child" in str(e) for e in result.errors), result.errors


def test_the_document_survives_a_save_and_reload():
    document = _tagged(2)
    buffer = io.BytesIO()
    document.save(buffer)
    reopened = Document(io.BytesIO(buffer.getvalue()))
    assert reopened.validate_pdfua(part=2).is_valid
    assert len(_root_kids(reopened)) == 1
