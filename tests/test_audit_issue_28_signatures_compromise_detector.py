"""AUDIT #28: ``SignaturesCompromiseDetector`` vs incremental / layering PDFs.

Cryptographic verification (``PdfSignature.valid``) intentionally ignores bytes
after the signed revision — those bytes may carry xref updates, new objects, or
annotations that viewers merge in. The detector flags **meaningful** non-comment
tails after ``ByteRange`` and annot/widget-shaped incremental blobs as compromise
indicators so callers are not misled into trusting “valid signature” alone.
"""

from __future__ import annotations

from aspose_pdf.engine.signing import SigningUtils
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.security import SignaturesCompromiseDetector
from aspose_pdf.signature import PdfSignature


def _sample_signed_pdf_bytes() -> bytes:
    return b"Hello, PDF world!"


def _make_valid_signature(data: bytes, *, tail: bytes = b"") -> PdfSignature:
    cert, key = SigningUtils.create_self_signed_cert()
    sig_bytes = SigningUtils.sign_data_pkcs7(data, cert, key)
    half = len(data) // 2
    byte_range = [0, half, half, len(data) - half]
    return PdfSignature(
        name="AUDIT28",
        contents=sig_bytes,
        byte_range=byte_range,
        reference_data=data + tail,
    )


def test_detector_not_compromised_when_signed_revision_fills_buffer():
    data = _sample_signed_pdf_bytes()
    sig = _make_valid_signature(data)
    assert sig.valid is True

    pdf = SimplePdf()
    pdf.signatures = [sig]
    r = SignaturesCompromiseDetector(pdf).check()
    assert r.has_compromised_signatures is False
    assert r.signatures_coverage == 1
    assert not r.reasons


def test_detector_flags_meaningful_incremental_tail():
    data = _sample_signed_pdf_bytes()
    tail = b"\nxref\n0 0\n"
    sig = _make_valid_signature(data, tail=tail)
    assert sig.valid is True

    pdf = SimplePdf()
    pdf.signatures = [sig]
    r = SignaturesCompromiseDetector(pdf).check()
    assert r.has_compromised_signatures is True
    assert "unsigned incremental PDF bytes" in " ".join(r.reasons)


def test_detector_ignores_comment_only_suffix_after_signed_revision():
    """Trailing ``%`` comment lines (incl. ``%%EOF``) do not imply layered payload."""
    data = _sample_signed_pdf_bytes()
    tail = b"\n% post-sign marker\n%%EOF\n"
    sig = _make_valid_signature(data, tail=tail)
    assert sig.valid is True

    pdf = SimplePdf()
    pdf.signatures = [sig]
    r = SignaturesCompromiseDetector(pdf).check()
    assert r.has_compromised_signatures is False


def test_detector_reports_annotation_layering_risk_in_incremental_tail():
    data = _sample_signed_pdf_bytes()
    tail = b"\n1 0 obj\n<< /Subtype /Widget /Rect [0 0 1 1] >>\nendobj\n"
    sig = _make_valid_signature(data, tail=tail)
    assert sig.valid is True

    pdf = SimplePdf()
    pdf.signatures = [sig]
    r = SignaturesCompromiseDetector(pdf).check()
    assert r.has_compromised_signatures is True
    joined = " ".join(r.reasons)
    assert "unsigned incremental PDF bytes" in joined
    assert "annotations/widgets (layering risk)" in joined
