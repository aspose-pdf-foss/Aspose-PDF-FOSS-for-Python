"""Text patterns create bounded bookmarks without relying on heading styles."""

from __future__ import annotations

import io
import re

import pytest

from aspose_pdf import Document, OutlineItem, PdfLoadLimits, PdfResourceLimitException
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName, PdfNumber, PdfStream
from aspose_pdf.engine.std_font_data import load_substitute_sfnt
from aspose_pdf.exceptions import AsposePdfException, PdfValidationException
from aspose_pdf.interactive import URIAction, XYZDestination


def _bytes(document):
    with io.BytesIO() as output:
        document.save(output)
        return output.getvalue()


def _titles(document):
    return [(item.title, item.page_index) for item in document.outlines]


def _report(document):
    for title in ("Cover", "Chapter 1: First", "Chapter 2: Second"):
        page = document.pages.add()
        page.add_text(title, 72, 700, font_size=12)
        page.add_text("Ordinary body text at the same size.", 72, 670, font_size=12)


@pytest.mark.parametrize("source", ["live", "bytes", "file", "streaming"])
def test_regex_bookmarks_work_for_uniform_text_and_survive_round_trips(source, tmp_path):
    with Document() as original:
        _report(original)
        raw = _bytes(original)
        if source == "live":
            document = original
        elif source == "bytes":
            document = Document(raw)
        else:
            path = tmp_path / "input.pdf"
            path.write_bytes(raw)
            document = (Document.open_streaming if source == "streaming" else Document)(path)
        try:
            before = document.extract_text()
            content = [page.content for page in document.pages]
            assert document.generate_outlines_from_text(r"(?m)^Chapter \d+: (.+)$", regex=True, title_group=1) == 2
            assert _titles(document) == [("First", 1), ("Second", 2)]
            assert document.extract_text() == before
            assert [page.content for page in document.pages] == content
            assert all(not item.children for item in document.outlines)
            path = tmp_path / "output.pdf"
            document.save(path)
            for value in (path, _bytes(document)):
                with Document(value) as reopened:
                    assert reopened.validate()
                    assert _titles(reopened) == [("First", 1), ("Second", 2)]
            assert b"/Fit" in path.read_bytes()
        finally:
            if document is not original:
                document.dispose()


@pytest.mark.parametrize("pattern,options,expected", [
    ("Chapter", {}, [("Chapter", 1), ("Chapter", 2)]),
    ("chapter", {}, []),
    ("chapter", {"case_sensitive": False}, [("Chapter", 1), ("Chapter", 2)]),
    (r"Chapter \d+", {"regex": True}, [("Chapter 1", 1), ("Chapter 2", 2)]),
    (re.compile(r"chapter \d+", re.I), {}, [("Chapter 1", 1), ("Chapter 2", 2)]),
    (re.compile(r"(?m)^Chapter (?P<number>\d+)"), {"title_group": "number"}, [("1", 1), ("2", 2)]),
    (re.compile(r"chapter (\d+)"), {"case_sensitive": False, "title_group": 1}, [("1", 1), ("2", 2)]),
])
def test_literal_regex_flags_case_and_title_groups(pattern, options, expected):
    with Document() as document:
        _report(document)
        assert document.generate_outlines_from_text(pattern, **options) == len(expected)
        assert _titles(document) == expected


def test_literal_metacharacters_are_escaped():
    with Document() as document:
        document.pages.add().add_text("Section [A].+? Section Azzz", 72, 700)
        assert document.generate_outlines_from_text("Section [A].+?") == 1
        assert _titles(document) == [("Section [A].+?", 0)]


def test_page_selection_keeps_order_repetitions_and_original_indices():
    with Document() as document:
        _report(document)
        assert document.generate_outlines_from_text(
            r"Chapter \d+", regex=True, pages=(index for index in [2, 1, 2]),
        ) == 3
        assert _titles(document) == [("Chapter 2", 2), ("Chapter 1", 1), ("Chapter 2", 2)]


def test_multiple_occurrences_on_a_page_are_retained_and_matches_do_not_cross_pages():
    with Document() as document:
        document.pages.add().add_text("Start Start", 72, 700)
        document.pages.add().add_text("End", 72, 700)
        assert document.generate_outlines_from_text("Start") == 2
        assert _titles(document) == [("Start", 0), ("Start", 0)]
        assert document.generate_outlines_from_text(r"Start.*End", regex=True) == 0
        assert _titles(document) == []


