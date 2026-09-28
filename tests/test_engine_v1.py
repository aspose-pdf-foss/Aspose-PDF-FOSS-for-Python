from pathlib import Path

import pytest

from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.exceptions import PdfSecurityException
from aspose_pdf.facades import PdfExtractor, PdfFileEditor
from tests.helpers_make_pdfs import write_min_pdf


def test_add_page(tmp_path: Path):
    src = tmp_path / "src.pdf"
    out = tmp_path / "out.pdf"
    write_min_pdf(src, page_count=1)

    editor = PdfFileEditor()
    editor.concatenate([src, src], out)  # Simulating add page by merging

    res = SimplePdf.from_file(out)
    assert res.page_count == 2


def test_remove_page(tmp_path: Path):
    src = tmp_path / "src.pdf"
    out = tmp_path / "out.pdf"
    write_min_pdf(src, page_count=3)

    editor = PdfFileEditor()
    editor.delete(src, out, page_from=2, page_to=2)

    res = SimplePdf.from_file(out)
    assert res.page_count == 2


def test_encrypt_decrypt(tmp_path: Path):
    src = tmp_path / "plain.pdf"
    enc = tmp_path / "enc.pdf"

    # create an unencrypted PDF
    writer = SimplePdf([(0, 0, 100, 100)])
    writer.save(src)

    # encrypt the PDF using the Document facade
    from aspose_pdf.generated.document import Document

    doc = Document(str(src))
    doc.encrypt("userpw")
    doc.save(str(enc))

    # ensure the encrypted file contains the /Encrypt entry
    raw_enc = enc.read_bytes()
    assert b"/Encrypt" in raw_enc

    # opening without a password should raise PdfSecurityException
    with pytest.raises(PdfSecurityException, match="Password required"):
        SimplePdf.from_file(enc)

    # opening with the correct password should succeed
    decrypted = SimplePdf.from_file(enc, password="userpw")
    assert decrypted.page_count == writer.page_count

    # Still encrypted on disk — verify file has /Encrypt
    raw_enc = enc.read_bytes()
    assert b"/Encrypt" in raw_enc


# @pytest.mark.skip(reason="Not implemented yet")
def test_extract_text(tmp_path: Path):
    src = tmp_path / "src.pdf"

    # 1. Create a SimplePdf with text content
    pages = [(0, 0, 612, 792)]
    # Standard PDF text operations:
    # BT: Begin Text, /F1 24 Tf: Font, 100 700 Td: Pos, (Hello World) Tj: Text, ET: End
    content = b"BT /F1 24 Tf 100 700 Td (Hello World) Tj ET"

    pdf = SimplePdf(pages=pages, page_contents=[content])
    pdf.save(src)

    # 2. Extract text using Facade
    extractor = PdfExtractor()
    extractor.bind_pdf(src)
    extractor.extract_text()

    text = extractor.get_text()

    # 3. Verify
    assert "Hello World" in text
