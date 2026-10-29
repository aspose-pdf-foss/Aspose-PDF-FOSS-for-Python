"""Reverse page-label lookup follows current metadata without reading content."""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document, NumberingStyle, PageLabel, PdfLoadLimits
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName, PdfNumber, PdfString
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfResourceLimitException,
    PdfValidationException,
)


def _document(count=10, *, limits=None):
    document = Document(limits=limits)
    for _ in range(count):
        document.pages.add()
    return document


def _book():
    document = _document()
    document.page_labels[0] = PageLabel(NumberingStyle.NUMERALS_ROMAN_LOWERCASE)
    document.page_labels[3] = PageLabel()
    document.page_labels[7] = PageLabel(prefix="A-")
    return document


def _bytes(document):
    with io.BytesIO() as stream:
        document.save(stream)
        return stream.getvalue()


@pytest.mark.parametrize(
    "style,prefix,start,query,expected",
    [
        (NumberingStyle.NUMERALS_ARABIC, "", 9, "12", [3]),
        (NumberingStyle.NUMERALS_ROMAN_LOWERCASE, "", 1, "iv", [3]),
        (NumberingStyle.NUMERALS_ROMAN_UPPERCASE, "Vol ", 3998, "Vol MMMM", [2]),
        (NumberingStyle.LETTERS_UPPERCASE, "", 25, "AA", [2]),
        (NumberingStyle.LETTERS_LOWERCASE, "章-", 51, "章-aaa", [2]),
        (NumberingStyle.NONE, "Cover", 1, "Cover", list(range(6))),
        (NumberingStyle.NONE, "", 1, "", list(range(6))),
        (NumberingStyle.NUMERALS_ARABIC, "A-", 12, "A-12", [0]),
        (NumberingStyle.LETTERS_UPPERCASE, "", 25, "AB", []),
        (NumberingStyle.NUMERALS_ROMAN_LOWERCASE, "", 1, "iiii", []),
        (NumberingStyle.NUMERALS_ROMAN_LOWERCASE, "", 1, "IV", []),
        (NumberingStyle.NUMERALS_ARABIC, "", 1, "01", []),
        (NumberingStyle.NUMERALS_ARABIC, "", 1, " 1", []),
        (NumberingStyle.NUMERALS_ARABIC, "", 1, "１", []),
    ],
)
def test_all_styles_and_canonical_exact_matching(style, prefix, start, query, expected):
    with _document(6) as document:
        document.page_labels[0] = PageLabel(style, prefix, start)
        assert document.page_labels.find_pages(query) == expected


def test_restarted_and_prefix_only_ranges_return_all_matches_in_page_order():
    with _document(8) as document:
        labels = document.page_labels
        labels[0] = PageLabel()
        labels[2] = PageLabel()
        labels[4] = PageLabel(NumberingStyle.NONE, "1")
        labels[6] = PageLabel(NumberingStyle.NONE)
        before = _bytes(document)
        result = labels.find_pages("1")
        assert result == [0, 2, 4, 5]
        assert labels.find_pages("") == [6, 7]
        result.append(999)
        assert labels.find_pages("1") == [0, 2, 4, 5]
        assert labels.find_pages("missing") == []
        assert _bytes(document) == before


def test_unicode_case_and_whitespace_are_not_normalized():
    with _document(2) as document:
        document.page_labels[0] = PageLabel(NumberingStyle.NONE, " e\u0301- ")
        assert document.page_labels.find_pages(" e\u0301- ") == [0, 1]
        for query in (" é- ", "e\u0301-", " E\u0301- ", " e\u0301-"):
            assert document.page_labels.find_pages(query) == []


def test_same_text_with_different_prefix_and_number_splits_matches_every_page():
    with _document(4) as document:
        document.page_labels[0] = PageLabel(prefix="A1", start=2)
        document.page_labels[1] = PageLabel(prefix="A", start=12)
        document.page_labels[2] = PageLabel(NumberingStyle.NONE, "A12")
        assert document.page_labels.find_pages("A12") == [0, 1, 2, 3]


