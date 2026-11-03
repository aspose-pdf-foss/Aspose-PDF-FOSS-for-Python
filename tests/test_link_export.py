"""Portable link reports retain source locations, targets and diagnostics."""

from __future__ import annotations

import csv
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
    GoToRAction,
    JavaScriptAction,
    NamedAction,
    PdfLoadLimits,
    PdfResourceLimitException,
    URIAction,
    XYZDestination,
)
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName, PdfNumber, PdfString
from aspose_pdf.engine.simple_pdf import CosExtractor
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfParseException,
    PdfValidationException,
)

RECT = (10, 20, 100, 40)
URI = 'https://example.test/資料?q="résumé",value\nnext'
COLUMNS = [
    "page_number", "annotation_index", "action_type", "action_path",
    "uri", "resolved_uri", "target_page_number", "remote_page_number",
    "destination_name", "destination_view", "destination_parameters", "file",
    "named_action", "x0", "y0", "x1", "y1", "issues",
]


@pytest.fixture
def document():
    with Document() as document:
        for _ in range(3):
            document.pages.add().add_text("Page text is not part of the report", 5, 10)
        document.pages[1].add_link(RECT, URIAction(URI))
        document.pages[1].add_link(RECT, XYZDestination(3, None, 100, 0))
        document.pages[2].add_link(RECT, GoToRAction("remote.pdf", FitRDestination(8, 1, 2, 3, 4)))
        document.pages[2].add_link(RECT, NamedAction("NextPage"))
        document.pages[2].add_link(RECT, JavaScriptAction("secret_script()"))
        document.pages[3].add_link(RECT, URIAction("mailto:team@example.test"))
        yield document


def _saved(document):
    with io.BytesIO() as output:
        document.save(output)
        return output.getvalue()


def _rows(data, *, delimiter=",", encoding="utf-8"):
    reader = csv.DictReader(io.StringIO(data.decode(encoding), newline=""), delimiter=delimiter)
    rows = list(reader)
    assert reader.fieldnames == COLUMNS
    return rows


def test_json_schema_preserves_typed_targets_and_omits_executable_payloads(document):
    data = document.export_links_json()
    payload = json.loads(data)
    assert payload["schema_version"] == 2
    uri, local, remote, named, script, email = payload["links"]
    assert uri == {
        "page_index": 1, "annotation_index": 0, "rect": list(RECT),
        "action_type": "URI", "action_path": "A", "uri": URI, "resolved_uri": URI,
        "destination": None, "destination_name": None, "target_page_index": None,
        "file": None, "named_action": None, "issues": [],
    }
    assert local["destination"] == {
        "page_index": 3, "view": "XYZ", "parameters": [None, 100, 0],
    }
    assert local["target_page_index"] == 3
    assert remote["destination"] == {
        "page_index": 8, "view": "FitR", "parameters": [1, 2, 3, 4],
    }
    assert remote["target_page_index"] is None and remote["file"] == "remote.pdf"
    assert named["named_action"] == "NextPage"
    assert script["action_type"] == "JavaScript"
    assert email["uri"] == "mailto:team@example.test"
    assert b"secret_script" not in data and b"Page text" not in data
    assert data.endswith(b"\n") and document.export_links_json() == data


def test_csv_has_one_action_per_row_and_distinguishes_remote_from_local_pages(document):
    data = document.export_links_csv()
    uri, local, remote, named, script, email = _rows(data)
    assert uri["page_number"] == "1" and uri["annotation_index"] == "0"
    assert uri["uri"] == uri["resolved_uri"] == URI
    assert [float(uri[key]) for key in ("x0", "y0", "x1", "y1")] == list(RECT)
    assert uri["issues"] == "[]" and not uri["destination_parameters"]
    assert local["target_page_number"] == "3" and local["remote_page_number"] == ""
    assert local["destination_view"] == "XYZ"
    assert json.loads(local["destination_parameters"]) == [None, 100, 0]
    assert remote["target_page_number"] == "" and remote["remote_page_number"] == "8"
    assert remote["file"] == "remote.pdf" and remote["destination_view"] == "FitR"
    assert named["named_action"] == "NextPage"
    assert script["action_type"] == "JavaScript" and not script["uri"]
    assert email["page_number"] == "3"
    assert b"secret_script" not in data and data.endswith(b"\r\n")
    assert document.export_links_csv() == data


