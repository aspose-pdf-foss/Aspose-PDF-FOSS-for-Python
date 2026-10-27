"""Search-driven text markup preserves text and reports unplaceable matches."""

from __future__ import annotations

import io
import re

import pytest

from aspose_pdf import (
    Color,
    Document,
    HighlightAnnotation,
    PdfLoadLimits,
    SquigglyAnnotation,
    StrikeOutAnnotation,
    TextHighlightResult,
    TextMarkupResult,
    UnderlineAnnotation,
)
from aspose_pdf.annotations import quad_from_rect
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName, PdfNumber, PdfStream
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfResourceLimitException,
    PdfValidationException,
)
from aspose_pdf.text import TextFragmentAbsorber

MARKUP_STYLES = [
    ("highlight", HighlightAnnotation, (1.0, 1.0, 0.0)),
    ("underline", UnderlineAnnotation, (0.0, 0.0, 0.0)),
    ("strike_out", StrikeOutAnnotation, (0.0, 0.0, 0.0)),
    ("squiggly", SquigglyAnnotation, (0.0, 0.0, 0.0)),
]


@pytest.fixture
def document():
    with Document() as doc:
        first, second = doc.pages.add(), doc.pages.add()
        first.add_text("Hello world world", 72, 700, font_size=12)
        first.add_text("Invoice 123 paid 456", 72, 650, font_size=12)
        second.add_text("HELLO world", 100, 500, font_size=12)
        yield doc


@pytest.mark.parametrize("on_disk", [False, True])
def test_literal_highlights_survive_round_trips_and_preserve_text(
    document, tmp_path, on_disk
):
    original = document.extract_text()
    contents = [page.content for page in document.pages]
    absorber = TextFragmentAbsorber("world")
    absorber.visit(document)
    result = document.highlight_text("world", contents="Reviewed", title="Reviewer")
    assert isinstance(result, TextHighlightResult)
    assert (result.matched_count, result.highlighted_count, result.skipped_count) == (
        3,
        3,
        0,
    )
    assert result.skipped_fragments == ()
    assert all(
        isinstance(annotation, HighlightAnnotation) for annotation in result.annotations
    )
    assert [len(page.annotations) for page in document.pages] == [2, 1]
    assert [page.content for page in document.pages] == contents
    assert document.extract_text() == original
    for annotation, fragment in zip(result.annotations, absorber.text_fragments):
        assert annotation.quad_points == [quad_from_rect(*fragment.rect)]
        assert annotation.color == (1.0, 1.0, 0.0)
        assert annotation.opacity == 0.5
        assert annotation.contents == "Reviewed" and annotation.title == "Reviewer"
    if on_disk:
        source = tmp_path / "highlighted.pdf"
        document.save(source)
    else:
        stream = io.BytesIO()
        document.save(stream)
        source = stream.getvalue()
    with Document(source) as reopened:
        assert reopened.extract_text() == original
        assert [len(page.annotations) for page in reopened.pages] == [2, 1]
        for expected, actual in zip(
            result.annotations, [a for p in reopened.pages for a in p.annotations]
        ):
            assert actual.get_property("QuadPoints") == pytest.approx(
                expected.get_property("QuadPoints"), rel=0, abs=1e-6
            )
            assert actual.contents == "Reviewed" and actual.title == "Reviewer"
            assert actual.color == expected.color and actual.opacity == expected.opacity


def test_page_method_and_document_page_selection(document):
    result = document.pages[1].highlight_text("HELLO")
    assert result.highlighted_count == 1
    assert [len(page.annotations) for page in document.pages] == [0, 1]
    result = document.highlight_text("Hello", page_index=0)
    assert result.highlighted_count == 1
    assert [len(page.annotations) for page in document.pages] == [1, 1]
    result.annotations[0].contents = "Edited"
    assert document.pages[0].annotations[0].contents == "Edited"


@pytest.mark.parametrize(
    "search, options, counts",
    [
        ("HELLO", {}, [0, 1]),
        ("hello", {"case_sensitive": False}, [1, 1]),
        (r"\d+", {"regex": True}, [2, 0]),
        (re.compile(r"hello", re.IGNORECASE), {}, [1, 1]),
        (re.compile(r"hello"), {"case_sensitive": False}, [1, 1]),
    ],
)
def test_literal_regex_and_case_options(document, search, options, counts):
    result = document.highlight_text(search, **options)
    assert result.highlighted_count == sum(counts) and result.skipped_count == 0
    assert [len(page.annotations) for page in document.pages] == counts


