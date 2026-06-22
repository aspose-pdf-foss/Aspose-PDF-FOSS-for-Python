import unittest

from aspose_pdf.engine.simple_pdf import SimplePdf


class TestPDFA(unittest.TestCase):
    def test_pdfa_compliance_check(self):
        pdf = SimplePdf()
        pdf.pages = [(0, 0, 612, 792)]
        issues = pdf.check_pdfa_compliance()
        self.assertEqual(len(issues), 0)

        pdf.encrypted = True
        issues = pdf.check_pdfa_compliance()
        self.assertTrue(
            any("Encryption" in issue or "encryption" in issue for issue in issues)
        )


if __name__ == "__main__":
    unittest.main()
