"""Public form-value validation, including foreign data and read-only guarantees."""

from __future__ import annotations

import io
from contextlib import contextmanager
from dataclasses import FrozenInstanceError

import pytest

import aspose_pdf
from aspose_pdf import (
    Document,
    FormValidationIssue,
    PdfLoadLimits,
    PdfResourceLimitException,
)
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfBoolean,
    PdfDictionary,
    PdfName,
    PdfNull,
    PdfNumber,
    PdfStream,
    PdfString,
)
from aspose_pdf.exceptions import AsposePdfException, PdfParseException
from aspose_pdf.forms import FormValidationIssue as ModuleIssue

_MISSING = object()
_RECT = (20, 20, 200, 45)


def _field(name="f", *, kind="Tx", value=_MISSING, flags=0, **attributes):
    mapping = {
        PdfName("T"): PdfString(name),
        PdfName("FT"): PdfName(kind),
        PdfName("Ff"): PdfNumber(flags),
    }
    if value is not _MISSING:
        mapping[PdfName("V")] = value
    mapping.update({PdfName(key): item for key, item in attributes.items()})
    return PdfDictionary(mapping)


@contextmanager
def _raw(fields, *, limits=None):
    with Document(limits=limits) as document:
        document.pages.add()
        form = document.form
        engine = document._engine_pdf
        root = engine._resolve(engine._cos_doc.trailer.mapping[PdfName("Root")])
        root.mapping[PdfName("AcroForm")] = PdfDictionary(
            {PdfName("Fields"): PdfArray(fields)}
        )
        yield document, form


def _button(
    *, radio=False, value=_MISSING, required=False, states=("Yes",), **attributes
):
    normal = PdfDictionary({PdfName("Off"): PdfStream(b"")})
    for state in states:
        normal.mapping[PdfName(state)] = PdfStream(b"not decoded")
    return _field(
        kind="Btn",
        value=value,
        flags=(1 << 15 if radio else 0) | (2 if required else 0),
        Subtype=PdfName("Widget"),
        AP=PdfDictionary({PdfName("N"): normal}),
        **attributes,
    )


def _codes(form, **kwargs):
    return [
        (issue.field_name, issue.field_type, issue.code)
        for issue in form.validate(**kwargs)
    ]


def _save(document):
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def test_public_record_and_empty_documents():
    assert ModuleIssue is FormValidationIssue
    assert "FormValidationIssue" in aspose_pdf.__all__
    issue = FormValidationIssue("f", "text", "required", "A value is required")
    assert "required" in repr(issue)
    with pytest.raises(FrozenInstanceError):
        issue.code = "other"
    with Document() as document:
        assert document.form.validate() == []
        document.pages.add()
        assert document.form.validate() == []
        assert document.form.validate([]) == []


@pytest.mark.parametrize(
    "value",
    [_MISSING, PdfNull(), PdfString(""), PdfString(" \t\n"), PdfString("\u00a0")],
)
@pytest.mark.parametrize("required", [False, True])
def test_required_text_and_unicode_whitespace(value, required):
    with _raw([_field(value=value, flags=2 if required else 0)]) as (_, form):
        assert _codes(form) == ([("f", "text", "required")] if required else [])


@pytest.mark.parametrize(
    ("flags", "required"), [(0, False), (1, False), (2, True), (4, False), (7, True)]
)
def test_standard_flags_and_inclusion_of_readonly_noexport(flags, required):
    with _raw([_field(flags=flags)]) as (_, form):
        assert _codes(form) == ([("f", "text", "required")] if required else [])


@pytest.mark.parametrize(
    ("text", "limit", "code"),
    [
        ("ab", 3, None),
        ("abc", 3, None),
        ("abcd", 3, "max_length"),
        ("A😀", 2, None),
        ("A😀B", 2, "max_length"),
        ("e\u0301", 1, "max_length"),
        ("A\nB", 2, "max_length"),
        (" a ", 2, "max_length"),
        ("   ", 2, "max_length"),
    ],
)
def test_length_counts_unmodified_unicode_codepoints(text, limit, code):
    with _raw([_field(value=PdfString(text), MaxLen=PdfNumber(limit))]) as (_, form):
        assert _codes(form) == ([("f", "text", code)] if code else [])