def test_regex_context_places_only_the_selected_repeated_word(document):
    result = document.pages[0].highlight_text(r"(?<=Hello )world", regex=True)
    assert (result.highlighted_count, result.skipped_count) == (1, 0)
    first = result.annotations[0].quad_points[0]
    last = (
        document.pages[0]
        .highlight_text(re.compile(r"world$", re.MULTILINE))
        .annotations[0]
        .quad_points[0]
    )
    assert first[1][0] < last[0][0]


def test_literal_metacharacters_and_nonoverlapping_matches():
    with Document() as doc:
        page = doc.pages.add()
        page.add_text("price [10].* aaaa", 72, 700)
        assert page.highlight_text("[10].*").highlighted_count == 1
        result = page.highlight_text("aa")
        assert (result.highlighted_count, result.skipped_count) == (2, 0)
        first, second = [annotation.quad_points[0] for annotation in result.annotations]
        assert first[1][0] <= second[0][0]


def test_zero_width_regex_matches_are_ignored(document):
    result = document.highlight_text(r"\d*", regex=True)
    assert (result.matched_count, result.highlighted_count, result.skipped_count) == (
        2,
        2,
        0,
    )
    assert document.highlight_text(r"(?=world)", regex=True).matched_count == 0


def test_max_count_applies_across_pages(document):
    result = document.highlight_text("world", max_count=2)
    assert (result.matched_count, result.highlighted_count) == (2, 2)
    assert [len(page.annotations) for page in document.pages] == [2, 0]


@pytest.mark.parametrize(
    "color, expected",
    [
        ("#00ff00", (0.0, 1.0, 0.0)),
        (Color.gray(0.25), (0.25,)),
        (Color.cmyk(0, 0.2, 1, 0), (0.0, 0.2, 1.0, 0.0)),
    ],
)
def test_highlight_style_and_rendering(document, color, expected):
    page = document.pages[0]
    before = page.render(dpi=36, antialias=False).pixels
    result = page.highlight_text("Hello", color=color, opacity=0.75)
    annotation = result.annotations[0]
    assert annotation.color == expected and annotation.opacity == 0.75
    assert page.render(dpi=36, antialias=False).pixels != before
    assert page.render(dpi=36, antialias=False, draw_annotations=False).pixels == before


def _with_content(content):
    document = Document()
    document.pages.add().add_text("seed", 72, 740)
    buffer = io.BytesIO()
    document.save(buffer)
    document.dispose()
    reopened = Document(buffer.getvalue())
    reopened._engine_pdf._set_page_content(0, content)
    return reopened


def test_unmeasurable_matches_are_reported_with_page_and_offsets():
    with _with_content(
        b"BT /Nosuch 12 Tf 72 700 Td (Unknown) Tj ET "
        b"BT /F1 12 Tf 72 650 Td (Known) Tj ET"
    ) as doc:
        result = doc.highlight_text(r"Unknown|Known", regex=True)
        assert (
            result.matched_count,
            result.highlighted_count,
            result.skipped_count,
        ) == (2, 1, 1)
        [fragment] = result.skipped_fragments
        assert fragment.page_index == 0 and fragment.text == "Unknown"
        assert (
            doc.pages[0].extract_text()[fragment.start : fragment.end] == fragment.text
        )
        assert fragment.quads is None and fragment.rect is None


def test_max_count_counts_skipped_matches():
    with _with_content(
        b"BT /Nosuch 12 Tf 72 700 Td (Unknown) Tj ET "
        b"BT /F1 12 Tf 72 650 Td (Known) Tj ET"
    ) as doc:
        result = doc.highlight_text(r"Unknown|Known", regex=True, max_count=1)
        assert (
            result.matched_count,
            result.highlighted_count,
            result.skipped_count,
        ) == (1, 0, 1)
        assert len(doc.pages[0].annotations) == 0


