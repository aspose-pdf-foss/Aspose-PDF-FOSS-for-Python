"""Human-readable page ranges compose with the public page selection APIs."""

from __future__ import annotations

import io
import zipfile

import pytest

from aspose_pdf import (
    ByteArrayDataSource,
    Document,
    PdfLoadLimits,
    PdfResourceLimitException,
    SplitOptions,
    Splitter,
    parse_page_range,
)
from aspose_pdf.exceptions import PdfValidationException


@pytest.mark.parametrize(
    "expression,count,expected",
    [
        ("1", 4, (0,)),
        ("4", 4, (3,)),
        ("1-3,7,10-", 12, (0, 1, 2, 6, 9, 10, 11)),
        ("3,1-2,2,3-", 4, (2, 0, 1, 1, 2, 3)),
        ("1-", 4, (0, 1, 2, 3)),
        ("4-", 4, (3,)),
        ("2-2", 4, (1,)),
        (" \t3 , 1 - 2 , 4 - \n", 4, (2, 0, 1, 3)),
        ("001-002", 4, (0, 1)),
        ("0" * 5000 + "1", 4, (0,)),
    ],
)
def test_ranges_return_zero_based_indices_in_requested_order(expression, count, expected):
    assert parse_page_range(expression, count) == expected


def test_numbering_can_be_explicitly_zero_based():
    assert parse_page_range("0-2,4-", 5, one_based=False) == (0, 1, 2, 4)
    assert parse_page_range("0-", 1, one_based=False) == (0,)
    with pytest.raises(PdfValidationException, match="out of range"):
        parse_page_range("5", 5, one_based=False)


@pytest.mark.parametrize(
    "expression",
    [
        "", " ", ",", "1,", ",1", "1,,2", "-", "-3", "1--3", "3-1",
        "0", "1-0", "5", "2-5", "5-", "1.5", "+1", "1:3", "1-3-5",
        "all", "odd", "1 2", "١", "１", "\ud800", "9" * 5000,
    ],
)
def test_invalid_or_out_of_bounds_expressions_are_rejected(expression):
    with pytest.raises(PdfValidationException):
        parse_page_range(expression, 4)


@pytest.mark.parametrize("expression", ["1", "1-", "0-"])
def test_empty_documents_have_no_valid_page_ranges(expression):
    with pytest.raises(PdfValidationException, match="out of range"):
        parse_page_range(expression, 0)


@pytest.mark.parametrize("expression", [None, b"1", 1, [1]])
def test_expression_must_be_text(expression):
    with pytest.raises(TypeError, match="expression"):
        parse_page_range(expression, 4)


@pytest.mark.parametrize("count", [True, 4.0, "4", None])
def test_page_count_must_be_an_integer(count):
    with pytest.raises(TypeError, match="page_count"):
        parse_page_range("1", count)


def test_negative_page_count_and_non_boolean_numbering_are_rejected():
    with pytest.raises(ValueError, match="page_count"):
        parse_page_range("1", -1)
    with pytest.raises(TypeError, match="one_based"):
        parse_page_range("1", 4, one_based=1)


@pytest.mark.parametrize("expression", ["1-1000000000", "1-"])
def test_huge_ranges_are_bounded_before_expansion(expression):
    with pytest.raises(PdfResourceLimitException, match="max_container_items"):
        parse_page_range(expression, 1_000_000_000)


def test_limits_count_repeated_pages_and_clauses_and_encoded_expression_bytes():
    limits = PdfLoadLimits(max_container_items=3)
    assert parse_page_range("1-3", 4, limits=limits) == (0, 1, 2)
    for expression in ("1-3,1", "1,1,1,1"):
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            parse_page_range(expression, 4, limits=limits)
    with pytest.raises(PdfResourceLimitException, match="max_object_bytes"):
        parse_page_range("1-3", 4, limits=PdfLoadLimits(max_object_bytes=2))
    with pytest.raises(PdfResourceLimitException, match="max_object_bytes"):
        parse_page_range("\u20031", 4, limits=PdfLoadLimits(max_object_bytes=3))


def _pdf(count=4):
    with Document() as document, io.BytesIO() as output:
        for index in range(count):
            document.pages.add(size=(24, 24)).add_text(f"P{index + 1}", 2, 12, font_size=6)
        document.save(output)
        return output.getvalue()


@pytest.mark.parametrize("source_kind", ["bytes", "file", "streaming"])
def test_document_extracts_range_strings_and_saves_independent_pages(source_kind, tmp_path):
    raw = _pdf()
    path = tmp_path / "input.pdf"
    path.write_bytes(raw)
    source = (
        Document.open_streaming(path) if source_kind == "streaming"
        else Document(path if source_kind == "file" else raw)
    )
    with source, source.extract_pages("3,1-2,4-") as selected:
        assert selected.extract_text() == "P3\nP1\nP2\nP4"
        with io.BytesIO() as output:
            selected.save(output)
            with Document(output.getvalue()) as reopened:
                assert reopened.extract_text() == selected.extract_text()
        assert source.extract_text() == "P1\nP2\nP3\nP4"


def test_parsed_ranges_select_text_and_rendered_pages():
    with Document(_pdf()) as document:
        selection = parse_page_range("3,1-2,2", document.page_count)
        assert document.extract_text(pages=selection) == "P3\nP1\nP2\nP2"
        data = document.export_pages_zip(pages=parse_page_range("1,3-", 4))
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            assert len(archive.namelist()) == 3


def test_direct_extraction_preserves_repeated_pages():
    with Document(_pdf()) as source, source.extract_pages("3,1-2,2") as selected:
        assert selected.extract_text() == "P3\nP1\nP2\nP2"
        assert source.page_count == 4


def test_invalid_document_range_is_checked_before_import_or_outline_flush(monkeypatch):
    with Document(_pdf()) as document:
        def unexpected(*args, **kwargs):
            raise AssertionError("Invalid ranges must be rejected before processing")

        monkeypatch.setattr(document._engine_pdf, "extract_pages", unexpected)
        monkeypatch.setattr(document, "_flush_outlines", unexpected)
        with pytest.raises(PdfValidationException):
            document.extract_pages("1,5")


def test_document_range_expansion_uses_its_resource_limits():
    with Document(limits=PdfLoadLimits(max_container_items=2)) as document:
        document.pages.add()
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            document.extract_pages("1,1,1")


def test_splitter_resolves_open_ends_per_input_and_keeps_existing_selections():
    options = SplitOptions(selections=["3-", [0], slice(1, 2)])
    options.add_input(ByteArrayDataSource(_pdf(3)))
    options.add_input(ByteArrayDataSource(_pdf(4)))
    results = Splitter().process(options)
    texts = []
    for result in results:
        with Document(result.to_array()) as document:
            texts.append(document.extract_text())
    assert texts == ["P3", "P1", "P2", "P3\nP4", "P1", "P2"]


def test_splitter_invalid_later_range_does_not_write_partial_output():
    output = ByteArrayDataSource(b"unchanged")
    options = SplitOptions(selections=["1", "5-"])
    options.add_input(ByteArrayDataSource(_pdf()))
    options.add_output(output)
    with pytest.raises(PdfValidationException, match="out of range"):
        Splitter().process(options)
    assert output.read_bytes() == b"unchanged"


def test_splitter_range_expansion_obeys_plugin_limits():
    options = SplitOptions(
        limits=PdfLoadLimits(max_container_items=20), selections=["1-" + ",1-" * 5]
    )
    options.add_input(ByteArrayDataSource(_pdf()))
    with pytest.raises(PdfResourceLimitException, match="expanded page range selection"):
        Splitter().process(options)
