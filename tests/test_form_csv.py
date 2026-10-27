"""CSV batches fill independent PDFs without guessing field types or values."""

from __future__ import annotations

import csv
import io
from dataclasses import replace
from itertools import repeat

import pytest

from aspose_pdf import (
    ByteArrayDataSource,
    DataSource,
    Document,
    FileDataSource,
    FormFiller,
    FormFillerOptions,
    PdfLoadLimits,
    PdfResourceLimitException,
)
from aspose_pdf.exceptions import PdfValidationException


def _template(title="Base"):
    with Document() as document:
        page = document.pages.add(size=(260, 200))
        page.add_text("Reusable template", 10, 180)
        document.info["Title"] = title
        document.form.add_text_field(
            "person.name", page, (10, 120, 240, 145), value=title
        )
        document.form.add_text_field("code", page, (10, 80, 240, 105), value="Original")
        document.form.add_checkbox("agree", page, (10, 40, 30, 60))
        with io.BytesIO() as output:
            document.save(output)
            return output.getvalue()


def _values(result, *, password=None):
    with Document(result.to_array(), password=password) as document:
        return document.form.to_dict()


@pytest.mark.parametrize("kind", ["bytes", "bytearray", "file", "stream"])
def test_csv_rows_fill_independent_pdfs_and_leave_sources_untouched(kind, tmp_path):
    data = b'person.name,code,agree\r\n"Alice, A.",00123,YES\r\nBob,false,no\r\n'
    path = tmp_path / "people.csv"
    path.write_bytes(data)
    with io.BytesIO(b"prefix" + data) as stream:
        stream.seek(6)
        source = {
            "bytes": data,
            "bytearray": bytearray(data),
            "file": path,
            "stream": stream,
        }[kind]
        options = FormFillerOptions.from_csv(source, checkbox_fields=["agree"])
        assert not stream.closed
        raw = _template()
        options.add_input(ByteArrayDataSource(raw))
        results = FormFiller().process(options)
        assert [_values(result) for result in results] == [
            {"person.name": "Alice, A.", "code": "00123", "agree": True},
            {"person.name": "Bob", "code": "false", "agree": False},
        ]
        with Document(raw) as original:
            assert original.form["person.name"].value == "Base"
        assert path.read_bytes() == data and stream.getvalue() == b"prefix" + data


def test_csv_mapping_selects_columns_and_uses_target_names_for_checkboxes():
    options = FormFillerOptions.from_csv(
        b"Name,Consent,Ignored\nAlice,X,not a PDF field\nBob,,unused\n",
        field_map={"Name": "person.name", "Consent": "agree"},
        checkbox_fields=["agree"],
        checkbox_values={"X": True, "": False},
    )
    options.add_input(ByteArrayDataSource(_template()))
    results = FormFiller().process(options)
    assert [_values(result) for result in results] == [
        {"person.name": "Alice", "code": "Original", "agree": True},
        {"person.name": "Bob", "code": "Original", "agree": False},
    ]


@pytest.mark.parametrize(
    "encoding,delimiter",
    [
        ("utf-8-sig", ","),
        ("utf-16", ";"),
        ("cp1251", "\t"),
    ],
)
def test_csv_encoding_delimiter_quotes_multiline_text_and_empty_cells(
    encoding, delimiter
):
    text = f'\r\nperson.name{delimiter}code\r\n"Привет ""мир""\r\nName"{delimiter}\r\n'
    options = FormFillerOptions.from_csv(
        text.encode(encoding), encoding=encoding, delimiter=delimiter
    )
    assert dict(options.records[0]) == {
        "person.name": 'Привет "мир"\r\nName',
        "code": "",
    }
    options.add_input(ByteArrayDataSource(_template()))
    assert _values(FormFiller().process(options)[0])["code"] == ""


@pytest.mark.parametrize(
    "token,expected",
    [
        ("true", True),
        ("1", True),
        ("YES", True),
        (" y ", True),
        ("On", True),
        ("false", False),
        ("0", False),
        ("NO", False),
        ("n", False),
        ("off", False),
        ("", False),
    ],
)
def test_explicit_checkbox_fields_convert_only_recognized_values(token, expected):
    data = f"person.name,agree\nfalse,{token}\n".encode()
    options = FormFillerOptions.from_csv(data, checkbox_fields=["agree"])
    assert dict(options.records[0]) == {"person.name": "false", "agree": expected}


def test_checkbox_conversion_is_never_inferred_from_csv_text():
    options = FormFillerOptions.from_csv(b"agree\ntrue\n")
    assert options.records[0]["agree"] == "true"
    options.add_input(ByteArrayDataSource(_template()))
    output = ByteArrayDataSource(b"unchanged")
    options.add_output(output)
    with pytest.raises(TypeError, match="requires a boolean"):
        FormFiller().process(options)
    assert output.read_bytes() == b"unchanged"


