"""Field-name Hide actions are authored and read without executing viewer events."""

from __future__ import annotations

import io
from collections import UserList

import pytest

from aspose_pdf import Document, HideAction, URIAction
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfBoolean,
    PdfDictionary,
    PdfName,
    PdfNull,
    PdfNumber,
    PdfString,
    decode_pdf_text_string,
)
from aspose_pdf.interactive import HideAction as InteractiveHideAction
from aspose_pdf.interactive import action_from_spec
from aspose_pdf.outlines import OutlineItem


@pytest.fixture
def document():
    with Document() as doc:
        doc.pages.add(size=(300, 200))
        doc.form.add_push_button("toggle", 0, (10, 10, 110, 35), caption="Toggle")
        yield doc


def _bytes(document):
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _button(document):
    return document.form["toggle"]


def _raw_action(document):
    engine = document._engine_pdf
    _, widgets = engine._field_and_widgets("toggle")
    return engine._resolve(widgets[0].mapping[PdfName("A")])


def _set_raw(document, mapping):
    _, widgets = document._engine_pdf._field_and_widgets("toggle")
    widgets[0].mapping[PdfName("A")] = PdfDictionary(mapping)


@pytest.mark.parametrize("hide", [True, False])
@pytest.mark.parametrize("on_disk", [False, True])
def test_button_authoring_and_all_action_owners_round_trip(
    document, tmp_path, hide, on_disk
):
    names = ["client.Имя", "optional.notes", "future field"]
    action = HideAction(names, hide=hide)
    assert HideAction is InteractiveHideAction
    # Forward references are allowed: a button can precede its target fields.
    button = document.form.add_push_button(
        "author", 0, (130, 10, 230, 35), action=action
    )
    document.form.add_text_field(names[0], 0, (10, 80, 250, 105), value="Alice")
    document.form.add_text_field(names[1], 0, (10, 120, 250, 145))
    button.actions["mouse_up"] = action
    document.pages[0].actions["open"] = action
    document.actions["will_print"] = action
    document.outlines.add(OutlineItem("Toggle details", destination=action))
    document.pages[0].add_link((10, 160, 200, 185), action)
    if on_disk:
        source = tmp_path / "hide.pdf"
        document.save(source)
    else:
        source = _bytes(document)
    with Document(source) as reopened:
        assert reopened.form["author"].actions["A"] == action
        assert reopened.form["author"].actions["mouse_up"] == action
        assert reopened.pages[0].actions["open"] == action
        assert reopened.actions["will_print"] == action
        assert reopened.outlines[0].destination == action
        link = next(
            annot for annot in reopened.pages[0].annotations if annot.subtype == "Link"
        )
        assert link.get_property("A")["T"] == names
        assert link.get_property("A")["H"] is hide
        assert reopened.form[names[0]].value == "Alice"


@pytest.mark.parametrize("names", [["notes"], ("notes", "other"), UserList(["notes"])])
def test_targets_are_pdf_text_strings_and_hide_is_a_boolean(document, names):
    _button(document).actions["A"] = HideAction(names)
    raw = _raw_action(document)
    assert raw.mapping[PdfName("S")] == PdfName("Hide")
    assert isinstance(raw.mapping[PdfName("H")], PdfBoolean)
    assert raw.mapping[PdfName("H")].value is True
    targets = raw.mapping[PdfName("T")]
    assert isinstance(targets, PdfArray)
    assert all(isinstance(target, PdfString) for target in targets.items)
    assert [decode_pdf_text_string(target) for target in targets.items] == list(names)


@pytest.mark.parametrize("hide", [None, True, False])
@pytest.mark.parametrize("shape", ["string", "array", "indirect"])
def test_loaded_scalar_array_and_indirect_targets_and_default_hide(
    document, hide, shape
):
    engine = document._engine_pdf
    target = PdfString("person.Имя")
    if shape == "array":
        target = PdfArray([target])
    elif shape == "indirect":
        target = engine._cos_doc.register_object(
            PdfArray([engine._cos_doc.register_object(target)])
        )
    mapping = {PdfName("S"): PdfName("Hide"), PdfName("T"): target}
    if hide is not None:
        mapping[PdfName("H")] = engine._cos_doc.register_object(PdfBoolean(hide))
    _set_raw(document, mapping)
    expected = HideAction(["person.Имя"], True if hide is None else hide)
    assert _button(document).actions["A"] == expected
    with Document(_bytes(document)) as reopened:
        assert _button(reopened).actions["A"] == expected


