"""What a form field reports about itself beyond its value.

``Field`` had a name, a value and a type. A form reader also needs to know
where the field is, whether it is read-only or required, what a choice field
offers, what a check box exports, how long a text may be and what the field is
called to the user. Each property now reads the field's dictionary, taking an
inheritable attribute from the nearest ancestor that has it (ISO 32000-1 table
220) -- which also fixed a value set on a parent field and read as ``None``.

Every expectation was compared with pdf.js (``getAnnotations``), pdfium
(``FPDFAnnot_*``), qpdf (``AcroFormField``) and MuPDF (``Widget``) on the same
fields: 210 comparisons, all equal. Two choices where they split: ``/TU`` is
read from the field, as pdfium and MuPDF do and the specification places it,
where pdf.js and qpdf look only at a separate widget; and a radio button's
export values are its on-state names, as pdf.js and MuPDF report them, where
pdfium maps them through ``/Opt``.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document, FieldWidget
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfIndirectReference,
    PdfName,
    PdfNumber,
    PdfStream,
    PdfString,
)


def _reloaded(document: Document) -> Document:
    buffer = io.BytesIO()
    document.save(buffer)
    return Document(io.BytesIO(buffer.getvalue()))


def _field(document: Document, name: str):
    return next(field for field in document.form.fields if field.name == name)


# --- a form this library authored ------------------------------------------------------------


@pytest.fixture(scope="module")
def authored() -> Document:
    document = Document()
    first, second = document.pages.add(), document.pages.add()
    form = document.form
    form.add_text_field("person.name", first, (72, 700, 272, 724), value="Alice", multiline=True, read_only=True)
    form.add_text_field("person.city", second, (72, 700, 272, 724), required=True)
    form.add_checkbox("agree", first, (72, 650, 90, 668), checked=True, on_value="Agree")
    form.add_radio_group("size", first, [("S", (72, 600, 90, 618)), ("M", (100, 600, 118, 618))], value="M")
    form.add_list_box("colours", first, (300, 500, 450, 600), [("r", "Red"), "Blue"], value=["r", "Blue"], multiselect=True)
    form.add_combo_box("country", second, (300, 500, 450, 520), ["NL", "DE"], value="DE", editable=True)
    form.add_push_button("go", first, (300, 400, 380, 430))
    return _reloaded(document)


def test_names_flags_and_defaults(authored):
    name = _field(authored, "person.name")
    assert (name.partial_name, name.flags, name.read_only, name.required) == ("name", 4097, True, False)
    assert name.multiline and not name.password and not name.comb and name.max_length is None
    assert name.default_value == "Alice"
    city = _field(authored, "person.city")
    assert (city.read_only, city.required, city.no_export) == (False, True, False)
    assert name.alternate_name is None and name.mapping_name is None


def test_choices_and_buttons(authored):
    colours = _field(authored, "colours")
    assert colours.options == [("r", "Red"), ("Blue", "Blue")]
    assert colours.multi_select and not colours.editable
    country = _field(authored, "country")
    assert country.options == [("NL", "NL"), ("DE", "DE")]
    assert country.editable and not country.multi_select
    assert _field(authored, "agree").export_values == ["Agree"]
    assert _field(authored, "size").export_values == ["S", "M"]
    # What a type does not have, it reports empty.
    assert _field(authored, "agree").options == [] and _field(authored, "colours").export_values == []
    assert _field(authored, "go").max_length is None and not _field(authored, "go").multiline


def test_widgets_say_where_the_field_is(authored):
    assert _field(authored, "person.city").widgets == [FieldWidget(1, (72.0, 700.0, 272.0, 724.0))]
    size = _field(authored, "size")
    assert [widget.rect for widget in size.widgets] == [(72.0, 600.0, 90.0, 618.0), (100.0, 600.0, 118.0, 618.0)]
    assert (size.page_index, size.rect) == (0, (72.0, 600.0, 90.0, 618.0))


# --- fields other producers write -------------------------------------------------------------------


def _register(engine, obj) -> PdfIndirectReference:
    return engine._cos_doc.register_object(obj)


def _widget(engine, page: int, rect, *, parent=None, own_page=True, **extra) -> PdfIndirectReference:
    entry = {PdfName("Type"): PdfName("Annot"), PdfName("Subtype"): PdfName("Widget"),
             PdfName("Rect"): PdfArray([PdfNumber(v) for v in rect])}
    entry.update({PdfName(k): v for k, v in extra.items()})
    page_ref = PdfIndirectReference(engine._page_obj_ids[page], 0)
    if own_page:
        entry[PdfName("P")] = page_ref
    if parent is not None:
        entry[PdfName("Parent")] = parent
    reference = _register(engine, PdfDictionary(entry))
    page_dict = engine._get_page_dict(page)
    annots = page_dict.mapping.get(PdfName("Annots"))
    if not isinstance(annots, PdfArray):
        annots = page_dict.mapping[PdfName("Annots")] = PdfArray([])
    annots.append(reference)
    return reference


def _appearance(engine, *states: str) -> PdfDictionary:
    return PdfDictionary({PdfName("N"): PdfDictionary({PdfName(s): _register(engine, PdfStream(b"")) for s in states})})


@pytest.fixture(scope="module")
def crafted() -> Document:
    """The structures pdf.js, pdfium, qpdf and MuPDF were compared on."""
    document = Document()
    document.pages.add(), document.pages.add()
    engine = document._engine_pdf
    engine._ensure_cos()
    engine._ensure_page_cache()
    fields = []

    # A parent carrying FT, Ff, V, DV and MaxLen for a named kid.
    parent = _register(engine, PdfDictionary({
        PdfName("T"): PdfString("group"), PdfName("FT"): PdfName("Tx"), PdfName("Ff"): PdfNumber(1 | 1 << 12),
        PdfName("V"): PdfString("inherited"), PdfName("DV"): PdfString("default"), PdfName("MaxLen"): PdfNumber(20),
        PdfName("Kids"): PdfArray([]),
    }))
    engine._resolve(parent).mapping[PdfName("Kids")].append(_widget(engine, 0, (72, 700, 272, 724), parent=parent, T=PdfString("child")))
    fields.append(parent)

    # Two widgets on two pages, the second without /P and its corners reversed;
    # TU and TM on the field, which the widgets do not repeat.
    twice = _register(engine, PdfDictionary({
        PdfName("T"): PdfString("twice"), PdfName("FT"): PdfName("Tx"), PdfName("V"): PdfString("both"),
        PdfName("TU"): PdfString("Tooltip ü"), PdfName("TM"): PdfString("export_name"), PdfName("Kids"): PdfArray([]),
    }))
    kids = engine._resolve(twice).mapping[PdfName("Kids")]
    kids.append(_widget(engine, 0, (300, 700, 400, 724), parent=twice))
    kids.append(_widget(engine, 1, (400, 624, 300, 600), parent=twice, own_page=False))
    fields.append(twice)

    # Radio buttons whose on states are /0 and /1, with /Opt texts.
    radio = _register(engine, PdfDictionary({
        PdfName("T"): PdfString("pick"), PdfName("FT"): PdfName("Btn"), PdfName("Ff"): PdfNumber(1 << 15),
        PdfName("V"): PdfName("1"), PdfName("Opt"): PdfArray([PdfString("Één"), PdfString("Twee")]),
        PdfName("Kids"): PdfArray([]),
    }))
    for index, rect in enumerate(((72, 500, 90, 518), (100, 500, 118, 518))):
        engine._resolve(radio).mapping[PdfName("Kids")].append(
            _widget(engine, 0, rect, parent=radio, AP=_appearance(engine, str(index), "Off"), AS=PdfName("Off"))
        )
    fields.append(radio)

    # A check box on "Ja", field and widget merged; and a password, comb text.
    fields.append(_widget(engine, 0, (72, 450, 90, 468), T=PdfString("tick"), FT=PdfName("Btn"), V=PdfName("Ja"),
                          AS=PdfName("Ja"), AP=_appearance(engine, "Ja", "Off")))
    fields.append(_widget(engine, 0, (72, 400, 200, 418), T=PdfString("pin"), FT=PdfName("Tx"),
                          Ff=PdfNumber(1 << 13 | 1 << 24 | 2), MaxLen=PdfNumber(4), V=PdfString("1234")))

    # A combo box whose options come from its parent: a pair and a string.
    options = PdfArray([PdfArray([PdfString("x"), PdfString("Ex")]), PdfString("Why")])
    choices = _register(engine, PdfDictionary({
        PdfName("T"): PdfString("choices"), PdfName("FT"): PdfName("Ch"), PdfName("Ff"): PdfNumber(1 << 17),
        PdfName("Opt"): options, PdfName("Kids"): PdfArray([]),
    }))
    engine._resolve(choices).mapping[PdfName("Kids")].append(
        _widget(engine, 1, (72, 400, 200, 418), parent=choices, T=PdfString("inner"), V=PdfString("Why"))
    )
    fields.append(choices)

    # States that are off, a one-item selection, and flags on fields of a
    # type they do not belong to.
    fields.append(_widget(engine, 0, (72, 350, 90, 368), T=PdfString("unticked"), FT=PdfName("Btn"),
                          V=PdfName("Off"), DV=PdfName("Off"), AP=_appearance(engine, "Yes", "Off")))
    radio_off = _register(engine, PdfDictionary({
        PdfName("T"): PdfString("unpicked"), PdfName("FT"): PdfName("Btn"), PdfName("Ff"): PdfNumber(1 << 15),
        PdfName("V"): PdfName("Off"), PdfName("Kids"): PdfArray([]),
    }))
    engine._resolve(radio_off).mapping[PdfName("Kids")].append(
        _widget(engine, 0, (72, 300, 90, 318), parent=radio_off, AP=_appearance(engine, "a", "Off"))
    )
    fields.append(radio_off)
    fields.append(_widget(engine, 1, (72, 300, 200, 360), T=PdfString("one"), FT=PdfName("Ch"),
                          Ff=PdfNumber(1 << 21 | 1 << 12 | 1 << 24), MaxLen=PdfNumber(3),
                          Opt=PdfArray([PdfString("a"), PdfString("b")]), V=PdfArray([PdfString("b")])))
    fields.append(_widget(engine, 1, (72, 250, 200, 268), T=PdfString("quiet"), FT=PdfName("Tx"),
                          Ff=PdfNumber(1 << 2 | 1 << 18 | 1 << 21)))
    # A widget no page lists: its /P names page 0, but no reader shows it there.
    fields.append(_register(engine, PdfDictionary({
        PdfName("T"): PdfString("orphan"), PdfName("FT"): PdfName("Tx"), PdfName("Type"): PdfName("Annot"),
        PdfName("Subtype"): PdfName("Widget"), PdfName("Rect"): PdfArray([PdfNumber(v) for v in (0, 0, 10, 10)]),
        PdfName("P"): PdfIndirectReference(engine._page_obj_ids[0], 0),
    })))

    catalog = engine._resolve(engine._cos_doc.trailer.mapping[PdfName("Root")])
    catalog.mapping[PdfName("AcroForm")] = PdfDictionary({PdfName("Fields"): PdfArray(fields)})
    return _reloaded(document)


def test_inherited_attributes(crafted):
    child = _field(crafted, "group.child")
    # The value was read as None: /V was only looked for on the field itself.
    assert (child.value, child.default_value) == ("inherited", "default")
    assert (child.flags, child.read_only, child.multiline, child.max_length) == (4097, True, True, 20)
    assert child.partial_name == "child"


def test_a_field_with_separate_widgets(crafted):
    twice = _field(crafted, "twice")
    assert twice.widgets == [
        FieldWidget(0, (300.0, 700.0, 400.0, 724.0)),
        FieldWidget(1, (300.0, 600.0, 400.0, 624.0)),  # found by /Annots, corners in order
    ]
    assert (twice.alternate_name, twice.mapping_name) == ("Tooltip ü", "export_name")


def test_button_export_values(crafted):
    assert _field(crafted, "pick").export_values == ["0", "1"]
    assert _field(crafted, "pick").value == "1"
    tick = _field(crafted, "tick")
    assert (tick.export_values, tick.value, tick.default_value) == (["Ja"], True, False)


def test_text_and_choice_details(crafted):
    pin = _field(crafted, "pin")
    assert (pin.password, pin.comb, pin.max_length, pin.required) == (True, True, 4, True)
    inner = _field(crafted, "choices.inner")
    assert inner.options == [("x", "Ex"), ("Why", "Why")]
    assert (inner.value, inner.editable, inner.multi_select) == ("Why", False, False)


def test_off_states_and_single_selections(crafted):
    unticked = _field(crafted, "unticked")
    assert (unticked.value, unticked.default_value, unticked.export_values) == (False, False, ["Yes"])
    assert _field(crafted, "unpicked").value is None  # a radio group at Off
    assert _field(crafted, "one").value == "b"  # a one-item array is the item


def test_flags_belong_to_their_field_type(crafted):
    one = _field(crafted, "one")  # a list box carrying text-field bits and /MaxLen
    assert one.multi_select and not one.multiline and not one.comb and one.max_length is None
    quiet = _field(crafted, "quiet")  # a text field carrying choice bits
    assert quiet.no_export and not quiet.multi_select and not quiet.editable
    assert _field(crafted, "pick").options == []  # /Opt on buttons is not a list of options


def test_a_widget_no_page_lists_is_on_no_page(crafted):
    assert _field(crafted, "orphan").widgets == [FieldWidget(None, (0.0, 0.0, 10.0, 10.0))]


def test_only_widget_annotations_are_widgets_and_a_parent_loop_ends():
    document = Document()
    document.pages.add()
    engine = document._engine_pdf
    engine._ensure_cos()
    engine._ensure_page_cache()
    field = _register(engine, PdfDictionary({
        PdfName("T"): PdfString("looped"), PdfName("FT"): PdfName("Tx"), PdfName("Kids"): PdfArray([]),
    }))
    kids = engine._resolve(field).mapping[PdfName("Kids")]
    kids.append(_widget(engine, 0, (72, 700, 272, 724), parent=field))
    # A kid that is no annotation at all, which pdf.js does not draw either.
    kids.append(_register(engine, PdfDictionary({PdfName("Parent"): field, PdfName("Rect"): PdfArray([PdfNumber(0)] * 4)})))
    # The field's /Parent leads back to itself.
    engine._resolve(field).mapping[PdfName("Parent")] = field
    catalog = engine._resolve(engine._cos_doc.trailer.mapping[PdfName("Root")])
    catalog.mapping[PdfName("AcroForm")] = PdfDictionary({PdfName("Fields"): PdfArray([field])})
    looped = _field(_reloaded(document), "looped")
    assert looped.widgets == [FieldWidget(0, (72.0, 700.0, 272.0, 724.0))]
    assert looped.max_length is None and looped.flags == 0


# --- changing flags ----------------------------------------------------------------------------------


def test_read_only_and_required_are_written_to_the_field(crafted):
    document = _reloaded(crafted)
    child = _field(document, "group.child")
    child.read_only = False
    child.required = True
    twice = _field(document, "twice")
    twice.read_only = True
    reopened = _reloaded(document)
    child = _field(reopened, "group.child")
    # Multiline came from the parent and stays: the flags start from what was inherited.
    assert (child.read_only, child.required, child.multiline) == (False, True, True)
    assert _field(reopened, "twice").read_only
    with pytest.raises(TypeError):
        child.read_only = 1


def test_a_setter_changes_one_field_not_its_siblings():
    document = Document()
    page = document.pages.add()
    document.form.add_text_field("group.a", page, (72, 700, 272, 724))
    document.form.add_text_field("group.b", page, (72, 650, 272, 674))
    document = _reloaded(document)
    _field(document, "group.a").read_only = True
    reopened = _reloaded(document)
    assert (_field(reopened, "group.a").read_only, _field(reopened, "group.b").read_only) == (True, False)


def test_a_removed_field_has_nothing_to_report(authored):
    document = _reloaded(authored)
    field = _field(document, "go")
    field.remove()
    with pytest.raises(KeyError):
        field.widgets
