"""Alignment edits update existing text and choice widgets without refilling them."""

from __future__ import annotations

import io
import re

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfIndirectReference,
    PdfName,
    PdfNumber,
    PdfString,
)
from aspose_pdf.engine.form_fields import field_dictionary, widget_dictionaries
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfValidationException,
    UnsupportedFeatureException,
)


@pytest.fixture
def document():
    with Document() as result:
        result.pages.add(size=(300, 200))
        yield result


def _field(document, kind="text", name="field", **options):
    if kind == "text":
        return document.form.add_text_field(
            name, 0, (20, 100, 220, 160), value="1234", **options
        )
    factory = (
        document.form.add_combo_box
        if kind == "combobox"
        else document.form.add_list_box
    )
    return factory(
        name,
        0,
        (20, 100, 220, 160),
        [("a", "Alpha"), ("b", "Beta")],
        value="b",
        **options,
    )


def _saved(document, tmp_path, on_disk=False, name="form.pdf"):
    if on_disk:
        path = tmp_path / name
        document.save(path)
        return path
    with io.BytesIO() as stream:
        document.save(stream)
        return stream.getvalue()


def _appearance(document, page=0, widget=0):
    return document.pages[page].annotations[widget].appearance_normal


def _x_positions(appearance):
    return [
        float(number)
        for number in re.findall(rb"1 0 0 1 ([\d.-]+) [\d.-]+ Tm", appearance)
    ]


def _acro(document):
    engine = document._engine_pdf
    root = engine._resolve(engine._cos_doc.trailer.mapping[PdfName("Root")])
    return engine._resolve(root.mapping[PdfName("AcroForm")])


@pytest.mark.parametrize("kind", ["text", "listbox", "combobox"])
@pytest.mark.parametrize("alignment", ["left", "center", "right"])
@pytest.mark.parametrize("on_disk", [False, True])
def test_reopened_fields_redraw_and_keep_values_after_alignment_edits(
    document, tmp_path, kind, alignment, on_disk
):
    field = _field(document, kind, alignment="right" if alignment == "left" else "left")
    field.required = field.read_only = True
    before = field.value, field.default_value, field.flags, field.options, field.rect
    with Document(_saved(document, tmp_path, on_disk)) as reopened:
        edited = reopened.form["field"]
        old_appearance = _appearance(reopened)
        edited.alignment = alignment
        appearance = _appearance(reopened)
        assert appearance != old_appearance
        assert edited.alignment == alignment
        assert (
            edited.value,
            edited.default_value,
            edited.flags,
            edited.options,
            edited.rect,
        ) == before
        assert reopened.form["field"] is edited
        with Document(_saved(reopened, tmp_path, on_disk, "aligned.pdf")) as saved:
            assert saved.form["field"].alignment == alignment
            assert _appearance(saved) == appearance
            saved.form.reset()
            assert saved.form["field"].alignment == alignment
            assert saved.form["field"].value == before[1]


@pytest.mark.parametrize(
    "value,expected",
    [
        ("LEFT", "left"),
        ("Centre", "center"),
        ("CENTER", "center"),
        ("RIGHT", "right"),
        (0, "left"),
        (1, "center"),
        (2, "right"),
    ],
)
def test_alignment_accepts_factory_names_aliases_and_numeric_codes(
    document, value, expected
):
    field = _field(document)
    field.alignment = value
    assert field.alignment == expected
    with Document() as authored:
        authored.pages.add(size=(300, 200))
        _field(authored, alignment=value)
        assert _appearance(document) == _appearance(authored)
        assert (
            document.pages[0].render(antialias=False).pixels
            == authored.pages[0].render(antialias=False).pixels
        )


@pytest.mark.parametrize("mode", ["multiline", "password", "comb"])
def test_alignment_moves_existing_layout_without_changing_text_mode(document, mode):
    field = _field(document)
    if mode == "comb":
        field.max_length = 6
    setattr(field, mode, True)
    if mode == "multiline":
        field.value = "first\nsecond"
    value, flags = field.value, field.flags
    left = _x_positions(_appearance(document))
    field.alignment = "center"
    center = _x_positions(_appearance(document))
    field.alignment = "right"
    right = _x_positions(_appearance(document))
    assert len(left) == len(center) == len(right)
    assert all(a < b < c for a, b, c in zip(left, center, right, strict=True))
    assert (field.value, field.flags) == (value, flags)
    if mode == "password":
        assert b"1234" not in _appearance(document)
        assert b"(****) Tj" in _appearance(document)


