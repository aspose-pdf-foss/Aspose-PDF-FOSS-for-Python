"""Automatic URI links preserve text, use reliable bounds and avoid duplicates."""

from __future__ import annotations

import io
import zlib

import pytest

from aspose_pdf import (
    AutoLinkResult,
    Document,
    LinkAnnotation,
    PdfLoadLimits,
    PdfResourceLimitException,
    URIAction,
)
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName, PdfNumber, PdfStream
from aspose_pdf.exceptions import AsposePdfException, PdfValidationException
from aspose_pdf.text import TextFragmentAbsorber


def _bytes(document):
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


def _targets(result):
    return [annotation.get_property("A")["URI"] for annotation in result.annotations]


def _with_content(content, *, limits=None):
    document = Document(limits=limits)
    document.pages.add().add_text("seed", 72, 740)
    document._engine_pdf._set_page_content(0, content)
    return document


@pytest.mark.parametrize(
    "text, matched, target",
    [
        ("https://example.com", "https://example.com", "https://example.com"),
        (
            "HTTP://Example.COM:8080/a",
            "HTTP://Example.COM:8080/a",
            "http://example.com:8080/a",
        ),
        ("www.example.com", "www.example.com", "https://www.example.com"),
        ("WWW.EXAMPLE.COM/a", "WWW.EXAMPLE.COM/a", "https://www.example.com/a"),
        ("<https://example.com/a>", "https://example.com/a", "https://example.com/a"),
        ("'https://example.com/a'.", "https://example.com/a", "https://example.com/a"),
        ("(https://example.com/a).", "https://example.com/a", "https://example.com/a"),
        (
            "https://example.com/a_(b)).",
            "https://example.com/a_(b)",
            "https://example.com/a_(b)",
        ),
        (
            "https://example.com/a?q=one&n=2#part!",
            "https://example.com/a?q=one&n=2#part",
            "https://example.com/a?q=one&n=2#part",
        ),
        (
            "https://example.com/u@example.org",
            "https://example.com/u@example.org",
            "https://example.com/u@example.org",
        ),
        (
            "http://127.0.0.1:8000/test",
            "http://127.0.0.1:8000/test",
            "http://127.0.0.1:8000/test",
        ),
        ("http://[::1]:8080/a]", "http://[::1]:8080/a", "http://[::1]:8080/a"),
        (
            "https://bücher.example/café",
            "https://bücher.example/café",
            "https://xn--bcher-kva.example/caf%C3%A9",
        ),
        (
            "User+tag@example.org,",
            "User+tag@example.org",
            "mailto:User+tag@example.org",
        ),
        ("<a.b@example.co.uk>.", "a.b@example.co.uk", "mailto:a.b@example.co.uk"),
        ("a?x#y@example.org", "a?x#y@example.org", "mailto:a%3Fx%23y@example.org"),
        ("a%20@example.org", "a%20@example.org", "mailto:a%2520@example.org"),
        ("mailto:hello@example.org", "hello@example.org", "mailto:hello@example.org"),
    ],
)
def test_recognition_and_uri_normalization(text, matched, target):
    with Document() as document:
        page = document.pages.add()
        page.add_text(text, 20, 700, font_size=8)
        result = page.auto_link()
        assert isinstance(result, AutoLinkResult)
        assert (result.linked_count, result.skipped_count, result.existing_count) == (
            1,
            0,
            0,
        )
        assert result.matched_count == 1
        assert _targets(result) == [target]
        annotation = result.annotations[0]
        assert isinstance(annotation, LinkAnnotation)
        assert annotation.get_property("A")["S"] == "URI"
        assert annotation.get_property("Border") == [0, 0, 0]
        assert page.extract_text() == text
        absorber = TextFragmentAbsorber(matched)
        absorber.visit(page)
        assert annotation.rect == pytest.approx(absorber.text_fragments[0].rect)


