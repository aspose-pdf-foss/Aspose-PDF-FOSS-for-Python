"""Resource-limit coverage for low-code and facade entry points."""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest

from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.exceptions import PdfResourceLimitException
from aspose_pdf.facades import PdfExtractor
from aspose_pdf.load_limits import PdfLoadLimits
from aspose_pdf.lowcode import (
    ByteArrayDataSource,
    DataSource,
    FileDataSource,
    MergeOptions,
    Merger,
    OptimizeOptions,
    PluginOptions,
    SplitOptions,
    StreamDataSource,
    TextExtractor,
    TextExtractorOptions,
)


class _RecordingStream(io.BytesIO):
    """Record bounded read sizes used by a stream data source."""

    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.read_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return super().read(size)


class _NoCopyBytearray(bytearray):
    """Fail if conversion to immutable bytes happens before the size check."""

    def __bytes__(self) -> bytes:
        raise AssertionError("bytearray was copied before enforcing the input limit")


class _LegacyDataSource(DataSource):
    """Model a user data source written against the original public contract."""

    def __init__(self, data: bytes) -> None:
        self.data = data

    def read_bytes(self) -> bytes:
        return self.data


def _text_pdf() -> bytes:
    pdf = SimplePdf(
        pages=[(0.0, 0.0, 200.0, 200.0)],
        page_contents=[b"BT (Hello) Tj ET"],
    )
    return pdf.to_bytes()


def test_plugin_option_constructors_accept_limits() -> None:
    limits = PdfLoadLimits(max_input_bytes=1024)

    assert PluginOptions(limits).limits is limits
    assert MergeOptions(limits).limits is limits
    assert SplitOptions(limits).limits is limits
    assert TextExtractorOptions(limits).limits is limits
    assert OptimizeOptions(limits=limits).limits is limits


def test_stream_data_source_uses_bounded_reads() -> None:
    stream = _RecordingStream(b"0123456789")
    source = StreamDataSource(stream)
    limits = PdfLoadLimits(max_input_bytes=4)

    with pytest.raises(PdfResourceLimitException, match="max_input_bytes=4"):
        source.read_bytes(limits=limits)

    assert stream.read_sizes
    assert all(0 < size <= 5 for size in stream.read_sizes)
    assert stream.tell() == 5
    assert not stream.closed


def test_file_data_source_checks_size_before_reading(tmp_path) -> None:
    path = tmp_path / "oversized.pdf"
    path.write_bytes(b"0123456789")

    with pytest.raises(PdfResourceLimitException, match="max_input_bytes=4"):
        FileDataSource(path).read_bytes(
            limits=PdfLoadLimits(max_input_bytes=4)
        )


def test_bytearray_data_source_checks_size_before_copying() -> None:
    source = ByteArrayDataSource(_NoCopyBytearray(b"0123456789"))

    with pytest.raises(PdfResourceLimitException, match="max_input_bytes=4"):
        source.read_bytes(limits=PdfLoadLimits(max_input_bytes=4))


def test_lowcode_plugin_applies_limit_before_pdf_parse() -> None:
    stream = _RecordingStream(b"not-a-pdf")
    options = MergeOptions(PdfLoadLimits(max_input_bytes=4))
    options.add_input(StreamDataSource(stream))

    with pytest.raises(PdfResourceLimitException, match="max_input_bytes=4"):
        Merger().process(options)

    assert all(0 < size <= 5 for size in stream.read_sizes)


def test_lowcode_plugin_accepts_legacy_data_source_override() -> None:
    options = TextExtractorOptions()
    options.add_input(_LegacyDataSource(_text_pdf()))

    result = TextExtractor().process(options)

    assert "Hello" in result[0].to_string()


def test_lowcode_plugin_postchecks_legacy_data_source_size() -> None:
    options = MergeOptions(PdfLoadLimits(max_input_bytes=4))
    options.add_input(_LegacyDataSource(b"not-a-pdf"))

    with pytest.raises(PdfResourceLimitException, match="max_input_bytes=4"):
        Merger().process(options)


@patch("aspose_pdf.engine.simple_pdf.SimplePdf")
def test_pdf_extractor_bind_forwards_limits(mock_simple_pdf) -> None:
    mock_simple_pdf.from_bytes.return_value = MagicMock()
    limits = PdfLoadLimits(max_content_tokens=10)
    extractor = PdfExtractor()

    extractor.bind_pdf(b"%PDF-1.7 stub", limits=limits)

    mock_simple_pdf.from_bytes.assert_called_once_with(
        b"%PDF-1.7 stub", None, limits=limits
    )


def test_pdf_extractor_reuses_bound_content_token_limit() -> None:
    extractor = PdfExtractor()
    extractor.bind_pdf(
        _text_pdf(), limits=PdfLoadLimits(max_content_tokens=2)
    )
    try:
        with pytest.raises(
            PdfResourceLimitException, match="max_content_tokens=2"
        ):
            extractor.extract_text()
    finally:
        extractor.dispose()


def test_text_extractor_plugin_propagates_content_token_limit() -> None:
    options = TextExtractorOptions(PdfLoadLimits(max_content_tokens=2))
    options.add_input(ByteArrayDataSource(_text_pdf()))

    with pytest.raises(PdfResourceLimitException, match="max_content_tokens=2"):
        TextExtractor().process(options)
