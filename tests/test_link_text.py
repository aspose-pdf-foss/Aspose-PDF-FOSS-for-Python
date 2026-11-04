"""Literal phrase links preserve PDF content and use conservative placement."""

from __future__ import annotations

import io
import re

import pytest

from aspose_pdf import AutoLinkResult, Document, PdfLoadLimits, URIAction
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName, PdfNumber, PdfStream
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfResourceLimitException,
    PdfValidationException,
)
from aspose_pdf.text import TextFragmentAbsorber

URI = "https://example.org/support?source=pdf#help"


def _bytes(document):
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


def _with_content(content):
    document = Document()
    document.pages.add().add_text("seed", 72, 700)
    document._engine_pdf._set_page_content(0, content)
    return document


@pytest.mark.parametrize("source", ["live", "bytes", "file", "streaming"])
def test_repeated_phrase_links_round_trip_without_changing_text_or_appearance(
    tmp_path, source
):
    with Document() as original:
        page = original.pages.add()
        page.add_text("Contact support. Contact support!", 30, 700)
        raw = _bytes(original)
        if source == "live":
            document = original
        elif source == "bytes":
            document = Document(raw)
        else:
            path = tmp_path / "input.pdf"
            path.write_bytes(raw)
            document = (Document.open_streaming if source == "streaming" else Document)(
                path
            )
        try:
            page = document.pages[1]
            text, content = page.extract_text(), page.content
            pixels = page.render(dpi=24, antialias=False).pixels
            result = document.link_text("Contact support", URI)
            assert isinstance(result, AutoLinkResult)
            assert (
                result.linked_count,
                result.skipped_count,
                result.existing_count,
            ) == (2, 0, 0)
            assert [a.get_property("A")["URI"] for a in result.annotations] == [
                URI,
                URI,
            ]
            absorber = TextFragmentAbsorber("Contact support")
            absorber.visit(page)
            for annotation, fragment in zip(
                result.annotations, absorber.text_fragments
            ):
                assert annotation.rect == pytest.approx(fragment.rect)
                assert annotation.get_property("Border") == [0, 0, 0]
            assert result.annotations[0].rect[2] < result.annotations[1].rect[0]
            result.annotations[0].contents = "Help center"
            assert page.annotations[0].contents == "Help center"
            assert page.content == content and page.extract_text() == text
            assert page.render(dpi=24, antialias=False).pixels == pixels
            output = tmp_path / "linked.pdf"
            document.save(output)
            for saved in (output, _bytes(document)):
                with Document(saved) as reopened:
                    assert reopened.extract_text() == text
                    assert reopened.pages[1].annotations[0].contents == "Help center"
                    assert [
                        a.get_property("A")["URI"]
                        for a in reopened.pages[1].annotations
                    ] == [URI, URI]
                    repeated = reopened.pages[1].link_text(
                        "Contact support", "https://other.org"
                    )
                    assert (repeated.linked_count, repeated.existing_count) == (0, 2)
                    for fragment in repeated.existing_fragments:
                        assert fragment.page_index == 1
                        assert text[fragment.start : fragment.end] == fragment.text
        finally:
            if document is not original:
                document.dispose()


@pytest.mark.parametrize("case_sensitive,count", [(True, 1), (False, 3)])
def test_case_sensitivity_and_literal_regex_characters(case_sensitive, count):
    with Document() as document:
        page = document.pages.add()
        page.add_text(
            "Help (v2.0)+ HELP (V2.0)+ help (v2.0)+ Help v2x0", 20, 700, font_size=8
        )
        result = page.link_text("Help (v2.0)+", URI, case_sensitive=case_sensitive)
        assert result.linked_count == count and result.skipped_count == 0
        assert page.link_text("absent", URI) == AutoLinkResult()


def test_default_case_sensitivity_and_non_overlapping_substring_matches():
    with Document() as document:
        page = document.pages.add()
        page.add_text("aaaa AA", 72, 700)
        result = page.link_text("aa", URI)
        assert result.linked_count == 2
        assert page.link_text("aa", URI, case_sensitive=False).existing_count == 2
        assert len(page.annotations) == 3


def test_page_selection_order_repetitions_and_limit_including_existing_links():
    with Document() as document:
        for _ in range(3):
            document.pages.add().add_text("Read more", 72, 700)
        result = document.link_text(
            "Read more", URI, pages=(p for p in [3, 1, 3, 2]), max_count=3
        )
        assert (result.linked_count, result.existing_count, result.matched_count) == (
            2,
            1,
            3,
        )
        assert result.existing_fragments[0].page_index == 3
        result.annotations[0].contents = "Third page"
        assert document.pages[3].annotations[0].contents == "Third page"
        assert [len(page.annotations) for page in document.pages] == [1, 0, 1]
        assert document.pages[2].link_text("Read more", URI).linked_count == 1


