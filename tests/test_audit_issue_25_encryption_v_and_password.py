"""AUDIT #25: Encryption password checks across /V and handler variants.

- Standard handler revision 4+: ``/EncryptMetadata`` participates in key derivation
  (Algorithm 3.2). Verification must use the same flag as the producer or correct
  passwords are rejected and wrong flags yield no key.
- Revision 5+ (AES-256-style): if ``/U``/``/O``/``/UE``/``/OE`` cannot support
  verification, loading must not proceed as if the password were accepted.
"""

from __future__ import annotations

import os

import pytest

from aspose_pdf.engine.cos import (
    PdfDictionary,
    PdfIndirectReference,
    PdfName,
    PdfString,
)
from aspose_pdf.engine.encryption import EncryptionUtils
from aspose_pdf.engine.pdf_parser_cos import PdfCosParser
from aspose_pdf.engine.simple_pdf import CosExtractor, SimplePdf
from aspose_pdf.exceptions import PdfSecurityException


def test_verify_password_v4_honors_encrypt_metadata_false() -> None:
    file_id = os.urandom(16)
    password = "user-secret"
    o_val = EncryptionUtils.compute_owner_key_v4("owner-x", password, 16, 4)
    u_val, enc_key = EncryptionUtils.compute_user_key_v4(
        password,
        o_val,
        -4,
        file_id,
        16,
        4,
        encrypt_metadata=False,
    )
    verified = EncryptionUtils.verify_password_v4(
        password,
        u_val,
        o_val,
        -4,
        file_id,
        16,
        4,
        encrypt_metadata=False,
    )
    assert verified == enc_key


def test_verify_password_v4_encrypt_metadata_mismatch_rejects_password() -> None:
    file_id = os.urandom(16)
    password = "meta-flag"
    o_val = EncryptionUtils.compute_owner_key_v4("ow", password, 16, 4)
    u_val, _ = EncryptionUtils.compute_user_key_v4(
        password,
        o_val,
        -4,
        file_id,
        16,
        4,
        encrypt_metadata=False,
    )
    assert (
        EncryptionUtils.verify_password_v4(
            password,
            u_val,
            o_val,
            -4,
            file_id,
            16,
            4,
            encrypt_metadata=True,
        )
        is None
    )


@pytest.mark.parametrize("algorithm", ("AES-256", "AES-128", "RC4"))
def test_simplepdf_wrong_password_raises(algorithm: str) -> None:
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 612, 792)]
    pdf.page_contents = [b""]
    pdf.encrypt("correct", algorithm=algorithm)
    data = pdf.to_bytes()
    with pytest.raises(PdfSecurityException, match="Incorrect password"):
        SimplePdf.from_bytes(data, password="wrong-password")


def test_cos_extractor_incomplete_r6_encrypt_dict_raises() -> None:
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 612, 792)]
    pdf.page_contents = [b""]
    pdf.encrypt("k", algorithm="AES-256")
    data = pdf.to_bytes()
    cos_doc = PdfCosParser(data).parse()
    enc_ref = cos_doc.trailer.mapping.get(PdfName("Encrypt"))
    assert isinstance(enc_ref, PdfIndirectReference)
    enc = cos_doc.objects.get(enc_ref.object_number)
    assert isinstance(enc, PdfDictionary)
    enc.mapping[PdfName("U")] = PdfString(b"\x00" * 16)
    enc.mapping.pop(PdfName("UE"), None)
    enc.mapping.pop(PdfName("OE"), None)

    extractor = CosExtractor(cos_doc, data)
    with pytest.raises(PdfSecurityException, match="Cannot verify password"):
        extractor.encryption_password_allows_access("k")
