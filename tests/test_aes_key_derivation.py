# AES Key Derivation Tests - PDF ISO 32000 Compliant
"""Comprehensive test suite for PDF-compliant AES key derivation utilities.

Tests cover:
- PDF_PADDING constant verification
- V4 key derivation (compute_owner_key_v4, compute_user_key_v4)
- Password verification (verify_password_v4)
- AES encryption/decryption roundtrips with derived keys
"""

import os
import pytest
from aspose_pdf.engine.encryption import EncryptionUtils, PDF_PADDING


@pytest.fixture(scope="function")
def random_file_id():
    """Return a 16-byte random file identifier."""
    return os.urandom(16)


@pytest.fixture(scope="function")
def sample_permissions():
    """Return typical permissions integer for PDF encryption."""
    return -4  # Standard PDF default permissions


# =============================================================================
# Basic Tests
# =============================================================================


def test_pdf_padding_constant():
    """Check that PDF_PADDING matches PDF specification."""
    assert isinstance(PDF_PADDING, (bytes, bytearray)), "PDF_PADDING should be bytes"
    assert len(PDF_PADDING) == 32, "PDF_PADDING must be 32 bytes long"
    assert PDF_PADDING[:4] == bytes([0x28, 0xBF, 0x4E, 0x5E]), (
        "First four bytes incorrect"
    )


def test_invalid_key_length_raises():
    """EncryptionUtils should reject invalid AES key lengths."""
    with pytest.raises(Exception, match="AES key must be 16, 24, or 32 bytes"):
        EncryptionUtils.encrypt_aes_cbc(b"short", b"data")
    with pytest.raises(Exception, match="AES key must be 16, 24, or 32 bytes"):
        EncryptionUtils.decrypt_aes_cbc(b"short", b"X" * 20)


# =============================================================================
# AES Encryption/Decryption with Derived Keys
# =============================================================================


def test_encrypt_decrypt_aes128_roundtrip():
    """Round-trip encryption using 128-bit AES key."""
    key = os.urandom(16)
    plaintext = b"Hello, PDF AES 128!"
    ciphertext = EncryptionUtils.encrypt_aes_cbc(key, plaintext)
    decrypted = EncryptionUtils.decrypt_aes_cbc(key, ciphertext)
    assert decrypted == plaintext


def test_encrypt_decrypt_aes256_roundtrip():
    """Round-trip encryption using 256-bit AES key."""
    key = os.urandom(32)
    plaintext = b"Hello, PDF AES 256!"
    ciphertext = EncryptionUtils.encrypt_aes_cbc(key, plaintext)
    decrypted = EncryptionUtils.decrypt_aes_cbc(key, ciphertext)
    assert decrypted == plaintext


def test_empty_data_aes():
    """AES encryption/decryption works with empty data."""
    key = os.urandom(16)
    plaintext = b""
    ciphertext = EncryptionUtils.encrypt_aes_cbc(key, plaintext)
    decrypted = EncryptionUtils.decrypt_aes_cbc(key, ciphertext)
    assert decrypted == plaintext


# =============================================================================
# PDF V4 Key Derivation Tests
# =============================================================================


def test_compute_owner_key_v4_produces_32_bytes():
    """compute_owner_key_v4 returns 32-byte O value."""
    o_value = EncryptionUtils.compute_owner_key_v4(
        owner_password="owner", user_password="user", key_length=16, revision=4
    )
    assert len(o_value) == 32, "O value must be 32 bytes"


def test_compute_owner_key_v4_different_passwords():
    """Different owner passwords produce different O values."""
    o1 = EncryptionUtils.compute_owner_key_v4("owner1", "user", 16, 4)
    o2 = EncryptionUtils.compute_owner_key_v4("owner2", "user", 16, 4)
    assert o1 != o2, "Different passwords should produce different O values"


def test_compute_owner_key_v4_empty_owner_uses_user():
    """Empty owner password defaults to user password."""
    o1 = EncryptionUtils.compute_owner_key_v4("", "user", 16, 4)
    o2 = EncryptionUtils.compute_owner_key_v4("user", "user", 16, 4)
    assert o1 == o2, "Empty owner password should use user password"


def test_compute_user_key_v4_returns_tuple(random_file_id):
    """compute_user_key_v4 returns (U value, encryption key)."""
    o_value = EncryptionUtils.compute_owner_key_v4("owner", "user", 16, 4)
    u_value, enc_key = EncryptionUtils.compute_user_key_v4(
        password="user",
        o_value=o_value,
        p_value=-4,
        file_id=random_file_id,
        key_length=16,
        revision=4,
    )
    assert len(u_value) == 32, "U value must be 32 bytes"
    assert len(enc_key) == 16, "Encryption key must be 16 bytes for AES-128"


def test_compute_user_key_v4_key_length_affects_result(random_file_id):
    """Different key lengths produce different results."""
    o_value = EncryptionUtils.compute_owner_key_v4("owner", "user", 16, 4)
    _, key16 = EncryptionUtils.compute_user_key_v4(
        "user", o_value, -4, random_file_id, 16, 4
    )

    o_value5 = EncryptionUtils.compute_owner_key_v4("owner", "user", 5, 2)
    _, key5 = EncryptionUtils.compute_user_key_v4(
        "user", o_value5, -4, random_file_id, 5, 2
    )

    assert len(key16) == 16
    assert len(key5) == 5


