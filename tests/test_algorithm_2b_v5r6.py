import os
from aspose_pdf.engine.encryption import EncryptionUtils


class TestAlgorithm2B:
    """Tests for ISO 32000-2 Algorithm 2.B (V5/R6 key derivation)."""

    def test_compute_hash_v5_returns_32_bytes(self):
        """Algorithm 2.B must always return exactly 32 bytes."""
        password = b"test"
        salt = os.urandom(8)
        result = EncryptionUtils.compute_hash_v5(password, salt)
        assert len(result) == 32, "compute_hash_v5 must return 32 bytes"

    def test_compute_hash_v5_deterministic(self):
        """Same inputs produce same output (deterministic)."""
        password = b"password"
        salt = b"12345678"
        result1 = EncryptionUtils.compute_hash_v5(password, salt)
        result2 = EncryptionUtils.compute_hash_v5(password, salt)
        assert result1 == result2, "Same inputs must produce same hash"

    def test_compute_hash_v5_different_salt(self):
        """Different salts produce different hashes."""
        password = b"password"
        salt1 = b"salt1___"
        salt2 = b"salt2___"
        result1 = EncryptionUtils.compute_hash_v5(password, salt1)
        result2 = EncryptionUtils.compute_hash_v5(password, salt2)
        assert result1 != result2, "Different salts must produce different hashes"

    def test_compute_hash_v5_different_password(self):
        """Different passwords produce different hashes."""
        salt = b"12345678"
        result1 = EncryptionUtils.compute_hash_v5(b"pass1", salt)
        result2 = EncryptionUtils.compute_hash_v5(b"pass2", salt)
        assert result1 != result2, "Different passwords must produce different hashes"

    def test_compute_hash_v5_with_user_key(self):
        """User key affects the hash result."""
        password = b"password"
        salt = b"12345678"
        user_key = os.urandom(48)

        result_without = EncryptionUtils.compute_hash_v5(password, salt, b"")
        result_with = EncryptionUtils.compute_hash_v5(password, salt, user_key)

        assert result_without != result_with, "User key must affect hash"

    def test_compute_hash_v5_empty_password(self):
        """Empty password should work without exception."""
        salt = b"12345678"
        result = EncryptionUtils.compute_hash_v5(b"", salt)
        assert len(result) == 32

    def test_compute_hash_v5_long_password_truncated(self):
        """Password should be max 127 bytes as per spec (caller responsibility)."""
        # Note: The function expects properly prepared password bytes
        salt = b"12345678"
        long_pwd = b"x" * 127  # Max allowed
        result = EncryptionUtils.compute_hash_v5(long_pwd, salt)
        assert len(result) == 32

    def test_compute_hash_v5_uses_multiple_hash_algorithms(self):
        """Algorithm should use different hash algorithms based on intermediate values.

        While we can't directly observe which hash is used, we verify that:
        1. Output is correct length
        2. Various inputs work (exercising different code paths)
        """
        # Test with many different inputs to exercise different hash algorithm selections
        for i in range(10):
            password = f"test{i}".encode()
            salt = os.urandom(8)
            result = EncryptionUtils.compute_hash_v5(password, salt)
            assert len(result) == 32

    def test_compute_hash_v5_minimum_64_rounds(self):
        """Algorithm must perform at least 64 rounds (implicit in the algorithm).

        We verify this by ensuring the function produces consistent,
        non-trivial results that would require the full algorithm.
        """
        # Simple validation that it's not just a single hash
        password = b"test"
        salt = b"12345678"
        simple_hash = __import__("hashlib").sha256(password + salt).digest()
        algo_2b_hash = EncryptionUtils.compute_hash_v5(password, salt)

        # The results should be different (Algorithm 2.B is much more complex)
        assert simple_hash != algo_2b_hash

    def test_aes256_key_derivation_roundtrip(self):
        """Full V5/R6 key derivation and verification roundtrip.

        Create encryption values, then verify password works.
        """
        password = "SecurePassword123"

        # Generate random values as would be in a real PDF
        _ = os.urandom(16)  # file_id
        file_key = os.urandom(32)  # The random file encryption key

        # Generate user validation and key salts
        u_val_salt = os.urandom(8)
        u_key_salt = os.urandom(8)

        pwd_bytes = password.encode("utf-8")[:127]

        # Compute U hash (validation)
        u_hash = EncryptionUtils.compute_hash_v5(pwd_bytes, u_val_salt, b"")

        # Compute intermediate key for encrypting file key
        intermediate = EncryptionUtils.compute_hash_v5(pwd_bytes, u_key_salt, b"")

        # Encrypt file key with intermediate key (AES-256-CBC, zero IV)
        zero_iv = bytes(16)
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        cipher = Cipher(algorithms.AES(intermediate), modes.CBC(zero_iv))
        encryptor = cipher.encryptor()
        ue_value = encryptor.update(file_key) + encryptor.finalize()

        # U value format: hash (32) + val_salt (8) + key_salt (8) = 48 bytes
        _ = u_hash + u_val_salt + u_key_salt  # u_value

        # Now verify: compute hash with val_salt and compare to stored hash
        verify_hash = EncryptionUtils.compute_hash_v5(pwd_bytes, u_val_salt, b"")
        assert verify_hash == u_hash, "Password verification should match"

        # Derive key and decrypt UE
        derived_intermediate = EncryptionUtils.compute_hash_v5(
            pwd_bytes, u_key_salt, b""
        )
        cipher = Cipher(algorithms.AES(derived_intermediate), modes.CBC(zero_iv))
        decryptor = cipher.decryptor()
        recovered_key = decryptor.update(ue_value) + decryptor.finalize()

        assert recovered_key == file_key, "Recovered file key should match original"
