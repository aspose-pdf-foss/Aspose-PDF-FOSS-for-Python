"""X.509 certificate-chain building and validation.

``cryptography.x509.verification`` exists but is geared to TLS (it requires a
server/client ``Subject`` and the corresponding extended-key-usage), so it
rejects ordinary document-signing certificates.  This module therefore builds
and validates the path manually using the low-level primitives:

* issuer-signature verification (``Certificate.verify_directly_issued_by``),
* validity-window checks at a caller-supplied time,
* BasicConstraints, path length, key purposes, name constraints and critical
  extension checks (unsupported policy constraints are rejected),
* anchoring against caller-supplied trust roots (and, optionally, the OS bundle).

Trust is intentionally *reported* rather than always *enforced*: the orchestrator
in :mod:`aspose_pdf.engine.signature_validator` decides whether a given
:class:`TrustStatus` should fail a signature, based on the validation options.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.x509.oid import ExtendedKeyUsageOID, ExtensionOID, NameOID

from aspose_pdf.validation import TrustStatus

_MAX_DEPTH = 16
_MAX_PATHS = 256
DOCUMENT_SIGNING = x509.ObjectIdentifier("1.3.6.1.5.5.7.3.36")

# These extensions are either processed below or carry descriptive data.
_HANDLED_CRITICAL = {
    ExtensionOID.BASIC_CONSTRAINTS,
    ExtensionOID.KEY_USAGE,
    ExtensionOID.EXTENDED_KEY_USAGE,
    ExtensionOID.NAME_CONSTRAINTS,
    ExtensionOID.SUBJECT_ALTERNATIVE_NAME,
    ExtensionOID.SUBJECT_KEY_IDENTIFIER,
    ExtensionOID.AUTHORITY_KEY_IDENTIFIER,
    ExtensionOID.OCSP_NO_CHECK,
}
_UNSUPPORTED_CONSTRAINTS = {
    ExtensionOID.POLICY_CONSTRAINTS,
    ExtensionOID.POLICY_MAPPINGS,
    ExtensionOID.INHIBIT_ANY_POLICY,
}


@dataclass
class ChainResult:
    """Result of building and validating a certificate path."""

    trust_status: TrustStatus
    chain: list[x509.Certificate] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


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
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _check_validity(cert: x509.Certificate, at_time: datetime, label: str, errors):
    not_before = _aware(cert.not_valid_before_utc)
    not_after = _aware(cert.not_valid_after_utc)
    if at_time < not_before:
        errors.append(f"{label} certificate is not yet valid")
    elif at_time > not_after:
        errors.append(f"{label} certificate has expired")


def _has_key_cert_sign(cert: x509.Certificate) -> bool | None:
    try:
        ku = cert.extensions.get_extension_for_oid(ExtensionOID.KEY_USAGE)
        return bool(ku.value.key_cert_sign)
    except x509.ExtensionNotFound:
        return None


def _leaf_can_sign(cert: x509.Certificate) -> bool | None:
    try:
        ku = cert.extensions.get_extension_for_oid(ExtensionOID.KEY_USAGE)
        return bool(ku.value.digital_signature or ku.value.content_commitment)
    except x509.ExtensionNotFound:
        return None


def _extension(cert, oid):
    try:
        return cert.extensions.get_extension_for_oid(oid).value
    except x509.ExtensionNotFound:
        return None


def _domain_matches(name: str, constraint: str, *, subdomains: bool) -> bool:
    name, constraint = name.lower().rstrip("."), constraint.lower().rstrip(".")
    if constraint.startswith("."):
        return name.endswith(constraint) and name != constraint[1:]
    return name == constraint or (subdomains and name.endswith("." + constraint))


def _name_matches(name, constraint) -> bool:
    if isinstance(name, x509.DNSName):
        return _domain_matches(name.value, constraint.value, subdomains=True)
    if isinstance(name, x509.RFC822Name):
        local, domain = name.value.rsplit("@", 1)
        if "@" in constraint.value:
            other_local, other_domain = constraint.value.rsplit("@", 1)
            return local == other_local and domain.lower() == other_domain.lower()
        return _domain_matches(domain, constraint.value, subdomains=False)
    if isinstance(name, x509.UniformResourceIdentifier):
        host = urlsplit(name.value).hostname
        return host is not None and _domain_matches(
            host, constraint.value, subdomains=False
        )
    if isinstance(name, x509.IPAddress):
        return name.value in constraint.value
    if isinstance(name, x509.DirectoryName):
        prefix = constraint.value.rdns
        return name.value.rdns[: len(prefix)] == prefix
    raise ValueError("unsupported name constraint type")


def _check_names(issuer, subordinate, errors):
    constraints = _extension(issuer, ExtensionOID.NAME_CONSTRAINTS)
    if constraints is None:
        return
    permitted = list(constraints.permitted_subtrees or ())
    excluded = list(constraints.excluded_subtrees or ())
    supported = (
        x509.DNSName,
        x509.RFC822Name,
        x509.UniformResourceIdentifier,
        x509.IPAddress,
        x509.DirectoryName,
    )
    if any(not isinstance(n, supported) for n in permitted + excluded):
        errors.append("unsupported name constraints in certificate chain")
        return
    names = [x509.DirectoryName(subordinate.subject)]
    names.extend(_extension(subordinate, ExtensionOID.SUBJECT_ALTERNATIVE_NAME) or ())
    names.extend(
        x509.RFC822Name(attr.value)
        for attr in subordinate.subject.get_attributes_for_oid(NameOID.EMAIL_ADDRESS)
    )
    for name in names:
        allowed = [n for n in permitted if type(n) is type(name)]
        denied = [n for n in excluded if type(n) is type(name)]
        try:
            if any(_name_matches(name, n) for n in denied) or (
                allowed and not any(_name_matches(name, n) for n in allowed)
            ):
                errors.append("certificate violates issuer name constraints")
        except (ValueError, TypeError, AttributeError):
            errors.append("certificate name constraints could not be evaluated")


def _path_errors(chain, at_time, purpose):
    errors = []
    if _leaf_can_sign(chain[0]) is False:
        errors.append("signer certificate key usage does not allow signing")
    for index, cert in enumerate(chain):
        label = "signer" if index == 0 else "issuer"
        _check_validity(cert, at_time, label, errors)
        for extension in cert.extensions:
            if extension.oid in _UNSUPPORTED_CONSTRAINTS or (
                extension.critical and extension.oid not in _HANDLED_CRITICAL
            ):
                errors.append(
                    f"unsupported certificate extension {extension.oid.dotted_string}"
                )
        eku = _extension(cert, ExtensionOID.EXTENDED_KEY_USAGE)
        if (
            eku is not None
            and purpose not in eku
            and ExtendedKeyUsageOID.ANY_EXTENDED_KEY_USAGE not in eku
        ):
            errors.append(
                "certificate extended key usage does not allow the requested purpose"
            )
        if index == 0:
            continue
        bc = _extension(cert, ExtensionOID.BASIC_CONSTRAINTS)
        if bc is None or not bc.ca:
            errors.append("an issuer certificate is not a CA (BasicConstraints)")
        elif bc.path_length is not None:
            intermediates = sum(c.subject != c.issuer for c in chain[1:index])
            if intermediates > bc.path_length:
                errors.append("certificate chain exceeds a CA path length constraint")
        if _has_key_cert_sign(cert) is False:
            errors.append("an issuer certificate lacks the keyCertSign key usage")
        for child_index, subordinate in enumerate(chain[:index]):
            if child_index == 0 or subordinate.subject != subordinate.issuer:
                _check_names(cert, subordinate, errors)
    return list(dict.fromkeys(errors))


def load_system_trust_roots() -> list[x509.Certificate]:
    """Best-effort load of the operating-system CA bundle (no extra deps)."""
    import ssl

    roots: list[x509.Certificate] = []
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
    extra_certs: list[x509.Certificate] | None = None,
    trust_roots: list[x509.Certificate] | None = None,
    *,
    at_time: datetime | None = None,
    use_system_trust: bool = False,
    purpose: x509.ObjectIdentifier = DOCUMENT_SIGNING,
) -> ChainResult:
    """Build the path from *leaf* toward a trust anchor and validate each link."""
    extra_certs = list(extra_certs or [])
    trust_roots = list(trust_roots or [])
    if use_system_trust:
        trust_roots = trust_roots + load_system_trust_roots()
    if at_time is None:
        at_time = datetime.now(UTC)
    at_time = _aware(at_time)

    anchor_prints = {_fingerprint(c) for c in trust_roots}
    pool = list({_fingerprint(c): c for c in extra_certs + trust_roots}.values())
    pending = [[leaf]]
    results = []
    attempts = 0
    while pending and attempts < _MAX_PATHS:
        chain = pending.pop()
        attempts += 1
        current = chain[-1]
        seen = {_fingerprint(c) for c in chain}
        if _fingerprint(current) in anchor_prints:
            trust = TrustStatus.TRUSTED
        elif _is_self_signed(current):
            trust = (
                TrustStatus.SELF_SIGNED if len(chain) == 1 else TrustStatus.UNTRUSTED
            )
        else:
            issuers = []
            for candidate in pool:
                if (
                    candidate.subject != current.issuer
                    or _fingerprint(candidate) in seen
                ):
                    continue
                try:
                    current.verify_directly_issued_by(candidate)
                except Exception:
                    continue
                issuers.append(candidate)
            if issuers and len(chain) < _MAX_DEPTH:
                pending.extend([*chain, candidate] for candidate in reversed(issuers))
                continue
            trust = TrustStatus.BROKEN
        errors = _path_errors(chain, at_time, purpose)
        if trust == TrustStatus.BROKEN:
            errors.append(
                "incomplete or cyclic certificate chain, or path depth exceeded"
            )
        result = ChainResult(trust, chain, errors)
        if trust == TrustStatus.TRUSTED and not errors:
            return result
        results.append(result)
    if not results:
        return ChainResult(
            TrustStatus.BROKEN, [leaf], ["certificate path search limit exceeded"]
        )
    # Prefer a valid complete path, then an anchored path with useful errors.
    return min(
        results,
        key=lambda r: (
            bool(r.errors),
            r.trust_status != TrustStatus.TRUSTED,
            r.trust_status == TrustStatus.BROKEN,
        ),
    )
