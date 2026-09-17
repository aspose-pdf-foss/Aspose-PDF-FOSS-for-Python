"""Encrypting for certificate recipients (the ``/Adobe.PubSec`` handler).

The standard security handler gates a document behind a shared password. The
public-key handler gates it behind certificates: a random seed is wrapped in a
CMS ``EnvelopedData`` per recipient, the file key is a hash over that seed and
*every* recipient blob, and each envelope carries its own permission flags. A
document encrypted this way could previously not be opened at all -- not a
degraded read, a hard failure -- and there was no way to produce one.

The CMS layer is cross-checked against OpenSSL and the whole handler against
pyHanko (an independent implementation) outside the suite; these tests pin the
behaviour that verification established, in both directions, without needing
either tool installed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID

from aspose_pdf import Document, Recipient
from aspose_pdf.engine.pubsec import (
    build_envelopes,
    compute_file_key,
    normalize_permissions,
    open_envelopes,
    open_kek_envelopes,
    open_password_envelopes,
)
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.exceptions import PdfSecurityException, PdfValidationException

_TEXT = "Recipients only."


def _make_cert(
    common_name: str, *, key_encipherment: bool = True
) -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    """A self-signed RSA certificate with an explicit ``keyUsage``."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=key_encipherment,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False
        )
        .sign(key, hashes.SHA256())
    )
    return certificate, key


