"""Resource-limit regressions for PDF functions and shadings."""

from __future__ import annotations

from dataclasses import replace

import pytest

from aspose_pdf import Document, PdfLoadLimits, PdfResourceLimitException
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfStream,
)
from aspose_pdf.engine.shading import build_function
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.exceptions import PdfParseException
from aspose_pdf.load_limits import _LoadBudget


def _limits(**changes: int | None) -> PdfLoadLimits:
    return replace(PdfLoadLimits.unlimited(), **changes)


def _numbers(*values: float) -> PdfArray:
    return PdfArray([PdfNumber(value) for value in values])


def _exponential_function() -> PdfDictionary:
    return PdfDictionary(
        {
            PdfName("FunctionType"): PdfNumber(2),
            PdfName("Domain"): _numbers(0, 1),
            PdfName("C0"): _numbers(0),
            PdfName("C1"): _numbers(1),
            PdfName("N"): PdfNumber(1),
        }
    )


def _sampled_function(
    size: int,
    *,
    range_values: tuple[float, ...] = (0, 1),
) -> PdfStream:
    output_count = max(1, len(range_values) // 2)
    return PdfStream(
        bytes(size * output_count),
        {
            PdfName("FunctionType"): PdfNumber(0),
            PdfName("Domain"): _numbers(0, 1),
            PdfName("Size"): _numbers(size),
            PdfName("BitsPerSample"): PdfNumber(8),
            PdfName("Range"): _numbers(*range_values),
        },
    )


def test_function_graph_cycle_is_rejected() -> None:
    functions = PdfArray([])
    stitching = PdfDictionary(
        {
            PdfName("FunctionType"): PdfNumber(3),
            PdfName("Domain"): _numbers(0, 1),
            PdfName("Functions"): functions,
        }
    )
    functions.items.append(stitching)

    with pytest.raises(PdfParseException, match="function graph contains a cycle"):
        build_function(SimplePdf(), stitching)


def test_function_graph_honors_nesting_limit() -> None:
    function: PdfDictionary = _exponential_function()
    for _ in range(3):
        function = PdfDictionary(
            {
                PdfName("FunctionType"): PdfNumber(3),
                PdfName("Domain"): _numbers(0, 1),
                PdfName("Functions"): PdfArray([function]),
                PdfName("Bounds"): PdfArray([]),
                PdfName("Encode"): _numbers(0, 1),
            }
        )

    with pytest.raises(PdfResourceLimitException, match="max_nesting_depth"):
        build_function(
            SimplePdf(),
            function,
            limits=_limits(max_nesting_depth=3),
        )


def test_sampled_function_bounds_declared_sample_count() -> None:
    with pytest.raises(PdfResourceLimitException, match="sampled function entries"):
        build_function(
            SimplePdf(),
            _sampled_function(12),
            limits=_limits(max_container_items=10),
        )


def test_sampled_function_checks_range_before_materializing_it() -> None:
    range_values = tuple(float(value) for value in range(12))

    with pytest.raises(PdfResourceLimitException, match="sampled function Range items"):
        build_function(
            SimplePdf(),
            _sampled_function(2, range_values=range_values),
            limits=_limits(max_container_items=8),
        )


def test_sampled_function_propagates_decode_resource_limit() -> None:
    pdf = SimplePdf()

    def reject_decode(_stream, _source_ref=None):
        raise PdfResourceLimitException("decoded shading stream limit")

    pdf._decode_cos_stream = reject_decode

    with pytest.raises(PdfResourceLimitException, match="decoded shading stream limit"):
        build_function(pdf, _sampled_function(2))


def test_rasterizer_uses_document_budget_for_sampled_shading() -> None:
    pdf = SimplePdf(
        pages=[(0, 0, 10, 10)],
        page_contents=[b"/Sh0 sh"],
    )
    pdf._ensure_cos()
    limits = _limits(max_container_items=20)
    pdf._load_limits = limits
    pdf._load_budget = _LoadBudget(limits)
    shading = PdfDictionary(
        {
            PdfName("ShadingType"): PdfNumber(2),
            PdfName("ColorSpace"): PdfName("DeviceGray"),
            PdfName("Coords"): _numbers(0, 0, 10, 0),
            PdfName("Function"): _sampled_function(25),
        }
    )
    page = pdf._get_page_dict(0)
    page.mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("Shading"): PdfDictionary({PdfName("Sh0"): shading})}
    )
    document = Document()
    document._engine_pdf = pdf

    with pytest.raises(PdfResourceLimitException, match="sampled function entries"):
        document.pages[0].render(antialias=False)
