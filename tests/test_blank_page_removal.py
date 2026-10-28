"""Blank-page cleanup preserves retained pages and their navigation."""

from __future__ import annotations

import io

import pytest

from aspose_pdf import (
    Document,
    FitDestination,
    NumberingStyle,
    OutlineItem,
    PageLabel,
    PdfLoadLimits,
    PdfResourceLimitException,
    URIAction,
    XYZDestination,
)
from aspose_pdf.engine.image_export import write_png
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfParseException,
    PdfValidationException,
)
from aspose_pdf.pages import Page


def _document():
    document = Document()
    for index in range(6):
        page = document.pages.add(size=(64, 64))
        if index in (1, 3, 5):
            page.add_text(f"P{index}", 5, 30, font_size=10)
    return document


def _bytes(document):
    with io.BytesIO() as output:
        document.save(output)
        return output.getvalue()


@pytest.mark.parametrize("source_kind", ["live", "bytes", "file", "streaming"])
def test_removes_blank_pages_and_round_trips_with_original_indices(
    source_kind, tmp_path
):
    with _document() as template:
        raw = _bytes(template)
    path = tmp_path / "input.pdf"
    path.write_bytes(raw)
    if source_kind == "live":
        document = _document()
    elif source_kind == "bytes":
        document = Document(raw)
    else:
        document = (
            Document.open_streaming if source_kind == "streaming" else Document
        )(path)
    with document:
        assert document.remove_blank_pages() == [0, 2, 4]
        assert document.page_count == 3
        assert [page.extract_text() for page in document.pages] == ["P1", "P3", "P5"]
        assert document.remove_blank_pages() == []
        output = tmp_path / "clean.pdf"
        document.save(output)
        for source in (_bytes(document), output):
            with Document(source) as reopened:
                assert reopened.extract_text() == "P1\nP3\nP5"
                assert reopened.validate()
    assert path.read_bytes() == raw


def test_selection_order_and_repeats_report_original_indices(monkeypatch):
    with _document() as document:
        original = Page.render
        visits = []

        def render(page, **options):
            visits.append(page.index)
            assert document.page_count == 6, "Detection must finish before deletion"
            return original(page, **options)

        monkeypatch.setattr(Page, "render", render)
        assert document.remove_blank_pages(pages=(i for i in (4, 1, 0, 4))) == [4, 0]
        assert visits == [4, 1, 0]
        assert [page.extract_text() for page in document.pages] == [
            "P1",
            "",
            "P3",
            "P5",
        ]


def test_empty_selection_empty_document_and_nonblank_documents_are_noops():
    with Document() as empty:
        assert empty.remove_blank_pages() == []
        assert empty.remove_blank_pages(dry_run=True) == []
    with _document() as document:
        before = _bytes(document)
        assert document.remove_blank_pages(pages=[]) == []
        assert document.remove_blank_pages(pages=[1, 3, 5]) == []
        assert _bytes(document) == before


@pytest.mark.parametrize("count", [1, 3])
def test_all_blank_removal_is_refused_without_changes_but_can_be_previewed(count):
    with Document() as document:
        for _ in range(count):
            document.pages.add(size=(64, 64))
        before = _bytes(document)
        assert document.remove_blank_pages(dry_run=True) == list(range(count))
        with pytest.raises(PdfValidationException, match="every page"):
            document.remove_blank_pages()
        assert document.page_count == count
        assert _bytes(document) == before


def test_a_subset_of_a_blank_document_can_be_removed():
    with Document() as document:
        for _ in range(3):
            document.pages.add(size=(10, 10))
        assert document.remove_blank_pages(pages=[2, 0, 2]) == [2, 0]
        assert document.page_count == 1


def test_dry_run_preserves_live_outlines_text_cache_and_source_bytes():
    with _document() as document:
        item = document.outlines.add(OutlineItem("Retained", 5))
        text = document.extract_text()
        before = _bytes(document)
        engine = document._engine_pdf
        model = engine._outlines_data
        engine._page_text_cursor = 2
        assert document.remove_blank_pages(dry_run=True) == [0, 2, 4]
        assert document.page_count == 6
        assert engine._outlines_data is model
        assert engine._extracted_text == text
        assert engine._page_text_cursor == 2
        assert document.outlines[0] is item and item.page_index == 5
        assert _bytes(document) == before


def test_cleanup_invalidates_cached_text_after_actual_removal():
    with _document() as document:
        assert document.extract_text() == "\nP1\n\nP3\n\nP5"
        document._engine_pdf._page_text_cursor = 5
        document.remove_blank_pages()
        assert document.extract_text() == "P1\nP3\nP5"
        assert document._engine_pdf.get_next_page_text() == "P1"


