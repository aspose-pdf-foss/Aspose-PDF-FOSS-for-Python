"""Selective flattening preserves unselected fields, pages and appearances."""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document, PdfLoadLimits, PdfResourceLimitException
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfBoolean,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfStream,
    PdfString,
)
from aspose_pdf.engine.form_fields import field_dictionary, widget_dictionaries
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfParseException,
    PdfValidationException,
)
from aspose_pdf.forms import Form
from aspose_pdf.lowcode import ByteArrayDataSource, FlattenOptions, FormFlattener


@pytest.fixture
def document():
    with Document() as doc:
        for index in range(2):
            page = doc.pages.add()
            page.size = (240, 120)
            doc.form.add_text_field(
                f"section.field{index}", page, (20, 20, 200, 45), value=f"value{index}"
            )
            page.annotations.add(
                "Square", (10, 70, 50, 100), "", properties={"C": [1, 0, 0]}
            )
        yield doc


def _bytes(document):
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _acro(document):
    pdf = document._engine_pdf
    root = pdf._resolve(pdf._cos_doc.trailer.mapping[PdfName("Root")])
    return pdf._resolve(root.mapping.get(PdfName("AcroForm")))


def _widgets(document, name):
    pdf = document._engine_pdf
    return widget_dictionaries(pdf, field_dictionary(pdf, name))


@pytest.mark.parametrize("api", ["document", "form", "plugin"])
@pytest.mark.parametrize(
    "fields,annotations", [(True, False), (False, True), (True, True)]
)
def test_modes_and_legacy_default(document, api, fields, annotations):
    kwargs = (
        {} if fields and annotations else dict(fields=fields, annotations=annotations)
    )
    if api == "plugin":
        options = FlattenOptions(**kwargs).add_input(
            ByteArrayDataSource(_bytes(document))
        )
        data = FormFlattener().process(options)[0].to_array()
    else:
        target = document.form if api == "form" else document
        result = target.flatten(**kwargs)
        assert result is (None if api == "form" else document)
        data = _bytes(document)
    with Document(data) as reopened:
        assert len(reopened.form) == (0 if fields else 2)
        for page in reopened.pages:
            subtypes = [annot.subtype for annot in page.annotations]
            assert subtypes.count("Widget") == (0 if fields else 1)
            assert subtypes.count("Square") == (0 if annotations else 1)
        if fields:
            assert "value0" in reopened.pages[0].extract_text()
            assert "value1" in reopened.pages[1].extract_text()
        else:
            reopened.form["section.field0"].value = "still editable"
            assert reopened.form["section.field0"].value == "still editable"


@pytest.mark.parametrize("on_disk", [False, True])
def test_selected_names_keep_handles_and_siblings(document, tmp_path, on_disk):
    removed = document.form["section.field0"]
    retained = document.form["section.field1"]
    form = document.form
    document.flatten(fields=iter([removed.name, removed.name]), annotations=False)
    assert [field.name for field in form] == [retained.name]
    with pytest.raises(KeyError, match="no longer"):
        removed.value = "ghost"
    retained.value = "edited"
    assert form[retained.name].value == "edited"
    assert len(document.pages[0].annotations) == 1
    assert len(document.pages[1].annotations) == 2
    if on_disk:
        source = tmp_path / "selected.pdf"
        document.save(source)
    else:
        source = _bytes(document)
    with Document(source) as reopened:
        assert [field.name for field in reopened.form] == [retained.name]
        assert reopened.form[retained.name].value == "edited"
        assert "value0" in reopened.pages[0].extract_text()
        reopened.flatten(annotations=False)
        assert len(reopened.form) == 0
        assert len(reopened.pages[0].annotations) == 1


def test_name_and_page_filters_intersect(document):
    before = _bytes(document)
    document.flatten(fields=["section.field0"], pages=[1], annotations=False)
    assert _bytes(document) == before
    document.form.flatten(["section.field0"], pages=[0, 0], annotations=False)
    assert len(document.form) == 1
    assert "value0" in document.pages[0].extract_text()


