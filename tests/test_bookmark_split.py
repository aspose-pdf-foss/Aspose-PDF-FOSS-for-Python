"""Chapter splitting follows current local bookmark targets and keeps whole pages."""

from __future__ import annotations

import io
from contextlib import ExitStack, contextmanager

import pytest

from aspose_pdf import Document, PdfLoadLimits, PdfResourceLimitException
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfParseException,
    PdfValidationException,
)
from aspose_pdf.interactive import (
    FitDestination,
    GoToAction,
    GoToRAction,
    JavaScriptAction,
    URIAction,
    XYZDestination,
)
from aspose_pdf.lowcode import ByteArrayDataSource, SplitOptions, Splitter
from aspose_pdf.outlines import OutlineItem


@pytest.fixture
def document():
    with Document() as doc:
        for index in range(6):
            page = doc.pages.add()
            page.size = (240, 160)
            page.add_text(f"PAGE {index}", 20, 130)
        doc.info["Title"] = "Original title"
        yield doc


@contextmanager
def _chapters(document, *args, **kwargs):
    with ExitStack() as stack:
        chapters = document.split_by_bookmarks(*args, **kwargs)
        for chapter in chapters:
            stack.enter_context(chapter)
        yield chapters


def _save(document):
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _texts(document):
    return [page.extract_text().strip() for page in document.pages]


def _outlines(document):
    result = []
    stack = list(reversed(list(document.outlines)))
    while stack:
        item = stack.pop()
        result.append((item.title, item.page_index))
        stack.extend(reversed(item.children))
    return result


def test_chapters_include_preamble_and_final_pages_without_changing_source(document):
    document.outlines.add(OutlineItem("Chapter one", 1))
    document.outlines.add(OutlineItem("Глава два", 4))
    before = _save(document)
    original_model = document._engine_pdf._outlines_data
    with _chapters(document) as chapters:
        assert [doc.info["Title"] for doc in chapters] == [
            "Preamble",
            "Chapter one",
            "Глава два",
        ]
        assert [_texts(doc) for doc in chapters] == [
            ["PAGE 0"],
            ["PAGE 1", "PAGE 2", "PAGE 3"],
            ["PAGE 4", "PAGE 5"],
        ]
        assert all(doc.load_limits is document.load_limits for doc in chapters)
        assert document._engine_pdf._outlines_data is original_model
        for chapter in chapters:
            with Document(_save(chapter)) as reopened:
                assert _texts(reopened) == _texts(chapter)
                assert reopened.info["Title"] == chapter.info["Title"]
        chapters[1].info["Title"] = "Edited"
        chapters[1].pages[0].add_text("CHAPTER ONLY", 20, 100)
        assert "CHAPTER ONLY" not in document.pages[1].extract_text()
    assert _save(document) == before
    assert document.info["Title"] == "Original title"


def test_nested_level_promotes_bookmarks_whose_parent_is_outside_the_chapter(document):
    parent = document.outlines.add(OutlineItem("Part", 0))
    first = parent.add(OutlineItem("Section one", 1, is_bold=True))
    first.add(OutlineItem("Detail", destination=XYZDestination(2, 10, 120, 1.5)))
    parent.add(
        OutlineItem(
            "Section two", destination=GoToAction(XYZDestination(4, 10, 120, 2))
        )
    )
    with _chapters(document, 2, include_preamble=False) as chapters:
        assert [doc.info["Title"] for doc in chapters] == ["Section one", "Section two"]
        assert _outlines(chapters[0]) == [("Section one", 0), ("Detail", 1)]
        assert _outlines(chapters[1]) == [("Section two", 0)]
        with Document(_save(chapters[0])) as reopened:
            assert reopened.outlines[0].is_bold
            assert reopened.outlines[0].children[0].destination == XYZDestination(
                1, 10, 120, 1.5
            )
        with Document(_save(chapters[1])) as reopened:
            assert reopened.outlines[0].destination == GoToAction(
                XYZDestination(0, 10, 120, 2)
            )


def test_page_order_duplicate_pages_and_duplicate_titles(document):
    document.outlines.add(OutlineItem("Same", 4))
    document.outlines.add(OutlineItem("Same", 0))
    document.outlines.add(OutlineItem("Other heading on page four", 4))
    document.outlines.add(OutlineItem("Same", 2))
    with _chapters(document) as chapters:
        assert [doc.page_count for doc in chapters] == [2, 2, 2]
        assert [doc.info["Title"] for doc in chapters] == ["Same", "Same", "Same"]
        assert [text for doc in chapters for text in _texts(doc)] == _texts(document)
        assert _outlines(chapters[-1]) == [
            ("Same", 0),
            ("Other heading on page four", 0),
        ]


