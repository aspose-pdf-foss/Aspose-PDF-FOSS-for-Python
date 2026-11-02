"""Package inferred tables as separate CSV files with stable source provenance."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from itertools import repeat

import pytest

from aspose_pdf import (
    Document,
    ExtractedTable,
    PdfLoadLimits,
    PdfResourceLimitException,
)
from aspose_pdf.exceptions import AsposePdfException, PdfValidationException


def _grid(page, rows, y=700):
    for row_index, row in enumerate(rows):
        for column, text in enumerate(row):
            if text:
                page.add_text(text, 60 + column * 60, y - row_index * 15, font_size=10)


def _files(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.testzip() is None
        return {name: archive.read(name) for name in archive.namelist()}


@pytest.fixture
def document():
    with Document() as doc:
        for index in range(3):
            page = doc.pages.add()
            page.add_text("Inventory report", 60, 760, font_size=20)
            page.add_text(
                "The table below lists available inventory.", 60, 730, font_size=10
            )
            _grid(
                page,
                [
                    ["Item", "Qty", "Price"],
                    [f"Widget{index}", "2", "9.99"],
                    ["Nut", "4", "0.50"],
                ],
            )
            page.add_text("- First inventory check", 70, 630, font_size=10)
            page.add_text("- Second inventory check", 70, 615, font_size=10)
            _grid(page, [["Part", "Count"], ["Bolt", "20"]], y=580)
            page.add_text("End of the inventory report.", 60, 530, font_size=10)
        yield doc


def test_order_repeated_pages_and_manifest_match_extracted_tables(document):
    pages = [2, 0, 2]
    tables = document.extract_tables(pages=pages)
    data = document.export_tables_zip(pages=iter(pages), include_manifest=True)
    files = _files(data)
    names = [f"table-{index:04d}.csv" for index in range(1, 7)]
    assert list(files) == [*names, "manifest.json"]
    assert [files[name] for name in names] == [
        table.to_csv().encode() for table in tables
    ]
    assert json.loads(files["manifest.json"]) == {
        "schema_version": 1,
        "delimiter": ",",
        "encoding": "utf-8",
        "tables": [
            dict(
                archive_name=name,
                page_index=table.page_index,
                table_index=index % 2,
                row_count=table.row_count,
                column_count=table.column_count,
                size=len(files[name]),
            )
            for index, (name, table) in enumerate(zip(names, tables))
        ],
    }


@pytest.mark.parametrize(
    "compression,method",
    [("deflate", zipfile.ZIP_DEFLATED), (" STORED ", zipfile.ZIP_STORED)],
)
def test_default_export_and_metadata_are_deterministic(document, compression, method):
    options = dict(compression=compression, compresslevel=3, include_manifest=True)
    data = document.export_tables_zip(**options)
    assert document.export_tables_zip(**options) == data
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert len(archive.infolist()) == 7
        for info in archive.infolist():
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert info.create_system == 3 and info.external_attr >> 16 == 0o100644
            assert info.compress_type == method
            assert "/" not in info.filename and "\\" not in info.filename


def test_page_export_retains_original_page_indices(document):
    files = _files(document.pages[1].export_tables_zip(include_manifest=True))
    assert list(files) == ["table-0001.csv", "table-0002.csv", "manifest.json"]
    assert [
        item["page_index"] for item in json.loads(files["manifest.json"])["tables"]
    ] == [1, 1]


@pytest.mark.parametrize("delimiter", [",", ";", "\t"])
@pytest.mark.parametrize(
    "encoding", ["utf-8", "utf-8-sig", "utf-16", "utf-32", "latin-1"]
)
def test_csv_quoting_encoding_and_bom_are_per_file(monkeypatch, delimiter, encoding):
    rows = [["Text", "Value"], ['café "quoted"\nnext', "=SUM(1,2)"], ["0007", ""]]
    table = ExtractedTable(0, rows)
    monkeypatch.setattr(
        "aspose_pdf.table_extraction._page_tables", lambda *a: [table, table]
    )
    with Document() as document:
        document.pages.add()
        files = _files(
            document.export_tables_zip(delimiter=delimiter, encoding=encoding)
        )
    for data in files.values():
        assert data == table.to_csv(delimiter=delimiter).encode(encoding)
        assert (
            list(
                csv.reader(
                    io.StringIO(data.decode(encoding), newline=""), delimiter=delimiter
                )
            )
            == rows
        )


@pytest.mark.parametrize("include_manifest", [False, True])
@pytest.mark.parametrize(
    "kind", ["empty-document", "empty-page", "empty-selection", "prose"]
)
def test_no_tables_produces_a_valid_empty_archive(include_manifest, kind):
    with Document() as document:
        if kind != "empty-document":
            page = document.pages.add()
            if kind == "prose":
                page.add_text("Ordinary prose with no text grid", 30, 700)
        files = _files(
            document.export_tables_zip(
                pages=[] if kind == "empty-selection" else None,
                include_manifest=include_manifest,
            )
        )
        assert list(files) == (["manifest.json"] if include_manifest else [])
        if include_manifest:
            assert json.loads(files["manifest.json"])["tables"] == []


@pytest.mark.parametrize(
    "options",
    [
        {"delimiter": "::"},
        {"delimiter": '"'},
        {"delimiter": "\n"},
        {"encoding": "missing-codec"},
        {"compression": "gzip"},
        {"compresslevel": True},
        {"compresslevel": 10},
        {"include_manifest": 1},
        {"clip_to_page": 1},
        {"overwrite": None},
        {"pages": "0"},
        {"pages": [True]},
        {"pages": [-1]},
        {"pages": [0, 100]},
    ],
)
def test_invalid_options_fail_before_extraction_or_output(
    document, monkeypatch, options
):
    def unexpected(*args):
        pytest.fail("Options must be validated before extracting pages")

    monkeypatch.setattr("aspose_pdf.table_extraction._page_tables", unexpected)
    output = io.BytesIO(b"Keep")
    with pytest.raises(PdfValidationException):
        document.export_tables_zip(output, **options)
    assert output.getvalue() == b"Keep"


def test_selection_generators_are_bounded(document):
    document._load_limits = PdfLoadLimits(max_container_items=2)
    with pytest.raises(PdfResourceLimitException, match="max_container_items"):
        document.export_tables_zip(pages=repeat(0))


def test_paths_streams_short_writes_and_late_failures_are_safe(
    document, tmp_path, monkeypatch
):
    expected = document.export_tables_zip()
    path = tmp_path / "new" / "tables.zip"
    assert document.export_tables_zip(path) == path.read_bytes() == expected
    with pytest.raises(FileExistsError):
        document.export_tables_zip(path, overwrite=False)

    class Short(io.BytesIO):
        def write(self, data):
            return super().write(data[:3])

    with Short(b"prefix") as output:
        output.seek(6)
        assert document.export_tables_zip(output) == expected
        assert output.getvalue() == b"prefix" + expected and not output.closed
    from aspose_pdf.table_extraction import _page_tables

    def extract(pdf, index, clipped):
        if index == 1:
            raise PdfValidationException("Damaged later page")
        return _page_tables(pdf, index, clipped)

    monkeypatch.setattr("aspose_pdf.table_extraction._page_tables", extract)
    output = io.BytesIO(b"Keep")
    with pytest.raises(PdfValidationException, match="Damaged"):
        document.export_tables_zip(output)
    assert output.getvalue() == b"Keep" and not output.closed
    with pytest.raises(PdfValidationException, match="Damaged"):
        document.export_tables_zip(path)
    assert path.read_bytes() == expected


def test_clip_option_uses_the_existing_table_recognition(document):
    document.pages[0].crop_box = (0, 0, 250, 100)
    assert _files(document.export_tables_zip(pages=[0])) == {}
    assert len(_files(document.export_tables_zip(pages=[0], clip_to_page=False))) == 2


def test_streaming_reads_only_selected_pages_and_does_not_render(
    document, tmp_path, monkeypatch
):
    path = tmp_path / "source.pdf"
    document.save(path)
    with Document.open_streaming(path) as streamed:
        pdf = streamed._engine_pdf
        original = pdf.get_page_content
        reads = []

        def read(index):
            reads.append(index)
            assert index == 1
            return original(index)

        monkeypatch.setattr(pdf, "get_page_content", read)
        monkeypatch.setattr(
            streamed, "render_page", lambda *a, **k: pytest.fail("No rendering")
        )
        assert len(_files(streamed.export_tables_zip(pages=[1]))) == 2
        assert reads == [1] and pdf._lazy and pdf.page_contents == []


@pytest.mark.parametrize(
    "limit",
    [
        "max_object_bytes",
        "max_decoded_stream_bytes",
        "max_total_decoded_bytes",
        "max_codec_work_bytes",
        "max_container_items",
    ],
)
def test_export_limits_preserve_the_output(document, limit):
    document._load_limits = PdfLoadLimits(**{limit: 3})
    output = io.BytesIO(b"Keep")
    with pytest.raises(PdfResourceLimitException, match=limit):
        document.export_tables_zip(output, include_manifest=True)
    assert output.getvalue() == b"Keep"


def test_cumulative_csv_and_manifest_bytes_are_bounded(monkeypatch):
    table = ExtractedTable(0, [["A", "B"], ["1", "2"]])
    monkeypatch.setattr(
        "aspose_pdf.table_extraction._page_tables", lambda *a: [table, table]
    )
    with Document() as document:
        document.pages.add()
        document._load_limits = PdfLoadLimits(
            max_total_decoded_bytes=len(table.to_csv().encode()) * 2 - 1
        )
        with pytest.raises(PdfResourceLimitException, match="max_total_decoded_bytes"):
            document.export_tables_zip()
        document._load_limits = PdfLoadLimits(
            max_total_decoded_bytes=len(table.to_csv().encode()) * 2
        )
        with pytest.raises(PdfResourceLimitException, match="max_total_decoded_bytes"):
            document.export_tables_zip(include_manifest=True)
        document._load_limits = PdfLoadLimits(max_object_bytes=100)
        with pytest.raises(PdfResourceLimitException, match="max_object_bytes"):
            document.export_tables_zip(include_manifest=True)


@pytest.mark.parametrize("encoding,limit", [("utf-8", 5), ("utf-16", 10)])
def test_each_csv_file_obeys_the_object_limit_before_output(
    monkeypatch, encoding, limit
):
    table = ExtractedTable(0, [["a", "b", "c"], ["d", "e", "f"]])
    monkeypatch.setattr("aspose_pdf.table_extraction._page_tables", lambda *a: [table])
    with Document() as document:
        document.pages.add()
        document._load_limits = PdfLoadLimits(max_object_bytes=limit)
        output = io.BytesIO(b"Keep")
        with pytest.raises(PdfResourceLimitException, match="max_object_bytes"):
            document.export_tables_zip(output, encoding=encoding)
        assert output.getvalue() == b"Keep"


def test_source_document_is_not_changed_and_stalled_streams_are_rejected(document):
    before = io.BytesIO()
    document.save(before)
    document.export_tables_zip()
    after = io.BytesIO()
    document.save(after)
    assert before.getvalue() == after.getvalue()

    class Stalled:
        def write(self, data):
            return 0

    with pytest.raises(AsposePdfException, match="Stream write"):
        document.export_tables_zip(Stalled())


def test_encoding_failures_and_disposed_documents_are_rejected(document):
    output = io.BytesIO(b"Keep")
    with pytest.raises(PdfValidationException):
        document.export_tables_zip(output, delimiter="é", encoding="ascii")
    assert output.getvalue() == b"Keep"
    page = document.pages[0]
    document.dispose()
    with pytest.raises(AsposePdfException):
        document.export_tables_zip()
    with pytest.raises(AsposePdfException):
        page.export_tables_zip()
