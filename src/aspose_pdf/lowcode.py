"""Low-code plugin API for common PDF workflows.

This module offers a small, Aspose.PDF-style "plugin" layer built on top of
the high-level :class:`aspose_pdf.document.Document` API and the engine. Each
plugin takes an *options* object describing inputs and outputs, performs one
operation, and returns a :class:`ResultContainer`.

Example
-------
::

    from aspose_pdf.lowcode import Merger, MergeOptions, FileDataSource

    merger = Merger()
    options = MergeOptions()
    options.add_input(FileDataSource("a.pdf"))
    options.add_input(FileDataSource("b.pdf"))
    options.add_output(FileDataSource("merged.pdf"))
    merger.process(options)

Data sources abstract over files, in-memory bytes, and binary streams, so the
same plugin works regardless of where the PDF data lives.
"""

from __future__ import annotations

import io
from enum import Enum
from pathlib import Path
from typing import BinaryIO, Iterable

from aspose_pdf.document import Document
from aspose_pdf.exceptions import AsposePdfException
from aspose_pdf.facades import PdfExtractor
from aspose_pdf.optimization import OptimizationOptions

__all__ = [
    "Plugin",
    "DataSource",
    "FileDataSource",
    "StreamDataSource",
    "ByteArrayDataSource",
    "OperationResult",
    "ResultContainer",
    "PluginOptions",
    "MergeOptions",
    "OptimizeOptions",
    "SplitOptions",
    "TextExtractorOptions",
    "PdfPlugin",
    "Merger",
    "Optimizer",
    "Splitter",
    "TextExtractor",
]


class Plugin(str, Enum):
    """Identifiers for the available low-code plugins."""

    OPTIMIZER = "Optimizer"
    MERGER = "Merger"
    SPLITTER = "Splitter"
    EXTRACTOR = "Extractor"
    CONVERTER = "Converter"
    GENERATOR = "Generator"
    EDITOR = "Editor"


# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------


class DataSource:
    """Base class for plugin inputs and outputs.

    A data source can be read from (used as an input) and written to (used as
    an output). Subclasses implement :meth:`read_bytes` and :meth:`write_bytes`.
    """

    def read_bytes(self) -> bytes:
        raise NotImplementedError(
            "This data source does not support reading; use it as an output "
            "or choose a readable source such as FileDataSource."
        )

    def write_bytes(self, data: bytes) -> None:
        raise NotImplementedError(
            "This data source does not support writing; use it as an input "
            "or choose a writable source such as FileDataSource."
        )


