import unittest

from aspose_pdf.engine.simple_pdf import SimplePdf


class TestMerging(unittest.TestCase):
    def test_image_collision_merging(self):
        pdf1 = SimplePdf()
        pdf1.pages = [(0, 0, 612, 792)]
        pdf1.images = {"img": b"data1"}
        pdf1.page_contents = [b"/img Do"]

        pdf2 = SimplePdf()
        pdf2.pages = [(0, 0, 612, 792)]
        pdf2.images = {"img": b"data2"}
        pdf2.page_contents = [b"/img Do"]

        merged = SimplePdf.merge(pdf1, pdf2)
        self.assertEqual(len(merged.images), 2)
        self.assertTrue(any(name.startswith("img_1_") for name in merged.images))
        self.assertIn(b"img_1_", merged.page_contents[1])


if __name__ == "__main__":
    unittest.main()
