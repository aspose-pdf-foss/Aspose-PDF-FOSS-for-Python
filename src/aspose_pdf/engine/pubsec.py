"""Public-key security handler (``Adobe.PubSec``) key material.

The standard security handler derives the file encryption key from a password.
The public-key handler derives it from a *certificate*: the producer generates a
random 20-byte seed, wraps it (plus that recipient's permission bits) in a CMS
``EnvelopedData`` for every recipient's public key, and stores those envelopes
in ``/Recipients``. A reader holding a matching private key opens one envelope,
recovers the seed, and hashes it together with **every** recipient blob to get
the same file key the producer used (ISO 32000-2, 7.6.5).

Everything downstream of that key -- crypt filters, per-object key derivation,
RC4/AES -- is shared with the standard handler, so this module covers only the
part that differs:

* :func:`build_envelopes` / :func:`open_envelopes` -- the CMS layer, built on
  ``asn1crypto`` for the ASN.1 and ``cryptography`` for RSA, EC, and AES.
* :func:`compute_file_key` -- the hash over seed and recipient blobs.
* :func:`normalize_permissions` -- the permission-bit fixups this handler
  requires, which differ from the standard handler's.

The envelopes are ordinary CMS: ``openssl cms -decrypt`` reads what
:func:`build_envelopes` writes, and :func:`open_envelopes` reads what
``openssl cms -encrypt`` writes.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Any, ClassVar

from asn1crypto import cms, core
from asn1crypto import x509 as asn1_x509
from cryptography.hazmat.primitives import hashes, keywrap, serialization
from cryptography.hazmat.primitives import padding as sym_padding
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from aspose_pdf.exceptions import PdfSecurityException

__all__ = [
    "PUBSEC_FILTER",
    "RecipientPayload",
    "build_envelopes",
    "compute_file_key",
    "normalize_permissions",
    "open_envelopes",
    "subfilter_for",
]

PUBSEC_FILTER = "Adobe.PubSec"

_SEED_LENGTH = 20
_PAYLOAD_LENGTH = 24  # 20-byte seed + 4 permission bytes

# Content-encryption ciphers a producer may have used. RC2 (Acrobat 5) is not
# in this list: `cryptography` does not implement it, and guessing is worse
# than an explicit error.
_CBC_KEY_SIZES = {
    "aes128_cbc": 16,
    "aes192_cbc": 24,
    "aes256_cbc": 32,
    "tripledes_3key": 24,
    "des_ede3_cbc": 24,
}

_AES_WRAP_KEY_SIZES = {
    "aes128_wrap": 16,
    "aes192_wrap": 24,
    "aes256_wrap": 32,
}

_ECDH_HASHES = {
    "1.3.133.16.840.63.0.2": hashes.SHA1,
    "1.3.132.1.11.0": hashes.SHA224,
    "1.3.132.1.11.1": hashes.SHA256,
    "1.3.132.1.11.2": hashes.SHA384,
    "1.3.132.1.11.3": hashes.SHA512,
}


class _EccCmsSharedInfo(core.Sequence):
    """The RFC 5753 input to the X9.63 key derivation function."""

    _fields: ClassVar[list[Any]] = [
        ("key_info", cms.KeyEncryptionAlgorithm),
        (
            "entity_u_info",
            core.OctetString,
            {"explicit": 0, "optional": True},
        ),
        ("supp_pub_info", core.OctetString, {"explicit": 2}),
    ]


class _WrongKey(PdfSecurityException):
    """A failure whose most likely cause is a private key that does not match.

    Raised where an envelope decrypts to something structurally impossible --
    which is exactly what implicit rejection produces from the wrong key --
    rather than where the envelope itself is unsupported or malformed.
    """


@dataclass(frozen=True)
class RecipientPayload:
    """What one opened envelope carries."""

    seed: bytes
    """The 20-byte seed every recipient's envelope repeats."""

    permissions: int
    """This recipient's ``/P`` flags, as a signed 32-bit integer."""


@dataclass(frozen=True)
class _RecipientMatch:
    """A matching CMS recipient and its selected wrapped content key."""

    kind: str
    info: Any
    encrypted_key: bytes | None = None


