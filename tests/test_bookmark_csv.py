"""Simple bookmark CSV exchange is loss-aware and transactional."""

from __future__ import annotations

import csv
import io

import pytest

from aspose_pdf import (
    Document,
    FitDestination,
    GoToAction,
    GoToRAction,
    JavaScriptAction,
    NamedAction,
    OutlineItem,
    PdfLoadLimits,
    PdfResourceLimitException,
    URIAction,
    XYZDestination,
)
from aspose_pdf.exceptions import AsposePdfException, PdfValidationException

HEADER = b"level,title,page\r\n"


def _saved(document, **options):
    with io.BytesIO() as output:
        document.save(output, **options)
        return output.getvalue()


def _rows(data):
    return list(csv.reader(io.StringIO(data.decode("utf-8"), newline="")))


@pytest.fixture
def document():
    with Document() as source:
        for number in range(1, 4):
            source.pages.add().add_text(f"Page {number}", 72, 700)
        yield source


@pytest.fixture
def bookmarked(document):
    root = document.outlines.add(OutlineItem("Part, one\nГлава 🌍", 1))
    root.add(OutlineItem("Section", 3)).add(OutlineItem("Heading", page_index=None))
    root.add(OutlineItem("Section", 2))
    document.outlines.add(OutlineItem("Appendix", destination=FitDestination(3)))
    return document


@pytest.mark.parametrize("kind", ["live", "bytes", "file", "streaming"])
def test_hierarchy_unicode_page_numbers_and_pdf_round_trips(bookmarked, tmp_path, kind):
    original = _saved(bookmarked)
    path = tmp_path / "bookmarked.pdf"
    path.write_bytes(original)
    source = (
        bookmarked
        if kind == "live"
        else Document(original)
        if kind == "bytes"
        else (Document.open_streaming if kind == "streaming" else Document)(path)
    )
    try:
        data = source.export_bookmarks_csv()
        assert _rows(data) == [
            ["level", "title", "page"],
            ["1", "Part, one\nГлава 🌍", "1"],
            ["2", "Section", "3"],
            ["3", "Heading", ""],
            ["2", "Section", "2"],
            ["1", "Appendix", "3"],
        ]
        assert data.endswith(b"\r\n") and source.export_bookmarks_csv() == data
        with Document() as target:
            for _ in range(3):
                target.pages.add()
            assert target.import_bookmarks_csv(data) == 5
            assert target.export_bookmarks_csv() == data
            with Document(_saved(target)) as reopened:
                assert reopened.export_bookmarks_csv() == data
                assert reopened.outlines[0].children[0].children[0].page_index is None
        assert _saved(source) == original
    finally:
        if source is not bookmarked:
            source.dispose()
    assert path.read_bytes() == original


@pytest.mark.parametrize("kind", ["bytes", "bytearray", "path", "short-stream"])
def test_input_sources_short_reads_current_position_and_stream_ownership(
    document, tmp_path, kind
):
    data = HEADER + b'1,"Title, quoted",2\r\n'
    path = tmp_path / "tree.csv"
    path.write_bytes(data)

    class Short(io.BytesIO):
        def read(self, size=-1):
            return super().read(min(size, 3))

    with Short(b"prefix" + data) as stream:
        stream.seek(6)
        source = {
            "bytes": data,
            "bytearray": bytearray(data),
            "path": path,
            "short-stream": stream,
        }[kind]
        assert document.import_bookmarks_csv(source) == 1
        assert (
            document.outlines[0].title == "Title, quoted"
            and document.outlines[0].page_index == 2
        )
        assert not stream.closed
        assert path.read_bytes() == data and stream.getvalue() == b"prefix" + data


@pytest.mark.parametrize(
    "encoding,delimiter",
    [("utf-8", ","), ("utf-8-sig", ";"), ("utf-16", "\t"), ("cp1251", ";")],
)
def test_custom_encoding_delimiter_whitespace_titles_and_blank_records(
    document, encoding, delimiter
):
    document.outlines.add(OutlineItem(' Глава; "one"\nNext ', page_index=None))
    data = document.export_bookmarks_csv(encoding=encoding, delimiter=delimiter)
    title = document.outlines[0].title
    document.outlines.clear()
    assert (
        document.import_bookmarks_csv(data, encoding=encoding, delimiter=delimiter) == 1
    )
    assert (
        document.outlines[0].title == title and document.outlines[0].page_index is None
    )
    assert document.export_bookmarks_csv(encoding=encoding, delimiter=delimiter) == data


