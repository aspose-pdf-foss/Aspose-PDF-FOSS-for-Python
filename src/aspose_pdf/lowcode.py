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
import math
import os
from collections.abc import Iterable, Mapping, Sequence
from contextlib import closing, nullcontext
from enum import Enum
from pathlib import Path
from typing import Any, BinaryIO, Literal

from aspose_pdf.document import Document
from aspose_pdf.engine.file_output import write_file_atomically
from aspose_pdf.engine.stream_output import write_all as _write_all
from aspose_pdf.exceptions import AsposePdfException, PdfValidationException
from aspose_pdf.facades import PdfExtractor
from aspose_pdf.load_limits import (
    PdfLoadLimits,
    _coerce_limits,
    _LoadBudget,
    _read_limited,
)
from aspose_pdf.optimization import OptimizationOptions
from aspose_pdf.permissions import Permissions

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
    "FormFiller",
    "FormFillerOptions",
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


def _validate_split_options(
    selections: Any,
    bookmark_level: int | None,
    include_preamble: bool,
    pages_per_part: int | None,
) -> None:
    """Validate split modes before consuming selections or reading any inputs."""
    from aspose_pdf.engine.bookmark_split import validate_options
    from aspose_pdf.engine.page_count_split import validate_pages_per_part

    validate_options(1 if bookmark_level is None else bookmark_level, include_preamble)
    if pages_per_part is not None:
        validate_pages_per_part(pages_per_part)
    modes = selections, bookmark_level, pages_per_part
    if sum(value is not None for value in modes) > 1:
        raise ValueError(
            "selections, bookmark_level and pages_per_part cannot be combined"
        )


