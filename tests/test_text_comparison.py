"""Text comparison preserves source data and reports normalized page differences."""

from __future__ import annotations

import io
from dataclasses import FrozenInstanceError
from itertools import repeat

import pytest

import aspose_pdf
from aspose_pdf import (
    Document,
    PageTextComparison,
    PdfLoadLimits,
    PdfResourceLimitException,
    RasterizedPage,
)
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfParseException,
    PdfValidationException,
)


def _page(document, text=""):
    page = document.pages.add(size=(120, 100))
    for line, value in enumerate(text.split("\n")):
        if value:
            page.add_text(value, 5, 85 - line * 12, font_size=10)
    return page


def _bytes(document):
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _extracted(monkeypatch, document, texts):
    for _ in texts:
        _page(document)
    monkeypatch.setattr(
        document._engine_pdf, "extract_page_text", lambda index, **kw: texts[index]
    )


def test_public_report_is_immutable_detached_and_ignores_drawing_changes():
    with Document() as left, Document() as right:
        _page(left, "Invoice 123")
        page = _page(right, "Invoice 123")
        page.draw_rectangle(0, 0, 20, 20, fill_color="#f00", stroke_color=None)
        right.info["Title"] = "Other metadata"
        (result,) = left.compare_text(right)
        assert isinstance(result, PageTextComparison)
        assert aspose_pdf.__all__.count("PageTextComparison") == 1
        assert result.page_index == result.other_page_index == 0
        assert result.text == result.other_text == "Invoice 123"
        assert result.matches and result.unified_diff == ""
        with pytest.raises(FrozenInstanceError):
            result.matches = False
    assert result.text == "Invoice 123" and result.matches
    assert not hasattr(result, "__dict__")


def test_changed_lines_produce_a_readable_unified_diff():
    with Document() as left, Document() as right:
        _page(left, "Invoice\nAmount: 10\nEnd")
        _page(right, "Invoice\nAmount: 20\nEnd")
        (result,) = left.compare_text(right)
        assert not result.matches
        assert result.text == "Invoice\nAmount: 10\nEnd"
        assert result.other_text == "Invoice\nAmount: 20\nEnd"
        assert result.unified_diff == (
            "--- page-1\n+++ other-page-1\n@@ -1,3 +1,3 @@\n"
            " Invoice\n-Amount: 10\n+Amount: 20\n End\n"
            "\\ No newline at end of text\n"
        )


@pytest.mark.parametrize(
    "before,after,removed,added",
    [
        ("Start\nEnd\n", "Start\nNew\nEnd\n", [], ["New"]),
        ("Start\nOld\nEnd\n", "Start\nEnd\n", ["Old"], []),
        ("", "New", [], ["New"]),
        ("Old", "", ["Old"], []),
        ("value\n", "value", ["value"], ["value"]),
    ],
)
def test_diff_insertions_deletions_and_final_lf(
    monkeypatch, before, after, removed, added
):
    with Document() as left, Document() as right:
        _extracted(monkeypatch, left, [before])
        _extracted(monkeypatch, right, [after])
        (result,) = left.compare_text(right)
        body = result.unified_diff.splitlines()[2:]
        assert [line[1:] for line in body if line.startswith("-")] == removed
        assert [line[1:] for line in body if line.startswith("+")] == added
        assert not result.matches


def test_distant_changes_keep_three_lines_of_context(monkeypatch):
    with Document() as left, Document() as right:
        lines = [f"Line {index}\n" for index in range(30)]
        revised = list(lines)
        revised[3], revised[25] = "First change\n", "Last change\n"
        _extracted(monkeypatch, left, ["".join(lines)])
        _extracted(monkeypatch, right, ["".join(revised)])
        (result,) = left.compare_text(right)
        assert "@@ -1,7 +1,7 @@" in result.unified_diff
        assert "@@ -23,7 +23,7 @@" in result.unified_diff
        assert "Line 15" not in result.unified_diff