def _make_ec_cert(
    common_name: str, *, key_agreement: bool = True
) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    """A self-signed EC certificate with an explicit ``keyUsage``."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=key_agreement,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False
        )
        .sign(key, hashes.SHA256())
    )
    return certificate, key


# RSA key generation is the slow part of this suite; two identities are enough
# for every case, so they are made once.
@pytest.fixture(scope="module")
def alice():
    return _make_cert("Alice")


@pytest.fixture(scope="module")
def bob():
    return _make_cert("Bob")


@pytest.fixture(scope="module")
def signing_only():
    return _make_cert("Signer", key_encipherment=False)


@pytest.fixture(scope="module")
def carol():
    return _make_ec_cert("Carol")


def _sealed(recipients, *, algorithm: str = "AES-256", text: str = _TEXT) -> bytes:
    document = Document()
    document.pages.add().add_text(text, 72, 700, font_size=14)
    document.encrypt_for_recipients(recipients, algorithm=algorithm)
    return document._engine_pdf.to_bytes()


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------


def test_recipient_opens_the_document_a_password_cannot(alice, tmp_path):
    data = _sealed([Recipient(alice[0])])
    path = tmp_path / "sealed.pdf"
    path.write_bytes(data)

    with Document(path, certificate=alice[0], private_key=alice[1]) as document:
        assert document.page_count == 1
        assert _TEXT.encode() in document.pages[0].content
        # The envelope's flags become the document's, and -- as with a
        # password-opened file -- the loaded document is no longer "encrypted"
        # from the caller's side once the credential has unlocked it.
        assert document.permissions == normalize_permissions(-1)
        assert document.is_encrypted is False


def test_the_saved_bytes_really_are_encrypted(alice):
    data = _sealed([Recipient(alice[0])])

    # The whole point: no plaintext content survives in the file.
    assert _TEXT.encode() not in data
    assert b"BT /F1" not in data
    assert b"/Adobe.PubSec" in data


def test_opening_without_a_certificate_says_what_is_missing(alice, tmp_path):
    path = tmp_path / "sealed.pdf"
    path.write_bytes(_sealed([Recipient(alice[0])]))

    with pytest.raises(PdfSecurityException, match="certificate recipients"):
        Document(path)


def test_a_stranger_certificate_is_rejected(alice, bob, tmp_path):
    path = tmp_path / "sealed.pdf"
    path.write_bytes(_sealed([Recipient(alice[0])]))

    with pytest.raises(PdfSecurityException, match="not a recipient"):
        Document(path, certificate=bob[0], private_key=bob[1])


def test_a_mismatched_private_key_is_rejected(alice, bob, tmp_path):
    path = tmp_path / "sealed.pdf"
    path.write_bytes(_sealed([Recipient(alice[0])]))

    # Alice's certificate names her envelope, but Bob's key cannot open it.
    with pytest.raises(PdfSecurityException, match="could not be opened"):
        Document(path, certificate=alice[0], private_key=bob[1])


@pytest.mark.parametrize("algorithm", ["AES-256", "AES-128", "RC4"])
def test_every_cipher_round_trips(alice, algorithm, tmp_path):
    path = tmp_path / f"sealed-{algorithm}.pdf"
    path.write_bytes(_sealed([Recipient(alice[0])], algorithm=algorithm))

    with Document(path, certificate=alice[0], private_key=alice[1]) as document:
        assert _TEXT.encode() in document.pages[0].content
        assert document._engine_pdf.encryption_algorithm == algorithm


def test_ec_recipient_opens_the_document(carol, tmp_path):
    from asn1crypto import cms

    _seed, envelopes = build_envelopes([(carol[0], -1)])
    envelope = cms.ContentInfo.load(envelopes[0])
    assert envelope["content"]["recipient_infos"][0].name == "kari"

    data = _sealed([Recipient(carol[0])])
    path = tmp_path / "ec-sealed.pdf"
    path.write_bytes(data)

    with Document(path, certificate=carol[0], private_key=carol[1]) as document:
        assert _TEXT.encode() in document.pages[0].content
        assert document.permissions == normalize_permissions(-1)


def test_a_mismatched_ec_private_key_is_rejected(carol, tmp_path):
    path = tmp_path / "ec-sealed.pdf"
    path.write_bytes(_sealed([Recipient(carol[0])]))
    wrong_key = ec.generate_private_key(ec.SECP256R1())

    with pytest.raises(PdfSecurityException, match="could not be opened"):
        Document(path, certificate=carol[0], private_key=wrong_key)


# ---------------------------------------------------------------------------
# Per-recipient permissions -- what no password scheme can express
# ---------------------------------------------------------------------------


def test_each_recipient_sees_its_own_permissions(alice, bob, tmp_path):
    path = tmp_path / "sealed.pdf"
    path.write_bytes(
        _sealed(
            [
                Recipient(alice[0]),  # everything
                Recipient(bob[0], permissions=-3844),  # a restricted set
            ]
        )
    )

    with Document(path, certificate=alice[0], private_key=alice[1]) as document:
        alice_permissions = document.permissions
    with Document(path, certificate=bob[0], private_key=bob[1]) as document:
        bob_permissions = document.permissions

    assert alice_permissions == normalize_permissions(-1)
    assert bob_permissions == normalize_permissions(-3844)
    assert alice_permissions != bob_permissions


def test_a_bare_certificate_takes_the_shared_permissions(alice, tmp_path):
    document = Document()
    document.pages.add().add_text(_TEXT, 72, 700, font_size=14)
    document.encrypt_for_recipients([alice[0]], permissions=-3844)
    path = tmp_path / "sealed.pdf"
    path.write_bytes(document._engine_pdf.to_bytes())

    with Document(path, certificate=alice[0], private_key=alice[1]) as reopened:
        assert reopened.permissions == normalize_permissions(-3844)


# ---------------------------------------------------------------------------
# The bits the format pins down
# ---------------------------------------------------------------------------


def test_permission_bit_13_stays_set_so_a_missing_mac_is_tolerated():
    """Bit 13 means "a missing PDF 2.0 MAC is acceptable".

    This handler writes no ``/AuthCode``, so clearing bit 13 -- which
    pre-MAC-amendment references suggest -- makes a PDF 2.0 reader demand a MAC
    that is not there and refuse the document outright.
    """
    for value in (-1, -4, -3844, 0):
        normalized = normalize_permissions(value)
        assert normalized & (1 << 12), f"bit 13 cleared for {value}"
        assert normalized & 1, f"bit 1 cleared for {value}"
        assert not normalized & (1 << 6), f"bit 7 set for {value}"
        assert not normalized & (1 << 7), f"bit 8 set for {value}"


def test_the_dictionary_names_the_handler_and_carries_recipients(alice):
    data = _sealed([Recipient(alice[0])])

    assert b"/Filter /Adobe.PubSec" in data
    assert b"/SubFilter /adbe.pkcs7.s5" in data
    # The two are asserted apart because the writer sorts dictionary keys for
    # deterministic output, so /R precedes /V and they are not adjacent.
    assert b"/V 5" in data
    assert b"/R 6" in data
    # /V 4 and 5 keep /Recipients inside the crypt filter, not at the top.
    assert b"/CF << /DefaultCryptFilter" in data
    assert b"/Recipients [" in data
    assert b"/StmF /DefaultCryptFilter /StrF /DefaultCryptFilter" in data
    # There is no password, so none of the standard handler's entries appear.
    assert b"/Filter /Standard" not in data
    assert b"/U <" not in data
    assert b"/O <" not in data


def test_aes128_and_rc4_declare_the_older_subfilter(alice):
    assert b"/SubFilter /adbe.pkcs7.s4" in _sealed(
        [Recipient(alice[0])], algorithm="AES-128"
    )
    assert b"/CFM /V2" in _sealed([Recipient(alice[0])], algorithm="RC4")


def test_recipient_blobs_are_written_byte_for_byte(alice):
    """The file key hashes the envelopes, so re-encoding one locks everyone out."""
    document = Document()
    document.pages.add()
    document.encrypt_for_recipients([Recipient(alice[0])])
    envelopes = document._engine_pdf._recipient_envelopes
    data = document._engine_pdf.to_bytes()

    assert envelopes
    for blob in envelopes:
        assert f"<{blob.hex()}>".encode() in data


def test_the_file_key_covers_every_recipient_blob():
    """A reader needs all the envelopes, not only the one it can open."""
    seed = bytes(range(20))
    one = compute_file_key(seed, [b"a"], key_length=32, sha256=True)
    two = compute_file_key(seed, [b"a", b"b"], key_length=32, sha256=True)
    swapped = compute_file_key(seed, [b"b", b"a"], key_length=32, sha256=True)

    assert one != two
    assert two != swapped  # order is part of the hash
    assert len(one) == 32
    assert len(compute_file_key(seed, [b"a"], key_length=16, sha256=False)) == 16


def test_metadata_left_in_the_clear_changes_the_key():
    seed = bytes(range(20))
    assert compute_file_key(
        seed, [b"a"], key_length=32, sha256=True, encrypt_metadata=True
    ) != compute_file_key(
        seed, [b"a"], key_length=32, sha256=True, encrypt_metadata=False
    )


# ---------------------------------------------------------------------------
# The CMS envelope layer
# ---------------------------------------------------------------------------


def test_an_envelope_round_trips_its_seed_and_permissions(alice):
    seed, envelopes = build_envelopes([(alice[0], -3844)])

    payload = open_envelopes(envelopes, alice[0], alice[1])

    assert payload.seed == seed
    assert payload.permissions == normalize_permissions(-3844)


def test_every_recipient_gets_the_same_seed(alice, bob):
    seed, envelopes = build_envelopes([(alice[0], -1), (bob[0], -3844)])

    assert len(envelopes) == 2
    assert open_envelopes(envelopes, alice[0], alice[1]).seed == seed
    assert open_envelopes(envelopes, bob[0], bob[1]).seed == seed


def test_rsa_and_ec_recipients_get_the_same_seed(alice, carol):
    seed, envelopes = build_envelopes([(alice[0], -1), (carol[0], -3844)])

    assert open_envelopes(envelopes, alice[0], alice[1]).seed == seed
    opened = open_envelopes(envelopes, carol[0], carol[1])
    assert opened.seed == seed
    assert opened.permissions == normalize_permissions(-3844)


def _foreign_envelope(certificate, payload: bytes, *, cipher: str) -> bytes:
    """Build a CMS envelope the way another producer would, not our builder.

    Acrobat 5-era files and several enterprise tools still wrap the seed with
    3DES rather than AES, so the read path has to cover both independently of
    what this library writes.
    """
    import os

    from asn1crypto import cms, core
    from asn1crypto import x509 as asn1_x509
    from cryptography.hazmat.primitives import padding as sym_padding
    from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    from aspose_pdf.engine.pubsec import _triple_des

    if cipher == "tripledes":
        content_key, iv, block_bits = os.urandom(24), os.urandom(8), 64
        algorithm = Cipher(_triple_des(content_key), modes.CBC(iv))
        oid = "tripledes_3key"
    else:
        content_key, iv, block_bits = os.urandom(16), os.urandom(16), 128
        algorithm = Cipher(algorithms.AES(content_key), modes.CBC(iv))
        oid = "aes128_cbc"
    padder = sym_padding.PKCS7(block_bits).padder()
    encryptor = algorithm.encryptor()
    body = encryptor.update(padder.update(payload) + padder.finalize())
    body += encryptor.finalize()

    asn1_cert = asn1_x509.Certificate.load(
        certificate.public_bytes(serialization.Encoding.DER)
    )
    encrypted_key = certificate.public_key().encrypt(
        content_key,
        asym_padding.OAEP(
            mgf=asym_padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    ktri = cms.KeyTransRecipientInfo(
        {
            "version": "v0",
            "rid": cms.RecipientIdentifier(
                name="issuer_and_serial_number",
                value=cms.IssuerAndSerialNumber(
                    {
                        "issuer": asn1_cert.issuer,
                        "serial_number": asn1_cert.serial_number,
                    }
                ),
            ),
            "key_encryption_algorithm": {
                "algorithm": "rsaes_oaep",
                "parameters": {
                    "hash_algorithm": {"algorithm": "sha256"},
                    "mask_gen_algorithm": {
                        "algorithm": "mgf1",
                        "parameters": {"algorithm": "sha256"},
                    },
                },
            },
            "encrypted_key": encrypted_key,
        }
    )
    enveloped = cms.EnvelopedData(
        {
            "version": "v0",
            "recipient_infos": cms.RecipientInfos(
                [cms.RecipientInfo(name="ktri", value=ktri)]
            ),
            "encrypted_content_info": {
                "content_type": "data",
                "content_encryption_algorithm": {
                    "algorithm": oid,
                    "parameters": core.OctetString(iv),
                },
                "encrypted_content": body,
            },
        }
    )
    return cms.ContentInfo(
        {"content_type": "enveloped_data", "content": enveloped}
    ).dump()


def _password_envelope(password: str, payload: bytes) -> bytes:
    """Build a PBKDF2/AES-wrap CMS password recipient."""
    import os

    from asn1crypto import algos, cms, core
    from cryptography.hazmat.primitives import keywrap
    from cryptography.hazmat.primitives import padding as sym_padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    content_key = os.urandom(32)
    iv = os.urandom(16)
    padder = sym_padding.PKCS7(128).padder()
    encryptor = Cipher(algorithms.AES(content_key), modes.CBC(iv)).encryptor()
    encrypted_content = encryptor.update(
        padder.update(payload) + padder.finalize()
    ) + encryptor.finalize()

    salt = os.urandom(16)
    iterations = 10_000
    kek = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    ).derive(password.encode("utf-8"))
    kdf = algos.KdfAlgorithm(
        {
            "algorithm": "pbkdf2",
            "parameters": {
                "salt": {"specified": salt},
                "iteration_count": iterations,
                "key_length": 32,
                "prf": {"algorithm": "sha256"},
            },
        }
    )
    recipient_info = cms.RecipientInfo(
        name="pwri",
        value={
            "version": "v0",
            "key_derivation_algorithm": kdf,
            "key_encryption_algorithm": {"algorithm": "aes256_wrap"},
            "encrypted_key": keywrap.aes_key_wrap(kek, content_key),
        },
    )
    enveloped = cms.EnvelopedData(
        {
            "version": "v3",
            "recipient_infos": [recipient_info],
            "encrypted_content_info": {
                "content_type": "data",
                "content_encryption_algorithm": {
                    "algorithm": "aes256_cbc",
                    "parameters": core.OctetString(iv),
                },
                "encrypted_content": encrypted_content,
            },
        }
    )
    return cms.ContentInfo(
        {"content_type": "enveloped_data", "content": enveloped}
    ).dump()


def _password_sealed(password: str, *, permissions: int = -3844) -> bytes:
    import os

    seed = os.urandom(20)
    normalized = normalize_permissions(permissions)
    payload = seed + (normalized & 0xFFFFFFFF).to_bytes(4, "big")
    envelope = _password_envelope(password, payload)

    document = Document()
    document.pages.add().add_text(_TEXT, 72, 700, font_size=14)
    pdf = document._engine_pdf
    pdf._recipient_envelopes = [envelope]
    pdf.encryption_key = compute_file_key(
        seed, [envelope], key_length=32, sha256=True
    )
    pdf.encrypted = True
    pdf.password = ""
    pdf.encryption_algorithm = "AES-256"
    pdf._encryption_algorithm = "AES-256"
    pdf._encryption_key_length = 32
    pdf._encryption_revision = 6
    pdf.P = normalized
    return pdf.to_bytes()


def _kek_envelope(
    key_encryption_key: bytes,
    payload: bytes,
    *,
    key_identifier: bytes = b"document-kek",
) -> bytes:
    """Build an AES-wrap CMS KEK recipient."""
    import os

    from asn1crypto import cms, core
    from cryptography.hazmat.primitives import keywrap
    from cryptography.hazmat.primitives import padding as sym_padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    content_key = os.urandom(32)
    iv = os.urandom(16)
    padder = sym_padding.PKCS7(128).padder()
    encryptor = Cipher(algorithms.AES(content_key), modes.CBC(iv)).encryptor()
    encrypted_content = encryptor.update(
        padder.update(payload) + padder.finalize()
    ) + encryptor.finalize()
    recipient_info = cms.RecipientInfo(
        name="kekri",
        value={
            "version": "v4",
            "kekid": {"key_identifier": key_identifier},
            "key_encryption_algorithm": {"algorithm": "aes256_wrap"},
            "encrypted_key": keywrap.aes_key_wrap(
                key_encryption_key, content_key
            ),
        },
    )
    enveloped = cms.EnvelopedData(
        {
            "version": "v2",
            "recipient_infos": [recipient_info],
            "encrypted_content_info": {
                "content_type": "data",
                "content_encryption_algorithm": {
                    "algorithm": "aes256_cbc",
                    "parameters": core.OctetString(iv),
                },
                "encrypted_content": encrypted_content,
            },
        }
    )
    return cms.ContentInfo(
        {"content_type": "enveloped_data", "content": enveloped}
    ).dump()


def _kek_sealed(
    key_encryption_key: bytes, *, permissions: int = -3844
) -> bytes:
    import os

    seed = os.urandom(20)
    normalized = normalize_permissions(permissions)
    payload = seed + (normalized & 0xFFFFFFFF).to_bytes(4, "big")
    envelope = _kek_envelope(key_encryption_key, payload)

    document = Document()
    document.pages.add().add_text(_TEXT, 72, 700, font_size=14)
    pdf = document._engine_pdf
    pdf._recipient_envelopes = [envelope]
    pdf.encryption_key = compute_file_key(
        seed, [envelope], key_length=32, sha256=True
    )
    pdf.encrypted = True
    pdf.password = ""
    pdf.encryption_algorithm = "AES-256"
    pdf._encryption_algorithm = "AES-256"
    pdf._encryption_key_length = 32
    pdf._encryption_revision = 6
    pdf.P = normalized
    return pdf.to_bytes()


@pytest.mark.parametrize("cipher", ["aes128", "tripledes"])
def test_envelopes_from_other_producers_are_opened(alice, cipher):
    """OAEP key transport and a 3DES content cipher, as older tools write them."""
    payload = bytes(range(20)) + (0xFFFFF0C1).to_bytes(4, "big")
    blob = _foreign_envelope(alice[0], payload, cipher=cipher)

    opened = open_envelopes([blob], alice[0], alice[1])

    assert opened.seed == payload[:20]
    assert opened.permissions == int.from_bytes(payload[20:], "big", signed=True)


def test_password_recipient_envelope_is_opened():
    payload = bytes(range(20)) + (0xFFFFF0C1).to_bytes(4, "big")
    blob = _password_envelope("correct horse", payload)

    opened = open_password_envelopes([blob], "correct horse")

    assert opened.seed == payload[:20]
    assert opened.permissions == int.from_bytes(payload[20:], "big", signed=True)


def test_password_recipient_document_round_trips(tmp_path):
    path = tmp_path / "password-recipient.pdf"
    data = _password_sealed("correct horse")
    path.write_bytes(data)

    assert _TEXT.encode() not in data
    with Document(path, password="correct horse") as document:
        assert _TEXT.encode() in document.pages[0].content
        assert document.permissions == normalize_permissions(-3844)

    with pytest.raises(PdfSecurityException, match="supplied password"):
        Document(path, password="wrong horse")


def test_password_recipient_document_requires_a_credential(tmp_path):
    path = tmp_path / "password-recipient.pdf"
    path.write_bytes(_password_sealed("correct horse"))

    with pytest.raises(PdfSecurityException, match="CMS password recipient"):
        Document(path)


def test_kek_recipient_envelope_is_opened():
    key_encryption_key = bytes(range(32))
    payload = bytes(range(20)) + (0xFFFFF0C1).to_bytes(4, "big")
    blob = _kek_envelope(key_encryption_key, payload)

    opened = open_kek_envelopes([blob], key_encryption_key)

    assert opened.seed == payload[:20]
    assert opened.permissions == int.from_bytes(payload[20:], "big", signed=True)


def test_kek_recipient_document_round_trips(tmp_path):
    key_encryption_key = bytes(range(32))
    path = tmp_path / "kek-recipient.pdf"
    data = _kek_sealed(key_encryption_key)
    path.write_bytes(data)

    assert _TEXT.encode() not in data
    with Document(path, key_encryption_key=key_encryption_key) as document:
        assert _TEXT.encode() in document.pages[0].content
        assert document.permissions == normalize_permissions(-3844)

    with pytest.raises(PdfSecurityException, match="key-encryption key"):
        Document(path, key_encryption_key=key_encryption_key[::-1])


def test_kek_credential_rejects_invalid_combinations(alice):
    with pytest.raises(PdfValidationException, match="cannot be combined"):
        Document(
            b"%PDF-1.4\n",
            certificate=alice[0],
            private_key=alice[1],
            key_encryption_key=bytes(32),
        )

    with pytest.raises(PdfValidationException, match="16, 24, or 32"):
        Document(b"%PDF-1.4\n", key_encryption_key=b"short")


def test_an_unsupported_content_cipher_is_named_in_the_error(alice):
    from asn1crypto import cms

    blob = _foreign_envelope(alice[0], b"\x00" * 24, cipher="aes128")
    info = cms.ContentInfo.load(blob)
    # RC2-CBC: what Acrobat 5 produced, and what no supported backend decrypts.
    info["content"]["encrypted_content_info"]["content_encryption_algorithm"][
        "algorithm"
    ] = "1.2.840.113549.3.2"

    with pytest.raises(PdfSecurityException, match="rc2"):
        open_envelopes([info.dump()], alice[0], alice[1])


def test_encrypting_needs_a_recipient(alice):
    document = Document()
    document.pages.add()

    with pytest.raises(PdfSecurityException, match="at least one recipient"):
        document.encrypt_for_recipients([])


def test_a_signing_only_certificate_is_refused_by_default(signing_only):
    document = Document()
    document.pages.add()

    with pytest.raises(PdfSecurityException, match="keyUsage"):
        document.encrypt_for_recipients([signing_only[0]])

    # ...but a caller who knows better can insist.
    document.encrypt_for_recipients([signing_only[0]], ignore_key_usage=True)
    assert document._engine_pdf._recipient_envelopes


def test_ec_recipient_needs_key_agreement_usage():
    certificate, _key = _make_ec_cert("No agreement", key_agreement=False)
    document = Document()
    document.pages.add()

    with pytest.raises(PdfSecurityException, match="keyAgreement"):
        document.encrypt_for_recipients([certificate])


def test_certificate_and_key_must_arrive_together(alice, tmp_path):
    path = tmp_path / "sealed.pdf"
    path.write_bytes(_sealed([Recipient(alice[0])]))

    with pytest.raises(PdfValidationException, match="supplied together"):
        Document(path, certificate=alice[0])
    with pytest.raises(PdfValidationException, match="supplied together"):
        Document(path, private_key=alice[1])


def test_credentials_need_a_source(alice):
    with pytest.raises(TypeError, match="require a load source"):
        Document(certificate=alice[0], private_key=alice[1])


def test_resaving_keeps_the_document_readable_by_its_recipients(alice, bob, tmp_path):
    """A re-save must not silently downgrade the handler.

    The writer's encrypted path knows only the standard handler, so a
    public-key document routed through it would come back out as
    ``/Filter /Standard`` with empty ``/O`` and ``/U`` -- a file nobody,
    this library included, could open again.
    """
    source = tmp_path / "sealed.pdf"
    source.write_bytes(
        _sealed([Recipient(alice[0]), Recipient(bob[0], permissions=-3844)])
    )
    target = tmp_path / "resaved.pdf"

    with Document(source, certificate=alice[0], private_key=alice[1]) as document:
        document.save(target)

    data = target.read_bytes()
    assert b"/Filter /Adobe.PubSec" in data
    assert b"/Filter /Standard" not in data
    assert _TEXT.encode() not in data

    for certificate, key, expected in (
        (alice[0], alice[1], normalize_permissions(-1)),
        (bob[0], bob[1], normalize_permissions(-3844)),
    ):
        with Document(target, certificate=certificate, private_key=key) as reopened:
            assert _TEXT.encode() in reopened.pages[0].content
            assert reopened.permissions == expected


# ---------------------------------------------------------------------------
# Malformed input
# ---------------------------------------------------------------------------


def test_a_public_key_dictionary_without_recipients_is_rejected(alice, tmp_path):
    data = _sealed([Recipient(alice[0])])
    # Rename the entry so the handler is still /Adobe.PubSec but the recipient
    # list can no longer be found.
    broken = data.replace(b"/Recipients [", b"/Recipientz [", 1)
    path = tmp_path / "broken.pdf"
    path.write_bytes(broken)

    with pytest.raises(PdfSecurityException, match="no /Recipients"):
        Document(path, certificate=alice[0], private_key=alice[1])


def test_a_truncated_envelope_is_rejected(alice, tmp_path):
    _seed, envelopes = build_envelopes([(alice[0], -1)])

    with pytest.raises(PdfSecurityException):
        open_envelopes([envelopes[0][:100]], alice[0], alice[1])


def test_lazy_open_takes_the_same_credential(alice, tmp_path):
    path = tmp_path / "sealed.pdf"
    path.write_bytes(_sealed([Recipient(alice[0])]))

    pdf = SimplePdf.from_file_lazy(path, credential=(alice[0], alice[1]))
    try:
        assert len(pdf.pages) == 1
    finally:
        pdf.dispose()


def test_certificates_are_matched_by_issuer_and_serial(alice, bob):
    """Two certificates with the same subject must not be confused."""
    seed, envelopes = build_envelopes([(alice[0], -1)])
    alice_der = alice[0].public_bytes(serialization.Encoding.DER)
    bob_der = bob[0].public_bytes(serialization.Encoding.DER)

    assert alice_der != bob_der
    assert open_envelopes(envelopes, alice[0], alice[1]).seed == seed
    with pytest.raises(PdfSecurityException, match="not a recipient"):
        open_envelopes(envelopes, bob[0], bob[1])


def test_a_password_does_not_stand_between_a_session_and_its_own_recipients(alice):
    # Protection this session set for certificates is not a password's to
    # unlock: taking it off again needs no password, whatever string is passed
    # -- as before the password check on `decrypt` was made real.
    document = Document()
    document.pages.add().add_text(_TEXT, 72, 700, font_size=14)
    document.encrypt_for_recipients([Recipient(alice[0])])
    document.decrypt("any string at all")
    assert b"/Encrypt" not in document._engine_pdf.to_bytes()
