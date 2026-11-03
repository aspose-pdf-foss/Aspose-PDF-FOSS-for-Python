"""Regression cases for silent merge and regex example failures."""

import io
import re

import pytest

from aspose_pdf import Document, FitDestination, OutlineItem
from aspose_pdf.exceptions import AsposePdfException, PdfValidationException
from aspose_pdf.text import TextFragmentAbsorber, TextSearchOptions


def _document(*texts, font_name="Helvetica"):
    document = Document()
    for text in texts:
        document.pages.add().add_text(text, 20, 700, font_name=font_name)
    return document


def _texts(document):
    return [page.extract_text().strip() for page in document.pages]


def _round_trip(document):
    output = io.BytesIO()
    document.save(output)
    return Document(output.getvalue())


@pytest.mark.parametrize("count", [0, 1, 3])
def test_add_collection_imports_all_pages_without_blank_substitutes(count):
    expected = [f"SOURCE-{number}" for number in range(count)]
    with _document("TARGET") as target, _document(*expected) as source:
        assert target.pages.add(source.pages) is None
        assert _texts(source) == expected
        source.pages.clear()
        with _round_trip(target) as saved:
            assert _texts(saved) == ["TARGET", *expected]


def test_add_collection_copies_its_own_original_pages_once():
    with _document("FIRST", "SECOND") as document:
        document.pages.add(document.pages)
        with _round_trip(document) as saved:
            assert _texts(saved) == ["FIRST", "SECOND", "FIRST", "SECOND"]


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("encrypted", [False, True])
def test_add_collection_keeps_resources_forms_links_and_bookmarks(
    tmp_path, streaming, encrypted
):
    path = tmp_path / "source.pdf"
    with _document("SOURCE-1", "SOURCE-2", font_name="Courier-Bold") as source:
        source.pages[1].add_link((20, 650, 80, 675), FitDestination(2))
        source.form.add_text_field("answer", 2, (20, 600, 120, 630), value="YES")
        source.outlines.add(OutlineItem("Second", 2))
        if encrypted:
            source.encrypt("secret")
        source.save(path)
    opener = Document.open_streaming if streaming else Document
    with opener(path, password="secret" if encrypted else None) as source:
        with _document("TARGET", font_name="Helvetica") as target:
            target.pages.add(source.pages)
            with _round_trip(target) as saved:
                assert _texts(saved) == ["TARGET", "SOURCE-1", "SOURCE-2"]
                assert saved.outlines[0].page_index == 3
                assert saved.form["answer"].page_index == 3
                assert saved.form["answer"].value == "YES"
                assert saved.extract_links()[0].destination.page == 3
                assert any(
                    "Courier-Bold" in font.base_font for font in saved.fonts
                )


def test_add_disposed_collection_fails_before_changing_target():
    with _document("TARGET") as target:
        source = _document("SOURCE")
        source.dispose()
        with pytest.raises(AsposePdfException):
            target.pages.add(source.pages)
        assert _texts(target) == ["TARGET"]


def test_regex_options_assigned_after_construction_find_and_replace_pdf_text():
    with _document("before 1234-5678 after") as document:
        absorber = TextFragmentAbsorber(r"\d{4}-\d{4}")
        absorber.text_search_options = TextSearchOptions(True)
        document.pages[1].accept(absorber)
        assert [fragment.text for fragment in absorber.text_fragments] == ["1234-5678"]
        for fragment in absorber.text_fragments:
            fragment.text = "ABC1-2XZY"
        with _round_trip(document) as saved:
            assert _texts(saved) == ["before ABC1-2XZY after"]


def test_mutable_regex_options_and_phrase_are_refreshed_between_visits():
    with _document("Code-12 code-34 literal+") as document:
        options = TextSearchOptions(True)
        absorber = TextFragmentAbsorber(r"code-\d+", text_search_options=options)
        absorber.visit(document)
        assert [fragment.text for fragment in absorber.text_fragments] == ["code-34"]
        absorber.reset()
        options.case_sensitive = False
        absorber.visit(document)
        assert len(absorber.text_fragments) == 2
        absorber.reset()
        options.is_regular_expression = False
        absorber.phrase = "literal+"
        absorber.visit(document)
        assert [fragment.text for fragment in absorber.text_fragments] == ["literal+"]