def subfilter_for(algorithm: str) -> str:
    """The ``/SubFilter`` that matches *algorithm*.

    ``adbe.pkcs7.s4`` is the crypt-filter form Acrobat 6 introduced (RC4 and
    AES-128); ``adbe.pkcs7.s5`` covers AES-256. ``adbe.pkcs7.s3`` is the
    pre-crypt-filter form and is read, not written.
    """
    return "adbe.pkcs7.s5" if algorithm == "AES-256" else "adbe.pkcs7.s4"


def normalize_permissions(permissions: int) -> int:
    """Apply the public-key handler's fixed permission bits.

    The envelope's permission word is not the standard handler's ``/P``. Bit 1
    is set (Adobe's public-key supplement requires it), bits 7 and 8 carry no
    meaning in this layout and are cleared, and **bit 13 is set**: in PDF 2.0
    it means "tolerate a missing MAC", and this handler writes no ``/AuthCode``
    dictionary. Clearing it -- which older references suggest, since it predates
    the 2024 MAC amendment -- makes a PDF 2.0 reader demand a MAC that is not
    there and refuse the document. Bit 2 ("may change encryption settings") is
    meaningful here and is passed through as the caller set it.
    """
    value = int(permissions) & 0xFFFFFFFF
    value |= 1 << 0  # bit 1: required by the public-key handler
    value &= ~(1 << 6)  # bit 7: unused
    value &= ~(1 << 7)  # bit 8: unused
    value |= 1 << 12  # bit 13: no /AuthCode is written, so a missing MAC is fine
    return value - (1 << 32) if value & (1 << 31) else value


def _payload_bytes(seed: bytes, permissions: int) -> bytes:
    if len(seed) != _SEED_LENGTH:
        raise PdfSecurityException(
            f"A public-key seed is {_SEED_LENGTH} bytes, got {len(seed)}"
        )
    return seed + (normalize_permissions(permissions) & 0xFFFFFFFF).to_bytes(
        4, "big"
    )


def compute_file_key(
    seed: bytes,
    recipient_blobs: list[bytes] | tuple[bytes, ...],
    *,
    key_length: int,
    sha256: bool,
    encrypt_metadata: bool = True,
) -> bytes:
    """Derive the file encryption key (ISO 32000-2, 7.6.5.3).

    The digest covers the 20-byte *seed*, then every entry of ``/Recipients``
    **in the order the array stores them** -- which is why a reader needs all
    of them, not just its own -- and then four ``0xFF`` bytes when metadata is
    left in the clear. ``sha256`` selects SHA-256 (AES-256 / ``AESV3``) over
    SHA-1 (everything older).
    """
    if len(seed) != _SEED_LENGTH:
        raise PdfSecurityException(
            f"A public-key seed is {_SEED_LENGTH} bytes, got {len(seed)}"
        )
    digest = hashlib.sha256() if sha256 else hashlib.sha1()  # noqa: S324
    digest.update(seed)
    for blob in recipient_blobs:
        digest.update(blob)
    if not encrypt_metadata:
        digest.update(b"\xff\xff\xff\xff")
    return digest.digest()[:key_length]


# ---------------------------------------------------------------------------
# CMS EnvelopedData
# ---------------------------------------------------------------------------
def _check_key_usage(certificate: Any) -> None:
    """Reject a certificate whose ``keyUsage`` forbids its CMS key method.

    A certificate marked for signing only cannot legitimately receive a
    wrapped key, and a reader that enforces the extension (pyHanko does)
    rejects the document. Failing here beats producing a file the recipient
    cannot open. Certificates with no ``keyUsage`` extension are allowed --
    the extension is optional, and its absence asserts nothing.
    """
    from cryptography import x509 as crypto_x509

    try:
        usage = certificate.extensions.get_extension_for_class(
            crypto_x509.KeyUsage
        ).value
    except (crypto_x509.ExtensionNotFound, AttributeError, ValueError):
        return
    public_key = certificate.public_key()
    if isinstance(public_key, ec.EllipticCurvePublicKey):
        permitted = usage.key_agreement
        required = "keyAgreement"
    else:
        permitted = usage.key_encipherment or usage.data_encipherment
        required = "keyEncipherment or dataEncipherment"
    if permitted:
        return
    subject = getattr(certificate, "subject", "?")
    raise PdfSecurityException(
        f"Certificate {subject} has a keyUsage extension that does not permit "
        f"{required}, so it cannot receive an "
        "encrypted document key; pass ignore_key_usage=True to encrypt anyway"
    )


