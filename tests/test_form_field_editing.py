"""Changing existing AcroForm field parameters through the public API."""

from __future__ import annotations

import io
import re
from collections import UserList

import pytest

from aspose_pdf import Document, PdfLoadLimits
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName, PdfNumber, PdfString
from aspose_pdf.engine.form_fields import field_dictionary
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfResourceLimitException,
    PdfValidationException,
    UnsupportedFeatureException,
)


@pytest.fixture
def document():
    with Document() as doc:
        doc.pages.add(size=(300, 200))
        yield doc


def _text(document, name="text", value="1234", **kwargs):
    return document.form.add_text_field(
        name, 0, (20, 100, 220, 140), value=value, **kwargs
    )


def _choice(document, kind="listbox", **kwargs):
    factory = (
        document.form.add_combo_box
        if kind == "combobox"
        else document.form.add_list_box
    )
    return factory(
        "choice", 0, (20, 20, 220, 80), [("a", "Alpha"), ("b", "Beta")], **kwargs
    )


def _saved(document, tmp_path, on_disk):
    if on_disk:
        path = tmp_path / "form.pdf"
        document.save(path)
        return path
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


@pytest.mark.parametrize("on_disk", [False, True])
def test_text_parameters_and_defaults_survive_save_and_reset(
    document, tmp_path, on_disk
):
    field = _text(document)
    field.max_length = 12
    field.multiline = True
    field.required = field.read_only = True
    field.default_value = "Template"
    assert field.value == "1234"
    with Document(_saved(document, tmp_path, on_disk)) as reopened:
        edited = reopened.form["text"]
        assert edited.max_length == 12 and edited.multiline
        assert edited.required and edited.read_only
        assert edited.default_value == "Template"
        reopened.form.reset()
        assert edited.value == "Template"
        edited.multiline = False
        edited.password = True
        edited.password = False
        edited.max_length = None
        assert (
            not edited.multiline and not edited.password and edited.max_length is None
        )


def test_multiline_change_refreshes_the_display_and_keeps_other_widgets(document):
    field = _text(document, value="first\nsecond")
    _text(document, name="other", value="untouched")
    page = document.pages[0]
    old_sibling = page.annotations[1].appearance_normal
    assert b"(first second) Tj" in page.annotations[0].appearance_normal
    field.multiline = True
    content = page.annotations[0].appearance_normal
    assert b"(first) Tj" in content and b"(second) Tj" in content
    assert content.count(b" Tm") == 2
    assert page.annotations[1].appearance_normal == old_sibling
    field.multiline = False
    assert b"(first second) Tj" in page.annotations[0].appearance_normal


def test_password_masks_the_appearance_while_preserving_the_value(document, tmp_path):
    field = _text(document, value="secret")
    field.password = True
    assert field.value == "secret"
    content = document.pages[0].annotations[0].appearance_normal
    assert b"(******) Tj" in content and b"secret" not in content
    field.value = "new"
    assert b"(***) Tj" in document.pages[0].annotations[0].appearance_normal
    with Document(_saved(document, tmp_path, False)) as reopened:
        field = reopened.form["text"]
        assert field.password and field.value == "new"
        field.password = False
        assert b"(new) Tj" in reopened.pages[0].annotations[0].appearance_normal


@pytest.mark.parametrize("alignment", ["left", "center", "right"])
def test_comb_distributes_characters_in_cells_and_honors_alignment(document, alignment):
    field = _text(document, value="11", alignment=alignment)
    field.max_length = 4
    field.comb = True
    content = document.pages[0].annotations[0].appearance_normal
    positions = [
        float(value) for value in re.findall(rb"1 0 0 1 ([\d.-]+) [\d.-]+ Tm", content)
    ]
    assert len(positions) == 2
    assert positions[1] - positions[0] == pytest.approx(50)
    expected_cell = {"left": 0, "center": 1, "right": 2}[alignment]
    assert expected_cell * 50 <= positions[0] < (expected_cell + 1) * 50
    field.comb = False
    assert b"(11) Tj" in document.pages[0].annotations[0].appearance_normal