@pytest.mark.parametrize(
    "text",
    [
        "No addresses here",
        "example.com",
        "ftp://example.com/a",
        "file:///tmp/a",
        "https://",
        "https:///missing",
        "https://example.org:bad/a",
        "https://example.org:99999",
        "https://[broken]/a",
        "https://-bad.example",
        "alice@localhost",
        "alice@-example.org",
        "a..b@example.org",
        ".a@example.org",
        "a@example.org_",
        "a@example.123",
        "éAlice@example.org",
        "İ@example.org",
        "a" * 65 + "@example.org",
    ],
)
def test_unsupported_or_malformed_addresses_do_not_create_links(text):
    with Document() as document:
        page = document.pages.add()
        if "İ" in text:
            from aspose_pdf.engine.std_font_data import load_substitute_sfnt

            page.add_text(
                text, 20, 700, font_size=8, font=load_substitute_sfnt("sans-regular")
            )
        else:
            page.add_text(text, 20, 700, font_size=8)
        assert page.auto_link() == AutoLinkResult()
        assert len(page.annotations) == 0


@pytest.mark.parametrize("source", ["live", "bytes", "file", "streaming"])
def test_links_survive_round_trips_and_repeated_calls_preserve_content(
    tmp_path, source
):
    with Document() as original:
        page = original.pages.add()
        page.add_text(
            "Web https://example.com/a. Email hello@example.org", 30, 700, font_size=9
        )
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
            page = document.pages[0]
            text, content = document.extract_text(), page.content
            pixels = page.render(dpi=24, antialias=False).pixels
            result = document.auto_link()
            assert result.linked_count == 2
            assert page.content == content and document.extract_text() == text
            assert page.render(dpi=24, antialias=False).pixels == pixels
            result.annotations[0].contents = "Web link"
            assert page.annotations[0].contents == "Web link"
            repeated = page.auto_link()
            assert (
                repeated.linked_count,
                repeated.existing_count,
                repeated.matched_count,
            ) == (0, 2, 2)
            assert [fragment.text for fragment in repeated.existing_fragments] == [
                "https://example.com/a",
                "hello@example.org",
            ]
            for fragment in repeated.existing_fragments:
                assert text[fragment.start : fragment.end] == fragment.text
            output = tmp_path / "output.pdf"
            document.save(output)
            with Document(output) as reopened:
                assert _targets(
                    AutoLinkResult(tuple(reopened.pages[0].annotations))
                ) == ["https://example.com/a", "mailto:hello@example.org"]
                assert reopened.auto_link().existing_count == 2
                assert reopened.pages[0].annotations[0].contents == "Web link"
                assert reopened.extract_text() == text
        finally:
            if document is not original:
                document.dispose()


@pytest.mark.parametrize(
    "urls,emails,count",
    [(True, True, 2), (True, False, 1), (False, True, 1), (False, False, 0)],
)
def test_recognition_switches(urls, emails, count):
    with Document() as document:
        document.pages.add().add_text("www.example.org user@example.com", 72, 700)
        result = document.auto_link(urls=urls, emails=emails)
        assert result.linked_count == count and result.skipped_count == 0
        if count == 1:
            assert _targets(result)[0].startswith("https:" if urls else "mailto:")


def test_page_selection_order_repeats_and_max_count():
    with Document() as document:
        for index in range(3):
            document.pages.add().add_text(f"https://example.org/{index}", 72, 700)
        assert document.auto_link(pages=[]) == AutoLinkResult()
        result = document.auto_link(pages=(i for i in [2, 0, 2, 1]), max_count=3)
        assert _targets(result) == ["https://example.org/2", "https://example.org/0"]
        assert result.existing_count == 1 and result.matched_count == 3
        assert [len(page.annotations) for page in document.pages] == [1, 0, 1]
        assert document.pages[1].auto_link().linked_count == 1


@pytest.mark.parametrize("target", ["https://example.org", "https://different.org"])
def test_existing_links_are_preserved_by_geometry_even_with_different_targets(target):
    with Document() as document:
        page = document.pages.add()
        page.add_text("https://example.org", 72, 700)
        page.add_text("https://example.org", 72, 650)
        page.add_link((70, 695, 250, 715), URIAction(target))
        before = page.annotations[0].properties
        result = page.auto_link()
        assert (result.linked_count, result.existing_count) == (1, 1)
        assert result.existing_fragments[0].rect[1] > result.annotations[0].rect[1]
        assert page.annotations[0].properties == before
        assert len(page.annotations) == 2


def test_non_link_annotations_do_not_prevent_link_creation():
    with Document() as document:
        page = document.pages.add()
        page.add_text("https://example.org", 72, 700)
        page.annotations.add("Text", (70, 695, 250, 715), "Review")
        page.add_link((300, 300, 400, 320), URIAction("https://other.org"))
        assert page.auto_link().linked_count == 1
        assert len(page.annotations) == 3


