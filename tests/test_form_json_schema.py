"""Standard JSON Schema describes normalized form answers without reading them."""

from __future__ import annotations

import io
import json
import sys

import pytest
from jsonschema import Draft202012Validator

from aspose_pdf import Document, PdfLoadLimits, PdfResourceLimitException
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName, PdfNull, PdfString
from aspose_pdf.engine.form_fields import field_dictionary
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfParseException,
    PdfValidationException,
)
from aspose_pdf.forms import Form


def _bytes(document):
    with io.BytesIO() as output:
        document.save(output)
        return output.getvalue()


@pytest.fixture
def document():
    with Document() as document:
        page = document.pages.add()
        form = document.form
        name = form.add_text_field("person.name", page, (10, 700, 200, 724),
                                   value="Ada", required=True)
        name.max_length = 4
        name.alternate_name = "Your name"
        form.add_text_field("notes", page, (10, 660, 200, 684))
        form.add_checkbox("agree", page, (10, 620, 30, 640), checked=True, required=True)
        form.add_checkbox("updates", page, (40, 620, 60, 640))
        form.add_radio_group("size", page, [("S", (10, 580, 30, 600)),
                                            ("M", (40, 580, 60, 600))], value="M")
        form["size"].required = True
        form.add_list_box("colours", page, (10, 460, 200, 560),
                          [("r", "Red"), ("g", "Green")], value=["r"], multiselect=True)
        form["colours"].required = True
        country = form.add_combo_box("country", page, (10, 420, 200, 444),
                                      [("BE", "Belgium"), ("NL", "Netherlands")], value="BE")
        country.read_only = country.no_export = True
        form.add_combo_box("free", page, (10, 380, 200, 404), ["BE"], editable=True)
        form.add_combo_box("editable", page, (10, 340, 200, 364),
                            ["BE", ""], value="Custom", editable=True)
        form["editable"].required = True
        form.add_push_button("go", page, (10, 300, 100, 324), caption="Go").required = True
        form.add_signature_field("signature", page, (10, 260, 100, 284)).required = True
        yield document


def test_schema_is_standard_and_validates_flat_normalized_answers(document):
    schema = document.form.to_json_schema()
    Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert list(schema["properties"]) == list(document.form.to_dict())
    assert schema["required"] == ["person.name", "agree", "size", "colours", "editable"]
    assert schema["properties"]["person.name"]["title"] == "Your name"
    assert schema["properties"]["person.name"]["maxLength"] == 4
    assert schema["properties"]["country"]["readOnly"] is True
    assert "go" not in schema["properties"] and "signature" not in schema["properties"]
    validator = Draft202012Validator(schema)
    assert validator.is_valid(document.form.to_dict())
    changed = document.form.to_dict()
    changed["country"] = "NL"
    assert validator.is_valid(changed), "readOnly is an annotation, not a value assertion"
    assert not validator.is_valid({**changed, "unknown": "Answer"})
    assert not validator.is_valid([])


@pytest.mark.parametrize("field,value", [
    ("person.name", None), ("person.name", ""), ("person.name", " \t\n"),
    ("person.name", "\u001c\u0085\u00a0\u2003"), ("person.name", "Longer"),
    ("person.name", 5), ("agree", False), ("agree", None), ("agree", "true"),
    ("size", None), ("size", ""), ("size", "Off"), ("size", "XL"),
    ("colours", []), ("colours", ["r", "r"]), ("colours", ["Blue"]),
    ("colours", "r"), ("colours", None), ("colours", [5]),
    ("country", "Belgium"), ("editable", None), ("editable", "\t\u2003"),
])
def test_invalid_answer_types_required_values_and_choices_fail_validation(
    document, field, value,
):
    values = document.form.to_dict()
    values[field] = value
    assert not Draft202012Validator(document.form.to_json_schema()).is_valid(values)


@pytest.mark.parametrize("field", ["person.name", "agree", "size", "colours", "editable"])
def test_required_fields_cannot_be_omitted(document, field):
    values = document.form.to_dict()
    del values[field]
    assert not Draft202012Validator(document.form.to_json_schema()).is_valid(values)


@pytest.mark.parametrize("field,value", [
    ("person.name", "🙂🙂🙂🙂"), ("person.name", "\ufeff"),
    ("notes", None), ("notes", ""), ("updates", False), ("colours", ["g", "r"]),
    ("country", None), ("country", "NL"), ("free", None), ("free", "Custom"),
    ("editable", ""), ("editable", "Other"),
])
def test_valid_nullable_unicode_and_editable_answers_pass(document, field, value):
    values = document.form.to_dict()
    values[field] = value
    assert Draft202012Validator(document.form.to_json_schema()).is_valid(values)


def test_generated_rules_agree_with_form_validation_on_normalized_filled_data(document):
    validator = Draft202012Validator(document.form.to_json_schema())
    for changes in ({"person.name": "  "}, {"person.name": "Five!"},
                    {"agree": False}, {"colours": []}, {"editable": ""},
                    {"person.name": "Ada", "agree": True, "colours": ["g"]}):
        document.form.fill(changes)
        assert validator.is_valid(document.form.to_dict()) == (not document.form.validate())


