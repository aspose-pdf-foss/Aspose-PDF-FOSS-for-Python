"""Form data out and in: FDF (ISO 32000-1 12.7.8) and XFDF (ISO 19444-1).

A form's data could not leave the document or come into it except field by
field. ``Form.export_fdf``/``export_xfdf`` and ``import_fdf``/``import_xfdf``
now carry it, and Apache PDFBox 3.0.8 is the reference both ways: for this
module's forms our export parses to exactly the entries PDFBox exports, PDFBox
fills a form from our files to exactly the state the data came from, and we
fill one from PDFBox's (the two files below are PDFBox's own output). Where
PDFBox is wrong it is not followed: it copies a signature's dictionary into FDF
and stops writing XFDF at a signed field, and it keeps only the last of several
``<value>`` elements when it imports a multi-select list box.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document, PdfLoadLimits
from aspose_pdf.engine.cos import PdfName
from aspose_pdf.engine.form_data import read_fdf, read_xfdf
from aspose_pdf.engine.form_fields import field_dictionary, widget_dictionaries
from aspose_pdf.exceptions import PdfResourceLimitException, PdfValidationException
from aspose_pdf.load_limits import _LoadBudget

PDFBOX_FDF = (
    b'%FDF-1.2\n'
    b'%\xf6\xe4\xfc\xdf\n'
    b'1 0 obj\n'
    b'<<\n'
    b'/FDF 2 0 R\n'
    b'>>\n'
    b'endobj\n'
    b'2 0 obj\n'
    b'<<\n'
    b'/ID [<FAAB3B30814992480DA59F67A5653A4C> <F37777D126AAF8EF4A359EADB223F1FC>]\n'
    b'/Fields [3 0 R 4 0 R 5 0 R 6 0 R 7 0 R 8 0 R 9 0 R]\n'
    b'>>\n'
    b'endobj\n'
    b'3 0 obj\n'
    b'<<\n'
    b'/T (person)\n'
    b'/Kids [10 0 R 11 0 R]\n'
    b'>>\n'
    b'endobj\n'
    b'4 0 obj\n'
    b'<<\n'
    b'/T (agree)\n'
    b'/V /Agree\n'
    b'>>\n'
    b'endobj\n'
    b'5 0 obj\n'
    b'<<\n'
    b'/T (size)\n'
    b'/V /M\n'
    b'>>\n'
    b'endobj\n'
    b'6 0 obj\n'
    b'<<\n'
    b'/T (colours)\n'
    b'/V [(g) (Blue)]\n'
    b'>>\n'
    b'endobj\n'
    b'7 0 obj\n'
    b'<<\n'
    b'/T (country)\n'
    b'/V (DE)\n'
    b'>>\n'
    b'endobj\n'
    b'8 0 obj\n'
    b'<<\n'
    b'/T (go)\n'
    b'>>\n'
    b'endobj\n'
    b'9 0 obj\n'
    b'<<\n'
    b'/T (sig)\n'
    b'>>\n'
    b'endobj\n'
    b'10 0 obj\n'
    b'<<\n'
    b'/T (name)\n'
    b'/V (Alice)\n'
    b'>>\n'
    b'endobj\n'
    b'11 0 obj\n'
    b'<<\n'
    b'/T (city)\n'
    b'/V ()\n'
    b'>>\n'
    b'endobj\n'
    b'xref\n'
    b'0 12\n'
    b'0000000000 65535 f\r\n'
    b'0000000015 00000 n\r\n'
    b'0000000047 00000 n\r\n'
    b'0000000196 00000 n\r\n'
    b'0000000251 00000 n\r\n'
    b'0000000293 00000 n\r\n'
    b'0000000330 00000 n\r\n'
    b'0000000380 00000 n\r\n'
    b'0000000422 00000 n\r\n'
    b'0000000451 00000 n\r\n'
    b'0000000481 00000 n\r\n'
    b'0000000524 00000 n\r\n'
    b'trailer\n'
    b'<<\n'
    b'/Root 1 0 R\n'
    b'/Size 12\n'
    b'>>\n'
    b'startxref\n'
    b'562\n'
    b'%%EOF\n'
    b'\n'
)
PDFBOX_XFDF = '<?xml version="1.0" encoding="UTF-8"?>\n<xfdf xmlns="http://ns.adobe.com/xfdf/" xml:space="preserve">\n<ids original="FAAB3B30814992480DA59F67A5653A4C" modified="F37777D126AAF8EF4A359EADB223F1FC" />\n<fields>\n<field name="person">\n<field name="name">\n<value>Alice</value>\n</field>\n<field name="city">\n<value></value>\n</field>\n</field>\n<field name="agree">\n<value>Agree</value>\n</field>\n<field name="size">\n<value>M</value>\n</field>\n<field name="colours">\n<value>g</value>\n<value>Blue</value>\n</field>\n<field name="country">\n<value>DE</value>\n</field>\n<field name="go">\n</field>\n<field name="sig">\n</field>\n</fields>\n</xfdf>\n'


def _form(**values) -> Document:
    """The form PDFBox exported the files above from, with *values* instead."""
    document = Document()
    first, second = document.pages.add(), document.pages.add()
    form = document.form
    form.add_text_field("person.name", first, (72, 700, 272, 724), value=values.get("name", "Alice"), multiline=True, read_only=True)
    form.add_text_field("person.city", second, (72, 700, 272, 724), value=values.get("city", ""), required=True)
    form.add_checkbox("agree", first, (72, 650, 90, 668), checked=values.get("agree", True), on_value="Agree")
    form.add_radio_group("size", first, [("S", (72, 600, 90, 618)), ("M", (100, 600, 118, 618)), ("L", (128, 600, 146, 618))], value=values.get("size", "M"))
    form.add_list_box("colours", first, (300, 500, 450, 600), [("r", "Red"), ("g", "Green"), "Blue"], value=values.get("colours", ["g", "Blue"]), multiselect=True)
    form.add_combo_box("country", second, (300, 500, 450, 520), ["NL", "DE"], value=values.get("country", "DE"), editable=True)
    form.add_push_button("go", first, (300, 400, 380, 430))
    form.add_signature_field("sig", second, (300, 300, 450, 350))
    return _reloaded(document)


def _reloaded(document: Document, **kwargs) -> Document:
    buffer = io.BytesIO()
    document.save(buffer)
    return Document(io.BytesIO(buffer.getvalue()), **kwargs)


def _values(document: Document) -> dict:
    return {field.name: field.value for field in document.form.fields}


def _entries(data: bytes, reader) -> list:
    return [(e.name, e.value if e.has_value else None, e.flags) for e in reader(data, _LoadBudget(PdfLoadLimits()))]


SOURCE = {
    "person.name": "Alice", "person.city": "", "agree": True, "size": "M",
    "colours": ["g", "Blue"], "country": "DE", "go": None, "sig": None,
}


def _other() -> Document:
    return _form(name="Bob", city="Paris", agree=False, size="L", colours=["r"], country="NL")


# --- export -------------------------------------------------------------------------------------


def test_our_export_is_what_pdfbox_exports():
    document = _form()
    assert _entries(document.form.export_fdf(), read_fdf) == _entries(PDFBOX_FDF, read_fdf)
    assert _entries(document.form.export_xfdf(), read_xfdf) == _entries(PDFBOX_XFDF.encode(), read_xfdf)


def test_the_fdf_carries_values_as_the_document_stores_them():
    fdf = _form().form.export_fdf()
    assert fdf.startswith(b"%FDF-1.2\n")
    assert b"<< /T (agree) /V /Agree >>" in fdf and b"<< /T (size) /V /M >>" in fdf  # names
    assert b"/V [ (g) (Blue) ]" in fdf and b"<< /T (go) >>" in fdf  # an array; no value
    assert b"/Kids [ << /T (name) /V (Alice) >>" in fdf  # partial names under the parent
    assert b"/ID [ <" in fdf  # the document's /ID


def test_the_xfdf_is_namespaced_nested_xml():
    import xml.etree.ElementTree as ElementTree

    root = ElementTree.fromstring(_form(name="Ünï ✓ <&>").form.export_xfdf())
    ns = "{http://ns.adobe.com/xfdf/}"
    assert root.tag == ns + "xfdf" and root.get("{http://www.w3.org/XML/1998/namespace}space") == "preserve"
    person = root.find(f"{ns}fields/{ns}field[@name='person']")
    assert person.find(f"{ns}field[@name='name']/{ns}value").text == "Ünï ✓ <&>"
    assert [v.text for v in root.findall(f"{ns}fields/{ns}field[@name='colours']/{ns}value")] == ["g", "Blue"]


def test_a_signature_is_not_form_data():
    from aspose_pdf.engine.signing import SigningUtils

    document = _form()
    cert, key = SigningUtils.create_self_signed_cert()
    document.sign("sig", certificate=cert, private_key=key)
    signed = _reloaded(document)
    assert b"<< /T (sig) >>" in signed.form.export_fdf()
    assert "sig" in signed.form.export_xfdf().decode() and b"ByteRange" not in signed.form.export_xfdf()
    # Importing PDFBox's FDF of a signed file does not copy the signature in.
    pdfbox_signed = PDFBOX_FDF.replace(b"/T (sig)\n", b"/T (sig)\n/V << /Type /Sig /Contents <00> >>\n")
    target = _other()
    target.form.import_fdf(pdfbox_signed)
    assert field_dictionary(target._engine_pdf, "sig").mapping.get(PdfName("V")) is None
    # Nor does a text value for it.
    target.form.import_fdf(PDFBOX_FDF.replace(b"/T (sig)\n", b"/T (sig)\n/V (forged)\n"))
    assert field_dictionary(target._engine_pdf, "sig").mapping.get(PdfName("V")) is None


def test_export_writes_a_path_or_a_stream(tmp_path):
    document = _form()
    stream = io.BytesIO()
    data = document.form.export_xfdf(stream)
    assert stream.getvalue() == data
    document.form.export_fdf(tmp_path / "form.fdf")
    assert (tmp_path / "form.fdf").read_bytes() == document.form.export_fdf()


# --- import -------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["fdf", "xfdf"])
def test_pdfbox_files_fill_the_form(kind):
    target = _other()
    importer = target.form.import_fdf if kind == "fdf" else target.form.import_xfdf
    names = importer(PDFBOX_FDF if kind == "fdf" else PDFBOX_XFDF.encode())
    assert _values(target) == SOURCE
    assert set(names) == {"person.name", "person.city", "agree", "size", "colours", "country"}
    assert _values(_reloaded(target)) == SOURCE


@pytest.mark.parametrize("kind", ["fdf", "xfdf"])
def test_our_files_round_trip_and_redraw_the_buttons(kind):
    source = _form()
    data = source.form.export_fdf() if kind == "fdf" else source.form.export_xfdf()
    target = _other()
    (target.form.import_fdf if kind == "fdf" else target.form.import_xfdf)(io.BytesIO(data))
    assert _values(_reloaded(target)) == SOURCE
    engine = target._engine_pdf
    states = [engine._resolve(w.mapping.get(PdfName("AS"))) for w in widget_dictionaries(engine, field_dictionary(engine, "size"))]
    assert states == [PdfName("Off"), PdfName("M"), PdfName("Off")]
    agree = widget_dictionaries(engine, field_dictionary(engine, "agree"))
    assert [engine._resolve(w.mapping.get(PdfName("AS"))) for w in agree] == [PdfName("Agree")]


def test_a_value_on_a_parent_goes_to_the_kids_without_their_own():
    document = Document()
    page = document.pages.add()
    document.form.add_text_field("grp.x", page, (72, 700, 272, 724), value="x0")
    document.form.add_text_field("grp.y", page, (72, 650, 272, 674), value="y0")
    document = _reloaded(document)
    del field_dictionary(document._engine_pdf, "grp.x").mapping[PdfName("V")]
    fdf = (
        b"%FDF-1.2\n1 0 obj\n<< /FDF << /Fields [ << /T (grp) /V (from-parent) /Kids [ << /T (y) /V (own-y) >> ] >> "
        b"<< /T (nosuch) /V (z) >> ] >> >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
    )
    # PDFBox: the parent gets the value, a kid with its own keeps it, an
    # unknown field is skipped.
    assert document.form.import_fdf(fdf) == ["grp", "grp.y"]
    assert _values(_reloaded(document)) == {"grp.x": "from-parent", "grp.y": "own-y"}


def test_a_one_item_array_is_its_item_and_a_nameless_node_is_transparent():
    # pdf.js, qpdf and MuPDF all name the kid of a parent without /T "inner";
    # the form used to lose it (and PDFBox stops writing XFDF there).
    document = Document()
    page = document.pages.add()
    document.form.add_text_field("box.inner", page, (72, 700, 272, 724), value="old")
    document = _reloaded(document)
    engine = document._engine_pdf
    # Take the name off the parent: its kid is then "inner", not "box.inner".
    parent = engine._resolve(field_dictionary(engine, "box.inner").mapping[PdfName("Parent")])
    del parent.mapping[PdfName("T")]
    document = _reloaded(document)
    assert [field.name for field in document.form.fields] == ["inner"]
    assert _entries(document.form.export_fdf(), read_fdf) == [("inner", "old", {})]
    fdf = b"%FDF-1.2\n1 0 obj\n<< /FDF << /Fields [ << /T (inner) /V [ (one) ] >> ] >> >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
    assert document.form.import_fdf(fdf) == ["inner"]
    assert _values(_reloaded(document)) == {"inner": "one"}


def test_fdf_flags_change_fields_and_widgets():
    document = _form()
    fdf = (
        b"%FDF-1.2\n1 0 obj\n<< /FDF << /Fields [ "
        b"<< /T (country) /SetFf 1 /SetF 2 >> "  # read-only; a hidden widget
        b"<< /T (person) /Kids [ << /T (name) /ClrFf 1 >> << /T (city) /Ff 3 /F 4 >> ] >> "
        b"] >> >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
    )
    document.form.import_fdf(fdf)
    document = _reloaded(document)
    fields = {field.name: field for field in document.form.fields}
    assert (fields["country"].read_only, fields["country"].editable) == (True, True)
    assert not fields["person.name"].read_only and fields["person.name"].multiline
    assert fields["person.city"].flags == 3
    engine = document._engine_pdf

    def flags(name):
        widgets = widget_dictionaries(engine, field_dictionary(engine, name))
        return [int(engine._resolve(w.mapping[PdfName("F")]).value) for w in widgets]

    assert flags("country")[0] & 2 and flags("person.city") == [4]


def test_an_inherited_field_type_takes_a_list():
    # _value_to_pdf read /FT and /Ff from the field alone: a kid of a list box
    # parent was taken for a text field and could not take several values.
    document = Document()
    page = document.pages.add()
    document.form.add_list_box("pick", page, (72, 600, 200, 700), ["a", "b", "c"], multiselect=True)
    document = _reloaded(document)
    field = field_dictionary(document._engine_pdf, "pick")
    parent_ft, parent_ff = field.mapping.pop(PdfName("FT")), field.mapping.pop(PdfName("Ff"))
    from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfString

    parent = document._engine_pdf._cos_doc.register_object(PdfDictionary({
        PdfName("T"): PdfString("outer"), PdfName("FT"): parent_ft, PdfName("Ff"): parent_ff,
        PdfName("Kids"): PdfArray([]),
    }))
    acro = document._engine_pdf._resolve(document._engine_pdf._resolve(document._engine_pdf._cos_doc.trailer.mapping[PdfName("Root")]).mapping[PdfName("AcroForm")])
    fields = document._engine_pdf._resolve(acro.mapping[PdfName("Fields")])
    reference = next(item for item in fields.items if document._engine_pdf._resolve(item) is field)
    fields.items[fields.items.index(reference)] = parent
    document._engine_pdf._resolve(parent).mapping[PdfName("Kids")].append(reference)
    field.mapping[PdfName("Parent")] = parent
    document = _reloaded(document)
    document.form.import_xfdf(
        b'<xfdf xmlns="http://ns.adobe.com/xfdf/"><fields><field name="outer"><field name="pick">'
        b"<value>a</value><value>c</value></field></field></fields></xfdf>"
    )
    assert _values(_reloaded(document)) == {"outer.pick": ["a", "c"]}


def test_an_encrypted_document_is_exported_in_the_clear_and_filled():
    document = _form()
    document.encrypt("user", "owner")
    encrypted = _reloaded(document, password="user")
    assert _entries(encrypted.form.export_fdf(), read_fdf) == _entries(PDFBOX_FDF, read_fdf)
    target = _other()
    target.encrypt("user", "owner")
    target = _reloaded(target, password="user")
    target.form.import_xfdf(PDFBOX_XFDF.encode())
    assert _values(_reloaded(target, password="user")) == SOURCE


# --- what is refused ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "data", "message"),
    [
        ("xfdf", b'<!DOCTYPE x [<!ENTITY a "aaaa">]><xfdf xmlns="http://ns.adobe.com/xfdf/"/>', "DOCTYPE"),
        ("xfdf", b"<xfdf><fields>", "well-formed"),
        ("xfdf", b"<html/>", "<xfdf>"),
        ("fdf", b"%PDF-1.7\n", "%FDF-"),
        ("fdf", b"%FDF-1.2\n1 0 obj\n<< /Other 1 >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n", "/FDF"),
    ],
    ids=["xfdf-doctype", "xfdf-broken", "xfdf-not-xfdf", "fdf-header", "fdf-no-fdf"],
)
def test_malformed_data_is_refused(kind, data, message):
    document = _form()
    with pytest.raises(PdfValidationException, match=message):
        (document.form.import_fdf if kind == "fdf" else document.form.import_xfdf)(data)
    assert _values(document) == SOURCE


def test_the_size_limit_applies():
    document = Document(io.BytesIO(_form()._engine_pdf.to_bytes()), limits=PdfLoadLimits(max_input_bytes=200_000))
    with pytest.raises(PdfResourceLimitException):
        document.form.import_xfdf(b"<xfdf>" + b" " * 300_000 + b"</xfdf>")
    with pytest.raises(TypeError):
        document.form.import_fdf(12)
