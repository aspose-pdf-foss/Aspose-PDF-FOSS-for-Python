import unittest

from aspose_pdf import Document
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

    def test_identical_font_resources_are_reused(self):
        documents = []
        try:
            for text in ("Part one", "Part two"):
                document = Document()
                page = document.pages.add()
                page.add_text(text, x=72, y=720, font_size=18)
                documents.append(document)

            merged = SimplePdf.merge(
                *(document._engine_pdf for document in documents)
            )

            self.assertEqual(set(merged.fonts), {"F1"})
            self.assertIn(b"/F1 18 Tf", merged.page_contents[0])
            self.assertIn(b"/F1 18 Tf", merged.page_contents[1])
            self.assertNotIn(b"/F1_1_1", merged.page_contents[1])
        finally:
            for document in documents:
                document.dispose()


if __name__ == "__main__":
    unittest.main()