def test_ambiguous_repeated_text_does_not_borrow_another_occurrences_geometry():
    with _with_content(
        b"BT /Nosuch 12 Tf 72 700 Td (Repeat) Tj ET "
        b"BT /F1 12 Tf 72 650 Td (Repeat) Tj ET"
    ) as doc:
        result = doc.highlight_text("Repeat")
        assert (result.highlighted_count, result.skipped_count) == (0, 2)
        assert [fragment.text for fragment in result.skipped_fragments] == [
            "Repeat",
            "Repeat",
        ]
        assert len(doc.pages[0].annotations) == 0


def test_text_inside_form_xobjects_is_reported_as_unplaced():
    with _with_content(b"q /Fm0 Do Q") as doc:
        engine = doc._engine_pdf
        page = engine._get_page_dict(0)
        resources = engine._resolve(page.mapping[PdfName("Resources")])
        form = PdfStream(
            b"BT /F1 12 Tf 72 700 Td (Inside form) Tj ET",
            {
                PdfName("Type"): PdfName("XObject"),
                PdfName("Subtype"): PdfName("Form"),
                PdfName("BBox"): PdfArray([PdfNumber(v) for v in (0, 0, 400, 800)]),
                PdfName("Resources"): resources,
            },
        )
        reference = engine._cos_doc.register_object(form)
        resources.mapping[PdfName("XObject")] = PdfDictionary(
            {PdfName("Fm0"): reference}
        )
        result = doc.highlight_text("Inside form")
        assert (result.highlighted_count, result.skipped_count) == (0, 1)


def test_rotated_text_keeps_its_quadrilateral_corner_order():
    with _with_content(
        b"q 0 1 -1 0 400 0 cm BT /F1 12 Tf 72 200 Td (Turned) Tj ET Q"
    ) as doc:
        result = doc.highlight_text("Turned")
        assert result.highlighted_count == 1
        [(upper_left, upper_right, lower_left, lower_right)] = result.annotations[
            0
        ].quad_points
        assert upper_left[1] < upper_right[1]
        assert upper_left[0] < lower_left[0]
        assert lower_left[1] < lower_right[1]
        assert upper_right[0] < lower_right[0]


def test_cross_line_match_without_a_single_run_is_reported():
    with _with_content(
        b"BT /F1 12 Tf 72 700 Td (First) Tj 0 -20 Td (Second) Tj ET"
    ) as doc:
        result = doc.highlight_text(r"First\s+Second", regex=True)
        # Position changes split logical runs, so this phrase is extractable
        # across lines but has no single reliably located match.
        assert (result.highlighted_count, result.skipped_count) == (0, 1)


@pytest.mark.parametrize(
    "search, options, error",
    [
        ("", {}, ValueError),
        (None, {}, TypeError),
        (re.compile(b"Hello"), {}, TypeError),
        ("[", {"regex": True}, re.error),
        ("Hello", {"max_count": -1}, ValueError),
        ("Hello", {"max_count": 1.5}, TypeError),
        ("Hello", {"max_count": True}, TypeError),
        ("Hello", {"regex": 1}, TypeError),
        ("Hello", {"case_sensitive": 0}, TypeError),
        ("Hello", {"page_index": -1}, IndexError),
        ("Hello", {"page_index": 2}, IndexError),
        ("Hello", {"page_index": True}, TypeError),
        ("Hello", {"opacity": -0.1}, PdfValidationException),
        ("Hello", {"opacity": float("nan")}, PdfValidationException),
        ("Hello", {"opacity": True}, TypeError),
        ("Hello", {"color": (1, 2)}, PdfValidationException),
        ("Hello", {"contents": 42}, TypeError),
        ("Hello", {"title": "\ud800"}, PdfValidationException),
    ],
)
def test_invalid_arguments_do_not_add_annotations(document, search, options, error):
    with pytest.raises(error):
        document.highlight_text(search, **options)
    assert all(len(page.annotations) == 0 for page in document.pages)


def test_empty_results_and_disposed_documents(document):
    assert document.highlight_text("missing") == TextHighlightResult()
    with Document() as empty:
        assert empty.highlight_text("Hello") == TextHighlightResult()
    page = document.pages[0]
    document.dispose()
    with pytest.raises(AsposePdfException, match="disposed"):
        document.highlight_text("Hello")
    with pytest.raises(AsposePdfException, match="disposed"):
        page.highlight_text("Hello")


