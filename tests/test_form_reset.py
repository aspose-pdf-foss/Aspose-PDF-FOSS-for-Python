"""Restore reusable PDF form templates to their effective default values."""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document, PdfLoadLimits
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNull,
    PdfNumber,
    PdfString,
)
from aspose_pdf.engine.form_fields import field_dictionary, widget_dictionaries
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
        form.add_text_field(
            "person.name", page, (20, 700, 220, 724), value="Template", read_only=True
        )
        form.add_checkbox(
            "agree", page, (20, 650, 40, 670), checked=True, on_value="Agree"
        )
        form.add_radio_group(
            "size",
            page,
            [("S", (20, 600, 40, 620)), ("M", (60, 600, 80, 620))],
            value="S",
        )
        form.add_list_box(
            "colours",
            page,
            (20, 470, 160, 560),
            [("r", "Red"), ("g", "Green")],
            value=["r"],
            multiselect=True,
        )
        form.add_list_box("single", page, (180, 470, 300, 560), ["a", "b"], value="a")
        form.add_combo_box(
            "country", page, (20, 420, 160, 444), ["NL", "DE"], value="NL"
        )
        form.add_combo_box(
            "free",
            page,
            (180, 420, 300, 444),
            ["NL", "DE"],
            value="Custom default",
            editable=True,
        )
        form.add_push_button("go", page, (20, 350, 100, 374), caption="Go")
        form.add_signature_field("sig", page, (20, 300, 160, 324))
        form["person.name"].no_export = True
        yield doc


FILLED = {
    "person.name": "Completed",
    "agree": False,
    "size": "M",
    "colours": ["g"],
    "single": "b",
    "country": "DE",
    "free": "Other value",
}


@pytest.mark.parametrize("on_disk", [False, True])
def test_reset_all_restores_template_defaults_and_survives_round_trips(
    document, tmp_path, on_disk
):
    defaults = document.form.to_dict()
    retained = document.form["person.name"]
    original_pixels = document.pages[0].render(dpi=36, antialias=False).pixels
    document.form.fill(FILLED)
    assert document.form.to_dict() != defaults
    assert document.pages[0].render(dpi=36, antialias=False).pixels != original_pixels

    assert document.form.reset() == list(defaults)
    assert document.form.to_dict() == defaults
    assert retained.value == "Template" and retained.read_only and retained.no_export
    assert document.pages[0].render(dpi=36, antialias=False).pixels == original_pixels
    if on_disk:
        source = tmp_path / "reset.pdf"
        document.save(source)
    else:
        buffer = io.BytesIO()
        document.save(buffer)
        source = buffer.getvalue()
    with Document(source) as reopened:
        assert reopened.form.to_dict() == defaults
        reopened.form.fill(FILLED)
        assert reopened.form.reset() == list(defaults)
        assert reopened.form.to_dict() == defaults


def test_selected_fields_restore_defaults_and_deduplicate_names(document):
    document.form.fill(FILLED)
    assert document.form.reset(name for name in ["size", "person.name", "size"]) == [
        "size",
        "person.name",
    ]
    expected = {**FILLED, "size": "S", "person.name": "Template"}
    assert document.form.to_dict() == expected
    assert document.form.reset([]) == []
    assert document.form.to_dict() == expected


def test_unknown_names_can_be_skipped_and_are_validated_before_updates(document):
    document.form.fill(FILLED)
    with pytest.raises(KeyError, match="missing"):
        document.form.reset(["person.name", "missing"])
    assert document.form.to_dict() == FILLED
    assert document.form.reset(["missing", "agree"], strict=False) == ["agree"]
    assert document.form.to_dict() == {**FILLED, "agree": True}


@pytest.mark.parametrize(
    "fields", ["agree", b"agree", bytearray(b"agree"), 1, True, ["agree", 1]]
)
def test_invalid_field_selection_does_not_modify_values(document, fields):
    document.form.fill(FILLED)
    with pytest.raises(TypeError):
        document.form.reset(fields)
    assert document.form.to_dict() == FILLED