def test_csv_records_are_immutable_and_reusable_after_source_changes():
    data = bytearray(b"person.name\nAlice\n")
    field_map = {"person.name": "person.name"}
    options = FormFillerOptions.from_csv(data, field_map=field_map)
    data[:] = b"invalid CSV"
    field_map["person.name"] = "changed"
    with pytest.raises(TypeError):
        options.records[0]["person.name"] = "Changed"
    options.add_input(ByteArrayDataSource(_template()))
    for _ in range(2):
        assert _values(FormFiller().process(options)[0])["person.name"] == "Alice"


def test_csv_preserves_batch_order_and_writes_file_outputs(tmp_path):
    options = FormFillerOptions.from_csv(b"code\n001\n002\n")
    for title in ("First", "Second"):
        options.add_input(ByteArrayDataSource(_template(title)))
    paths = [tmp_path / f"filled-{index}.pdf" for index in range(4)]
    for path in paths:
        options.add_output(FileDataSource(path))
    results = FormFiller().process(options)
    assert [
        (_values(result)["person.name"], _values(result)["code"]) for result in results
    ] == [
        ("First", "001"),
        ("First", "002"),
        ("Second", "001"),
        ("Second", "002"),
    ]
    assert [path.read_bytes() for path in paths] == [
        result.to_array() for result in results
    ]


def test_csv_reuses_password_opening_and_field_only_flattening():
    with Document(_template()) as template:
        template.encrypt("user", "owner")
        with io.BytesIO() as output:
            template.save(output)
            encrypted = output.getvalue()
    options = FormFillerOptions.from_csv(
        b"person.name\nAlice\n",
        password="user",
        flatten=True,
    )
    options.add_input(ByteArrayDataSource(encrypted))
    [result] = FormFiller().process(options)
    with Document(result.to_array(), password="user") as document:
        assert not document.form.fields
        assert "Alice" in document.extract_text()


@pytest.mark.parametrize("strict", [True, False])
def test_csv_reuses_strict_unknown_field_handling_without_partial_outputs(strict):
    options = FormFillerOptions.from_csv(b"unknown,code\nIgnored,001\n", strict=strict)
    options.add_input(ByteArrayDataSource(_template()))
    output = ByteArrayDataSource(b"unchanged")
    options.add_output(output)
    if strict:
        with pytest.raises(KeyError, match="unknown"):
            FormFiller().process(options)
        assert output.read_bytes() == b"unchanged"
    else:
        [result] = FormFiller().process(options)
        assert _values(result)["code"] == "001"


class _UnreadTemplate(DataSource):
    def read_bytes(self, **kwargs):
        raise AssertionError("Template must not be read")


def test_header_only_csv_skips_blank_records_and_does_not_read_templates():
    options = FormFillerOptions.from_csv(b"\n\r\nname,code\n\n")
    assert options.records == ()
    options.add_input(_UnreadTemplate())
    assert len(FormFiller().process(options)) == 0


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\n\n",
        b'""\n',
        b"name,name\nA,B\n",
        b"name,\nA,B\n",
        b"name,code\nAlice\n",
        b"name\nAlice,extra\n",
        b'name\n"unterminated',
        b'name\n"closed"junk\n',
        b"name\n\xff\n",
    ],
)
def test_malformed_csv_is_rejected_before_any_pdf_work(data):
    with pytest.raises(PdfValidationException):
        FormFillerOptions.from_csv(data)


@pytest.mark.parametrize(
    "options,error",
    [
        ({"encoding": 3}, TypeError),
        ({"encoding": "no-such-encoding"}, PdfValidationException),
        ({"delimiter": 1}, TypeError),
        ({"delimiter": "::"}, PdfValidationException),
        ({"delimiter": '"'}, PdfValidationException),
        ({"delimiter": "\n"}, PdfValidationException),
        ({"field_map": []}, TypeError),
        ({"field_map": {}}, PdfValidationException),
        ({"field_map": {"missing": "name"}}, PdfValidationException),
        ({"field_map": {"name": "code", "code": "code"}}, PdfValidationException),
        ({"field_map": {"name": 1}}, TypeError),
        ({"checkbox_fields": "agree"}, TypeError),
        ({"checkbox_fields": [1]}, TypeError),
        ({"checkbox_fields": ["missing"]}, PdfValidationException),
        ({"checkbox_values": {"yes": True}}, PdfValidationException),
        ({"checkbox_fields": ["agree"], "checkbox_values": {}}, PdfValidationException),
        ({"checkbox_fields": ["agree"], "checkbox_values": {"yes": 1}}, TypeError),
        (
            {
                "checkbox_fields": ["agree"],
                "checkbox_values": {"YES": True, "yes": False},
            },
            PdfValidationException,
        ),
        ({"checkbox_fields": ["agree"]}, PdfValidationException),
    ],
)
def test_invalid_csv_options_and_unknown_checkbox_values(options, error):
    with pytest.raises(error):
        FormFillerOptions.from_csv(b"name,code,agree\nAlice,001,maybe\n", **options)