def build_envelopes(
    recipients: list[tuple[Any, int]],
    *,
    algorithm: str = "AES-256",
    seed: bytes | None = None,
    ignore_key_usage: bool = False,
) -> tuple[bytes, list[bytes]]:
    """Wrap one shared seed for every recipient; return ``(seed, envelopes)``.

    *recipients* pairs an ``x509.Certificate`` with the ``/P`` flags that
    recipient gets -- the handler's one real advantage over a password, since
    each envelope carries its own permissions. Each envelope is a complete CMS
    ``ContentInfo`` (``EnvelopedData``) with an RSA key-transport or EC
    key-agreement recipient and AES-CBC encrypted content, which is what
    ``/Recipients`` stores.
    """
    if not recipients:
        raise PdfSecurityException(
            "Public-key encryption needs at least one recipient certificate"
        )
    if not ignore_key_usage:
        for certificate, _permissions in recipients:
            _check_key_usage(certificate)
    seed = seed if seed is not None else os.urandom(_SEED_LENGTH)
    content_key_size = 32 if algorithm == "AES-256" else 16
    envelopes = [
        _build_envelope(
            certificate, _payload_bytes(seed, permissions), content_key_size
        )
        for certificate, permissions in recipients
    ]
    return seed, envelopes


def _build_envelope(certificate: Any, payload: bytes, key_size: int) -> bytes:
    public_key = certificate.public_key()
    content_key = os.urandom(key_size)
    iv = os.urandom(16)
    padder = sym_padding.PKCS7(128).padder()
    padded = padder.update(payload) + padder.finalize()
    encryptor = Cipher(algorithms.AES(content_key), modes.CBC(iv)).encryptor()
    encrypted_content = encryptor.update(padded) + encryptor.finalize()

    asn1_cert = asn1_x509.Certificate.load(
        certificate.public_bytes(serialization.Encoding.DER)
    )
    if isinstance(public_key, rsa.RSAPublicKey):
        recipient_info = _rsa_recipient_info(
            public_key, asn1_cert, content_key
        )
        version = "v0"
    elif isinstance(public_key, ec.EllipticCurvePublicKey):
        recipient_info = _ec_recipient_info(
            public_key, asn1_cert, content_key
        )
        version = "v2"
    else:
        raise PdfSecurityException(
            "Public-key encryption needs an RSA or EC recipient certificate; "
            f"{type(public_key).__name__} cannot transport or agree a key"
        )
    enveloped = cms.EnvelopedData(
        {
            "version": version,
            "recipient_infos": cms.RecipientInfos([recipient_info]),
            "encrypted_content_info": {
                "content_type": "data",
                "content_encryption_algorithm": {
                    "algorithm": f"aes{key_size * 8}_cbc",
                    "parameters": core.OctetString(iv),
                },
                "encrypted_content": encrypted_content,
            },
        }
    )
    return cms.ContentInfo(
        {"content_type": "enveloped_data", "content": enveloped}
    ).dump()


def _rsa_recipient_info(
    public_key: rsa.RSAPublicKey,
    asn1_cert: Any,
    content_key: bytes,
) -> cms.RecipientInfo:
    encrypted_key = public_key.encrypt(content_key, asym_padding.PKCS1v15())
    return cms.RecipientInfo(
        name="ktri",
        value=cms.KeyTransRecipientInfo(
            {
                "version": "v0",
                "rid": _issuer_and_serial(asn1_cert, key_agreement=False),
                "key_encryption_algorithm": {"algorithm": "rsaes_pkcs1v15"},
                "encrypted_key": encrypted_key,
            }
        ),
    )


def _ec_recipient_info(
    public_key: ec.EllipticCurvePublicKey,
    asn1_cert: Any,
    content_key: bytes,
) -> cms.RecipientInfo:
    ephemeral_key = ec.generate_private_key(public_key.curve)
    shared_secret = ephemeral_key.exchange(ec.ECDH(), public_key)
    ukm = os.urandom(16)
    wrap_name = f"aes{len(content_key) * 8}_wrap"
    wrap_algorithm = cms.KeyEncryptionAlgorithm({"algorithm": wrap_name})
    shared_info = _shared_info(wrap_algorithm, ukm, len(content_key))
    kek = _x963_kdf(
        shared_secret,
        shared_info,
        len(content_key),
        hashes.SHA256,
    )
    encrypted_key = keywrap.aes_key_wrap(kek, content_key)
    point = ephemeral_key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return cms.RecipientInfo(
        name="kari",
        value=cms.KeyAgreeRecipientInfo(
            {
                "version": "v3",
                "originator": cms.OriginatorIdentifierOrKey(
                    name="originator_key",
                    value={
                        "algorithm": {"algorithm": "ec"},
                        "public_key": point,
                    },
                ),
                "ukm": ukm,
                "key_encryption_algorithm": {
                    "algorithm": "1.3.132.1.11.1",
                    "parameters": wrap_algorithm,
                },
                "recipient_encrypted_keys": [
                    {
                        "rid": _issuer_and_serial(
                            asn1_cert, key_agreement=True
                        ),
                        "encrypted_key": encrypted_key,
                    }
                ],
            }
        ),
    )


