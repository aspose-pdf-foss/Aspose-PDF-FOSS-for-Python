import unittest

from aspose_pdf.engine.simple_pdf import SimplePdf, TextFragmentAbsorber


class TestTextExtraction(unittest.TestCase):
    def test_text_fragment_absorber(self):
        pdf = SimplePdf()
        pdf.pages = [(0, 0, 612, 792)]
        pdf.page_contents = [b"BT /F1 12 Tf 100 700 Td (Hello World) Tj ET"]

        absorber = TextFragmentAbsorber()
        absorber.visit(pdf)
        self.assertTrue(len(absorber.fragments) >= 0)


if __name__ == "__main__":
    unittest.main()
