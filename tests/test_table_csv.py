"""CSV imports build editable tables while preserving original cell strings."""

from __future__ import annotations

import csv
import io
from dataclasses import replace

import pytest

from aspose_pdf import Document, PdfLoadLimits, PdfResourceLimitException, Table
from aspose_pdf.exceptions import PdfValidationException


def _cells(table):
    return [[cell.text for cell in row.cells] for row in table.rows]


@pytest.mark.parametrize("kind", ["bytes", "bytearray", "path", "stream"])
def test_csv_sources_preserve_values_and_leave_inputs_untouched(kind, tmp_path):
    data = b'\xef\xbb\xbfCode,Description,Value\r\n001,"Widget, large",false\r\n002,"Two ""quotes""",\r\n'
    path = tmp_path / "items.csv"
    path.write_bytes(data)
    with io.BytesIO(b"prefix" + data) as stream:
        stream.seek(6)
        source = {
            "bytes": data,
            "bytearray": bytearray(data),
            "path": path,
            "stream": stream,
        }[kind]
        table = Table.from_csv(source, width=300, auto_column_widths=True)
        assert _cells(table) == [
            ["Code", "Description", "Value"],
            ["001", "Widget, large", "false"],
            ["002", 'Two "quotes"', ""],
        ]
        assert [row.header for row in table] == [True, False, False]
        assert table.width == 300 and table.auto_column_widths
        assert not stream.closed and stream.getvalue() == b"prefix" + data
    assert path.read_bytes() == data


@pytest.mark.parametrize(
    "encoding,delimiter", [("utf-8-sig", ","), ("utf-16", ";"), ("cp1251", "\t")]
)
def test_csv_encoding_quoting_multiline_and_empty_cells(encoding, delimiter):
    data = f'\nName{delimiter}{delimiter}Name\r\n"Привет\r\nмир"{delimiter}{delimiter}001\r\n'.encode(
        encoding
    )
    table = Table.from_csv(data, encoding=encoding, delimiter=delimiter)
    assert _cells(table) == [["Name", "", "Name"], ["Привет\r\nмир", "", "001"]]


@pytest.mark.parametrize("header", [False, True])
def test_first_row_is_retained_with_optional_header_and_blank_records_are_skipped(
    header,
):
    table = Table.from_csv(b'\n\r\n"",Same,Same\n\n001,,=SUM(A1:A2)\n', header=header)
    assert _cells(table) == [["", "Same", "Same"], ["001", "", "=SUM(A1:A2)"]]
    assert [row.header for row in table] == [header, False]
    assert _cells(Table.from_csv(b'""\n', header=header)) == [[""]]


@pytest.mark.parametrize(
    "header,repeat_header", [(True, True), (True, False), (False, True)]
)
def test_imported_tables_paginate_and_repeat_headers_after_pdf_round_trips(
    tmp_path, header, repeat_header
):
    data = (
        "Code,Description\n"
        + "".join(f"{index:03d},Product {index}\n" for index in range(25))
    ).encode()
    table = Table.from_csv(
        data, header=header, repeat_header=repeat_header, width=180, font_size=9
    )
    table.rows[1].cells[1].text = "Edited product"
    with Document() as document:
        page = document.pages.add(size=(220, 160))
        placement = page.add_table(table, 20, 140, bottom_margin=20)
        assert len(placement["pages"]) > 1
        output = tmp_path / "table.pdf"
        document.save(output)
        with io.BytesIO() as stream:
            document.save(stream)
            raw = stream.getvalue()
        for source in (output, raw):
            with Document(source) as reopened:
                texts = [page.extract_text() for page in reopened.pages]
                assert "Edited product" in "\n".join(texts)
                assert "024" in "\n".join(texts)
                assert texts[0].startswith("Code")
                assert all(
                    text.startswith("Code") == (header and repeat_header)
                    for text in texts[1:]
                )


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\n\r\n",
        b"a,b\nc\n",
        b"a\nb,c\n",
        b'a\n"unterminated',
        b'a\n"closed"junk\n',
        b"a\n\xff\n",
    ],
)
def test_empty_ragged_malformed_or_undecodable_csv_is_rejected(data):
    with pytest.raises(PdfValidationException):
        Table.from_csv(data)


@pytest.mark.parametrize(
    "options,error",
    [
        ({"header": 1}, TypeError),
        ({"encoding": 3}, TypeError),
        ({"encoding": "missing-codec"}, PdfValidationException),
        ({"encoding": "base64_codec"}, PdfValidationException),
        ({"encoding": "rot_13"}, PdfValidationException),
        ({"delimiter": 1}, TypeError),
        ({"delimiter": "::"}, PdfValidationException),
        ({"delimiter": '"'}, PdfValidationException),
        ({"delimiter": "\n"}, PdfValidationException),
        ({"limits": {}}, TypeError),
        ({"width": -1}, PdfValidationException),
        ({"rows": []}, TypeError),
        ({"unknown_option": True}, TypeError),
    ],
)
def test_invalid_options_are_rejected_before_reading_csv(options, error):
    class UnreadSource:
        def read(self, size=-1):
            pytest.fail("Validate table and CSV options before reading")

    with pytest.raises(error):
        Table.from_csv(UnreadSource(), **options)


@pytest.mark.parametrize(
    "data,limit",
    [
        (b"Code\n001\n", "max_input_bytes"),
        (b"a,b,c\n1,2,3\n", "max_container_items"),
        (b"Code\nLong field value\n", "max_object_bytes"),
        (b"Code\n001\n", "max_total_decoded_bytes"),
    ],
)
def test_csv_import_obeys_resource_limits(data, limit):
    limits = replace(PdfLoadLimits.unlimited(), **{limit: 8})
    if limit == "max_container_items":
        limits = replace(limits, max_container_items=7)
    with pytest.raises(PdfResourceLimitException, match=limit):
        Table.from_csv(data, limits=limits)


def test_csv_field_limit_and_stream_ownership_are_preserved_on_errors():
    before = csv.field_size_limit()
    with io.BytesIO(b'a\n"unterminated') as stream:
        with pytest.raises(PdfValidationException):
            Table.from_csv(stream)
        assert not stream.closed
    with io.StringIO("a\ntext\n") as stream:
        with pytest.raises(TypeError, match="bytes"):
            Table.from_csv(stream)
        assert not stream.closed
    with pytest.raises(PdfValidationException, match="field larger than field limit"):
        Table.from_csv(b"a\n" + b"x" * (before + 1) + b"\n")
    assert csv.field_size_limit() == before
