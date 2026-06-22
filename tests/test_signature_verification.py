"""Tests for PdfSignature, SimplePdf and related security utilities.

These tests focus on the internal integrity verification of ``PdfSignature``
and on the integration helpers ``SimplePdf`` and ``SignaturesCompromiseDetector``.
External PDF parsing is deliberately skipped because the library under test
provides its own PDF handling which is out of scope for these unit tests.
"""

import pytest

from aspose_pdf.signature import PdfSignature
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.security import SignaturesCompromiseDetector


def test_signature_class_integrity_check(monkeypatch):
    """Validate ``PdfSignature._verify_integrity`` handling of ByteRange.

    The test creates a dummy signature object with a deliberately short
    reference_data buffer. It then patches the PKCS7 verification step to
    always succeed so that the integrity path is exercised only.
    """

    # Prepare a reference buffer of 30 bytes.
    reference = b"A" * 30

    # ByteRange that stays within the buffer (0-10 and 20-10 => total 20 bytes).
    good_range = [0, 10, 20, 10]
    sig_good = PdfSignature(
        name="TestSig",
        byte_range=good_range,
        reference_data=reference,
        contents=b"dummy",
    )

    # Patch the PKCS7 verification to avoid parsing error on "dummy" content.
    import aspose_pdf.signature

    monkeypatch.setattr(
        aspose_pdf.signature.pkcs7, "load_der_pkcs7_certificates", lambda x: []
    )
    # Force fallback to basic verification by hiding load_der_pkcs7_signed_data
    monkeypatch.delattr(
        aspose_pdf.signature.pkcs7, "load_der_pkcs7_signed_data", raising=False
    )

    assert sig_good._verify_integrity() is True

    # ByteRange that exceeds the reference_data size should fail.
    bad_range = [0, 25, 30, 10]  # 30+10 goes beyond 30 bytes buffer
    sig_bad = PdfSignature(
        name="BadSig", byte_range=bad_range, reference_data=reference, contents=b"dummy"
    )
    assert sig_bad._verify_integrity() is False


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


@pytest.mark.skip(
    reason="Full PDF extraction requires complex binary setup not covered in unit tests."
)
def test_cos_extractor_extracts_signature():
    """Placeholder for COS extractor test – skipped in CI.

    The real implementation would construct a minimal PDF binary containing a
    signature field and verify that ``SimplePdf.from_bytes`` extracts it.
    """
    pass
