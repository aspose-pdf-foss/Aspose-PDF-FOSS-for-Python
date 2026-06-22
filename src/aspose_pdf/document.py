"""Document class for PDF manipulation.

This module provides the main Document class that wraps the native PDF engine.
"""

from __future__ import annotations

from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    BinaryIO,
    Callable,
    Dict,
    Generator,
    Iterator,
    List,
    Optional,
    Sequence,
    Union,
)

from aspose_pdf.attachments import FileSpecification
from aspose_pdf.engine.simple_pdf import (
    SimplePdf,
    _effective_encryption_password,
    _parse_pdf_date,
)
from aspose_pdf.exceptions import AsposePdfException
from aspose_pdf.outlines import OutlineCollection
from aspose_pdf.pdfa import PdfAValidationResult
from aspose_pdf.pdfua import PdfUaValidationResult

if TYPE_CHECKING:
    import datetime as _datetime

    from aspose_pdf.engine.rasterizer import RasterizedPage
    from aspose_pdf.forms import Form
    from aspose_pdf.optimization import OptimizationOptions
    from aspose_pdf.pages import Page, PageCollection
    from aspose_pdf.xmp import XmpPacket


def _coerce_date(value: Any) -> "Optional[_datetime.datetime]":
    """Normalise an attachment date to a ``datetime`` (or ``None``).

    Read-back metadata is already parsed to :class:`datetime.datetime`; metadata
    supplied to :meth:`Document.add_attachment` may instead be a pre-formatted
    ``D:`` string, which is parsed here.
    """
    import datetime as _dt

    if value is None or isinstance(value, _dt.datetime):
        return value
    if isinstance(value, str):
        return _parse_pdf_date(value)
    return None


