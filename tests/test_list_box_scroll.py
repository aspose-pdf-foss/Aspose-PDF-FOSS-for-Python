"""List-box scroll positions update appearances without changing selections."""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName, PdfNumber, PdfString
from aspose_pdf.engine.form_fields import field_dictionary, widget_dictionaries
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfValidationException,
    UnsupportedFeatureException,
)

OPTIONS = [(f"v{number}", f"Item {number}") for number in range(8)]


def _bytes(document, *, incremental=None):
    with io.BytesIO() as output:
        document.save(output, incremental=incremental)
        return output.getvalue()


def _list(document, *, name="choice", top_index=0, value=None, multiselect=False):
    return document.form.add_list_box(
        name,
        1,
        (20, 40, 220, 72),
        OPTIONS,
        value=value,
        multiselect=multiselect,
        top_index=top_index,
        font_size=10,
    )


@pytest.fixture
def document():
    with Document() as source:
        source.pages.add(size=(240, 160))
        yield source


def _appearance(document, index=0):
    return document.pages[1].annotations[index].appearance_normal


def _visible(content, *numbers):
    for number in range(8):
        assert (f"(Item {number}) Tj".encode() in content) is (number in numbers)


def test_creation_and_live_edits_update_visible_rows_without_selecting_them(document):
    field = _list(document, top_index=3, value=["v1", "v4"], multiselect=True)
    assert field.top_index == 3 and field.value == ["v1", "v4"]
    _visible(_appearance(document), 3, 4)
    assert _appearance(document).count(b"0.153 0.447 0.816 rg") == 1
    baseline = document.pages[1].render(antialias=False).pixels
    default = field.default_value
    dictionary = field_dictionary(document._engine_pdf, field.name)
    assert [item.value for item in dictionary.mapping[PdfName("I")].items] == [1, 4]
    field.top_index = 0
    _visible(_appearance(document), 0, 1)
    assert document.pages[1].render(antialias=False).pixels != baseline
    assert document.form["choice"] is field
    assert field.value == ["v1", "v4"] and field.default_value == default
    assert [item.value for item in dictionary.mapping[PdfName("I")].items] == [1, 4]
    field.top_index = 7
    _visible(_appearance(document), 7)
    assert field.options == OPTIONS and field.multi_select


def test_default_missing_and_cleared_scroll_position_start_at_zero(document):
    field = _list(document)
    assert field.top_index == 0
    dictionary = field_dictionary(document._engine_pdf, field.name)
    dictionary.mapping.pop(PdfName("TI"))
    assert field.top_index == 0
    field.top_index = 4
    field.top_index = None
    assert field.top_index == 0 and PdfName("TI") not in dictionary.mapping
    _visible(_appearance(document), 0, 1)


@pytest.mark.parametrize("on_disk", [False, True])
def test_scroll_position_and_appearance_survive_pdf_round_trips(
    document, on_disk, tmp_path
):
    field = _list(document, value="v0")
    field.top_index = 5
    expected = _appearance(document)
    if on_disk:
        source = tmp_path / "list.pdf"
        document.save(source)
    else:
        source = _bytes(document)
    with Document(source) as reopened:
        saved = reopened.form["choice"]
        assert saved.top_index == 5 and saved.value == "v0"
        assert _appearance(reopened) == expected
        reopened.form.generate_appearances()
        assert _appearance(reopened) == expected
        saved.top_index = 2
        _visible(_appearance(reopened), 2, 3)


def test_value_changes_resets_and_flattening_keep_the_scroll_position(document):
    field = _list(document, value="v1", top_index=4)
    field.value = "v4"
    _visible(_appearance(document), 4, 5)
    assert field.top_index == 4 and b"0.153 0.447 0.816 rg" in _appearance(document)
    document.form.reset()
    assert field.value == "v1" and field.top_index == 4
    _visible(_appearance(document), 4, 5)
    before = document.pages[1].render(antialias=False).pixels
    document.form.flatten()
    assert document.pages[1].render(antialias=False).pixels == before
    assert not document.form.fields


