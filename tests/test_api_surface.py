from aspose_pdf.document import Document
from aspose_pdf.facades import PdfFileEditor, PdfExtractor


def test_document_has_core_methods():
    doc = Document()  # should not crash
    for name in [
        "load_from",
        "save",
        "dispose",
        "merge",
        "encrypt",
        "decrypt",
        "validate",
        "check",
    ]:
        assert hasattr(doc, name), f"Document missing method: {name}"


def test_facades_constructible():
    e = PdfFileEditor()
    x = PdfExtractor()
    assert e is not None
    assert x is not None
