"""Versioned bookmark JSON exchange and transactional tree-edit regressions."""

from __future__ import annotations

import io
import json

import pytest

from aspose_pdf import (
    Document,
    FitBDestination,
    FitBHDestination,
    FitBVDestination,
    FitDestination,
    FitHDestination,
    FitRDestination,
    FitVDestination,
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
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfBoolean,
    PdfDictionary,
    PdfIndirectReference,
    PdfName,
    PdfNull,
    PdfNumber,
    PdfStream,
    PdfString,
)
from aspose_pdf.exceptions import AsposePdfException, PdfValidationException


def _page(index=1, view="Fit", parameters=None):
    return {
        "kind": "page",
        "page_index": index,
        "view": view,
        "parameters": [] if parameters is None else parameters,
    }


def _record(**changes):
    return {
        "title": "Chapter",
        "target": _page(),
        "is_bold": False,
        "is_italic": False,
        "color": None,
        "open": False,
        "children": [],
        **changes,
    }


def _data(*records):
    return json.dumps(
        {"schema_version": 1, "bookmarks": list(records)}, ensure_ascii=False
    ).encode()


def _saved(document, **options):
    output = io.BytesIO()
    document.save(output, **options)
    return output.getvalue()


def _payload(document, **options):
    return json.loads(document.export_bookmarks_json(**options))


@pytest.fixture
def document():
    with Document() as doc:
        for _ in range(3):
            doc.pages.add()
        yield doc


@pytest.fixture
def bookmarked(document):
    chapter = document.outlines.add(
        OutlineItem(
            "Глава 🌍\nOne",
            destination=XYZDestination(1, 0, 700, 2),
            is_bold=True,
            is_italic=True,
            color=(0.1, 0.2, 0.3),
            open=True,
        )
    )
    chapter.add(OutlineItem("Section", 2)).add(
        OutlineItem("Heading only", page_index=None)
    )
    document.outlines.add(
        OutlineItem("Website", destination=URIAction("https://example.invalid/"))
    )
    return document


@pytest.mark.parametrize("kind", ["live", "bytes", "file", "streaming"])
def test_versioned_unicode_hierarchy_style_and_pdf_roundtrips(
    bookmarked, kind, tmp_path
):
    raw = _saved(bookmarked)
    path = tmp_path / "source.pdf"
    path.write_bytes(raw)
    source = (
        bookmarked
        if kind == "live"
        else Document(raw)
        if kind == "bytes"
        else (Document.open_streaming if kind == "streaming" else Document)(path)
    )
    try:
        data = source.export_bookmarks_json()
        tree = json.loads(data)
        assert isinstance(data, bytes) and data.endswith(b"\n")
        assert not data.startswith(b"\xef\xbb\xbf") and "Глава".encode() in data
        assert (
            list(tree) == ["schema_version", "bookmarks"]
            and tree["schema_version"] == 1
        )
        root = tree["bookmarks"][0]
        assert root["title"] == "Глава 🌍\nOne"
        assert root["is_bold"] and root["is_italic"] and root["open"]
        assert root["color"] == [0.1, 0.2, 0.3]
        assert root["target"] == _page(1, "XYZ", [0, 700, 2])
        assert root["children"][0]["target"] == _page(2)
        assert root["children"][0]["children"][0]["target"] is None
        assert source.export_bookmarks_json() == data
        with Document() as target:
            for _ in range(3):
                target.pages.add()
            assert target.import_bookmarks_json(data) == 4
            assert _payload(target) == tree
            for pdf in (_saved(target),):
                with Document(pdf) as reopened:
                    assert _payload(reopened) == tree
                    assert reopened.validate_links() == []
    finally:
        if source is not bookmarked:
            source.dispose()


@pytest.mark.parametrize(
    "destination",
    [
        FitDestination(1),
        FitBDestination(2),
        XYZDestination(0),
        FitHDestination(1),
        FitBHDestination(1, 200),
        FitVDestination(2, 10),
        FitBVDestination(2),
        FitRDestination(1, 1, 2, 100, 200),
    ],
)
@pytest.mark.parametrize("action", [False, True])
def test_all_eight_local_views_recover_typed_targets(document, destination, action):
    target = GoToAction(destination) if action else destination
    document.outlines.add(OutlineItem("view", destination=target))
    data = document.export_bookmarks_json()
    assert document.import_bookmarks_json(data, replace=True) == 1
    assert document.outlines[0].destination == target
    with Document(_saved(document)) as reopened:
        assert reopened.outlines[0].destination == target


