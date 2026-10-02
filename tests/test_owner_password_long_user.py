"""The owner password works whatever the user password's length.

Algorithm 7 recovers the **padded** user password from `/O` -- always exactly 32
bytes -- and the padded form is what the key computation takes. The code used to
turn it back into plaintext first by searching for the two bytes the padding
begins with:

    padding_idx = decoded.find("\\x28\\xbf")
    if padding_idx > 0:
        user_pwd = decoded[:padding_idx]

A 31-byte user password leaves exactly **one** padding byte and a 32-byte or
longer one leaves **none**, so the search failed, the recovered password fell
back to the empty string, the derived key was wrong, and the owner password was
refused. Every revision <= 4 handler was affected -- RC4 40-bit, RC4 128-bit and
AES-128 -- and not only opening: `decrypt()` and `change_passwords()` failed the
same way, so the owner could neither remove nor rotate the password of a
document this library had itself written. AES-256 was never affected: R5/R6
check the owner password against `/OE` and recover nothing.

qpdf, pdfium and MuPDF all open those files with the owner password, at every
length, so the `/O` written was right all along and only the reading was wrong.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aspose_pdf import Document
from aspose_pdf.exceptions import PdfSecurityException

FIXTURES = Path(__file__).parent

# 30 is the last length the old code handled, 31 leaves one padding byte and 32
# leaves none; 40 and 200 are past the point where a revision <= 4 handler
# truncates.
LENGTHS = [8, 30, 31, 32, 40, 127, 200]
ALGORITHMS = ["RC4", "AES-128", "AES-256"]


def _encrypted(tmp_path, algorithm: str, user: str, owner: str = "owner") -> Path:
    document = Document()
    document.pages.add()
    document.pages[0].add_text("SECRET42", 50, 700)
    document.encrypt(user, owner, algorithm=algorithm)
    path = tmp_path / f"{algorithm}-{len(user)}.pdf"
    document.save(path)
    return path


@pytest.mark.parametrize("algorithm", ALGORITHMS)
@pytest.mark.parametrize("length", LENGTHS)
def test_the_owner_password_opens_it(tmp_path, algorithm, length):
    path = _encrypted(tmp_path, algorithm, "x" * length)
    with Document(path, password="owner") as document:
        assert "SECRET42" in document.pages[0].extract_text()


@pytest.mark.parametrize("algorithm", ALGORITHMS)
@pytest.mark.parametrize("length", LENGTHS)
def test_the_user_password_still_opens_it(tmp_path, algorithm, length):
    path = _encrypted(tmp_path, algorithm, "x" * length)
    with Document(path, password="x" * length) as document:
        assert "SECRET42" in document.pages[0].extract_text()


@pytest.mark.parametrize("algorithm", ALGORITHMS)
@pytest.mark.parametrize("length", [31, 40])
def test_the_owner_can_remove_and_rotate_the_password(tmp_path, algorithm, length):
    path = _encrypted(tmp_path, algorithm, "x" * length)
    Document(path, password="owner").decrypt("owner")
    Document(path, password="owner").change_passwords("owner", "new-user")


@pytest.mark.parametrize("algorithm", ["RC4", "AES-128"])
def test_a_user_password_containing_the_paddings_first_bytes(tmp_path, algorithm):
    # The old search would have cut the recovered password here.
    user = "ab" + chr(0x28) + chr(0xBF) + "cd"
    path = _encrypted(tmp_path, algorithm, user)
    for password in ("owner", user):
        with Document(path, password=password) as document:
            assert "SECRET42" in document.pages[0].extract_text()


@pytest.mark.parametrize("algorithm", ALGORITHMS)
@pytest.mark.parametrize("length", [8, 31, 40])
@pytest.mark.parametrize(
    "wrong", ["", "owne", "ownerr", "OWNER", "user", "nowhere-near-it"]
)
def test_a_wrong_password_is_still_refused(tmp_path, algorithm, length, wrong):
    # The fix must not make the check more permissive.
    path = _encrypted(tmp_path, algorithm, "x" * length)
    with pytest.raises(PdfSecurityException):
        Document(path, password=wrong) if wrong else Document(path)


def test_a_password_past_the_truncation_point_is_the_same_password(tmp_path):
    """Revision <= 4 sees only the first 32 bytes, and so do qpdf and MuPDF.

    Measured on our own file: qpdf and MuPDF open a 40-``x`` document with 39,
    41 and 32 ``x``\\ s and refuse 31 of them, which is this equivalence. It is
    the spec's (Algorithm 2 pads *or truncates* to 32 bytes), not a weakness, and
    it is worth pinning so nobody "fixes" it.
    """
    path = _encrypted(tmp_path, "RC4", "x" * 40)
    for same in ("x" * 39, "x" * 40, "x" * 41, "x" * 32):
        with Document(path, password=same) as document:
            assert "SECRET42" in document.pages[0].extract_text()
    with pytest.raises(PdfSecurityException):
        Document(path, password="x" * 31)


def test_the_owner_check_is_not_satisfied_by_a_near_miss():
    """A wrong owner password stays wrong, however close its derived /U lands.

    The check compares 16 bytes of the computed ``/U`` (32 for revision 2).
    Narrowing that to one byte would let roughly one wrong password in 256
    through, which a handful of attempts would not notice -- so this tries
    enough of them that such a weakening cannot pass unseen, and does it through
    the key functions rather than by loading 1500 documents.
    """
    from aspose_pdf.engine.encryption import EncryptionUtils

    user, owner = "x" * 40, "owner"
    file_id, permissions, key_length, revision = b"\x01" * 16, -4, 16, 3
    o_value = EncryptionUtils.compute_owner_key_v4(owner, user, key_length, revision)
    u_value, _ = EncryptionUtils.compute_user_key_v4(
        user, o_value, permissions, file_id, key_length, revision, True
    )

    def verify(password):
        return EncryptionUtils._verify_owner_password(
            password, u_value, o_value, permissions, file_id,
            key_length, revision, True,
        )

    assert verify(owner) is not None          # the owner password works
    assert verify(user) is None               # the user password is not an owner one
    assert [f"wrong-{i}" for i in range(1500) if verify(f"wrong-{i}") is not None] == []


@pytest.mark.parametrize(
    "name",
    [
        "fixtures_encrypted_rc4_40.pdf",
        "fixtures_encrypted_rc4_128.pdf",
        "fixtures_encrypted_aes_128.pdf",
        "fixtures_encrypted_aes_256_r5.pdf",
        "fixtures_encrypted_aes_256_r6.pdf",
    ],
)
@pytest.mark.parametrize("password", ["user", "owner"])
def test_the_third_party_fixtures_are_unaffected(name, password):
    with Document(FIXTURES / name, password=password) as document:
        assert b"SECRET42" in document.pages[0].content
