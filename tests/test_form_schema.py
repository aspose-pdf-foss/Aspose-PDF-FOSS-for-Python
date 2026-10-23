"""Reusable AcroForm descriptions for interfaces and template integrations."""

from __future__ import annotations

import io
import json

import pytest

from aspose_pdf import Document, PdfLoadLimits, PdfResourceLimitException
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNull,
    PdfNumber,
    PdfString,
)
from aspose_pdf.engine.form_fields import field_dictionary
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfParseException,
    PdfValidationException,
    UnsupportedFeatureException,
)
from aspose_pdf.forms import Form


def _bytes(document):
    with io.BytesIO() as output:
        document.save(output)
        return output.getvalue()


def _by_name(form):
    return {field["name"]: field for field in form.to_schema()["fields"]}


@pytest.fixture
def document():
    with Document() as doc:
        page = doc.pages.add()
        form = doc.form
        name = form.add_text_field(
            "person.name", page, (20, 700, 220, 724), value="Template", required=True,
        )
        name.alternate_name = "Your name"
        name.mapping_name = "customer_name"
        name.max_length = 40
        name.multiline = True
        form.add_text_field("secret", page, (20, 660, 220, 684), value="")
        form["secret"].password = True
        form.add_text_field("code", page, (20, 620, 220, 644))
        form["code"].max_length = 6
        form["code"].comb = True
        form.add_checkbox("agree", page, (20, 580, 40, 600), checked=True, on_value="Agree")
        form.add_radio_group("size", page, [("S", (20, 540, 40, 560)), ("M", (60, 540, 80, 560))], value="S")
        form.add_list_box(
            "colours", page, (20, 420, 160, 510), [("r", "Red"), ("g", "Green")],
            value=["r"], multiselect=True,
        )
        form.add_list_box("single", page, (180, 420, 300, 510), ["a", "b"], value="a")
        form.add_combo_box("country", page, (20, 380, 160, 404), ["NL", "DE"], value="NL")
        form.add_combo_box("free", page, (180, 380, 300, 404), ["NL", "DE"], editable=True)
        form["free"].default_value = "Other"
        form.add_push_button("go", page, (20, 330, 100, 354), caption="Go")
        form.add_signature_field("signature", page, (20, 280, 160, 304))
        form["country"].read_only = True
        form["country"].no_export = True
        yield doc


def test_schema_covers_all_types_rules_labels_and_defaults(document):
    schema = document.form.to_schema()
    assert schema["schema_version"] == 1
    fields = _by_name(document.form)
    assert list(fields) == [
        "person.name", "secret", "code", "agree", "size", "colours", "single",
        "country", "free", "go", "signature",
    ]
    assert fields["person.name"] == {
        "name": "person.name", "partial_name": "name", "alternate_name": "Your name",
        "mapping_name": "customer_name", "field_type": "text", "flags": 4098,
        "read_only": False, "required": True, "no_export": False,
        "default_value": "Template", "max_length": 40, "multiline": True,
        "password": False, "comb": False, "options": [], "export_values": [],
        "multi_select": False, "editable": False,
    }
    assert fields["secret"]["password"] and fields["code"]["comb"]
    assert fields["agree"]["default_value"] is True
    assert fields["agree"]["export_values"] == ["Agree"]
    assert fields["size"]["field_type"] == "radio"
    assert fields["size"]["default_value"] == "S"
    assert fields["size"]["export_values"] == ["S", "M"]
    assert fields["colours"]["field_type"] == "listbox"
    assert fields["colours"]["multi_select"]
    assert fields["colours"]["default_value"] == ["r"]
    assert fields["colours"]["options"] == [
        {"export_value": "r", "display_text": "Red"},
        {"export_value": "g", "display_text": "Green"},
    ]
    assert fields["single"]["default_value"] == "a"
    assert fields["country"]["field_type"] == "combobox"
    assert fields["country"]["read_only"] and fields["country"]["no_export"]
    assert fields["country"]["options"][0] == {"export_value": "NL", "display_text": "NL"}
    assert fields["free"]["editable"] and fields["free"]["default_value"] == "Other"
    assert fields["go"]["field_type"] == "pushbutton"
    assert fields["signature"]["field_type"] == "signature"
    assert fields["go"]["default_value"] is fields["signature"]["default_value"] is None


