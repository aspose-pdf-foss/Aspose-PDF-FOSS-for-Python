"""CMS / PKCS#7 ``SignedData`` parsing and signer verification.

``cryptography`` (as of 48.x) can *build* PKCS#7 signatures and list embedded
certificates, but exposes **no** API to inspect a ``SignedData`` structure —
``SignerInfo`` fields, signed attributes, the actual signature value, embedded
timestamps, or revocation material.  This module fills that gap by parsing the
DER with :mod:`asn1crypto` and verifying the signer's signature with the
public-key primitives in :mod:`cryptography`.

The functions here are pure ASN.1 / crypto helpers; PDF-specific orchestration
lives in :mod:`aspose_pdf.engine.signature_validator`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Sequence, Tuple

from asn1crypto import cms, core, tsp
from asn1crypto import crl as asn1_crl
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed448, ed25519, padding, rsa

# OID of the RFC 3161 signature-timestamp unsigned attribute.
TIMESTAMP_TOKEN_OID = "1.2.840.113549.1.9.16.2.14"
# ESS signing-certificate attributes (RFC 5035) that bind the signer's
# certificate into the signed attributes — required for CAdES/PAdES.
SIGNING_CERT_V2_OID = "1.2.840.113549.1.9.16.2.47"  # signing-certificate-v2
SIGNING_CERT_V1_OID = "1.2.840.113549.1.9.16.2.12"  # signing-certificate (SHA-1)

# Errors that mean "this is not a CMS structure we can read".
_PARSE_ERRORS = (ValueError, TypeError, KeyError, OverflowError)

_HASH_BY_NAME = {
    "md5": hashes.MD5,
    "sha1": hashes.SHA1,
    "sha224": hashes.SHA224,
    "sha256": hashes.SHA256,
    "sha384": hashes.SHA384,
    "sha512": hashes.SHA512,
    "sha3_256": hashes.SHA3_256,
    "sha3_384": hashes.SHA3_384,
    "sha3_512": hashes.SHA3_512,
}


def hash_from_name(name: Optional[str]):
    """Return a ``cryptography`` hash instance for an asn1crypto algo name."""
    cls = _HASH_BY_NAME.get((name or "").lower())
    if cls is None:
        raise UnsupportedAlgorithm(f"Unsupported digest algorithm: {name!r}")
    return cls()


@dataclass
class SignedDataInfo:
    """Everything we need from a parsed CMS ``SignedData``."""

    signer_cert: Optional[x509.Certificate]
    certificates: List[x509.Certificate] = field(default_factory=list)
    digest_algo: str = "sha256"
    signature_algo: str = "rsassa_pkcs1v15"
    signature: bytes = b""
    signed_attrs_der: Optional[bytes] = None
    message_digest: Optional[bytes] = None
    signing_time: Optional[datetime] = None
    pss_params: Optional[object] = None
    econtent: Optional[bytes] = None
    timestamp_token_der: Optional[bytes] = None
    crls_der: List[bytes] = field(default_factory=list)
    ocsps_der: List[bytes] = field(default_factory=list)
    # ESS signing-certificate cert IDs as ``(hash_algo, cert_hash)`` pairs.  The
    # first entry (per RFC 5035) identifies the signer's own certificate.  Empty
    # when the signature carries no signing-certificate attribute (i.e. it is a
    # bare PKCS#7 rather than CAdES/PAdES).
    ess_cert_ids: List[Tuple[str, bytes]] = field(default_factory=list)


@dataclass
class SignerVerification:
    """Outcome of verifying a single signer."""

    intact: bool
    digest_ok: bool
    signature_ok: bool
    reason: str = ""


def _match_signer_cert(signer_info, asn1_certs, crypto_certs):
    """Return the ``cryptography`` certificate that matches the SignerInfo SID."""
    sid = signer_info["sid"]
    if sid.name == "issuer_and_serial_number":
        iasn = sid.chosen
        issuer = iasn["issuer"]
        serial = iasn["serial_number"].native
        for a_cert, c_cert in zip(asn1_certs, crypto_certs):
            if a_cert.serial_number == serial and a_cert.issuer == issuer:
                return c_cert
    elif sid.name == "subject_key_identifier":
        ski = sid.chosen.native
        for a_cert, c_cert in zip(asn1_certs, crypto_certs):
            if a_cert.key_identifier == ski:
                return c_cert
    # Fall back to the first certificate when the SID cannot be resolved.
    return crypto_certs[0] if crypto_certs else None


def _collect_revocation(signed_data):
    """Return (crls_der, ocsps_der) embedded in a SignedData ``crls`` field."""
    crls_der: List[bytes] = []
    ocsps_der: List[bytes] = []
    revocation = signed_data["crls"]
    if isinstance(revocation, core.Void):
        return crls_der, ocsps_der
    for choice in revocation:
        try:
            if choice.name == "crl":
                crls_der.append(choice.chosen.dump())
            else:  # 'other' -> OtherRevocationInfoFormat (e.g. OCSP response)
                values = choice.chosen["values"]
                ocsps_der.append(values.dump())
        except _PARSE_ERRORS:
            continue
    return crls_der, ocsps_der


def _signed_attrs_der(signer_info) -> Optional[bytes]:
    """Return the DER of the signed attributes as an explicit ``SET OF``.

    RFC 5652 §5.4: the value signed is the DER of ``SignedAttributes`` encoded
    with an explicit ``SET OF`` tag, *not* the implicit ``[0]`` tag used inside
    the ``SignerInfo``.  Re-tagging the leading identifier octet ``0xA0`` to
    ``0x31`` yields that encoding (the length octets are unchanged).
    """
    sa = signer_info["signed_attrs"]
    if isinstance(sa, core.Void) or sa.native is None:
        return None
    der = sa.dump()
    return b"\x31" + der[1:]


def _signed_attr_values(signer_info):
    """Yield ``(type_name, dotted_oid, first_value)`` for each signed attribute."""
    sa = signer_info["signed_attrs"]
    if isinstance(sa, core.Void) or sa.native is None:
        return
    for attr in sa:
        try:
            yield attr["type"].native, attr["type"].dotted, attr["values"][0]
        except _PARSE_ERRORS:
            continue


def _parse_ess_cert_ids(value, is_v2: bool) -> List[Tuple[str, bytes]]:
    """Extract ``(hash_algo, cert_hash)`` pairs from an ESS signing-cert attr.

    ``value`` is the parsed ``SigningCertificateV2`` (``is_v2``) or
    ``SigningCertificate`` (SHA-1, fixed) structure.
    """
    out: List[Tuple[str, bytes]] = []
    try:
        for cert_id in value["certs"]:
            if is_v2:
                algo = cert_id["hash_algorithm"]["algorithm"].native or "sha256"
            else:
                algo = "sha1"
            out.append((algo, cert_id["cert_hash"].native))
    except _PARSE_ERRORS:
        return out
    return out


def _find_timestamp_token(signer_info) -> Optional[bytes]:
    """Return the DER of the RFC 3161 timestamp token in unsigned attrs, if any."""
    ua = signer_info["unsigned_attrs"]
    if isinstance(ua, core.Void) or ua.native is None:
        return None
    for attr in ua:
        try:
            if attr["type"].dotted == TIMESTAMP_TOKEN_OID:
                return attr["values"][0].dump()
        except _PARSE_ERRORS:
            continue
    return None


def parse_signed_data(der: bytes) -> SignedDataInfo:
    """Parse a DER-encoded CMS ``SignedData`` blob.

    Raises ``ValueError`` when *der* is not a CMS ``SignedData`` container.
    """
    content_info = cms.ContentInfo.load(der)
    if content_info["content_type"].native != "signed_data":
        raise ValueError("CMS content is not signed_data")
    signed_data = content_info["content"]

    asn1_certs = []
    crypto_certs: List[x509.Certificate] = []
    certs = signed_data["certificates"]
    if not isinstance(certs, core.Void):
        for choice in certs:
            if choice.name != "certificate":
                continue
            a_cert = choice.chosen
            asn1_certs.append(a_cert)
            crypto_certs.append(x509.load_der_x509_certificate(a_cert.dump()))

    signer_infos = signed_data["signer_infos"]
    signer_info = signer_infos[0]

    signer_cert = _match_signer_cert(signer_info, asn1_certs, crypto_certs)

    sig_alg = signer_info["signature_algorithm"]
    pss_params = None
    if sig_alg.signature_algo == "rsassa_pss":
        pss_params = sig_alg["parameters"]

    message_digest: Optional[bytes] = None
    signing_time: Optional[datetime] = None
    ess_cert_ids: List[Tuple[str, bytes]] = []
    for type_name, _dotted, value in _signed_attr_values(signer_info):
        if type_name == "message_digest":
            message_digest = value.native
        elif type_name == "signing_time":
            signing_time = value.native
        elif type_name == "signing_certificate_v2":
            ess_cert_ids = _parse_ess_cert_ids(value, is_v2=True)
        elif type_name == "signing_certificate" and not ess_cert_ids:
            ess_cert_ids = _parse_ess_cert_ids(value, is_v2=False)

    crls_der, ocsps_der = _collect_revocation(signed_data)

    encap = signed_data["encap_content_info"]
    econtent = None
    raw_content = encap["content"]
    if not isinstance(raw_content, core.Void) and raw_content.native is not None:
        econtent = raw_content.native

    return SignedDataInfo(
        signer_cert=signer_cert,
        certificates=crypto_certs,
        digest_algo=signer_info["digest_algorithm"]["algorithm"].native,
        signature_algo=sig_alg.signature_algo,
        signature=signer_info["signature"].native,
        signed_attrs_der=_signed_attrs_der(signer_info),
        message_digest=message_digest,
        signing_time=signing_time,
        pss_params=pss_params,
        econtent=econtent,
        timestamp_token_der=_find_timestamp_token(signer_info),
        crls_der=crls_der,
        ocsps_der=ocsps_der,
        ess_cert_ids=ess_cert_ids,
    )


def verify_signing_certificate(info: SignedDataInfo) -> Optional[bool]:
    """Verify the ESS signing-certificate attribute binds the signer cert.

    Returns ``True``/``False`` when a signing-certificate(-v2) attribute is
    present (CAdES/PAdES), or ``None`` when it is absent (a bare PKCS#7
    signature, where the binding does not apply).  Per RFC 5035 the first
    ``ESSCertID`` identifies the signing certificate, so only that entry is
    matched against the signer's own certificate.
    """
    if not info.ess_cert_ids or info.signer_cert is None:
        return None
    algo, declared = info.ess_cert_ids[0]
    try:
        der = info.signer_cert.public_bytes(serialization.Encoding.DER)
        digest = hashes.Hash(hash_from_name(algo))
        digest.update(der)
        return digest.finalize() == declared
    except (UnsupportedAlgorithm, ValueError, TypeError):
        return False


def _verify_public_key_signature(
    cert: x509.Certificate,
    signature: bytes,
    signed_bytes: bytes,
    signature_algo: str,
    hash_name: str,
    pss_params,
) -> None:
    """Verify *signature* over *signed_bytes*; raise ``InvalidSignature`` on failure."""
    public_key = cert.public_key()

    if signature_algo == "ed25519":
        if not isinstance(public_key, ed25519.Ed25519PublicKey):
            raise InvalidSignature("key/alg mismatch for Ed25519")
        public_key.verify(signature, signed_bytes)
        return
    if signature_algo == "ed448":
        if not isinstance(public_key, ed448.Ed448PublicKey):
            raise InvalidSignature("key/alg mismatch for Ed448")
        public_key.verify(signature, signed_bytes)
        return

    hash_alg = hash_from_name(hash_name)

    if signature_algo == "ecdsa":
        if not isinstance(public_key, ec.EllipticCurvePublicKey):
            raise InvalidSignature("key/alg mismatch for ECDSA")
        public_key.verify(signature, signed_bytes, ec.ECDSA(hash_alg))
        return

    if not isinstance(public_key, rsa.RSAPublicKey):
        raise InvalidSignature(f"unexpected key type for {signature_algo}")

    if signature_algo == "rsassa_pss":
        mgf_hash = hash_alg
        salt_len = hash_alg.digest_size
        if pss_params is not None:
            try:
                mgf_hash = hash_from_name(
                    pss_params["mask_gen_algorithm"]["parameters"]["algorithm"].native
                )
                salt_len = pss_params["salt_length"].native
            except _PARSE_ERRORS:
                pass
        public_key.verify(
            signature,
            signed_bytes,
            padding.PSS(mgf=padding.MGF1(mgf_hash), salt_length=salt_len),
            hash_alg,
        )
        return

    # Default: RSASSA-PKCS1-v1_5.
    public_key.verify(signature, signed_bytes, padding.PKCS1v15(), hash_alg)


def verify_signer(info: SignedDataInfo, signed_bytes: bytes) -> SignerVerification:
    """Cryptographically verify a parsed signer against *signed_bytes*.

    *signed_bytes* is the data the signature is meant to protect: for a PDF
    detached signature it is the concatenated ByteRange slices; for an attached
    CMS (e.g. an RFC 3161 token) it is the encapsulated content.
    """
    if info.signer_cert is None:
        return SignerVerification(False, False, False, "no signer certificate")

    try:
        digest = hashes.Hash(hash_from_name(info.digest_algo))
        digest.update(signed_bytes)
        computed = digest.finalize()
    except UnsupportedAlgorithm as exc:
        return SignerVerification(False, False, False, str(exc))

    digest_ok = True
    if info.signed_attrs_der is not None:
        # With signed attributes the messageDigest attribute must equal the
        # digest of the content, and the signature covers the attributes.
        if info.message_digest is None:
            return SignerVerification(False, False, False, "missing messageDigest")
        digest_ok = info.message_digest == computed
        if not digest_ok:
            return SignerVerification(False, False, False, "messageDigest mismatch")
        to_verify = info.signed_attrs_der
    else:
        # No signed attributes: the signature is computed over the content.
        to_verify = signed_bytes

    try:
        _verify_public_key_signature(
            info.signer_cert,
            info.signature,
            to_verify,
            info.signature_algo,
            info.digest_algo,
            info.pss_params,
        )
    except InvalidSignature:
        return SignerVerification(False, digest_ok, False, "signature verification failed")
    except (UnsupportedAlgorithm, ValueError, TypeError) as exc:
        return SignerVerification(False, digest_ok, False, f"verification error: {exc}")

    return SignerVerification(True, digest_ok, True, "")


# ---------------------------------------------------------------------------
# CMS construction (signing side)
# ---------------------------------------------------------------------------
def ess_signing_cert_v2_attr(cert: x509.Certificate, hash_algo: str = "sha256"):
    """Build the ESS ``signing-certificate-v2`` signed attribute for *cert*.

    This attribute is what distinguishes a CAdES/PAdES signature from a bare
    PKCS#7 one: it binds the signer's certificate into the signed data so it
    cannot be substituted.
    """
    der = cert.public_bytes(serialization.Encoding.DER)
    digest = hashes.Hash(hash_from_name(hash_algo))
    digest.update(der)
    cert_id = {"cert_hash": digest.finalize()}
    # SHA-256 is the ESSCertIDv2 default and is omitted for canonical encoding.
    if hash_algo.lower() != "sha256":
        cert_id["hash_algorithm"] = {"algorithm": hash_algo}
    structure = tsp.SigningCertificateV2({"certs": [tsp.ESSCertIDv2(cert_id)]})
    return cms.CMSAttribute(
        {"type": "signing_certificate_v2", "values": [structure]}
    )


def _asn1_signature_algorithm(private_key, hash_algo: str) -> str:
    """Return the asn1crypto SignedDigestAlgorithm id for *private_key*."""
    if isinstance(private_key, rsa.RSAPrivateKey):
        return "rsassa_pkcs1v15"
    if isinstance(private_key, ec.EllipticCurvePrivateKey):
        return f"{hash_algo.lower()}_ecdsa"
    if isinstance(private_key, ed25519.Ed25519PrivateKey):
        return "ed25519"
    if isinstance(private_key, ed448.Ed448PrivateKey):
        return "ed448"
    raise UnsupportedAlgorithm(f"Unsupported signing key: {type(private_key).__name__}")


def _sign_bytes(private_key, hash_algo: str, payload: bytes) -> bytes:
    """Sign *payload* with the appropriate scheme for the key type."""
    if isinstance(private_key, rsa.RSAPrivateKey):
        return private_key.sign(payload, padding.PKCS1v15(), hash_from_name(hash_algo))
    if isinstance(private_key, ec.EllipticCurvePrivateKey):
        return private_key.sign(payload, ec.ECDSA(hash_from_name(hash_algo)))
    if isinstance(private_key, (ed25519.Ed25519PrivateKey, ed448.Ed448PrivateKey)):
        return private_key.sign(payload)
    raise UnsupportedAlgorithm(f"Unsupported signing key: {type(private_key).__name__}")


def build_cades_signed_data(
    data: bytes,
    signer_cert: x509.Certificate,
    signer_key,
    *,
    hash_algo: str = "sha256",
    extra_certs: Sequence[x509.Certificate] = (),
    signing_time: Optional[datetime] = None,
) -> bytes:
    """Build a detached **CAdES-BES** ``SignedData`` over *data* (PAdES-B core).

    The result carries the signed attributes required by ETSI EN 319 122 /
    319 142: ``content-type``, ``message-digest``, ``signing-time`` and the ESS
    ``signing-certificate-v2``.  It is otherwise a normal detached CMS and is
    verified by :func:`verify_signer`.
    """
    if signing_time is None:
        signing_time = datetime.now(timezone.utc)

    digest = hashes.Hash(hash_from_name(hash_algo))
    digest.update(data)
    message_digest = digest.finalize()

    signed_attrs = cms.CMSAttributes(
        [
            cms.CMSAttribute({"type": "content_type", "values": ["data"]}),
            cms.CMSAttribute(
                {"type": "signing_time", "values": [cms.Time({"utc_time": signing_time})]}
            ),
            cms.CMSAttribute({"type": "message_digest", "values": [message_digest]}),
            ess_signing_cert_v2_attr(signer_cert, hash_algo),
        ]
    )
    # RFC 5652 §5.4: sign the DER of SignedAttributes under an explicit SET OF
    # tag (0x31), not the implicit [0] that appears inside the SignerInfo.
    to_sign = b"\x31" + signed_attrs.dump(force=True)[1:]
    signature = _sign_bytes(signer_key, hash_algo, to_sign)

    asn1_signer = asn1_x509.Certificate.load(
        signer_cert.public_bytes(serialization.Encoding.DER)
    )
    signer_info = cms.SignerInfo(
        {
            "version": "v1",
            "sid": {
                "issuer_and_serial_number": {
                    "issuer": asn1_signer.issuer,
                    "serial_number": signer_cert.serial_number,
                }
            },
            "digest_algorithm": {"algorithm": hash_algo},
            "signed_attrs": signed_attrs,
            "signature_algorithm": {
                "algorithm": _asn1_signature_algorithm(signer_key, hash_algo)
            },
            "signature": signature,
        }
    )

    asn1_certs = [asn1_signer]
    for extra in extra_certs:
        asn1_certs.append(
            asn1_x509.Certificate.load(extra.public_bytes(serialization.Encoding.DER))
        )

    signed_data = cms.SignedData(
        {
            "version": "v1",
            "digest_algorithms": [{"algorithm": hash_algo}],
            "encap_content_info": {"content_type": "data"},
            "certificates": asn1_certs,
            "signer_infos": [signer_info],
        }
    )
    return cms.ContentInfo(
        {"content_type": "signed_data", "content": signed_data}
    ).dump()


# ---------------------------------------------------------------------------
# CMS mutation helpers (used on the signing side)
# ---------------------------------------------------------------------------
def inject_unsigned_timestamp(der: bytes, token_der: bytes) -> bytes:
    """Return *der* with an RFC 3161 timestamp added as an unsigned attribute.

    Unsigned attributes are not covered by the signer's signature, so the
    existing signature stays valid.
    """
    content_info = cms.ContentInfo.load(der)
    signed_data = content_info["content"]
    signer_info = signed_data["signer_infos"][0]

    token = cms.ContentInfo.load(token_der)
    attr = cms.CMSAttribute(
        {"type": "signature_time_stamp_token", "values": [token]}
    )

    existing = signer_info["unsigned_attrs"]
    attrs = []
    if not isinstance(existing, core.Void) and existing.native is not None:
        attrs = list(existing)
    attrs.append(attr)
    signer_info["unsigned_attrs"] = cms.CMSAttributes(attrs)
    return content_info.dump()


def inject_crls(der: bytes, crls_der) -> bytes:
    """Return *der* with the given DER CRLs embedded in the SignedData ``crls``.

    The ``crls`` field is outside the signed content, so the signature is not
    affected.
    """
    crls_der = list(crls_der)
    if not crls_der:
        return der
    content_info = cms.ContentInfo.load(der)
    signed_data = content_info["content"]

    choices = []
    existing = signed_data["crls"]
    if not isinstance(existing, core.Void):
        choices = list(existing)
    for crl_bytes in crls_der:
        choices.append(
            cms.RevocationInfoChoice(
                name="crl", value=asn1_crl.CertificateList.load(crl_bytes)
            )
        )
    signed_data["crls"] = cms.RevocationInfoChoices(choices)
    return content_info.dump()