@pytest.mark.parametrize(
    "kind", ["bytes", "bytearray", "path", "str", "stream", "short"]
)
def test_import_input_types_bom_short_reads_and_caller_streams(
    document, tmp_path, kind
):
    data = b"\xef\xbb\xbf" + _data(_record(title="Привет"))
    path = tmp_path / "bookmarks.json"
    path.write_bytes(data)

    class ShortReader(io.BytesIO):
        def read(self, size=-1):
            return super().read(min(size, 9) if size >= 0 else size)

    source = {
        "bytes": data,
        "bytearray": bytearray(data),
        "path": path,
        "str": str(path),
        "stream": io.BytesIO(b"skip" + data),
        "short": ShortReader(data),
    }[kind]
    if kind == "stream":
        source.seek(4)
    assert document.import_bookmarks_json(source) == 1
    assert document.outlines[0].title == "Привет"
    if hasattr(source, "read"):
        assert not source.closed and source.read() == b""


def test_append_replace_order_duplicates_and_live_collection_identity(bookmarked):
    collection = bookmarked.outlines
    existing = collection[0]
    data = _data(_record(title="New", children=[_record(title="Child")]))
    assert bookmarked.import_bookmarks_json(data) == 2
    assert bookmarked.outlines is collection and collection[0] is existing
    assert [item.title for item in collection] == ["Глава 🌍\nOne", "Website", "New"]
    assert bookmarked.import_bookmarks_json(data) == 2
    assert len(collection) == 4
    assert bookmarked.import_bookmarks_json(data, replace=True) == 2
    assert bookmarked.outlines is collection and len(collection) == 1
    assert existing.title == "Глава 🌍\nOne"


@pytest.mark.parametrize("incremental", [False, True])
@pytest.mark.parametrize("method", ["import", "clear"])
def test_empty_replacement_or_public_clear_removes_saved_bookmarks(
    bookmarked, incremental, method
):
    original = _saved(bookmarked)
    with Document(original) as reopened:
        if method == "import":
            assert reopened.import_bookmarks_json(_data(), replace=True) == 0
        else:
            reopened.outlines.clear()
        saved = _saved(reopened, incremental=incremental)
        if incremental:
            assert saved.startswith(original)
    with Document(saved) as reopened:
        assert len(reopened.outlines) == 0
        catalog = reopened._engine_pdf._resolve(
            reopened._engine_pdf._cos_doc.trailer.mapping[PdfName("Root")]
        )
        assert PdfName("Outlines") not in catalog.mapping


def test_empty_append_does_not_initialize_lazy_outline_collection(document):
    assert document._outlines is None
    assert document.import_bookmarks_json(_data()) == 0
    assert document._outlines is None


def test_append_to_unopened_loaded_collection_preserves_existing_targets(bookmarked):
    with Document(_saved(bookmarked)) as reopened:
        assert reopened._outlines is None
        assert reopened.import_bookmarks_json(_data(_record(title="Added"))) == 1
        assert [item.title for item in reopened.outlines] == [
            "Глава 🌍\nOne",
            "Website",
            "Added",
        ]
        with Document(_saved(reopened)) as again:
            assert len(again.outlines) == 3
            assert again.outlines[0].destination == XYZDestination(1, 0, 700, 2)


def test_named_destinations_are_portable_by_default_or_can_be_retained(document):
    document.destinations["chapter"] = XYZDestination(2, 10, 300, 1)
    document.outlines.add(OutlineItem("named", destination="chapter"))
    resolved = _payload(document)
    retained = _payload(document, resolve_named_destinations=False)
    assert resolved["bookmarks"][0]["target"] == _page(2, "XYZ", [10, 300, 1])
    assert retained["bookmarks"][0]["target"] == {
        "kind": "named",
        "name": "chapter",
        "representation": "string",
    }
    with Document() as target:
        for _ in range(3):
            target.pages.add()
        target.import_bookmarks_json(json.dumps(resolved).encode(), page_map={2: 0})
        assert target.outlines[0].destination == XYZDestination(0, 10, 300, 1)
        assert target.validate_links() == []
        target.destinations["chapter"] = 1
        target.import_bookmarks_json(json.dumps(retained).encode(), replace=True)
        assert (
            target.outlines[0].destination_name == "chapter"
            and target.outlines[0].page_index == 1
        )


