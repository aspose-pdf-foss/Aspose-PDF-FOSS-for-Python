"""Move and resize individual form widgets without rewriting their appearances."""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document, FieldWidget
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName, PdfNumber, PdfStream
from aspose_pdf.engine.form_fields import field_dictionary, widget_dictionaries
from aspose_pdf.exceptions import AsposePdfException, PdfValidationException


@pytest.fixture
def document():
    with Document() as doc:
        doc.pages.add(size=(300, 200))
        yield doc


def _widgets(document, name):
    pdf = document._engine_pdf
    return widget_dictionaries(pdf, field_dictionary(pdf, name))


def _bytes(document):
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _add_field(document, kind):
    form = document.form
    rect = (10, 20, 90, 50)
    if kind == "text":
        return form.add_text_field("field", 1, rect, value="Alice", required=True)
    if kind == "checkbox":
        return form.add_checkbox("field", 1, rect, checked=True, on_value="Agree")
    if kind == "radio":
        return form.add_radio_group(
            "field", 1, [("A", rect), ("B", (100, 20, 120, 40))], value="B"
        )
    if kind == "signature":
        return form.add_signature_field("field", 1, rect)
    if kind == "pushbutton":
        return form.add_push_button("field", 1, rect, caption="Submit")
    factory = form.add_combo_box if kind == "combobox" else form.add_list_box
    return factory("field", 1, rect, [("a", "Alpha"), "Beta"], value="a")


@pytest.mark.parametrize("on_disk", [False, True])
@pytest.mark.parametrize(
    "kind",
    ["text", "checkbox", "radio", "listbox", "combobox", "pushbutton", "signature"],
)
def test_first_widget_rect_round_trip(document, tmp_path, kind, on_disk):
    field = _add_field(document, kind)
    before = (
        field.value,
        field.default_value,
        field.flags,
        field.options,
        field.export_values,
    )
    appearances = [annot.appearance_normal for annot in document.pages[1].annotations]
    field.rect = [40, 80, 200, 140]
    assert document.form["field"] is field
    assert field.rect == (40, 80, 200, 140)
    if on_disk:
        source = tmp_path / "geometry.pdf"
        document.save(source)
    else:
        source = _bytes(document)
    with Document(source) as reopened:
        edited = reopened.form["field"]
        assert edited.widgets[0] == FieldWidget(1, (40, 80, 200, 140))
        assert (
            edited.value,
            edited.default_value,
            edited.flags,
            edited.options,
            edited.export_values,
        ) == before
        assert [
            a.appearance_normal for a in reopened.pages[1].annotations
        ] == appearances
        if kind == "radio":
            assert edited.widgets[1].rect == (100, 20, 120, 40)


def test_select_second_widget_preserves_other_widgets_and_metadata(document):
    field = _add_field(document, "radio")
    widgets = _widgets(document, field.name)
    before = [dict(widget.mapping) for widget in widgets]
    snapshot = field.widgets
    assert field.set_widget_rect((150, 80, 190, 120), widget_index=1) is field
    assert widgets[0].mapping == before[0]
    assert {k: v for k, v in widgets[1].mapping.items() if k != PdfName("Rect")} == {
        k: v for k, v in before[1].items() if k != PdfName("Rect")
    }
    assert field.widgets == [snapshot[0], FieldWidget(1, (150, 80, 190, 120))]
    assert snapshot[1].rect == (100, 20, 120, 40)
    assert field.value == "B"


@pytest.mark.parametrize("page_index", [None, 1])
def test_shared_rect_and_page_membership_are_preserved(document, page_index):
    """Imported widgets can share a rectangle and omit their /P entry."""
    field = _add_field(document, "radio")
    document.pages.add(size=(300, 200))
    pdf = document._engine_pdf
    first, second = _widgets(document, field.name)
    second.mapping[PdfName("Rect")] = first.mapping[PdfName("Rect")]
    second.mapping.pop(PdfName("P"), None)
    annots = pdf._get_page_dict(0).mapping[PdfName("Annots")]
    reference = annots.items.pop(1)
    if page_index is not None:
        pdf._get_page_dict(page_index).mapping[PdfName("Annots")] = PdfArray(
            [reference]
        )
    field.set_widget_rect((150, 80, 190, 120), widget_index=1)
    assert field.widgets == [
        FieldWidget(1, (10, 20, 90, 50)),
        FieldWidget(None if page_index is None else page_index + 1, (150, 80, 190, 120)),
    ]
    assert PdfName("P") not in second.mapping
    with Document(_bytes(document)) as reopened:
        assert reopened.form["field"].widgets == field.widgets


def test_field_without_widgets_rejects_geometry_edit(document):
    field = _add_field(document, "text")
    dictionary = field_dictionary(document._engine_pdf, field.name)
    dictionary.mapping.pop(PdfName("Subtype"), None)
    dictionary.mapping.pop(PdfName("Kids"), None)
    assert field.rect is None and field.widgets == []
    before = dict(dictionary.mapping)
    with pytest.raises(IndexError, match="widget_index"):
        field.rect = (0, 0, 20, 20)
    assert dictionary.mapping == before