def test_required_choices_accept_declared_blank_exports(document):
    form = document.form
    form["country"].options = ["BE", ""]
    form["country"].required = True
    form["editable"].options = ["BE", " "]
    values = form.to_dict()
    values.update(country="", editable=" ")
    validator = Draft202012Validator(form.to_json_schema())
    assert validator.is_valid(values)
    form.fill(values)
    assert not form.validate()


def test_optional_empty_choice_lists_and_arbitrary_field_names_are_standard():
    with Document() as document:
        document.pages.add()
        form = document.form
        for name in ('person.name', 'a/b~c', '$ref', 'Имя "name"'):
            form.add_text_field(name, 0, (10, 10, 100, 30), value="Answer")
        form.add_list_box("many", 0, (10, 40, 100, 100), ["a"], multiselect=True)
        form.add_combo_box("one", 0, (10, 110, 100, 130), ["a"])
        for name in ("many", "one"):
            field_dictionary(document._engine_pdf, name).mapping[PdfName("Opt")] = PdfArray([])
        schema = form.to_json_schema()
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        assert validator.is_valid(form.to_dict())
        assert not validator.is_valid({"person": {"name": "Answer"}})
        values = form.to_dict()
        values["many"] = ["a"]
        assert not validator.is_valid(values)


def test_defaults_are_annotations_and_schema_is_detached_from_answers(document):
    form = document.form
    before = form.to_json_schema()
    default = before["properties"]["colours"]["default"]
    assert default == ["r"]
    form.fill({"person.name": "PRIVATE ANSWER", "colours": ["g"]})
    assert form.to_json_schema() == before
    assert b"PRIVATE ANSWER" not in form.export_json_schema()
    default.append("Changed")
    assert form.to_json_schema()["properties"]["colours"]["default"] == ["r"]
    document.form["person.name"].default_value = "Invalid default"
    schema = form.to_json_schema()
    Draft202012Validator.check_schema(schema)
    assert schema["properties"]["person.name"]["default"] == "Invalid default"


def test_inheritance_indirect_metadata_and_live_edits_are_reflected(document):
    pdf = document._engine_pdf
    field = field_dictionary(pdf, "person.name")
    parent = pdf._resolve(field.mapping[PdfName("Parent")])
    for key in ("FT", "Ff", "DV", "MaxLen"):
        parent.mapping[PdfName(key)] = field.mapping.pop(PdfName(key))
    field.mapping[PdfName("DV")] = PdfNull()
    schema = document.form.to_json_schema()
    assert schema["properties"]["person.name"]["maxLength"] == 4
    assert schema["properties"]["person.name"]["default"] is None
    assert "person.name" in schema["required"]
    document.form["person.name"].required = False
    document.form.remove_field("notes")
    schema = document.form.to_json_schema()
    assert "person.name" not in schema["required"] and "notes" not in schema["properties"]


def test_duplicate_and_empty_choices_produce_valid_schema_without_losing_constraints(document):
    pdf = document._engine_pdf
    country = field_dictionary(pdf, "country")
    country.mapping[PdfName("Opt")] = PdfArray([
        PdfArray([PdfString("BE"), PdfString("Belgium")]),
        PdfArray([PdfString("BE"), PdfString("Belgie")]),
        PdfString(""),
    ])
    schema = document.form.to_json_schema()
    Draft202012Validator.check_schema(schema)
    assert schema["properties"]["country"]["enum"] == ["BE", "", None]
    country.mapping[PdfName("Opt")] = PdfArray([])
    field_dictionary(pdf, "colours").mapping[PdfName("Opt")] = PdfArray([])
    document.form["country"].required = True
    schema = document.form.to_json_schema()
    Draft202012Validator.check_schema(schema)
    assert not Draft202012Validator(schema).is_valid(document.form.to_dict())
    assert schema["properties"]["country"]["not"] == {}
    assert schema["properties"]["colours"]["items"] is False


def test_optional_multiselects_and_radios_allow_empty_answers_and_missing_states(document):
    form = document.form
    form["size"].required = form["colours"].required = False
    schema = form.to_json_schema()
    assert schema["properties"]["size"]["enum"] == ["S", "M", "", None]
    validator = Draft202012Validator(schema)
    values = form.to_dict()
    values.update(size=None, colours=[])
    assert validator.is_valid(values)
    radio = field_dictionary(document._engine_pdf, "size")
    for widget in document._engine_pdf._resolve(radio.mapping[PdfName("Kids")]).items:
        document._engine_pdf._resolve(widget).mapping.pop(PdfName("AP"), None)
    form["size"].required = True
    schema = form.to_json_schema()
    Draft202012Validator.check_schema(schema)
    assert "enum" not in schema["properties"]["size"]
    values["size"] = "Unspecified state"
    assert Draft202012Validator(schema).is_valid(values)
    values["size"] = "Off"
    assert not Draft202012Validator(schema).is_valid(values)