def _issuer_and_serial(
    asn1_cert: Any, *, key_agreement: bool
) -> cms.RecipientIdentifier | cms.KeyAgreementRecipientIdentifier:
    value = cms.IssuerAndSerialNumber(
        {
            "issuer": asn1_cert.issuer,
            "serial_number": asn1_cert.serial_number,
        }
    )
    identifier_type = (
        cms.KeyAgreementRecipientIdentifier
        if key_agreement
        else cms.RecipientIdentifier
    )
    return identifier_type(name="issuer_and_serial_number", value=value)


def open_envelopes(
    blobs: list[bytes] | tuple[bytes, ...],
    certificate: Any,
    private_key: Any,
) -> RecipientPayload:
    """Open the first envelope *certificate* is a recipient of.

    Raises :class:`PdfSecurityException` when none of the envelopes names this
    certificate, or when one does but cannot be unwrapped -- an explicit
    failure, rather than a wrong key silently producing garbage.
    """
    asn1_cert = asn1_x509.Certificate.load(
        certificate.public_bytes(serialization.Encoding.DER)
    )
    matched = False
    failure: Exception | None = None
    for blob in blobs:
        recipient = _find_recipient(blob, asn1_cert)
        if recipient is None:
            continue
        matched = True
        try:
            payload = _open_envelope(blob, recipient, private_key)
        except _WrongKey as exc:
            # RSA PKCS#1 v1.5 decryption uses implicit rejection: a private key
            # that does not match yields plausible-looking garbage instead of an
            # error, and only shows up further down as a content key of the
            # wrong size or content that will not unpad. Report the actionable
            # cause here and keep the detail on __cause__. Anything else -- an
            # unsupported cipher, a malformed envelope -- says what it is.
            failure = exc
            continue
        if payload is not None:
            return payload
    if matched:
        raise PdfSecurityException(
            "The certificate is a recipient of this document, but its envelope "
            "could not be opened with the supplied private key"
        ) from failure
    raise PdfSecurityException(
        "The supplied certificate is not a recipient of this document"
    )


def _enveloped_data(blob: bytes) -> cms.EnvelopedData | None:
    try:
        info = cms.ContentInfo.load(bytes(blob))
        if info["content_type"].native != "enveloped_data":
            return None
        return info["content"]
    except (ValueError, TypeError, KeyError):
        return None


def _find_recipient(blob: bytes, asn1_cert: Any) -> _RecipientMatch | None:
    """The RecipientInfo naming *asn1_cert*, or ``None``."""
    enveloped = _enveloped_data(blob)
    if enveloped is None:
        return None
    try:
        key_identifier = asn1_cert.key_identifier
    except (ValueError, KeyError):
        key_identifier = None
    for recipient_info in enveloped["recipient_infos"]:
        if recipient_info.name == "ktri":
            ktri = recipient_info.chosen
            if _recipient_id_matches(ktri["rid"], asn1_cert, key_identifier):
                return _RecipientMatch("ktri", ktri)
        elif recipient_info.name == "kari":
            kari = recipient_info.chosen
            for recipient_key in kari["recipient_encrypted_keys"]:
                if _recipient_id_matches(
                    recipient_key["rid"], asn1_cert, key_identifier
                ):
                    return _RecipientMatch(
                        "kari", kari, recipient_key["encrypted_key"].native
                    )
    return None


