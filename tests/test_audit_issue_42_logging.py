"""AUDIT #42: swallowed recoveries and boolean facade failures log at WARNING (default visibility)."""

from __future__ import annotations

import logging
import zlib
from unittest.mock import MagicMock, patch

import pytest

from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.facades import PdfFileEditor


def _minimal_valid_pdf() -> bytes:
    return (
        b"%PDF-1.7\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Count 1 /Kids [3 0 R] >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /MediaBox [0 0 612 792] >>\nendobj\n"
        b"xref\n0 4\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000058 00000 n \n"
        b"0000000115 00000 n \n"
        b"trailer\n<< /Root 1 0 R /Size 4 >>\n"
        b"startxref\n170\n%%EOF"
    )


def test_pdf_file_editor_operation_fail_logs_warning(
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="aspose_pdf.facades")
    out = tmp_path / "out.pdf"
    with patch(
        "aspose_pdf.engine.simple_pdf.SimplePdf.from_file",
        side_effect=ValueError("simulated load failure"),
    ):
        editor = PdfFileEditor()
        assert editor.concatenate([str(tmp_path / "a.pdf")], str(out)) is False
    assert any("PdfFileEditor operation failed" in r.message for r in caplog.records), (
        caplog.text
    )


def test_pdf_file_editor_dispose_failure_logs_warning(
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="aspose_pdf.facades")
    src = tmp_path / "in.pdf"
    src.write_bytes(_minimal_valid_pdf())
    loaded = SimplePdf.from_file(str(src))

    def _from_file(_p: str) -> SimplePdf:
        return loaded

    out = tmp_path / "out.pdf"
    with patch(
        "aspose_pdf.engine.simple_pdf.SimplePdf.from_file", side_effect=_from_file
    ):
        with patch.object(loaded, "dispose", side_effect=OSError("dispose failed")):
            editor = PdfFileEditor()
            assert editor.add_page_break(str(src), str(out)) is True
    assert any(
        "dispose failed after add_page_break" in r.message for r in caplog.records
    ), caplog.text


def test_from_bytes_safe_logs_primary_parse_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="aspose_pdf")
    broken = b"%PDF-1.7\nthis is not a valid pdf structure\n%%EOF"
    with patch.object(
        SimplePdf,
        "from_bytes",
        side_effect=ValueError("simulated eager parse failure"),
    ):
        _ = SimplePdf.from_bytes_safe(broken)
    assert any(
        "from_bytes_safe: eager parse failed" in r.message for r in caplog.records
    ), caplog.text


def test_repair_logs_cos_reparse_failure(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="aspose_pdf")
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 612, 792)]
    pdf.page_contents = [b""]
    pdf.metadata = {}
    pdf._raw_bytes = b"%PDF-1.4\nstub\n%%EOF"
    pdf._cos_doc = None
    mock_parser = MagicMock()
    mock_parser.parse.side_effect = ValueError("simulated COS parse failure")
    with patch(
        "aspose_pdf.engine.pdf_parser_cos.PdfCosParser", return_value=mock_parser
    ):
        assert pdf.repair() is True
    assert any("repair: COS re-parse failed" in r.message for r in caplog.records), (
        caplog.text
    )


def test_optimize_logs_traversal_skip(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="aspose_pdf")
    pdf = SimplePdf()
    mock_doc = MagicMock()
    # Reachability traversal explodes while reading the trailer; optimize() must
    # swallow it and log a warning rather than crash.
    mock_doc.trailer.mapping.values.side_effect = KeyError("missing indirect object")
    mock_doc.objects = {}
    pdf._cos_doc = mock_doc
    pdf.optimize()
    assert any(
        "optimize: garbage-collection skipped" in r.message
        for r in caplog.records
    ), caplog.text


def test_compress_streams_logs_compression_skip(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="aspose_pdf")
    from aspose_pdf.engine.cos import PdfStream

    pdf = SimplePdf()
    # Single uncompressed stream so compress_streams attempts zlib.compress
    stm = PdfStream(b"hello")
    doc = MagicMock()
    doc.objects = {1: stm}
    pdf._cos_doc = doc
    with patch(
        "aspose_pdf.engine.simple_pdf.zlib.compress",
        side_effect=zlib.error("simulated compression failure"),
    ):
        pdf.compress_streams()
    assert any(
        "compress_streams: skipped stream compression" in r.message
        for r in caplog.records
    ), caplog.text