@pytest.mark.parametrize(
    "style,start",
    [
        ("D", -100),
        ("D", 9998),
        ("R", -5),
        ("R", 1),
        ("R", 440),
        ("R", 940),
        ("r", 3998),
        ("A", -5),
        ("A", 20),
        ("A", 50),
        ("a", 675),
    ],
)
def test_engine_inverse_matches_canonical_formatter_across_number_boundaries(
    style, start
):
    from aspose_pdf.engine import page_labels
    from aspose_pdf.load_limits import _LoadBudget

    rule = page_labels.LabelRange(style, "p-", start)
    values = [page_labels.format_label(rule, start + offset) for offset in range(60)]
    for query in {*values, "p-IC", "p-AB", "p-01", "p-+1"}:
        assert page_labels.find_pages([(0, rule)], 60, query, _LoadBudget()) == [
            index for index, value in enumerate(values) if value == query
        ]


@pytest.mark.parametrize("count", [0, 3])
def test_absent_labels_do_not_match_viewer_page_positions(count):
    with _document(count) as document:
        assert document.page_labels.find_pages("1") == []
        assert document.page_labels.find_pages("") == []


@pytest.mark.parametrize(
    "rules",
    [
        [],
        [(2, "r", "", 1)],
        [(-1, "r", "", 1), (2, "D", "", 1)],
        [(0, "D", "", -2)],
        [(0, "r", "", -2)],
        [(0, "A", "", 0)],
        [(0, "X", "p-", 1)],
        [(3, "D", "", 10), (0, "r", "", 1)],
        [(0, "r", "", 1), (0, "D", "", 1)],
        [(0, "r", "", 1), (2.5, "D", "", 1)],
        [(0, "D", "", 2.7)],
        [(20, "r", "", 1)],
    ],
)
def test_existing_tree_reading_conventions_match_page_label_values(rules):
    with _document(6) as document:
        engine = document._engine_pdf
        engine._ensure_cos()
        entries = []
        for key, style, prefix, start in rules:
            entries.extend(
                (
                    PdfNumber(key),
                    PdfDictionary(
                        {
                            PdfName("S"): PdfName(style),
                            PdfName("P"): PdfString(prefix),
                            PdfName("St"): PdfNumber(start),
                        }
                    ),
                )
            )
        catalog = engine._resolve(engine._cos_doc.trailer.mapping[PdfName("Root")])
        catalog.mapping[PdfName("PageLabels")] = PdfDictionary(
            {PdfName("Nums"): PdfArray(entries)}
        )
        with Document(_bytes(document)) as reopened:
            values = [page.label for page in reopened.pages]
            for query in {*values, "", "missing"}:
                assert reopened.page_labels.find_pages(query) == [
                    index for index, value in enumerate(values) if value == query
                ]


def test_unsaved_range_changes_and_page_edits_are_visible_immediately():
    with _book() as document:
        labels = document.page_labels
        original = labels.find_pages("A-2")
        assert original == [8]
        document.pages.insert(0)
        assert labels.find_pages("A-2") == [9]
        document.pages.delete(0)
        assert labels.find_pages("A-2") == [8]
        labels[7] = PageLabel(prefix="B-")
        assert labels.find_pages("A-2") == []
        assert labels.find_pages("B-2") == [8]
        assert original == [8]
        labels.clear()
        assert labels.find_pages("B-2") == []
        assert labels.find_pages("1") == []


def test_extracted_repeated_pages_and_merges_preserve_lookup_labels():
    with _book() as source, source.extract_pages([8, 0, 8]) as selected:
        assert selected.page_labels.find_pages("A-2") == [0, 2]
        assert selected.page_labels.find_pages("i") == [1]
        with _document(2) as unlabelled:
            selected.merge(unlabelled)
        assert selected.page_labels.find_pages("") == [3, 4]
        assert selected.page_labels.find_pages("A-2") == [0, 2]