@pytest.mark.parametrize(
    "kwargs,error",
    [
        ({"fields": "section.field0"}, TypeError),
        ({"fields": ["section.field0", 42]}, TypeError),
        ({"fields": {"section.field0": True}}, TypeError),
        ({"fields": None}, TypeError),
        ({"fields": ["section.field0", "missing"]}, KeyError),
        ({"fields": ["missing"], "pages": []}, KeyError),
        ({"fields": ["section"]}, KeyError),
        ({"pages": [0, 2]}, IndexError),
        ({"pages": [-1]}, IndexError),
        ({"pages": [True]}, TypeError),
        ({"pages": [0.0]}, TypeError),
        ({"pages": "0"}, TypeError),
        ({"annotations": 1}, TypeError),
        ({"strict": 1}, TypeError),
    ],
)
def test_invalid_selection_is_rejected_before_mutation(document, kwargs, error):
    before = _bytes(document)
    with pytest.raises(error):
        document.flatten(**kwargs)
    assert _bytes(document) == before


@pytest.mark.parametrize(
    "kwargs",
    [
        {"fields": False, "annotations": False},
        {"fields": [], "annotations": False},
        {"pages": []},
        {"fields": ["missing"], "annotations": False, "strict": False},
    ],
)
def test_empty_selections_do_not_modify_document(document, kwargs):
    before = _bytes(document)
    document.flatten(**kwargs)
    assert _bytes(document) == before


def test_unselected_appearances_and_need_appearances_are_preserved(document):
    pdf = document._engine_pdf
    selected, other = (_widgets(document, f"section.field{i}")[0] for i in range(2))
    old_ap = other.mapping[PdfName("AP")]
    other.mapping[PdfName("V")] = PdfString("stale widget value")
    _acro(document).mapping[PdfName("NeedAppearances")] = PdfBoolean(True)
    other_before = dict(other.mapping)
    annot = pdf._resolve(pdf._get_page_dict(1)[PdfName("Annots")].items[1])
    annot_before = dict(annot.mapping)
    document.flatten(fields=["section.field0"], annotations=False)
    assert other.mapping == other_before
    assert other.mapping[PdfName("AP")] is old_ap
    assert annot.mapping == annot_before
    assert _acro(document)[PdfName("NeedAppearances")].value is True
    assert selected.mapping[PdfName("AP")] is not None
    assert "value0" in document.pages[0].extract_text()


def test_multi_page_widget_field_survives_until_last_widget_is_baked(document):
    pdf = document._engine_pdf
    pdf.create_form_field(
        "shared",
        "text",
        [
            {"page_index": 0, "rect": (80, 80, 200, 100)},
            {"page_index": 1, "rect": (80, 80, 200, 100)},
        ],
        value="shared value",
    )
    field = document.form["shared"]
    document.flatten(fields=["shared"], annotations=False, pages=[0])
    assert document.form["shared"] is field
    assert len(field.widgets) == 1
    assert field.widgets[0].page_index == 1
    field.value = "remaining"
    with Document(_bytes(document)) as reopened:
        assert len(reopened.form["shared"].widgets) == 1
        assert "shared value" in reopened.pages[0].extract_text()
        reopened.flatten(fields=["shared"], annotations=False, pages=[1])
        assert "shared" not in [f.name for f in reopened.form]
        assert "remaining" in reopened.pages[1].extract_text()
        assert len(reopened.form) == 2


def test_calculation_order_keeps_only_surviving_fields(document):
    pdf = document._engine_pdf
    acro = _acro(document)
    fields = [field_dictionary(pdf, f"section.field{i}") for i in range(2)]
    parent = pdf._resolve(fields[0][PdfName("Parent")])
    acro.mapping[PdfName("CO")] = PdfArray(fields)
    document.flatten(fields=["section.field0"], annotations=False)
    assert acro[PdfName("CO")].items == [fields[1]]
    assert len(parent[PdfName("Kids")].items) == 1
    document.flatten(annotations=False)
    assert _acro(document) is None


