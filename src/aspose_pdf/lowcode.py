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

import inspect
import io
import os
from collections.abc import Iterable
from enum import Enum
from pathlib import Path
from typing import Any, BinaryIO

from aspose_pdf.document import Document
from aspose_pdf.engine.file_output import write_file_atomically
from aspose_pdf.engine.stream_output import write_all as _write_all
from aspose_pdf.exceptions import AsposePdfException
from aspose_pdf.facades import PdfExtractor
from aspose_pdf.load_limits import (
    PdfLoadLimits,
    _coerce_limits,
    _LoadBudget,
    _read_limited,
)
from aspose_pdf.optimization import OptimizationOptions

__all__ = [
    "ByteArrayDataSource",
    "ConvertOptions",
    "Converter",
    "DataSource",
    "DecryptOptions",
    "Decryptor",
    "EncryptOptions",
    "Encryptor",
    "FileDataSource",
    "FlattenOptions",
    "FormFlattener",
    "ImageExtractor",
    "ImageExtractorOptions",
    "ImagesToPdf",
    "ImagesToPdfOptions",
    "MergeOptions",
    "Merger",
    "OperationResult",
    "OptimizeOptions",
    "Optimizer",
    "PageRemoveOptions",
    "PageRemover",
    "PdfAConvertOptions",
    "PdfAConverter",
    "PdfPlugin",
    "Plugin",
    "PluginOptions",
    "ResultContainer",
    "RotateOptions",
    "Rotator",
    "SplitOptions",
    "Splitter",
    "StampOptions",
    "Stamper",
    "StreamDataSource",
    "TextExtractor",
    "TextExtractorOptions",
]


