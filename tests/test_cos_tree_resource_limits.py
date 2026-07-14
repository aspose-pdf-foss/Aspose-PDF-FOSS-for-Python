"""Resource-limit regressions for auxiliary COS graph traversals."""

from __future__ import annotations

from dataclasses import replace

import pytest

from aspose_pdf import PdfLoadLimits, PdfResourceLimitException
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfDocument,
    PdfName,
    PdfNumber,
    PdfStream,
    PdfString,
)
from aspose_pdf.engine.pdf_parser_cos import PdfCosParser
from aspose_pdf.engine.rasterizer import _PageRasterizer
from aspose_pdf.engine.simple_pdf import CosExtractor, SimplePdf
from aspose_pdf.exceptions import PdfParseException
from aspose_pdf.load_limits import _LoadBudget


def _limits(**changes: int | None) -> PdfLoadLimits:
    return replace(PdfLoadLimits.unlimited(), **changes)


def _document_with_catalog(catalog: PdfDictionary) -> PdfDocument:
    document = PdfDocument()
    document.objects = {}
    document.trailer = PdfDictionary({PdfName("Root"): catalog})
    return document


def _named_field(name: str) -> PdfDictionary:
    return PdfDictionary({PdfName("T"): PdfString(name.encode("ascii"))})


def _simple_pdf(limits: PdfLoadLimits) -> SimplePdf:
    pdf = SimplePdf()
    pdf._load_limits = limits
    pdf._load_budget = _LoadBudget(limits)
    return pdf


def test_outline_tree_uses_configured_nesting_limit() -> None:
    deepest = PdfDictionary({PdfName("Title"): PdfString(b"third")})
    middle = PdfDictionary(
        {
            PdfName("Title"): PdfString(b"second"),
            PdfName("First"): deepest,
        }
    )
    first = PdfDictionary(
        {
            PdfName("Title"): PdfString(b"first"),
            PdfName("First"): middle,
        }
    )
    outlines = PdfDictionary({PdfName("First"): first})
    document = _document_with_catalog(
        PdfDictionary({PdfName("Outlines"): outlines})
    )

    extractor = CosExtractor(
        document,
        b"",
        limits=_limits(max_nesting_depth=2),
    )

    with pytest.raises(PdfResourceLimitException, match="max_nesting_depth"):
        extractor.extract_outlines()


def test_outline_tree_uses_configured_item_limit() -> None:
    third = PdfDictionary({PdfName("Title"): PdfString(b"third")})
    second = PdfDictionary(
        {
            PdfName("Title"): PdfString(b"second"),
            PdfName("Next"): third,
        }
    )
    first = PdfDictionary(
        {
            PdfName("Title"): PdfString(b"first"),
            PdfName("Next"): second,
        }
    )
    document = _document_with_catalog(
        PdfDictionary(
            {PdfName("Outlines"): PdfDictionary({PdfName("First"): first})}
        )
    )

    extractor = CosExtractor(
        document,
        b"",
        limits=_limits(max_container_items=2),
    )

    with pytest.raises(PdfResourceLimitException, match="max_container_items"):
        extractor.extract_outlines()


def test_signature_field_cycle_is_rejected() -> None:
    field = _named_field("signature-parent")
    field[PdfName("Kids")] = PdfArray([field])
    catalog = PdfDictionary(
        {
            PdfName("AcroForm"): PdfDictionary(
                {PdfName("Fields"): PdfArray([field])}
            )
        }
    )
    extractor = CosExtractor(_document_with_catalog(catalog), b"")

    with pytest.raises(PdfParseException, match="cycle"):
        extractor.extract_signatures(b"")


def test_signature_field_tree_uses_configured_nesting_limit() -> None:
    third = _named_field("third")
    second = _named_field("second")
    second[PdfName("Kids")] = PdfArray([third])
    first = _named_field("first")
    first[PdfName("Kids")] = PdfArray([second])
    catalog = PdfDictionary(
        {
            PdfName("AcroForm"): PdfDictionary(
                {PdfName("Fields"): PdfArray([first])}
            )
        }
    )
    extractor = CosExtractor(
        _document_with_catalog(catalog),
        b"",
        limits=_limits(max_nesting_depth=2),
    )

    with pytest.raises(PdfResourceLimitException, match="max_nesting_depth"):
        extractor.extract_signatures(b"")


def test_form_field_cycle_is_rejected() -> None:
    field = _named_field("parent")
    field[PdfName("Kids")] = PdfArray([field])
    catalog = PdfDictionary(
        {
            PdfName("AcroForm"): PdfDictionary(
                {PdfName("Fields"): PdfArray([field])}
            )
        }
    )
    extractor = CosExtractor(_document_with_catalog(catalog), b"")

    with pytest.raises(PdfParseException, match="cycle"):
        extractor.extract_form_fields()


