"""Form answers export as one CSV record without editing the PDF."""

from __future__ import annotations

import csv
import io
import json

import pytest

from aspose_pdf import (
    ByteArrayDataSource,
    Document,
    FormFiller,
    FormFillerOptions,
    PdfLoadLimits,
    PdfResourceLimitException,
)
from aspose_pdf.exceptions import AsposePdfException, PdfValidationException
from aspose_pdf.forms import Form


@pytest.fixture
def document():
    with Document() as document:
        page = document.pages.add()
        form = document.form
        form.add_text_field("person.name", page, (20, 700, 220, 724), value="Before")
        form.add_checkbox("agree", page, (20, 650, 40, 670), on_value="Agree")
        form.add_radio_group(
            "size", page, [("S", (20, 600, 40, 620)), ("M", (60, 600, 80, 620))]
        )
        form.add_list_box(
            "colors",
            page,
            (20, 470, 160, 560),
            ["r", "Blue", 'a,b"c'],
            multiselect=True,
        )
        form.add_combo_box("country", page, (20, 420, 160, 444), ["NL", "DE"])
        form.add_text_field("code", page, (20, 350, 100, 374), value="00123")
        form.add_push_button("go", page, (20, 300, 100, 324), caption="Go")
        form.add_signature_field("sig", page, (20, 250, 160, 274))
        yield document


def _rows(data, *, encoding="utf-8", delimiter=","):
    return list(
        csv.reader(io.StringIO(data.decode(encoding), newline=""), delimiter=delimiter)
    )


def _bytes(document):
    with io.BytesIO() as output:
        document.save(output)
        return output.getvalue()


def test_exports_current_answers_with_headers_types_and_no_pdf_changes(document):
    document.form.fill({"agree": True, "size": "M", "colors": ["r", 'a,b"c']})
    document.form["code"].read_only = True
    document.form["code"].no_export = True
    document.form["code"].mapping_name = "OtherName"
    before = _bytes(document)
    data = document.form.export_csv()
    header, row = _rows(data)
    assert header == ["person.name", "agree", "size", "colors", "country", "code"]
    assert row == ["Before", "true", "M", '["r","a,b\\"c"]', "", "00123"]
    assert json.loads(row[3]) == ["r", 'a,b"c']
    assert data.endswith(b"\r\n")
    assert document.form.export_csv() == data
    assert _bytes(document) == before


@pytest.mark.parametrize("selected", [[], ["Blue"], ["r", "Blue"]])
def test_multiselect_lists_keep_empty_single_and_multiple_values(document, selected):
    document.form.fill({"colors": selected})
    header, row = _rows(document.form.export_csv(fields=["colors"]))
    assert header == ["colors"]
    assert json.loads(row[0]) == selected


@pytest.mark.parametrize("value", [False, True])
def test_checkboxes_use_explicit_lowercase_booleans(document, value):
    document.form["agree"].value = value
    assert _rows(document.form.export_csv(fields=["agree"])) == [
        ["agree"],
        [str(value).lower()],
    ]


@pytest.mark.parametrize("delimiter", [",", ";", "\t", "|"])
def test_quoting_unicode_and_literal_text_survive_csv_parsing(document, delimiter):
    value = 'Привет, a;b\tc|d "quoted"\nnext\rthird\r\nfourth'
    document.form.fill({"person.name": value, "code": "=SUM(A1:A2)"})
    rows = _rows(document.form.export_csv(delimiter=delimiter), delimiter=delimiter)
    assert rows[1][0] == value
    assert rows[1][-1] == "=SUM(A1:A2)"


def test_selected_columns_accept_generators_and_preserve_requested_order(document):
    data = document.form.export_csv(
        fields=(name for name in ("code", "agree", "person.name"))
    )
    assert _rows(data) == [
        ["code", "agree", "person.name"],
        ["00123", "false", "Before"],
    ]
    assert document.form.export_csv(fields=[]) == b""


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16", "cp1251"])
def test_text_encodings_and_bom_are_written_once(document, encoding):
    document.form.fill({"person.name": "Привет"})
    data = document.form.export_csv(fields=["person.name"], encoding=encoding)
    assert _rows(data, encoding=encoding) == [["person.name"], ["Привет"]]
    if encoding == "utf-8-sig":
        assert data.startswith(b"\xef\xbb\xbf") and data.count(b"\xef\xbb\xbf") == 1
    elif encoding == "utf-16":
        assert data.startswith((b"\xff\xfe", b"\xfe\xff"))
        assert data.count(data[:2]) == 1


