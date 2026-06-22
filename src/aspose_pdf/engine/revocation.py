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

from dataclasses import dataclass
from typing import List, Optional, Sequence

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed448, ed25519, padding, rsa
from cryptography.x509 import ocsp
from cryptography.x509.oid import AuthorityInformationAccessOID, ExtensionOID

from aspose_pdf.validation import RevocationStatus, ValidationMode

_NET_ERRORS = (OSError, ValueError)


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
    except (InvalidSignature, ValueError, TypeError):
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
    response: ocsp.OCSPResponse, issuer: x509.Certificate
) -> bool:
    """Verify the OCSP response was signed by the issuer or a delegated responder."""
    candidates = []
    try:
        candidates.extend(list(response.certificates))
    except Exception:
        pass
    candidates.append(issuer)
    for cand in candidates:
        if _pubkey_verify(
            cand.public_key(),
            response.signature,
            response.tbs_response_bytes,
            response.signature_hash_algorithm,
        ):
            # A delegated responder must itself be issued by the CA.
            if cand is issuer:
                return True
            try:
                cand.verify_directly_issued_by(issuer)
                return True
            except Exception:
                continue
    return False


def check_ocsp_response(
    der: bytes, cert: x509.Certificate, issuer: x509.Certificate
) -> Optional[RevocationResult]:
    """Evaluate a single DER OCSP response for *cert*; ``None`` if not applicable."""
    try:
        response = ocsp.load_der_ocsp_response(der)
    except _NET_ERRORS:
        return None
    if response.response_status != ocsp.OCSPResponseStatus.SUCCESSFUL:
        return None
    try:
        if response.serial_number != cert.serial_number:
            return None
    except Exception:
        return None
    if not _verify_ocsp_signature(response, issuer):
        return RevocationResult(
            RevocationStatus.UNKNOWN, "ocsp", "responder signature not verified"
        )
    return RevocationResult(
        _ocsp_status_to_enum(response.certificate_status), "ocsp"
    )


# ---------------------------------------------------------------------------
# CRL
# ---------------------------------------------------------------------------
def check_crl(
    der: bytes, cert: x509.Certificate, issuer: x509.Certificate
) -> Optional[RevocationResult]:
    """Evaluate a single DER CRL for *cert*; ``None`` if not applicable."""
    try:
        crl = x509.load_der_x509_crl(der)
    except _NET_ERRORS:
        return None
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
def _ocsp_urls(cert: x509.Certificate) -> List[str]:
    urls: List[str] = []
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


def _crl_urls(cert: x509.Certificate) -> List[str]:
    urls: List[str] = []
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
    cert: x509.Certificate, issuer: x509.Certificate, timeout: float
) -> Optional[RevocationResult]:
    urls = _ocsp_urls(cert)
    if not urls:
        return None
    builder = ocsp.OCSPRequestBuilder().add_certificate(cert, issuer, hashes.SHA1())
    req_der = builder.build().public_bytes(serialization.Encoding.DER)
    for url in urls:
        try:
            body = _http_post(url, req_der, "application/ocsp-request", timeout)
        except _NET_ERRORS:
            continue
        result = check_ocsp_response(body, cert, issuer)
        if result is not None:
            return result
    return None


def _fetch_crl(
    cert: x509.Certificate, issuer: x509.Certificate, timeout: float
) -> Optional[RevocationResult]:
    for url in _crl_urls(cert):
        try:
            body = _http_get(url, timeout)
        except _NET_ERRORS:
            continue
        result = check_crl(body, cert, issuer)
        if result is not None:
            return result
    return None


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
) -> RevocationResult:
    """Determine the revocation status of *cert* (issued by *issuer*).

    Embedded OCSP/CRL material is always consulted first.  Network lookups are
    attempted only when *mode* is ``ONLINE`` or ``AUTO`` and the embedded
    material is inconclusive.
    """
    best = RevocationResult(RevocationStatus.UNKNOWN)

    for der in embedded_ocsps:
        result = check_ocsp_response(der, cert, issuer)
        if result is None:
            continue
        if result.status == RevocationStatus.REVOKED:
            return result
        if result.status == RevocationStatus.GOOD:
            best = result

    for der in embedded_crls:
        result = check_crl(der, cert, issuer)
        if result is None:
            continue
        if result.status == RevocationStatus.REVOKED:
            return result
        if result.status == RevocationStatus.GOOD:
            best = result

    if best.status == RevocationStatus.GOOD:
        return best

    if mode in (ValidationMode.ONLINE, ValidationMode.AUTO):
        online = _fetch_ocsp(cert, issuer, timeout) or _fetch_crl(
            cert, issuer, timeout
        )
        if online is not None:
            return online

    return best
