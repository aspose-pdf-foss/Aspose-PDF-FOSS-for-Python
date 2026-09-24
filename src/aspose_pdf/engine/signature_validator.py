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

from datetime import UTC, datetime

from cryptography import x509

from aspose_pdf.validation import (
    CertificationLevel,
    PadesLevel,
    RevocationStatus,
    TrustStatus,
    ValidationMethod,
    ValidationMode,
    ValidationOptions,
    ValidationResult,
    ValidationStatus,
)

_CADES_SUBFILTERS = {"etsi.cades.detached"}


def _to_cert(obj) -> x509.Certificate | None:
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


def _normalise_trust_roots(options: ValidationOptions) -> list[x509.Certificate]:
    roots: list[x509.Certificate] = []
    for item in options.trusted_certificates or []:
        cert = _to_cert(item)
        if cert is None:
            raise ValueError("trusted_certificates contains an invalid certificate")
        roots.append(cert)
    return roots


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if isinstance(dt, datetime) else None


def _load_der_certs(ders) -> list[x509.Certificate]:
    out: list[x509.Certificate] = []
    for der in ders or ():
        cert = _to_cert(der)
        if cert is not None:
            out.append(cert)
    return out


def _pades_level(
    *,
    is_cades: bool,
    timestamp_verified: bool,
    long_term_verified: bool,
    archive_verified: bool,
) -> PadesLevel:
    """Report only levels established by verified, applicable evidence."""
    if not is_cades:
        return PadesLevel.NONE
    if not timestamp_verified:
        return PadesLevel.B
    if not long_term_verified:
        return PadesLevel.T
    if not archive_verified:
        return PadesLevel.LT
    return PadesLevel.LTA