def test_empty_unmatched_groups_and_whitespace_only_titles_are_skipped():
    with Document() as document:
        document.pages.add().add_text("A B C", 72, 700)
        assert document.generate_outlines_from_text(r"(A)?B|(C)", regex=True, title_group=1) == 0
        assert document.generate_outlines_from_text(r"\s+|^|$", regex=True) == 0
        assert document.generate_outlines_from_text(r"(?=(C))", regex=True, title_group=1) == 1
        assert _titles(document) == [("C", 0)]


def test_multiline_titles_collapse_whitespace_and_keep_the_matched_case():
    with Document() as document:
        page = document.pages.add()
        page.add_text("Chapter   One", 72, 700)
        page.add_text("Continued", 72, 670)
        assert document.generate_outlines_from_text(
            re.compile(r"chapter.*continued", re.S | re.I),
        ) == 1
        assert _titles(document) == [("Chapter One Continued", 0)]


def test_replacement_and_append_preserve_collection_identity_and_live_targets():
    with Document() as document:
        _report(document)
        collection = document.outlines
        existing = collection.add(OutlineItem("Website", 0, destination=URIAction("https://example.com")))
        child = existing.add(OutlineItem("Child", 2))
        assert document.generate_outlines_from_text("Chapter", replace=False) == 2
        assert document.outlines is collection
        assert collection[0] is existing and collection[0].children[0] is child
        assert existing.destination.uri == "https://example.com"
        assert document.generate_outlines_from_text("Missing", replace=False) == 0
        assert len(collection) == 3
        assert document.generate_outlines_from_text("Cover") == 1
        assert document.outlines is collection and _titles(document) == [("Cover", 0)]
        assert document.generate_outlines_from_text("Missing") == 0
        assert len(collection) == 0
        with Document(_bytes(document)) as reopened:
            assert len(reopened.outlines) == 0


def test_clip_exclusions_and_artifacts_combine_for_bookmark_creation():
    with Document() as document:
        page = document.pages.add()
        page.add_text("Chapter Header", 72, 760)
        page.add_text("Chapter Body", 72, 700)
        page.add_text("Chapter Artifact", 72, 670, artifact=True)
        page.add_text("Chapter Footer", 72, 50)
        assert document.generate_outlines_from_text(
            r"(?m)^Chapter (.+)$", regex=True, title_group=1,
            clip=(0, 650, 600, 800), exclude_regions=[(0, 740, 600, 800)], skip_artifacts=True,
        ) == 1
        assert _titles(document) == [("Body", 0)]


def test_hidden_optional_content_does_not_generate_bookmarks():
    with Document() as document:
        page = document.pages.add()
        hidden = document.layers.add("Draft", visible=False)
        with page.layer(hidden):
            page.add_text("Chapter Hidden", 72, 760)
        page.add_text("Chapter Visible", 72, 700)
        assert document.generate_outlines_from_text(r"Chapter \w+", regex=True) == 1
        assert _titles(document) == [("Chapter Visible", 0)]


@pytest.mark.parametrize("pattern,options", [
    (None, {}), (42, {}), (b"text", {}), ("", {}), (re.compile(b"text"), {}),
    ("\ud800", {}), ("[", {"regex": True}),
    ("text", {"regex": 1}), ("text", {"case_sensitive": None}),
    ("text", {"title_group": True}), ("text", {"title_group": 1}),
    ("text", {"title_group": "missing"}), ("text", {"title_group": -1}),
    ("text", {"title_group": 1.0}), ("text", {"replace": 1}),
    ("text", {"max_count": -1}), ("text", {"max_count": True}),
    ("text", {"max_count": 1.0}), ("text", {"skip_artifacts": 1}),
    ("text", {"clip": (0, 0, 0, 1)}), ("text", {"exclude_regions": [(0, 0, 0, 1)]}),
])
@pytest.mark.parametrize("empty", [False, True])
def test_invalid_options_are_rejected_before_content_or_outline_access(pattern, options, empty, monkeypatch):
    with Document() as document:
        if not empty:
            _report(document)
        monkeypatch.setattr(document._engine_pdf, "extract_page_text", lambda *a, **k: pytest.fail("Validate first"))
        with pytest.raises(PdfValidationException):
            document.generate_outlines_from_text(pattern, **options)
        assert document._outlines is None


