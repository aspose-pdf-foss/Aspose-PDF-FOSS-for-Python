import unittest

from aspose_pdf.engine.simple_pdf import SimplePdf


class TestOptimization(unittest.TestCase):
    def test_image_deduplication(self):
        pdf = SimplePdf()
        pdf.pages = [(0, 0, 612, 792)]
        data = b"image content"
        pdf.images = {"img1": data, "img2": data}
        pdf.page_contents = [b"/img2 Do"]

        pdf.optimize()
        self.assertEqual(len(pdf.images), 1)
        self.assertIn("img1", pdf.images)
        self.assertIn(b"/img1 Do", pdf.page_contents[0])


if __name__ == "__main__":
    unittest.main()