def test_live_targets_override_cached_page_indices_and_use_current_named_destinations(
    document,
):
    first = document.outlines.add(OutlineItem("First", 5))
    first.destination = FitDestination(1)
    document.destinations["chapter"] = 2
    document.outlines.add(OutlineItem("Named", destination="chapter"))
    document.destinations["chapter"] = XYZDestination(4, 0, 150, 1)
    with _chapters(document, include_preamble=False) as chapters:
        assert [doc.page_count for doc in chapters] == [3, 2]
        assert [_texts(doc)[0] for doc in chapters] == ["PAGE 1", "PAGE 4"]
        assert _outlines(chapters[1]) == [("Named", 0)]
        with Document(_save(chapters[1])) as reopened:
            assert reopened.outlines[0].destination == XYZDestination(0, 0, 150, 1)


@pytest.mark.parametrize("cached", [False, True])
def test_loaded_named_and_goto_bookmarks_follow_page_deletions_and_name_edits(
    document, cached
):
    document.destinations["named"] = 2
    document.outlines.add(OutlineItem("Named", destination="named"))
    document.outlines.add(
        OutlineItem("GoTo", destination=GoToAction(FitDestination(4)))
    )
    with Document(_save(document)) as loaded:
        if cached:
            assert loaded.outlines[0].page_index == 2
        loaded.pages.delete(0)
        loaded.destinations["named"] = 0
        with _chapters(loaded) as chapters:
            assert [doc.page_count for doc in chapters] == [3, 2]
            assert [_texts(doc)[0] for doc in chapters] == ["PAGE 1", "PAGE 4"]


def test_nonlocal_unresolved_and_invalid_targets_do_not_create_boundaries(document):
    for title, target in [
        ("URI", URIAction("https://example.org")),
        ("Remote", GoToRAction("other.pdf", FitDestination(2))),
        ("Script", JavaScriptAction("app.alert('test')")),
        ("Missing name", "undefined"),
        ("Invalid index", FitDestination(999)),
    ]:
        document.outlines.add(OutlineItem(title, destination=target))
    document.outlines.add(OutlineItem("Heading", None))
    document.outlines.add(OutlineItem("Only chapter", 3))
    with _chapters(document, include_preamble=False) as chapters:
        assert len(chapters) == 1
        assert chapters[0].info["Title"] == "Only chapter"
        assert _texts(chapters[0]) == ["PAGE 3", "PAGE 4", "PAGE 5"]


@pytest.mark.parametrize("target", [None, URIAction("https://example.org"), "missing"])
def test_no_usable_bookmarks_is_an_error_not_a_fallback_to_another_split_mode(
    document, target
):
    document.outlines.add(OutlineItem("Not a local chapter", None, destination=target))
    with pytest.raises(
        PdfValidationException, match="No local bookmark destinations at level 1"
    ):
        document.split_by_bookmarks()


def test_empty_document_and_absent_level_are_reported(document):
    document.outlines.add(OutlineItem("Root", 0))
    with pytest.raises(PdfValidationException, match="level 2"):
        document.split_by_bookmarks(2)
    with Document() as empty:
        with pytest.raises(PdfValidationException, match="No local bookmark"):
            empty.split_by_bookmarks()


@pytest.mark.parametrize(
    "level,error",
    [
        (0, ValueError),
        (-1, ValueError),
        (True, TypeError),
        (1.5, TypeError),
        ("1", TypeError),
        (None, TypeError),
    ],
)
def test_invalid_levels_are_rejected(document, level, error):
    with pytest.raises(error):
        document.split_by_bookmarks(level)


def test_invalid_preamble_option_and_disposal(document):
    with pytest.raises(TypeError, match="include_preamble"):
        document.split_by_bookmarks(include_preamble=1)
    document.dispose()
    with pytest.raises(AsposePdfException, match="disposed"):
        document.split_by_bookmarks()