def test_file_id_affects_derived_key(random_file_id):
    """Different file IDs produce different encryption keys."""
    file_id1 = os.urandom(16)
    file_id2 = os.urandom(16)

    o_value = EncryptionUtils.compute_owner_key_v4("owner", "user", 16, 4)

    _, key1 = EncryptionUtils.compute_user_key_v4("user", o_value, -4, file_id1, 16, 4)
    _, key2 = EncryptionUtils.compute_user_key_v4("user", o_value, -4, file_id2, 16, 4)

    assert key1 != key2, "Different file IDs should produce different keys"


def test_permissions_affect_derived_key(random_file_id):
    """Different permissions produce different encryption keys."""
    o_value = EncryptionUtils.compute_owner_key_v4("owner", "user", 16, 4)

    _, key1 = EncryptionUtils.compute_user_key_v4(
        "user", o_value, -4, random_file_id, 16, 4
    )
    _, key2 = EncryptionUtils.compute_user_key_v4(
        "user", o_value, -100, random_file_id, 16, 4
    )

    assert key1 != key2, "Different permissions should produce different keys"


# =============================================================================
# Password Verification Tests
# =============================================================================


def test_verify_password_v4_correct_user_password(random_file_id):
    """Correct user password returns valid encryption key."""
    user_pwd = "testuser"
    o_value = EncryptionUtils.compute_owner_key_v4("owner", user_pwd, 16, 4)
    u_value, expected_key = EncryptionUtils.compute_user_key_v4(
        user_pwd, o_value, -4, random_file_id, 16, 4
    )

    verified_key = EncryptionUtils.verify_password_v4(
        user_pwd, u_value, o_value, -4, random_file_id, 16, 4
    )

    assert verified_key == expected_key, "Correct password should return encryption key"


def test_verify_password_v4_wrong_password_returns_none(random_file_id):
    """Wrong password returns None."""
    o_value = EncryptionUtils.compute_owner_key_v4("owner", "user", 16, 4)
    u_value, _ = EncryptionUtils.compute_user_key_v4(
        "user", o_value, -4, random_file_id, 16, 4
    )

    result = EncryptionUtils.verify_password_v4(
        "wrong", u_value, o_value, -4, random_file_id, 16, 4
    )

    assert result is None, "Wrong password should return None"


def test_verify_password_v4_empty_password(random_file_id):
    """Empty password verification works."""
    o_value = EncryptionUtils.compute_owner_key_v4("owner", "", 16, 4)
    u_value, expected_key = EncryptionUtils.compute_user_key_v4(
        "", o_value, -4, random_file_id, 16, 4
    )

    verified_key = EncryptionUtils.verify_password_v4(
        "", u_value, o_value, -4, random_file_id, 16, 4
    )

    assert verified_key == expected_key, "Empty password verification should work"


# =============================================================================
# Integration Tests
# =============================================================================


def test_full_encryption_roundtrip_aes128(random_file_id):
    """Full roundtrip: derive keys, encrypt data, decrypt data."""
    user_pwd = "mypassword"

    # Derive keys
    o_value = EncryptionUtils.compute_owner_key_v4("owner", user_pwd, 16, 4)
    u_value, enc_key = EncryptionUtils.compute_user_key_v4(
        user_pwd, o_value, -4, random_file_id, 16, 4
    )

    # Encrypt some data
    plaintext = b"Confidential PDF content"
    ciphertext = EncryptionUtils.encrypt_aes_cbc(enc_key, plaintext)

    # Verify password and decrypt
    verified_key = EncryptionUtils.verify_password_v4(
        user_pwd, u_value, o_value, -4, random_file_id, 16, 4
    )
    assert verified_key is not None

    decrypted = EncryptionUtils.decrypt_aes_cbc(verified_key, ciphertext)
    assert decrypted == plaintext


def test_object_key_derivation():
    """Object-specific key derivation produces 16-byte keys."""
    file_key = os.urandom(16)

    obj_key = EncryptionUtils.derive_object_key(file_key, 10, 0, use_aes=True)
    assert len(obj_key) == 16, "AES object key must be 16 bytes"

    # Different objects get different keys
    obj_key2 = EncryptionUtils.derive_object_key(file_key, 20, 0, use_aes=True)
    assert obj_key != obj_key2


def test_generate_file_id():
    """generate_file_id produces unique 16-byte IDs."""
    id1 = EncryptionUtils.generate_file_id()
    id2 = EncryptionUtils.generate_file_id()

    assert len(id1) == 16
    assert len(id2) == 16
    assert id1 != id2


# =============================================================================
# Revision-specific Tests
# =============================================================================


def test_revision_2_key_derivation(random_file_id):
    """R2 key derivation (40-bit RC4) works correctly."""
    o_value = EncryptionUtils.compute_owner_key_v4("owner", "user", 5, revision=2)
    u_value, key = EncryptionUtils.compute_user_key_v4(
        "user", o_value, -4, random_file_id, 5, revision=2
    )

    assert len(key) == 5, "R2 should produce 40-bit (5-byte) key"
    assert len(u_value) == 32, "U value always 32 bytes"


def test_revision_3_key_derivation(random_file_id):
    """R3 key derivation (128-bit) works correctly."""
    o_value = EncryptionUtils.compute_owner_key_v4("owner", "user", 16, revision=3)
    u_value, key = EncryptionUtils.compute_user_key_v4(
        "user", o_value, -4, random_file_id, 16, revision=3
    )

    assert len(key) == 16, "R3 should produce 128-bit (16-byte) key"
