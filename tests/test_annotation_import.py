"""JSON review import, PDF type recovery and atomic validation regressions."""

from __future__ import annotations

import io
import json

import pytest

from aspose_pdf import Document, PdfLoadLimits, PdfResourceLimitException
from aspose_pdf.annotations import Name
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfIndirectReference,
    PdfName,
    PdfNull,
    PdfNumber,
)
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfSecurityException,
    PdfValidationException,
)


def _record(**changes):
    return {
        "id": "source-note",
        "page_index": 0,
        "annotation_index": 12,
        "subtype": "Text",
        "rect": [10, 20, 30, 40],
        "contents": "Привет 🌍\nReview",
        "author": "Alice Иванова",
        "properties": {"Name": "Comment", "C": [1, 0, 0], "CA": 0.5},
        "links": {"in_reply_to": None, "popup": None, "parent": None},
        **changes,
    }


def _data(*records):
    return json.dumps(
        {"schema_version": 1, "annotations": list(records)}, ensure_ascii=False
    ).encode()


def _saved(doc):
    output = io.BytesIO()
    doc.save(output)
    return output.getvalue()


@pytest.fixture
def document():
    with Document() as doc:
        doc.pages.add().annotations.add_text((1, 2, 3, 4), "existing")
        doc.pages.add()
        yield doc


@pytest.mark.parametrize(
    "source_type", ["bytes", "bytearray", "path", "str", "stream", "short"]
)
def test_input_forms_append_preserve_existing_and_roundtrip(
    document, tmp_path, source_type
):
    data = _data(_record())
    path = tmp_path / "comments.json"
    path.write_bytes(data)

    class ShortReader(io.BytesIO):
        def read(self, size=-1):
            return super().read(min(size, 7) if size >= 0 else size)

    source = {
        "bytes": data,
        "bytearray": bytearray(data),
        "path": path,
        "str": str(path),
        "stream": io.BytesIO(b"skip" + data),
        "short": ShortReader(data),
    }[source_type]
    if source_type == "stream":
        source.seek(4)
    assert document.import_annotations_json(source) == 1
    if hasattr(source, "read"):
        assert not source.closed and source.read() == b""
    assert document.import_annotations_json(data) == 1
    target = tmp_path / "imported.pdf"
    document.save(target)
    for source in (_saved(document), target):
        with Document(source) as reopened:
            annotations = reopened.pages[0].annotations
            assert [a.contents for a in annotations] == [
                "existing",
                _record()["contents"],
                _record()["contents"],
            ]
            note = annotations[1]
            assert note.title == "Alice Иванова"
            assert note.rect == (10, 20, 30, 40)
            assert note.color == (1, 0, 0) and note.opacity == 0.5
            assert isinstance(note.get_property("Name"), Name)
            assert len(reopened.pages[1].annotations) == 0


