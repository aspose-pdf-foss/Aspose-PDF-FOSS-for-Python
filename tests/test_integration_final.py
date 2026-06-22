import unittest

from aspose_pdf.engine.simple_pdf import SimplePdf


class TestIntegrationFinal(unittest.TestCase):
    def test_all_new_methods(self):
        pdf = SimplePdf()
        pdf.pages = [(0, 0, 612, 792)]
        pdf.page_contents = [b""]
        pdf._ensure_cos()

        # Task 1 & 2
        pdf.validate()
        pdf.repair()

        # Task 4
        other = SimplePdf()
        other.pages = [(0, 0, 1, 1)]
        other.page_contents = [b""]
        pdf.merge(other)

        # Task 5
        pdf.flatten()

        # Task 6
        pdf.optimize()

        # Task 7
        pdf.encrypt("user", "owner", algorithm="AES-256")

        # Task 9
        pdf.check_pdfa_compliance()

        # Task 10
        from aspose_pdf.engine.logging import get_logger

        logger = get_logger("integration")
        logger.info("Integration test complete")


if __name__ == "__main__":
    unittest.main()