def test_invalid_strict_and_nonterminal_names_do_not_modify_values(document):
    document.form.fill(FILLED)
    with pytest.raises(TypeError, match="strict"):
        document.form.reset(strict=1)
    with pytest.raises(KeyError, match="person"):
        document.form.reset(["person"])
    assert document.form.to_dict() == FILLED


@pytest.mark.parametrize("selected", [[], ["r"], ["r", "g"]])
def test_multiselect_defaults_with_zero_one_or_many_values(document, selected):
    field = field_dictionary(document._engine_pdf, "colours")
    default = PdfArray([PdfString(value) for value in selected])
    field.mapping[PdfName("DV")] = default
    document.form.fill({"colours": ["g"]})
    assert document.form.reset(["colours"]) == ["colours"]
    assert document.form.to_dict()["colours"] == selected
    document.form.fill({"colours": ["g"]})
    assert [item.value for item in default.items] == [
        value.encode() for value in selected
    ]
    document.form.reset(["colours"])
    assert document.form.to_dict()["colours"] == selected


def test_absent_defaults_clear_each_field_type(document):
    for name in FILLED:
        field_dictionary(document._engine_pdf, name).mapping.pop(PdfName("DV"), None)
    document.form.fill(FILLED)
    assert document.form.reset() == list(FILLED)
    assert document.form.to_dict() == {
        "person.name": "",
        "agree": False,
        "size": None,
        "colours": [],
        "single": None,
        "country": None,
        "free": None,
    }


@pytest.mark.parametrize("on_value", ["No", "0", "false"])
def test_custom_checkbox_state_names_restore_checked_defaults(on_value):
    with Document() as doc:
        doc.pages.add()
        field = doc.form.add_checkbox(
            "custom", 0, (20, 20, 40, 40), checked=True, on_value=on_value
        )
        assert field.default_value is True
        field.value = False
        assert doc.form.reset() == ["custom"]
        assert doc.form.to_dict() == {"custom": True}
        buffer = io.BytesIO()
        doc.save(buffer)
        with Document(buffer.getvalue()) as reopened:
            assert reopened.form["custom"].value is True


def test_null_defaults_are_treated_as_absent(document):
    for name in FILLED:
        field_dictionary(document._engine_pdf, name).mapping[PdfName("DV")] = PdfNull()
        assert document.form[name].default_value is (False if name == "agree" else None)
    document.form.fill(FILLED)
    document.form.reset()
    assert document.form.to_dict() == {
        "person.name": "",
        "agree": False,
        "size": None,
        "colours": [],
        "single": None,
        "country": None,
        "free": None,
    }


def test_inherited_default_is_restored_without_changing_siblings(document):
    form = document.form
    form.add_text_field("person.city", 0, (20, 250, 220, 274), value="Keep city")
    field = field_dictionary(document._engine_pdf, "person.name")
    parent = document._engine_pdf._resolve(field.mapping[PdfName("Parent")])
    parent.mapping[PdfName("DV")] = PdfString("Inherited default")
    field.mapping.pop(PdfName("DV"))
    form.fill({"person.name": "Changed", "person.city": "Keep city"})
    assert form.reset(["person.name"]) == ["person.name"]
    assert form["person.name"].value == "Inherited default"
    assert form["person.city"].value == "Keep city"
    assert parent.mapping[PdfName("DV")].value == b"Inherited default"


def test_indirect_multiselect_default_is_normalized(document):
    field = field_dictionary(document._engine_pdf, "colours")
    field.mapping[PdfName("DV")] = document._engine_pdf._cos_doc.register_object(
        PdfArray([PdfString("r")])
    )
    document.form.fill({"colours": ["g"]})
    document.form.reset(["colours"])
    assert document.form.to_dict()["colours"] == ["r"]