def _resolve_selection(
    selection: Iterable[int] | slice | str,
    page_count: int,
    *,
    limits: PdfLoadLimits | None = None,
) -> list[int]:
    """One of :class:`SplitOptions`'s selections as a plain list of page indexes.

    A ``slice`` is resolved against the document's own length, so
    ``slice(5, None)`` means "page six onward" however long the input turns out
    to be, and a negative index counts from the end as it does everywhere else.
    A range string uses one-based numbers and resolves open ends per input.
    """
    if isinstance(selection, str):
        from aspose_pdf.page_ranges import parse_page_range

        indexes = list(parse_page_range(selection, page_count, limits=limits))
    elif isinstance(selection, slice):
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
    """Concatenate inputs, optionally grouping bookmarks by source document.

    With ``create_bookmarks=True``, each non-empty input gets a section
    pointing to its first page, with its original bookmarks nested beneath it.
    ``bookmark_titles`` overrides automatic titles with one non-blank string
    per input. Otherwise file stems or named-stream stems take precedence over
    the PDF Title and the ``"Document N"`` fallback. Titles are snapshotted at
    construction and validated again before inputs are read.
    """

    def __init__(
        self,
        limits: PdfLoadLimits | None = None,
        *,
        create_bookmarks: bool = False,
        bookmark_titles: Iterable[str] | None = None,
    ) -> None:
        from aspose_pdf.engine.merge_bookmarks import snapshot_titles

        super().__init__(limits=limits)
        self.bookmark_titles = snapshot_titles(
            create_bookmarks, bookmark_titles, self.limits
        )
        self.create_bookmarks = create_bookmarks


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

    By default every page of every input becomes a document of its own.
    *selections* splits into groups instead: each entry is what
    :meth:`~aspose_pdf.Document.extract_pages` takes -- an iterable of 0-based
    page indexes, a ``slice``, or a one-based range string such as ``"1-3,7-"``
    -- and becomes one output document, in the order given::

        SplitOptions(selections=[range(0, 3), [3, 4], slice(5, None)])

    A page may appear in more than one selection, or in none.

    ``bookmark_level=1`` instead splits at root bookmarks; higher levels select
    nested headings. It follows :meth:`Document.split_by_bookmarks`, including
    a leading ``Preamble`` result unless ``include_preamble=False``. This mode
    cannot be combined with ``selections`` or ``pages_per_part``.

    ``pages_per_part=N`` instead groups consecutive pages in parts of at most
    N pages, retaining a shorter last part. It follows :meth:`Document.split_every`
    for each input independently and cannot be combined with either other mode.
    """

    def __init__(
        self,
        limits: PdfLoadLimits | None = None,
        *,
        selections: Iterable[Iterable[int] | slice | str] | None = None,
        bookmark_level: int | None = None,
        include_preamble: bool = True,
        pages_per_part: int | None = None,
    ) -> None:
        # ``limits`` stays the first positional argument, as it was when this
        # class had no __init__ of its own and inherited PluginOptions'.
        super().__init__(limits=limits)
        _validate_split_options(
            selections, bookmark_level, include_preamble, pages_per_part
        )
        self.selections = None if selections is None else list(selections)
        self.bookmark_level = bookmark_level
        self.include_preamble = include_preamble
        self.pages_per_part = pages_per_part


class TextExtractorOptions(PluginOptions):
    """Extract text from all or selected pages, optionally within a ``clip``.

    ``pages`` contains zero-based indices, preserving order and repetitions;
    ``None`` selects all and ``[]`` selects none. The selection is snapshotted
    for reuse across inputs/runs, and checked against each input's page count.
    ``exclude_regions`` snapshots rectangles to omit from each selected page,
    including later assignment and reuse across inputs. The rectangles apply
    in unrotated PDF coordinates and combine with ``clip``; see
    :meth:`Document.extract_text`.
    """

    def __init__(
        self,
        limits: PdfLoadLimits | None = None,
        *,
        clip: Sequence[float] | None = None,
        pages: Iterable[int] | None = None,
        skip_artifacts: bool = False,
        exclude_regions: Iterable[Sequence[float]] | None = None,
    ) -> None:
        super().__init__(limits=limits)
        self.pages = pages
        self.skip_artifacts = skip_artifacts
        self.exclude_regions = exclude_regions
        if clip is not None:
            from aspose_pdf.engine.text_region import validate_clip

            clip = validate_clip(clip)
        self.clip = clip

    @property
    def exclude_regions(self) -> tuple[tuple[float, float, float, float], ...]:
        """An immutable snapshot of excluded PDF-coordinate rectangles."""
        return self._exclude_regions

    @exclude_regions.setter
    def exclude_regions(self, regions: Iterable[Sequence[float]] | None) -> None:
        from aspose_pdf.engine.text_region import validate_exclude_regions

        self._exclude_regions = validate_exclude_regions(regions, limits=self.limits)

    @property
    def pages(self) -> tuple[int, ...] | None:
        """A reusable snapshot of zero-based page indices, or all pages."""
        return self._pages

    @pages.setter
    def pages(self, values: Iterable[int] | None) -> None:
        from aspose_pdf.engine.text_selection import page_indexes

        self._pages = page_indexes(values, limits=self.limits)

    @property
    def skip_artifacts(self) -> bool:
        """Whether explicitly marked Artifact content is omitted from text."""
        return self._skip_artifacts

    @skip_artifacts.setter
    def skip_artifacts(self, value: bool) -> None:
        from aspose_pdf.engine.text_selection import validate_skip_artifacts

        self._skip_artifacts = validate_skip_artifacts(value)


class FormFillerOptions(PluginOptions):
    """Fill every input PDF template once per immutable field-value record.

    ``records`` accepts an iterable of flat mappings with :meth:`Form.fill`
    values, including a generator. Records, names and list values are copied
    and validated before input reads, also on later assignment. ``strict``
    controls unknown field names; signature fields and push buttons are skipped.
    ``flatten=True`` bakes fields using ``Document.flatten(annotations=False)``.
    ``password`` opens password-protected templates. Normal save/encryption and
    appearance-generation rules apply. Results are ordered by input, then record.
    """

    def __init__(
        self,
        records: Iterable[Mapping[str, Any]],
        *,
        strict: bool = True,
        flatten: bool = False,
        password: str | None = None,
        limits: PdfLoadLimits | None = None,
    ) -> None:
        from aspose_pdf.engine.form_batch import validate_options

        super().__init__(limits=limits)
        validate_options(strict, flatten, password)
        self.strict, self.flatten, self.password = strict, flatten, password
        self.records = records

    @classmethod
    def from_csv(
        cls,
        source: str | Path | bytes | bytearray | BinaryIO,
        *,
        encoding: str = "utf-8-sig",
        delimiter: str = ",",
        field_map: Mapping[str, str] | None = None,
        checkbox_fields: Iterable[str] | None = None,
        checkbox_values: Mapping[str, bool] | None = None,
        strict: bool = True,
        flatten: bool = False,
        password: str | None = None,
        limits: PdfLoadLimits | None = None,
    ) -> FormFillerOptions:
        """Build reusable batch records from a CSV path, bytes or binary stream.

        CSV headers are field names unless ``field_map`` selects and renames
        columns. Other columns are then ignored. ``checkbox_fields`` lists PDF
        field names whose cells are converted to booleans; other cells remain
        exact strings. Default checkbox values are true/1/yes/y/on and
        false/0/no/n/off/empty, ignoring case and surrounding whitespace.
        ``checkbox_values`` replaces that table with an explicit text-to-bool
        mapping. Unrecognized values raise instead of guessing.

        Blank records are skipped; empty cells are retained. Duplicate/blank
        headers, missing mapped columns, unequal row widths and malformed CSV
        are refused. CSV parsing and snapshotting complete before returning,
        without reading any PDF template. Binary streams are consumed from
        their current position and left open. The existing batch options and
        resource limits apply; no optional dependencies are needed.
        """
        from aspose_pdf.engine.form_csv import records

        resolved_limits = _coerce_limits(limits)
        return cls(
            records(
                source,
                encoding=encoding,
                delimiter=delimiter,
                field_map=field_map,
                checkbox_fields=checkbox_fields,
                checkbox_values=checkbox_values,
                limits=resolved_limits,
            ),
            strict=strict,
            flatten=flatten,
            password=password,
            limits=resolved_limits,
        )

    @property
    def records(self) -> tuple[Mapping[str, Any], ...]:
        """A reusable tuple of read-only mappings with immutable field values."""
        return self._records

    @records.setter
    def records(self, values: Iterable[Mapping[str, Any]]) -> None:
        from aspose_pdf.engine.form_batch import snapshot_records

        self._records = snapshot_records(values, self.limits)[0]


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
        from aspose_pdf.engine.merge_bookmarks import default_title, snapshot_titles

        self._require_inputs(options)
        limits = _coerce_limits(getattr(options, "limits", None))
        create_bookmarks = getattr(options, "create_bookmarks", False)
        titles = snapshot_titles(
            create_bookmarks,
            getattr(options, "bookmark_titles", None),
            limits,
            count=len(options.inputs),
        )
        if create_bookmarks:
            _LoadBudget(limits).check(
                len(options.inputs), "max_container_items", "merge bookmark inputs"
            )
        inputs, outputs = tuple(options.inputs), tuple(options.outputs)
        source_names = tuple(
            source.path if isinstance(source, FileDataSource)
            else source.name if isinstance(source, StreamDataSource) else None
            for source in inputs
        ) if create_bookmarks and titles is None else ()
        base = Document(limits=limits)
        loaded: list[Document] = []
        try:
            for source in inputs:
                doc = Document(limits=limits)
                loaded.append(doc)
                doc.load_from(_read_source_bytes(source, limits))
            if create_bookmarks and titles is None:
                titles = tuple(
                    default_title(doc, index + 1, source_name)
                    for index, (doc, source_name) in enumerate(zip(loaded, source_names))
                )
            base.merge(
                *loaded, create_bookmarks=create_bookmarks, bookmark_titles=titles
            )
            buffer = io.BytesIO()
            base.save(buffer)
            result = OperationResult(buffer.getvalue())
            self._emit([result] * max(len(outputs), 1), outputs)
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
    """Split every input per page, per fixed-size group, per selection or by bookmarks."""

    def process(self, options: SplitOptions) -> ResultContainer:
        self._require_inputs(options)
        from aspose_pdf.engine.bookmark_split import split_by_bookmarks
        from aspose_pdf.engine.page_count_split import split_every
        from aspose_pdf.engine.simple_pdf import SimplePdf

        limits = _coerce_limits(getattr(options, "limits", None))
        selections = getattr(options, "selections", None)
        bookmark_level = getattr(options, "bookmark_level", None)
        include_preamble = getattr(options, "include_preamble", True)
        pages_per_part = getattr(options, "pages_per_part", None)
        _validate_split_options(
            selections, bookmark_level, include_preamble, pages_per_part
        )
        budget = _LoadBudget(limits)
        results: list[OperationResult] = []
        for source in options.inputs:
            pdf = SimplePdf.from_bytes(
                _read_source_bytes(source, limits), limits=limits
            )
            try:
                page_count = len(pdf.pages)
                if bookmark_level is not None or pages_per_part is not None:
                    parts = (
                        split_by_bookmarks(
                            pdf, level=bookmark_level, include_preamble=include_preamble
                        )
                        if bookmark_level is not None
                        else split_every(pdf, pages_per_part)
                    )
                    for part in parts:
                        try:
                            if pages_per_part is not None:
                                budget.check(
                                    len(results) + 1,
                                    "max_container_items",
                                    "page-count split results",
                                )
                            results.append(OperationResult(part.to_bytes()))
                        finally:
                            part.dispose()
                    continue
                if selections is None:
                    groups = [[index] for index in range(page_count)]
                else:
                    groups = [
                        _resolve_selection(selection, page_count, limits=limits)
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
        from aspose_pdf.engine.text_selection import (
            page_indexes,
            validate_skip_artifacts,
        )

        pages = page_indexes(getattr(options, "pages", None), limits=limits)
        skip_artifacts = validate_skip_artifacts(getattr(options, "skip_artifacts", False))
        from aspose_pdf.engine.text_region import validate_exclude_regions

        exclusions = validate_exclude_regions(
            getattr(options, "exclude_regions", None), limits=limits,
        )
        page_numbers = None if pages is None else tuple(index + 1 for index in pages)
        results: list[OperationResult] = []
        for source in options.inputs:
            extractor = PdfExtractor()
            try:
                extractor.bind_pdf(
                    _read_source_bytes(source, limits), limits=limits
                )
                extractor.extract_text(
                    page_numbers=page_numbers,
                    clip=getattr(options, "clip", None),
                    skip_artifacts=skip_artifacts,
                    exclude_regions=exclusions,
                )
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
    one file per image. Each TIFF, GIF or WebP frame counts as one image.
    TIFF/WebP/BMP/GIF inputs require the optional ``images`` extra; WebP also
    needs Pillow's WebP codec. Animated GIF/WebP frames become full composited
    pages in display order, once per stored frame regardless of timing/loops.
    A numeric ``dpi`` overrides source resolution metadata. ``dpi="auto"``
    uses each image/frame's absolute X/Y resolution, falling back to 72 DPI
    when no usable pair exists. Inch/cm units and PNG/BMP pixels per metre
    are supported. JPEG EXIF takes priority over JFIF; GIF uses the fallback.
    ``auto_orient=True`` applies all eight JPEG EXIF orientations, including
    reflections, when placing the original JPEG bytes without recompression.
    The default is ``False``. It affects JPEG only; TIFF already applies its
    own orientation. Missing JPEG orientation means upright; malformed EXIF
    raises :class:`~aspose_pdf.exceptions.PdfValidationException` when enabled.
    """

    def __init__(
        self,
        *,
        page_size: Any = None,
        dpi: float | Literal["auto"] = 72.0,
        margin: float = 0.0,
        single_document: bool = True,
        auto_orient: bool = False,
        limits: PdfLoadLimits | None = None,
    ) -> None:
        super().__init__(limits=limits)
        self.page_size = page_size
        self.dpi = dpi
        self.margin = margin
        self.single_document = single_document
        self.auto_orient = auto_orient