@pytest.mark.parametrize("reason", ["no_ap", "bad_rect", "bad_state"])
def test_unbakeable_annotations_are_retained(document, reason):
    page = document._engine_pdf._get_page_dict(0)
    annotation = PdfDictionary(
        {
            PdfName("Subtype"): PdfName("Link"),
            PdfName("Rect"): PdfArray([PdfNumber(v) for v in (60, 60, 90, 90)]),
        }
    )
    if reason == "bad_rect":
        annotation.mapping[PdfName("Rect")] = PdfArray([PdfString("wrong")])
    elif reason == "bad_state":
        annotation.mapping[PdfName("AP")] = PdfDictionary(
            {
                PdfName("N"): PdfDictionary(
                    {
                        PdfName("On"): document._engine_pdf._cos_doc.register_object(
                            PdfStream(content=b"0 0 1 rg 0 0 10 10 re f")
                        ),
                    }
                )
            }
        )
        annotation.mapping[PdfName("AS")] = PdfName("Missing")
    page[PdfName("Annots")].items.append(annotation)
    document.flatten(fields=False, pages=[0])
    assert annotation in page[PdfName("Annots")].items
    assert len(document.form) == 2
    with Document(_bytes(document)) as reopened:
        assert len(reopened.pages[0].annotations) == 2


def test_unsupported_field_appearance_is_not_dropped(document):
    pdf = document._engine_pdf
    field = field_dictionary(pdf, "section.field0")
    field.mapping[PdfName("FT")] = PdfName("Unsupported")
    _widgets(document, "section.field0")[0].mapping.pop(PdfName("AP"))
    document.flatten(fields=["section.field0"], annotations=False)
    assert len(document.form) == 2
    assert len(document.pages[0].annotations) == 2


def test_rotated_appearance_and_page_graphics_state_keep_pixels(document):
    pdf = document._engine_pdf
    widget = _widgets(document, "section.field0")[0]
    widget.mapping[PdfName("AP")] = PdfDictionary(
        {
            PdfName("N"): PdfStream(
                content=b"0 0 1 rg 10 20 20 20 re f",
                mapping={
                    PdfName("Subtype"): PdfName("Form"),
                    PdfName("BBox"): PdfArray([PdfNumber(v) for v in (10, 20, 30, 40)]),
                    PdfName("Matrix"): PdfArray(
                        [PdfNumber(v) for v in (0, 1, -1, 0, 0, 0)]
                    ),
                },
            )
        }
    )
    pdf._set_page_content(0, b"2 0 0 2 100 100 cm 0 0 1 1 re W n")
    before = document.pages[0].render(dpi=72, antialias=False).pixels
    document.flatten(fields=["section.field0"], annotations=False)
    assert document.pages[0].render(dpi=72, antialias=False).pixels == before
    with Document(_bytes(document)) as reopened:
        assert reopened.pages[0].render(dpi=72, antialias=False).pixels == before


def test_inherited_and_shared_resources_keep_existing_xobjects(document):
    pdf = document._engine_pdf
    first, second = [pdf._get_page_dict(i) for i in range(2)]
    old = PdfDictionary(
        {PdfName("FlatAnnot0"): pdf._cos_doc.register_object(PdfStream(content=b""))}
    )
    resources = PdfDictionary({PdfName("XObject"): pdf._cos_doc.register_object(old)})
    parent = pdf._resolve(first[PdfName("Parent")])
    parent.mapping[PdfName("Resources")] = resources
    first.mapping.pop(PdfName("Resources"), None)
    second.mapping[PdfName("Resources")] = resources
    document.flatten(fields=False, pages=[0])
    new_resources = first[PdfName("Resources")]
    assert new_resources is not resources
    assert list(old.mapping) == [PdfName("FlatAnnot0")]
    assert (
        new_resources[PdfName("XObject")][PdfName("FlatAnnot0")]
        is old[PdfName("FlatAnnot0")]
    )
    assert PdfName("FlatAnnot1") in new_resources[PdfName("XObject")].mapping
    assert second[PdfName("Resources")] is resources