@pytest.mark.parametrize("goto", [False, True])
@pytest.mark.parametrize("representation", ["name", "string"])
def test_retained_names_preserve_representation_and_goto_actions(
    document, goto, representation
):
    target = {"kind": "named", "name": "chapteré", "representation": representation}
    if goto:
        target = {"kind": "goto", "destination": target}
    document.destinations["chapteré"] = 2
    data = _data(_record(target=target))
    document.import_bookmarks_json(data)
    assert _payload(document, resolve_named_destinations=False) == json.loads(data)
    with Document(_saved(document)) as reopened:
        assert _payload(reopened, resolve_named_destinations=False) == json.loads(data)
        resolved = _payload(reopened)["bookmarks"][0]["target"]
        assert (resolved["destination"] if goto else resolved) == _page(2)


def test_unresolved_names_can_be_retained_but_default_export_refuses_them(
    document, tmp_path
):
    document.import_bookmarks_json(
        _data(
            _record(
                target={"kind": "named", "name": "absent", "representation": "string"}
            )
        )
    )
    path = tmp_path / "existing.json"
    path.write_bytes(b"original")
    with pytest.raises(PdfValidationException, match="named destination"):
        document.export_bookmarks_json(path)
    assert path.read_bytes() == b"original"
    assert (
        _payload(document, resolve_named_destinations=False)["bookmarks"][0]["target"][
            "name"
        ]
        == "absent"
    )


@pytest.mark.parametrize(
    "target",
    [
        {"kind": "uri", "uri": "https://example.invalid/путь", "is_map": True},
        {"kind": "uri", "uri": "https://example.invalid", "is_map": None},
        {
            "kind": "remote",
            "file": "nonexistent.pdf",
            "destination": _page(100, "XYZ", [0, None, 2]),
            "new_window": True,
        },
        {
            "kind": "remote",
            "file": "nonexistent.pdf",
            "destination": {
                "kind": "named",
                "name": "remote-name",
                "representation": "name",
            },
            "new_window": False,
        },
        {"kind": "named_action", "name": "NextPage"},
    ],
)
def test_uri_remote_and_named_actions_survive_pdf_roundtrip_without_access(
    document, target
):
    data = _data(_record(target=target))
    assert document.import_bookmarks_json(data, page_map={1: 0}) == 1
    assert _payload(document) == json.loads(data)
    with Document(_saved(document)) as reopened:
        assert _payload(reopened) == json.loads(data)


@pytest.mark.parametrize(
    "target",
    [
        URIAction("https://example.invalid"),
        GoToRAction("file.pdf", FitDestination(50)),
        NamedAction("NextPage"),
    ],
)
def test_typed_action_export_and_import(document, target):
    document.outlines.add(OutlineItem("action", destination=target))
    data = document.export_bookmarks_json()
    assert document.import_bookmarks_json(data, replace=True) == 1
    assert document.outlines[0].destination == target


def test_page_mapping_is_recursive_and_imported_references_follow_insertions(document):
    data = _data(
        _record(
            target=_page(9),
            children=[_record(target={"kind": "goto", "destination": _page(8)})],
        )
    )
    assert document.import_bookmarks_json(data, page_map={9: 0, 8: 2}) == 2
    document.pages.insert(0)
    tree = _payload(document)["bookmarks"][0]
    assert tree["target"]["page_index"] == 1
    assert tree["children"][0]["target"]["destination"]["page_index"] == 3


def test_export_reads_current_page_identity_and_unsaved_target_edits(bookmarked):
    with Document(_saved(bookmarked)) as reopened:
        reopened.pages.insert(0)
        assert _payload(reopened)["bookmarks"][0]["target"]["page_index"] == 2
        reopened.outlines[0].destination = FitDestination(0)
        assert _payload(reopened)["bookmarks"][0]["target"] == _page(0)


