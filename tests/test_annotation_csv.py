"""Annotation CSV reports preserve review text and source relationships."""

from __future__ import annotations

import csv
import io
import json
import zlib

import pytest

from aspose_pdf import (
    AnnotationType,
    Document,
    PdfLoadLimits,
    PdfResourceLimitException,
)
from aspose_pdf.engine.cos import PdfDictionary, PdfName, PdfNumber, PdfStream
from aspose_pdf.exceptions import AsposePdfException, PdfValidationException

_COLUMNS = [
    "page_number",
    "annotation_index",
    "id",
    "subtype",
    "author",
    "subject",
    "contents",
    "created",
    "modified",
    "in_reply_to",
    "popup",
    "parent",
]


@pytest.fixture
def document():
    with Document() as document:
        page = document.pages.add()
        note = page.annotations.add_text(
            (10, 20, 30, 40), 'Привет "reader"\nnext line', title="Alice Иванова"
        )
        note.subject = "Review"
        note.creation_date = "D:20260102030405+02'00'"
        note.modified = "D:20260203040506Z"
        reply = note.reply("Reply", title="Bob")
        reply.reply("Nested reply")
        note.add_popup((40, 20, 200, 100), is_open=True)
        document.pages.add().annotations.add_highlight(
            (50, 200, 200, 220), "Highlight", title="Carol"
        )
        yield document


def _saved(document):
    with io.BytesIO() as output:
        document.save(output)
        return output.getvalue()


def _report(data, *, delimiter=",", encoding="utf-8"):
    reader = csv.DictReader(
        io.StringIO(data.decode(encoding), newline=""), delimiter=delimiter
    )
    rows = list(reader)
    assert reader.fieldnames == _COLUMNS
    return rows


@pytest.mark.parametrize("kind", ["live", "bytes", "file", "streaming"])
def test_report_matches_review_metadata_and_preserves_pdf(document, kind, tmp_path):
    raw = _saved(document)
    path = tmp_path / "input.pdf"
    path.write_bytes(raw)
    if kind == "live":
        loaded = document
    elif kind == "bytes":
        loaded = Document(raw)
    else:
        loaded = (Document.open_streaming if kind == "streaming" else Document)(path)
    try:
        records = json.loads(loaded.export_annotations_json())["annotations"]
        data = loaded.export_annotations_csv()
        rows = _report(data)
        assert len(rows) == len(records) == 5
        assert [row["page_number"] for row in rows] == ["1", "1", "1", "1", "2"]
        for row, record in zip(rows, records, strict=True):
            assert row["annotation_index"] == str(record["annotation_index"])
            for key in ("id", "subtype", "author", "contents"):
                assert row[key] == record[key]
            for column, key in (
                ("subject", "Subj"),
                ("created", "CreationDate"),
                ("modified", "M"),
            ):
                assert row[column] == record["properties"].get(key, "")
            for key in ("in_reply_to", "popup", "parent"):
                assert row[key] == (record["links"][key] or "")
        assert rows[0]["created"] == "D:20260102030405+02'00'"
        assert data.endswith(b"\r\n") and not data.startswith(b"\xef\xbb\xbf")
        assert loaded.export_annotations_csv() == data
        assert loaded.pages[
            0
        ].annotations.export_csv() == loaded.export_annotations_csv(pages=[0])
        assert _saved(loaded) == raw
    finally:
        if loaded is not document:
            loaded.dispose()
    assert path.read_bytes() == raw


def test_reply_and_popup_ids_allow_review_threads_to_be_rebuilt(document):
    note, reply, nested, popup, _highlight = _report(document.export_annotations_csv())
    assert reply["in_reply_to"] == note["id"]
    assert nested["in_reply_to"] == reply["id"]
    assert note["popup"] == popup["id"]
    assert popup["parent"] == note["id"]
    assert not note["in_reply_to"] and not note["parent"]


def test_cross_page_reply_keeps_id_when_parent_is_outside_selection(document):
    page = document.pages[1]
    page.annotations.add_text((10, 20, 30, 40), "Cross-page reply")
    engine = document._engine_pdf
    parent = engine._annotation_refs(0)[0]
    reply = engine._resolve(engine._annotation_refs(1)[-1])
    reply.mapping[PdfName("IRT")] = parent
    all_rows = _report(document.export_annotations_csv())
    selected = _report(document.export_annotations_csv(pages=[1]))
    assert selected[-1]["in_reply_to"] == all_rows[0]["id"]
    assert all(row["id"] != selected[-1]["in_reply_to"] for row in selected)


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
def test_subtype_filters_match_existing_export_and_keep_original_positions(
    document, subtypes
):
    (row,) = _report(document.export_annotations_csv(subtypes=subtypes))
    assert row["page_number"] == "2" and row["annotation_index"] == "0"
    assert row["subtype"] == "Highlight"
    assert document.pages[1].annotations.export_csv(
        subtypes="Highlight"
    ) == document.export_annotations_csv(pages=[1], subtypes="Highlight")