def test_export_import_preserves_review_properties_names_and_geometry(document):
    document.pages[1].rotation = 90
    document.pages[1].crop_box = (5, 5, 300, 400)
    with Document() as source:
        annotations = source.pages.add().annotations
        annotations.add_line(
            (10, 20), (100, 80), line_endings=["OpenArrow", "None"], border_width=2
        )
        annotations.add_ink([[(10, 10), (20, 30)], [(40, 50), (60, 70)]])
        annotations.add_polygon([(10, 10), (20, 10), (15, 20)])
        annotations.add_free_text(
            (10, 50, 80, 100),
            "Plain",
            properties={"RC": "<body><b>Rich</b></body>", "Q": 2},
        )
        annotations.add_highlight((10, 200, 100, 220), "Highlighted")
        annotations.add_text(
            (30, 30, 50, 50),
            "All properties",
            properties={
                "BE": {"S": Name("C"), "I": 1.5},
                "Border": [0, 0, 2, [3, 1]],
                "BS": {"Type": Name("Border"), "S": Name("D"), "W": 2, "D": [3, 1]},
                "CL": [1, 2, 3, 4, 5, 6],
                "CO": [3, 4],
                "CP": Name("Top"),
                "Cap": True,
                "CreationDate": "D:20260102030405Z",
                "DA": "/Helv 12 Tf 0 g",
                "DS": "font:12pt Helvetica",
                "F": 6,
                "IC": [0.1],
                "IT": Name("LineArrow"),
                "LL": 3,
                "LLE": 4,
                "LLO": -1,
                "Lang": "en-US",
                "M": "D:20261020000000Z",
                "Measure": {
                    "Type": Name("Measure"),
                    "Subtype": Name("RL"),
                    "R": "1 in = 1 in",
                    "X": [
                        {
                            "Type": Name("NumberFormat"),
                            "U": "in",
                            "C": 1,
                            "F": Name("D"),
                            "D": 100,
                            "FD": True,
                            "RT": ".",
                            "RD": ",",
                            "PS": " ",
                            "SS": " ",
                            "O": Name("S"),
                        }
                    ],
                    "CYX": 1,
                    "O": [0, 0],
                },
                "NM": "non-unique",
                "Name": Name("Comment"),
                "Open": True,
                "OverlayText": "overlay",
                "RD": [1, 2, 3, 4],
                "RT": Name("Reply"),
                "Repeat": False,
                "State": "Accepted",
                "StateModel": "Review",
                "Subj": "Check",
                "Sy": Name("P"),
            },
        )
        expected = annotations.to_list()
        assert document.pages[1].annotations.import_json(
            annotations.export_json()
        ) == len(expected)
    with Document(_saved(document)) as reopened:
        actual = reopened.pages[1].annotations.to_list()
        for wanted, got in zip(expected, actual, strict=True):
            for key in ("subtype", "rect", "contents", "author", "properties"):
                assert got[key] == wanted[key]
        line = reopened.pages[1].annotations[0]
        assert all(isinstance(v, Name) for v in line.get_property("LE"))
        props = reopened.pages[1].annotations[-1].properties
        assert isinstance(props["BS"]["S"], Name)
        assert isinstance(props["Measure"]["X"][0]["F"], Name)
        assert not isinstance(props["State"], Name)


def test_links_use_new_ids_and_complete_popup_pairs_even_in_reverse_order(document):
    note = _record(id="note")
    popup = _record(
        id="popup",
        subtype="Popup",
        links={"in_reply_to": None, "popup": None, "parent": "note"},
    )
    reply = _record(
        id="reply",
        links={"in_reply_to": "note", "popup": None, "parent": None},
        properties={"RT": "Reply"},
    )
    nested = _record(
        id="nested", links={"in_reply_to": "reply", "popup": None, "parent": None}
    )
    assert document.import_annotations_json(_data(popup, nested, reply, note)) == 4
    with Document(_saved(document)) as reopened:
        existing, popup, nested, reply, note = reopened.pages[0].annotations.to_list()
        assert note["links"]["popup"] == popup["id"]
        assert popup["links"]["parent"] == note["id"]
        assert reply["links"]["in_reply_to"] == note["id"]
        assert nested["links"]["in_reply_to"] == reply["id"]
        assert existing["links"] == _record()["links"]


def test_external_links_are_omitted_and_never_resolve_against_destination(document):
    existing = document.pages[0].annotations.to_list()[0]["id"]
    assert (
        document.import_annotations_json(
            _data(
                _record(
                    links={"in_reply_to": existing, "popup": "missing", "parent": None}
                )
            )
        )
        == 1
    )
    assert document.pages[0].annotations.to_list()[-1]["links"] == _record()["links"]


def test_page_mapping_merges_reorders_and_links_across_pages(document):
    records = [
        _record(id="one", page_index=8),
        _record(
            id="two",
            page_index=9,
            links={"in_reply_to": "one", "popup": None, "parent": None},
        ),
    ]
    assert document.import_annotations_json(_data(*records), page_map={8: 1, 9: 0}) == 2
    one = document.pages[1].annotations.to_list()[0]
    two = document.pages[0].annotations.to_list()[-1]
    assert two["links"]["in_reply_to"] == one["id"]
    assert document.import_annotations_json(_data(*records), page_map={8: 1, 9: 1}) == 2
    assert len(document.pages[1].annotations) == 3