def test_invalid_csv_options_do_not_read_the_csv_source():
    class UnreadSource:
        def read(self, size=-1):
            raise AssertionError("CSV source must not be read")

    for options in (
        {"delimiter": ""},
        {"strict": 1},
        {"encoding": "missing-codec"},
        {"encoding": "base64_codec"},
        {"encoding": "rot_13"},
    ):
        with pytest.raises((TypeError, PdfValidationException)):
            FormFillerOptions.from_csv(UnreadSource(), **options)


@pytest.mark.parametrize(
    "data,limit",
    [
        (b"name\nAlice\n", "max_input_bytes"),
        (b"name,code\nAlice,001\nBob,002\n", "max_container_items"),
        (b"name\nLong field value\n", "max_object_bytes"),
        (b"name\nAlice\n", "max_total_decoded_bytes"),
    ],
)
def test_csv_input_records_cells_and_text_obey_resource_limits(data, limit):
    limits = replace(PdfLoadLimits.unlimited(), **{limit: 8})
    with pytest.raises(PdfResourceLimitException, match=limit):
        FormFillerOptions.from_csv(data, limits=limits)


def test_csv_wide_rows_are_bounded_before_the_csv_reader_allocates_them(monkeypatch):
    from aspose_pdf.engine import form_csv

    def fail_reader(*args, **kwargs):
        raise AssertionError("CSV parser must not run before container limits")

    monkeypatch.setattr(form_csv.csv, "reader", fail_reader)
    limits = replace(PdfLoadLimits.unlimited(), max_container_items=8)
    with pytest.raises(PdfResourceLimitException, match="max_container_items"):
        FormFillerOptions.from_csv(b"," * 100 + b"\n", limits=limits)


def test_csv_structure_limits_ignore_delimiters_and_newlines_in_quoted_cells():
    limits = replace(PdfLoadLimits.unlimited(), max_container_items=8)
    options = FormFillerOptions.from_csv(
        b'name\n"a,b,c,d,e,f\nnext line"\n', limits=limits
    )
    assert options.records[0]["name"] == "a,b,c,d,e,f\nnext line"


@pytest.mark.parametrize("delimiter", [",", ";", "\t"])
@pytest.mark.parametrize(
    "value", ["", 'a"b', '"', "a,b", "a\rb", "a\nb", ' "x"', "=SUM(A1,A2)"]
)
def test_csv_structure_limits_match_standard_quoting_at_exact_container_limit(
    delimiter, value
):
    with io.StringIO(newline="") as stream:
        writer = csv.writer(stream, delimiter=delimiter)
        writer.writerow(["a", "b", "c"])
        writer.writerow([value, value, value])
        data = stream.getvalue().encode()
    limits = replace(PdfLoadLimits.unlimited(), max_container_items=8)
    options = FormFillerOptions.from_csv(data, delimiter=delimiter, limits=limits)
    assert [dict(record) for record in options.records] == [
        {"a": value, "b": value, "c": value}
    ]


def test_unending_checkbox_field_options_are_bounded_before_csv_reads():
    limits = replace(PdfLoadLimits.unlimited(), max_container_items=8)
    with pytest.raises(PdfResourceLimitException, match="max_container_items"):
        FormFillerOptions.from_csv(
            b"agree\ntrue\n", checkbox_fields=repeat("agree"), limits=limits
        )


def test_csv_field_size_limit_is_not_changed_and_errors_are_clear():
    before = csv.field_size_limit()
    FormFillerOptions.from_csv(b"name\nAlice\n")
    with pytest.raises(PdfValidationException, match="field larger than field limit"):
        FormFillerOptions.from_csv(b"name\n" + b"a" * (before + 1) + b"\n")
    assert csv.field_size_limit() == before


def test_csv_rejects_text_streams_and_keeps_binary_stream_open_on_failure():
    with io.StringIO("name\nAlice\n") as text_stream:
        with pytest.raises(TypeError, match="bytes"):
            FormFillerOptions.from_csv(text_stream)
        assert not text_stream.closed
    with io.BytesIO(b"name,code\nAlice\n") as binary_stream:
        with pytest.raises(PdfValidationException):
            FormFillerOptions.from_csv(binary_stream)
        assert not binary_stream.closed


def test_csv_bytearrays_are_size_checked_before_copying():
    class NoCopyBytearray(bytearray):
        def __bytes__(self):
            raise AssertionError("Size must be checked before copying")

    limits = replace(PdfLoadLimits.unlimited(), max_input_bytes=1)
    with pytest.raises(PdfResourceLimitException, match="max_input_bytes"):
        FormFillerOptions.from_csv(NoCopyBytearray(b"name\nAlice\n"), limits=limits)