def test_later_invalid_page_is_rejected_before_any_page_is_read(monkeypatch):
    with Document() as document:
        _report(document)
        monkeypatch.setattr(document._engine_pdf, "extract_page_text", lambda *a, **k: pytest.fail("Validate first"))
        with pytest.raises(PdfValidationException):
            document.generate_outlines_from_text("Chapter", pages=[1, 99])
        assert document._outlines is None


@pytest.mark.parametrize("replace", [False, True])
def test_later_extraction_failure_preserves_live_edits_and_engine_model(replace, monkeypatch):
    with Document() as document:
        _report(document)
        collection = document.outlines
        original = collection.add(OutlineItem("Unsaved edit", 0))
        engine = document._engine_pdf
        previous = engine._outlines_data
        extract = engine.extract_page_text

        def failing(index, **options):
            if index == 2:
                raise PdfValidationException("Cannot read later page")
            return extract(index, **options)

        monkeypatch.setattr(engine, "extract_page_text", failing)
        with pytest.raises(PdfValidationException, match="later page"):
            document.generate_outlines_from_text("Chapter", replace=replace)
        assert document.outlines is collection and list(collection) == [original]
        assert engine._outlines_data is previous


def test_max_count_stops_after_created_titles_and_does_not_read_later_pages(monkeypatch):
    with Document() as document:
        _report(document)
        extract = document._engine_pdf.extract_page_text
        read = []

        def selected(index, **options):
            assert index < 2
            read.append(index)
            return extract(index, **options)

        monkeypatch.setattr(document._engine_pdf, "extract_page_text", selected)
        assert document.generate_outlines_from_text(r"(?m)^|Chapter \d+", regex=True, max_count=1) == 1
        assert read == [0, 1]
        assert _titles(document) == [("Chapter 1", 1)]


def test_streaming_only_reads_selected_content_without_filling_text_or_page_caches(tmp_path, monkeypatch):
    path = tmp_path / "input.pdf"
    with Document() as original:
        _report(original)
        original.save(path)
    with Document.open_streaming(path) as document:
        engine = document._engine_pdf
        get = engine.get_page_content
        visited = []

        def selected(index):
            assert index == 2
            visited.append(index)
            return get(index)

        monkeypatch.setattr(engine, "get_page_content", selected)
        assert document.generate_outlines_from_text("Chapter", pages=[2, 2]) == 2
        assert visited == [2, 2] and engine.page_contents == []
        assert engine._extracted_text is None
        assert _titles(document) == [("Chapter", 2), ("Chapter", 2)]


def test_empty_document_and_empty_selection_create_no_titles():
    with Document() as document:
        assert document.generate_outlines_from_text("Chapter") == 0
        _report(document)
        assert document.generate_outlines_from_text("Chapter", pages=[]) == 0
        assert len(document.outlines) == 0


def test_disposed_document_is_rejected():
    document = Document()
    document.dispose()
    with pytest.raises(AsposePdfException, match="disposed"):
        document.generate_outlines_from_text("Chapter")


def test_projected_pdf_objects_are_checked_before_outline_mutation():
    with Document(limits=PdfLoadLimits(max_objects=12)) as document:
        document.pages.add().add_text("A A A A A A A A A A A A", 72, 700)
        before = set(document._engine_pdf._cos_doc.objects)
        with pytest.raises(PdfResourceLimitException, match="max_objects"):
            document.generate_outlines_from_text("A")
        assert document._outlines is None
        assert set(document._engine_pdf._cos_doc.objects) == before


def test_literal_escaping_is_bounded_before_page_reads(monkeypatch):
    with Document(limits=PdfLoadLimits(max_object_bytes=8)) as document:
        document.pages.add()
        monkeypatch.setattr(document._engine_pdf, "extract_page_text", lambda *a, **k: pytest.fail("Validate escaped pattern first"))
        with pytest.raises(PdfResourceLimitException, match="text bookmark pattern"):
            document.generate_outlines_from_text("........")
        assert document._outlines is None