@pytest.mark.parametrize(
    "limit",
    [
        PdfString("2"),
        PdfName("2"),
        PdfBoolean(True),
        PdfNumber(0),
        PdfNumber(-1),
        PdfNumber(1.5),
        PdfNumber(float("nan")),
        PdfNumber(float("inf")),
        PdfNull(),
    ],
)
def test_unusable_length_limits_are_not_repaired(limit):
    field = _field(value=PdfString("long text"), MaxLen=limit)
    with _raw([field]) as (_, form):
        before = repr(field)
        assert form.validate() == []
        assert repr(field) == before


@pytest.mark.parametrize("flags", [1 << 12, 1 << 13, 1 << 24])
def test_length_check_applies_to_multiline_password_and_comb(flags):
    secret = "private-answer"
    with _raw([_field(value=PdfString(secret), flags=flags, MaxLen=PdfNumber(2))]) as (
        _,
        form,
    ):
        [issue] = form.validate()
        assert issue.code == "max_length" and secret not in issue.message


@pytest.mark.parametrize("radio", [False, True])
@pytest.mark.parametrize("state", ["No", "0", "false"])
def test_name_on_states_are_not_false_aliases(radio, state):
    with _raw(
        [_button(radio=radio, value=PdfName(state), required=True, states=(state,))]
    ) as (_, form):
        assert form.validate() == []


@pytest.mark.parametrize("radio", [False, True])
@pytest.mark.parametrize(
    "value", [_MISSING, PdfNull(), PdfName(""), PdfName("Off"), PdfString("Off")]
)
def test_required_unselected_button(radio, value):
    with _raw([_button(radio=radio, value=value, required=True)]) as (_, form):
        assert _codes(form) == [("f", "radio" if radio else "checkbox", "required")]


@pytest.mark.parametrize("alias", ["", "0", "false", "No"])
def test_legacy_checkbox_strings_use_existing_off_convention(alias):
    with _raw(
        [_button(value=PdfString(alias), required=True, states=(alias or "Yes",))]
    ) as (_, form):
        assert _codes(form) == [("f", "checkbox", "required")]


@pytest.mark.parametrize("radio", [False, True])
def test_button_undeclared_state_and_absent_appearance(radio):
    field = _button(radio=radio, value=PdfName("Other"), required=True)
    with _raw([field]) as (_, form):
        kind = "radio" if radio else "checkbox"
        assert _codes(form) == [("f", kind, "invalid_choice")]
        field.mapping.pop(PdfName("AP"))
        assert form.validate() == []
        field.mapping[PdfName("V")] = PdfName("Off")
        assert _codes(form) == [("f", kind, "required")]


def test_radio_uses_state_names_and_down_appearance_fallback():
    field = _button(
        radio=True,
        value=PdfName("a"),
        required=True,
        states=("a",),
        Opt=PdfArray([PdfString("Display")]),
    )
    ap = field.mapping[PdfName("AP")]
    ap.mapping[PdfName("D")] = ap.mapping.pop(PdfName("N"))
    with _raw([field]) as (_, form):
        assert form.validate() == []
        field.mapping[PdfName("V")] = PdfString("Display")
        assert _codes(form) == [("f", "radio", "invalid_choice")]


@pytest.mark.parametrize(
    "kind", ["Tx", "checkbox", "radio", "single", "multi", "combo"]
)
@pytest.mark.parametrize(
    "value",
    [PdfNumber(42), PdfBoolean(False), PdfDictionary({}), PdfStream(b"not text")],
)
def test_raw_invalid_types_are_not_stringified_or_treated_as_empty(kind, value):
    if kind in ("checkbox", "radio"):
        field = _button(radio=kind == "radio", value=value, required=True)
    else:
        flags = 2 | (1 << 21 if kind == "multi" else 1 << 17 if kind == "combo" else 0)
        field = _field(kind="Tx" if kind == "Tx" else "Ch", value=value, flags=flags)
    with _raw([field]) as (_, form):
        assert form.validate()[0].code == "invalid_value"


@pytest.mark.parametrize("value", [PdfArray([]), PdfArray([PdfString("x")])])
@pytest.mark.parametrize("kind", ["Tx", "checkbox", "radio"])
def test_text_and_buttons_reject_even_one_item_arrays(kind, value):
    field = (
        _field(value=value, flags=2)
        if kind == "Tx"
        else _button(radio=kind == "radio", value=value, required=True)
    )
    with _raw([field]) as (_, form):
        assert form.validate()[0].code == "invalid_value"