def test_rich_text_paragraph_styles_override_the_edited_default(document):
    field = _field(document)
    dictionary = field_dictionary(document._engine_pdf, field.name)
    dictionary.mapping[PdfName("Ff")] = PdfNumber(1 << 25)
    markup = PdfString(
        '<body><p>Default</p><p style="text-align:left">Pinned</p></body>'
    )
    dictionary.mapping[PdfName("RV")] = markup
    document.form.generate_appearances()
    before = _x_positions(_appearance(document))
    field.alignment = "right"
    after = _x_positions(_appearance(document))
    assert len(before) == len(after) == 2
    assert after[0] > before[0]
    assert after[1] == before[1]
    assert dictionary.mapping[PdfName("RV")] is markup
    assert field.value == "1234"


def test_alignment_preserves_multiple_choice_selections_and_defaults(document):
    field = _field(document, "listbox")
    field.multi_select = True
    field.value = ["a", "b"]
    field.default_value = ["a"]
    before = field.options, field.flags, field.default_value
    field.alignment = "right"
    assert field.value == ["a", "b"]
    assert (field.options, field.flags, field.default_value) == before
    assert _appearance(document).count(b"0.153 0.447 0.816 rg") == 2
    document.form.reset()
    assert document.form.to_dict()["field"] == ["a"] and field.alignment == "right"


def test_clearing_alignment_restores_parent_then_form_defaults_without_editing_siblings(
    document, tmp_path
):
    first = _field(document, name="group.first")
    second = _field(document, name="group.second")
    engine = document._engine_pdf
    child = field_dictionary(engine, first.name)
    sibling = field_dictionary(engine, second.name)
    parent = engine._resolve(child.mapping[PdfName("Parent")])
    parent.mapping[PdfName("Q")] = engine._cos_doc.register_object(PdfNumber(1))
    _acro(document).mapping[PdfName("Q")] = PdfNumber(2)
    for dictionary in (child, sibling):
        dictionary.mapping.pop(PdfName("Q"))
    document.form.generate_appearances()
    assert first.alignment == second.alignment == "center"
    sibling_appearance = _appearance(document, widget=1)
    center = _appearance(document)
    first.alignment = "right"
    assert first.alignment == "right" and second.alignment == "center"
    first.alignment = None
    assert first.alignment == "center" and _appearance(document) == center
    assert _appearance(document, widget=1) == sibling_appearance
    assert PdfName("Q") not in child.mapping
    parent.mapping.pop(PdfName("Q"))
    first.alignment = None
    assert first.alignment == second.alignment == "right"
    right = _appearance(document)
    document.form.generate_appearances()
    assert _appearance(document) == right
    first.value = "1234"
    assert _appearance(document) == right
    with Document(_saved(document, tmp_path)) as reopened:
        assert reopened.form["group.first"].alignment == "right"
        assert _appearance(reopened) == right
    _acro(document).mapping.pop(PdfName("Q"))
    first.alignment = None
    assert first.alignment == "left"
    assert _x_positions(_appearance(document)) == [2]


def _second_widget(document, name):
    """Construct an existing multi-widget field, which the factories do not author."""
    document.pages.add(size=(300, 200))
    engine = document._engine_pdf
    field = field_dictionary(engine, name)
    first = widget_dictionaries(engine, field)[0]
    second = PdfDictionary(dict(first.mapping))
    second.mapping[PdfName("P")] = PdfIndirectReference(engine._page_obj_ids[1], 0)
    reference = engine._cos_doc.register_object(second)
    engine._resolve(field.mapping[PdfName("Kids")]).items.append(reference)
    engine._get_page_dict(1).mapping[PdfName("Annots")] = PdfArray([reference])
    return first, second


def test_every_widget_is_redrawn_and_local_widget_overrides_are_cleared(
    document, tmp_path
):
    field = _field(document)
    widgets = _second_widget(document, field.name)
    widgets[0].mapping[PdfName("Q")] = PdfNumber(1)
    widgets[1].mapping[PdfName("Q")] = PdfNumber(2)
    document.form.generate_appearances()
    assert _appearance(document, page=0) != _appearance(document, page=1)
    field.alignment = "right"
    assert _appearance(document, page=0) == _appearance(document, page=1)
    assert all(PdfName("Q") not in widget.mapping for widget in widgets)
    field.alignment = None
    assert field.alignment == "left"
    assert _x_positions(_appearance(document, page=0)) == [2]
    assert _appearance(document, page=0) == _appearance(document, page=1)
    with Document(_saved(document, tmp_path)) as reopened:
        assert len(reopened.form["field"].widgets) == 2
        assert _appearance(reopened, page=0) == _appearance(reopened, page=1)


