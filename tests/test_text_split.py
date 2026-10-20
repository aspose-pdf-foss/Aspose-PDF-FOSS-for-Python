"""Text separators start independent whole-page documents without editing the source."""

from __future__ import annotations

import io
import re
from contextlib import ExitStack, contextmanager

import pytest

from aspose_pdf import (
    Document,
    FitDestination,
    GoToAction,
    NumberingStyle,
    PageLabel,
    PageStamp,
    PdfLoadLimits,
    PdfResourceLimitException,
    XYZDestination,
)
from aspose_pdf.engine.cos import PdfDictionary, PdfName, PdfStream
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfParseException,
    PdfValidationException,
)
from aspose_pdf.outlines import OutlineItem

_HEADINGS = ("Cover", "INVOICE A", "Detail A", "INVOICE B", "INVOICE C", "Detail C")
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c49444154789c63f8cfc0000003010100c9fe92ef0000000049454e44ae426082"
)


def _bytes(document):
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


def _texts(document):
    return [page.extract_text() for page in document.pages]


@contextmanager
def _parts(document, pattern="INVOICE", **options):
    with ExitStack() as stack:
        parts = document.split_by_text(pattern, **options)
        for part in parts:
            stack.enter_context(part)
        yield parts


@pytest.fixture
def source():
    with Document() as document:
        for index, heading in enumerate(_HEADINGS):
            page = document.pages.add(size=(240, 160))
            page.add_text(heading, 15, 135)
            page.add_text(f"PAGE {index}", 15, 110)
        document.info["Title"] = "Invoice batch"
        document.info["Author"] = "Test author"
        yield document


@pytest.mark.parametrize(
    "include_preamble,counts", [(True, [1, 2, 1, 2]), (False, [2, 1, 2])]
)
def test_matching_pages_start_parts_and_leave_no_gaps_or_duplicates(
    source, include_preamble, counts, tmp_path
):
    before = _bytes(source)
    original_model = source._engine_pdf._outlines_data
    with _parts(source, include_preamble=include_preamble) as parts:
        assert [part.page_count for part in parts] == counts
        expected = _texts(source)[0 if include_preamble else 1 :]
        assert [text for part in parts for text in _texts(part)] == expected
        assert source._engine_pdf._outlines_data is original_model
        for index, part in enumerate(parts):
            assert part.info["Title"] == "Invoice batch"
            assert part.info["Author"] == "Test author"
            assert part.load_limits is source.load_limits and part.file_name is None
            path = tmp_path / f"part-{index}.pdf"
            part.save(path)
            for data in (_bytes(part), path):
                with Document(data) as loaded:
                    assert _texts(loaded) == _texts(part)
                    assert loaded.validate()
        parts[-1].pages[0].add_text("OUTPUT ONLY", 15, 80)
        parts[-1].info["Title"] = "Edited part"
        assert "OUTPUT ONLY" not in source.extract_text()
    assert _bytes(source) == before


@pytest.mark.parametrize(
    "pattern,options,counts",
    [
        ("Cover", {}, [6]),
        ("Detail C", {}, [5, 1]),
        ("PAGE", {}, [1, 1, 1, 1, 1, 1]),
        ("INVOICE [AB]", {"regex": True}, [1, 2, 3]),
        ("invoice", {"case_sensitive": False}, [1, 2, 1, 2]),
        ("invoice [ab]", {"regex": True, "case_sensitive": False}, [1, 2, 3]),
        (re.compile(r"(?m)^INVOICE [AB]$"), {}, [1, 2, 3]),
        (re.compile("invoice", re.IGNORECASE), {}, [1, 2, 1, 2]),
        (re.compile("invoice [ab]"), {"case_sensitive": False}, [1, 2, 3]),
        (r"(?m)^PAGE [24]$", {"regex": True}, [2, 2, 2]),
        (r"\AINVOICE", {"regex": True}, [1, 2, 1, 2]),
        (r"INVOICE.*PAGE", {"regex": True}, None),
        (r"(?s)INVOICE.*PAGE", {"regex": True}, [1, 2, 1, 2]),
        ("invoice", {}, None),
        ("INVOICE [AB]", {}, None),
    ],
)
def test_literal_and_regex_matching_use_page_text_and_python_flags(
    source, pattern, options, counts
):
    if counts is None:
        with pytest.raises(PdfValidationException, match="No pages match"):
            source.split_by_text(pattern, **options)
    else:
        with _parts(source, pattern, **options) as parts:
            assert [part.page_count for part in parts] == counts