def test_export_rejects_deleted_or_stale_explicit_targets(document):
    document.outlines.add(OutlineItem("page", destination=FitDestination(2)))
    with Document(_saved(document)) as reopened:
        reopened.pages.delete(2)
        with pytest.raises(PdfValidationException, match="current document page"):
            reopened.export_bookmarks_json()


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"null",
        b"[]",
        b"{}",
        b"\xff",
        b'{"schema_version":1,"bookmarks":[],}',
        b'{"schema_version":1,"schema_version":1,"bookmarks":[]}',
        b'{"schema_version":true,"bookmarks":[]}',
        b'{"schema_version":2,"bookmarks":[]}',
        b'{"schema_version":1,"bookmarks":{}}',
        b'{"schema_version":1,"bookmarks":[],"extra":1}',
        b'{"schema_version":1,"bookmarks":[NaN]}',
        b'{"schema_version":1,"bookmarks":[1e999]}',
        b'{"schema_version":1,"bookmarks":[{"title":"a","title":"b"}]}',
    ],
)
def test_malformed_json_does_not_replace_existing_tree(bookmarked, data):
    before = bookmarked.export_bookmarks_json()
    collection = bookmarked.outlines
    with pytest.raises(PdfValidationException):
        bookmarked.import_bookmarks_json(data, replace=True)
    assert (
        bookmarked.outlines is collection
        and bookmarked.export_bookmarks_json() == before
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"title": None},
        {"title": "\ud800"},
        {"is_bold": 1},
        {"is_italic": None},
        {"open": "yes"},
        {"children": {}},
        {"children": [None]},
        {"extra": 1},
        {"color": [0, 1]},
        {"color": [1, 0, 2]},
        {"color": [True, 0, 0]},
        {"target": []},
        {"target": {"kind": "script", "script": "bad"}},
    ],
)
def test_invalid_late_record_is_atomic(bookmarked, changes):
    before = bookmarked.export_bookmarks_json()
    payload = {"schema_version": 1, "bookmarks": [_record(), _record(**changes)]}
    with pytest.raises(PdfValidationException):
        bookmarked.import_bookmarks_json(json.dumps(payload).encode(), replace=True)
    assert bookmarked.export_bookmarks_json() == before


@pytest.mark.parametrize(
    "target",
    [
        _page(-1),
        _page(True),
        _page(3),
        _page(1.0),
        _page(1, "Unknown"),
        _page(1, "XYZ", []),
        _page(1, "XYZ", [None, None, -1]),
        _page(1, "FitH", [True]),
        _page(1, "FitR", [0, 0, None, 10]),
        _page(1, "FitR", [10, 0, 0, 10]),
        {"kind": "page", "page_index": 1, "view": "Fit"},
        {"kind": "named", "name": "глава", "representation": "string"},
        {"kind": "named", "name": "/bad", "representation": "name"},
        {"kind": "uri", "uri": 7, "is_map": None},
        {"kind": "uri", "uri": "x", "is_map": 1},
        {
            "kind": "remote",
            "file": "f",
            "destination": _page(2**31),
            "new_window": None,
        },
        {"kind": "remote", "file": "f", "destination": _page(True), "new_window": None},
        {"kind": "named_action", "name": ""},
        {"kind": "goto", "destination": None},
    ],
)
def test_invalid_target_payloads_are_rejected_before_mutation(bookmarked, target):
    before = bookmarked.export_bookmarks_json()
    with pytest.raises(PdfValidationException):
        bookmarked.import_bookmarks_json(_data(_record(), _record(target=target)))
    assert bookmarked.export_bookmarks_json() == before


@pytest.mark.parametrize(
    "mapping", [[], {}, {True: 0}, {-1: 0}, {1: False}, {1: 3}, {1: -1}, {1: 0, 2: 99}]
)
def test_invalid_or_incomplete_page_maps_are_atomic(bookmarked, mapping):
    before = bookmarked.export_bookmarks_json()
    with pytest.raises(PdfValidationException):
        bookmarked.import_bookmarks_json(_data(_record()), page_map=mapping)
    assert bookmarked.export_bookmarks_json() == before