@pytest.mark.parametrize("kind", ["text", "listbox", "combobox"])
def test_alignment_changes_are_visible_and_survive_flattening(document, kind):
    field = _field(document, kind)
    before = document.pages[0].render(antialias=False).pixels
    field.alignment = "right"
    edited = document.pages[0].render(antialias=False).pixels
    assert before != edited
    document.form.flatten()
    assert document.pages[0].render(antialias=False).pixels == edited


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        -1,
        3,
        1.5,
        float("nan"),
        float("inf"),
        "justify",
        "1",
        "",
        b"right",
        [],
        {},
    ],
)
def test_invalid_alignment_leaves_settings_values_and_appearances_unchanged(
    document, value
):
    field = _field(document, alignment="center")
    before = field.alignment, field.value, field.default_value, _appearance(document)
    with pytest.raises(PdfValidationException, match="alignment"):
        field.alignment = value
    assert (
        field.alignment,
        field.value,
        field.default_value,
        _appearance(document),
    ) == before


@pytest.mark.parametrize("kind", ["checkbox", "radio", "pushbutton", "signature"])
def test_nontext_fields_do_not_offer_alignment(document, kind):
    if kind == "radio":
        field = document.form.add_radio_group("field", 0, [("A", (20, 20, 40, 40))])
    else:
        factory = {
            "checkbox": document.form.add_checkbox,
            "pushbutton": document.form.add_push_button,
            "signature": document.form.add_signature_field,
        }[kind]
        field = factory("field", 0, (20, 20, 80, 50))
    assert field.alignment is None
    with pytest.raises(PdfValidationException, match="applies only"):
        field.alignment = "right"


@pytest.mark.parametrize("widget_font", [False, True])
@pytest.mark.parametrize("kind", ["text", "listbox", "combobox"])
def test_unrebuildable_cid_appearances_are_rejected_before_any_widget_changes(
    document, widget_font, kind
):
    field = _field(document, kind)
    widgets = _second_widget(document, field.name)
    engine = document._engine_pdf
    fonts = engine._resolve(
        engine._resolve(_acro(document).mapping[PdfName("DR")]).mapping[PdfName("Font")]
    )
    fonts.mapping[PdfName("CID")] = PdfDictionary(
        {PdfName("Subtype"): PdfName("Type0")}
    )
    target = widgets[-1] if widget_font else field_dictionary(engine, field.name)
    target.mapping[PdfName("DA")] = PdfString("/CID 12 Tf 0 g")
    before = [_appearance(document, page=index) for index in range(2)]
    with pytest.raises(UnsupportedFeatureException, match="embedded CID"):
        field.alignment = "right"
    assert field.alignment == "left"
    assert [_appearance(document, page=index) for index in range(2)] == before
    field.alignment = "left"
    assert [_appearance(document, page=index) for index in range(2)] == before


def test_streaming_encrypted_forms_edit_without_reading_page_content(
    document, tmp_path, monkeypatch
):
    _field(document)
    document.pages.add().add_text("Unrelated page", 20, 20)
    document.encrypt("user", "owner")
    source = _saved(document, tmp_path, True)
    original = source.read_bytes()
    with Document.open_streaming(source, password="user") as opened:
        field = opened.form["field"]

        def no_content(*args, **kwargs):
            raise AssertionError("Alignment edits must not load page content")

        with monkeypatch.context() as patch:
            patch.setattr(opened._engine_pdf, "get_page_content", no_content)
            field.alignment = "right"
            assert field.alignment == "right" and field.value == "1234"
        destination = tmp_path / "aligned.pdf"
        opened.save(destination)
    with Document(destination, password="user") as reopened:
        assert reopened.form["field"].alignment == "right"
        assert reopened.form["field"].value == "1234"
    assert source.read_bytes() == original


def test_removed_and_disposed_fields_reject_alignment_reads_and_edits(document):
    field = _field(document)
    field.remove()
    replacement = _field(document)
    for operation in (
        lambda: field.alignment,
        lambda: setattr(field, "alignment", "right"),
    ):
        with pytest.raises(KeyError, match="no longer"):
            operation()
    document.dispose()
    for operation in (
        lambda: replacement.alignment,
        lambda: setattr(replacement, "alignment", None),
    ):
        with pytest.raises(AsposePdfException, match="disposed"):
            operation()