def test_shorter_options_clamp_the_scroll_position_and_preserve_valid_selections(
    document,
):
    field = _list(document, value="v0", top_index=7)
    field.options = OPTIONS[:2]
    assert field.top_index == 1 and field.value == "v0" and field.default_value == "v0"
    _visible(_appearance(document), 1)
    field.options = OPTIONS
    assert field.top_index == 1
    _visible(_appearance(document), 1, 2)
    before = _bytes(document)
    with pytest.raises(PdfValidationException):
        field.options = OPTIONS[2:]
    assert _bytes(document) == before


def test_nearest_field_scroll_override_is_local_and_reaches_every_widget(document):
    first = _list(document, name="group.first")
    second = document.form.add_list_box(
        "group.second", 1, (20, 90, 220, 122), OPTIONS, font_size=10
    )
    engine = document._engine_pdf
    dictionary = field_dictionary(engine, first.name)
    parent = engine._resolve(dictionary.mapping[PdfName("Parent")])
    parent.mapping[PdfName("TI")] = PdfNumber(2)
    dictionary.mapping.pop(PdfName("TI"))
    assert first.top_index == 2 and second.top_index == 0
    document.form.generate_appearances()
    _visible(_appearance(document), 2, 3)
    widget = widget_dictionaries(engine, dictionary)[0]
    widget.mapping[PdfName("TI")] = PdfNumber(6)
    sibling = _appearance(document, 1)
    first.top_index = 4
    assert parent.mapping[PdfName("TI")].value == 2
    assert PdfName("TI") not in widget.mapping
    _visible(_appearance(document), 4, 5)
    assert second.top_index == 0 and _appearance(document, 1) == sibling
    first.top_index = None
    assert first.top_index == 2
    _visible(_appearance(document), 2, 3)


def test_empty_foreign_option_list_accepts_only_zero(document):
    field = _list(document)
    dictionary = field_dictionary(document._engine_pdf, field.name)
    dictionary.mapping[PdfName("Opt")] = PdfArray([])
    assert field.top_index == 0
    field.top_index = 0
    with pytest.raises(PdfValidationException):
        field.top_index = 1


@pytest.mark.parametrize(
    "value,error",
    [
        (True, TypeError),
        (False, TypeError),
        (1.0, TypeError),
        ("1", TypeError),
        ([], TypeError),
        (-1, PdfValidationException),
        (8, PdfValidationException),
        (10**1000, PdfValidationException),
    ],
)
def test_invalid_edits_and_creation_fail_without_changes(document, value, error):
    field = _list(document, top_index=3, value="v4")
    before = _bytes(document)
    with pytest.raises(error):
        field.top_index = value
    assert _bytes(document) == before and field.top_index == 3
    with pytest.raises(error):
        _list(document, name="bad", top_index=value)
    assert _bytes(document) == before and "bad" not in [
        field.name for field in document.form.fields
    ]


@pytest.mark.parametrize("kind", ["text", "checkbox", "combobox"])
def test_other_field_types_have_no_scroll_position_and_reject_edits(document, kind):
    if kind == "text":
        field = document.form.add_text_field("other", 1, (20, 40, 220, 72))
    elif kind == "checkbox":
        field = document.form.add_checkbox("other", 1, (20, 40, 40, 60))
    else:
        field = document.form.add_combo_box("other", 1, (20, 40, 220, 72), ["a", "b"])
    assert field.top_index is None
    before = _bytes(document)
    for value in (0, None):
        with pytest.raises(PdfValidationException, match="listbox"):
            field.top_index = value
    assert _bytes(document) == before


@pytest.mark.parametrize(
    "entry",
    [
        PdfString("2"),
        PdfNumber(-1),
        PdfNumber(1.5),
        PdfNumber(8),
        PdfNumber(float("inf")),
        PdfNumber(float("nan")),
        PdfNumber(True),
    ],
)
def test_malformed_stored_scroll_indices_raise_and_can_be_repaired(document, entry):
    field = _list(document)
    dictionary = field_dictionary(document._engine_pdf, field.name)
    dictionary.mapping[PdfName("TI")] = entry
    with pytest.raises(PdfValidationException):
        _ = field.top_index
    field.top_index = 2
    assert field.top_index == 2
    _visible(_appearance(document), 2, 3)


