"""Read-only annotation review snapshots and versioned JSON export."""

from __future__ import annotations

import io
import json
import zlib

import pytest

from aspose_pdf import (
    AnnotationFlags,
    AnnotationType,
    Document,
    PdfLoadLimits,
    PdfResourceLimitException,
)
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName, PdfNumber, PdfString
from aspose_pdf.exceptions import AsposePdfException, PdfValidationException


def _saved(document):
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _stream(data, entries=b""):
    return (
        b"<< "
        + entries
        + b" /Length %d >>\nstream\n" % len(data)
        + data
        + b"\nendstream"
    )


def _pdf(objects, generations=None):
    """Build small PDFs for lazy-stream and malformed-metadata regressions."""
    data = bytearray(b"%PDF-1.7\n")
    offsets = {}
    generations = generations or {}
    for number, value in sorted(objects.items()):
        offsets[number] = len(data)
        data += (
            b"%d %d obj\n" % (number, generations.get(number, 0))
            + value
            + b"\nendobj\n"
        )
    start = len(data)
    size = max(objects) + 1
    data += b"xref\n0 %d\n" % size
    for number in range(size):
        data += (
            b"%010d %05d n \n" % (offsets[number], generations.get(number, 0))
            if number in offsets
            else b"0000000000 65535 f \n"
        )
    data += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        size,
        start,
    )
    return bytes(data)


def _objects():
    compressed = zlib.compress(b"x" * 20_000)
    return {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Count 2 /Kids [3 0 R 5 0 R] /MediaBox [0 0 400 400] >>",
        3: b"<< /Type /Page /Parent 2 0 R /Contents 4 0 R /Annots [8 0 R] >>",
        4: _stream(compressed, b"/Filter /FlateDecode"),
        5: b"<< /Type /Page /Parent 2 0 R /Contents 6 0 R /Annots [9 0 R] >>",
        6: _stream(compressed, b"/Filter /FlateDecode"),
        8: b"<< /Type /Annot /Subtype /Text /Rect [10 20 30 40] /Contents (note) "
        b"/AP << /N 12 0 R >> /RC 13 0 R /FS 14 0 R /A 16 0 R /Custom 8 0 R >>",
        9: b"<< /Type /Annot /Subtype /Text /Rect [50 60 70 80] /Contents (reply) /IRT 8 0 R >>",
        12: _stream(
            compressed, b"/Subtype /Form /BBox [0 0 20 20] /Filter /FlateDecode"
        ),
        13: _stream(compressed, b"/Filter /FlateDecode"),
        14: b"<< /Type /Filespec /F (secret.bin) /EF << /F 15 0 R >> >>",
        15: _stream(compressed, b"/Type /EmbeddedFile /Filter /FlateDecode"),
        16: b"<< /S /JavaScript /JS (private-action-text) >>",
    }


@pytest.fixture
def document():
    with Document() as doc:
        page = doc.pages.add()
        note = page.annotations.add_text(
            (10, 20, 30, 40), 'Привет "reader"\nnext line', title="Alice Иванова"
        )
        note.color = (0.1, 0.2, 0.3, 0.4)
        note.opacity = 0.5
        note.subject = "Review"
        note.creation_date = "D:20260102030405+02'00'"
        note.modified = "D:20260203040506Z"
        note.update_properties(
            NM="comment-1", F=int(AnnotationFlags.PRINT | AnnotationFlags.HIDDEN)
        )
        reply = note.reply("Reply", title="Bob")
        reply.reply("Nested reply")
        note.add_popup((40, 20, 200, 100), is_open=True)
        doc.pages.add().annotations.add_highlight(
            (50, 200, 200, 220), "Highlight", title="Carol"
        )
        yield doc