class FileDataSource(DataSource):
    """A data source backed by a file on disk."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def read_bytes(self) -> bytes:
        try:
            return self.path.read_bytes()
        except OSError as exc:
            raise AsposePdfException(
                f"Could not read input file {self.path}: {exc}"
            ) from exc

    def write_bytes(self, data: bytes) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_bytes(data)
        except OSError as exc:
            raise AsposePdfException(
                f"Could not write output file {self.path}: {exc}"
            ) from exc


class StreamDataSource(DataSource):
    """A data source backed by a binary stream (e.g. ``io.BytesIO``)."""

    def __init__(self, stream: BinaryIO, name: str | None = None) -> None:
        self.stream = stream
        self.name = name

    def read_bytes(self) -> bytes:
        data = self.stream.read()
        if not isinstance(data, (bytes, bytearray)):
            raise AsposePdfException("Stream read() did not return bytes")
        return bytes(data)

    def write_bytes(self, data: bytes) -> None:
        self.stream.write(data)


class ByteArrayDataSource(DataSource):
    """A data source backed by in-memory bytes."""

    def __init__(self, data: bytes | None = None) -> None:
        self.data = bytes(data) if data is not None else b""

    def read_bytes(self) -> bytes:
        return self.data

    def write_bytes(self, data: bytes) -> None:
        self.data = bytes(data)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


class OperationResult:
    """A single result produced by a plugin.

    Results are either binary (a produced PDF) or textual (extracted text).
    Use :meth:`is_string` / :meth:`is_byte_array` to discriminate, and
    :meth:`to_array` / :meth:`to_string` / :meth:`save` to consume.
    """

    def __init__(self, value: bytes | str) -> None:
        self._value = value

    def is_string(self) -> bool:
        return isinstance(self._value, str)

    def is_byte_array(self) -> bool:
        return isinstance(self._value, (bytes, bytearray))

    def to_array(self) -> bytes:
        """Return the result as bytes (text is UTF-8 encoded)."""
        if isinstance(self._value, str):
            return self._value.encode("utf-8")
        return bytes(self._value)

    def to_string(self) -> str:
        """Return the result as text (binary is UTF-8 decoded leniently)."""
        if isinstance(self._value, str):
            return self._value
        return bytes(self._value).decode("utf-8", errors="replace")

    def save(self, destination: DataSource | str | Path | BinaryIO) -> None:
        """Write the result to a data source, file path, or binary stream."""
        data = self.to_array()
        if isinstance(destination, DataSource):
            destination.write_bytes(data)
        elif isinstance(destination, (str, Path)):
            FileDataSource(destination).write_bytes(data)
        elif hasattr(destination, "write"):
            destination.write(data)
        else:
            raise TypeError(
                "destination must be a DataSource, path, or writable stream"
            )


class ResultContainer:
    """Holds the ordered results of a plugin operation."""

    def __init__(self, results: Iterable[OperationResult] | None = None) -> None:
        self.result_collection: list[OperationResult] = list(results or [])

    def __len__(self) -> int:
        return len(self.result_collection)

    def __iter__(self):
        return iter(self.result_collection)

    def __getitem__(self, index: int) -> OperationResult:
        return self.result_collection[index]


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------


class PluginOptions:
    """Base options object holding input and output data sources."""

    def __init__(self) -> None:
        self.inputs: list[DataSource] = []
        self.outputs: list[DataSource] = []

    def add_input(self, source: DataSource) -> "PluginOptions":
        if not isinstance(source, DataSource):
            raise TypeError("input must be a DataSource")
        self.inputs.append(source)
        return self

    def add_output(self, source: DataSource) -> "PluginOptions":
        if not isinstance(source, DataSource):
            raise TypeError("output must be a DataSource")
        self.outputs.append(source)
        return self

    # Aspose-style alias.
    def add_data_source(self, source: DataSource) -> "PluginOptions":
        return self.add_input(source)


class MergeOptions(PluginOptions):
    """Options for :class:`Merger`: concatenate all inputs in order."""


class OptimizeOptions(PluginOptions):
    """Options for :class:`Optimizer`.

    ``compress_streams`` and ``remove_unused_objects`` mirror the underlying
    :meth:`Document.optimize` behaviour (both on by default).
    """

    def __init__(
        self,
        *,
        compress_streams: bool = True,
        remove_unused_objects: bool = True,
    ) -> None:
        super().__init__()
        self.compress_streams = compress_streams
        self.remove_unused_objects = remove_unused_objects


class SplitOptions(PluginOptions):
    """Options for :class:`Splitter`: split the first input into single pages."""


class TextExtractorOptions(PluginOptions):
    """Options for :class:`TextExtractor`: extract text from each input."""


# ---------------------------------------------------------------------------
# Plugins
# ---------------------------------------------------------------------------


class PdfPlugin:
    """Base class for low-code plugins."""

    def process(self, options: PluginOptions) -> ResultContainer:
        raise NotImplementedError(
            "Plugin subclasses must implement process()"
        )

    @staticmethod
    def _require_inputs(options: PluginOptions) -> None:
        if not options.inputs:
            raise AsposePdfException("No input data sources were provided")

    @staticmethod
    def _emit(results: list[OperationResult], outputs: list[DataSource]) -> None:
        """Write produced results to outputs, pairing them by position."""
        for result, output in zip(results, outputs):
            output.write_bytes(result.to_array())


class Merger(PdfPlugin):
    """Concatenate every input PDF into a single document."""

    def process(self, options: MergeOptions) -> ResultContainer:
        self._require_inputs(options)
        base = Document()
        loaded: list[Document] = []
        try:
            for source in options.inputs:
                doc = Document()
                doc.load_from(source.read_bytes())
                loaded.append(doc)
            base.merge(*loaded)
            buffer = io.BytesIO()
            base.save(buffer)
            result = OperationResult(buffer.getvalue())
            self._emit([result] * max(len(options.outputs), 1), options.outputs)
            return ResultContainer([result])
        finally:
            base.dispose()
            for doc in loaded:
                doc.dispose()


class Optimizer(PdfPlugin):
    """Optimize each input PDF (compression + unused-object cleanup)."""

    def process(self, options: OptimizeOptions) -> ResultContainer:
        self._require_inputs(options)
        remove_unused = getattr(options, "remove_unused_objects", True)
        compress = getattr(options, "compress_streams", True)
        if remove_unused:
            opts = OptimizationOptions()
        else:
            # Compression-only / no-op: disable structural cleanups and let the
            # ``compress`` flag decide whether streams get compressed.
            opts = OptimizationOptions(
                remove_unused_objects=False,
                remove_unused_streams=False,
                remove_duplicate_images=False,
                link_duplicate_streams=False,
            )
        results: list[OperationResult] = []
        for source in options.inputs:
            doc = Document()
            try:
                doc.load_from(source.read_bytes())
                doc.optimize(opts, compress_streams=compress)
                buffer = io.BytesIO()
                doc.save(buffer)
                results.append(OperationResult(buffer.getvalue()))
            finally:
                doc.dispose()
        self._emit(results, options.outputs)
        return ResultContainer(results)


class Splitter(PdfPlugin):
    """Split the first input PDF into one document per page."""

    def process(self, options: SplitOptions) -> ResultContainer:
        self._require_inputs(options)
        from aspose_pdf.engine.simple_pdf import SimplePdf

        source = options.inputs[0]
        pdf = SimplePdf.from_bytes(source.read_bytes())
        results: list[OperationResult] = []
        try:
            page_count = len(pdf.pages)
            for index in range(page_count):
                single = pdf.extract_pages([index])
                try:
                    results.append(OperationResult(single.to_bytes()))
                finally:
                    single.dispose()
        finally:
            pdf.dispose()
        self._emit(results, options.outputs)
        return ResultContainer(results)


class TextExtractor(PdfPlugin):
    """Extract plain text from each input PDF."""

    def process(self, options: TextExtractorOptions) -> ResultContainer:
        self._require_inputs(options)
        results: list[OperationResult] = []
        for source in options.inputs:
            extractor = PdfExtractor()
            try:
                extractor.bind_pdf(source.read_bytes())
                extractor.extract_text()
                results.append(OperationResult(extractor.get_text()))
            finally:
                extractor.dispose()
        return ResultContainer(results)
