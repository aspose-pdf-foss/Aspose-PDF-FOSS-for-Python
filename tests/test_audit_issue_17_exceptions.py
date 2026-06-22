"""AUDIT #17: narrowed exception handling vs bare ``except Exception``.

Ensures PdfFileEditor captures expected PDF/I/O failures but lets unexpected
errors propagate; shared exception groups exist; filter and parser fallbacks
only swallow documented recoverable types.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from aspose_pdf.exceptions import (
    AsposePdfException,
    CONTENT_PARSER_RECOVERABLE,
    PDF_OPERATION_ERRORS,
    PDF_STREAM_DECODE_ERRORS,
    PdfParseException,
)
from aspose_pdf.facades import PdfFileEditor
from aspose_pdf.engine.content_stream_parser import ContentStreamParser
from aspose_pdf.engine.filters import StreamDecoder
from aspose_pdf.signature import PdfSignature


def test_pdf_parse_exception_subclass_is_covered_by_tuple() -> None:
    assert issubclass(PdfParseException, AsposePdfException)
    assert AsposePdfException in PDF_OPERATION_ERRORS


def test_stream_decode_errors_is_broader_than_operation() -> None:
    assert len(PDF_STREAM_DECODE_ERRORS) >= len(PDF_OPERATION_ERRORS)
    assert RuntimeError in PDF_STREAM_DECODE_ERRORS


def test_content_parser_recoverable_is_subset_style() -> None:
    assert CONTENT_PARSER_RECOVERABLE
    assert KeyError in CONTENT_PARSER_RECOVERABLE


def test_pdf_file_editor_catches_valueerror_records_last_exception(
    tmp_path: Path,
) -> None:
    out = tmp_path / "out.pdf"
    with patch(
        "aspose_pdf.engine.simple_pdf.SimplePdf.from_file",
        side_effect=ValueError("simulated parse failure"),
    ):
        editor = PdfFileEditor()
        assert editor.concatenate([str(tmp_path / "x.pdf")], str(out)) is False
        assert isinstance(editor.last_exception, ValueError)


def test_pdf_file_editor_propagates_runtime_error() -> None:
    with patch(
        "aspose_pdf.engine.simple_pdf.SimplePdf.from_file",
        side_effect=RuntimeError("unexpected engine failure"),
    ):
        editor = PdfFileEditor()
        with pytest.raises(RuntimeError, match="unexpected engine failure"):
            editor.concatenate(
                ["/nonexistent/input1.pdf", "/nonexistent/input2.pdf"], "/tmp/out.pdf"
            )


def test_stream_decoder_ccitt_decode_error_raises_validation_exception() -> None:
    from aspose_pdf.engine import filters as filters_mod
    from aspose_pdf.exceptions import PdfValidationException

    raw = b"\x00\xff\xee"
    if filters_mod.CCITTDecoder is None:
        with pytest.raises(PdfValidationException, match="CCITTFaxDecode"):
            StreamDecoder._decode_ccitt(raw, None)
        return

    class _Boom:
        @staticmethod
        def decode(_data: bytes, _parms: dict) -> bytes:
            raise ValueError("CCITT decode failed")

    with patch.object(filters_mod, "CCITTDecoder", _Boom):
        with pytest.raises(PdfValidationException, match="CCITTFaxDecode"):
            StreamDecoder._decode_ccitt(raw, None)


def test_content_stream_best_effort_tolerates_tokenizer_failure() -> None:
    parser = ContentStreamParser.__new__(ContentStreamParser)

    def bad_tokenize():
        raise ValueError("tokenizer abort")

    parser._tokenize = bad_tokenize  # type: ignore[method-assign]
    parser._buffer = []
    assert parser.best_effort_extract_text() == ""


def test_pdf_signature_invalid_pkcs7_validate_status() -> None:
    data = b"X" * 20
    sig = PdfSignature(
        name="n",
        contents=b"not-pkcs7",
        byte_range=[0, 5, 10, 10],
        reference_data=data,
    )
    result = sig.validate()
    assert not result.is_valid