def test_missing_choice_default_clears_an_inherited_current_value(document):
    form = document.form
    form.add_combo_box(
        "person.choice", 0, (20, 250, 220, 274), ["Inherited", "Other"], value="Other"
    )
    field = field_dictionary(document._engine_pdf, "person.choice")
    parent = document._engine_pdf._resolve(field.mapping[PdfName("Parent")])
    parent.mapping[PdfName("V")] = PdfString("Inherited")
    field.mapping.pop(PdfName("DV"), None)
    assert form.reset(["person.choice"]) == ["person.choice"]
    assert form["person.choice"].value is None
    assert parent.mapping[PdfName("V")].value == b"Inherited"
    buffer = io.BytesIO()
    document.save(buffer)
    with Document(buffer.getvalue()) as reopened:
        assert reopened.form["person.choice"].value is None


@pytest.mark.parametrize(
    "default", [PdfNumber(42), PdfDictionary({}), PdfArray([PdfString("a")])]
)
def test_malformed_text_default_is_rejected_before_any_updates(document, default):
    field_dictionary(document._engine_pdf, "person.name").mapping[PdfName("DV")] = (
        default
    )
    document.form.fill(FILLED)
    with pytest.raises(PdfValidationException, match="default"):
        document.form.reset(["agree", "person.name"])
    assert document.form.to_dict() == FILLED


def test_invalid_choice_and_button_defaults_are_rejected_before_updates(document):
    document.form.fill(FILLED)
    field_dictionary(document._engine_pdf, "country").mapping[PdfName("DV")] = (
        PdfString("FR")
    )
    with pytest.raises(PdfValidationException, match="options"):
        document.form.reset(["agree", "country"])
    assert document.form.to_dict() == FILLED
    field_dictionary(document._engine_pdf, "agree").mapping[PdfName("DV")] = PdfName(
        "Unknown"
    )
    with pytest.raises(PdfValidationException, match="button states"):
        document.form.reset(["size", "agree"])
    assert document.form.to_dict() == FILLED


def test_malformed_multiselect_default_is_not_silently_truncated(document):
    field_dictionary(document._engine_pdf, "colours").mapping[PdfName("DV")] = PdfArray(
        [PdfString("r"), PdfDictionary({})]
    )
    document.form.fill(FILLED)
    with pytest.raises(PdfValidationException, match="default"):
        document.form.reset(["agree", "colours"])
    assert document.form.to_dict() == FILLED


def test_push_buttons_and_signature_values_and_appearances_are_skipped(document):
    engine = document._engine_pdf
    signature = field_dictionary(engine, "sig")
    marker = PdfDictionary({PdfName("Reason"): PdfString("Keep signature")})
    signature.mapping[PdfName("V")] = marker
    signature.mapping[PdfName("DV")] = PdfDictionary({})
    signature_aps = [
        widget.mapping[PdfName("AP")]
        for widget in widget_dictionaries(engine, signature)
    ]
    button = field_dictionary(engine, "go")
    button_aps = [
        widget.mapping[PdfName("AP")] for widget in widget_dictionaries(engine, button)
    ]
    assert document.form.reset(["go", "sig"]) == []
    document.form.fill(FILLED)
    document.form.reset()
    assert signature.mapping[PdfName("V")] is marker
    assert [
        widget.mapping[PdfName("AP")]
        for widget in widget_dictionaries(engine, signature)
    ] == signature_aps
    assert [
        widget.mapping[PdfName("AP")] for widget in widget_dictionaries(engine, button)
    ] == button_aps


def test_empty_and_disposed_forms():
    with Document() as document:
        form = document.form
        assert form.reset() == []
        assert form.reset(["missing"], strict=False) == []
    with pytest.raises(AsposePdfException, match="disposed"):
        form.reset()


def test_selection_is_bounded_even_with_repeated_unknown_names():
    with Document(limits=PdfLoadLimits(max_container_items=4)) as document:
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            document.form.reset(("missing" for _ in range(5)), strict=False)


def test_streaming_reset_round_trip(document, tmp_path):
    defaults = document.form.to_dict()
    document.form.fill(FILLED)
    source = tmp_path / "filled.pdf"
    document.save(source)
    with Document.open_streaming(source) as reopened:
        assert reopened.form.reset() == list(defaults)
        assert reopened.form.to_dict() == defaults
        target = tmp_path / "reset.pdf"
        reopened.save(target)
    with Document(target) as saved:
        assert saved.form.to_dict() == defaults
