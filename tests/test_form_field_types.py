"""Tests for form field types: checkbox, radio, listbox, combobox."""

import unittest

from aspose_pdf.engine.simple_pdf import CosExtractor
from aspose_pdf.engine.cos import (
    PdfDocument,
    PdfDictionary,
    PdfArray,
    PdfName,
    PdfString,
    PdfNumber,
)


def _make_cos_doc_with_acroform(fields_spec):
    """Build minimal COS document with AcroForm for testing."""
    doc = PdfDocument()
    doc.objects = {}
    fields_arr = PdfArray([])
    for name, ft, ff, v in fields_spec:
        fd = PdfDictionary(
            {
                PdfName("T"): PdfString(name.encode()),
                PdfName("FT"): PdfName(ft),
                PdfName("Ff"): PdfNumber(ff),
            }
        )
        if v is not None:
            fd[PdfName("V")] = v
        fields_arr.items.append(fd)
    acroform = PdfDictionary({PdfName("Fields"): fields_arr})
    root = PdfDictionary({PdfName("AcroForm"): acroform})
    doc.trailer = PdfDictionary({PdfName("Root"): root})
    return doc


class TestFormFieldTypesExtraction(unittest.TestCase):
    """Test CosExtractor extracts values correctly for all field types."""

    def test_text_field(self):
        doc = _make_cos_doc_with_acroform(
            [
                ("txt1", "Tx", 0, PdfString(b"hello")),
            ]
        )
        ext = CosExtractor(doc, b"")
        result = ext.extract_form_fields()
        self.assertIn("txt1", result)
        self.assertEqual(result["txt1"]["value"], "hello")
        self.assertEqual(result["txt1"]["type"], "text")

    def test_checkbox_checked(self):
        doc = _make_cos_doc_with_acroform(
            [
                ("cb1", "Btn", 0, PdfName("Yes")),
            ]
        )
        ext = CosExtractor(doc, b"")
        result = ext.extract_form_fields()
        self.assertEqual(result["cb1"]["value"], True)
        self.assertEqual(result["cb1"]["type"], "checkbox")

    def test_checkbox_unchecked(self):
        doc = _make_cos_doc_with_acroform(
            [
                ("cb2", "Btn", 0, PdfName("Off")),
            ]
        )
        ext = CosExtractor(doc, b"")
        result = ext.extract_form_fields()
        self.assertEqual(result["cb2"]["value"], False)
        self.assertEqual(result["cb2"]["type"], "checkbox")

    def test_checkbox_no_value(self):
        doc = _make_cos_doc_with_acroform(
            [
                ("cb3", "Btn", 0, None),
            ]
        )
        ext = CosExtractor(doc, b"")
        result = ext.extract_form_fields()
        self.assertEqual(result["cb3"]["value"], False)
        self.assertEqual(result["cb3"]["type"], "checkbox")

    def test_radio(self):
        doc = _make_cos_doc_with_acroform(
            [
                ("r1", "Btn", 1 << 15, PdfName("OptionB")),
            ]
        )
        ext = CosExtractor(doc, b"")
        result = ext.extract_form_fields()
        self.assertEqual(result["r1"]["value"], "OptionB")
        self.assertEqual(result["r1"]["type"], "radio")

    def test_choice_single(self):
        doc = _make_cos_doc_with_acroform(
            [
                ("ch1", "Ch", 0, PdfString(b"Item2")),
            ]
        )
        ext = CosExtractor(doc, b"")
        result = ext.extract_form_fields()
        self.assertEqual(result["ch1"]["value"], "Item2")
        self.assertEqual(result["ch1"]["type"], "listbox")

    def test_choice_multi(self):
        doc = _make_cos_doc_with_acroform(
            [
                ("ch2", "Ch", 0, PdfArray([PdfString(b"A"), PdfString(b"C")])),
            ]
        )
        ext = CosExtractor(doc, b"")
        result = ext.extract_form_fields()
        self.assertEqual(result["ch2"]["value"], ["A", "C"])
        self.assertEqual(result["ch2"]["type"], "listbox")

    def test_combobox(self):
        doc = _make_cos_doc_with_acroform(
            [
                ("combo1", "Ch", 1 << 18, PdfString(b"Selected")),
            ]
        )
        ext = CosExtractor(doc, b"")
        result = ext.extract_form_fields()
        self.assertEqual(result["combo1"]["value"], "Selected")
        self.assertEqual(result["combo1"]["type"], "combobox")


if __name__ == "__main__":
    unittest.main()
