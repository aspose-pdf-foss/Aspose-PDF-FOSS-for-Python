"""Setting a field's value changes what a reader draws.

An appearance stream *is* what a reader draws. ISO 32000-1 12.7.3.3 lets a
reader trust it and never look at ``/V`` unless the AcroForm sets
``/NeedAppearances`` -- and this library writes ``/NeedAppearances false``,
which is a promise that the streams are current.

Assigning to ``Field.value`` wrote ``/V`` and stopped there. The field kept the
appearance it was authored with, so a filled form showed the *old* value in
every reader that follows the rule, while readers that regenerate anyway showed
the new one. Two readers disagreeing about a form's contents is worse than
either answer on its own.

The generator that builds these streams was already here -- ``generate_
appearances()`` runs it over the whole form. The value setter now runs it over
the one field it changed.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import PdfDictionary, PdfName, PdfStream


def _authored(make) -> Document:
    document = Document()
    document.pages.add()
    make(document.form)
    return document


def _reopened(document: Document) -> Document:
    buffer = io.BytesIO()
    document.save(buffer)
    return Document(io.BytesIO(buffer.getvalue()))


def _widgets(document: Document, name: str) -> list[PdfDictionary]:
    pdf = document._engine_pdf
    root = pdf._resolve(pdf._cos_doc.trailer.mapping[PdfName("Root")])
    acro = pdf._resolve(root.mapping[PdfName("AcroForm")])
    for ref in pdf._resolve(acro.mapping[PdfName("Fields")]).items:
        field = pdf._resolve(ref)
        title = pdf._resolve(field.mapping.get(PdfName("T")))
        if title is None or title.value.decode("latin-1").strip("\x00") != name:
            continue
        kids = pdf._resolve(field.mapping.get(PdfName("Kids")))
        return [pdf._resolve(k) for k in kids.items] if kids else [field]
    raise AssertionError(f"no field named {name!r}")


def _drawn(document: Document, name: str) -> str:
    """What the field's widgets would put on the page, as text."""
    pdf = document._engine_pdf
    parts = []
    for widget in _widgets(document, name):
        ap = pdf._resolve(widget.mapping.get(PdfName("AP")))
        if ap is None:
            parts.append("<no appearance>")
            continue
        normal = pdf._resolve(ap.mapping.get(PdfName("N")))
        if isinstance(normal, PdfStream):
            parts.append(normal.content.decode("latin-1"))
        else:  # a states dictionary: /AS picks the one in force
            state = pdf._resolve(widget.mapping.get(PdfName("AS")))
            parts.append(f"state={state.name if state else None}")
    return " || ".join(parts)


# --- text and choice fields show their text ---------------------------------


def test_a_text_field_redraws_when_its_value_changes():
    document = _authored(
        lambda form: form.add_text_field("t", 0, (40, 700, 300, 730), value="Hello")
    )
    assert "(Hello) Tj" in _drawn(document, "t")
    document.form["t"].value = "Goodbye"
    drawn = _drawn(document, "t")
    assert "(Goodbye) Tj" in drawn
    assert "(Hello)" not in drawn


def test_the_change_survives_a_save_and_reload():
    document = _authored(
        lambda form: form.add_text_field("t", 0, (40, 700, 300, 730), value="Hello")
    )
    document.form["t"].value = "Goodbye"
    assert "(Goodbye) Tj" in _drawn(_reopened(document), "t")


def test_a_value_set_on_a_reopened_document_redraws_too():
    document = _reopened(
        _authored(
            lambda form: form.add_text_field("t", 0, (40, 700, 300, 730), value="Hello")
        )
    )
    document.form["t"].value = "Goodbye"
    assert "(Goodbye) Tj" in _drawn(document, "t")


def test_a_multiline_field_redraws():
    document = _authored(
        lambda form: form.add_text_field(
            "m", 0, (40, 600, 300, 690), value="first", multiline=True
        )
    )
    document.form["m"].value = "second"
    assert "(second) Tj" in _drawn(document, "m")


def test_a_combo_box_redraws():
    document = _authored(
        lambda form: form.add_combo_box(
            "k", 0, (40, 450, 200, 475), ["one", "two"], value="one"
        )
    )
    document.form["k"].value = "two"
    drawn = _drawn(document, "k")
    assert "(two) Tj" in drawn
    assert "(one)" not in drawn