def test_repeated_values_with_www_inside_another_url_get_distinct_correct_boxes():
    with Document() as document:
        page = document.pages.add()
        page.add_text(
            "https://www.example.org www.example.org https://www.example.org",
            20,
            700,
            font_size=8,
        )
        result = page.auto_link()
        assert result.linked_count == 3
        assert _targets(result) == ["https://www.example.org"] * 3
        assert result.annotations[0].rect[2] < result.annotations[1].rect[0]
        assert result.annotations[1].rect[2] < result.annotations[2].rect[0]


def test_uncertain_geometry_is_reported_and_counts_toward_max_count():
    content = b"BT /Missing 10 Tf 72 700 Td (https://unknown.org) Tj ET BT /F1 10 Tf 72 650 Td (known@example.org) Tj ET"
    with _with_content(content) as document:
        result = document.auto_link(max_count=1)
        assert (result.linked_count, result.skipped_count, result.matched_count) == (
            0,
            1,
            1,
        )
        fragment = result.skipped_fragments[0]
        assert fragment.text == "https://unknown.org" and fragment.page_index == 0
        assert document.extract_text()[fragment.start : fragment.end] == fragment.text
        assert fragment.rect is None and fragment.quads is None
        result = document.auto_link()
        assert (result.linked_count, result.skipped_count) == (1, 1)


def test_ambiguous_repetition_does_not_borrow_the_known_address_coordinates():
    with _with_content(
        b"BT /Missing 10 Tf 72 700 Td (https://repeat.org) Tj ET BT /F1 10 Tf 72 650 Td (https://repeat.org) Tj ET"
    ) as document:
        result = document.auto_link()
        assert (result.linked_count, result.skipped_count) == (0, 2)
        assert len(document.pages[0].annotations) == 0


def test_form_text_without_supported_geometry_is_reported_as_skipped():
    with _with_content(b"q /Fm Do Q") as document:
        engine = document._engine_pdf
        page = engine._get_page_dict(0)
        resources = engine._resolve_resources_cos(page)
        form = PdfStream(
            b"BT /F1 10 Tf 72 700 Td (https://form.org) Tj ET",
            {
                PdfName("Subtype"): PdfName("Form"),
                PdfName("BBox"): PdfArray([PdfNumber(v) for v in (0, 0, 595, 842)]),
            },
        )
        reference = engine._cos_doc.register_object(form)
        resources.mapping[PdfName("XObject")] = PdfDictionary(
            {PdfName("Fm"): reference}
        )
        result = document.auto_link()
        assert (result.linked_count, result.skipped_count) == (0, 1)
        assert result.skipped_fragments[0].text == "https://form.org"


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_transformed_text_uses_unrotated_page_coordinates(rotation):
    with _with_content(
        b"q 0 1 -1 0 400 0 cm BT /F1 10 Tf 72 200 Td (https://example.org) Tj ET Q"
    ) as document:
        page = document.pages[0]
        page.rotation = rotation
        page.crop_box = (10, 10, 500, 780)
        absorber = TextFragmentAbsorber("https://example.org")
        absorber.visit(page)
        result = page.auto_link()
        assert result.linked_count == 1
        assert result.annotations[0].rect == pytest.approx(
            absorber.text_fragments[0].rect
        )
        assert page.rotation == rotation and page.crop_box == (10, 10, 500, 780)


def test_contiguous_show_operands_and_inherited_resources_are_supported():
    with _with_content(
        b"BT /F1 10 Tf 72 700 Td [(https://) 0 (example.org)] TJ ET"
    ) as document:
        engine = document._engine_pdf
        page = engine._get_page_dict(0)
        parent = engine._resolve(page.mapping[PdfName("Parent")])
        parent.mapping[PdfName("Resources")] = page.mapping.pop(PdfName("Resources"))
        result = document.auto_link()
        assert result.linked_count == 1 and _targets(result) == ["https://example.org"]


def test_embedded_unicode_font_and_percent_encoded_uri():
    from aspose_pdf.engine.std_font_data import load_substitute_sfnt

    with Document() as document:
        page = document.pages.add()
        page.add_text(
            "Visit https://example.org/café",
            72,
            700,
            font=load_substitute_sfnt("sans-regular"),
        )
        result = page.auto_link()
        assert _targets(result) == ["https://example.org/caf%C3%A9"]
        assert result.skipped_count == 0