def test_page_import_retargets_nonzero_source_index_and_rejects_multiple_pages(
    document,
):
    assert document.pages[1].annotations.import_json(_data(_record(page_index=19))) == 1
    before = document.export_annotations_json()
    with pytest.raises(PdfValidationException, match="single source page"):
        document.pages[1].annotations.import_json(
            _data(_record(id="a"), _record(id="b", page_index=1))
        )
    assert document.export_annotations_json() == before


@pytest.mark.parametrize(
    "subtype", "Text FreeText Square Circle Stamp Caret Popup Redact".split()
)
def test_rect_only_review_subtypes(document, subtype):
    assert document.import_annotations_json(_data(_record(subtype=subtype))) == 1
    with Document(_saved(document)) as reopened:
        assert reopened.pages[0].annotations[-1].subtype == subtype


@pytest.mark.parametrize(
    "subtype",
    "Widget Link FileAttachment Sound Movie Screen PrinterMark TrapNet Watermark 3D RichMedia Vendor".split(),
)
def test_unsupported_subtypes_reject_entire_batch(document, subtype):
    before = _saved(document)
    with pytest.raises(PdfValidationException, match="unsupported annotation subtype"):
        document.import_annotations_json(
            _data(_record(id="ok"), _record(subtype=subtype))
        )
    assert _saved(document) == before


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"null",
        b"[]",
        b"{}",
        b"\xff",
        b'{"schema_version":1,"annotations":[],}',
        b'{"schema_version":1,"schema_version":1,"annotations":[]}',
        b'{"schema_version":true,"annotations":[]}',
        b'{"schema_version":2,"annotations":[]}',
        b'{"schema_version":1,"annotations":{}}',
        b'{"schema_version":1,"annotations":[],"extra":0}',
        b'{"schema_version":1,"annotations":[NaN]}',
        b'{"schema_version":1,"annotations":[1e999]}',
        b'{"schema_version":1,"annotations":[{"x":1,"x":2}]}',
    ],
)
def test_malformed_json_is_atomic(document, data):
    before = _saved(document)
    with pytest.raises(PdfValidationException):
        document.import_annotations_json(data)
    assert _saved(document) == before


@pytest.mark.parametrize(
    "changes",
    [
        {"id": ""},
        {"id": 1},
        {"page_index": True},
        {"page_index": -1},
        {"page_index": 2},
        {"annotation_index": -1},
        {"annotation_index": False},
        {"contents": None},
        {"author": 4},
        {"contents": "\ud800"},
        {"rect": [1, 2, 3]},
        {"rect": [False, 2, 3, 4]},
        {"rect": [1, 2, 3, "4"]},
        {"rect": [1, 2, 3, 10**400]},
        {"properties": []},
        {"links": {}},
        {"links": {"in_reply_to": 0, "popup": None, "parent": None}},
        {"links": {"in_reply_to": None, "popup": None, "parent": "missing"}},
        {"extra": 1},
    ],
)
def test_invalid_records_reject_before_changes(document, changes):
    before = document.export_annotations_json()
    data = json.dumps(
        {"schema_version": 1, "annotations": [_record(id="ok"), _record(**changes)]}
    ).encode()
    with pytest.raises(PdfValidationException):
        document.import_annotations_json(data)
    assert document.export_annotations_json() == before


