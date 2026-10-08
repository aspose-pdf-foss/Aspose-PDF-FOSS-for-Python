"""Regular expressions in ``replace_text`` and ``redact_text``.

Searching already understood patterns -- ``TextFragmentAbsorber`` with
``TextSearchOptions(is_regular_expression=True)`` -- while editing did not, so
the one thing redaction is most wanted for, removing a *shape* (an account
number, a date, a card) rather than a phrase, could not be asked for. These
tests cover the pattern reaching every layer that matches text, the replacement
template, and the literal path staying exactly as literal as it was.
"""

from __future__ import annotations

import io
import re

import pytest

from aspose_pdf import Document, PageSize
from aspose_pdf.engine.text_edit import (
    prepare_search,
    redact_text_in_content,
    replace_text_in_content,
)
from aspose_pdf.exceptions import PdfValidationException

SSN_PATTERN = r"\d{3}-\d{2}-\d{4}"
CARD_PATTERN = r"\b\d{4}( \d{4}){3}\b"


def _content(text: str) -> bytes:
    return f"BT /F1 12 Tf 1 0 0 1 10 700 Tm ({text}) Tj ET\n".encode("latin-1")


def _shown(content: bytes) -> str:
    """The first show string of *content*, unescaped as a reader would read it.

    Through the module's own lexer rather than a regex over the bytes: a PDF
    literal escapes a backslash, so the operand for ``C:\\new`` holds
    ``C:\\\\new`` and reading the raw bytes would compare the escaping instead
    of the text.
    """
    from aspose_pdf.engine.text_edit import _lex

    for token in _lex(content):
        if token.kind == "string":
            return token.value.decode("latin-1")
    return ""


def _replaced(text: str, search, replacement: str, **kwargs) -> tuple[str, int]:
    out, count = replace_text_in_content(
        _content(text), search, replacement, **kwargs
    )
    return _shown(out), count


def _document(*lines: str) -> Document:
    """A saved, reopened document with each of *lines* on its own baseline."""
    document = Document()
    page = document.pages.add(PageSize.A4)
    y = 700
    for line in lines:
        page.add_text(line, 50, y)
        y -= 40
    buffer = io.BytesIO()
    document.save(buffer)
    document.close()
    return Document(buffer.getvalue())


# ---------------------------------------------------------------------------
# prepare_search
# ---------------------------------------------------------------------------
class TestPrepareSearch:
    def test_a_literal_passes_through(self):
        assert prepare_search("plain") == "plain"

    def test_regex_true_compiles(self):
        pattern = prepare_search(SSN_PATTERN, regex=True)
        assert isinstance(pattern, re.Pattern)
        assert pattern.pattern == SSN_PATTERN
        assert pattern.flags & re.IGNORECASE == 0

    def test_case_insensitive_compiles_with_the_flag(self):
        pattern = prepare_search("abc", regex=True, case_sensitive=False)
        assert pattern.flags & re.IGNORECASE

    def test_a_compiled_pattern_needs_no_flag(self):
        compiled = re.compile(SSN_PATTERN)
        assert prepare_search(compiled) is compiled

    def test_a_compiled_pattern_keeps_its_own_flags(self):
        compiled = re.compile("a.b", re.DOTALL | re.VERBOSE)
        prepared = prepare_search(compiled, case_sensitive=False)
        assert prepared.flags & re.DOTALL
        assert prepared.flags & re.VERBOSE
        assert prepared.flags & re.IGNORECASE

    def test_an_already_insensitive_pattern_is_untouched(self):
        compiled = re.compile("abc", re.IGNORECASE)
        assert prepare_search(compiled, case_sensitive=False) is compiled

    def test_a_pattern_that_cannot_compile_is_a_validation_error(self):
        with pytest.raises(PdfValidationException, match="not a usable regular"):
            prepare_search("(unclosed", regex=True)

    def test_an_unusable_type(self):
        with pytest.raises(TypeError, match="string or a compiled"):
            prepare_search(42, regex=True)

    def test_an_empty_pattern(self):
        with pytest.raises(ValueError, match="must not be empty"):
            prepare_search("", regex=True)


