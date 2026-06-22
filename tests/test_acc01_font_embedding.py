"""Tests for issue ACC-01: Automatic font embedding during PDF/A conversion."""

from aspose_pdf.document import Document
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.engine.cos import (
    PdfName,
    PdfDictionary,
    PdfArray,
    PdfNumber,
)

def _build_pdf_with_unembedded_font() -> bytes:
    """Build a PDF with a single page and one unembedded TrueType font using SimplePdf."""
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 612, 792)]
    pdf.page_contents = [b"BT /F1 12 Tf 100 700 Td (Hello) Tj ET"]
    
    # Initialize COS structure
    pdf._ensure_cos()
    doc = pdf._cos_doc
    
    # Create FontDescriptor (missing FontFile2)
    descriptor = PdfDictionary({
        PdfName("Type"): PdfName("FontDescriptor"),
        PdfName("FontName"): PdfName("MyTestFont"),
        PdfName("Flags"): PdfNumber(32),
        PdfName("ItalicAngle"): PdfNumber(0),
        PdfName("Ascent"): PdfNumber(700),
        PdfName("Descent"): PdfNumber(-200),
        PdfName("CapHeight"): PdfNumber(700),
        PdfName("StemV"): PdfNumber(80),
    })
    desc_ref = doc.register_object(descriptor)
    
    # Create Font
    widths = PdfArray([PdfNumber(600)] * 256)
    font = PdfDictionary({
        PdfName("Type"): PdfName("Font"),
        PdfName("Subtype"): PdfName("TrueType"),
        PdfName("BaseFont"): PdfName("MyTestFont"),
        PdfName("FontDescriptor"): desc_ref,
        PdfName("FirstChar"): PdfNumber(0),
        PdfName("LastChar"): PdfNumber(255),
        PdfName("Widths"): widths,
    })
    font_ref = doc.register_object(font)
    
    # Update Page Resources
    page_ref = doc.objects[pdf._page_obj_ids[0]]
    resources = PdfDictionary({
        PdfName("Font"): PdfDictionary({
            PdfName("F1"): font_ref
        })
    })
    page_ref.mapping[PdfName("Resources")] = resources
    
    return pdf.to_bytes()

def test_font_embedding_success(tmp_path):
    # 1. Setup font lookup directory with a dummy font file
    font_dir = tmp_path / "fonts"
    font_dir.mkdir()
    font_file = font_dir / "MyTestFont.ttf"
    dummy_font_data = b"dummy ttf data"
    font_file.write_bytes(dummy_font_data)

    # 2. Load PDF with unembedded font
    pdf_bytes = _build_pdf_with_unembedded_font()
    doc = Document()
    doc.load_from(pdf_bytes)
    
    # Verify it fails PDF/A compliance check initially
    results = doc.validate_pdfa("1b")
    assert not results.is_valid
    assert any("MyTestFont" in err and "not embedded" in err for err in results.errors)

    # 3. Convert to PDF/A with font lookup directory
    doc.convert_to_pdfa("1b", font_lookup_directory=font_dir)

    # 4. Verify compliance and embedding
    results = doc.validate_pdfa("1b")
    # The font error should be GONE.
    assert not any("MyTestFont" in err and "not embedded" in err for err in results.errors), \
        f"Font still reported as not embedded: {results.errors}"

    # Verify PDF structure (FontDescriptor should have FontFile2)
    engine = doc._engine_pdf
    page_dict = engine._get_page_dict(0)
    res = engine._resolve(page_dict.get(PdfName("Resources")))
    fonts = engine._resolve(res.get(PdfName("Font")))
    font = engine._resolve(fonts.get(PdfName("F1")))
    descriptor = engine._resolve(font.get(PdfName("FontDescriptor")))
    
    assert PdfName("FontFile2") in descriptor
    font_stream = engine._resolve(descriptor.get(PdfName("FontFile2")))
    assert font_stream.content == dummy_font_data

def test_font_embedding_missing_file(tmp_path):
    # Empty font directory
    font_dir = tmp_path / "empty_fonts"
    font_dir.mkdir()

    pdf_bytes = _build_pdf_with_unembedded_font()
    doc = Document()
    doc.load_from(pdf_bytes)
    
    # Convert without the right font in directory
    remaining = doc.convert_to_pdfa("1b", font_lookup_directory=font_dir)
    
    # Should still report the font issue
    assert any("MyTestFont" in err and "not embedded" in err for err in remaining)
