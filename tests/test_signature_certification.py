"""DocMDP certification-grade signatures: creation, round-trip and validation.

Signs a document with a certifying (DocMDP) signature through ``SimplePdf``,
re-parses it, and checks that the certification level round-trips and that the
level-aware modification policy is enforced.
"""

from __future__ import annotations

from aspose_pdf.engine.signing import SigningUtils
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.validation import (
    CertificationLevel,
    ValidationOptions,
    ValidationStatus,
)

_INCREMENTAL_CHANGE = b"\n5 0 obj\n<< /Modified true >>\nendobj\n"


def _make_certified_pdf(perms: int):
    root, rk = SigningUtils.create_self_signed_ca("Cert Root CA")
    leaf, lk = SigningUtils.issue_certificate("Cert Leaf", root, rk)
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 200, 200)]
    pdf.page_contents = [b"Certified content"]
    pdf.signing_creds = (leaf, lk)
    pdf.extra_certs = [root]
    pdf.certify_permissions = perms
    pdf.signature = {"Reason": "Certify", "Name": "CertSig1"}
    return root, pdf.to_bytes()


def test_certified_pdf_roundtrips_and_reports_level():
    root, blob = _make_certified_pdf(2)
    assert b"/Perms" in blob and b"/DocMDP" in blob and b"/AcroForm" in blob

    reparsed = SimplePdf.from_bytes(blob)
    assert len(reparsed.signatures) == 1
    sig = reparsed.signatures[0]
    assert sig.name == "CertSig1"
    assert sig.sub_filter == "adbe.pkcs7.detached"
    assert sig.docmdp_level == 2

    result = sig.validate(ValidationOptions(trusted_certificates=[root]))
    assert result.status == ValidationStatus.VALID
    assert result.certification_level == CertificationLevel.FORM_FILLING


def test_certification_level_1_change_is_violation():
    root, blob = _make_certified_pdf(1)
    sig = SimplePdf.from_bytes(blob).signatures[0]
    assert sig.docmdp_level == 1

    # A meaningful incremental change after a "no changes" certification.
    sig.reference_data = sig.reference_data + _INCREMENTAL_CHANGE
    result = sig.validate(ValidationOptions(trusted_certificates=[root]))
    assert result.status == ValidationStatus.INVALID
    assert any("certification" in e.lower() for e in result.errors)
    assert result.certification_level == CertificationLevel.NO_CHANGES


def test_certification_level_2_allows_incremental_change():
    root, blob = _make_certified_pdf(2)
    sig = SimplePdf.from_bytes(blob).signatures[0]

    sig.reference_data = sig.reference_data + _INCREMENTAL_CHANGE
    result = sig.validate(ValidationOptions(trusted_certificates=[root]))
    # Form-filling certification permits later incremental changes.
    assert result.status == ValidationStatus.VALID
    assert result.certification_level == CertificationLevel.FORM_FILLING
