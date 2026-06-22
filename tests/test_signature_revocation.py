"""Revocation checking (CRL + OCSP) for PDF signatures.

CRLs and OCSP responses are built in memory with ``cryptography``.  The offline
tests embed a CRL into the CMS; the online test monkeypatches the HTTP helper in
:mod:`aspose_pdf.engine.revocation` so no network access occurs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509 import ocsp

from aspose_pdf.engine import cms as cms_mod
from aspose_pdf.engine import revocation as rev
from aspose_pdf.engine.signing import SigningUtils
from aspose_pdf.signature import PdfSignature
from aspose_pdf.validation import (
    RevocationStatus,
    ValidationMode,
    ValidationOptions,
    ValidationStatus,
)

DATA = b"%PDF revocation body\nwith newlines\n" * 8


def _pdf_sig(contents) -> PdfSignature:
    half = len(DATA) // 2
    return PdfSignature(
        name="S",
        contents=contents,
        byte_range=[0, half, half, len(DATA) - half],
        reference_data=DATA,
    )


def _root_and_leaf(name):
    root, rk = SigningUtils.create_self_signed_ca(name)
    leaf, lk = SigningUtils.issue_certificate(name + " Leaf", root, rk)
    return root, rk, leaf, lk


def _build_crl(root, rk, revoked_serial=None):
    now = datetime.now(timezone.utc)
    builder = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(root.subject)
        .last_update(now - timedelta(hours=1))
        .next_update(now + timedelta(days=1))
    )
    if revoked_serial is not None:
        builder = builder.add_revoked_certificate(
            x509.RevokedCertificateBuilder()
            .serial_number(revoked_serial)
            .revocation_date(now - timedelta(minutes=5))
            .build()
        )
    return builder.sign(rk, hashes.SHA256()).public_bytes(serialization.Encoding.DER)


def test_embedded_crl_good():
    root, rk, leaf, lk = _root_and_leaf("Rev Good")
    blob = SigningUtils.sign_data_pkcs7(DATA, leaf, lk, extra_certs=[root])
    blob = cms_mod.inject_crls(blob, [_build_crl(root, rk)])
    result = _pdf_sig(blob).validate(
        ValidationOptions(trusted_certificates=[root], check_revocation=True)
    )
    assert result.status == ValidationStatus.VALID
    assert result.revocation_status == RevocationStatus.GOOD


def test_embedded_crl_revoked():
    root, rk, leaf, lk = _root_and_leaf("Rev Bad")
    blob = SigningUtils.sign_data_pkcs7(DATA, leaf, lk, extra_certs=[root])
    blob = cms_mod.inject_crls(blob, [_build_crl(root, rk, leaf.serial_number)])
    result = _pdf_sig(blob).validate(
        ValidationOptions(trusted_certificates=[root], check_revocation=True)
    )
    assert result.status == ValidationStatus.INVALID
    assert result.revocation_status == RevocationStatus.REVOKED


def test_revocation_not_checked_by_default():
    root, rk, leaf, lk = _root_and_leaf("Rev Off")
    blob = SigningUtils.sign_data_pkcs7(DATA, leaf, lk, extra_certs=[root])
    blob = cms_mod.inject_crls(blob, [_build_crl(root, rk, leaf.serial_number)])
    # check_revocation defaults to False -> the revoked status is not consulted.
    result = _pdf_sig(blob).validate(ValidationOptions(trusted_certificates=[root]))
    assert result.status == ValidationStatus.VALID
    assert result.revocation_status == RevocationStatus.NOT_CHECKED


def test_revocation_unknown_without_info():
    root, _rk, leaf, lk = _root_and_leaf("Rev Unknown")
    blob = SigningUtils.sign_data_pkcs7(DATA, leaf, lk, extra_certs=[root])
    result = _pdf_sig(blob).validate(
        ValidationOptions(trusted_certificates=[root], check_revocation=True)
    )
    # No embedded material, offline -> UNKNOWN, which is not fatal.
    assert result.revocation_status == RevocationStatus.UNKNOWN
    assert result.status == ValidationStatus.VALID


def test_online_ocsp_revoked_via_monkeypatch(monkeypatch):
    root, rk, leaf, lk = _root_and_leaf("OCSP")
    now = datetime.now(timezone.utc)
    response = (
        ocsp.OCSPResponseBuilder()
        .add_response(
            cert=leaf,
            issuer=root,
            algorithm=hashes.SHA1(),
            cert_status=ocsp.OCSPCertStatus.REVOKED,
            this_update=now - timedelta(hours=1),
            next_update=now + timedelta(days=1),
            revocation_time=now - timedelta(minutes=5),
            revocation_reason=None,
        )
        .responder_id(ocsp.OCSPResponderEncoding.NAME, root)
        .sign(rk, hashes.SHA256())
    )
    der = response.public_bytes(serialization.Encoding.DER)
    # Force an OCSP URL and a fake responder over HTTP (no network).
    monkeypatch.setattr(rev, "_ocsp_urls", lambda cert: ["http://ocsp.example.test"])
    monkeypatch.setattr(rev, "_http_post", lambda url, data, ct, timeout: der)

    blob = SigningUtils.sign_data_pkcs7(DATA, leaf, lk, extra_certs=[root])
    result = _pdf_sig(blob).validate(
        ValidationOptions(
            trusted_certificates=[root],
            check_revocation=True,
            validation_mode=ValidationMode.ONLINE,
        )
    )
    assert result.revocation_status == RevocationStatus.REVOKED
    assert result.status == ValidationStatus.INVALID
