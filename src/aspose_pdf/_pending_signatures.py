"""What ``Document.sign``, ``add_ltv`` and ``add_document_timestamp`` leave for ``save``.

A signature covers bytes, so it can only be made once the document has been
serialized: each call records what to do, and ``Document.save`` does it, in call
order, to the bytes it is about to write -- each as an incremental revision, so
an earlier signature in the same save stays valid under a later one. The
arguments are checked when the call is made, so a mistake surfaces there rather
than halfway through a save that may already have asked a timestamp authority
for a token.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed448, ed25519, rsa
from cryptography.x509 import ocsp

from aspose_pdf.engine.dss import DssMaterial, add_document_timestamp, enable_ltv
from aspose_pdf.engine.sign_field import sign_field
from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.validation import CertificationLevel

_KEY_TYPES = (
    rsa.RSAPrivateKey,
    ec.EllipticCurvePrivateKey,
    ed25519.Ed25519PrivateKey,
    ed448.Ed448PrivateKey,
)
# PKCS#7 (``adbe.pkcs7.detached``) goes through ``cryptography``'s builder,
# which signs with RSA and EC keys only; the CAdES builder also takes Ed25519.
_PKCS7_KEY_TYPES = (rsa.RSAPrivateKey, ec.EllipticCurvePrivateKey)


def _public_key_der(key: Any) -> bytes:
    return key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def _check_pair(certificate: Any, private_key: Any, role: str) -> None:
    if not isinstance(certificate, x509.Certificate):
        raise TypeError(f"The {role} certificate must be a cryptography x509.Certificate")
    if not isinstance(private_key, _KEY_TYPES):
        raise TypeError(f"The {role} private key must be an RSA, EC or Ed25519 private key")
    # A key that is not the certificate's makes a signature that no validator
    # accepts; nothing further along notices, so it is caught here.
    if _public_key_der(private_key.public_key()) != _public_key_der(certificate.public_key()):
        raise PdfValidationException(f"The {role} private key does not belong to its certificate")


def check_signer(certificate: Any, private_key: Any, *, pades: bool) -> None:
    """Refuse a certificate and key that could not make a verifiable signature."""
    _check_pair(certificate, private_key, "signer")
    if isinstance(private_key, ed448.Ed448PrivateKey):
        # RFC 8419: Ed448 in CMS digests with SHAKE256, not produced here.
        raise PdfValidationException(
            "Ed448 keys are not supported for signing: an Ed448 signature "
            "digests with SHAKE256 (RFC 8419), which this signer does not produce"
        )
    if not pades and not isinstance(private_key, _PKCS7_KEY_TYPES):
        raise PdfValidationException(
            f"A {type(private_key).__name__} signs only with pades=True: the "
            "adbe.pkcs7.detached encoder takes RSA and EC keys"
        )


def check_timestamp_source(url: Any, authority: Any) -> tuple[Any, Any] | None:
    """Validate a timestamp URL or local authority; return the authority as a pair."""
    if url is not None and authority is not None:
        raise PdfValidationException(
            "Give a timestamp_url or a timestamp_authority, not both"
        )
    if url is not None and (not isinstance(url, str) or not url):
        raise TypeError("timestamp_url must be a non-empty string")
    if authority is None:
        return None
    try:
        certificate, private_key = authority
    except (TypeError, ValueError):
        raise TypeError(
            "timestamp_authority must be a (certificate, private_key) pair"
        ) from None
    _check_pair(certificate, private_key, "timestamp authority")
    # The local authority signs its tokens with RSA PKCS#1 v1.5.
    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise PdfValidationException("A local timestamp authority needs an RSA key")
    return certificate, private_key


def certification_level(value: Any) -> int | None:
    """The DocMDP ``/P`` a ``certify`` argument asks for, or ``None``."""
    if value is None or value is CertificationLevel.NOT_CERTIFIED:
        return None
    if isinstance(value, CertificationLevel):
        return value.value
    if isinstance(value, bool) or not isinstance(value, int) or value not in (1, 2, 3):
        raise PdfValidationException(
            "certify must be a CertificationLevel or a DocMDP level 1, 2 or 3"
        )
    return value


def der_items(values: Iterable[Any], kind: str) -> list[bytes]:
    """DER bytes for each certificate, CRL or OCSP response in *values*."""
    if isinstance(values, (bytes, bytearray)):
        raise TypeError(f"{kind} must be a sequence of items, not one bytes value")
    out: list[bytes] = []
    for value in values:
        if isinstance(value, (bytes, bytearray)):
            out.append(bytes(value))
        elif isinstance(
            value,
            (x509.Certificate, x509.CertificateRevocationList, ocsp.OCSPResponse),
        ):
            out.append(value.public_bytes(serialization.Encoding.DER))
        else:
            raise TypeError(
                f"{kind} items must be DER bytes or cryptography objects, "
                f"not {type(value).__name__}"
            )
    return out


@dataclass(frozen=True)
class PendingSignature:
    """A signature to put into field *field* when the document is saved."""

    field: str
    certificate: Any
    private_key: Any
    extra_certificates: tuple[Any, ...]
    reason: str | None
    location: str | None
    contact: str | None
    signer_name: str | None
    pades: bool
    timestamp_url: str | None
    timestamp_authority: tuple[Any, Any] | None
    timestamp_timeout: float
    certify: int | None

    def apply(self, data: bytes, *, encryption: Any, limits: Any) -> bytes:
        return sign_field(
            data,
            self.field,
            self.certificate,
            self.private_key,
            pades=self.pades,
            reason=self.reason,
            location=self.location,
            contact=self.contact,
            signer_name=self.signer_name,
            extra_certs=list(self.extra_certificates) or None,
            tsa=self.timestamp_authority,
            timestamp_url=self.timestamp_url,
            timestamp_timeout=self.timestamp_timeout,
            certify_permissions=self.certify,
            limits=limits,
            encryption=encryption,
        )


@dataclass(frozen=True)
class PendingLtv:
    """A ``/DSS`` to add, or extend, when the document is saved."""

    material: DssMaterial

    def apply(self, data: bytes, *, encryption: Any, limits: Any) -> bytes:
        return enable_ltv(data, extra=self.material)


@dataclass(frozen=True)
class PendingDocumentTimestamp:
    """A document timestamp (``ETSI.RFC3161``) to append when the document is saved."""

    timestamp_url: str | None
    timestamp_authority: tuple[Any, Any] | None
    timeout: float

    def apply(self, data: bytes, *, encryption: Any, limits: Any) -> bytes:
        return add_document_timestamp(
            data,
            tsa=self.timestamp_authority,
            timestamp_url=self.timestamp_url,
            timeout=self.timeout,
        )


PendingOperation = PendingSignature | PendingLtv | PendingDocumentTimestamp
