"""Duplicate-page cleanup keeps chosen copies and retained navigation intact."""

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
    PdfSecurityException,
    PdfValidationException,
)
from aspose_pdf.pages import Page


def _document():
    document = Document()
    for text in ("A", "B", "A", "C", "B", "A"):
        document.pages.add(size=(64, 64)).add_text(text, 5, 30, font_size=10)
    return document


def _bytes(document):
    with io.BytesIO() as output:
        document.save(output)
        return output.getvalue()


@pytest.mark.parametrize("source_kind", ["live", "bytes", "file", "streaming"])
@pytest.mark.parametrize("keep,removed,remaining", [
    ("first", [2, 4, 5], ["A", "B", "C"]),
    ("last", [0, 1, 2], ["C", "B", "A"]),
])
def test_selected_copies_and_original_indices_survive_round_trips(
    source_kind, keep, removed, remaining, tmp_path,
):
    with _document() as source:
        raw = _bytes(source)
    path = tmp_path / "source.pdf"
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
        assert document.remove_duplicate_pages(keep=keep) == removed
        assert [page.extract_text() for page in document.pages] == remaining
        assert document.remove_duplicate_pages(keep=keep) == []
        output_path = tmp_path / "cleaned.pdf"
        document.save(output_path)
        for value in (_bytes(document), output_path):
            with Document(value) as reopened:
                assert [page.extract_text() for page in reopened.pages] == remaining
                assert reopened.validate()
    assert path.read_bytes() == raw


@pytest.mark.parametrize("keep,removed,remaining", [
    ("first", [0, 1, 5], ["A", "C", "B"]),
    ("last", [2, 4, 5], ["A", "B", "C"]),
])
def test_selection_order_controls_keep_and_results_use_document_order(
    monkeypatch, keep, removed, remaining,
):
    with _document() as document:
        original = Page.render
        visited = []

        def render(page, **options):
            assert document.page_count == 6, "All inspection must precede deletion"
            assert page.index != 3, "Unselected pages must not be rendered"
            visited.append(page.index)
            return original(page, **options)

        with monkeypatch.context() as patch:
            patch.setattr(Page, "render", render)
            assert document.remove_duplicate_pages(
                pages=(i for i in (4, 2, 1, 5, 4, 0)), keep=keep,
            ) == removed
        assert set(visited) == {0, 1, 2, 4, 5}
        assert [page.extract_text() for page in document.pages] == remaining


@pytest.mark.parametrize("keep", ["first", "last"])
def test_blank_documents_keep_one_page_and_noops_do_not_render(monkeypatch, keep):
    with Document() as document:
        assert document.remove_duplicate_pages(keep=keep) == []
        document.pages.add(size=(10, 10))
        with monkeypatch.context() as patch:
            patch.setattr(Page, "render", lambda *a, **k: pytest.fail("No comparison"))
            assert document.remove_duplicate_pages(keep=keep) == []
            assert document.remove_duplicate_pages(pages=[], keep=keep) == []
            assert document.remove_duplicate_pages(pages=[0, 0], keep=keep) == []
        for _ in range(3):
            document.pages.add(size=(10, 10))
        expected = [1, 2, 3] if keep == "first" else [0, 1, 2]
        assert document.remove_duplicate_pages(keep=keep, dry_run=True) == expected
        assert document.page_count == 4
        assert document.remove_duplicate_pages(keep=keep) == expected
        assert document.page_count == 1


def test_dry_run_keeps_bytes_live_outlines_and_text_caches():
    with _document() as document:
        item = document.outlines.add(OutlineItem("Last copy", 5))
        cached = document.extract_text()
        before = _bytes(document)
        engine = document._engine_pdf
        model = engine._outlines_data
        engine._page_text_cursor = 4
        assert document.remove_duplicate_pages(dry_run=True) == [2, 4, 5]
        assert document.page_count == 6
        assert engine._outlines_data is model
        assert engine._extracted_text is cached and engine._page_text_cursor == 4
        assert document.outlines[0] is item and item.page_index == 5
        assert _bytes(document) == before