def test_existing_different_target_is_preserved_and_notes_do_not_block_links():
    with Document() as document:
        page = document.pages.add()
        page.add_text("Support", 72, 700)
        page.add_text("Support", 72, 650)
        page.add_link((70, 695, 250, 715), URIAction("https://existing.org"))
        page.annotations.add("Text", (70, 645, 250, 665), "Review")
        result = page.link_text("Support", URI)
        assert (result.linked_count, result.existing_count) == (1, 1)
        assert page.annotations[0].get_property("A")["URI"] == "https://existing.org"
        assert result.existing_fragments[0].rect[1] > result.annotations[0].rect[1]
        assert len(page.annotations) == 3


def test_unmeasurable_match_counts_toward_limit_without_reading_later_pages(
    monkeypatch,
):
    with _with_content(b"BT /Missing 10 Tf 72 700 Td (Support) Tj ET") as document:
        document.pages.add().add_text("Support", 72, 700)
        engine = document._engine_pdf
        original = engine.get_page_content

        def first_only(index):
            assert index == 0
            return original(index)

        monkeypatch.setattr(engine, "get_page_content", first_only)
        result = document.link_text("Support", URI, max_count=1)
        assert (result.linked_count, result.skipped_count, result.matched_count) == (
            0,
            1,
            1,
        )
        fragment = result.skipped_fragments[0]
        assert fragment.text == "Support" and fragment.page_index == 1
        assert fragment.rect is None and fragment.quads is None
        assert (
            document.pages[1].extract_text()[fragment.start : fragment.end] == "Support"
        )
        assert len(document.pages[2].annotations) == 0


def test_ambiguous_repeated_phrase_never_borrows_another_occurrences_geometry():
    with _with_content(
        b"BT /Missing 10 Tf 72 700 Td (Support) Tj ET BT /F1 10 Tf 72 650 Td (Support) Tj ET"
    ) as document:
        result = document.link_text("Support", URI)
        assert (result.linked_count, result.skipped_count) == (0, 2)
        assert len(document.pages[1].annotations) == 0


def test_form_text_is_reported_without_guessing_coordinates():
    with _with_content(b"q /Fm Do Q") as document:
        engine = document._engine_pdf
        resources = engine._resolve_resources_cos(engine._get_page_dict(0))
        form = PdfStream(
            b"BT /F1 10 Tf 72 700 Td (Contact support) Tj ET",
            {
                PdfName("Subtype"): PdfName("Form"),
                PdfName("BBox"): PdfArray([PdfNumber(v) for v in (0, 0, 595, 842)]),
            },
        )
        resources.mapping[PdfName("XObject")] = PdfDictionary(
            {PdfName("Fm"): engine._cos_doc.register_object(form)}
        )
        result = document.link_text("Contact support", URI)
        assert (result.linked_count, result.skipped_count) == (0, 1)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_transformed_phrase_across_contiguous_operands_and_inherited_resources(
    rotation,
):
    with _with_content(
        b"q 0 1 -1 0 400 0 cm BT /F1 10 Tf 72 200 Td [(Contact ) 0 (support)] TJ ET Q"
    ) as document:
        engine = document._engine_pdf
        page_dict = engine._get_page_dict(0)
        parent = engine._resolve(page_dict.mapping[PdfName("Parent")])
        parent.mapping[PdfName("Resources")] = page_dict.mapping.pop(
            PdfName("Resources")
        )
        page = document.pages[1]
        page.rotation = rotation
        page.crop_box = (10, 10, 500, 780)
        absorber = TextFragmentAbsorber("Contact support")
        absorber.visit(page)
        result = page.link_text("Contact support", URI)
        assert result.linked_count == 1
        assert result.annotations[0].rect == pytest.approx(
            absorber.text_fragments[0].rect
        )


@pytest.mark.parametrize(
    "uri", ["mailto:help@example.org", "../help.html#part", "https://example.org/café"]
)
def test_embedded_unicode_phrase_and_verbatim_uri_round_trip(uri):
    from aspose_pdf.engine.std_font_data import load_substitute_sfnt

    with Document() as document:
        page = document.pages.add()
        page.add_text("Café help", 72, 700, font=load_substitute_sfnt("sans-regular"))
        result = page.link_text("Café help", uri)
        assert result.linked_count == 1 and result.skipped_count == 0
        with Document(_bytes(document)) as reopened:
            assert reopened.pages[1].annotations[0].get_property("A")["URI"] == uri


