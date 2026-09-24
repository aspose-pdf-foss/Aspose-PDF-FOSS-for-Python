"""PAdES baseline profiles (B / T / LT / LTA), the ESS signing-certificate
binding, and the document security store (``/DSS``) for long-term validation.

All material — certificates, the local TSA, CRLs — is built in memory with
``cryptography``; nothing touches the network.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization

from aspose_pdf.engine import cms as cms_mod
from aspose_pdf.engine import dss
from aspose_pdf.engine.signing import SigningUtils
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.security import SignaturesCompromiseDetector
from aspose_pdf.signature import PdfSignature
from aspose_pdf.validation import (
    PadesLevel,
    RevocationStatus,
    ValidationOptions,
    ValidationStatus,
)
from tests.helpers_signatures import timestamp_authority

DATA = b"%PDF pades body\nwith newlines\n" * 9


def _der(cert) -> bytes:
    return cert.public_bytes(serialization.Encoding.DER)


def _chain():
    root, rk = SigningUtils.create_self_signed_ca("PAdES Root")
    leaf, lk = SigningUtils.issue_certificate("PAdES Signer", root, rk)
    return root, rk, leaf, lk


def _pdf_sig(blob, sub_filter="ETSI.CAdES.detached", data=DATA) -> PdfSignature:
    half = len(data) // 2
    return PdfSignature(
        name="S",
        contents=blob,
        byte_range=[0, half, half, len(data) - half],
        reference_data=data,
        sub_filter=sub_filter,
    )


def _good_crl(issuer, issuer_key, revoked_serial=None) -> bytes:
    now = datetime.now(UTC)
    builder = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(issuer.subject)
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
    return builder.sign(issuer_key, hashes.SHA256()).public_bytes(
        serialization.Encoding.DER
    )


# ---------------------------------------------------------------------------
# CAdES-BES / PAdES-B core
# ---------------------------------------------------------------------------
def test_cades_signature_carries_ess_and_verifies():
    root, _rk, leaf, lk = _chain()
    blob = SigningUtils.sign_data_cades(DATA, leaf, lk, extra_certs=[root])
    info = cms_mod.parse_signed_data(blob)

    assert info.ess_cert_ids, "CAdES signature must carry signing-certificate-v2"
    assert cms_mod.verify_signing_certificate(info) is True
    assert cms_mod.verify_signer(info, DATA).signature_ok


def test_pades_b_validates_with_level_b():
    root, _rk, leaf, lk = _chain()
    blob = SigningUtils.sign_data_cades(DATA, leaf, lk, extra_certs=[root])
    result = _pdf_sig(blob).validate(ValidationOptions(trusted_certificates=[root]))

    assert result.status == ValidationStatus.VALID
    assert result.pades_level == PadesLevel.B
    assert result.pades_level.baseline_name == "B-B"


def test_plain_pkcs7_is_not_pades():
    root, _rk, leaf, lk = _chain()
    blob = SigningUtils.sign_data_pkcs7(DATA, leaf, lk, extra_certs=[root])
    info = cms_mod.parse_signed_data(blob)
    assert info.ess_cert_ids == []
    assert cms_mod.verify_signing_certificate(info) is None

    result = _pdf_sig(blob, sub_filter="adbe.pkcs7.detached").validate(
        ValidationOptions(trusted_certificates=[root])
    )
    assert result.status == ValidationStatus.VALID
    assert result.pades_level == PadesLevel.NONE


def test_ess_binding_mismatch_is_rejected():
    """A signature whose signing-certificate attr points at a different cert
    (even with an otherwise-valid signature) must fail validation."""
    root, _rk, leaf, lk = _chain()
    other, _ok = SigningUtils.issue_certificate("Someone Else", root, _rk)

    # Build a CAdES SignedData but bind the ESS attribute to ``other`` instead
    # of the real signer ``leaf`` — the signer still signs these attributes, so
    # the signature itself is valid; only the binding is wrong.
    from asn1crypto import cms as acms
    from asn1crypto import x509 as ax509

    digest = hashes.Hash(hashes.SHA256())
    digest.update(DATA)
    signed_attrs = acms.CMSAttributes(
        [
            acms.CMSAttribute({"type": "content_type", "values": ["data"]}),
            acms.CMSAttribute({"type": "message_digest", "values": [digest.finalize()]}),
            cms_mod.ess_signing_cert_v2_attr(other),  # wrong cert
        ]
    )
    to_sign = b"\x31" + signed_attrs.dump(force=True)[1:]
    from cryptography.hazmat.primitives.asymmetric import padding

    signature = lk.sign(to_sign, padding.PKCS1v15(), hashes.SHA256())
    asn1_leaf = ax509.Certificate.load(_der(leaf))
    signer_info = acms.SignerInfo(
        {
            "version": "v1",
            "sid": {
                "issuer_and_serial_number": {
                    "issuer": asn1_leaf.issuer,
                    "serial_number": leaf.serial_number,
                }
            },
            "digest_algorithm": {"algorithm": "sha256"},
            "signed_attrs": signed_attrs,
            "signature_algorithm": {"algorithm": "rsassa_pkcs1v15"},
            "signature": signature,
        }
    )
    signed_data = acms.SignedData(
        {
            "version": "v1",
            "digest_algorithms": [{"algorithm": "sha256"}],
            "encap_content_info": {"content_type": "data"},
            "certificates": [asn1_leaf, ax509.Certificate.load(_der(root))],
            "signer_infos": [signer_info],
        }
    )
    blob = acms.ContentInfo(
        {"content_type": "signed_data", "content": signed_data}
    ).dump()

    info = cms_mod.parse_signed_data(blob)
    assert cms_mod.verify_signing_certificate(info) is False

    result = _pdf_sig(blob).validate(ValidationOptions(trusted_certificates=[root]))
    assert result.status == ValidationStatus.INVALID
    assert any("signing-certificate" in e for e in result.errors)


# ---------------------------------------------------------------------------
# PAdES-T
# ---------------------------------------------------------------------------
def test_pades_t_with_embedded_timestamp():
    root, _rk, leaf, lk = _chain()
    tsa_cert, tsa_key = timestamp_authority("PAdES TSA", root, _rk)
    blob = SigningUtils.sign_data_cades(
        DATA, leaf, lk, extra_certs=[root], tsa=(tsa_cert, tsa_key)
    )
    result = _pdf_sig(blob).validate(
        ValidationOptions(trusted_certificates=[root], check_timestamp=True)
    )
    assert result.status == ValidationStatus.VALID
    assert result.pades_level == PadesLevel.T
    assert result.timestamp is not None and result.timestamp.verified


# ---------------------------------------------------------------------------
# Writer end-to-end (real signature dictionaries)
# ---------------------------------------------------------------------------
def _sign_pades_pdf(*, timestamp=None):
    root, _rk, leaf, lk = _chain()
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 200, 200)]
    pdf.page_contents = [b"PAdES content"]
    pdf.signing_creds = (leaf, lk)
    pdf.extra_certs = [root]
    pdf.pades = True
    if timestamp is not None:
        pdf.timestamp_tsa = timestamp
    return pdf.to_bytes(), root, leaf


def test_writer_emits_cades_subfilter_and_validates_as_pades_b():
    blob, root, _leaf = _sign_pades_pdf()
    assert b"/SubFilter /ETSI.CAdES.detached" in blob

    sigs = SimplePdf.from_bytes(blob).signatures
    assert len(sigs) == 1
    assert sigs[0].sub_filter == "ETSI.CAdES.detached"
    assert sigs[0].valid

    result = sigs[0].validate(ValidationOptions(trusted_certificates=[root]))
    assert result.status == ValidationStatus.VALID
    assert result.pades_level == PadesLevel.B


# ---------------------------------------------------------------------------
# DSS / LTV
# ---------------------------------------------------------------------------
def test_dss_build_and_harvest_round_trip():
    root, _rk, leaf, _lk = _chain()
    material = dss.DssMaterial(certs=[_der(leaf), _der(root)])

    blob, _root, _leaf = _sign_pades_pdf()
    lt = dss.build_dss(blob, material, vri_contents=b"sig-contents")
    assert lt.startswith(blob), "original bytes must be preserved verbatim"
    assert b"/DSS" in lt and b"/VRI" in lt

    harvested = dss.read_dss(lt)
    assert sorted(harvested.certs) == sorted(material.certs)


def test_pades_lt_end_to_end():
    root, rk = SigningUtils.create_self_signed_ca("LT Root")
    leaf, lk = SigningUtils.issue_certificate("LT Signer", root, rk)
    tsa_cert, tsa_key = timestamp_authority("LT TSA", root, rk)

    pdf = SimplePdf()
    pdf.pages = [(0, 0, 200, 200)]
    pdf.page_contents = [b"LT"]
    pdf.signing_creds = (leaf, lk)
    pdf.extra_certs = [root]
    pdf.pades = True
    pdf.timestamp_tsa = (tsa_cert, tsa_key)  # -> PAdES-T
    signed = pdf.to_bytes()

    crl = _good_crl(root, rk)  # GOOD: leaf is not revoked
    lt = dss.enable_ltv(signed, extra=dss.DssMaterial(crls=[crl], certs=[_der(tsa_cert)]))

    sig = SimplePdf.from_bytes(lt).signatures[0]
    result = sig.validate(
        ValidationOptions(
            trusted_certificates=[root], check_revocation=True, check_timestamp=True
        )
    )
    assert result.status == ValidationStatus.VALID
    assert result.pades_level == PadesLevel.LT
    assert result.revocation_status == RevocationStatus.GOOD


# ---------------------------------------------------------------------------
# PAdES-LTA (document timestamp)
# ---------------------------------------------------------------------------
def _build_lta():
    root, rk = SigningUtils.create_self_signed_ca("LTA Root")
    leaf, lk = SigningUtils.issue_certificate("LTA Signer", root, rk)
    tsa_cert, tsa_key = timestamp_authority("LTA TSA", root, rk)

    pdf = SimplePdf()
    pdf.pages = [(0, 0, 200, 200)]
    pdf.page_contents = [b"LTA"]
    pdf.signing_creds = (leaf, lk)
    pdf.extra_certs = [root]
    pdf.pades = True
    pdf.timestamp_tsa = (tsa_cert, tsa_key)
    signed = pdf.to_bytes()

    crl = _good_crl(root, rk)
    lt = dss.enable_ltv(signed, extra=dss.DssMaterial(crls=[crl], certs=[_der(tsa_cert)]))
    lta = dss.add_document_timestamp(lt, tsa=(tsa_cert, tsa_key))
    return lt, lta, root


def test_pades_lta_document_timestamp_and_level():
    lt, lta, root = _build_lta()
    assert lta.startswith(lt), "the archive timestamp is a pure incremental update"
    assert b"/SubFilter /ETSI.RFC3161" in lta

    sigs = SimplePdf.from_bytes(lta).signatures
    by_subfilter = {s.sub_filter: s for s in sigs}
    assert set(by_subfilter) == {"ETSI.CAdES.detached", "ETSI.RFC3161"}

    opts = ValidationOptions(
        trusted_certificates=[root], check_revocation=True, check_timestamp=True
    )
    approval = by_subfilter["ETSI.CAdES.detached"].validate(opts)
    assert approval.status == ValidationStatus.VALID
    assert approval.pades_level == PadesLevel.LTA

    doc_ts = by_subfilter["ETSI.RFC3161"]
    assert doc_ts.valid
    ts_result = doc_ts.validate(opts)
    assert ts_result.status == ValidationStatus.VALID
    assert ts_result.timestamp is not None and ts_result.timestamp.verified


def test_document_timestamp_detects_tampering_under_its_coverage():
    _lt, lta, _root = _build_lta()
    # Flip a byte inside the first page content (covered by the doc timestamp).
    idx = lta.index(b"LTA")
    tampered = lta[:idx] + b"XXX" + lta[idx + 3 :]

    doc_ts = next(
        s for s in SimplePdf.from_bytes(tampered).signatures
        if s.sub_filter == "ETSI.RFC3161"
    )
    assert doc_ts.valid is False


# ---------------------------------------------------------------------------
# Compromise detector vs. legitimate long-term updates
# ---------------------------------------------------------------------------
def test_detector_accepts_lt_and_lta_increments():
    lt, lta, _root = _build_lta()

    for label, blob in (("LT", lt), ("LTA", lta)):
        result = SignaturesCompromiseDetector(SimplePdf.from_bytes(blob)).check()
        assert result.has_compromised_signatures is False, label
        assert not result.reasons, label


def test_detector_flags_unsigned_payload_after_archive_timestamp():
    _lt, lta, _root = _build_lta()
    # Append an unsigned widget *after* the archive timestamp — nothing covers it.
    tail = b"\n99 0 obj\n<< /Subtype /Widget /Rect [0 0 9 9] >>\nendobj\n"
    pdf = SimplePdf.from_bytes(lta + tail)
    result = SignaturesCompromiseDetector(pdf).check()
    assert result.has_compromised_signatures is True
    assert "annotations/widgets (layering risk)" in " ".join(result.reasons)


def test_building_a_dss_into_an_encrypted_document_needs_its_handler():
    """The validation material is enciphered with the file's own key.

    A store written in plaintext into an encrypted file would be certificates
    every reader decrypts into noise, so the builder needs the document's
    handler; without one it refuses rather than writing that. pyHanko reads
    the store this writes and validates the signatures under it.
    """
    from aspose_pdf import Document
    from aspose_pdf.exceptions import PdfSecurityException

    document = Document()
    document.pages.add()
    buffer = io.BytesIO()
    document.save(buffer)

    sealed = Document(io.BytesIO(buffer.getvalue()))
    sealed.encrypt("u", "owner")
    out = io.BytesIO()
    sealed.save(out)
    material = dss.DssMaterial(certs=[b"\x30\x03\x02\x01\x00"])

    with pytest.raises(PdfSecurityException, match="encrypted"):
        dss.build_dss(out.getvalue(), material)
    with pytest.raises(PdfSecurityException, match="encrypted"):
        dss.add_document_timestamp(
            out.getvalue(), tsa=SigningUtils.create_self_signed_ca("T")
        )

    opened = Document(io.BytesIO(out.getvalue()), password="u")
    handler = opened._engine_pdf._writer_encryption()
    with_store = dss.build_dss(out.getvalue(), material, encryption=handler)
    assert dss.read_dss(with_store, encryption=handler).certs == material.certs
    # Without the key the same bytes are the ciphertext they are stored as.
    assert dss.read_dss(with_store).certs != material.certs


@pytest.mark.parametrize("subfilter", ["ETSI.CAdES.detached", "adbe.pkcs7.detached"])
def test_pades_requires_both_ess_binding_and_cades_subfilter(subfilter):
    root, _rk, leaf, key = _chain()
    # Each case supplies only one of the two baseline requirements.
    signer = SigningUtils.sign_data_pkcs7 if subfilter.startswith("ETSI") else SigningUtils.sign_data_cades
    blob = signer(DATA, leaf, key, extra_certs=[root])
    result = _pdf_sig(blob, sub_filter=subfilter).validate(ValidationOptions(trusted_certificates=[root]))
    assert result.status == ValidationStatus.VALID
    assert result.pades_level == PadesLevel.NONE


def test_timestamp_marker_in_unsigned_comment_cannot_promote_pades():
    signed, root, _leaf = _sign_pades_pdf()
    blob = signed + b"\n% /SubFilter /ETSI.RFC3161\n"
    sig = SimplePdf.from_bytes(blob).signatures[0]
    result = sig.validate(ValidationOptions(trusted_certificates=[root]))
    assert result.status == ValidationStatus.VALID
    assert result.pades_level == PadesLevel.B


@pytest.mark.parametrize("kind", ["certs", "garbage", "unrelated_crl"])
def test_incomplete_or_unrelated_dss_cannot_promote_to_lt(kind):
    lt, _lta, root = _build_lta()
    original = SimplePdf.from_bytes(lt).signatures[0]
    signed = lt[:sum(original.byte_range[2:])]
    material = dss.read_dss(lt)
    if kind == "certs":
        material.crls = []
    elif kind == "garbage":
        material.crls = [b"not a CRL"]
    else:
        unrelated, key = SigningUtils.create_self_signed_ca("Unrelated")
        material.crls = [_good_crl(unrelated, key)]
    blob = dss.build_dss(signed, material)
    result = SimplePdf.from_bytes(blob).signatures[0].validate(ValidationOptions(trusted_certificates=[root]))
    assert result.status == ValidationStatus.VALID
    assert result.pades_level == PadesLevel.T


def test_untrusted_archive_timestamp_cannot_promote_lt():
    lt, _lta, root = _build_lta()
    other_tsa = timestamp_authority("Untrusted archive TSA")
    blob = dss.add_document_timestamp(lt, tsa=other_tsa)
    result = SimplePdf.from_bytes(blob).signatures[0].validate(ValidationOptions(trusted_certificates=[root]))
    assert result.status == ValidationStatus.VALID
    assert result.pades_level == PadesLevel.LT


def test_tampered_archive_timestamp_cannot_promote_lt():
    _lt, lta, root = _build_lta()
    marker = lta.rfind(b"/Contents <") + len(b"/Contents <")
    damaged = lta[:marker] + b"00" + lta[marker + 2:]
    result = SimplePdf.from_bytes(damaged).signatures[0].validate(ValidationOptions(trusted_certificates=[root]))
    assert result.status == ValidationStatus.VALID
    assert result.pades_level == PadesLevel.LT


def test_archive_must_cover_validation_material():
    lt, _lta, root = _build_lta()
    original = SimplePdf.from_bytes(lt).signatures[0]
    signed = lt[:sum(original.byte_range[2:])]
    tsa_root, root_key = SigningUtils.create_self_signed_ca("Archive root")
    tsa = timestamp_authority("Archive TSA", tsa_root, root_key)
    stamped = dss.add_document_timestamp(signed, tsa=tsa)
    material = dss.read_dss(lt)
    material.certs.extend([_der(tsa[0]), _der(tsa_root)])
    material.crls.append(_good_crl(tsa_root, root_key))
    # The timestamp came first; it cannot protect this later DSS.
    updated = dss.build_dss(stamped, material)
    options = ValidationOptions(trusted_certificates=[root, tsa_root])
    result = SimplePdf.from_bytes(updated).signatures[0].validate(options)
    assert result.status == ValidationStatus.VALID
    assert result.pades_level == PadesLevel.LT
    renewed = dss.add_document_timestamp(updated, tsa=tsa)
    result = SimplePdf.from_bytes(renewed).signatures[0].validate(options)
    assert result.status == ValidationStatus.VALID
    assert result.pades_level == PadesLevel.LTA


def test_document_timestamp_supplies_t_without_signature_timestamp():
    signed, root, _leaf = _sign_pades_pdf()
    tsa = timestamp_authority("Trusted document TSA")
    stamped = dss.add_document_timestamp(signed, tsa=tsa)
    result = SimplePdf.from_bytes(stamped).signatures[0].validate(
        ValidationOptions(trusted_certificates=[root, tsa[0]])
    )
    assert result.status == ValidationStatus.VALID
    assert result.pades_level == PadesLevel.T
    assert result.trusted_at is not None


def test_disabling_timestamp_checks_prevents_profile_promotion():
    _lt, lta, root = _build_lta()
    result = SimplePdf.from_bytes(lta).signatures[0].validate(
        ValidationOptions(trusted_certificates=[root], check_timestamp=False)
    )
    assert result.status == ValidationStatus.VALID
    assert result.pades_level == PadesLevel.B


def test_document_timestamp_with_partial_coverage_is_not_evidence():
    import hashlib

    from aspose_pdf.engine import timestamp

    signed, root, _leaf = _sign_pades_pdf()
    tsa = timestamp_authority("Partial TSA")
    stamped = bytearray(dss.add_document_timestamp(signed, tsa=tsa))
    start = stamped.rfind(b"/ByteRange [") + len(b"/ByteRange [")
    # A genuine trusted token, but it signs only the earlier PDF. Its own
    # dictionary and the rest of the timestamp revision are wholly unsigned.
    stamped[start:start + 43] = f"{0:010d} {0:010d} {0:010d} {len(signed):010d}".encode()
    token = timestamp.make_timestamp_token(hashlib.sha256(signed).digest(), "sha256", *tsa)
    start = stamped.rfind(b"/Contents <") + len(b"/Contents <")
    end = stamped.index(b">", start)
    stamped[start:end] = token.hex().encode().ljust(end - start, b"0")
    signatures = SimplePdf.from_bytes(bytes(stamped)).signatures
    options = ValidationOptions(trusted_certificates=[root, tsa[0]])
    assert signatures[0].validate(options).pades_level == PadesLevel.B
    assert signatures[1].valid
    result = signatures[1].validate(options)
    assert result.status == ValidationStatus.INVALID
    assert "ByteRange" in result.message


def test_archive_time_cannot_precede_the_signature_timestamp(monkeypatch):
    from aspose_pdf.engine import timestamp

    lt, _lta, root = _build_lta()
    tsa_root, key = SigningUtils.create_self_signed_ca("Archive root")
    tsa = timestamp_authority("Archive TSA", tsa_root, key)
    material = dss.read_dss(lt)
    material.certs.extend([_der(tsa[0]), _der(tsa_root)])
    material.crls.append(_good_crl(tsa_root, key))
    lt = dss.build_dss(lt, material)
    make_token = timestamp.make_timestamp_token

    def backdated(*args, **kwargs):
        return make_token(*args, **kwargs, gen_time=datetime.now(UTC) - timedelta(minutes=1))

    monkeypatch.setattr(timestamp, "make_timestamp_token", backdated)
    stamped = dss.add_document_timestamp(lt, tsa=tsa)
    result = SimplePdf.from_bytes(stamped).signatures[0].validate(
        ValidationOptions(trusted_certificates=[root, tsa_root])
    )
    assert result.status == ValidationStatus.VALID
    assert result.pades_level == PadesLevel.LT


@pytest.mark.parametrize("algorithm", ["AES-256", "AES-128", "RC4"])
def test_encrypted_lta_requires_and_accepts_verified_embedded_evidence(algorithm):
    from aspose_pdf import Document

    root, root_key, signer, key = _chain()
    tsa = timestamp_authority("Encrypted TSA", root, root_key)
    document = Document()
    page = document.pages.add()
    document.form.add_signature_field("Approval", page, (0, 0, 0, 0))
    document.encrypt("user", "owner", algorithm=algorithm)
    document.sign("Approval", certificate=signer, private_key=key,
                  extra_certificates=[root], pades=True, timestamp_authority=tsa)
    document.add_ltv(certificates=[tsa[0]], crls=[_good_crl(root, root_key)])
    document.add_document_timestamp(timestamp_authority=tsa)
    output = io.BytesIO()
    document.save(output)
    reopened = Document(io.BytesIO(output.getvalue()), password="user")
    result = reopened.signatures[0].validate(ValidationOptions(trusted_certificates=[root]))
    assert result.status == ValidationStatus.VALID, result.errors
    assert result.pades_level == PadesLevel.LTA
