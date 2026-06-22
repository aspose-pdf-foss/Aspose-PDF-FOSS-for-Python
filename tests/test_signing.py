from aspose_pdf.engine.signing import SigningUtils

# cryptography types for precise assertions
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import rsa


def test_create_self_signed_cert_returns_correct_types():
    """Ensure the certificate and private key have expected cryptography types."""
    cert, key = SigningUtils.create_self_signed_cert()
    assert isinstance(cert, x509.Certificate)
    assert isinstance(key, rsa.RSAPrivateKey)


def test_sign_data_pkcs7_returns_non_empty_bytes():
    """Signing arbitrary data should produce a non‑empty PKCS#7 signature bytes object."""
    # Prepare a self‑signed certificate and its private key
    cert, key = SigningUtils.create_self_signed_cert()

    data = b"sample data for signing"
    signature = SigningUtils.sign_data_pkcs7(data, cert, key)

    # Basic assertions about the signature output
    assert isinstance(signature, bytes)
    assert len(signature) > 0

    # If a verification helper exists, perform a basic check; otherwise, ensure the
    # function exists and is callable without raising.
    if hasattr(SigningUtils, "verify_pkcs7_signature"):
        # Verify should return True for a signature created with the same cert/key
        verified = SigningUtils.verify_pkcs7_signature(signature, data, cert)
        assert verified is True
    else:
        # Ensure the method signature exists to avoid silent failures
        assert callable(SigningUtils.sign_data_pkcs7)