def test_streaming_only_decodes_selected_page(document, tmp_path, monkeypatch):
    source = tmp_path / "streamed.pdf"
    document.save(source)
    with Document.open_streaming(source) as streamed:
        pdf = streamed._engine_pdf
        original = pdf.get_page_content
        reads = []

        def read(index):
            reads.append(index)
            assert index == 0
            return original(index)

        with monkeypatch.context() as patch:
            patch.setattr(pdf, "get_page_content", read)
            streamed.flatten(pages=[0])
        assert reads == [0]
        assert pdf._lazy is True
        assert pdf.page_contents == []
        assert len(streamed.form) == 1
        with Document(_bytes(streamed)) as reopened:
            assert "value0" in reopened.pages[0].extract_text()
            assert len(reopened.pages[1].annotations) == 2


def test_cycle_and_metadata_limits_fail_without_mutation(document):
    pdf = document._engine_pdf
    field = field_dictionary(pdf, "section.field0")
    kids = field.mapping[PdfName("Kids")]
    field.mapping[PdfName("Kids")] = PdfArray([field])
    with pytest.raises(PdfParseException, match="cycle"):
        document.flatten()
    field.mapping[PdfName("Kids")] = kids
    assert len(document.pages[0].annotations) == 2
    with Document(limits=PdfLoadLimits(max_container_items=2)) as limited:
        limited.pages.add()
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            limited.flatten(fields=iter(["a", "a", "a"]))


def test_plugin_reuses_iterators_for_multiple_inputs(document):
    data = _bytes(document)
    options = FlattenOptions(
        fields=iter(["section.field0"]), annotations=False, pages=iter([0])
    )
    options.add_input(ByteArrayDataSource(data)).add_input(ByteArrayDataSource(data))
    results = FormFlattener().process(options)
    assert len(results) == 2
    for result in results:
        with Document(result.to_array()) as reopened:
            assert len(reopened.form) == 1
            assert "value0" in reopened.pages[0].extract_text()
            assert len(reopened.pages[0].annotations) == 1


def test_disposed_document_and_form_reject_flatten(document):
    form = document.form
    document.dispose()
    for target in (document, form):
        with pytest.raises(AsposePdfException, match="disposed"):
            target.flatten()


@pytest.mark.parametrize(
    "kind", ["checkbox", "radio", "list", "combo", "button", "signature"]
)
def test_selected_field_types_have_renderable_static_content(document, kind):
    form = document.form
    rect = (100, 65, 220, 95)
    if kind == "checkbox":
        form.add_checkbox("selected", 0, rect, checked=True)
    elif kind == "radio":
        form.add_radio_group("selected", 0, {"yes": rect}, value="yes")
    elif kind == "list":
        form.add_list_box("selected", 0, rect, ["one", "two"], value="two")
    elif kind == "combo":
        form.add_combo_box("selected", 0, rect, ["one", "two"], value="one")
    elif kind == "button":
        form.add_push_button("selected", 0, rect, caption="Run")
    else:
        form.add_signature_field("selected", 0, rect)
    # Force the selected widget down the missing-appearance path.
    _widgets(document, "selected")[0].mapping.pop(PdfName("AP"), None)
    document.flatten(fields=["selected"], annotations=False)
    assert [field.name for field in document.form] == [
        "section.field0",
        "section.field1",
    ]
    with Document(_bytes(document)) as reopened:
        assert len(reopened.form) == 2
        assert len(reopened.pages[0].annotations) == 2
        page = reopened._engine_pdf._get_page_dict(0)
        xobjects = page[PdfName("Resources")][PdfName("XObject")]
        assert len(xobjects.mapping) == 1