def test_paths_return_the_same_bytes_and_honor_overwrite_protection(document, tmp_path):
    path = tmp_path / "nested" / "answers.csv"
    data = document.form.export_csv(path, encoding="utf-8-sig", delimiter=";")
    assert path.read_bytes() == data
    document.form["code"].value = "New value"
    with pytest.raises(FileExistsError):
        document.form.export_csv(path, overwrite=False)
    assert path.read_bytes() == data
    replacement = document.form.export_csv(path)
    assert path.read_bytes() == replacement != data


def test_binary_streams_use_current_position_retry_short_writes_and_stay_open(document):
    class ShortWriter(io.BytesIO):
        def write(self, data):
            return super().write(data[:3])

    with ShortWriter(b"prefix") as stream:
        stream.seek(6)
        data = document.form.export_csv(stream)
        assert stream.getvalue() == b"prefix" + data
        assert not stream.closed


def test_stalled_and_text_streams_are_rejected(document):
    class StalledWriter(io.BytesIO):
        def write(self, data):
            return 0

    with pytest.raises(AsposePdfException, match="did not consume"):
        document.form.export_csv(StalledWriter())
    with pytest.raises(TypeError):
        document.form.export_csv(io.StringIO())


@pytest.mark.parametrize(
    "options,error",
    [
        ({"delimiter": ""}, PdfValidationException),
        ({"delimiter": "::"}, PdfValidationException),
        ({"delimiter": '"'}, PdfValidationException),
        ({"delimiter": "\r"}, PdfValidationException),
        ({"delimiter": "\n"}, PdfValidationException),
        ({"delimiter": "\x00"}, PdfValidationException),
        ({"delimiter": "\ud800"}, PdfValidationException),
        ({"delimiter": 1}, TypeError),
        ({"encoding": "missing-encoding"}, PdfValidationException),
        ({"encoding": "base64_codec"}, PdfValidationException),
        ({"encoding": "rot_13"}, PdfValidationException),
        ({"encoding": None}, TypeError),
        ({"overwrite": 1}, PdfValidationException),
        ({"fields": "code"}, TypeError),
        ({"fields": {"code": "Label"}}, TypeError),
        ({"fields": [1]}, TypeError),
        ({"fields": ["missing"]}, KeyError),
        ({"fields": ["go"]}, KeyError),
        ({"fields": ["sig"]}, KeyError),
        ({"fields": ["code", "code"]}, PdfValidationException),
    ],
)
def test_invalid_options_leave_existing_outputs_untouched(
    document, tmp_path, options, error
):
    path = tmp_path / "answers.csv"
    path.write_bytes(b"original")
    with pytest.raises(error):
        document.form.export_csv(path, **options)
    assert path.read_bytes() == b"original"
    with io.BytesIO(b"original") as stream:
        with pytest.raises(error):
            document.form.export_csv(stream, **options)
        assert stream.getvalue() == b"original"


def test_encoding_failure_writes_nothing_and_creates_no_directories(document, tmp_path):
    document.form.fill({"person.name": "Привет"})
    path = tmp_path / "new" / "answers.csv"
    with pytest.raises(PdfValidationException, match="encoded as CSV"):
        document.form.export_csv(path, encoding="ascii")
    assert not path.parent.exists()


def test_empty_forms_and_button_only_forms_export_empty_bytes():
    with Document() as document:
        assert document.form.export_csv() == b""
        assert document.form.export_csv(encoding="utf-8-sig") == b""
        page = document.pages.add()
        document.form.add_push_button("go", page, (20, 300, 100, 324), caption="Go")
        document.form.add_signature_field("sig", page, (20, 250, 160, 274))
        assert document.form.export_csv() == b""
        with pytest.raises(PdfValidationException, match="delimiter"):
            document.form.export_csv(delimiter="invalid")


@pytest.mark.parametrize("encrypted", [False, True])
def test_streaming_export_reads_metadata_only_and_keeps_source_unchanged(
    document, tmp_path, monkeypatch, encrypted
):
    if encrypted:
        document.encrypt("secret", "owner")
    path = tmp_path / "form.pdf"
    document.save(path)
    raw = path.read_bytes()
    expected = document.form.export_csv()
    with Document.open_streaming(
        path, password="secret" if encrypted else None
    ) as loaded:

        def forbidden(*args, **kwargs):
            pytest.fail("CSV export must not decode page content")

        monkeypatch.setattr(loaded._engine_pdf, "get_page_content", forbidden)
        assert loaded.form.export_csv() == expected
        assert loaded._engine_pdf.page_contents == []
    assert path.read_bytes() == raw