def test_unicode_title_limit_includes_pdf_byte_order_mark(monkeypatch):
    with Document(limits=PdfLoadLimits(max_object_bytes=11)) as document:
        document.pages.add()
        monkeypatch.setattr(document._engine_pdf, "extract_page_text", lambda *a, **k: "é" * 5)
        with pytest.raises(PdfResourceLimitException, match="text bookmark title"):
            document.generate_outlines_from_text("é" * 5)
        assert document._outlines is None


def test_cumulative_title_limit_counts_actual_pdf_encoding(monkeypatch):
    with Document(limits=PdfLoadLimits(max_total_decoded_bytes=7)) as document:
        document.pages.add()
        monkeypatch.setattr(document._engine_pdf, "extract_page_text", lambda *a, **k: "é é")
        with pytest.raises(PdfResourceLimitException, match="text bookmark titles"):
            document.generate_outlines_from_text("é")
        assert document._outlines is None
    with Document(limits=PdfLoadLimits(max_total_decoded_bytes=3)) as document:
        document.pages.add()
        monkeypatch.setattr(document._engine_pdf, "extract_page_text", lambda *a, **k: "A B C")
        assert document.generate_outlines_from_text(r"\w", regex=True) == 3
        assert _titles(document) == [("A", 0), ("B", 0), ("C", 0)]


@pytest.mark.parametrize("pattern,text", [("A", "A A A A"), (r"(?=.)", "AAAA")])
def test_match_limit_includes_empty_matches_before_editing(pattern, text, monkeypatch):
    with Document(limits=PdfLoadLimits(max_container_items=3)) as document:
        document.pages.add()
        monkeypatch.setattr(document._engine_pdf, "extract_page_text", lambda *a, **k: text)
        with pytest.raises(PdfResourceLimitException, match="text bookmark matches"):
            document.generate_outlines_from_text(pattern, regex=True)
        assert document._outlines is None


def test_append_limits_include_retained_descendants_and_detect_cycles(monkeypatch):
    with Document(limits=PdfLoadLimits(max_container_items=5)) as document:
        document.pages.add()
        monkeypatch.setattr(document._engine_pdf, "extract_page_text", lambda *a, **k: "A")
        root = document.outlines.add(OutlineItem("Existing", 0))
        for index in range(4):
            root.add(OutlineItem(str(index), 0))
        with pytest.raises(PdfResourceLimitException, match="text bookmark tree items"):
            document.generate_outlines_from_text("A", replace=False)
        assert list(document.outlines) == [root] and len(root.children) == 4
        root.children[:] = [root]
        with pytest.raises(PdfValidationException, match="cycle"):
            document.generate_outlines_from_text("A", replace=False)
        assert root.children == [root]
        assert document.generate_outlines_from_text("A") == 1
        assert _titles(document) == [("A", 0)]


def test_replace_reuses_loaded_outline_slots_under_pdf_object_limit():
    with Document() as document:
        document.pages.add().add_text("A A A A A A", 72, 700)
        for index in range(4):
            document.outlines.add(OutlineItem(str(index), 0))
        raw = _bytes(document)
    with Document(raw) as source:
        cos = source._engine_pdf._cos_doc
        maximum = max(max(cos.objects), max(cos.generations))
    with Document(raw, limits=PdfLoadLimits(max_objects=maximum + 1)) as document:
        with pytest.raises(PdfResourceLimitException, match="max_objects"):
            document.generate_outlines_from_text("A", replace=False)
        assert document._outlines is None
        assert document.generate_outlines_from_text("A", max_count=1) == 1
        with Document(_bytes(document)) as reopened:
            assert _titles(reopened) == [("A", 0)]


def test_streaming_encrypted_unicode_titles_survive_save_and_reopen(tmp_path, monkeypatch):
    path = tmp_path / "encrypted.pdf"
    with Document() as document:
        font = load_substitute_sfnt("sans-regular")
        for title in ("Chapter 1: café", "Chapter 2: Résumé"):
            document.pages.add().add_text(title, 72, 700, font=font)
        document.encrypt("user", "owner")
        document.save(path)
    before = path.read_bytes()
    with Document.open_streaming(path, password="user") as document:
        engine = document._engine_pdf
        get = engine.get_page_content

        def selected(index):
            assert index == 1
            return get(index)

        monkeypatch.setattr(engine, "get_page_content", selected)
        assert document.generate_outlines_from_text(
            re.compile(r"Chapter \d+: (?P<title>.+)"), title_group="title", pages=[1],
        ) == 1
        assert _titles(document) == [("Résumé", 1)]
        assert document.is_encrypted and engine._extracted_text is None
        assert engine.page_contents == []
        monkeypatch.setattr(engine, "get_page_content", get)
        with Document(_bytes(document), password="user") as reopened:
            assert _titles(reopened) == [("Résumé", 1)]
    assert path.read_bytes() == before


