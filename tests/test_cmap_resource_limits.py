"""Resource-limit coverage for CMap and CID width expansion."""

import pytest

from aspose_pdf import PdfLoadLimits, PdfResourceLimitException
from aspose_pdf.engine.content_stream_parser import (
    ContentStreamParser,
    load_cid_widths,
    parse_encoding_cmap,
    parse_to_unicode_cmap,
)


def test_cmap_input_uses_decoded_stream_limit() -> None:
    limits = PdfLoadLimits(max_decoded_stream_bytes=3)

    with pytest.raises(PdfResourceLimitException, match="max_decoded_stream_bytes"):
        parse_to_unicode_cmap(b"abcd", limits=limits)


def test_cmap_nonempty_lines_are_bounded() -> None:
    limits = PdfLoadLimits(max_container_items=2)

    with pytest.raises(PdfResourceLimitException, match="max_container_items"):
        parse_encoding_cmap(b"one\ntwo\nthree", limits=limits)


def test_cid_width_declared_range_is_bounded_before_expansion() -> None:
    limits = PdfLoadLimits(max_container_items=4)

    with pytest.raises(PdfResourceLimitException, match="max_container_items"):
        load_cid_widths([0, 10_000_000, 500], limits=limits)


def test_cid_width_nested_array_honors_nesting_limit() -> None:
    limits = PdfLoadLimits(max_nesting_depth=1)

    with pytest.raises(PdfResourceLimitException, match="max_nesting_depth"):
        load_cid_widths([0, [500]], limits=limits)


def test_to_unicode_bfrange_is_bounded_before_expansion() -> None:
    cmap = b"1 beginbfrange\n<0000> <ffff> <0041>\nendbfrange"
    limits = PdfLoadLimits(max_container_items=4)

    with pytest.raises(PdfResourceLimitException, match="max_container_items"):
        parse_to_unicode_cmap(cmap, limits=limits)


def test_encoding_cidrange_is_bounded_before_expansion() -> None:
    cmap = b"1 begincidrange\n<0000> <ffff> 0\nendcidrange"
    limits = PdfLoadLimits(max_container_items=4)

    with pytest.raises(PdfResourceLimitException, match="max_container_items"):
        parse_encoding_cmap(cmap, limits=limits)


def test_to_unicode_mapping_dict_is_bounded() -> None:
    cmap = (
        b"4 beginbfchar\n"
        b"<01> <0041> <02> <0042> <03> <0043> <04> <0044>\n"
        b"endbfchar"
    )
    limits = PdfLoadLimits(max_container_items=3)

    with pytest.raises(PdfResourceLimitException, match="max_container_items"):
        parse_to_unicode_cmap(cmap, limits=limits)


def test_content_stream_parser_threads_budget_to_cid_widths() -> None:
    resources = {
        "Font": {
            "F1": {
                "Subtype": "Type0",
                "Encoding": "Identity-H",
                "DescendantFonts": [
                    {"W": [0, 10_000_000, 500], "DW": 1000},
                ],
            }
        }
    }
    parser = ContentStreamParser(
        b"BT /F1 12 Tf <0001> Tj ET",
        resources,
        limits=PdfLoadLimits(max_container_items=4),
    )

    with pytest.raises(PdfResourceLimitException, match="max_container_items"):
        parser.extract_text()
