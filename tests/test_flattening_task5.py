import unittest

from aspose_pdf.engine.simple_pdf import SimplePdf


class TestFlattening(unittest.TestCase):
    def test_flatten_stub(self):
        pdf = SimplePdf()
        pdf.pages = [(0, 0, 612, 792)]
        pdf.page_contents = [b""]
        pdf._ensure_cos()

        pdf.flatten()
        self.assertEqual(len(pdf.pages), 1)


if __name__ == "__main__":
    unittest.main()