def test_comb_autosizing_and_flattening_render_the_configured_cells(document):
    field = _text(document, value="WWWW", font_size=0)
    field.max_length = 100
    field.comb = True
    content = document.pages[0].annotations[0].appearance_normal
    size = float(re.search(rb"/Helv ([\d.]+) Tf", content).group(1))
    assert 0 < size < 3
    before = document.pages[0].render(antialias=False).pixels
    document.form.flatten()
    assert document.pages[0].render(antialias=False).pixels == before


@pytest.mark.parametrize(
    "parameter,value",
    [
        ("comb", True),
        ("max_length", 0),
        ("max_length", -1),
        ("max_length", 2**31),
    ],
)
def test_invalid_limits_or_comb_without_length_leave_configuration_unchanged(
    document, parameter, value
):
    field = _text(document)
    before = (
        field.flags,
        field.max_length,
        document.pages[0].annotations[0].appearance_normal,
    )
    with pytest.raises(PdfValidationException):
        setattr(field, parameter, value)
    assert (
        field.flags,
        field.max_length,
        document.pages[0].annotations[0].appearance_normal,
    ) == before


@pytest.mark.parametrize("parameter", ["multiline", "password"])
def test_comb_conflicts_are_rejected_in_both_directions(document, parameter):
    field = _text(document)
    field.max_length = 4
    setattr(field, parameter, True)
    with pytest.raises(PdfValidationException, match="comb requires"):
        field.comb = True
    assert not field.comb and getattr(field, parameter)
    setattr(field, parameter, False)
    field.comb = True
    with pytest.raises(PdfValidationException, match="comb requires"):
        setattr(field, parameter, True)
    with pytest.raises(PdfValidationException, match="positive max_length"):
        field.max_length = None
    assert field.comb and field.max_length == 4 and not getattr(field, parameter)


@pytest.mark.parametrize(
    "parameter", ["multiline", "password", "comb", "multi_select", "editable"]
)
def test_mode_setters_require_booleans(document, parameter):
    kind = (
        "listbox"
        if parameter == "multi_select"
        else "combobox"
        if parameter == "editable"
        else "text"
    )
    field = _text(document) if kind == "text" else _choice(document, kind)
    with pytest.raises(TypeError, match="boolean"):
        setattr(field, parameter, 1)


@pytest.mark.parametrize("value", [True, "10", 3.5])
def test_max_length_requires_an_integer(document, value):
    with pytest.raises(TypeError, match="integer"):
        _text(document).max_length = value


@pytest.mark.parametrize(
    "parameter,value",
    [
        ("max_length", 4),
        ("multiline", True),
        ("password", True),
        ("comb", True),
        ("options", ["a"]),
        ("multi_select", True),
        ("editable", True),
    ],
)
def test_configuration_setters_reject_the_wrong_field_type(document, parameter, value):
    field = document.form.add_checkbox("check", 0, (10, 10, 30, 30))
    with pytest.raises(PdfValidationException, match="applies only"):
        setattr(field, parameter, value)


@pytest.mark.parametrize("on_disk", [False, True])
def test_choice_option_edits_preserve_selections_and_update_indices(
    document, tmp_path, on_disk
):
    field = _choice(document, value=["a", "b"], multiselect=True)
    field.default_value = ["b"]
    field.options = (option for option in [("b", "New Beta"), ("a", "New Alpha"), "γ"])
    assert field.value == ["a", "b"]
    assert field.options == [("b", "New Beta"), ("a", "New Alpha"), ("γ", "γ")]
    content = document.pages[0].annotations[0].appearance_normal
    assert b"New Beta" in content and b"New Alpha" in content
    with Document(_saved(document, tmp_path, on_disk)) as reopened:
        edited = reopened.form["choice"]
        assert edited.options == field.options and edited.value == ["a", "b"]
        # This specifically checks the engine's serialized selection-index bookkeeping.
        dictionary = field_dictionary(reopened._engine_pdf, "choice")
        indices = reopened._engine_pdf._resolve(dictionary.mapping[PdfName("I")])
        assert [index.value for index in indices.items] == [0, 1]
        reopened.form.reset()
        assert reopened.form.to_dict()["choice"] == ["b"]