def test_literal_metacharacters_are_escaped_and_multiple_occurrences_share_a_boundary():
    with Document() as document:
        for text in ("Cover", "Start [A].+? Start [A].+?", "Tail", "Start Azzz"):
            document.pages.add().add_text(text, 20, 700)
        with _parts(document, "Start [A].+?") as parts:
            assert [part.page_count for part in parts] == [1, 3]


@pytest.mark.parametrize("pattern", ["^", r"(?=PAGE)", r"(?m)$"])
def test_nonempty_zero_width_patterns_make_one_boundary_per_matching_page(
    source, pattern
):
    with _parts(source, pattern, regex=True) as parts:
        assert [part.page_count for part in parts] == [1] * 6


@pytest.mark.parametrize("regex", [False, True])
def test_matches_do_not_cross_page_boundaries(regex):
    with Document() as document:
        document.pages.add().add_text("START", 20, 700)
        document.pages.add().add_text("END", 20, 700)
        with pytest.raises(PdfValidationException, match="No pages match"):
            document.split_by_text("START\nEND", regex=regex)


def test_text_split_reads_adjacent_text_operators_and_invisible_ocr():
    with Document() as document:
        document.pages.add()
        document.pages.add(
            ((0, 0, 240, 160), b"BT /F1 12 Tf 3 Tr 20 120 Td [(IN)(VOICE)] TJ ET")
        )
        document.pages.add()
        with _parts(document) as parts:
            assert [part.page_count for part in parts] == [1, 2]
            assert parts[1].pages[0].extract_text() == "INVOICE"


def test_annotations_form_values_and_images_do_not_supply_search_text():
    with Document() as document:
        page = document.pages.add()
        page.annotations.add("Text", (10, 10, 40, 40), "INVOICE")
        document.form.add_text_field("INVOICE", 0, (20, 50, 150, 80), value="INVOICE")
        document.generate_field_appearances()
        page.add_image(_PNG, 20, 100, width=20, height=20)
        with pytest.raises(PdfValidationException, match="No pages match"):
            document.split_by_text("INVOICE")


@pytest.mark.parametrize("case_sensitive", [False, True])
def test_unicode_search_uses_decoded_font_text_and_survives_round_trip(case_sensitive):
    word = "СЧЁТ"
    codes = bytes(range(1, len(word) + 1)).hex().encode()
    cmap = (
        b"begincmap 1 begincodespacerange <00> <ff> endcodespacerange "
        + str(len(word)).encode()
        + b" beginbfchar "
        + b" ".join(
            f"<{index:02x}> <{character.encode('utf-16-be').hex()}>".encode()
            for index, character in enumerate(word, 1)
        )
        + b" endbfchar endcmap"
    )
    with Document() as document:
        document.pages.add()
        document.pages.add(
            ((0, 0, 240, 160), b"BT /F1 12 Tf 20 120 Td <" + codes + b"> Tj ET")
        )
        engine = document._engine_pdf
        resources = engine._resolve_resources_cos(engine._get_page_dict(1))
        resources.mapping[PdfName("Font")] = PdfDictionary(
            {
                PdfName("F1"): PdfDictionary(
                    {
                        PdfName("Type"): PdfName("Font"),
                        PdfName("Subtype"): PdfName("Type1"),
                        PdfName("BaseFont"): PdfName("Helvetica"),
                        PdfName("ToUnicode"): engine._cos_doc.register_object(
                            PdfStream(cmap, {})
                        ),
                    }
                )
            }
        )
        with Document(_bytes(document)) as loaded:
            with _parts(
                loaded,
                word if case_sensitive else word.lower(),
                case_sensitive=case_sensitive,
            ) as parts:
                assert [part.page_count for part in parts] == [1, 1]
                assert parts[1].pages[0].extract_text() == word
                with Document(_bytes(parts[1])) as reopened:
                    assert reopened.pages[0].extract_text() == word


def test_invoked_forms_supply_text_but_hidden_layers_do_not():
    with Document() as document, Document() as stamp:
        document.pages.add()
        target = document.pages.add()
        artwork = stamp.pages.add(size=(100, 100))
        artwork.add_text("INVOICE", 10, 50)
        target.add_stamp(PageStamp(source=artwork))
        last = document.pages.add()
        layer = document.layers.add("Hidden header", visible=False)
        with last.layer(layer):
            last.add_text("INVOICE", 20, 700)
        with _parts(document) as parts:
            assert [part.page_count for part in parts] == [1, 2]
            assert parts[1].pages[0].extract_text() == "INVOICE"


