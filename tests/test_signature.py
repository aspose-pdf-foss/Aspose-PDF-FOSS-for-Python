import pytest

from aspose_pdf.engine.simple_pdf import SimplePdf


def test_add_signature_metadata():
    pdf = SimplePdf()
    pdf.add_signature(
        reason="Test reason", contact="test@example.com", location="Test location"
    )
    pdf_bytes = pdf.to_bytes()
    # ISO 32000-1 table 252: a signature dictionary's /Type is /Sig, not
    # /Signature -- which is what this used to write.
    assert b"/Type /Sig " in pdf_bytes
    assert b"/Type /Signature" not in pdf_bytes
    assert b"/Reason (Test reason)" in pdf_bytes


def test_signature_object_presence():
    pdf = SimplePdf()
    pdf.add_signature(reason="R", contact="C", location="L")
    pdf_bytes = pdf.to_bytes()
    assert b"/Contents" in pdf_bytes


def test_signature_roundtrip():
    pdf = SimplePdf()
    pdf.add_signature(reason="Roundtrip", contact="r@example.com", location="Nowhere")
    pdf_bytes = pdf.to_bytes()
    pdf2 = SimplePdf.from_bytes(pdf_bytes)
    with pytest.raises(Exception):
        pdf2.add_signature(reason="Again", contact="a", location="X")
