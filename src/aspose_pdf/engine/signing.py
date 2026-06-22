"""Signing utilities for PDF handling.

This module provides a small wrapper around the ``cryptography`` library to
create a self‑signed X.509 certificate and to sign arbitrary binary data using
PKCS#7 (CMS) detached signatures.

Certificate and signature *creation* uses only the ``cryptography`` package.
Embedding an RFC 3161 timestamp additionally relies on
:mod:`aspose_pdf.engine.cms` / :mod:`aspose_pdf.engine.timestamp` (which use
``asn1crypto``) to inject the unsigned attribute into the CMS structure.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509.oid import NameOID
import logging

logger = logging.getLogger("aspose_pdf.signing")


def _key_usage(*, cert_sign: bool) -> x509.KeyUsage:
    """Return a :class:`KeyUsage` extension for a CA or a leaf signing cert."""
    return x509.KeyUsage(
        digital_signature=True,
        content_commitment=not cert_sign,
        key_encipherment=False,
        data_encipherment=False,
        key_agreement=False,
        key_cert_sign=cert_sign,
        crl_sign=cert_sign,
        encipher_only=False,
        decipher_only=False,
    )


class SigningUtils:
    """Utility class for generating certificates and PKCS#7 signatures.

    The methods are implemented as ``@staticmethod`` because they do not rely on
    instance state. They can be used directly via ``SigningUtils.create_self_signed_cert``
    and ``SigningUtils.sign_data_pkcs7``.
    """

    @staticmethod
    def create_self_signed_cert() -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
        """Generate a self‑signed X.509 certificate and a matching RSA private key.

        Returns
        -------
        tuple
            ``(certificate, private_key)`` where ``certificate`` is an instance of
            :class:`cryptography.x509.Certificate` and ``private_key`` is an
            :class:`cryptography.hazmat.primitives.asymmetric.rsa.RSAPrivateKey`.
        """
        # Generate a 2048-bit RSA private key.
        logger.info("Generating 2048-bit RSA private key for self-signed certificate")
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

        # Subject and issuer are identical for a self‑signed certificate.
        subject = issuer = x509.Name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, "SelfSignedCertificate"),
            ]
        )

        now = datetime.now(timezone.utc)
        cert_builder = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(private_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=365))
            .add_extension(
                x509.BasicConstraints(ca=True, path_length=None), critical=True
            )
        )

        certificate = cert_builder.sign(private_key, hashes.SHA256())
        return certificate, private_key

    @staticmethod
    def create_self_signed_ca(
        common_name: str = "Test Root CA", days: int = 3650
    ) -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
        """Generate a self-signed CA certificate and key (for building chains)."""
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
        now = datetime.now(timezone.utc)
        ski = x509.SubjectKeyIdentifier.from_public_key(key.public_key())
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=days))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
            .add_extension(_key_usage(cert_sign=True), True)
            .add_extension(ski, False)
            .sign(key, hashes.SHA256())
        )
        return cert, key

    @staticmethod
    def issue_certificate(
        subject_cn: str,
        issuer_cert: x509.Certificate,
        issuer_key: rsa.RSAPrivateKey,
        *,
        ca: bool = False,
        days: int = 365,
        not_before: Optional[datetime] = None,
        not_after: Optional[datetime] = None,
        eku: Optional[Sequence[x509.ObjectIdentifier]] = None,
    ) -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
        """Issue a certificate signed by *issuer_cert* / *issuer_key*.

        Used both to build trust chains for certification-grade signing and as a
        test fixture.  ``not_before`` / ``not_after`` allow constructing expired
        certificates for negative tests.
        """
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = datetime.now(timezone.utc)
        if not_before is None:
            not_before = now - timedelta(days=1)
        if not_after is None:
            not_after = now + timedelta(days=days)
        public_key = key.public_key()
        builder = (
            x509.CertificateBuilder()
            .subject_name(
                x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject_cn)])
            )
            .issuer_name(issuer_cert.subject)
            .public_key(public_key)
            .serial_number(x509.random_serial_number())
            .not_valid_before(not_before)
            .not_valid_after(not_after)
            .add_extension(x509.BasicConstraints(ca=ca, path_length=None), True)
            .add_extension(_key_usage(cert_sign=ca), True)
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(public_key), False
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(
                    issuer_key.public_key()
                ),
                False,
            )
        )
        if eku:
            builder = builder.add_extension(x509.ExtendedKeyUsage(list(eku)), False)
        cert = builder.sign(issuer_key, hashes.SHA256())
        return cert, key

    @staticmethod
    def sign_data_pkcs7(
        data: bytes,
        cert: x509.Certificate,
        key: rsa.RSAPrivateKey,
        *,
        extra_certs: Optional[Sequence[x509.Certificate]] = None,
        tsa: Optional[tuple] = None,
        timestamp_url: Optional[str] = None,
        timestamp_timeout: float = 10.0,
    ) -> bytes:
        """Create a detached PKCS#7 signature for *data*.

        Parameters
        ----------
        data:
            The raw bytes to be signed.
        cert:
            The X.509 certificate corresponding to ``key``.
        key:
            The private key used for signing.
        extra_certs:
            Additional certificates (e.g. intermediate CAs) to embed so a
            verifier can build the trust chain.
        tsa:
            Optional ``(tsa_cert, tsa_key)`` tuple for embedding a timestamp
            from a *local* TSA (offline).
        timestamp_url:
            Optional URL of a network RFC 3161 TSA; used when *tsa* is ``None``.

        Returns
        -------
        bytes
            DER‑encoded PKCS#7 signature (detached).
        """
        builder = (
            pkcs7.PKCS7SignatureBuilder()
            .set_data(data)
            .add_signer(cert, key, hashes.SHA256())
        )
        for extra in extra_certs or []:
            builder = builder.add_certificate(extra)
        # ``Binary`` is essential: without it ``cryptography`` performs S/MIME
        # text canonicalisation (LF -> CRLF) before hashing, which corrupts the
        # digest for binary PDF byte ranges and makes the signature unverifiable.
        signature = builder.sign(
            serialization.Encoding.DER,
            [pkcs7.PKCS7Options.DetachedSignature, pkcs7.PKCS7Options.Binary],
        )
        if tsa is not None or timestamp_url:
            signature = SigningUtils._embed_timestamp(
                signature, tsa, timestamp_url, timestamp_timeout
            )
        logger.debug("Successfully generated PKCS7 signature (detached)")
        return signature

    @staticmethod
    def sign_data_cades(
        data: bytes,
        cert: x509.Certificate,
        key: rsa.RSAPrivateKey,
        *,
        extra_certs: Optional[Sequence[x509.Certificate]] = None,
        hash_algo: str = "sha256",
        tsa: Optional[tuple] = None,
        timestamp_url: Optional[str] = None,
        timestamp_timeout: float = 10.0,
    ) -> bytes:
        """Create a detached **CAdES-BES** signature for *data* (PAdES baseline).

        Unlike :meth:`sign_data_pkcs7`, the result includes the ESS
        ``signing-certificate-v2`` signed attribute that binds the signer's
        certificate, as required for PAdES.  Pair it with the
        ``ETSI.CAdES.detached`` ``/SubFilter`` in the signature dictionary.

        Embedding a signature timestamp (via *tsa* or *timestamp_url*) upgrades
        the result from PAdES-B to PAdES-T.
        """
        from aspose_pdf.engine import cms as cms_mod

        signature = cms_mod.build_cades_signed_data(
            data,
            cert,
            key,
            hash_algo=hash_algo,
            extra_certs=list(extra_certs or []),
        )
        if tsa is not None or timestamp_url:
            signature = SigningUtils._embed_timestamp(
                signature, tsa, timestamp_url, timestamp_timeout
            )
        logger.debug("Successfully generated CAdES-BES signature (detached)")
        return signature

    @staticmethod
    def _embed_timestamp(
        signature_der: bytes,
        tsa: Optional[tuple],
        timestamp_url: Optional[str],
        timeout: float,
    ) -> bytes:
        """Add an RFC 3161 timestamp over the signer's signature value."""
        import hashlib

        from aspose_pdf.engine import cms as cms_mod
        from aspose_pdf.engine import timestamp as ts_mod

        info = cms_mod.parse_signed_data(signature_der)
        imprint = hashlib.sha256(info.signature).digest()
        if tsa is not None:
            tsa_cert, tsa_key = tsa
            token = ts_mod.make_timestamp_token(imprint, "sha256", tsa_cert, tsa_key)
        else:
            token = ts_mod.request_timestamp(
                imprint, "sha256", timestamp_url, timeout=timeout
            )
        return cms_mod.inject_unsigned_timestamp(signature_der, token)
