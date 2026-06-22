"""RFC 3161 timestamp creation and verification for PDF signatures."""

from __future__ import annotations

from cryptography.hazmat.primitives import hashes

from aspose_pdf.engine import timestamp as ts
from aspose_pdf.engine.signing import SigningUtils
from aspose_pdf.signature import PdfSignature
from aspose_pdf.validation import ValidationOptions, ValidationStatus

DATA = b"%PDF timestamp body\nwith newlines\n" * 8


def _pdf_sig(contents) -> PdfSignature:
    half = len(DATA) // 2
    return PdfSignature(
        name="S",
        contents=contents,
        byte_range=[0, half, half, len(DATA) - half],
        reference_data=DATA,
    )


def test_embedded_timestamp_is_verified():
    root, rk = SigningUtils.create_self_signed_ca("TS Chain Root")
    leaf, lk = SigningUtils.issue_certificate("TS Leaf", root, rk)
    tsa_cert, tsa_key = SigningUtils.create_self_signed_ca("Local TSA")

    blob = SigningUtils.sign_data_pkcs7(
        DATA, leaf, lk, extra_certs=[root], tsa=(tsa_cert, tsa_key)
    )
    result = _pdf_sig(blob).validate(
        ValidationOptions(trusted_certificates=[root], check_timestamp=True)
    )
    assert result.status == ValidationStatus.VALID
    assert result.timestamp is not None
    assert result.timestamp.verified
    assert result.timestamp.gen_time is not None
    assert "TSA" in (result.timestamp.tsa or "")


def test_timestamp_token_unit_verify_and_tamper():
    tsa_cert, tsa_key = SigningUtils.create_self_signed_ca("Unit TSA")
    value = b"the-signer-signature-value"
    digest = hashes.Hash(hashes.SHA256())
    digest.update(value)
    imprint = digest.finalize()

    token = ts.make_timestamp_token(imprint, "sha256", tsa_cert, tsa_key)
    good = ts.verify_timestamp_token(token, value)
    assert good.verified and good.imprint_ok and good.signature_ok

    bad = ts.verify_timestamp_token(token, value + b"!")
    assert not bad.verified
    assert not bad.imprint_ok


def test_signature_without_timestamp_reports_none():
    cert, key = SigningUtils.create_self_signed_cert()
    blob = SigningUtils.sign_data_pkcs7(DATA, cert, key)
    result = _pdf_sig(blob).validate()
    assert result.status == ValidationStatus.VALID
    assert result.timestamp is None
