# PDF Encryption Module - ISO 32000 Compliant Key Derivation
"""
Production-ready PDF encryption utilities with proper key derivation per ISO 32000.

Implements:
- PDF V4 (AES-128) key derivation algorithms 3.2, 3.3, 3.4, 3.5
- PDF V5/R6 (AES-256) key derivation algorithm 2.A (simplified)
- Standard encryption/decryption for streams and strings
"""

import os
import hashlib
from typing import Optional, Tuple
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding

from aspose_pdf.exceptions import PdfSecurityException
import logging

logger = logging.getLogger("aspose_pdf.encryption")

try:
    from cryptography.hazmat.decrepit.ciphers.algorithms import ARC4
except ImportError:
    from cryptography.hazmat.primitives.ciphers.algorithms import ARC4


# PDF Standard Security Handler padding (32 bytes) - ISO 32000-1:2008 Table 3.19
PDF_PADDING = bytes(
    [
        0x28,
        0xBF,
        0x4E,
        0x5E,
        0x4E,
        0x75,
        0x8A,
        0x41,
        0x64,
        0x00,
        0x4E,
        0x56,
        0xFF,
        0xFA,
        0x01,
        0x08,
        0x2E,
        0x2E,
        0x00,
        0xB6,
        0xD0,
        0x68,
        0x3E,
        0x80,
        0x2F,
        0x0C,
        0xA9,
        0xFE,
        0x64,
        0x53,
        0x69,
        0x7A,
    ]
)