@pytest.mark.parametrize("source", ["bytes", "stream", "file", "streaming"])
def test_schema_survives_round_trips_with_same_metadata(document, source, tmp_path):
    expected = document.form.to_schema()
    if source in ("bytes", "stream"):
        data = _bytes(document)
        reopened = Document(data if source == "bytes" else io.BytesIO(data))
    else:
        path = tmp_path / "form.pdf"
        document.save(path)
        opener = Document.open_streaming if source == "streaming" else Document
        reopened = opener(path)
    with reopened:
        assert reopened.form.to_schema() == expected
        assert json.loads(reopened.form.export_schema()) == expected


def test_schema_stays_independent_of_filled_answers_and_source_lifetime(document):
    expected = document.form.to_schema()
    document.form.fill({"person.name": "PRIVATE ANSWER", "agree": False, "colours": ["g"]})
    assert document.form.to_schema() == expected
    assert b"PRIVATE ANSWER" not in document.form.export_schema()
    changed = document.form.to_schema()
    changed["fields"][5]["options"][0]["display_text"] = "Changed"
    changed["fields"][5]["default_value"].append("g")
    assert document.form.to_schema() == expected
    document.dispose()
    assert expected["fields"][0]["default_value"] == "Template"


def test_json_destinations_unicode_overwrite_and_short_writes(document, tmp_path):
    document.form["person.name"].alternate_name = 'Ваше имя "name"'
    data = document.form.export_schema()
    assert 'Ваше имя'.encode() in data and data.endswith(b"\n")
    assert json.loads(data) == document.form.to_schema()
    assert document.form.export_schema() == data
    target = tmp_path / "nested" / "schema.json"
    assert document.form.export_schema(target) == data == target.read_bytes()
    with pytest.raises(FileExistsError):
        document.form.export_schema(target, overwrite=False)
    assert target.read_bytes() == data

    class ShortWriter(io.BytesIO):
        def write(self, value):
            return super().write(value[:3])

    with ShortWriter(b"prefix:") as stream:
        stream.seek(0, 2)
        assert document.form.export_schema(stream) == data
        assert stream.getvalue() == b"prefix:" + data and not stream.closed


def test_stalled_stream_and_invalid_overwrite(document, monkeypatch):
    class StalledWriter(io.BytesIO):
        def write(self, data):
            return 0

    with StalledWriter() as stream, pytest.raises(AsposePdfException, match="Stream write"):
        document.form.export_schema(stream)

    def no_read(*args):
        raise AssertionError("Invalid options must fail before metadata reads")

    monkeypatch.setattr(Form, "to_schema", no_read)
    with pytest.raises(PdfValidationException, match="overwrite"):
        document.form.export_schema(overwrite=1)


def test_empty_and_disposed_forms():
    with Document() as document:
        form = document.form
        assert form.to_schema() == {"schema_version": 1, "fields": []}
        assert json.loads(form.export_schema()) == form.to_schema()
    for call in (form.to_schema, form.export_schema):
        with pytest.raises(AsposePdfException):
            call()


def test_effective_inheritance_indirect_options_and_default_overrides(document):
    pdf = document._engine_pdf
    child = field_dictionary(pdf, "person.name")
    parent = pdf._resolve(child.mapping[PdfName("Parent")])
    for key in ("FT", "Ff", "DV", "MaxLen"):
        parent.mapping[PdfName(key)] = child.mapping.pop(PdfName(key))
    parent.mapping[PdfName("DV")] = pdf._cos_doc.register_object(PdfString("Inherited"))
    field = _by_name(document.form)["person.name"]
    assert field["default_value"] == "Inherited" and field["max_length"] == 40
    assert field["required"] and field["multiline"]
    child.mapping[PdfName("DV")] = PdfNull()
    assert _by_name(document.form)["person.name"]["default_value"] is None
    choices = field_dictionary(pdf, "colours")
    choices.mapping[PdfName("Opt")] = pdf._cos_doc.register_object(PdfArray([
        pdf._cos_doc.register_object(PdfArray([PdfString("x"), PdfString("Label X")])),
    ]))
    assert _by_name(document.form)["colours"]["options"] == [
        {"export_value": "x", "display_text": "Label X"},
    ]