@pytest.mark.parametrize("loaded", [False, True])
def test_bookmarks_labels_metadata_and_live_items_survive_cleanup(loaded):
    with _document() as template:
        template.info["Title"] = "Report"
        template.add_attachment("notes.txt", b"Keep this")
        template.page_labels[0] = PageLabel(NumberingStyle.NUMERALS_ROMAN_LOWERCASE)
        template.page_labels[2] = PageLabel(prefix="A-")
        template.outlines.add(OutlineItem("Blank heading", 0)).add(
            OutlineItem(
                "Zoomed", destination=XYZDestination(3, 4, 50, 1.5), is_bold=True
            )
        )
        template.outlines.add(OutlineItem("Last", 5))
        template.outlines.add(
            OutlineItem("External", destination=URIAction("https://example.com"))
        )
        if loaded:
            document = Document(_bytes(template))
        else:
            document = template
        try:
            collection = document.outlines
            heading, last, external = collection
            zoomed = heading.children[0]
            assert document.remove_blank_pages() == [0, 2, 4]
            assert document.outlines is collection
            assert collection[0] is heading and heading.children[0] is zoomed
            assert heading.page_index is None and heading.destination is None
            assert zoomed.page_index == 1
            assert zoomed.destination == XYZDestination(1, 4, 50, 1.5)
            assert zoomed.is_bold and last.page_index == 2
            assert external.destination == URIAction("https://example.com")
            assert [page.label for page in document.pages] == ["i", "A-1", "A-2"]
            assert document.info["Title"] == "Report"
            assert document.attachments["notes.txt"] == b"Keep this"
            with Document(_bytes(document)) as reopened:
                assert reopened.outlines[0].page_index is None
                assert reopened.outlines[0].children[0].destination == XYZDestination(
                    1, 4, 50, 1.5
                )
                assert reopened.outlines[1].page_index == 2
                assert [page.label for page in reopened.pages] == ["i", "A-1", "A-2"]
        finally:
            if document is not template:
                document.dispose()


def test_loaded_bookmarks_are_preserved_without_accessing_the_collection_first():
    with _document() as source:
        source.outlines.add(OutlineItem("Last", 5))
        raw = _bytes(source)
    with Document(raw) as document:
        document.remove_blank_pages()
        assert document.outlines[0].page_index == 2
        assert document.outlines[0].destination == FitDestination(2)


def test_named_bookmarks_keep_names_and_local_links_keep_retained_targets():
    with _document() as document:
        document.destinations["last"] = XYZDestination(5, 4, 50, 1.5)
        document.destinations["blank"] = FitDestination(2)
        document.outlines.add(OutlineItem("Last", destination="last"))
        document.outlines.add(OutlineItem("Blank", destination="blank"))
        document.pages[1].add_link((1, 1, 5, 5), FitDestination(5))
        document.pages[1].add_link((6, 1, 10, 5), FitDestination(2))
        document.remove_blank_pages()
        assert document.outlines[0].destination_name == "last"
        assert document.outlines[0].page_index == 2
        assert document.outlines[1].page_index is None
        assert document.outlines[1].destination_name is None
        links = list(document.pages[0].annotations)
        assert links[0].get_property("Dest") == FitDestination(2)
        assert links[1].get_property("Dest") is None


@pytest.mark.parametrize("include", [True, False])
def test_annotation_visibility_and_noise_thresholds_are_forwarded(include):
    with Document() as document:
        document.pages.add(size=(64, 64)).add_text("Keep", 5, 30)
        note = document.pages.add(size=(64, 64))
        note.annotations.add_square((5, 5, 25, 25), interior_color=(0, 0, 0))
        assert document.remove_blank_pages(include_annotations=include) == (
            [] if include else [1]
        )
    with Document() as document:
        document.pages.add(size=(10, 10)).draw_rectangle(
            0, 0, 10, 10, fill_color="#000"
        )
        document.pages.add(size=(10, 10)).add_image(
            write_png(1, 1, "RGB", bytes([249, 249, 249])), 0, 0, 10, 10
        )
        assert document.remove_blank_pages(
            white_threshold=249, max_ink_ratio=0, dpi=144
        ) == [1]


