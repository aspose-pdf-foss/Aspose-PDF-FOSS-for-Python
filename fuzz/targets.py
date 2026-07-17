"""Bounded fuzz targets for the parser, tokenizer, content, and filters."""

from __future__ import annotations

import struct
import zlib
from collections.abc import Callable

from aspose_pdf.engine.content_stream_parser import ContentStreamParser
from aspose_pdf.engine.filters import StreamDecoder
from aspose_pdf.engine.pdf_parser_cos import PdfCosParser, _Tokenizer
from aspose_pdf.exceptions import AsposePdfException
from aspose_pdf.load_limits import PdfLoadLimits

_MAX_INPUT_BYTES = 1024 * 1024
_LIMITS = PdfLoadLimits(
    max_input_bytes=_MAX_INPUT_BYTES,
    max_objects=2_000,
    max_xref_sections=16,
    max_nesting_depth=32,
    max_container_items=10_000,
    max_object_bytes=256 * 1024,
    max_decoded_stream_bytes=256 * 1024,
    max_codec_work_bytes=2 * 1024 * 1024,
    max_compression_ratio=100,
    max_content_stream_bytes=256 * 1024,
    max_total_decoded_bytes=1024 * 1024,
    max_stream_filters=8,
    max_pages=100,
    max_image_pixels=1024 * 1024,
    max_raster_pixels=1024 * 1024,
    max_content_tokens=100_000,
)
_EXPECTED_ERRORS = (
    AsposePdfException,
    EOFError,
    IndexError,
    KeyError,
    TypeError,
    UnicodeError,
    ValueError,
    struct.error,
    zlib.error,
)
_FILTERS = (
    None,
    "FlateDecode",
    "ASCIIHexDecode",
    "ASCII85Decode",
    "RunLengthDecode",
    "LZWDecode",
)


def _within_input_limit(data: bytes) -> bool:
    return len(data) <= _MAX_INPUT_BYTES


def fuzz_tokenizer(data: bytes) -> None:
    if not _within_input_limit(data):
        return
    tokenizer = _Tokenizer(data.decode("latin-1"), _LIMITS)
    try:
        while tokenizer.pos < tokenizer.len:
            tokenizer._consume_whitespace()
            if tokenizer.pos >= tokenizer.len:
                break
            tokenizer.read()
    except _EXPECTED_ERRORS:
        return


def fuzz_cos(data: bytes) -> None:
    if not _within_input_limit(data):
        return
    try:
        PdfCosParser(data, limits=_LIMITS).parse()
    except _EXPECTED_ERRORS:
        return


def fuzz_content(data: bytes) -> None:
    if not _within_input_limit(data):
        return
    try:
        parser = ContentStreamParser(data, {}, limits=_LIMITS)
        parser.extract_text()
        parser.best_effort_extract_text()
    except _EXPECTED_ERRORS:
        return


def fuzz_filters(data: bytes) -> None:
    if not data or not _within_input_limit(data):
        return
    filter_name = _FILTERS[data[0] % len(_FILTERS)]
    try:
        StreamDecoder.decode(
            data[1:],
            filter_name,
            None,
            limits=_LIMITS,
            max_output_bytes=256 * 1024,
        )
    except _EXPECTED_ERRORS:
        return


TARGETS: dict[str, Callable[[bytes], None]] = {
    "tokenizer": fuzz_tokenizer,
    "cos": fuzz_cos,
    "content": fuzz_content,
    "filters": fuzz_filters,
}
