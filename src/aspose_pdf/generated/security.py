"""Compatibility exports for signature validation and compromise checks.

The generated compatibility surface intentionally re-exports the canonical
implementations so both import paths provide identical, fully functional
behavior.
"""

from __future__ import annotations

from aspose_pdf.security import CompromiseCheckResult, SignaturesCompromiseDetector
from aspose_pdf.validation import (
    CertificationLevel,
    RevocationStatus,
    TrustStatus,
    ValidationMethod,
    ValidationMode,
    ValidationOptions,
    ValidationResult,
    ValidationStatus,
)

__all__ = [
    "CertificationLevel",
    "CompromiseCheckResult",
    "RevocationStatus",
    "SignaturesCompromiseDetector",
    "TrustStatus",
    "ValidationMethod",
    "ValidationMode",
    "ValidationOptions",
    "ValidationResult",
    "ValidationStatus",
]