@pytest.mark.parametrize(
    "properties",
    [
        {"AP": {}},
        {"A": {"S": "JavaScript", "JS": "alert(1)"}},
        {"Type": "Page"},
        {"Name": 3},
        {"Name": ""},
        {"Name": "/Comment"},
        {"NM": False},
        {"Open": 1},
        {"CA": 1.1},
        {"CA": -1},
        {"F": True},
        {"F": 2**31},
        {"Q": 3},
        {"IC": [0, 1]},
        {"C": [2]},
        {"LE": ["None"]},
        {"QuadPoints": [1, 2, 3, 4]},
        {"Vertices": [1, 2, 3]},
        {"InkList": [[]]},
        {"L": [1, 2]},
        {"CL": [1, 2]},
        {"Border": [1, 2]},
        {"BS": {"A": "injected"}},
        {"BS": {"D": "dashed"}},
        {"Measure": {"X": ["bad"]}},
        {"Measure": {"X": [{"Custom": "bad"}]}},
    ],
)
def test_invalid_properties_reject_atomically(document, properties):
    before = _saved(document)
    with pytest.raises(PdfValidationException):
        document.import_annotations_json(
            _data(_record(id="ok"), _record(properties=properties))
        )
    assert _saved(document) == before


def test_explicit_nulls_survive_export_and_import(document):
    assert (
        document.import_annotations_json(
            _data(_record(properties={"NM": None, "BS": None, "CA": None}))
        )
        == 1
    )
    with Document(_saved(document)) as reopened:
        assert reopened.pages[0].annotations.to_list()[-1]["properties"] == {
            "NM": None,
            "BS": None,
            "CA": None,
        }


@pytest.mark.parametrize(
    "mapping",
    [[], {True: 0}, {-1: 0}, {0: False}, {0: 0.0}, {0: -1}, {0: 2}, {}, {1: 0}],
)
def test_page_map_is_validated_before_mutation(document, mapping):
    before = document.export_annotations_json()
    with pytest.raises(PdfValidationException):
        document.import_annotations_json(_data(_record()), page_map=mapping)
    assert document.export_annotations_json() == before


@pytest.mark.parametrize(
    "kind",
    [
        "duplicate",
        "self",
        "reply-cycle",
        "popup-type",
        "parent-type",
        "popup-page",
        "conflict",
        "popup-reply",
    ],
)
def test_invalid_relationships_are_atomic(document, kind):
    a, b, c = _record(id="a"), _record(id="b"), _record(id="c", subtype="Popup")
    if kind == "duplicate":
        b["id"] = "a"
    elif kind == "self":
        a["links"]["in_reply_to"] = "a"
    elif kind == "reply-cycle":
        a["links"]["in_reply_to"], b["links"]["in_reply_to"] = "b", "a"
    elif kind == "popup-type":
        a["links"]["popup"] = "b"
    elif kind == "parent-type":
        c["links"]["parent"] = "c"
    elif kind == "popup-page":
        a["links"]["popup"], c["page_index"] = "c", 1
    elif kind == "conflict":
        a["links"]["popup"], b["links"]["popup"] = "c", "c"
    else:
        a["links"]["in_reply_to"] = "c"
    before = _saved(document)
    with pytest.raises(PdfValidationException):
        document.import_annotations_json(_data(a, b, c))
    assert _saved(document) == before


def test_empty_json_bom_missing_fields_and_disposed_apis(document):
    assert document.import_annotations_json(b"\xef\xbb\xbf" + _data()) == 0
    assert document.pages[0].annotations.import_json(_data()) == 0
    record = _record()
    del record["author"]
    with pytest.raises(PdfValidationException):
        document.import_annotations_json(_data(record))
    collection = document.pages[0].annotations
    document.dispose()
    for call in (document.import_annotations_json, collection.import_json):
        with pytest.raises(AsposePdfException, match="disposed"):
            call(_data())


@pytest.mark.parametrize("source", [None, 12, {}, io.StringIO("{}")])
def test_invalid_sources(document, source):
    with pytest.raises(TypeError):
        document.import_annotations_json(source)


@pytest.mark.parametrize("kind", ["bytes", "path", "stream"])
def test_input_size_limit(kind, tmp_path):
    data = _data(_record())
    path = tmp_path / "large.json"
    path.write_bytes(data)
    source = {"bytes": data, "path": path, "stream": io.BytesIO(data)}[kind]
    with Document(limits=PdfLoadLimits(max_input_bytes=32)) as document:
        document.pages.add()
        with pytest.raises(PdfResourceLimitException, match="max_input_bytes"):
            document.import_annotations_json(source)
        assert len(document.pages[0].annotations) == 0