def test_selected_merged_widget_and_unnamed_intermediate(document):
    pdf = document._engine_pdf
    acro = _acro(document)
    appearance = pdf._cos_doc.register_object(
        PdfStream(
            content=b"0 0 1 rg 0 0 20 20 re f",
            mapping={
                PdfName("Subtype"): PdfName("Form"),
                PdfName("BBox"): PdfArray([PdfNumber(v) for v in (0, 0, 20, 20)]),
            },
        )
    )
    widget = PdfDictionary(
        {
            PdfName("T"): PdfString("merged"),
            PdfName("Subtype"): PdfName("Widget"),
            PdfName("Rect"): PdfArray([PdfNumber(v) for v in (100, 70, 120, 90)]),
            PdfName("AP"): PdfDictionary({PdfName("N"): appearance}),
        }
    )
    reference = pdf._cos_doc.register_object(widget)
    intermediate = PdfDictionary(
        {
            PdfName("FT"): PdfName("Tx"),
            PdfName("Kids"): PdfArray([reference]),
        }
    )
    parent = PdfDictionary(
        {
            PdfName("T"): PdfString("nested"),
            PdfName("Kids"): PdfArray([intermediate]),
        }
    )
    acro[PdfName("Fields")].items.append(parent)
    pdf._get_page_dict(0)[PdfName("Annots")].items.append(reference)
    document.flatten(fields=["nested.merged"], annotations=False)
    assert parent not in acro[PdfName("Fields")].items
    assert len(document.form) == 2
    assert document.pages[0].render(dpi=72, antialias=False).get_pixel(110, 40) == (
        0,
        0,
        255,
    )


def test_selecting_field_beyond_old_4096_entry_cutoff(document):
    acro = _acro(document)
    for index in range(4100):
        acro[PdfName("Fields")].items.append(
            PdfDictionary(
                {
                    PdfName("T"): PdfString(f"unused{index}"),
                    PdfName("FT"): PdfName("Tx"),
                }
            )
        )
    document.form.add_text_field("last", 0, (100, 80, 220, 100), value="last value")
    document.flatten(fields=["last"], annotations=False)
    assert "last" not in document.form.to_dict()
    assert len(document.form) == 4102
    assert "last value" in document.pages[0].extract_text()


def test_annotations_only_does_not_walk_invalid_form_tree(document):
    pdf = document._engine_pdf
    field = field_dictionary(pdf, "section.field0")
    field.mapping[PdfName("Kids")] = PdfArray([field])
    # Avoid loading the malformed form through the cached public collection.
    document._form = None
    document.flatten(fields=False, pages=[0])
    assert len(document.pages[0].annotations) == 1


def test_selection_metadata_respects_depth_limit(document):
    with Document(limits=PdfLoadLimits(max_nesting_depth=2)) as limited:
        limited.pages.add()
        pdf = limited._engine_pdf
        root = pdf._resolve(pdf._cos_doc.trailer.mapping[PdfName("Root")])
        leaf = PdfDictionary(
            {PdfName("T"): PdfString("leaf"), PdfName("FT"): PdfName("Tx")}
        )
        middle = PdfDictionary({PdfName("Kids"): PdfArray([leaf])})
        parent = PdfDictionary({PdfName("Kids"): PdfArray([middle])})
        root.mapping[PdfName("AcroForm")] = PdfDictionary(
            {PdfName("Fields"): PdfArray([parent])}
        )
        with pytest.raises(PdfResourceLimitException, match="max_nesting_depth"):
            limited.flatten(annotations=False)