@pytest.mark.parametrize(
    "fields", [None, "notes", b"notes", {"notes": 1}, iter(["notes"]), ["notes", 3]]
)
def test_invalid_target_sequences_do_not_replace_an_existing_action(document, fields):
    button = _button(document)
    button.actions["A"] = URIAction("https://example.test")
    with pytest.raises(TypeError, match="sequence of strings"):
        button.actions["A"] = HideAction(fields)
    assert button.actions["A"] == URIAction("https://example.test")


@pytest.mark.parametrize("fields", [[], [""], ["notes", ""]])
def test_empty_targets_are_rejected(document, fields):
    with pytest.raises(ValueError, match="nonempty field names"):
        _button(document).actions["A"] = HideAction(fields)
    assert "A" not in _button(document).actions


@pytest.mark.parametrize("hide", [None, 0, 1, "false", [], {}])
def test_hide_requires_a_real_boolean(document, hide):
    with pytest.raises(TypeError, match="hide must be a boolean"):
        _button(document).actions["A"] = HideAction(["notes"], hide=hide)


@pytest.mark.parametrize(
    "spec",
    [
        {"S": "Hide"},
        {"S": "Hide", "T": None},
        {"S": "Hide", "T": []},
        {"S": "Hide", "T": ["notes", 1]},
        {"S": "Hide", "T": ""},
        {"S": "Hide", "T": ["notes"], "H": 0},
        {"S": "Hide", "T": ["notes"], "H": None},
        {"S": "Hide", "T": ["notes"], "Next": {"S": "Named", "N": "NextPage"}},
    ],
)
def test_unrepresentable_plain_specs_stay_untyped(spec):
    assert action_from_spec(spec) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"T": PdfName("notes")},
        {"T": PdfDictionary({PdfName("Subtype"): PdfName("Widget")})},
        {"T": PdfArray([PdfString("notes"), PdfNumber(1)])},
        {"T": PdfArray([PdfString("notes"), PdfName("other")])},
        {"T": PdfArray([])},
        {"H": PdfNumber(0)},
        {"H": PdfString("false")},
        {"H": PdfNull()},
        {
            "Next": PdfDictionary(
                {PdfName("S"): PdfName("Named"), PdfName("N"): PdfName("NextPage")}
            )
        },
    ],
)
def test_unsupported_loaded_actions_are_preserved_when_other_triggers_change(
    document, changes
):
    mapping = {PdfName("S"): PdfName("Hide"), PdfName("T"): PdfString("notes")}
    mapping.update({PdfName(key): value for key, value in changes.items()})
    _set_raw(document, mapping)
    original = _raw_action(document)
    assert _button(document).actions["A"] is None
    _button(document).actions["mouse_up"] = HideAction(["other"], hide=False)
    assert _raw_action(document) is original
    with Document(_bytes(document)) as reopened:
        assert _button(reopened).actions["A"] is None
        assert _button(reopened).actions["mouse_up"] == HideAction(
            ["other"], hide=False
        )
        assert set(_raw_action(reopened).mapping) == set(original.mapping)


def test_attaching_reading_and_saving_actions_does_not_execute_them(document):
    field = document.form.add_text_field(
        "notes", 0, (10, 80, 250, 105), value="Visible"
    )
    before = document.pages[0].render(antialias=False).pixels
    _button(document).actions["A"] = HideAction(["notes"])
    _button(document).actions["mouse_up"] = HideAction(["notes"], hide=False)
    assert document.pages[0].render(antialias=False).pixels == before
    assert field.value == "Visible"
    with Document(_bytes(document)) as reopened:
        assert _button(reopened).actions["A"] == HideAction(["notes"])
        assert reopened.pages[0].render(antialias=False).pixels == before


@pytest.mark.parametrize("encrypted", [False, True])
def test_streaming_round_trip_is_lazy_and_keeps_action_boolean(
    document, tmp_path, monkeypatch, encrypted
):
    _button(document).actions["A"] = HideAction(["notes"], hide=False)
    if encrypted:
        document.encrypt("secret", "owner")
    path = tmp_path / "form.pdf"
    document.save(path)
    with Document.open_streaming(
        path, password="secret" if encrypted else None
    ) as opened:

        def forbidden(*args, **kwargs):
            pytest.fail("Reading an action must not decode page or appearance streams")

        with monkeypatch.context() as context:
            context.setattr(opened._engine_pdf, "_decode_cos_stream", forbidden)
            assert _button(opened).actions["A"] == HideAction(["notes"], hide=False)
            assert opened._engine_pdf.page_contents == []
        result = _bytes(opened)
    with Document(result, password="secret" if encrypted else None) as reopened:
        assert _button(reopened).actions["A"] == HideAction(["notes"], hide=False)
