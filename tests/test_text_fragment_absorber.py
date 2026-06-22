"""Tests for TextFragmentAbsorber phrase and regex search (feature 3).

Covers:
- No-filter mode: all non-empty lines collected with correct positions.
- Phrase search: exact match, multiple occurrences, overlapping.
- Case-insensitive phrase search via TextSearchOptions.
- Regex search: basic pattern, groups, multiple matches.
- Case-insensitive regex search.
- regex_results populated correctly.
- reset() clears everything.
- remove_all_text() clears fragments & regex_results.
- apply_for_all_fragments() applies callable.
- visit() with page-like object (has extract_text returning str).
- visit() with document-like object (has iterable pages).
- TextSearchOptions defaults.
- RegexResult attributes.
- TextFragment position correctness.
"""

import re
import pytest

from aspose_pdf.text import (
    TextFragment,
    TextFragmentAbsorber,
    TextFragmentCollection,
    TextSearchOptions,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakePage:
    """Minimal page stub with extract_text() returning a string."""

    def __init__(self, text: str) -> None:
        self._text = text

    def extract_text(self) -> str:
        return self._text


class FakeDocument:
    """Minimal document stub with iterable pages."""

    def __init__(self, *page_texts: str) -> None:
        self.pages = [FakePage(t) for t in page_texts]


# ---------------------------------------------------------------------------
# TextFragment
# ---------------------------------------------------------------------------


class TestTextFragment:
    def test_end_defaults_to_start_plus_len(self):
        frag = TextFragment(page_index=0, text="hello", start=5)
        assert frag.end == 10

    def test_explicit_end(self):
        frag = TextFragment(page_index=1, text="hi", start=3, end=5)
        assert frag.end == 5

    def test_equality(self):
        a = TextFragment(0, "hello", 0)
        b = TextFragment(0, "hello", 0)
        assert a == b

    def test_repr_contains_text(self):
        frag = TextFragment(2, "world", 7)
        assert "world" in repr(frag)


# ---------------------------------------------------------------------------
# TextSearchOptions
# ---------------------------------------------------------------------------


class TestTextSearchOptions:
    def test_defaults(self):
        opts = TextSearchOptions()
        assert opts.is_regular_expression is False
        assert opts.case_sensitive is True

    def test_regex_flag(self):
        opts = TextSearchOptions(is_regular_expression=True)
        assert opts.is_regular_expression is True

    def test_case_insensitive(self):
        opts = TextSearchOptions(case_sensitive=False)
        assert opts.case_sensitive is False


# ---------------------------------------------------------------------------
# No-filter mode
# ---------------------------------------------------------------------------


class TestNoFilter:
    def test_collects_all_lines(self):
        page = FakePage("Hello\nWorld")
        absorber = TextFragmentAbsorber()
        absorber.visit(page)
        texts = [f.text for f in absorber.text_fragments]
        assert "Hello" in texts
        assert "World" in texts

    def test_empty_lines_excluded(self):
        page = FakePage("Line1\n\n\nLine2")
        absorber = TextFragmentAbsorber()
        absorber.visit(page)
        assert all(f.text.strip() for f in absorber.text_fragments)

    def test_single_line(self):
        page = FakePage("OneLiner")
        absorber = TextFragmentAbsorber()
        absorber.visit(page)
        assert len(absorber.text_fragments) == 1
        assert absorber.text_fragments[0].text == "OneLiner"

    def test_page_index_is_zero_for_single_page(self):
        page = FakePage("text")
        absorber = TextFragmentAbsorber()
        absorber.visit(page)
        assert absorber.text_fragments[0].page_index == 0

    def test_positions_are_within_text(self):
        full_text = "Alpha\nBeta\nGamma"
        page = FakePage(full_text)
        absorber = TextFragmentAbsorber()
        absorber.visit(page)
        for frag in absorber.text_fragments:
            assert full_text[frag.start : frag.end] == frag.text

    def test_no_regex_results_in_no_filter_mode(self):
        page = FakePage("some text")
        absorber = TextFragmentAbsorber()
        absorber.visit(page)
        assert absorber.regex_results == []


# ---------------------------------------------------------------------------
# Phrase search
# ---------------------------------------------------------------------------


class TestPhraseSearch:
    def test_finds_phrase(self):
        page = FakePage("The quick brown fox")
        absorber = TextFragmentAbsorber("quick")
        absorber.visit(page)
        assert len(absorber.text_fragments) == 1
        assert absorber.text_fragments[0].text == "quick"

    def test_phrase_not_present(self):
        page = FakePage("nothing here")
        absorber = TextFragmentAbsorber("missing")
        absorber.visit(page)
        assert absorber.text_fragments == []

    def test_multiple_occurrences(self):
        page = FakePage("cat and cat and cat")
        absorber = TextFragmentAbsorber("cat")
        absorber.visit(page)
        assert len(absorber.text_fragments) == 3

    def test_correct_start_offset(self):
        text = "Hello World"
        page = FakePage(text)
        absorber = TextFragmentAbsorber("World")
        absorber.visit(page)
        assert absorber.text_fragments[0].start == text.index("World")

    def test_correct_end_offset(self):
        text = "Hello World"
        page = FakePage(text)
        absorber = TextFragmentAbsorber("World")
        absorber.visit(page)
        frag = absorber.text_fragments[0]
        assert frag.end == frag.start + len("World")

    def test_case_sensitive_default(self):
        page = FakePage("Hello hello HELLO")
        absorber = TextFragmentAbsorber("hello")
        absorber.visit(page)
        assert len(absorber.text_fragments) == 1
        assert absorber.text_fragments[0].text == "hello"

    def test_case_insensitive(self):
        page = FakePage("Hello hello HELLO")
        opts = TextSearchOptions(case_sensitive=False)
        absorber = TextFragmentAbsorber("hello", text_search_options=opts)
        absorber.visit(page)
        assert len(absorber.text_fragments) == 3

    def test_case_insensitive_preserves_original_case(self):
        page = FakePage("Hello HELLO hello")
        opts = TextSearchOptions(case_sensitive=False)
        absorber = TextFragmentAbsorber("hello", text_search_options=opts)
        absorber.visit(page)
        texts = [f.text for f in absorber.text_fragments]
        assert "Hello" in texts
        assert "HELLO" in texts
        assert "hello" in texts

    def test_overlapping_occurrences(self):
        # "aa" in "aaaa" → offsets 0,1,2
        page = FakePage("aaaa")
        absorber = TextFragmentAbsorber("aa")
        absorber.visit(page)
        assert len(absorber.text_fragments) == 3

    def test_page_index_correct(self):
        page = FakePage("find me here")
        absorber = TextFragmentAbsorber("me")
        absorber.visit(page)
        assert absorber.text_fragments[0].page_index == 0

    def test_multiline_phrase(self):
        page = FakePage("first line\nsecond line\nthird line")
        absorber = TextFragmentAbsorber("line")
        absorber.visit(page)
        assert len(absorber.text_fragments) == 3

    def test_no_regex_results_in_phrase_mode(self):
        page = FakePage("test phrase search")
        absorber = TextFragmentAbsorber("phrase")
        absorber.visit(page)
        assert absorber.regex_results == []


# ---------------------------------------------------------------------------
# Regex search
# ---------------------------------------------------------------------------


class TestRegexSearch:
    def _make_absorber(
        self, pattern: str, case_sensitive: bool = True
    ) -> TextFragmentAbsorber:
        opts = TextSearchOptions(
            is_regular_expression=True,
            case_sensitive=case_sensitive,
        )
        return TextFragmentAbsorber(pattern, text_search_options=opts)

    def test_basic_pattern(self):
        page = FakePage("Call 123-456 or 789-012")
        absorber = self._make_absorber(r"\d{3}-\d{3}")
        absorber.visit(page)
        assert len(absorber.text_fragments) == 2

    def test_matched_text(self):
        page = FakePage("price: 42.50 USD")
        absorber = self._make_absorber(r"\d+\.\d+")
        absorber.visit(page)
        assert absorber.text_fragments[0].text == "42.50"

    def test_start_end_offsets(self):
        text = "foo bar baz"
        page = FakePage(text)
        absorber = self._make_absorber(r"bar")
        absorber.visit(page)
        frag = absorber.text_fragments[0]
        assert frag.start == text.index("bar")
        assert frag.end == frag.start + 3

    def test_regex_results_populated(self):
        page = FakePage("2024-01-15 and 2025-06-30")
        absorber = self._make_absorber(r"\d{4}-\d{2}-\d{2}")
        absorber.visit(page)
        assert len(absorber.regex_results) == 2

    def test_regex_results_and_text_fragments_aligned(self):
        page = FakePage("abc 123 def 456")
        absorber = self._make_absorber(r"\d+")
        absorber.visit(page)
        assert len(absorber.text_fragments) == len(absorber.regex_results)
        for frag, rr in zip(absorber.text_fragments, absorber.regex_results):
            assert frag.text == rr.text
            assert frag.start == rr.start
            assert frag.end == rr.end

    def test_regex_result_attributes(self):
        page = FakePage("hello world")
        absorber = self._make_absorber(r"(hel)(lo)")
        absorber.visit(page)
        rr = absorber.regex_results[0]
        assert rr.text == "hello"
        assert rr.groups == ("hel", "lo")
        assert rr.page_index == 0
        assert isinstance(rr.match, re.Match)

    def test_case_sensitive_regex_default(self):
        page = FakePage("Apple apple APPLE")
        absorber = self._make_absorber(r"apple")
        absorber.visit(page)
        assert len(absorber.text_fragments) == 1

    def test_case_insensitive_regex(self):
        page = FakePage("Apple apple APPLE")
        absorber = self._make_absorber(r"apple", case_sensitive=False)
        absorber.visit(page)
        assert len(absorber.text_fragments) == 3

    def test_no_match(self):
        page = FakePage("nothing matches")
        absorber = self._make_absorber(r"\d+")
        absorber.visit(page)
        assert absorber.text_fragments == []
        assert absorber.regex_results == []

    def test_page_index_in_regex_result(self):
        page = FakePage("match here")
        absorber = self._make_absorber(r"match")
        absorber.visit(page)
        assert absorber.regex_results[0].page_index == 0


# ---------------------------------------------------------------------------
# Multi-page document
# ---------------------------------------------------------------------------


class TestMultiPageDocument:
    def test_phrase_search_across_pages(self):
        doc = FakeDocument("find me on page one", "and find me on page two")
        absorber = TextFragmentAbsorber("find")
        absorber.visit(doc)
        assert len(absorber.text_fragments) == 2
        page_indices = {f.page_index for f in absorber.text_fragments}
        assert page_indices == {0, 1}

    def test_regex_search_across_pages(self):
        doc = FakeDocument("code: A1B2", "code: C3D4 and E5F6")
        opts = TextSearchOptions(is_regular_expression=True)
        absorber = TextFragmentAbsorber(r"[A-Z]\d[A-Z]\d", text_search_options=opts)
        absorber.visit(doc)
        assert len(absorber.text_fragments) == 3

    def test_no_filter_across_pages(self):
        doc = FakeDocument("line one\nline two", "line three")
        absorber = TextFragmentAbsorber()
        absorber.visit(doc)
        page_indices = [f.page_index for f in absorber.text_fragments]
        assert 0 in page_indices
        assert 1 in page_indices

    def test_page_indices_monotone(self):
        doc = FakeDocument("a", "b", "c")
        absorber = TextFragmentAbsorber()
        absorber.visit(doc)
        indices = [f.page_index for f in absorber.text_fragments]
        assert indices == sorted(indices)


# ---------------------------------------------------------------------------
# reset() and remove_all_text()
# ---------------------------------------------------------------------------


class TestReset:
    def test_reset_clears_fragments(self):
        page = FakePage("some text")
        absorber = TextFragmentAbsorber()
        absorber.visit(page)
        assert len(absorber.text_fragments) > 0
        absorber.reset()
        assert absorber.text_fragments == []

    def test_reset_clears_regex_results(self):
        opts = TextSearchOptions(is_regular_expression=True)
        absorber = TextFragmentAbsorber(r"\w+", text_search_options=opts)
        absorber.visit(FakePage("hello"))
        absorber.reset()
        assert absorber.regex_results == []

    def test_reset_clears_errors(self):
        absorber = TextFragmentAbsorber()
        absorber._errors.append("some error")
        absorber.reset()
        assert absorber.errors == []

    def test_remove_all_text_clears_fragments(self):
        page = FakePage("data")
        absorber = TextFragmentAbsorber()
        absorber.visit(page)
        absorber.remove_all_text()
        assert absorber.text_fragments == []

    def test_remove_all_text_clears_regex_results(self):
        opts = TextSearchOptions(is_regular_expression=True)
        absorber = TextFragmentAbsorber(r"\d+", text_search_options=opts)
        absorber.visit(FakePage("42"))
        absorber.remove_all_text()
        assert absorber.regex_results == []


# ---------------------------------------------------------------------------
# apply_for_all_fragments()
# ---------------------------------------------------------------------------


class TestApplyForAllFragments:
    def test_applies_callable_to_each_fragment(self):
        page = FakePage("alpha\nbeta\ngamma")
        absorber = TextFragmentAbsorber()
        absorber.visit(page)
        texts = absorber.apply_for_all_fragments(lambda f: f.text)
        assert set(texts) == {"alpha", "beta", "gamma"}

    def test_returns_list_of_results(self):
        page = FakePage("one\ntwo")
        absorber = TextFragmentAbsorber()
        absorber.visit(page)
        result = absorber.apply_for_all_fragments(lambda f: len(f.text))
        assert isinstance(result, list)
        assert all(isinstance(r, int) for r in result)

    def test_empty_fragments_returns_empty_list(self):
        absorber = TextFragmentAbsorber("notfound")
        absorber.visit(FakePage("nothing"))
        result = absorber.apply_for_all_fragments(lambda f: f)
        assert result == []


# ---------------------------------------------------------------------------
# TextFragmentCollection
# ---------------------------------------------------------------------------


class TestTextFragmentCollection:
    def test_add_and_len(self):
        col = TextFragmentCollection()
        frag = TextFragment(0, "hello", 0)
        col.add(frag)
        assert len(col) == 1

    def test_add_none_ignored(self):
        col = TextFragmentCollection()
        col.add(None)
        assert len(col) == 0

    def test_remove_existing(self):
        col = TextFragmentCollection()
        frag = TextFragment(0, "test", 0)
        col.add(frag)
        assert col.remove(frag) is True
        assert len(col) == 0

    def test_remove_nonexistent_returns_false(self):
        col = TextFragmentCollection()
        frag = TextFragment(0, "nope", 0)
        assert col.remove(frag) is False

    def test_contains(self):
        col = TextFragmentCollection()
        frag = TextFragment(0, "x", 0)
        col.add(frag)
        assert col.contains(frag) is True

    def test_item_by_index(self):
        col = TextFragmentCollection()
        frag = TextFragment(0, "item", 0)
        col.add(frag)
        assert col.item(0) == frag

    def test_item_index_error(self):
        col = TextFragmentCollection()
        with pytest.raises(IndexError):
            col.item(0)

    def test_iteration(self):
        col = TextFragmentCollection()
        for i in range(3):
            col.add(TextFragment(0, str(i), i))
        texts = [f.text for f in col]
        assert texts == ["0", "1", "2"]

    def test_clear(self):
        col = TextFragmentCollection()
        col.add(TextFragment(0, "a", 0))
        col.clear()
        assert len(col) == 0

    def test_getitem(self):
        col = TextFragmentCollection()
        frag = TextFragment(0, "z", 0)
        col.add(frag)
        assert col[0] == frag