@pytest.mark.parametrize(
    "limits,data,match",
    [
        (PdfLoadLimits(max_nesting_depth=2), _data(_record()), "max_nesting_depth"),
        (PdfLoadLimits(max_container_items=4), _data(_record()), "max_container_items"),
        (
            PdfLoadLimits(max_container_items=30),
            _data(_record(), _record(id="b")),
            "max_container_items",
        ),
        (
            PdfLoadLimits(max_object_bytes=24),
            _data(_record(contents="x" * 25)),
            "max_object_bytes",
        ),
        (
            PdfLoadLimits(max_objects=8),
            _data(*[_record(id=str(i)) for i in range(8)]),
            "max_objects",
        ),
    ],
)
def test_limits_fail_before_import(limits, data, match):
    with Document(limits=limits) as document:
        document.pages.add()
        before = document.export_annotations_json()
        with pytest.raises(PdfResourceLimitException, match=match):
            document.import_annotations_json(data)
        assert document.export_annotations_json() == before


def test_malformed_destination_array_and_late_page_failure_are_atomic(document):
    page = document._engine_pdf._get_page_dict(1)
    page.mapping[PdfName("Annots")] = PdfNumber(42)
    engine = document._engine_pdf
    count = len(engine._cos_doc.objects)
    before = document.pages[0].annotations.to_list()
    with pytest.raises(PdfValidationException, match="Annots"):
        document.import_annotations_json(
            _data(_record(id="a"), _record(id="b", page_index=1))
        )
    assert document.pages[0].annotations.to_list() == before
    assert len(engine._cos_doc.objects) == count


def test_null_or_indirect_annots_arrays_are_supported_without_mutating_shared_array(
    document,
):
    engine = document._engine_pdf
    first, second = engine._get_page_dict(0), engine._get_page_dict(1)
    old = PdfArray([])
    first.mapping[PdfName("Annots")] = engine._cos_doc.register_object(old)
    second.mapping[PdfName("Annots")] = PdfNull()
    assert (
        document.import_annotations_json(
            _data(_record(id="a"), _record(id="b", page_index=1))
        )
        == 2
    )
    assert old.items == []
    with Document(_saved(document)) as reopened:
        assert [len(p.annotations) for p in reopened.pages] == [1, 1]


def test_import_after_page_insert_uses_actual_page_tree(document):
    document.pages.insert(0)
    assert document.import_annotations_json(_data(_record(page_index=1))) == 1
    with Document(_saved(document)) as reopened:
        assert [len(p.annotations) for p in reopened.pages] == [0, 2, 0]


@pytest.mark.parametrize("encrypted", [False, True])
def test_streaming_import_is_lazy_and_survives_encrypted_save(
    document, tmp_path, monkeypatch, encrypted
):
    if encrypted:
        document.encrypt(user_password="secret", owner_password="owner")
    path = tmp_path / "input.pdf"
    document.save(path)
    password = "secret" if encrypted else None
    with Document.open_streaming(path, password=password) as lazy:
        engine = lazy._engine_pdf

        def no_decode(*args, **kwargs):
            raise AssertionError("Annotation import must not decode page content")

        with monkeypatch.context() as patch:
            patch.setattr(engine, "get_page_content", no_decode)
            patch.setattr(engine, "_decode_cos_stream", no_decode)
            assert lazy.import_annotations_json(_data(_record(page_index=1))) == 1
        output = tmp_path / "output.pdf"
        lazy.save(output)
    with Document(output, password=password) as reopened:
        assert reopened.pages[1].annotations[0].contents == _record()["contents"]
        if encrypted:
            with pytest.raises(PdfSecurityException):
                Document(output)


def test_imported_markup_can_generate_visible_appearances(document):
    record = _record(
        subtype="Square",
        rect=[20, 20, 80, 80],
        properties={"C": [1, 0, 0], "IC": [1, 0, 0]},
    )
    document.import_annotations_json(_data(record))
    assert document.pages[0].annotations.generate_appearances() >= 1
    with Document(_saved(document)) as reopened:
        assert reopened.pages[0].annotations[-1].appearance_normal