def test_image_only_pages_require_a_text_layer():
    with Document() as left, Document() as right:
        for document, rgb in ((left, b"\xff\xff\xff"), (right, b"\x00\x00\x00")):
            page = _page(document)
            page.add_image(
                RasterizedPage(1, 1, rgb).to_png(), 0, 0, width=120, height=100
            )
        (result,) = left.compare_text(right)
        assert result.matches and result.text == result.other_text == ""


@pytest.mark.parametrize(
    "before,after,options,matches",
    [
        ("Same", "Same", {}, True),
        ("Same", "same", {}, False),
        ("Same", "same", {"case_sensitive": False}, True),
        ("Straße Σς", "STRASSE σσ", {"case_sensitive": False}, True),
        (" a\t b\n", "a b", {}, False),
        (" a\t b\n", "a b", {"ignore_whitespace": True}, True),
        ("a\u00a0\u2003b", "a b", {"ignore_whitespace": True}, True),
        ("a b", "ab", {"ignore_whitespace": True}, False),
        ("\n\t ", "", {"ignore_whitespace": True}, True),
        (" A\nB ", "a b", {"case_sensitive": False, "ignore_whitespace": True}, True),
        ("é", "e\u0301", {}, False),
        ("a\r\nb", "a\nb", {}, False),
        ("a\nb", "a\nb\n", {}, False),
        ("a\v b", "a\n b", {}, False),
        ("", "\n", {}, False),
    ],
)
def test_exact_unicode_case_and_whitespace_rules_keep_original_strings(
    monkeypatch, before, after, options, matches
):
    with Document() as left, Document() as right:
        _extracted(monkeypatch, left, [before])
        _extracted(monkeypatch, right, [after])
        (result,) = left.compare_text(right, **options)
        assert result.matches is matches
        assert (result.text, result.other_text) == (before, after)
        assert bool(result.unified_diff) is not matches


def test_diff_uses_the_same_normalization_as_the_match_flag(monkeypatch):
    with Document() as left, Document() as right:
        _extracted(monkeypatch, left, [" A\n  OLD "])
        _extracted(monkeypatch, right, ["a  NEW\t"])
        (result,) = left.compare_text(
            right, case_sensitive=False, ignore_whitespace=True
        )
        assert "-a old\n" in result.unified_diff
        assert "+a new\n" in result.unified_diff
        assert result.unified_diff.count("No newline at end of text") == 2


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("text", ["", "Extra"])
def test_missing_pages_are_distinct_from_empty_text(reverse, text):
    with Document() as left, Document() as right:
        _page(left, text)
        if reverse:
            left, right = right, left
        (result,) = left.compare_text(right)
        assert not result.matches
        assert (result.page_index, result.other_page_index) == (
            (None, 0) if reverse else (0, None)
        )
        assert (result.text, result.other_text) == (
            (None, text) if reverse else (text, None)
        )
        assert "missing\n" in result.unified_diff
        if not text:
            assert result.unified_diff.count("\n") == 2


def test_selections_keep_order_repeats_generators_and_missing_tail():
    with Document() as left, Document() as right:
        for text in ("A", "B", "C"):
            _page(left, text)
            _page(right, text)
        results = left.compare_text(
            right,
            pages=(index for index in (2, 0, 2)),
            other_pages=(index for index in (2, 1)),
        )
        assert [(r.page_index, r.other_page_index) for r in results] == [
            (2, 2),
            (0, 1),
            (2, None),
        ]
        assert [r.matches for r in results] == [True, False, False]
        assert results[2].text == "C"
        assert "--- page-1\n+++ other-page-2\n" in results[1].unified_diff
        assert len(left.compare_text(right, pages=[])) == 3
        assert left.compare_text(right, pages=[], other_pages=[]) == []
        assert all(r.matches for r in left.compare_text(left))


def test_empty_documents_and_blank_pages():
    with Document() as left, Document() as right:
        assert left.compare_text(right) == []
        _page(left)
        _page(right)
        (result,) = left.compare_text(right)
        assert result.matches and result.text == result.other_text == ""
        assert result.unified_diff == ""