def test_pages_resources_fields_and_hidden_annotations_survive_source_disposal(
    document, tmp_path
):
    document.form.add_text_field("f", 4, (20, 20, 180, 40), value="Editable")
    annotation = document.pages[4].annotations.add_square(
        (20, 50, 70, 100), interior_color=(0, 0, 1)
    )
    layer = document.layers.add("Hidden notes", visible=False)
    layer.add(annotation)
    document.outlines.add(OutlineItem("First", 0))
    document.outlines.add(OutlineItem("Second", 4))
    path = tmp_path / "source.pdf"
    document.save(path)
    with Document.open_streaming(path) as streamed:
        before = streamed.pages[4].render(dpi=72, antialias=False).pixels
        chapters = streamed.split_by_bookmarks()
        assert streamed._engine_pdf._lazy
        assert streamed._engine_pdf.page_contents == []
    try:
        assert chapters[1].pages[0].render(dpi=72, antialias=False).pixels == before
        assert chapters[1].layers["Hidden notes"].visible is False
        assert chapters[1].form["f"].widgets[0].page_index == 0
        chapters[1].form["f"].value = "Changed"
        chapters[1].save(tmp_path / "chapter.pdf")
        with Document(tmp_path / "chapter.pdf") as reopened:
            assert reopened.form["f"].value == "Changed"
            assert reopened.layers["Hidden notes"].visible is False
    finally:
        for chapter in chapters:
            chapter.dispose()


@pytest.mark.parametrize("kind", ["cycle", "alias", "invalid_child"])
def test_malformed_live_tree_is_rejected_before_import(document, kind, monkeypatch):
    root = document.outlines.add(OutlineItem("Root", 0))
    if kind == "cycle":
        root.children.append(root)
    elif kind == "alias":
        document.outlines.add(root)
    else:
        root.children.append("not a bookmark")

    def unexpected(*args, **kwargs):
        pytest.fail("page import must not run for an invalid tree")

    monkeypatch.setattr(document._engine_pdf, "extract_pages", unexpected)
    with pytest.raises(PdfParseException):
        document.split_by_bookmarks()


def test_depth_and_node_limits_are_enforced_before_import():
    with Document(limits=PdfLoadLimits(max_nesting_depth=2)) as document:
        document.pages.add()
        root = document.outlines.add(OutlineItem("Root", 0))
        root.add(OutlineItem("Child", 0)).add(OutlineItem("Too deep", 0))
        with pytest.raises(PdfResourceLimitException, match="max_nesting_depth"):
            document.split_by_bookmarks()
    with Document(limits=PdfLoadLimits(max_container_items=2)) as document:
        document.pages.add()
        for _ in range(3):
            document.outlines.add(OutlineItem("Chapter", 0))
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            document.split_by_bookmarks()


def test_partial_import_failure_restores_source_and_disposes_created_chapters(
    document, monkeypatch
):
    document.outlines.add(OutlineItem("First", 0))
    document.outlines.add(OutlineItem("Second", 3))
    pdf = document._engine_pdf
    original = pdf.extract_pages
    original_outlines = pdf._outlines_data
    created = []

    def extract(selection):
        if created:
            raise PdfValidationException("simulated import failure")
        result = original(selection)
        created.append(result)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(pdf, "extract_pages", extract)
        with pytest.raises(PdfValidationException, match="simulated import failure"):
            document.split_by_bookmarks()
    assert pdf._outlines_data is original_outlines
    with pytest.raises(AsposePdfException, match="disposed"):
        created[0]._ensure_not_disposed()
    assert _texts(document) == [f"PAGE {i}" for i in range(6)]
    with _chapters(document) as chapters:
        assert [doc.page_count for doc in chapters] == [3, 3]


@pytest.mark.parametrize(
    "include_preamble,counts", [(True, [1, 3, 2]), (False, [3, 2])]
)
def test_lowcode_outputs_chapters_for_every_input_and_writes_in_order(
    document, include_preamble, counts
):
    document.outlines.add(OutlineItem("One", 1))
    document.outlines.add(OutlineItem("Two", 4))
    data = _save(document)
    options = SplitOptions(bookmark_level=1, include_preamble=include_preamble)
    options.add_input(ByteArrayDataSource(data)).add_input(ByteArrayDataSource(data))
    outputs = [ByteArrayDataSource() for _ in counts * 2]
    for output in outputs:
        options.add_output(output)
    results = Splitter().process(options)
    assert len(results) == len(counts) * 2
    for result, output, count in zip(results, outputs, counts * 2):
        assert result.to_array() == output.data
        with Document(result.to_array()) as reopened:
            assert reopened.page_count == count
            assert reopened.info["Title"] in ("Preamble", "One", "Two")


def test_lowcode_nested_bookmarks_and_option_validation(document):
    document.outlines.add(OutlineItem("Part", 0)).add(OutlineItem("Section", 2))
    options = SplitOptions(bookmark_level=2, include_preamble=False).add_input(
        ByteArrayDataSource(_save(document))
    )
    with Document(Splitter().process(options)[0].to_array()) as chapter:
        assert chapter.page_count == 4
        assert chapter.info["Title"] == "Section"
    with pytest.raises(ValueError, match="cannot be combined"):
        SplitOptions(bookmark_level=1, selections=[])
    with pytest.raises(TypeError, match="integer"):
        SplitOptions(bookmark_level=True)
    with pytest.raises(ValueError, match="at least 1"):
        SplitOptions(bookmark_level=0)


