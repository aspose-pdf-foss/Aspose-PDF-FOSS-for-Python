"""Merged PDFs expose source sections without losing existing navigation."""

from __future__ import annotations

import io
from dataclasses import replace
from itertools import repeat

import pytest

from aspose_pdf import (
    ByteArrayDataSource,
    DataSource,
    Document,
    FileDataSource,
    MergeOptions,
    Merger,
    OutlineItem,
    PdfLoadLimits,
    PdfResourceLimitException,
    StreamDataSource,
    URIAction,
    XYZDestination,
)
from aspose_pdf.exceptions import PdfParseException, PdfValidationException


def _document(*texts, limits=None):
    document = Document(limits=limits)
    for text in texts:
        document.pages.add().add_text(text, 20, 100)
    return document


def _saved(document):
    with io.BytesIO() as stream:
        document.save(stream)
        return stream.getvalue()


def _titles(document):
    return [(item.title, item.page_index) for item in document.outlines]


@pytest.mark.parametrize("source_kind", ["live", "bytes", "file", "streaming"])
@pytest.mark.parametrize("on_disk", [False, True])
def test_sections_preserve_nested_bookmarks_targets_and_source_documents(
    source_kind, on_disk, tmp_path
):
    with _document("First", "Second", "Third") as authored:
        authored.destinations["chapter"] = XYZDestination(2, 30, 450, 1.5)
        heading = authored.outlines.add(
            OutlineItem(
                "Chapter",
                destination=XYZDestination(1, 20, 500, 2),
                is_bold=True,
                is_italic=True,
                color="#336699",
                open=True,
            )
        )
        heading.add(OutlineItem("Subsection", destination="chapter"))
        authored.outlines.add(
            OutlineItem(
                "Website", page_index=None, destination=URIAction("https://example.com")
            )
        )
        raw = _saved(authored)
        path = tmp_path / "source.pdf"
        path.write_bytes(raw)
        source = (
            authored
            if source_kind == "live"
            else Document(raw)
            if source_kind == "bytes"
            else Document(path)
            if source_kind == "file"
            else Document.open_streaming(path)
        )
        try:
            source_view = source.outlines
            source_heading = source_view[0]
            with _document("Cover") as target:
                target.outlines.add(OutlineItem("Cover", 0))
                assert (
                    target.merge(
                        source,
                        create_bookmarks=True,
                        bookmark_titles=["Report Привет 🌍"],
                    )
                    is target
                )
                assert _titles(target) == [("Cover", 0), ("Report Привет 🌍", 1)]
                assert source.outlines is source_view
                assert source.outlines[0] is source_heading
                assert [item.title for item in source.outlines] == [
                    "Chapter",
                    "Website",
                ]
                source.dispose()
                if on_disk:
                    destination = tmp_path / "merged.pdf"
                    target.save(destination)
                else:
                    destination = _saved(target)
                with Document(destination) as reopened:
                    assert [page.extract_text().strip() for page in reopened.pages] == [
                        "Cover",
                        "First",
                        "Second",
                        "Third",
                    ]
                    section = reopened.outlines[1]
                    assert section.page_index == 1
                    chapter, website = section.children
                    assert chapter.page_index == 2
                    assert chapter.destination == XYZDestination(2, 20, 500, 2)
                    assert chapter.children[0].destination == XYZDestination(
                        3, 30, 450, 1.5
                    )
                    assert chapter.is_bold and chapter.is_italic and chapter.open
                    assert chapter.color == pytest.approx((0.2, 0.4, 0.6))
                    assert website.page_index is None
                    assert website.destination == URIAction("https://example.com")
        finally:
            source.dispose()


@pytest.mark.parametrize("edit, expected", [("insert", 2), ("delete", 0)])
def test_section_targets_follow_pages_after_insertion_or_deletion(edit, expected):
    with _document("A", "B") as source, _document("Cover") as target:
        target.merge(source, create_bookmarks=True)
        if edit == "insert":
            target.pages.insert(0)
        else:
            target.pages.delete(0)
        with Document(_saved(target)) as reopened:
            assert _titles(reopened) == [("Document 1", expected)]
            assert reopened.pages[expected].extract_text().strip() == "A"


def test_default_titles_prefer_file_stems_then_pdf_metadata_then_input_numbers(
    tmp_path,
):
    path = tmp_path / "Annual report.pdf"
    with _document("File") as authored:
        authored.info["Title"] = "Metadata title"
        authored.save(path)
    with (
        Document(path) as file_source,
        _document("Metadata") as metadata_source,
        _document("Anonymous") as anonymous,
        Document() as target,
    ):
        metadata_source.info["Title"] = "Financial summary"
        target.merge(file_source, metadata_source, anonymous, create_bookmarks=True)
        with Document(_saved(target)) as reopened:
            assert _titles(reopened) == [
                ("Annual report", 0),
                ("Financial summary", 1),
                ("Document 3", 2),
            ]