def test_hidden_optional_content_does_not_get_linked():
    with Document() as document:
        page = document.pages.add()
        page.add_text("https://visible.org", 72, 700)
        layer = document.layers.add("Hidden", visible=False)
        with page.layer(layer):
            page.add_text("https://hidden.org", 72, 650)
        result = page.auto_link()
        assert _targets(result) == ["https://visible.org"]


@pytest.mark.parametrize(
    "options",
    [
        {"urls": 1},
        {"urls": None},
        {"emails": "true"},
        {"max_count": True},
        {"max_count": -1},
        {"max_count": 1.5},
        {"pages": "0"},
        {"pages": [True]},
        {"pages": [-1]},
        {"pages": [0, 1]},
        {"pages": [0, "0"]},
    ],
)
def test_invalid_options_and_selections_do_not_modify_document(options):
    with Document() as document:
        document.pages.add().add_text("https://example.org", 72, 700)
        before = _bytes(document)
        with pytest.raises(PdfValidationException):
            document.auto_link(**options)
        assert _bytes(document) == before


def test_empty_and_disposed_documents_and_disabled_recognition():
    with Document() as document:
        assert document.auto_link() == AutoLinkResult()
        with pytest.raises(PdfValidationException):
            document.auto_link(pages=[], urls=None)
        page = document.pages.add()
        document.dispose()
        for target in (document, page):
            with pytest.raises(AsposePdfException, match="disposed"):
                target.auto_link()


def test_max_count_includes_existing_addresses_across_pages():
    with Document() as document:
        for index in range(2):
            document.pages.add().add_text(f"https://example.org/{index}", 72, 700)
        document.pages[0].auto_link()
        result = document.auto_link(max_count=1)
        assert (result.linked_count, result.existing_count, result.matched_count) == (
            0,
            1,
            1,
        )
        assert len(document.pages[1].annotations) == 0


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("algorithm", ["RC4", "AES-128", "AES-256"])
def test_password_protected_pdf_round_trip(tmp_path, streaming, algorithm):
    path = tmp_path / "encrypted.pdf"
    with Document() as document:
        document.pages.add().add_text("user@example.org https://example.org", 72, 700)
        document.encrypt(
            user_password="secret", owner_password="owner", algorithm=algorithm
        )
        document.save(path)
    with (Document.open_streaming if streaming else Document)(
        path, password="secret"
    ) as document:
        assert document.auto_link().linked_count == 2
        output = tmp_path / "linked.pdf"
        document.save(output)
    with Document(output, password="secret") as document:
        assert _targets(AutoLinkResult(tuple(document.pages[0].annotations))) == [
            "mailto:user@example.org",
            "https://example.org",
        ]
        assert document.auto_link().existing_count == 2


def test_streaming_selection_does_not_decode_other_pages(tmp_path, monkeypatch):
    path = tmp_path / "source.pdf"
    with Document() as document:
        for index in range(3):
            document.pages.add().add_text(f"https://example.org/{index}", 72, 700)
        document.save(path)
    with Document.open_streaming(path) as document:
        engine = document._engine_pdf
        original = engine.get_page_content

        def selected_only(index):
            assert index == 1
            return original(index)

        monkeypatch.setattr(engine, "get_page_content", selected_only)
        result = document.auto_link(pages=[1])
        assert result.linked_count == 1
        assert engine.page_contents == []
        assert result.annotations[0].get_property("A")["URI"] == "https://example.org/1"


def test_address_collection_and_token_limits_propagate_before_page_edits():
    with Document(limits=PdfLoadLimits(max_container_items=16)) as document:
        document.pages.add().add_text(
            " ".join(["https://e.org"] * 17), 20, 700, font_size=4
        )
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            document.auto_link()
        assert len(document.pages[0].annotations) == 0
    with Document(limits=PdfLoadLimits(max_content_tokens=5)) as document:
        document.pages.add()
        document._engine_pdf._set_page_content(
            0, b"BT /F1 10 Tf 72 700 Td (https://example.org) Tj ET"
        )
        with pytest.raises(PdfResourceLimitException, match="max_content_tokens"):
            document.auto_link()


