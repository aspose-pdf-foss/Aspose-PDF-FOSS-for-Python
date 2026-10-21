"""Visually blank separators divide independent PDF parts without editing sources."""

from __future__ import annotations

import io
import weakref
from contextlib import ExitStack, contextmanager
from itertools import product

import pytest

from aspose_pdf import (
    Document,
    FitDestination,
    GoToAction,
    NumberingStyle,
    OutlineItem,
    PageLabel,
    PdfLoadLimits,
    PdfResourceLimitException,
    RasterizedPage,
    XYZDestination,
)
from aspose_pdf.engine import blank_split
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfParseException,
    PdfValidationException,
)
from aspose_pdf.pages import Page


def _bytes(document):
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _batch(document, pattern):
    for index, kind in enumerate(pattern):
        page = document.pages.add(size=(40, 40))
        if kind != "B":
            page.draw_rectangle(2, 2, 8, 8, fill_color="#000", stroke_color=None)
            page.add_text(f"PAGE {index}", 2, 25, font_size=5)
    if pattern:
        document.page_labels[0] = PageLabel(
            NumberingStyle.NUMERALS_ARABIC, "Source-", 1
        )


def _indices(part):
    return [int(page.label.removeprefix("Source-")) - 1 for page in part.pages]


@contextmanager
def _parts(document, **options):
    with ExitStack() as stack:
        parts = document.split_by_blank_pages(**options)
        for part in parts:
            stack.enter_context(part)
        yield parts


@pytest.mark.parametrize(
    "pattern,discarded,kept",
    [
        ("", [], []),
        ("B", [], [[0]]),
        ("BBB", [], [[0, 1, 2]]),
        ("A", [[0]], [[0]]),
        ("AA", [[0, 1]], [[0, 1]]),
        ("BAB", [[1]], [[0, 1, 2]]),
        ("ABA", [[0], [2]], [[0, 1], [2]]),
        ("ABBA", [[0], [3]], [[0, 1, 2], [3]]),
        ("BABBABB", [[1], [4]], [[0, 1, 2, 3], [4, 5, 6]]),
        ("ABAB", [[0], [2]], [[0, 1], [2, 3]]),
        (
            "BBAABBBABBAABB",
            [[2, 3], [7], [10, 11]],
            [[0, 1, 2, 3, 4, 5, 6], [7, 8, 9], [10, 11, 12, 13]],
        ),
    ],
)
@pytest.mark.parametrize("keep_separators", [False, True])
def test_blank_runs_leading_trailing_and_all_blank_inputs(
    pattern, discarded, kept, keep_separators
):
    with Document() as document:
        _batch(document, pattern)
        with _parts(document, keep_separators=keep_separators) as parts:
            assert [_indices(part) for part in parts] == (
                kept if keep_separators else discarded
            )
            assert all(part.page_count > 0 for part in parts)
            if keep_separators:
                assert [index for part in parts for index in _indices(part)] == list(
                    range(len(pattern))
                )


def test_default_drops_separators_and_returns_independent_round_trippable_parts(
    tmp_path,
):
    with Document() as document:
        _batch(document, "ABBAA")
        document.info["Title"] = "Scan batch"
        document.info["Author"] = "Original author"
        before = _bytes(document)
        with _parts(document) as parts:
            assert [_indices(part) for part in parts] == [[0], [3, 4]]
            for index, part in enumerate(parts):
                assert (
                    part.load_limits is document.load_limits and part.file_name is None
                )
                assert (
                    part.info["Title"] == "Scan batch"
                    and part.info["Author"] == "Original author"
                )
                path = tmp_path / f"part-{index}.pdf"
                part.save(path)
                for source in (path, _bytes(part)):
                    with Document(source) as loaded:
                        assert _indices(loaded) == _indices(part) and loaded.validate()
                        assert loaded.extract_text() == part.extract_text()
            parts[0].pages[0].add_text("OUTPUT ONLY", 2, 15, font_size=3)
            parts[0].info["Title"] = "Edited"
            assert "OUTPUT ONLY" not in document.extract_text()
        assert _bytes(document) == before