@pytest.mark.parametrize("access_outlines", [False, True])
def test_bookmarks_labels_metadata_and_attachments_survive_cleanup(access_outlines):
    with _document() as source:
        source.info["Title"] = "Batch"
        source.add_attachment("notes.txt", b"Keep this")
        source.page_labels[0] = PageLabel(NumberingStyle.NUMERALS_ROMAN_LOWERCASE)
        source.page_labels[3] = PageLabel(prefix="A-")
        source.outlines.add(OutlineItem("Removed copy", 2)).add(
            OutlineItem("Retained", destination=XYZDestination(3, 4, 50, 1.5),
                        is_bold=True)
        )
        source.outlines.add(OutlineItem("External", destination=URIAction("https://example.com")))
        raw = _bytes(source)
    with Document(raw) as document:
        if access_outlines:
            collection = document.outlines
            parent = collection[0]
            child = parent.children[0]
        assert document.remove_duplicate_pages() == [2, 4, 5]
        parent_after = document.outlines[0]
        assert parent_after.page_index is None and parent_after.destination is None
        assert parent_after.children[0].destination == XYZDestination(2, 4, 50, 1.5)
        assert parent_after.children[0].is_bold
        if access_outlines:
            assert document.outlines is collection and parent_after is parent
            assert parent_after.children[0] is child
        assert document.outlines[1].destination == URIAction("https://example.com")
        assert [page.label for page in document.pages] == ["i", "ii", "A-1"]
        assert document.info["Title"] == "Batch"
        assert document.attachments == {"notes.txt": b"Keep this"}
        with Document(_bytes(document)) as reopened:
            assert reopened.outlines[0].page_index is None
            assert reopened.outlines[0].children[0].destination == XYZDestination(2, 4, 50, 1.5)
            assert [page.label for page in reopened.pages] == ["i", "ii", "A-1"]


def test_named_bookmarks_and_local_links_follow_ordinary_deletion():
    with _document() as document:
        document.destinations["retained"] = XYZDestination(3, 4, 50, 1.5)
        document.destinations["removed"] = FitDestination(2)
        document.outlines.add(OutlineItem("Retained", destination="retained"))
        document.outlines.add(OutlineItem("Removed", destination="removed"))
        page = document.pages[1]
        page.add_link((1, 1, 5, 5), FitDestination(3))
        page.add_link((6, 1, 10, 5), FitDestination(2))
        assert document.remove_duplicate_pages(include_annotations=False) == [2, 4, 5]
        assert document.outlines[0].destination_name == "retained"
        assert document.outlines[0].page_index == 2
        assert document.outlines[1].destination_name is None
        links = list(document.pages[1].annotations)
        assert links[0].get_property("Dest") == FitDestination(2)
        assert links[1].get_property("Dest") is None


@pytest.mark.parametrize("include", [True, False])
def test_annotation_visibility_controls_duplicate_removal(include):
    with Document() as document:
        for _ in range(3):
            document.pages.add(size=(64, 64))
        document.pages[1].annotations.add_square(
            (5, 5, 25, 25), interior_color=(0, 0, 0),
        )
        assert document.remove_duplicate_pages(include_annotations=include) == (
            [2] if include else [1, 2]
        )


@pytest.mark.parametrize("encrypted", [False, True])
def test_streaming_cleanup_reads_only_selected_scans(tmp_path, monkeypatch, encrypted):
    path = tmp_path / "scans.pdf"
    with Document() as source:
        for value in (255, 0, 255, 0):
            source.pages.add(size=(10, 10)).add_image(
                write_png(1, 1, "L", bytes([value])), 0, 0, 10, 10,
            )
        if encrypted:
            source.encrypt("secret", "owner")
        source.save(path)
    before = path.read_bytes()
    password = "secret" if encrypted else None
    with Document.open_streaming(path, password=password) as document:
        engine = document._engine_pdf
        original = engine.get_page_content
        visited = []

        def read(index):
            assert index in (0, 2)
            visited.append(index)
            return original(index)

        with monkeypatch.context() as patch:
            patch.setattr(engine, "get_page_content", read)
            assert document.remove_duplicate_pages(pages=[2, 0, 2]) == [0]
            assert set(visited) == {0, 2}
            assert engine.page_contents == [] and engine._extracted_text is None
            assert document.page_count == 3
        saved = _bytes(document)
    with Document(saved, password=password) as reopened:
        assert reopened.page_count == 3
    if encrypted:
        with pytest.raises(PdfSecurityException):
            Document(saved)
    assert path.read_bytes() == before


