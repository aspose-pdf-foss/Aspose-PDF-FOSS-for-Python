"""Tests for PdfSignature, SimplePdf and related security utilities.

These tests focus on the internal integrity verification of ``PdfSignature``
and on the integration helpers ``SimplePdf`` and ``SignaturesCompromiseDetector``.
The integration test constructs a signed PDF through the writer and parses it
back through the COS extractor, keeping the fixture compact and redistributable.
"""

import pytest

from aspose_pdf.engine.signing import SigningUtils
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.security import SignaturesCompromiseDetector
from aspose_pdf.signature import PdfSignature


def _signed_fixture():
    """A real detached PKCS#7 over known bytes, laid out as a PDF ByteRange."""
    data = b"%PDF-1.7 signed body\n" * 4
    half = len(data) // 2
    cert, key = SigningUtils.create_self_signed_cert()
    blob = SigningUtils.sign_data_pkcs7(data[:half] + data[half:], cert, key)
    return PdfSignature(
        name="TestSig",
        byte_range=[0, half, half, len(data) - half],
        reference_data=data,
        contents=blob,
    )


def test_verify_integrity_accepts_a_real_signature():
    assert _signed_fixture()._verify_integrity() is True


def test_verify_integrity_rejects_tampered_bytes():
    """A byte flipped inside the covered range must fail the digest check."""
    sig = _signed_fixture()
    tampered = bytearray(sig.reference_data)
    tampered[5] ^= 0xFF
    sig.reference_data = bytes(tampered)
    assert sig._verify_integrity() is False
    assert sig.valid is False


def test_verify_integrity_rejects_a_non_cms_blob():
    """A sane ByteRange is not enough: /Contents must be a verifiable CMS.

    This used to return True — the digest comparison went through a
    ``cryptography`` API that does not exist in every release, and its absence
    (as well as any parse error) was treated as success.
    """
    sig = PdfSignature(
        name="TestSig",
        byte_range=[0, 10, 20, 10],
        reference_data=b"A" * 30,
        contents=b"dummy",
    )
    assert sig._verify_integrity() is False
    assert sig.valid is False


@pytest.mark.parametrize(
    "byte_range",
    [
        [0, 10, 20],  # not four entries
        [0, 10, 20, 10, 30],
        [0, -1, 20, 10],  # negative length
        [0, 25, 30, 10],  # runs past the buffer
        [0, 15, 10, 5],  # second range overlaps the first
        [2, 8, 20, 10],  # does not start at byte 0
    ],
)
def test_verify_integrity_rejects_malformed_byte_range(byte_range):
    sig = _signed_fixture()
    sig.byte_range = byte_range
    assert sig._verify_integrity() is False


def test_simple_pdf_signatures_empty():
    """A freshly instantiated ``SimplePdf`` must report no signatures."""
    pdf = SimplePdf()
    # The public ``signatures`` attribute should be an empty list.
    assert isinstance(pdf.signatures, list)
    assert pdf.signatures == []


def test_signatures_compromise_detector(monkeypatch):
    """Detector should flag a document as compromised when a signature is invalid.

    The test injects a mock ``PdfSignature`` with ``valid`` property set to ``False``
    and verifies that the detector reports compromise.
    """
    pdf = SimplePdf()

    # Create a dummy signature and force its ``valid`` attribute to False.
    # We use a simple object because PdfSignature.valid is a read-only property.
    class MockSig:
        valid = False
        name = "DummySig"

    dummy_sig = MockSig()

    # Manually attach the signature to the PDF document.
    pdf.signatures.append(dummy_sig)

    detector = SignaturesCompromiseDetector(pdf)
    # Assume ``check`` returns a result object indicating compromise.
    result = detector.check()
    assert result.has_compromised_signatures is True
    assert result.signatures_coverage == 1


def test_cos_extractor_extracts_signature():
    """A writer-produced signature field is recovered by the COS extractor."""
    certificate, private_key = SigningUtils.create_self_signed_cert()
    pdf = SimplePdf(
        pages=[(0.0, 0.0, 200.0, 200.0)],
        page_contents=[b"BT (Signature extraction) Tj ET"],
    )
    pdf.signing_creds = (certificate, private_key)
    pdf.signature = {
        "Name": "ExtractionSignature",
        "Reason": "Regression test",
        "Location": "Test suite",
    }

    data = pdf.to_bytes()
    extracted = SimplePdf.from_bytes(data).signatures

    assert len(extracted) == 1
    signature = extracted[0]
    assert signature.name == "ExtractionSignature"
    assert signature.reason == "Regression test"
    assert signature.location == "Test suite"
    assert signature.byte_range[0] == 0
    assert signature.reference_data == data
    assert signature.contents
    assert signature.valid is True