@pytest.mark.parametrize("selector", ["pages", "other_pages"])
@pytest.mark.parametrize("value", [1, "0", b"0", [True], [0.0], [-1], [2], [0, 2]])
def test_invalid_full_selectors_fail_before_extraction(monkeypatch, selector, value):
    with Document() as left, Document() as right:
        _page(left)
        _page(right)
        for document in (left, right):
            monkeypatch.setattr(
                document._engine_pdf,
                "extract_page_text",
                lambda *a, **kw: pytest.fail("validate first"),
            )
        with pytest.raises(PdfValidationException):
            left.compare_text(right, **{selector: value})


@pytest.mark.parametrize(
    "name", ["case_sensitive", "ignore_whitespace", "skip_artifacts"]
)
@pytest.mark.parametrize("value", [0, 1, None, "false"])
@pytest.mark.parametrize("empty", [False, True])
def test_flags_require_booleans_even_with_empty_selections(
    monkeypatch, name, value, empty
):
    with Document() as left, Document() as right:
        if not empty:
            _page(left)
            _page(right)
        for document in (left, right):
            monkeypatch.setattr(
                document._engine_pdf,
                "extract_page_text",
                lambda *a, **kw: pytest.fail("validate first"),
            )
        with pytest.raises(PdfValidationException, match=name):
            left.compare_text(right, **{name: value})


@pytest.mark.parametrize("other", [None, "other.pdf", b"%PDF-1.7", 0])
def test_other_must_be_an_open_document(other):
    with Document() as document:
        with pytest.raises(PdfValidationException, match="Document"):
            document.compare_text(other)


@pytest.mark.parametrize("side", [0, 1])
def test_disposed_documents_are_rejected(side):
    with Document() as left, Document() as right:
        (left, right)[side].dispose()
        with pytest.raises(AsposePdfException, match="disposed"):
            left.compare_text(right)


def test_artifacts_hidden_ocr_layers_and_non_page_text_follow_extraction():
    with Document() as left, Document() as right:
        _page(left, "Body")
        _page(right, "Body")
        left._engine_pdf._set_page_content(
            0,
            left.pages[0].content
            + b" /Artifact BMC BT /F1 10 Tf 5 60 Td (Header) Tj ET EMC",
        )
        assert not left.compare_text(right)[0].matches
        assert left.compare_text(right, skip_artifacts=True)[0].matches
        right._engine_pdf._set_page_content(
            0, right.pages[0].content + b" BT /F1 10 Tf 3 Tr 5 45 Td (OCR) Tj ET"
        )
        assert not left.compare_text(right, skip_artifacts=True)[0].matches
        right._engine_pdf._set_page_content(0, left.pages[0].content)
        layer = right.layers.add("Hidden", visible=False)
        with right.pages[0].layer(layer):
            right.pages[0].add_text("HIDDEN", 5, 30)
        right.form.add_text_field("Value", 0, (5, 5, 40, 20), value="Form")
        right.pages[0].annotations.add("Text", (70, 5, 90, 20), "Comment")
        assert left.compare_text(right)[0].matches
        layer.visible = True
        assert not left.compare_text(right)[0].matches


@pytest.mark.parametrize("kind", ["bytes", "file", "streaming"])
@pytest.mark.parametrize("encrypted", [False, True])
def test_saved_inputs_preserve_sources_and_reports_survive_disposal(
    tmp_path, kind, encrypted
):
    paths = [tmp_path / "left.pdf", tmp_path / "right.pdf"]
    for path, text in zip(paths, ("Original", "Revision")):
        with Document() as document:
            _page(document, "Shared")
            _page(document, text)
            document.info["Title"] = text
            if encrypted:
                document.encrypt("secret", "owner")
            document.save(path)
    original = [path.read_bytes() for path in paths]
    opener = Document.open_streaming if kind == "streaming" else Document
    arguments = original if kind == "bytes" else paths
    with (
        opener(arguments[0], password="secret" if encrypted else None) as left,
        opener(arguments[1], password="secret" if encrypted else None) as right,
    ):
        protection = [document.is_encrypted for document in (left, right)]
        before = (
            [_bytes(document) for document in (left, right)]
            if kind != "streaming" and not encrypted
            else None
        )
        results = left.compare_text(right, pages=[1, 0], other_pages=[1, 0])
        assert [result.matches for result in results] == [False, True]
        for document, protected in zip((left, right), protection):
            assert document.is_encrypted is protected
            if kind == "streaming":
                assert document._engine_pdf.page_contents == []
                assert document._engine_pdf._extracted_text is None
        if before:
            assert [_bytes(document) for document in (left, right)] == before
    assert results[0].text == "Original" and results[0].other_text == "Revision"
    assert "-Original\n" in results[0].unified_diff
    assert [path.read_bytes() for path in paths] == original


