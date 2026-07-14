"""Resource-limit coverage for heuristic content scanners."""

import pytest

from aspose_pdf import PdfLoadLimits, PdfResourceLimitException
from aspose_pdf.engine.auto_tag import (
    find_image_placements,
    find_layout_elements,
    find_mcids,
    find_text_objects,
    has_marked_content,
)
from aspose_pdf.load_limits import _LoadBudget


def test_content_scanner_rejects_oversized_stream() -> None:
    limits = PdfLoadLimits(max_content_stream_bytes=2)

    with pytest.raises(PdfResourceLimitException, match="max_content_stream_bytes"):
        has_marked_content(b"abc", limits=limits)


def test_content_scanner_enforces_token_limit() -> None:
    limits = PdfLoadLimits(max_content_tokens=2)

    with pytest.raises(PdfResourceLimitException, match="max_content_tokens"):
        has_marked_content(b"1 2 3", limits=limits)


def test_text_object_wrapper_uses_one_token_scan() -> None:
    content = b"BT (x) Tj ET"
    limits = PdfLoadLimits(max_content_tokens=4)
    budget = _LoadBudget(limits)

    objects = find_text_objects(content, budget=budget)

    assert len(objects) == 1


@pytest.mark.parametrize(
    "content",
    [
        b"BT (((x))) Tj ET",
        b"[[[1]]]",
        b"<< /A << /B << /C 1 >> >> >>",
    ],
)
def test_content_scanner_enforces_syntax_nesting(content: bytes) -> None:
    limits = PdfLoadLimits(max_nesting_depth=2)

    with pytest.raises(PdfResourceLimitException, match="max_nesting_depth"):
        find_layout_elements(content, limits=limits)


def test_image_placement_scanner_limits_graphics_state_stack() -> None:
    limits = PdfLoadLimits(max_nesting_depth=2)

    with pytest.raises(PdfResourceLimitException, match="max_nesting_depth"):
        find_image_placements(b"q q q /Im0 Do Q Q Q", limits=limits)


def test_layout_scanner_limits_numeric_operand_buffer() -> None:
    limits = PdfLoadLimits(max_container_items=2)

    with pytest.raises(PdfResourceLimitException, match="max_container_items"):
        find_layout_elements(b"1 2 3 cm", limits=limits)


def test_layout_scanner_limits_result_list() -> None:
    limits = PdfLoadLimits(max_container_items=1)

    with pytest.raises(PdfResourceLimitException, match="max_container_items"):
        find_layout_elements(b"BT ET BT ET", limits=limits)


def test_mcid_scanner_limits_result_set() -> None:
    content = b"/P <</MCID 0>> BDC EMC /P <</MCID 1>> BDC EMC"
    limits = PdfLoadLimits(max_container_items=1)

    with pytest.raises(PdfResourceLimitException, match="max_container_items"):
        find_mcids(content, limits=limits)