@pytest.mark.parametrize(
    "options",
    [
        {"search": ""},
        {"search": None},
        {"search": b"Support"},
        {"search": re.compile("Support")},
        {"search": "\ud800"},
        {"uri": ""},
        {"uri": "  "},
        {"uri": None},
        {"uri": b"https://example.org"},
        {"uri": "\ud800"},
        {"case_sensitive": 1},
        {"case_sensitive": None},
        {"max_count": True},
        {"max_count": -1},
        {"max_count": 1.5},
        {"pages": "1"},
        {"pages": [True]},
        {"pages": [0]},
        {"pages": [1, 2]},
        {"pages": [1, "1"]},
    ],
)
def test_invalid_options_are_rejected_before_content_reads_or_edits(
    monkeypatch, options
):
    with Document() as document:
        document.pages.add().add_text("Support", 72, 700)
        before = _bytes(document)

        def unexpected_read(*args, **kwargs):
            raise AssertionError("Options must be validated before reading content")

        with monkeypatch.context() as patch:
            patch.setattr(document._engine_pdf, "get_page_content", unexpected_read)
            with pytest.raises(PdfValidationException):
                document.link_text(**{"search": "Support", "uri": URI, **options})
        assert _bytes(document) == before


def test_empty_selections_documents_and_disposal(monkeypatch):
    with Document() as document:
        assert document.link_text("Support", URI) == AutoLinkResult()
        with pytest.raises(PdfValidationException):
            document.link_text("", URI, pages=[])
        page = document.pages.add()

        def unexpected_read(*args, **kwargs):
            raise AssertionError("Empty selections must not read page content")

        monkeypatch.setattr(document._engine_pdf, "get_page_content", unexpected_read)
        assert document.link_text("Support", URI, pages=[]) == AutoLinkResult()
        document.dispose()
        for target in (document, page):
            with pytest.raises(AsposePdfException, match="disposed"):
                target.link_text("Support", URI)


@pytest.mark.parametrize("algorithm", ["RC4", "AES-128", "AES-256"])
def test_encrypted_streaming_selection_stays_lazy_and_saves_links(
    tmp_path, monkeypatch, algorithm
):
    path = tmp_path / "encrypted.pdf"
    with Document() as document:
        for _ in range(3):
            document.pages.add().add_text("Contact support", 72, 700)
        document.encrypt(
            user_password="secret", owner_password="owner", algorithm=algorithm
        )
        document.save(path)
    with Document.open_streaming(path, password="secret") as document:
        engine = document._engine_pdf
        original = engine.get_page_content

        def selected_only(index):
            assert index == 1
            return original(index)

        with monkeypatch.context() as patch:
            patch.setattr(engine, "get_page_content", selected_only)
            assert (
                document.link_text("Contact support", URI, pages=[2]).linked_count == 1
            )
            assert engine.page_contents == []
        output = tmp_path / "linked.pdf"
        document.save(output)
    with Document(output, password="secret") as reopened:
        assert [len(page.annotations) for page in reopened.pages] == [0, 1, 0]
        assert reopened.pages[2].annotations[0].get_property("A")["URI"] == URI


@pytest.mark.parametrize(
    "options",
    [{"search": "x" * 65}, {"search": "." * 33}, {"uri": "x" * 65}, {"uri": "é" * 32}],
)
def test_input_and_pattern_sizes_obey_limits_even_with_empty_selection(options):
    with Document(limits=PdfLoadLimits(max_object_bytes=64)) as document:
        with pytest.raises(PdfResourceLimitException, match="max_object_bytes"):
            document.link_text(
                **{"search": "Support", "uri": URI, "pages": [], **options}
            )


def test_match_collection_limit_precedes_annotation_creation():
    with Document(limits=PdfLoadLimits(max_container_items=16)) as document:
        page = document.pages.add()
        page.add_text(" ".join(["Help"] * 17), 20, 700, font_size=4)
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            page.link_text("Help", URI)
        assert len(page.annotations) == 0


def test_object_capacity_is_checked_for_all_links_before_page_edits():
    with Document() as original:
        original.pages.add().add_text("Help Help Help", 72, 700)
        raw = _bytes(original)
        objects = original._engine_pdf._cos_doc.objects
        limit = max(objects) + 2
    with Document(raw, limits=PdfLoadLimits(max_objects=limit)) as document:
        before = len(document._engine_pdf._cos_doc.objects)
        with pytest.raises(PdfResourceLimitException, match="max_objects"):
            document.link_text("Help", URI)
        assert len(document.pages[1].annotations) == 0
        assert len(document._engine_pdf._cos_doc.objects) == before