def test_only_selected_pages_are_extracted_and_cache_cursor_are_preserved(monkeypatch):
    with Document() as left, Document() as right:
        for document in (left, right):
            _page(document, "First")
            _page(document, "Second")
            document.extract_text()
            document._engine_pdf._page_text_cursor = 1
        caches = [document._engine_pdf._extracted_text for document in (left, right)]
        calls = []
        for side, document in enumerate((left, right)):
            original = document._engine_pdf.extract_page_text

            def extract(index, *, skip_artifacts=False, original=original, side=side):
                calls.append((side, index, skip_artifacts))
                return original(index, skip_artifacts=skip_artifacts)

            monkeypatch.setattr(document._engine_pdf, "extract_page_text", extract)
            monkeypatch.setattr(
                document,
                "extract_text",
                lambda *a, **kw: pytest.fail("no all-page extraction"),
            )
        assert (
            left.compare_text(right, pages=[1], other_pages=[0], skip_artifacts=True)[
                0
            ].text
            == "Second"
        )
        assert calls == [(0, 1, True), (1, 0, True)]
        for document, cache in zip((left, right), caches):
            assert document._engine_pdf._extracted_text is cache
            assert document._engine_pdf._page_text_cursor == 1


def test_subsequent_calls_observe_unsaved_edits_and_crop_does_not_clip():
    with Document() as left, Document() as right:
        _page(left, "Original")
        page = _page(right, "Original")
        page.crop_box = (60, 0, 120, 50)
        page.rotation = 90
        assert left.compare_text(right)[0].matches
        page.add_text("Added", 5, 50)
        assert not left.compare_text(right)[0].matches


@pytest.mark.parametrize("side", [0, 1])
@pytest.mark.parametrize("limit", ["max_pages", "max_container_items"])
def test_stricter_pair_count_limits_are_checked_before_reading(
    monkeypatch, side, limit
):
    limits = [PdfLoadLimits.unlimited(), PdfLoadLimits.unlimited()]
    limits[side] = PdfLoadLimits(**{limit: 1})
    with Document(limits=limits[0]) as left, Document(limits=limits[1]) as right:
        for document in (left, right):
            _page(document)
            _page(document)
            monkeypatch.setattr(
                document._engine_pdf,
                "extract_page_text",
                lambda *a, **kw: pytest.fail("limit first"),
            )
        with pytest.raises(PdfResourceLimitException, match=limit):
            left.compare_text(right)


def test_unbounded_selector_is_stopped_by_combined_limits():
    with (
        Document() as left,
        Document(limits=PdfLoadLimits(max_container_items=2)) as right,
    ):
        _page(left)
        _page(right)
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            left.compare_text(right, pages=repeat(0))


@pytest.mark.parametrize("side", [0, 1])
def test_extracted_utf8_size_uses_the_stricter_policy(monkeypatch, side):
    limits = [PdfLoadLimits.unlimited(), PdfLoadLimits.unlimited()]
    limits[side] = PdfLoadLimits(max_object_bytes=3)
    with Document(limits=limits[0]) as left, Document(limits=limits[1]) as right:
        _extracted(monkeypatch, left, ["éé"])
        _extracted(monkeypatch, right, ["éé"])
        with pytest.raises(PdfResourceLimitException, match="max_object_bytes"):
            left.compare_text(right)