def test_default_import_accepts_bom_and_blank_lines_with_numeric_whitespace(document):
    data = b"\xef\xbb\xbf\nlevel,title,page\n\n 1 , title , 2 \n2,,\n"
    assert document.import_bookmarks_csv(data) == 2
    assert (
        document.outlines[0].title == " title " and document.outlines[0].page_index == 2
    )
    assert (
        document.outlines[0].children[0].title == ""
        and document.outlines[0].children[0].page_index is None
    )


def test_append_replace_clear_and_existing_handles_keep_identity(bookmarked):
    collection = bookmarked.outlines
    existing = collection[0]
    data = HEADER + b"1,Added,2\r\n2,Child,3\r\n"
    assert bookmarked.import_bookmarks_csv(data) == 2
    assert bookmarked.import_bookmarks_csv(data) == 2
    assert bookmarked.outlines is collection and collection[0] is existing
    assert [item.title for item in collection] == [
        "Part, one\nГлава 🌍",
        "Appendix",
        "Added",
        "Added",
    ]
    assert bookmarked.import_bookmarks_csv(data, replace=True) == 2
    assert len(collection) == 1 and collection[0].title == "Added"
    assert bookmarked.import_bookmarks_csv(HEADER, replace=True) == 0
    assert len(collection) == 0


def test_empty_append_keeps_unopened_collection_and_nonempty_import_requires_pages(
    document,
):
    assert document._outlines is None
    assert document.import_bookmarks_csv(HEADER) == 0 and document._outlines is None
    with Document() as empty:
        assert empty.export_bookmarks_csv() == HEADER
        assert empty.import_bookmarks_csv(HEADER, replace=True) == 0
        with pytest.raises(PdfValidationException, match="requires a document page"):
            empty.import_bookmarks_csv(HEADER + b"1,Heading,\r\n")


@pytest.mark.parametrize("incremental", [False, True])
def test_header_only_replacement_clears_saved_bookmarks(bookmarked, incremental):
    original = _saved(bookmarked)
    with Document(original) as loaded:
        assert loaded._outlines is None
        assert loaded.import_bookmarks_csv(HEADER, replace=True) == 0
        updated = _saved(loaded, incremental=incremental)
        if incremental:
            assert updated.startswith(original)
    with Document(updated) as reopened:
        assert reopened.export_bookmarks_csv() == HEADER


def test_replacement_reuses_outline_slots_at_the_pdf_object_limit(bookmarked):
    original = _saved(bookmarked)
    with Document(original) as source:
        cos = source._engine_pdf._cos_doc
        maximum = max(max(cos.objects), max(cos.generations))
    with Document(original, limits=PdfLoadLimits(max_objects=maximum + 1)) as loaded:
        assert (
            loaded.import_bookmarks_csv(HEADER + b"1,Replacement,1\n", replace=True)
            == 1
        )
        with Document(_saved(loaded)) as reopened:
            assert (
                len(reopened.outlines) == 1
                and reopened.outlines[0].title == "Replacement"
            )


@pytest.mark.parametrize(
    "csv_data",
    [
        b"",
        b"title,level,page\n1,Wrong,1\n",
        b"level,title,title\n1,Wrong,1\n",
        HEADER + b"1,Valid,1\n2,Bad\n",
        HEADER + b"1,Valid,1\n2,Bad,1,extra\n",
        HEADER + b"2,Missing parent,1\n",
        HEADER + b"1,Valid,1\n3,Skipped,1\n",
        HEADER + b"1,Valid,1\n0,Bad,1\n",
        HEADER + b"1,Valid,1\n-1,Bad,1\n",
        HEADER + b"1,Valid,1\n1.0,Bad,1\n",
        HEADER + b"1,Valid,1\n2,Bad,0\n",
        HEADER + b"1,Valid,1\n2,Bad,4\n",
        HEADER + b"1,Valid,1\n2,Bad,2.5\n",
        HEADER + b"1,Valid,1\n2,Bad,1e0\n",
        HEADER + b'1,Valid,1\n2,"Unclosed,1\n',
        HEADER + b"1,Valid,1\n2,\xff,1\n",
    ],
)
@pytest.mark.parametrize("replace", [False, True])
def test_invalid_later_rows_are_atomic(bookmarked, csv_data, replace):
    collection = bookmarked.outlines
    original_items = list(collection)
    before = _saved(bookmarked)
    with pytest.raises(PdfValidationException):
        bookmarked.import_bookmarks_csv(csv_data, replace=replace)
    assert bookmarked.outlines is collection and list(collection) == original_items
    assert _saved(bookmarked) == before


