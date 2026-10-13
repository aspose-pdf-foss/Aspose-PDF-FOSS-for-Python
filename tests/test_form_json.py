"""Dictionary and JSON form data round trips through the public API."""

from __future__ import annotations

import io
import json
from collections import UserDict

import pytest

from aspose_pdf import Document, PdfLoadLimits
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfResourceLimitException,
    PdfValidationException,
)


@pytest.fixture
def document():
    with Document() as doc:
        page = doc.pages.add()
        form = doc.form
        form.add_text_field("person.name", page, (20, 700, 220, 724), value="Before")
        form.add_checkbox("agree", page, (20, 650, 40, 670), on_value="Agree")
        form.add_radio_group(
            "size", page, [("S", (20, 600, 40, 620)), ("M", (60, 600, 80, 620))]
        )
        form.add_list_box(
            "colours",
            page,
            (20, 470, 160, 560),
            [("r", "Red"), ("g", "Green"), "Blue"],
            multiselect=True,
        )
        form.add_list_box("single", page, (180, 470, 300, 560), ["a", "b"])
        form.add_combo_box("country", page, (20, 420, 160, 444), ["NL", "DE"])
        form.add_combo_box(
            "free", page, (180, 420, 300, 444), ["NL", "DE"], editable=True
        )
        form.add_push_button("go", page, (20, 350, 100, 374), caption="Go")
        form.add_signature_field("sig", page, (20, 300, 160, 324))
        yield doc


VALUES = {
    "person.name": "Alice Иванова",
    "agree": True,
    "size": "M",
    "colours": ["r", "Blue"],
    "single": "b",
    "country": "DE",
    "free": "custom",
}


@pytest.mark.parametrize("on_disk", [False, True])
def test_fill_and_json_survive_pdf_round_trips(document, tmp_path, on_disk):
    retained = document.form["person.name"]
    assert document.form.fill(UserDict(VALUES)) == list(VALUES)
    assert retained.value == VALUES["person.name"]
    assert document.form.to_dict() == VALUES

    if on_disk:
        destination = tmp_path / "form.pdf"
        document.save(destination)
        source = destination
    else:
        destination = io.BytesIO()
        document.save(destination)
        source = destination.getvalue()
    with Document(source) as reopened:
        assert reopened.form.to_dict() == VALUES
        assert json.loads(reopened.form.export_json()) == VALUES


@pytest.mark.parametrize("selected", [[], ["r"], ["r", "Blue"]])
def test_multiselect_keeps_a_list_and_copies_values(document, selected):
    document.form.fill({"colours": selected})
    snapshot = document.form.to_dict()
    assert snapshot["colours"] == selected
    selected.append("g")
    snapshot["colours"].append("g")
    assert document.form.to_dict()["colours"] == selected[:-1]

    buffer = io.BytesIO()
    document.save(buffer)
    with Document(buffer.getvalue()) as reopened:
        assert reopened.form.to_dict()["colours"] == selected[:-1]
        data = reopened.form.export_json()
        assert reopened.form.import_json(data) == list(reopened.form.to_dict())
        assert reopened.form.to_dict()["colours"] == selected[:-1]


def test_snapshot_refreshes_values_and_includes_read_only_and_no_export(document):
    field = document.form["person.name"]
    field.read_only = field.no_export = True
    field.value = "Current"
    snapshot = document.form.to_dict()
    assert snapshot["person.name"] == "Current"
    assert "go" not in snapshot and "sig" not in snapshot
    document.form.fill({"person.name": "Updated"})
    assert snapshot["person.name"] == "Current"
    assert field.value == "Updated"
    assert field.read_only and field.no_export


def test_null_clears_fields_and_non_value_fields_are_skipped(document):
    document.form.fill(VALUES)
    assert document.form.fill(
        {**dict.fromkeys(VALUES), "go": "ignored", "sig": "ignored"}
    ) == list(VALUES)
    assert document.form.to_dict() == {
        "person.name": "",
        "agree": False,
        "size": None,
        "colours": [],
        "single": None,
        "country": None,
        "free": None,
    }
    assert document.form["sig"].value is None
    assert document.form["go"].value is None


def test_fill_updates_visible_checkbox_and_text_appearances(document):
    before = document.pages[0].render(dpi=36, antialias=False).pixels
    document.form.fill({"person.name": "After", "agree": True, "size": "M"})
    after = document.pages[0].render(dpi=36, antialias=False).pixels
    assert before != after
    buffer = io.BytesIO()
    document.save(buffer)
    with Document(buffer.getvalue()) as reopened:
        assert reopened.pages[0].render(dpi=36, antialias=False).pixels == after