def test_normalized_casefold_expansion_is_bounded(monkeypatch):
    with (
        Document(limits=PdfLoadLimits(max_object_bytes=2)) as left,
        Document() as right,
    ):
        _extracted(monkeypatch, left, ["ΐ"])
        _extracted(monkeypatch, right, ["ΐ"])
        with pytest.raises(PdfResourceLimitException, match="max_object_bytes"):
            left.compare_text(right, case_sensitive=False)


@pytest.mark.parametrize("different", [False, True])
def test_retained_text_and_diffs_share_a_cumulative_budget(monkeypatch, different):
    with (
        Document(limits=PdfLoadLimits(max_total_decoded_bytes=12)) as left,
        Document() as right,
    ):
        _extracted(monkeypatch, left, ["four", "four"])
        _extracted(monkeypatch, right, ["five" if different else "four", "four"])
        with pytest.raises(PdfResourceLimitException, match="max_total_decoded_bytes"):
            left.compare_text(right)


def test_diff_size_and_line_count_are_bounded(monkeypatch):
    for limits, before, after, name in (
        (PdfLoadLimits(max_object_bytes=30), "a", "b", "max_object_bytes"),
        (PdfLoadLimits(max_container_items=2), "a\nb", "a\nc", "max_container_items"),
    ):
        with Document(limits=limits) as left, Document() as right:
            _extracted(monkeypatch, left, [before])
            _extracted(monkeypatch, right, [after])
            with pytest.raises(PdfResourceLimitException, match=name):
                left.compare_text(right)


def test_unlimited_policy_and_exact_cumulative_boundary(monkeypatch):
    with (
        Document(limits=PdfLoadLimits(max_total_decoded_bytes=8)) as left,
        Document(limits=PdfLoadLimits.unlimited()) as right,
    ):
        _extracted(monkeypatch, left, ["éé"])
        _extracted(monkeypatch, right, ["éé"])
        assert left.compare_text(right)[0].matches


def test_invalid_unicode_is_rejected_clearly(monkeypatch):
    with Document() as left, Document() as right:
        _extracted(monkeypatch, left, ["\ud800"])
        _extracted(monkeypatch, right, ["valid"])
        with pytest.raises(PdfValidationException, match="Unicode"):
            left.compare_text(right)


def test_extraction_failure_propagates_without_modifying_sources(monkeypatch):
    with Document() as left, Document() as right:
        for document in (left, right):
            _page(document, "First")
            _page(document, "Last")
        before = [_bytes(document) for document in (left, right)]
        original = right._engine_pdf.extract_page_text

        def extract(index, **options):
            if index == 1:
                raise PdfParseException("Unreadable selected page")
            return original(index, **options)

        monkeypatch.setattr(right._engine_pdf, "extract_page_text", extract)
        with pytest.raises(PdfParseException, match="Unreadable"):
            left.compare_text(right)
        assert [_bytes(document) for document in (left, right)] == before


def test_streaming_content_limit_is_not_swallowed(tmp_path):
    path = tmp_path / "source.pdf"
    with Document() as source:
        _page(source, "Some content")
        source.save(path)
    with (
        Document.open_streaming(
            path, limits=PdfLoadLimits(max_content_stream_bytes=4)
        ) as left,
        Document(path) as right,
    ):
        with pytest.raises(PdfResourceLimitException, match="max_content_stream_bytes"):
            left.compare_text(right)


def test_streaming_unselected_oversized_content_is_not_decoded(tmp_path):
    path = tmp_path / "source.pdf"
    with Document() as source:
        _page(source, "Selected")
        _page(source, "x" * 200)
        limit = len(source.pages[0].content)
        source.save(path)
    with (
        Document.open_streaming(
            path, limits=PdfLoadLimits(max_content_stream_bytes=limit)
        ) as left,
        Document.open_streaming(path) as right,
    ):
        assert left.compare_text(right, pages=[0], other_pages=[0])[0].matches
        assert left._engine_pdf.page_contents == right._engine_pdf.page_contents == []
        with pytest.raises(PdfResourceLimitException, match="max_content_stream_bytes"):
            left.compare_text(right, pages=[1], other_pages=[1])
