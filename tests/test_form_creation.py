"""Public AcroForm authoring, round-trip editing, removal, and flattening."""

from __future__ import annotations

from io import BytesIO

import pytest

from aspose_pdf import Document, FieldType
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfIndirectReference,
    PdfName,
    PdfNumber,
    PdfStream,
    PdfString,
)
from aspose_pdf.engine.simple_pdf import decode_pdf_text_string
from aspose_pdf.exceptions import PdfValidationException


def _round_trip(document: Document) -> Document:
    output = BytesIO()
    document.save(output)
    return Document().load_from(output.getvalue())


def _resolve(document: Document, value):
    return document._engine_pdf._resolve(value)


def _terminal_fields(document: Document):
    engine = document._engine_pdf
    root = _resolve(document, engine._cos_doc.trailer.mapping[PdfName("Root")])
    acro = _resolve(document, root.mapping[PdfName("AcroForm")])
    roots = _resolve(document, acro.mapping[PdfName("Fields")])
    result = {}

    def visit(field_ref, prefix=""):
        field = _resolve(document, field_ref)
        name_obj = _resolve(document, field.mapping.get(PdfName("T")))
        if not isinstance(name_obj, PdfString):
            return
        local_name = decode_pdf_text_string(name_obj)
        full_name = f"{prefix}.{local_name}" if prefix else local_name
        if PdfName("FT") in field.mapping:
            result[full_name] = (field_ref, field)
        kids = _resolve(document, field.mapping.get(PdfName("Kids")))
        if isinstance(kids, PdfArray):
            for kid_ref in kids.items:
                kid = _resolve(document, kid_ref)
                if isinstance(kid, PdfDictionary) and PdfName("T") in kid.mapping:
                    visit(kid_ref, full_name)

    for root_ref in roots.items:
        visit(root_ref)
    return acro, result


def _build_public_form() -> Document:
    document = Document()
    page = document.pages.add()
    form = document.form
    form.add_text_field(
        "person.name",
        page,
        (40, 700, 240, 725),
        value="Alice",
        required=True,
    )
    form.add_checkbox(
        "accepted",
        page,
        (40, 660, 56, 676),
        checked=True,
        on_value="Accepted",
    )
    form.add_radio_group(
        "delivery",
        page,
        {
            "Email": (40, 620, 56, 636),
            "Post": (80, 620, 96, 636),
        },
        value="Post",
    )
    form.add_list_box(
        "topics",
        page,
        (40, 520, 220, 600),
        [("pdf", "PDF"), ("fonts", "Fonts")],
        value=["pdf", "fonts"],
        multiselect=True,
    )
    form.add_combo_box(
        "priority",
        page,
        (40, 480, 220, 502),
        ["Normal", "High"],
        value="High",
        editable=True,
    )
    form.add_push_button(
        "submit", page, (40, 430, 130, 458), caption="Submit"
    )
    return document


def test_public_form_creation_round_trips_and_remains_editable():
    document = _round_trip(_build_public_form())

    assert [(field.name, field.field_type) for field in document.form] == [
        ("person.name", "text"),
        ("accepted", "checkbox"),
        ("delivery", "radio"),
        ("topics", "listbox"),
        ("priority", "combobox"),
        ("submit", "pushbutton"),
    ]
    assert document.form["person.name"].value == "Alice"
    assert document.form["accepted"].value is True
    assert document.form["delivery"].value == "Post"
    assert document.form["topics"].value == ["pdf", "fonts"]
    assert document.form["priority"].value == "High"
    assert document.form["submit"].value is None

    document.form["person.name"].value = "Bob"
    document.form["accepted"].value = False
    document.form["delivery"].value = "Email"
    document.form["topics"].value = ["fonts"]
    document.form["priority"].value = "Custom"
    assert document.form.generate_appearances() == 7

    edited = _round_trip(document)
    assert edited.form["person.name"].value == "Bob"
    assert edited.form["accepted"].value is False
    assert edited.form["delivery"].value == "Email"
    assert edited.form["topics"].value == "fonts"
    assert edited.form["priority"].value == "Custom"