def test_a_list_box_moves_its_highlight():
    document = _authored(
        lambda form: form.add_list_box(
            "l", 0, (40, 350, 200, 430), ["one", "two", "three"], value="one"
        )
    )
    before = _drawn(document, "l")
    document.form["l"].value = "three"
    after = _drawn(document, "l")
    # Every option is drawn either way; what moves is the selected row's band.
    assert "(one) Tj" in after and "(three) Tj" in after
    assert before != after


def test_an_emptied_field_stops_showing_its_old_value():
    document = _authored(
        lambda form: form.add_text_field("t", 0, (40, 700, 300, 730), value="Hello")
    )
    document.form["t"].value = ""
    assert "(Hello)" not in _drawn(document, "t")


# --- buttons move the state their /AS names ---------------------------------


def test_a_checkbox_points_at_the_state_it_now_holds():
    document = _authored(
        lambda form: form.add_checkbox("c", 0, (40, 650, 60, 670), checked=False)
    )
    assert _drawn(document, "c") == "state=/Off"
    document.form["c"].value = True
    assert _drawn(document, "c") == "state=/Yes"
    document.form["c"].value = False
    assert _drawn(document, "c") == "state=/Off"


def test_a_radio_group_moves_the_state_between_its_widgets():
    document = _authored(
        lambda form: form.add_radio_group(
            "r", 0, [("a", (40, 500, 60, 520)), ("b", (80, 500, 100, 520))], value="a"
        )
    )
    assert _drawn(document, "r") == "state=/a || state=/Off"
    document.form["r"].value = "b"
    assert _drawn(document, "r") == "state=/Off || state=/b"


# --- and it is still one field that is touched ------------------------------


def test_only_the_named_field_is_rebuilt():
    def make(form):
        form.add_text_field("one", 0, (40, 700, 300, 730), value="first")
        form.add_text_field("two", 0, (40, 650, 300, 680), value="second")

    document = _authored(make)
    untouched = _drawn(document, "two")
    document.form["one"].value = "changed"
    assert "(changed) Tj" in _drawn(document, "one")
    assert _drawn(document, "two") == untouched


def test_another_field_s_hand_made_appearance_is_left_alone():
    # Rebuilding the whole form on every assignment would give the same drawing
    # for fields whose value did not change -- and quietly throw away one an
    # author had customised. Only the field that changed is rebuilt.
    def make(form):
        form.add_text_field("one", 0, (40, 700, 300, 730), value="first")
        form.add_text_field("two", 0, (40, 650, 300, 680), value="second")

    document = _authored(make)
    pdf = document._engine_pdf
    widget = _widgets(document, "two")[0]
    normal = pdf._resolve(
        pdf._resolve(widget.mapping[PdfName("AP")]).mapping[PdfName("N")]
    )
    normal.content = b"q 1 0 0 rg 0 0 10 10 re f Q"

    document.form["one"].value = "changed"
    assert "(changed) Tj" in _drawn(document, "one")
    assert _drawn(document, "two") == "q 1 0 0 rg 0 0 10 10 re f Q"


def test_setting_an_unknown_field_changes_nothing():
    document = _authored(
        lambda form: form.add_text_field("t", 0, (40, 700, 300, 730), value="Hello")
    )
    before = _drawn(document, "t")
    with pytest.raises(Exception):
        document.form["absent"].value = "x"
    assert _drawn(document, "t") == before


# --- the inherited attributes have one definition ---------------------------


def test_an_acroform_hands_the_same_attributes_down_either_way():
    # The bulk generator and the per-field one used to build this dictionary
    # separately, and the bulk one never seeded `rv` -- so a rich-text field
    # could inherit an ancestor's /RV on one path and not the other.
    document = _authored(
        lambda form: form.add_text_field("t", 0, (40, 700, 300, 730), value="Hello")
    )
    pdf = document._engine_pdf
    root = pdf._resolve(pdf._cos_doc.trailer.mapping[PdfName("Root")])
    acro = pdf._resolve(root.mapping[PdfName("AcroForm")])
    inherited = pdf._acroform_inherited(acro)
    assert set(inherited) == {"da", "q", "ft", "ff", "v", "rv", "opt"}