def test_no_defaults_and_single_multiselect_default_are_typed(document):
    pdf = document._engine_pdf
    for name in ("person.name", "agree", "size", "colours", "single"):
        field_dictionary(pdf, name).mapping.pop(PdfName("DV"), None)
    fields = _by_name(document.form)
    assert fields["person.name"]["default_value"] is None
    assert fields["agree"]["default_value"] is False
    assert fields["size"]["default_value"] is None
    assert fields["colours"]["default_value"] == []
    assert fields["single"]["default_value"] is None
    field_dictionary(pdf, "colours").mapping[PdfName("DV")] = PdfString("g")
    assert _by_name(document.form)["colours"]["default_value"] == ["g"]


def test_current_values_actions_and_page_streams_are_not_read(document, monkeypatch):
    form = document.form
    pdf = document._engine_pdf
    field = field_dictionary(pdf, "person.name")
    private = pdf._cos_doc.register_object(PdfString("PRIVATE ANSWER"))
    field.mapping[PdfName("V")] = private
    resolve = pdf._resolve

    def safe_resolve(value):
        if value is private:
            raise AssertionError("Schema must not resolve current answers")
        return resolve(value)

    def no_decode(*args, **kwargs):
        raise AssertionError("Schema must not decode streams")

    monkeypatch.setattr(pdf, "_resolve", safe_resolve)
    monkeypatch.setattr(pdf, "get_page_content", no_decode)
    monkeypatch.setattr(pdf, "_decode_cos_stream", no_decode)
    assert len(form.to_schema()["fields"]) == 11
    assert b"PRIVATE ANSWER" not in form.export_schema()


def test_export_does_not_modify_pdf_fields_appearances_or_caches(document):
    values = document.form.to_dict()
    before = _bytes(document)
    schema = document.form.to_schema()
    assert json.loads(document.form.export_schema()) == schema
    assert document.form.to_dict() == values and _bytes(document) == before


@pytest.mark.parametrize("key, value", [
    ("MaxLen", PdfNumber(-1)), ("MaxLen", PdfNumber(1.5)),
    ("Ff", PdfString("bad")), ("Ff", PdfNumber(float("inf"))),
    ("DV", PdfDictionary()), ("DV", PdfArray([PdfString("a")])),
    ("TU", PdfNumber(4)),
])
def test_malformed_metadata_is_rejected_without_touching_output(document, key, value):
    field_dictionary(document._engine_pdf, "person.name").mapping[PdfName(key)] = value
    with io.BytesIO(b"unchanged") as stream:
        with pytest.raises(PdfValidationException):
            document.form.export_schema(stream)
        assert stream.getvalue() == b"unchanged"


def test_schema_json_output_limit_preserves_existing_file(document, tmp_path):
    data = _bytes(document)
    expected = document.form.export_schema()
    target = tmp_path / "schema.json"
    target.write_bytes(b"unchanged")
    with Document(data, limits=PdfLoadLimits(max_codec_work_bytes=len(expected) - 1)) as limited:
        with pytest.raises(PdfResourceLimitException, match="form schema JSON bytes"):
            limited.form.export_schema(target)
    assert target.read_bytes() == b"unchanged"
    with Document(data, limits=PdfLoadLimits(max_codec_work_bytes=len(expected))) as limited:
        assert limited.form.export_schema(target) == expected


@pytest.mark.parametrize("streaming", [False, True])
def test_encrypted_inputs_and_lazy_streams(document, tmp_path, streaming):
    expected = document.form.to_schema()
    path = tmp_path / "encrypted.pdf"
    document.encrypt("user", "owner")
    document.save(path)
    before = path.read_bytes()
    opener = Document.open_streaming if streaming else Document
    with opener(path, password="user") as reopened:
        assert reopened.form.to_schema() == expected
        assert json.loads(reopened.form.export_schema()) == expected
        if streaming:
            assert reopened._engine_pdf.page_contents == []
    assert path.read_bytes() == before


@pytest.mark.parametrize("damage", ["acroform", "field", "name", "type", "huge_flags"])
def test_malformed_field_tree_never_becomes_a_silently_incomplete_schema(document, damage):
    pdf = document._engine_pdf
    root = pdf._resolve(pdf._cos_doc.trailer.mapping[PdfName("Root")])
    acroform = pdf._resolve(root.mapping[PdfName("AcroForm")])
    if damage == "acroform":
        root.mapping[PdfName("AcroForm")] = PdfNumber(1)
    elif damage == "field":
        acroform.mapping[PdfName("Fields")] = PdfArray([PdfNumber(1)])
    else:
        field = field_dictionary(pdf, "person.name")
        key, value = {
            "name": ("T", PdfNumber(1)),
            "type": ("FT", PdfString("Tx")),
            "huge_flags": ("Ff", PdfNumber(10**1000)),
        }[damage]
        field.mapping[PdfName(key)] = value
    with pytest.raises((PdfParseException, PdfValidationException)):
        document.form.to_schema()