def test_page_mapping_binds_page_identity_and_export_reads_unsaved_edits(document):
    assert (
        document.import_bookmarks_csv(
            HEADER + b"1,First,9\n2,Second,8\n", page_map={9: 2, 8: 3}
        )
        == 2
    )
    document.pages.insert(1)
    assert _rows(document.export_bookmarks_csv())[1:] == [
        ["1", "First", "3"],
        ["2", "Second", "4"],
    ]
    document.outlines[0].title = "Edited"
    document.outlines[0].children[0].page_index = 1
    assert _rows(document.export_bookmarks_csv())[1:] == [
        ["1", "Edited", "3"],
        ["2", "Second", "1"],
    ]


@pytest.mark.parametrize(
    "mapping", [{}, {1: 2}, {2: 4}, {True: 1}, {2: False}, {0: 1}, {2: 1.5}]
)
def test_incomplete_or_invalid_maps_leave_the_tree_unchanged(bookmarked, mapping):
    before = bookmarked.export_bookmarks_csv()
    with pytest.raises(PdfValidationException):
        bookmarked.import_bookmarks_csv(HEADER + b"1,New,2\n", page_map=mapping)
    assert bookmarked.export_bookmarks_csv() == before


@pytest.mark.parametrize(
    "destination",
    [
        XYZDestination(1, top=700),
        GoToAction(XYZDestination(2)),
        URIAction("https://example.invalid"),
        GoToRAction("other.pdf", FitDestination(1)),
        JavaScriptAction("noop()"),
        NamedAction("NextPage"),
        "named-section",
    ],
)
def test_unrepresentable_targets_fail_before_any_output(
    document, tmp_path, destination
):
    document.destinations["named-section"] = FitDestination(1)
    document.outlines.add(OutlineItem("Simple", 1))
    document.outlines.add(OutlineItem("Unsupported", destination=destination))
    path = tmp_path / "tree.csv"
    path.write_bytes(b"Keep")
    with io.BytesIO(b"Keep") as stream:
        for target in (path, stream):
            with pytest.raises(PdfValidationException, match="JSON"):
                document.export_bookmarks_csv(target)
            assert path.read_bytes() == stream.getvalue() == b"Keep"


@pytest.mark.parametrize(
    "style",
    [{"is_bold": True}, {"is_italic": True}, {"color": (0, 0, 0)}, {"open": True}],
)
def test_styles_and_open_state_are_explicitly_rejected(document, style):
    document.outlines.add(OutlineItem("Styled", 1, **style))
    with pytest.raises(PdfValidationException, match="JSON"):
        document.export_bookmarks_csv()


def test_simple_goto_actions_normalize_to_fit_page_destinations(document):
    document.outlines.add(
        OutlineItem("Local action", destination=GoToAction(FitDestination(2)))
    )
    data = document.export_bookmarks_csv()
    assert document.import_bookmarks_csv(data, replace=True) == 1
    assert document.outlines[0].destination == FitDestination(2)


def test_append_preserves_unopened_unsupported_existing_actions(document):
    document.outlines.add(
        OutlineItem("Script", destination=JavaScriptAction("noop()"), color=(1, 0, 0))
    )
    with Document(_saved(document)) as loaded:
        assert loaded._outlines is None
        assert loaded.import_bookmarks_csv(HEADER + b"1,Added,2\n") == 1
        assert loaded.outlines[0].destination == JavaScriptAction("noop()")
        assert loaded.outlines[0].color == (1, 0, 0)
        with Document(_saved(loaded)) as reopened:
            assert reopened.outlines[0].destination == JavaScriptAction("noop()")
            assert reopened.outlines[1].page_index == 2


def test_file_outputs_short_writes_stalled_stream_and_overwrite(bookmarked, tmp_path):
    expected = bookmarked.export_bookmarks_csv()
    path = tmp_path / "new" / "tree.csv"
    assert bookmarked.export_bookmarks_csv(path) == path.read_bytes() == expected
    with pytest.raises(FileExistsError):
        bookmarked.export_bookmarks_csv(path, overwrite=False)

    class Short(io.BytesIO):
        def write(self, data):
            return super().write(data[:3])

    with Short(b"prefix") as stream:
        stream.seek(6)
        assert bookmarked.export_bookmarks_csv(stream) == expected
        assert stream.getvalue() == b"prefix" + expected and not stream.closed

    class Stalled:
        def write(self, data):
            return 0

    with pytest.raises(AsposePdfException, match="Stream write"):
        bookmarked.export_bookmarks_csv(Stalled())


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("encrypted", [False, True])
def test_metadata_only_encrypted_streaming_and_incremental_saves(
    bookmarked, tmp_path, monkeypatch, streaming, encrypted
):
    if encrypted:
        bookmarked.encrypt("user", "owner", algorithm="AES-256")
    path = tmp_path / "bookmarks.pdf"
    bookmarked.save(path)
    original = path.read_bytes()
    with (Document.open_streaming if streaming else Document)(
        path, password="owner" if encrypted else None
    ) as loaded:
        engine = loaded._engine_pdf
        protected = loaded.is_encrypted
        cursor = engine._page_text_cursor
        with monkeypatch.context() as patch:
            patch.setattr(
                engine,
                "get_page_content",
                lambda *a: pytest.fail("Unexpected page read"),
            )
            data = loaded.export_bookmarks_csv()
            assert loaded._outlines is None
            assert loaded.import_bookmarks_csv(data, replace=True) == 5
            assert loaded.export_bookmarks_csv() == data
            assert (
                loaded.is_encrypted is protected and engine._page_text_cursor == cursor
            )
            if streaming:
                assert engine.page_contents == []
        updated = _saved(loaded, incremental=True)
        assert updated.startswith(original)
    with Document(updated, password="owner" if encrypted else None) as reopened:
        assert reopened.export_bookmarks_csv() == data
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "options", [{"replace": 1}, {"delimiter": ""}, {"encoding": "invalid-codec"}]
)
def test_import_options_are_validated_before_input_reads(document, options):
    class Unreadable:
        def read(self, size=-1):
            pytest.fail("Invalid options must not read CSV input")

    with pytest.raises(PdfValidationException):
        document.import_bookmarks_csv(Unreadable(), **options)