def test_empty_or_plain_documents_are_valid():
    with Document() as document:
        assert document.flatten() is document
        document.pages.add()
        before = _bytes(document)
        assert document.flatten() is document
        assert _bytes(document) == before


def test_repeating_selection_with_lenient_names_is_a_noop(document):
    kwargs = dict(fields=["section.field0"], annotations=False, strict=False)
    document.flatten(**kwargs)
    before = _bytes(document)
    document.flatten(**kwargs)
    assert _bytes(document) == before


@pytest.mark.parametrize("level", ["field", "parent", "acroform"])
def test_missing_appearance_uses_indirect_inherited_default_appearance(document, level):
    pdf = document._engine_pdf
    field = field_dictionary(pdf, "section.field0")
    widget = _widgets(document, "section.field0")[0]
    widget.mapping.pop(PdfName("AP"))
    field.mapping.pop(PdfName("DA"), None)
    parent = pdf._resolve(field[PdfName("Parent")])
    parent.mapping.pop(PdfName("DA"), None)
    target = {"field": field, "parent": parent, "acroform": _acro(document)}[level]
    target.mapping[PdfName("DA")] = pdf._cos_doc.register_object(
        PdfString("/Helv 7 Tf 0 1 0 rg")
    )
    document.flatten(fields=["section.field0"], annotations=False)
    ap = pdf._resolve(widget[PdfName("AP")])
    normal = pdf._resolve(ap[PdfName("N")])
    assert b"/Helv 7 Tf" in normal.content
    assert b"0 1 0 rg" in normal.content


@pytest.mark.parametrize("flags", [2, 32])
@pytest.mark.parametrize("on_disk", [False, True])
def test_hidden_and_no_view_objects_keep_visibility_and_flags(
    document,
    flags,
    on_disk,
    tmp_path,
):
    pdf = document._engine_pdf
    page = pdf._get_page_dict(0)
    for ref in page[PdfName("Annots")].items:
        pdf._resolve(ref).mapping[PdfName("F")] = PdfNumber(flags)
    before_pixels = document.pages[0].render(dpi=72, antialias=False).pixels
    before = _bytes(document)
    document.flatten(fields=["section.field0"], pages=[0])
    assert _bytes(document) == before
    if on_disk:
        source = tmp_path / "hidden.pdf"
        document.save(source)
    else:
        source = _bytes(document)
    with Document(source) as reopened:
        assert reopened.pages[0].render(dpi=72, antialias=False).pixels == before_pixels
        assert len(reopened.form) == 2
        assert len(reopened.pages[0].annotations) == 2
        pdf = reopened._engine_pdf
        for ref in pdf._get_page_dict(0)[PdfName("Annots")].items:
            assert pdf._resolve(ref)[PdfName("F")].value == flags


def test_field_with_visible_and_hidden_widgets_keeps_hidden_widget(document):
    pdf = document._engine_pdf
    pdf.create_form_field(
        "shared",
        "text",
        [
            {"page_index": 0, "rect": (100, 70, 220, 90)},
            {"page_index": 1, "rect": (100, 70, 220, 90)},
        ],
        value="shared value",
    )
    widgets = _widgets(document, "shared")
    widgets[0].mapping[PdfName("F")] = PdfNumber(2)
    document.flatten(fields=["shared"], annotations=False)
    with Document(_bytes(document)) as reopened:
        assert [widget.page_index for widget in reopened.form["shared"].widgets] == [0]
        assert "shared value" in reopened.pages[1].extract_text()
        assert _widgets(reopened, "shared")[0][PdfName("F")].value == 2


def test_independent_form_refreshes_handles_after_flatten(document):
    independent = Form(document)
    removed = independent["section.field0"]
    retained = independent["section.field1"]
    independent.flatten([removed.name], annotations=False)
    assert independent.fields == [retained]
    assert len(document.form) == len(independent) == 1
    document.form.add_text_field(
        removed.name, 0, (20, 80, 220, 100), value="replacement"
    )
    with pytest.raises(KeyError, match="no longer"):
        removed.value = "ghost edit"
    retained.value = "still editable"
    with Document(_bytes(document)) as reopened:
        assert reopened.form[removed.name].value == "replacement"
        assert reopened.form[retained.name].value == "still editable"


