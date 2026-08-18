"""Signing an authored AcroForm signature field in place.

``Form.add_signature_field`` creates the field; ``sign_field`` fills it. The
signature is appended as an incremental update, so the original bytes — and any
signature already in them — survive untouched, and the authored field keeps its
widget, seed value, and lock dictionary.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from cryptography.hazmat.primitives import serialization

from aspose_pdf import Document
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName
from aspose_pdf.engine.sign_field import sign_field
from aspose_pdf.engine.signing import SigningUtils
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.exceptions import PdfSecurityException, PdfValidationException
from aspose_pdf.validation import ValidationOptions, ValidationStatus


@pytest.fixture(scope="module")
def creds():
    return SigningUtils.create_self_signed_cert()


def _options(cert) -> ValidationOptions:
    return ValidationOptions(
        trusted_certificates=[cert.public_bytes(serialization.Encoding.DER)]
    )


def _authored(**kwargs) -> bytes:
    document = Document()
    page = document.pages.add()
    document.form.add_signature_field("Signature1", page, (60, 600, 260, 680), **kwargs)
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def _resolve(doc, value):
    return doc.get_object(value) if hasattr(value, "object_number") else value


def _cos_field(pdf_bytes: bytes, name: str) -> PdfDictionary:
    from aspose_pdf.engine.pdf_parser_cos import PdfCosParser

    doc = PdfCosParser(pdf_bytes).parse()
    root = _resolve(doc, doc.trailer.mapping[PdfName("Root")])
    acro = _resolve(doc, root.mapping[PdfName("AcroForm")])
    for ref in _resolve(doc, acro.mapping[PdfName("Fields")]).items:
        field = _resolve(doc, ref)
        t = _resolve(doc, field.mapping.get(PdfName("T")))
        if t is not None and t.value.decode("latin-1") == name:
            return doc, field
    raise AssertionError(f"field {name!r} not found")


def test_signs_authored_field_and_preserves_original_bytes(creds):
    cert, key = creds
    original = _authored()
    signed = sign_field(original, "Signature1", cert, key, reason="Approved")

    # An incremental update: the original revision is byte-identical.
    assert signed.startswith(original)

    sig = SimplePdf.from_bytes(signed).signatures[0]
    assert sig.name == "Signature1"
    assert sig.sub_filter == "adbe.pkcs7.detached"
    result = sig.validate(_options(cert))
    assert result.status is ValidationStatus.VALID, result.errors


def test_signature_covers_the_document_bytes(creds):
    """A byte flipped inside the signed range breaks validation."""
    cert, key = creds
    signed = sign_field(_authored(), "Signature1", cert, key)

    tampered = bytearray(signed)
    offset = signed.index(b"/MediaBox")
    tampered[offset : offset + 9] = b"/mediaBOX"

    sig = SimplePdf.from_bytes(bytes(tampered)).signatures[0]
    result = sig.validate(_options(cert))
    assert result.status is ValidationStatus.INVALID
    assert any("digest" in err.lower() for err in result.errors), result.errors


def test_authored_field_and_widget_survive_signing(creds):
    cert, key = creds
    signed = sign_field(_authored(), "Signature1", cert, key)

    reloaded = Document().load_from(signed)
    assert reloaded.form["Signature1"].field_type == "signature"

    doc, field = _cos_field(signed, "Signature1")
    assert _resolve(doc, field.mapping[PdfName("FT")]) == PdfName("Sig")
    # /V now points at the signature dictionary, and the widget is still there.
    assert field.mapping.get(PdfName("V")) is not None
    kids = _resolve(doc, field.mapping[PdfName("Kids")])
    assert isinstance(kids, PdfArray) and len(kids.items) == 1


def test_other_fields_are_untouched(creds):
    """Signing must not discard the rest of the form."""
    cert, key = creds
    document = Document()
    page = document.pages.add()
    document.form.add_text_field("Name", page, (60, 700, 260, 730), value="Ada")
    document.form.add_checkbox("Agree", page, (60, 660, 80, 680), checked=True)
    document.form.add_signature_field("Signature1", page, (60, 600, 260, 680))
    output = BytesIO()
    document.save(output)

    signed = sign_field(output.getvalue(), "Signature1", cert, key)
    reloaded = Document().load_from(signed)
    assert {f.name for f in reloaded.form} == {"Name", "Agree", "Signature1"}
    assert reloaded.form["Name"].value == "Ada"


def test_second_signature_leaves_the_first_valid(creds):
    cert, key = creds
    document = Document()
    page = document.pages.add()
    document.form.add_signature_field("Sig1", page, (60, 600, 260, 660))
    document.form.add_signature_field("Sig2", page, (60, 500, 260, 560))
    output = BytesIO()
    document.save(output)

    once = sign_field(output.getvalue(), "Sig1", cert, key)
    twice = sign_field(once, "Sig2", cert, key)
    assert twice.startswith(once)

    signatures = {sig.name: sig for sig in SimplePdf.from_bytes(twice).signatures}
    assert set(signatures) == {"Sig1", "Sig2"}
    for sig in signatures.values():
        assert sig.validate(_options(cert)).status is ValidationStatus.VALID


def test_pades_subfilter(creds):
    cert, key = creds
    signed = sign_field(_authored(), "Signature1", cert, key, pades=True)
    sig = SimplePdf.from_bytes(signed).signatures[0]
    assert sig.sub_filter == "ETSI.CAdES.detached"
    assert sig.validate(_options(cert)).status is ValidationStatus.VALID


def test_rejects_already_signed_field(creds):
    cert, key = creds
    signed = sign_field(_authored(), "Signature1", cert, key)
    with pytest.raises(PdfSecurityException, match="already signed"):
        sign_field(signed, "Signature1", cert, key)


def test_rejects_missing_and_non_signature_fields(creds):
    cert, key = creds
    document = Document()
    page = document.pages.add()
    document.form.add_text_field("Name", page, (60, 700, 260, 730))
    document.form.add_signature_field("Signature1", page, (60, 600, 260, 680))
    output = BytesIO()
    document.save(output)
    data = output.getvalue()

    with pytest.raises(PdfValidationException, match="not found"):
        sign_field(data, "Nope", cert, key)
    with pytest.raises(PdfValidationException, match="not a signature field"):
        sign_field(data, "Name", cert, key)


def test_certifying_signature_writes_docmdp_and_perms(creds):
    cert, key = creds
    signed = sign_field(
        _authored(), "Signature1", cert, key, certify_permissions=2
    )
    doc, field = _cos_field(signed, "Signature1")
    sig = _resolve(doc, field.mapping[PdfName("V")])
    refs = _resolve(doc, sig.mapping[PdfName("Reference")])
    methods = {
        _resolve(doc, _resolve(doc, r).mapping[PdfName("TransformMethod")]).name
        for r in refs.items
    }
    assert "/DocMDP" in methods

    root = _resolve(doc, doc.trailer.mapping[PdfName("Root")])
    perms = _resolve(doc, root.mapping[PdfName("Perms")])
    assert perms.mapping.get(PdfName("DocMDP")) is not None
    assert SimplePdf.from_bytes(signed).signatures[0].validate(
        _options(cert)
    ).status is ValidationStatus.VALID


def test_rejects_invalid_certify_permissions(creds):
    cert, key = creds
    with pytest.raises(PdfValidationException, match="certify_permissions"):
        sign_field(_authored(), "Signature1", cert, key, certify_permissions=9)


# --- seed value (/SV) ------------------------------------------------------
def test_required_seed_subfilter_is_enforced(creds):
    cert, key = creds
    data = _authored(
        seed_value={
            "sub_filter": ["ETSI.CAdES.detached"],
            "required": ["sub_filter"],
        }
    )
    # PAdES matches the seed value.
    assert sign_field(data, "Signature1", cert, key, pades=True)
    # Plain PKCS#7 does not.
    with pytest.raises(PdfSecurityException, match="SubFilter"):
        sign_field(data, "Signature1", cert, key)


def test_advisory_seed_subfilter_does_not_block(creds):
    """Without its /Ff bit a seed value entry is a preference, not a rule."""
    cert, key = creds
    data = _authored(seed_value={"sub_filter": ["ETSI.CAdES.detached"]})
    assert sign_field(data, "Signature1", cert, key)


def test_required_seed_reasons_are_enforced(creds):
    cert, key = creds
    data = _authored(
        seed_value={"reasons": ["Approved", "Reviewed"], "required": ["reasons"]}
    )
    assert sign_field(data, "Signature1", cert, key, reason="Reviewed")
    with pytest.raises(PdfSecurityException, match="Reason"):
        sign_field(data, "Signature1", cert, key, reason="Rubber-stamped")


def test_seed_value_authoring_is_validated():
    document = Document()
    page = document.pages.add()
    with pytest.raises(PdfValidationException, match="Unknown seed value"):
        document.form.add_signature_field(
            "S", page, (0, 0, 10, 10), seed_value={"nope": 1}
        )
    with pytest.raises(PdfValidationException, match="required but not supplied"):
        document.form.add_signature_field(
            "S", page, (0, 0, 10, 10), seed_value={"required": ["reasons"]}
        )


# --- field lock (/Lock) ----------------------------------------------------
def test_lock_becomes_a_fieldmdp_transform(creds):
    cert, key = creds
    document = Document()
    page = document.pages.add()
    document.form.add_text_field("Name", page, (60, 700, 260, 730))
    document.form.add_signature_field(
        "Signature1",
        page,
        (60, 600, 260, 680),
        lock={"action": "Include", "fields": ["Name"]},
    )
    output = BytesIO()
    document.save(output)

    signed = sign_field(output.getvalue(), "Signature1", cert, key)
    doc, field = _cos_field(signed, "Signature1")
    sig = _resolve(doc, field.mapping[PdfName("V")])
    refs = _resolve(doc, sig.mapping[PdfName("Reference")])
    field_mdp = [
        r
        for r in (_resolve(doc, item) for item in refs.items)
        if _resolve(doc, r.mapping[PdfName("TransformMethod")]) == PdfName("FieldMDP")
    ]
    assert len(field_mdp) == 1
    params = _resolve(doc, field_mdp[0].mapping[PdfName("TransformParams")])
    assert _resolve(doc, params.mapping[PdfName("Action")]) == PdfName("Include")
    locked = _resolve(doc, params.mapping[PdfName("Fields")])
    assert [item.value.decode("latin-1") for item in locked.items] == ["Name"]


def test_lock_authoring_is_validated():
    document = Document()
    page = document.pages.add()
    with pytest.raises(PdfValidationException, match="action"):
        document.form.add_signature_field(
            "S", page, (0, 0, 10, 10), lock={"action": "Freeze"}
        )
    with pytest.raises(PdfValidationException, match="non-empty 'fields'"):
        document.form.add_signature_field(
            "S", page, (0, 0, 10, 10), lock={"action": "Include"}
        )


def test_seed_value_and_lock_rejected_on_other_field_types():
    document = Document()
    document.pages.add()
    with pytest.raises(PdfValidationException, match="signature fields only"):
        document.form._create_field(
            "T",
            "text",
            [{"page_index": 0, "rect": (0, 0, 10, 10)}],
            seed_value={"filter": "Adobe.PPKLite"},
        )