def test_cycles_repeated_nodes_and_stale_page_targets_are_rejected(document):
    root = document.outlines.add(OutlineItem("Root", 1))
    root.children.append(root)
    with pytest.raises(PdfValidationException, match="cycle"):
        document.export_bookmarks_csv()
    root.children.clear()
    document.outlines.add(root)
    with pytest.raises(PdfValidationException, match="repeated"):
        document.export_bookmarks_csv()
    document.outlines.clear()
    document.import_bookmarks_csv(HEADER + b"1,Last,3\n")
    document.pages.delete(3)
    with pytest.raises(PdfValidationException):
        document.export_bookmarks_csv()


def test_input_depth_combined_tree_and_object_limits_are_atomic():
    with Document(limits=PdfLoadLimits(max_input_bytes=30)) as document:
        document.pages.add()
        with pytest.raises(PdfResourceLimitException, match="max_input_bytes"):
            document.import_bookmarks_csv(HEADER + b"1,Very long bookmark title,1\n")
        assert document._outlines is None
    with Document(limits=PdfLoadLimits(max_nesting_depth=2)) as document:
        document.pages.add()
        with pytest.raises(PdfResourceLimitException, match="max_nesting_depth"):
            document.import_bookmarks_csv(HEADER + b"1,A,1\n2,B,1\n3,C,1\n")
        assert document._outlines is None
    with Document(limits=PdfLoadLimits(max_container_items=20)) as document:
        document.pages.add()
        for number in range(20):
            document.outlines.add(OutlineItem(str(number), 1))
        original = list(document.outlines)
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            document.import_bookmarks_csv(HEADER + b"1,New,1\n")
        assert list(document.outlines) == original
    with Document(limits=PdfLoadLimits(max_objects=8)) as document:
        document.pages.add()
        objects = set(document._engine_pdf._cos_doc.objects)
        with pytest.raises(PdfResourceLimitException, match="max_objects"):
            document.import_bookmarks_csv(HEADER + b"1,New,1\n" * 8)
        assert (
            document._outlines is None
            and set(document._engine_pdf._cos_doc.objects) == objects
        )


@pytest.mark.parametrize(
    "limit", ["max_decoded_stream_bytes", "max_total_decoded_bytes"]
)
def test_output_limits_and_encoding_errors_preserve_files(bookmarked, tmp_path, limit):
    expected = bookmarked.export_bookmarks_csv()
    path = tmp_path / "tree.csv"
    path.write_bytes(b"Keep")
    with pytest.raises(PdfValidationException, match="encoded as CSV"):
        bookmarked.export_bookmarks_csv(path, encoding="ascii")
    assert path.read_bytes() == b"Keep"
    bookmarked._load_limits = PdfLoadLimits(**{limit: len(expected) - 1})
    with pytest.raises(PdfResourceLimitException, match=limit):
        bookmarked.export_bookmarks_csv(path)
    assert path.read_bytes() == b"Keep"
    bookmarked._load_limits = PdfLoadLimits(**{limit: len(expected)})
    assert bookmarked.export_bookmarks_csv(path) == expected


def test_disposed_documents_reject_both_csv_methods(document):
    document.dispose()
    for method, args in [
        (document.export_bookmarks_csv, ()),
        (document.import_bookmarks_csv, (HEADER,)),
    ]:
        with pytest.raises(AsposePdfException, match="disposed"):
            method(*args)