def test_commit_failure_restores_objects_and_page_arrays(document):
    class FailingMapping(dict):
        def update(self, objects):
            first = next(iter(objects))
            self[first] = objects[first]
            raise OSError("injected commit failure")

    cos = document._engine_pdf._cos_doc
    cos.objects = FailingMapping(cos.objects)
    before = document.export_annotations_json()
    numbers = set(cos.objects)
    with pytest.raises(OSError, match="injected"):
        document.import_annotations_json(_data(_record()))
    assert set(cos.objects) == numbers
    assert document.export_annotations_json() == before


@pytest.mark.parametrize("reverse", [False, True])
def test_external_popup_link_is_cleared_before_rebuilding_internal_inverse(
    document, reverse
):
    note = _record(
        id="note", links={"in_reply_to": None, "popup": "external", "parent": None}
    )
    popup = _record(
        id="popup",
        subtype="Popup",
        links={"in_reply_to": None, "popup": None, "parent": "note"},
    )
    records = [note, popup]
    if reverse:
        records.reverse()
    assert document.import_annotations_json(_data(*records)) == 2
    imported = document.pages[0].annotations.to_list()[1:]
    note = next(record for record in imported if record["subtype"] == "Text")
    popup = next(record for record in imported if record["subtype"] == "Popup")
    assert note["links"]["popup"] == popup["id"]
    assert popup["links"]["parent"] == note["id"]


@pytest.mark.parametrize(
    "subtype,properties",
    [
        ("Line", {}),
        ("Polygon", {}),
        ("PolyLine", {}),
        ("Ink", {}),
        ("Highlight", {}),
        ("Underline", {}),
        ("Squiggly", {}),
        ("StrikeOut", {}),
        ("Line", {"L": None}),
        ("Polygon", {"Vertices": [0, 0, 10, 10]}),
    ],
)
def test_missing_required_geometry_is_refused(document, subtype, properties):
    before = document.export_annotations_json()
    with pytest.raises(PdfValidationException, match="requires"):
        document.import_annotations_json(
            _data(_record(subtype=subtype, properties=properties))
        )
    assert document.export_annotations_json() == before


@pytest.mark.parametrize(
    "properties",
    [
        {"BS": {"W": -1}},
        {"BS": {"D": [-1, 2]}},
        {"BS": {"D": [0, 0]}},
        {"BE": {"I": 3}},
        {"Border": [-1, 0, 1]},
        {"Border": [0, 0, 1, [0]]},
        {"Measure": {"O": [1, 2, 3]}},
        {"Measure": {"Subtype": "GEO"}},
    ],
)
def test_invalid_border_and_measurement_values_are_refused(document, properties):
    before = document.export_annotations_json()
    with pytest.raises(PdfValidationException):
        document.import_annotations_json(_data(_record(properties=properties)))
    assert document.export_annotations_json() == before


def test_dangling_destination_array_reference_is_refused(document):
    page = document._engine_pdf._get_page_dict(1)
    page.mapping[PdfName("Annots")] = PdfIndirectReference(99999)
    with pytest.raises(PdfValidationException, match="Annots"):
        document.import_annotations_json(_data(_record(page_index=1)))
    assert page.mapping[PdfName("Annots")].object_number == 99999


def test_page_assignment_failure_rolls_back_all_pages_and_objects(document):
    class FailOnce(dict):
        def __setitem__(self, key, value):
            if key == PdfName("Annots") and not hasattr(self, "failed"):
                self.failed = True
                raise OSError("injected page failure")
            super().__setitem__(key, value)

    engine = document._engine_pdf
    page = engine._get_page_dict(1)
    page.mapping = FailOnce(page.mapping)
    before = document.export_annotations_json()
    numbers = set(engine._cos_doc.objects)
    with pytest.raises(OSError, match="injected"):
        document.import_annotations_json(
            _data(_record(id="a"), _record(id="b", page_index=1))
        )
    assert document.export_annotations_json() == before
    assert set(engine._cos_doc.objects) == numbers