@pytest.mark.parametrize(
    "kind",
    [
        "white_scan",
        "near_white_scan",
        "noise",
        "hidden_ocr",
        "cropped_content",
        "hidden_layer",
    ],
)
def test_visual_detection_uses_images_visible_crop_and_layers_without_ocr(kind):
    with Document() as document:
        _batch(document, "ABA")
        page = document.pages[1]
        if kind in {"white_scan", "near_white_scan"}:
            rgb = (255, 255, 255) if kind == "white_scan" else (250, 250, 250)
            page.add_image(
                RasterizedPage(1, 1, bytes(rgb)).to_png(), 0, 0, width=40, height=40
            )
        elif kind == "noise":
            page.draw_rectangle(0, 0, 1, 1, fill_color="#000", stroke_color=None)
        elif kind == "hidden_ocr":
            document._engine_pdf._set_page_content(
                1, b"BT /F1 5 Tf 3 Tr 1 20 Td (Invisible OCR) Tj ET"
            )
        elif kind == "cropped_content":
            page.draw_rectangle(0, 0, 5, 5, fill_color="#000", stroke_color=None)
            page.crop_box = (10, 10, 30, 30)
            page.rotation = 90
        elif kind == "hidden_layer":
            layer = document.layers.add("Hidden", visible=False)
            with page.layer(layer):
                page.draw_rectangle(0, 0, 40, 40, fill_color="#000", stroke_color=None)
        options = dict(max_ink_ratio=1 / 1600) if kind == "noise" else {}
        with _parts(document, **options) as parts:
            assert [_indices(part) for part in parts] == [[0], [2]]
        if kind == "hidden_ocr":
            assert "Invisible OCR" in page.extract_text()
        if kind == "noise":
            with _parts(document, max_ink_ratio=1 / 1600 - 1e-8) as parts:
                assert [_indices(part) for part in parts] == [[0, 1, 2]]


def test_white_threshold_is_shared_with_blank_detection():
    with Document() as document:
        _batch(document, "ABA")
        page = document.pages[1]
        page.draw_rectangle(0, 0, 40, 40, fill_color="#f9ffff", stroke_color=None)
        with _parts(document) as parts:
            assert [_indices(part) for part in parts] == [[0, 1, 2]]
        with _parts(document, white_threshold=249) as parts:
            assert [_indices(part) for part in parts] == [[0], [2]]


@pytest.mark.parametrize("hidden", [False, True])
def test_annotation_appearances_count_unless_excluded(hidden):
    with Document() as document:
        _batch(document, "ABA")
        document.pages[1].annotations.add(
            "Square",
            (5, 5, 25, 25),
            "Review",
            properties={"IC": [0, 0, 0], "F": 2 if hidden else 0},
        )
        with _parts(document) as parts:
            assert [_indices(part) for part in parts] == (
                [[0], [2]] if hidden else [[0, 1, 2]]
            )
        with _parts(document, include_annotations=False) as parts:
            assert [_indices(part) for part in parts] == [[0], [2]]


def test_form_appearance_can_prevent_a_blank_separator():
    with Document() as document:
        _batch(document, "ABA")
        document.form.add_text_field("Filled", 1, (2, 10, 38, 30), value="Visible")
        with _parts(document) as parts:
            assert [_indices(part) for part in parts] == [[0, 1, 2]]
        with _parts(document, include_annotations=False) as parts:
            assert [_indices(part) for part in parts] == [[0], [2]]


@pytest.mark.parametrize("action", [False, True])
@pytest.mark.parametrize("keep_separators", [False, True])
def test_live_bookmarks_promote_children_and_remap_local_targets(
    action, keep_separators
):
    with Document() as document:
        _batch(document, "ABBAA")
        root = document.outlines.add(OutlineItem("Separator", 1))
        first = XYZDestination(0, 1, 30, 2)
        root.add(
            OutlineItem("First", destination=GoToAction(first) if action else first)
        )
        document.destinations["last"] = FitDestination(3)
        root.add(OutlineItem("Last", destination="last"))
        model = document._engine_pdf._outlines_data
        with _parts(document, keep_separators=keep_separators) as parts:
            assert len(parts) == 2
            first_root = parts[0].outlines[0]
            first_item = first_root.children[0] if keep_separators else first_root
            assert first_item.title == "First"
            assert first_item.destination == (GoToAction(first) if action else first)
            assert parts[1].outlines[0].title == "Last"
            assert parts[1].outlines[0].page_index == 0
            assert document._engine_pdf._outlines_data is model
            assert document.outlines[0] is root and root.page_index == 1