@pytest.mark.parametrize("combo", [False, True])
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (_MISSING, "required"),
        (PdfNull(), "required"),
        (PdfArray([]), "required"),
        (PdfString("a"), None),
        (PdfName("a"), None),
        (PdfArray([PdfString("a")]), None),
        (PdfArray([PdfString("a"), PdfString("b")]), "invalid_value"),
        (PdfArray([PdfString("a"), PdfNumber(2)]), "invalid_value"),
        (PdfString("Display A"), "invalid_choice"),
        (PdfString("A"), "invalid_choice"),
        (PdfString(" a "), "invalid_choice"),
        (PdfString(""), "invalid_choice"),
    ],
)
def test_single_choice_shape_export_values_and_absence(combo, value, expected):
    options = PdfArray(
        [PdfArray([PdfString("a"), PdfString("Display A")]), PdfString("b")]
    )
    with _raw(
        [
            _field(
                kind="Ch", flags=2 | (1 << 17 if combo else 0), value=value, Opt=options
            )
        ]
    ) as (_, form):
        assert [issue.code for issue in form.validate()] == (
            [expected] if expected else []
        )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (_MISSING, "required"),
        (PdfArray([]), "required"),
        (PdfNull(), "required"),
        (PdfString("a"), None),
        (PdfArray([PdfName("a")]), None),
        (PdfArray([PdfString("a"), PdfString("b")]), None),
        (PdfArray([PdfString("unknown"), PdfString("also-unknown")]), "invalid_choice"),
        (PdfArray([PdfString("unknown"), PdfString("unknown")]), "invalid_value"),
        (PdfArray([PdfString("a"), PdfBoolean(True)]), "invalid_value"),
    ],
)
def test_multiselect_preserves_raw_array_entries(value, expected):
    with _raw(
        [
            _field(
                kind="Ch",
                flags=2 | 1 << 21,
                value=value,
                Opt=PdfArray([PdfString("a"), PdfString("b")]),
            )
        ]
    ) as (_, form):
        assert [issue.code for issue in form.validate()] == (
            [expected] if expected else []
        )


@pytest.mark.parametrize("multiple", [False, True])
@pytest.mark.parametrize("required", [False, True])
def test_missing_options_do_not_make_present_selections_valid(multiple, required):
    field = _field(
        kind="Ch",
        flags=(1 << 21 if multiple else 0) | (2 if required else 0),
        value=PdfArray([PdfString("a")]),
    )
    with _raw([field]) as (_, form):
        assert form.validate()[0].code == "invalid_choice"
        field.mapping[PdfName("V")] = PdfArray([])
        assert [issue.code for issue in form.validate()] == (
            ["required"] if required else []
        )


@pytest.mark.parametrize("editable", [False, True])
@pytest.mark.parametrize("multiple", [False, True])
def test_explicit_empty_export_is_a_valid_required_selection(editable, multiple):
    flags = 2 | (1 << 21 if multiple else 1 << 17) | (1 << 18 if editable else 0)
    with _raw(
        [
            _field(
                kind="Ch",
                flags=flags,
                value=PdfString(""),
                Opt=PdfArray([PdfString("")]),
            )
        ]
    ) as (_, form):
        assert form.validate() == []


@pytest.mark.parametrize("value", ["", " \t\n", "\u00a0", "custom answer", "a"])
@pytest.mark.parametrize("required", [False, True])
def test_editable_combo_accepts_custom_answers_and_checks_required_blanks(
    value, required
):
    field = _field(
        kind="Ch",
        flags=(1 << 17) | (1 << 18) | (2 if required else 0),
        value=PdfString(value),
        Opt=PdfArray([PdfString("a")]),
        MaxLen=PdfNumber(1),
    )
    with _raw([field]) as (_, form):
        assert [issue.code for issue in form.validate()] == (
            ["required"] if required and not value.strip() else []
        )


@pytest.mark.parametrize("strict", [0, 1, None, "yes"])
def test_strict_requires_actual_boolean(strict):
    with Document() as document:
        with pytest.raises(TypeError, match="strict"):
            document.form.validate([], strict=strict)


@pytest.mark.parametrize(
    "fields", ["f", b"f", bytearray(b"f"), {"f": 1}, 42, True, [1], ["f", None]]
)
def test_selection_rejects_wrong_types(fields):
    with _raw([_field(flags=2)]) as (_, form):
        with pytest.raises(TypeError):
            form.validate(fields)


