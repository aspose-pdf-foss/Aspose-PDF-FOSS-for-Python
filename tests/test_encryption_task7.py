import unittest

from aspose_pdf.engine.simple_pdf import SimplePdf


class TestEncryption(unittest.TestCase):
    def test_aes256_setup(self):
        pdf = SimplePdf()
        pdf.pages = [(0, 0, 612, 792)]

        pdf.encrypt("user", "owner", algorithm="AES-256")
        self.assertEqual(len(pdf.encryption_key), 32)
        self.assertEqual(pdf._encryption_revision, 6)
        self.assertTrue(hasattr(pdf, "UE"))
        self.assertTrue(hasattr(pdf, "OE"))


if __name__ == "__main__":
    unittest.main()
