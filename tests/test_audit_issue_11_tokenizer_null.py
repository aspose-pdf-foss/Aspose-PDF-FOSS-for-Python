"""AUDIT #11: COS tokenizer must parse PDF ``null`` and reject ``n…`` false positives."""

import pytest

from aspose_pdf.engine.cos import PdfDictionary, PdfName, PdfNull
from aspose_pdf.engine.pdf_parser_cos import _Tokenizer
from aspose_pdf.exceptions import PdfParseException


def test_tokenizer_null_keyword():
    t = _Tokenizer(" null ")
    obj = t.read()
    assert isinstance(obj, PdfNull)


def test_tokenizer_null_in_dictionary():
    t = _Tokenizer("<< /K null >>")
    d = t._read_dictionary()
    assert isinstance(d, PdfDictionary)
    assert isinstance(d[PdfName("K")], PdfNull)


def test_tokenizer_n_prefix_that_is_not_null_raises():
    with pytest.raises(PdfParseException):
        _Tokenizer("nope").read()


def test_tokenizer_partial_nul_raises():
    with pytest.raises(PdfParseException):
        _Tokenizer("nul ").read()


def test_tokenizer_n_alone_raises():
    with pytest.raises(PdfParseException):
        _Tokenizer("n").read()