# ---------------------------------------------------------------------------
# Replacing
# ---------------------------------------------------------------------------
class TestReplace:
    def test_a_pattern_finds_what_a_literal_cannot(self):
        text, count = _replaced(
            "SSN 123-45-6789 and 987-65-4321",
            prepare_search(SSN_PATTERN, regex=True),
            "[gone]",
        )
        assert count == 2
        assert text == "SSN [gone] and [gone]"

    def test_a_literal_search_stays_literal(self):
        """The metacharacters are characters: the old behaviour, unchanged."""
        text, count = _replaced("SSN 123-45-6789", SSN_PATTERN, "X")
        assert count == 0
        assert text == "SSN 123-45-6789"
        text, count = _replaced("a.b", ".", "X")
        assert count == 1
        assert text == "aXb"  # the dot is a dot, not "any character"

    def test_group_backreferences(self):
        text, count = _replaced(
            "SSN 123-45-6789",
            prepare_search(r"(\d{3})-(\d{2})-(\d{4})", regex=True),
            r"***-**-\3",
        )
        assert count == 1
        assert text == "SSN ***-**-6789"

    def test_each_match_expands_its_own_groups(self):
        text, _count = _replaced(
            "123-45-6789 987-65-4321",
            prepare_search(r"\d{3}-\d{2}-(\d{4})", regex=True),
            r"<\1>",
        )
        assert text == "<6789> <4321>"

    def test_a_named_group(self):
        text, _count = _replaced(
            "SSN 123-45-6789",
            prepare_search(r"(?P<area>\d{3})-\d{2}-\d{4}", regex=True),
            r"\g<area>-XX-XXXX",
        )
        assert text == "SSN 123-XX-XXXX"

    def test_a_template_naming_a_group_that_is_not_there(self):
        with pytest.raises(PdfValidationException, match="not a usable template"):
            _replaced(
                "123", prepare_search(r"\d+", regex=True), r"\7"
            )

    def test_a_literal_replacement_keeps_its_backslashes(self):
        text, _count = _replaced("path", "path", r"C:\new")
        assert text == r"C:\new"

    def test_max_count_limits_a_pattern(self):
        text, count = _replaced(
            "1-2 3-4 5-6",
            prepare_search(r"\d-\d", regex=True),
            "X",
            max_count=2,
        )
        assert count == 2
        assert text == "X X 5-6"

    def test_case_insensitive(self):
        text, count = _replaced(
            "Sun SUN sun",
            prepare_search("sun", regex=True, case_sensitive=False),
            "moon",
            case_sensitive=False,
        )
        assert count == 3
        assert text == "moon moon moon"

    def test_a_zero_length_match_is_skipped(self):
        """``a*`` matches between every pair of letters; none of those is text."""
        text, count = _replaced(
            "bcd", prepare_search("a*", regex=True), "X"
        )
        assert count == 0
        assert text == "bcd"

    def test_a_pattern_that_can_match_nothing_still_finds_what_it_can(self):
        text, count = _replaced(
            "ab12cd34", prepare_search(r"\d*", regex=True), "#"
        )
        assert count == 2
        assert text == "ab#cd#"

    def test_an_anchor_binds_to_the_run(self):
        text, count = _replaced(
            "SSN 123", prepare_search(r"^SSN", regex=True), "ID"
        )
        assert count == 1
        assert text == "ID 123"

    def test_a_pattern_reaches_across_a_tj_element_boundary(self):
        content = b"BT /F1 12 Tf 1 0 0 1 10 700 Tm [(SSN 123-) (45-) (6789 end)] TJ ET\n"
        out, count = replace_text_in_content(
            content, prepare_search(SSN_PATTERN, regex=True), "[gone]"
        )
        assert count == 1
        assert b"(SSN [gone])" in out
        assert b"( end)" in out

    def test_backreferences_across_a_boundary(self):
        content = b"BT /F1 12 Tf 1 0 0 1 10 700 Tm [(SSN 123-) (45-) (6789)] TJ ET\n"
        out, count = replace_text_in_content(
            content,
            prepare_search(r"(\d{3})-(\d{2})-(\d{4})", regex=True),
            r"\3-\2-\1",
        )
        assert count == 1
        assert b"(SSN 6789-45-123)" in out

    def test_overlapping_candidates_keep_the_earliest(self):
        text, count = _replaced(
            "aaaa", prepare_search("aa", regex=True), "b"
        )
        assert count == 2
        assert text == "bb"


# ---------------------------------------------------------------------------
# Redacting
# ---------------------------------------------------------------------------
class TestRedact:
    def test_a_shape_is_removed(self):
        content = _content("SSN 123-45-6789 and 987-65-4321")
        out, count = redact_text_in_content(
            content, prepare_search(SSN_PATTERN, regex=True)
        )
        assert count == 2
        assert b"(SSN  and )" in out

    def test_the_literal_path_is_unchanged(self):
        content = _content("SSN 123-45-6789")
        out, count = redact_text_in_content(content, "123-45-6789")
        assert count == 1
        assert b"(SSN )" in out