def test_selection_order_deduplication_generators_and_unknown_names():
    with _raw([_field("first", flags=2), _field("second", flags=2)]) as (_, form):
        assert [x.field_name for x in form.validate()] == ["first", "second"]
        assert [
            x.field_name
            for x in form.validate(fields=(n for n in ["second", "second", "first"]))
        ] == ["second", "first"]
        assert form.validate(iter(())) == []
        with pytest.raises(KeyError):
            form.validate(["first", "unknown"])
        assert [
            x.field_name for x in form.validate(["unknown", "first"], strict=False)
        ] == ["first"]


def test_complete_selection_checked_before_value_checks(monkeypatch):
    from aspose_pdf.engine import form_validation

    with _raw([_field(flags=2)]) as (_, form):

        def forbidden(*args):
            pytest.fail("A value was checked before selection validation completed")

        monkeypatch.setattr(form_validation, "_check", forbidden)
        with pytest.raises(KeyError):
            form.validate(n for n in ["f", "unknown"])

        def broken():
            yield "f"
            raise RuntimeError("generator failed")

        with pytest.raises(RuntimeError, match="generator failed"):
            form.validate(broken())


def test_disposal_and_skipped_types_obey_public_contract():
    with Document() as document:
        page = document.pages.add()
        form = document.form
        form.add_push_button("button", page, _RECT, required=True)
        form.add_signature_field("signature", page, (20, 60, 200, 85))
        assert form.validate(["button", "signature"]) == []
        with pytest.raises(KeyError):
            form.validate(["missing"])
    for fields in (None, []):
        with pytest.raises(AsposePdfException, match="disposed"):
            form.validate(fields)


@pytest.mark.parametrize("on_disk", [False, True])
def test_public_authoring_filling_and_validation_roundtrip(tmp_path, on_disk):
    with Document() as document:
        page = document.pages.add()
        form = document.form
        field = form.add_text_field("person.name", page, _RECT, required=True)
        field.max_length = 3
        form.add_checkbox("agree", page, (20, 60, 40, 80), required=True)
        form.add_radio_group("pick", page, [("a", (20, 100, 40, 120))], required=True)
        form.add_list_box(
            "multi",
            page,
            (20, 150, 200, 200),
            ["a", "b"],
            required=True,
            multiselect=True,
        )
        assert _codes(form) == [
            ("person.name", "text", "required"),
            ("agree", "checkbox", "required"),
            ("pick", "radio", "required"),
            ("multi", "listbox", "required"),
        ]
        form.fill(
            {
                "person.name": "longer than allowed",
                "agree": True,
                "pick": "a",
                "multi": ["a"],
            }
        )
        assert _codes(form) == [("person.name", "text", "max_length")]
        assert field.value == "longer than allowed"
        destination = tmp_path / "form.pdf" if on_disk else io.BytesIO()
        document.save(destination)
        source = destination if on_disk else destination.getvalue()
        with Document(source) as reopened:
            assert _codes(reopened.form) == [("person.name", "text", "max_length")]
            reopened.form.fill({"person.name": "Ada"})
            assert reopened.form.validate() == []


def test_inheritance_null_override_defaults_and_namespace_nodes():
    parent = _field(
        "group",
        flags=2 | 1 | 4,
        value=PdfString("long"),
        MaxLen=PdfNumber(3),
        DV=PdfString("default"),
    )
    transparent = PdfDictionary({PdfName("Parent"): parent})
    child = PdfDictionary(
        {PdfName("T"): PdfString("child"), PdfName("Parent"): transparent}
    )
    sibling = PdfDictionary(
        {
            PdfName("T"): PdfString("sibling"),
            PdfName("Parent"): parent,
            PdfName("V"): PdfNull(),
        }
    )
    transparent.mapping[PdfName("Kids")] = PdfArray([child])
    parent.mapping[PdfName("Kids")] = PdfArray([transparent, sibling])
    with _raw([parent]) as (_, form):
        assert _codes(form) == [
            ("group.child", "text", "max_length"),
            ("group.sibling", "text", "required"),
        ]
        with pytest.raises(KeyError):
            form.validate(["group"])
        child.mapping[PdfName("V")] = PdfString("OK")
        child.mapping[PdfName("Ff")] = PdfNumber(0)
        child.mapping[PdfName("MaxLen")] = PdfNumber(1)
        assert _codes(form) == [
            ("group.child", "text", "max_length"),
            ("group.sibling", "text", "required"),
        ]
        assert parent.mapping[PdfName("Ff")].value == 7


