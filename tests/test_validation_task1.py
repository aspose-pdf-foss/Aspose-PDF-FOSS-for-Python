import unittest

from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.document import Document


class TestValidation(unittest.TestCase):
    def test_empty_pdf_validation(self):
        pdf = SimplePdf()
        self.assertFalse(pdf.validate())

        pdf.pages = [(0, 0, 612, 792)]
        self.assertTrue(pdf.validate())

    def test_malformed_mbox_validation(self):
        pdf = SimplePdf()
        pdf.pages = [(0, 0, 612)]
        self.assertFalse(pdf.validate())

    def test_metadata_type_validation(self):
        pdf = SimplePdf()
        pdf.pages = [(0, 0, 612, 792)]
        pdf.metadata = "not a dict"
        self.assertFalse(pdf.validate())

    def test_document_wrapper_validation(self):
        doc = Document()
        self.assertFalse(doc.validate())


if __name__ == "__main__":
    unittest.main()
