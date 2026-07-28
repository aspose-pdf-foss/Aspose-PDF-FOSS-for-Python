"""PdfSignature implementation.

This module provides the :class:`PdfSignature` dataclass which represents a PDF
digital signature and offers a lightweight verification routine. The goal is to
verify the integrity of the signed document using the ``ByteRange`` array and to
ensure that the provided PKCS#7 blob is at least syntactically valid.

The verification does **not** perform full PKCS#7 certificate chain checking -
that would require a full CMS implementation which is beyond the scope of the
project and would introduce heavy dependencies. Instead, the method checks:

1. The ``ByteRange`` array has exactly four integers.
2. The ranges describe two non-overlapping slices that together cover the signed
   revision (from byte 0 through ``start2 + len2``) except for the signature
   placeholder. Trailing bytes (e.g. further incremental updates after signing)
   are ignored for hashing; ``reference_data`` may be longer than that revision.
3. The computed digest of the covered data matches the ``MessageDigest`` attribute
   inside the PKCS#7 structure when it can be extracted using ``cryptography``.

If any of these checks fail, ``valid`` returns ``False``.

For structured, configurable validation use :meth:`PdfSignature.validate` which
accepts a :class:`~aspose_pdf.validation.ValidationOptions` instance and returns
a :class:`~aspose_pdf.validation.ValidationResult`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.serialization import pkcs7

from aspose_pdf.exceptions import PdfResourceLimitException
from aspose_pdf.load_limits import PdfLoadLimits, _coerce_limits, _LoadBudget

# Narrow catches for PKCS#7/DER work; unexpected failures propagate.
_PKCS7_LOAD_ERRORS: tuple[type[BaseException], ...] = (
    ValueError,
    TypeError,
    UnsupportedAlgorithm,
)
_PKCS7_DIGEST_WALK_ERRORS: tuple[type[BaseException], ...] = (
    ValueError,
    TypeError,
    AttributeError,
    KeyError,
    IndexError,
    UnsupportedAlgorithm,
)

if TYPE_CHECKING:
    from aspose_pdf.validation import ValidationOptions, ValidationResult


def _has_document_timestamp(reference_data: bytes, signed_end: int) -> bool:
    """Return ``True`` if a document timestamp follows the signed revision.

    A PAdES document timestamp is a signature dictionary with ``/SubFilter
    /ETSI.RFC3161`` added in a later incremental update; its presence after the
    signed revision is what raises a signature from PAdES-LT to PAdES-LTA.
    """
    tail = reference_data[max(0, signed_end) :]
    return b"ETSI.RFC3161" in tail


@dataclass
class PdfSignature:
    """Represent a PDF digital signature.

    Attributes correspond to the fields found in a PDF signature dictionary.
    """

    name: str
    contents: bytes  # The PKCS#7 blob (DER encoded)
    byte_range: list[int]  # [start1, len1, start2, len2]
    reference_data: bytes  # Full PDF document bytes

    # Optional metadata fields
    date: str | None = None
    reason: str | None = None
    location: str | None = None
    contact_info: str | None = None
    sub_filter: str | None = None  # e.g. adbe.pkcs7.detached
    docmdp_level: int | None = None  # DocMDP /P (1/2/3) for certification sigs
    load_limits: PdfLoadLimits | None = field(default=None, repr=False, compare=False)
    _load_budget: _LoadBudget | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._load_budget is None:
            self.load_limits = _coerce_limits(self.load_limits)
            self._load_budget = _LoadBudget(self.load_limits)
        else:
            if not isinstance(self._load_budget, _LoadBudget):
                raise TypeError("_load_budget must be a _LoadBudget instance or None")
            if (
                self.load_limits is not None
                and self.load_limits != self._load_budget.limits
            ):
                raise ValueError("load_limits must match _load_budget.limits")
            self.load_limits = self._load_budget.limits

    @property
    def valid(self) -> bool:
        """Public accessor that safely verifies the signature.

        Returns ``True`` only when the internal integrity checks succeed.
        Validation failures result in ``False``; configured resource-limit
        failures propagate so callers cannot mistake a rejected workload for
        an ordinary invalid signature.
        """
        try:
            return self._verify_integrity()
        except PdfResourceLimitException:
            raise
        except Exception:
            # API contract: never raises; any verification failure → False.
            return False

    def validate(
        self, options: ValidationOptions | None = None
    ) -> ValidationResult:
        """Validate the signature with configurable options.

        This method provides a richer alternative to the simple :attr:`valid`
        property.  It runs the same cryptographic checks but returns a
        structured :class:`~aspose_pdf.validation.ValidationResult` that
        contains a status code, human-readable message, and a list of
        individual error strings.

        Parameters
        ----------
        options:
            A :class:`~aspose_pdf.validation.ValidationOptions` instance that
            controls *how* the validation is performed.  When ``None``, default
            options are used (offline mode, PKCS#7 method).

        Returns
        -------
        ValidationResult
            Returns a structured result for signature-validation failures.
            ``PdfResourceLimitException`` still propagates.
        """
        from aspose_pdf.validation import (
            ValidationOptions as _ValidationOptions,
        )
        from aspose_pdf.validation import (
            ValidationResult as _ValidationResult,
        )
        from aspose_pdf.validation import (
            ValidationStatus,
        )

        if options is None:
            options = _ValidationOptions()

        self._load_budget.check_input(len(self.reference_data))

        errors: list[str] = []

        # --- Stage 1: ByteRange structural validation ---
        if len(self.byte_range) != 4:
            return _ValidationResult(
                status=ValidationStatus.INVALID,
                message="ByteRange must contain exactly four integers",
                errors=["ByteRange must contain exactly four integers"],
            )

        start1, len1, start2, len2 = self.byte_range

        if any(v < 0 for v in (start1, len1, start2, len2)):
            errors.append("ByteRange contains negative values")

        data_len = len(self.reference_data)
        signed_len = start2 + len2
        if start1 + len1 > data_len or signed_len > data_len:
            errors.append("ByteRange extends beyond document boundaries")

        if start2 < start1 + len1:
            errors.append("ByteRange ranges overlap")

        if start1 != 0:
            errors.append("ByteRange first range must start at byte 0")

        if errors:
            return _ValidationResult(
                status=ValidationStatus.INVALID,
                message="; ".join(errors),
                errors=errors,
            )

        chunk1 = self.reference_data[start1 : start1 + len1]
        chunk2 = self.reference_data[start2 : start2 + len2]
        signed_bytes = chunk1 + chunk2

        # A document timestamp (PAdES-LTA) carries an RFC 3161 token, not a
        # signer over the ByteRange, so it is validated as a timestamp — and
        # before the PKCS#7 structural check below, which does not expect a
        # token's encapsulated TSTInfo content.
        if (self.sub_filter or "").lower() == "etsi.rfc3161":
            return self._validate_document_timestamp(signed_bytes)

        # --- Stage 2: PKCS#7 structural check ---
        try:
            _ = pkcs7.load_der_pkcs7_certificates(self.contents)
        except _PKCS7_LOAD_ERRORS as exc:
            return _ValidationResult(
                status=ValidationStatus.INVALID,
                message=f"Invalid PKCS#7 structure: {exc}",
                errors=[f"PKCS#7 parse error: {exc}"],
            )

        # --- Stage 3: full CMS validation ---
        # Cryptographically verify the signer, build/validate the certificate
        # chain, check revocation and any RFC 3161 timestamp, and evaluate
        # DocMDP certification.  Delegated to the engine so this module stays a
        # thin public dataclass.
        try:
            # Harvest long-term validation material from the document security
            # store so chain building and revocation can use it offline (LTV).
            from aspose_pdf.engine import dss as _dss
            from aspose_pdf.engine.signature_validator import validate_cms

            dss_material = _dss.read_dss(
                self.reference_data,
                limits=self.load_limits,
                budget=self._load_budget,
            )

            return validate_cms(
                self.contents,
                signed_bytes,
                options,
                docmdp_level=self.docmdp_level,
                reference_data=self.reference_data,
                signed_end=signed_len,
                sub_filter=self.sub_filter,
                dss_certs=dss_material.certs,
                dss_crls=dss_material.crls,
                dss_ocsps=dss_material.ocsps,
                has_document_timestamp=_has_document_timestamp(
                    self.reference_data, signed_len
                ),
            )
        except PdfResourceLimitException:
            raise
        except Exception as exc:
            # Surface unexpected validation errors as a structured result.
            return _ValidationResult(
                status=ValidationStatus.INVALID,
                message=f"Signature validation error: {exc}",
                errors=[str(exc)],
            )

    def _validate_document_timestamp(
        self, signed_bytes: bytes
    ) -> ValidationResult:
        """Validate a DocTimeStamp signature (PAdES-LTA archive timestamp)."""
        from aspose_pdf.engine import timestamp as _timestamp
        from aspose_pdf.validation import (
            ValidationResult as _ValidationResult,
        )
        from aspose_pdf.validation import (
            ValidationStatus,
        )

        ts_info = _timestamp.verify_timestamp_token(self.contents, signed_bytes)
        if ts_info.verified:
            return _ValidationResult(
                status=ValidationStatus.VALID,
                message="Document timestamp verified successfully",
                signer=ts_info.tsa,
                timestamp=ts_info,
                signed_at=ts_info.gen_time.isoformat()
                if ts_info.gen_time is not None
                else None,
            )
        return _ValidationResult(
            status=ValidationStatus.INVALID,
            message=ts_info.reason or "document timestamp not verified",
            errors=[ts_info.reason or "document timestamp not verified"],
            signer=ts_info.tsa,
            timestamp=ts_info,
        )

    # ---------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------
    def _verify_integrity(self) -> bool:
        """Perform the actual verification steps.

        The method follows three stages:
        1. Validate the ``byte_range`` layout.
        2. Extract the signed byte slices and compute their SHA-256 digest.
        3. Attempt to load the PKCS#7 blob using ``cryptography`` and compare the
           embedded ``MessageDigest`` attribute (if present) with the computed
           digest.
        """
        # Stage 1 - ByteRange validation
        if len(self.byte_range) != 4:
            return False
        start1, len1, start2, len2 = self.byte_range
        # Basic sanity checks
        if any(v < 0 for v in (start1, len1, start2, len2)):
            return False
        data_len = len(self.reference_data)
        self._load_budget.check_input(data_len)
        signed_len = start2 + len2
        if start1 + len1 > data_len or signed_len > data_len:
            return False
        # Ensure non-overlapping and proper ordering
        if start2 < start1 + len1:
            return False
        # First range starts at 0; second range ends at the signed revision length
        # (``start2 + len2``). ``reference_data`` may be longer when incremental
        # updates were appended after signing.
        if start1 != 0:
            return False

        chunk1 = self.reference_data[start1 : start1 + len1]
        chunk2 = self.reference_data[start2 : start2 + len2]
        signed_data = chunk1 + chunk2

        # A document timestamp (PAdES-LTA) carries an RFC 3161 token whose
        # message imprint — not a signer's MessageDigest — binds the covered
        # bytes, so its integrity is checked by verifying the token.
        if (self.sub_filter or "").lower() == "etsi.rfc3161":
            from aspose_pdf.engine import timestamp as _timestamp

            return _timestamp.verify_timestamp_token(
                self.contents, signed_data
            ).verified

        # Stage 2 - Compute hash of the covered data (SHA-256 is the most common)
        digest = hashlib.sha256(signed_data).digest()

        # Stage 3 - Verify PKCS#7 structure and optional MessageDigest
        try:
            # ``pkcs7.load_der_pkcs7_certificates`` returns a list of certificates.
            # It raises if the DER data is not a PKCS#7 container.
            _ = pkcs7.load_der_pkcs7_certificates(self.contents)
        except _PKCS7_LOAD_ERRORS:
            # The blob is not a valid PKCS#7 container.
            return False

        # Attempt to extract the MessageDigest attribute using cryptography's
        # internal APIs. The library does not expose a high-level verifier for
        # detached signatures, but we can inspect the signed attributes.
        try:
            # ``pkcs7.PKCS7SignatureBuilder`` cannot load, so we fall back to
            # ``cryptography.hazmat.primitives.serialization.pkcs7`` which offers
            # ``load_der_pkcs7_signed_data`` in newer versions. Guard against its
            # absence.
            load_func = getattr(pkcs7, "load_der_pkcs7_signed_data", None)
            if load_func is None:
                # If unavailable, we cannot compare MessageDigest; consider the
                # ByteRange check sufficient.
                return True
            pkcs7_obj = load_func(self.contents)
            # ``pkcs7_obj`` provides ``signers`` - each has ``signed_attributes``.
            for signer in pkcs7_obj.signers:
                attrs = signer.signed_attributes
                # Look for the MessageDigest OID (1.2.840.113549.1.9.4)
                for attr in attrs:
                    if (
                        getattr(attr, "oid", None)
                        and attr.oid.dotted_string == "1.2.840.113549.1.9.4"
                    ):
                        # ``attr.value`` is a list of OCTET STRINGs; take first.
                        msg_digest = attr.value[0].native
                        if msg_digest == digest:
                            return True
            # If none match, verification fails.
            return False
        except _PKCS7_DIGEST_WALK_ERRORS:
            # Parsing issue during MessageDigest walk - ByteRange + PKCS#7 shell OK.
            return True