@pytest.mark.parametrize("source", ["live", "bytes", "file", "streaming"])
def test_versioned_unicode_json_and_page_collection_match_after_pdf_round_trips(
    document, source, tmp_path
):
    raw = _saved(document)
    if source == "live":
        reopened = document
    elif source == "bytes":
        reopened = Document(raw)
    else:
        path = tmp_path / "input.pdf"
        path.write_bytes(raw)
        reopened = (Document.open_streaming if source == "streaming" else Document)(
            path
        )
    try:
        data = reopened.export_annotations_json()
        payload = json.loads(data)
        assert isinstance(data, bytes) and data.endswith(b"\n")
        assert not data.startswith(b"\xef\xbb\xbf")
        assert "Иванова".encode() in data
        assert payload["schema_version"] == 1
        assert list(payload) == ["schema_version", "annotations"]
        assert [a["page_index"] for a in payload["annotations"]] == [0, 0, 0, 0, 1]
        note = payload["annotations"][0]
        assert note["annotation_index"] == 0 and note["subtype"] == "Text"
        assert note["rect"] == [10, 20, 30, 40]
        assert note["contents"] == 'Привет "reader"\nnext line'
        assert note["author"] == "Alice Иванова"
        assert note["properties"] == {
            "C": [0.1, 0.2, 0.3, 0.4],
            "CA": 0.5,
            "CreationDate": "D:20260102030405+02'00'",
            "F": 6,
            "M": "D:20260203040506Z",
            "NM": "comment-1",
            "Subj": "Review",
        }
        page_records = reopened.pages[0].annotations.to_list()
        assert page_records == payload["annotations"][:4]
        assert (
            json.loads(reopened.pages[0].annotations.export_json())["annotations"]
            == page_records
        )
        assert reopened.export_annotations_json() == data
    finally:
        if reopened is not document:
            reopened.dispose()


def test_reply_and_popup_links_are_ids_without_recursive_objects(document):
    note, reply, nested, popup = document.pages[0].annotations.to_list()
    assert reply["links"]["in_reply_to"] == note["id"]
    assert nested["links"]["in_reply_to"] == reply["id"]
    assert note["links"]["popup"] == popup["id"]
    assert popup["links"]["parent"] == note["id"]
    assert reply["properties"]["RT"] == "Reply"
    assert popup["properties"]["Open"] is True
    assert len({a["id"] for a in document.pages[0].annotations.to_list()}) == 4
    filtered = json.loads(document.export_annotations_json(subtypes="Popup"))[
        "annotations"
    ]
    assert filtered == [popup]
    assert popup["links"]["parent"] not in {a["id"] for a in filtered}


@pytest.mark.parametrize(
    "subtypes",
    [
        "Highlight",
        "/Highlight",
        AnnotationType.HIGHLIGHT,
        [AnnotationType.HIGHLIGHT],
        iter(["Highlight"]),
    ],
)
def test_subtype_strings_enums_and_generators_select_original_indices(
    document, subtypes
):
    records = json.loads(document.export_annotations_json(subtypes=subtypes))[
        "annotations"
    ]
    assert len(records) == 1
    assert records[0]["subtype"] == "Highlight" and records[0]["page_index"] == 1
    popup = document.pages[0].annotations.to_list(subtypes="Popup")
    assert len(popup) == 1 and popup[0]["annotation_index"] == 3


def test_page_selection_order_and_duplicates_empty_and_unknown_filters(document):
    data = document.export_annotations_json(pages=(i for i in [1, 0, 1]))
    assert [a["page_index"] for a in json.loads(data)["annotations"]] == [1, 0, 0, 0, 0]
    for options in ({"pages": []}, {"subtypes": []}, {"subtypes": "VendorType"}):
        assert (
            json.loads(document.export_annotations_json(**options))["annotations"] == []
        )
    assert document.pages[0].annotations.to_list(subtypes=[]) == []


@pytest.mark.parametrize(
    "pages", [0, "0", b"", bytearray(), [True], [0.0], ["0"], [None], [-1], [2]]
)
def test_invalid_page_selections_leave_existing_output_intact(
    document, pages, tmp_path
):
    path = tmp_path / "annotations.json"
    path.write_bytes(b"original")
    with pytest.raises(PdfValidationException):
        document.export_annotations_json(path, pages=pages)
    assert path.read_bytes() == b"original"


@pytest.mark.parametrize(
    "subtypes", [True, 2, b"", b"Text", bytearray(), [None], [2], "", "/", ["Text", ""]]
)
def test_invalid_subtype_filters_are_rejected(document, subtypes):
    with pytest.raises(PdfValidationException):
        document.export_annotations_json(subtypes=subtypes)


def test_selection_is_validated_before_reading_any_annotations(document, monkeypatch):
    def no_metadata(*args):
        raise AssertionError(
            "Invalid or empty selection must not read annotation metadata"
        )

    monkeypatch.setattr(document._engine_pdf, "_get_page_dict", no_metadata)
    with pytest.raises(PdfValidationException):
        document.export_annotations_json(pages=[0, 100])
    with pytest.raises(PdfValidationException):
        document.export_annotations_json(subtypes=["Text", None])
    assert json.loads(document.export_annotations_json(pages=[]))["annotations"] == []
    assert (
        json.loads(document.export_annotations_json(subtypes=[]))["annotations"] == []
    )