def test_inherited_choices_indirect_values_and_multiple_widgets_report_once():
    parent = _field(
        "group",
        kind="Ch",
        flags=2 | 1 << 21,
        Opt=PdfArray([PdfString("a")]),
        value=PdfString("a"),
    )
    child = PdfDictionary({PdfName("T"): PdfString("child"), PdfName("Parent"): parent})
    parent.mapping[PdfName("Kids")] = PdfArray([child])
    radio = _field("radio", kind="Btn", flags=2 | 1 << 15)
    radio.mapping[PdfName("Kids")] = PdfArray(
        [
            PdfDictionary(
                {PdfName("Subtype"): PdfName("Widget"), PdfName("Parent"): radio}
            )
            for _ in range(2)
        ]
    )
    with _raw([parent, radio]) as (document, form):
        child.mapping[PdfName("V")] = document._engine_pdf._cos_doc.register_object(
            PdfString("a")
        )
        assert _codes(form) == [("radio", "radio", "required")]


def test_parent_inheritance_beyond_legacy_64_node_cutoff():
    ancestor = PdfDictionary(
        {PdfName("FT"): PdfName("Tx"), PdfName("Ff"): PdfNumber(2)}
    )
    for _ in range(70):
        ancestor = PdfDictionary({PdfName("Parent"): ancestor})
    ancestor.mapping[PdfName("T")] = PdfString("deep")
    with _raw([ancestor]) as (_, form):
        assert _codes(form) == [("deep", "text", "required")]


def test_issue_precedence():
    with _raw(
        [
            _field("wrong-type", value=PdfNumber(42), flags=2),
            _field("blank", value=PdfString("    "), flags=2, MaxLen=PdfNumber(2)),
            _field(
                "duplicate",
                kind="Ch",
                value=PdfArray([PdfString("bad"), PdfString("bad")]),
                flags=2 | 1 << 21,
            ),
        ]
    ) as (_, form):
        assert [(x.field_name, x.code) for x in form.validate()] == [
            ("wrong-type", "invalid_value"),
            ("blank", "required"),
            ("duplicate", "invalid_value"),
        ]


def test_validation_snapshots_and_retained_handles_follow_lifecycle():
    with Document() as document:
        page = document.pages.add()
        form = document.form
        field = form.add_text_field("f", page, _RECT, required=True)
        [old_issue] = form.validate()
        form.fill({"f": "OK"})
        assert form.validate() == [] and form["f"] is field
        field.default_value = ""
        form.reset()
        assert _codes(form) == [("f", "text", "required")]
        form.import_json(b'{"f":"new"}')
        assert form.validate() == [] and old_issue.code == "required"
        form.remove_field("f")
        assert form.validate() == []
        with Document() as source:
            source.pages.add()
            source.form.add_text_field("imported", 0, _RECT, required=True)
            document.merge(source)
        assert _codes(form) == [("imported", "text", "required")]
        document.flatten()
        assert form.validate() == []


def test_read_only_pdf_and_unchanged_incremental_save(monkeypatch):
    with Document() as document:
        document.pages.add()
        document.form.add_text_field("f", 0, _RECT, required=True)
        data = _save(document)
    with Document(data) as document:
        form = document.form
        engine = document._engine_pdf
        before = io.BytesIO()
        document.save(before, incremental=True)
        assert before.getvalue() == data
        keys = set(engine._cos_doc.objects)
        snapshot = repr(engine._cos_doc.objects)

        def forbidden(*args, **kwargs):
            pytest.fail("Validation attempted to change values or appearances")

        for name in (
            "set_field_value",
            "generate_field_appearances",
            "generate_appearances",
            "_set_page_content",
        ):
            monkeypatch.setattr(engine, name, forbidden)
        assert _codes(form) == [("f", "text", "required")]
        with pytest.raises(KeyError):
            form.validate(["missing"])
        assert set(engine._cos_doc.objects) == keys
        assert repr(engine._cos_doc.objects) == snapshot
        after = io.BytesIO()
        document.save(after, incremental=True)
        assert after.getvalue() == data