@pytest.mark.parametrize(
    "target,view,parameters",
    [
        (FitDestination(1), "Fit", []), (FitBDestination(1), "FitB", []),
        (XYZDestination(1, None, 0, 0), "XYZ", [None, 0, 0]),
        (FitHDestination(1), "FitH", [None]),
        (FitBHDestination(1, 100), "FitBH", [100]),
        (FitVDestination(1, 20), "FitV", [20]),
        (FitBVDestination(1), "FitBV", [None]),
        (FitRDestination(1, 1, 2, 3, 4), "FitR", [1, 2, 3, 4]),
    ],
)
def test_all_destination_views_keep_null_zero_and_parameter_order(target, view, parameters):
    with Document() as document:
        document.pages.add().add_link(RECT, target)
        destination = json.loads(document.export_links_json())["links"][0]["destination"]
        assert destination == {"page_index": 1, "view": view, "parameters": parameters}
        [row] = _rows(document.export_links_csv())
        assert row["target_page_number"] == "1" and row["destination_view"] == view
        assert json.loads(row["destination_parameters"]) == parameters


def test_named_relative_chained_and_broken_links_retain_their_metadata(document):
    pdf = document._engine_pdf
    catalog = pdf._resolve(pdf._cos_doc.trailer.mapping[PdfName("Root")])
    catalog.mapping[PdfName("URI")] = PdfDictionary({PdfName("Base"): PdfString("https://example.test/base/")})
    document.destinations.add("chapter", FitDestination(3))
    document.pages[3].add_link(RECT, FitDestination(1))
    annotation = pdf._resolve(pdf._annotation_refs(2)[-1])
    annotation.mapping[PdfName("Dest")] = PdfString("chapter")
    document.pages[3].add_link(RECT, URIAction("relative.pdf"))
    annotation = pdf._resolve(pdf._annotation_refs(2)[-1])
    action = pdf._resolve(annotation.mapping[PdfName("A")])
    action.mapping[PdfName("Next")] = PdfDictionary({
        PdfName("S"): PdfName("GoTo"), PdfName("D"): PdfString("missing"),
    })
    annotation.mapping[PdfName("Rect")] = PdfArray([PdfNumber(float("nan"))] * 4)
    records = json.loads(document.export_links_json(pages=[3]))["links"]
    assert records[1]["destination_name"] == "chapter"
    assert records[1]["target_page_index"] == 3
    assert records[2]["resolved_uri"] == "https://example.test/base/relative.pdf"
    assert records[2]["rect"] is None and records[2]["issues"] == ["invalid_rect"]
    assert records[3]["action_path"] == "A.Next[0]"
    assert "missing_named_destination" in records[3]["issues"]
    rows = _rows(document.export_links_csv(pages=[3]))
    assert not rows[2]["x0"] and json.loads(rows[3]["issues"]) == records[3]["issues"]


def test_selections_retain_first_occurrence_order_and_empty_reports_have_a_schema(document):
    records = json.loads(document.export_links_json(pages=(number + 1 if type(number) is int else number for number in (iter([2, 0, 2])))))["links"]
    assert [record["page_index"] for record in records] == [3, 1, 1]
    assert [row["page_number"] for row in _rows(document.export_links_csv(pages=[3, 1, 3]))] == ["3", "1", "1"]
    assert json.loads(document.export_links_json(pages=[])) == {"schema_version": 2, "links": []}
    assert _rows(document.export_links_csv(pages=[])) == []
    with Document() as empty:
        assert json.loads(empty.export_links_json())["links"] == []
        assert _rows(empty.export_links_csv()) == []


@pytest.mark.parametrize("report", ["json", "csv"])
def test_export_does_not_change_the_serialized_pdf(document, report):
    before = _saved(document)
    getattr(document, f"export_links_{report}")()
    assert _saved(document) == before


@pytest.mark.parametrize("report", ["json", "csv"])
def test_unselected_malformed_annotations_are_not_read(document, report):
    pdf = document._engine_pdf
    pdf._get_page_dict(1).mapping[PdfName("Annots")] = PdfString("broken")
    data = getattr(document, f"export_links_{report}")(pages=[3, 1])
    assert len(json.loads(data)["links"] if report == "json" else _rows(data)) == 3


@pytest.mark.parametrize("report", ["json", "csv"])
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("encrypted", [False, True])
def test_exports_do_not_decode_streams_mutate_pdf_or_hydrate_lazy_content(
    document, report, streaming, encrypted, tmp_path, monkeypatch,
):
    path = tmp_path / "source.pdf"
    if encrypted:
        document.encrypt("secret")
    document.save(path)
    original = path.read_bytes()
    opener = Document.open_streaming if streaming else Document
    with opener(path, password="secret" if encrypted else None) as loaded:
        pdf = loaded._engine_pdf
        before = list(pdf.page_contents), pdf._extracted_text

        def forbidden(*args, **kwargs):
            pytest.fail("Link reports must not decode page, image or appearance streams")

        monkeypatch.setattr(CosExtractor, "_decode_stream", forbidden)
        data = getattr(loaded, f"export_links_{report}")(pages=[3, 1])
        assert len(json.loads(data)["links"] if report == "json" else _rows(data)) == 3
        assert (pdf.page_contents, pdf._extracted_text) == before
        assert pdf._lazy == streaming
    assert path.read_bytes() == original