def _recipient_id_matches(
    rid: Any, asn1_cert: Any, key_identifier: bytes | None
) -> bool:
    if rid.name == "issuer_and_serial_number":
        issuer_serial = rid.chosen
        return (
            issuer_serial["issuer"] == asn1_cert.issuer
            and issuer_serial["serial_number"].native == asn1_cert.serial_number
        )
    if rid.name == "subject_key_identifier" and key_identifier is not None:
        return rid.chosen.native == key_identifier
    if rid.name == "r_key_id" and key_identifier is not None:
        return rid.chosen["subjectKeyIdentifier"].native == key_identifier
    return False


def _open_envelope(
    blob: bytes, recipient: _RecipientMatch, private_key: Any
) -> RecipientPayload | None:
    enveloped = _enveloped_data(blob)
    if enveloped is None:
        return None
    if recipient.kind == "ktri":
        content_key = _unwrap_transport_key(recipient.info, private_key)
    else:
        content_key = _unwrap_agreement_key(
            recipient.info, recipient.encrypted_key, private_key
        )
    encrypted_info = enveloped["encrypted_content_info"]
    encrypted = encrypted_info["encrypted_content"]
    if encrypted is None:
        raise PdfSecurityException(
            "The recipient envelope carries no encrypted content"
        )
    payload = _decrypt_content(
        encrypted_info["content_encryption_algorithm"],
        content_key,
        encrypted.native,
    )
    if len(payload) < _PAYLOAD_LENGTH:
        raise _WrongKey(
            "The recipient envelope is too short to hold a seed and permissions"
        )
    seed = payload[:_SEED_LENGTH]
    permissions = int.from_bytes(
        payload[_SEED_LENGTH:_PAYLOAD_LENGTH], "big", signed=True
    )
    return RecipientPayload(seed=seed, permissions=permissions)


def _unwrap_transport_key(ktri: Any, private_key: Any) -> bytes:
    algorithm = ktri["key_encryption_algorithm"]["algorithm"].native
    encrypted_key = ktri["encrypted_key"].native
    if not hasattr(private_key, "decrypt"):
        raise PdfSecurityException(
            "Opening a recipient envelope needs an RSA private key; "
            f"{type(private_key).__name__} cannot unwrap one"
        )
    if algorithm == "rsaes_pkcs1v15":
        scheme: Any = asym_padding.PKCS1v15()
    elif algorithm == "rsaes_oaep":
        scheme = _oaep_scheme(ktri["key_encryption_algorithm"])
    else:
        raise PdfSecurityException(
            f"Unsupported recipient key-transport algorithm {algorithm!r}"
        )
    try:
        return private_key.decrypt(encrypted_key, scheme)
    except Exception as exc:  # noqa: BLE001 - any failure means the wrong key
        raise _WrongKey(
            "The private key does not open this recipient envelope"
        ) from exc


def _unwrap_agreement_key(
    kari: Any, encrypted_key: bytes | None, private_key: Any
) -> bytes:
    if not isinstance(private_key, ec.EllipticCurvePrivateKey):
        raise PdfSecurityException(
            "Opening a key-agreement recipient envelope needs an EC private "
            f"key; {type(private_key).__name__} cannot derive one"
        )
    algorithm = kari["key_encryption_algorithm"]
    digest_factory = _ECDH_HASHES.get(algorithm["algorithm"].dotted)
    if digest_factory is None:
        raise PdfSecurityException(
            "Unsupported recipient key-agreement algorithm "
            f"{algorithm['algorithm'].native!r}"
        )
    parameters = algorithm["parameters"]
    if parameters is None or isinstance(parameters, core.Void):
        raise PdfSecurityException(
            "The recipient key-agreement algorithm names no key-wrap algorithm"
        )
    try:
        wrap_algorithm = cms.KeyEncryptionAlgorithm.load(parameters.dump())
    except (TypeError, ValueError) as exc:
        raise PdfSecurityException(
            "The recipient key-agreement envelope has malformed parameters"
        ) from exc
    wrap_name = wrap_algorithm["algorithm"].native
    kek_size = _AES_WRAP_KEY_SIZES.get(wrap_name)
    if kek_size is None:
        raise PdfSecurityException(
            f"Unsupported key-wrap algorithm {wrap_name!r} in a recipient envelope"
        )
    originator = kari["originator"]
    if originator.name != "originator_key":
        raise PdfSecurityException(
            "The recipient key-agreement envelope carries no originator key"
        )
    ukm_value = kari["ukm"].native
    ukm = ukm_value if isinstance(ukm_value, bytes) else None
    try:
        originator_key = ec.EllipticCurvePublicKey.from_encoded_point(
            private_key.curve, originator.chosen["public_key"].native
        )
        shared_secret = private_key.exchange(ec.ECDH(), originator_key)
        shared_info = _shared_info(wrap_algorithm, ukm, kek_size)
        kek = _x963_kdf(
            shared_secret,
            shared_info,
            kek_size,
            digest_factory,
        )
        if encrypted_key is None:
            raise ValueError("missing encrypted key")
        return keywrap.aes_key_unwrap(kek, encrypted_key)
    except (TypeError, ValueError, keywrap.InvalidUnwrap) as exc:
        raise _WrongKey(
            "The private key does not open this key-agreement envelope"
        ) from exc