def test_search_obeys_resource_limits_before_adding_annotations():
    with Document(limits=PdfLoadLimits(max_container_items=16)) as doc:
        doc.pages.add().add_text(" ".join(["one"] * 17), 72, 700)
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            doc.highlight_text("one")
        assert len(doc.pages[0].annotations) == 0


def test_page_selection_does_not_read_other_streaming_page_content(
    document, tmp_path, monkeypatch
):
    source = tmp_path / "source.pdf"
    document.save(source)
    with Document.open_streaming(source) as reopened:
        original = reopened._engine_pdf.get_page_content
        accessed = []

        def selected_only(index):
            assert index == 1
            accessed.append(index)
            return original(index)

        monkeypatch.setattr(reopened._engine_pdf, "get_page_content", selected_only)
        result = reopened.highlight_text("world", page_index=1)
        assert result.highlighted_count == 1 and accessed


@pytest.mark.parametrize("kind, annotation_class, default_color", MARKUP_STYLES)
@pytest.mark.parametrize("on_disk", [False, True])
def test_markup_styles_survive_round_trips_without_changing_text(
    document, tmp_path, kind, annotation_class, default_color, on_disk
):
    original_text = document.extract_text()
    original_content = [page.content for page in document.pages]
    result = document.markup_text(
        "world", kind=kind, contents="Review <&> Привет", title="Reviewer"
    )
    assert type(result) is TextMarkupResult
    assert (result.marked_count, result.skipped_count, result.matched_count) == (3, 0, 3)
    assert [len(page.annotations) for page in document.pages] == [2, 1]
    assert [page.content for page in document.pages] == original_content
    assert document.extract_text() == original_text
    for annotation in result.annotations:
        assert isinstance(annotation, annotation_class)
        assert annotation.color == default_color
        assert annotation.opacity == 0.5
        assert annotation.contents == "Review <&> Привет"
        assert annotation.title == "Reviewer"
    if on_disk:
        source = tmp_path / f"{kind}.pdf"
        document.save(source)
    else:
        with io.BytesIO() as stream:
            document.save(stream)
            source = stream.getvalue()
    with Document(source) as reopened:
        assert reopened.extract_text() == original_text
        actual = [annotation for page in reopened.pages for annotation in page.annotations]
        assert len(actual) == result.marked_count
        for expected, annotation in zip(result.annotations, actual):
            assert isinstance(annotation, annotation_class)
            assert annotation.get_property("QuadPoints") == pytest.approx(
                expected.get_property("QuadPoints"), rel=0, abs=1e-6
            )
            assert annotation.color == expected.color
            assert annotation.opacity == expected.opacity
            assert annotation.contents == expected.contents
            assert annotation.title == expected.title


@pytest.mark.parametrize("kind", ["underline", "strike_out", "squiggly"])
def test_markup_page_search_keeps_regex_context_and_live_annotation_handles(
    document, kind
):
    first = document.pages[0].markup_text(r"(?<=Hello )world", kind=kind, regex=True)
    last = document.pages[0].markup_text(
        re.compile(r"world$", re.MULTILINE), kind=kind
    )
    assert (first.marked_count, first.skipped_count) == (1, 0)
    assert (
        first.annotations[0].quad_points[0][1][0]
        < last.annotations[0].quad_points[0][0][0]
    )
    first.annotations[0].contents = "Updated review"
    assert document.pages[0].annotations[0].contents == "Updated review"
    assert len(document.pages[1].annotations) == 0


@pytest.mark.parametrize("kind", ["underline", "strike_out", "squiggly"])
def test_markup_case_matching_and_max_count_across_pages(document, kind):
    result = document.markup_text("hello", kind=kind, case_sensitive=False, max_count=2)
    assert (result.marked_count, result.matched_count) == (2, 2)
    assert [len(page.annotations) for page in document.pages] == [1, 1]