def test_unsupported_cid_appearance_is_rejected_before_scroll_changes(document):
    field = _list(document)
    engine = document._engine_pdf
    catalog = engine._resolve(engine._cos_doc.trailer.mapping[PdfName("Root")])
    acro = engine._resolve(catalog.mapping[PdfName("AcroForm")])
    resources = engine._resolve(acro.mapping[PdfName("DR")])
    fonts = engine._resolve(resources.mapping[PdfName("Font")])
    font = engine._resolve(fonts.mapping[PdfName("Helv")])
    font.mapping[PdfName("Subtype")] = PdfName("Type0")
    before = _bytes(document)
    with pytest.raises(UnsupportedFeatureException, match="CID"):
        field.top_index = 3
    assert field.top_index == 0 and _bytes(document) == before


@pytest.mark.parametrize(
    "encrypted,incremental", [(False, False), (False, True), (True, False)]
)
def test_streaming_and_encrypted_sources_preserve_scroll_positions(
    document, tmp_path, encrypted, incremental
):
    _list(document, value="v0")
    if encrypted:
        document.encrypt(
            user_password="secret", owner_password="owner", algorithm="AES-256"
        )
    source = tmp_path / "source.pdf"
    document.save(source)
    before = source.read_bytes()
    with Document.open_streaming(
        source, password="secret" if encrypted else None
    ) as streamed:
        streamed.form["choice"].top_index = 3
        assert streamed._engine_pdf.page_contents == []
        data = _bytes(streamed, incremental=incremental)
    with Document(data, password="secret" if encrypted else None) as reopened:
        assert reopened.form["choice"].top_index == 3
        assert reopened.form["choice"].value == "v0"
        _visible(_appearance(reopened), 3, 4)
    assert source.read_bytes() == before


def test_independent_reader_sees_ti_and_unchanged_selections(document):
    pikepdf = pytest.importorskip("pikepdf")
    _list(document, top_index=3, value=["v1", "v4"], multiselect=True)
    with pikepdf.open(io.BytesIO(_bytes(document))) as pdf:
        field = pdf.Root.AcroForm.Fields[0]
        assert int(field.TI) == 3
        assert list(field.V) == ["v1", "v4"]
        assert list(field.I) == [1, 4]


def test_multiple_widgets_show_the_same_first_option_and_keep_their_heights(document):
    field = _list(document, value="v4")
    document.pages.add(size=(240, 160))
    engine = document._engine_pdf
    dictionary = field_dictionary(engine, field.name)
    original = widget_dictionaries(engine, dictionary)[0]
    mapping = dict(original.mapping)
    mapping[PdfName("Rect")] = PdfArray(
        [PdfNumber(value) for value in (20, 40, 220, 96)]
    )
    mapping[PdfName("P")] = engine._make_page_ref(1)
    reference = engine._cos_doc.register_object(PdfDictionary(mapping))
    dictionary.mapping[PdfName("Kids")].items.append(reference)
    engine._get_page_dict(1).mapping[PdfName("Annots")] = PdfArray([reference])
    field.top_index = 3
    assert len(field.widgets) == 2 and field.value == "v4"
    _visible(_appearance(document), 3, 4)
    _visible(document.pages[2].annotations[0].appearance_normal, 3, 4, 5, 6)
    with Document(_bytes(document)) as reopened:
        assert reopened.form["choice"].top_index == 3
        _visible(_appearance(reopened), 3, 4)
        _visible(reopened.pages[2].annotations[0].appearance_normal, 3, 4, 5, 6)


def test_removed_fields_and_disposed_documents_reject_scroll_access(document):
    field = _list(document)
    document.form.remove_field("choice")
    for operation in (lambda: field.top_index, lambda: setattr(field, "top_index", 1)):
        with pytest.raises(KeyError, match="no longer"):
            operation()
    field = _list(document)
    document.dispose()
    for operation in (lambda: field.top_index, lambda: setattr(field, "top_index", 1)):
        with pytest.raises(AsposePdfException, match="disposed"):
            operation()
