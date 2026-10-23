"""Document class for PDF manipulation.

This module provides the main Document class that wraps the native PDF engine.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Generator, Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    BinaryIO,
)

from aspose_pdf._compat_surface import (
    describe as _describe_unsupported,
)
from aspose_pdf._compat_surface import (
    reject_load_options as _reject_load_options,
)
from aspose_pdf._compat_surface import (
    require_pdf_save_format as _require_pdf_save_format,
)
from aspose_pdf.actions import (
    DOCUMENT_TRIGGERS,
    ActionCollection,
    JavaScriptCollection,
)
from aspose_pdf.attachments import AF_RELATIONSHIPS, FileSpecification
from aspose_pdf.color import ColorValue
from aspose_pdf.destinations import NamedDestinationCollection
from aspose_pdf.document_fonts import DocumentFont
from aspose_pdf.engine import viewer_preferences as engine_viewer_prefs
from aspose_pdf.engine.file_output import write_file_atomically
from aspose_pdf.engine.simple_pdf import (
    SimplePdf,
    _effective_encryption_password,
    _parse_pdf_date,
)
from aspose_pdf.engine.stream_output import write_all
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfSecurityException,
    PdfValidationException,
)
from aspose_pdf.font_substitution import FontSubstitutionOptions
from aspose_pdf.layers import LayerCollection
from aspose_pdf.load_limits import (
    PdfLoadLimits,
    _coerce_limits,
    _LoadBudget,
    _read_limited,
)
from aspose_pdf.outlines import OutlineCollection, OutlineItem
from aspose_pdf.pdfa import PdfAValidationResult
from aspose_pdf.pdfua import PdfUaValidationResult
from aspose_pdf.pdfx import PdfXValidationResult, normalize_pdfx_standard
from aspose_pdf.permissions import Permissions
from aspose_pdf.recipients import ALL_PERMISSIONS, Recipient
from aspose_pdf.sanitization import SanitizationReport
from aspose_pdf.signature_appearance import SignatureAppearance

if TYPE_CHECKING:
    import datetime as _datetime

    from aspose_pdf._pending_signatures import PendingOperation
    from aspose_pdf.auto_links import AutoLinkResult
    from aspose_pdf.engine.rasterizer import RasterizedPage
    from aspose_pdf.font_registry import FontDescriptor
    from aspose_pdf.forms import Form
    from aspose_pdf.image_resolution import ImageResolutionIssue
    from aspose_pdf.interactive import Action, Destination
    from aspose_pdf.link_extraction import ExtractedLink
    from aspose_pdf.link_validation import LinkValidationIssue
    from aspose_pdf.optimization import OptimizationOptions
    from aspose_pdf.page_labels import PageLabelCollection
    from aspose_pdf.pages import Page, PageCollection
    from aspose_pdf.signature import PdfSignature
    from aspose_pdf.stamps import Stamp
    from aspose_pdf.table_extraction import ExtractedTable
    from aspose_pdf.tagged import TaggedContent
    from aspose_pdf.text import TextHighlightResult, TextWord
    from aspose_pdf.text_comparison import PageTextComparison
    from aspose_pdf.text_layout import TextLayoutOptions
    from aspose_pdf.validation import CertificationLevel
    from aspose_pdf.viewer_preferences import (
        PageLayout,
        PageMode,
        ViewerPreferences,
    )
    from aspose_pdf.visual_comparison import PageVisualComparison
    from aspose_pdf.xmp import XmpPacket

logger = logging.getLogger(__name__)


def _is_svg_save_format(value: Any) -> bool:
    """True when *value* asks for SVG output rather than PDF."""
    if value is None:
        return False
    name = getattr(value, "name", None) or getattr(value, "value", None)
    return isinstance(name, str) and name.upper() == "SVG"


def _text_export_format(value: Any) -> str | None:
    """``"html"``, ``"markdown"`` or ``None`` for a structure export request.

    Both the ``DocFormat`` member and the matching save-options object select
    the export, so ported code that builds an options object reaches the same
    place as code that names the format.
    """
    if value is None:
        return None
    name = getattr(value, "name", None) or getattr(value, "value", None)
    if isinstance(name, str):
        upper = name.upper()
        if upper == "HTML":
            return "html"
        if upper in ("MARKDOWN", "MD"):
            return "markdown"
    type_name = type(value).__name__
    if type_name == "HtmlSaveOptions":
        return "html"
    if type_name == "MarkdownSaveOptions":
        return "markdown"
    return None


def _coerce_credential(
    certificate: Any,
    private_key: Any,
    key_encryption_key: bytes | bytearray | None = None,
) -> tuple[Any, Any] | bytes | None:
    """Validate recipient credentials for an ``/Adobe.PubSec`` document."""
    if key_encryption_key is not None:
        if certificate is not None or private_key is not None:
            raise PdfValidationException(
                "key_encryption_key cannot be combined with certificate or "
                "private_key"
            )
        if not isinstance(key_encryption_key, (bytes, bytearray)):
            raise PdfValidationException(
                "key_encryption_key must be bytes containing an AES key"
            )
        value = bytes(key_encryption_key)
        if len(value) not in (16, 24, 32):
            raise PdfValidationException(
                "key_encryption_key must contain a 16, 24, or 32-byte AES key"
            )
        return value
    if certificate is None and private_key is None:
        return None
    if certificate is None or private_key is None:
        raise PdfValidationException(
            "certificate and private_key must be supplied together to open a "
            "public-key encrypted document"
        )
    return certificate, private_key


def _coerce_date(value: Any) -> _datetime.datetime | None:
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

    def __init__(
        self,
        source: str | Path | bytes | bytearray | BinaryIO | None = None,
        options: Any = None,
        *,
        password: str | None = None,
        certificate: Any = None,
        private_key: Any = None,
        key_encryption_key: bytes | bytearray | None = None,
        limits: PdfLoadLimits | None = None,
    ) -> None:
        """Create an empty document, or load *source* when one is supplied.

        Parameters
        ----------
        source : str, Path, bytes, bytearray, or BinaryIO, optional
            PDF to load, with the same semantics (and the same errors) as
            :meth:`load_from`. When omitted, a new empty document is created.
        options : Any
            Present only for API compatibility: no load options are
            implemented, so any non-``None`` value raises. Format-specific
            containers such as ``SvgLoadOptions`` raise
            :exc:`~aspose_pdf.exceptions.UnsupportedFeatureException`.
        password : str, optional
            Password for a standard encrypted *source*, or for a CMS password
            recipient in an ``/Adobe.PubSec`` source.
        certificate, private_key : optional
            Recipient credentials for a *source* encrypted with the public-key
            handler (``/Adobe.PubSec``). Both are ``cryptography`` objects and
            both are required together. See :class:`~aspose_pdf.Recipient`.
        key_encryption_key : bytes, optional
            Pre-shared AES key for a CMS KEK recipient in an
            ``/Adobe.PubSec`` source. It cannot be combined with certificate
            credentials.
        limits : PdfLoadLimits, optional
            Resource policy for this document. Defaults to the standard policy.

        Raises
        ------
        FileNotFoundError
            If *source* is a path that does not exist.
        UnsupportedFeatureException
            If *source* or *options* is a compatibility placeholder for a
            format this package does not implement.
        """
        self._load_limits = _coerce_limits(limits)
        self._engine_pdf: SimplePdf = SimplePdf()  # Start with empty PDF
        self._engine_pdf._load_limits = self._load_limits
        self._engine_pdf._load_budget = _LoadBudget(self._load_limits)
        self._disposed: bool = False
        self._pages: Any | None = None
        self._form: Any | None = None
        self._outlines: OutlineCollection | None = None
        self._destinations: NamedDestinationCollection | None = None
        self._javascript: JavaScriptCollection | None = None
        self._tagged_content: Any | None = None
        self._password: str | None = None
        self._encrypted: bool = False
        # Signatures, LTV material and document timestamps for the next save.
        self._pending_signing: list[PendingOperation] = []
        self.file_name: str | None = None

        if options is not None:
            _reject_load_options(options)
        if source is None:
            if password is not None:
                raise TypeError(
                    "password requires a load source; open an existing PDF with "
                    "Document(path, password=...) or Document().load_from(...)"
                )
            if certificate is not None or private_key is not None:
                raise TypeError(
                    "certificate/private_key require a load source; open an "
                    "existing PDF with Document(path, certificate=..., "
                    "private_key=...)"
                )
            if key_encryption_key is not None:
                raise TypeError(
                    "key_encryption_key requires a load source; open an "
                    "existing PDF with Document(path, key_encryption_key=...)"
                )
            return
        self.load_from(
            source,
            password=password,
            certificate=certificate,
            private_key=private_key,
            key_encryption_key=key_encryption_key,
        )

    @property
    def load_limits(self) -> PdfLoadLimits:
        """Return limits used for loading, lazy processing, and authored assets."""
        return self._load_limits

    def _ensure_not_disposed(self) -> None:
        """Raise if the document has been disposed."""
        if self._disposed:
            raise AsposePdfException("Document has been disposed")

    def __enter__(self) -> Document:
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
    def page_labels(self) -> PageLabelCollection:
        """The page label ranges, keyed by the index of the page each starts at.

        See :class:`~aspose_pdf.page_labels.PageLabelCollection`; a page's
        own label is :attr:`Page.label <aspose_pdf.pages.Page.label>`.
        """
        self._ensure_not_disposed()
        from aspose_pdf.page_labels import PageLabelCollection

        return PageLabelCollection(self)

    @property
    def page_mode(self) -> PageMode:
        """What a viewer shows beside the page when the document opens.

        ``/PageMode`` (ISO 32000-1 table 28): the bookmarks, the thumbnails,
        the layers or the attachments panel, full screen, or nothing.
        A document that says nothing, or something the specification does not
        allow, opens with :attr:`PageMode.USE_NONE`.
        """
        self._ensure_not_disposed()
        from aspose_pdf.viewer_preferences import PageMode

        return PageMode(
            engine_viewer_prefs.catalog_name(
                self._engine_pdf, "PageMode", engine_viewer_prefs.PAGE_MODES, "UseNone"
            )
        )

    @page_mode.setter
    def page_mode(self, value: PageMode | str) -> None:
        self._ensure_not_disposed()
        from aspose_pdf.viewer_preferences import PageMode, _member

        self._engine_pdf._ensure_cos()
        engine_viewer_prefs.set_catalog_name(
            self._engine_pdf, "PageMode", _member(PageMode, value, "page_mode")
        )

    @property
    def page_layout(self) -> PageLayout:
        """How the pages are arranged (``/PageLayout``, table 28).

        One page at a time, one continuous column, or two of either, with the
        odd-numbered pages on whichever side. Defaults to
        :attr:`PageLayout.SINGLE_PAGE`.
        """
        self._ensure_not_disposed()
        from aspose_pdf.viewer_preferences import PageLayout

        return PageLayout(
            engine_viewer_prefs.catalog_name(
                self._engine_pdf,
                "PageLayout",
                engine_viewer_prefs.PAGE_LAYOUTS,
                "SinglePage",
            )
        )

    @page_layout.setter
    def page_layout(self, value: PageLayout | str) -> None:
        self._ensure_not_disposed()
        from aspose_pdf.viewer_preferences import PageLayout, _member

        self._engine_pdf._ensure_cos()
        engine_viewer_prefs.set_catalog_name(
            self._engine_pdf, "PageLayout", _member(PageLayout, value, "page_layout")
        )

    @property
    def open_action(self) -> Action | Destination | None:
        """Where the document opens, or what it does when opened.

        ``/OpenAction`` (table 28) is either a destination -- a page and how to
        fit it -- or an action, so this gives back a :class:`Destination` or an
        :class:`Action`, and takes either. ``None`` removes it, and reading
        gives ``None`` for a destination whose page the document no longer
        has: a viewer ignores such an entry rather than opening somewhere else.
        """
        self._ensure_not_disposed()
        return engine_viewer_prefs.open_action(self._engine_pdf)

    @open_action.setter
    def open_action(self, value: Action | Destination | None) -> None:
        self._ensure_not_disposed()
        self._engine_pdf._ensure_cos()
        engine_viewer_prefs.set_open_action(self._engine_pdf, value)

    def add_stamp(
        self, stamp: Stamp, pages: Sequence[int] | slice | None = None
    ) -> Document:
        """Put *stamp* on *pages*, or on every page when none are named.

        See :mod:`aspose_pdf.stamps`. A stamp whose drawing is the same on
        every page is written once and invoked from each of them, so stamping a
        long document does not repeat it; a
        :class:`~aspose_pdf.stamps.PageNumberStamp` says something different on
        each page and is written per page.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        count = len(self._engine_pdf.pages)
        if pages is None:
            indices: Sequence[int] = range(count)
        elif isinstance(pages, slice):
            indices = range(count)[pages]
        else:
            indices = [int(index) for index in pages]
        from aspose_pdf.engine import stamps as engine_stamps

        engine_stamps.apply(self._engine_pdf, stamp, indices)
        return self

    @property
    def viewer_preferences(self) -> ViewerPreferences:
        """The window, the reading direction and the print dialogue.

        See :class:`~aspose_pdf.viewer_preferences.ViewerPreferences`: the
        ``/ViewerPreferences`` entries (table 150), read and written in place.
        """
        self._ensure_not_disposed()
        from aspose_pdf.viewer_preferences import ViewerPreferences

        return ViewerPreferences(self)

    @property
    def form(self) -> Form:
        """Get the interactive form of the document."""
        self._ensure_not_disposed()
        if self._form is None:
            from aspose_pdf.forms import Form

            self._form = Form(self)
        return self._form

    @property
    def tagged_content(self) -> TaggedContent:
        """Get an editable view of the document's tagged structure tree."""
        self._ensure_not_disposed()
        if self._tagged_content is None:
            from aspose_pdf.tagged import TaggedContent

            self._tagged_content = TaggedContent(self)
        return self._tagged_content

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
        relationship: str | None = None,
        compress: bool = True,
    ) -> Document:
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
        * *relationship* — the associated-file relationship written as
          ``/AFRelationship`` (one of :data:`aspose_pdf.attachments.AF_RELATIONSHIPS`:
          ``"Source"``, ``"Data"``, ``"Alternative"``, ``"Supplement"``,
          ``"EncryptedPayload"``, ``"FormData"``, ``"Schema"``, or
          ``"Unspecified"``). Defaults to ``"Unspecified"``.
        * *compress* — Flate-compress the payload (default), unless that would
          make it larger.

        Re-adding an existing *name* replaces its contents and metadata. Returns
        *self* for chaining.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        if relationship is not None and relationship not in AF_RELATIONSHIPS:
            raise ValueError(
                f"relationship must be one of {sorted(AF_RELATIONSHIPS)}, "
                f"got {relationship!r}"
            )
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
        if relationship is not None:
            meta["relationship"] = relationship
        self._engine_pdf.attachment_meta[name] = meta
        # A previously loaded document may carry parsed metadata for this name;
        # a fresh add fully supersedes it.
        self._engine_pdf.attachment_read_meta.pop(name, None)
        return self

    def find_low_resolution_images(
        self,
        min_dpi: float = 150,
        *,
        pages: Iterable[int] | None = None,
    ) -> list[ImageResolutionIssue]:
        """Find image placements whose effective DPI is below ``min_dpi``.

        The finite, positive threshold applies to either image axis; equality
        passes. ``pages`` contains zero-based indices, retaining order and
        repeats; None scans all pages. Each image paint produces its own
        immutable ``ImageResolutionIssue``, in page/content order, even when
        the same image or form is reused at different sizes.

        Image XObjects, inline images and invoked nested forms are inspected.
        DPI uses declared pixel dimensions, composed transforms and page
        UserUnit, including rotation, reflection and shear. Singular transforms
        have no painted area and are skipped. Findings locate full images in
        unrotated page user coordinates. Clipping, crop boxes, opacity, optional
        content visibility and overlap are not evaluated; hidden or off-page
        paints may be reported. Unused resources, annotation appearances,
        patterns, Type 3 glyph content and separate mask images are not scanned.

        This is a resolution check, not a sharpness or print certification
        check: already blurry or upscaled images can have sufficient DPI.
        No image pixels are decoded and no optional imaging dependency is
        required. Only selected page content and invoked forms are read, under
        resource limits, without modifying the source or extraction cursors.
        Malformed geometry, missing invoked resources and cyclic form calls
        raise exceptions rather than silently omitting findings.
        """
        from aspose_pdf.engine.image_resolution import find_low_resolution_images

        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        return find_low_resolution_images(self, min_dpi, pages)

    def export_images_zip(
        self,
        destination: str | Path | BinaryIO | None = None,
        *,
        pages: Iterable[int] | None = None,
        compression: str = "deflate",
        compresslevel: int = 6,
        include_manifest: bool = False,
        overwrite: bool = True,
    ) -> bytes:
        """Return selected page image resources as ZIP bytes, optionally saving them.

        ``pages`` uses zero-based indices; ``None`` selects all pages. Selected
        order is retained, but repeated pages and shared image objects export
        once. Distinct image objects with identical pixels remain distinct.
        Page resources and nested Form resources are scanned without reading
        page/Form content. Unused, hidden and cropped-out resource images can
        be included; this is an asset inventory, not a visible-placement list.
        Inline images, annotation appearances, separate soft-mask streams and
        unattached legacy image mappings are not separately exported.

        Members have generated flat names ``image-0001.png``, etc., in selected
        page/resource-path order. JPEG and JPEG 2000 retain encoded payloads
        after decoding any outer filters; other supported samples become PNG.
        Original masks, placement transforms, clipping and PDF Decode effects
        on encoded JPEG/JPX files are not composited into those files.
        Rendering/OCR is not performed. Invalid image data or unsupported
        reconstruction propagates an exception instead of skipping an image.

        ``include_manifest=True`` appends a versioned ``manifest.json`` mapping
        files to zero-based page indices and resource-name paths, with declared
        dimensions, sample depth, source filter, soft-mask presence and sizes.
        ``compression`` is ``"deflate"`` or ``"stored"``; ``compresslevel`` is
        an integer 0-9. ZIP metadata is fixed for deterministic output in the
        same runtime. Empty selections produce valid empty archives.

        Selection, graph traversal, image decoding, encoded files, manifest and
        archive growth obey resource limits. Images are reconstructed one at a
        time without populating the source image cache or extraction cursor.
        Streaming page content remains lazy. The archive is held in memory and
        fully built before writing a destination. File replacement is atomic;
        ``overwrite=False`` refuses existing files. Binary streams receive bytes
        at their current position, retry short writes and remain open; a stream
        failure can leave partial output. The source document is not edited.
        """
        from aspose_pdf._export_files import write_files
        from aspose_pdf.engine.image_zip import export_zip

        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        if not isinstance(overwrite, bool):
            raise PdfValidationException("overwrite must be a boolean")
        data = export_zip(
            self,
            pages=pages,
            compression=compression,
            compresslevel=compresslevel,
            include_manifest=include_manifest,
        )
        if destination is not None:
            if hasattr(destination, "write"):
                write_all(destination, data)
            else:
                write_files([(Path(destination), data)], overwrite)
        return data

    def export_attachments_zip(
        self,
        destination: str | Path | BinaryIO | None = None,
        *,
        names: str | Iterable[str] | None = None,
        compression: str = "deflate",
        compresslevel: int = 6,
        include_manifest: bool = False,
        overwrite: bool = True,
    ) -> bytes:
        """Return embedded-file payloads as ZIP bytes, optionally writing them.

        ``names=None`` selects all document-level attachments sorted by their
        original names. A string selects one exact PDF attachment name; an
        iterable preserves selection order and includes repeated names once.
        Unknown names raise ``KeyError``. An empty selection produces a valid
        empty ZIP. File-attachment annotations are not included.

        ZIP members use flat basenames, with both slash styles removed. Unsafe
        characters and controls are replaced, leading/trailing dots and spaces
        removed, Windows device names prefixed, and UTF-8 names shortened to
        240 bytes. Empty names become ``attachment``. Case-insensitive Unicode
        collisions receive ``_2``, ``_3``, etc. before the extension. Payloads
        are unchanged; no attachment is extracted to the filesystem.

        ``compression`` is ``"deflate"`` (default) or ``"stored"``;
        ``compresslevel`` is an integer from 0 to 9, ignored for stored output.
        Archive timestamps and permissions are fixed for deterministic output
        in the same runtime. ``include_manifest=True`` appends ``manifest.json``
        with schema version 1 and original/archive names, sizes, MIME types,
        descriptions, dates as ISO 8601 strings or null, and relationships.
        The manifest name is reserved, so an attachment of that name is renamed.

        Selections, entries, text, per-file and cumulative payload sizes obey
        ``load_limits``. Manifest size is bounded by ``max_object_bytes``;
        archive growth by ``max_codec_work_bytes``. ZIP bytes are built in
        memory before any destination is written. Files are replaced atomically
        with parent directories created; ``overwrite=False`` refuses existing
        files. Binary streams are written at their current position, retry
        short writes and remain open. A stream failure can leave partial bytes.
        Neither the PDF nor its attachment mapping is changed; page content is
        not decoded, including on streaming documents. PDF attachment payloads
        are already loaded by the document's attachment reader.
        """
        from aspose_pdf._export_files import write_files
        from aspose_pdf.engine.attachment_zip import export_zip

        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        if not isinstance(overwrite, bool):
            raise PdfValidationException("overwrite must be a boolean")
        data = export_zip(
            self,
            names=names,
            compression=compression,
            compresslevel=compresslevel,
            include_manifest=include_manifest,
        )
        if destination is not None:
            if hasattr(destination, "write"):
                write_all(destination, data)
            else:
                write_files([(Path(destination), data)], overwrite)
        return data

    def import_attachments_zip(
        self,
        source: str | Path | bytes | bytearray | BinaryIO,
        *,
        names: str | Iterable[str] | None = None,
        on_conflict: str = "error",
        use_manifest: bool = True,
        compress: bool = True,
    ) -> int:
        """Import selected ZIP members as document-level embedded files.

        Accepts a path, ZIP bytes or a binary stream read from its current
        position and left open. Returns the number of imported members,
        including replacements. ``names=None`` imports ordinary files in ZIP
        order; a string selects one exact archive name and an iterable selects
        names in its order, deduplicating repeats. Unknown names raise
        ``KeyError``. Directories are ignored; nothing is extracted to disk.

        By default, a root ``manifest.json`` must use the version-one format
        produced by :meth:`export_attachments_zip`. It is consumed as metadata,
        restoring original PDF names, MIME types, descriptions, dates and
        associated-file relationships. Set ``use_manifest=False`` to import
        it as an ordinary attachment. Without a manifest, names use the same
        portable flat-basename rules as ZIP export; ZIP timestamps and file
        permissions are not imported.

        ``on_conflict`` handles exact PDF name collisions with existing files
        or earlier selected members: ``"error"`` (default), ``"skip"``,
        ``"replace"`` or ``"rename"`` (``_2``, ``_3``, ... before the extension).
        Replacements replace metadata as well as content. ``compress`` controls
        PDF embedded-stream compression on save, independently of ZIP storage.

        Stored and deflated single-disk archives with classic central
        directories are supported; ZIP64 central directories, encrypted files,
        symlinks, special files and duplicate archive names are rejected.
        Structural checks cover the whole archive. CRC and decompression checks
        cover the manifest and selected, non-skipped files. All checks and
        staging finish before any attachment changes; page content stays lazy.

        ``load_limits`` bounds input and working bytes, entry/selection/result
        counts, metadata text/JSON nesting, per-file and total decoded sizes and
        compression ratios. The archive and imported bytes are buffered in
        memory. Invalid archives/options raise ``PdfValidationException``;
        filesystem and input-stream errors propagate. Changes reach the PDF
        when it is saved normally.
        """
        from aspose_pdf.engine.attachment_zip_import import import_zip

        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        return import_zip(
            self,
            source,
            names=names,
            on_conflict=on_conflict,
            use_manifest=use_manifest,
            compress=compress,
        )

    def update_attachment(
        self,
        name: str,
        *,
        new_name: str | None = None,
        content: bytes | None = None,
        mime: str | None = None,
        description: str | None = None,
        creation_date=None,
        mod_date=None,
        relationship: str | None = None,
        compress: bool | None = None,
    ) -> FileSpecification:
        """Change one embedded file, keeping everything not named here.

        :meth:`add_attachment` *replaces* an attachment: re-adding a name to
        change its description drops the MIME type, the dates and the
        relationship it already had, because a fresh add supersedes whatever
        was read from the file. That leaves no way to edit one field, which is
        what this is for -- an argument left out is left alone::

            document.update_attachment("notes.txt", description="Reviewed")

        *new_name* renames the attachment; it must not collide with another,
        since a rename that quietly replaced a different file would be worse
        than an error. Returns the resulting :class:`FileSpecification`, and
        raises :class:`KeyError` when the document has no such attachment --
        a name that does not match is a typo, not a no-op.
        """
        self._ensure_not_disposed()
        eng = self._engine_pdf
        if eng is None:
            raise AsposePdfException("No document loaded")
        if name not in eng.attachments:
            raise KeyError(f"No attachment named {name!r}")
        if relationship is not None and relationship not in AF_RELATIONSHIPS:
            raise ValueError(
                f"relationship must be one of {sorted(AF_RELATIONSHIPS)}, "
                f"got {relationship!r}"
            )
        target = name if new_name is None else new_name
        if target != name and target in eng.attachments:
            raise ValueError(
                f"cannot rename {name!r} to {target!r}: the document already "
                "has an attachment with that name"
            )

        # What the file carried, under what a caller has already set, under
        # what this call changes -- the same order everything else reads them.
        meta = {
            **(eng.attachment_read_meta.get(name) or {}),
            **(eng.attachment_meta.get(name) or {}),
        }
        for key, value in (
            ("mime", mime),
            ("description", description),
            ("creation_date", creation_date),
            ("mod_date", mod_date),
            ("relationship", relationship),
            ("compress", compress),
        ):
            if value is not None:
                meta[key] = value

        payload = eng.attachments[name] if content is None else bytes(content)
        eng.attachments.pop(name, None)
        eng.attachment_meta.pop(name, None)
        eng.attachment_read_meta.pop(name, None)
        eng.attachments[target] = bytes(payload)
        # Merged in full, so the read-back copy is not consulted again: it is
        # keyed by the old name and would be wrong after a rename.
        eng.attachment_meta[target] = meta
        eng.attachment_read_meta.pop(target, None)
        return self.get_embedded_file(target)

    @property
    def embedded_files(self) -> list[FileSpecification]:
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
        specs: list[FileSpecification] = []
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
                    relationship=meta.get("relationship"),
                )
            )
        return specs

    def remove_attachment(self, name: str) -> bool:
        """Remove the embedded file named *name*.

        Returns ``True`` when an attachment was removed, ``False`` when the
        document had no attachment with that name. The change is written to the
        ``/Names /EmbeddedFiles`` name tree on the next save.
        """
        self._ensure_not_disposed()
        eng = self._engine_pdf
        if eng is None:
            return False
        existed = eng.attachments.pop(name, None) is not None
        eng.attachment_meta.pop(name, None)
        eng.attachment_read_meta.pop(name, None)
        return existed

    def get_embedded_file(self, name: str) -> FileSpecification | None:
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
    def info(self) -> dict[str, str]:
        """Get or set the document metadata (info dictionary).

        A ``dict`` of text: ``/Info`` holds text strings (ISO 32000-1 14.3.3),
        with dates written ``D:YYYYMMDDHHmmSSOHH'mm'`` (7.9.4) and
        ``Trapped`` written as a name — pass ``"True"``, ``"False"`` or
        ``"Unknown"`` for it and the name is what reaches the file. A value of
        another type is refused on :meth:`save` with a message naming the
        entry.

        A producer's own entries are shown as text where text is faithful (a
        number, a boolean) and left out where it is not (an array), and every
        one of them keeps its original type on save unless you change it. An
        entry you *do* change is written as the text you set, since the type it
        used to have cannot be read back out of a string.

        It is a dictionary in both directions: ``del``, ``pop``, ``clear`` and
        assigning a smaller dictionary all remove those entries from the file
        on :meth:`save`. An entry with no text form is the exception — you
        cannot delete what was never shown to you, so it stays, and a warning
        names it whenever something else was removed.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            return {}
        return self._engine_pdf.metadata

    @info.setter
    def info(self, value: dict[str, str]):
        self._ensure_not_disposed()
        if self._engine_pdf is not None:
            self._engine_pdf.metadata = dict(value)

    @property
    def xmp_metadata(self) -> XmpPacket:
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
    def xmp_metadata(self, value: XmpPacket) -> None:
        self._ensure_not_disposed()
        if self._engine_pdf is not None:
            self._engine_pdf.xmp_packet = value

    def sync_metadata(self, *, direction: str = "info_to_xmp") -> Document:
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

        Both date properties are typed, so a value that is not a date in the
        source format is **not copied**: it is left where it is and logged,
        rather than written into the other side as text it cannot hold. Dates
        that do convert keep their precision — ``D:2026`` becomes ``2026``, not
        ``2026-01-01``, since both formats truncate from the right.

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
    def signatures(self) -> list[PdfSignature]:
        """The digital signatures in the document, in the order the file holds them.

        Each is a :class:`~aspose_pdf.signature.PdfSignature` read from its
        signature field: ``valid`` checks it, ``validate()`` reports trust,
        certification and PAdES level. A document built here, or one that
        carries none, has an empty list. What the signatures do not cover is
        :class:`~aspose_pdf.forms.UnsignedContentAbsorber`'s to report, and
        whether anything after them looks like tampering is
        :class:`~aspose_pdf.security.SignaturesCompromiseDetector`'s.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            return []
        return list(getattr(self._engine_pdf, "signatures", None) or [])

    def sign(
        self,
        field: str | None = None,
        *,
        certificate: Any,
        private_key: Any,
        extra_certificates: Sequence[Any] = (),
        reason: str | None = None,
        location: str | None = None,
        contact: str | None = None,
        signer_name: str | None = None,
        pades: bool = False,
        timestamp_url: str | None = None,
        timestamp_authority: tuple[Any, Any] | None = None,
        timestamp_timeout: float = 10.0,
        certify: CertificationLevel | int | None = None,
        appearance: SignatureAppearance | None = None,
    ) -> Document:
        """Sign the document when it is next saved.

        A signature covers the bytes of a file, so it is made by :meth:`save`:
        the document is written as it would be anyway -- as an incremental
        update when it already carries signatures, which keeps them valid --
        and the signature goes into an appended revision of its own. Signing,
        :meth:`add_ltv` and :meth:`add_document_timestamp` happen in the order
        they were called. After the save the document is the file it wrote:
        :attr:`signatures` lists the new signature and a later save appends to
        it. A :attr:`form` taken before that save lists the fields as they
        were; take it again.

        Parameters
        ----------
        field : str, optional
            Fully qualified name of an unsigned signature field to fill, such as
            one made by :meth:`Form.add_signature_field`. When omitted, an
            invisible signature field (``Signature1``, or the next free number)
            is added to the first page.
        certificate, private_key :
            The signer: a ``cryptography`` ``x509.Certificate`` and its RSA, EC
            or Ed25519 private key; Ed25519 needs ``pades=True`` and digests
            with SHA-512 (RFC 8419). A PKCS#12 file loads with
            ``cryptography.hazmat.primitives.serialization.pkcs12.load_key_and_certificates``.
        extra_certificates : sequence of x509.Certificate
            Intermediate certificates to embed, so a validator can build the
            chain to a trusted root.
        reason, location, contact, signer_name : str, optional
            ``/Reason``, ``/Location``, ``/ContactInfo`` and ``/Name`` of the
            signature dictionary.
        pades : bool
            Write a CAdES signature (``ETSI.CAdES.detached``, PAdES baseline B)
            instead of ``adbe.pkcs7.detached``. A timestamp makes it PAdES-T.
        timestamp_url : str, optional
            RFC 3161 timestamp authority to embed a signature timestamp from.
        timestamp_authority : (x509.Certificate, RSA private key), optional
            A local timestamp authority, for closed environments and tests.
        timestamp_timeout : float
            Seconds to wait for *timestamp_url*.
        certify : CertificationLevel or int, optional
            Make this a certifying (DocMDP) signature permitting no changes (1),
            form filling (2), or form filling and annotations (3). It has to be
            the document's first signature.
        appearance : SignatureAppearance, optional
            Make the signature **visible**: draw who signed, when, why and
            where -- and an image, if one is given -- into the field's widget.
            Without it the signature shows whatever the field already showed,
            which for a field this library authored is an empty box and for a
            field it added itself is nothing at all.

            The appearance is drawn into the document *before* it is
            serialized, so the signature covers it: it cannot be replaced
            without breaking the signature. Its name line is *signer_name*, or
            the common name in *certificate* when that is not given, and its
            date is the moment of the save.

            Signing a field that exists draws into that field's widget, so the
            field says where the appearance goes. With no *field*, give the
            appearance a ``page`` and ``rect`` and the field is added there;
            an appearance with neither is refused, since the invisible field
            that would otherwise be added has no area to draw in.

        Returns
        -------
        Document
            Self for method chaining.

        Raises
        ------
        TypeError
            If the certificate, key or another argument has the wrong type.
        PdfValidationException
            If *field* does not name a signature field, the key does not belong
            to the certificate, or an argument is out of range.
        PdfSecurityException
            If *field* is signed already, a certification is asked for on a
            signed document, or the document is being encrypted for certificate
            recipients -- it could not be reopened after the save.
        """
        self._ensure_not_disposed()
        from cryptography import x509

        from aspose_pdf import _pending_signatures as pending

        pending.check_signer(certificate, private_key, pades=bool(pades))
        extra = tuple(extra_certificates)
        if not all(isinstance(item, x509.Certificate) for item in extra):
            raise TypeError("extra_certificates must hold cryptography x509.Certificate objects")
        for label, value in (
            ("reason", reason),
            ("location", location),
            ("contact", contact),
            ("signer_name", signer_name),
        ):
            if value is not None and not isinstance(value, str):
                raise TypeError(f"{label} must be a string")
        if appearance is not None and not isinstance(appearance, SignatureAppearance):
            raise TypeError("appearance must be a SignatureAppearance")
        if (
            appearance is not None
            and field is None
            and not appearance.places_its_own_field
        ):
            raise PdfValidationException(
                "A visible signature needs somewhere to be drawn: name a "
                "field with a rectangle, or give the appearance a page and "
                "rect to add one at. Without either, the field added here is "
                "invisible and nothing would be shown."
            )
        authority = pending.check_timestamp_source(timestamp_url, timestamp_authority)
        level = pending.certification_level(certify)
        if level is not None and (self.signatures or self._pending_signing):
            raise PdfSecurityException(
                "A certifying (DocMDP) signature must be the document's first "
                "signature, and this document is signed, or is to be, already"
            )
        self._check_reopenable()
        name = self._signature_field_to_fill(field, appearance)
        self._pending_signing.append(
            pending.PendingSignature(
                field=name,
                certificate=certificate,
                private_key=private_key,
                extra_certificates=extra,
                reason=reason,
                location=location,
                contact=contact,
                signer_name=signer_name,
                pades=bool(pades),
                timestamp_url=timestamp_url,
                timestamp_authority=authority,
                timestamp_timeout=float(timestamp_timeout),
                certify=level,
                appearance=appearance,
            )
        )
        return self

    def add_ltv(
        self,
        *,
        certificates: Sequence[Any] = (),
        crls: Sequence[Any] = (),
        ocsp_responses: Sequence[Any] = (),
    ) -> Document:
        """Add long-term validation material (a ``/DSS``) when the document is next saved.

        The certificates, CRLs and OCSP responses embedded in the document's
        signatures -- the ones already there and the ones :meth:`sign` makes
        before this call -- go into the document security store (ISO 32000-2
        12.8.4.3), with whatever is given here: revocation data fetched
        separately is the usual case. It turns a PAdES-T signature into
        PAdES-LT. A store the document already has is extended. The store is
        written as an incremental revision, so every signature stays valid.

        Parameters
        ----------
        certificates, crls, ocsp_responses : sequence
            DER bytes, or ``cryptography`` ``x509.Certificate``,
            ``x509.CertificateRevocationList`` and ``ocsp.OCSPResponse`` objects.

        Returns
        -------
        Document
            Self for method chaining.

        """
        self._ensure_not_disposed()
        from aspose_pdf import _pending_signatures as pending
        from aspose_pdf.engine.dss import DssMaterial

        material = DssMaterial(
            certs=pending.der_items(certificates, "certificates"),
            crls=pending.der_items(crls, "crls"),
            ocsps=pending.der_items(ocsp_responses, "ocsp_responses"),
        )
        self._pending_signing.append(pending.PendingLtv(material))
        return self

    def add_document_timestamp(
        self,
        *,
        timestamp_url: str | None = None,
        timestamp_authority: tuple[Any, Any] | None = None,
        timeout: float = 10.0,
    ) -> Document:
        """Add a document timestamp when the document is next saved.

        A document timestamp (``ETSI.RFC3161``, ISO 32000-2 12.8.5) is an RFC
        3161 token over the whole file as it stands, signatures and ``/DSS``
        included: with :meth:`add_ltv` before it, it makes PAdES-LTA, and a
        later one renews it. It is written as an incremental revision, in a
        field of its own (``Timestamp``, or the next free number).

        Parameters
        ----------
        timestamp_url : str, optional
            RFC 3161 timestamp authority to request the token from.
        timestamp_authority : (x509.Certificate, RSA private key), optional
            A local timestamp authority, for closed environments and tests.
            One of the two is required.
        timeout : float
            Seconds to wait for *timestamp_url*.

        Returns
        -------
        Document
            Self for method chaining.
        """
        self._ensure_not_disposed()
        from aspose_pdf import _pending_signatures as pending

        authority = pending.check_timestamp_source(timestamp_url, timestamp_authority)
        if timestamp_url is None and authority is None:
            raise PdfValidationException(
                "A document timestamp needs a timestamp_url or a timestamp_authority"
            )
        self._pending_signing.append(
            pending.PendingDocumentTimestamp(timestamp_url, authority, float(timeout))
        )
        return self

    def _signature_field_to_fill(
        self, field: str | None, appearance: SignatureAppearance | None = None
    ) -> str:
        """Name the field :meth:`sign` fills, adding one when asked.

        The field added is invisible -- a zero-size widget covering the whole
        document -- unless *appearance* says where to put it, in which case it
        is a visible field of that size on that page.
        """
        from aspose_pdf._pending_signatures import PendingSignature
        from aspose_pdf.engine.cos import PdfName
        from aspose_pdf.engine.sign_field import _find_field

        engine = self._engine_pdf
        engine._ensure_cos()
        if field is None:
            # A field queued for signing is in the form already: one is added
            # when it is asked for, not when it is signed.
            taken = {name for name, _entry in engine._iter_form_fields()}
            number = 1
            while f"Signature{number}" in taken:
                number += 1
            name = f"Signature{number}"
            if appearance is not None and appearance.places_its_own_field:
                if appearance.page >= len(self.pages):
                    raise PdfValidationException(
                        f"The appearance puts the signature on page "
                        f"{appearance.page + 1}, and the document has "
                        f"{len(self.pages)}."
                    )
                self.form.add_signature_field(
                    name, appearance.page, list(appearance.rect)
                )
                return name
            return engine._ensure_signature_field(name)
        if not isinstance(field, str):
            raise TypeError("field must be the name of a signature field")
        queued = {op.field for op in self._pending_signing if isinstance(op, PendingSignature)}
        if field in queued:
            raise PdfValidationException(f"Field '{field}' is to be signed on save already")
        # The same lookup, and the same two rules, as the save will apply: a
        # mistake is reported here, before anything is written.
        _ref, entry = _find_field(engine._cos_doc, _LoadBudget(self._load_limits), field)
        if engine._resolve(entry.mapping.get(PdfName("FT"))) != PdfName("Sig"):
            raise PdfValidationException(f"Field '{field}' is not a signature field")
        if entry.mapping.get(PdfName("V")) is not None:
            raise PdfSecurityException(f"Field '{field}' is already signed")
        return field

    def _check_reopenable(self) -> None:
        """Refuse to sign what could not be opened again once it is saved.

        After a signing save the document is reloaded from the bytes it wrote.
        A file encrypted for certificate recipients in this session opens only
        with a recipient's private key, which the document does not have.
        """
        engine = self._engine_pdf
        if engine.encrypted and engine._recipients:
            raise PdfSecurityException(
                "A document being encrypted for certificate recipients cannot be "
                "signed here: after the save it could only be reopened with a "
                "recipient's private key. Save it, open it with a recipient "
                "credential, then sign it."
            )

    def _flush_signature_appearances(self) -> None:
        """Draw every queued visible signature into its widget, before the save.

        The appearance goes into the document the save is about to serialize,
        not into the revision ``sign_field`` appends afterwards, so the bytes
        the signature covers include it: a visible signature cannot be lifted
        off one document and dropped on another, nor its name and date edited,
        without the signature failing. It is written here rather than at
        :meth:`sign` time so the date it shows is the moment of the save.
        """
        from aspose_pdf._pending_signatures import (
            PendingSignature,
            certificate_common_name,
        )

        for operation in self._pending_signing:
            if not isinstance(operation, PendingSignature):
                continue
            appearance = operation.appearance
            if appearance is None:
                continue
            self._engine_pdf.write_signature_appearance(
                operation.field,
                appearance,
                signer_name=operation.signer_name
                or certificate_common_name(operation.certificate),
                reason=operation.reason,
                location=operation.location,
                contact=operation.contact,
            )

    def _apply_pending_signing(self, data: bytes) -> bytes:
        """Make the queued signatures, LTV material and timestamps on *data*."""
        # The bytes may be encrypted; a signature revision has to be enciphered
        # the same way, with the same key and the same exemptions.
        encryption = self._engine_pdf._writer_encryption()
        for operation in self._pending_signing:
            data = operation.apply(data, encryption=encryption, limits=self._load_limits)
        return data

    def _reopen(self, data: bytes) -> None:
        """Make this document the file *data* is, as if it had been loaded from it."""
        engine = self._engine_pdf
        if engine.encrypted:
            # Protection set in this session: its owner password opens it fully.
            password, credential = engine._owner_password or engine.password, None
        else:
            password, credential = self._password, engine._credential
        certificate = private_key = key_encryption_key = None
        if isinstance(credential, tuple):
            certificate, private_key = credential
        elif isinstance(credential, bytes):
            key_encryption_key = credential
        file_name = self.file_name
        self.load_from(
            data,
            password=password,
            certificate=certificate,
            private_key=private_key,
            key_encryption_key=key_encryption_key,
        )
        self.file_name = file_name
        # Pages, outlines and the tagged view read the engine as they go or
        # hold plain data that is still true; the form lists the fields it read
        # from the old one, without a field sign() added.
        self._form = None

    @property
    def is_encrypted(self) -> bool:
        """Return True if document is encrypted."""
        return self._encrypted or (
            self._engine_pdf is not None and self._engine_pdf.encrypted
        )

    @property
    def id(self) -> list[bytes] | None:
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
    def layers(self) -> LayerCollection:
        """The document's optional content groups (layers).

        Each :class:`~aspose_pdf.layers.Layer` reports its name and whether the
        default configuration shows it; setting ``visible`` updates the
        document's ``/OCProperties /D`` configuration, so rendering, graphics
        absorption and a later :meth:`save` all follow it. A document without
        optional content returns an empty collection.

        Example
        -------
        ::

            for layer in document.layers:
                if layer.name == "Watermark":
                    layer.visible = False
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        from aspose_pdf.engine.optional_content import OptionalContent

        return LayerCollection(OptionalContent(self._engine_pdf))

    def flatten_layers(self) -> int:
        """Resolve optional content once and for all; return pages changed.

        Switching a layer off changes what is *drawn*; the content stays in the
        file and comes back the moment someone switches it on again. That is
        right for a viewer and wrong for handing the document to somebody -- a
        hidden draft watermark or an alternate-language layer is still in
        there. Flattening deletes what the current configuration hides
        (marked content, XObject invocations and annotations alike), drops
        every surviving ``/OC`` reference and removes ``/OCProperties``,
        leaving an ordinary PDF that shows exactly what was visible.

        Returns the number of pages whose content changed. A document without
        optional content is left alone and returns ``0``.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        from aspose_pdf.engine.optional_content import flatten

        return flatten(self._engine_pdf)

    @property
    def actions(self) -> ActionCollection:
        """What the document does around saving, printing and closing (``/AA``).

        ISO 32000-1 12.6.3, Table 197: ``will_close``, ``will_save``,
        ``did_save``, ``will_print`` and ``did_print``, on the catalog::

            document.actions["will_print"] = JavaScriptAction("...")

        :attr:`open_action` is the separate entry for what happens when the
        document is *opened* (12.6.2), and :attr:`javascript` holds the scripts a
        viewer runs before any of it.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        engine = self._engine_pdf
        return ActionCollection(
            DOCUMENT_TRIGGERS,
            engine.get_document_actions,
            engine.set_document_action,
            "document",
        )

    @property
    def javascript(self) -> JavaScriptCollection:
        """The document-level scripts, as a mapping of name to source.

        ISO 32000-1 12.6.4.17: the catalog's ``/Names /JavaScript`` name tree
        holds what a viewer runs when the document opens -- the place to define
        the functions a field's ``/AA`` scripts call -- and it runs them **in key
        order**, which is why such names are usually written to sort the way they
        are meant to run::

            document.javascript["00_helpers"] = "function total(a, b) ..."
            del document.javascript["00_helpers"]
        """
        self._ensure_not_disposed()
        if self._javascript is None:
            self._javascript = JavaScriptCollection(self)
        return self._javascript

    @property
    def destinations(self) -> NamedDestinationCollection:
        """The document's named destinations, as a mapping of name to destination.

        ISO 32000-1 12.3.2.3: a place in the document under a name, which a link,
        a bookmark or another file can point at instead of naming a page and a
        view. Both places a PDF keeps them are read, and writing one name leaves
        every other entry in the file exactly as it was::

            document.destinations["chapter-2"] = XYZDestination(page=7, top=760)
            page.add_link((72, 700, 200, 720), "chapter-2")

        See :class:`~aspose_pdf.destinations.NamedDestinationCollection`.
        """
        self._ensure_not_disposed()
        if self._destinations is None:
            self._destinations = NamedDestinationCollection(self)
        return self._destinations

    @property
    def outlines(self) -> OutlineCollection:
        """Bookmark tree for this document.

        Returns a live :class:`~aspose_pdf.outlines.OutlineCollection`.  Changes
        made to the collection are persisted when :meth:`save` is called.
        """
        self._ensure_not_disposed()
        if self._outlines is None:
            data = self._engine_pdf.outline_items() if self._engine_pdf else []
            self._outlines = OutlineCollection._from_list(data)
        return self._outlines

    def export_bookmarks_json(
        self,
        destination: str | Path | BinaryIO | None = None,
        *,
        resolve_named_destinations: bool = True,
        overwrite: bool = True,
    ) -> bytes:
        """Return version 1 UTF-8 JSON for the current bookmark tree.

        Preserve hierarchy/order, titles, bold/italic flags, RGB colour, open
        state and supported targets: all eight local views, named destinations,
        GoTo, URI, GoToR and Named actions. Local names resolve to explicit page
        views by default for transfer between documents. Set
        ``resolve_named_destinations=False`` to retain names and their PDF
        name/string representation; destination definitions are not archived.

        Read unsaved edits and current page identities without decoding page
        content. Unsupported/malformed targets, missing explicit pages and
        cycles are refused before output. Scripts, launches, custom action
        entries and Next chains are outside this exchange format.

        Return deterministic pretty-printed bytes with Unicode and a final
        newline. Optionally write a path atomically (creating parent folders),
        or write a binary stream from its current position and leave it open.
        ``overwrite=False`` refuses an existing file. Limits follow
        :attr:`load_limits`. This is not a complete PDF bookmark backup.
        """
        from aspose_pdf._export_files import write_files
        from aspose_pdf.engine.bookmark_json import export_json

        self._ensure_not_disposed()
        if not isinstance(overwrite, bool):
            raise PdfValidationException("overwrite must be a boolean")
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        data = export_json(self, resolve_named_destinations)
        if destination is not None:
            if hasattr(destination, "write"):
                write_all(destination, data)
            else:
                write_files([(Path(destination), data)], overwrite)
        return data

    def import_bookmarks_json(
        self,
        source: str | Path | bytes | bytearray | BinaryIO,
        *,
        replace: bool = False,
        page_map: Mapping[int, int] | None = None,
    ) -> int:
        """Import a complete JSON bookmark tree and return the added node count.

        Accept UTF-8 bytes, a path or a readable binary stream (left open).
        Append root entries by default; ``replace=True`` replaces the entire
        tree, including clearing it for an empty bookmarks array. Existing
        live collection handles remain usable. A nonempty import requires at
        least one document page. ``page_map`` maps every explicit local source
        page to an existing destination page; names and remote pages are not
        remapped. Indices are zero-based, coordinates are not transformed.

        All JSON, target, style, page-map and combined-tree checks finish
        before the live tree is changed. Duplicate keys, unsupported versions,
        non-finite numbers and unknown/missing fields are refused. Invalid
        input and limit failures leave bookmarks unchanged. Retained names
        resolve against destination definitions, which this import does not
        create. It neither executes actions nor accesses URLs or remote files.
        """
        from aspose_pdf.engine.bookmark_json import import_json

        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        return import_json(self, source, replace, page_map)

    @property
    def permissions(self) -> Permissions:
        """What a reader may do with this document: the ``/P`` word, with names.

        A :class:`~aspose_pdf.permissions.Permissions` -- an ``int`` subclass, so
        it still compares and masks exactly as the raw signed 32-bit value did,
        while ``permissions.can_copy`` and ``permissions.allowed`` say what it
        means (ISO 32000-1 Table 22).

        An **unencrypted** document has no ``/P`` at all, and nothing is being
        withheld, so this reports :meth:`Permissions.all` (``-4``) for one.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            return Permissions.all()
        return Permissions.of(self._engine_pdf.P)

    @classmethod
    def open_streaming(
        cls,
        path: str | Path,
        *,
        password: str | None = None,
        limits: PdfLoadLimits | None = None,
    ) -> Document:
        """Open a PDF in streaming/lazy mode for memory-efficient page processing.

        Unlike :meth:`load_from`, page content streams are **not** decoded
        upfront.  Each page's content is decoded on demand when accessed via
        :attr:`~aspose_pdf.pages.Page.content` or
        :meth:`iter_page_content_streams`.  This is ideal for large PDFs where
        only a subset of pages needs to be processed.

        Reading text and deleting pages work here as they do anywhere else,
        and both keep the document lazy: ``extract_text`` decodes one page at
        a time, and ``pages.delete`` shifts the page's identity without
        decoding the rest of the file.

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
        limits:
            Optional resource policy for untrusted input and later lazy work.

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
        doc = cls(limits=limits)
        doc._engine_pdf = SimplePdf.from_file_lazy(
            Path(path), password=password, limits=doc._load_limits
        )
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

    @property
    def fonts(self) -> list[DocumentFont]:
        """Every font this document draws with, one entry each.

        The other font APIs answer what is available to *draw* with --
        :class:`~aspose_pdf.font_repository.FontRepository` the machine's
        fonts, :attr:`font_substitution` the ones you have made available. This
        answers what the document itself uses::

            for font in document.fonts:
                print(font)
            # Arial (TrueType, embedded TrueType) on pages 1, 2
            # Helvetica (Type1, not embedded) on pages 2, 3

            missing = [f for f in document.fonts if not f.embedded]
            weight = sum(f.program_bytes or 0 for f in document.fonts)

        Each :class:`~aspose_pdf.document_fonts.DocumentFont` says what the
        font is called, what kind it is, how it is encoded, whether the
        program is in the file and what it costs, whether the name carries a
        subset tag, whether the text can be extracted (``has_to_unicode``),
        and which pages use it.

        The fonts found are the ones used **for rendering**, by the same walk
        the PDF/A, PDF/UA and PDF/X checks use -- so a font reached only
        through a form's default resources or an annotation's appearance is
        listed too, with no page but a *location* saying where it came from.
        Fonts that say the same thing are one entry: a document that writes
        its own ``/Helv`` dictionary on every page uses one font, not twenty.
        Two *subsets* of one family stay separate, since they carry different
        glyphs.

        Reading this does not change the document, and the list is a snapshot
        -- take it again after embedding fonts or adding pages.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            return []
        from aspose_pdf.engine.font_inventory import inventory

        self._engine_pdf._ensure_cos()
        return inventory(self._engine_pdf)

    @property
    def font_substitution(self) -> FontSubstitutionOptions | None:
        """Font sources the renderer may substitute non-embedded fonts from.

        ``None`` (the default) keeps rendering to the bundled substitute faces,
        which cover the Standard 14 plus Symbol and ZapfDingbats. Assign a
        :class:`~aspose_pdf.font_substitution.FontSubstitutionOptions` to let
        the renderer also draw with fonts from directories you name, programs
        you supply, or the machine's installed fonts::

            document.font_substitution = FontSubstitutionOptions.system()

        The setting applies to every render path on the document -- including
        :meth:`save_page_as_image` and :meth:`save_as_tiff` -- and a single
        options object keeps its font index across pages.
        """
        self._ensure_not_disposed()
        return getattr(self._engine_pdf, "_font_substitution", None)

    @font_substitution.setter
    def font_substitution(self, value: FontSubstitutionOptions | None) -> None:
        self._ensure_not_disposed()
        if value is not None and not isinstance(value, FontSubstitutionOptions):
            raise PdfValidationException(
                "font_substitution must be a FontSubstitutionOptions or None, "
                f"not {type(value).__name__}."
            )
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        self._engine_pdf._font_substitution = value

    def embed_fonts(
        self,
        *,
        sources: FontSubstitutionOptions | None = None,
        directory: str | Path | None = None,
    ) -> list[str]:
        """Embed a real font program for every font the document only names.

        A PDF may *refer* to a font without carrying it -- ``/BaseFont /Arial``
        and nothing else -- and then it draws differently wherever Arial is
        different or absent. PDF/A, PDF/UA and PDF/X all forbid that, and no
        amount of rewriting fixes it, because the program simply is not in the
        file. This supplies it: each missing font is matched to a real face, and
        the face is written into the document, which carries it from then on::

            report = document.embed_fonts()                      # bundled faces
            report = document.embed_fonts(directory="/fonts")    # and a folder
            report = document.embed_fonts(
                sources=FontSubstitutionOptions.system()         # and this machine
            )

        With no arguments it supplies the **Standard-14** families, and Symbol
        and ZapfDingbats, from the bundled metric-compatible faces -- those need
        no font source and are the same on every machine. For anything else,
        name where the fonts are: a *directory*, a
        :class:`~aspose_pdf.font_substitution.FontSubstitutionOptions` with
        programs you hand over or the machine's own fonts, or neither, in which
        case the document's :attr:`font_substitution` is used. Faces are matched
        on what they *are* -- the family and style in their own ``name`` table,
        refined by the font's descriptor -- not on what their files are called.

        The document's own metrics stay: ``/Widths`` and ``/W`` are not
        replaced, so an embedded face changes which glyphs are drawn and never
        where they sit. That is deliberate -- it is the contract rendering
        substitution has always had, and what a viewer does with a font it does
        not have. It also means a face that is **not** metrically compatible
        with the one the document named shows as bad spacing, up to glyphs
        overlapping each other, rather than as reflowed text. Supply a face
        metrically close to the original, and check the result, before relying
        on this for a document whose look matters.

        Returns
        -------
        List[str]
            One message per font it could **not** supply, saying why: no source
            answers to the name; the face's outlines are a kind that font
            cannot carry; the font is a ``CIDFontType0``, whose CIDs index its
            original program's own charset; or it is a composite font whose
            codes are not its CIDs -- anything but an Identity CMap -- so
            nothing says which character each CID stands for. An empty list
            means every font in the document now has a program. Call it before
            :meth:`convert_to_pdfa`, :meth:`convert_to_pdfua` or
            :meth:`convert_to_pdfx` to see what will be left over -- each of
            those does this pass itself.

        Raises
        ------
        PdfValidationException
            If *sources* is not a ``FontSubstitutionOptions``, or both *sources*
            and *directory* are given.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        if sources is not None and directory is not None:
            raise PdfValidationException(
                "Name the fonts' location once: either sources or directory, "
                "not both."
            )
        if sources is not None and not isinstance(sources, FontSubstitutionOptions):
            raise PdfValidationException(
                "sources must be a FontSubstitutionOptions or None, not "
                f"{type(sources).__name__}."
            )
        return self._engine_pdf.embed_missing_fonts(
            options=sources, lookup_directory=directory
        )

    def render_page(
        self,
        page_index: int,
        *,
        dpi: float = 72.0,
        scale: float = 1.0,
        background: tuple[int, int, int] = (255, 255, 255),
        antialias: bool | int = True,
        clip: Sequence[float] | None = None,
        shape_substitute_text: bool = True,
        draw_annotations: bool = True,
        font_substitution: FontSubstitutionOptions | None = None,
    ) -> RasterizedPage:
        """Render a page to an RGB raster image.

        ``page_index`` is zero-based. The result can be encoded with
        :meth:`RasterizedPage.to_png`, :meth:`RasterizedPage.to_tiff`,
        :meth:`RasterizedPage.to_jpeg`, :meth:`RasterizedPage.to_webp`, or saved
        directly with :meth:`RasterizedPage.save`. ``antialias`` smooths edges by
        supersampling (``True`` = 3x, an integer 1-8 sets the factor, ``False``
        disables it). ``shape_substitute_text`` (default on) joins complex-script
        runs drawn with a bundled substitute face; it needs the optional
        ``text-layout`` extra and only affects non-embedded fonts.
        ``draw_annotations`` (default on) composites each visible annotation's
        normal appearance over the page, the way a viewer shows it.
        ``font_substitution`` overrides :attr:`font_substitution` for this call.
        ``clip=(x0, y0, x1, y1)`` selects an area in unrotated PDF coordinates
        with a lower-left origin. Ordered corners and a positive intersection
        with the effective CropBox are required. Only that intersection is
        rasterized, with page rotation applied and page boxes preserved.
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
            clip=clip,
            shape_substitute_text=shape_substitute_text,
            draw_annotations=draw_annotations,
            font_substitution=font_substitution,
        )

    def save_page_as_image(
        self,
        page_index: int,
        destination: str | Path,
        *,
        dpi: float = 72.0,
        scale: float = 1.0,
        background: tuple[int, int, int] = (255, 255, 255),
        antialias: bool | int = True,
        clip: Sequence[float] | None = None,
        mode: str = "rgb",
        compression: str = "deflate",
        quality: int = 85,
        threshold: int = 128,
        lossless: bool = False,
        method: int = 4,
        dither: bool = False,
    ) -> Path:
        """Render one page and save it as PNG, TIFF, JPEG or WebP.

        The format follows the suffix of *destination*. ``mode`` selects
        ``"rgb"``, ``"gray"`` or ``"bilevel"`` output, ``compression`` applies
        to TIFF and ``quality`` to JPEG/WebP. ``lossless`` and ``method`` apply
        to WebP, which requires the optional ``images`` extra.
        ``dither`` applies Floyd-Steinberg dithering when ``mode`` is ``"bilevel"``.
        ``clip`` selects a region as described in :meth:`render_page`.
        """
        return self.render_page(
            page_index,
            dpi=dpi,
            scale=scale,
            background=background,
            antialias=antialias,
            clip=clip,
        ).save(
            destination,
            mode=mode,
            compression=compression,
            quality=quality,
            threshold=threshold,
            lossless=lossless,
            method=method,
            dither=dither,
        )

    def rasterized_copy(
        self,
        *,
        pages: Iterable[int] | None = None,
        dpi: float = 150.0,
        background: Sequence[int] = (255, 255, 255),
        antialias: bool | int = True,
        include_annotations: bool = True,
    ) -> Document:
        """Return an independent image-only PDF of the selected visible pages.

        ``pages`` uses zero-based indices, preserving order and repeats;
        ``None`` selects all pages. An empty selection returns a new empty
        document. All options, indices, page geometry and planned output limits
        are validated before any page renders or an output document is built.

        Pages render at finite positive ``dpi`` (default 150), on the RGB byte
        triple ``background``. ``antialias`` is a boolean (True = 3x) or an
        integer factor 1-8. Visible annotations and form appearances count
        unless ``include_annotations=False``. The source's font substitution
        and layer visibility apply, with the renderer's coverage/limitations.

        Each result page has one opaque 8-bit RGB image. Storage is lossless,
        using Flate when smaller and allowed by the compression-ratio policy.
        CropBox and rotation are baked into the image; the displayed physical
        size is retained, with a new zero-origin MediaBox and zero rotation.
        Pixel dimensions round up at the selected DPI; image placement uses
        the original displayed size rather than enlarging it to those pixels.

        Only rendered appearance is copied. Text/OCR layers, vector paths,
        annotations, fields, links, outlines, labels, tags, scripts, attachments
        and original metadata are not transferred as objects. New documents
        start without encryption or valid source signatures. Text is no longer
        searchable/selectable; images may be larger and less sharp when zoomed.
        This is not OCR or a guarantee that unsupported PDF graphics render.

        Source content/files are unchanged, and streaming sources read only
        selected pages without filling whole-document text/content caches.
        One completed source raster is held at a time; the result retains all
        image samples and compressed streams in memory. It inherits resource
        limits, including page/object counts, per-image/supersampled pixels,
        sample/content sizes and cumulative decoded output bytes. Compression
        buffers are bounded conservatively. Failures dispose partial results.
        The result survives source disposal; dispose it when done.
        """
        from aspose_pdf.engine.rasterized_copy import rasterized_copy

        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        return rasterized_copy(
            self,
            pages=pages,
            dpi=dpi,
            background=background,
            antialias=antialias,
            include_annotations=include_annotations,
        )

    def render_contact_sheet(
        self,
        *,
        pages: Iterable[int] | None = None,
        columns: int = 4,
        thumbnail_size: tuple[int, int] = (160, 220),
        margin: int = 12,
        spacing: int = 12,
        labels: bool = True,
        background: tuple[int, int, int] = (240, 240, 240),
        page_background: tuple[int, int, int] = (255, 255, 255),
        label_color: tuple[int, int, int] = (0, 0, 0),
        antialias: bool | int = True,
        include_annotations: bool = True,
    ) -> RasterizedPage:
        """Render a page overview as one RGB image without changing the PDF.

        ``pages`` selects zero-based indices; ``None`` selects all pages.
        Order and repeated indices are preserved. An empty selection raises
        ``PdfValidationException``. ``columns`` is the maximum number of cells
        per row; fewer selected pages produce fewer columns.

        ``thumbnail_size=(width, height)``, ``margin`` and ``spacing`` are
        integer pixels. Each visible CropBox, with page rotation applied, is
        fitted and centered inside its thumbnail rectangle without stretching.
        ``labels=True`` adds 22 pixels below each rectangle for its one-based
        source page number, drawn with built-in 14-pixel decimal digits. Custom
        PDF page labels are not used. A rectangle too narrow for its number is
        rejected; ``labels=False`` removes the label band.

        ``background``, ``page_background`` and ``label_color`` are RGB byte
        triples for the sheet, page paper and digits. ``antialias`` follows
        :meth:`render_page` (boolean or integer 1-8). Visible annotation
        appearances are included unless ``include_annotations=False``.
        The document's font substitution setting applies to thumbnails.

        Final sheet and supersampled thumbnail dimensions are checked against
        ``load_limits.max_raster_pixels`` before allocating the sheet. Pages
        are rendered one at a time at their fitted scale; unselected content
        is not read. Rendering coverage and limitations match ``render_page``.
        The returned ``RasterizedPage`` uses 72 DPI metadata; layout sizes are
        pixels and are independent of source page DPI.
        """
        from aspose_pdf.engine.contact_sheet import render_contact_sheet

        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        return render_contact_sheet(
            self,
            pages=pages,
            columns=columns,
            thumbnail_size=thumbnail_size,
            margin=margin,
            spacing=spacing,
            labels=labels,
            background=background,
            page_background=page_background,
            label_color=label_color,
            antialias=antialias,
            include_annotations=include_annotations,
        )

    def save_contact_sheet(
        self,
        destination: str | Path,
        *,
        pages: Iterable[int] | None = None,
        columns: int = 4,
        thumbnail_size: tuple[int, int] = (160, 220),
        margin: int = 12,
        spacing: int = 12,
        labels: bool = True,
        background: tuple[int, int, int] = (240, 240, 240),
        page_background: tuple[int, int, int] = (255, 255, 255),
        label_color: tuple[int, int, int] = (0, 0, 0),
        antialias: bool | int = True,
        include_annotations: bool = True,
        mode: str = "rgb",
        compression: str = "deflate",
        quality: int = 85,
        threshold: int = 128,
        lossless: bool = False,
        method: int = 4,
        dither: bool = False,
    ) -> Path:
        """Save a contact sheet as PNG, TIFF, JPEG or optional WebP.

        Layout options follow :meth:`render_contact_sheet`. Encoding options
        follow :meth:`RasterizedPage.save`; the destination suffix chooses the
        format, with ``.png`` added when absent. PNG, TIFF and JPEG need no
        optional dependencies; WebP requires the ``images`` extra. The encoded
        file replaces the destination atomically, after successful rendering
        and encoding. Return the resulting path.
        """
        return self.render_contact_sheet(
            pages=pages,
            columns=columns,
            thumbnail_size=thumbnail_size,
            margin=margin,
            spacing=spacing,
            labels=labels,
            background=background,
            page_background=page_background,
            label_color=label_color,
            antialias=antialias,
            include_annotations=include_annotations,
        ).save(
            destination,
            mode=mode,
            compression=compression,
            quality=quality,
            threshold=threshold,
            lossless=lossless,
            method=method,
            dither=dither,
        )

    def remove_annotations(
        self,
        *,
        pages: Iterable[int] | None = None,
        authors: str | Iterable[str] | None = None,
        before: _datetime.datetime | _datetime.date | str | None = None,
        after: _datetime.datetime | _datetime.date | str | None = None,
        date_field: str = "modified",
        subtypes: str | Iterable[str] | None = None,
        case_sensitive: bool = True,
        dry_run: bool = False,
    ) -> int:
        """Remove matching review annotations and return removed occurrence count.

        Combine filters with AND; ``authors`` accepts one exact author or an
        iterable of alternatives. None disables that filter; an empty author
        string matches an unset/empty author and an empty iterable matches none.
        ``case_sensitive=False`` uses Unicode casefolding, without trimming.
        ``pages`` uses zero-based indices; repeated pages are inspected once.

        ``before`` and ``after`` are exclusive date bounds, accepting datetime,
        date, ISO strings or PDF date strings. Dates mean midnight; naive values
        and PDF dates without zones use UTC. PDF dates may omit trailing fields,
        defaulting month/day to 1 and time to zero. ``date_field`` selects
        ``modified`` (/M) or ``created`` (/CreationDate), without fallback.
        Missing, invalid or non-string annotation dates do not match date
        filters. Authors that are not PDF text strings do not match authors.

        Default subtypes are Text, FreeText, Line, Square, Circle, Polygon,
        PolyLine, Highlight, Underline, Squiggly, StrikeOut, Stamp, Caret, Ink
        and Redact. Explicit subtypes can select other kinds, but Widget is
        refused: use the form API. Links and file attachments are preserved by
        default. Removing Redact markup does not apply redaction.

        Owned popups on selected pages are removed with their matched owner,
        even if the popup itself does not match. Count includes those popups.
        Surviving replies are preserved, detaching IRT/RT when their parent is
        no longer attached anywhere; incoming Popup links are repaired too.
        References to shared targets remain while those targets stay attached.
        Metadata on other pages is inspected for these repairs. A deletion
        that would orphan a popup outside the selection is refused atomically.

        ``dry_run=True`` performs the same validation/planning and returns the
        count without edits. All validation and relationship planning precede
        mutation, under resource limits. Content, appearances and embedded
        assets are not decoded; streaming inputs stay lazy. Annotation arrays
        are replaced rather than editing shared arrays in place. Other PDF
        data is preserved. Changes remain in memory until saved. This is not
        physical erasure, tag-tree repair or a secure sanitization operation;
        detached object bytes or unrelated references can remain in the PDF.
        Reacquire annotation views after removal because indices may shift.
        """
        from aspose_pdf.engine.annotation_cleanup import remove

        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        return remove(
            self,
            pages=pages,
            authors=authors,
            before=before,
            after=after,
            date_field=date_field,
            subtypes=subtypes,
            case_sensitive=case_sensitive,
            dry_run=dry_run,
        )

    def _annotation_records(
        self,
        pages: Iterable[int] | None,
        subtypes: Any,
    ) -> list[dict[str, Any]]:
        from aspose_pdf.engine.annotation_json import records

        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        return records(self._engine_pdf, pages, subtypes)

    def export_annotations_json(
        self,
        destination: str | Path | BinaryIO | None = None,
        *,
        pages: Iterable[int] | None = None,
        subtypes: str | Iterable[str] | None = None,
        overwrite: bool = True,
    ) -> bytes:
        """Return UTF-8 JSON annotation metadata, optionally writing it.

        ``pages`` selects zero-based page indices in the requested order;
        repeated indices are exported once. ``subtypes`` accepts a subtype
        string, ``AnnotationType`` or an iterable of either. ``None`` includes
        every annotation, including popups and form widgets; ``[]`` selects
        none. All selection arguments are validated before reading metadata.

        Schema version 1 contains an ``annotations`` array with source page
        and annotation indices, subtype, rectangle, text, author, selected PDF
        properties and reference IDs for replies/popups. Coordinates remain in
        unrotated PDF space and date strings retain their original format.
        IDs identify objects within this PDF snapshot, not across rewrites;
        links may name annotations outside the selected output.

        Supported review annotations can be appended to a PDF using
        :meth:`import_annotations_json`. This is not a lossless PDF backup.
        Appearance streams, actions, destinations, field values, embedded
        assets, custom properties and stream-valued rich text are omitted.
        Page content and appearance streams are not decoded.

        Files are written atomically, creating parent folders as needed;
        ``overwrite=False`` refuses an existing file. Binary streams are
        written from their current position, retrying short writes, and left
        open. The complete JSON is prepared before writing any output.
        """
        from aspose_pdf._export_files import write_files
        from aspose_pdf.engine.annotation_json import to_json

        self._ensure_not_disposed()
        if not isinstance(overwrite, bool):
            raise PdfValidationException("overwrite must be a boolean")
        data = to_json(self._annotation_records(pages, subtypes))
        if destination is not None:
            if hasattr(destination, "write"):
                write_all(destination, data)
            else:
                write_files([(Path(destination), data)], overwrite)
        return data

    def import_annotations_json(
        self,
        source: str | Path | bytes | bytearray | BinaryIO,
        *,
        page_map: Mapping[int, int] | None = None,
    ) -> int:
        """Append version 1 JSON review annotations and return the number added.

        Accept UTF-8 bytes, a path or a readable binary stream. Streams are
        read from their current position and left open. ``page_map`` maps
        zero-based source page indices to existing destination pages; every
        used source page must be mapped. Without it, page indices are retained.
        Input order is retained within each page; ``annotation_index`` is
        informational. Existing annotations are preserved and repeat imports
        append duplicates.

        Supported subtypes are Text, FreeText, Line, Square, Circle, Polygon,
        PolyLine, Highlight, Underline, Squiggly, StrikeOut, Stamp, Caret, Ink,
        Popup and Redact. Widgets, links, media and unknown subtypes are refused
        because the export omits data needed to recreate them. Supported review
        properties recover their PDF name/string types; nonstandard dictionary
        entries are refused. Appearances are not archived or generated; call
        ``page.annotations.generate_appearances()`` when needed.

        Replies and popups are linked to newly imported IDs only. Links to IDs
        outside the input are omitted; internal popup pairs are completed in
        both directions. Conflicting popup links, cross-page popups and reply
        cycles are refused. All JSON, property, page and relationship checks
        finish before any annotation is added. Malformed/unsupported data and
        resource-limit failures leave annotations unchanged. Limits follow
        :attr:`load_limits`. Redact imports markup; it does not redact content.
        """
        from aspose_pdf.engine.annotation_import import import_json

        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        return import_json(self._engine_pdf, source, page_map=page_map)

    def extract_links(
        self,
        *,
        pages: Iterable[int] | None = None,
    ) -> list[ExtractedLink]:
        """Inventory Link annotation targets without executing or fetching them.

        ``pages`` selects zero-based source pages; None means all pages.
        Repeated selections are inspected once in first-selected order. Results
        follow annotation order, direct Dest before A, then action Next preorder.
        Every target/action has a detached immutable ``ExtractedLink`` with its
        source page, annotation index, normalized rectangle and action path.
        Reused targets and shared actions retain each occurrence.

        URI actions expose raw and base-resolved URLs; GoTo targets resolve
        named destinations and current local page identities into typed views.
        GoToR retains remote file/page/name metadata; Launch and SubmitForm
        expose common file specifications (UF before F), and Named retains its
        action name. Other action types retain their type without executable
        payloads. Invalid link geometry/targets/actions appear with issue codes
        rather than being silently skipped. Malformed annotation containers
        raise exceptions because their links cannot be inventoried reliably.

        Only Link annotations and their primary action chains are inspected,
        including hidden annotations. Bookmarks, AA actions, plain-text URLs,
        appearance streams, QuadPoints, platform-specific launch dictionaries,
        embedded-file GoToE targets and network reachability are not inspected.
        Rectangles use unrotated page user coordinates before cropping, with
        no UserUnit conversion. No content/image stream decoding, text
        extraction, filesystem lookup or network access occurs. Reads and
        result growth obey load_limits; the document remains unchanged.
        """
        from aspose_pdf.engine.link_extraction import extract

        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        return extract(self, pages)

    def validate_links(
        self,
        *,
        pages: Iterable[int] | None = None,
        include_outlines: bool = True,
        include_named_destinations: bool = True,
    ) -> list[LinkValidationIssue]:
        """Report broken local navigation without changing the document.

        Inspect Link annotations on zero-based ``pages`` (all by default),
        then bookmarks in preorder and named destination definitions. Repeated
        page selections are checked once, retaining their first occurrence.
        Both flags must be booleans; selection/options are validated before
        metadata reads. ``pages=[]`` still checks the enabled global sources.

        Return detached immutable :class:`LinkValidationIssue` values for
        missing targets, undefined names, missing/stale page references,
        invalid destinations/actions, conflicting Link Dest/A entries, cycles and
        malformed navigation containers. Check all eight standard destination
        views with their exact arities; only XYZ and FitH/V/BH/BV parameters
        may be null. XYZ zoom must be non-negative and FitR bounds increasing.
        Name lookup follows each source name/string representation's priority.

        Read current page identities and unsaved bookmark edits, including
        action Next chains. URI, remote-file, JavaScript and other non-GoTo
        actions are not executed or checked for reachability; their Next chains
        are still inspected. No page content or appearance stream is decoded.
        Parser/resource failures propagate under :attr:`load_limits`.

        This checks navigation targets, not full PDF conformance, link geometry,
        accessibility or exhaustive bookmark/name-tree structural correctness.
        A targetless bookmark is valid. No repairs are made.
        """
        from aspose_pdf.engine.link_validation import validate

        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        return validate(self, pages, include_outlines, include_named_destinations)

    def extract_tables(
        self,
        *,
        pages: Iterable[int] | None = None,
        clip_to_page: bool = True,
    ) -> list[ExtractedTable]:
        """Extract regular text grids as detached :class:`ExtractedTable` values.

        ``pages`` contains zero-based indices, in the requested order; repeats
        are retained and an empty iterable selects nothing. All indices must
        be integers within the document. Only selected pages are decoded.
        Tables follow the layout analyzer's reading order within each page;
        tables on different pages remain separate, including repeated headers.

        ``clip_to_page`` leaves out text anchored outside the effective crop
        box by default. The HTML/Markdown export's geometric heuristics apply:
        no OCR, border analysis, tag-tree reconstruction or multiline/vertical
        merge recovery is performed. Whitespace is normalized but cell text
        is not truncated. Use ``table.to_list()``, ``table.to_csv()`` or
        ``table.save_csv(path)`` to consume each result.
        """
        from aspose_pdf.table_extraction import _page_tables

        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        if not isinstance(clip_to_page, bool):
            raise PdfValidationException("clip_to_page must be a boolean")
        count = self.page_count
        if pages is None:
            indexes = range(count)
        else:
            try:
                if isinstance(pages, (str, bytes)):
                    raise TypeError
                indexes = list(pages)
            except TypeError:
                raise PdfValidationException("pages must be an iterable of integers") from None
            for index in indexes:
                if isinstance(index, bool) or not isinstance(index, int):
                    raise PdfValidationException("Page indices must be integers")
                if not 0 <= index < count:
                    raise PdfValidationException(f"Page index out of range: {index}")
        return [
            table
            for index in indexes
            for table in _page_tables(self._engine_pdf, index, clip_to_page)
        ]

    def to_html(
        self,
        *,
        pages: Sequence[int] | None = None,
        title: str | None = None,
        embed_images: bool = True,
        paragraph_gap: float | None = None,
        clip_to_page: bool = True,
    ) -> str:
        """Return the document's inferred structure as one HTML document.

        Headings, paragraphs, lists, tables and figures are inferred with the
        same layout analysis :meth:`auto_tag` uses, and the text is decoded the
        way :meth:`extract_text` decodes it. HTML has no page model, so pages
        are separated by a rule and read as one flow. *title* defaults to the
        document's own ``/Title``.

        *paragraph_gap* is the largest baseline-to-baseline step, in multiples
        of the font size, at which a line still continues the paragraph above
        it (``None``: 1.6, the layout analysis's own). With *clip_to_page*
        (the default), text and images anchored outside the page's visible
        area -- its crop box -- are left out, as no viewer shows them.

        This is a conversion to a *flowing document*, not a facsimile: exact
        positioning, colour and fonts are dropped. For a facsimile, use
        :meth:`save_as_svg`.
        """
        from aspose_pdf.engine.text_export import to_html

        return to_html(
            self._export_blocks(pages, embed_images, paragraph_gap, clip_to_page),
            title=title if title is not None else self._document_title(),
            embed_images=embed_images,
        )

    def to_markdown(
        self,
        *,
        pages: Sequence[int] | None = None,
        title: str | None = None,
        embed_images: bool = True,
        markdown_format: str = "GFM",
        paragraph_gap: float | None = None,
        clip_to_page: bool = True,
    ) -> str:
        """Return the document's inferred structure as Markdown.

        *markdown_format* is ``"GFM"`` (GitHub Flavored Markdown, the default)
        or ``"CommonMark"``, which has no tables: a table is written as the
        HTML block CommonMark passes through. See :meth:`to_html` for what the
        conversion carries over and for the other arguments.
        """
        from aspose_pdf.engine.text_export import to_markdown

        dialect = self._markdown_format(markdown_format)
        return to_markdown(
            self._export_blocks(pages, embed_images, paragraph_gap, clip_to_page),
            title=title if title is not None else self._document_title(),
            embed_images=embed_images,
            dialect=dialect,
        )

    @staticmethod
    def _markdown_format(value: Any) -> str:
        from aspose_pdf.engine.text_export import markdown_format

        try:
            return markdown_format(value)
        except ValueError as exc:
            raise PdfValidationException(str(exc)) from exc

    def _document_title(self) -> str:
        try:
            return str(self.info.get("Title") or "")
        except Exception:
            return ""

    def _export_blocks(
        self,
        pages: Sequence[int] | None,
        embed_images: bool,
        paragraph_gap: float | None = None,
        clip_to_page: bool = True,
    ) -> list[list[Any]]:
        from aspose_pdf.engine.text_export import page_blocks

        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        if paragraph_gap is not None and (
            isinstance(paragraph_gap, bool) or not float(paragraph_gap) > 0
        ):
            raise PdfValidationException(
                "paragraph_gap must be a positive number of font sizes"
            )
        indexes = (
            list(range(self.page_count)) if pages is None else [int(i) for i in pages]
        )
        return [
            page_blocks(
                self._engine_pdf,
                index,
                include_images=embed_images,
                paragraph_gap=None if paragraph_gap is None else float(paragraph_gap),
                clip_to_page=clip_to_page,
            )
            for index in indexes
        ]

    def save_as_html(
        self,
        destination: str | Path,
        *,
        pages: Sequence[int] | None = None,
        title: str | None = None,
        embed_images: bool = True,
        split_into_pages: bool = False,
        resources_directory: str | Path | None = None,
        paragraph_gap: float | None = None,
        clip_to_page: bool = True,
        overwrite: bool = True,
    ) -> list[Path]:
        """Write the document as HTML and return the HTML files written.

        One file by default. ``split_into_pages`` writes ``name-1.html``,
        ``name-2.html`` … instead, one per page.

        Figures are embedded as ``data:`` URIs unless *resources_directory* is
        given: then each distinct image is written there once, as
        ``name-image-1.png`` and so on, and the HTML links to it by a relative
        URL. A relative *resources_directory* is taken from the folder the
        HTML goes to. *embed_images* ``False`` leaves figures out altogether
        (their alternate text stays). *paragraph_gap* and *clip_to_page* are as
        for :meth:`to_html`.

        With *overwrite* ``False`` an export any of whose files exists is
        refused before anything is written. Each file is written atomically.
        """
        from aspose_pdf import _export_files as files
        from aspose_pdf.engine.text_export import to_html

        blocks = self._export_blocks(pages, embed_images, paragraph_gap, clip_to_page)
        target = Path(destination)
        document_title = title if title is not None else self._document_title()
        images = (
            files.ExportImages(
                files.directory_beside(target, resources_directory),
                target.stem,
                target.parent,
            )
            if resources_directory is not None
            else None
        )

        def html(selection: list[list[Any]]) -> bytes:
            return to_html(
                selection,
                title=document_title,
                embed_images=embed_images,
                image_src=images,
            ).encode("utf-8")

        if split_into_pages:
            documents = [
                (target.with_name(f"{target.stem}-{offset}{target.suffix or '.html'}"), html([page]))
                for offset, page in enumerate(blocks, 1)
            ]
        else:
            documents = [(target, html(blocks))]
        files.write_files(
            documents + (images.outputs() if images is not None else []), overwrite
        )
        return [path for path, _ in documents]

    def save_as_markdown(
        self,
        destination: str | Path,
        *,
        pages: Sequence[int] | None = None,
        title: str | None = None,
        embed_images: bool = True,
        image_directory: str | Path | None = None,
        markdown_format: str = "GFM",
        paragraph_gap: float | None = None,
        clip_to_page: bool = True,
        overwrite: bool = True,
    ) -> Path:
        """Write the document as one Markdown file and return its path.

        Figures are embedded as ``data:`` URIs unless *image_directory* is
        given: then each distinct image is written there once and linked by a
        relative URL, as :meth:`save_as_html` does with its
        *resources_directory*. *markdown_format*, *paragraph_gap* and
        *clip_to_page* are as for :meth:`to_markdown`; *overwrite* is as for
        :meth:`save_as_html`.
        """
        from aspose_pdf import _export_files as files
        from aspose_pdf.engine.text_export import to_markdown

        dialect = self._markdown_format(markdown_format)
        blocks = self._export_blocks(pages, embed_images, paragraph_gap, clip_to_page)
        target = Path(destination)
        images = (
            files.ExportImages(
                files.directory_beside(target, image_directory),
                target.stem,
                target.parent,
            )
            if image_directory is not None
            else None
        )
        text = to_markdown(
            blocks,
            title=title if title is not None else self._document_title(),
            embed_images=embed_images,
            image_src=images,
            dialect=dialect,
        )
        files.write_files(
            [(target, text.encode("utf-8"))]
            + (images.outputs() if images is not None else []),
            overwrite,
        )
        return target

    def save_as_svg(
        self,
        destination: str | Path,
        *,
        pages: Sequence[int] | None = None,
        background: tuple[int, int, int] | None = (255, 255, 255),
        draw_annotations: bool = True,
        font_substitution: FontSubstitutionOptions | None = None,
        precision: int = 3,
        text: str = "outlines",
        overwrite: bool = True,
    ) -> list[Path]:
        """Write pages as SVG and return the files written.

        SVG has no notion of a multi-page document, so each page becomes its
        own file. One page writes exactly *destination*; several write
        ``name-1.svg``, ``name-2.svg`` … beside it, numbered by the page's
        position in the document rather than in *pages*, so the file names
        still say which page they came from.

        See :meth:`~aspose_pdf.pages.Page.to_svg` for what the conversion does
        and does not turn into vectors, and for *text*, which chooses between
        glyph outlines and selectable ``<text>``. *overwrite* is as for
        :meth:`save_as_html`.

        Returns
        -------
        list[Path]
            The files written, in the order the pages were selected.
        """
        from aspose_pdf import _export_files as files

        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        indexes = (
            list(range(self.page_count)) if pages is None else [int(i) for i in pages]
        )
        if not indexes:
            raise PdfValidationException("Saving SVG needs at least one page")
        target = Path(destination)
        outputs: list[tuple[Path, bytes]] = []
        for index in indexes:
            if len(indexes) == 1:
                path = target
            else:
                path = target.with_name(
                    f"{target.stem}-{index + 1}{target.suffix or '.svg'}"
                )
            svg = self.pages[index].to_svg(
                background=background,
                draw_annotations=draw_annotations,
                font_substitution=font_substitution,
                precision=precision,
                text=text,
            )
            outputs.append((path, svg.encode("utf-8")))
        files.write_files(outputs, overwrite)
        return [path for path, _ in outputs]

    def to_tiff(
        self,
        *,
        pages: Sequence[int] | None = None,
        dpi: float = 72.0,
        scale: float = 1.0,
        background: tuple[int, int, int] = (255, 255, 255),
        antialias: bool | int = True,
        mode: str = "rgb",
        compression: str = "deflate",
        threshold: int = 128,
        dither: bool = False,
    ) -> bytes:
        """Render pages into the bytes of one multi-page TIFF.

        Every page becomes one image in the file, in the order given by *pages*
        (all pages, in document order, when omitted). Pages are rendered one at a
        time and encoded as they go, so only the compressed result accumulates
        rather than every raster at once.

        ``mode`` selects ``"rgb"``, ``"gray"`` or ``"bilevel"`` output and
        ``compression`` is ``"deflate"`` (the default) or ``"none"``.
        ``dither`` applies Floyd-Steinberg dithering when ``mode`` is ``"bilevel"``.

        :meth:`save_as_tiff` writes these bytes to a file. Every other export has
        both forms -- :meth:`to_html`, :meth:`to_markdown`, :meth:`Page.to_svg`,
        :meth:`render_page` -- and this is TIFF's.
        """
        from aspose_pdf.engine.image_export import TiffPage, write_tiff

        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        indexes = list(range(self.page_count)) if pages is None else [int(i) for i in pages]
        if not indexes:
            raise PdfValidationException("A TIFF file needs at least one page")

        def rendered() -> Iterator[Any]:
            for index in indexes:
                raster = self.render_page(
                    index,
                    dpi=dpi,
                    scale=scale,
                    background=background,
                    antialias=antialias,
                )
                encoder_mode, samples = raster._samples(mode, threshold, dither)
                yield TiffPage(
                    width=raster.width,
                    height=raster.height,
                    mode=encoder_mode,
                    data=samples,
                    dpi=raster.dpi,
                )

        try:
            return write_tiff(rendered(), compression=compression)
        except ValueError as exc:
            raise PdfValidationException(str(exc)) from exc

    def save_as_tiff(
        self,
        destination: str | Path,
        *,
        pages: Sequence[int] | None = None,
        dpi: float = 72.0,
        scale: float = 1.0,
        background: tuple[int, int, int] = (255, 255, 255),
        antialias: bool | int = True,
        mode: str = "rgb",
        compression: str = "deflate",
        threshold: int = 128,
        dither: bool = False,
    ) -> Path:
        """Render pages into a single multi-page TIFF file.

        The bytes are :meth:`to_tiff`'s; this writes them to *destination*.

        Returns
        -------
        Path
            The path written.
        """
        data = self.to_tiff(
            pages=pages,
            dpi=dpi,
            scale=scale,
            background=background,
            antialias=antialias,
            mode=mode,
            compression=compression,
            threshold=threshold,
            dither=dither,
        )
        out = Path(destination)
        out.parent.mkdir(parents=True, exist_ok=True)
        write_file_atomically(out, data)
        return out

    def replace_text(
        self,
        search: str | re.Pattern[str],
        replacement: str,
        *,
        page_index: int | None = None,
        case_sensitive: bool = True,
        max_count: int = 0,
        regex: bool = False,
        font: FontDescriptor | bytes | bytearray | str | Path | None = None,
        layout: TextLayoutOptions | None = None,
    ) -> int:
        """Replace existing text in simple page-content text-showing operands.

        When *page_index* is omitted, every page is scanned. ``max_count=0``
        means unlimited. This is a conservative content-stream edit for simple
        ``Tj``/``TJ`` operands; it does not perform layout reflow. Returns the
        number of replacements made.

        A replacement containing right-to-left or complex-script characters is
        shaped (HarfBuzz + Unicode bidi): it reuses the run's own embedded font
        when that font already carries every shaped glyph, otherwise a
        shaping-capable *font* is embedded and the replacement drawn at the match
        position, using *layout* (a :class:`~aspose_pdf.text_layout.TextLayoutOptions`)
        for direction, script, and features. Reshaping needs the optional
        ``text-layout`` extra; without a usable path the edit raises rather than
        emit misshaped glyphs.

        With ``regex=True`` -- or a compiled :class:`re.Pattern` as *search*,
        which needs no flag -- the search is a regular expression and
        *replacement* becomes a **template**: ``\\1`` and ``\\g<name>`` expand to
        what each match captured, as :func:`re.sub` defines them. ``\\d{3}-\\d{2}-
        \\d{4}`` finds a shape rather than a phrase, which is what redacting an
        account number or a date needs. The pattern is matched against one
        logical text run at a time -- the text the page paints as one -- so
        ``^`` and ``$`` anchor to the run and not to a line, and a match cannot
        reach across a break that starts a new run. A pattern that matches the
        empty string is ignored. An RTL phrase is found in its stored visual
        order only when searched for as a literal; a regular expression's source
        is grammar, not text, and is not reordered.
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
            regex=regex,
            font=font,
            layout=layout,
        )

    def auto_link(
        self,
        *,
        pages: Iterable[int] | None = None,
        urls: bool = True,
        emails: bool = True,
        max_count: int = 0,
    ) -> AutoLinkResult:
        """Make recognized web and email addresses in existing text clickable.

        Recognize HTTP(S), ``www.`` (using HTTPS), and common ASCII email
        addresses (using ``mailto:``). Surrounding prose punctuation is removed
        from targets. No network requests or text/content changes are made.
        ``pages`` selects zero-based pages in order, including repeats.
        ``max_count=0`` considers all addresses; a positive cap includes
        unplaceable addresses and those overlapping existing links.

        The result reports new live ``annotations``, ``skipped_fragments``
        without reliable geometry, and ``existing_fragments`` overlapping any
        existing Link rectangle. Repeated runs do not add overlapping links.
        Coordinates use axis-aligned text bounds; forms, unmeasurable fonts,
        ambiguous repetitions and wrapped addresses may remain unlinked.
        Selected streaming page content stays lazy. Validation precedes edits;
        errors on later pages can leave earlier pages' links in place.
        """
        from aspose_pdf.auto_links import _auto_link

        return _auto_link(self, pages=pages, urls=urls, emails=emails, max_count=max_count)

    def highlight_text(
        self,
        search: str | re.Pattern[str],
        *,
        page_index: int | None = None,
        case_sensitive: bool = True,
        max_count: int = 0,
        regex: bool = False,
        color: ColorValue = (1.0, 1.0, 0.0),
        opacity: float = 0.5,
        contents: str = "",
        title: str | None = None,
    ) -> TextHighlightResult:
        """Add one highlight annotation per reliably placed text match.

        Search all pages or one zero-based ``page_index``. A string is literal
        unless ``regex=True``; compiled text patterns retain their flags.
        ``case_sensitive=False`` adds case-insensitive matching. Matches are
        non-overlapping, and zero-width regex matches are ignored.
        ``max_count=0`` is unlimited; otherwise it caps matches considered
        across pages, including skipped ones, in extraction order.

        The result reports live ``annotations`` and ``skipped_fragments`` with
        page indexes and text offsets. Text without usable font metrics, text
        assembled across separate runs, and form-XObject text may be skipped.
        Repeated occurrences are placed only when extraction and geometry
        agree on their count. Existing page text is preserved. The colour uses
        the standard grey/RGB/CMYK forms; opacity is between zero and one.
        """
        from aspose_pdf.engine.text_highlight import highlight_text

        return highlight_text(
            self,
            search,
            page_index=page_index,
            case_sensitive=case_sensitive,
            max_count=max_count,
            regex=regex,
            color=color,
            opacity=opacity,
            contents=contents,
            title=title,
        )

    def redact_text(
        self,
        search: str | re.Pattern[str],
        *,
        page_index: int | None = None,
        case_sensitive: bool = True,
        max_count: int = 0,
        regex: bool = False,
        overlay: bool = False,
        overlay_color: ColorValue = (0.0, 0.0, 0.0),
    ) -> int:
        """Remove existing text from simple page-content text-showing operands.

        With ``overlay=True`` a filled rectangle (``overlay_color`` -- grey, RGB
        or CMYK, default black) is drawn over each removed run -- the
        classic redaction bar. The bar is cosmetic (the text is already removed
        from the content); a run whose position cannot be tracked is left unmarked.

        A successful redaction requires a full save: obsolete streams and
        object storage are discarded, and ``save(incremental=True)`` raises.
        Existing signatures are invalidated by that rewrite. Seekable output
        streams are replaced from offset zero and truncated; append-mode and
        nonseekable streams are refused. Alternate text attached to changed
        runs and their structure ancestors is removed in full. Tagged Form
        XObjects and named alternate-text properties inside forms are refused.
        Unresolved structure mappings, ambiguous property aliases and removal
        of named properties inherited by forms also raise
        :exc:`~aspose_pdf.exceptions.PdfValidationException`. If an error occurs
        after earlier pages were edited, those edits remain in the document.

        This edits matched page/form text only, not independent copies in
        metadata, attachments, annotations, images, or unselected occurrences.
        The original input file is unchanged unless explicitly overwritten.

        With ``regex=True`` -- or a compiled :class:`re.Pattern` as *search* --
        the search is a regular expression, which is what redacting a *shape*
        rather than a phrase needs: ``\\d{3}-\\d{2}-\\d{4}`` for a social security
        number, ``\\b\\d{4}( \\d{4}){3}\\b`` for a card. The overlay bars come from
        the same spans the removal used, so they land on the text that went. See
        :meth:`replace_text` for how a pattern is matched.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        return self._engine_pdf.redact_text(
            search,
            page_index=page_index,
            case_sensitive=case_sensitive,
            max_count=max_count,
            regex=regex,
            overlay=overlay,
            overlay_color=overlay_color,
        )

    def load_from(
        self,
        source: str | bytes | bytearray | Path | BinaryIO,
        *,
        password: str | None = None,
        certificate: Any = None,
        private_key: Any = None,
        key_encryption_key: bytes | bytearray | None = None,
        limits: PdfLoadLimits | None = None,
    ) -> Document:
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
        certificate, private_key : optional
            Certificate recipient credentials for an ``/Adobe.PubSec`` PDF.
        key_encryption_key : bytes, optional
            Pre-shared AES key for a CMS KEK recipient.
        limits : PdfLoadLimits, optional
            Resource policy for this load. When omitted, the policy configured
            on the document is reused.

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
        if limits is not None:
            self._load_limits = _coerce_limits(limits)
        resolved_limits = self._load_limits
        eff_pwd = _effective_encryption_password(password)
        self._password = eff_pwd if eff_pwd is not None else password
        credential = _coerce_credential(
            certificate, private_key, key_encryption_key
        )

        if isinstance(source, (str, Path)):
            path = Path(source)
            if not path.is_file():
                raise FileNotFoundError(f"File not found: {path}")
            self._engine_pdf = SimplePdf.from_file(
                path,
                password=password,
                credential=credential,
                limits=resolved_limits,
            )
            self.file_name = str(path)
        elif isinstance(source, (bytes, bytearray)):
            # SimplePdf.from_bytes validates the PDF header; pass password so
            # that encrypted PDFs are not rejected before decrypt() is called.
            budget = _LoadBudget(resolved_limits)
            budget.check_input(len(source))
            data = bytes(source) if isinstance(source, bytearray) else source
            self._engine_pdf = SimplePdf.from_bytes(
                data,
                password=password,
                credential=credential,
                limits=resolved_limits,
                _budget=budget,
            )
            self.file_name = None
        elif hasattr(source, "read"):
            # BinaryIO / file-like object — read all bytes then delegate
            budget = _LoadBudget(resolved_limits)
            data = _read_limited(source, budget)
            self._engine_pdf = SimplePdf.from_bytes(
                data,
                password=password,
                credential=credential,
                limits=resolved_limits,
                _budget=budget,
            )
            self.file_name = None
        elif _describe_unsupported(source) is not None:
            _reject_load_options(source)
        else:
            raise TypeError(
                "source must be str, Path, bytes, or a readable binary stream"
            )

        if eff_pwd and self._engine_pdf:
            self._engine_pdf.decrypt(eff_pwd)
        elif credential is not None and self._engine_pdf:
            # A public-key document has no password to hand to decrypt(); the
            # credential already unlocked it during load. Running the same
            # post-load decrypt keeps it on the COS writer's path, which
            # preserves the original /Encrypt dictionary (and its /Recipients)
            # on a re-save, exactly as for a password-protected file.
            self._engine_pdf.decrypt("")

        self._encrypted = self._engine_pdf.encrypted if self._engine_pdf else False
        # Queued signing named fields of whatever was loaded before.
        self._pending_signing = []
        return self

    def sanitize(
        self,
        *,
        metadata: bool = True,
        javascript: bool = True,
        attachments: bool = True,
        hidden_layers: bool = True,
        comments: bool = True,
        links: bool = False,
        forms: bool = False,
    ) -> SanitizationReport:
        """Take out everything the document carries that is not what it shows.

        A PDF travels with more than its pages. Who wrote it and when, what
        tool made it and what that tool kept in its own private store, scripts
        a viewer runs the moment it opens, whole files embedded inside, layers
        switched off but still there, comments with a reviewer's name on them.
        Each of those is removable on its own; this is the one pass that goes
        through all of them and says what it took::

            with Document("contract.pdf") as document:
                print(document.sanitize())
                # removed 7 metadata entries, 1 scripts, 3 actions, 1 attachments
                document.save("contract-clean.pdf")

        What is removed, and what is left alone:

        metadata
            ``/Info`` (title, author, producer, dates), the XMP packet, and the
            ``/PieceInfo`` an editor keeps its working data in -- on the
            catalog and on every page.
        javascript
            The ``/Names /JavaScript`` tree, the open action when it *is* an
            action, the catalog's and every page's additional actions, and any
            action on an annotation or field that runs script, launches a file
            or sends data away. A link that opens a URL keeps working: that is
            what a link is for, and the annotation stays either way.
        attachments
            Embedded files, from the name tree and from the ``/AF`` entries
            that reach them from the catalog and the pages.
        hidden_layers
            Optional content is resolved once and for all, as
            :meth:`flatten_layers` does: content the current configuration
            hides is **deleted**, not merely switched off.
        comments
            Markup annotations -- notes, highlights, stamps, ink -- each of
            which carries an author and a date (12.5.6.2).
        links
            Link annotations. **Off** by default: a link is part of the
            document's content, and a dangerous action on one is already dealt
            with by *javascript*.
        forms
            The interactive form and its widgets. **Off** by default, since
            removing it takes the fields' values with it. To keep what the
            fields show while losing the interactivity, use :meth:`flatten`.

        What this does **not** touch is the page content: text stays text and
        images stay images. Text that must not be readable is
        :meth:`redact_text`'s job, not this one.

        Returns
        -------
        SanitizationReport
            The counts of what was removed; ``bool(report)`` is ``False`` when
            the document carried none of it.

        Notes
        -----
        The next :meth:`save` is a **full rewrite**, not an incremental one --
        an appended revision would keep the unsanitized one in front of it in
        the same file, so everything removed would still be there to read. For
        the same reason a save after this breaks any signature the document
        carries, which is unavoidable: a signature attests to bytes that no
        longer exist.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        from aspose_pdf.engine.sanitize import sanitize as _sanitize

        # Bookmarks are a copy taken on first access; putting them back before
        # the pass means edits made through the collection are in the engine
        # when it rewrites, rather than thrown away with the view.
        self._flush_outlines()
        report = _sanitize(
            self._engine_pdf,
            metadata=metadata,
            javascript=javascript,
            attachments=attachments,
            hidden_layers=hidden_layers,
            comments=comments,
            links=links,
            forms=forms,
        )
        self._engine_pdf._sanitized = True
        # The form is a view over fields and widgets that may have just gone;
        # the next access rebuilds it from what is actually there now.
        self._form = None
        return report

    def optimize(
        self,
        options: OptimizationOptions | None = None,
        *,
        compress_streams: bool = True,
    ) -> Document:
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
        self, options: OptimizationOptions | None = None
    ) -> Document:
        """Alias for :meth:`optimize`."""
        return self.optimize(options)

    def compress_streams(self) -> Document:
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
            PDF/A level: ``'1a'``/``'1b'``, ``'2a'``/``'2b'``/``'2u'``,
            ``'3a'``/``'3b'``/``'3u'``, or ``'4'``/``'4e'``/``'4f'``.

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
            PDF/A conformance level to check (e.g. ``"1b"``, ``"2b"``,
            ``"4"``). ISO 19005-4 has no accessible/basic/unicode split: its
            levels are ``"4"``, ``"4e"`` (engineering, which permits 3D and
            rich media) and ``"4f"`` (embedded files of any type).

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
        """Heuristic PDF/UA-1 catalog structure check (tagged PDF shell).

        Uses the same rules as :meth:`validate_pdfua`.  A ``True`` value does
        **not** mean the document is accessible or PDF/UA certified — only
        that required catalog entries for tagging passed this library's
        lightweight inspection. For ISO 14289-2 call
        ``validate_pdfua(part=2)``.
        """
        self._ensure_not_disposed()
        return self.validate_pdfua().is_valid

    def validate_pdfua(self, part: int = 1) -> PdfUaValidationResult:
        """Validate catalog-level PDF/UA prerequisites (heuristic).

        Checks for ``/StructTreeRoot``, ``/MarkInfo`` with ``/Marked true``,
        and emits a warning when ``/Lang`` is missing.  This is **not** a full
        PDF/UA validator: use :attr:`PdfUaValidationResult.is_heuristic` and
        :attr:`PdfUaValidationResult.HEURISTIC_VALIDATION_NOTICE` when
        building compliance or accessibility automation.

        Parameters
        ----------
        part : int
            ``1`` for ISO 14289-1 (the default) or ``2`` for ISO 14289-2, which
            is defined on PDF 2.0 and additionally requires an XMP
            ``pdfuaid:rev`` and structure types drawn from the PDF 2.0 standard
            structure namespace.

        Returns
        -------
        PdfUaValidationResult
            ``errors``, ``warnings``, ``is_heuristic`` (default ``True``), and
            ``is_valid``.  ``len(result)`` equals ``len(result.errors)``.
        """
        self._ensure_not_disposed()
        if self._engine_pdf:
            errs, warns = self._engine_pdf.check_pdfua_compliance(part)
        else:
            errs, warns = (["No document loaded"], [])
        return PdfUaValidationResult(errors=errs, warnings=warns)

    def convert_to_pdfa(
        self,
        level: str = "1b",
        *,
        font_lookup_directory: str | Path | None = None,
    ) -> list[str]:
        """Convert the document to PDF/A format in-place.

        Removes prohibited content, injects an OutputIntents array with an
        sRGB ICC profile, adds an XMP metadata stream, and ensures the
        document title is set — bringing the document into conformance with
        the requested PDF/A level.

        A **Standard-14** font that the document does not embed is embedded
        from the bundled metric-compatible substitutes, since PDF/A requires
        every font embedded and those are the ones a converter can supply. Any
        *other* unembedded font is reported in the returned list instead: the
        program is not in the file and cannot be invented.

        Parameters
        ----------
        level : str
            Target PDF/A conformance level (e.g. ``'1b'``, ``'2b'``, ``'3b'``,
            ``'4'``).  Case-insensitive. PDF/A-4 is written against PDF 2.0, so
            the header version is raised to match.

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

    def is_pdfx_compliant(self, standard: str = "PDF/X-4") -> bool:
        """Whether the document conforms to a PDF/X level (heuristic).

        Uses the same checks as :meth:`validate_pdfx`; do not use as a
        certification gate -- and note that no free validator covers PDF/X, so
        unlike the PDF/A checks these are not cross-checked against a reference
        implementation.

        Parameters
        ----------
        standard : str
            ``"PDF/X-1a:2001"``, ``"PDF/X-1a:2003"``, ``"PDF/X-3:2002"``,
            ``"PDF/X-3:2003"`` or ``"PDF/X-4"``, in any spelling
            :meth:`aspose_pdf.pdfx.PdfXStandard.by_name` accepts.

        Returns
        -------
        bool
            ``True`` when the document raises no PDF/X errors.
        """
        return self.validate_pdfx(standard).is_valid

    def validate_pdfx(self, standard: str = "PDF/X-4") -> PdfXValidationResult:
        """Validate the document against a PDF/X standard (heuristic checks).

        PDF/X (ISO 15930) is about print exchange: a conforming file carries
        everything the press needs and nothing whose result would depend on the
        reader. The checks cover the identification keys, the output intent and
        its ICC profile, font embedding, the trim/bleed geometry, the trapping
        status, encryption, prohibited actions and annotations, transfer
        functions, and -- per standard -- transparency, optional content,
        JPEG 2000 and device-independent colour.

        Partial, rule-of-thumb checks only. Use
        :attr:`~aspose_pdf.pdfx.PdfXValidationResult.is_heuristic` (always
        ``True`` here) and
        :attr:`~aspose_pdf.pdfx.PdfXValidationResult.HEURISTIC_VALIDATION_NOTICE`
        when building prepress automation.

        Parameters
        ----------
        standard : str
            The conformance level to check. Defaults to ``"PDF/X-4"``, the
            newest level this library implements.

        Returns
        -------
        PdfXValidationResult
            ``errors``, ``warnings``, ``standard``, ``is_heuristic`` and
            ``is_valid``. ``len(result)`` equals ``len(result.errors)``, as for
            :meth:`validate_pdfa`.

        Raises
        ------
        ValueError
            If *standard* is not a level this library knows.
        """
        self._ensure_not_disposed()
        name = normalize_pdfx_standard(standard)
        if self._engine_pdf:
            errors, warnings = self._engine_pdf.check_pdfx_compliance(name)
        else:
            errors, warnings = (["No document loaded"], [])
        return PdfXValidationResult(
            errors=errors, warnings=warnings, standard=name
        )

    def convert_to_pdfx(
        self,
        standard: str = "PDF/X-4",
        *,
        icc_profile: bytes | bytearray | str | Path | None = None,
        output_condition_identifier: str | None = None,
        output_condition: str | None = None,
        registry_name: str = "http://www.color.org",
        trapped: str | bool = "False",
        title: str | None = None,
        font_lookup_directory: str | Path | None = None,
    ) -> list[str]:
        """Convert the document toward a PDF/X standard in place.

        Writes the identification the standard asks for in the place it asks
        for it, states the trapping status, gives every page a ``/TrimBox``
        where it has neither trim nor art box, embeds the output intent, strips
        what the standard prohibits (additional actions, document JavaScript,
        XFA, and optional content for the parts built on PDF 1.4), and embeds
        the bundled substitutes for unembedded Standard-14 fonts.

        It will **not invent a printing condition**. The output intent is the
        promise a PDF/X file makes about the press it was made for, so where
        one is needed and neither *icc_profile* nor the document supplies it,
        the requirement stays in the returned list instead of a profile nobody
        chose being embedded. A standard that permits an RGB intent (PDF/X-3
        and PDF/X-4) falls back to the bundled sRGB profile, which is a real
        registered characterization; PDF/X-1a admits no RGB intent, so
        converting to it needs a CMYK profile passed in.

        Colour is **not** converted either: a PDF/X-1a target with RGB content
        reports the RGB rather than guessing a separation for it, the way an
        unembeddable font is reported rather than substituted.

        Parameters
        ----------
        standard : str
            Target conformance level; see :meth:`validate_pdfx`.
        icc_profile : bytes, bytearray, str or Path, optional
            The characterized printing condition, as ICC profile bytes or the
            path of an ``.icc``/``.icm`` file.
        output_condition_identifier : str, optional
            ``/OutputConditionIdentifier`` -- the registered name of that
            printing condition, such as ``"FOGRA39"``. Defaults to the
            profile's own ``desc`` tag, and failing that to ``"Custom"``.
        output_condition : str, optional
            ``/OutputCondition``, a human-readable note about the condition.
        registry_name : str
            ``/RegistryName``, where the identifier is registered.
        trapped : str or bool
            Written to ``/Trapped``. ``"Unknown"`` is refused: PDF/X exists so
            that the receiver does not have to guess.
        title : str, optional
            ``dc:title`` for the XMP packet. Defaults to the existing title.
        font_lookup_directory : str or Path, optional
            Searched for the programs of unembedded fonts before the bundled
            substitutes are tried.

        Returns
        -------
        List[str]
            The conformance issues that remain. An empty list means the
            document now passes :meth:`validate_pdfx` for that standard.

        Raises
        ------
        AsposePdfException
            If the document is disposed, was not loaded from a file or byte
            stream, or is encrypted (PDF/X prohibits encryption).
        PdfValidationException
            If *trapped* is not a yes-or-no answer.
        ValueError
            If *standard* is not a level this library knows.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        return self._engine_pdf.convert_to_pdfx(
            standard,
            icc_profile=icc_profile,
            output_condition_identifier=output_condition_identifier,
            output_condition=output_condition,
            registry_name=registry_name,
            trapped=trapped,
            title=title,
            font_lookup_directory=font_lookup_directory,
        )

    def convert_to_pdfua(
        self,
        *,
        language: str = "en",
        title: str | None = None,
        auto_tag: bool = False,
        part: int = 1,
    ) -> list[str]:
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
        part : int
            ``1`` for ISO 14289-1 (the default) or ``2`` for ISO 14289-2, which
            also raises the header to PDF 2.0, adds an XMP ``pdfuaid:rev`` and
            declares the PDF 2.0 standard structure namespace.

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
            language=language, title=title, auto_tag=auto_tag, part=part
        )

    def add_table_of_contents(
        self,
        *,
        title: str | None = "Contents",
        max_level: int = 3,
        prefer_structure: bool = True,
        at: int = 0,
        size: Any = None,
        margin: float = 72.0,
        font_name: str = "Helvetica",
        title_size: float = 20.0,
        entry_size: float = 11.0,
        indent: float = 18.0,
        leader: str = ".",
        links: bool = True,
        page_numbers: bool = True,
        tag: bool = False,
    ) -> list[int]:
        """Put a contents page in front of the document, and return its pages.

        The headings come from the same reading :meth:`generate_outlines` does
        -- the structure tree where the document has one, the size tiers where
        it does not -- so a contents page and a bookmark tree say the same
        thing. Each line is the heading, dot leaders, the page it is on, and a
        link that lands on the heading itself rather than on the top of its
        sheet::

            with Document("report.pdf") as document:
                document.add_table_of_contents()
                document.save("report-with-contents.pdf")

        The **page numbers account for the contents itself**. Putting it at the
        front pushes every page it points at further back, so the numbers are
        worked out after the pages exist, not before; a heading on a page
        *before* *at* keeps the number it had. Where the document has page
        labels, the label is what is shown -- a contents that says ``12`` for a
        page the reader sees as ``xii`` is wrong in the only way that matters.

        Parameters
        ----------
        title : str, optional
            The heading over the list. ``None`` writes none.
        max_level : int
            Ignore headings deeper than this (1 to 6).
        prefer_structure : bool
            Read the structure tree when it has headings in it, as
            :meth:`generate_outlines` describes.
        at : int
            Where the contents goes. ``0`` is in front of everything; ``1``
            puts it after a cover page.
        size : PageSize, str or (width, height), optional
            The sheet to make it on. Defaults to the size of the page it goes
            in front of, so it matches the document it belongs to.
        margin : float
            Points of blank around the list.
        font_name : str
            One of the standard 14 fonts.
        title_size, entry_size, indent : float
            Point sizes for the title and the lines, and how far each level is
            indented past the one above.
        leader : str
            What fills the gap between a heading and its number. ``""`` leaves
            it blank.
        links : bool
            Make each line clickable.
        page_numbers : bool
            Show the page each heading is on.
        tag : bool
            Mark the lines as ``/TOCI`` in the structure tree (table 333). Off
            by default, since a document that is not otherwise tagged gains
            nothing from one tagged page.

        Returns
        -------
        list[int]
            The indexes of the pages inserted, in order. **Empty** when the
            document has no headings to list -- nothing is inserted, because a
            contents page listing nothing is not worth a sheet of paper.

        Raises
        ------
        PdfValidationException
            If a size or a measurement is one a page cannot have.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        from aspose_pdf.engine.toc import build

        for label, value in (
            ("margin", margin),
            ("title_size", title_size),
            ("entry_size", entry_size),
            ("indent", indent),
        ):
            number = float(value)
            if number != number or number < 0:
                raise PdfValidationException(f"{label} is not negative: {value!r}.")
        if entry_size <= 0:
            raise PdfValidationException("entry_size is more than zero.")
        if not isinstance(leader, str):
            raise PdfValidationException("leader is a string, possibly empty.")
        at = max(0, min(int(at), self.page_count))
        sheet = None
        if size is not None:
            from aspose_pdf.pages import _coerce_page_size

            chosen = _coerce_page_size(size)
            sheet = (float(chosen.width), float(chosen.height))

        self._flush_outlines()
        pages = build(
            self._engine_pdf,
            self,
            title=title,
            max_level=max_level,
            prefer_structure=prefer_structure,
            at=at,
            size=sheet,
            margin=float(margin),
            font_name=font_name,
            title_size=float(title_size),
            entry_size=float(entry_size),
            indent=float(indent),
            leader=leader,
            links=links,
            page_numbers=page_numbers,
            tag=tag,
        )
        if pages and self._form is not None:
            self._form._load_fields()
        return pages

    def generate_outlines(
        self,
        *,
        max_level: int = 6,
        prefer_structure: bool = True,
        replace: bool = True,
        open_to_level: int = 0,
        zoom: float | None = None,
    ) -> int:
        """Build a bookmark tree from the document's headings.

        A **tagged** document has already been told what its headings are: the
        structure tree names them ``/H1``..``/H6``, or ``/H`` with the section
        nesting giving the level, and that is the author's own answer. An
        untagged one is read with the same size-tier analysis
        :meth:`to_markdown` and :meth:`auto_tag` use -- a heuristic, so the
        headings it finds are the ones those would find, and no better.

        Each bookmark lands on the heading itself, not on the top of its page: a
        heading's place comes from the marked-content sequence it owns, or from
        the block's own anchor, and is written as an ``/XYZ`` destination. A
        heading whose place cannot be told -- a structure element whose content
        is not on its own page -- gets the page alone.

        Levels nest, skips included: an ``H3`` after an ``H1`` with no ``H2``
        between them becomes a child of the ``H1`` rather than a sibling.

        Parameters
        ----------
        max_level : int
            Ignore headings deeper than this (1 to 6).
        prefer_structure : bool
            Read the structure tree when it has headings in it. ``False`` reads
            the pages whatever the tree says, which is what a document whose
            tagging is only a shell wants.
        replace : bool
            Clear the existing bookmarks first, which is what *generate* means.
            ``False`` appends to them.
        open_to_level : int
            Write bookmarks up to this level **unfolded**, so a reader shows
            their children. ``0``, the default, leaves every one folded, as a
            bookmark tree is written unless asked otherwise.
        zoom : float, optional
            The magnification each bookmark asks for. ``None`` keeps whatever
            the reader is already using, which is what a reader expects of a
            bookmark.

        Returns
        -------
        int
            How many bookmarks were created. ``0`` means no headings were
            found -- a document of body text has none, and one whose only
            structure is a shell has none to read either.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        if int(max_level) < 1:
            raise PdfValidationException("max_level is a whole number from one")
        from aspose_pdf.engine.headings import find_headings
        from aspose_pdf.interactive import XYZDestination

        headings = find_headings(
            self._engine_pdf,
            max_level=int(max_level),
            prefer_structure=bool(prefer_structure),
        )
        outlines = self.outlines
        if replace:
            outlines.clear()
        if not headings:
            return 0

        # One frame per open level, so a deeper heading becomes a child of the
        # nearest shallower one -- which is what nesting means even when a level
        # is skipped.
        stack: list[tuple[int, OutlineItem]] = []
        created = 0
        for heading in headings:
            target: object | None = None
            if heading.x is not None and heading.y is not None:
                target = XYZDestination(
                    page=heading.page_index,
                    left=heading.x,
                    top=heading.y,
                    zoom=zoom,
                )
            item = OutlineItem(
                heading.text,
                heading.page_index,
                destination=target,
                open=heading.level <= int(open_to_level),
            )
            while stack and stack[-1][0] >= heading.level:
                stack.pop()
            if stack:
                stack[-1][1].add(item)
            else:
                outlines.add(item)
            stack.append((heading.level, item))
            created += 1
        return created

    def auto_tag(
        self,
        image_alt: str | Callable[[str], str] | None = "Image",
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
        aid, not certified accessibility; review and refine its reading order,
        hierarchy, and alternate descriptions through :attr:`tagged_content`,
        and pair it with :meth:`convert_to_pdfua` for catalog prerequisites.

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
        self,
        destination: str | Path | BinaryIO,
        save_format: Any = None,
        *,
        overwrite: bool = False,
        incremental: bool | None = None,
    ) -> Document:
        """Save the document to a file path or a binary stream.

        Parameters
        ----------
        destination : str, Path, or BinaryIO
            File system path *or* any writable binary stream (e.g. ``BytesIO``,
            an open file handle in binary mode, an HTTP response body, …).
        save_format : SaveFormat, DocFormat, HtmlSaveOptions, MarkdownSaveOptions, or None
            ``None`` (the default), ``SaveFormat.PDF`` or ``DocFormat.PDF``
            write PDF. ``DocFormat.HTML``, ``DocFormat.MARKDOWN`` and
            ``DocFormat.SVG`` export through :meth:`save_as_html`,
            :meth:`save_as_markdown` and :meth:`save_as_svg` with their
            defaults; an ``HtmlSaveOptions`` or ``MarkdownSaveOptions`` passes
            its settings on, and one it cannot honour raises rather than being
            ignored. These need a path: an export may be several files.
            Placeholders such as ``SaveFormat.PPTX`` raise instead of writing a
            mislabelled PDF.
        overwrite : bool
            Only relevant when *destination* is a path.  When ``False`` (the
            default) an existing file raises :exc:`FileExistsError` -- for an
            export, any of the files it would write, before it writes one. When
            ``True`` the file is replaced in one step -- the new bytes are
            staged beside it and renamed over it -- so a save that fails, for
            want of disk space or anything else, leaves it exactly as it was,
            including when it is the file this document was loaded from.
        incremental : bool or None
            ``None`` (the default) writes an incremental update when the file
            carries signatures that a rewrite would break -- its ``/SigFlags``
            says *AppendOnly* (ISO 32000-1 12.7.2) or a field is signed -- and a
            full rewrite otherwise, so opening a signed document and saving it
            keeps its signatures valid. ``False`` asks for a full rewrite
            regardless, and logs a warning naming what it invalidates.
            When ``True``, write a byte-preserving incremental update: the
            original file bytes are emitted verbatim and only the objects added
            or modified since load are appended as a new revision chained
            through ``/Prev``. This keeps any existing digital signature valid
            and is efficient for small edits to large files. Requires a document
            that was loaded from an existing PDF (a document built from scratch
            falls back to a full write); encrypted or to-be-signed documents are
            rejected with :exc:`~aspose_pdf.exceptions.PdfSecurityException`.
            After text redaction, ``None`` and ``False`` always perform a full
            rewrite and ``True`` raises the same exception, since preserving
            any prior revision would also preserve the removed text. A redacted
            PDF written to a stream replaces its contents from offset zero
            and truncates the stream; nonseekable and append-mode outputs are
            refused.

        What :meth:`sign`, :meth:`add_ltv` and :meth:`add_document_timestamp`
        asked for is done here, each in a revision appended to the bytes the
        choice above produces; the document is then reloaded from the file it
        wrote.

        Returns
        -------
        Document
            Self for method chaining.

        Raises
        ------
        UnsupportedFeatureException
            If *save_format* names an export this package does not implement.
            Nothing is written to *destination* in that case.
        """
        self._ensure_not_disposed()
        exporter = _text_export_format(save_format)
        if _is_svg_save_format(save_format) or exporter is not None:
            if hasattr(destination, "write"):
                raise PdfValidationException(
                    "SVG, HTML and Markdown output need a file path, not a "
                    "stream: a document may become several files"
                )
            from aspose_pdf import _export_files as files

            # A DocFormat member carries no options: every setting at its
            # default. An options object is honoured in full, or refused.
            if exporter == "html":
                options = save_format if hasattr(save_format, "split_into_pages") else None
                self.save_as_html(
                    destination, overwrite=overwrite, **files.html_arguments(options)
                )
            elif exporter == "markdown":
                options = save_format if hasattr(save_format, "extract_images") else None
                self.save_as_markdown(
                    destination, overwrite=overwrite, **files.markdown_arguments(options)
                )
            else:
                self.save_as_svg(destination, overwrite=overwrite)
            return self
        _require_pdf_save_format(save_format)

        self._flush_outlines()

        signing = bool(self._pending_signing)
        if signing:
            # Protection may have changed since sign() was called.
            self._check_reopenable()
            # Before serialization, so the signature covers what it draws.
            self._flush_signature_appearances()
        incremental = self._choose_incremental(incremental)
        path = None if hasattr(destination, "write") else Path(destination)
        redacted = self._engine_pdf._redacted
        if redacted and path is None:
            if (
                not callable(getattr(destination, "seek", None))
                or not callable(getattr(destination, "truncate", None))
                or (hasattr(destination, "seekable") and not destination.seekable())
                or "a" in str(getattr(destination, "mode", ""))
            ):
                raise PdfSecurityException(
                    "Saving a redacted PDF requires a file path or a seekable, "
                    "truncatable stream opened without append mode."
                )
        # Refused before anything is serialized, signed or timestamped.
        if path is not None and path.exists() and not overwrite:
            raise FileExistsError(f"File already exists: {path}")
        if incremental:
            data = self._engine_pdf.to_bytes_incremental()
        else:
            data = self._engine_pdf.to_bytes()
        if signing:
            data = self._apply_pending_signing(data)
        if path is None:
            if redacted:
                destination.seek(0)
            write_all(destination, data)
            if redacted:
                destination.truncate()
        else:
            write_file_atomically(path, data)
        if signing:
            self._reopen(data)
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
        self._tagged_content = None
        self._pending_signing = []
        self.file_name = None

    def close(self) -> None:
        """Alias of :meth:`dispose` (matches .NET ``Close``)."""
        self.dispose()

    def extract_text(
        self,
        *,
        pages: Iterable[int] | None = None,
        clip: Sequence[float] | None = None,
        skip_artifacts: bool = False,
    ) -> str:
        """The text of all or selected pages, separated by one line break.

        ``pages`` is an iterable of zero-based integer indices, preserving its
        order and repetitions. ``None`` selects every page; an empty iterable
        returns an empty string. Invalid indices are rejected before any page
        is read. Streaming documents decode only the selected page content.
        Each page's own line breaks are kept; see :meth:`Page.extract_text`.
        ``clip=(x0, y0, x1, y1)`` applies the same unrotated PDF-coordinate
        rectangle to each selected page, selecting overlapping glyph bounds.
        Empty pages retain their separators, and a subset or region does not
        replace the cached full-document text.
        ``skip_artifacts=True`` omits text in explicitly marked ``/Artifact``
        sequences, including nested blocks and invoked forms. Untagged headers
        and footers are ordinary text; no positional heuristic is applied.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            return ""
        return self._engine_pdf.extract_text(
            pages=pages, clip=clip, skip_artifacts=skip_artifacts
        )

    def extract_words(
        self,
        *,
        pages: Iterable[int] | None = None,
        clip: Sequence[float] | None = None,
        skip_artifacts: bool = False,
    ) -> list[TextWord]:
        """Return detached positioned words from selected pages in content order.

        Results contain ``text``, zero-based ``page_index``, ``rect`` (also
        ``bbox``) and ``quad``. Coordinates are PDF points in unrotated user
        space, independent of CropBox and page rotation. Bounds use font
        advances, ascent and descent rather than visible glyph ink.

        Words are non-whitespace sequences within a measured text run.
        Adjacent show operands and font changes can form one word; line/run,
        marked-content and Form XObject boundaries separate words. Punctuation
        stays attached; no language-specific segmentation or reading-order
        reconstruction is performed. Hidden layers are omitted. Invisible OCR
        text counts, and ``skip_artifacts=True`` omits marked artifacts.

        ``clip`` selects whole words whose axis-aligned rectangles overlap
        the region, retaining their full text and bounds. It does not crop
        words or implicitly intersect the page boxes. Unknown/unmeasurable
        runs and logical /ActualText replacement spans are omitted rather than
        assigned invented coordinates. Images and widget values are not OCRed.

        ``pages`` preserves order and repetitions; all indices and options are
        validated before content is decoded. Streaming inputs read selected
        page streams and invoked forms, without filling full-document caches
        or decoding images and unused forms. Resource limits propagate without
        a partial result. Results survive source edits and disposal.
        """
        from aspose_pdf.engine.word_extraction import extract_words

        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        return extract_words(
            self, pages=pages, clip=clip, skip_artifacts=skip_artifacts
        )

    def _choose_incremental(self, requested: bool | None) -> bool:
        """Decide how :meth:`save` writes, honouring signatures already there.

        A full save used to be the default for every document, and it rewrites
        every byte offset -- so opening a signed document and saving it, even
        untouched, broke every signature in it, silently, while the file it
        wrote still carried ``/SigFlags 3``: *AppendOnly*, the flag that says a
        writer must not do exactly that.
        """
        engine = self._engine_pdf
        binds = engine.existing_signatures_bind()
        if engine._redacted or engine._sanitized:
            what = "Text redaction" if engine._redacted else "Sanitizing"
            if requested:
                raise PdfSecurityException(
                    f"Incremental save is forbidden after {what.lower()}: it "
                    "would preserve what was removed in the revision it "
                    "appends to."
                )
            if binds:
                logger.warning(
                    "%s requires a full rewrite; existing signatures will no "
                    "longer verify.",
                    what,
                )
            return False
        if requested is None:
            if not binds:
                return False
            if engine.can_append():
                return True
            logger.warning(
                "Saving with a full rewrite: this document carries signatures, "
                "but its protection is being changed (or it is about to be "
                "signed), which an incremental update cannot express. The "
                "existing signatures will no longer verify."
            )
            return False
        if not requested and binds:
            logger.warning(
                "Saving with a full rewrite as asked: the existing signatures "
                "in this document will no longer verify. Save without "
                "incremental=False to keep them."
            )
        return requested

    def _flush_outlines(self) -> None:
        """Put the live outline collection back where the engine reads it.

        The collection is a copy taken on first access, so anything the engine
        does with bookmarks -- writing them, or handing them to a merge --
        must see the caller's edits first.
        """
        if self._outlines is not None and self._engine_pdf is not None:
            self._engine_pdf._outlines_data = self._outlines._to_list()
            if not self._outlines._items and self._engine_pdf._cos_doc is not None:
                from aspose_pdf.engine.cos import PdfDictionary, PdfName

                cos = self._engine_pdf._cos_doc
                catalog = self._engine_pdf._resolve(
                    cos.trailer.mapping.get(PdfName("Root"))
                )
                if isinstance(catalog, PdfDictionary):
                    catalog.mapping.pop(PdfName("Outlines"), None)

    def get_content_bounds(
        self,
        *,
        pages: Iterable[int] | None = None,
        dpi: float = 72.0,
        white_threshold: int = 250,
        include_annotations: bool = True,
    ) -> dict[int, tuple[float, float, float, float] | None]:
        """Measure visible content on selected pages without modifying the PDF.

        Return a dictionary keyed by zero-based source page indices, in
        first-selected order; repeated pages are measured once. None selects
        all pages, and an empty selection/document returns an empty dictionary.
        Blank-looking pages retain a None value. Other values are detached
        ``(x0, y0, x1, y1)`` tuples in unrotated PDF page user coordinates.

        Options and the complete selection are validated before any rendering,
        even for an empty selection. Detection follows Page.get_content_bounds():
        white paper, no antialiasing, current CropBox/rotation/layer visibility,
        and optional visible annotation/widget appearances. At finite DPI it
        is approximate and may miss faint or tiny marks. Stored hidden content
        is not inventoried and no OCR is performed.

        Selected pages render one at a time; streaming inputs do not populate
        all-page content caches. Render, parser and resource-limit failures
        propagate without a partial dictionary. Boxes, content, annotations,
        metadata and extraction cursors remain unchanged. No optional imaging
        dependency is required.
        """
        from aspose_pdf.engine.content_bounds import document_bounds

        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        return document_bounds(
            self,
            pages=pages,
            dpi=dpi,
            white_threshold=white_threshold,
            include_annotations=include_annotations,
        )

    def find_blank_pages(
        self,
        *,
        pages: Iterable[int] | None = None,
        dpi: float = 72.0,
        white_threshold: int = 250,
        max_ink_ratio: float = 0.0,
        include_annotations: bool = True,
    ) -> list[int]:
        """Find visually blank pages without changing this document.

        Return zero-based indices in the selected order, ignoring repeats.
        ``pages=None`` checks all pages; an empty iterable checks none. All
        indices and options are validated before rendering. Streaming inputs
        read only selected pages, and each raster is released before the next.

        Detection uses :meth:`Page.is_blank <aspose_pdf.pages.Page.is_blank>`:
        at the chosen DPI, a pixel counts as ink if any RGB channel is below
        ``white_threshold``; an ink fraction at most ``max_ink_ratio`` is blank.
        Defaults are 72 DPI, threshold 250, zero tolerated ink, with visible
        annotations included. The renderer's limitations and finite resolution
        apply; hidden text or data may still exist on a blank-looking page.
        Rendering errors and resource limits propagate without a partial list.
        """
        from aspose_pdf.engine.blank_pages import find_blank_pages

        self._ensure_not_disposed()
        return find_blank_pages(
            self,
            pages=pages,
            dpi=dpi,
            white_threshold=white_threshold,
            max_ink_ratio=max_ink_ratio,
            include_annotations=include_annotations,
        )

    def find_duplicate_pages(
        self,
        *,
        pages: Iterable[int] | None = None,
        dpi: float = 72.0,
        include_annotations: bool = True,
    ) -> list[list[int]]:
        """Find groups of pages with identical rendered views without editing them.

        Each group contains at least two distinct zero-based page indices,
        including the first occurrence. Groups and their members follow first
        occurrence in the selection. ``pages=None`` selects all pages; explicit
        indices are validated in full and deduplicated before rendering. Fewer
        than two distinct pages returns ``[]`` without rendering.

        Pages match when their raster dimensions and every RGB pixel match at
        ``dpi`` on white paper, with antialiasing disabled. CropBox, rotation,
        layer visibility and font substitution follow :meth:`Page.render
        <aspose_pdf.pages.Page.render>`. Visible annotations and form appearances
        count unless ``include_annotations=False``. No text extraction or OCR
        is required. DPI must be finite and positive; flags must be booleans.

        SHA-256 selects candidates, and a new render of a group's first page
        confirms pixel equality. At most two completed rasters are held at
        once, with compact fingerprints for earlier pages. Streaming inputs
        read only selected pages without filling all-page content caches;
        candidate verification can read and render reference pages again.
        Rendering errors and resource limits propagate without a partial list.

        Equality is specific to this renderer and resolution. Tiny differences
        may be missed; different physical sizes can round to the same raster
        dimensions. Scan noise, color changes and other visible pixel changes
        remain differences. There is no fuzzy matching, rescaling, automatic
        rotation alignment or margin removal. Hidden text, form values and
        other stored data may differ even when the visible pages match.
        """
        from aspose_pdf.engine.duplicate_pages import find_duplicate_pages

        self._ensure_not_disposed()
        return find_duplicate_pages(
            self,
            pages=pages,
            dpi=dpi,
            include_annotations=include_annotations,
        )

    def compare_text(
        self,
        other: Document,
        *,
        pages: Iterable[int] | None = None,
        other_pages: Iterable[int] | None = None,
        case_sensitive: bool = True,
        ignore_whitespace: bool = False,
        skip_artifacts: bool = False,
    ) -> list[PageTextComparison]:
        """Compare extracted page text and return detached per-pair reports.

        Select pages independently with zero-based iterables; order and repeats
        are retained. ``None`` means all pages. Pages are paired by position,
        without automatic alignment. Unpaired pages have a missing index/text
        on the other side and never match, even against blank pages. Two empty
        selections return []. Both selectors and all flags are validated first.

        Defaults compare exact extracted strings. ``ignore_whitespace=True``
        collapses Unicode whitespace runs to a single space and strips leading
        and trailing whitespace; word boundaries remain significant.
        ``case_sensitive=False`` applies Unicode case folding. Original text
        remains in each report; ``unified_diff`` describes the normalized text
        with three lines of context. Missing blank pages have headers alone.
        ``skip_artifacts=True`` omits explicitly marked Artifact text.

        Uses ordinary full-page text extraction, including hidden OCR text and
        its reading-order/font limitations. Images need an existing text layer;
        appearance, metadata, annotations and form values are not compared.
        CropBox and rotation do not clip or reorder extraction. This is not OCR
        or a semantic comparison and does not establish PDF equivalence.

        Extracts one pair at a time without filling full-document text/content
        caches. Reports retain original strings and diffs and survive document
        disposal. Shared work uses the stricter enabled source limits for pair
        count, string/diff UTF-8 sizes and cumulative retained report bytes;
        content decoding keeps each source's own limits. Line diff matching can
        be expensive for repetitive text; use page subsets for large inputs.
        Errors propagate without a partial list. Neither document is edited.
        """
        from aspose_pdf.engine.text_comparison import compare_text

        self._ensure_not_disposed()
        if not isinstance(other, Document):
            raise PdfValidationException("other must be a Document")
        other._ensure_not_disposed()
        if self._engine_pdf is None or other._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        return compare_text(
            self,
            other,
            pages=pages,
            other_pages=other_pages,
            case_sensitive=case_sensitive,
            ignore_whitespace=ignore_whitespace,
            skip_artifacts=skip_artifacts,
        )

    def compare_visual(
        self,
        other: Document,
        *,
        pages: Iterable[int] | None = None,
        other_pages: Iterable[int] | None = None,
        dpi: float = 72.0,
        tolerance: int = 0,
        max_diff_ratio: float = 0.0,
        include_annotations: bool = True,
        include_diff_images: bool = False,
        ignore_regions: Iterable[Sequence[float]] | None = None,
    ) -> list[PageVisualComparison]:
        """Compare rendered page pairs and optionally retain red difference images.

        ``other`` must be an open ``Document``. ``pages`` and ``other_pages``
        independently select zero-based indices in their respective documents;
        ``None`` means all pages on that side. Order and repetitions are kept.
        Selections are paired by position; a shorter side produces missing-page
        results with a ``None`` index and size. Two empty selections return [].

        Pages render on white paper at finite positive ``dpi``, without
        antialiasing, using each document's font substitution, CropBox, rotation
        and layer visibility. Visible annotations and form appearances count
        unless ``include_annotations=False``. Views align at the top-left
        without scaling, alignment correction, deskewing or page matching.

        A pixel changes when any RGB channel differs by more than ``tolerance``
        (integer 0-255). Nonignored pixels covered by only one page always
        change. The difference ratio divides changed pixels by the union of
        page areas after removing ignored pixels.
        ``matches`` requires both pages, equal raster dimensions and a ratio
        at most ``max_diff_ratio`` (0-1). Default tolerances require exact
        rendered equality. Bounding boxes use top-left pixel coordinates with
        exclusive upper bounds; metadata and hidden content are not compared.

        ``ignore_regions`` is an iterable of ``(x0, y0, x1, y1)`` rectangles in
        PDF points (1/72 inch), measured right/down from the top-left of the
        displayed comparison canvas, after CropBox and rotation. The same
        rectangles apply to every pair and both sides; they are independent of
        DPI, not unrotated PDF user-space boxes. Finite real coordinates must
        enclose positive area. Negative/off-canvas bounds are clipped. Pixels
        touched by a region are ignored, rounding its bounds outward. Overlaps
        count once. Exclusions affect metrics, bounds and match status, and
        ``ignored_pixels`` reports their union within the page areas. An all-
        ignored pair has zero total/ratio and matches only if both pages exist
        with equal raster dimensions. Neither PDF's content is removed.

        ``include_diff_images=True`` attaches a ``RasterizedPage`` to every
        result: changes are red over pale grayscale context. Images enclose
        both views, with ignored areas in light blue and white padding outside
        both pages. Save them through
        ``result.diff_image.save(path)``. Without this flag, only metrics are
        retained. Reports and images outlive the source documents.

        Both selections, ignore regions, geometry and comparison canvas limits
        are checked before rendering. The stricter enabled resource limits
        apply to shared work and output images; retained images also share a
        cumulative ``max_raster_pixels`` allowance. Selected pages render one pair at a
        time; streaming content remains lazy. Errors propagate without partial
        results, and neither PDF is edited. Equality depends on the renderer's
        coverage and resolution; tiny differences or unsupported drawing may
        be missed, and different physical sizes can round to the same raster.
        """
        from aspose_pdf.engine.visual_comparison import compare_visual

        self._ensure_not_disposed()
        if not isinstance(other, Document):
            raise PdfValidationException("other must be a Document")
        other._ensure_not_disposed()
        if self._engine_pdf is None or other._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        return compare_visual(
            self,
            other,
            pages=pages,
            other_pages=other_pages,
            dpi=dpi,
            tolerance=tolerance,
            max_diff_ratio=max_diff_ratio,
            include_annotations=include_annotations,
            include_diff_images=include_diff_images,
            ignore_regions=ignore_regions,
        )

    def extract_pages(self, selection: Iterable[int] | slice) -> Document:
        """Return an independent document containing selected pages in order.

        *selection* is a zero-based iterable of page indexes or a slice. Page
        resources, annotations, fields, layers, and bookmarks that belong to
        those pages travel with them. An empty selection is invalid.
        """
        self._ensure_not_disposed()
        self._flush_outlines()
        extracted = self._engine_pdf.extract_pages(selection)
        result = Document(limits=self._load_limits)
        result._engine_pdf = extracted
        return result

    def interleave(
        self,
        other: Document,
        *,
        reverse_other: bool = False,
        strict: bool = True,
    ) -> Document:
        """Return a new document alternating this document's pages with *other*.

        Use ``fronts.interleave(backs, reverse_other=True)`` for two scanner
        passes whose back sides were scanned in reverse order. The first
        source keeps its order. With ``strict=True`` the page counts must
        match; ``strict=False`` appends the unmatched tail in its chosen order.
        No blank pages are inserted, and neither input is edited or disposed.

        Page resources, annotations, form fields, labels, layers, attachments
        and bookmarks follow the shared page importer. Local destinations stay
        attached to their pages. The result keeps this document's Info metadata
        and resource limits and the higher PDF version. It is independent of
        both inputs and starts without encryption; existing signatures cannot
        retain their validity in an assembled document. Dispose it when done.
        """
        from aspose_pdf.engine.interleave import interleave

        self._ensure_not_disposed()
        if not isinstance(other, Document):
            raise TypeError("other must be a Document")
        other._ensure_not_disposed()
        if self._engine_pdf is None or other._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        engine = interleave(
            self._engine_pdf,
            other._engine_pdf,
            reverse_other=reverse_other,
            strict=strict,
            first_outlines=None if self._outlines is None else self._outlines._items,
            second_outlines=None if other._outlines is None else other._outlines._items,
        )
        result = Document(limits=self._load_limits)
        result._engine_pdf = engine
        return result

    def split_by_bookmarks(
        self,
        level: int = 1,
        *,
        include_preamble: bool = True,
    ) -> list[Document]:
        """Return independent chapter documents using bookmarks at ``level``.

        Level 1 selects root bookmarks. Local page destinations, named
        destinations and GoTo actions are resolved as they stand now; headings,
        external actions and unresolved targets do not start chapters. Starts
        are sorted by page, with one output per distinct start page. The first
        bookmark in outline order supplies the title when several share a page.

        Each chapter ends before the next start, or at the end of the PDF. Pages
        before the first start become a ``Preamble`` document unless
        ``include_preamble=False``. No usable bookmarks at the requested level
        raises ``PdfValidationException``. The source is unchanged.

        Results follow :meth:`extract_pages`, with their Info ``Title`` set to
        the chapter's bookmark title. Relevant nested bookmarks are retained
        even when an ancestor points outside the chapter. Dispose every result
        when done. Splitting imports whole pages, not parts between headings
        on one page; it does not execute bookmark actions. These new documents
        do not inherit encryption or preserve existing signature validity.
        """
        from aspose_pdf.engine.bookmark_split import split_by_bookmarks

        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        result = []
        try:
            for engine in split_by_bookmarks(
                self._engine_pdf, level=level, include_preamble=include_preamble,
                outlines=None if self._outlines is None else self._outlines._items,
            ):
                chapter = Document(limits=self._load_limits)
                chapter._engine_pdf = engine
                result.append(chapter)
        except BaseException:
            for chapter in result:
                chapter.dispose()
            raise
        return result

    def split_by_text(
        self,
        pattern: str | re.Pattern[str],
        *,
        regex: bool = False,
        case_sensitive: bool = True,
        include_preamble: bool = True,
    ) -> list[Document]:
        """Return independent parts starting at pages whose extracted text matches.

        A string is a literal substring unless ``regex=True``. Compiled text
        regular expressions always use regex matching and keep their flags;
        ``case_sensitive=False`` adds ``re.IGNORECASE``. Matching is a search
        anywhere in each page's extracted text, with Python's regex semantics.
        Zero-width regex matches count. Patterns cannot be empty.

        A matching page starts a new part, ending before the next matching
        page or at the end of the PDF. Multiple matches on one page give one
        boundary. Leading pages become a separate part unless
        ``include_preamble=False``. No matches, including an empty document,
        raises ``PdfValidationException``. Pages are never split internally.

        Uses ordinary text extraction, including its line breaks, hidden OCR
        text and limitations; image-only scans need an existing text layer.
        Matches cannot span pages. Annotations and form values are not searched.
        Streaming inputs are scanned one page at a time without filling the
        full-document text or content caches. Python's regex engine has no
        execution timeout; use patterns appropriate for the input.

        Results follow :meth:`extract_pages`, retaining source Info metadata
        and relevant bookmarks, including live edits and children whose parents
        point outside the part. Neither the source nor its files are changed.
        Dispose each returned document when done. Partial results are disposed
        on failure. New parts do not inherit source encryption or retain the
        validity of existing digital signatures.
        """
        from aspose_pdf.engine.text_split import split_by_text

        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        result = []
        try:
            for engine in split_by_text(
                self._engine_pdf,
                pattern,
                regex=regex,
                case_sensitive=case_sensitive,
                include_preamble=include_preamble,
                outlines=None if self._outlines is None else self._outlines._items,
            ):
                part = Document(limits=self._load_limits)
                part._engine_pdf = engine
                result.append(part)
        except BaseException:
            for part in result:
                part.dispose()
            raise
        return result

    def split_by_blank_pages(
        self,
        *,
        keep_separators: bool = False,
        dpi: float = 72.0,
        white_threshold: int = 250,
        max_ink_ratio: float = 0.0,
        include_annotations: bool = True,
    ) -> list[Document]:
        """Split a scan batch into independent parts using visibly blank pages.

        With ``keep_separators=False`` (default), discard blank pages from the
        outputs and return each nonblank run as one part. Leading, trailing
        and consecutive blank pages do not create empty or blank-only parts.
        With ``keep_separators=True``, attach each separating blank run to the
        preceding part, keeping leading blanks in the first part. Every source
        page then appears exactly once. An entirely blank document returns []
        by default, or one full copy when keeping separators; an empty document
        always returns []. A document without blank pages returns one copy.

        Detection follows :meth:`find_blank_pages`: white paper, antialiasing
        disabled, finite positive ``dpi``, byte ``white_threshold`` (1-255) and
        tolerated ink fraction ``max_ink_ratio`` (0-1). Visible annotations and
        form appearances count unless ``include_annotations=False``. CropBox,
        rotation, layer visibility and font substitution follow the renderer.
        Image-only scans work without OCR. Tiny or faint marks, scan borders
        and unsupported graphics are subject to the renderer's limitations.
        Blank-looking pages can still contain hidden text or other data.

        Options and page-count limits are checked first. All pages are scanned
        one at a time before any part is imported, without filling all-page
        content/text caches. Results follow :meth:`extract_pages`: retain Info
        metadata, page resources, boxes, labels, annotations, fields, layers,
        tags and relevant bookmarks, including live edits and promoted children
        of excluded parents. Local links are remapped by the page importer;
        targets outside a part cannot remain valid local page targets.
        Document-level attachments are not copied. Parts are independent,
        inherit resource limits, and start without source encryption or valid
        source signatures. Dispose every returned document when done.

        Neither the source nor its files are edited. Rendering/import errors
        propagate, with partial results disposed and the source outline model
        restored. This operation copies whole pages; it does not erase data
        from the original PDF or clear hidden data on pages retained in outputs.
        """
        from aspose_pdf.engine.blank_split import split_by_blank_pages

        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        result = []
        try:
            for engine in split_by_blank_pages(
                self,
                keep_separators=keep_separators,
                dpi=dpi,
                white_threshold=white_threshold,
                max_ink_ratio=max_ink_ratio,
                include_annotations=include_annotations,
            ):
                part = Document(limits=self._load_limits)
                part._engine_pdf = engine
                result.append(part)
        except BaseException:
            for part in result:
                part.dispose()
            raise
        return result

    def n_up(
        self,
        rows: int = 2,
        columns: int = 2,
        *,
        page_size: Any = None,
        margin: float = 0.0,
        gutter: float = 0.0,
        order: str = "row",
    ) -> Document:
        """Return a new document with ``rows * columns`` pages on each sheet.

        Each page is brought across as a form XObject with everything it draws
        with, scaled to fit its cell proportionally and centred in it -- nothing
        is stretched and nothing is cut off. The sheet is *page_size*, or the
        first page's own size when none is given, so four pages go onto a sheet
        the size of one. A landscape sheet for a 2-up of portrait pages is asked
        for by passing one::

            two_up = document.n_up(1, 2, page_size=PageSize.A4.landscape())

        *order* is ``"row"`` (left to right, top row first) or ``"column"``.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        from aspose_pdf.engine import imposition

        imposed = imposition.n_up(
            self._engine_pdf,
            rows=rows,
            columns=columns,
            page_size=page_size,
            margin=margin,
            gutter=gutter,
            order=order,
        )
        result = Document(limits=self._load_limits)
        result._engine_pdf = imposed
        return result

    def booklet(
        self,
        *,
        page_size: Any = None,
        margin: float = 0.0,
        gutter: float = 0.0,
    ) -> Document:
        """Return a new document imposed as a saddle-stitched booklet.

        Two pages to a sheet, in the order that reads correctly once the printed
        stack is folded down the middle: the last page beside the first, the
        second beside the second-last, and so on. The page count is padded to a
        multiple of four with blanks, because a folded sheet carries four pages
        whether they are all used or not. The sheet is twice the width of a page
        unless *page_size* says otherwise.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        from aspose_pdf.engine import imposition

        imposed = imposition.booklet(
            self._engine_pdf,
            page_size=page_size,
            margin=margin,
            gutter=gutter,
        )
        result = Document(limits=self._load_limits)
        result._engine_pdf = imposed
        return result

    def resize_pages(
        self,
        size: Any,
        *,
        mode: str = "fit",
        pages: Sequence[int] | slice | None = None,
    ) -> int:
        """Give *pages* a new sheet size and scale their drawing onto it.

        The operation :meth:`Page.resize` performs, over a document: every page
        when none are named. A mixed-size document comes out uniform, each page
        scaled from whatever it was -- which is what printing a folder of
        drawings on one paper stock needs.

        ``mode`` is ``"fit"`` (keep the aspect ratio, even margin on the long
        side), ``"fill"`` (keep it, let the drawing run past the short side) or
        ``"stretch"`` (scale the axes apart to fill the sheet exactly). See
        :meth:`Page.resize`; setting :attr:`Page.size` changes the sheet and
        leaves the drawing where it was.

        Returns
        -------
        int
            How many pages were resized.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        count = len(self._engine_pdf.pages)
        if pages is None:
            indices: Sequence[int] = range(count)
        elif isinstance(pages, slice):
            indices = range(count)[pages]
        else:
            indices = [int(index) for index in pages]
        resolved = 0
        for index in indices:
            self.pages[index].resize(size, mode=mode)
            resolved += 1
        return resolved

    def merge(self, *documents: Document) -> Document:
        """Merge the supplied documents into this one."""
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            self._engine_pdf = SimplePdf()

        for doc in documents:
            if not isinstance(doc, Document):
                raise TypeError("All items to merge must be Document instances")
            if doc._engine_pdf is not None:
                self._import_pages_from(doc)
        return self

    def _import_pages_from(
        self,
        other: Document,
        pages: Sequence[int] | None = None,
        at: int | None = None,
    ) -> None:
        """Bring pages from *other* into this document, with all they own.

        The one way pages cross a document boundary, whether that is a whole
        :meth:`merge` or a single :meth:`PageCollection.insert
        <aspose_pdf.pages.PageCollection.insert>`: the page *and* the graph it
        names -- resources, annotations, the fields its widgets belong to, the
        optional content it mentions, the structure under it, the bookmarks
        pointing at it. Copying the page dictionary alone leaves content whose
        ``/F1`` resolves to the target's ``/F1``, or to nothing.

        *pages* names which of *other*'s pages to take, in the order to take
        them, and *at* where to put them; both default to "all, at the end".
        """
        other._flush_outlines()
        self._flush_outlines()
        self._engine_pdf.append(other._engine_pdf, pages=pages, at=at)
        # The bookmarks the import appended are the engine's now; a collection
        # taken before it would still be the old tree.
        self._outlines = None
        if self._form is not None:
            self._form._load_fields()

    def encrypt(
        self,
        user_password: str,
        owner_password: str | None = None,
        *,
        permissions: int = -4,
        algorithm: str = "AES-256",
    ) -> Document:
        """Encrypt the PDF document with the standard security handler.

        Parameters
        ----------
        user_password : str
            Password required to open the document.
        owner_password : str, optional
            Password required to change security settings.  Defaults to the
            user password if omitted.
        permissions : int or Permissions
            What a reader may then do, as a ``/P`` word (ISO 32000-1 Table 22).
            A raw signed integer still works; build one by name with
            :class:`~aspose_pdf.permissions.Permissions` --
            ``Permissions.denying("copy")``,
            ``Permissions.allowing("print", "print_high_resolution")`` -- which
            keeps the reserved bits as the standard requires, where ORing the
            bit values together by hand clears them. Defaults to
            :meth:`Permissions.all` (``-4``), every permission granted.
        algorithm : str
            ``"AES-256"`` (the default, ``/V 5 /R 6``), ``"AES-128"``
            (``/V 4 /R 4``, AESV2) or ``"RC4"`` (128-bit, ``/V 2 /R 3``).
            RC4 is offered for readers that predate AES and is weak; prefer a
            default AES-256 document. An unrecognised name raises
            :class:`~aspose_pdf.exceptions.PdfSecurityException` rather than
            quietly encrypting with a different cipher.

        Raises
        ------
        PdfSecurityException
            If *algorithm* is not one of the supported names.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        permissions = int(Permissions.of(permissions))
        self._engine_pdf.encrypt(
            user_password,
            owner_password or user_password,
            permissions=permissions,
            algorithm=algorithm,
        )
        self._encrypted = True
        return self

    def encrypt_for_recipients(
        self,
        recipients: Sequence[Recipient | Any],
        *,
        algorithm: str = "AES-256",
        permissions: int = ALL_PERMISSIONS,
        ignore_key_usage: bool = False,
    ) -> Document:
        """Encrypt for certificate *recipients* with the public-key handler.

        Where :meth:`encrypt` gates the document behind a shared password, this
        gates it behind certificates: each recipient opens the file with the
        private key it already holds, and each one carries its own permissions.
        There is no user/owner split and no password to distribute.

        Parameters
        ----------
        recipients:
            :class:`~aspose_pdf.Recipient` objects, or bare ``cryptography``
            certificates, which then all receive *permissions*. At least one is
            required, and each needs an RSA or EC public key.
        algorithm:
            ``"AES-256"`` (the default, ``/V 5 /R 6``, ``/SubFilter
            adbe.pkcs7.s5``), ``"AES-128"`` or ``"RC4"`` (128-bit), the latter
            two ``/V 4 /R 4`` with ``adbe.pkcs7.s4``.
        permissions:
            Flags for recipients given as bare certificates. Defaults to
            granting everything; see :class:`~aspose_pdf.Recipient` for the bit
            layout, which is *not* quite the standard handler's ``/P``. The
            eight permissions :class:`~aspose_pdf.permissions.Permissions` names
            sit at the same bit positions in both words, so
            ``Permissions.denying("copy")`` means the same thing here -- the
            handler's own fixed bits are normalised on the way out, which is why
            the default is ``-1`` rather than :meth:`Permissions.all`.
        ignore_key_usage:
            Encrypt to a certificate whose ``keyUsage`` extension forbids RSA
            key transport or EC key agreement. Off by default, because a
            reader that enforces the extension would reject the result.

        Raises
        ------
        PdfSecurityException
            If *recipients* is empty, a certificate cannot transport or agree
            a key, or *algorithm* is not a supported name.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        pairs: list[tuple[Any, int]] = []
        for entry in recipients:
            if isinstance(entry, Recipient):
                pairs.append(entry.as_pair())
            else:
                pairs.append((entry, int(permissions)))
        self._engine_pdf.encrypt_for_recipients(
            pairs, algorithm=algorithm, ignore_key_usage=ignore_key_usage
        )
        self._encrypted = True
        return self

    def decrypt(self, password: str) -> Document:
        """Remove the document's password protection.

        This is the counterpart of :meth:`encrypt`: the next save writes a
        plain file. Opening an encrypted document already unlocks it for
        reading, and a re-save keeps the protection it came with -- taking the
        lock off is this explicit call.

        *password* must open the protection: the user or the owner password,
        whichever the document was opened with. Anything else raises
        :class:`~aspose_pdf.exceptions.PdfSecurityException` and leaves the
        protection in place. (A document certificate-protected in this session
        needs no password to take that protection off again.)
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        self._engine_pdf.decrypt(password)
        self._engine_pdf.remove_password()
        self._encrypted = False
        return self

    def change_passwords(
        self,
        old_password: str,
        new_user_password: str,
        new_owner_password: str | None = None,
        *,
        permissions: int | Permissions | None = None,
        algorithm: str | None = None,
    ) -> Document:
        """Change the document's passwords and keep everything else.

        *old_password* may be the user or the owner password. By default the new
        passwords guard the **same permissions with the same cipher** the
        document already had; only the passwords change. As with
        :meth:`encrypt`, an omitted *new_owner_password* makes the new user
        password the owner password too. A document with no protection gains
        one with :meth:`encrypt`'s defaults.

        Parameters
        ----------
        permissions : int or Permissions, optional
            New access permissions -- ``Permissions.denying("print")`` and the
            rest. ``None``, the default, keeps the ones the document has, which
            is what makes this a password change rather than a re-protection.
        algorithm : str, optional
            A new cipher, as :meth:`encrypt` names them. ``None`` keeps the
            document's own.

        Raises
        ------
        PdfSecurityException
            If *old_password* opens neither the user nor the owner side.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        self._engine_pdf.change_passwords(
            old_password,
            new_user_password,
            new_owner_password,
            permissions=(
                None if permissions is None else int(Permissions.of(permissions))
            ),
            algorithm=algorithm,
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

    def repair(self) -> Document:
        """Attempt to repair the PDF document."""
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        self._engine_pdf.repair()
        return self

    def flatten(
        self,
        *,
        fields: bool | Iterable[str] = True,
        annotations: bool = True,
        pages: Iterable[int] | None = None,
        strict: bool = True,
    ) -> Document:
        """Bake selected form fields and annotations into static page content.

        By default both are flattened. Set ``annotations=False`` for fields
        only, ``fields=False`` for non-widget annotations only, or pass fully
        qualified terminal field names in ``fields``. ``pages`` contains
        zero-based indices; omitted means all pages. Duplicate selections are
        ignored. Unknown field names raise ``KeyError`` unless ``strict=False``;
        invalid selections are rejected before modifying the document.

        Missing supported appearances are generated only for selected objects.
        Hidden/NoView objects and objects without a usable normal appearance
        or rectangle are retained, preserving viewing and printing semantics.
        A field with widgets on other pages stays editable there. Unselected
        appearances and the AcroForm's ``NeedAppearances`` flag are preserved.
        An unsafe page graphics state raises ``PdfValidationException``. If a
        later page fails, completed pages stay flattened and form collections
        are refreshed; the failing page's annotations are retained. The whole
        operation is not transactional. This does not execute JavaScript or
        flatten XFA forms.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        try:
            self._engine_pdf.flatten(
                fields=fields, annotations=annotations, pages=pages, strict=strict
            )
        finally:
            if self._form is not None:
                self._form._load_fields()
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

    def generate_field_appearances(self, *, keep_existing: bool = False) -> int:
        """Regenerate AcroForm field appearance streams from their values.

        Builds the variable-text appearance (``/AP /N``) of each text and choice
        field from its value and default appearance (``/DA``) so the value renders
        without relying on ``/NeedAppearances``, and points each check box / radio
        widget's ``/AS`` at the state matching its value. Missing caption-only
        push-button appearances are also generated. Returns the number of widgets
        updated. With *keep_existing*, existing normal appearances of text,
        choice and signature widgets are preserved while missing ones are built.
        Call after setting field values; :meth:`flatten` does this automatically.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        return self._engine_pdf.generate_field_appearances(
            keep_existing=keep_existing
        )

    def free_memory(self) -> Document:
        """Free memory by clearing caches."""
        self._ensure_not_disposed()
        if self._engine_pdf:
            self._engine_pdf.free_memory()
        return self