class Document:
    """Pythonic wrapper for PDF document lifecycle and core operations."""

    def __init__(self, *args, **kwargs) -> None:
        """Create a new Document instance."""
        self._engine_pdf: SimplePdf = SimplePdf()  # Start with empty PDF
        self._disposed: bool = False
        self._pages: Optional[Any] = None
        self._form: Optional[Any] = None
        self._outlines: Optional[OutlineCollection] = None
        self._password: Optional[str] = None
        self._encrypted: bool = False
        self.file_name: Optional[str] = None

    def _ensure_not_disposed(self) -> None:
        """Raise if the document has been disposed."""
        if self._disposed:
            raise AsposePdfException("Document has been disposed")

    def __enter__(self) -> "Document":
        """Support for context manager."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Support for context manager."""
        self.dispose()

    @property
    def pages(self) -> PageCollection:
        """Get the collection of pages."""
        self._ensure_not_disposed()
        if self._pages is None:
            from aspose_pdf.pages import PageCollection

            self._pages = PageCollection(self)
        return self._pages

    @property
    def form(self) -> Form:
        """Get the interactive form of the document."""
        self._ensure_not_disposed()
        if self._form is None:
            from aspose_pdf.forms import Form

            self._form = Form(self)
        return self._form

    @property
    def attachments(self):
        """Get the collection of attachments in the document."""
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            return []
        # Return attachments as a collection-like object
        return self._engine_pdf.attachments if hasattr(self._engine_pdf, 'attachments') else []

    def add_attachment(
        self,
        name: str,
        content: bytes,
        *,
        mime: str | None = None,
        description: str | None = None,
        creation_date=None,
        mod_date=None,
        compress: bool = True,
    ) -> "Document":
        """Embed *content* as a document-level file attachment named *name*.

        The attachment is written to the catalog ``/Names /EmbeddedFiles`` name
        tree (as a ``/Filespec`` referencing an ``/EmbeddedFile`` stream) when
        the document is saved.  This is equivalent to assigning into the
        :attr:`attachments` mapping; re-adding the same *name* replaces it.

        Optional metadata:

        * *mime* — the media type written as the embedded file ``/Subtype``
          (e.g. ``"text/plain"``).
        * *description* — a human-readable ``/Desc`` on the file specification.
        * *creation_date* / *mod_date* — a :class:`datetime.datetime` (or a
          pre-formatted ``D:`` string) stored in the embedded file ``/Params``.
        * *compress* — Flate-compress the payload (default), unless that would
          make it larger.

        Returns *self* for chaining.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        self._engine_pdf.attachments[name] = bytes(content)
        meta = {"compress": compress}
        if mime is not None:
            meta["mime"] = mime
        if description is not None:
            meta["description"] = description
        if creation_date is not None:
            meta["creation_date"] = creation_date
        if mod_date is not None:
            meta["mod_date"] = mod_date
        self._engine_pdf.attachment_meta[name] = meta
        return self

    @property
    def embedded_files(self) -> List[FileSpecification]:
        """The document's embedded files as typed :class:`FileSpecification`.

        Each entry carries the decoded ``contents`` plus any MIME type,
        description and creation / modification dates stored with the attachment —
        read back from the ``/Filespec`` and ``/EmbeddedFile`` objects, or taken
        from metadata passed to :meth:`add_attachment` before the first save.
        Attachments added without metadata expose ``None`` for those fields. The
        list is ordered by name.

        This is the typed, read-only counterpart to :attr:`attachments` (a plain
        ``name -> bytes`` mapping, which stays writable).
        """
        self._ensure_not_disposed()
        eng = self._engine_pdf
        if eng is None:
            return []
        read_meta = getattr(eng, "attachment_read_meta", {}) or {}
        write_meta = getattr(eng, "attachment_meta", {}) or {}
        specs: List[FileSpecification] = []
        for name in sorted(eng.attachments):
            # In-memory metadata (add_attachment) wins over parsed read-back
            # metadata for the same name.
            meta = {**(read_meta.get(name) or {}), **(write_meta.get(name) or {})}
            specs.append(
                FileSpecification(
                    name=name,
                    contents=bytes(eng.attachments[name]),
                    mime_type=meta.get("mime"),
                    description=meta.get("description"),
                    creation_date=_coerce_date(meta.get("creation_date")),
                    mod_date=_coerce_date(meta.get("mod_date")),
                )
            )
        return specs

    def get_embedded_file(self, name: str) -> Optional[FileSpecification]:
        """Return the embedded file named *name* as a :class:`FileSpecification`,
        or ``None`` when the document has no attachment with that name."""
        self._ensure_not_disposed()
        for spec in self.embedded_files:
            if spec.name == name:
                return spec
        return None

    @property
    def page_count(self) -> int:
        """Return the current number of pages."""
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            return 0
        return len(self._engine_pdf.pages)

    @property
    def info(self) -> Dict[str, str]:
        """Get or set the document metadata (info dictionary)."""
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            return {}
        return self._engine_pdf.metadata

    @info.setter
    def info(self, value: Dict[str, str]):
        self._ensure_not_disposed()
        if self._engine_pdf is not None:
            self._engine_pdf.metadata = dict(value)

    @property
    def xmp_metadata(self) -> "XmpPacket":
        """Get or set the document's XMP metadata packet (catalog ``/Metadata``).

        The getter lazily parses the catalog ``/Metadata`` stream (an empty
        :class:`~aspose_pdf.xmp.XmpPacket` when the document has none). Edits are
        written back to the stream on :meth:`save`. The get/modify/set pattern is
        the most explicit way to persist a change::

            xmp = doc.xmp_metadata
            xmp.set_value("dc", "title", "My Title")
            doc.xmp_metadata = xmp
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            from aspose_pdf.xmp import XmpPacket as _XmpPacket

            return _XmpPacket()
        return self._engine_pdf.xmp_packet

    @xmp_metadata.setter
    def xmp_metadata(self, value: "XmpPacket") -> None:
        self._ensure_not_disposed()
        if self._engine_pdf is not None:
            self._engine_pdf.xmp_packet = value

    def sync_metadata(self, *, direction: str = "info_to_xmp") -> "Document":
        """Synchronise the ``/Info`` dictionary and the XMP metadata packet.

        The standard document properties are kept consistent between the
        ``/Info`` dictionary (:attr:`info`) and the XMP packet
        (:attr:`xmp_metadata`) — ``Title``/``Author``/``Subject``/``Keywords``/
        ``Creator``/``Producer``/``CreationDate``/``ModDate`` map to
        ``dc:title``/``dc:creator``/``dc:description``/``pdf:Keywords``/
        ``xmp:CreatorTool``/``pdf:Producer``/``xmp:CreateDate``/
        ``xmp:ModifyDate`` (PDF dates are converted to/from ISO-8601). Keeping
        the two in sync is required for PDF/A conformance.

        *direction* selects which side is authoritative:

        * ``"info_to_xmp"`` (default) — copy ``/Info`` values into the XMP
          packet (overwriting the mapped XMP properties).
        * ``"xmp_to_info"`` — copy the mapped XMP properties into ``/Info``.

        Returns ``self`` for chaining; changes persist on :meth:`save`.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            return self
        from aspose_pdf.xmp import info_to_xmp, xmp_to_info

        if direction == "info_to_xmp":
            packet = self.xmp_metadata
            info_to_xmp(self.info, packet)
            self.xmp_metadata = packet
        elif direction == "xmp_to_info":
            info = dict(self.info)
            info.update(xmp_to_info(self.xmp_metadata))
            self.info = info
        else:
            raise ValueError(
                "direction must be 'info_to_xmp' or 'xmp_to_info', "
                f"got {direction!r}"
            )
        return self

    @property
    def is_encrypted(self) -> bool:
        """Return True if document is encrypted."""
        return self._encrypted or (
            self._engine_pdf is not None and self._engine_pdf.encrypted
        )

    @property
    def id(self) -> Optional[List[bytes]]:
        """Return the two-element file-identifier array from the PDF trailer.

        The value is ``None`` for freshly created (unsaved) documents; after
        the first :meth:`save` the array is generated and preserved.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            return None
        return self._engine_pdf.file_id

    @property
    def version(self) -> str:
        """PDF version string as it appears in the file header (e.g. ``'1.7'``)."""
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            return "1.7"
        return self._engine_pdf.pdf_version

    @version.setter
    def version(self, value: str) -> None:
        """Set the PDF version written on the next :meth:`save` call."""
        self._ensure_not_disposed()
        if self._engine_pdf is not None:
            self._engine_pdf.pdf_version = str(value)

    @property
    def outlines(self) -> OutlineCollection:
        """Bookmark tree for this document.

        Returns a live :class:`~aspose_pdf.outlines.OutlineCollection`.  Changes
        made to the collection are persisted when :meth:`save` is called.
        """
        self._ensure_not_disposed()
        if self._outlines is None:
            data = (
                getattr(self._engine_pdf, "_outlines_data", [])
                if self._engine_pdf
                else []
            )
            self._outlines = OutlineCollection._from_list(data)
        return self._outlines

    @property
    def permissions(self) -> int:
        """Access-permission flags (PDF ``/P`` value).

        For unencrypted documents this returns ``-4`` (all permissions granted).
        The value is a signed 32-bit integer as defined in the PDF spec
        (Table 22 – User access permissions).
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            return -4
        return self._engine_pdf.P

    @classmethod
    def open_streaming(
        cls, path: Union[str, Path], *, password: Optional[str] = None
    ) -> "Document":
        """Open a PDF in streaming/lazy mode for memory-efficient page processing.

        Unlike :meth:`load_from`, page content streams are **not** decoded
        upfront.  Each page's content is decoded on demand when accessed via
        :attr:`~aspose_pdf.pages.Page.content` or
        :meth:`iter_page_content_streams`.  This is ideal for large PDFs where
        only a subset of pages needs to be processed.

        The returned :class:`Document` is a normal context manager — use it
        with ``with`` to ensure resources are released::

            with Document.open_streaming("large.pdf") as doc:
                for page in doc.iter_pages():
                    text = page.content   # decoded on demand
                    ...

        Parameters
        ----------
        path:
            File system path to the PDF.
        password:
            Optional password for encrypted PDFs.

        Returns
        -------
        Document
            A document with ``_engine_pdf._lazy == True``.

        Raises
        ------
        FileNotFoundError
            If *path* does not exist.
        PdfSecurityException
            If the PDF is encrypted and *password* is missing, empty, or
            whitespace-only (after stripping).
        """
        doc = cls()
        doc._engine_pdf = SimplePdf.from_file_lazy(Path(path), password=password)
        doc.file_name = str(path)
        eff = _effective_encryption_password(password)
        if eff:
            doc._password = eff
        return doc

    def iter_pages(self) -> Iterator[Page]:
        """Iterate over the pages of the document one at a time.

        This is a lightweight generator that yields :class:`~aspose_pdf.pages.Page`
        objects without materialising the full list.  It works for both
        normally-loaded documents and documents opened in streaming mode via
        :meth:`open_streaming`.

        In streaming mode, each page's content stream is decoded only when
        :attr:`~aspose_pdf.pages.Page.content` is accessed, keeping memory
        usage proportional to one page rather than the whole document.

        Yields
        ------
        Page
            Pages in document order, starting at index 0.
        """
        self._ensure_not_disposed()
        from aspose_pdf.pages import Page

        for i in range(self.page_count):
            yield Page(self, i)

    def iter_page_content_streams(self) -> Generator[bytes, None, None]:
        """Yield decoded content stream bytes for each page, one at a time.

        Delegates to :meth:`SimplePdf.iter_page_content_streams`.  In lazy
        mode only one page's content is held in memory at any point.

        Yields
        ------
        bytes
            Decoded content bytes for each page in order.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            return
        yield from self._engine_pdf.iter_page_content_streams()

    def render_page(
        self,
        page_index: int,
        *,
        dpi: float = 72.0,
        scale: float = 1.0,
        background: tuple[int, int, int] = (255, 255, 255),
        antialias: Union[bool, int] = True,
    ) -> "RasterizedPage":
        """Render a page to an RGB raster image.

        ``page_index`` is zero-based. The result can be encoded with
        :meth:`RasterizedPage.to_png`, :meth:`RasterizedPage.to_tiff`, or saved
        directly with :meth:`RasterizedPage.save`. ``antialias`` smooths edges by
        supersampling (``True`` = 3x, an integer 1-8 sets the factor, ``False``
        disables it).
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        from aspose_pdf.engine.rasterizer import render_page

        return render_page(
            self._engine_pdf,
            page_index,
            dpi=dpi,
            scale=scale,
            background=background,
            antialias=antialias,
        )

    def save_page_as_image(
        self,
        page_index: int,
        destination: Union[str, Path],
        *,
        dpi: float = 72.0,
        scale: float = 1.0,
        background: tuple[int, int, int] = (255, 255, 255),
        antialias: Union[bool, int] = True,
    ) -> Path:
        """Render a page and save it as ``.png`` or ``.tif/.tiff``."""
        return self.render_page(
            page_index,
            dpi=dpi,
            scale=scale,
            background=background,
            antialias=antialias,
        ).save(destination)

    def replace_text(
        self,
        search: str,
        replacement: str,
        *,
        page_index: Optional[int] = None,
        case_sensitive: bool = True,
        max_count: int = 0,
    ) -> int:
        """Replace existing text in simple page-content text-showing operands.

        When *page_index* is omitted, every page is scanned. ``max_count=0``
        means unlimited. This is a conservative content-stream edit for simple
        ``Tj``/``TJ`` operands; it does not perform layout reflow. Returns the
        number of replacements made.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        return self._engine_pdf.replace_text(
            search,
            replacement,
            page_index=page_index,
            case_sensitive=case_sensitive,
            max_count=max_count,
        )

    def redact_text(
        self,
        search: str,
        *,
        page_index: Optional[int] = None,
        case_sensitive: bool = True,
        max_count: int = 0,
        overlay: bool = False,
        overlay_color: Sequence[float] = (0.0, 0.0, 0.0),
    ) -> int:
        """Remove existing text from simple page-content text-showing operands.

        With ``overlay=True`` a filled rectangle (``overlay_color``, a DeviceRGB
        triple of 0..1, default black) is drawn over each removed run -- the
        classic redaction bar. The bar is cosmetic (the text is already removed
        from the content); a run whose position cannot be tracked (a Type0 or
        unresolved font) is left unmarked rather than leaking text.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        return self._engine_pdf.redact_text(
            search,
            page_index=page_index,
            case_sensitive=case_sensitive,
            max_count=max_count,
            overlay=overlay,
            overlay_color=tuple(overlay_color),
        )

    def load_from(
        self,
        source: Union[str, bytes, bytearray, Path, BinaryIO],
        *,
        password: Optional[str] = None,
    ) -> "Document":
        """Load a PDF from a file path, raw bytes, or a binary stream.

        Parameters
        ----------
        source : str, Path, bytes, bytearray, or BinaryIO
            File path, raw PDF bytes, or any readable binary stream
            (e.g. ``BytesIO``, an open file handle in ``"rb"`` mode).
            When a stream is supplied its current position is read to EOF;
            the stream is **not** closed afterwards.
        password : str, optional
            Password for encrypted PDFs.

        Returns
        -------
        Document
            Self for method chaining.

        Raises
        ------
        FileNotFoundError
            If source is a path that doesn't exist.
        ValueError
            If source is bytes/stream that are not valid PDF data.
        TypeError
            If source is none of the accepted types.
        """
        self._ensure_not_disposed()
        eff_pwd = _effective_encryption_password(password)
        self._password = eff_pwd if eff_pwd is not None else password

        if isinstance(source, (str, Path)):
            path = Path(source)
            if not path.is_file():
                raise FileNotFoundError(f"File not found: {path}")
            self._engine_pdf = SimplePdf.from_file(path, password=password)
            self.file_name = str(path)
        elif isinstance(source, (bytes, bytearray)):
            # SimplePdf.from_bytes validates the PDF header; pass password so
            # that encrypted PDFs are not rejected before decrypt() is called.
            self._engine_pdf = SimplePdf.from_bytes(bytes(source), password=password)
            self.file_name = None
        elif hasattr(source, "read"):
            # BinaryIO / file-like object — read all bytes then delegate
            data = source.read()
            if not isinstance(data, (bytes, bytearray)):
                raise TypeError("stream read() must return bytes")
            self._engine_pdf = SimplePdf.from_bytes(bytes(data), password=password)
            self.file_name = None
        else:
            raise TypeError(
                "source must be str, Path, bytes, or a readable binary stream"
            )

        if eff_pwd and self._engine_pdf:
            self._engine_pdf.decrypt(eff_pwd)

        self._encrypted = self._engine_pdf.encrypted if self._engine_pdf else False
        return self

    def optimize(
        self,
        options: "OptimizationOptions | None" = None,
        *,
        compress_streams: bool = True,
    ) -> "Document":
        """Process the document and remove unused resources.

        This includes image/stream deduplication, garbage collection, and stream
        compression.

        Parameters
        ----------
        options : OptimizationOptions, optional
            Controls which techniques run. Defaults to
            :class:`~aspose_pdf.optimization.OptimizationOptions` with the
            standard cleanups enabled.
        compress_streams : bool
            When ``False``, skip Flate compression of streams (other cleanups
            still run). Defaults to ``True``.

        Returns
        -------
        Document
            Self for method chaining.
        """
        self._ensure_not_disposed()
        if self._engine_pdf:
            self._engine_pdf.optimize(options, compress_streams=compress_streams)
        return self

    def optimize_resources(
        self, options: "OptimizationOptions | None" = None
    ) -> "Document":
        """Alias for :meth:`optimize`."""
        return self.optimize(options)

    def compress_streams(self) -> "Document":
        """Compress uncompressed document streams.

        Returns
        -------
        Document
            Self for method chaining.
        """
        self._ensure_not_disposed()
        if self._engine_pdf:
            self._engine_pdf.compress_streams()
        return self

    def is_pdfa_compliant(self, level: str = "1b") -> bool:
        """Check if the document complies with the specified PDF/A level.

        Uses the same **heuristic** engine as :meth:`validate_pdfa`; do not use
        as a certification gate.

        Parameters
        ----------
        level : str
            PDF/A level ('1b', '2b', '3b', etc.)

        Returns
        -------
        bool
            True if compliant, False otherwise.
        """
        return len(self.validate_pdfa(level)) == 0

    def validate_pdfa(self, level: str = "1b") -> PdfAValidationResult:
        """Validate the document against PDF/A standards (heuristic checks).

        This implementation performs partial, rule-of-thumb checks — not a
        full PDF/A validator. Use :attr:`PdfAValidationResult.is_heuristic`
        (always ``True`` here) and :attr:`PdfAValidationResult.HEURISTIC_VALIDATION_NOTICE`
        when building compliance automation.

        Parameters
        ----------
        level : str
            PDF/A conformance level to check (e.g. ``"1b"``, ``"2b"``).

        Returns
        -------
        PdfAValidationResult
            Detailed result with ``errors``, ``warnings``, ``level``,
            ``is_heuristic``, and ``is_valid`` fields.  ``len(result)`` equals
            ``len(result.errors)`` for backward-compatible usage.
        """
        self._ensure_not_disposed()
        if self._engine_pdf:
            issues, warnings = self._engine_pdf.check_pdfa_compliance_detailed(level)
        else:
            issues, warnings = (["No document loaded"], [])
        return PdfAValidationResult(errors=issues, warnings=warnings, level=level)

    @property
    def is_pdfua_compliant(self) -> bool:
        """Heuristic PDF/UA catalog structure check (tagged PDF shell).

        Uses the same rules as :meth:`validate_pdfua`.  A ``True`` value does
        **not** mean the document is accessible or PDF/UA certified — only
        that required catalog entries for tagging passed this library's
        lightweight inspection.
        """
        self._ensure_not_disposed()
        return self.validate_pdfua().is_valid

    def validate_pdfua(self) -> PdfUaValidationResult:
        """Validate catalog-level PDF/UA prerequisites (heuristic).

        Checks for ``/StructTreeRoot``, ``/MarkInfo`` with ``/Marked true``,
        and emits a warning when ``/Lang`` is missing.  This is **not** a full
        PDF/UA-1 validator: use :attr:`PdfUaValidationResult.is_heuristic` and
        :attr:`PdfUaValidationResult.HEURISTIC_VALIDATION_NOTICE` when
        building compliance or accessibility automation.

        Returns
        -------
        PdfUaValidationResult
            ``errors``, ``warnings``, ``is_heuristic`` (default ``True``), and
            ``is_valid``.  ``len(result)`` equals ``len(result.errors)``.
        """
        self._ensure_not_disposed()
        if self._engine_pdf:
            errs, warns = self._engine_pdf.check_pdfua_compliance()
        else:
            errs, warns = (["No document loaded"], [])
        return PdfUaValidationResult(errors=errs, warnings=warns)

    def convert_to_pdfa(
        self,
        level: str = "1b",
        *,
        font_lookup_directory: Optional[Union[str, Path]] = None,
    ) -> List[str]:
        """Convert the document to PDF/A format in-place.

        Removes prohibited content, injects an OutputIntents array with an
        sRGB ICC profile, adds an XMP metadata stream, and ensures the
        document title is set — bringing the document into conformance with
        the requested PDF/A level.

        Font embedding is not performed automatically; any fonts that are not
        already embedded are reported as warnings in the returned list.

        Parameters
        ----------
        level : str
            Target PDF/A conformance level (e.g. ``'1b'``, ``'2b'``,
            ``'3b'``).  Case-insensitive.

        Returns
        -------
        List[str]
            Remaining compliance issues that could not be fixed automatically
            (typically unembedded-font warnings).  An empty list means the
            document is now fully compliant.

        Raises
        ------
        AsposePdfException
            If the document is disposed, encrypted, or was not loaded from a
            file or byte stream.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        return self._engine_pdf.convert_to_pdfa(
            level, font_lookup_directory=font_lookup_directory
        )

    def convert_to_pdfua(
        self,
        *,
        language: str = "en",
        title: Optional[str] = None,
        auto_tag: bool = False,
    ) -> List[str]:
        """Add the catalog-level PDF/UA prerequisites to the document in place.

        Creates the structural shell PDF/UA-1 requires at the catalog level — a
        ``/StructTreeRoot``, ``/MarkInfo /Marked true``, a document ``/Lang``,
        ``/ViewerPreferences /DisplayDocTitle true``, an ``/Info /Title``, and
        an XMP metadata stream declaring ``pdfuaid:part = 1`` — so that
        :meth:`validate_pdfua` passes.

        With ``auto_tag=True`` it also infers a real (if coarse) structure tree
        from the existing page text first (see :meth:`auto_tag`). This is still
        **not** certification-grade — images, alternate descriptions, and
        fine-grained reading order are not inferred — but it produces a tagged
        document rather than only a shell.

        Parameters
        ----------
        language : str
            BCP 47 language tag for the catalog ``/Lang`` (default ``"en"``).
        title : str, optional
            Document title; falls back to an existing title or ``"Untitled"``.
        auto_tag : bool
            When ``True``, heuristically tag existing page text into the
            structure tree before building the shell (default ``False``).

        Returns
        -------
        List[str]
            Remaining PDF/UA issues that could not be fixed automatically. An
            empty list means the catalog-level prerequisites are satisfied.

        Raises
        ------
        AsposePdfException
            If the document is disposed, not loaded from a file/byte stream, or
            encrypted.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        return self._engine_pdf.convert_to_pdfua(
            language=language, title=title, auto_tag=auto_tag
        )

    def auto_tag(
        self,
        image_alt: Optional[Union[str, Callable[[str], str]]] = "Image",
    ) -> int:
        """Heuristically tag existing page content into the structure tree.

        Each text object on every page becomes a ``/P`` (or ``/H1`` when its
        font size dominates) structure element, and each image XObject paint
        becomes a ``/Figure`` with ``/Alt``. Elements are wrapped in marked
        content and linked, in reading order, through ``/StructParents`` and the
        ``/StructTreeRoot`` ``/ParentTree``. Pages already carrying marked
        content are skipped.

        Parameters
        ----------
        image_alt : str, callable, or None
            Alternate text for image figures: a fixed string, a callable
            mapping an image's resource name to its alt text, or ``None`` to
            leave images untagged (text only). Defaults to ``"Image"`` -- a
            placeholder that needs human review, since alt text cannot be
            inferred.

        Returns the number of structure elements created. This is a heuristic
        aid (no fine reading order or paragraph grouping), not certified
        accessibility; pair it with :meth:`convert_to_pdfua` for the catalog
        prerequisites.

        Raises
        ------
        AsposePdfException
            If the document is disposed, not loaded from a file/byte stream, or
            encrypted.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        return self._engine_pdf.auto_tag(image_alt)

    def save(
        self, destination: Union[str, Path, BinaryIO], *, overwrite: bool = False
    ) -> "Document":
        """Save the document to a file path or a binary stream.

        Parameters
        ----------
        destination : str, Path, or BinaryIO
            File system path *or* any writable binary stream (e.g. ``BytesIO``,
            an open file handle in binary mode, an HTTP response body, …).
        overwrite : bool
            Only relevant when *destination* is a path.  When ``False`` (the
            default) an existing file raises :exc:`FileExistsError`.

        Returns
        -------
        Document
            Self for method chaining.
        """
        self._ensure_not_disposed()

        # Sync the in-memory outline collection back to the engine before writing
        if self._outlines is not None and self._engine_pdf is not None:
            self._engine_pdf._outlines_data = self._outlines._to_list()

        if hasattr(destination, "write"):
            destination.write(self._engine_pdf.to_bytes())
        else:
            path = Path(destination)
            if path.exists() and not overwrite:
                raise FileExistsError(f"File already exists: {path}")
            self._engine_pdf.save(path)
        return self

    def dispose(self) -> None:
        """Release the document and underlying engine resources (primary lifecycle API).

        ``subset_api.yaml`` maps .NET ``Dispose`` here; :meth:`close` is specified
        as an alias. Consistent with :class:`~aspose_pdf.engine.simple_pdf.SimplePdf`
        and facades, disposal is idempotent.
        """
        if self._disposed:
            return
        self._disposed = True
        if self._engine_pdf:
            self._engine_pdf.dispose()
            self._engine_pdf = None
        self._pages = None
        self._form = None
        self._outlines = None
        self.file_name = None

    def close(self) -> None:
        """Alias of :meth:`dispose` (matches .NET ``Close``)."""
        self.dispose()

    def merge(self, *documents: "Document") -> "Document":
        """Merge the supplied documents into this one."""
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            self._engine_pdf = SimplePdf()

        for doc in documents:
            if not isinstance(doc, Document):
                raise TypeError("All items to merge must be Document instances")
            if doc._engine_pdf is not None:
                self._engine_pdf.append(doc._engine_pdf)
        return self

    def encrypt(
        self,
        user_password: str,
        owner_password: Optional[str] = None,
        *,
        permissions: int = -4,
    ) -> "Document":
        """Encrypt the PDF document.

        Parameters
        ----------
        user_password : str
            Password required to open the document.
        owner_password : str, optional
            Password required to change security settings.  Defaults to the
            user password if omitted.
        permissions : int
            PDF access-permission flags (signed 32-bit, see PDF spec Table 22).
            Defaults to ``-4`` (all standard permissions granted).
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        self._engine_pdf.encrypt(
            user_password, owner_password or user_password, permissions=permissions
        )
        self._encrypted = True
        return self

    def decrypt(self, password: str) -> "Document":
        """Decrypt the PDF document."""
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        self._engine_pdf.decrypt(password)
        self._encrypted = False
        return self

    def change_passwords(
        self,
        old_password: str,
        new_user_password: str,
        new_owner_password: Optional[str] = None,
    ) -> "Document":
        """Change document passwords."""
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        self._engine_pdf.change_passwords(
            old_password, new_user_password, new_owner_password
        )
        return self

    def validate(self) -> bool:
        """Validate the PDF document."""
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            return False
        return self._engine_pdf.validate()

    def check(self) -> bool:
        """Check PDF integrity."""
        return self.validate()

    def repair(self) -> "Document":
        """Attempt to repair the PDF document."""
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        self._engine_pdf.repair()
        return self

    def flatten(self) -> "Document":
        """Flatten annotations and forms.

        Supported shape and text-markup annotations without an appearance stream
        are given a synthesised one first (see :meth:`generate_appearances`) so
        they render into the page content rather than being dropped.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        self._engine_pdf.flatten()
        return self

    def generate_appearances(self, *, force: bool = False) -> int:
        """Synthesise missing annotation appearance streams across all pages.

        Builds an ``/AP /N`` appearance from the geometry and colours of every
        supported shape / text-markup annotation that lacks one (``Square``,
        ``Circle``, ``Line``, ``Polygon``, ``PolyLine``, ``Ink``, ``Highlight``,
        ``Underline``, ``StrikeOut``, ``Squiggly``). Returns the number created;
        existing appearances are preserved unless *force* is given.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        return self._engine_pdf.generate_appearances(force=force)

    def generate_field_appearances(self) -> int:
        """Regenerate AcroForm field appearance streams from their values.

        Builds the variable-text appearance (``/AP /N``) of each text and choice
        field from its value and default appearance (``/DA``) so the value renders
        without relying on ``/NeedAppearances``, and points each check box / radio
        widget's ``/AS`` at the state matching its value. Returns the number of
        widgets updated. Call after setting field values; :meth:`flatten` does this
        automatically.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        return self._engine_pdf.generate_field_appearances()

    def free_memory(self) -> "Document":
        """Free memory by clearing caches."""
        self._ensure_not_disposed()
        if self._engine_pdf:
            self._engine_pdf.free_memory()
        return self