@pytest.mark.parametrize(
    "target",
    [
        JavaScriptAction("app.alert(1)"),
        GoToRAction("file.pdf"),
    ],
)
def test_unsupported_or_incomplete_action_export_fails_before_writing(document, target):
    document.outlines.add(OutlineItem("unsupported", destination=target))
    stream = io.BytesIO(b"original")
    with pytest.raises(PdfValidationException):
        document.export_bookmarks_json(stream)
    assert stream.getvalue() == b"original"


@pytest.mark.parametrize("extra", ["Next", "Custom"])
def test_action_entries_are_not_silently_lost(document, extra):
    item = document.outlines.add(OutlineItem("action", page_index=None))
    item._loaded_target = (
        "A",
        PdfDictionary(
            {
                PdfName("S"): PdfName("URI"),
                PdfName("URI"): PdfString("https://example.invalid"),
                PdfName(extra): PdfNull(),
            }
        ),
    )
    with pytest.raises(PdfValidationException, match="action entries"):
        document.export_bookmarks_json()


@pytest.mark.parametrize("repeat", [False, True])
def test_live_tree_cycles_and_reused_nodes_are_refused(document, repeat):
    root = document.outlines.add(OutlineItem("root", 1))
    root.children.append(root)
    if repeat:
        root.children.clear()
        document.outlines._items.append(root)
    with pytest.raises(PdfValidationException, match="cycle or repeated"):
        document.export_bookmarks_json()
    with pytest.raises(PdfValidationException, match="cycle or repeated"):
        document.import_bookmarks_json(_data(_record()))
    assert document.outlines[0] is root
    assert document.import_bookmarks_json(_data(_record()), replace=True) == 1


def test_export_is_detached_read_only_and_does_not_initialize_live_outlines(bookmarked):
    original = _saved(bookmarked)
    with Document(original) as document:
        engine = document._engine_pdf
        numbers = set(engine._cos_doc.objects)
        outlines = engine._outlines_data
        data = document.export_bookmarks_json()
        assert document._outlines is None and engine._outlines_data is outlines
        assert set(engine._cos_doc.objects) == numbers
        assert _saved(document) == original
        edited = json.loads(data)
        edited["bookmarks"][0]["children"].clear()
        assert document.export_bookmarks_json() == data


def test_files_atomic_overwrite_and_short_output_streams(
    bookmarked, tmp_path, monkeypatch
):
    path = tmp_path / "nested/bookmarks.json"
    data = bookmarked.export_bookmarks_json(path, overwrite=False)
    assert path.read_bytes() == data
    with pytest.raises(FileExistsError):
        bookmarked.export_bookmarks_json(path, overwrite=False)

    class ShortWriter(io.BytesIO):
        def write(self, data):
            return super().write(data[:7])

    output = ShortWriter(b"prefix")
    output.seek(6)
    assert bookmarked.export_bookmarks_json(output) == data
    assert output.getvalue() == b"prefix" + data and not output.closed

    def fail_replace(*args):
        raise OSError("injected replace failure")

    monkeypatch.setattr("aspose_pdf.engine.file_output.os.replace", fail_replace)
    with pytest.raises(OSError, match="injected"):
        bookmarked.export_bookmarks_json(path)
    assert path.read_bytes() == data


@pytest.mark.parametrize("value", [0, -1, None, "1", True])
def test_invalid_output_stream_progress_is_rejected(bookmarked, value):
    class BadWriter:
        def write(self, data):
            return value

    with pytest.raises(AsposePdfException):
        bookmarked.export_bookmarks_json(BadWriter())


@pytest.mark.parametrize(
    "option", [{"overwrite": 1}, {"resolve_named_destinations": 1}]
)
def test_export_options_require_booleans(document, option):
    with pytest.raises(PdfValidationException):
        document.export_bookmarks_json(**option)


@pytest.mark.parametrize("source", [None, 1, {}, io.StringIO("{}")])
def test_invalid_import_sources(document, source):
    with pytest.raises(TypeError):
        document.import_bookmarks_json(source)