def validate_cms(
    contents: bytes,
    signed_bytes: bytes,
    options: ValidationOptions,
    *,
    docmdp_level: int | None = None,
    certification_errors=(),
    sub_filter: str | None = None,
    dss_certs=(),
    dss_crls=(),
    dss_ocsps=(),
    document_timestamp=None,
    archive_verified: bool = False,
) -> ValidationResult:
    """Perform full CMS validation of a detached PDF signature.

    *signed_bytes* is the concatenation of the ByteRange-covered slices.

    The ``dss_*`` arguments carry long-term validation material harvested from
    the document security store (``/DSS``); it is merged with any material
    embedded in the CMS for chain building and revocation. Document timestamp
    evidence must already have been checked against its signed PDF revision.
    """
    from aspose_pdf.engine import cert_chain, cms, revocation, timestamp

    errors: list[str] = list(certification_errors)
    notes: list[str] = []
    inconclusive: list[str] = []

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

    # --- Timestamp (only trusted time can replace the current time) --------
    ts_info = None
    at_time = datetime.now(UTC)
    dss_cert_objs = _load_der_certs(dss_certs)
    chain_extra = list(info.certificates) + dss_cert_objs
    trust_roots = _normalise_trust_roots(options)
    check_rev = (
        options.check_revocation or options.validation_method == ValidationMethod.LTIP
    )
    embedded_crls = list(info.crls_der) + list(dss_crls)
    embedded_ocsps = list(info.ocsps_der) + list(dss_ocsps)
    if options.check_timestamp and info.timestamp_token_der:
        ts_info = timestamp.validate_timestamp_token(
            info.timestamp_token_der,
            info.signature,
            trust_roots=trust_roots,
            extra_certs=chain_extra,
            use_system_trust=options.use_system_trust,
            check_revocation=check_rev,
            embedded_crls=embedded_crls,
            embedded_ocsps=embedded_ocsps,
            validation_mode=options.validation_mode,
            timeout=options.network_timeout,
        )
        if ts_info.verified and ts_info.gen_time is not None:
            at_time = ts_info.gen_time
        else:
            target = (
                inconclusive
                if ts_info.validation_status == ValidationStatus.UNKNOWN
                else errors
            )
            target.append(f"timestamp not verified ({ts_info.reason})")
    elif options.check_timestamp and document_timestamp is not None:
        ts_info = document_timestamp
        if ts_info.verified and ts_info.gen_time is not None:
            at_time = ts_info.gen_time

    # --- Certificate chain / trust ----------------------------------------
    # Intermediates may live in the CMS or only in the document security store;
    # offer both pools to the path builder.
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
    if enforce_trust and trust != TrustStatus.TRUSTED:
        errors.append("certificate chain does not terminate at a trusted anchor")
    elif trust == TrustStatus.UNTRUSTED:
        errors.append("certificate chain has no configured trust anchor")
    elif trust == TrustStatus.SELF_SIGNED and not options.allow_self_signed:
        errors.append("signer certificate is self-signed and not trusted")

    # --- Revocation -------------------------------------------------------
    revocation_status = RevocationStatus.NOT_CHECKED
    if check_rev and info.signer_cert is not None:
        rev = revocation.check_chain_revocation(
            chain.chain,
            at_time=at_time,
            mode=options.validation_mode,
            embedded_crls=embedded_crls,
            embedded_ocsps=embedded_ocsps,
            timeout=options.network_timeout,
        )
        revocation_status = rev.status
        if rev.status == RevocationStatus.REVOKED:
            errors.append("signer certificate chain has been revoked")
        elif rev.status == RevocationStatus.UNKNOWN:
            inconclusive.append("revocation status could not be determined")

    # --- DocMDP certification ---------------------------------------------
    certification_level = CertificationLevel.NOT_CERTIFIED
    if docmdp_level in (1, 2, 3):
        certification_level = CertificationLevel(docmdp_level)

    # --- PAdES baseline level --------------------------------------------
    is_cades = ess_ok is True and (
        (sub_filter or "").lower() in _CADES_SUBFILTERS
    )
    long_term_verified = False
    if is_cades and ts_info and ts_info.verified and not errors and not inconclusive:
        # Online success cannot establish that the PDF is self-contained.
        # Independently check embedded evidence even if revocation was optional.
        long_term_verified = embedded_long_term_valid(
            contents, options, dss_certs, dss_crls, dss_ocsps,
            document_timestamp=document_timestamp,
        )
    pades_level = _pades_level(
        is_cades=is_cades,
        timestamp_verified=bool(ts_info and ts_info.verified),
        long_term_verified=long_term_verified,
        archive_verified=archive_verified,
    )

    # --- Assemble ---------------------------------------------------------
    if errors:
        status = ValidationStatus.INVALID
        message = "; ".join(errors)
    elif inconclusive:
        status = ValidationStatus.UNKNOWN
        message = "; ".join(inconclusive)
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
        errors=errors + inconclusive,
        signer=signer_name,
        trust_status=trust,
        revocation_status=revocation_status,
        timestamp=ts_info,
        certification_level=certification_level,
        signed_at=_iso(info.signing_time),
        pades_level=pades_level,
        trusted_at=_iso(ts_info.gen_time) if ts_info and ts_info.verified else None,
        validated_at=_iso(at_time),
    )


def embedded_long_term_valid(contents, options, certs, crls, ocsps, *, document_timestamp=None):
    """Check signer and timestamp paths using only embedded revocation data."""
    from aspose_pdf.engine import cert_chain, cms, revocation, timestamp

    if not options.check_timestamp or not (crls or ocsps):
        return False
    info = cms.parse_signed_data(contents)
    roots = _normalise_trust_roots(options)
    extra = list(info.certificates) + _load_der_certs(certs)
    ts = document_timestamp
    if info.timestamp_token_der:
        ts = timestamp.validate_timestamp_token(
            info.timestamp_token_der, info.signature,
            trust_roots=roots, extra_certs=extra,
            use_system_trust=options.use_system_trust, check_revocation=True,
            embedded_crls=list(crls) + list(info.crls_der),
            embedded_ocsps=list(ocsps) + list(info.ocsps_der),
            validation_mode=ValidationMode.OFFLINE,
        )
    if not ts or not ts.verified or ts.revocation_status != RevocationStatus.GOOD:
        return False
    chain = cert_chain.build_and_validate(
        info.signer_cert, extra_certs=extra, trust_roots=roots,
        at_time=ts.gen_time, use_system_trust=options.use_system_trust,
    )
    if chain.trust_status != TrustStatus.TRUSTED or chain.errors:
        return False
    rev = revocation.check_chain_revocation(
        chain.chain, at_time=ts.gen_time, mode=ValidationMode.OFFLINE,
        embedded_crls=list(crls) + list(info.crls_der),
        embedded_ocsps=list(ocsps) + list(info.ocsps_der),
    )
    return rev.status == RevocationStatus.GOOD