def test_empty_document_and_page_export_a_valid_empty_schema():
    with Document() as document:
        assert json.loads(document.export_annotations_json()) == {
            "schema_version": 1,
            "annotations": [],
        }
        page = document.pages.add()
        assert page.annotations.to_list() == []
        assert page.annotations.export_json() == document.export_annotations_json()


def test_snapshots_are_deeply_detached_fresh_and_survive_disposal(document):
    page = document.pages[1]
    snapshot = page.annotations.to_list()
    snapshot[0]["rect"][0] = 999
    snapshot[0]["properties"]["QuadPoints"][0] = 999
    snapshot[0]["links"]["popup"] = "changed"
    current = page.annotations.to_list()
    assert current[0]["rect"][0] != 999
    assert current[0]["properties"]["QuadPoints"][0] != 999
    page.annotations[0].contents = "Updated"
    assert page.annotations.to_list()[0]["contents"] == "Updated"
    assert current[0]["contents"] == "Highlight"
    collection = page.annotations
    document.dispose()
    assert current[0]["contents"] == "Highlight"
    with pytest.raises(AsposePdfException, match="disposed"):
        collection.to_list()
    with pytest.raises(AsposePdfException, match="disposed"):
        collection.export_json()
    with pytest.raises(AsposePdfException, match="disposed"):
        document.export_annotations_json()


def test_export_does_not_change_pdf_bytes_or_generate_appearances(document):
    before = _saved(document)
    assert not document.pages[0].annotations[0].has_appearance
    document.export_annotations_json()
    document.pages[0].annotations.to_list()
    assert _saved(document) == before
    assert not document.pages[0].annotations[0].has_appearance


@pytest.mark.parametrize(
    "subtype", [t.value for t in AnnotationType] + ["VendorCustom"]
)
def test_every_subtype_including_widgets_and_vendor_annotations_exports_common_metadata(
    subtype,
):
    with Document() as document:
        page = document.pages.add()
        page.annotations.add(
            subtype,
            (1, 2, 3, 4),
            "Contents",
            title="Author",
            properties={"NM": "name", "Custom": "omit"},
        )
        (record,) = page.annotations.to_list()
        assert record["subtype"] == subtype
        assert record["contents"] == "Contents" and record["author"] == "Author"
        assert record["properties"] == {"NM": "name"}


def test_geometry_names_border_states_and_rich_text_export_as_json_values():
    with Document() as document:
        annotations = document.pages.add().annotations
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
        line, ink, polygon, free_text = annotations.to_list()
        assert line["properties"]["L"] == [10, 20, 100, 80]
        assert line["properties"]["LE"] == ["OpenArrow", "None"]
        assert line["properties"]["BS"] == {"S": "S", "W": 2}
        assert ink["properties"]["InkList"] == [[10, 10, 20, 30], [40, 50, 60, 70]]
        assert polygon["properties"]["Vertices"] == [10, 10, 20, 10, 15, 20]
        assert free_text["properties"]["Q"] == 2
        assert free_text["properties"]["RC"] == "<body><b>Rich</b></body>"
        note = annotations.add_text((0, 0, 20, 20), icon="Comment", is_open=True)
        note.state, note.state_model = "Accepted", "Review"
        assert annotations.to_list()[-1]["properties"] == {
            "Name": "Comment",
            "Open": True,
            "State": "Accepted",
            "StateModel": "Review",
        }


def test_coordinates_stay_in_pdf_space_for_rotated_cropped_pages():
    with Document() as document:
        page = document.pages.add()
        page.annotations.add_text((-10, 20, 30, 40), "hidden", properties={"F": 2})
        page.rotation = 90
        page.crop_box = (100, 100, 300, 300)
        assert page.annotations.to_list()[0]["rect"] == [-10, 20, 30, 40]
        assert page.annotations.to_list()[0]["properties"]["F"] == 2