class EncryptionUtils:
    """Utility class for PDF-compliant AES-CBC, RC4 encryption, and key derivation.

    Implements key derivation algorithms per ISO 32000-1 (PDF 1.7) and ISO 32000-2.
    """

    # -------------------------------------------------------------------------
    # Key Validation
    # -------------------------------------------------------------------------
    @staticmethod
    def _validate_aes_key(key: bytes) -> None:
        if len(key) not in (16, 24, 32):
            raise PdfSecurityException("AES key must be 16, 24, or 32 bytes long")

    @staticmethod
    def _validate_rc4_key(key: bytes) -> None:
        if not (5 <= len(key) <= 256):
            raise PdfSecurityException("RC4 key must be between 5 and 256 bytes long")

    @staticmethod
    def _validate_key_length(key_length: int) -> None:
        if key_length not in (5, 16, 24, 32):
            raise PdfSecurityException(
                f"Invalid key length: {key_length}. Must be 5, 16, 24, or 32 bytes"
            )

    # -------------------------------------------------------------------------
    # Basic Encryption/Decryption
    # -------------------------------------------------------------------------
    @staticmethod
    def encrypt_aes_cbc(key: bytes, data: bytes, iv: Optional[bytes] = None) -> bytes:
        """Encrypt data using AES-CBC with PKCS7 padding.

        Args:
            key: AES key (16, 24, or 32 bytes)
            data: Plaintext data
            iv: Optional IV (16 bytes). If not provided, random IV is generated.

        Returns:
            IV + ciphertext
        """
        EncryptionUtils._validate_aes_key(key)
        if iv is None:
            iv = os.urandom(16)
        elif len(iv) != 16:
            raise PdfSecurityException("IV must be 16 bytes")
        padder = padding.PKCS7(128).padder()
        padded_data = padder.update(data) + padder.finalize()
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        encryptor = cipher.encryptor()
        ciphertext = encryptor.update(padded_data) + encryptor.finalize()
        return iv + ciphertext

    @staticmethod
    def decrypt_aes_cbc(key: bytes, data: bytes) -> bytes:
        """Decrypt data encrypted by encrypt_aes_cbc.

        Expects IV + ciphertext.
        """
        EncryptionUtils._validate_aes_key(key)
        if len(data) < 16:
            raise PdfSecurityException("Data is too short to contain IV")
        iv = data[:16]
        ciphertext = data[16:]
        if len(ciphertext) == 0:
            return b""
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        padded_plain = decryptor.update(ciphertext) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        plain = unpadder.update(padded_plain) + unpadder.finalize()
        return plain

    @staticmethod
    def encrypt_rc4(key: bytes, data: bytes) -> bytes:
        """Encrypt data using RC4 (ARC4) algorithm."""
        EncryptionUtils._validate_rc4_key(key)
        cipher = Cipher(ARC4(key), mode=None)
        encryptor = cipher.encryptor()
        return encryptor.update(data) + encryptor.finalize()

    @staticmethod
    def decrypt_rc4(key: bytes, data: bytes) -> bytes:
        """Decrypt data using RC4 (ARC4) algorithm."""
        # RC4 is symmetric; decryption is same as encryption
        return EncryptionUtils.encrypt_rc4(key, data)

    @staticmethod
    def decrypt_writer_encrypted_stream(
        file_key: bytes,
        algorithm: str,
        obj_id: int,
        data: bytes,
    ) -> bytes:
        """Decrypt stream bytes produced by :meth:`PdfWriterV0._encrypt_data`.

        Matches the native writer: AES uses ``encrypt_aes_cbc`` on the file key;
        RC4 derives a per-object key with MD5.
        """
        if not data or not file_key:
            return data
        if algorithm.startswith("AES"):
            return EncryptionUtils.decrypt_aes_cbc(file_key, data)
        key = file_key + bytes(
            [obj_id & 0xFF, (obj_id >> 8) & 0xFF, (obj_id >> 16) & 0xFF, 0, 0]
        )
        real_key = hashlib.md5(key).digest()[:10]
        return EncryptionUtils.decrypt_rc4(real_key, data)

    # -------------------------------------------------------------------------
    # PDF V2/V3 (40-bit RC4) and V4 (128-bit AES) Key Derivation
    # ISO 32000-1:2008 Section 7.6.3
    # -------------------------------------------------------------------------
    @staticmethod
    def _pad_password(password: str) -> bytes:
        """Pad or truncate password to exactly 32 bytes using PDF padding.

        Algorithm 3.2, Step 1: ISO 32000-1:2008
        """
        if not password:
            return PDF_PADDING
        pwd_bytes = password.encode("latin-1", errors="replace")[:32]
        return (pwd_bytes + PDF_PADDING)[:32]

    @staticmethod
    def compute_owner_key_v4(
        owner_password: str, user_password: str, key_length: int = 16, revision: int = 4
    ) -> bytes:
        """Compute Owner password value (O) per Algorithm 3.3.

        Args:
            owner_password: Owner password (or user password if empty)
            user_password: User password
            key_length: Key length in bytes (5 for 40-bit, 16 for 128-bit)
            revision: Security handler revision (2, 3, or 4)

        Returns:
            32-byte O value for the encryption dictionary
        """
        # Step 1: Use owner password or fall back to user password
        if not owner_password:
            owner_password = user_password

        # Step 2: Pad to 32 bytes
        padded_owner = EncryptionUtils._pad_password(owner_password)

        # Step 3: MD5 hash
        md5_hash = hashlib.md5(padded_owner).digest()

        # Step 4: For R >= 3, iterate MD5 50 times
        if revision >= 3:
            for _ in range(50):
                md5_hash = hashlib.md5(md5_hash[:key_length]).digest()

        # Step 5: Use first key_length bytes as RC4 key
        rc4_key = md5_hash[:key_length]

        # Step 6: Pad user password
        padded_user = EncryptionUtils._pad_password(user_password)

        # Step 7: RC4-encrypt the padded user password
        encrypted = EncryptionUtils._rc4_with_key(rc4_key, padded_user)

        # Step 8: For R >= 3, do 19 additional RC4 passes with modified keys
        if revision >= 3:
            for i in range(1, 20):
                modified_key = bytes([b ^ i for b in rc4_key])
                encrypted = EncryptionUtils._rc4_with_key(modified_key, encrypted)

        return encrypted

    @staticmethod
    def _rc4_with_key(key: bytes, data: bytes) -> bytes:
        """Perform RC4 encryption with variable-length key (for key derivation)."""
        # Extend key if less than 5 bytes for RC4 validation
        if len(key) < 5:
            key = (key * 5)[:5]
        cipher = Cipher(ARC4(key), mode=None)
        encryptor = cipher.encryptor()
        return encryptor.update(data) + encryptor.finalize()

    @staticmethod
    def compute_file_encryption_key(
        password: str,
        o_value: bytes,
        p_value: int,
        file_id: bytes,
        key_length: int = 16,
        revision: int = 4,
        encrypt_metadata: bool = True,
    ) -> bytes:
        """Compute the file encryption key per Algorithm 3.2.

        Args:
            password: User or owner password
            o_value: O value from encryption dictionary
            p_value: P value (permissions) from encryption dictionary
            file_id: First element of /ID array in trailer
            key_length: Key length in bytes
            revision: Security handler revision
            encrypt_metadata: Whether metadata is encrypted (R4+)

        Returns:
            Encryption key of key_length bytes
        """
        # Step 1: Pad password
        padded = EncryptionUtils._pad_password(password)

        # Step 2: Concatenate padded password + O + P (4 bytes little-endian) + file_id
        md5_input = padded + o_value
        md5_input += p_value.to_bytes(4, "little", signed=True)
        md5_input += file_id

        # Step 3: If R4+ and metadata not encrypted, append 0xFFFFFFFF
        if revision >= 4 and not encrypt_metadata:
            md5_input += bytes([0xFF, 0xFF, 0xFF, 0xFF])

        # Step 4: MD5 hash
        md5_hash = hashlib.md5(md5_input).digest()

        # Step 5: For R >= 3, iterate MD5 50 times
        if revision >= 3:
            for _ in range(50):
                md5_hash = hashlib.md5(md5_hash[:key_length]).digest()

        # Step 6: Return first key_length bytes
        return md5_hash[:key_length]

    @staticmethod
    def compute_user_key_v4(
        password: str,
        o_value: bytes,
        p_value: int,
        file_id: bytes,
        key_length: int = 16,
        revision: int = 4,
        encrypt_metadata: bool = True,
    ) -> Tuple[bytes, bytes]:
        """Compute User password value (U) and encryption key per Algorithm 3.4/3.5.

        Args:
            password: User password
            o_value: O value computed by compute_owner_key_v4
            p_value: Permissions value (P)
            file_id: Document ID (first element of /ID array)
            key_length: Key length in bytes (5, 16, 24, or 32)
            revision: Security handler revision
            encrypt_metadata: /EncryptMetadata from the encryption dictionary (R4+)

        Returns:
            Tuple of (U value, encryption key)
        """
        # Compute encryption key using Algorithm 3.2
        encryption_key = EncryptionUtils.compute_file_encryption_key(
            password,
            o_value,
            p_value,
            file_id,
            key_length,
            revision,
            encrypt_metadata,
        )

        if revision == 2:
            # Algorithm 3.4: R2 - RC4-encrypt the padding
            u_value = EncryptionUtils._rc4_with_key(encryption_key, PDF_PADDING)
        else:
            # Algorithm 3.5: R3/R4 - MD5 hash of padding + file_id, then RC4
            md5_input = PDF_PADDING + file_id
            md5_hash = hashlib.md5(md5_input).digest()

            # RC4-encrypt with key
            encrypted = EncryptionUtils._rc4_with_key(encryption_key, md5_hash)

            # 19 additional RC4 passes with modified keys
            for i in range(1, 20):
                modified_key = bytes([b ^ i for b in encryption_key])
                encrypted = EncryptionUtils._rc4_with_key(modified_key, encrypted)

            # Pad to 32 bytes with arbitrary data
            u_value = encrypted + os.urandom(16)

        return u_value, encryption_key

    @staticmethod
    def verify_password_v4(
        password: str,
        u_value: bytes,
        o_value: bytes,
        p_value: int,
        file_id: bytes,
        key_length: int = 16,
        revision: int = 4,
        encrypt_metadata: bool = True,
    ) -> Optional[bytes]:
        """Verify password and return encryption key if valid.

        Args:
            password: Password to verify (user or owner)
            u_value: U value from encryption dictionary
            o_value: O value from encryption dictionary
            p_value: P value from encryption dictionary
            file_id: Document ID
            key_length: Key length in bytes
            revision: Security handler revision
            encrypt_metadata: /EncryptMetadata from the encryption dictionary (R4+)

        Returns:
            Encryption key if password is valid, None otherwise
        """
        # Try as user password first
        computed_u, enc_key = EncryptionUtils.compute_user_key_v4(
            password,
            o_value,
            p_value,
            file_id,
            key_length,
            revision,
            encrypt_metadata,
        )

        # For R2, compare all 32 bytes; for R3/R4, compare first 16 bytes
        compare_len = 32 if revision == 2 else 16
        if computed_u[:compare_len] == u_value[:compare_len]:
            return enc_key

        # Try as owner password (decrypt O to get U password, then verify)
        enc_key = EncryptionUtils._verify_owner_password(
            password,
            u_value,
            o_value,
            p_value,
            file_id,
            key_length,
            revision,
            encrypt_metadata,
        )
        if enc_key:
            return enc_key

        return None

    @staticmethod
    def _verify_owner_password(
        owner_password: str,
        u_value: bytes,
        o_value: bytes,
        p_value: int,
        file_id: bytes,
        key_length: int,
        revision: int,
        encrypt_metadata: bool = True,
    ) -> Optional[bytes]:
        """Verify owner password and return encryption key.

        Per Algorithm 3.7: Use owner password to recover user password from O.
        """
        # Pad owner password
        padded_owner = EncryptionUtils._pad_password(owner_password)

        # MD5 hash
        md5_hash = hashlib.md5(padded_owner).digest()

        # For R >= 3, iterate 50 times
        if revision >= 3:
            for _ in range(50):
                md5_hash = hashlib.md5(md5_hash[:key_length]).digest()

        rc4_key = md5_hash[:key_length]

        # Decrypt O value to recover padded user password
        if revision == 2:
            decrypted = EncryptionUtils._rc4_with_key(rc4_key, o_value)
        else:
            decrypted = o_value
            # Reverse the 19 RC4 passes
            for i in range(19, -1, -1):
                modified_key = bytes([b ^ i for b in rc4_key])
                decrypted = EncryptionUtils._rc4_with_key(modified_key, decrypted)

        # The decrypted value is the padded user password
        # Use it to compute the encryption key
        # Find where the padding starts
        user_pwd = ""
        try:
            # Try to decode as latin-1 and find padding boundary
            decoded = decrypted.decode("latin-1", errors="replace")
            # User password is before the PDF padding
            padding_idx = decoded.find("\x28\xbf")
            if padding_idx > 0:
                user_pwd = decoded[:padding_idx]
            elif decoded == PDF_PADDING.decode("latin-1", errors="replace"):
                user_pwd = ""
        except Exception:
            user_pwd = ""

        # Compute encryption key with recovered user password
        enc_key = EncryptionUtils.compute_file_encryption_key(
            user_pwd,
            o_value,
            p_value,
            file_id,
            key_length,
            revision,
            encrypt_metadata,
        )

        # Verify by computing U and comparing
        computed_u, _ = EncryptionUtils.compute_user_key_v4(
            user_pwd,
            o_value,
            p_value,
            file_id,
            key_length,
            revision,
            encrypt_metadata,
        )

        compare_len = 32 if revision == 2 else 16
        if computed_u[:compare_len] == u_value[:compare_len]:
            return enc_key

        return None

    # -------------------------------------------------------------------------
    # PDF V5/R6 (AES-256) Key Derivation - ISO 32000-2
    # -------------------------------------------------------------------------
    @staticmethod
    def compute_hash_v5(password: bytes, salt: bytes, user_key: bytes = b"") -> bytes:
        """Compute hash for V5/R6 per Algorithm 2.B (ISO 32000-2).

        Production-ready implementation of the full Algorithm 2.B specification:
        - Performs iterative hashing starting with SHA-256
        - Dynamically selects SHA-256/384/512 based on AES-encrypted intermediate values
        - Continues for at least 64 rounds with termination based on last byte

        Args:
            password: Password bytes (UTF-8 encoded, max 127 bytes per caller)
            salt: 8-byte salt (validation salt or key salt from U/O value)
            user_key: For owner password operations, the 48-byte U value; otherwise empty

        Returns:
            32-byte hash result for password verification or key derivation

        References:
            ISO 32000-2:2020 Section 7.6.4.3.4 Algorithm 2.B
        """
        # Step a: Initial hash input = password + salt + user_key
        input_data = password + salt + user_key

        # Step b: Initialize K with SHA-256 of input
        K = hashlib.sha256(input_data).digest()

        # Step c: Perform iterative processing (at least 64 rounds)
        round_num = 0

        while True:
            # Build K1: repeat (password + K + user_key) 64 times
            K1_unit = password + K + user_key
            K1 = K1_unit * 64  # Repeat 64 times

            # AES-128-CBC encrypt K1 using first 16 bytes of K as key and next 16 as IV
            # K is at least 32 bytes (SHA-256 output), so this is always valid
            aes_key = K[:16]
            aes_iv = K[16:32]

            # AES-CBC with NO PADDING (K1 length is always multiple of 16 since
            # each K1_unit contains K which is >= 32 bytes, repeated 64 times)
            cipher = Cipher(algorithms.AES(aes_key), modes.CBC(aes_iv))
            encryptor = cipher.encryptor()
            E = encryptor.update(K1) + encryptor.finalize()

            # Take first 16 bytes of E, interpret as 128-bit big-endian integer
            # Calculate mod 3 to determine next hash algorithm
            big_int = int.from_bytes(E[:16], byteorder="big")
            hash_choice = big_int % 3

            # Hash E using selected algorithm
            if hash_choice == 0:
                K = hashlib.sha256(E).digest()  # 32 bytes
            elif hash_choice == 1:
                K = hashlib.sha384(E).digest()  # 48 bytes
            else:  # hash_choice == 2
                K = hashlib.sha512(E).digest()  # 64 bytes

            round_num += 1

            # Termination condition per Algorithm 2.B:
            # Continue while round_num < 64 OR last_byte_of_E > (round_num - 32)
            if round_num >= 64:
                last_byte = E[-1]
                # Stop when last_byte <= round_num - 32
                if last_byte <= round_num - 32:
                    break

        # Return first 32 bytes of K
        return K[:32]

    @staticmethod
    def compute_user_owner_keys_v6(
        user_password: str, owner_password: str, file_key: Optional[bytes] = None
    ) -> Tuple[bytes, bytes, bytes, bytes]:
        """Compute U, O, UE, OE values for AES-256 (R6) per ISO 32000-2.

        Algorithm 2.A variants for creation.
        """
        import os

        if file_key is None:
            file_key = os.urandom(32)

        u_val_salt = os.urandom(8)
        u_key_salt = os.urandom(8)
        o_val_salt = os.urandom(8)
        o_key_salt = os.urandom(8)

        user_pwd_bytes = user_password.encode("utf-8")[:127]
        owner_pwd_bytes = owner_password.encode("utf-8")[:127]

        # 1. User values
        u_hash = EncryptionUtils.compute_hash_v5(user_pwd_bytes, u_val_salt)
        u_value = u_hash + u_val_salt + u_key_salt

        u_inter_key = EncryptionUtils.compute_hash_v5(user_pwd_bytes, u_key_salt)
        cipher_u = Cipher(algorithms.AES(u_inter_key), modes.CBC(bytes(16)))
        enc_u = cipher_u.encryptor()
        ue_value = enc_u.update(file_key) + enc_u.finalize()

        # 2. Owner values
        o_hash = EncryptionUtils.compute_hash_v5(owner_pwd_bytes, o_val_salt, u_value)
        o_value = o_hash + o_val_salt + o_key_salt

        o_inter_key = EncryptionUtils.compute_hash_v5(
            owner_pwd_bytes, o_key_salt, u_value
        )
        cipher_o = Cipher(algorithms.AES(o_inter_key), modes.CBC(bytes(16)))
        enc_o = cipher_o.encryptor()
        oe_value = enc_o.update(file_key) + enc_o.finalize()

        return u_value, o_value, ue_value, oe_value, file_key

    @staticmethod
    def verify_password_v6(
        password: str,
        u_value: bytes,
        o_value: bytes,
        ue_value: bytes,
        oe_value: bytes,
    ) -> Optional[bytes]:
        """Verify user or owner password for PDF V5 revision 5/6; return file key or None."""
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives import padding

        pwd = password.encode("utf-8")[:127]

        def _try_decrypt_ue_oe(key_material: bytes, blob: bytes) -> Optional[bytes]:
            if not blob:
                return None
            try:
                cipher = Cipher(algorithms.AES(key_material), modes.CBC(bytes(16)))
                decryptor = cipher.decryptor()
                plain = decryptor.update(blob) + decryptor.finalize()
                # Writer uses unpadded 32-byte file keys (two CBC blocks); PKCS7 may apply
                # when the ciphertext includes an explicit padding block.
                if len(plain) == 32:
                    return plain
                unpadder = padding.PKCS7(128).unpadder()
                data = unpadder.update(plain) + unpadder.finalize()
                return data if len(data) == 32 else None
            except Exception:
                return None

        if len(u_value) >= 48 and ue_value:
            u_val_salt = u_value[32:40]
            u_key_salt = u_value[40:48]
            u_hash = EncryptionUtils.compute_hash_v5(pwd, u_val_salt)
            if u_hash == u_value[:32]:
                u_inter_key = EncryptionUtils.compute_hash_v5(pwd, u_key_salt)
                key = _try_decrypt_ue_oe(u_inter_key, ue_value)
                if key is not None:
                    return key

        if len(o_value) >= 48 and oe_value:
            o_val_salt = o_value[32:40]
            o_key_salt = o_value[40:48]
            o_hash = EncryptionUtils.compute_hash_v5(pwd, o_val_salt, u_value)
            if o_hash == o_value[:32]:
                o_inter_key = EncryptionUtils.compute_hash_v5(pwd, o_key_salt, u_value)
                key = _try_decrypt_ue_oe(o_inter_key, oe_value)
                if key is not None:
                    return key

        return None

    @staticmethod
    def encrypt_perms_v6(
        file_key: bytes, p_value: int, encrypt_metadata: bool = True
    ) -> bytes:
        """Encrypt permissions and metadata flag for R6.

        Perms = 16 bytes:
        - Bytes 0-3: P value (little endian)
        - Byte 4: 0xFF if encrypt_metadata else 0xF0
        - Bytes 5-8: 'adb '
        - Bytes 9-11: random
        """
        import os

        perms = p_value.to_bytes(4, "little", signed=True)
        perms += b"\xff" if encrypt_metadata else b"\xf0"
        perms += b"adb "
        perms += os.urandom(3)
        perms += b"\xff\xff\xff\xff"[:4]  # ensure 16 bytes

        cipher = Cipher(algorithms.AES(file_key), modes.ECB())
        enc = cipher.encryptor()
        return enc.update(perms[:16]) + enc.finalize()

    # -------------------------------------------------------------------------
    # Convenience functions for PDF encryption/decryption
    # -------------------------------------------------------------------------
    @staticmethod
    def derive_object_key(
        file_key: bytes, obj_num: int, gen_num: int, use_aes: bool = True
    ) -> bytes:
        """Derive object-specific key per Algorithm 3.1.

        For encrypting individual objects in the PDF.
        """
        # Extend file key with object/generation number
        extended = (
            file_key + obj_num.to_bytes(3, "little") + gen_num.to_bytes(2, "little")
        )

        if use_aes:
            # For AES, append "sAlT"
            extended += b"sAlT"

        # MD5 hash and truncate
        obj_key = hashlib.md5(extended).digest()

        # Key length is min(file_key_length + 5, 16) for RC4
        # For AES, always use 16 bytes
        if use_aes:
            return obj_key[:16]
        else:
            return obj_key[: min(len(file_key) + 5, 16)]

    @staticmethod
    def generate_file_id() -> bytes:
        """Generate a random 16-byte file ID for new documents."""
        return os.urandom(16)
