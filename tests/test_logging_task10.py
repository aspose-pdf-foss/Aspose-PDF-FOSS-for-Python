import unittest

from aspose_pdf.engine.logging import get_logger
from aspose_pdf.engine.parser_exceptions import PdfValidationError


class TestLogging(unittest.TestCase):
    def test_logger_retrieval(self):
        logger = get_logger("test")
        self.assertEqual(logger.name, "aspose_pdf.test")

    def test_custom_exceptions(self):
        with self.assertRaises(PdfValidationError):
            raise PdfValidationError("test validation error")


if __name__ == "__main__":
    unittest.main()