@pytest.mark.parametrize("report", ["json", "csv"])
def test_path_and_short_writing_stream_outputs_match_returned_bytes(document, report, tmp_path):
    export = getattr(document, f"export_links_{report}")
    expected = export()
    target = tmp_path / "nested" / f"links.{report}"
    assert export(target, overwrite=False) == expected == target.read_bytes()
    with pytest.raises(FileExistsError):
        export(target, overwrite=False)
    assert target.read_bytes() == expected

    class ShortWriter(io.BytesIO):
        def write(self, data):
            return super().write(data[:3])

    with ShortWriter(b"prefix:") as stream:
        stream.seek(0, io.SEEK_END)
        assert export(stream) == expected
        assert not stream.closed and stream.getvalue() == b"prefix:" + expected


@pytest.mark.parametrize("delimiter", [",", ";", "\t", "|"])
@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16"])
def test_csv_quoting_unicode_and_custom_encodings_roundtrip(document, delimiter, encoding):
    data = document.export_links_csv(delimiter=delimiter, encoding=encoding)
    assert _rows(data, delimiter=delimiter, encoding=encoding)[0]["uri"] == URI


@pytest.mark.parametrize("report", ["json", "csv"])
@pytest.mark.parametrize("pages", [[True], [-1], [4], [0, 4], ["1"], "odd", 0])
def test_invalid_selections_leave_existing_outputs_intact(document, report, pages, tmp_path):
    target = tmp_path / f"links.{report}"
    target.write_bytes(b"keep")
    with pytest.raises(PdfValidationException):
        getattr(document, f"export_links_{report}")(target, pages=pages)
    assert target.read_bytes() == b"keep"


@pytest.mark.parametrize("options", [
    {"delimiter": ""}, {"delimiter": "aa"}, {"delimiter": "\n"},
    {"delimiter": '"'}, {"delimiter": "\x00"}, {"encoding": "missing-codec"},
    {"encoding": "base64_codec"}, {"overwrite": 1},
])
def test_invalid_csv_options_fail_before_link_metadata_reads(document, options, monkeypatch):
    monkeypatch.setattr(document, "extract_links", lambda **kw: pytest.fail("No metadata reads"))
    with pytest.raises(PdfValidationException):
        document.export_links_csv(**options)


@pytest.mark.parametrize("report", ["json", "csv"])
@pytest.mark.parametrize("limit", ["max_decoded_stream_bytes", "max_total_decoded_bytes"])
def test_report_output_limits_fail_before_writing(report, limit, tmp_path):
    target = tmp_path / "report"
    target.write_bytes(b"keep")
    with Document(limits=PdfLoadLimits(**{limit: 350})) as document:
        document.pages.add().add_link(RECT, URIAction("a" * 100))
        with pytest.raises(PdfResourceLimitException, match=limit):
            getattr(document, f"export_links_{report}")(target)
        assert target.read_bytes() == b"keep"


def test_json_escaping_is_bounded_before_large_output_is_allocated(monkeypatch):
    with Document(limits=PdfLoadLimits(max_decoded_stream_bytes=500)) as document:
        document.pages.add().add_link(RECT, URIAction("\x00" * 100_000))
        original = json.dumps

        def guarded(value, *args, **kwargs):
            if isinstance(value, str):
                assert len(value) <= 65_536, "Large escaped text must be encoded in chunks"
            return original(value, *args, **kwargs)

        monkeypatch.setattr(json, "dumps", guarded)
        with pytest.raises(PdfResourceLimitException, match="max_decoded_stream_bytes"):
            document.export_links_json()


def test_unencodable_csv_preserves_the_existing_file(document, tmp_path):
    target = tmp_path / "links.csv"
    target.write_bytes(b"keep")
    with pytest.raises(PdfValidationException, match="encoded as CSV"):
        document.export_links_csv(target, encoding="ascii")
    assert target.read_bytes() == b"keep"


def test_json_overwrite_must_be_boolean_before_reading_metadata(document, monkeypatch):
    monkeypatch.setattr(document, "extract_links", lambda **kw: pytest.fail("No metadata reads"))
    with pytest.raises(PdfValidationException, match="overwrite"):
        document.export_links_json(overwrite="yes")


@pytest.mark.parametrize("report", ["json", "csv"])
def test_disposed_documents_and_malformed_containers_are_reported(document, report):
    export = getattr(document, f"export_links_{report}")
    page = document._engine_pdf._get_page_dict(0)
    page.mapping[PdfName("Annots")] = PdfString("broken")
    with pytest.raises(PdfParseException):
        export()
    document.dispose()
    with pytest.raises(AsposePdfException, match="disposed"):
        export()