class Plugin(str, Enum):  # noqa: UP042
    """Identifiers for the available low-code plugins.

    Three members name one plugin each; the other four name a *kind* of plugin,
    and the classes in this module answering each are:

    ==================  ====================================================
    ``EXTRACTOR``       :class:`TextExtractor`, :class:`ImageExtractor`
    ``CONVERTER``       :class:`Converter`, :class:`PdfAConverter`
    ``GENERATOR``       :class:`ImagesToPdf`
    ``EDITOR``          :class:`Rotator`, :class:`PageRemover`,
                        :class:`Stamper`, :class:`FormFlattener`,
                        :class:`Encryptor`, :class:`Decryptor`
    ==================  ====================================================
    """

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
    Reads apply the default PDF input limit unless a different ``limits``
    policy is supplied.
    """

    def read_bytes(self, *, limits: PdfLoadLimits | None = None) -> bytes:
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

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    def read_bytes(self, *, limits: PdfLoadLimits | None = None) -> bytes:
        budget = _LoadBudget(limits)
        try:
            budget.check_input(self.path.stat().st_size)
            with self.path.open("rb") as stream:
                return _read_limited(stream, budget)
        except OSError as exc:
            raise AsposePdfException(
                f"Could not read input file {self.path}: {exc}"
            ) from exc

    def write_bytes(self, data: bytes) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            write_file_atomically(self.path, data)
        except OSError as exc:
            raise AsposePdfException(
                f"Could not write output file {self.path}: {exc}"
            ) from exc


class StreamDataSource(DataSource):
    """A data source backed by a binary stream (e.g. ``io.BytesIO``)."""

    def __init__(self, stream: BinaryIO, name: str | None = None) -> None:
        self.stream = stream
        self.name = name

    def read_bytes(self, *, limits: PdfLoadLimits | None = None) -> bytes:
        budget = _LoadBudget(limits)
        try:
            return _read_limited(self.stream, budget)
        except TypeError as exc:
            raise AsposePdfException("Stream read() did not return bytes") from exc
        except (OSError, ValueError) as exc:
            label = f" {self.name!r}" if self.name else ""
            raise AsposePdfException(
                f"Could not read input stream{label}: {exc}"
            ) from exc

    def write_bytes(self, data: bytes) -> None:
        try:
            if self.stream.seekable():
                self.stream.seek(0)
                self.stream.truncate(0)
            _write_all(self.stream, data)
        except (OSError, ValueError) as exc:
            label = f" {self.name!r}" if self.name else ""
            raise AsposePdfException(
                f"Could not write output stream{label}: {exc}"
            ) from exc


class ByteArrayDataSource(DataSource):
    """A data source backed by in-memory bytes."""

    def __init__(self, data: bytes | bytearray | None = None) -> None:
        if data is None:
            self.data: bytes | memoryview = b""
        elif isinstance(data, bytearray):
            self.data = memoryview(data)
        elif isinstance(data, bytes):
            self.data = data
        else:
            raise TypeError("data must be bytes, bytearray, or None")

    def read_bytes(self, *, limits: PdfLoadLimits | None = None) -> bytes:
        _LoadBudget(limits).check_input(len(self.data))
        return bytes(self.data)

    def write_bytes(self, data: bytes) -> None:
        self.data = bytes(data)


def _read_source_bytes(source: DataSource, limits: PdfLoadLimits) -> bytes:
    """Read a source while preserving the legacy no-argument override contract."""
    reader = source.read_bytes
    try:
        inspect.signature(reader).bind(limits=limits)
    except (TypeError, ValueError):
        data = reader()
    else:
        data = reader(limits=limits)
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise AsposePdfException("DataSource.read_bytes() must return bytes")
    payload = bytes(data)
    _LoadBudget(limits).check_input(len(payload))
    return payload


def _resolve_selection(selection: Iterable[int] | slice, page_count: int) -> list[int]:
    """One of :class:`SplitOptions`'s selections as a plain list of page indexes.

    A ``slice`` is resolved against the document's own length, so
    ``slice(5, None)`` means "page six onward" however long the input turns out
    to be, and a negative index counts from the end as it does everywhere else.
    """
    if isinstance(selection, slice):
        indexes = list(range(page_count))[selection]
    else:
        indexes = [
            index + page_count if index < 0 else index
            for index in (int(value) for value in selection)
        ]
    if not indexes:
        raise AsposePdfException("A split selection names no page")
    for index in indexes:
        if index < 0 or index >= page_count:
            raise AsposePdfException(
                f"Page index {index} is outside this document's {page_count} pages"
            )
    return indexes


def _selected_pages(document: Document, pages: Iterable[int] | None) -> list[int]:
    """The 0-based pages *pages* names, or all of them when it names none.

    An index the document does not have is refused rather than skipped: a plugin
    run over a batch should say that page 12 was asked for in a document of ten,
    not quietly do nine-tenths of the job.
    """
    count = document.page_count
    indexes = list(range(count)) if pages is None else [int(index) for index in pages]
    if not indexes:
        raise AsposePdfException("No pages were selected")
    for index in indexes:
        if index < 0 or index >= count:
            raise AsposePdfException(
                f"Page index {index} is outside this document's {count} pages"
            )
    return indexes


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


class OperationResult:
    """A single result produced by a plugin.

    Results are either binary (a produced PDF) or textual (extracted text).
    Use :meth:`is_string` / :meth:`is_byte_array` to discriminate, and
    :meth:`to_array` / :meth:`to_string` / :meth:`save` to consume.
    """

    def __init__(self, value: bytes | bytearray | str) -> None:
        if isinstance(value, str):
            self._value: bytes | str = value
        elif isinstance(value, (bytes, bytearray)):
            self._value = bytes(value)
        else:
            raise TypeError("value must be bytes, bytearray, or str")

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

    def save(
        self,
        destination: DataSource | str | os.PathLike[str] | BinaryIO,
    ) -> None:
        """Write the result to a data source, file path, or binary stream."""
        data = self.to_array()
        if isinstance(destination, DataSource):
            destination.write_bytes(data)
        elif isinstance(destination, (str, os.PathLike)):
            FileDataSource(destination).write_bytes(data)
        elif hasattr(destination, "write"):
            _write_all(destination, data)
        else:
            raise TypeError(
                "destination must be a DataSource, path, or writable stream"
            )


class ResultContainer:
    """Holds the ordered results of a plugin operation."""

    def __init__(self, results: Iterable[OperationResult] | None = None) -> None:
        values = [] if results is None else list(results)
        if not all(isinstance(result, OperationResult) for result in values):
            raise TypeError("results must contain only OperationResult instances")
        self.result_collection: list[OperationResult] = values

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
    """Hold input/output data sources and their PDF resource-limit policy."""

    def __init__(self, limits: PdfLoadLimits | None = None) -> None:
        self.limits = _coerce_limits(limits)
        self.inputs: list[DataSource] = []
        self.outputs: list[DataSource] = []

    def add_input(self, source: DataSource) -> PluginOptions:
        if not isinstance(source, DataSource):
            raise TypeError("input must be a DataSource")
        self.inputs.append(source)
        return self

    def add_output(self, source: DataSource) -> PluginOptions:
        if not isinstance(source, DataSource):
            raise TypeError("output must be a DataSource")
        self.outputs.append(source)
        return self

    # Aspose-style alias.
    def add_data_source(self, source: DataSource) -> PluginOptions:
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
        limits: PdfLoadLimits | None = None,
    ) -> None:
        super().__init__(limits=limits)
        self.compress_streams = compress_streams
        self.remove_unused_objects = remove_unused_objects


class SplitOptions(PluginOptions):
    """Options for :class:`Splitter`.

    Without *selections* every page of every input becomes a document of its own.
    *selections* splits into groups instead: each entry is what
    :meth:`~aspose_pdf.Document.extract_pages` takes -- an iterable of 0-based
    page indexes, or a ``slice`` -- and becomes one output document, in the order
    given::

        SplitOptions(selections=[range(0, 3), [3, 4], slice(5, None)])

    A page may appear in more than one selection, or in none.
    """

    def __init__(
        self,
        limits: PdfLoadLimits | None = None,
        *,
        selections: Iterable[Iterable[int] | slice] | None = None,
    ) -> None:
        # ``limits`` stays the first positional argument, as it was when this
        # class had no __init__ of its own and inherited PluginOptions'.
        super().__init__(limits=limits)
        self.selections = None if selections is None else list(selections)


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
        if len(outputs) > len(results):
            raise AsposePdfException(
                "More output data sources were provided than results were produced"
            )
        for result, output in zip(results, outputs):
            output.write_bytes(result.to_array())

    def _transform(
        self,
        options: PluginOptions,
        operation: Any,
        *,
        password: str | None = None,
    ) -> ResultContainer:
        """Run *operation* on each input document and collect the saved bytes.

        The shape every editing plugin has: load an input under the options'
        resource limits, change it, save it to memory, and dispose of it whether
        the change succeeded or not. *operation* takes the open document and
        returns nothing; what it did is in the bytes.
        """
        self._require_inputs(options)
        limits = _coerce_limits(getattr(options, "limits", None))
        results: list[OperationResult] = []
        for source in options.inputs:
            document = Document(limits=limits)
            try:
                document.load_from(
                    _read_source_bytes(source, limits), password=password
                )
                operation(document)
                buffer = io.BytesIO()
                document.save(buffer)
                results.append(OperationResult(buffer.getvalue()))
            finally:
                document.dispose()
        self._emit(results, options.outputs)
        return ResultContainer(results)


class Merger(PdfPlugin):
    """Concatenate every input PDF into a single document."""

    def process(self, options: MergeOptions) -> ResultContainer:
        self._require_inputs(options)
        limits = _coerce_limits(getattr(options, "limits", None))
        base = Document(limits=limits)
        loaded: list[Document] = []
        try:
            for source in options.inputs:
                doc = Document(limits=limits)
                doc.load_from(_read_source_bytes(source, limits))
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
        limits = _coerce_limits(getattr(options, "limits", None))
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
            doc = Document(limits=limits)
            try:
                doc.load_from(_read_source_bytes(source, limits))
                doc.optimize(opts, compress_streams=compress)
                buffer = io.BytesIO()
                doc.save(buffer)
                results.append(OperationResult(buffer.getvalue()))
            finally:
                doc.dispose()
        self._emit(results, options.outputs)
        return ResultContainer(results)


class Splitter(PdfPlugin):
    """Split every input PDF into one document per page, or per selection."""

    def process(self, options: SplitOptions) -> ResultContainer:
        self._require_inputs(options)
        from aspose_pdf.engine.simple_pdf import SimplePdf

        limits = _coerce_limits(getattr(options, "limits", None))
        selections = getattr(options, "selections", None)
        results: list[OperationResult] = []
        for source in options.inputs:
            pdf = SimplePdf.from_bytes(
                _read_source_bytes(source, limits), limits=limits
            )
            try:
                page_count = len(pdf.pages)
                if selections is None:
                    groups = [[index] for index in range(page_count)]
                else:
                    groups = [
                        _resolve_selection(selection, page_count)
                        for selection in selections
                    ]
                for group in groups:
                    single = pdf.extract_pages(group)
                    try:
                        single._load_limits = pdf.load_limits
                        single._load_budget = pdf._load_budget
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
        limits = _coerce_limits(getattr(options, "limits", None))
        results: list[OperationResult] = []
        for source in options.inputs:
            extractor = PdfExtractor()
            try:
                extractor.bind_pdf(
                    _read_source_bytes(source, limits), limits=limits
                )
                extractor.extract_text()
                results.append(OperationResult(extractor.get_text()))
            finally:
                extractor.dispose()
        self._emit(results, options.outputs)
        return ResultContainer(results)


# ---------------------------------------------------------------------------
# Options for the rest of the catalogue
# ---------------------------------------------------------------------------


class ImageExtractorOptions(PluginOptions):
    """Options for :class:`ImageExtractor`: pull the images out of each input."""


class ImagesToPdfOptions(PluginOptions):
    """Options for :class:`ImagesToPdf`.

    ``page_size`` is a :class:`~aspose_pdf.page_size.PageSize`, one of its names,
    or a ``(width, height)`` pair; without one each page is cut to its own
    picture, which is what a scan or a photo set wants. ``dpi`` says how many
    pixels go to the inch when a page is cut to the image (and how large the
    image is drawn inside a fixed page): 72 is one point per pixel, 300 makes a
    300 dpi scan come out at its physical size. ``margin`` is in points and
    applies to a fixed page size only -- a page cut to its image has no room for
    one -- and ``single_document`` collects every input into one file instead of
    one file per image.
    """

    def __init__(
        self,
        *,
        page_size: Any = None,
        dpi: float = 72.0,
        margin: float = 0.0,
        single_document: bool = True,
        limits: PdfLoadLimits | None = None,
    ) -> None:
        super().__init__(limits=limits)
        self.page_size = page_size
        self.dpi = dpi
        self.margin = margin
        self.single_document = single_document


class ConvertOptions(PluginOptions):
    """Options for :class:`Converter`: render or re-express each input.

    ``output_format`` is ``"html"``, ``"markdown"``, ``"svg"``, ``"tiff"``,
    ``"png"`` or ``"jpeg"``. HTML, Markdown and TIFF give **one result per
    input**; SVG, PNG and JPEG give **one per page**, since none of the three has
    a multi-page form. ``pages`` names the pages to take (all of them when
    omitted). The remaining arguments are passed to the operation each format
    uses: ``embed_images`` to :meth:`~aspose_pdf.Document.to_html` /
    :meth:`~aspose_pdf.Document.to_markdown`, and ``dpi``, ``mode``,
    ``compression`` and ``quality`` to the renderer and its encoders.
    """

    FORMATS = ("html", "markdown", "svg", "tiff", "png", "jpeg")

    def __init__(
        self,
        output_format: str = "html",
        *,
        pages: Iterable[int] | None = None,
        embed_images: bool = True,
        dpi: float = 72.0,
        mode: str = "rgb",
        compression: str = "deflate",
        quality: int = 85,
        limits: PdfLoadLimits | None = None,
    ) -> None:
        super().__init__(limits=limits)
        normalized = str(output_format).strip().lower().lstrip(".")
        aliases = {"md": "markdown", "jpg": "jpeg", "tif": "tiff", "htm": "html"}
        normalized = aliases.get(normalized, normalized)
        if normalized not in self.FORMATS:
            raise AsposePdfException(
                f"Unsupported output format {output_format!r}; use one of: "
                + ", ".join(self.FORMATS)
            )
        self.output_format = normalized
        self.pages = None if pages is None else [int(index) for index in pages]
        self.embed_images = embed_images
        self.dpi = dpi
        self.mode = mode
        self.compression = compression
        self.quality = quality


class RotateOptions(PluginOptions):
    """Options for :class:`Rotator`.

    ``rotation`` is a multiple of 90, clockwise. It is **added** to what each
    page already says, so a batch of pages lying on their side is straightened
    whatever each one started at; pass ``relative=False`` to set the rotation
    instead. ``pages`` names the pages to turn (all of them when omitted).
    """

    def __init__(
        self,
        rotation: int = 90,
        *,
        pages: Iterable[int] | None = None,
        relative: bool = True,
        limits: PdfLoadLimits | None = None,
    ) -> None:
        super().__init__(limits=limits)
        self.rotation = rotation
        self.pages = None if pages is None else [int(index) for index in pages]
        self.relative = relative


class PageRemoveOptions(PluginOptions):
    """Options for :class:`PageRemover`: ``pages`` are the 0-based pages to drop."""

    def __init__(
        self,
        pages: Iterable[int] | None = None,
        *,
        limits: PdfLoadLimits | None = None,
    ) -> None:
        super().__init__(limits=limits)
        self.pages = None if pages is None else [int(index) for index in pages]


class EncryptOptions(PluginOptions):
    """Options for :class:`Encryptor`, as :meth:`~aspose_pdf.Document.encrypt`."""

    def __init__(
        self,
        user_password: str = "",
        owner_password: str | None = None,
        *,
        permissions: int = -4,
        algorithm: str = "AES-256",
        limits: PdfLoadLimits | None = None,
    ) -> None:
        super().__init__(limits=limits)
        self.user_password = user_password
        self.owner_password = owner_password
        self.permissions = permissions
        self.algorithm = algorithm


class DecryptOptions(PluginOptions):
    """Options for :class:`Decryptor`: the ``password`` that opens each input."""

    def __init__(
        self,
        password: str = "",
        *,
        limits: PdfLoadLimits | None = None,
    ) -> None:
        super().__init__(limits=limits)
        self.password = password


class PdfAConvertOptions(PluginOptions):
    """Options for :class:`PdfAConverter`: the ``level`` to convert each input to.

    ``level`` is what :meth:`~aspose_pdf.Document.convert_to_pdfa` takes --
    ``"1b"``, ``"2b"``, ``"3b"``, ``"4"`` and the rest. The fonts a conversion
    could not embed are reported through :attr:`PdfAConverter.unembedded_fonts`
    rather than raising, since the file is still produced.
    """

    def __init__(
        self,
        level: str = "1b",
        *,
        font_lookup_directory: str | os.PathLike[str] | None = None,
        limits: PdfLoadLimits | None = None,
    ) -> None:
        super().__init__(limits=limits)
        self.level = level
        self.font_lookup_directory = font_lookup_directory


class FlattenOptions(PluginOptions):
    """Options for :class:`FormFlattener`: nothing beyond the inputs and outputs."""


class StampOptions(PluginOptions):
    """Options for :class:`Stamper`: the ``stamp`` to put on ``pages``.

    ``stamp`` is any :class:`~aspose_pdf.stamps.Stamp` -- a ``TextStamp``, an
    ``ImageStamp`` or a ``PageNumberStamp`` -- and carries its own opacity,
    rotation, alignment and background flag. ``pages`` names the pages to stamp
    (all of them when omitted).
    """

    def __init__(
        self,
        stamp: Any = None,
        *,
        pages: Iterable[int] | None = None,
        limits: PdfLoadLimits | None = None,
    ) -> None:
        super().__init__(limits=limits)
        self.stamp = stamp
        self.pages = None if pages is None else [int(index) for index in pages]


# ---------------------------------------------------------------------------
# The rest of the catalogue
# ---------------------------------------------------------------------------


class ImageExtractor(PdfPlugin):
    """Pull every embedded image out of each input, as a real image file.

    One result per image, in document order, across all inputs: PNG for raster
    codecs, and the original payload where the file holds a JPEG or a JPEG 2000.
    An input that draws no image contributes no result.
    """

    def process(self, options: ImageExtractorOptions) -> ResultContainer:
        self._require_inputs(options)
        limits = _coerce_limits(getattr(options, "limits", None))
        results: list[OperationResult] = []
        for source in options.inputs:
            extractor = PdfExtractor()
            try:
                extractor.bind_pdf(_read_source_bytes(source, limits), limits=limits)
                extractor.extract_image()
                while extractor.has_next_image():
                    image = extractor.get_next_image()
                    if image:
                        results.append(OperationResult(bytes(image)))
            finally:
                extractor.dispose()
        self._emit(results, options.outputs)
        return ResultContainer(results)


class ImagesToPdf(PdfPlugin):
    """Make a PDF out of JPEG or PNG inputs, one page per image.

    Each page is cut to its own picture unless ``page_size`` says otherwise, in
    which case the image is centred inside that sheet and scaled down to fit
    within the margin. By default every input becomes one page of **one**
    document; with ``single_document=False`` each image becomes a document of its
    own.
    """

    def process(self, options: ImagesToPdfOptions) -> ResultContainer:
        self._require_inputs(options)
        from aspose_pdf.engine.content_authoring import image_pixel_size

        limits = _coerce_limits(getattr(options, "limits", None))
        # Read, then checked: ``value or default`` would turn a dpi of 0 into the
        # default instead of refusing it.
        try:
            dpi = float(getattr(options, "dpi", 72.0))
            margin = float(getattr(options, "margin", 0.0))
        except (TypeError, ValueError):
            raise AsposePdfException("dpi and margin must be numbers") from None
        if not dpi > 0:
            raise AsposePdfException("dpi must be above zero")
        if margin < 0:
            raise AsposePdfException("margin cannot be negative")
        requested = getattr(options, "page_size", None)
        single = bool(getattr(options, "single_document", True))

        images: list[tuple[bytes, float, float]] = []
        for source in options.inputs:
            data = _read_source_bytes(source, limits)
            pixel_width, pixel_height = image_pixel_size(data)
            images.append((data, pixel_width * 72.0 / dpi, pixel_height * 72.0 / dpi))

        results: list[OperationResult] = []
        batches = [images] if single else [[image] for image in images]
        for batch in batches:
            document = Document(limits=limits)
            try:
                for data, width, height in batch:
                    self._place(document, data, width, height, requested, margin)
                buffer = io.BytesIO()
                document.save(buffer)
                results.append(OperationResult(buffer.getvalue()))
            finally:
                document.dispose()
        self._emit(results, options.outputs)
        return ResultContainer(results)

    @staticmethod
    def _place(
        document: Document,
        data: bytes,
        width: float,
        height: float,
        requested: Any,
        margin: float,
    ) -> None:
        """Add one page carrying one image, sized the way the options ask."""
        if requested is None:
            page = document.pages.add(size=(width, height))
            page.add_image(data, 0, 0, width, height)
            return
        page = document.pages.add(size=requested)
        box_width = page.size.width - 2 * margin
        box_height = page.size.height - 2 * margin
        if box_width <= 0 or box_height <= 0:
            raise AsposePdfException(
                "The margin leaves no room for the image on this page size"
            )
        scale = min(box_width / width, box_height / height, 1.0)
        drawn_width, drawn_height = width * scale, height * scale
        page.add_image(
            data,
            margin + (box_width - drawn_width) / 2.0,
            margin + (box_height - drawn_height) / 2.0,
            drawn_width,
            drawn_height,
        )


class Converter(PdfPlugin):
    """Re-express each input in another format -- HTML, Markdown, SVG or a raster.

    HTML, Markdown and TIFF produce one result per input; SVG, PNG and JPEG
    produce one per page, because none of those three has a multi-page form. The
    text formats come back as text results, the rasters as bytes.
    """

    def process(self, options: ConvertOptions) -> ResultContainer:
        self._require_inputs(options)
        limits = _coerce_limits(getattr(options, "limits", None))
        output_format = getattr(options, "output_format", "html")
        results: list[OperationResult] = []
        for source in options.inputs:
            document = Document(limits=limits)
            try:
                document.load_from(_read_source_bytes(source, limits))
                results.extend(self._convert(document, options, output_format))
            finally:
                document.dispose()
        self._emit(results, options.outputs)
        return ResultContainer(results)

    def _convert(
        self, document: Document, options: ConvertOptions, output_format: str
    ) -> list[OperationResult]:
        indexes = _selected_pages(document, getattr(options, "pages", None))
        if output_format == "html":
            return [
                OperationResult(
                    document.to_html(
                        pages=indexes, embed_images=getattr(options, "embed_images", True)
                    )
                )
            ]
        if output_format == "markdown":
            return [
                OperationResult(
                    document.to_markdown(
                        pages=indexes, embed_images=getattr(options, "embed_images", True)
                    )
                )
            ]
        if output_format == "tiff":
            return [
                OperationResult(
                    document.to_tiff(
                        pages=indexes,
                        dpi=getattr(options, "dpi", 72.0),
                        mode=getattr(options, "mode", "rgb"),
                        compression=getattr(options, "compression", "deflate"),
                    )
                )
            ]
        if output_format == "svg":
            return [OperationResult(document.pages[index].to_svg()) for index in indexes]
        results: list[OperationResult] = []
        for index in indexes:
            raster = document.render_page(index, dpi=getattr(options, "dpi", 72.0))
            if output_format == "png":
                results.append(
                    OperationResult(raster.to_png(mode=getattr(options, "mode", "rgb")))
                )
            else:
                results.append(
                    OperationResult(
                        raster.to_jpeg(
                            quality=getattr(options, "quality", 85),
                            mode=getattr(options, "mode", "rgb"),
                        )
                    )
                )
        return results

class Rotator(PdfPlugin):
    """Turn pages of each input by a multiple of 90 degrees."""

    def process(self, options: RotateOptions) -> ResultContainer:
        rotation = int(getattr(options, "rotation", 90))
        relative = bool(getattr(options, "relative", True))
        pages = getattr(options, "pages", None)

        def rotate(document: Document) -> None:
            for index in _selected_pages(document, pages):
                page = document.pages[index]
                page.rotation = page.rotation + rotation if relative else rotation

        return self._transform(options, rotate)


class PageRemover(PdfPlugin):
    """Drop the named pages from each input."""

    def process(self, options: PageRemoveOptions) -> ResultContainer:
        pages = getattr(options, "pages", None)
        if not pages:
            raise AsposePdfException("No pages to remove were named")

        def remove(document: Document) -> None:
            targets = sorted(set(_selected_pages(document, pages)), reverse=True)
            if len(targets) >= document.page_count:
                raise AsposePdfException(
                    "Removing every page would leave no document behind"
                )
            # Last page first, so an index is never shifted before it is used.
            for index in targets:
                document.pages.delete(index)

        return self._transform(options, remove)


class Encryptor(PdfPlugin):
    """Encrypt each input with the standard security handler."""

    def process(self, options: EncryptOptions) -> ResultContainer:
        def encrypt(document: Document) -> None:
            document.encrypt(
                getattr(options, "user_password", ""),
                getattr(options, "owner_password", None),
                permissions=getattr(options, "permissions", -4),
                algorithm=getattr(options, "algorithm", "AES-256"),
            )

        return self._transform(options, encrypt)


class Decryptor(PdfPlugin):
    """Remove the standard security handler from each input.

    The ``password`` opens the input *and* authorises the removal, so it has to
    be one the document accepts -- the user or the owner password. An input that
    carries no protection passes through unchanged.

    The removal is unconditional, because ``Document.is_encrypted`` is about
    whether a document is still *locked*: opening one with its password unlocks
    it and the flag goes false, while a plain re-save would put the protection
    back. Asking it first would have skipped every document this plugin exists
    for.
    """

    def process(self, options: DecryptOptions) -> ResultContainer:
        password = getattr(options, "password", "")

        def decrypt(document: Document) -> None:
            document.decrypt(password)

        return self._transform(options, decrypt, password=password)


class PdfAConverter(PdfPlugin):
    """Convert each input to PDF/A.

    The fonts a conversion could not embed are collected in
    :attr:`unembedded_fonts`, one list per input, rather than raising: the file is
    produced either way, and whether an unembedded font matters is the caller's
    call.
    """

    def __init__(self) -> None:
        self.unembedded_fonts: list[list[str]] = []

    def process(self, options: PdfAConvertOptions) -> ResultContainer:
        self.unembedded_fonts = []
        level = getattr(options, "level", "1b")
        directory = getattr(options, "font_lookup_directory", None)

        def convert(document: Document) -> None:
            self.unembedded_fonts.append(
                list(
                    document.convert_to_pdfa(level, font_lookup_directory=directory)
                    or []
                )
            )

        return self._transform(options, convert)


class FormFlattener(PdfPlugin):
    """Bake each input's form fields -- and its annotations -- into page content.

    Annotations come with the fields: flattening is one operation in the engine,
    and :meth:`Form.flatten` is that same operation rather than a narrower one.
    Fields and annotations without an appearance are given a synthesised one first
    so they are drawn rather than dropped.
    """

    def process(self, options: FlattenOptions) -> ResultContainer:
        return self._transform(options, lambda document: document.flatten())


class Stamper(PdfPlugin):
    """Put one stamp on the pages of each input."""

    def process(self, options: StampOptions) -> ResultContainer:
        stamp = getattr(options, "stamp", None)
        if stamp is None:
            raise AsposePdfException("No stamp was provided")
        pages = getattr(options, "pages", None)

        def apply(document: Document) -> None:
            document.add_stamp(stamp, _selected_pages(document, pages))

        return self._transform(options, apply)