# ---------------------------------------------------------------------------
# The public surface
# ---------------------------------------------------------------------------
class TestDocumentSurface:
    def test_page_replace_text(self):
        with _document("SSN 123-45-6789 and 987-65-4321") as document:
            assert document.pages[0].replace_text(
                SSN_PATTERN, "***", regex=True
            ) == 2
            assert document.pages[0].extract_text() == "SSN *** and ***"

    def test_page_replace_text_with_a_compiled_pattern(self):
        with _document("call 555-0100") as document:
            assert document.pages[0].replace_text(
                re.compile(r"\d{3}-\d{4}"), "<number>"
            ) == 1
            assert "<number>" in document.pages[0].extract_text()

    def test_page_redact_text(self):
        with _document("SSN 123-45-6789") as document:
            assert document.pages[0].redact_text(SSN_PATTERN, regex=True) == 1
            assert document.pages[0].extract_text().strip() == "SSN"

    def test_document_replace_text_covers_every_page(self):
        with _document("first 111-11-1111") as document:
            second = document.pages.add(PageSize.A4)
            second.add_text("second 222-22-2222", 50, 700)
            assert document.replace_text(SSN_PATTERN, "X", regex=True) == 2
            assert "X" in document.pages[0].extract_text()
            assert "X" in document.pages[1].extract_text()

    def test_page_index_limits_the_edit(self):
        with _document("first 111-11-1111") as document:
            second = document.pages.add(PageSize.A4)
            second.add_text("second 222-22-2222", 50, 700)
            assert document.replace_text(
                SSN_PATTERN, "X", regex=True, page_index=1
            ) == 1
            assert "111-11-1111" in document.pages[0].extract_text()

    def test_document_redact_text(self):
        with _document("Email a@b.com, card 4111 1111 1111 1111") as document:
            assert document.redact_text(CARD_PATTERN, regex=True) == 1
            assert "4111" not in document.pages[0].extract_text()

    def test_a_bad_pattern_from_the_public_surface(self):
        with _document("x") as document:
            with pytest.raises(PdfValidationException, match="not a usable regular"):
                document.replace_text("(unclosed", "y", regex=True)

    def test_an_empty_search_is_still_refused(self):
        with _document("x") as document:
            with pytest.raises(ValueError, match="must not be empty"):
                document.replace_text("", "y", regex=True)
            with pytest.raises(ValueError, match="must not be empty"):
                document.redact_text("")

    def test_a_non_string_replacement_is_still_refused(self):
        with _document("x") as document:
            with pytest.raises(TypeError, match="replacement must be a string"):
                document.replace_text("x", 42, regex=True)

    def test_case_insensitive_from_the_public_surface(self):
        with _document("Sun and SUN") as document:
            assert document.replace_text(
                "sun", "moon", regex=True, case_sensitive=False
            ) == 2


# ---------------------------------------------------------------------------
# Overlay bars
# ---------------------------------------------------------------------------
def _bars(content: bytes) -> list[tuple[float, float]]:
    """The horizontal extent of each filled overlay path in *content*."""
    bars = []
    for match in re.finditer(
        rb"([\d.]+) ([\d.]+) m ([\d.]+) ([\d.]+) l", content
    ):
        bars.append((float(match.group(1)), float(match.group(3))))
    return bars


class TestOverlay:
    def test_one_bar_per_match(self):
        with _document("SSN 123-45-6789 and 987-65-4321") as document:
            count = document.pages[0].redact_text(
                SSN_PATTERN, regex=True, overlay=True
            )
            assert count == 2
            assert len(_bars(document.pages[0].content)) == 2

    def test_a_pattern_puts_its_bar_where_the_literal_would(self):
        """The bars come from the spans the removal used, not a second search."""
        with _document("SSN 123-45-6789 and 987-65-4321") as literal:
            literal.pages[0].redact_text("123-45-6789", overlay=True)
            expected = _bars(literal.pages[0].content)
        with _document("SSN 123-45-6789 and 987-65-4321") as pattern:
            pattern.pages[0].redact_text(SSN_PATTERN, regex=True, overlay=True)
            found = _bars(pattern.pages[0].content)
        assert len(expected) == 1 and len(found) == 2
        assert found[0] == pytest.approx(expected[0])

    def test_a_bar_covers_the_text_that_went(self):
        with _document("SSN 123-45-6789") as document:
            before = document.pages[0].content
            # Where the digits sit, measured from the text that is still there.
            document.pages[0].redact_text(SSN_PATTERN, regex=True, overlay=True)
            left, right = _bars(document.pages[0].content)[0]
            assert b"123-45-6789" in before
            assert 50 < left < right
            assert right - left > 40  # eleven characters of 12 pt text