def test_appending_to_unloaded_tree_keeps_named_targets_actions_styles_and_children():
    with Document() as source:
        _report(source)
        source.destinations["chapter"] = XYZDestination(1, left=30, top=700, zoom=1.2)
        root = source.outlines.add(OutlineItem(
            "Named chapter", 1, destination="chapter", is_bold=True,
            is_italic=True, color="#008000", open=True,
        ))
        root.add(OutlineItem("Website", 0, destination=URIAction("https://example.com")))
        raw = _bytes(source)
    with Document(raw) as document:
        assert document._outlines is None
        assert document.generate_outlines_from_text("Chapter", replace=False) == 2
        old = document.outlines[0]
        assert old.title == "Named chapter" and old.destination_name == "chapter"
        assert old.is_bold and old.is_italic and old.open
        assert old.color == pytest.approx((0, 128 / 255, 0), abs=1e-6)
        assert old.children[0].destination.uri == "https://example.com"
        with Document(_bytes(document)) as reopened:
            assert _titles(reopened) == [("Named chapter", 1), ("Chapter", 1), ("Chapter", 2)]
            assert reopened.outlines[0].destination_name == "chapter"
            assert reopened.outlines[0].children[0].destination.uri == "https://example.com"


def test_appending_loaded_targets_tracks_page_reordering_before_search():
    with Document() as source:
        _report(source)
        source.outlines.add(OutlineItem("Existing", 1, destination=XYZDestination(1, top=700)))
        raw = _bytes(source)
    with Document(raw) as document:
        document.pages.insert(0)
        assert document.generate_outlines_from_text("Chapter", replace=False) == 2
        assert _titles(document) == [("Existing", 2), ("Chapter", 2), ("Chapter", 3)]
        with Document(_bytes(document)) as reopened:
            assert _titles(reopened) == [("Existing", 2), ("Chapter", 2), ("Chapter", 3)]


def test_search_keeps_cached_text_cursor_metadata_layout_and_page_content():
    with Document() as document:
        _report(document)
        document.info["Title"] = "Report"
        page = document.pages[1]
        page.rotation = 90
        page.crop_box = (50, 50, 400, 740)
        content = page.content
        text = document.extract_text()
        engine = document._engine_pdf
        assert engine.get_next_page_text().startswith("Cover")
        assert document.generate_outlines_from_text("Chapter") == 2
        assert engine._extracted_text == text
        assert engine.get_next_page_text().startswith("Chapter 1")
        assert page.content == content and page.rotation == 90
        assert page.crop_box == (50, 50, 400, 740)
        assert document.info["Title"] == "Report"


def test_bookmarks_are_not_created_from_widget_values_annotations_or_existing_titles():
    with Document() as document:
        page = document.pages.add()
        page.add_text("Ordinary body", 72, 700)
        page.annotations.add("Text", (10, 10, 40, 40), "Chapter Annotation")
        document.form.add_text_field("chapter", 0, (20, 50, 150, 80), value="Chapter Field")
        document.generate_field_appearances()
        original = document.outlines.add(OutlineItem("Chapter Existing", 0))
        assert document.generate_outlines_from_text("Chapter", replace=False) == 0
        assert list(document.outlines) == [original]


def _add_form(document, content):
    engine = document._engine_pdf
    resources = engine._resolve_resources_cos(engine._get_page_dict(0))
    form = PdfStream(content, {
        PdfName("Subtype"): PdfName("Form"),
        PdfName("BBox"): PdfArray([PdfNumber(value) for value in (0, 0, 600, 800)]),
        PdfName("Resources"): PdfDictionary({PdfName("Font"): resources.mapping[PdfName("Font")]}),
    })
    resources.mapping[PdfName("XObject")] = PdfDictionary({
        PdfName("Form"): engine._cos_doc.register_object(form),
    })
    return form