def test_page_selection_keeps_order_deduplicates_and_reports_header_only_empty_results(
    document,
):
    rows = _report(
        document.export_annotations_csv(pages=(index for index in [1, 0, 1]))
    )
    assert [row["page_number"] for row in rows] == ["2", "1", "1", "1", "1"]
    for options in ({"pages": []}, {"subtypes": []}, {"subtypes": "VendorMissing"}):
        assert _report(document.export_annotations_csv(**options)) == []
    with Document() as empty:
        assert _report(empty.export_annotations_csv()) == []


@pytest.mark.parametrize("delimiter", [",", ";", "\t", "|"])
@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16", "cp1251"])
def test_text_quotes_newlines_delimiters_and_encodings_are_preserved(
    document, delimiter, encoding
):
    value = 'Привет, a;b\tc|d "quoted"\nnext\rthird\r\nfourth'
    document.pages[0].annotations[0].contents = value
    data = document.export_annotations_csv(delimiter=delimiter, encoding=encoding)
    assert _report(data, delimiter=delimiter, encoding=encoding)[0]["contents"] == value
    if encoding == "utf-8-sig":
        assert data.startswith(b"\xef\xbb\xbf") and data.count(b"\xef\xbb\xbf") == 1
    elif encoding == "utf-16":
        assert data.startswith((b"\xff\xfe", b"\xfe\xff"))
        assert data.count(data[:2]) == 1


def test_paths_overwrite_and_page_stream_exports_return_the_written_bytes(
    document, tmp_path
):
    path = tmp_path / "nested" / "review.csv"
    data = document.export_annotations_csv(path, overwrite=False)
    assert path.read_bytes() == data
    with pytest.raises(FileExistsError):
        document.export_annotations_csv(path, pages=[], overwrite=False)
    assert path.read_bytes() == data
    replacement = document.export_annotations_csv(path, pages=[])
    assert path.read_bytes() == replacement != data
    assert _report(path.read_bytes()) == []
    with io.BytesIO(b"prefix") as output:
        output.seek(6)
        data = document.pages[1].annotations.export_csv(output)
        assert output.getvalue() == b"prefix" + data and not output.closed
        assert _report(data)[0]["page_number"] == "2"


def test_short_writes_are_retried_and_stalled_writers_are_rejected(document):
    class ShortWriter(io.BytesIO):
        def write(self, data):
            return super().write(data[:3])

    class StalledWriter(io.BytesIO):
        def write(self, data):
            return 0

    with ShortWriter() as output:
        assert document.export_annotations_csv(output) == output.getvalue()
        assert not output.closed
    with pytest.raises(AsposePdfException, match="did not consume"):
        document.export_annotations_csv(StalledWriter())
    with pytest.raises(TypeError):
        document.export_annotations_csv(io.StringIO())


@pytest.mark.parametrize(
    "options",
    [
        {"pages": [0, 2]},
        {"pages": [True]},
        {"pages": "0"},
        {"subtypes": [1]},
        {"subtypes": ""},
        {"delimiter": "::"},
        {"delimiter": '"'},
        {"delimiter": "\r"},
        {"delimiter": "\n"},
        {"delimiter": "\x00"},
        {"delimiter": "\ud800"},
        {"encoding": "missing-encoding"},
        {"encoding": "base64_codec"},
        {"overwrite": 1},
    ],
)
def test_invalid_options_write_nothing_and_fail_before_metadata_reads(
    document, tmp_path, monkeypatch, options
):
    path = tmp_path / "review.csv"
    path.write_bytes(b"original")

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid options must fail before reading annotations")

    monkeypatch.setattr(document._engine_pdf, "_get_page_dict", forbidden)
    with pytest.raises((PdfValidationException, TypeError)):
        document.export_annotations_csv(path, **options)
    assert path.read_bytes() == b"original"


@pytest.mark.parametrize("key", ["Contents", "T", "Subj", "CreationDate", "M"])
def test_invalid_later_annotation_metadata_cannot_write_a_partial_report(
    document, tmp_path, key
):
    engine = document._engine_pdf
    obj = engine._resolve(engine._annotation_refs(1)[0])
    obj.mapping[PdfName(key)] = PdfNumber(42)
    path = tmp_path / "review.csv"
    path.write_bytes(b"original")
    with pytest.raises(PdfValidationException, match="text string"):
        document.export_annotations_csv(path)
    assert path.read_bytes() == b"original"


def test_encoding_failure_creates_no_output_directories(document, tmp_path):
    path = tmp_path / "new" / "review.csv"
    with pytest.raises(PdfValidationException, match="encoded as CSV"):
        document.export_annotations_csv(path, encoding="ascii")
    assert not path.parent.exists()