def test_unknown_field_type_is_explicitly_unsupported(document):
    field_dictionary(document._engine_pdf, "person.name").mapping[PdfName("FT")] = PdfName("VendorField")
    with pytest.raises(UnsupportedFeatureException):
        document.form.to_schema()


def test_full_names_count_towards_aggregate_schema_text_limit():
    with Document() as source:
        page = source.pages.add()
        for index in range(20):
            source.form.add_text_field(f"{'p' * 100}.f{index}", page, (20, 500, 120, 520))
        data = _bytes(source)
    with Document(data, limits=PdfLoadLimits(max_total_decoded_bytes=1000)) as document:
        with pytest.raises(PdfResourceLimitException, match="form schema text"):
            document.form.to_schema()


def test_choice_arrays_are_bounded_across_fields():
    with Document() as source:
        page = source.pages.add()
        for name in ("first", "second"):
            source.form.add_combo_box(name, page, (20, 500, 120, 520), [str(i) for i in range(12)])
        data = _bytes(source)
    with Document(data, limits=PdfLoadLimits(max_container_items=20)) as document:
        with pytest.raises(PdfResourceLimitException, match="form schema metadata items"):
            document.form.to_schema()


@pytest.mark.parametrize("state", ["No", "0", "Off"])
def test_checkbox_name_defaults_preserve_pdf_on_state_semantics(document, state):
    field_dictionary(document._engine_pdf, "agree").mapping[PdfName("DV")] = PdfName(state)
    assert _by_name(document.form)["agree"]["default_value"] is (state != "Off")


@pytest.mark.parametrize("damage", ["parent_cycle", "child_cycle", "duplicate_name"])
def test_cycles_and_duplicate_fields_are_rejected(document, damage):
    pdf = document._engine_pdf
    first = field_dictionary(pdf, "person.name")
    if damage == "parent_cycle":
        first.mapping[PdfName("Parent")] = first
    elif damage == "child_cycle":
        first.mapping[PdfName("Kids")] = PdfArray([first])
    else:
        second = field_dictionary(pdf, "secret")
        second.mapping[PdfName("T")] = PdfString("person.name")
    with pytest.raises(PdfParseException):
        document.form.to_schema()


@pytest.mark.parametrize("value", [
    PdfNumber(1), PdfArray([PdfString("only one")]),
    PdfArray([PdfString("a"), PdfNumber(2)]),
])
def test_malformed_choice_entries_are_rejected(document, value):
    field_dictionary(document._engine_pdf, "colours").mapping[PdfName("Opt")] = PdfArray([value])
    with pytest.raises(PdfValidationException):
        document.form.to_schema()


def test_duplicate_choices_and_export_display_values_are_preserved(document):
    field = field_dictionary(document._engine_pdf, "country")
    field.mapping[PdfName("Opt")] = PdfArray([
        PdfArray([PdfString("NL"), PdfString("Netherlands")]),
        PdfArray([PdfString("NL"), PdfString("Nederland")]),
    ])
    assert _by_name(document.form)["country"]["options"] == [
        {"export_value": "NL", "display_text": "Netherlands"},
        {"export_value": "NL", "display_text": "Nederland"},
    ]


def test_schema_reflects_live_template_edits_and_removals(document):
    original = document.form.to_schema()
    field = document.form["person.name"]
    field.max_length = 25
    field.required = False
    field.default_value = "New template"
    document.form.remove_field("secret")
    current = _by_name(document.form)
    assert "secret" not in current
    assert current["person.name"]["max_length"] == 25
    assert current["person.name"]["required"] is False
    assert current["person.name"]["default_value"] == "New template"
    assert original["fields"][0]["max_length"] == 40


def test_long_escaped_unicode_labels_and_unlimited_policy(document):
    label = 'Имя "name" \\ \n' * 5000
    document.form["person.name"].alternate_name = label
    data = _bytes(document)
    with Document(data, limits=PdfLoadLimits.unlimited()) as reopened:
        result = json.loads(reopened.form.export_schema())
        assert result["fields"][0]["alternate_name"] == label
        assert result == reopened.form.to_schema()
