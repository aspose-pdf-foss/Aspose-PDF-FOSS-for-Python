"""Document class for PDF manipulation.

This module provides the main Document class that wraps the native PDF engine.
"""

from __future__ import annotations

from collections.abc import Callable, Generator, Iterator, Sequence
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
from aspose_pdf.attachments import AF_RELATIONSHIPS, FileSpecification
from aspose_pdf.engine.simple_pdf import (
    SimplePdf,
    _effective_encryption_password,
    _parse_pdf_date,
)
from aspose_pdf.exceptions import AsposePdfException, PdfValidationException
from aspose_pdf.font_substitution import FontSubstitutionOptions
from aspose_pdf.layers import LayerCollection
from aspose_pdf.load_limits import (
    PdfLoadLimits,
    _coerce_limits,
    _LoadBudget,
    _read_limited,
)
from aspose_pdf.outlines import OutlineCollection
from aspose_pdf.pdfa import PdfAValidationResult
from aspose_pdf.pdfua import PdfUaValidationResult
from aspose_pdf.recipients import ALL_PERMISSIONS, Recipient

if TYPE_CHECKING:
    import datetime as _datetime

    from aspose_pdf.engine.rasterizer import RasterizedPage
    from aspose_pdf.font_registry import FontDescriptor
    from aspose_pdf.forms import Form
    from aspose_pdf.optimization import OptimizationOptions
    from aspose_pdf.pages import Page, PageCollection
    from aspose_pdf.tagged import TaggedContent
    from aspose_pdf.text_layout import TextLayoutOptions
    from aspose_pdf.xmp import XmpPacket


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