def test_unknown_names_are_strict_by_default_and_can_be_skipped(document):
    before = document.form.to_dict()
    with pytest.raises(KeyError, match="missing"):
        document.form.fill({"person.name": "After", "missing": "ignored"})
    assert document.form.to_dict() == before
    assert document.form.fill({"missing": "ignored", "agree": True}, strict=False) == [
        "agree"
    ]
    assert document.form["agree"].value is True
    assert document.form.import_json(b'{"missing": "ignored"}', strict=False) == []


@pytest.mark.parametrize(
    "values, error",
    [
        ({1: "bad"}, TypeError),
        ({"person.name": 42}, TypeError),
        ({"person.name": True}, TypeError),
        ({"person.name": ["bad"]}, TypeError),
        ({"agree": "Agree"}, TypeError),
        ({"size": False}, TypeError),
        ({"size": "L"}, PdfValidationException),
        ({"country": "FR"}, PdfValidationException),
        ({"single": ["a"]}, TypeError),
        ({"colours": "r"}, TypeError),
        ({"colours": ["r", 1]}, TypeError),
        ({"colours": ["r", "r"]}, PdfValidationException),
        ({"colours": ["unknown"]}, PdfValidationException),
        ({"free": {"nested": "value"}}, TypeError),
    ],
)
def test_invalid_batch_is_rejected_before_any_field_changes(document, values, error):
    before = document.form.to_dict()
    with pytest.raises(error):
        document.form.fill({"person.name": "After", **values})
    assert document.form.to_dict() == before


@pytest.mark.parametrize("values", [None, [], "name", [("person.name", "Alice")]])
def test_fill_requires_a_mapping(document, values):
    with pytest.raises(TypeError, match="mapping"):
        document.form.fill(values)


def test_fill_accepts_tuples_for_multiselect_but_requires_boolean_strict(document):
    document.form.fill({"colours": ("r", "Blue")})
    assert document.form.to_dict()["colours"] == ["r", "Blue"]
    with pytest.raises(TypeError, match="strict"):
        document.form.fill({}, strict=1)


@pytest.mark.parametrize("kind", ["bytes", "bytearray", "stream", "path"])
def test_json_io_round_trip_utf8(document, tmp_path, kind):
    document.form.fill(VALUES)
    data = document.form.export_json()
    assert "Иванова".encode() in data
    assert json.loads(data) == VALUES
    if kind == "path":
        source = tmp_path / "form.json"
        source.write_bytes(b"old contents")
        assert document.form.export_json(source) == data
        assert source.read_bytes() == data
    elif kind == "stream":
        source = io.BytesIO()
        assert document.form.export_json(source) == data
        source.seek(0)
    elif kind == "bytearray":
        source = bytearray(data)
    else:
        source = data
    document.form.fill(dict.fromkeys(VALUES))
    assert document.form.import_json(source) == list(VALUES)
    assert document.form.to_dict() == VALUES


def test_export_json_handles_short_writes_and_rejects_stalled_streams(document):
    class ShortWriter(io.BytesIO):
        def write(self, data):
            return super().write(data[:3])

    class StalledWriter(io.BytesIO):
        def write(self, data):
            return 0

    stream = ShortWriter()
    expected = document.form.export_json()
    assert document.form.export_json(stream) == expected
    assert stream.getvalue() == expected
    with pytest.raises(AsposePdfException, match="did not consume"):
        document.form.export_json(StalledWriter())


@pytest.mark.parametrize(
    "data, message",
    [
        (b"{", "Invalid"),
        (b"{} trailing", "Invalid"),
        (b'{"person.name":"\xff"}', "UTF-8"),
        (b"[]", "object"),
        (b"null", "object"),
        (b'{"agree":true,"agree":false}', "Duplicate"),
        (b'{"agree":true,"\\u0061gree":false}', "Duplicate"),
        (b'{"person.name":NaN}', "constant"),
        (b'{"person.name":Infinity}', "constant"),
        (b'{"person.name":-Infinity}', "constant"),
    ],
)
def test_malformed_json_does_not_mutate_fields(document, data, message):
    before = document.form.to_dict()
    with pytest.raises(PdfValidationException, match=message):
        document.form.import_json(data)
    assert document.form.to_dict() == before


