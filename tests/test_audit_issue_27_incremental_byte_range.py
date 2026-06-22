"""AUDIT #27: ByteRange / PKCS#7 verification with incremental tail bytes.

PDF signatures record ``ByteRange`` against the file at signing time. Appending
further bytes after the signed revision (e.g. incremental updates) must not
break verification: the hashed slices are unchanged and end at ``start2 + len2``.
"""

from __future__ import annotations

from aspose_pdf.engine.signing import SigningUtils
from aspose_pdf.signature import PdfSignature


def test_byte_range_verifies_when_reference_data_has_incremental_suffix():
    """``reference_data`` longer than ``start2 + len2`` (signed revision) still verifies."""
    data = b"Hello, PDF world!"
    cert, key = SigningUtils.create_self_signed_cert()
    sig_bytes = SigningUtils.sign_data_pkcs7(data, cert, key)

    half = len(data) // 2
    byte_range = [0, half, half, len(data) - half]

    tail = b"\n% incremental / appended after signing\n%%EOF\n"
    sig = PdfSignature(
        name="AUDIT27",
        contents=sig_bytes,
        byte_range=byte_range,
        reference_data=data + tail,
    )

    assert sig.valid is True
    r = sig.validate()
    assert r.is_valid
    assert r.status.name == "VALID"