def test_empty_inputs_do_not_create_dead_sections_and_repeated_inputs_stay_independent():
    with Document() as empty, _document("Page") as source, Document() as target:
        source.outlines.add(OutlineItem("Original", 0))
        target.merge(empty, source, source, create_bookmarks=True)
        assert _titles(target) == [("Document 2", 0), ("Document 3", 1)]
        first, second = target.outlines
        first.children[0].title = "Edited first copy"
        assert second.children[0].title == "Original"
        assert source.outlines[0].title == "Original"
        with Document(_saved(target)) as reopened:
            assert [item.children[0].page_index for item in reopened.outlines] == [0, 1]


def test_empty_input_keeps_existing_non_page_bookmarks_without_creating_a_section():
    with Document() as empty, _document("Page") as target:
        empty.outlines.add(
            OutlineItem(
                "Website", page_index=None, destination=URIAction("https://example.com")
            )
        )
        target.merge(empty, create_bookmarks=True)
        with Document(_saved(target)) as reopened:
            assert _titles(reopened) == [("Website", None)]


def test_default_merge_keeps_existing_flat_bookmark_behavior():
    with _document("A") as first, _document("B") as second, Document() as target:
        first.outlines.add(OutlineItem("First", 0))
        second.outlines.add(OutlineItem("Second", 0))
        target.merge(first, second)
        with Document(_saved(target)) as reopened:
            assert _titles(reopened) == [("First", 0), ("Second", 1)]
            assert all(not item.children for item in reopened.outlines)


@pytest.mark.parametrize("titles", [[], ["One", "Two"], [""], ["  \t"], ["\ud800"]])
def test_invalid_section_titles_leave_target_unchanged(titles):
    with _document("Source") as source, _document("Cover") as target:
        with pytest.raises(PdfValidationException):
            target.merge(source, create_bookmarks=True, bookmark_titles=titles)
        assert target.page_count == 1 and not target.outlines


@pytest.mark.parametrize(
    "options",
    [
        {"create_bookmarks": 1},
        {"create_bookmarks": True, "bookmark_titles": "Title"},
        {"create_bookmarks": True, "bookmark_titles": {"Title": "Ignored"}},
        {"create_bookmarks": True, "bookmark_titles": [1]},
    ],
)
def test_invalid_section_option_types_are_rejected_before_import(options):
    with _document("Source") as source, _document("Cover") as target:
        with pytest.raises(TypeError):
            target.merge(source, **options)
        assert target.page_count == 1


def test_titles_require_explicit_section_bookmark_mode():
    with _document("Source") as source, Document() as target:
        with pytest.raises(PdfValidationException, match="requires create_bookmarks"):
            target.merge(source, bookmark_titles=["Title"])
        assert target.page_count == 0


def test_all_sources_are_validated_before_import():
    with _document("Source") as source, _document("Cover") as target:
        with pytest.raises(TypeError):
            target.merge(source, "not a document", create_bookmarks=True)
        with pytest.raises(PdfValidationException, match="into itself"):
            target.merge(source, target, create_bookmarks=True)
        assert target.page_count == 1


@pytest.mark.parametrize("failure", ["depth", "items", "unicode", "cycle"])
def test_invalid_or_oversized_bookmark_trees_fail_before_import(failure):
    limits = replace(
        PdfLoadLimits.unlimited(), max_container_items=8, max_nesting_depth=2
    )
    with _document("Source") as source, _document("Cover", limits=limits) as target:
        root = source.outlines.add(OutlineItem("Root", 0))
        if failure == "depth":
            root.add(OutlineItem("Child", 0))
        elif failure == "items":
            for index in range(9):
                root.add(OutlineItem(str(index), 0))
        elif failure == "unicode":
            root.title = "\ud800"
        else:
            root.add(root)
        error = (
            PdfResourceLimitException
            if failure in ("depth", "items")
            else PdfValidationException
        )
        with pytest.raises(error):
            target.merge(source, create_bookmarks=True)
        assert target.page_count == 1 and not target.outlines


@pytest.mark.parametrize("limit", ["max_object_bytes", "max_total_decoded_bytes"])
def test_section_title_text_obeys_resource_limits_before_import(limit):
    limits = replace(PdfLoadLimits.unlimited(), **{limit: 8})
    with _document("Source") as source, Document(limits=limits) as target:
        with pytest.raises(PdfResourceLimitException, match=limit):
            target.merge(
                source, create_bookmarks=True, bookmark_titles=["Title is too long"]
            )
        assert target.page_count == 0


