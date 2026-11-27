"""Tests for Document constructor password as positional argument.

This test module verifies that Document accepts password as a second
positional argument for compatibility with Aspose.PDF for Python via .NET.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document
from aspose_pdf.load_options import CdrLoadOptions, SvgLoadOptions


class TestDocumentPasswordPositional:
    """Tests for password as a positional argument."""

    def test_document_with_no_args(self):
        """Document() creates an empty document."""
        doc = Document()
        assert doc.page_count == 0

    def test_document_with_source_only(self):
        """Document(source) loads PDF from source."""
        doc = Document()
        doc.pages.add()
        data = doc.to_bytes()
        reloaded = Document(io.BytesIO(data))
        assert reloaded.page_count == 1

    def test_document_with_source_and_password_positional(self):
        """Document(source, password) accepts password as second positional."""
        doc = Document()
        doc.pages.add()
        data = doc.to_bytes()
        reloaded = Document(io.BytesIO(data), "secret123")
        assert reloaded.page_count == 1

    def test_document_with_source_and_password_keyword(self):
        """Document(source, password=...) still works with keyword argument."""
        doc = Document()
        doc.pages.add()
        data = doc.to_bytes()
        reloaded = Document(io.BytesIO(data), password="secret123")
        assert reloaded.page_count == 1

    def test_document_with_source_password_and_is_managed_stream_false(self):
        """Document(source, password, is_managed_stream=False) is accepted."""
        doc = Document()
        doc.pages.add()
        data = doc.to_bytes()
        reloaded = Document(io.BytesIO(data), "secret123", is_managed_stream=False)
        assert reloaded.page_count == 1

    def test_document_with_source_password_and_is_managed_stream_omitted(self):
        """Document(source, password) works without is_managed_stream."""
        doc = Document()
        doc.pages.add()
        data = doc.to_bytes()
        reloaded = Document(io.BytesIO(data), "secret123")
        assert reloaded.page_count == 1

    def test_document_with_source_password_and_is_managed_stream_true_raises(self):
        """Document(source, password, is_managed_stream=True) raises TypeError."""
        doc = Document()
        doc.pages.add()
        data = doc.to_bytes()
        with pytest.raises(TypeError, match="is_managed_stream is not supported"):
            Document(io.BytesIO(data), "secret123", is_managed_stream=True)

    def test_document_with_source_and_options_raises(self):
        """Document(source, options) raises for non-None options."""
        doc = Document()
        doc.pages.add()
        data = doc.to_bytes()
        from aspose_pdf.exceptions import UnsupportedFeatureException
        with pytest.raises(UnsupportedFeatureException, match="SVG import"):
            Document(io.BytesIO(data), options=SvgLoadOptions())

    def test_document_with_source_none_options_raises(self):
        """Document(source, None) works; Document(source, None, options=...) raises."""
        doc = Document()
        doc.pages.add()
        data = doc.to_bytes()
        # Document(source, None) - None is password, this is fine
        reloaded = Document(io.BytesIO(data), None)
        assert reloaded.page_count == 1
        from aspose_pdf.exceptions import UnsupportedFeatureException
        # Document(source, None, options=...) - None is password (second positional),
        # options=... is keyword to third positional arg. The options=... triggers rejection.
        with pytest.raises(UnsupportedFeatureException, match="SVG import"):
            Document(io.BytesIO(data), None, options=SvgLoadOptions())

    def test_document_with_source_and_none_password(self):
        """Document(source, None) works (password=None means no password)."""
        doc = Document()
        doc.pages.add()
        data = doc.to_bytes()
        reloaded = Document(io.BytesIO(data), None)
        assert reloaded.page_count == 1

    def test_document_with_source_password_keyword_only(self):
        """Document(source, password=...) works with keyword-only password."""
        doc = Document()
        doc.pages.add()
        data = doc.to_bytes()
        reloaded = Document(io.BytesIO(data), password="secret123")
        assert reloaded.page_count == 1

    def test_document_with_source_none_and_is_managed_stream_false(self):
        """Document(source, None, is_managed_stream=False) works."""
        doc = Document()
        doc.pages.add()
        data = doc.to_bytes()
        reloaded = Document(io.BytesIO(data), None, is_managed_stream=False)
        assert reloaded.page_count == 1

    def test_document_password_requires_source(self):
        """password without source raises TypeError."""
        with pytest.raises(TypeError, match="password requires a load source"):
            Document(password="secret123")

    def test_document_certificate_requires_source(self):
        """certificate without source raises TypeError."""
        with pytest.raises(TypeError, match="certificate/private_key require a load source"):
            Document(certificate="some_cert")

    def test_document_private_key_requires_source(self):
        """private_key without source raises TypeError."""
        with pytest.raises(TypeError, match="certificate/private_key require a load source"):
            Document(private_key="some_key")

    def test_document_key_encryption_key_requires_source(self):
        """key_encryption_key without source raises TypeError."""
        with pytest.raises(TypeError, match="key_encryption_key requires a load source"):
            Document(key_encryption_key=b"some_key_bytes")


class TestDocumentCdrLoadOptions:
    """Tests for CdrLoadOptions (another unsupported format)."""

    def test_document_with_cdr_options_raises(self):
        """Document(source, options=CdrLoadOptions()) raises UnsupportedFeatureException."""
        doc = Document()
        doc.pages.add()
        data = doc.to_bytes()
        with pytest.raises(Exception, match="CorelDRAW"):
            Document(io.BytesIO(data), options=CdrLoadOptions())


class TestDocumentFilepath:
    """Tests for Document with file paths."""

    def test_document_with_filepath_and_password_positional(self, tmp_path):
        """Document(filepath, password) works with file path."""
        doc = Document()
        doc.pages.add()
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(doc.to_bytes())
        reloaded = Document(str(pdf_path), "secret123")
        assert reloaded.page_count == 1

    def test_document_with_filepath_and_password_keyword(self, tmp_path):
        """Document(filepath, password=...) works with file path."""
        doc = Document()
        doc.pages.add()
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(doc.to_bytes())
        reloaded = Document(str(pdf_path), password="secret123")
        assert reloaded.page_count == 1

    def test_document_with_bytes_and_password_positional(self):
        """Document(bytes, password) works with bytes."""
        doc = Document()
        doc.pages.add()
        data = doc.to_bytes()
        reloaded = Document(data, "secret123")
        assert reloaded.page_count == 1

    def test_document_with_bytearray_and_password_positional(self):
        """Document(bytearray, password) works with bytearray."""
        doc = Document()
        doc.pages.add()
        data = doc.to_bytes()
        reloaded = Document(bytearray(data), "secret123")
        assert reloaded.page_count == 1


class TestDocumentPositionalOverloads:
    """Tests for positional overload handling."""

    def test_document_source_password_is_managed_stream_positional_true_raises(self):
        """Document(source, password, True) raises TypeError for is_managed_stream."""
        doc = Document()
        doc.pages.add()
        data = doc.to_bytes()
        with pytest.raises(TypeError, match="is_managed_stream is not supported"):
            Document(io.BytesIO(data), "secret123", True)

    def test_document_source_password_is_managed_stream_positional_false(self):
        """Document(source, password, False) works with positional is_managed_stream."""
        doc = Document()
        doc.pages.add()
        data = doc.to_bytes()
        reloaded = Document(io.BytesIO(data), "secret123", False)
        assert reloaded.page_count == 1

    def test_document_source_options_positional_raises(self):
        """Document(source, options_obj) raises UnsupportedFeatureException."""
        doc = Document()
        doc.pages.add()
        data = doc.to_bytes()
        from aspose_pdf.exceptions import UnsupportedFeatureException
        with pytest.raises(UnsupportedFeatureException, match="SVG import"):
            Document(io.BytesIO(data), SvgLoadOptions())

    def test_document_source_cdr_options_positional_raises(self):
        """Document(source, CdrLoadOptions()) raises UnsupportedFeatureException."""
        doc = Document()
        doc.pages.add()
        data = doc.to_bytes()
        from aspose_pdf.exceptions import UnsupportedFeatureException
        with pytest.raises(UnsupportedFeatureException, match="CorelDRAW"):
            Document(io.BytesIO(data), CdrLoadOptions())

    def test_document_source_none_options_positional_raises(self):
        """Document(source, None, options_obj) raises TypeError for is_managed_stream."""
        doc = Document()
        doc.pages.add()
        data = doc.to_bytes()
        # Third positional (options_obj) is treated as is_managed_stream when second is None (password)
        with pytest.raises(TypeError, match="is_managed_stream is not supported"):
            Document(io.BytesIO(data), None, SvgLoadOptions())

    def test_document_source_password_is_managed_stream_positional_omitted(self):
        """Document(source, password) works without is_managed_stream positional."""
        doc = Document()
        doc.pages.add()
        data = doc.to_bytes()
        reloaded = Document(io.BytesIO(data), "secret123")
        assert reloaded.page_count == 1

    def test_document_source_password_is_managed_stream_positional_none(self):
        """Document(source, password, None) works (is_managed_stream=None ignored)."""
        doc = Document()
        doc.pages.add()
        data = doc.to_bytes()
        reloaded = Document(io.BytesIO(data), "secret123", None)
        assert reloaded.page_count == 1