def test_scalar_fields_can_feed_the_existing_csv_batch_import(document):
    document.form.fill(
        {"person.name": "Alice", "agree": True, "size": "M", "country": "DE"}
    )
    data = document.form.export_csv(
        fields=["person.name", "agree", "size", "country", "code"]
    )
    options = FormFillerOptions.from_csv(data, checkbox_fields=["agree"])
    options.add_input(ByteArrayDataSource(_bytes(document)))
    with Document(FormFiller().process(options)[0].to_array()) as filled:
        values = filled.form.to_dict()
        assert [
            values[name] for name in ["person.name", "agree", "size", "country", "code"]
        ] == ["Alice", True, "M", "DE", "00123"]


@pytest.mark.parametrize(
    "values,limits,options,context",
    [
        (
            {"a": "x", "b": "y"},
            PdfLoadLimits(max_container_items=1),
            {},
            "max_container_items",
        ),
        (
            {"a": "Long value"},
            PdfLoadLimits(max_object_bytes=5),
            {},
            "max_object_bytes",
        ),
        (
            {"a": ["a", "b"]},
            PdfLoadLimits(max_container_items=1),
            {},
            "max_container_items",
        ),
        (
            {"a": "x" * 10},
            PdfLoadLimits(max_decoded_stream_bytes=5),
            {},
            "max_decoded_stream_bytes",
        ),
        (
            {"a": "xx", "b": "yy"},
            PdfLoadLimits(max_total_decoded_bytes=5),
            {},
            "max_total_decoded_bytes",
        ),
        (
            {"a": ""},
            PdfLoadLimits(max_decoded_stream_bytes=6),
            {"encoding": "utf-16"},
            "max_decoded_stream_bytes",
        ),
    ],
)
def test_export_limits_fail_before_writing(
    monkeypatch, values, limits, options, context
):
    monkeypatch.setattr(Form, "to_dict", lambda self: values)
    with Document(limits=limits) as document, io.BytesIO(b"original") as stream:
        with pytest.raises(PdfResourceLimitException, match=context):
            document.form.export_csv(stream, **options)
        assert stream.getvalue() == b"original"


@pytest.mark.parametrize("value", ["\ud800", ["\ud800"], {"nested": True}, 42])
def test_invalid_field_values_cannot_write_a_partial_csv(monkeypatch, value):
    monkeypatch.setattr(Form, "to_dict", lambda self: {"answer": value})
    with Document() as document, io.BytesIO(b"original") as stream:
        with pytest.raises(PdfValidationException):
            document.form.export_csv(stream)
        assert stream.getvalue() == b"original"


def test_disposed_forms_reject_csv_export(document):
    form = document.form
    document.dispose()
    with pytest.raises(AsposePdfException, match="disposed"):
        form.export_csv()


def test_field_names_use_standard_csv_header_escaping():
    with Document() as document:
        page = document.pages.add()
        name = 'customer,"nickname"'
        document.form.add_text_field(name, page, (20, 700, 220, 724), value="Alice")
        assert _rows(document.form.export_csv()) == [[name], ["Alice"]]


def test_large_unicode_values_keep_encoding_state_across_chunks(monkeypatch):
    value = "Привет🙂\n" * 10_000
    monkeypatch.setattr(Form, "to_dict", lambda self: {"answer": value})
    with Document() as document:
        data = document.form.export_csv(encoding="utf-16")
        assert _rows(data, encoding="utf-16") == [["answer"], [value]]
        assert data.count(data[:2]) == 1


def test_multiselect_json_growth_is_bounded_before_output(monkeypatch):
    monkeypatch.setattr(Form, "to_dict", lambda self: {"a": ["a", "b", "c"]})
    limits = PdfLoadLimits(max_object_bytes=10)
    with Document(limits=limits) as document, io.BytesIO(b"original") as stream:
        with pytest.raises(PdfResourceLimitException, match="CSV multiselect cell"):
            document.form.export_csv(stream)
        assert stream.getvalue() == b"original"