def test_merged_field_widget_without_appearance(document):
    field = _add_field(document, "text")
    pdf = document._engine_pdf
    dictionary = field_dictionary(pdf, field.name)
    dictionary.mapping.update(_widgets(document, field.name)[0].mapping)
    for key in ("Kids", "Parent", "AP"):
        dictionary.mapping.pop(PdfName(key), None)
    pdf._get_page_dict(0).mapping[PdfName("Annots")] = PdfArray(
        [pdf._cos_doc.register_object(dictionary)]
    )
    field.set_widget_rect((100, 80, 240, 140))
    assert field.widgets == [FieldWidget(1, (100, 80, 240, 140))]
    assert PdfName("AP") not in dictionary.mapping
    with Document(_bytes(document)) as reopened:
        assert reopened.form["field"].widgets == field.widgets
        assert reopened.form["field"].value == "Alice"


@pytest.mark.parametrize(
    "rect",
    [
        None,
        "1234",
        (),
        (1, 2, 3),
        (1, 2, 3, 4, 5),
        (False, 0, 10, 10),
        (0, 0, object(), 10),
        (0, 0, float("nan"), 10),
        (0, 0, float("inf"), 10),
        (0, 0, 10**1000, 10),
        (10, 0, 0, 10),
        (0, 10, 10, 0),
        (0, 0, 0, 10),
        (0, 0, 10, 0),
        (-1e308, 0, 1e308, 10),
    ],
)
def test_invalid_rect_does_not_mutate(document, rect):
    field = _add_field(document, "text")
    widget = _widgets(document, field.name)[0]
    before = dict(widget.mapping)
    with pytest.raises(PdfValidationException):
        field.set_widget_rect(rect)
    assert widget.mapping == before


@pytest.mark.parametrize(
    "index,error",
    [
        (-1, IndexError),
        (1, IndexError),
        (True, TypeError),
        (0.0, TypeError),
        ("0", TypeError),
    ],
)
def test_invalid_widget_index_does_not_mutate(document, index, error):
    field = _add_field(document, "text")
    widget = _widgets(document, field.name)[0]
    before = dict(widget.mapping)
    with pytest.raises(error, match="widget_index"):
        field.set_widget_rect((0, 0, 20, 20), widget_index=index)
    assert widget.mapping == before


def test_removed_and_disposed_fields_cannot_change_geometry(document):
    field = _add_field(document, "text")
    field.remove()
    _add_field(document, "text")
    with pytest.raises(KeyError, match="no longer"):
        field.rect = (0, 0, 20, 20)
    replacement = document.form["field"]
    document.dispose()
    with pytest.raises(AsposePdfException):
        replacement.set_widget_rect((0, 0, 20, 20))


def test_streaming_geometry_edit_does_not_decode_streams(
    document, tmp_path, monkeypatch
):
    _add_field(document, "text")
    path = tmp_path / "lazy.pdf"
    document.save(path)
    with Document.open_streaming(path) as streamed:
        pdf = streamed._engine_pdf

        def unexpected(*args, **kwargs):
            pytest.fail("Geometry edits must not decode content or appearances")

        with monkeypatch.context() as patch:
            patch.setattr(pdf, "get_page_content", unexpected)
            patch.setattr(pdf, "_decode_cos_stream", unexpected)
            streamed.form["field"].rect = (-20, -10, 200, 100)
        assert pdf._lazy and pdf.page_contents == []
        with Document(_bytes(streamed)) as reopened:
            assert reopened.form["field"].rect == (-20, -10, 200, 100)


def test_custom_appearance_scales_and_moves_when_rendered_and_flattened(document):
    field = _add_field(document, "text")
    widget = _widgets(document, field.name)[0]
    pdf = document._engine_pdf
    appearance = PdfStream(
        b"1 0 0 rg 0 0 10 10 re f\n",
        {
            PdfName("Type"): PdfName("XObject"),
            PdfName("Subtype"): PdfName("Form"),
            PdfName("BBox"): PdfArray([PdfNumber(v) for v in (0, 0, 10, 10)]),
        },
    )
    ap = PdfDictionary({PdfName("N"): pdf._cos_doc.register_object(appearance)})
    widget.mapping[PdfName("AP")] = ap
    field.set_widget_rect((100, 80, 240, 140))
    assert widget.mapping[PdfName("AP")] is ap
    for flattened in (False, True):
        if flattened:
            document.flatten(annotations=False)
        raster = document.pages[1].render(dpi=72, antialias=False)
        assert raster.get_pixel(110, 70) == (255, 0, 0)
        assert raster.get_pixel(230, 110) == (255, 0, 0)
        assert raster.get_pixel(20, 160) == (255, 255, 255)
        assert raster.get_pixel(245, 90) == (255, 255, 255)
