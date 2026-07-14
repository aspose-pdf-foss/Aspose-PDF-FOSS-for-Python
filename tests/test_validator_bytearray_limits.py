"""Regression tests for validator and bytearray input hardening."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Callable

import pytest

import aspose_pdf.document as document_module
from aspose_pdf.document import Document
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.exceptions import PdfResourceLimitException
from aspose_pdf.generated.document import Document as GeneratedDocument
from aspose_pdf.load_limits import PdfLoadLimits
from aspose_pdf.pdfa import (
    PdfAValidateOptions,
    PdfAValidationResult,
    PdfAValidator,
)
from aspose_pdf.pdfua import (
    PdfUaValidateOptions,
    PdfUaValidationResult,
    PdfUaValidator,
)


class _CopyGuardBytearray(bytearray):
    """Fail if a loader copies this value before enforcing its input limit."""

    def __bytes__(self) -> bytes:
        raise AssertionError("bytearray was copied before its size was checked")


class _ShortReadStream(io.BytesIO):
    """Return short chunks while recording every requested read size."""

    def __init__(self, data: bytes, *, chunk_size: int = 3) -> None:
        super().__init__(data)
        self.chunk_size = chunk_size
        self.read_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        if size < 0:
            raise AssertionError("validator input streams must use bounded reads")
        return super().read(min(size, self.chunk_size))


def _load_document(data: bytearray, limits: PdfLoadLimits) -> None:
    document = Document()
    try:
        document.load_from(data, limits=limits)
    finally:
        document.dispose()


def _load_generated_document(data: bytearray, limits: PdfLoadLimits) -> None:
    document = GeneratedDocument()
    try:
        document.load_from(data, limits=limits)
    finally:
        document.dispose()


_BytearrayLoader = Callable[[bytearray, PdfLoadLimits], object]


@pytest.mark.parametrize(
    "loader",
    [
        pytest.param(
            lambda data, limits: PdfAValidateOptions(limits).add_input(data),
            id="pdfa-options",
        ),
        pytest.param(
            lambda data, limits: PdfUaValidateOptions(limits).add_input(data),
            id="pdfua-options",
        ),
        pytest.param(_load_document, id="document"),
        pytest.param(_load_generated_document, id="generated-document"),
        pytest.param(
            lambda data, limits: SimplePdf.from_bytes(data, limits=limits),
            id="simple-from-bytes",
        ),
        pytest.param(
            lambda data, limits: SimplePdf.from_bytes_safe(data, limits=limits),
            id="simple-from-bytes-safe",
        ),
        pytest.param(
            lambda data, limits: SimplePdf.load_from(data, limits=limits),
            id="simple-load-from",
        ),
        pytest.param(
            lambda data, limits: SimplePdf.load_cos(data, limits=limits),
            id="simple-load-cos",
        ),
    ],
)
def test_bytearray_size_is_checked_before_copy(loader: _BytearrayLoader) -> None:
    data = _CopyGuardBytearray(b"%PDF-1.7 oversized")
    limits = PdfLoadLimits(max_input_bytes=len(data) - 1)

    with pytest.raises(PdfResourceLimitException, match="max_input_bytes"):
        loader(data, limits)


@pytest.mark.parametrize(
    "options_type",
    [PdfAValidateOptions, PdfUaValidateOptions],
)
def test_validator_options_accumulate_bounded_short_reads(options_type) -> None:
    data = b"0123456789"
    stream = _ShortReadStream(data)
    limits = PdfLoadLimits(max_input_bytes=len(data))

    options = options_type(limits).add_input(stream)

    assert options.limits is limits
    assert options.inputs == [data]
    assert stream.read_sizes
    assert all(0 < size <= len(data) + 1 for size in stream.read_sizes)
    assert stream.closed is False


@pytest.mark.parametrize(
    "options_type",
    [PdfAValidateOptions, PdfUaValidateOptions],
)
def test_validator_options_stop_stream_after_limit(options_type) -> None:
    stream = _ShortReadStream(b"0123456789")
    limits = PdfLoadLimits(max_input_bytes=4)

    with pytest.raises(PdfResourceLimitException, match="max_input_bytes=4"):
        options_type(limits).add_input(stream)

    assert stream.read_sizes
    assert all(0 < size <= 5 for size in stream.read_sizes)
    assert stream.tell() == 5
    assert stream.closed is False


@pytest.mark.parametrize(
    "options_type",
    [PdfAValidateOptions, PdfUaValidateOptions],
)
def test_validator_options_precheck_bytes_and_paths(
    options_type,
    tmp_path: Path,
) -> None:
    limits = PdfLoadLimits(max_input_bytes=4)
    path = tmp_path / "oversized.pdf"
    path.write_bytes(b"0123456789")

    with pytest.raises(PdfResourceLimitException, match="max_input_bytes=4"):
        options_type(limits).add_input(b"12345")
    with pytest.raises(PdfResourceLimitException, match="max_input_bytes=4"):
        options_type(limits).add_input(path)


class _RecordingDocument:
    """Record the policy and context-manager lifecycle used by validators."""

    instances: list["_RecordingDocument"] = []

    def __init__(self, *, limits: PdfLoadLimits) -> None:
        self.constructor_limits = limits
        self.load_limits: PdfLoadLimits | None = None
        self.disposed = False
        self.__class__.instances.append(self)

    def __enter__(self) -> "_RecordingDocument":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.disposed = True

    def load_from(
        self,
        source: bytes,
        *,
        limits: PdfLoadLimits,
    ) -> "_RecordingDocument":
        self.load_limits = limits
        return self

    def validate_pdfa(self, level: str) -> PdfAValidationResult:
        return PdfAValidationResult(level=level)

    def validate_pdfua(self) -> PdfUaValidationResult:
        return PdfUaValidationResult()


@pytest.mark.parametrize(
    ("options_type", "validator_type"),
    [
        (PdfAValidateOptions, PdfAValidator),
        (PdfUaValidateOptions, PdfUaValidator),
    ],
)
def test_validators_forward_limits_and_dispose_documents(
    monkeypatch,
    options_type,
    validator_type,
) -> None:
    limits = PdfLoadLimits(max_input_bytes=32)
    options = options_type(limits).add_input(b"%PDF-1.7\n")
    _RecordingDocument.instances.clear()
    monkeypatch.setattr(document_module, "Document", _RecordingDocument)

    results = validator_type().process(options)

    assert len(results) == 1
    assert len(_RecordingDocument.instances) == 1
    document = _RecordingDocument.instances[0]
    assert document.constructor_limits is limits
    assert document.load_limits is limits
    assert document.disposed is True