def test_export_unicode_paths_streams_overwrite_and_short_writes(document, tmp_path):
    document.form["person.name"].alternate_name = 'Имя "name"'
    expected = document.form.to_json_schema()
    data = document.form.export_json_schema()
    assert json.loads(data) == expected and data.endswith(b"\n")
    assert 'Имя'.encode() in data
    target = tmp_path / "nested" / "schema.json"
    assert document.form.export_json_schema(target) == data == target.read_bytes()
    with pytest.raises(FileExistsError):
        document.form.export_json_schema(target, overwrite=False)

    class ShortWriter(io.BytesIO):
        def write(self, value):
            return super().write(value[:3])

    with ShortWriter(b"prefix:") as stream:
        stream.seek(0, 2)
        assert document.form.export_json_schema(stream) == data
        assert stream.getvalue() == b"prefix:" + data and not stream.closed


def test_export_errors_never_touch_destination_and_invalid_options_do_no_reads(document, monkeypatch):
    field = field_dictionary(document._engine_pdf, "person.name")
    field.mapping[PdfName("DV")] = PdfDictionary()
    with io.BytesIO(b"untouched") as stream:
        with pytest.raises(PdfValidationException):
            document.form.export_json_schema(stream)
        assert stream.getvalue() == b"untouched"

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid options must fail before reading schema metadata")

    monkeypatch.setattr(Form, "to_json_schema", forbidden)
    with pytest.raises(PdfValidationException, match="overwrite"):
        document.form.export_json_schema(overwrite=1)


@pytest.mark.parametrize("source_kind", ["bytes", "file", "streaming"])
@pytest.mark.parametrize("encrypted", [False, True])
def test_schema_round_trips_and_streaming_inputs_do_not_decode_content(
    document, tmp_path, monkeypatch, source_kind, encrypted,
):
    expected = document.form.to_json_schema()
    if encrypted:
        document.encrypt("secret", "owner")
    path = tmp_path / "form.pdf"
    document.save(path)
    raw = path.read_bytes()
    if source_kind == "bytes":
        reopened = Document(raw, password="secret" if encrypted else None)
    else:
        reopened = (Document.open_streaming if source_kind == "streaming" else Document)(
            path, password="secret" if encrypted else None,
        )
    with reopened:
        form = reopened.form

        def forbidden(*args, **kwargs):
            pytest.fail("Schema export must not decode page/image/appearance streams")

        monkeypatch.setattr(reopened._engine_pdf, "get_page_content", forbidden)
        monkeypatch.setattr(reopened._engine_pdf, "_decode_cos_stream", forbidden)
        assert form.to_json_schema() == expected
        assert json.loads(form.export_json_schema()) == expected
        if source_kind == "streaming":
            assert reopened._engine_pdf.page_contents == []
    assert path.read_bytes() == raw


def test_current_answers_and_validator_package_are_not_read_at_runtime(document, monkeypatch):
    form = document.form
    pdf = document._engine_pdf
    answer = pdf._cos_doc.register_object(PdfString("PRIVATE ANSWER"))
    field_dictionary(pdf, "person.name").mapping[PdfName("V")] = answer
    resolve = pdf._resolve

    def guarded(value):
        assert value is not answer, "Current field answers must not be resolved"
        return resolve(value)

    monkeypatch.setattr(pdf, "_resolve", guarded)
    monkeypatch.setitem(sys.modules, "jsonschema", None)
    assert "person.name" in form.to_json_schema()["properties"]
    assert b"PRIVATE ANSWER" not in form.export_json_schema()


def test_empty_disposed_and_cyclic_forms():
    with Document() as document:
        form = document.form
        schema = form.to_json_schema()
        Draft202012Validator.check_schema(schema)
        assert Draft202012Validator(schema).is_valid({})
        assert not Draft202012Validator(schema).is_valid({"unknown": 1})
        assert json.loads(form.export_json_schema()) == schema
    for call in (form.to_json_schema, form.export_json_schema):
        with pytest.raises(AsposePdfException, match="disposed"):
            call()
    with Document() as document:
        document.pages.add()
        document.form.add_text_field("name", 0, (10, 10, 100, 30))
        field = field_dictionary(document._engine_pdf, "name")
        field.mapping[PdfName("Kids")] = PdfArray([field])
        with pytest.raises(PdfParseException, match="cycle"):
            document.form.to_json_schema()


def test_schema_output_limits_preserve_existing_files(document, tmp_path):
    expected = document.form.export_json_schema()
    raw = _bytes(document)
    target = tmp_path / "schema.json"
    target.write_bytes(b"untouched")
    with Document(raw, limits=PdfLoadLimits(max_codec_work_bytes=len(expected) - 1)) as limited:
        with pytest.raises(PdfResourceLimitException, match="form schema JSON bytes"):
            limited.form.export_json_schema(target)
    assert target.read_bytes() == b"untouched"
    with Document(raw, limits=PdfLoadLimits(max_codec_work_bytes=len(expected))) as limited:
        assert limited.form.export_json_schema(target) == expected


def test_export_leaves_pdf_values_appearances_and_navigation_intact(document):
    before = _bytes(document)
    values = document.form.to_dict()
    schema = document.form.to_json_schema()
    assert json.loads(document.form.export_json_schema()) == schema
    assert document.form.to_dict() == values
    assert _bytes(document) == before