def test_json_schema_validation_and_utf8_bom(document):
    before = document.form.to_dict()
    with pytest.raises(TypeError):
        document.form.import_json(b'{"person.name":"After","free":{"nested":true}}')
    assert document.form.to_dict() == before
    document.form.import_json(b'\xef\xbb\xbf{"agree":true}')
    assert document.form["agree"].value is True


def test_invalid_unicode_is_rejected_before_any_field_changes(document):
    before = document.form.to_dict()
    with pytest.raises(PdfValidationException, match="Unicode"):
        document.form.import_json(b'{"agree":true,"person.name":"\\ud800"}')
    assert document.form.to_dict() == before


@pytest.mark.parametrize("kind", ["bytes", "stream", "path"])
def test_json_input_byte_limit(tmp_path, kind):
    data = b'{"name":"long value"}'
    if kind == "path":
        source = tmp_path / "large.json"
        source.write_bytes(data)
    elif kind == "stream":
        source = io.BytesIO(data)
    else:
        source = data
    with Document(limits=PdfLoadLimits(max_input_bytes=8)) as doc:
        with pytest.raises(PdfResourceLimitException, match="max_input_bytes"):
            doc.form.import_json(source)


@pytest.mark.parametrize(
    "limits, data, message",
    [
        (PdfLoadLimits(max_nesting_depth=2), b'{"a":[["b"]]}', "max_nesting_depth"),
        (
            PdfLoadLimits(max_container_items=2),
            b'{"a":null,"b":null,"c":null}',
            "max_container_items",
        ),
        (
            PdfLoadLimits(max_container_items=2),
            b'{"a":["b","c","d"]}',
            "max_container_items",
        ),
    ],
)
def test_json_container_limits(limits, data, message):
    with Document(limits=limits) as doc:
        with pytest.raises(PdfResourceLimitException, match=message):
            doc.form.import_json(data)


def test_json_structural_limits_ignore_escaped_strings():
    limits = PdfLoadLimits(max_nesting_depth=2, max_container_items=2)
    with Document(limits=limits) as doc:
        data = json.dumps({"commas,,,braces{{{": '[,]"\\{{{{'}).encode()
        assert doc.form.import_json(data, strict=False) == []
        assert doc.form.export_json() == b"{}\n"


def test_dictionary_values_obey_container_limits():
    with Document(limits=PdfLoadLimits(max_container_items=2)) as doc:
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            doc.form.fill({"a": None, "b": None, "c": None}, strict=False)
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            doc.form.fill({"a": ["b", "c", "d"]}, strict=False)


@pytest.mark.parametrize(
    "method, args",
    [
        ("to_dict", ()),
        ("fill", ({},)),
        ("export_json", ()),
        ("import_json", (b"{}",)),
    ],
)
def test_disposed_form_rejects_data_exchange(method, args):
    document = Document()
    form = document.form
    document.dispose()
    with pytest.raises(AsposePdfException, match="disposed"):
        getattr(form, method)(*args)


def test_json_exchange_preserves_encryption(document, tmp_path):
    document.encrypt(user_password="secret", owner_password="owner")
    source = tmp_path / "encrypted.pdf"
    document.save(source)
    with Document(source, password="secret") as reopened:
        reopened.form.import_json(json.dumps(VALUES).encode())
        assert reopened.form.to_dict() == VALUES
        target = tmp_path / "filled.pdf"
        reopened.save(target)
    with Document(target, password="secret") as saved:
        assert json.loads(saved.form.export_json()) == VALUES


@pytest.mark.parametrize("encrypted", [False, True])
def test_json_exchange_reads_and_fills_streaming_forms(document, tmp_path, encrypted):
    if encrypted:
        document.encrypt(user_password="secret", owner_password="owner")
    source = tmp_path / "source.pdf"
    document.save(source)
    with Document.open_streaming(
        source, password="secret" if encrypted else None
    ) as reopened:
        assert reopened.form.import_json(json.dumps(VALUES).encode()) == list(VALUES)
        assert json.loads(reopened.form.export_json()) == VALUES


def test_json_filled_streaming_form_survives_a_full_save(document, tmp_path):
    source = tmp_path / "source.pdf"
    document.save(source)
    with Document.open_streaming(source) as reopened:
        reopened.form.import_json(json.dumps(VALUES).encode())
        target = tmp_path / "filled.pdf"
        reopened.save(target)
    with Document(target) as saved:
        assert saved.form.to_dict() == VALUES