def test_replace_boolean_is_validated_before_input_reads(document):
    class ForbiddenReader:
        def read(self, *args):
            raise AssertionError("Invalid replace must not read input")

    with pytest.raises(PdfValidationException, match="replace"):
        document.import_bookmarks_json(ForbiddenReader(), replace=1)


@pytest.mark.parametrize("kind", ["bytes", "path", "stream"])
def test_input_byte_limit(kind, tmp_path):
    data = _data(_record())
    path = tmp_path / "large.json"
    path.write_bytes(data)
    with Document(limits=PdfLoadLimits(max_input_bytes=32)) as document:
        document.pages.add()
        source = {"bytes": data, "path": path, "stream": io.BytesIO(data)}[kind]
        with pytest.raises(PdfResourceLimitException, match="max_input_bytes"):
            document.import_bookmarks_json(source)
        assert document._outlines is None


@pytest.mark.parametrize(
    "limits,data,match",
    [
        (PdfLoadLimits(max_nesting_depth=3), _data(_record()), "max_nesting_depth"),
        (PdfLoadLimits(max_container_items=4), _data(_record()), "max_container_items"),
        (
            PdfLoadLimits(max_container_items=20),
            _data(_record(), _record()),
            "max_container_items",
        ),
        (
            PdfLoadLimits(max_object_bytes=24),
            _data(_record(title="x" * 25)),
            "max_object_bytes",
        ),
    ],
)
def test_import_structural_and_string_limits_leave_tree_unchanged(limits, data, match):
    with Document(limits=limits) as document:
        document.pages.add()
        with pytest.raises(PdfResourceLimitException, match=match):
            document.import_bookmarks_json(data)
        assert document._outlines is None


def test_export_and_import_agree_on_actual_json_nesting_limits():
    with Document(limits=PdfLoadLimits(max_nesting_depth=8)) as document:
        document.pages.add()
        root = document.outlines.add(OutlineItem("root", page_index=None))
        for _ in range(3):
            root = root.add(OutlineItem("child", page_index=None))
        with pytest.raises(PdfResourceLimitException, match="max_nesting_depth"):
            document.export_bookmarks_json()


@pytest.mark.parametrize("encrypted", [False, True])
def test_streaming_exchange_and_save_do_not_decode_content(
    bookmarked, tmp_path, monkeypatch, encrypted
):
    bookmarked.pages[0].add_text("Do not decode while exchanging bookmarks", 20, 100)
    if encrypted:
        bookmarked.encrypt(user_password="secret", owner_password="owner")
    path = tmp_path / "source.pdf"
    bookmarked.save(path)
    password = "secret" if encrypted else None
    with Document.open_streaming(path, password=password) as document:

        def no_decode(*args, **kwargs):
            raise AssertionError("Bookmark JSON must not decode page content")

        with monkeypatch.context() as patch:
            patch.setattr(document._engine_pdf, "get_page_content", no_decode)
            patch.setattr(document._engine_pdf, "_decode_cos_stream", no_decode)
            data = document.export_bookmarks_json()
            assert document.import_bookmarks_json(data, replace=True) == 4
        target = tmp_path / "result.pdf"
        document.save(target)
    with Document(target, password=password) as reopened:
        assert _payload(reopened) == json.loads(data)


def test_incremental_save_preserves_original_content_and_imported_bookmarks(document):
    document.pages[0].add_text("Original page text", 20, 100)
    original = _saved(document)
    with Document(original) as reopened:
        before = reopened.extract_text()
        assert (
            reopened.import_bookmarks_json(
                _data(_record(children=[_record(target=None)]))
            )
            == 2
        )
        output = _saved(reopened, incremental=True)
        assert output.startswith(original)
    with Document(output) as reopened:
        assert reopened.extract_text() == before and len(reopened.outlines) == 1
        assert reopened.validate_links() == []


def test_empty_documents_and_disposed_exchange():
    with Document() as document:
        assert _payload(document) == {"schema_version": 1, "bookmarks": []}
        assert document.import_bookmarks_json(_data(), replace=True) == 0
        with pytest.raises(PdfValidationException, match="requires a document page"):
            document.import_bookmarks_json(_data(_record(target=None)))
    for operation, args in (
        (document.export_bookmarks_json, ()),
        (document.import_bookmarks_json, (_data(),)),
    ):
        with pytest.raises(AsposePdfException, match="disposed"):
            operation(*args)