@pytest.mark.parametrize("kind", ["bytes", "file", "streaming"])
@pytest.mark.parametrize("encrypted", [False, True])
def test_round_trip_lookup_reads_tree_once_and_never_decodes_page_content(
    kind, encrypted, tmp_path, monkeypatch
):
    path = tmp_path / "book.pdf"
    with _book() as source:
        source.pages[0].add_text("Content stays unread", 10, 10)
        if encrypted:
            source.encrypt("secret", "owner")
        source.save(path)
    before = path.read_bytes()
    arguments = {"password": "secret"} if encrypted else {}
    if kind == "bytes":
        document = Document(before, **arguments)
    else:
        document = (Document.open_streaming if kind == "streaming" else Document)(
            path, **arguments
        )
    with document:
        engine = document._engine_pdf
        original = engine.page_label_ranges
        reads = []

        def read():
            reads.append(True)
            return original()

        def forbidden(*args, **kwargs):
            pytest.fail("Label lookup must not read or render page content")

        monkeypatch.setattr(engine, "page_label_ranges", read)
        monkeypatch.setattr(engine, "get_page_content", forbidden)
        monkeypatch.setattr(engine, "extract_page_text", forbidden)
        monkeypatch.setattr(document, "render_page", forbidden)
        engine._page_text_cursor = 2
        cached = engine._extracted_text
        assert document.page_labels.find_pages("ii") == [1]
        assert len(reads) == 1
        assert document.page_labels.find_pages("A-2") == [8]
        assert len(reads) == 2
        assert engine._page_text_cursor == 2 and engine._extracted_text == cached
        if kind == "streaming":
            assert engine.page_contents == []
    assert path.read_bytes() == before


@pytest.mark.parametrize("query", [None, 1, True, b"1", [], {}])
def test_non_string_queries_fail_before_reading_tree(query, monkeypatch):
    with _book() as document:

        def forbidden():
            pytest.fail("Invalid query types must fail before reading label metadata")

        monkeypatch.setattr(document._engine_pdf, "page_label_ranges", forbidden)
        with pytest.raises(TypeError, match="label must be a string"):
            document.page_labels.find_pages(query)


def test_query_unicode_and_size_are_validated_even_without_labels():
    with Document(limits=PdfLoadLimits(max_object_bytes=3)) as document:
        assert document.page_labels.find_pages("é") == []
        with pytest.raises(PdfResourceLimitException, match="page label query bytes"):
            document.page_labels.find_pages("éé")
        with pytest.raises(PdfValidationException, match="Unicode"):
            document.page_labels.find_pages("\ud800")


def test_match_count_and_comparison_work_obey_limits():
    with _document(3, limits=PdfLoadLimits(max_container_items=2)) as document:
        document.page_labels[0] = PageLabel(NumberingStyle.NONE, "Cover")
        with pytest.raises(PdfResourceLimitException, match="page label matches"):
            document.page_labels.find_pages("Cover")
        assert document.pages[0].label == "Cover"
    with _document(1, limits=PdfLoadLimits(max_codec_work_bytes=3)) as document:
        document.page_labels[0] = PageLabel(NumberingStyle.NONE, "Cover")
        with pytest.raises(
            PdfResourceLimitException, match="page label search comparisons"
        ):
            document.page_labels.find_pages("Cover")


@pytest.mark.parametrize(
    "style", [NumberingStyle.NUMERALS_ROMAN_UPPERCASE, NumberingStyle.LETTERS_UPPERCASE]
)
def test_large_start_numbers_do_not_expand_irrelevant_label_strings(style, monkeypatch):
    with _document(3) as document:
        document.page_labels[0] = PageLabel(style, start=10**30)
        from aspose_pdf.engine import page_labels

        def forbidden(*args):
            pytest.fail("A short nonmatching query must not expand a huge page label")

        monkeypatch.setattr(page_labels, "format_label", forbidden)
        assert document.page_labels.find_pages("I") == []
        assert document.page_labels.find_pages("A") == []
        assert document.page_labels.find_pages("") == []


def test_retained_collection_rejects_use_after_disposal():
    with _book() as document:
        labels = document.page_labels
    with pytest.raises(AsposePdfException, match="disposed"):
        labels.find_pages("i")
