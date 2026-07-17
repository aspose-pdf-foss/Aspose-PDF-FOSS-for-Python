"""Public tagged-PDF remediation API tests."""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document, StructureElement, TaggedContent
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName, PdfNumber
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.exceptions import PdfValidationException


_TWO_BLOCKS = (
    b"BT /F1 24 Tf 1 0 0 1 72 700 Tm (Heading) Tj ET\n"
    b"BT /F1 12 Tf 1 0 0 1 72 680 Tm (Body text) Tj ET"
)


def _auto_tagged_document() -> Document:
    document = Document()
    document._engine_pdf = SimplePdf(
        pages=[(0, 0, 612, 792)], page_contents=[_TWO_BLOCKS]
    )
    document._engine_pdf._ensure_cos()
    assert document.auto_tag() == 2
    return document


def test_tagged_content_is_public_and_edits_roundtrip() -> None:
    document = _auto_tagged_document()

    tagged = document.tagged_content
    assert isinstance(tagged, TaggedContent)
    heading, body = tagged.root_elements
    assert isinstance(heading, StructureElement)
    assert [heading.structure_type, body.structure_type] == ["H1", "P"]
    assert heading.page_number == 1
    assert heading.mcids == (0,)
    assert heading.parent is None

    heading.structure_type = "Figure"
    heading.alt_text = "Диаграмма продаж"
    heading.actual_text = "Sales chart"
    tagged.set_reading_order([body, heading])

    output = io.BytesIO()
    document.save(output)
    reloaded = Document().load_from(output.getvalue())
    first, second = reloaded.tagged_content.root_elements

    assert [first.structure_type, second.structure_type] == ["P", "Figure"]
    assert second.alt_text == "Диаграмма продаж"
    assert second.actual_text == "Sales chart"
    assert reloaded.tagged_content.element_for_mcid(1, 0) == second


def test_reparent_and_remove_clean_parent_tree_mappings() -> None:
    document = _auto_tagged_document()
    tagged = document.tagged_content
    heading, body = tagged.root_elements

    section = tagged.add_element("Sect", index=0)
    heading.move_to(section)

    assert [element.structure_type for element in tagged.root_elements] == [
        "Sect",
        "P",
    ]
    assert section.children == [heading]
    assert heading.parent == section

    section.remove()

    assert tagged.root_elements == [body]
    assert tagged.element_for_mcid(1, 0) is None
    assert tagged.element_for_mcid(1, 1) == body
    with pytest.raises(PdfValidationException, match="no longer attached"):
        _ = heading.structure_type


def test_add_element_repairs_missing_mcid_mapping() -> None:
    content = b"/Figure <</MCID 3>> BDC q Q EMC"
    document = Document()
    document._engine_pdf = SimplePdf(
        pages=[(0, 0, 612, 792)], page_contents=[content]
    )
    document._engine_pdf._ensure_cos()

    figure = document.tagged_content.add_element(
        "Figure",
        page_number=1,
        mcids=[3],
        alt_text="Decorative divider",
    )

    assert figure.page_number == 1
    assert figure.mcids == (3,)
    assert document.tagged_content.element_for_mcid(1, 3) == figure
    with pytest.raises(PdfValidationException, match="already mapped"):
        document.tagged_content.add_element("P", page_number=1, mcids=[3])

    document.convert_to_pdfua(title="Remediated document")
    result = document.validate_pdfua()
    assert not any("MCID 3" in warning for warning in result.warnings)


def test_reading_order_preserves_non_structure_kids() -> None:
    content = b"/Sect <</MCID 0>> BDC q Q EMC"
    document = Document()
    document._engine_pdf = SimplePdf(
        pages=[(0, 0, 612, 792)], page_contents=[content]
    )
    document._engine_pdf._ensure_cos()
    tagged = document.tagged_content

    section = tagged.add_element("Sect", page_number=1, mcids=[0])
    first = section.add_child("P")
    second = section.add_child("Figure", alt_text="Chart")
    section.set_reading_order([second, first])

    assert section.mcids == (0,)
    assert section.children == [second, first]
    raw_kids = document._engine_pdf._resolve(
        section._element.mapping.get(PdfName("K"))
    )
    assert isinstance(raw_kids, PdfArray)
    assert isinstance(document._engine_pdf._resolve(raw_kids.items[0]), PdfNumber)


def test_move_rejects_descendant_parent_and_foreign_elements() -> None:
    document = _auto_tagged_document()
    tagged = document.tagged_content
    parent = tagged.add_element("Sect")
    child = parent.add_child("P")

    with pytest.raises(PdfValidationException, match="descendant"):
        parent.move_to(child)

    foreign = _auto_tagged_document().tagged_content.root_elements[0]
    with pytest.raises(ValueError, match="different document"):
        tagged.move(foreign)


def test_invalid_move_index_does_not_detach_element() -> None:
    document = _auto_tagged_document()
    tagged = document.tagged_content
    heading, body = tagged.root_elements

    with pytest.raises(IndexError, match="out of range"):
        heading.move_to(index=10)

    assert tagged.root_elements == [heading, body]
    assert tagged.element_for_mcid(1, 0) == heading


def test_remediation_handles_parent_tree_number_tree_kids() -> None:
    document = _auto_tagged_document()
    engine = document._engine_pdf
    catalog = engine._resolve(engine._cos_doc.trailer.get(PdfName("Root")))
    struct_root = engine._resolve(catalog.mapping.get(PdfName("StructTreeRoot")))
    parent_tree = engine._resolve(struct_root.mapping.get(PdfName("ParentTree")))
    nums = parent_tree.mapping.pop(PdfName("Nums"))
    leaf = engine._cos_doc.register_object(PdfDictionary({PdfName("Nums"): nums}))
    parent_tree.mapping[PdfName("Kids")] = PdfArray([leaf])

    tagged = document.tagged_content
    heading, body = tagged.root_elements
    assert tagged.element_for_mcid(1, 0) == heading

    heading.remove()
    assert tagged.element_for_mcid(1, 0) is None
    assert tagged.element_for_mcid(1, 1) == body

    figure = tagged.add_element(
        "Figure", page_number=1, mcids=[2], alt_text="Chart"
    )
    assert tagged.element_for_mcid(1, 2) == figure
    assert PdfName("Kids") not in parent_tree.mapping
    assert isinstance(parent_tree.mapping.get(PdfName("Nums")), PdfArray)