def test_form_field_tree_uses_configured_item_limit() -> None:
    third = _named_field("third")
    second = _named_field("second")
    second[PdfName("Kids")] = PdfArray([third])
    first = _named_field("first")
    first[PdfName("Kids")] = PdfArray([second])
    catalog = PdfDictionary(
        {
            PdfName("AcroForm"): PdfDictionary(
                {PdfName("Fields"): PdfArray([first])}
            )
        }
    )
    extractor = CosExtractor(
        _document_with_catalog(catalog),
        b"",
        limits=_limits(max_container_items=2),
    )

    with pytest.raises(PdfResourceLimitException, match="max_container_items"):
        extractor.extract_form_fields()


def test_cos_resource_conversion_rejects_cycles() -> None:
    resource = PdfDictionary()
    resource[PdfName("Loop")] = resource
    pdf = _simple_pdf(PdfLoadLimits())

    with pytest.raises(PdfParseException, match="cycle"):
        pdf._convert_cos_to_dict(resource)


def test_cos_resource_conversion_uses_configured_nesting_limit() -> None:
    resource = PdfDictionary(
        {
            PdfName("Nested"): PdfArray(
                [PdfDictionary({PdfName("Value"): PdfString(b"x")})]
            )
        }
    )
    pdf = _simple_pdf(_limits(max_nesting_depth=2))

    with pytest.raises(PdfResourceLimitException, match="max_nesting_depth"):
        pdf._convert_cos_to_dict(resource)


def test_cos_resource_conversion_uses_aggregate_item_limit() -> None:
    resource = PdfDictionary(
        {
            PdfName("Nested"): PdfDictionary(
                {
                    PdfName("First"): PdfString(b"a"),
                    PdfName("Second"): PdfString(b"b"),
                }
            )
        }
    )
    pdf = _simple_pdf(_limits(max_container_items=2))

    with pytest.raises(PdfResourceLimitException, match="max_container_items"):
        pdf._convert_cos_to_dict(resource)


def test_page_parent_cycle_is_rejected() -> None:
    page = PdfDictionary()
    page[PdfName("Parent")] = page
    pdf = _simple_pdf(PdfLoadLimits())

    with pytest.raises(PdfParseException, match="cycle"):
        pdf._get_inherited_attr(page, "Rotate")


def test_annotation_property_conversion_rejects_cycles() -> None:
    value = PdfArray()
    value.items.append(value)
    pdf = _simple_pdf(PdfLoadLimits())

    with pytest.raises(PdfParseException, match="cycle"):
        pdf._annotation_cos_to_value(value)


def test_annotation_property_conversion_uses_nesting_limit() -> None:
    value = PdfArray([PdfArray([PdfArray([PdfString(b"x")])])])
    pdf = _simple_pdf(_limits(max_nesting_depth=2))

    with pytest.raises(PdfResourceLimitException, match="max_nesting_depth"):
        pdf._annotation_cos_to_value(value)


def test_validate_uses_resource_depth_without_recursive_python_walk() -> None:
    nested = PdfArray([PdfArray([PdfArray([PdfString(b"x")])])])
    catalog = PdfDictionary({PdfName("Nested"): nested})
    document = _document_with_catalog(catalog)
    document.objects = {1: catalog}
    pdf = _simple_pdf(_limits(max_nesting_depth=2))
    pdf.pages = [(0.0, 0.0, 100.0, 100.0)]
    pdf._cos_doc = document

    with pytest.raises(PdfResourceLimitException, match="max_nesting_depth"):
        pdf.validate(max_depth=10_000)


def test_validate_rejects_literal_cycle_without_recursion_error() -> None:
    catalog = PdfDictionary()
    catalog[PdfName("Loop")] = catalog
    document = _document_with_catalog(catalog)
    document.objects = {1: catalog}
    pdf = _simple_pdf(PdfLoadLimits())
    pdf.pages = [(0.0, 0.0, 100.0, 100.0)]
    pdf._cos_doc = document

    assert pdf.validate(max_depth=10_000) is False


def test_rasterizer_cid_width_range_is_bounded_before_expansion() -> None:
    limits = _limits(max_container_items=10)
    rasterizer = object.__new__(_PageRasterizer)
    rasterizer.pdf = _simple_pdf(limits)
    rasterizer._load_limits = limits
    rasterizer._load_budget = rasterizer.pdf._load_budget
    cid_font = PdfDictionary(
        {
            PdfName("W"): PdfArray(
                [PdfNumber(0), PdfNumber(10_000_000), PdfNumber(500)]
            )
        }
    )

    with pytest.raises(PdfResourceLimitException, match="max_container_items"):
        rasterizer._cid_widths(cid_font)


def test_object_stream_member_size_is_checked_before_tokenizing() -> None:
    first_body = b"<< /A 1 >>"
    second_body = b"<< /B 2 >>"
    second_offset = len(first_body) + 1
    header = f"1 0 2 {second_offset} ".encode("ascii")
    content = header + first_body + b" " + second_body
    stream = PdfStream(
        mapping={
            PdfName("N"): PdfNumber(2),
            PdfName("First"): PdfNumber(len(header)),
        },
        content=content,
    )
    parser = PdfCosParser(
        b"%PDF-1.7",
        limits=_limits(max_object_bytes=5),
    )

    with pytest.raises(PdfResourceLimitException, match="max_object_bytes"):
        parser._parse_object_stream(stream)