@pytest.mark.parametrize(
    "subtype,properties",
    [
        ("Line", {"L": [10, 20, 30, 40]}),
        ("Polygon", {"Vertices": [0, 0, 10, 0, 10, 10]}),
        ("PolyLine", {"Vertices": [10, 20, 30, 40]}),
        ("Ink", {"InkList": [[10, 20, 30, 40], [20, 30]]}),
        *[
            (name, {"QuadPoints": [0, 10, 10, 10, 0, 0, 10, 0]})
            for name in ("Highlight", "Underline", "Squiggly", "StrikeOut")
        ],
    ],
)
def test_all_geometry_subtypes_survive_round_trip(document, subtype, properties):
    assert (
        document.import_annotations_json(
            _data(_record(subtype=subtype, properties=properties))
        )
        == 1
    )
    with Document(_saved(document)) as reopened:
        record = reopened.pages[0].annotations.to_list()[-1]
        assert record["subtype"] == subtype
        assert record["properties"] == properties


def test_long_reply_chain_is_iterative_and_input_order_wins_over_annotation_indices(
    document,
):
    records = []
    for index in range(200):
        record = _record(
            id=str(index), annotation_index=200 - index, properties={"NM": "same-name"}
        )
        record["contents"] = str(index)
        if index:
            record["links"]["in_reply_to"] = str(index - 1)
        records.append(record)
    assert document.import_annotations_json(_data(*records)) == 200
    with Document(_saved(document)) as reopened:
        records = reopened.pages[0].annotations.to_list()[1:]
        assert [record["contents"] for record in records] == [
            str(index) for index in range(200)
        ]
        for index in range(1, 200):
            assert records[index]["links"]["in_reply_to"] == records[index - 1]["id"]


def test_stream_read_failure_does_not_mutate_pdf_or_close_stream(document):
    class BrokenStream(io.BytesIO):
        def read(self, size=-1):
            if self.tell():
                raise OSError("injected read failure")
            return super().read(10)

    source = BrokenStream(_data(_record()))
    before = document.export_annotations_json()
    with pytest.raises(OSError, match="injected"):
        document.import_annotations_json(source)
    assert not source.closed
    assert document.export_annotations_json() == before


def test_empty_document_does_not_create_pages_to_satisfy_import():
    with Document() as document:
        assert document.import_annotations_json(_data()) == 0
        with pytest.raises(PdfValidationException, match="out of range"):
            document.import_annotations_json(_data(_record()))
        assert len(document.pages) == 0


def test_annotation_flags_keep_integer_type_at_pdf_integer_boundary(document):
    assert (
        document.import_annotations_json(_data(_record(properties={"F": 2**31 - 1})))
        == 1
    )
    with Document(_saved(document)) as reopened:
        flags = reopened.pages[0].annotations.to_list()[-1]["properties"]["F"]
        assert type(flags) is int and flags == 2**31 - 1


def test_incremental_save_preserves_original_bytes_content_and_imported_links(document):
    document.pages[0].add_text("Original page content", x=20, y=100)
    original = _saved(document)
    with Document() as source:
        note = source.pages.add().annotations.add_text((10, 20, 30, 40), "Review")
        note.reply("Reply")
        note.add_popup((40, 20, 100, 100))
        payload = source.export_annotations_json()
    with Document(original) as target:
        text = target.extract_text()
        assert target.import_annotations_json(payload) == 3
        output = io.BytesIO()
        target.save(output, incremental=True)
        assert output.getvalue().startswith(original)
    with Document(output.getvalue()) as reopened:
        assert reopened.extract_text() == text
        records = reopened.pages[0].annotations.to_list()[1:]
        assert records[1]["links"]["in_reply_to"] == records[0]["id"]
        assert records[0]["links"]["popup"] == records[2]["id"]
        assert records[2]["links"]["parent"] == records[0]["id"]
