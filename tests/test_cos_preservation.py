import pytest
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.engine.cos import PdfName


def _build_minimal_pdf_bytes():
    header = b"%PDF-1.7\n"
    # Object 1: Catalog
    obj1 = b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R /CustomData 123 >>\nendobj\n"
    # Object 2: Pages (minimal)
    obj2 = b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    # Object 3: Page
    obj3 = b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << >> >>\nendobj\n"

    # Calculate offsets
    off1 = len(header)
    off2 = off1 + len(obj1)
    off3 = off2 + len(obj2)

    xref_pos = len(header) + len(obj1) + len(obj2) + len(obj3)

    xref = (
        b"xref\n0 4\n0000000000 65535 f \n%010d 00000 n \n%010d 00000 n \n%010d 00000 n \n"
        % (off1, off2, off3)
    )
    trailer = b"trailer\n<< /Size 4 /Root 1 0 R >>\n"
    startxref = b"startxref\n%d\n%%EOF" % xref_pos
    return header + obj1 + obj2 + obj3 + xref + trailer + startxref


def test_cos_preservation_roundtrip(tmp_path):
    """Verify that loading via COS and saving preserves custom data."""
    pdf_bytes = _build_minimal_pdf_bytes()

    # 1. Load using new COS loader
    pdf = SimplePdf.load_cos(pdf_bytes)

    # Verify internal COS doc is populated
    assert pdf._cos_doc is not None
    assert len(pdf._cos_doc.objects) >= 3

    # Verify custom data is present in the object graph
    catalog = pdf._cos_doc.objects[1]
    assert catalog[PdfName("CustomData")].value == 123

    # 2. Save using new COS saver
    out_path = tmp_path / "output.pdf"
    pdf.save_cos(out_path)

    # 3. Verify output
    assert out_path.exists()
    out_bytes = out_path.read_bytes()

    # Parse back to verify structure preservation
    pdf2 = SimplePdf.load_cos(out_bytes)
    catalog2 = pdf2._cos_doc.objects[1]
    # The /CustomData should still be there!
    assert catalog2[PdfName("CustomData")].value == 123

    # 4. Verify legacy compatibility (should still be loadable by old parser)
    pdf3 = SimplePdf.from_bytes(out_bytes)
    assert pdf3.page_count >= 1  # Legacy parser finding the page


def test_mixed_mode_error(tmp_path):
    """Verify error when saving COS without loading COS."""
    pdf = SimplePdf()  # Legacy creation
    out_path = tmp_path / "fail.pdf"

    # Should fail because no _cos_doc
    with pytest.raises(Exception, match="No COS document loaded"):
        pdf.save_cos(out_path)
