"""Certificate-chain validation for PDF signatures.

Builds real Root -> Intermediate -> Leaf chains in memory and exercises
``PdfSignature.validate`` trust evaluation: trusted anchors, untrusted roots,
incomplete chains, expired certificates and self-signed handling.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aspose_pdf.engine.signing import SigningUtils
from aspose_pdf.signature import PdfSignature
from aspose_pdf.validation import TrustStatus, ValidationOptions, ValidationStatus

DATA = b"%PDF-1.7 chain validation body\nwith\nseveral\nlines\n" * 4


def _signed(data, cert, key, extra_certs=None) -> PdfSignature:
    """Build a PdfSignature whose ByteRange reconstructs the full signed data."""
    blob = SigningUtils.sign_data_pkcs7(data, cert, key, extra_certs=extra_certs)
    half = len(data) // 2
    return PdfSignature(
        name="S",
        contents=blob,
        byte_range=[0, half, half, len(data) - half],
        reference_data=data,
    )


def _chain():
    root, rk = SigningUtils.create_self_signed_ca("Chain Root CA")
    inter, ik = SigningUtils.issue_certificate("Chain Intermediate", root, rk, ca=True)
    leaf, lk = SigningUtils.issue_certificate("Chain Leaf", inter, ik)
    return root, rk, inter, ik, leaf, lk


def test_trusted_chain_is_valid_and_trusted():
    root, _rk, inter, _ik, leaf, lk = _chain()
    sig = _signed(DATA, leaf, lk, extra_certs=[inter])
    result = sig.validate(ValidationOptions(trusted_certificates=[root]))
    assert result.status == ValidationStatus.VALID
    assert result.trust_status == TrustStatus.TRUSTED
    assert result.signer and "Chain Leaf" in result.signer


def test_untrusted_root_rejected_when_anchors_supplied():
    root, _rk, inter, _ik, leaf, lk = _chain()
    other_root, _ = SigningUtils.create_self_signed_ca("Some Other Root")
    # Embed the full chain (incl. the real root) but trust a different anchor.
    sig = _signed(DATA, leaf, lk, extra_certs=[inter, root])
    result = sig.validate(ValidationOptions(trusted_certificates=[other_root]))
    assert result.status == ValidationStatus.INVALID
    assert result.trust_status == TrustStatus.UNTRUSTED


def test_missing_intermediate_breaks_chain():
    root, _rk, _inter, _ik, leaf, lk = _chain()
    sig = _signed(DATA, leaf, lk, extra_certs=[])  # intermediate absent
    result = sig.validate(ValidationOptions(trusted_certificates=[root]))
    assert result.status == ValidationStatus.INVALID
    assert result.trust_status == TrustStatus.BROKEN


def test_expired_leaf_rejected():
    root, rk = SigningUtils.create_self_signed_ca("Exp Root")
    now = datetime.now(timezone.utc)
    leaf, lk = SigningUtils.issue_certificate(
        "Exp Leaf",
        root,
        rk,
        not_before=now - timedelta(days=10),
        not_after=now - timedelta(days=1),
    )
    sig = _signed(DATA, leaf, lk)
    result = sig.validate(ValidationOptions(trusted_certificates=[root]))
    assert result.status == ValidationStatus.INVALID
    assert any("expired" in e for e in result.errors)


def test_self_signed_valid_by_default():
    cert, key = SigningUtils.create_self_signed_cert()
    result = _signed(DATA, cert, key).validate()
    assert result.status == ValidationStatus.VALID
    assert result.trust_status == TrustStatus.SELF_SIGNED


def test_self_signed_rejected_when_disallowed():
    cert, key = SigningUtils.create_self_signed_cert()
    result = _signed(DATA, cert, key).validate(
        ValidationOptions(allow_self_signed=False)
    )
    assert result.status == ValidationStatus.INVALID


def test_tampered_signed_data_is_invalid():
    cert, key = SigningUtils.create_self_signed_cert()
    sig = _signed(DATA, cert, key)
    tampered = PdfSignature(
        name="S",
        contents=sig.contents,
        byte_range=list(sig.byte_range),
        reference_data=b"X" + DATA[1:],
    )
    assert tampered.validate().status == ValidationStatus.INVALID