def test_public_form_emits_interoperable_field_widget_hierarchy_and_flags():
    document = _round_trip(_build_public_form())
    acro, fields = _terminal_fields(document)

    assert set(fields) == {
        "person.name",
        "accepted",
        "delivery",
        "topics",
        "priority",
        "submit",
    }
    assert isinstance(_resolve(document, acro.mapping[PdfName("DA")]), PdfString)
    resources = _resolve(document, acro.mapping[PdfName("DR")])
    fonts = _resolve(document, resources.mapping[PdfName("Font")])
    assert PdfName("Helv") in fonts.mapping

    expected_flags = {
        "person.name": 2,
        "accepted": 0,
        "delivery": 1 << 15,
        "topics": 1 << 21,
        "priority": (1 << 17) | (1 << 18),
        "submit": 1 << 16,
    }
    page = document._engine_pdf._get_page_dict(0)
    page_annots = _resolve(document, page.mapping[PdfName("Annots")])
    widget_numbers = {
        ref.object_number
        for ref in page_annots.items
        if isinstance(ref, PdfIndirectReference)
    }

    for name, (field_ref, field) in fields.items():
        assert isinstance(field_ref, PdfIndirectReference)
        flags = _resolve(document, field.mapping[PdfName("Ff")])
        assert isinstance(flags, PdfNumber)
        assert int(flags.value) == expected_flags[name]
        kids = _resolve(document, field.mapping[PdfName("Kids")])
        assert isinstance(kids, PdfArray) and kids.items
        for widget_ref in kids.items:
            assert isinstance(widget_ref, PdfIndirectReference)
            assert widget_ref.object_number in widget_numbers
            widget = _resolve(document, widget_ref)
            assert _resolve(document, widget.mapping[PdfName("Subtype")]) == PdfName(
                "Widget"
            )
            parent = widget.mapping[PdfName("Parent")]
            assert isinstance(parent, PdfIndirectReference)
            assert parent.object_number == field_ref.object_number
            assert isinstance(widget.mapping[PdfName("P")], PdfIndirectReference)
            assert PdfName("AP") in widget.mapping

    topics = fields["topics"][1]
    selected_indices = _resolve(document, topics.mapping[PdfName("I")])
    assert [item.value for item in selected_indices.items] == [0, 1]
    topics_widget = _resolve(
        document, _resolve(document, topics.mapping[PdfName("Kids")]).items[0]
    )
    topics_ap = _resolve(document, topics_widget.mapping[PdfName("AP")])
    topics_normal = _resolve(document, topics_ap.mapping[PdfName("N")])
    assert isinstance(topics_normal, PdfStream)
    assert b"(PDF) Tj" in topics_normal.content
    assert b"(Fonts) Tj" in topics_normal.content
    assert b"0.153 0.447 0.816 rg" in topics_normal.content

    submit_widget = _resolve(
        document,
        _resolve(document, fields["submit"][1].mapping[PdfName("Kids")]).items[0],
    )
    submit_ap = _resolve(document, submit_widget.mapping[PdfName("AP")])
    submit_normal = _resolve(document, submit_ap.mapping[PdfName("N")])
    assert isinstance(submit_normal, PdfStream)
    assert b"(Submit) Tj" in submit_normal.content


def test_remove_field_removes_widgets_and_prunes_empty_name_parents():
    document = _round_trip(_build_public_form())
    page = document._engine_pdf._get_page_dict(0)
    before = _resolve(document, page.mapping[PdfName("Annots")])
    assert len(before.items) == 7

    removed = document.form["person.name"].remove()
    assert removed.name == "person.name"
    assert "person.name" not in [field.name for field in document.form]
    after = _resolve(document, page.mapping[PdfName("Annots")])
    assert len(after.items) == 6

    reopened = _round_trip(document)
    assert "person.name" not in [field.name for field in reopened.form]
    acro, _fields = _terminal_fields(reopened)
    roots = _resolve(reopened, acro.mapping[PdfName("Fields")])
    root_names = [
        decode_pdf_text_string(
            _resolve(reopened, _resolve(reopened, ref).mapping[PdfName("T")])
        )
        for ref in roots.items
    ]
    assert "person" not in root_names


def test_publicly_created_form_flattens_every_widget_state():
    document = _round_trip(_build_public_form())
    document.flatten()

    assert len(document.form) == 0
    page = document._engine_pdf._get_page_dict(0)
    annots = _resolve(document, page.mapping[PdfName("Annots")])
    assert isinstance(annots, PdfArray) and not annots.items
    assert document._engine_pdf.page_contents[0].count(b" Do") == 7

    output = BytesIO()
    document.save(output)
    assert b"/AcroForm" not in output.getvalue()
    reopened = Document().load_from(output.getvalue())
    assert len(reopened.form) == 0
    assert reopened.pages[0].content.count(b" Do") == 7
    assert b"//FlatAnnot" not in reopened.pages[0].content


def test_public_form_creation_validates_names_pages_values_and_duplicates():
    document = Document()
    page = document.pages.add()
    form = document.form

    with pytest.raises(IndexError):
        form.add_text_field("bad", 1, (0, 0, 10, 10))
    with pytest.raises(PdfValidationException):
        form.add_text_field("bad..name", page, (0, 0, 10, 10))
    with pytest.raises(PdfValidationException):
        form.add_checkbox("bad-state", page, (0, 0, 10, 10), on_value="Not valid")
    with pytest.raises(PdfValidationException):
        form.add_radio_group(
            "radio", page, {"A": (0, 0, 10, 10)}, value="B"
        )

    form.add_text_field("unique", page, (0, 0, 10, 10))
    with pytest.raises(PdfValidationException):
        form.add_text_field("unique", page, (20, 0, 30, 10))

    other_page = Document().pages.add()
    with pytest.raises(PdfValidationException):
        form.add_text_field("foreign", other_page, (0, 0, 10, 10))

    assert FieldType.PUSHBUTTON.value == "PushButton"