@pytest.mark.parametrize("default_only", [False, True])
def test_options_cannot_discard_current_or_default_selection(document, default_only):
    field = _choice(document, value=None if default_only else "a")
    if default_only:
        field.default_value = "a"
    before = field.options, field.value, field.default_value
    with pytest.raises(PdfValidationException, match="not one of the options"):
        field.options = ["b"]
    assert (field.options, field.value, field.default_value) == before


@pytest.mark.parametrize(
    "options", [[], ["a", "a"], [("a", "one"), ("a", "two")], [2], [("a",)], [("a", 1)]]
)
def test_invalid_option_lists_leave_the_choices_unchanged(document, options):
    field = _choice(document)
    before = field.options
    with pytest.raises(PdfValidationException):
        field.options = options
    assert field.options == before


@pytest.mark.parametrize("options", ["a", b"a", {"a": "Alpha"}, None])
def test_options_require_an_iterable_of_choices(document, options):
    with pytest.raises(TypeError, match="iterable"):
        _choice(document).options = options


def test_multi_select_can_be_enabled_and_disabled_without_losing_single_selection(
    document,
):
    field = _choice(document, value="b")
    field.default_value = "a"
    field.multi_select = True
    assert field.multi_select
    assert document.form.to_dict()["choice"] == ["b"]
    document.form.reset()
    assert document.form.to_dict()["choice"] == ["a"]
    field.value = ["b"]
    field.multi_select = False
    assert not field.multi_select and field.value == "b" and field.default_value == "a"


@pytest.mark.parametrize("default_only", [False, True])
def test_multi_select_cannot_be_disabled_with_multiple_current_or_default_selections(
    document, default_only
):
    field = _choice(
        document, multiselect=True, value=[] if default_only else ["a", "b"]
    )
    if default_only:
        field.default_value = ["a", "b"]
    with pytest.raises(
        PdfValidationException, match="Cannot disable multiple selection"
    ):
        field.multi_select = False
    assert field.multi_select


def test_empty_multi_selection_can_be_converted_back_to_single(document):
    field = _choice(document, multiselect=True, value=[])
    field.default_value = []
    field.multi_select = False
    assert field.value is None and field.default_value is None


@pytest.mark.parametrize("default_only", [False, True])
def test_editable_combo_cannot_be_disabled_while_free_text_is_used(
    document, default_only
):
    field = _choice(
        document, "combobox", editable=True, value="a" if default_only else "custom"
    )
    if default_only:
        field.default_value = "custom"
    with pytest.raises(PdfValidationException, match="not one of the options"):
        field.editable = False
    assert field.editable
    field.options = ["a", "b", "custom"]
    field.editable = False
    assert not field.editable


def test_editable_combo_can_use_free_text_after_enabling_it(document):
    field = _choice(document, "combobox", value="a")
    field.editable = True
    field.value = "custom"
    field.default_value = "other custom"
    document.form.reset()
    assert field.value == "other custom"


def test_default_values_for_buttons_and_text_are_typed_and_resettable(document):
    text = _text(document)
    check = document.form.add_checkbox("check", 0, (10, 10, 30, 30), on_value="Agree")
    radio = document.form.add_radio_group(
        "radio", 0, [("A", (35, 10, 55, 30)), ("B", (60, 10, 80, 30))]
    )
    text.default_value = "Шаблон"
    check.default_value = True
    radio.default_value = "B"
    assert text.value == "1234" and not check.value and radio.value is None
    document.form.reset()
    assert (text.value, check.value, radio.value) == ("Шаблон", True, "B")
    text.default_value = check.default_value = radio.default_value = None
    document.form.reset()
    assert (text.value, check.value, radio.value) == ("", False, None)


