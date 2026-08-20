"""Interoperability of the standard security handler.

The ``fixtures_encrypted_*.pdf`` files were produced by qpdf 12.3.2 (through
pikepdf) from the same one-page source, each with user password ``user`` and
owner password ``owner``, and each page draws the text ``SECRET42``. They pin
the read side against a third-party producer: every ``/V``, ``/R`` and crypt
filter combination the standard handler defines.

The write side is checked the other way round -- this module derives the file
key straight from the spec (ISO 32000-1 algorithms 2, 3.2, 3.5 and 1, ISO
32000-2 algorithm 2.B) and decrypts what the writer produced, so a file that
only this library can read fails the test.
"""

from __future__ import annotations

import hashlib
import re
import struct
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from aspose_pdf import Document
from aspose_pdf.engine.encryption import PDF_PADDING, EncryptionUtils
from aspose_pdf.exceptions import PdfSecurityException

FIXTURES = Path(__file__).parent

READABLE = [
    ("fixtures_encrypted_rc4_40.pdf", "RC4 40-bit, /V 1 /R 2"),
    ("fixtures_encrypted_rc4_128.pdf", "RC4 128-bit, /V 2 /R 3"),
    ("fixtures_encrypted_aes_128.pdf", "AES-128, /V 4 /R 4 AESV2"),
    ("fixtures_encrypted_aes_256_r5.pdf", "AES-256, /V 5 /R 5"),
    ("fixtures_encrypted_aes_256_r6.pdf", "AES-256, /V 5 /R 6"),
]


# ---------------------------------------------------------------------------
# Reading documents written by another producer
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name,label", READABLE)
@pytest.mark.parametrize("password", ["user", "owner"])
def test_third_party_encrypted_pdf_opens(name: str, label: str, password: str) -> None:
    with Document(FIXTURES / name, password=password) as document:
        assert b"SECRET42" in document.pages[0].content, label


@pytest.mark.parametrize("name,label", READABLE)
def test_third_party_encrypted_pdf_rejects_wrong_password(name: str, label: str) -> None:
    with pytest.raises(PdfSecurityException):
        Document(FIXTURES / name, password="not-the-password")


def test_owner_only_document_opens_without_a_password() -> None:
    """An empty user password is a password: no reader asks for one."""
    with Document(FIXTURES / "fixtures_encrypted_owner_only.pdf") as document:
        assert b"SECRET42" in document.pages[0].content


def test_owner_only_document_opens_with_the_owner_password() -> None:
    path = FIXTURES / "fixtures_encrypted_owner_only.pdf"
    with Document(path, password="owner") as document:
        assert b"SECRET42" in document.pages[0].content


def test_encrypted_document_renders_its_images() -> None:
    """Images live in the COS graph and are decoded long after load.

    ``Document(..., password=...)`` decrypts the page contents up front and
    clears the writer-facing key; the key the *graph* still needs has to
    survive, or every image and form XObject silently disappears from a render.
    """
    with Document(FIXTURES / "fixtures_encrypted_image.pdf", password="user") as doc:
        raster = doc.pages[0].render(dpi=36)
    red = sum(
        1
        for i in range(0, len(raster.pixels), 3)
        if raster.pixels[i] > 200
        and raster.pixels[i + 1] < 80
        and raster.pixels[i + 2] < 80
    )
    assert red > 100, "the embedded red image was not painted"


# ---------------------------------------------------------------------------
# Writing documents another producer can read
# ---------------------------------------------------------------------------
def _rc4(key: bytes, data: bytes) -> bytes:
    """Textbook RC4, independent of the library's implementation."""
    state = list(range(256))
    j = 0
    for i in range(256):
        j = (j + state[i] + key[i % len(key)]) % 256
        state[i], state[j] = state[j], state[i]
    out = bytearray()
    i = j = 0
    for byte in data:
        i = (i + 1) % 256
        j = (j + state[i]) % 256
        state[i], state[j] = state[j], state[i]
        out.append(byte ^ state[(state[i] + state[j]) % 256])
    return bytes(out)


def _file_key_v4(password: bytes, o_value: bytes, p: int, id0: bytes, n: int) -> bytes:
    """ISO 32000-1 Algorithm 3.2 for revision 3 and 4 with /EncryptMetadata true."""
    padded = (password + PDF_PADDING)[:32]
    digest = hashlib.md5(padded + o_value + struct.pack("<i", p) + id0).digest()
    for _ in range(50):
        digest = hashlib.md5(digest[:n]).digest()
    return digest[:n]


def _object_key(file_key: bytes, obj_num: int, aes: bool) -> bytes:
    """ISO 32000-1 Algorithm 1."""
    extended = file_key + bytes(
        [obj_num & 0xFF, (obj_num >> 8) & 0xFF, (obj_num >> 16) & 0xFF, 0, 0]
    )
    if aes:
        extended += b"sAlT"
    return hashlib.md5(extended).digest()[: min(len(file_key) + 5, 16)]


def _aes_cbc_decrypt(key: bytes, data: bytes) -> bytes:
    decryptor = Cipher(algorithms.AES(key), modes.CBC(data[:16])).decryptor()
    plain = decryptor.update(data[16:]) + decryptor.finalize()
    return plain[: -plain[-1]] if plain else plain