@pytest.mark.parametrize("source_kind", ["bytes", "file", "streaming"])
@pytest.mark.parametrize("encrypted", [False, True])
def test_imported_resources_and_metadata_survive_source_disposal(
    tmp_path, source_kind, encrypted
):
    path = tmp_path / "source.pdf"
    with Document() as source:
        _batch(source, "AABBAA")
        source.version = "2.0"
        source.info["Title"] = "Original batch"
        source.add_attachment("note.txt", b"Document-level data")
        source.pages[4].crop_box = (1, 1, 39, 39)
        source.pages[4].rotation = 90
        source.pages[4].add_image(
            RasterizedPage(1, 1, b"\xff\x00\x00").to_png(), 10, 10, width=5, height=5
        )
        source.pages[4].add_link((1, 1, 5, 5), FitDestination(5))
        source.pages[4].add_link((6, 1, 10, 5), FitDestination(0))
        source.form.add_text_field("Editable", 5, (2, 10, 38, 20), value="Keep")
        layer = source.layers.add("Private", visible=False)
        with source.pages[5].layer(layer):
            source.pages[5].draw_rectangle(
                0, 0, 40, 40, fill_color="#f00", stroke_color=None
            )
        source.outlines.add(
            OutlineItem("Second part", destination=XYZDestination(4, 1, 35, 1.5))
        )
        if encrypted:
            source.encrypt("secret", "owner")
        source.save(path)
    original_bytes = path.read_bytes()
    opener = Document.open_streaming if source_kind == "streaming" else Document
    argument = original_bytes if source_kind == "bytes" else path
    with opener(argument, password="secret" if encrypted else None) as loaded:
        protected = loaded.is_encrypted
        reference = loaded.pages[4].render(antialias=False)
        parts = loaded.split_by_blank_pages()
        assert loaded.is_encrypted is protected and loaded.attachments == {
            "note.txt": b"Document-level data"
        }
        if source_kind == "streaming":
            assert loaded._engine_pdf.page_contents == []
            assert loaded._engine_pdf._extracted_text is None
    with ExitStack() as stack:
        for part in parts:
            stack.enter_context(part)
        assert [_indices(part) for part in parts] == [[0, 1], [4, 5]]
        part = parts[1]
        assert part.info["Title"] == "Original batch" and part.version == "2.0"
        assert part.attachments == {} and not part.is_encrypted
        assert part.pages[0].crop_box == (1, 1, 39, 39) and part.pages[0].rotation == 90
        assert part.pages[0].render(antialias=False).pixels == reference.pixels
        assert part.form["Editable"].widgets[0].page_index == 1
        assert (
            part.form["Editable"].value == "Keep" and not part.layers["Private"].visible
        )
        assert part.outlines[0].destination == XYZDestination(0, 1, 35, 1.5)
        links = [
            annotation
            for annotation in part.pages[0].annotations
            if annotation.subtype == "Link"
        ]
        assert links[0].get_property("Dest") == FitDestination(1)
        assert links[1].get_property("Dest") == [None, "Fit"]
        part.form["Editable"].value = "Edited after close"
        output = tmp_path / "part.pdf"
        part.save(output)
        with Document(output) as reopened:
            assert reopened.form["Editable"].value == "Edited after close"
            assert reopened.outlines[0].destination == XYZDestination(0, 1, 35, 1.5)
            assert _indices(reopened) == [4, 5]
    assert path.read_bytes() == original_bytes