@pytest.mark.parametrize(
    "kind,value",
    [
        ("text", True),
        ("checkbox", "yes"),
        ("radio", True),
        ("radio", "unknown"),
        ("listbox", "unknown"),
        ("combobox", 1),
    ],
)
def test_invalid_default_values_do_not_change_existing_defaults(document, kind, value):
    if kind == "text":
        field = _text(document)
    elif kind == "checkbox":
        field = document.form.add_checkbox("check", 0, (10, 10, 30, 30))
    elif kind == "radio":
        field = document.form.add_radio_group("radio", 0, [("A", (10, 10, 30, 30))])
    else:
        field = _choice(document, kind)
    before = field.default_value
    with pytest.raises((TypeError, PdfValidationException)):
        field.default_value = value
    assert field.default_value == before


@pytest.mark.parametrize("kind", ["pushbutton", "signature"])
def test_nonfillable_fields_reject_defaults(document, kind):
    factory = (
        document.form.add_push_button
        if kind == "pushbutton"
        else document.form.add_signature_field
    )
    field = factory("field", 0, (20, 20, 100, 50))
    with pytest.raises(PdfValidationException, match="does not support"):
        field.default_value = "value"


def test_local_overrides_do_not_mutate_inherited_sibling_parameters(document, tmp_path):
    first = _text(document, "group.first", "one")
    sibling = _text(document, "group.second", "two")
    engine = document._engine_pdf
    child = field_dictionary(engine, first.name)
    other = field_dictionary(engine, sibling.name)
    parent = engine._resolve(child.mapping[PdfName("Parent")])
    parent.mapping.update(
        {
            PdfName("Ff"): PdfNumber(1),
            PdfName("MaxLen"): PdfNumber(20),
            PdfName("DV"): PdfString("parent"),
        }
    )
    child.mapping.pop(PdfName("Ff"), None)
    other.mapping.pop(PdfName("Ff"), None)
    child.mapping.pop(PdfName("DV"), None)
    other.mapping.pop(PdfName("DV"), None)
    first.max_length = 8
    first.multiline = True
    first.default_value = None
    assert sibling.max_length == 20 and sibling.read_only and not sibling.multiline
    assert sibling.default_value == "parent"
    assert (
        first.read_only
        and first.multiline
        and first.max_length == 8
        and first.default_value is None
    )
    first.max_length = None
    assert first.max_length == 20
    with Document(_saved(document, tmp_path, False)) as reopened:
        assert (
            reopened.form["group.first"].multiline
            and not reopened.form["group.second"].multiline
        )
        reopened.form.reset()
        assert reopened.form["group.first"].value == ""
        assert reopened.form["group.second"].value == "parent"


def test_inherited_options_and_defaults_are_overridden_only_on_the_selected_child(
    document,
):
    first = _choice(document, value="a")
    second = document.form.add_list_box(
        "other", 0, (20, 85, 220, 140), ["a", "b"], value="b"
    )
    engine = document._engine_pdf
    first_dict = field_dictionary(engine, first.name)
    second_dict = field_dictionary(engine, second.name)
    parent = engine._cos_doc.register_object(
        PdfDictionary(
            {
                PdfName("Opt"): PdfArray([PdfString("a"), PdfString("b")]),
                PdfName("DV"): PdfString("b"),
            }
        )
    )
    for dictionary in (first_dict, second_dict):
        dictionary.mapping[PdfName("Parent")] = parent
        dictionary.mapping.pop(PdfName("Opt"))
    first.options = ["b", ("a", "Changed")]
    first.default_value = "a"
    assert second.options == [("a", "a"), ("b", "b")] and second.default_value == "b"
    assert first.options == [("b", "b"), ("a", "Changed")]