@pytest.mark.parametrize("filled", [False, True])
def test_streaming_validation_decodes_no_streams(tmp_path, monkeypatch, filled):
    from aspose_pdf.engine.simple_pdf import CosExtractor

    with Document() as document:
        for _ in range(2):
            document.pages.add().add_text("Page content", 20, 700)
        document.form.add_text_field(
            "f", 0, _RECT, required=True, value="OK" if filled else ""
        )
        document.form.add_checkbox("box", 1, (20, 20, 40, 40), checked=True)
        source = tmp_path / "form.pdf"
        document.save(source)
    with Document.open_streaming(source) as document:
        form = document.form

        def forbidden(*args, **kwargs):
            pytest.fail(
                "Validation read a drawing/appearance stream or rendered a page"
            )

        monkeypatch.setattr(document._engine_pdf, "get_page_content", forbidden)
        monkeypatch.setattr(document._engine_pdf, "_decode_cos_stream", forbidden)
        monkeypatch.setattr(CosExtractor, "_decode_stream", forbidden)
        monkeypatch.setattr(document, "render_page", forbidden)
        assert len(form.validate()) == (0 if filled else 1)
        assert document._engine_pdf.page_contents == []


@pytest.mark.parametrize(
    "names", [lambda: ["unknown"] * 3, lambda: (f"unknown{i}" for i in range(3))]
)
def test_requested_names_count_unknowns_and_duplicates_before_materialization(names):
    with Document(limits=PdfLoadLimits(max_container_items=2)) as document:
        with pytest.raises(PdfResourceLimitException, match="requested names"):
            document.form.validate(names(), strict=False)


@pytest.mark.parametrize("context", ["fields", "options", "values", "pair", "states"])
def test_metadata_container_limits(context):
    field = _field(kind="Ch", value=PdfString("a"), flags=1 << 21)
    fields = [field]
    if context == "fields":
        fields = [_field(str(i)) for i in range(3)]
    elif context == "options":
        field.mapping[PdfName("Opt")] = PdfArray([PdfString("a")] * 3)
    elif context == "values":
        field.mapping[PdfName("V")] = PdfArray([PdfString("a")] * 3)
    elif context == "pair":
        field.mapping[PdfName("Opt")] = PdfArray([PdfArray([PdfString("a")] * 3)])
    else:
        fields = [_button(value=PdfName("a"), states=("a", "b"))]
    with _raw(fields, limits=PdfLoadLimits(max_container_items=2)) as (_, form):
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            form.validate()


@pytest.mark.parametrize("graph", ["parent", "tree", "repeated", "duplicate-name"])
def test_bad_graphs_raise_instead_of_reporting_success(graph):
    field = _field(value=PdfString("OK"))
    fields = [field]
    if graph == "parent":
        field.mapping[PdfName("Parent")] = field
    elif graph == "tree":
        field.mapping[PdfName("Kids")] = PdfArray([field])
    elif graph == "repeated":
        fields.append(field)
    else:
        fields.append(_field())
    with _raw(fields) as (_, form):
        with pytest.raises(PdfParseException):
            form.validate()


@pytest.mark.parametrize("parents_only", [False, True])
def test_depth_limit_covers_tree_and_parent_chains(parents_only):
    root = _field("root", flags=2)
    child = PdfDictionary({PdfName("T"): PdfString("child"), PdfName("Parent"): root})
    leaf = PdfDictionary({PdfName("T"): PdfString("leaf"), PdfName("Parent"): child})
    if not parents_only:
        root.mapping[PdfName("Kids")] = PdfArray([child])
        child.mapping[PdfName("Kids")] = PdfArray([leaf])
    with _raw(
        [leaf if parents_only else root], limits=PdfLoadLimits(max_nesting_depth=2)
    ) as (_, form):
        with pytest.raises(PdfResourceLimitException, match="max_nesting_depth"):
            form.validate()


def test_resource_exception_from_decoder_is_not_converted_to_an_issue(monkeypatch):
    from aspose_pdf.engine import form_validation

    with _raw([_field(value=PdfString("OK"))]) as (_, form):
        original = form_validation.decode_pdf_text_string

        def fail_value(value):
            if value.value == b"OK":
                raise PdfResourceLimitException("decoder budget")
            return original(value)

        monkeypatch.setattr(form_validation, "decode_pdf_text_string", fail_value)
        with pytest.raises(PdfResourceLimitException, match="decoder budget"):
            form.validate()


def test_large_valid_field_tree_has_no_silent_4096_node_cutoff():
    fields = [_field(f"f{i}", value=PdfString("OK")) for i in range(4200)]
    fields[-1].mapping[PdfName("V")] = PdfNumber(42)
    with _raw(fields) as (_, form):
        assert _codes(form) == [("f4199", "text", "invalid_value")]
        assert _codes(form, fields=["f4199"]) == [("f4199", "text", "invalid_value")]