def _shared_info(
    wrap_algorithm: cms.KeyEncryptionAlgorithm,
    ukm: bytes | None,
    kek_size: int,
) -> bytes:
    values: dict[str, Any] = {
        "key_info": wrap_algorithm,
        "supp_pub_info": (kek_size * 8).to_bytes(4, "big"),
    }
    if ukm is not None:
        values["entity_u_info"] = ukm
    return _EccCmsSharedInfo(values).dump()


def _x963_kdf(
    shared_secret: bytes,
    shared_info: bytes,
    length: int,
    digest_factory: Any,
) -> bytes:
    output = bytearray()
    counter = 1
    while len(output) < length:
        digest = hashes.Hash(digest_factory())
        digest.update(shared_secret)
        digest.update(counter.to_bytes(4, "big"))
        digest.update(shared_info)
        output.extend(digest.finalize())
        counter += 1
    return bytes(output[:length])


_OAEP_HASHES = {
    "sha1": hashes.SHA1,
    "sha224": hashes.SHA224,
    "sha256": hashes.SHA256,
    "sha384": hashes.SHA384,
    "sha512": hashes.SHA512,
}


def _oaep_scheme(key_encryption_algorithm: Any) -> Any:
    parameters = key_encryption_algorithm["parameters"]
    digest_name = "sha1"
    if parameters is not None and not isinstance(parameters, core.Void):
        try:
            digest_name = parameters["hash_algorithm"]["algorithm"].native
        except (ValueError, KeyError, TypeError):
            digest_name = "sha1"
    factory = _OAEP_HASHES.get(digest_name)
    if factory is None:
        raise PdfSecurityException(
            f"Unsupported OAEP digest {digest_name!r} in a recipient envelope"
        )
    digest = factory()
    return asym_padding.OAEP(
        mgf=asym_padding.MGF1(algorithm=digest), algorithm=digest, label=None
    )


def _triple_des(key: bytes) -> Any:
    """3DES, from wherever the installed ``cryptography`` keeps it.

    It moved to ``hazmat.decrepit`` in 43; older releases still expose it from
    ``primitives.ciphers.algorithms``, and this package supports both.
    """
    try:
        from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
    except ImportError:  # pragma: no cover - cryptography < 43
        from cryptography.hazmat.primitives.ciphers.algorithms import (  # type: ignore[attr-defined]
            TripleDES,
        )
    return TripleDES(key)


def _decrypt_content(
    content_algorithm: Any, content_key: bytes, encrypted: bytes
) -> bytes:
    name = content_algorithm["algorithm"].native
    key_size = _CBC_KEY_SIZES.get(name)
    if key_size is None:
        raise PdfSecurityException(
            f"Unsupported content-encryption algorithm {name!r} in a recipient "
            "envelope"
        )
    if len(content_key) != key_size:
        raise _WrongKey(
            f"The unwrapped content key is {len(content_key)} bytes; "
            f"{name} needs {key_size}"
        )
    iv = content_algorithm["parameters"].native
    if not isinstance(iv, bytes):
        raise PdfSecurityException(
            "The recipient envelope's content algorithm carries no IV"
        )
    if name.startswith("aes"):
        cipher = Cipher(algorithms.AES(content_key), modes.CBC(iv))
        block_bits = 128
    else:
        cipher = Cipher(_triple_des(content_key), modes.CBC(iv))
        block_bits = 64
    decryptor = cipher.decryptor()
    padded = decryptor.update(encrypted) + decryptor.finalize()
    unpadder = sym_padding.PKCS7(block_bits).unpadder()
    try:
        return unpadder.update(padded) + unpadder.finalize()
    except ValueError as exc:
        raise _WrongKey(
            "The recipient envelope's content did not decrypt cleanly"
        ) from exc