@pytest.mark.parametrize(
    "name,value",
    [
        ("keep_separators", 1),
        ("keep_separators", None),
        ("dpi", 0),
        ("dpi", -1),
        ("dpi", True),
        ("dpi", "72"),
        ("dpi", float("nan")),
        ("dpi", float("inf")),
        ("dpi", 10**1000),
        ("dpi", 5e-324),
        ("white_threshold", 0),
        ("white_threshold", 256),
        ("white_threshold", True),
        ("white_threshold", 250.5),
        ("white_threshold", "250"),
        ("max_ink_ratio", -0.1),
        ("max_ink_ratio", 1.1),
        ("max_ink_ratio", True),
        ("max_ink_ratio", "0"),
        ("max_ink_ratio", float("nan")),
        ("max_ink_ratio", float("inf")),
        ("include_annotations", 0),
        ("include_annotations", None),
    ],
)
@pytest.mark.parametrize("empty", [False, True])
def test_all_options_are_validated_before_scanning_or_importing(
    monkeypatch, name, value, empty
):
    with Document() as document:
        if not empty:
            _batch(document, "ABA")

        def forbidden(*args, **kwargs):
            pytest.fail("invalid options must not scan or import")

        monkeypatch.setattr(blank_split, "_is_blank", forbidden)
        monkeypatch.setattr(document._engine_pdf, "extract_pages", forbidden)
        with pytest.raises(PdfValidationException):
            document.split_by_blank_pages(**{name: value})


@pytest.mark.parametrize(
    "limits,match",
    [
        (PdfLoadLimits(max_container_items=2), "max_container_items"),
        (PdfLoadLimits(max_pages=2), "max_pages"),
    ],
)
def test_page_count_limits_are_checked_before_rendering(monkeypatch, limits, match):
    with Document(limits=limits) as document:
        for _ in range(3):
            document.pages.add(size=(10, 10))
        monkeypatch.setattr(
            blank_split,
            "_is_blank",
            lambda *a, **kw: pytest.fail("check page limits first"),
        )
        with pytest.raises(PdfResourceLimitException, match=match):
            document.split_by_blank_pages()


def test_raster_limit_error_occurs_before_any_part_is_imported(monkeypatch):
    with Document(limits=PdfLoadLimits(max_raster_pixels=399)) as document:
        document.pages.add(size=(20, 20))
        monkeypatch.setattr(
            document._engine_pdf,
            "extract_pages",
            lambda *a, **kw: pytest.fail("rendering must finish first"),
        )
        with pytest.raises(PdfResourceLimitException, match="max_raster_pixels"):
            document.split_by_blank_pages()


def test_scan_is_complete_before_import_and_releases_rasters_between_pages(monkeypatch):
    with Document() as document:
        _batch(document, "ABAABBA")
        original_render, original_import = (
            Page.render,
            document._engine_pdf.extract_pages,
        )
        visited, references = [], []

        def render(page, **options):
            assert all(reference() is None for reference in references)
            assert options == dict(
                dpi=96.0,
                background=(255, 255, 255),
                antialias=False,
                draw_annotations=False,
            )
            visited.append(page.index)
            result = original_render(page, **options)
            references.append(weakref.ref(result))
            return result

        def extract(selection):
            assert visited == list(range(7))
            assert all(reference() is None for reference in references)
            return original_import(selection)

        monkeypatch.setattr(Page, "render", render)
        monkeypatch.setattr(document._engine_pdf, "extract_pages", extract)
        with _parts(document, dpi=96, include_annotations=False) as parts:
            assert [_indices(part) for part in parts] == [[0], [2, 3], [6]]


def test_late_render_failure_does_not_import_earlier_parts(monkeypatch):
    with Document() as document:
        _batch(document, "ABABA")
        before = _bytes(document)
        original = Page.render

        def render(page, **options):
            if page.index == 4:
                raise PdfParseException("Unreadable last page")
            return original(page, **options)

        with monkeypatch.context() as patch:
            patch.setattr(Page, "render", render)
            patch.setattr(
                document._engine_pdf,
                "extract_pages",
                lambda *a, **kw: pytest.fail("must scan every page first"),
            )
            with pytest.raises(PdfParseException, match="Unreadable last page"):
                document.split_by_blank_pages()
        assert _bytes(document) == before