class ConvertOptions(PluginOptions):
    """Options for :class:`Converter`: render or re-express each input.

    ``output_format`` is ``"html"``, ``"markdown"``, ``"svg"``, ``"tiff"``,
    ``"png"``, ``"jpeg"`` or ``"webp"``. HTML, Markdown and TIFF give **one
    result per input**; the other formats give **one per page**. ``pages`` names
    the pages to take (all of them when omitted). The remaining arguments are
    passed to the operation each format uses: ``embed_images`` to
    :meth:`~aspose_pdf.Document.to_html` /
    :meth:`~aspose_pdf.Document.to_markdown`, and ``dpi``, ``mode``,
    ``compression`` and ``quality`` to the renderer and its encoders.
    ``lossless`` and ``method`` configure WebP, which needs the ``images`` extra.
    """

    FORMATS = ("html", "markdown", "svg", "tiff", "png", "jpeg", "webp")

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
        lossless: bool = False,
        method: int = 4,
        dither: bool = False,
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
        self.lossless = lossless
        self.method = method
        self.dither = dither


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
        permissions: int | Permissions = -4,
        algorithm: str = "AES-256",
        limits: PdfLoadLimits | None = None,
    ) -> None:
        super().__init__(limits=limits)
        self.user_password = user_password
        self.owner_password = owner_password
        self.permissions = Permissions.of(permissions)
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
    """Select fields, non-widget annotations and zero-based pages to flatten.

    Filters match :meth:`Document.flatten`. Inputs default to flattening both
    fields and annotations. Iterable selections are stored for reuse across
    multiple input documents; unknown names raise unless ``strict=False``.
    """

    def __init__(
        self,
        limits: PdfLoadLimits | None = None,
        *,
        fields: bool | Iterable[str] = True,
        annotations: bool = True,
        pages: Iterable[int] | None = None,
        strict: bool = True,
    ) -> None:
        super().__init__(limits=limits)
        from aspose_pdf.engine.flatten import _selection

        # Materialize iterators once, with the same limits as the input PDFs.
        budget = _LoadBudget(self.limits)
        self.fields = (
            fields if isinstance(fields, bool)
            else _selection(fields, names=True, budget=budget)
        )
        if not isinstance(annotations, bool) or not isinstance(strict, bool):
            raise TypeError("annotations and strict must be booleans")
        self.annotations = annotations
        self.strict = strict
        self.pages = (
            None if pages is None
            else _selection(pages, names=False, budget=budget)
        )


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
    """Make PDFs from JPEG/PNG/TIFF/WebP/BMP/GIF images and animation frames.

    Each page is cut to its own picture unless ``page_size`` says otherwise, in
    which case the image is centred inside that sheet and scaled down to fit
    within the margin. By default all images and frames become pages of one
    document; with ``single_document=False`` each image becomes a document of its
    own. TIFF frames retain their source order, including eight-bit RGBA/LA
    transparency. GIF/WebP animations produce one page per composited frame,
    retaining alpha, blend and disposal results; durations and loops are not
    carried into the PDF. TIFF and WebP/BMP/GIF require the ``images`` extra.
    Set ``auto_orient=True`` to honor JPEG EXIF rotation and reflection without
    recompressing the JPEG or requiring Pillow.
    """

    def process(self, options: ImagesToPdfOptions) -> ResultContainer:
        self._require_inputs(options)
        from aspose_pdf.engine.content_authoring import image_pixel_size
        from aspose_pdf.engine.image_import import image_frames, pillow_image_format
        from aspose_pdf.engine.image_import_resolution import (
            DEFAULT_RESOLUTION,
            image_resolution,
        )
        from aspose_pdf.engine.jpeg_orientation import jpeg_metadata
        from aspose_pdf.engine.tiff_import import is_tiff, tiff_frames

        limits = _coerce_limits(getattr(options, "limits", None))
        auto_orient = getattr(options, "auto_orient", False)
        if not isinstance(auto_orient, bool):
            raise PdfValidationException("auto_orient must be a boolean")
        # Read, then checked: ``value or default`` would turn a dpi of 0 into the
        # default instead of refusing it.
        dpi_option = getattr(options, "dpi", 72.0)
        auto_resolution = isinstance(dpi_option, str) and dpi_option == "auto"
        try:
            dpi = 72.0 if auto_resolution else float(dpi_option)
            margin = float(getattr(options, "margin", 0.0))
        except (TypeError, ValueError):
            raise AsposePdfException("dpi and margin must be numbers") from None
        if not math.isfinite(dpi) or not dpi > 0:
            raise AsposePdfException("dpi must be finite and above zero")
        if not math.isfinite(margin) or margin < 0:
            raise AsposePdfException("margin must be finite and cannot be negative")
        requested = getattr(options, "page_size", None)
        single = bool(getattr(options, "single_document", True))

        results: list[OperationResult] = []
        budget = _LoadBudget(limits)
        page_count = 0
        document: Document | None = Document(limits=limits) if single else None
        try:
            for source in options.inputs:
                data = _read_source_bytes(source, limits)
                orientation = 1
                resolution = DEFAULT_RESOLUTION
                if data.startswith(b"\xff\xd8") and (auto_orient or auto_resolution):
                    orientation, metadata_resolution = jpeg_metadata(
                        data, budget, read_orientation=auto_orient,
                        read_resolution=auto_resolution,
                    )
                    resolution = metadata_resolution or DEFAULT_RESOLUTION
                elif auto_resolution and not is_tiff(data):
                    resolution = image_resolution(data, budget)
                if is_tiff(data):
                    frames = tiff_frames(
                        data, budget, preceding_pages=page_count,
                        read_resolution=auto_resolution,
                    )
                    frame_context = closing(frames)
                elif pillow_image_format(data) is not None:
                    frames = image_frames(
                        data, budget, preceding_pages=page_count, resolution=resolution,
                    )
                    frame_context = closing(frames)
                else:
                    pixel_width, pixel_height = image_pixel_size(data)
                    frames = iter(((data, pixel_width, pixel_height, resolution),))
                    frame_context = nullcontext(frames)
                with frame_context as images:
                    for image_data, pixel_width, pixel_height, source_resolution in images:
                        page_count += 1
                        budget.check_pages(page_count)
                        if document is None:
                            document = Document(limits=limits)
                        x_dpi, y_dpi = source_resolution if auto_resolution else (dpi, dpi)
                        self._place(
                            document,
                            image_data,
                            pixel_width * 72.0 / x_dpi,
                            pixel_height * 72.0 / y_dpi,
                            requested,
                            margin,
                            orientation,
                        )
                        del image_data
                        if not single:
                            buffer = io.BytesIO()
                            document.save(buffer)
                            results.append(OperationResult(buffer.getvalue()))
                            document.dispose()
                            document = None
            if single and document is not None:
                buffer = io.BytesIO()
                document.save(buffer)
                results.append(OperationResult(buffer.getvalue()))
        finally:
            if document is not None:
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
        orientation: int = 1,
    ) -> None:
        """Add one page carrying one image, sized the way the options ask."""
        if orientation >= 5:
            width, height = height, width
        if requested is None:
            page = document.pages.add(size=(width, height))
            x = y = 0.0
        else:
            page = document.pages.add(size=requested)
            box_width = page.size.width - 2 * margin
            box_height = page.size.height - 2 * margin
            if box_width <= 0 or box_height <= 0:
                raise AsposePdfException(
                    "The margin leaves no room for the image on this page size"
                )
            scale = min(box_width / width, box_height / height, 1.0)
            width, height = width * scale, height * scale
            x = margin + (box_width - width) / 2.0
            y = margin + (box_height - height) / 2.0
        if orientation == 1:
            page.add_image(data, x, y, width, height)
        else:
            from aspose_pdf.engine.jpeg_orientation import orientation_matrix

            matrix = orientation_matrix(orientation, x, y, width, height)
            with page.graphics(transform=matrix):
                page.add_image(data, 0, 0, 1, 1)


