"""Signature validation options and results for PDF digital signatures.

This module provides:

- :class:`ValidationMode` – controls *how* certificates are checked (offline
  cryptographic verification vs. online OCSP/CRL lookup).
- :class:`ValidationMethod` – selects the signature format to validate
  (PKCS#7 or Long-Term Information Profile).
- :class:`ValidationStatus` – the outcome of a validation run.
- :class:`ValidationOptions` – bundles mode + method into a single config
  object passed to :meth:`~aspose_pdf.signature.PdfSignature.validate`.
- :class:`ValidationResult` – the structured result returned by
  :meth:`~aspose_pdf.signature.PdfSignature.validate`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional


class ValidationMode(Enum):
    """Controls whether certificate revocation is checked via network."""

    OFFLINE = "offline"
    """Perform only cryptographic verification (ByteRange + PKCS#7 digest).
    No network requests are made.  This is the default and always available.
    """

    ONLINE = "online"
    """Attempt OCSP/CRL revocation check in addition to the cryptographic
    verification.  Falls back to offline result if the network is unavailable.
    Not fully implemented – the class records the intent but the current
    engine only performs offline checks regardless.
    """

    AUTO = "auto"
    """Try online revocation check first; fall back to offline on any error."""


class ValidationMethod(Enum):
    """Selects the signature format / validation algorithm."""

    PKCS7 = "pkcs7"
    """Validate a standard PKCS#7 / CMS detached or attached signature."""

    LTIP = "ltip"
    """Long-Term Information Profile – validate embedded validation info (not
    yet fully implemented; falls back to PKCS#7 validation).
    """


class ValidationStatus(Enum):
    """Outcome of a :class:`ValidationResult`."""

    VALID = "valid"
    """The signature is cryptographically intact."""

    INVALID = "invalid"
    """The signature failed one or more validation checks."""

    UNKNOWN = "unknown"
    """Validation could not be completed (e.g. missing data)."""


class TrustStatus(Enum):
    """Outcome of building/validating the signer's certificate chain."""

    TRUSTED = "trusted"
    """A path was built to a configured (or system) trust anchor."""

    SELF_SIGNED = "self_signed"
    """The signer certificate is self-signed and no external anchor applies."""

    UNTRUSTED = "untrusted"
    """A complete chain was built but it does not terminate at a trust anchor."""

    BROKEN = "broken"
    """The chain is invalid (bad issuer signature, expired, or incomplete)."""


class RevocationStatus(Enum):
    """Certificate revocation outcome (OCSP/CRL)."""

    GOOD = "good"
    """The certificate was confirmed not revoked."""

    REVOKED = "revoked"
    """The certificate was found revoked."""

    UNKNOWN = "unknown"
    """Revocation could not be determined (no responder data / network)."""

    NOT_CHECKED = "not_checked"
    """Revocation checking was not requested."""


class PadesLevel(Enum):
    """PAdES baseline conformance level reached by a signature.

    Mirrors the ETSI EN 319 142 baseline levels (named *B-B*, *B-T*, *B-LT*,
    *B-LTA* in the standard).  Each level builds on the previous one:

    * :attr:`B`   – CAdES-BES baseline: the signed attributes carry an ESS
      ``signing-certificate-v2`` binding and the ``ETSI.CAdES.detached``
      ``/SubFilter``.
    * :attr:`T`   – B plus a verified RFC 3161 signature timestamp.
    * :attr:`LT`  – T plus long-term validation material (certificates, CRLs
      and OCSP responses) available in the document security store (``/DSS``).
    * :attr:`LTA` – LT plus a document timestamp (``ETSI.RFC3161``) that allows
      the validation material to be renewed before the algorithms weaken.
    """

    NONE = "none"
    """Not a PAdES/CAdES signature (a bare PKCS#7 approval signature)."""

    B = "B"
    T = "T"
    LT = "LT"
    LTA = "LTA"

    @property
    def baseline_name(self) -> str:
        """Return the ETSI baseline label (e.g. ``"B-LT"``)."""
        return "none" if self is PadesLevel.NONE else f"B-{self.value}"


class CertificationLevel(Enum):
    """DocMDP certification level of a signature.

    Mirrors the PDF ``/DocMDP`` transform-parameter ``/P`` values.
    """

    NOT_CERTIFIED = 0
    """An ordinary approval signature (no ``/DocMDP``)."""

    NO_CHANGES = 1
    """``P=1`` – no changes are permitted after signing."""

    FORM_FILLING = 2
    """``P=2`` – form filling and digital signatures are permitted."""

    FORM_FILLING_AND_ANNOTATIONS = 3
    """``P=3`` – also permits annotation creation/editing."""


@dataclass
class ValidationOptions:
    """Configuration for signature validation.

    Pass an instance of this class to
    :meth:`~aspose_pdf.signature.PdfSignature.validate` to customise the
    validation behaviour.

    Attributes
    ----------
    validation_mode:
        Determines whether certificate revocation is checked over the network.
        Defaults to :attr:`ValidationMode.OFFLINE`.
    validation_method:
        Selects the signature format to validate.
        Defaults to :attr:`ValidationMethod.PKCS7`.
    trusted_certificates:
        Trust anchors used for chain validation.  Each item may be a
        :class:`cryptography.x509.Certificate` or PEM/DER ``bytes``.  When
        empty, only ``allow_self_signed`` / ``use_system_trust`` apply.
    allow_self_signed:
        When ``True`` (default) a cryptographically intact self-signed
        signature is still reported ``VALID`` (flagged ``SELF_SIGNED``).
    check_revocation:
        When ``True`` perform OCSP/CRL revocation checking.  Offline this uses
        only revocation material embedded in the document; combined with
        :attr:`ValidationMode.ONLINE`/``AUTO`` it may fetch over the network.
    check_timestamp:
        When ``True`` (default) verify an embedded RFC 3161 signature
        timestamp if present.
    use_system_trust:
        When ``True`` also consult the operating-system CA bundle as trust
        anchors (best effort, via the standard library).
    network_timeout:
        Socket timeout (seconds) for any opt-in online OCSP/CRL/TSA request.
    """

    validation_mode: ValidationMode = ValidationMode.OFFLINE
    validation_method: ValidationMethod = ValidationMethod.PKCS7
    trusted_certificates: List[Any] = field(default_factory=list)
    allow_self_signed: bool = True
    check_revocation: bool = False
    check_timestamp: bool = True
    use_system_trust: bool = False
    network_timeout: float = 10.0

    def to_dict(self) -> dict:
        """Return a plain-dict representation of the scalar options."""
        return {
            "validation_mode": self.validation_mode.value,
            "validation_method": self.validation_method.value,
            "allow_self_signed": self.allow_self_signed,
            "check_revocation": self.check_revocation,
            "check_timestamp": self.check_timestamp,
            "use_system_trust": self.use_system_trust,
            "network_timeout": self.network_timeout,
        }

    def __repr__(self) -> str:
        return (
            f"ValidationOptions("
            f"validation_mode={self.validation_mode!r}, "
            f"validation_method={self.validation_method!r})"
        )


@dataclass
class ValidationResult:
    """Structured result returned by signature validation.

    Attributes
    ----------
    status:
        The overall outcome (:class:`ValidationStatus`).
    message:
        Human-readable summary of the validation outcome.
    errors:
        A list of individual error / warning strings.  Empty when the
        signature is valid.
    signer:
        Human-readable signer identity (certificate subject), when available.
    trust_status:
        Result of certificate-chain trust evaluation (:class:`TrustStatus`).
    revocation_status:
        Result of revocation checking (:class:`RevocationStatus`).
    timestamp:
        Verified RFC 3161 timestamp information, or ``None`` when absent.
    certification_level:
        DocMDP certification level (:class:`CertificationLevel`) when the
        signature is a certifying signature.
    signed_at:
        Claimed signing time (from the CMS ``signing-time`` attribute), if any.
    pades_level:
        PAdES baseline level (:class:`PadesLevel`) the signature satisfies, or
        :attr:`PadesLevel.NONE` for a bare PKCS#7 signature.
    """

    status: ValidationStatus
    message: str = ""
    errors: List[str] = field(default_factory=list)
    signer: Optional[str] = None
    trust_status: Optional[TrustStatus] = None
    revocation_status: Optional[RevocationStatus] = None
    timestamp: Optional[Any] = None
    certification_level: Optional[CertificationLevel] = None
    signed_at: Optional[str] = None
    pades_level: Optional["PadesLevel"] = None

    @property
    def is_valid(self) -> bool:
        """Return ``True`` when the signature passed all checks."""
        return self.status == ValidationStatus.VALID

    def __repr__(self) -> str:
        return (
            f"ValidationResult("
            f"status={self.status!r}, "
            f"message={self.message!r}, "
            f"errors={self.errors!r})"
        )