@pytest.mark.parametrize("annots", [PdfDictionary({}), PdfArray([PdfNumber(42)])])
def test_malformed_annotation_arrays_are_rejected_without_orphan_links(annots):
    with Document() as document:
        document.pages.add().add_text("https://example.org", 72, 700)
        engine = document._engine_pdf
        engine._get_page_dict(0).mapping[PdfName("Annots")] = annots
        before = len(engine._cos_doc.objects)
        with pytest.raises(PdfValidationException, match=r"annot|Annots"):
            document.auto_link()
        assert len(engine._cos_doc.objects) == before


def test_existing_annotation_appearances_are_not_decoded_for_duplicate_checks():
    with Document(limits=PdfLoadLimits(max_decoded_stream_bytes=1000)) as document:
        document.pages.add().add_text("https://example.org", 72, 700)
        engine = document._engine_pdf
        appearance = PdfStream(
            zlib.compress(b" " * 20_000), {PdfName("Filter"): PdfName("FlateDecode")}
        )
        appearance_ref = engine._cos_doc.register_object(appearance)
        note = PdfDictionary(
            {
                PdfName("Subtype"): PdfName("Text"),
                PdfName("AP"): PdfDictionary({PdfName("N"): appearance_ref}),
            }
        )
        reference = engine._cos_doc.register_object(note)
        engine._get_page_dict(0).mapping[PdfName("Annots")] = PdfArray([reference])
        assert document.auto_link().linked_count == 1


def test_new_links_do_not_modify_other_pages_with_a_shared_annotation_array():
    with Document() as document:
        first, second = document.pages.add(), document.pages.add()
        first.add_text("https://first.org", 72, 700)
        second.add_text("https://second.org", 72, 700)
        first.add_link((20, 20, 50, 40), URIAction("https://manual.org"))
        engine = document._engine_pdf
        shared = engine._get_page_dict(0).mapping[PdfName("Annots")]
        engine._get_page_dict(1).mapping[PdfName("Annots")] = shared
        result = first.auto_link()
        assert result.linked_count == 1
        assert len(first.annotations) == 2 and len(second.annotations) == 1
        assert second.annotations[0].get_property("A")["URI"] == "https://manual.org"


def test_inserted_and_deleted_pages_keep_links_and_live_handles_on_the_right_page():
    with Document() as document:
        document.pages.add().add_text("https://deleted.org", 72, 700)
        document.pages.add().add_text("https://last.org", 72, 700)
        document.pages.insert(1).add_text("https://inserted.org", 72, 700)
        document.pages.delete(0)
        result = document.auto_link()
        assert _targets(result) == ["https://inserted.org", "https://last.org"]
        result.annotations[0].contents = "Inserted"
        assert document.pages[0].annotations[0].contents == "Inserted"
        assert document.pages[1].annotations[0].contents == ""


def test_disabled_recognition_and_empty_selection_do_not_read_page_content(monkeypatch):
    with Document() as document:
        document.pages.add().add_text("https://example.org", 72, 700)

        def unexpected_read(*args, **kwargs):
            raise AssertionError("Page content must not be read")

        monkeypatch.setattr(document._engine_pdf, "get_page_content", unexpected_read)
        assert document.auto_link(urls=False, emails=False) == AutoLinkResult()
        assert document.auto_link(pages=[]) == AutoLinkResult()


def test_email_option_does_not_link_an_email_spelling_inside_a_url():
    with Document() as document:
        document.pages.add().add_text("https://example.org/user@example.org", 72, 700)
        assert document.auto_link(urls=False) == AutoLinkResult()


def test_inline_images_do_not_confuse_address_geometry():
    with _with_content(
        b"q BI /W 1 /H 1 /BPC 8 /CS /RGB ID \x00\x00\x00 EI Q "
        b"BT /F1 10 Tf 72 700 Td (https://example.org) Tj ET"
    ) as document:
        assert _targets(document.auto_link()) == ["https://example.org"]


def test_duplicate_overlap_work_is_bounded_before_page_edits():
    with Document(limits=PdfLoadLimits(max_content_tokens=50)) as document:
        page = document.pages.add()
        page.add_text("https://first.org https://second.org", 72, 700)
        for index in range(30):
            page.add_link((index, 10, index + 1, 20), URIAction("https://manual.org"))
        with pytest.raises(PdfResourceLimitException, match="overlap"):
            page.auto_link()
        assert len(page.annotations) == 30