def test_unknown_types_hidden_annotations_and_missing_values_are_reported():
    with Document() as document:
        page = document.pages.add()
        page.crop_box = (100, 100, 300, 300)
        page.rotation = 90
        page.annotations.add(
            "VendorType", (-10, -20, 10, 20), "Hidden", properties={"F": 2}
        )
        (row,) = _report(document.export_annotations_csv())
        assert row["subtype"] == "VendorType" and row["contents"] == "Hidden"
        assert all(
            row[key] == ""
            for key in ("author", "subject", "created", "modified", "in_reply_to")
        )


@pytest.mark.parametrize("encrypted", [False, True])
def test_streaming_report_does_not_decode_streams_or_export_widget_values(
    tmp_path, monkeypatch, encrypted
):
    path = tmp_path / "source.pdf"
    with Document() as source:
        page = source.pages.add()
        page.add_text("Private page content", 10, 700)
        page.annotations.add_text((10, 20, 30, 40), "Review")
        source.form.add_text_field(
            "Field", page, (20, 600, 200, 620), value="Private answer"
        )
        engine = source._engine_pdf
        annotation = engine._resolve(engine._annotation_refs(0)[0])
        stream = engine._cos_doc.register_object(
            PdfStream(
                mapping={PdfName("Filter"): PdfName("FlateDecode")},
                content=zlib.compress(b"Private appearance" * 2000),
            )
        )
        annotation.mapping[PdfName("AP")] = PdfDictionary({PdfName("N"): stream})
        annotation.mapping[PdfName("RC")] = stream
        annotation.mapping[PdfName("Measure")] = PdfDictionary(
            {PdfName("Private"): stream}
        )
        if encrypted:
            source.encrypt("secret", "owner")
        source.save(path)
    before = path.read_bytes()
    with Document.open_streaming(
        path, password="secret" if encrypted else None
    ) as document:

        def forbidden(*args, **kwargs):
            pytest.fail("CSV reports must read metadata without decoding streams")

        monkeypatch.setattr(document._engine_pdf, "_decode_cos_stream", forbidden)
        monkeypatch.setattr(document._engine_pdf, "get_page_content", forbidden)
        data = document.export_annotations_csv()
        rows = _report(data)
        assert [row["subtype"] for row in rows] == ["Text", "Widget"]
        assert rows[0]["contents"] == "Review"
        assert b"Private" not in data
        assert document._engine_pdf.page_contents == []
    assert path.read_bytes() == before


def test_report_ignores_unused_cyclic_style_properties(document):
    engine = document._engine_pdf
    annotation = engine._resolve(engine._annotation_refs(0)[0])
    annotation.mapping[PdfName("Measure")] = annotation
    assert _report(document.export_annotations_csv())[0]["subject"] == "Review"


@pytest.mark.parametrize(
    "name,value",
    [
        ("max_object_bytes", 25),
        ("max_decoded_stream_bytes", 150),
        ("max_total_decoded_bytes", 150),
        ("max_container_items", 11),
    ],
)
def test_resource_limits_apply_before_writing(monkeypatch, name, value):
    import aspose_pdf.engine.annotation_csv as export_module

    record = {
        "page_index": 0,
        "annotation_index": 0,
        "id": "object-1-0",
        "subtype": "Text",
        "author": "A",
        "contents": "x" * 100,
        "properties": {},
        "links": {"in_reply_to": None, "popup": None, "parent": None},
    }
    monkeypatch.setattr(
        export_module, "iter_records", lambda *args, **kwargs: iter([record])
    )
    with (
        Document(limits=PdfLoadLimits(**{name: value})) as document,
        io.BytesIO(b"original") as output,
    ):
        with pytest.raises(PdfResourceLimitException, match=name):
            document.export_annotations_csv(output)
        assert output.getvalue() == b"original"


def test_disposed_document_and_page_collection_reject_reports(document):
    annotations = document.pages[0].annotations
    document.dispose()
    with pytest.raises(AsposePdfException, match="disposed"):
        document.export_annotations_csv()
    with pytest.raises(AsposePdfException, match="disposed"):
        annotations.export_csv()


def test_output_limit_stops_metadata_iteration_before_reading_more_records(monkeypatch):
    import aspose_pdf.engine.annotation_csv as export_module

    def records(*args, **kwargs):
        yield {
            "page_index": 0,
            "annotation_index": 0,
            "id": "object-1-0",
            "subtype": "Text",
            "author": "A",
            "contents": "x" * 100,
            "properties": {},
            "links": {"in_reply_to": None, "popup": None, "parent": None},
        }
        pytest.fail("An oversized report must stop before requesting another snapshot")

    monkeypatch.setattr(export_module, "iter_records", records)
    with Document(limits=PdfLoadLimits(max_decoded_stream_bytes=150)) as document:
        with pytest.raises(PdfResourceLimitException, match="max_decoded_stream_bytes"):
            document.export_annotations_csv()