@pytest.mark.parametrize("error", [PdfValidationException, KeyboardInterrupt])
def test_partial_import_failure_disposes_earlier_parts_and_restores_source(
    monkeypatch, error
):
    with Document() as document:
        _batch(document, "ABA")
        document.outlines.add(OutlineItem("Live", 0))
        model = document._engine_pdf._outlines_data
        original_import = document._engine_pdf.extract_pages
        created = []

        def extract(selection):
            if created:
                raise error("Import interrupted")
            result = original_import(selection)
            created.append(result)
            return result

        with monkeypatch.context() as patch:
            patch.setattr(document._engine_pdf, "extract_pages", extract)
            with pytest.raises(error, match="Import interrupted"):
                document.split_by_blank_pages()
        assert document._engine_pdf._outlines_data is model
        assert document.outlines[0].title == "Live"
        assert created
        with pytest.raises(AsposePdfException, match="disposed"):
            created[0]._ensure_not_disposed()
        with _parts(document) as parts:
            assert [_indices(part) for part in parts] == [[0], [2]]


@pytest.mark.parametrize(
    "limits,match",
    [
        (PdfLoadLimits(max_object_bytes=16), "max_object_bytes"),
        (PdfLoadLimits(max_decoded_stream_bytes=16), "max_decoded_stream_bytes"),
        (PdfLoadLimits(max_total_decoded_bytes=30), "max_total_decoded_bytes"),
        (PdfLoadLimits(max_objects=4), "max_objects"),
    ],
)
def test_imported_results_obey_source_limits_and_are_disposed_on_failure(
    monkeypatch, limits, match
):
    with Document(limits=limits) as document:
        for content in (b"BT (First page) Tj ET", b"BT (Second page) Tj ET"):
            document.pages.add(((0, 0, 10, 10), content))
        # Isolate importer limits from the renderer's own independent limits.
        monkeypatch.setattr(blank_split, "_is_blank", lambda *a: False)
        original = document._engine_pdf.extract_pages
        created = []

        def extract(selection):
            result = original(selection)
            created.append(result)
            return result

        monkeypatch.setattr(document._engine_pdf, "extract_pages", extract)
        with pytest.raises(PdfResourceLimitException, match=match):
            document.split_by_blank_pages()
        assert created
        for result in created:
            with pytest.raises(AsposePdfException, match="disposed"):
                result._ensure_not_disposed()


def test_invalid_live_outline_tree_is_rejected_before_import(monkeypatch):
    with Document() as document:
        _batch(document, "ABA")
        root = document.outlines.add(OutlineItem("Loop", 0))
        root.children.append(root)
        model = document._engine_pdf._outlines_data
        monkeypatch.setattr(
            document._engine_pdf,
            "extract_pages",
            lambda *a, **kw: pytest.fail("must validate outlines first"),
        )
        with pytest.raises(PdfParseException, match="cycle"):
            document.split_by_blank_pages()
        assert document._engine_pdf._outlines_data is model


def test_streaming_content_limits_fail_before_import(tmp_path, monkeypatch):
    path = tmp_path / "source.pdf"
    with Document() as source:
        _batch(source, "ABA")
        source.save(path)
    with Document.open_streaming(
        path, limits=PdfLoadLimits(max_content_stream_bytes=8)
    ) as document:
        monkeypatch.setattr(
            document._engine_pdf,
            "extract_pages",
            lambda *a, **kw: pytest.fail("content limits must fail before import"),
        )
        with pytest.raises(PdfResourceLimitException, match="max_content_stream_bytes"):
            document.split_by_blank_pages()


def test_existing_text_cache_cursor_and_outline_model_are_unchanged():
    with Document() as document:
        _batch(document, "ABA")
        document.outlines.add(OutlineItem("First", 0))
        cached = document.extract_text()
        document._engine_pdf._page_text_cursor = 2
        model = document._engine_pdf._outlines_data
        with _parts(document) as parts:
            assert len(parts) == 2
        assert document._engine_pdf._extracted_text is cached
        assert document._engine_pdf._page_text_cursor == 2
        assert document._engine_pdf._outlines_data is model