@pytest.mark.parametrize(
    "target",
    [XYZDestination(1, top=True), XYZDestination(1, zoom=float("nan")), URIAction(7)],
)
def test_invalid_live_typed_values_are_not_silently_coerced(document, target):
    document.outlines.add(OutlineItem("invalid", destination=target))
    with pytest.raises(PdfValidationException):
        document.export_bookmarks_json()


def test_append_refreshes_current_indices_for_unopened_existing_bookmarks(bookmarked):
    with Document(_saved(bookmarked)) as document:
        document.pages.insert(0)
        assert document._outlines is None
        document.import_bookmarks_json(_data(_record()))
        assert document.outlines[0].page_index == 2
        assert document.outlines[0].children[0].page_index == 3


def test_goto_action_with_a_named_target_exports_without_losing_action(document):
    document.destinations["chapter"] = 2
    document.outlines.add(OutlineItem("named", destination=GoToAction("chapter")))
    assert _payload(document)["bookmarks"][0]["target"] == {
        "kind": "goto",
        "destination": _page(2),
    }


@pytest.mark.parametrize(
    "kind", ["stream", "dictionary", "wrong_generation", "reference_cycle"]
)
def test_malformed_retained_targets_are_refused_before_output(document, kind):
    raw = (
        PdfStream(b"not decoded")
        if kind == "stream"
        else PdfDictionary({PdfName("D"): PdfNumber(1)})
    )
    if kind == "wrong_generation":
        ref = document._engine_pdf._make_page_ref(1)
        raw = PdfArray(
            [
                PdfIndirectReference(ref.object_number, ref.gen_number + 1),
                PdfName("Fit"),
            ]
        )
    elif kind == "reference_cycle":
        raw = document._engine_pdf._cos_doc.register_object(PdfNull())
        document._engine_pdf._cos_doc.objects[raw.object_number] = raw
    item = document.outlines.add(OutlineItem("bad", page_index=None))
    item._loaded_target = ("Dest", raw)
    with pytest.raises(PdfValidationException):
        document.export_bookmarks_json()


def test_indirect_named_goto_uri_flags_and_remote_window_flags_are_preserved(document):
    document.destinations["chapter"] = 1
    cos = document._engine_pdf._cos_doc
    item = document.outlines.add(OutlineItem("GoTo name", page_index=None))
    item._loaded_target = (
        "A",
        cos.register_object(
            PdfDictionary(
                {
                    PdfName("S"): PdfName("GoTo"),
                    PdfName("D"): cos.register_object(PdfName("chapter")),
                }
            )
        ),
    )
    item = document.outlines.add(OutlineItem("URI flag", page_index=None))
    item._loaded_target = (
        "A",
        PdfDictionary(
            {
                PdfName("S"): PdfName("URI"),
                PdfName("URI"): PdfString("https://example.invalid"),
                PdfName("IsMap"): PdfBoolean(False),
            }
        ),
    )
    tree = _payload(document)
    assert tree["bookmarks"][0]["target"] == {"kind": "goto", "destination": _page(1)}
    assert tree["bookmarks"][1]["target"]["is_map"] is False


def test_unsupported_existing_actions_survive_appending_new_bookmarks(document):
    document.outlines.add(
        OutlineItem("Script", destination=JavaScriptAction("original"))
    )
    with Document(_saved(document)) as reopened:
        assert reopened._outlines is None
        reopened.import_bookmarks_json(_data(_record(title="New")))
        assert reopened.outlines[0].destination == JavaScriptAction("original")
        with Document(_saved(reopened)) as again:
            assert again.outlines[0].destination == JavaScriptAction("original")
            assert again.outlines[1].title == "New"


def test_combined_tree_limit_checks_existing_nodes_before_append():
    with Document(limits=PdfLoadLimits(max_container_items=50)) as document:
        document.pages.add()
        for index in range(50):
            document.outlines.add(OutlineItem(str(index), 0))
        original = list(document.outlines)
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            document.import_bookmarks_json(_data(_record(target=None)))
        assert list(document.outlines) == original
        assert (
            document.import_bookmarks_json(_data(_record(target=None)), replace=True)
            == 1
        )