def test_unending_section_titles_are_bounded():
    limits = replace(PdfLoadLimits.unlimited(), max_container_items=8)
    with _document("Source") as source, Document(limits=limits) as target:
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            target.merge(source, create_bookmarks=True, bookmark_titles=repeat("Title"))
        assert target.page_count == 0


def test_lowcode_merger_names_file_stream_metadata_and_anonymous_inputs(tmp_path):
    path = tmp_path / "Report.pdf"
    with _document("Page 1", "Page 2") as source:
        source.outlines.add(OutlineItem("Chapter", 1))
        source.save(path)
        raw = _saved(source)
        source.info["Title"] = "Metadata title"
        metadata_raw = _saved(source)
    options = MergeOptions(create_bookmarks=True)
    with io.BytesIO(raw) as input_stream:
        options.add_input(FileDataSource(path))
        options.add_input(StreamDataSource(input_stream, name="Statement.pdf"))
        options.add_input(ByteArrayDataSource(metadata_raw))
        options.add_input(ByteArrayDataSource(raw))
        destination = tmp_path / "merged.pdf"
        options.add_output(FileDataSource(destination))
        result = Merger().process(options)
        assert not input_stream.closed
    assert result[0].to_array() == destination.read_bytes()
    with Document(destination) as reopened:
        assert _titles(reopened) == [
            ("Report", 0),
            ("Statement", 2),
            ("Metadata title", 4),
            ("Document 4", 6),
        ]
        assert [root.children[0].page_index for root in reopened.outlines] == [
            1,
            3,
            5,
            7,
        ]


def test_lowcode_custom_titles_are_snapshotted_and_allow_duplicates():
    titles = ["Section", "Section"]
    options = MergeOptions(create_bookmarks=True, bookmark_titles=iter(titles))
    titles[0] = "Changed"
    with _document("Page") as source:
        raw = _saved(source)
    options.add_input(ByteArrayDataSource(raw))
    options.add_input(ByteArrayDataSource(raw))
    with Document(Merger().process(options)[0].to_array()) as reopened:
        assert _titles(reopened) == [("Section", 0), ("Section", 1)]


class _FailOnRead(DataSource):
    def read_bytes(self, **kwargs):
        raise AssertionError("Invalid options must fail before reading input")


@pytest.mark.parametrize(
    "updates",
    [
        {"create_bookmarks": 1},
        {"bookmark_titles": ["One", "Two"]},
        {"bookmark_titles": ["\ud800"]},
    ],
)
def test_lowcode_revalidates_options_before_reading_or_writing(updates):
    options = MergeOptions(create_bookmarks=True, bookmark_titles=["One"])
    options.add_input(_FailOnRead())
    output = ByteArrayDataSource(b"unchanged")
    options.add_output(output)
    for name, value in updates.items():
        setattr(options, name, value)
    with pytest.raises((TypeError, PdfValidationException)):
        Merger().process(options)
    assert output.read_bytes() == b"unchanged"


def test_lowcode_late_invalid_pdf_does_not_write_partial_output():
    with _document("First") as source:
        raw = _saved(source)
    options = MergeOptions(create_bookmarks=True)
    options.add_input(ByteArrayDataSource(raw))
    options.add_input(ByteArrayDataSource(b"not a PDF"))
    output = ByteArrayDataSource(b"unchanged")
    options.add_output(output)
    with pytest.raises(PdfParseException):
        Merger().process(options)
    assert output.read_bytes() == b"unchanged"


def test_source_bookmark_views_are_restored_when_import_fails(monkeypatch):
    with _document("Source") as source, Document() as target:
        source.outlines.add(OutlineItem("Original", 0))
        view = source.outlines
        original = source._engine_pdf._outlines_data

        def fail_import(other):
            assert other is source and source._outlines is None
            raise RuntimeError("Import failed")

        monkeypatch.setattr(target, "_import_pages_from", fail_import)
        with pytest.raises(RuntimeError, match="Import failed"):
            target.merge(source, create_bookmarks=True)
        assert source.outlines is view
        assert source._engine_pdf._outlines_data is original
        assert _titles(source) == [("Original", 0)]


def test_lowcode_input_order_is_snapshotted_before_custom_source_reads():
    options = MergeOptions(create_bookmarks=True, bookmark_titles=["First", "Second"])
    with _document("Source") as source:
        raw = _saved(source)

    class MutatingSource(ByteArrayDataSource):
        def read_bytes(self, **kwargs):
            options.inputs.clear()
            options.bookmark_titles = ["Changed"]
            return super().read_bytes(**kwargs)

    options.add_input(MutatingSource(raw))
    options.add_input(ByteArrayDataSource(raw))
    with Document(Merger().process(options)[0].to_array()) as reopened:
        assert _titles(reopened) == [("First", 0), ("Second", 1)]