def test_lowcode_missing_bookmarks_does_not_write_partial_outputs(document):
    target = ByteArrayDataSource(b"untouched")
    options = (
        SplitOptions(bookmark_level=1)
        .add_input(ByteArrayDataSource(_save(document)))
        .add_output(target)
    )
    with pytest.raises(PdfValidationException, match="No local bookmark"):
        Splitter().process(options)
    assert target.data == b"untouched"


def test_legacy_destination_dictionary_is_resolved(document):
    pdf = document._engine_pdf
    root = pdf._resolve(pdf._cos_doc.trailer.mapping[PdfName("Root")])
    destination = PdfArray([pdf._make_page_ref(3), PdfName("Fit")])
    root.mapping[PdfName("Dests")] = PdfDictionary(
        {
            PdfName("legacy"): PdfDictionary({PdfName("D"): destination}),
        }
    )
    document.outlines.add(OutlineItem("Legacy", destination="legacy"))
    with Document(_save(document)) as loaded:
        with _chapters(loaded, include_preamble=False) as chapters:
            assert len(chapters) == 1
            assert chapters[0].info["Title"] == "Legacy"
            assert _texts(chapters[0]) == ["PAGE 3", "PAGE 4", "PAGE 5"]


def test_bookmark_to_deleted_page_does_not_copy_an_orphan_or_start_a_chapter(document):
    document.outlines.add(OutlineItem("Deleted", 1))
    document.outlines.add(OutlineItem("Surviving", 4))
    with Document(_save(document)) as loaded:
        assert loaded.outlines[0].page_index == 1
        loaded.pages.delete(1)
        with _chapters(loaded) as chapters:
            assert [doc.info["Title"] for doc in chapters] == ["Preamble", "Surviving"]
            assert [doc.page_count for doc in chapters] == [3, 2]
            assert [text for doc in chapters for text in _texts(doc)] == _texts(loaded)
            for chapter in chapters:
                with Document(_save(chapter)) as reopened:
                    assert reopened.page_count == chapter.page_count


@pytest.mark.parametrize("algorithm", ["AES-128", "AES-256"])
def test_opened_encrypted_document_can_be_split_without_changing_its_protection(
    document, algorithm
):
    document.outlines.add(OutlineItem("First", 0))
    document.outlines.add(OutlineItem("Last", 5))
    document.encrypt("user", "owner", algorithm=algorithm)
    with Document(_save(document), password="user") as loaded:
        with _chapters(loaded) as chapters:
            assert [doc.page_count for doc in chapters] == [5, 1]
            for chapter in chapters:
                with Document(_save(chapter)) as reopened:
                    assert not reopened.is_encrypted
                    assert _texts(reopened) == _texts(chapter)
        with Document(_save(loaded), password="user") as reopened_source:
            assert _texts(reopened_source) == _texts(document)


@pytest.mark.parametrize("title", ["", "../unsafe/name", "同じ章\nTitle"])
def test_titles_are_metadata_and_never_output_paths(document, title):
    document.outlines.add(OutlineItem(title, 0))
    with _chapters(document) as chapters:
        assert len(chapters) == 1
        assert chapters[0].info["Title"] == title
        assert chapters[0].file_name is None
        with Document(_save(chapters[0])) as reopened:
            assert reopened.info["Title"] == title


def test_title_size_and_unicode_are_checked_before_import():
    with Document(limits=PdfLoadLimits(max_object_bytes=4)) as document:
        document.pages.add()
        document.outlines.add(OutlineItem("large title", 0))
        with pytest.raises(PdfResourceLimitException, match="max_object_bytes"):
            document.split_by_bookmarks()
    with Document() as document:
        document.pages.add()
        document.outlines.add(OutlineItem("bad\ud800", 0))
        with pytest.raises(PdfValidationException, match="Unicode"):
            document.split_by_bookmarks()


def test_lowcode_later_input_failure_does_not_write_earlier_results(document):
    document.outlines.add(OutlineItem("Chapter", 0))
    with Document() as no_bookmarks:
        no_bookmarks.pages.add()
        invalid_input = _save(no_bookmarks)
    target = ByteArrayDataSource(b"untouched")
    options = SplitOptions(bookmark_level=1).add_input(
        ByteArrayDataSource(_save(document))
    )
    options.add_input(ByteArrayDataSource(invalid_input)).add_output(target)
    with pytest.raises(PdfValidationException, match="No local bookmark"):
        Splitter().process(options)
    assert target.data == b"untouched"
