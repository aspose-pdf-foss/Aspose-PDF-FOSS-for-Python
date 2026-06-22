"""Full CMS signature validation orchestration.

Ties together the low-level engine modules — :mod:`cms` (signer verification),
:mod:`cert_chain` (X.509 path/trust), :mod:`revocation` (OCSP/CRL) and
:mod:`timestamp` (RFC 3161) — and a DocMDP certification check into a single
:class:`~aspose_pdf.validation.ValidationResult`.

A signature is reported ``VALID`` when it is cryptographically intact **and**
trust is acceptable (or trust is not being enforced) **and** the certificate is
not revoked **and** any DocMDP certification it carries has not been violated.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from cryptography import x509

from aspose_pdf.validation import (
    CertificationLevel,
    PadesLevel,
    RevocationStatus,
    TrustStatus,
    ValidationMethod,
    ValidationOptions,
    ValidationResult,
    ValidationStatus,
)

_CADES_SUBFILTERS = {"etsi.cades.detached", "etsi.cades"}


def _to_cert(obj) -> Optional[x509.Certificate]:
    if isinstance(obj, x509.Certificate):
        return obj
    if isinstance(obj, (bytes, bytearray)):
        data = bytes(obj)
        for loader in (x509.load_der_x509_certificate, x509.load_pem_x509_certificate):
            try:
                return loader(data)
            except (ValueError, TypeError):
                continue
    return None


def _normalise_trust_roots(options: ValidationOptions) -> List[x509.Certificate]:
    roots: List[x509.Certificate] = []
    for item in options.trusted_certificates or []:
        cert = _to_cert(item)
        if cert is not None:
            roots.append(cert)
    return roots


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if isinstance(dt, datetime) else None


def _load_der_certs(ders) -> List[x509.Certificate]:
    out: List[x509.Certificate] = []
    for der in ders or ():
        cert = _to_cert(der)
        if cert is not None:
            out.append(cert)
    return out


def _pades_level(
    *,
    is_cades: bool,
    timestamp_verified: bool,
    has_dss: bool,
    has_document_timestamp: bool,
) -> PadesLevel:
    """Map the present validation artefacts onto a PAdES baseline level.

    The levels are cumulative (ETSI EN 319 142): a document timestamp also
    satisfies the timestamp requirement of the -T level because it timestamps
    the whole signed document.
    """
    if not is_cades:
        return PadesLevel.NONE
    has_ts = timestamp_verified or has_document_timestamp
    if not has_ts:
        return PadesLevel.B
    if not has_dss:
        return PadesLevel.T
    if not has_document_timestamp:
        return PadesLevel.LT
    return PadesLevel.LTA


def validate_cms(
    contents: bytes,
    signed_bytes: bytes,
    options: ValidationOptions,
    *,
    docmdp_level: Optional[int] = None,
    reference_data: Optional[bytes] = None,
    signed_end: Optional[int] = None,
    sub_filter: Optional[str] = None,
    dss_certs=(),
    dss_crls=(),
    dss_ocsps=(),
    has_document_timestamp: bool = False,
) -> ValidationResult:
    """Perform full CMS validation of a detached PDF signature.

    *signed_bytes* is the concatenation of the ByteRange-covered slices.

    The ``dss_*`` arguments carry long-term validation material harvested from
    the document security store (``/DSS``); it is merged with any material
    embedded in the CMS for chain building and revocation.  ``sub_filter`` and
    ``has_document_timestamp`` feed PAdES baseline-level detection.
    """
    from aspose_pdf.engine import cert_chain, cms, revocation, timestamp

    errors: List[str] = []
    notes: List[str] = []

    # --- Parse + signer signature -----------------------------------------
    try:
        info = cms.parse_signed_data(contents)
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        return ValidationResult(
            status=ValidationStatus.INVALID,
            message=f"Invalid PKCS#7 structure: {exc}",
            errors=[f"PKCS#7 parse error: {exc}"],
        )

    signer = cms.verify_signer(info, signed_bytes)
    signer_name = (
        info.signer_cert.subject.rfc4514_string()
        if info.signer_cert is not None
        else None
    )
    if not signer.signature_ok:
        return ValidationResult(
            status=ValidationStatus.INVALID,
            message=signer.reason or "signature verification failed",
            errors=[signer.reason or "signature verification failed"],
            signer=signer_name,
        )

    # ESS signing-certificate binding (CAdES/PAdES).  Absent on bare PKCS#7.
    ess_ok = cms.verify_signing_certificate(info)
    if ess_ok is False:
        errors.append(
            "signing-certificate attribute does not match the signer certificate"
        )

    # --- Timestamp (also fixes the time used for chain validation) --------
    ts_info = None
    at_time = info.signing_time
    if options.check_timestamp and info.timestamp_token_der:
        ts_info = timestamp.verify_timestamp_token(
            info.timestamp_token_der, info.signature
        )
        if ts_info.verified and ts_info.gen_time is not None:
            at_time = ts_info.gen_time
        else:
            notes.append(f"timestamp not verified ({ts_info.reason})")
    if at_time is None:
        at_time = datetime.now(timezone.utc)

    # --- Certificate chain / trust ----------------------------------------
    # Intermediates may live in the CMS or only in the document security store;
    # offer both pools to the path builder.
    dss_cert_objs = _load_der_certs(dss_certs)
    chain_extra = list(info.certificates) + dss_cert_objs
    trust_roots = _normalise_trust_roots(options)
    enforce_trust = bool(trust_roots) or options.use_system_trust
    chain = cert_chain.build_and_validate(
        info.signer_cert,
        extra_certs=chain_extra,
        trust_roots=trust_roots,
        at_time=at_time,
        use_system_trust=options.use_system_trust,
    )
    errors.extend(chain.errors)
    notes.extend(chain.warnings)

    trust = chain.trust_status
    if trust == TrustStatus.SELF_SIGNED and not options.allow_self_signed:
        errors.append("signer certificate is self-signed and not trusted")
    elif trust == TrustStatus.UNTRUSTED and enforce_trust:
        errors.append("certificate chain does not terminate at a trusted anchor")
    elif trust == TrustStatus.BROKEN and enforce_trust:
        errors.append("could not build a certificate chain to a trusted anchor")

    # --- Revocation -------------------------------------------------------
    revocation_status = RevocationStatus.NOT_CHECKED
    check_rev = options.check_revocation or (
        options.validation_method == ValidationMethod.LTIP
    )
    if check_rev and info.signer_cert is not None:
        issuer = chain.chain[1] if len(chain.chain) > 1 else info.signer_cert
        rev = revocation.check_revocation(
            info.signer_cert,
            issuer,
            mode=options.validation_mode,
            embedded_crls=list(info.crls_der) + list(dss_crls),
            embedded_ocsps=list(info.ocsps_der) + list(dss_ocsps),
            timeout=options.network_timeout,
        )
        revocation_status = rev.status
        if rev.status == RevocationStatus.REVOKED:
            errors.append("signer certificate has been revoked")
        elif rev.status == RevocationStatus.UNKNOWN:
            notes.append("revocation status could not be determined")

    # --- DocMDP certification ---------------------------------------------
    certification_level = CertificationLevel.NOT_CERTIFIED
    if docmdp_level in (1, 2, 3):
        certification_level = CertificationLevel(docmdp_level)
        if (
            certification_level == CertificationLevel.NO_CHANGES
            and reference_data is not None
            and signed_end is not None
        ):
            from aspose_pdf.security import _has_meaningful_unsigned_tail

            if _has_meaningful_unsigned_tail(bytes(reference_data), int(signed_end)):
                errors.append(
                    "certification level 1 (no changes) violated by changes "
                    "after the certified revision"
                )

    # --- PAdES baseline level --------------------------------------------
    is_cades = bool(info.ess_cert_ids) or (
        (sub_filter or "").lower() in _CADES_SUBFILTERS
    )
    pades_level = _pades_level(
        is_cades=is_cades,
        timestamp_verified=bool(ts_info and ts_info.verified),
        has_dss=bool(dss_certs or dss_crls or dss_ocsps),
        has_document_timestamp=has_document_timestamp,
    )

    # --- Assemble ---------------------------------------------------------
    if errors:
        status = ValidationStatus.INVALID
        message = "; ".join(errors)
    else:
        status = ValidationStatus.VALID
        message = "Signature verified successfully"
        if pades_level is not PadesLevel.NONE:
            message = f"PAdES-{pades_level.value} signature verified successfully"
        if notes:
            message += " (" + "; ".join(notes) + ")"

    return ValidationResult(
        status=status,
        message=message,
        errors=errors,
        signer=signer_name,
        trust_status=trust,
        revocation_status=revocation_status,
        timestamp=ts_info,
        certification_level=certification_level,
        signed_at=_iso(info.signing_time),
        pades_level=pades_level,
    )
