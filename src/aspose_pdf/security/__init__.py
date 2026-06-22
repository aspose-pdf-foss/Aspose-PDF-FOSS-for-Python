"""Security-related public API for signature compromise detection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["CompromiseCheckResult", "SignaturesCompromiseDetector"]


def _append_reason(reasons: list[str], message: str) -> None:
    if message not in reasons:
        reasons.append(message)


def _has_meaningful_unsigned_tail(reference_data: bytes, signed_end: int) -> bool:
    if signed_end >= len(reference_data):
        return False
    for line in reference_data[signed_end:].splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(b"%"):
            continue
        return True
    return False


def _tail_suggests_annotation_layering(tail: bytes) -> bool:
    lowered = tail.lower()
    if b"/subtype" not in lowered:
        return False
    return b"widget" in lowered or b"/annot" in lowered or b"/popup" in lowered


def _signed_end(signature: Any) -> int | None:
    byte_range = getattr(signature, "byte_range", None)
    if not isinstance(byte_range, list) or len(byte_range) != 4:
        return None
    try:
        return int(byte_range[2]) + int(byte_range[3])
    except (TypeError, ValueError):
        return None


def _tail_protected_by_later_signature(
    signature: Any, signatures: list[Any], data_len: int
) -> bool:
    """Return ``True`` if another signature/timestamp signs over this tail.

    A PAdES archive timestamp (or any later signature) added by incremental
    update covers everything before its own ``/Contents`` hole — including this
    signature and the ``/DSS`` — so the unsigned bytes are not an attack.
    """
    this_end = _signed_end(signature)
    if this_end is None:
        return False
    for other in signatures:
        if other is signature:
            continue
        other_end = _signed_end(other)
        if other_end is None:
            continue
        # A later revision that signs to (essentially) end-of-file covers it.
        if other_end > this_end and other_end >= data_len - 4:
            return True
    return False


def _tail_is_only_validation_material(tail: bytes) -> bool:
    """Heuristic: the tail only adds a ``/DSS`` (LTV material), not content.

    Recognises a document-security-store incremental update — which legitimately
    appears after a signed revision for PAdES-LT — while still rejecting tails
    that layer annotations/widgets on top of the signed content.
    """
    lowered = tail.lower()
    if b"/dss" not in lowered and b"/vri" not in lowered:
        return False
    return not _tail_suggests_annotation_layering(tail)


@dataclass
class CompromiseCheckResult:
    has_compromised_signatures: bool
    signatures_coverage: int
    reasons: list[str] = field(default_factory=list)

    @property
    def compromised(self) -> bool:
        return self.has_compromised_signatures


class SignaturesCompromiseDetector:
    """Detect possible compromise indicators around signed PDFs."""

    def __init__(self, document: Any | None = None) -> None:
        self._document = document

    def check(self) -> CompromiseCheckResult:
        if self._document is None or not hasattr(self._document, "signatures"):
            return CompromiseCheckResult(
                has_compromised_signatures=False,
                signatures_coverage=0,
                reasons=["unsigned document"],
            )

        signatures = list(getattr(self._document, "signatures") or [])
        if not signatures:
            return CompromiseCheckResult(
                has_compromised_signatures=False,
                signatures_coverage=0,
                reasons=["unsigned document"],
            )

        compromised = False
        reasons: list[str] = []
        for signature in signatures:
            if not getattr(signature, "valid", True):
                compromised = True
                _append_reason(reasons, "corrupted signature")
                continue

            byte_range = getattr(signature, "byte_range", None)
            reference_data = getattr(signature, "reference_data", None)
            if not isinstance(byte_range, list) or len(byte_range) != 4:
                continue
            if not isinstance(reference_data, (bytes, bytearray)):
                continue

            signed_end = int(byte_range[2]) + int(byte_range[3])
            if not _has_meaningful_unsigned_tail(bytes(reference_data), signed_end):
                continue

            reference_bytes = bytes(reference_data)
            tail = reference_bytes[signed_end:]
            # Legitimate PAdES long-term updates after the signed revision: an
            # archive timestamp (or later signature) that covers the tail, or a
            # /DSS that only adds validation material — neither is a compromise.
            if _tail_protected_by_later_signature(
                signature, signatures, len(reference_bytes)
            ) or _tail_is_only_validation_material(tail):
                continue

            compromised = True
            _append_reason(
                reasons,
                "document contains unsigned incremental PDF bytes after the signed revision",
            )
            if _tail_suggests_annotation_layering(tail):
                _append_reason(
                    reasons,
                    "incremental bytes may include unsigned annotations/widgets (layering risk)",
                )

        return CompromiseCheckResult(
            has_compromised_signatures=compromised,
            signatures_coverage=len(signatures),
            reasons=reasons,
        )
