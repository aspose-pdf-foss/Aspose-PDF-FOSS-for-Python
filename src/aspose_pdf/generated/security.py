"""Compatibility helpers for signature-compromise checks (Aspose.PDF subset).

``ValidationOptions`` and ``ValidationResult`` are re-exported from the
canonical implementation in :mod:`aspose_pdf.validation` so that code importing
from this module receives the fully-functional classes.
"""

from __future__ import annotations

from typing import Any, List

# Re-export the real implementations so generated-module consumers work.
from aspose_pdf.validation import (  # noqa: F401
    CertificationLevel,
    RevocationStatus,
    TrustStatus,
    ValidationOptions,
    ValidationResult,
    ValidationMode,
    ValidationMethod,
    ValidationStatus,
)


class SignaturesCompromiseDetector:
    """Lightweight signature-compromise heuristic.

    Mirrors the Aspose.PDF ``SignaturesCompromiseDetector`` API.
    """

    def __init__(self, *args, **kwargs) -> None:
        """Initialize the detector.

        The detector does not require any mandatory configuration; however,
        any supplied keyword arguments are retained in ``self.config`` for
        potential future use.
        """
        self.config: dict = dict(kwargs)

    # Methods

    def check(
        self, signatures: List[Any] | None = None, *args, **kwargs
    ) -> "CompromiseCheckResult":
        """Perform a basic compromise detection.

        The implementation looks for the literal word ``"compromised"`` in any
        string representation of the supplied ``signatures`` collection.  This
        heuristic is intentionally simple and is not a cryptographic signature
        validator.  The method returns a :class:`CompromiseCheckResult`
        populated with:

        * ``has_compromised_signatures`` – ``True`` if a compromised signature
          was detected, otherwise ``False``.
        * ``signatures_coverage`` – the total number of signatures examined.
        """
        # Normalise the input to an iterable list; ``None`` uses stored signatures.
        if signatures is None:
            signatures = getattr(self, "_signatures", [])

        sigs: List[Any] = list(signatures)

        has_compromised = False
        for sig in sigs:
            try:
                # Convert the signature to a string and perform a case‑insensitive
                # search for the keyword ``compromised``.
                if isinstance(sig, str) and "compromised" in sig.lower():
                    has_compromised = True
                    break
                # If the signature provides a ``__repr__`` that contains the word,
                # the same check applies.
                sig_str = repr(sig)
                if "compromised" in sig_str.lower():
                    has_compromised = True
                    break
            except Exception:
                # Defensive: ignore any signature that raises during inspection.
                continue

        result = CompromiseCheckResult(
            has_compromised_signatures=has_compromised,
            signatures_coverage=len(sigs),
        )
        return result


class CompromiseCheckResult:
    """Result of a :class:`SignaturesCompromiseDetector` check.

    Mirrors the Aspose.PDF ``CompromiseCheckResult`` API.
    """

    def __init__(self, *args, **kwargs) -> None:
        """Instantiate a result object.

        Keyword arguments ``has_compromised_signatures`` and
        ``signatures_coverage`` are stored directly on the instance.
        """
        self.has_compromised_signatures: Any = kwargs.get("has_compromised_signatures")
        self.signatures_coverage: Any = kwargs.get("signatures_coverage")

    # Class-level attribute defaults.
    has_compromised_signatures: Any = None  # maps_from=HasCompromisedSignatures
    signatures_coverage: Any = None  # maps_from=SignaturesCoverage


# --------------------------------------------------------------------------- #
# Extended helper methods.                                                    #
# --------------------------------------------------------------------------- #

# NOTE: ValidationOptions and ValidationResult helpers live in their canonical
# classes inside aspose_pdf.validation; no monkey-patching needed here.


# ---- SignaturesCompromiseDetector ---------------------------------------- #
def SignaturesCompromiseDetector_reset(self) -> None:
    """Clear any stored configuration and accumulated signatures."""
    self.config.clear()
    if hasattr(self, "_signatures"):
        self._signatures.clear()


def SignaturesCompromiseDetector_add_signature(self, signature: Any) -> None:
    """Add a signature to the internal list for later ``check`` calls."""
    if not hasattr(self, "_signatures"):
        self._signatures = []
    self._signatures.append(signature)


# (Safe helpers)
SignaturesCompromiseDetector.reset = SignaturesCompromiseDetector_reset
SignaturesCompromiseDetector.add_signature = SignaturesCompromiseDetector_add_signature


# ---- CompromiseCheckResult ------------------------------------------------ #
def CompromiseCheckResult___repr__(self) -> str:
    return (
        f"CompromiseCheckResult(has_compromised_signatures={self.has_compromised_signatures!r}, "
        f"signatures_coverage={self.signatures_coverage!r})"
    )


def CompromiseCheckResult___bool__(self) -> bool:
    """Truthiness reflects presence of compromised signatures."""
    return bool(self.has_compromised_signatures)


CompromiseCheckResult.__repr__ = CompromiseCheckResult___repr__
CompromiseCheckResult.__bool__ = CompromiseCheckResult___bool__