@pytest.mark.parametrize("options", [
    {"keep": "all"}, {"keep": None}, {"keep": []}, {"dry_run": 1},
    {"dpi": 0}, {"dpi": float("nan")}, {"include_annotations": 1},
    {"pages": [0, 6]}, {"pages": [True]},
])
def test_invalid_options_fail_before_rendering_or_deletion(monkeypatch, options):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid options must fail before rendering or deletion")

    with Document() as empty, _document() as document:
        monkeypatch.setattr(Page, "render", forbidden)
        monkeypatch.setattr(document._engine_pdf, "delete", forbidden)
        for instance in (empty, document):
            with pytest.raises(PdfValidationException):
                instance.remove_duplicate_pages(**options)
        assert document.page_count == 6


@pytest.mark.parametrize("error", [PdfParseException, PdfResourceLimitException])
def test_late_render_failure_preserves_all_pages_and_live_bookmarks(monkeypatch, error):
    with _document() as document:
        item = document.outlines.add(OutlineItem("Last", 5))
        before = _bytes(document)
        original = Page.render

        def render(page, **options):
            if page.index == 5:
                raise error("Cannot render page")
            return original(page, **options)

        monkeypatch.setattr(Page, "render", render)
        with pytest.raises(error, match="Cannot render"):
            document.remove_duplicate_pages()
        assert document.page_count == 6 and item.page_index == 5
        assert _bytes(document) == before


def test_render_and_selection_limits_leave_pages_intact():
    with Document(limits=PdfLoadLimits(max_raster_pixels=99)) as document:
        for _ in range(2):
            document.pages.add(size=(10, 10))
        with pytest.raises(PdfResourceLimitException, match="max_raster_pixels"):
            document.remove_duplicate_pages()
        assert document.page_count == 2
        assert document.remove_duplicate_pages(dpi=36) == [1]
    with Document(limits=PdfLoadLimits(max_container_items=2)) as document:
        for _ in range(3):
            document.pages.add(size=(10, 10))
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            document.remove_duplicate_pages()
        assert document.page_count == 3


def test_invalid_navigation_is_rejected_before_any_deletion(monkeypatch):
    with _document() as document:
        def invalid():
            raise PdfResourceLimitException("PageLabels depth")

        with monkeypatch.context() as patch:
            patch.setattr(document._engine_pdf, "page_label_ranges", invalid)
            with pytest.raises(PdfResourceLimitException, match="PageLabels"):
                document.remove_duplicate_pages()
        assert document.page_count == 6
        item = document.outlines.add(OutlineItem("Cycle", 5))
        item.children.append(item)
        with pytest.raises(PdfParseException, match="cycle"):
            document.remove_duplicate_pages()
        assert document.page_count == 6


def test_removal_invalidates_text_cache_and_keeps_retained_field_handles():
    with _document() as document:
        field = document.form.add_text_field("Retained", 3, (5, 5, 55, 20), value="Keep")
        document.extract_text()
        document._engine_pdf._page_text_cursor = 5
        assert document.remove_duplicate_pages() == [2, 4, 5]
        assert document._engine_pdf._extracted_text is None
        assert document._engine_pdf.get_next_page_text() == "A"
        assert document.form["Retained"] is field
        assert field.value == "Keep" and field.page_index == 2
        with Document(_bytes(document)) as reopened:
            assert reopened.form["Retained"].value == "Keep"
            assert reopened.form["Retained"].page_index == 2
    with pytest.raises(AsposePdfException, match="disposed"):
        document.remove_duplicate_pages()