def test_disposed_document_is_rejected():
    document = Document()
    document.dispose()
    with pytest.raises(AsposePdfException, match="disposed"):
        document.split_by_blank_pages()


def test_all_short_blank_patterns_agree_with_an_independent_grouping_model(monkeypatch):
    with Document() as document:
        _batch(document, "AAAAAA")
        original_import = document._engine_pdf.extract_pages
        imported = []

        def extract(selection):
            imported.append(list(selection))
            return original_import(selection)

        monkeypatch.setattr(document._engine_pdf, "extract_pages", extract)
        for flags in product((False, True), repeat=6):
            monkeypatch.setattr(
                blank_split, "_is_blank", lambda page, options: flags[page.index]
            )
            # Build reference page groups from explicit transitions.
            expected = []
            for index, blank in enumerate(flags):
                if not blank:
                    if index == 0 or flags[index - 1]:
                        expected.append([])
                    expected[-1].append(index)
            imported.clear()
            with _parts(document) as parts:
                assert imported == expected
                assert [_indices(part) for part in parts] == expected
            kept = []
            for index, blank in enumerate(flags):
                if (
                    not blank
                    and index
                    and flags[index - 1]
                    and any(not value for value in flags[:index])
                ):
                    kept.append([])
                if not kept:
                    kept.append([])
                kept[-1].append(index)
            imported.clear()
            with _parts(document, keep_separators=True) as parts:
                assert imported == kept
                assert [_indices(part) for part in parts] == kept


def test_loaded_cached_bookmarks_observe_page_changes_and_unsaved_edits():
    with Document() as source:
        _batch(source, "ABBAA")
        parent = source.outlines.add(OutlineItem("Separator", 1))
        source.destinations["last"] = FitDestination(3)
        parent.add(OutlineItem("Last", destination="last"))
        data = _bytes(source)
    with Document(data) as loaded:
        last = loaded.outlines[0].children[0]
        assert last.page_index == 3
        loaded.pages.delete(0)
        loaded.destinations["last"] = XYZDestination(3, 1, 30, 2)
        last.title = "Edited last"
        last.is_bold = True
        loaded.outlines.add(OutlineItem("Live", 3))
        model = loaded._engine_pdf._outlines_data
        expected_labels = [loaded.pages[index].label for index in (2, 3)]
        with _parts(loaded) as parts:
            assert len(parts) == 1
            assert [page.label for page in parts[0].pages] == expected_labels
            assert [(item.title, item.page_index) for item in parts[0].outlines] == [
                ("Edited last", 1),
                ("Live", 1),
            ]
            assert parts[0].outlines[0].destination == XYZDestination(1, 1, 30, 2)
            assert parts[0].outlines[0].is_bold
            assert loaded._engine_pdf._outlines_data is model
        assert loaded.outlines[0].children[0] is last


def test_post_import_validation_failure_disposes_current_and_previous_parts(
    monkeypatch,
):
    with Document() as document:
        _batch(document, "ABA")
        model = document._engine_pdf._outlines_data
        original_import = document._engine_pdf.extract_pages
        original_refresh = blank_split._refresh_targets
        created = []

        def extract(selection):
            result = original_import(selection)
            created.append(result)
            return result

        def refresh(result):
            assert document._engine_pdf._outlines_data is model
            if len(created) == 2:
                raise PdfParseException("Invalid imported target")
            original_refresh(result)

        monkeypatch.setattr(document._engine_pdf, "extract_pages", extract)
        monkeypatch.setattr(blank_split, "_refresh_targets", refresh)
        with pytest.raises(PdfParseException, match="Invalid imported target"):
            document.split_by_blank_pages()
        assert len(created) == 2
        assert document._engine_pdf._outlines_data is model
        for result in created:
            with pytest.raises(AsposePdfException, match="disposed"):
                result._ensure_not_disposed()