@pytest.mark.parametrize("failed_page", [0, 1])
@pytest.mark.parametrize("api", ["document", "cached_form", "independent_form"])
def test_page_failure_keeps_committed_fields_consistent_and_allows_retry(
    api,
    failed_page,
):
    with Document(limits=PdfLoadLimits(max_content_tokens=100)) as doc:
        for index in range(2):
            doc.pages.add()
            doc.form.add_text_field(
                f"f{index}", index, (20, 20, 200, 40), value=f"value{index}"
            )
        target = {
            "document": doc,
            "cached_form": doc.form,
            "independent_form": Form(doc),
        }[api]
        form = doc.form if api == "document" else target
        first, second = form["f0"], form["f1"]
        pdf = doc._engine_pdf
        acro = _acro(doc)
        refs = list(acro[PdfName("Fields")].items)
        acro.mapping[PdfName("CO")] = PdfArray(refs)
        pdf._set_page_content(failed_page, b"q Q\n" * 101)
        with pytest.raises(PdfResourceLimitException, match="max_content_tokens"):
            target.flatten(annotations=False)
        expected = ["f0", "f1"] if failed_page == 0 else ["f1"]
        assert [field.name for field in form] == expected
        assert [field.name for field in doc.form] == expected
        assert first._removed is (failed_page == 1)
        assert not second._removed
        assert acro[PdfName("CO")].items == (refs if failed_page == 0 else refs[1:])
        for field in form:
            assert len(field.widgets) == 1
            assert field.widgets[0].page_index == int(field.name[-1])
        # The failed page is still interactive and can be repaired and retried.
        pdf._set_page_content(failed_page, b"")
        with Document(_bytes(doc)) as reopened:
            assert [field.name for field in reopened.form] == expected
        target.flatten(annotations=False)
        assert len(form) == len(doc.form) == 0
        assert all(len(page.annotations) == 0 for page in doc.pages)
        with Document(_bytes(doc)) as reopened:
            assert len(reopened.form) == 0
            assert "value0" in reopened.pages[0].extract_text()
            assert "value1" in reopened.pages[1].extract_text()


def test_calculation_order_limits_are_checked_before_any_page_is_edited():
    with Document(limits=PdfLoadLimits(max_container_items=6)) as doc:
        doc.pages.add()
        doc.form.add_text_field("f", 0, (20, 20, 200, 40), value="value")
        acro = _acro(doc)
        acro.mapping[PdfName("CO")] = PdfArray(acro[PdfName("Fields")].items * 7)
        pdf = doc._engine_pdf
        page = pdf._get_page_dict(0)
        annots = page[PdfName("Annots")]
        content = pdf.get_page_content(0)
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            doc.flatten()
        assert page[PdfName("Annots")] is annots
        assert pdf.get_page_content(0) == content
        assert len(doc.form) == 1


def test_partial_failure_preserves_only_remaining_widgets_of_shared_field():
    with Document(limits=PdfLoadLimits(max_content_tokens=100)) as doc:
        doc.pages.add()
        doc.pages.add()
        pdf = doc._engine_pdf
        pdf.create_form_field(
            "shared",
            "text",
            [
                {"page_index": 0, "rect": (20, 20, 200, 40)},
                {"page_index": 1, "rect": (20, 20, 200, 40)},
            ],
            value="original",
        )
        field = doc.form["shared"]
        acro = _acro(doc)
        acro.mapping[PdfName("CO")] = PdfArray(list(acro[PdfName("Fields")].items))
        pdf._set_page_content(1, b"q Q\n" * 101)
        with pytest.raises(PdfResourceLimitException, match="max_content_tokens"):
            doc.flatten(annotations=False)
        assert doc.form["shared"] is field
        assert [widget.page_index for widget in field.widgets] == [1]
        assert len(acro[PdfName("CO")].items) == 1
        field.value = "remaining"
        pdf._set_page_content(1, b"")
        doc.flatten(annotations=False)
        assert len(doc.form) == 0
        with Document(_bytes(doc)) as reopened:
            assert "original" in reopened.pages[0].extract_text()
            assert "remaining" in reopened.pages[1].extract_text()


