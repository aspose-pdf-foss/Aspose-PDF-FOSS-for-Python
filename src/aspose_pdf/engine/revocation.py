"""Certificate revocation checking via OCSP and CRL.

Two modes of operation:

* **Offline** (default) — only revocation material already embedded in the
  document/CMS is consulted (OCSP responses and CRLs).  No network access.
* **Opt-in online** — when the caller selects ``ValidationMode.ONLINE`` or
  ``AUTO`` and the embedded material is inconclusive, the responder/CRL is
  fetched over HTTP from the certificate's AIA / CRL-distribution-point URLs.

The two HTTP helpers (:func:`_http_post`, :func:`_http_get`) are deliberately
isolated so tests can monkeypatch them and exercise the online path without a
network.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise

from asn1crypto import ocsp as asn1_ocsp
from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed448, ed25519, padding, rsa
from cryptography.x509 import ocsp
from cryptography.x509.oid import (
    AuthorityInformationAccessOID,
    ExtendedKeyUsageOID,
    ExtensionOID,
)

from aspose_pdf.validation import RevocationStatus, ValidationMode

_NET_ERRORS = (OSError, ValueError)
_MAX_OCSP_AGE = timedelta(days=1)


@dataclass
class RevocationResult:
    status: RevocationStatus
    source: str = ""
    detail: str = ""


# ---------------------------------------------------------------------------
# Signature helpers
# ---------------------------------------------------------------------------
def _pubkey_verify(public_key, signature, data, hash_alg) -> bool:
    try:
        if isinstance(public_key, rsa.RSAPublicKey):
            public_key.verify(signature, data, padding.PKCS1v15(), hash_alg)
        elif isinstance(public_key, ec.EllipticCurvePublicKey):
            public_key.verify(signature, data, ec.ECDSA(hash_alg))
        elif isinstance(public_key, (ed25519.Ed25519PublicKey, ed448.Ed448PublicKey)):
            public_key.verify(signature, data)
        else:
            return False
        return True
    except (InvalidSignature, ValueError, TypeError, UnsupportedAlgorithm):
        return False


def _time(value: datetime | None) -> datetime:
    value = value or datetime.now(UTC)
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _current(this_update, next_update, at_time, *, ocsp_without_next=False) -> bool:
    if this_update > at_time:
        return False
    if next_update is None:
        return ocsp_without_next and at_time - this_update <= _MAX_OCSP_AGE
    return this_update < next_update and at_time < next_update


def _issued_by(cert, issuer) -> bool:
    try:
        cert.verify_directly_issued_by(issuer)
        return True
    except (ValueError, TypeError, InvalidSignature, UnsupportedAlgorithm):
        return False


def _ocsp_status_to_enum(status) -> RevocationStatus:
    if status == ocsp.OCSPCertStatus.GOOD:
        return RevocationStatus.GOOD
    if status == ocsp.OCSPCertStatus.REVOKED:
        return RevocationStatus.REVOKED
    return RevocationStatus.UNKNOWN


# ---------------------------------------------------------------------------
# OCSP
# ---------------------------------------------------------------------------
def _verify_ocsp_signature(
    response: ocsp.OCSPResponse,
    issuer: x509.Certificate,
    at_time: datetime,
    embedded_crls: Sequence[bytes],
) -> bool:
    """Verify the OCSP response was signed by the issuer or a delegated responder."""
    from .cert_chain import build_and_validate

    candidates = [issuer, *response.certificates]
    for cand in candidates:
        if response.responder_name is not None:
            if response.responder_name != cand.subject:
                continue
        elif (
            response.responder_key_hash
            != x509.SubjectKeyIdentifier.from_public_key(cand.public_key()).digest
        ):
            continue
        if not (cand.not_valid_before_utc <= at_time <= cand.not_valid_after_utc):
            continue
        if cand.fingerprint(hashes.SHA256()) != issuer.fingerprint(hashes.SHA256()):
            if not _issued_by(cand, issuer):
                continue
            try:
                eku = cand.extensions.get_extension_for_oid(
                    ExtensionOID.EXTENDED_KEY_USAGE
                ).value
                if ExtendedKeyUsageOID.OCSP_SIGNING not in eku:
                    continue
            except x509.ExtensionNotFound:
                continue
            chain = build_and_validate(
                cand,
                trust_roots=[issuer],
                at_time=at_time,
                purpose=ExtendedKeyUsageOID.OCSP_SIGNING,
            )
            if chain.errors:
                continue
            try:
                usage = cand.extensions.get_extension_for_oid(
                    ExtensionOID.KEY_USAGE
                ).value
                if not usage.digital_signature:
                    continue
            except x509.ExtensionNotFound:
                pass
            # Avoid recursive OCSP trust. A delegated responder needs a
            # CA-issued no-check exemption or a current, complete CRL.
            statuses = [
                check_crl(der, cand, issuer, at_time=at_time) for der in embedded_crls
            ]
            if any(r and r.status == RevocationStatus.REVOKED for r in statuses):
                continue
            try:
                cand.extensions.get_extension_for_oid(ExtensionOID.OCSP_NO_CHECK)
            except x509.ExtensionNotFound:
                if not any(r and r.status == RevocationStatus.GOOD for r in statuses):
                    continue
        if _pubkey_verify(
            cand.public_key(),
            response.signature,
            response.tbs_response_bytes,
            response.signature_hash_algorithm,
        ):
            return True
    return False


def check_ocsp_response(
    der: bytes,
    cert: x509.Certificate,
    issuer: x509.Certificate,
    *,
    at_time: datetime | None = None,
    embedded_crls: Sequence[bytes] = (),
) -> RevocationResult | None:
    """Evaluate a single DER OCSP response for *cert*; ``None`` if not applicable."""
    at_time = _time(at_time)
    try:
        response = ocsp.load_der_ocsp_response(der)
    except _NET_ERRORS:
        return None
    if response.response_status != ocsp.OCSPResponseStatus.SUCCESSFUL:
        return None
    if not _issued_by(cert, issuer):
        return None
    matches = []
    try:
        for single in response.responses:
            if single.serial_number != cert.serial_number:
                continue
            requested = (
                ocsp.OCSPRequestBuilder()
                .add_certificate(cert, issuer, single.hash_algorithm)
                .build()
            )
            if (
                single.issuer_name_hash == requested.issuer_name_hash
                and single.issuer_key_hash == requested.issuer_key_hash
            ):
                matches.append(single)
        if not matches:
            return None
        if len(matches) != 1:
            return RevocationResult(
                RevocationStatus.UNKNOWN, "ocsp", "ambiguous OCSP responses"
            )
        single = matches[0]
        if any(extension.critical for extension in response.extensions):
            return RevocationResult(
                RevocationStatus.UNKNOWN, "ocsp", "unsupported critical OCSP extension"
            )
        basic = asn1_ocsp.OCSPResponse.load(der)["response_bytes"]["response"].parsed
        for entry in basic["tbs_response_data"]["responses"]:
            if any(ext["critical"].native for ext in entry["single_extensions"]):
                return RevocationResult(
                    RevocationStatus.UNKNOWN,
                    "ocsp",
                    "unsupported critical OCSP single extension",
                )
        if not _current(
            single.this_update_utc,
            single.next_update_utc,
            at_time,
            ocsp_without_next=True,
        ):
            return RevocationResult(
                RevocationStatus.UNKNOWN,
                "ocsp",
                "OCSP response is stale or not yet valid",
            )
        produced = response.produced_at_utc
        if produced > datetime.now(UTC) or produced < single.this_update_utc:
            return RevocationResult(
                RevocationStatus.UNKNOWN, "ocsp", "invalid OCSP production time"
            )
        if (
            single.certificate_status == ocsp.OCSPCertStatus.REVOKED
            and single.revocation_time_utc > produced
        ):
            return RevocationResult(
                RevocationStatus.UNKNOWN, "ocsp", "invalid OCSP revocation time"
            )
        signature_ok = _verify_ocsp_signature(response, issuer, produced, embedded_crls)
    except (ValueError, TypeError, UnsupportedAlgorithm):
        return RevocationResult(
            RevocationStatus.UNKNOWN, "ocsp", "OCSP response cannot be evaluated"
        )
    if not signature_ok:
        return RevocationResult(
            RevocationStatus.UNKNOWN, "ocsp", "responder signature not verified"
        )
    return RevocationResult(_ocsp_status_to_enum(single.certificate_status), "ocsp")


# ---------------------------------------------------------------------------
# CRL
# ---------------------------------------------------------------------------
def check_crl(
    der: bytes,
    cert: x509.Certificate,
    issuer: x509.Certificate,
    *,
    at_time: datetime | None = None,
) -> RevocationResult | None:
    """Evaluate a single DER CRL for *cert*; ``None`` if not applicable."""
    at_time = _time(at_time)
    try:
        crl = x509.load_der_x509_crl(der)
    except _NET_ERRORS:
        return None
    if crl.issuer != issuer.subject or not _issued_by(cert, issuer):
        return None
    if not _current(crl.last_update_utc, crl.next_update_utc, at_time):
        return RevocationResult(
            RevocationStatus.UNKNOWN, "crl", "CRL is stale or not yet valid"
        )
    try:
        usage = issuer.extensions.get_extension_for_oid(ExtensionOID.KEY_USAGE).value
        if not usage.crl_sign:
            return RevocationResult(
                RevocationStatus.UNKNOWN, "crl", "issuer key usage forbids CRL signing"
            )
    except x509.ExtensionNotFound:
        pass
    for extension in crl.extensions:
        if extension.oid in (
            ExtensionOID.DELTA_CRL_INDICATOR,
            ExtensionOID.ISSUING_DISTRIBUTION_POINT,
        ) or (
            extension.critical
            and extension.oid
            not in (ExtensionOID.AUTHORITY_KEY_IDENTIFIER, ExtensionOID.CRL_NUMBER)
        ):
            return RevocationResult(
                RevocationStatus.UNKNOWN,
                "crl",
                "unsupported CRL scope or critical extension",
            )
    for entry in crl:
        if any(
            extension.critical or extension.oid == ExtensionOID.CERTIFICATE_ISSUER
            for extension in entry.extensions
        ):
            return RevocationResult(
                RevocationStatus.UNKNOWN, "crl", "unsupported CRL entry extension"
            )
    try:
        if not crl.is_signature_valid(issuer.public_key()):
            return RevocationResult(
                RevocationStatus.UNKNOWN, "crl", "CRL signature not valid"
            )
    except Exception:
        return RevocationResult(RevocationStatus.UNKNOWN, "crl", "CRL not verifiable")
    if crl.get_revoked_certificate_by_serial_number(cert.serial_number) is not None:
        return RevocationResult(RevocationStatus.REVOKED, "crl")
    return RevocationResult(RevocationStatus.GOOD, "crl")


# ---------------------------------------------------------------------------
# URL extraction
# ---------------------------------------------------------------------------
def _ocsp_urls(cert: x509.Certificate) -> list[str]:
    urls: list[str] = []
    try:
        aia = cert.extensions.get_extension_for_oid(
            ExtensionOID.AUTHORITY_INFORMATION_ACCESS
        ).value
    except x509.ExtensionNotFound:
        return urls
    for desc in aia:
        if desc.access_method == AuthorityInformationAccessOID.OCSP:
            urls.append(desc.access_location.value)
    return urls


def _crl_urls(cert: x509.Certificate) -> list[str]:
    urls: list[str] = []
    try:
        cdp = cert.extensions.get_extension_for_oid(
            ExtensionOID.CRL_DISTRIBUTION_POINTS
        ).value
    except x509.ExtensionNotFound:
        return urls
    for dp in cdp:
        for name in dp.full_name or []:
            value = getattr(name, "value", None)
            if isinstance(value, str) and value.lower().startswith("http"):
                urls.append(value)
    return urls


# ---------------------------------------------------------------------------
# Network (opt-in; isolated for tests)
# ---------------------------------------------------------------------------
def _http_post(url: str, data: bytes, content_type: str, timeout: float) -> bytes:
    import urllib.request

    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": content_type}
    )
    with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310
        return resp.read()


def _http_get(url: str, timeout: float) -> bytes:
    import urllib.request

    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
        return resp.read()


def _fetch_ocsp(
    cert: x509.Certificate,
    issuer: x509.Certificate,
    timeout: float,
    at_time: datetime,
    embedded_crls: Sequence[bytes] = (),
) -> RevocationResult | None:
    urls = _ocsp_urls(cert)
    if not urls:
        return None
    builder = ocsp.OCSPRequestBuilder().add_certificate(cert, issuer, hashes.SHA1())
    req_der = builder.build().public_bytes(serialization.Encoding.DER)
    best = None
    for url in urls:
        try:
            body = _http_post(url, req_der, "application/ocsp-request", timeout)
        except _NET_ERRORS:
            continue
        result = check_ocsp_response(
            body, cert, issuer, at_time=at_time, embedded_crls=embedded_crls
        )
        if result is not None:
            best = result
            if result.status != RevocationStatus.UNKNOWN:
                return result
    return best


def _fetch_crl(
    cert: x509.Certificate, issuer: x509.Certificate, timeout: float, at_time: datetime
) -> RevocationResult | None:
    best = None
    for url in _crl_urls(cert):
        try:
            body = _http_get(url, timeout)
        except _NET_ERRORS:
            continue
        result = check_crl(body, cert, issuer, at_time=at_time)
        if result is not None:
            best = result
            if result.status != RevocationStatus.UNKNOWN:
                return result
    return best


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def check_revocation(
    cert: x509.Certificate,
    issuer: x509.Certificate,
    *,
    mode: ValidationMode = ValidationMode.OFFLINE,
    embedded_crls: Sequence[bytes] = (),
    embedded_ocsps: Sequence[bytes] = (),
    timeout: float = 10.0,
    at_time: datetime | None = None,
) -> RevocationResult:
    """Determine the revocation status of *cert* (issued by *issuer*).

    Embedded OCSP/CRL material is always consulted first.  Network lookups are
    attempted only when *mode* is ``ONLINE`` or ``AUTO`` and the embedded
    material is inconclusive.
    """
    at_time = _time(at_time)
    best = RevocationResult(RevocationStatus.UNKNOWN)

    for der in embedded_ocsps:
        result = check_ocsp_response(
            der, cert, issuer, at_time=at_time, embedded_crls=embedded_crls
        )
        if result is None:
            continue
        if result.status == RevocationStatus.REVOKED:
            return result
        if (
            result.status == RevocationStatus.GOOD
            or best.status == RevocationStatus.UNKNOWN
        ):
            best = result

    for der in embedded_crls:
        result = check_crl(der, cert, issuer, at_time=at_time)
        if result is None:
            continue
        if result.status == RevocationStatus.REVOKED:
            return result
        if (
            result.status == RevocationStatus.GOOD
            or best.status == RevocationStatus.UNKNOWN
        ):
            best = result

    if best.status == RevocationStatus.GOOD:
        return best

    if mode in (ValidationMode.ONLINE, ValidationMode.AUTO):
        for fetch in (_fetch_ocsp, _fetch_crl):
            extra = {"embedded_crls": embedded_crls} if fetch is _fetch_ocsp else {}
            online = fetch(cert, issuer, timeout, at_time, **extra)
            if online is not None:
                best = online
                if online.status != RevocationStatus.UNKNOWN:
                    return online

    return best


def check_chain_revocation(chain, *, at_time=None, **kwargs) -> RevocationResult:
    """Require applicable status evidence for every non-root certificate."""
    statuses = []
    for cert, issuer in pairwise(chain):
        result = check_revocation(cert, issuer, at_time=at_time, **kwargs)
        if result.status == RevocationStatus.REVOKED:
            return result
        statuses.append(result)
    if not statuses:
        return RevocationResult(
            RevocationStatus.UNKNOWN,
            detail="no issuer available for revocation checking",
        )
    return next(
        (r for r in statuses if r.status == RevocationStatus.UNKNOWN), statuses[0]
    )
