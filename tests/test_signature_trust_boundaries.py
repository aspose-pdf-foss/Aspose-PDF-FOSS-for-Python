"""Negative trust checks using independently constructed X.509 material."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509 import ocsp
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID, ObjectIdentifier

from aspose_pdf.engine import cert_chain, cms, revocation, timestamp
from aspose_pdf.signature import PdfSignature
from aspose_pdf.validation import (
    PadesLevel,
    RevocationStatus,
    TrustStatus,
    ValidationOptions,
    ValidationStatus,
)

DATA = b"Signed document data"


def certificate(
    name,
    key,
    issuer=None,
    issuer_key=None,
    *,
    ca=False,
    path_length=None,
    basic=True,
    usage=None,
    eku=None,
    eku_critical=False,
    extensions=(),
    start=None,
    end=None,
    serial=None,
):
    now = datetime.now(UTC)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer.subject if issuer else subject)
        .public_key(key.public_key())
        .serial_number(serial or x509.random_serial_number())
        .not_valid_before(start or now - timedelta(days=30))
        .not_valid_after(end or now + timedelta(days=30))
    )
    if basic:
        builder = builder.add_extension(x509.BasicConstraints(ca, path_length), True)
    if usage is not None:
        builder = builder.add_extension(usage, True)
    if eku is not None:
        builder = builder.add_extension(x509.ExtendedKeyUsage(eku), eku_critical)
    for value, critical in extensions:
        builder = builder.add_extension(value, critical)
    return builder.sign(issuer_key or key, hashes.SHA256())


def key_usage(*, sign=True, cert_sign=False, crl_sign=False):
    return x509.KeyUsage(
        sign, False, False, False, False, cert_sign, crl_sign, False, False
    )


@pytest.fixture(scope="module")
def keys():
    return [
        rsa.generate_private_key(public_exponent=65537, key_size=2048) for _ in range(4)
    ]


@pytest.fixture
def chain(keys):
    root = certificate("Root", keys[0], ca=True)
    leaf = certificate("Signer", keys[1], root, keys[0])
    return root, leaf


def pdf_signature(blob):
    return PdfSignature(
        "S", blob, [0, 5, 5, len(DATA) - 5], DATA, sub_filter="ETSI.CAdES.detached"
    )


def token_for(blob, tsa, key, *, when=None):
    digest = hashes.Hash(hashes.SHA256())
    digest.update(cms.parse_signed_data(blob).signature)
    return timestamp.make_timestamp_token(
        digest.finalize(), "sha256", tsa, key, gen_time=when
    )


def crl(root, key, *, start=None, end=None, extensions=(), issuer_name=None):
    now = datetime.now(UTC)
    builder = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(issuer_name or root.subject)
        .last_update(start or now - timedelta(hours=1))
        .next_update(end or now + timedelta(hours=1))
    )
    for value, critical in extensions:
        builder = builder.add_extension(value, critical)
    return builder.sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.DER)


def ocsp_response(
    leaf,
    issuer,
    responder,
    key,
    *,
    start=None,
    end=None,
    embedded=(),
    status=ocsp.OCSPCertStatus.GOOD,
    responder_id=None,
):
    now = datetime.now(UTC)
    builder = (
        ocsp.OCSPResponseBuilder()
        .add_response(
            cert=leaf,
            issuer=issuer,
            algorithm=hashes.SHA256(),
            cert_status=status,
            this_update=start or now - timedelta(minutes=5),
            next_update=end,
            revocation_time=None,
            revocation_reason=None,
        )
        .responder_id(ocsp.OCSPResponderEncoding.HASH, responder)
    )
    if embedded:
        builder = builder.certificates(embedded)
    der = builder.sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.DER)
    if responder_id is not None:
        from asn1crypto import ocsp as asn1_ocsp

        response = asn1_ocsp.OCSPResponse.load(der)
        basic = response["response_bytes"]["response"].parsed
        basic["tbs_response_data"]["responder_id"] = asn1_ocsp.ResponderId(
            {
                "by_key": x509.SubjectKeyIdentifier.from_public_key(
                    responder_id.public_key()
                ).digest,
            }
        )
        basic["signature"] = key.sign(
            basic["tbs_response_data"].dump(), padding.PKCS1v15(), hashes.SHA256()
        )
        response["response_bytes"]["response"] = basic
        der = response.dump()
    return der


@pytest.mark.parametrize("kind", ["expired", "future"])
def test_crl_outside_its_validity_window_is_unknown(chain, keys, kind):
    root, leaf = chain
    now = datetime.now(UTC)
    start, end = (
        (now - timedelta(days=2), now - timedelta(days=1))
        if kind == "expired"
        else (now + timedelta(hours=1), now + timedelta(hours=2))
    )
    result = revocation.check_crl(crl(root, keys[0], start=start, end=end), leaf, root)
    assert result.status == RevocationStatus.UNKNOWN


@pytest.mark.parametrize("kind", ["expired", "future", "old_without_next"])
def test_ocsp_outside_its_validity_window_is_unknown(chain, keys, kind):
    root, leaf = chain
    now = datetime.now(UTC)
    start = now + timedelta(hours=1) if kind == "future" else now - timedelta(days=3)
    end = None if kind == "old_without_next" else start + timedelta(hours=1)
    result = revocation.check_ocsp_response(
        ocsp_response(leaf, root, root, keys[0], start=start, end=end), leaf, root
    )
    assert result.status == RevocationStatus.UNKNOWN


def test_ocsp_binds_issuer_hashes_not_only_serial_number(chain, keys):
    root, leaf = chain
    other = certificate("Other issuer", keys[2], ca=True)
    same_serial = certificate(
        "Other signer", keys[1], other, keys[2], serial=leaf.serial_number
    )
    der = ocsp_response(same_serial, other, root, keys[0])
    result = revocation.check_ocsp_response(der, leaf, root)
    assert result is None or result.status == RevocationStatus.UNKNOWN


@pytest.mark.parametrize(
    "kind", ["no_eku", "wrong_eku", "expired", "key_usage", "wrong_id"]
)
def test_unauthorized_ocsp_responder_is_unknown(chain, keys, kind):
    root, leaf = chain
    eku = None if kind == "no_eku" else [ExtendedKeyUsageOID.OCSP_SIGNING]
    if kind == "wrong_eku":
        eku = [ExtendedKeyUsageOID.SERVER_AUTH]
    responder = certificate(
        "Responder",
        keys[2],
        root,
        keys[0],
        eku=eku,
        extensions=[(x509.OCSPNoCheck(), False)],
        end=datetime.now(UTC) - timedelta(days=1) if kind == "expired" else None,
        usage=key_usage(sign=False) if kind == "key_usage" else None,
    )
    der = ocsp_response(
        leaf,
        root,
        responder,
        keys[2],
        embedded=[responder],
        responder_id=leaf if kind == "wrong_id" else None,
    )
    result = revocation.check_ocsp_response(der, leaf, root)
    assert result.status == RevocationStatus.UNKNOWN


def test_authorized_delegated_ocsp_responder_is_good(chain, keys):
    root, leaf = chain
    responder = certificate(
        "Responder",
        keys[2],
        root,
        keys[0],
        eku=[ExtendedKeyUsageOID.OCSP_SIGNING],
        extensions=[(x509.OCSPNoCheck(), False)],
    )
    der = ocsp_response(leaf, root, responder, keys[2], embedded=[responder])
    assert (
        revocation.check_ocsp_response(der, leaf, root).status == RevocationStatus.GOOD
    )


@pytest.mark.parametrize(
    "kind", ["issuer_name", "key_usage", "delta", "partial", "critical"]
)
def test_inapplicable_or_unsupported_crl_cannot_report_good(chain, keys, kind):
    root, leaf = chain
    extensions = []
    name = None
    if kind == "issuer_name":
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Another CA")])
    if kind == "key_usage":
        root = certificate("Root", keys[0], ca=True, usage=key_usage(cert_sign=True))
    if kind == "delta":
        extensions = [(x509.DeltaCRLIndicator(1), True)]
    if kind == "partial":
        extensions = [
            (
                x509.IssuingDistributionPoint(
                    None, None, True, False, None, False, False
                ),
                True,
            )
        ]
    if kind == "critical":
        extensions = [
            (x509.UnrecognizedExtension(ObjectIdentifier("1.2.3.4"), b"\x05\x00"), True)
        ]
    result = revocation.check_crl(
        crl(root, keys[0], extensions=extensions, issuer_name=name), leaf, root
    )
    assert result is None or result.status == RevocationStatus.UNKNOWN


@pytest.mark.parametrize(
    "kind",
    [
        "missing_ca",
        "not_ca",
        "path_length",
        "leaf_usage",
        "critical",
        "name_constraints",
    ],
)
def test_certificate_constraints_are_enforced(chain, keys, kind):
    root, _leaf = chain
    if kind == "path_length":
        root = certificate("Root", keys[0], ca=True, path_length=0)
    extensions = []
    if kind == "name_constraints":
        extensions = [
            (x509.NameConstraints([x509.DNSName("allowed.test")], None), True)
        ]
    inter = certificate(
        "Intermediate",
        keys[2],
        root,
        keys[0],
        ca=kind != "not_ca",
        basic=kind != "missing_ca",
        extensions=extensions,
    )
    leaf_extensions = []
    if kind == "critical":
        leaf_extensions = [
            (x509.UnrecognizedExtension(ObjectIdentifier("1.2.3.4"), b"\x05\x00"), True)
        ]
    if kind == "name_constraints":
        leaf_extensions = [
            (x509.SubjectAlternativeName([x509.DNSName("forbidden.test")]), False)
        ]
    leaf = certificate(
        "Signer",
        keys[1],
        inter,
        keys[2],
        extensions=leaf_extensions,
        usage=key_usage(sign=False) if kind == "leaf_usage" else None,
    )
    sig = pdf_signature(
        cms.build_cades_signed_data(DATA, leaf, keys[1], extra_certs=[inter])
    )
    assert (
        sig.validate(ValidationOptions(trusted_certificates=[root])).status
        == ValidationStatus.INVALID
    )


def test_self_signed_option_does_not_override_explicit_anchors(chain, keys):
    root, _ = chain
    leaf = certificate("Untrusted signer", keys[1])
    sig = pdf_signature(cms.build_cades_signed_data(DATA, leaf, keys[1]))
    assert (
        sig.validate(ValidationOptions(trusted_certificates=[root])).status
        == ValidationStatus.INVALID
    )


def test_claimed_signing_time_does_not_rescue_an_expired_certificate(chain, keys):
    root, _ = chain
    claimed = datetime.now(UTC) - timedelta(days=10)
    leaf = certificate(
        "Expired", keys[1], root, keys[0], end=claimed + timedelta(days=1)
    )
    sig = pdf_signature(
        cms.build_cades_signed_data(DATA, leaf, keys[1], signing_time=claimed)
    )
    result = sig.validate(ValidationOptions(trusted_certificates=[root]))
    assert result.status == ValidationStatus.INVALID
    assert any("expired" in error for error in result.errors)


@pytest.mark.parametrize(
    "kind",
    ["untrusted", "missing_eku", "noncritical_eku", "mixed_eku", "expired", "future"],
)
def test_invalid_tsa_cannot_establish_trusted_time(chain, keys, kind):
    root, leaf = chain
    eku = None if kind == "missing_eku" else [ExtendedKeyUsageOID.TIME_STAMPING]
    if kind == "mixed_eku":
        eku.append(ExtendedKeyUsageOID.SERVER_AUTH)
    tsa = certificate(
        "TSA",
        keys[2],
        None if kind == "untrusted" else root,
        None if kind == "untrusted" else keys[0],
        eku=eku,
        eku_critical=kind != "noncritical_eku",
        end=datetime.now(UTC) - timedelta(days=1) if kind == "expired" else None,
    )
    blob = cms.build_cades_signed_data(DATA, leaf, keys[1])
    when = datetime.now(UTC) + timedelta(days=1) if kind == "future" else None
    blob = cms.inject_unsigned_timestamp(blob, token_for(blob, tsa, keys[2], when=when))
    result = pdf_signature(blob).validate(
        ValidationOptions(trusted_certificates=[root])
    )
    assert result.status == ValidationStatus.INVALID
    assert not result.timestamp.verified
    assert result.pades_level == PadesLevel.B


def test_only_a_trusted_timestamp_can_establish_historical_validity(chain, keys):
    root, _ = chain
    when = datetime.now(UTC) - timedelta(days=10)
    leaf = certificate("Expired", keys[1], root, keys[0], end=when + timedelta(days=1))
    tsa = certificate(
        "TSA",
        keys[2],
        root,
        keys[0],
        eku=[ExtendedKeyUsageOID.TIME_STAMPING],
        eku_critical=True,
    )
    blob = cms.build_cades_signed_data(
        DATA, leaf, keys[1], signing_time=when - timedelta(days=1)
    )
    blob = cms.inject_unsigned_timestamp(blob, token_for(blob, tsa, keys[2], when=when))
    result = pdf_signature(blob).validate(
        ValidationOptions(trusted_certificates=[root])
    )
    assert result.status == ValidationStatus.VALID
    assert result.timestamp.trust_status == TrustStatus.TRUSTED
    assert result.trusted_at == when.isoformat()
    assert result.signed_at != result.trusted_at
    unchecked = pdf_signature(blob).validate(
        ValidationOptions(trusted_certificates=[root], check_timestamp=False)
    )
    assert unchecked.status == ValidationStatus.INVALID
    assert unchecked.trusted_at is None


def test_online_unknown_ocsp_falls_back_to_crl(chain, keys, monkeypatch):
    root, leaf = chain
    now = datetime.now(UTC)
    stale = ocsp_response(leaf, root, root, keys[0], start=now - timedelta(days=3))
    monkeypatch.setattr(revocation, "_ocsp_urls", lambda cert: ["https://ocsp.test"])
    monkeypatch.setattr(revocation, "_crl_urls", lambda cert: ["https://crl.test"])
    monkeypatch.setattr(revocation, "_http_post", lambda *args: stale)
    monkeypatch.setattr(revocation, "_http_get", lambda *args: crl(root, keys[0]))
    from aspose_pdf.validation import ValidationMode

    result = revocation.check_revocation(leaf, root, mode=ValidationMode.ONLINE)
    assert result.status == RevocationStatus.GOOD and result.source == "crl"


def test_path_builder_tries_alternative_issuers(chain, keys):
    root, _ = chain
    bad = certificate("Intermediate", keys[2], root, keys[0], ca=False)
    good = certificate("Intermediate", keys[2], root, keys[0], ca=True)
    leaf = certificate("Signer", keys[1], good, keys[2])
    result = cert_chain.build_and_validate(leaf, [bad, good], [root])
    assert result.trust_status == TrustStatus.TRUSTED and not result.errors
    assert result.chain[1] == good


def test_cyclic_chain_is_rejected_without_explicit_trust(keys):
    placeholder = certificate("B", keys[2], ca=True)
    first = certificate("A", keys[0], placeholder, keys[2], ca=True)
    second = certificate("B", keys[2], first, keys[0], ca=True)
    leaf = certificate("Signer", keys[1], first, keys[0])
    result = cert_chain.build_and_validate(leaf, [first, second])
    assert result.trust_status == TrustStatus.BROKEN
    assert any("cyclic" in error for error in result.errors)


def test_embedded_root_does_not_become_a_trust_anchor(chain, keys):
    root, leaf = chain
    blob = cms.build_cades_signed_data(DATA, leaf, keys[1], extra_certs=[root])
    result = pdf_signature(blob).validate()
    assert result.status == ValidationStatus.INVALID
    assert result.trust_status == TrustStatus.UNTRUSTED


@pytest.mark.parametrize("allowed", [False, True])
@pytest.mark.parametrize("kind", ["dns", "email", "uri", "ip", "directory"])
def test_name_constraints_allow_only_matching_names(chain, keys, allowed, kind):
    import ipaddress

    root, _ = chain
    if kind == "dns":
        constraint = x509.DNSName("allowed.test")
        name = x509.DNSName("sub.allowed.test" if allowed else "notallowed.test")
    elif kind == "email":
        constraint = x509.RFC822Name("allowed.test")
        name = x509.RFC822Name(
            "signer@allowed.test" if allowed else "signer@other.test"
        )
    elif kind == "uri":
        constraint = x509.UniformResourceIdentifier(".allowed.test")
        name = x509.UniformResourceIdentifier(
            "https://sub.allowed.test/a" if allowed else "https://other.test/a"
        )
    elif kind == "ip":
        constraint = x509.IPAddress(ipaddress.ip_network("192.0.2.0/24"))
        name = x509.IPAddress(
            ipaddress.ip_address("192.0.2.2" if allowed else "198.51.100.2")
        )
    else:
        constraint = x509.DirectoryName(
            x509.Name(
                [
                    x509.NameAttribute(
                        NameOID.COMMON_NAME, "Signer" if allowed else "Other"
                    )
                ]
            )
        )
        name = None
    inter = certificate(
        "Intermediate",
        keys[2],
        root,
        keys[0],
        ca=True,
        extensions=[(x509.NameConstraints([constraint], None), True)],
    )
    leaf = certificate(
        "Signer",
        keys[1],
        inter,
        keys[2],
        extensions=[(x509.SubjectAlternativeName([name]), False)] if name else [],
    )
    result = cert_chain.build_and_validate(leaf, [inter], [root])
    assert bool(result.errors) is not allowed


def test_excluded_names_and_unsupported_policy_constraints_fail(chain, keys):
    root, _ = chain
    inter = certificate(
        "Intermediate",
        keys[2],
        root,
        keys[0],
        ca=True,
        extensions=[(x509.NameConstraints(None, [x509.DNSName("bad.test")]), True)],
    )
    leaf = certificate(
        "Signer",
        keys[1],
        inter,
        keys[2],
        extensions=[(x509.SubjectAlternativeName([x509.DNSName("bad.test")]), False)],
    )
    assert cert_chain.build_and_validate(leaf, [inter], [root]).errors
    constrained = certificate(
        "Signer",
        keys[1],
        root,
        keys[0],
        extensions=[(x509.PolicyConstraints(0, None), True)],
    )
    assert cert_chain.build_and_validate(constrained, [], [root]).errors


@pytest.mark.parametrize("kind", ["issuer_usage", "leaf_eku", "issuer_eku"])
def test_key_purposes_constrain_the_whole_chain(chain, keys, kind):
    root, _ = chain
    inter = certificate(
        "Intermediate",
        keys[2],
        root,
        keys[0],
        ca=True,
        usage=key_usage() if kind == "issuer_usage" else None,
        eku=[ExtendedKeyUsageOID.SERVER_AUTH] if kind == "issuer_eku" else None,
    )
    leaf = certificate(
        "Signer",
        keys[1],
        inter,
        keys[2],
        eku=[ExtendedKeyUsageOID.SERVER_AUTH] if kind == "leaf_eku" else None,
    )
    assert cert_chain.build_and_validate(leaf, [inter], [root]).errors


def test_revoked_intermediate_invalidates_a_signature(chain, keys):
    root, _ = chain
    inter = certificate("Intermediate", keys[2], root, keys[0], ca=True)
    leaf = certificate("Signer", keys[1], inter, keys[2])
    now = datetime.now(UTC)
    root_crl = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(root.subject)
        .last_update(now - timedelta(hours=1))
        .next_update(now + timedelta(hours=1))
        .add_revoked_certificate(
            x509.RevokedCertificateBuilder()
            .serial_number(inter.serial_number)
            .revocation_date(now - timedelta(minutes=30))
            .build()
        )
        .sign(keys[0], hashes.SHA256())
        .public_bytes(serialization.Encoding.DER)
    )
    blob = cms.build_cades_signed_data(DATA, leaf, keys[1], extra_certs=[inter])
    blob = cms.inject_crls(blob, [crl(inter, keys[2]), root_crl])
    result = pdf_signature(blob).validate(
        ValidationOptions(trusted_certificates=[root], check_revocation=True)
    )
    assert result.revocation_status == RevocationStatus.REVOKED
    assert result.status == ValidationStatus.INVALID


def test_stale_revocation_data_makes_validation_unknown(chain, keys):
    root, leaf = chain
    now = datetime.now(UTC)
    blob = cms.build_cades_signed_data(DATA, leaf, keys[1])
    blob = cms.inject_crls(
        blob,
        [
            crl(
                root,
                keys[0],
                start=now - timedelta(days=3),
                end=now - timedelta(days=2),
            )
        ],
    )
    result = pdf_signature(blob).validate(
        ValidationOptions(trusted_certificates=[root], check_revocation=True)
    )
    assert result.revocation_status == RevocationStatus.UNKNOWN
    assert result.status == ValidationStatus.UNKNOWN
    assert not result.is_valid


def test_invalid_trust_anchor_data_does_not_disable_trust_checks(chain, keys):
    _root, leaf = chain
    sig = pdf_signature(cms.build_cades_signed_data(DATA, leaf, keys[1]))
    assert (
        sig.validate(
            ValidationOptions(trusted_certificates=[b"not a certificate"])
        ).status
        == ValidationStatus.INVALID
    )


def test_document_timestamp_integrity_does_not_imply_tsa_trust(chain, keys, tmp_path):
    from aspose_pdf import Document
    from tests.helpers_signatures import timestamp_authority

    root, _ = chain
    tsa = timestamp_authority()
    path = tmp_path / "timestamped.pdf"
    with Document() as document:
        document.pages.add().add_text("Document", 40, 80)
        document.add_document_timestamp(timestamp_authority=tsa)
        document.save(path)
    with Document(path) as document:
        signature = document.signatures[0]
        assert signature.valid
        untrusted = signature.validate(ValidationOptions(trusted_certificates=[root]))
        assert untrusted.status == ValidationStatus.INVALID
        assert not untrusted.timestamp.verified
        trusted = signature.validate(ValidationOptions(trusted_certificates=[tsa[0]]))
        assert trusted.status == ValidationStatus.VALID
        assert trusted.trusted_at == trusted.timestamp.gen_time.isoformat()


def test_timestamp_requires_its_own_revocation_evidence(chain, keys):
    root, leaf = chain
    intermediate = certificate("TSA Intermediate", keys[3], root, keys[0], ca=True)
    tsa = certificate(
        "TSA",
        keys[2],
        intermediate,
        keys[3],
        eku=[ExtendedKeyUsageOID.TIME_STAMPING],
        eku_critical=True,
    )
    blob = cms.build_cades_signed_data(DATA, leaf, keys[1], extra_certs=[intermediate])
    blob = cms.inject_unsigned_timestamp(blob, token_for(blob, tsa, keys[2]))
    blob = cms.inject_crls(blob, [crl(root, keys[0])])
    opts = ValidationOptions(trusted_certificates=[root], check_revocation=True)
    result = pdf_signature(blob).validate(opts)
    assert result.status == ValidationStatus.UNKNOWN
    assert result.timestamp.revocation_status == RevocationStatus.UNKNOWN
    assert result.trusted_at is None
    blob = cms.inject_crls(blob, [crl(intermediate, keys[3])])
    result = pdf_signature(blob).validate(opts)
    assert result.status == ValidationStatus.VALID
    assert result.timestamp.revocation_status == RevocationStatus.GOOD


def test_delegated_responder_without_no_check_needs_a_current_crl(chain, keys):
    root, leaf = chain
    responder = certificate(
        "Responder", keys[2], root, keys[0], eku=[ExtendedKeyUsageOID.OCSP_SIGNING]
    )
    der = ocsp_response(leaf, root, responder, keys[2], embedded=[responder])
    assert (
        revocation.check_ocsp_response(der, leaf, root).status
        == RevocationStatus.UNKNOWN
    )
    assert (
        revocation.check_ocsp_response(
            der, leaf, root, embedded_crls=[crl(root, keys[0])]
        ).status
        == RevocationStatus.GOOD
    )


@pytest.mark.parametrize(
    "kind", ["future_production", "single_critical", "response_critical"]
)
def test_ocsp_rejects_invalid_production_time_and_critical_extensions(
    chain, keys, kind
):
    from asn1crypto import ocsp as asn1_ocsp

    root, leaf = chain
    response = asn1_ocsp.OCSPResponse.load(ocsp_response(leaf, root, root, keys[0]))
    basic = response["response_bytes"]["response"].parsed
    data = basic["tbs_response_data"]
    if kind == "future_production":
        data["produced_at"] = (datetime.now(UTC) + timedelta(days=1)).replace(
            microsecond=0
        )
    else:
        owner = data["responses"][0] if kind == "single_critical" else data
        name = (
            "single_extensions" if kind == "single_critical" else "response_extensions"
        )
        owner[name] = [
            {"extn_id": "1.2.3.4", "critical": True, "extn_value": b"\x05\x00"}
        ]
    basic["signature"] = keys[0].sign(data.dump(), padding.PKCS1v15(), hashes.SHA256())
    response["response_bytes"]["response"] = basic
    result = revocation.check_ocsp_response(response.dump(), leaf, root)
    assert result.status == RevocationStatus.UNKNOWN