def _coerce_credential(certificate: Any, private_key: Any) -> tuple[Any, Any] | None:
    """Validate a recipient credential pair for a public-key document."""
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
            Password for an encrypted *source*.
        certificate, private_key : optional
            Recipient credentials for a *source* encrypted with the public-key
            handler (``/Adobe.PubSec``), which has no password. Both are
            ``cryptography`` objects and both are required together. See
            :class:`~aspose_pdf.Recipient`.
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
        self._tagged_content: Any | None = None
        self._password: str | None = None
        self._encrypted: bool = False
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
            return
        self.load_from(
            source,
            password=password,
            certificate=certificate,
            private_key=private_key,
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
        """Get or set the document metadata (info dictionary)."""
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

    @property
    def permissions(self) -> int:
        """Access-permission flags (PDF ``/P`` value).

        For unencrypted documents this returns ``-4`` (all permissions granted).
        The value is a signed 32-bit integer as defined in the PDF spec
        (Table 22 - User access permissions).
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            return -4
        return self._engine_pdf.P

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

    def render_page(
        self,
        page_index: int,
        *,
        dpi: float = 72.0,
        scale: float = 1.0,
        background: tuple[int, int, int] = (255, 255, 255),
        antialias: bool | int = True,
        shape_substitute_text: bool = True,
        draw_annotations: bool = True,
        font_substitution: FontSubstitutionOptions | None = None,
    ) -> RasterizedPage:
        """Render a page to an RGB raster image.

        ``page_index`` is zero-based. The result can be encoded with
        :meth:`RasterizedPage.to_png`, :meth:`RasterizedPage.to_tiff`, or saved
        directly with :meth:`RasterizedPage.save`. ``antialias`` smooths edges by
        supersampling (``True`` = 3x, an integer 1-8 sets the factor, ``False``
        disables it). ``shape_substitute_text`` (default on) joins complex-script
        runs drawn with a bundled substitute face; it needs the optional
        ``text-layout`` extra and only affects non-embedded fonts.
        ``draw_annotations`` (default on) composites each visible annotation's
        normal appearance over the page, the way a viewer shows it.
        ``font_substitution`` overrides :attr:`font_substitution` for this call.
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
        mode: str = "rgb",
        compression: str = "deflate",
        quality: int = 85,
        threshold: int = 128,
    ) -> Path:
        """Render one page and save it as PNG, TIFF or JPEG.

        The format follows the suffix of *destination*. ``mode`` selects
        ``"rgb"``, ``"gray"`` or ``"bilevel"`` output, ``compression`` applies
        to TIFF and ``quality`` to JPEG.
        """
        return self.render_page(
            page_index,
            dpi=dpi,
            scale=scale,
            background=background,
            antialias=antialias,
        ).save(
            destination,
            mode=mode,
            compression=compression,
            quality=quality,
            threshold=threshold,
        )

    def to_html(
        self,
        *,
        pages: Sequence[int] | None = None,
        title: str | None = None,
        embed_images: bool = True,
    ) -> str:
        """Return the document's inferred structure as one HTML document.

        Headings, paragraphs, lists, tables and figures are inferred with the
        same layout analysis :meth:`auto_tag` uses, and the text is decoded the
        way :meth:`extract_text` decodes it. HTML has no page model, so pages
        are separated by a rule and read as one flow. *title* defaults to the
        document's own ``/Title``.

        This is a conversion to a *flowing document*, not a facsimile: exact
        positioning, colour and fonts are dropped. For a facsimile, use
        :meth:`save_as_svg`.
        """
        from aspose_pdf.engine.text_export import to_html

        return to_html(
            self._export_blocks(pages, embed_images),
            title=title if title is not None else self._document_title(),
            embed_images=embed_images,
        )

    def to_markdown(
        self,
        *,
        pages: Sequence[int] | None = None,
        title: str | None = None,
        embed_images: bool = True,
    ) -> str:
        """Return the document's inferred structure as Markdown (GFM).

        See :meth:`to_html` for what the conversion carries over.
        """
        from aspose_pdf.engine.text_export import to_markdown

        return to_markdown(
            self._export_blocks(pages, embed_images),
            title=title if title is not None else self._document_title(),
            embed_images=embed_images,
        )

    def _document_title(self) -> str:
        try:
            return str(self.info.get("Title") or "")
        except Exception:
            return ""

    def _export_blocks(
        self, pages: Sequence[int] | None, embed_images: bool
    ) -> list[list[Any]]:
        from aspose_pdf.engine.text_export import page_blocks

        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        indexes = (
            list(range(self.page_count)) if pages is None else [int(i) for i in pages]
        )
        return [
            page_blocks(self._engine_pdf, index, include_images=embed_images)
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
    ) -> list[Path]:
        """Write the document as HTML and return the files written.

        One file by default. ``split_into_pages`` writes ``name-1.html``,
        ``name-2.html`` … instead, one per page.
        """
        blocks = self._export_blocks(pages, embed_images)
        from aspose_pdf.engine.text_export import to_html

        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        document_title = title if title is not None else self._document_title()
        if not split_into_pages:
            target.write_text(
                to_html(blocks, title=document_title, embed_images=embed_images),
                encoding="utf-8",
            )
            return [target]
        written: list[Path] = []
        for offset, page in enumerate(blocks, 1):
            path = target.with_name(
                f"{target.stem}-{offset}{target.suffix or '.html'}"
            )
            path.write_text(
                to_html([page], title=document_title, embed_images=embed_images),
                encoding="utf-8",
            )
            written.append(path)
        return written

    def save_as_markdown(
        self,
        destination: str | Path,
        *,
        pages: Sequence[int] | None = None,
        title: str | None = None,
        embed_images: bool = True,
    ) -> Path:
        """Write the document as one Markdown (GFM) file and return its path."""
        text = self.to_markdown(
            pages=pages, title=title, embed_images=embed_images
        )
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
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
    ) -> list[Path]:
        """Write pages as SVG and return the files written.

        SVG has no notion of a multi-page document, so each page becomes its
        own file. One page writes exactly *destination*; several write
        ``name-1.svg``, ``name-2.svg`` … beside it, numbered by the page's
        position in the document rather than in *pages*, so the file names
        still say which page they came from.

        See :meth:`~aspose_pdf.pages.Page.to_svg` for what the conversion does
        and does not turn into vectors.

        Returns
        -------
        list[Path]
            The files written, in the order the pages were selected.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        indexes = (
            list(range(self.page_count)) if pages is None else [int(i) for i in pages]
        )
        if not indexes:
            raise PdfValidationException("Saving SVG needs at least one page")
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for index in indexes:
            if len(indexes) == 1:
                path = target
            else:
                path = target.with_name(
                    f"{target.stem}-{index + 1}{target.suffix or '.svg'}"
                )
            written.append(
                self.pages[index].save_as_svg(
                    path,
                    background=background,
                    draw_annotations=draw_annotations,
                    font_substitution=font_substitution,
                    precision=precision,
                )
            )
        return written

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
    ) -> Path:
        """Render pages into a single multi-page TIFF file.

        Every page becomes one image in the file, in the order given by
        *pages* (all pages, in document order, when omitted). Pages are
        rendered one at a time and encoded as they go, so only the compressed
        result accumulates rather than every raster at once.

        ``mode`` selects ``"rgb"``, ``"gray"`` or ``"bilevel"`` output and
        ``compression`` is ``"deflate"`` (the default) or ``"none"``.

        Returns
        -------
        Path
            The path written.
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
                encoder_mode, samples = raster._samples(mode, threshold)
                yield TiffPage(
                    width=raster.width,
                    height=raster.height,
                    mode=encoder_mode,
                    data=samples,
                    dpi=raster.dpi,
                )

        try:
            data = write_tiff(rendered(), compression=compression)
        except ValueError as exc:
            raise PdfValidationException(str(exc)) from exc
        out = Path(destination)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        return out

    def replace_text(
        self,
        search: str,
        replacement: str,
        *,
        page_index: int | None = None,
        case_sensitive: bool = True,
        max_count: int = 0,
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
            font=font,
            layout=layout,
        )

    def redact_text(
        self,
        search: str,
        *,
        page_index: int | None = None,
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
        source: str | bytes | bytearray | Path | BinaryIO,
        *,
        password: str | None = None,
        certificate: Any = None,
        private_key: Any = None,
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
        credential = _coerce_credential(certificate, private_key)

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
        return self

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

        Font embedding is not performed automatically; any fonts that are not
        already embedded are reported as warnings in the returned list.

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
        incremental: bool = False,
    ) -> Document:
        """Save the document to a file path or a binary stream.

        Parameters
        ----------
        destination : str, Path, or BinaryIO
            File system path *or* any writable binary stream (e.g. ``BytesIO``,
            an open file handle in binary mode, an HTTP response body, …).
        save_format : SaveFormat, DocFormat, or None
            Only PDF output is implemented, so this accepts ``None`` (the
            default), ``SaveFormat.PDF``, or ``DocFormat.PDF``. Export
            placeholders such as ``SaveFormat.PPTX`` or ``HtmlSaveOptions``
            raise instead of writing a mislabelled PDF.
        overwrite : bool
            Only relevant when *destination* is a path.  When ``False`` (the
            default) an existing file raises :exc:`FileExistsError`.
        incremental : bool
            When ``True``, write a byte-preserving incremental update: the
            original file bytes are emitted verbatim and only the objects added
            or modified since load are appended as a new revision chained
            through ``/Prev``. This keeps any existing digital signature valid
            and is efficient for small edits to large files. Requires a document
            that was loaded from an existing PDF (a document built from scratch
            falls back to a full write); encrypted or to-be-signed documents are
            rejected with :exc:`~aspose_pdf.exceptions.PdfSecurityException`.

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
            if exporter == "html":
                options = save_format if hasattr(save_format, "split_into_pages") else None
                self.save_as_html(
                    destination,
                    split_into_pages=bool(
                        getattr(options, "split_into_pages", False)
                    ),
                )
            elif exporter == "markdown":
                options = save_format if hasattr(save_format, "extract_images") else None
                self.save_as_markdown(
                    destination,
                    embed_images=bool(getattr(options, "extract_images", True)),
                )
            else:
                self.save_as_svg(destination)
            return self
        _require_pdf_save_format(save_format)

        self._flush_outlines()

        if incremental:
            data = self._engine_pdf.to_bytes_incremental()
            if hasattr(destination, "write"):
                destination.write(data)
            else:
                path = Path(destination)
                if path.exists() and not overwrite:
                    raise FileExistsError(f"File already exists: {path}")
                path.write_bytes(data)
            return self

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
        self._tagged_content = None
        self.file_name = None

    def close(self) -> None:
        """Alias of :meth:`dispose` (matches .NET ``Close``)."""
        self.dispose()

    def _flush_outlines(self) -> None:
        """Put the live outline collection back where the engine reads it.

        The collection is a copy taken on first access, so anything the engine
        does with bookmarks -- writing them, or handing them to a merge --
        must see the caller's edits first.
        """
        if self._outlines is not None and self._engine_pdf is not None:
            self._engine_pdf._outlines_data = self._outlines._to_list()

    def merge(self, *documents: Document) -> Document:
        """Merge the supplied documents into this one."""
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            self._engine_pdf = SimplePdf()

        for doc in documents:
            if not isinstance(doc, Document):
                raise TypeError("All items to merge must be Document instances")
            if doc._engine_pdf is not None:
                doc._flush_outlines()
                self._flush_outlines()
                self._engine_pdf.append(doc._engine_pdf)
                # The bookmarks the merge appended are the engine's now; a
                # collection taken before it would still be the old tree.
                self._outlines = None
        return self

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
        permissions : int
            PDF access-permission flags (signed 32-bit, see PDF spec Table 22).
            Defaults to ``-4`` (all standard permissions granted).
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
            required, and each needs an RSA public key.
        algorithm:
            ``"AES-256"`` (the default, ``/V 5 /R 6``, ``/SubFilter
            adbe.pkcs7.s5``), ``"AES-128"`` or ``"RC4"`` (128-bit), the latter
            two ``/V 4 /R 4`` with ``adbe.pkcs7.s4``.
        permissions:
            Flags for recipients given as bare certificates. Defaults to
            granting everything; see :class:`~aspose_pdf.Recipient` for the bit
            layout, which is *not* quite the standard handler's ``/P``.
        ignore_key_usage:
            Encrypt to a certificate whose ``keyUsage`` extension forbids key
            transport. Off by default, because a reader that enforces the
            extension would reject the result.

        Raises
        ------
        PdfSecurityException
            If *recipients* is empty, a certificate cannot transport a key, or
            *algorithm* is not a supported name.
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
    ) -> Document:
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

    def repair(self) -> Document:
        """Attempt to repair the PDF document."""
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        self._engine_pdf.repair()
        return self

    def flatten(self) -> Document:
        """Flatten annotations and forms.

        Supported shape and text-markup annotations without an appearance stream
        are given a synthesised one first (see :meth:`generate_appearances`) so
        they render into the page content rather than being dropped.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        self._engine_pdf.flatten()
        if self._form is not None:
            self._form._fields.clear()
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
        widget's ``/AS`` at the state matching its value. Missing caption-only
        push-button appearances are also generated. Returns the number of widgets
        updated. Call after setting field values; :meth:`flatten` does this
        automatically.
        """
        self._ensure_not_disposed()
        if self._engine_pdf is None:
            raise AsposePdfException("No document loaded")
        return self._engine_pdf.generate_field_appearances()

    def free_memory(self) -> Document:
        """Free memory by clearing caches."""
        self._ensure_not_disposed()
        if self._engine_pdf:
            self._engine_pdf.free_memory()
        return self