# ---------------------------------------------------------------------------
# Locating, which the bars and the absorber share
# ---------------------------------------------------------------------------
class TestLocating:
    """The locators share ``_aligned_spans`` with the editor, so they get the
    pattern for free -- which is what puts the overlay bars in the right place.
    Real font metrics are needed for any of it: without an advance for each code
    there is no pen to follow, and a run places nothing rather than guessing.
    """

    def test_locate_matches_takes_a_pattern(self):
        from aspose_pdf.engine.text_locate import locate_matches

        with _document("SSN 123-45-6789 and 987-65-4321") as document:
            engine = document._engine_pdf
            engine._materialize_page_contents_for_edit()
            quads = locate_matches(
                engine.page_contents[0],
                prepare_search(SSN_PATTERN, regex=True),
                engine._build_simple_font_metrics(0),
            )
        assert len(quads) == 2
        assert quads[0][0][0] < quads[1][0][0]  # in the order they are painted

    def test_locate_text_takes_a_pattern(self):
        from aspose_pdf.engine.text_locate import locate_text

        with _document("SSN 123-45-6789") as document:
            engine = document._engine_pdf
            engine._materialize_page_contents_for_edit()
            located = locate_text(
                engine.page_contents[0],
                prepare_search(SSN_PATTERN, regex=True),
                engine._build_simple_font_metrics(0),
            )
        assert len(located) == 1
        assert located[0].rect[0] > 50
        assert located[0].font_size == 12


# ---------------------------------------------------------------------------
# Composite fonts and form XObjects, the two paths that decode differently
# ---------------------------------------------------------------------------
def _font_bytes() -> bytes:
    from aspose_pdf.engine.std_font_data import load_substitute_sfnt

    data = load_substitute_sfnt("sans-regular")
    if not data:
        pytest.skip("no bundled substitute font to embed")
    return data


def test_a_pattern_edits_text_in_an_embedded_font():
    document = Document()
    page = document.pages.add(PageSize.A4)
    # The replacement's glyphs have to be in the run's own subset: a subset
    # holds what was asked for, and a CID edit splices codes rather than
    # growing the font. "ukryte" is in the line for that reason.
    page.add_text(
        "Konto 123-45-6789 ukryte zamknięte", 50, 700, font=_font_bytes()
    )
    buffer = io.BytesIO()
    document.save(buffer)
    document.close()
    with Document(buffer.getvalue()) as reopened:
        assert reopened.replace_text(SSN_PATTERN, "ukryte", regex=True) == 1
        text = reopened.pages[0].extract_text()
    assert "ukryte" in text
    assert "123-45" not in text


def test_a_pattern_reaches_text_inside_a_form_xobject():
    from aspose_pdf.engine.cos import PdfName, PdfNumber, PdfStream

    document = Document()
    page = document.pages.add(PageSize.A4)
    page.add_text("on the page 111-11-1111", 50, 700)
    engine = document._engine_pdf
    body = b"BT /F1 12 Tf 1 0 0 1 10 600 Tm (in the form 222-22-2222) Tj ET\n"
    fonts = engine._ensure_resource_subdict(0, "Font")
    form = PdfStream(
        body,
        {
            PdfName("Type"): PdfName("XObject"),
            PdfName("Subtype"): PdfName("Form"),
            PdfName("BBox"): __import__(
                "aspose_pdf.engine.cos", fromlist=["PdfArray"]
            ).PdfArray([PdfNumber(0), PdfNumber(0), PdfNumber(400), PdfNumber(800)]),
            PdfName("Resources"): __import__(
                "aspose_pdf.engine.cos", fromlist=["PdfDictionary"]
            ).PdfDictionary({PdfName("Font"): fonts}),
            PdfName("Length"): PdfNumber(len(body)),
        },
    )
    xobjects = engine._ensure_resource_subdict(0, "XObject")
    xobjects.mapping[PdfName("Fm0")] = engine._cos_doc.register_object(form)
    engine._append_content_to_page(0, b"q /Fm0 Do Q\n")
    buffer = io.BytesIO()
    document.save(buffer)
    document.close()

    with Document(buffer.getvalue()) as reopened:
        assert reopened.replace_text(SSN_PATTERN, "X", regex=True) == 2
        text = reopened.pages[0].extract_text()
    assert "111-11-1111" not in text
    assert "222-22-2222" not in text


def test_a_saved_regex_redaction_reads_back_clean(tmp_path):
    target = tmp_path / "redacted.pdf"
    with _document(
        "Name: Ada Lovelace", "SSN 123-45-6789", "Card 4111 1111 1111 1111"
    ) as document:
        assert document.redact_text(SSN_PATTERN, regex=True) == 1
        assert document.redact_text(CARD_PATTERN, regex=True) == 1
        document.save(target)
    with Document(target) as reopened:
        text = reopened.pages[0].extract_text()
    assert "Ada Lovelace" in text
    assert "123-45-6789" not in text
    assert "4111" not in text