def test_existing_full_text_cache_and_page_cursor_are_preserved(source):
    engine = source._engine_pdf
    cached = source.extract_text()
    engine._page_text_cursor = 3
    with _parts(source) as parts:
        assert [part.page_count for part in parts] == [1, 2, 1, 2]
    assert engine._extracted_text is cached and engine._page_text_cursor == 3


@pytest.mark.parametrize("include_preamble", [False, True])
def test_missing_matches_and_empty_documents_fail_without_import(
    source, include_preamble, monkeypatch
):
    def unexpected(*args, **kwargs):
        pytest.fail("No page should be imported without a matching start")

    monkeypatch.setattr(source._engine_pdf, "extract_pages", unexpected)
    with pytest.raises(PdfValidationException, match="No pages match"):
        source.split_by_text("absent", include_preamble=include_preamble)
    with Document() as empty:
        with pytest.raises(PdfValidationException, match="No pages match"):
            empty.split_by_text("INVOICE", include_preamble=include_preamble)


@pytest.mark.parametrize(
    "pattern,options,error,match",
    [
        (None, {}, TypeError, "pattern"),
        (b"INVOICE", {}, TypeError, "pattern"),
        (True, {}, TypeError, "pattern"),
        (123, {}, TypeError, "pattern"),
        (re.compile(b"INVOICE"), {}, TypeError, "text regular expression"),
        ("", {}, ValueError, "empty"),
        (re.compile(""), {}, ValueError, "empty"),
        ("[", {"regex": True}, re.error, "unterminated"),
        ("INVOICE", {"regex": 1}, TypeError, "regex"),
        ("INVOICE", {"case_sensitive": "yes"}, TypeError, "case_sensitive"),
        ("INVOICE", {"include_preamble": None}, TypeError, "include_preamble"),
        ("bad\ud800", {}, PdfValidationException, "Unicode"),
    ],
)
def test_invalid_options_fail_before_reading_pages_or_importing(
    source, monkeypatch, pattern, options, error, match
):
    before = _bytes(source)

    def unexpected(*args, **kwargs):
        pytest.fail("Invalid options must not read or import pages")

    with monkeypatch.context() as patch:
        patch.setattr(source._engine_pdf, "extract_page_text", unexpected)
        patch.setattr(source._engine_pdf, "extract_pages", unexpected)
        with pytest.raises(error, match=match):
            source.split_by_text(pattern, **options)
    assert _bytes(source) == before


def test_disposed_document_is_rejected(source):
    source.dispose()
    with pytest.raises(AsposePdfException, match="disposed"):
        source.split_by_text("INVOICE")