def test_streaming_export_does_not_decode_content_appearances_rich_text_or_attachments(
    tmp_path, monkeypatch
):
    path = tmp_path / "lazy.pdf"
    path.write_bytes(_pdf(_objects()))
    with Document.open_streaming(
        path, limits=PdfLoadLimits(max_decoded_stream_bytes=1000)
    ) as document:

        def no_decoding(*args, **kwargs):
            raise AssertionError("Metadata export must not decode any stream")

        monkeypatch.setattr(document._engine_pdf, "_decode_cos_stream", no_decoding)
        monkeypatch.setattr(document._engine_pdf, "get_page_content", no_decoding)
        record = document.pages[0].annotations.to_list()[0]
        assert record["contents"] == "note" and record["properties"] == {}
        data = document.export_annotations_json(pages=[0])
        assert b"private-action-text" not in data and b"secret.bin" not in data
        assert b"Custom" not in data and b"RC" not in data and b"AP" not in data
        assert json.loads(data)["annotations"] == [record]


def test_cross_page_reply_links_remain_identifiable_with_parent_outside_selection(
    tmp_path,
):
    path = tmp_path / "cross-page.pdf"
    path.write_bytes(_pdf(_objects()))
    with Document.open_streaming(path) as document:
        parent, reply = json.loads(document.export_annotations_json())["annotations"]
        assert reply["links"]["in_reply_to"] == parent["id"]
        assert json.loads(document.export_annotations_json(pages=[1]))[
            "annotations"
        ] == [reply]


def test_reference_ids_keep_nonzero_object_generations():
    objects = _objects()
    objects[3] = objects[3].replace(b"[8 0 R]", b"[8 2 R]")
    objects[8] = b"<< /Subtype /Text /Rect [1 2 3 4] /Contents (parent) >>"
    objects[9] = objects[9].replace(b"/IRT 8 0 R", b"/IRT 8 2 R")
    with Document(_pdf(objects, generations={8: 2})) as document:
        parent, reply = json.loads(document.export_annotations_json())["annotations"]
        assert parent["id"] == "object-8-2"
        assert reply["links"]["in_reply_to"] == parent["id"]


@pytest.mark.parametrize("streaming", [False, True])
def test_export_reads_decrypted_annotation_metadata(document, tmp_path, streaming):
    expected = document.pages[0].annotations.to_list()
    document.encrypt(user_password="secret", owner_password="owner")
    path = tmp_path / "encrypted.pdf"
    document.save(path)
    opener = Document.open_streaming if streaming else Document
    with opener(path, password="secret") as reopened:
        actual = json.loads(reopened.export_annotations_json(pages=[0]))["annotations"]
        assert actual == expected


def test_widget_field_values_and_field_parent_are_not_exported():
    with Document() as document:
        page = document.pages.add()
        document.form.add_text_field(
            "private-field", page, (10, 20, 100, 40), value="sensitive-value"
        )
        data = document.export_annotations_json()
        (record,) = json.loads(data)["annotations"]
        assert record["subtype"] == "Widget"
        assert b"sensitive-value" not in data
        assert record["links"]["parent"] is None


def test_inline_annotations_and_missing_optional_fields_export():
    objects = _objects()
    objects[3] = (
        b"<< /Type /Page /Parent 2 0 R /Annots [<< /Subtype /Text /Rect [1 2 3 4] >>] >>"
    )
    with Document(_pdf(objects)) as document:
        (record,) = document.pages[0].annotations.to_list()
        assert record["id"] == "page-0-annotation-0"
        assert record["author"] == record["contents"] == ""
        assert record["links"] == {"in_reply_to": None, "popup": None, "parent": None}


def test_file_export_overwrite_and_page_export_to_binary_stream(document, tmp_path):
    path = tmp_path / "nested" / "annotations.json"
    data = document.export_annotations_json(path, overwrite=False)
    assert path.read_bytes() == data
    with pytest.raises(FileExistsError):
        document.export_annotations_json(path, pages=[], overwrite=False)
    assert path.read_bytes() == data
    assert document.export_annotations_json(path, pages=[]) == path.read_bytes()
    output = io.BytesIO(b"prefix")
    output.seek(0, io.SEEK_END)
    data = document.pages[1].annotations.export_json(output)
    assert output.getvalue() == b"prefix" + data and not output.closed
    assert json.loads(data)["annotations"][0]["page_index"] == 1


def test_short_stream_writes_are_retried(document):
    class ShortWriter(io.BytesIO):
        def write(self, data):
            return super().write(data[:3])

    output = ShortWriter()
    assert document.export_annotations_json(output) == output.getvalue()
    assert not output.closed