class Converter(PdfPlugin):
    """Re-express each input in another format -- HTML, Markdown, SVG or a raster.

    HTML, Markdown and TIFF produce one result per input; SVG, PNG, JPEG and WebP
    produce one per page. Text formats come back as text results, rasters as bytes.
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
                        threshold=getattr(options, "threshold", 128),
                        dither=getattr(options, "dither", False),
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
                    OperationResult(raster.to_png(mode=getattr(options, "mode", "rgb"), threshold=getattr(options, "threshold", 128), dither=getattr(options, "dither", False)))
                )
            elif output_format == "webp":
                results.append(
                    OperationResult(
                        raster.to_webp(
                            quality=getattr(options, "quality", 85),
                            mode=getattr(options, "mode", "rgb"), threshold=getattr(options, "threshold", 128), dither=getattr(options, "dither", False),
                            lossless=getattr(options, "lossless", False),
                            method=getattr(options, "method", 4),
                        )
                    )
                )
            else:
                results.append(
                    OperationResult(
                        raster.to_jpeg(
                            quality=getattr(options, "quality", 85),
                            mode=getattr(options, "mode", "rgb"),
                            threshold=getattr(options, "threshold", 128),
                            dither=getattr(options, "dither", False),
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
                permissions=getattr(options, "permissions", Permissions.all()),
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


class FormFiller(PdfPlugin):
    """Generate an independent filled PDF for every template/record pair.

    Each input is read once and reopened from its original bytes per record,
    so omitted values retain the template's answers without carrying data from
    previous rows. Current form filling, choice validation and optional field
    flattening are reused. No JavaScript or business-rule validation is run.

    All results are produced before configured outputs are written; processing
    failures close working documents and emit no partial batch. File/stream
    output failures follow ordinary DataSource semantics. The returned PDFs
    are retained in memory under the batch byte policy; use smaller batches
    for large templates. Streams are read from their current position and left
    open. Duplicate output file paths/stream objects are refused before work.
    """

    def process(self, options: FormFillerOptions) -> ResultContainer:
        from aspose_pdf.engine.form_batch import (
            OutputBuffer,
            check_document,
            snapshot_records,
            snapshot_streams,
            validate_options,
        )

        if not isinstance(options, FormFillerOptions):
            raise TypeError("options must be FormFillerOptions")
        self._require_inputs(options)
        limits = _coerce_limits(options.limits)
        strict, flatten, password = options.strict, options.flatten, options.password
        validate_options(strict, flatten, password)
        records, retained = snapshot_records(options.records, limits)
        inputs, outputs = tuple(options.inputs), tuple(options.outputs)
        if not all(isinstance(source, DataSource) for source in (*inputs, *outputs)):
            raise TypeError("inputs and outputs must contain DataSource objects")
        budget = _LoadBudget(limits)
        budget.check(len(inputs), "max_container_items", "form batch templates")
        count = len(inputs) * len(records)
        budget.check(count, "max_container_items", "form batch result count")
        if len(outputs) > count:
            raise AsposePdfException("More output data sources were provided than results were produced")
        destinations = set()
        for output in outputs:
            if isinstance(output, FileDataSource):
                identity = ("file", os.path.normcase(str(output.path.resolve())))
            elif isinstance(output, StreamDataSource):
                identity = ("stream", id(output.stream))
            else:
                identity = ("source", id(output))
            if identity in destinations:
                raise PdfValidationException("Form batch outputs must have distinct destinations")
            destinations.add(identity)
        if not records:
            return ResultContainer()
        results = []
        total_pages = 0
        for source in inputs:
            data = _read_source_bytes(source, limits)
            for index, values in enumerate(records):
                with Document(limits=limits) as document:
                    document.load_from(data, password=password)
                    if index == 0:
                        total_pages += document.page_count * len(records)
                        budget.check_pages(total_pages)
                    original_streams = snapshot_streams(document)
                    document.form.fill(values, strict=strict)
                    if flatten:
                        document.flatten(annotations=False)
                    check_document(document, original_streams)
                    with OutputBuffer(budget, retained) as output:
                        document.save(output)
                        payload = output.getvalue()
                    retained += len(payload)
                    results.append(OperationResult(payload))
        self._emit(results, outputs)
        return ResultContainer(results)


class FormFlattener(PdfPlugin):
    """Bake the fields and annotations selected by :class:`FlattenOptions`."""

    def process(self, options: FlattenOptions) -> ResultContainer:
        return self._transform(
            options,
            lambda document: document.flatten(
                fields=options.fields, annotations=options.annotations,
                pages=options.pages, strict=options.strict,
            ),
        )


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
