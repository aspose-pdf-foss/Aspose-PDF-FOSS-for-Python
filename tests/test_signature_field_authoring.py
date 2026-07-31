"""Public authoring of empty (unsigned) signature fields.

``Form.add_signature_field`` creates an ``/FT /Sig`` field with a widget on a
page, sets the AcroForm ``/SigFlags`` SignaturesExist bit, carries no value, and
round-trips as a ``signature`` field. It coexists with other field types and can
be removed.
"""

from __future__ import annotations

from io import BytesIO

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName, PdfNumber
from aspose_pdf.exceptions import PdfValidationException


def _round_trip(document: Document) -> Document:
    output = BytesIO()
    document.save(output)
    return Document().load_from(output.getvalue())


def _resolve(document: Document, value):
    return document._engine_pdf._resolve(value)


def _acroform(document: Document) -> PdfDictionary:
    engine = document._engine_pdf
    root = _resolve(document, engine._cos_doc.trailer.mapping[PdfName("Root")])
    return _resolve(document, root.mapping[PdfName("AcroForm")])


def _field_by_name(document: Document, name: str) -> PdfDictionary:
    acro = _acroform(document)
    for ref in _resolve(document, acro.mapping[PdfName("Fields")]).items:
        field = _resolve(document, ref)
        t = _resolve(document, field.mapping.get(PdfName("T")))
        if t is not None and t.value.decode("latin-1") == name:
            return field
    raise AssertionError(f"field {name!r} not found")


def _with_signature_field() -> Document:
    document = Document()
    page = document.pages.add()
    document.form.add_signature_field("Signature1", page, (60, 600, 260, 680))
    return document


def test_add_signature_field_creates_unsigned_sig_field():
    document = _with_signature_field()
    field = document.form["Signature1"]
    assert field.field_type == "signature"

    cos_field = _field_by_name(document, "Signature1")
    assert _resolve(document, cos_field.mapping[PdfName("FT")]).name == "/Sig"
    # An unsigned field carries no value.
    assert PdfName("V") not in cos_field.mapping
    assert PdfName("DV") not in cos_field.mapping
    # Variable-text attributes do not belong on a signature field.
    assert PdfName("DA") not in cos_field.mapping
    assert PdfName("Q") not in cos_field.mapping


def test_signature_field_sets_signatures_exist_flag():
    document = _with_signature_field()
    acro = _acroform(document)
    sig_flags = _resolve(document, acro.mapping[PdfName("SigFlags")])
    assert isinstance(sig_flags, PdfNumber)
    # Bit 1 (SignaturesExist) must be set; AppendOnly (bit 2) must not, since
    # there is no signature to invalidate yet.
    assert int(sig_flags.value) & 1
    assert not int(sig_flags.value) & 2


def test_signature_field_widget_wiring():
    document = _with_signature_field()
    cos_field = _field_by_name(document, "Signature1")
    kids = _resolve(document, cos_field.mapping[PdfName("Kids")])
    assert isinstance(kids, PdfArray) and len(kids.items) == 1
    widget = _resolve(document, kids.items[0])
    assert _resolve(document, widget.mapping[PdfName("Subtype")]).name == "/Widget"
    # The widget links back to its field and forward to a page, has an
    # appearance, and appears in that page's /Annots.
    assert PdfName("Parent") in widget.mapping
    page = _resolve(document, widget.mapping[PdfName("P")])
    assert PdfName("AP") in widget.mapping
    annots = _resolve(document, page.mapping[PdfName("Annots")])
    assert kids.items[0] in annots.items


def test_signature_field_round_trips_as_unsigned():
    reloaded = _round_trip(_with_signature_field())
    field = reloaded.form["Signature1"]
    assert field.field_type == "signature"
    assert field.value is None
    # The reloaded catalog still advertises the signature field.
    acro = _acroform(reloaded)
    assert int(_resolve(reloaded, acro.mapping[PdfName("SigFlags")]).value) & 1


def test_signature_field_coexists_with_other_fields_and_is_removable():
    document = Document()
    page = document.pages.add()
    document.form.add_text_field("Name", page, (40, 700, 240, 720), value="Bob")
    document.form.add_signature_field("Signature1", page, (40, 600, 240, 680))

    reloaded = _round_trip(document)
    assert {f.name: f.field_type for f in reloaded.form} == {
        "Name": "text",
        "Signature1": "signature",
    }

    reloaded.form.remove_field("Signature1")
    assert [f.name for f in reloaded.form] == ["Name"]


def test_signature_field_duplicate_name_is_rejected():
    document = _with_signature_field()
    with pytest.raises(PdfValidationException):
        document.form.add_signature_field(
            "Signature1", document.pages[0], (60, 500, 260, 560)
        )


def test_signature_field_survives_incremental_save():
    # Signature fields are the motivating case for incremental save; adding one
    # to a loaded document and saving incrementally must preserve the original
    # bytes and reload with the field present.
    base = BytesIO()
    doc = Document()
    doc.pages.add()
    doc.save(base)
    original = base.getvalue()

    loaded = Document()
    loaded.load_from(original)
    loaded.form.add_signature_field("Signature1", loaded.pages[0], (40, 600, 240, 680))
    out = BytesIO()
    loaded.save(out, incremental=True)
    whole = out.getvalue()

    assert whole[: len(original)] == original
    reloaded = Document()
    reloaded.load_from(whole)
    assert reloaded.form["Signature1"].field_type == "signature"