@pytest.mark.parametrize("result", [0, None, True, -1, 10**9])
def test_stalled_and_invalid_stream_writes_raise(document, result):
    class InvalidWriter:
        def write(self, data):
            return result

    with pytest.raises(AsposePdfException, match="did not consume"):
        document.export_annotations_json(InvalidWriter())


@pytest.mark.parametrize("overwrite", [None, 0, 1, "yes"])
def test_overwrite_requires_a_boolean(document, overwrite):
    with pytest.raises(PdfValidationException, match="overwrite"):
        document.export_annotations_json(overwrite=overwrite)


def test_atomic_write_failure_preserves_existing_file(document, tmp_path, monkeypatch):
    import aspose_pdf.engine.file_output as output

    path = tmp_path / "annotations.json"
    path.write_bytes(b"original")

    def failure(*args):
        raise OSError("simulated write failure")

    monkeypatch.setattr(output.os, "replace", failure)
    with pytest.raises(OSError, match="simulated write failure"):
        document.export_annotations_json(path)
    assert path.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize(
    "entry",
    [
        b"null",
        b"42",
        b"<< /Rect [1 2 3 4] >>",
        b"<< /Subtype /Text /Rect [1 2 3] >>",
        b"<< /Subtype /Text /Rect [true 2 3 4] >>",
        b"<< /Subtype /Text /Rect [1 2 3 4] /Contents /WrongType >>",
    ],
)
def test_malformed_annotation_metadata_is_rejected_before_writing(entry, tmp_path):
    objects = _objects()
    objects[8] = entry
    path = tmp_path / "annotations.json"
    path.write_bytes(b"original")
    with Document(_pdf(objects)) as document:
        with pytest.raises(PdfValidationException):
            document.export_annotations_json(path, pages=[0])
    assert path.read_bytes() == b"original"


def test_invalid_annots_array_is_rejected():
    objects = _objects()
    objects[3] = b"<< /Type /Page /Parent 2 0 R /Annots (invalid) >>"
    with Document(_pdf(objects)) as document:
        with pytest.raises(PdfValidationException, match="Annots"):
            document.export_annotations_json()


def test_non_finite_metadata_and_cycles_fail_without_partial_output():
    for value in (PdfNumber(float("nan")), PdfNumber(float("inf"))):
        with Document() as document:
            document.pages.add().annotations.add_text((0, 0, 20, 20))
            engine = document._engine_pdf
            obj = engine._resolve(engine._annotation_refs(0)[0])
            obj.mapping[PdfName("CA")] = value
            output = io.BytesIO()
            with pytest.raises(PdfValidationException, match="non-finite"):
                document.export_annotations_json(output)
            assert output.getvalue() == b""
    with Document() as document:
        document.pages.add().annotations.add_text((0, 0, 20, 20))
        engine = document._engine_pdf
        obj = engine._resolve(engine._annotation_refs(0)[0])
        cyclic = PdfDictionary()
        cyclic.mapping[PdfName("Self")] = cyclic
        obj.mapping[PdfName("BS")] = cyclic
        with pytest.raises(PdfValidationException, match="cycle"):
            document.export_annotations_json()


def test_metadata_conversion_and_selector_work_respect_resource_limits():
    with Document(limits=PdfLoadLimits(max_container_items=20)) as document:
        page = document.pages.add()
        for number in range(4):
            page.annotations.add_text((0, 0, 20, 20), str(number))
        with pytest.raises(PdfResourceLimitException, match="annotation export values"):
            document.export_annotations_json()
        with pytest.raises(PdfResourceLimitException, match="page selection"):
            document.export_annotations_json(pages=[0] * 21)
        with pytest.raises(PdfResourceLimitException, match="subtype selection"):
            document.export_annotations_json(subtypes=["Text"] * 21)
    with Document(limits=PdfLoadLimits(max_nesting_depth=4)) as document:
        document.pages.add().annotations.add_text((0, 0, 20, 20))
        engine = document._engine_pdf
        obj = engine._resolve(engine._annotation_refs(0)[0])
        value = PdfString(b"nested")
        for _ in range(6):
            value = PdfArray([value])
        obj.mapping[PdfName("BS")] = value
        with pytest.raises(PdfResourceLimitException, match="property depth"):
            document.export_annotations_json()


def test_json_encoder_rejects_invalid_unicode():
    from aspose_pdf.engine.annotation_json import to_json

    with pytest.raises(PdfValidationException, match="UTF-8 JSON"):
        to_json([{"contents": "\ud800"}])