def test_failed_stream_read_does_not_mutate_or_close_source(bookmarked):
    class BrokenReader(io.BytesIO):
        def read(self, size=-1):
            if self.tell():
                raise OSError("injected read failure")
            return super().read(10)

    source = BrokenReader(_data(_record()))
    before = bookmarked.export_bookmarks_json()
    with pytest.raises(OSError, match="injected"):
        bookmarked.import_bookmarks_json(source, replace=True)
    assert not source.closed and bookmarked.export_bookmarks_json() == before


def test_export_name_lookup_is_fresh_and_does_not_overwrite_engine_cache(document):
    document.destinations["chapter"] = 1
    assert document.destinations["chapter"] == FitDestination(1)
    cached = document._engine_pdf._named_destination_cache
    catalog = document._engine_pdf._resolve(
        document._engine_pdf._cos_doc.trailer.mapping[PdfName("Root")]
    )
    tree = document._engine_pdf._resolve(catalog.mapping[PdfName("Names")])
    tree = document._engine_pdf._resolve(tree.mapping[PdfName("Dests")])
    tree.mapping[PdfName("Names")].items[1] = PdfArray(
        [document._engine_pdf._make_page_ref(2), PdfName("Fit")]
    )
    document.outlines.add(OutlineItem("chapter", destination="chapter"))
    assert _payload(document)["bookmarks"][0]["target"]["page_index"] == 2
    assert document._engine_pdf._named_destination_cache is cached


def test_literal_leading_slash_in_pdf_name_is_not_silently_removed(document):
    item = document.outlines.add(OutlineItem("name", page_index=None))
    item._loaded_target = ("Dest", PdfName("//literal"))
    with pytest.raises(PdfValidationException, match="representation"):
        document.export_bookmarks_json(resolve_named_destinations=False)


def test_missing_record_fields_and_deep_child_errors_reject_entire_tree(bookmarked):
    before = bookmarked.export_bookmarks_json()
    invalid = _record()
    del invalid["title"]
    with pytest.raises(PdfValidationException):
        bookmarked.import_bookmarks_json(
            _data(_record(children=[_record(children=[invalid])])), replace=True
        )
    assert bookmarked.export_bookmarks_json() == before


def test_projected_pdf_object_limit_rejects_import_before_mutation():
    with Document(limits=PdfLoadLimits(max_objects=8)) as document:
        document.pages.add()
        objects = set(document._engine_pdf._cos_doc.objects)
        with pytest.raises(PdfResourceLimitException, match="max_objects"):
            document.import_bookmarks_json(
                _data(*[_record(target=_page(0)) for _ in range(8)])
            )
        assert document._outlines is None
        assert set(document._engine_pdf._cos_doc.objects) == objects


def test_replacing_tree_reuses_existing_pdf_slots_at_object_limit(document):
    for index in range(4):
        document.outlines.add(OutlineItem(str(index), 0))
    raw = _saved(document)
    with Document(raw) as source:
        cos = source._engine_pdf._cos_doc
        maximum = max(max(cos.objects), max(cos.generations))
    with Document(raw, limits=PdfLoadLimits(max_objects=maximum + 1)) as target:
        assert (
            target.import_bookmarks_json(_data(_record(target=_page(0))), replace=True)
            == 1
        )
        with Document(_saved(target)) as reopened:
            assert len(reopened.outlines) == 1 and reopened.outlines[0].page_index == 0


@pytest.mark.parametrize("object_kind", ["Page", "Pages"])
def test_malformed_existing_outline_pointer_cannot_overwrite_page_objects(
    document, object_kind
):
    engine = document._engine_pdf
    catalog = engine._resolve(engine._cos_doc.trailer.mapping[PdfName("Root")])
    reference = (
        engine._make_page_ref(0)
        if object_kind == "Page"
        else catalog.mapping[PdfName("Pages")]
    )
    catalog.mapping[PdfName("Outlines")] = reference
    original = engine._resolve(reference)
    with pytest.raises(PdfValidationException, match="outline slots"):
        document.import_bookmarks_json(_data(_record()))
    assert document._outlines is None and engine._resolve(reference) is original