def test_invalid_updated_regex_is_rejected_before_collecting_more_fragments():
    with _document("MATCH") as document:
        absorber = TextFragmentAbsorber("MATCH")
        absorber.visit(document)
        absorber.phrase = "["
        absorber.text_search_options.is_regular_expression = True
        with pytest.raises(re.error):
            absorber.visit(document)
        assert [fragment.text for fragment in absorber.text_fragments] == ["MATCH"]


def test_fragment_edits_target_individual_repeated_occurrences_in_any_order():
    with _document("cat cat cat") as document:
        absorber = TextFragmentAbsorber("cat")
        absorber.visit(document)
        first, middle, last = absorber.text_fragments
        last.text = "THIRD"
        first.text = "FIRST"
        last.text = "LAST"
        middle.text = ""
        with _round_trip(document) as saved:
            assert _texts(saved) == ["FIRST  LAST"]


def test_regex_context_does_not_replace_an_identical_unselected_occurrence():
    with _document("ID:1234-5678 ignore:1234-5678") as document:
        absorber = TextFragmentAbsorber(
            r"(?<=ID:)\d{4}-\d{4}", text_search_options=TextSearchOptions(True)
        )
        absorber.visit(document)
        [fragment] = absorber.text_fragments
        fragment.text = r"literal\1"
        with _round_trip(document) as saved:
            assert _texts(saved) == [r"ID:literal\1 ignore:1234-5678"]


def test_stale_fragment_and_unencodable_replacement_do_not_change_content():
    with _document("old") as document:
        page = document.pages[1]
        absorber = TextFragmentAbsorber("old")
        absorber.visit(page)
        [fragment] = absorber.text_fragments
        original = page.content
        with pytest.raises(PdfValidationException):
            fragment.text = "snowman \u2603"
        assert page.content == original
        assert fragment.text == "old"
        page.replace_text("old", "new")
        with pytest.raises(PdfValidationException, match="collect fragments again"):
            fragment.text = "stale"
        assert page.extract_text() == "new"


@pytest.mark.parametrize("encrypted", [False, True])
def test_regex_fragment_replacement_on_streaming_documents(tmp_path, encrypted):
    path = tmp_path / "input.pdf"
    with _document("1234-5678", "UNTOUCHED") as document:
        if encrypted:
            document.encrypt("secret")
        document.save(path)
    with Document.open_streaming(
        path, password="secret" if encrypted else None
    ) as document:
        absorber = TextFragmentAbsorber(r"\d{4}-\d{4}")
        absorber.text_search_options = TextSearchOptions(True)
        absorber.visit(document.pages[1])
        [fragment] = absorber.text_fragments
        fragment.text = "ABC1-2XZY"
        output = io.BytesIO()
        document.save(output)
    with Document(output.getvalue(), password="secret" if encrypted else None) as saved:
        assert _texts(saved) == ["ABC1-2XZY", "UNTOUCHED"]


def test_fragment_replacement_spans_kerned_tj_elements():
    with _document("seed") as document:
        document._engine_pdf._set_page_content(
            0, b"BT /F1 12 Tf 20 700 Td [(1234) 0 (-5678)] TJ ET"
        )
        absorber = TextFragmentAbsorber(r"\d{4}-\d{4}")
        absorber.text_search_options = TextSearchOptions(True)
        absorber.visit(document)
        [fragment] = absorber.text_fragments
        fragment.text = "ABC1-2XZY"
        with _round_trip(document) as saved:
            assert _texts(saved) == ["ABC1-2XZY"]


def test_page_deletion_does_not_retarget_a_fragment_to_an_identical_page():
    with _document("same", "same") as document:
        absorber = TextFragmentAbsorber("same")
        absorber.visit(document.pages[1])
        [fragment] = absorber.text_fragments
        document.pages.delete(1)
        with pytest.raises(PdfValidationException, match="collect fragments again"):
            fragment.text = "wrong page"
        assert _texts(document) == ["same"]
