"""Tests for the low-code plugin API (merge / optimize / split / extract)."""

from __future__ import annotations

import io

import pytest

from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.exceptions import AsposePdfException
from aspose_pdf.lowcode import (
    ByteArrayDataSource,
    DataSource,
    FileDataSource,
    Merger,
    MergeOptions,
    OperationResult,
    Optimizer,
    OptimizeOptions,
    PdfPlugin,
    Plugin,
    PluginOptions,
    ResultContainer,
    Splitter,
    SplitOptions,
    StreamDataSource,
    TextExtractor,
    TextExtractorOptions,
)


def _pdf_bytes(page_count: int = 1) -> bytes:
    pages = [(0.0, 0.0, 200.0, 200.0)] * page_count
    return SimplePdf(pages=pages).to_bytes()


def _page_count(data: bytes) -> int:
    pdf = SimplePdf.from_bytes(data)
    try:
        return len(pdf.pages)
    finally:
        pdf.dispose()


# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------


def test_bytearray_source_round_trip():
    source = ByteArrayDataSource(b"hello")
    assert source.read_bytes() == b"hello"
    source.write_bytes(b"world")
    assert source.read_bytes() == b"world"


def test_file_source_round_trip(tmp_path):
    path = tmp_path / "out" / "doc.pdf"  # nested dir is created on write
    source = FileDataSource(path)
    source.write_bytes(b"%PDF-1.7")
    assert path.read_bytes() == b"%PDF-1.7"
    assert source.read_bytes() == b"%PDF-1.7"


def test_file_source_missing_read_raises(tmp_path):
    with pytest.raises(AsposePdfException):
        FileDataSource(tmp_path / "nope.pdf").read_bytes()


def test_stream_source_round_trip():
    read_buf = io.BytesIO(b"input-bytes")
    assert StreamDataSource(read_buf).read_bytes() == b"input-bytes"
    write_buf = io.BytesIO()
    StreamDataSource(write_buf).write_bytes(b"output-bytes")
    assert write_buf.getvalue() == b"output-bytes"


def test_base_data_source_rejects_io():
    base = DataSource()
    with pytest.raises(NotImplementedError):
        base.read_bytes()
    with pytest.raises(NotImplementedError):
        base.write_bytes(b"x")


# ---------------------------------------------------------------------------
# OperationResult / ResultContainer
# ---------------------------------------------------------------------------


def test_operation_result_bytes():
    result = OperationResult(b"\x00\x01")
    assert result.is_byte_array()
    assert not result.is_string()
    assert result.to_array() == b"\x00\x01"


def test_operation_result_string():
    result = OperationResult("text")
    assert result.is_string()
    assert result.to_string() == "text"
    assert result.to_array() == b"text"  # UTF-8 encoded


def test_operation_result_save_to_path_stream_and_source(tmp_path):
    result = OperationResult(b"data")

    path = tmp_path / "r.bin"
    result.save(path)
    assert path.read_bytes() == b"data"

    buf = io.BytesIO()
    result.save(buf)
    assert buf.getvalue() == b"data"

    sink = ByteArrayDataSource()
    result.save(sink)
    assert sink.read_bytes() == b"data"


def test_operation_result_save_rejects_bad_destination():
    with pytest.raises(TypeError):
        OperationResult(b"data").save(123)  # type: ignore[arg-type]


def test_result_container_sequence_protocol():
    a, b = OperationResult(b"a"), OperationResult(b"b")
    container = ResultContainer([a, b])
    assert len(container) == 2
    assert container[0] is a
    assert list(container) == [a, b]


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------


def test_plugin_options_add_input_output_chaining():
    options = PluginOptions()
    src, out = ByteArrayDataSource(b""), ByteArrayDataSource()
    assert options.add_input(src) is options
    assert options.add_output(out) is options
    assert options.inputs == [src]
    assert options.outputs == [out]


def test_plugin_options_reject_non_sources():
    options = PluginOptions()
    with pytest.raises(TypeError):
        options.add_input("not-a-source")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        options.add_output("not-a-source")  # type: ignore[arg-type]


def test_add_data_source_alias():
    options = MergeOptions()
    src = ByteArrayDataSource(b"")
    options.add_data_source(src)
    assert options.inputs == [src]


# ---------------------------------------------------------------------------
# Merger
# ---------------------------------------------------------------------------


def test_merger_concatenates_pages():
    options = MergeOptions()
    options.add_input(ByteArrayDataSource(_pdf_bytes(2)))
    options.add_input(ByteArrayDataSource(_pdf_bytes(3)))
    result = Merger().process(options)
    assert len(result) == 1
    assert _page_count(result[0].to_array()) == 5


