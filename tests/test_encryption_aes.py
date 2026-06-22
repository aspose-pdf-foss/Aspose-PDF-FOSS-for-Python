import os
import pytest
from aspose_pdf.engine.encryption import EncryptionUtils


def test_aes_cbc_roundtrip():
    key = os.urandom(16)
    data = b"sample data for AES encryption"
    encrypted = EncryptionUtils.encrypt_aes_cbc(key, data)
    decrypted = EncryptionUtils.decrypt_aes_cbc(key, encrypted)
    assert decrypted == data


def test_aes_key_sizes():
    for key_len in (24, 32):
        key = os.urandom(key_len)
        data = b"test data"
        encrypted = EncryptionUtils.encrypt_aes_cbc(key, data)
        decrypted = EncryptionUtils.decrypt_aes_cbc(key, encrypted)
        assert decrypted == data


def test_aes_random_iv():
    key = os.urandom(16)
    data = b"repeatable data"
    encrypted1 = EncryptionUtils.encrypt_aes_cbc(key, data)
    encrypted2 = EncryptionUtils.encrypt_aes_cbc(key, data)
    assert encrypted1 != encrypted2


def test_aes_invalid_key_length():
    key = os.urandom(10)
    data = b"data"
    with pytest.raises(Exception):
        EncryptionUtils.encrypt_aes_cbc(key, data)


def test_rc4_roundtrip():
    key = os.urandom(16)
    data = b"RC4 test data"
    encrypted = EncryptionUtils.encrypt_rc4(key, data)
    decrypted = EncryptionUtils.decrypt_rc4(key, encrypted)
    assert decrypted == data


def test_rc4_key_length():
    key = os.urandom(16)
    data = b"RC4 key length test"
    encrypted = EncryptionUtils.encrypt_rc4(key, data)
    decrypted = EncryptionUtils.decrypt_rc4(key, encrypted)
    assert decrypted == data