def test_inherited_comb_length_is_used_by_every_appearance_refresh_path(document):
    field = _text(document, "group.code", "11")
    field.max_length = 6
    field.comb = True
    engine = document._engine_pdf
    child = field_dictionary(engine, field.name)
    parent = engine._resolve(child.mapping[PdfName("Parent")])
    parent.mapping[PdfName("MaxLen")] = child.mapping.pop(PdfName("MaxLen"))
    field.value = "22"
    appearance = document.pages[0].annotations[0].appearance_normal
    positions = [
        float(value)
        for value in re.findall(rb"1 0 0 1 ([\d.-]+) [\d.-]+ Tm", appearance)
    ]
    assert positions[1] - positions[0] == pytest.approx(200 / 6, abs=0.002)
    document.form.generate_appearances()
    assert document.pages[0].annotations[0].appearance_normal == appearance
    field.multiline = False
    assert document.pages[0].annotations[0].appearance_normal == appearance


@pytest.mark.parametrize(
    "parameter,value",
    [("multiline", True), ("max_length", 8), ("default_value", "reset")],
)
def test_removed_and_disposed_fields_reject_parameter_edits(document, parameter, value):
    field = _text(document)
    field.remove()
    replacement = _text(document)
    with pytest.raises(KeyError, match="no longer"):
        setattr(field, parameter, value)
    document.dispose()
    with pytest.raises(AsposePdfException, match="disposed"):
        setattr(replacement, parameter, value)


@pytest.mark.parametrize(
    "parameter,value", [("default_value", "\ud800"), ("options", ["\ud800"])]
)
def test_invalid_unicode_configuration_is_rejected(document, parameter, value):
    field = _text(document) if parameter == "default_value" else _choice(document)
    with pytest.raises(PdfValidationException, match="invalid Unicode"):
        setattr(field, parameter, value)


def test_option_iterables_obey_container_limits_before_being_collected():
    with Document(limits=PdfLoadLimits(max_container_items=10)) as document:
        document.pages.add()
        field = _choice(document)
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            field.options = (str(index) for index in range(11))
        assert field.options == [("a", "Alpha"), ("b", "Beta")]


def test_default_and_option_text_obey_metadata_size_limits():
    with Document(limits=PdfLoadLimits(max_object_bytes=256)) as document:
        document.pages.add()
        field = _text(document)
        with pytest.raises(PdfResourceLimitException, match="max_object_bytes"):
            field.default_value = "x" * 200
        choice = _choice(document)
        with pytest.raises(PdfResourceLimitException, match="max_object_bytes"):
            choice.options = ["x" * 60, "y" * 70]
        assert choice.options == [("a", "Alpha"), ("b", "Beta")]


def test_sequence_defaults_are_checked_before_materializing_the_selections():
    with Document(limits=PdfLoadLimits(max_container_items=10)) as document:
        document.pages.add()
        field = _choice(document, multiselect=True)
        field.default_value = UserList(["a", "b"])
        assert field.default_value == ["a", "b"]
        with pytest.raises(
            PdfResourceLimitException, match="default choice selections"
        ):
            field.default_value = UserList(["a"] * 11)
        assert field.default_value == ["a", "b"]


@pytest.mark.parametrize("parameter", ["multiline", "password", "comb", "max_length"])
def test_cid_text_mode_changes_fail_without_overwriting_the_existing_appearance(
    document, parameter
):
    field = _text(document)
    field.max_length = 4
    if parameter == "max_length":
        field.comb = True
    engine = document._engine_pdf
    root = engine._resolve(engine._cos_doc.trailer.mapping[PdfName("Root")])
    acro = engine._resolve(root.mapping[PdfName("AcroForm")])
    resources = engine._resolve(acro.mapping[PdfName("DR")])
    fonts = engine._resolve(resources.mapping[PdfName("Font")])
    fonts.mapping[PdfName("CID")] = PdfDictionary(
        {PdfName("Subtype"): PdfName("Type0")}
    )
    dictionary = field_dictionary(engine, field.name)
    dictionary.mapping[PdfName("DA")] = PdfString("/CID 12 Tf 0 g")
    appearance = document.pages[0].annotations[0].appearance_normal
    before = field.flags
    with pytest.raises(UnsupportedFeatureException, match="embedded CID"):
        setattr(field, parameter, 8 if parameter == "max_length" else True)
    assert field.flags == before
    assert document.pages[0].annotations[0].appearance_normal == appearance
