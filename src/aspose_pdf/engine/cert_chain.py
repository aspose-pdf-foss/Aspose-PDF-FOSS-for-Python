"""X.509 certificate-chain building and validation.

``cryptography.x509.verification`` exists but is geared to TLS (it requires a
server/client ``Subject`` and the corresponding extended-key-usage), so it
rejects ordinary document-signing certificates.  This module therefore builds
and validates the path manually using the low-level primitives:

* issuer-signature verification (``Certificate.verify_directly_issued_by``),
* validity-window checks at a caller-supplied time,
* BasicConstraints / KeyUsage sanity checks,
* anchoring against caller-supplied trust roots (and, optionally, the OS bundle).

Trust is intentionally *reported* rather than always *enforced*: the orchestrator
in :mod:`aspose_pdf.engine.signature_validator` decides whether a given
:class:`TrustStatus` should fail a signature, based on the validation options.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.x509.oid import ExtensionOID

from aspose_pdf.validation import TrustStatus

_MAX_DEPTH = 16


@dataclass
class ChainResult:
    """Result of building and validating a certificate path."""

    trust_status: TrustStatus
    chain: List[x509.Certificate] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _fingerprint(cert: x509.Certificate) -> bytes:
    return cert.fingerprint(hashes.SHA256())


def _is_self_signed(cert: x509.Certificate) -> bool:
    if cert.subject != cert.issuer:
        return False
    try:
        cert.verify_directly_issued_by(cert)
        return True
    except Exception:
        return False


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _check_validity(cert: x509.Certificate, at_time: datetime, label: str, errors):
    not_before = _aware(cert.not_valid_before_utc)
    not_after = _aware(cert.not_valid_after_utc)
    if at_time < not_before:
        errors.append(f"{label} certificate is not yet valid")
    elif at_time > not_after:
        errors.append(f"{label} certificate has expired")


def _basic_constraints_ca(cert: x509.Certificate) -> Optional[bool]:
    try:
        bc = cert.extensions.get_extension_for_oid(ExtensionOID.BASIC_CONSTRAINTS)
        return bool(bc.value.ca)
    except x509.ExtensionNotFound:
        return None


def _has_key_cert_sign(cert: x509.Certificate) -> Optional[bool]:
    try:
        ku = cert.extensions.get_extension_for_oid(ExtensionOID.KEY_USAGE)
        return bool(ku.value.key_cert_sign)
    except x509.ExtensionNotFound:
        return None


def _leaf_can_sign(cert: x509.Certificate) -> Optional[bool]:
    try:
        ku = cert.extensions.get_extension_for_oid(ExtensionOID.KEY_USAGE)
        return bool(ku.value.digital_signature or ku.value.content_commitment)
    except x509.ExtensionNotFound:
        return None


def load_system_trust_roots() -> List[x509.Certificate]:
    """Best-effort load of the operating-system CA bundle (no extra deps)."""
    import ssl

    roots: List[x509.Certificate] = []
    paths = ssl.get_default_verify_paths()
    for cafile in (paths.cafile, paths.openssl_cafile):
        if not cafile:
            continue
        try:
            with open(cafile, "rb") as handle:
                roots.extend(x509.load_pem_x509_certificates(handle.read()))
            break
        except (OSError, ValueError):
            continue
    return roots


def build_and_validate(
    leaf: x509.Certificate,
    extra_certs: Optional[List[x509.Certificate]] = None,
    trust_roots: Optional[List[x509.Certificate]] = None,
    *,
    at_time: Optional[datetime] = None,
    use_system_trust: bool = False,
) -> ChainResult:
    """Build the path from *leaf* toward a trust anchor and validate each link."""
    extra_certs = list(extra_certs or [])
    trust_roots = list(trust_roots or [])
    if use_system_trust:
        trust_roots = trust_roots + load_system_trust_roots()
    if at_time is None:
        at_time = datetime.now(timezone.utc)
    at_time = _aware(at_time)

    anchor_prints = {_fingerprint(c) for c in trust_roots}
    # Pool of certs we may use as issuers while building the path.
    pool = extra_certs + trust_roots

    errors: List[str] = []
    warnings: List[str] = []
    chain: List[x509.Certificate] = [leaf]

    # Leaf-level checks.
    _check_validity(leaf, at_time, "signer", errors)
    if _leaf_can_sign(leaf) is False:
        warnings.append("signer certificate key usage does not allow signing")

    current = leaf
    reached_self_signed = False
    for _ in range(_MAX_DEPTH):
        if _fingerprint(current) in anchor_prints and current is not leaf:
            break
        if _is_self_signed(current):
            reached_self_signed = True
            break
        # Find an issuer in the pool that actually signed `current`.
        issuer = None
        cur_print = _fingerprint(current)
        for cand in pool:
            if _fingerprint(cand) == cur_print:
                continue
            if cand.subject != current.issuer:
                continue
            try:
                current.verify_directly_issued_by(cand)
            except Exception:
                continue
            issuer = cand
            break
        if issuer is None:
            break
        # Validate the issuer as a CA.
        _check_validity(issuer, at_time, "issuer", errors)
        if _basic_constraints_ca(issuer) is False:
            errors.append("an issuer certificate is not a CA (BasicConstraints)")
        if _has_key_cert_sign(issuer) is False:
            errors.append("an issuer certificate lacks the keyCertSign key usage")
        chain.append(issuer)
        current = issuer

    # Determine the trust status.
    chain_prints = {_fingerprint(c) for c in chain}
    if anchor_prints & chain_prints:
        trust = TrustStatus.TRUSTED
    elif reached_self_signed and len(chain) == 1:
        trust = TrustStatus.SELF_SIGNED
    elif reached_self_signed:
        trust = TrustStatus.UNTRUSTED
    else:
        # Could not anchor the path to any self-signed root.
        trust = TrustStatus.BROKEN

    return ChainResult(
        trust_status=trust, chain=chain, errors=errors, warnings=warnings
    )