def _encrypt_dict(data: bytes) -> dict[str, bytes]:
    """Pull the fields this test needs out of the /Encrypt dictionary."""
    fields = {}
    for key in ("O", "U", "UE", "OE", "Perms"):
        match = re.search(rb"/" + key.encode() + rb" <([0-9a-fA-F]*)>", data)
        if match:
            fields[key] = bytes.fromhex(match.group(1).decode())
    fields["ID"] = bytes.fromhex(
        re.search(rb"/ID \[<([0-9a-fA-F]*)>", data).group(1).decode()
    )
    fields["P"] = int(re.search(rb"/P (-?\d+)", data).group(1))
    return fields


def _first_content_stream(data: bytes) -> tuple[int, bytes]:
    """Return (object number, raw stream bytes) of the first content stream."""
    for match in re.finditer(rb"(\d+) 0 obj\s*<< /Length (\d+) >>\s*stream\r?\n", data):
        obj_num = int(match.group(1))
        length = int(match.group(2))
        start = match.end()
        return obj_num, data[start : start + length]
    raise AssertionError("no content stream found")


def _write_encrypted(tmp_path: Path, algorithm: str) -> bytes:
    document = Document()
    page = document.pages.add()
    page.add_text("SECRET42", 20, 20)
    document.info = {"Title": "Encrypted title"}
    document.encrypt("user", "owner", algorithm=algorithm)
    out = tmp_path / f"{algorithm}.pdf"
    document.save(out)
    document.dispose()
    return out.read_bytes()


def test_written_rc4_is_readable_from_the_specification(tmp_path: Path) -> None:
    data = _write_encrypted(tmp_path, "RC4")
    assert b"/V 2" in data and b"/R 3" in data and b"/Length 128" in data
    fields = _encrypt_dict(data)
    file_key = _file_key_v4(b"user", fields["O"], fields["P"], fields["ID"], 16)
    obj_num, stream = _first_content_stream(data)
    assert b"SECRET42" not in stream
    plain = _rc4(_object_key(file_key, obj_num, aes=False), stream)
    assert b"SECRET42" in plain


def test_written_aes_128_is_readable_from_the_specification(tmp_path: Path) -> None:
    data = _write_encrypted(tmp_path, "AES-128")
    assert b"/V 4" in data and b"/R 4" in data and b"/AESV2" in data
    fields = _encrypt_dict(data)
    file_key = _file_key_v4(b"user", fields["O"], fields["P"], fields["ID"], 16)
    obj_num, stream = _first_content_stream(data)
    assert b"SECRET42" not in stream
    plain = _aes_cbc_decrypt(_object_key(file_key, obj_num, aes=True), stream)
    assert b"SECRET42" in plain


def test_written_aes_256_declares_revision_6_and_validates(tmp_path: Path) -> None:
    data = _write_encrypted(tmp_path, "AES-256")
    assert b"/V 5" in data and b"/R 6" in data and b"/AESV3" in data
    fields = _encrypt_dict(data)
    u_value = fields["U"]
    # Revision 6 validates the password with the hardened hash of algorithm 2.B;
    # a document that declared /R 5 would be checked with a plain SHA-256 and
    # would not open in a conforming reader.
    assert EncryptionUtils.compute_hash_v5(b"user", u_value[32:40]) == u_value[:32]
    assert hashlib.sha256(b"user" + u_value[32:40]).digest() != u_value[:32]
    # /Perms is required by revision 6 and carries the permissions again.
    assert "Perms" in fields
    key = EncryptionUtils.verify_password_v6(
        "user", fields["U"], fields["O"], fields["UE"], fields["OE"]
    )
    assert key is not None
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()  # noqa: S305
    perms = decryptor.update(fields["Perms"]) + decryptor.finalize()
    assert perms[9:12] == b"adb"
    assert struct.unpack("<i", perms[:4])[0] == fields["P"]


@pytest.mark.parametrize("algorithm", ["RC4", "AES-128", "AES-256"])
def test_encrypted_strings_round_trip(tmp_path: Path, algorithm: str) -> None:
    data = _write_encrypted(tmp_path, algorithm)
    # The /Info title is a string, and strings are encrypted too.
    assert b"Encrypted title" not in data
    out = tmp_path / f"roundtrip-{algorithm}.pdf"
    out.write_bytes(data)
    with Document(out, password="user") as document:
        assert document.info.get("Title") == "Encrypted title"
        assert b"SECRET42" in document.pages[0].content


@pytest.mark.parametrize("algorithm", ["RC4", "AES-128", "AES-256"])
def test_written_document_binds_the_key_to_the_trailer_id(
    tmp_path: Path, algorithm: str
) -> None:
    """/ID[0] feeds the key derivation, so it cannot be regenerated on save."""
    document = Document()
    document.pages.add()
    document.encrypt("user", "owner", algorithm=algorithm)
    out = tmp_path / "id.pdf"
    document.save(out)
    derivation_id = document._engine_pdf._file_id
    document.dispose()
    trailer_id = bytes.fromhex(
        re.search(rb"/ID \[<([0-9a-fA-F]*)>", out.read_bytes()).group(1).decode()
    )
    assert trailer_id == derivation_id


def test_unknown_algorithm_is_rejected() -> None:
    document = Document()
    document.pages.add()
    with pytest.raises(PdfSecurityException, match="Unsupported encryption algorithm"):
        document.encrypt("user", algorithm="DES")
    document.dispose()


@pytest.mark.parametrize(
    "spelling,expected",
    [("aes256", "AES-256"), ("AES_128", "AES-128"), ("rc4-128", "RC4")],
)
def test_algorithm_spellings_normalize(spelling: str, expected: str) -> None:
    document = Document()
    document.pages.add()
    document.encrypt("user", algorithm=spelling)
    assert document._engine_pdf.encryption_algorithm == expected
    document.dispose()