def test_merger_writes_to_output_sink():
    options = MergeOptions()
    options.add_input(ByteArrayDataSource(_pdf_bytes(1)))
    options.add_input(ByteArrayDataSource(_pdf_bytes(1)))
    sink = ByteArrayDataSource()
    options.add_output(sink)
    Merger().process(options)
    assert _page_count(sink.read_bytes()) == 2


def test_merger_single_input_passthrough():
    options = MergeOptions()
    options.add_input(ByteArrayDataSource(_pdf_bytes(2)))
    result = Merger().process(options)
    assert _page_count(result[0].to_array()) == 2


def test_merger_requires_inputs():
    with pytest.raises(AsposePdfException):
        Merger().process(MergeOptions())


# ---------------------------------------------------------------------------
# Optimizer
# ---------------------------------------------------------------------------


def test_optimizer_preserves_pages_and_returns_pdf():
    options = OptimizeOptions()
    options.add_input(ByteArrayDataSource(_pdf_bytes(3)))
    result = Optimizer().process(options)
    assert len(result) == 1
    out = result[0].to_array()
    assert out.startswith(b"%PDF")
    assert _page_count(out) == 3


def test_optimizer_handles_multiple_inputs():
    options = OptimizeOptions()
    options.add_input(ByteArrayDataSource(_pdf_bytes(1)))
    options.add_input(ByteArrayDataSource(_pdf_bytes(2)))
    result = Optimizer().process(options)
    assert [_page_count(r.to_array()) for r in result] == [1, 2]


def test_optimizer_compress_only(tmp_path):
    options = OptimizeOptions(remove_unused_objects=False, compress_streams=True)
    options.add_input(ByteArrayDataSource(_pdf_bytes(1)))
    result = Optimizer().process(options)
    assert _page_count(result[0].to_array()) == 1


# ---------------------------------------------------------------------------
# Splitter
# ---------------------------------------------------------------------------


def test_splitter_one_document_per_page():
    options = SplitOptions()
    options.add_input(ByteArrayDataSource(_pdf_bytes(4)))
    result = Splitter().process(options)
    assert len(result) == 4
    assert all(_page_count(r.to_array()) == 1 for r in result)


def test_splitter_single_page_input():
    options = SplitOptions()
    options.add_input(ByteArrayDataSource(_pdf_bytes(1)))
    result = Splitter().process(options)
    assert len(result) == 1


def test_splitter_writes_outputs_paired_by_position():
    options = SplitOptions()
    options.add_input(ByteArrayDataSource(_pdf_bytes(3)))
    sinks = [ByteArrayDataSource() for _ in range(3)]
    for sink in sinks:
        options.add_output(sink)
    Splitter().process(options)
    assert all(_page_count(s.read_bytes()) == 1 for s in sinks)


def test_splitter_requires_inputs():
    with pytest.raises(AsposePdfException):
        Splitter().process(SplitOptions())


# ---------------------------------------------------------------------------
# TextExtractor
# ---------------------------------------------------------------------------


def test_text_extractor_returns_string_result():
    options = TextExtractorOptions()
    options.add_input(ByteArrayDataSource(_pdf_bytes(1)))
    result = TextExtractor().process(options)
    assert len(result) == 1
    assert result[0].is_string()
    assert isinstance(result[0].to_string(), str)


def test_text_extractor_multiple_inputs():
    options = TextExtractorOptions()
    options.add_input(ByteArrayDataSource(_pdf_bytes(1)))
    options.add_input(ByteArrayDataSource(_pdf_bytes(1)))
    result = TextExtractor().process(options)
    assert len(result) == 2


# ---------------------------------------------------------------------------
# Base plugin / misc
# ---------------------------------------------------------------------------


def test_base_plugin_process_not_implemented():
    with pytest.raises(NotImplementedError):
        PdfPlugin().process(PluginOptions())


def test_plugin_enum_values():
    assert Plugin.MERGER.value == "Merger"
    assert Plugin.SPLITTER == "Splitter"  # str-enum equality


def test_end_to_end_file_workflow(tmp_path):
    # Build two files, merge to a third, all via FileDataSource.
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    out = tmp_path / "merged.pdf"
    a.write_bytes(_pdf_bytes(1))
    b.write_bytes(_pdf_bytes(2))

    options = MergeOptions()
    options.add_input(FileDataSource(a))
    options.add_input(FileDataSource(b))
    options.add_output(FileDataSource(out))
    Merger().process(options)

    assert out.is_file()
    assert _page_count(out.read_bytes()) == 3