@pytest.mark.parametrize("cached_outlines", [False, True])
@pytest.mark.parametrize("action", [False, True])
def test_live_and_loaded_bookmarks_follow_current_pages_and_promote_children(
    source, cached_outlines, action
):
    root = source.outlines.add(OutlineItem("Parent", 2))
    first = XYZDestination(1, 10, 135, 2)
    root.add(OutlineItem("First", destination=GoToAction(first) if action else first))
    source.destinations["last"] = FitDestination(4)
    root.add(OutlineItem("Last", destination="last"))
    with Document(_bytes(source)) as loaded:
        if cached_outlines:
            assert loaded.outlines[0].children[0].page_index == 1
        loaded.pages.delete(0)
        loaded.destinations["last"] = XYZDestination(3, 5, 130, 1.5)
        # Keep this edit live; the operation must not flush or discard it.
        loaded.outlines.add(OutlineItem("Live edit", 2))
        original_model = loaded._engine_pdf._outlines_data
        with _parts(loaded) as parts:
            assert [part.page_count for part in parts] == [2, 1, 2]
            assert [(o.title, o.page_index) for o in parts[0].outlines] == [
                ("Parent", 1)
            ]
            expected = XYZDestination(0, 10, 135, 2)
            assert parts[0].outlines[0].children[0].destination == (
                GoToAction(expected) if action else expected
            )
            assert [(o.title, o.page_index) for o in parts[1].outlines] == [
                ("Live edit", 0)
            ]
            assert parts[2].outlines[0].destination == XYZDestination(0, 5, 130, 1.5)
            assert loaded._engine_pdf._outlines_data is original_model
        assert loaded.outlines[-1].title == "Live edit"


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("encrypted", [False, True])
def test_parts_keep_resources_boxes_fields_links_and_labels_after_source_disposal(
    source, streaming, encrypted, tmp_path
):
    source.version = "2.0"
    source.page_labels[0] = PageLabel(NumberingStyle.NUMERALS_ARABIC, "Sheet-", 10)
    source.pages[1].rotation = 90
    source.pages[1].crop_box = (5, 5, 230, 155)
    source.pages[1].add_image(_PNG, 50, 50, width=20, height=20)
    source.pages[1].annotations.add_square((20, 30, 40, 50), interior_color=(0, 0, 1))
    source.pages[1].add_link((10, 10, 30, 25), FitDestination(2))
    source.pages[1].add_link((40, 10, 60, 25), FitDestination(4))
    source.form.add_text_field("Editable", 2, (10, 60, 100, 80), value="Keep me")
    layer = source.layers.add("Private note", visible=False)
    with source.pages[2].layer(layer):
        source.pages[2].draw_rectangle(
            0, 0, 20, 20, fill_color="#000", stroke_color=None
        )
    source.add_attachment("batch.txt", b"Document attachment")
    if encrypted:
        source.encrypt("secret", "owner")
    path = tmp_path / "source.pdf"
    source.save(path)
    original = path.read_bytes()
    opener = Document.open_streaming if streaming else Document
    with opener(path, password="secret" if encrypted else None) as loaded:
        reference = loaded.pages[1].render(antialias=False)
        protected = loaded.is_encrypted
        parts = loaded.split_by_text("INVOICE")
        assert loaded.is_encrypted == protected
        if streaming:
            assert loaded._engine_pdf.page_contents == []
            assert loaded._engine_pdf._extracted_text is None
    with ExitStack() as stack:
        for part in parts:
            stack.enter_context(part)
        invoice = parts[1]
        assert invoice.pages[0].rotation == 90
        assert invoice.pages[0].crop_box == (5, 5, 230, 155)
        rendered = invoice.pages[0].render(antialias=False)
        assert (rendered.width, rendered.height, rendered.pixels) == (
            reference.width,
            reference.height,
            reference.pixels,
        )
        assert [page.label for page in invoice.pages] == ["Sheet-11", "Sheet-12"]
        assert invoice.form["Editable"].widgets[0].page_index == 1
        assert invoice.form["Editable"].value == "Keep me"
        assert not invoice.layers["Private note"].visible
        assert invoice.attachments == {} and invoice.version == "2.0"
        links = [
            note for note in invoice.pages[0].annotations if note.subtype == "Link"
        ]
        assert links[0].get_property("Dest") == FitDestination(1)
        assert links[1].get_property("Dest") == [None, "Fit"]
        assert "Editable" not in [field.name for field in parts[-1].form]
        invoice.form["Editable"].value = "Changed after source disposal"
        output = tmp_path / "invoice.pdf"
        invoice.save(output)
        with Document(output) as reopened:
            assert not reopened.is_encrypted
            assert reopened.form["Editable"].value == "Changed after source disposal"
            assert reopened.pages[0].crop_box == (5, 5, 230, 155)
    assert path.read_bytes() == original


def test_streaming_scans_each_page_in_order_without_joining_text_before_import(
    source, tmp_path, monkeypatch
):
    source.pages[2].add_image(_PNG, 30, 30, width=20, height=20)
    path = tmp_path / "stream.pdf"
    source.save(path)
    with Document.open_streaming(path) as loaded:
        engine = loaded._engine_pdf
        original = engine.extract_page_text
        original_import = engine.extract_pages
        visited = []

        def extract(index):
            visited.append(index)
            return original(index)

        def forbidden(*args, **kwargs):
            pytest.fail("Text detection must not extract whole-document text or render")

        def extract_pages(selection):
            assert visited == list(range(6))
            assert engine.page_contents == [] and engine._extracted_text is None
            if selection.start == 0:
                assert dict.__len__(engine.images) == 0
            return original_import(selection)

        monkeypatch.setattr(engine, "extract_page_text", extract)
        monkeypatch.setattr(engine, "extract_text", forbidden)
        monkeypatch.setattr(engine, "extract_pages", extract_pages)
        with _parts(loaded) as parts:
            assert visited == list(range(6))
            assert [part.page_count for part in parts] == [1, 2, 1, 2]
            assert engine.page_contents == [] and engine._extracted_text is None