@pytest.mark.parametrize("encrypted", [False, True])
def test_streaming_cleanup_reads_only_selected_scans(tmp_path, monkeypatch, encrypted):
    path = tmp_path / "scans.pdf"
    with Document() as source:
        for value in (255, 0, 250, 0):
            source.pages.add(size=(10, 10)).add_image(
                write_png(1, 1, "L", bytes([value])), 0, 0, 10, 10
            )
        if encrypted:
            source.encrypt("secret", "owner")
        source.save(path)
    before = path.read_bytes()
    with Document.open_streaming(
        path, password="secret" if encrypted else None
    ) as document:
        engine = document._engine_pdf
        original = engine.get_page_content
        visited = []

        def read(index):
            assert index in (0, 2)
            visited.append(index)
            return original(index)

        with monkeypatch.context() as patch:
            patch.setattr(engine, "get_page_content", read)
            assert document.remove_blank_pages(pages=[2, 0, 2]) == [2, 0]
            assert set(visited) == {0, 2}
            assert engine.page_contents == []
            assert engine._extracted_text is None
            assert document.page_count == 2
        saved = _bytes(document)
    with Document(saved, password="secret" if encrypted else None) as reopened:
        assert reopened.page_count == 2
        assert reopened.find_blank_pages() == []
    assert path.read_bytes() == before


def test_later_render_failure_leaves_every_page_and_live_bookmark_intact(monkeypatch):
    with _document() as document:
        item = document.outlines.add(OutlineItem("Last", 5))
        before = _bytes(document)
        original = Page.render

        def render(page, **options):
            if page.index == 4:
                raise PdfParseException("Cannot render page")
            return original(page, **options)

        monkeypatch.setattr(Page, "render", render)
        with pytest.raises(PdfParseException, match="Cannot render"):
            document.remove_blank_pages()
        assert document.page_count == 6 and item.page_index == 5
        assert _bytes(document) == before


@pytest.mark.parametrize(
    "options",
    [
        {"dry_run": 1},
        {"dpi": 0},
        {"white_threshold": 256},
        {"max_ink_ratio": -1},
        {"include_annotations": 1},
        {"pages": [0, 6]},
        {"pages": [True]},
    ],
)
def test_invalid_options_fail_before_rendering_or_deletion(monkeypatch, options):
    with _document() as document:

        def forbidden(*args, **kwargs):
            pytest.fail("Invalid options must fail before work")

        monkeypatch.setattr(Page, "render", forbidden)
        monkeypatch.setattr(document._engine_pdf, "delete", forbidden)
        with pytest.raises(PdfValidationException):
            document.remove_blank_pages(**options)
        assert document.page_count == 6


def test_render_limits_preserve_pages_and_disposed_documents_are_rejected():
    with Document(limits=PdfLoadLimits(max_raster_pixels=99)) as document:
        document.pages.add(size=(10, 10))
        document.pages.add(size=(10, 10))
        with pytest.raises(PdfResourceLimitException, match="max_raster_pixels"):
            document.remove_blank_pages()
        assert document.page_count == 2
    with pytest.raises(AsposePdfException, match="disposed"):
        document.remove_blank_pages()


def test_invalid_live_outline_tree_is_rejected_before_deletion():
    with _document() as document:
        item = document.outlines.add(OutlineItem("Cycle", 5))
        item.children.append(item)
        with pytest.raises(PdfParseException, match="cycle"):
            document.remove_blank_pages()
        assert document.page_count == 6


def test_noise_tolerance_removes_a_speckled_page():
    with Document() as document:
        document.pages.add(size=(10, 10)).draw_rectangle(
            0, 0, 10, 10, fill_color="#000", stroke_color=None
        )
        document.pages.add(size=(10, 10)).draw_rectangle(
            0, 0, 1, 1, fill_color="#000", stroke_color=None
        )
        assert document.remove_blank_pages() == []
        assert document.remove_blank_pages(max_ink_ratio=0.01) == [1]
        assert document.page_count == 1


def test_retained_form_fields_keep_their_page_and_value_after_cleanup():
    with _document() as document:
        field = document.form.add_text_field("Value", 3, (5, 5, 55, 20), value="Keep")
        document.generate_field_appearances()
        document.remove_blank_pages()
        assert document.form["Value"] is field
        assert field.value == "Keep" and field.page_index == 1
        with Document(_bytes(document)) as reopened:
            assert reopened.form["Value"].value == "Keep"
            assert reopened.form["Value"].page_index == 1


def test_invalid_label_tree_is_checked_before_any_page_is_deleted(monkeypatch):
    with _document() as document:

        def invalid():
            raise PdfResourceLimitException("PageLabels number-tree depth")

        monkeypatch.setattr(document._engine_pdf, "page_label_ranges", invalid)
        with pytest.raises(PdfResourceLimitException, match="PageLabels"):
            document.remove_blank_pages()
        assert document.page_count == 6