def test_uncommitted_widget_is_not_pruned_if_another_widget_fails(
    document, monkeypatch
):
    document.form.add_text_field("second", 0, (80, 80, 220, 105), value="second value")
    pdf = document._engine_pdf
    failing_widget = _widgets(document, "second")[0]
    original = pdf._set_widget_appearance
    before = _bytes(document)

    def generate(widget, *args, **kwargs):
        if widget is failing_widget:
            raise PdfResourceLimitException("appearance budget exceeded")
        return original(widget, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(pdf, "_set_widget_appearance", generate)
        with pytest.raises(PdfResourceLimitException, match="appearance budget"):
            document.flatten(fields=["section.field0", "second"], annotations=False)
    assert _bytes(document) == before
    assert len(document.form) == 3
    assert len(document.pages[0].annotations) == 3
    document.flatten(fields=["section.field0", "second"], annotations=False)
    assert [field.name for field in document.form] == ["section.field1"]


def test_unsafe_graphics_state_is_rejected_without_removing_selected_objects(document):
    pdf = document._engine_pdf
    pdf._set_page_content(0, b"2 0 0 2 50 50 cm Q\n")
    before = _bytes(document)
    pixels = document.pages[0].render(dpi=72, antialias=False).pixels
    retained = document.form["section.field0"]
    with pytest.raises(PdfValidationException, match=r"page 0.*safely isolated"):
        document.flatten(fields=[retained.name], annotations=False)
    assert _bytes(document) == before
    assert document.form[retained.name] is retained
    assert len(document.pages[0].annotations) == 2
    with Document(_bytes(document)) as reopened:
        assert reopened.pages[0].render(dpi=72, antialias=False).pixels == pixels
    retained.value = "still editable"
    pdf._set_page_content(0, b"")
    document.flatten(fields=[retained.name], annotations=False)
    assert "still editable" in document.pages[0].extract_text()


@pytest.mark.parametrize("indirect", [False, True])
@pytest.mark.parametrize("override", [None, 0, 1])
def test_acroform_default_alignment_is_inherited_and_can_be_overridden(
    document,
    indirect,
    override,
):
    pdf = document._engine_pdf
    field = field_dictionary(pdf, "section.field0")
    widget = _widgets(document, "section.field0")[0]
    field.mapping.pop(PdfName("Q"), None)
    widget.mapping.pop(PdfName("Q"), None)
    widget.mapping.pop(PdfName("AP"), None)
    value = PdfNumber(2)
    _acro(document).mapping[PdfName("Q")] = (
        pdf._cos_doc.register_object(value) if indirect else value
    )
    if override is not None:
        field.mapping[PdfName("Q")] = PdfNumber(override)
    document.flatten(fields=["section.field0"], annotations=False)
    actual = pdf._resolve(pdf._resolve(widget[PdfName("AP")])[PdfName("N")]).content
    with Document() as reference:
        reference.pages.add()
        reference.form.add_text_field(
            "reference",
            0,
            (20, 20, 200, 45),
            value="value0",
            alignment=2 if override is None else override,
        )
        reference_pdf = reference._engine_pdf
        ref_widget = _widgets(reference, "reference")[0]
        expected = reference_pdf._resolve(
            reference_pdf._resolve(ref_widget[PdfName("AP")])[PdfName("N")]
        ).content
        assert actual == expected