def test_later_text_extraction_failure_does_not_import_earlier_matches(
    source, monkeypatch
):
    before = _bytes(source)
    original = source._engine_pdf.extract_page_text

    def extract(index):
        if index == 5:
            raise PdfParseException("Unreadable final page")
        return original(index)

    def forbidden(*args, **kwargs):
        pytest.fail("All text boundaries must be found before importing pages")

    with monkeypatch.context() as patch:
        patch.setattr(source._engine_pdf, "extract_page_text", extract)
        patch.setattr(source._engine_pdf, "extract_pages", forbidden)
        with pytest.raises(PdfParseException, match="Unreadable final page"):
            source.split_by_text("INVOICE")
    assert _bytes(source) == before


@pytest.mark.parametrize("error", [PdfValidationException, KeyboardInterrupt])
def test_partial_import_failure_disposes_results_and_restores_live_source(
    source, monkeypatch, error
):
    source.outlines.add(OutlineItem("Live", 1))
    model = source._engine_pdf._outlines_data
    original = source._engine_pdf.extract_pages
    created = []

    def extract(selection):
        if created:
            raise error("Import interrupted")
        result = original(selection)
        created.append(result)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(source._engine_pdf, "extract_pages", extract)
        with pytest.raises(error, match="Import interrupted"):
            source.split_by_text("INVOICE")
    assert source._engine_pdf._outlines_data is model
    with pytest.raises(AsposePdfException, match="disposed"):
        created[0]._ensure_not_disposed()
    assert source.outlines[0].title == "Live"
    with _parts(source) as parts:
        assert [part.page_count for part in parts] == [1, 2, 1, 2]


def test_pattern_and_page_selection_limits_fail_before_reading_text(
    source, monkeypatch
):
    def forbidden(*args, **kwargs):
        pytest.fail("Limits must be checked before reading text")

    monkeypatch.setattr(source._engine_pdf, "extract_page_text", forbidden)
    with Document(limits=PdfLoadLimits(max_object_bytes=4)) as small:
        small.pages.add()
        for pattern in ("INVOICE", "ЖЖЖ"):
            with pytest.raises(PdfResourceLimitException, match="max_object_bytes"):
                small.split_by_text(pattern)
    source._engine_pdf._load_budget.limits = PdfLoadLimits(max_container_items=5)
    with pytest.raises(PdfResourceLimitException, match="max_container_items"):
        source.split_by_text("INVOICE")


def test_streaming_content_limits_propagate_before_import(
    source, tmp_path, monkeypatch
):
    path = tmp_path / "limited.pdf"
    source.save(path)
    with Document.open_streaming(
        path, limits=PdfLoadLimits(max_content_stream_bytes=8)
    ) as loaded:

        def forbidden(*args, **kwargs):
            pytest.fail("Content limits must fail before importing parts")

        monkeypatch.setattr(loaded._engine_pdf, "extract_pages", forbidden)
        with pytest.raises(PdfResourceLimitException, match="max_content_stream_bytes"):
            loaded.split_by_text("INVOICE")


@pytest.mark.parametrize(
    "limits,limit_name",
    [
        (PdfLoadLimits(max_object_bytes=16), "max_object_bytes"),
        (PdfLoadLimits(max_decoded_stream_bytes=16), "max_decoded_stream_bytes"),
        (PdfLoadLimits(max_total_decoded_bytes=30), "max_total_decoded_bytes"),
        (PdfLoadLimits(max_objects=4), "max_objects"),
    ],
)
def test_imported_parts_are_checked_against_limits_and_disposed_on_failure(
    limits, limit_name, monkeypatch
):
    with Document(limits=limits) as document:
        for content in (b"BT (INVOICE) Tj ET", b"BT (Details) Tj ET"):
            document.pages.add(((0, 0, 100, 100), content))
        original = document._engine_pdf.extract_pages
        created = []

        def extract(selection):
            result = original(selection)
            created.append(result)
            return result

        monkeypatch.setattr(document._engine_pdf, "extract_pages", extract)
        with pytest.raises(PdfResourceLimitException, match=limit_name):
            document.split_by_text("INVOICE")
        assert created
        for result in created:
            with pytest.raises(AsposePdfException, match="disposed"):
                result._ensure_not_disposed()


def test_invalid_live_outline_tree_is_rejected_without_import(source, monkeypatch):
    root = source.outlines.add(OutlineItem("Loop", 1))
    root.children.append(root)

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid outline trees must not be imported")

    monkeypatch.setattr(source._engine_pdf, "extract_pages", forbidden)
    with pytest.raises(PdfParseException, match="cycle"):
        source.split_by_text("INVOICE")
    assert source._engine_pdf._outlines_data == []
