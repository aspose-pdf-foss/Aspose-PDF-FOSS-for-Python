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
    TextAbsorber,
    TextFragment,
    TextFragmentAbsorber,
    TextFragmentCollection,
    TextSearchOptions,
    TextState,
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
        frag = TextFragment(page_index=1, text="hello", start=5)
        assert frag.end == 10

    def test_explicit_end(self):
        frag = TextFragment(page_index=2, text="hi", start=3, end=5)
        assert frag.end == 5

    def test_equality(self):
        a = TextFragment(1, "hello", 0)
        b = TextFragment(1, "hello", 0)
        assert a == b

    def test_repr_contains_text(self):
        frag = TextFragment(3, "world", 7)
        assert "world" in repr(frag)


class TestTextFragmentAsposeNetApi:
    """Test Aspose.PDF for Python via .NET API compatibility."""

    def test_text_only(self):
        """TextFragment(text) - Aspose.NET style with only text."""
        frag = TextFragment("Hello, world!")
        assert frag.text == "Hello, world!"
        assert frag.page_index == 0
        assert frag.start == 0
        assert frag.end == 13

    def test_text_only_with_explicit_end(self):
        """TextFragment(text, end) - Aspose.NET style with explicit end."""
        frag = TextFragment("Hello", 3)
        assert frag.text == "Hello"
        assert frag.end == 3

    def test_text_only_with_rect(self):
        """TextFragment(text, rect) - Aspose.NET style with rect."""
        rect = (10.0, 20.0, 30.0, 40.0)
        frag = TextFragment("Hello", rect=rect)
        assert frag.text == "Hello"
        assert frag.rect == rect

    def test_text_only_with_all_params(self):
        """TextFragment(text, ...) - Aspose.NET style with all params."""
        quads = (((10.0, 20.0), (30.0, 20.0), (30.0, 40.0), (10.0, 40.0)),)
        frag = TextFragment(
            "Hello",
            5,
            rect=(10.0, 20.0, 30.0, 40.0),
            quads=list(quads),
            font_name="Arial",
            font_size=12.0,
            color=(1.0, 0.0, 0.0),
        )
        assert frag.text == "Hello"
        assert frag.end == 5
        assert frag.rect == (10.0, 20.0, 30.0, 40.0)
        assert frag.quads == list(quads)
        assert frag.font_name == "Arial"
        assert frag.font_size == 12.0
        assert frag.color == (1.0, 0.0, 0.0)

    def test_no_args(self):
        """TextFragment() - Aspose.NET style with no args."""
        frag = TextFragment()
        assert frag.text == ""
        assert frag.page_index == 0
        assert frag.start == 0
        assert frag.end == 0


# ---------------------------------------------------------------------------
# TextState
# ---------------------------------------------------------------------------


class TestTextState:
    def test_default_values(self):
        state = TextState()
        assert state.superscript is False
        assert state.subscript is False
        assert state.font_name is None
        assert state.font_size is None
        assert state.color is None
        assert state.rise == 0.0

    def test_with_custom_values(self):
        state = TextState(
            superscript=True,
            subscript=False,
            font_name="Arial",
            font_size=12.0,
            color=(1.0, 0.0, 0.0),
            rise=2.0,
        )
        assert state.superscript is True
        assert state.subscript is False
        assert state.font_name == "Arial"
        assert state.font_size == 12.0
        assert state.color == (1.0, 0.0, 0.0)
        assert state.rise == 2.0

    def test_equality(self):
        state1 = TextState(font_name="Arial", font_size=12.0)
        state2 = TextState(font_name="Arial", font_size=12.0)
        state3 = TextState(font_name="Arial", font_size=10.0)
        assert state1 == state2
        assert state1 != state3

    def test_repr(self):
        state = TextState(font_name="Arial", font_size=12.0)
        assert "TextState" in repr(state)
        assert "Arial" in repr(state)
        assert "12.0" in repr(state)


# ---------------------------------------------------------------------------
# TextState property on TextFragment
# ---------------------------------------------------------------------------


