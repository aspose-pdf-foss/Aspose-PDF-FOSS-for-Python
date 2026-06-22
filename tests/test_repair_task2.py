import unittest

from aspose_pdf.engine.simple_pdf import SimplePdf


class TestRepair(unittest.TestCase):
    def test_mbox_ordering_repair(self):
        pdf = SimplePdf()
        pdf.pages = [(612, 792, 0, 0)]
        pdf.repair()
        self.assertEqual(pdf.pages[0], (0, 0, 612, 792))

    def test_corrupted_images_repair(self):
        pdf = SimplePdf()
        pdf.pages = [(0, 0, 612, 792)]
        pdf.images = {"img1": b"valid", "img2": 123}
        pdf.repair()
        self.assertIn("img1", pdf.images)
        self.assertNotIn("img2", pdf.images)

    def test_metadata_repair(self):
        pdf = SimplePdf()
        pdf.pages = [(0, 0, 612, 792)]
        pdf.metadata = None
        pdf.repair()
        self.assertEqual(pdf.metadata, {})


if __name__ == "__main__":
    unittest.main()