@pytest.mark.parametrize("kind", ["underline", "strike_out", "squiggly"])
@pytest.mark.parametrize("color", ["#ff0000", Color.gray(0.25), Color.cmyk(1, 0, 0, 0)])
def test_markup_styles_render_and_respect_annotation_visibility(kind, color):
    with Document() as document:
        page = document.pages.add(size=(200, 100))
        page.add_text("Review this", 20, 50, font_size=20)
        before = page.render(dpi=72, antialias=False).pixels
        result = page.markup_text("Review", kind=kind, color=color, opacity=0.75)
        assert result.marked_count == 1 and result.annotations[0].opacity == 0.75
        assert page.render(dpi=72, antialias=False).pixels != before
        assert (
            page.render(dpi=72, antialias=False, draw_annotations=False).pixels
            == before
        )


@pytest.mark.parametrize("kind", [None, 1, [], "", "Underline", "redact", "strikeout"])
def test_invalid_markup_kind_is_rejected_before_adding_annotations(document, kind):
    error = PdfValidationException if isinstance(kind, str) else TypeError
    with pytest.raises(error, match="kind must be"):
        document.markup_text("world", kind=kind)
    assert all(len(page.annotations) == 0 for page in document.pages)


@pytest.mark.parametrize("kind", ["underline", "strike_out", "squiggly"])
def test_markup_skips_unknown_geometry_and_counts_skips_toward_limit(kind):
    with _with_content(
        b"BT /Nosuch 12 Tf 72 700 Td (Unknown) Tj ET "
        b"BT /F1 12 Tf 72 650 Td (Known) Tj ET"
    ) as document:
        skipped = document.markup_text(
            r"Unknown|Known", kind=kind, regex=True, max_count=1
        )
        assert (
            skipped.marked_count, skipped.skipped_count, skipped.matched_count
        ) == (0, 1, 1)
        assert skipped.skipped_fragments[0].text == "Unknown"
        assert len(document.pages[0].annotations) == 0
        result = document.markup_text(r"Unknown|Known", kind=kind, regex=True)
        assert (
            result.marked_count, result.skipped_count, result.matched_count
        ) == (1, 1, 2)


@pytest.mark.parametrize("kind", ["underline", "strike_out", "squiggly"])
def test_rotated_markup_uses_the_same_match_quadrilaterals_as_highlighting(kind):
    with _with_content(
        b"q 0 1 -1 0 400 0 cm BT /F1 12 Tf 72 200 Td (Turned) Tj ET Q"
    ) as document:
        highlight = document.highlight_text("Turned").annotations[0]
        result = document.markup_text("Turned", kind=kind)
        assert result.marked_count == 1
        assert result.annotations[0].quad_points == highlight.quad_points


@pytest.mark.parametrize("kind", ["underline", "strike_out", "squiggly"])
def test_markup_selected_page_stays_lazy(document, tmp_path, monkeypatch, kind):
    path = tmp_path / "source.pdf"
    document.save(path)
    with Document.open_streaming(path) as reopened:
        original = reopened._engine_pdf.get_page_content
        accessed = []

        def selected_only(index):
            assert index == 1
            accessed.append(index)
            return original(index)

        monkeypatch.setattr(reopened._engine_pdf, "get_page_content", selected_only)
        result = reopened.markup_text("world", kind=kind, page_index=1)
        assert result.marked_count == 1 and accessed


@pytest.mark.parametrize("kind", ["underline", "strike_out", "squiggly"])
def test_markup_resource_limits_apply_before_adding_page_annotations(kind):
    with Document(limits=PdfLoadLimits(max_container_items=16)) as document:
        document.pages.add().add_text(" ".join(["one"] * 17), 72, 700)
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            document.markup_text("one", kind=kind)
        assert len(document.pages[0].annotations) == 0


def test_markup_empty_results_defaults_and_disposed_documents(document):
    assert document.markup_text("missing") == TextMarkupResult()
    annotation = document.markup_text("Hello").annotations[0]
    assert isinstance(annotation, HighlightAnnotation)
    assert annotation.color == (1.0, 1.0, 0.0)
    with Document() as empty:
        assert empty.markup_text("Hello", kind="underline") == TextMarkupResult()
    page = document.pages[0]
    document.dispose()
    with pytest.raises(AsposePdfException, match="disposed"):
        document.markup_text("Hello", kind="underline")
    with pytest.raises(AsposePdfException, match="disposed"):
        page.markup_text("Hello", kind="underline")