class TestTextFragmentTextState:
    def test_text_state_default(self):
        frag = TextFragment(page_index=1, text="hello", start=0)
        state = frag.text_state
        assert isinstance(state, TextState)
        assert state.superscript is False
        assert state.subscript is False

    def test_text_state_from_font_name(self):
        frag = TextFragment(page_index=1, text="hello", start=0, font_name="Arial")
        state = frag.text_state
        assert state.font_name == "Arial"

    def test_text_state_from_font_size(self):
        frag = TextFragment(page_index=1, text="hello", start=0, font_size=12.0)
        state = frag.text_state
        assert state.font_size == 12.0

    def test_text_state_from_color(self):
        color = (1.0, 0.0, 0.0)
        frag = TextFragment(page_index=1, text="hello", start=0, color=color)
        state = frag.text_state
        assert state.color == color

    def test_text_state_superscript(self):
        frag = TextFragment(page_index=1, text="hello", start=0)
        state = frag.text_state
        # Mutating the returned TextState creates a new instance, so we need to
        # assign a new TextState to the fragment to update it
        state.superscript = True
        frag.text_state = state
        assert frag.text_state.superscript is True

    def test_text_state_subscript(self):
        frag = TextFragment(page_index=1, text="hello", start=0)
        state = frag.text_state
        state.subscript = True
        frag.text_state = state
        assert frag.text_state.subscript is True

    def test_text_state_syncs_with_font_attributes(self):
        """Test that text_state always reflects current font attributes."""
        frag = TextFragment(page_index=1, text="hello", start=0, font_name="Arial", font_size=12.0)
        state1 = frag.text_state
        assert state1.font_name == "Arial"
        assert state1.font_size == 12.0

        # Mutate the fragment directly
        frag.font_name = "Times"
        frag.font_size = 14.0

        # text_state should reflect the new values
        state2 = frag.text_state
        assert state2.font_name == "Times"
        assert state2.font_size == 14.0

    def test_text_state_setter(self):
        state = TextState(superscript=True, font_name="Arial", font_size=14.0)
        frag = TextFragment(page_index=1, text="hello", start=0)
        frag.text_state = state
        # The text_state property creates a new instance, so we check the
        # fragment's attributes are synced from the setter
        assert frag.font_name == "Arial"
        assert frag.font_size == 14.0

    def test_text_state_rise_syncs(self):
        """Test that rise attribute is properly synced with text_state setter."""
        state = TextState(rise=3.0, superscript=True, font_name="Arial", font_size=12.0)
        frag = TextFragment(page_index=1, text="hello", start=0)
        frag.text_state = state
        # Check that rise is stored and returned via text_state
        assert frag.text_state.rise == 3.0


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
        assert absorber.text_fragments[0].page_index == 1

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
        assert absorber.text_fragments[0].page_index == 1

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
        assert rr.page_index == 1
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
        assert absorber.regex_results[0].page_index == 1


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
        assert page_indices == {1, 2}

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
        assert 1 in page_indices
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

    def test_remove_all_text_no_args_keeps_errors(self):
        """remove_all_text() is like reset but without clearing errors."""
        absorber = TextFragmentAbsorber()
        absorber._errors.append("some error")
        absorber.remove_all_text()
        # remove_all_text() clears fragments but keeps errors
        assert absorber.errors == ["some error"]

    def test_remove_all_text_invalid_type_raises(self):
        """Invalid page_or_doc raises TypeError."""
        absorber = TextFragmentAbsorber()
        with pytest.raises(TypeError, match="page_or_doc must be a Page, Document, or None"):
            absorber.remove_all_text("not a page or document")


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
        frag = TextFragment(1, "hello", 0)
        col.add(frag)
        assert len(col) == 1

    def test_add_none_ignored(self):
        col = TextFragmentCollection()
        col.add(None)
        assert len(col) == 0

    def test_remove_existing(self):
        col = TextFragmentCollection()
        frag = TextFragment(1, "test", 0)
        col.add(frag)
        assert col.remove(frag) is True
        assert len(col) == 0

    def test_remove_nonexistent_returns_false(self):
        col = TextFragmentCollection()
        frag = TextFragment(1, "nope", 0)
        assert col.remove(frag) is False

    def test_contains(self):
        col = TextFragmentCollection()
        frag = TextFragment(1, "x", 0)
        col.add(frag)
        assert col.contains(frag) is True

    def test_item_by_index(self):
        col = TextFragmentCollection()
        frag = TextFragment(1, "item", 0)
        col.add(frag)
        assert col.item(0) == frag

    def test_item_index_error(self):
        col = TextFragmentCollection()
        with pytest.raises(IndexError):
            col.item(0)

    def test_iteration(self):
        col = TextFragmentCollection()
        for i in range(3):
            col.add(TextFragment(1, str(i), i))
        texts = [f.text for f in col]
        assert texts == ["0", "1", "2"]

    def test_clear(self):
        col = TextFragmentCollection()
        col.add(TextFragment(1, "a", 0))
        col.clear()
        assert len(col) == 0

    def test_getitem(self):
        col = TextFragmentCollection()
        frag = TextFragment(1, "z", 0)
        col.add(frag)
        assert col[0] == frag


# ---------------------------------------------------------------------------
# TextAbsorber remove_all_text()
# ---------------------------------------------------------------------------


class TestTextAbsorberRemoveAllText:
    """Test TextAbsorber.remove_all_text() compatibility."""

    def test_remove_all_text_no_args_clears_fragments(self):
        page = FakePage("data")
        absorber = TextAbsorber()
        absorber.visit(page)
        assert len(absorber.text_fragments) > 0
        absorber.remove_all_text()
        assert absorber.text_fragments == []


# ---------------------------------------------------------------------------
# TextFragmentAbsorber remove_all_text edge cases
# ---------------------------------------------------------------------------


class TestTextFragmentAbsorberRemoveAllTextEdgeCases:
    """Test edge cases for remove_all_text overloads."""

    def test_remove_all_text_invalid_type_raises(self):
        """Invalid page_or_doc raises TypeError."""
        absorber = TextFragmentAbsorber()
        with pytest.raises(TypeError, match="page_or_doc must be a Page, Document, or None"):
            absorber.remove_all_text("not a page or document")