def test_invoked_forms_actual_text_and_adjacent_operands_can_supply_titles():
    with Document() as document:
        page = document.pages.add()
        page.add_text("Seed", 72, 700)
        engine = document._engine_pdf
        _add_form(document, b"BT /F1 12 Tf 72 760 Td [(Chapter) ( Form)] TJ ET")
        name = engine._register_actual_text_property(0, "Chapter Logical")
        engine._set_page_content(0, (
            b"/Form Do " + f"/Span /{name} BDC ".encode()
            + b"BT /F1 12 Tf 72 700 Td (Glyphs) Tj ET EMC"
        ))
        assert document.generate_outlines_from_text(r"Chapter (?:Form|Logical)", regex=True, skip_artifacts=True) == 2
        assert _titles(document) == [("Chapter Form", 0), ("Chapter Logical", 0)]


def test_nonempty_exclusions_avoid_decoding_unused_form_streams(monkeypatch):
    with Document() as document:
        page = document.pages.add()
        page.add_text("Chapter Main", 72, 700)
        engine = document._engine_pdf
        form = _add_form(document, b"not compressed")
        form.mapping[PdfName("Filter")] = PdfName("FlateDecode")
        monkeypatch.setattr(engine, "_decode_cos_stream", lambda *a, **k: pytest.fail("Do not decode unused forms"))
        assert document.generate_outlines_from_text(
            r"Chapter \w+", regex=True, exclude_regions=[(0, 0, 600, 50)],
        ) == 1
        assert _titles(document) == [("Chapter Main", 0)]


def test_region_generator_is_snapshotted_for_all_selected_pages():
    with Document() as document:
        for title in ("First", "Second"):
            page = document.pages.add()
            page.add_text("Chapter Header", 72, 760)
            page.add_text("Chapter " + title, 72, 700)
        assert document.generate_outlines_from_text(
            r"Chapter (\w+)", regex=True, title_group=1,
            exclude_regions=(box for box in [(0, 740, 600, 800)]),
        ) == 2
        assert _titles(document) == [("First", 0), ("Second", 1)]


def test_non_latin_to_unicode_text_creates_a_unicode_bookmark_title():
    title = "СЧЁТ 12"
    codes = bytes(range(1, len(title) + 1))
    cmap = (
        b"begincmap 1 begincodespacerange <00> <ff> endcodespacerange "
        + str(len(title)).encode() + b" beginbfchar "
        + b" ".join(
            f"<{index:02x}> <{value.encode('utf-16-be').hex()}>".encode()
            for index, value in enumerate(title, 1)
        )
        + b" endbfchar endcmap"
    )
    with Document() as source:
        source.pages.add(((0, 0, 600, 800), b"BT /F1 12 Tf 72 700 Td <" + codes.hex().encode() + b"> Tj ET"))
        engine = source._engine_pdf
        resources = engine._resolve_resources_cos(engine._get_page_dict(0))
        resources.mapping[PdfName("Font")] = PdfDictionary({
            PdfName("F1"): PdfDictionary({
                PdfName("Type"): PdfName("Font"), PdfName("Subtype"): PdfName("Type1"),
                PdfName("BaseFont"): PdfName("Helvetica"),
                PdfName("ToUnicode"): engine._cos_doc.register_object(PdfStream(cmap, {})),
            }),
        })
        raw = _bytes(source)
    with Document(raw) as document:
        assert document.generate_outlines_from_text(r"СЧЁТ \d+", regex=True) == 1
        assert _titles(document) == [(title, 0)]
        with Document(_bytes(document)) as reopened:
            assert _titles(reopened) == [(title, 0)]


@pytest.mark.parametrize("change,expected", [("insert", 2), ("delete_before", 0)])
def test_generated_bookmarks_follow_the_matched_page_when_page_order_changes(change, expected):
    with Document() as document:
        document.pages.add().add_text("Cover", 72, 700)
        document.pages.add().add_text("Chapter One", 72, 700)
        assert document.generate_outlines_from_text("Chapter One") == 1
        if change == "insert":
            document.pages.insert(0)
        else:
            document.pages.delete(0)
        with Document(_bytes(document)) as reopened:
            assert _titles(reopened) == [("Chapter One", expected)]
            assert "Chapter One" in reopened.pages[expected].extract_text()
