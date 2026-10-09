"""Facade layer: the task-shaped classes ported Aspose.PDF code reaches for.

Each facade binds a document, does one kind of work to it, and saves it --
`PdfFileInfo` for the information dictionary and the page geometry,
`PdfFileSecurity` for passwords and permissions, `PdfBookmarkEditor` for the
outline, `PdfAnnotationEditor` for annotations, `PdfFileStamp` for stamps and
running heads, `PdfPageEditor` for rotating, resizing and reordering pages,
`PdfXmpMetadata` for the XMP packet, and `PdfConverter` for rendering pages --
beside the original `PdfExtractor` and `PdfFileEditor`.

They add no capability: every one of them is a composition of the `Document` API
documented in `supported-features.md`. What they add is the shape, and one habit
of this layer worth stating once: **page numbers here are 1-based**, as they are
in the Aspose facades (and as `PdfFileEditor.extract` already was), while
everything else in the package counts pages from zero.

An operation that writes returns ``True`` or ``False`` and records why in
:attr:`last_exception`, rather than raising -- again the layer's own habit.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, ClassVar

from aspose_pdf.exceptions import (
    PDF_OPERATION_ERRORS,
    AsposePdfException,
    PdfResourceLimitException,
)
from aspose_pdf.load_limits import PdfLoadLimits
from aspose_pdf.permissions import Permissions

logger = logging.getLogger(__name__)


class _BoundFacade:
    """What every bound facade does the same way: bind, operate, save, dispose.

    A facade holds an open :class:`~aspose_pdf.document.Document`; the operations
    on it are the ones that document already offers. Nothing is written until
    :meth:`save`, so a facade can be bound, asked questions and thrown away
    without touching the file.
    """

    #: What a failed operation is logged as; a subclass needs no more than this.
    _what = "facade"

    def __init__(self) -> None:
        self._disposed: bool = False
        self._document: Any = None
        self._borrowed: bool = False
        self._password: str | None = None
        self._last_exception: BaseException | None = None

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def close(self) -> None:
        """Close the facade and the document it holds."""
        self.dispose()

    def dispose(self) -> None:
        """Mark the facade disposed and release the document it bound."""
        if self._disposed:
            return
        self._disposed = True
        document, self._document = self._document, None
        borrowed, self._borrowed = self._borrowed, False
        self._last_exception = None
        # A document the caller handed over open stays open: the facade worked on
        # it in place and closing it would take it from under them.
        if document is not None and not borrowed:
            try:
                document.dispose()
            except PDF_OPERATION_ERRORS as exc:
                logger.warning(
                    "%s: dispose failed for the bound document: %s", self._what, exc
                )

    def _ensure_not_disposed(self) -> None:
        if self._disposed:
            raise AsposePdfException("Object has been disposed")

    # -- failures ----------------------------------------------------------

    @property
    def last_exception(self) -> BaseException | None:
        """Exception from the last failed operation, or ``None`` if none."""
        return self._last_exception

    def _operation_start(self) -> None:
        self._last_exception = None

    def _operation_fail(self, exc: BaseException) -> bool:
        logger.warning(
            "%s operation failed (%s): %s", self._what, type(exc).__name__, exc
        )
        self._last_exception = exc
        return False

    # -- binding -----------------------------------------------------------

    @property
    def password(self) -> str | None:
        """Password used when binding an encrypted document."""
        return self._password

    @password.setter
    def password(self, value: str | None) -> None:
        self._password = value

    def bind_pdf(
        self,
        source: Any,
        password: str | None = None,
        *,
        limits: PdfLoadLimits | None = None,
    ) -> None:
        """Open *source* -- a path, bytes, a stream, or an open ``Document``.

        An open document is **borrowed**, not copied: the facade works on it in
        place and does not dispose of it. Anything else is opened here and closed
        when the facade is.
        """
        self._ensure_not_disposed()
        from aspose_pdf.document import Document

        self.dispose_bound()
        if isinstance(source, Document):
            self._document = source
            self._borrowed = True
            return
        pwd = self._password if password is None else password
        self._document = Document(source, password=pwd, limits=limits)
        self._borrowed = False

    def dispose_bound(self) -> None:
        """Release the document currently bound, if this facade opened it."""
        document, self._document = self._document, None
        borrowed, self._borrowed = self._borrowed, False
        if document is not None and not borrowed:
            try:
                document.dispose()
            except PDF_OPERATION_ERRORS as exc:
                logger.warning("%s: dispose failed while rebinding: %s", self._what, exc)

    @property
    def document(self) -> Any:
        """The bound document, for anything the facade does not wrap itself."""
        self._ensure_not_disposed()
        if self._document is None:
            raise AsposePdfException("No document is bound; call bind_pdf first")
        return self._document

    @property
    def is_bound(self) -> bool:
        """Whether a document is bound."""
        return not self._disposed and self._document is not None

    def save(self, destination: Any = None) -> bool:
        """Write the bound document to *destination*; ``True`` on success."""
        self._ensure_not_disposed()
        self._operation_start()
        try:
            self.document.save(destination)
            return True
        except PDF_OPERATION_ERRORS as exc:
            return self._operation_fail(exc)

    # -- page numbers ------------------------------------------------------

    def _page_index(self, page_number: int) -> int:
        """A 1-based page number as the 0-based index everything else uses."""
        count = len(self.document.pages)
        try:
            number = int(page_number)
        except (TypeError, ValueError):
            raise AsposePdfException("A page number must be a whole number") from None
        if number < 1 or number > count:
            raise AsposePdfException(
                f"Page number {number} is outside this document's {count} pages"
            )
        return number - 1

    def _page_indices(self, page_numbers: Any) -> list[int]:
        """The 0-based indices *page_numbers* names, or every page for ``None``."""
        if page_numbers is None:
            return list(range(len(self.document.pages)))
        if isinstance(page_numbers, int) and not isinstance(page_numbers, bool):
            page_numbers = [page_numbers]
        return [self._page_index(number) for number in page_numbers]


class PdfExtractor:
    """Simple PDF text and image extractor.

    The extractor stores extracted page texts in `_page_texts` and tracks the
    position of the next unread page with `_current_index`.
    """

    def __init__(self) -> None:
        self._page_texts: list[str] = []
        self._current_index: int = 0
        self._disposed: bool = False
        self._attachments: dict = {}
        self._images: list[Any] = []
        self._image_index: int = 0
        self._bound_pdf = None
        self._password: str | None = None

    def close(self) -> None:
        """Close the extractor and release resources."""
        self.dispose()

    def __enter__(self) -> PdfExtractor:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def dispose(self) -> None:
        """Mark the extractor as disposed."""
        if self._disposed:
            return
        self._disposed = True
        self._page_texts.clear()
        self._attachments.clear()
        self._images.clear()
        if self._bound_pdf:
            if hasattr(self._bound_pdf, "dispose"):
                self._bound_pdf.dispose()
        self._bound_pdf = None

    def _ensure_not_disposed(self) -> None:
        if self._disposed:
            raise AsposePdfException("Object has been disposed")

    @property
    def password(self) -> str | None:
        """Optional owner/user password used when binding encrypted PDFs (maps .NET ``Password``)."""
        return self._password

    @password.setter
    def password(self, value: str | None) -> None:
        self._password = value

    def bind_pdf(
        self,
        source: str | Path | bytes,
        password: str | None = None,
        *,
        limits: PdfLoadLimits | None = None,
    ) -> None:
        """Bind to a PDF source for extraction.

        For encrypted documents, pass ``password`` or set :attr:`PdfExtractor.password` first.
        An explicit ``password`` argument overrides the property for this call only.
        Use ``limits`` to constrain loading and subsequent text extraction.
        """
        self._ensure_not_disposed()
        from aspose_pdf.engine.simple_pdf import SimplePdf

        pwd = self._password if password is None else password
        load_kwargs = {} if limits is None else {"limits": limits}
        if isinstance(source, (str, Path)):
            self._bound_pdf = SimplePdf.from_file(source, pwd, **load_kwargs)
        else:
            self._bound_pdf = SimplePdf.from_bytes(source, pwd, **load_kwargs)

    def extract_text(self) -> None:
        """Extract text from the bound document's pages.

        Each page is read through the engine's own extractor, which is where
        page content is decoded on demand and a page that cannot be parsed
        is recovered from. Repeating the parse here instead meant a document
        whose content is loaded lazily extracted nothing at all, and a page
        with a damaged content stream lost text the engine reads.
        """
        self._ensure_not_disposed()
        self._page_texts.clear()
        self._current_index = 0
        if self._bound_pdf is None:
            return
        self._page_texts.extend(
            self._bound_pdf.extract_page_text(index)
            for index in range(len(self._bound_pdf.pages))
        )

    def get_text(self) -> str:
        """Return all extracted text concatenated."""
        self._ensure_not_disposed()
        return "\n".join(self._page_texts)

    def get_next_page_text(self) -> str:
        """Return text for the next page, advancing cursor."""
        self._ensure_not_disposed()
        if self._current_index >= len(self._page_texts):
            raise StopIteration("No more page text")
        text = self._page_texts[self._current_index]
        self._current_index += 1
        return text

    def has_next_page_text(self) -> bool:
        """Return True if there is another page's text available."""
        self._ensure_not_disposed()
        return self._current_index < len(self._page_texts)

    def extract_image(self) -> None:
        """Collect the bound document's images, each as a real image file.

        What :meth:`get_next_image` hands back used to be the decoded samples
        -- the pixels, without the header that says how wide they are or what
        they mean -- so writing them to a ``.png`` produced a file nothing
        opens. Each image is now reconstructed the way ``save_image`` writes
        it (PNG for raster codecs, the original JPEG/JPX payload where the
        file holds one).
        """
        self._ensure_not_disposed()
        self._images.clear()
        self._image_index = 0
        if self._bound_pdf is None:
            return
        images = getattr(self._bound_pdf, "images", {})
        to_file = getattr(self._bound_pdf, "image_file", None)
        for name, data in images.items():
            if to_file is None:
                self._images.append((name, data, ""))
                continue
            try:
                file_bytes, produced = to_file(name)
            except PdfResourceLimitException:
                raise
            except PDF_OPERATION_ERRORS:
                file_bytes, produced = data, ""
            self._images.append((name, file_bytes, produced))

    def has_next_image(self) -> bool:
        """Return True if there is another image available."""
        self._ensure_not_disposed()
        return self._image_index < len(self._images)

    def get_next_image(self, destination: Any = None) -> Any:
        """The next image as an image file, advancing the cursor.

        Returns its bytes, or ``None`` once they are all read. With
        *destination* -- a path or a writable binary stream -- the file is
        written there too; a path's suffix is corrected to the format actually
        produced, and the path written is returned.
        """
        self._ensure_not_disposed()
        if self._image_index >= len(self._images):
            return None
        _name, file_bytes, produced = self._images[self._image_index]
        self._image_index += 1
        if destination is None:
            return file_bytes
        if hasattr(destination, "write"):
            destination.write(file_bytes)
            return file_bytes
        from aspose_pdf.engine.file_output import write_file_atomically
        from aspose_pdf.engine.image_export import resolve_output_path

        out_path = resolve_output_path(destination, produced)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        write_file_atomically(out_path, file_bytes)
        return out_path

    def get_next_image_name(self) -> str | None:
        """The name of the image :meth:`get_next_image` will hand back next."""
        self._ensure_not_disposed()
        if self._image_index >= len(self._images):
            return None
        return self._images[self._image_index][0]

    def extract_attachment(self) -> None:
        """Extract attachments from bound PDF."""
        self._ensure_not_disposed()
        self._attachments.clear()
        if self._bound_pdf is None:
            return
        attachments = getattr(self._bound_pdf, "attachments", {})
        self._attachments.update(attachments)

    def get_attachment(self, name: str) -> Any:
        """Return attached file by name."""
        self._ensure_not_disposed()
        if name not in self._attachments:
            raise AsposePdfException(f"Attachment '{name}' not found")
        return self._attachments[name]

    def get_attach_names(self) -> list[str]:
        """Return list of attachment names."""
        self._ensure_not_disposed()
        return list(self._attachments.keys())


class PdfFileEditor:
    """Facade for PDF file editing operations."""

    def __init__(self) -> None:
        self._disposed: bool = False
        self._pages: list[Any] = []
        self._last_exception: BaseException | None = None

    @property
    def last_exception(self) -> BaseException | None:
        """Exception from the last failed operation, or ``None`` if none."""
        return self._last_exception

    def _operation_start(self) -> None:
        self._last_exception = None

    def _operation_fail(self, exc: BaseException) -> bool:
        # Surface boolean API failures in default logs (WARNING+).
        logger.warning(
            "PdfFileEditor operation failed (%s): %s",
            type(exc).__name__,
            exc,
        )
        self._last_exception = exc
        return False

    def close(self) -> None:
        """Close the editor and release resources."""
        self.dispose()

    def __enter__(self) -> PdfFileEditor:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def dispose(self) -> None:
        """Mark the editor as disposed."""
        if self._disposed:
            return
        self._disposed = True
        self._pages.clear()
        self._last_exception = None

    def _ensure_not_disposed(self) -> None:
        if self._disposed:
            raise AsposePdfException("Object has been disposed")

    def concatenate(self, inputs: list[str], output: str) -> bool:
        """Concatenate multiple PDF files into one.

        Args:
            inputs: List of input file paths
            output: Output file path

        Returns:
            True on success, False on failure
        """
        self._ensure_not_disposed()
        self._operation_start()
        docs: list[Any] = []
        result: Any = None
        try:
            from aspose_pdf.engine.simple_pdf import SimplePdf

            for inp in inputs:
                docs.append(SimplePdf.from_file(inp))

            result = SimplePdf.merge(*docs)
            result.save(output)
            return True
        except PDF_OPERATION_ERRORS as exc:
            return self._operation_fail(exc)
        finally:
            for d in docs:
                try:
                    d.dispose()
                except PDF_OPERATION_ERRORS as exc:
                    logger.warning(
                        "PdfFileEditor: dispose failed during concatenate cleanup: %s",
                        exc,
                    )
            if result is not None:
                try:
                    result.dispose()
                except PDF_OPERATION_ERRORS as exc:
                    logger.warning(
                        "PdfFileEditor: dispose failed for merged result: %s",
                        exc,
                    )

    def make_n_up(
        self,
        source: str,
        destination: str,
        rows: int = 2,
        columns: int = 2,
        *,
        page_size: Any = None,
        margin: float = 0.0,
        gutter: float = 0.0,
        order: str = "row",
    ) -> bool:
        """Impose *source* as ``rows x columns`` pages per sheet into *destination*.

        See :meth:`aspose_pdf.Document.n_up`, which this wraps with file I/O.

        Returns
        -------
        bool
            ``True`` on success, ``False`` on failure (and
            :attr:`last_exception` says why).
        """
        return self._impose(
            source,
            destination,
            lambda document: document.n_up(
                rows,
                columns,
                page_size=page_size,
                margin=margin,
                gutter=gutter,
                order=order,
            ),
        )

    def make_booklet(
        self,
        source: str,
        destination: str,
        *,
        page_size: Any = None,
        margin: float = 0.0,
        gutter: float = 0.0,
    ) -> bool:
        """Impose *source* as a saddle-stitched booklet into *destination*.

        See :meth:`aspose_pdf.Document.booklet`, which this wraps with file I/O.
        """
        return self._impose(
            source,
            destination,
            lambda document: document.booklet(
                page_size=page_size, margin=margin, gutter=gutter
            ),
        )

    def _impose(self, source: str, destination: str, impose: Any) -> bool:
        """Open *source*, impose it, write *destination*; dispose of both either way."""
        self._ensure_not_disposed()
        self._operation_start()
        from aspose_pdf.document import Document

        document = None
        imposed = None
        try:
            document = Document(source)
            imposed = impose(document)
            imposed.save(destination)
            return True
        except PDF_OPERATION_ERRORS as exc:
            return self._operation_fail(exc)
        finally:
            for opened in (imposed, document):
                if opened is None:
                    continue
                try:
                    opened.dispose()
                except PDF_OPERATION_ERRORS as exc:
                    logger.warning(
                        "PdfFileEditor: dispose failed after imposing: %s", exc
                    )

    def extract(
        self,
        source: str,
        destination: str,
        page_from: int | None = None,
        page_to: int | None = None,
    ) -> bool:
        """Extract pages from source to destination.

        Args:
            source: Source file path
            destination: Output file path
            page_from: Start page (1-based)
            page_to: End page (1-based, inclusive)

        Returns:
            True on success, False on failure
        """
        self._ensure_not_disposed()
        self._operation_start()
        doc: Any = None
        result: Any = None
        try:
            from aspose_pdf.engine.simple_pdf import SimplePdf

            doc = SimplePdf.from_file(source)

            start = 1
            end = len(doc.pages)
            if page_from is not None:
                start = page_from
            if page_to is not None:
                end = page_to

            if start < 1 or end > len(doc.pages) or start > end:
                return self._operation_fail(
                    AsposePdfException(
                        f"Invalid page range: pages {start}-{end} "
                        f"(document has {len(doc.pages)} page(s))"
                    )
                )

            indices = list(range(start - 1, end))
            result = doc.extract_pages(indices)
            result.save(destination)
            return True
        except PDF_OPERATION_ERRORS as exc:
            return self._operation_fail(exc)
        finally:
            if result is not None:
                try:
                    result.dispose()
                except PDF_OPERATION_ERRORS as exc:
                    logger.warning(
                        "PdfFileEditor: dispose failed for extract result: %s",
                        exc,
                    )
            if doc is not None:
                try:
                    doc.dispose()
                except PDF_OPERATION_ERRORS as exc:
                    logger.warning(
                        "PdfFileEditor: dispose failed for extract source: %s",
                        exc,
                    )

    def insert(
        self, source: str, insert_file: str, destination: str, position: int
    ) -> bool:
        """Insert pages from one PDF into another.

        Args:
            source: Source file path (base PDF)
            insert_file: File containing pages to insert
            destination: Output file path
            position: Where to insert (1-based page number)

        Returns:
            True on success, False on failure
        """
        self._ensure_not_disposed()
        self._operation_start()
        base: Any = None
        to_insert: Any = None
        try:
            from aspose_pdf.engine.simple_pdf import SimplePdf

            base = SimplePdf.from_file(source)
            to_insert = SimplePdf.from_file(insert_file)

            pos = position - 1
            if pos < 0:
                pos = 0
            if pos > len(base.pages):
                pos = len(base.pages)

            # The same page import the rest of the library uses. Passing the
            # rectangles and the content bytes -- which is what this did --
            # carried a page's *drawing* and nothing it drew with: no
            # /Resources, so every font and image the content named resolved to
            # nothing, and the annotations and form fields were dropped.
            base.append(to_insert, at=pos)
            base.save(destination)
            return True
        except PDF_OPERATION_ERRORS as exc:
            return self._operation_fail(exc)
        finally:
            if to_insert is not None:
                try:
                    to_insert.dispose()
                except PDF_OPERATION_ERRORS as exc:
                    logger.warning(
                        "PdfFileEditor: dispose failed for insert payload: %s",
                        exc,
                    )
            if base is not None:
                try:
                    base.dispose()
                except PDF_OPERATION_ERRORS as exc:
                    logger.warning(
                        "PdfFileEditor: dispose failed for insert base: %s",
                        exc,
                    )

    def delete(
        self,
        source: str,
        destination: str,
        pages_to_delete: Any = None,
        page_to: int | None = None,
        page_from: int | None = None,
    ) -> bool:
        """Delete specified pages from a PDF.

        Args:
            source: Source file path
            destination: Output file path
            pages_to_delete: List of page numbers OR Start page (int)
            page_to: End page (int) - used if pages_to_delete is start page
            page_from: Start page (int) - used if keyword arguments

        Returns:
            True on success, False on failure
        """
        self._ensure_not_disposed()
        self._operation_start()
        doc: Any = None
        try:
            from aspose_pdf.engine.simple_pdf import SimplePdf

            doc = SimplePdf.from_file(source)

            indices = []

            # Case 1: pages_to_delete is a list
            if isinstance(pages_to_delete, list):
                indices = sorted([p - 1 for p in pages_to_delete], reverse=True)

            # Case 2: pages_to_delete is int (Start Page), page_to (End Page) passed positionally
            elif isinstance(pages_to_delete, int):
                start = pages_to_delete
                end = page_to if page_to is not None else start
                indices = sorted([p - 1 for p in range(start, end + 1)], reverse=True)

            # Case 3: Keyword arguments page_from / page_to
            elif page_from is not None and page_to is not None:
                indices = sorted(
                    [p - 1 for p in range(page_from, page_to + 1)], reverse=True
                )

            for idx in indices:
                if 0 <= idx < len(doc.pages):
                    doc.delete_pages(idx, 1)

            doc.save(destination)
            return True
        except PDF_OPERATION_ERRORS as exc:
            return self._operation_fail(exc)
        finally:
            if doc is not None:
                try:
                    doc.dispose()
                except PDF_OPERATION_ERRORS as exc:
                    logger.warning(
                        "PdfFileEditor: dispose failed for delete source: %s",
                        exc,
                    )

    def append(self, source: str, append_source: str, destination: str) -> bool:
        """Append pages from one PDF to another.

        Args:
            source: Base file path
            append_source: File to append
            destination: Output file path

        Returns:
            True on success, False on failure
        """
        self._ensure_not_disposed()
        return self.concatenate([source, append_source], destination)

    def add_page_break(self, input_path: str, output_path: str) -> bool:
        """Add a blank page to the PDF.

        Args:
            input_path: Source file path
            output_path: Output file path

        Returns:
            True on success, False on failure
        """
        self._ensure_not_disposed()
        self._operation_start()
        pdf: Any = None
        try:
            from aspose_pdf.engine.simple_pdf import SimplePdf

            pdf = SimplePdf.from_file(input_path)
            pdf.add_page_break()
            pdf.save(output_path)
            return True
        except PDF_OPERATION_ERRORS as exc:
            return self._operation_fail(exc)
        finally:
            if pdf is not None:
                try:
                    pdf.dispose()
                except PDF_OPERATION_ERRORS as exc:
                    logger.warning(
                        "PdfFileEditor: dispose failed after add_page_break: %s",
                        exc,
                    )


class PdfFileInfo(_BoundFacade):
    """The document information dictionary, and the geometry of its pages.

    The ``/Info`` entries ISO 32000-1 14.3.3 names are properties; anything else
    the dictionary holds is reachable with :meth:`get_meta_info` and
    :meth:`set_meta_info`. Page numbers are 1-based.

        info = PdfFileInfo()
        info.bind_pdf("report.pdf")
        info.title = "Quarterly report"
        info.save("report.pdf")
    """

    _what = "PdfFileInfo"

    #: The ``/Info`` keys this facade names, by property.
    _KEYS: ClassVar[dict[str, str]] = {
        "title": "Title",
        "author": "Author",
        "subject": "Subject",
        "keywords": "Keywords",
        "creator": "Creator",
        "producer": "Producer",
        "creation_date": "CreationDate",
        "mod_date": "ModDate",
    }

    def get_meta_info(self, name: str, default: str | None = None) -> str | None:
        """One ``/Info`` entry by its own key, or *default*."""
        return self.document.info.get(str(name), default)

    def set_meta_info(self, name: str, value: str | None) -> None:
        """Set one ``/Info`` entry; ``None`` removes it."""
        info = dict(self.document.info)
        if value is None:
            info.pop(str(name), None)
        else:
            info[str(name)] = str(value)
        self.document.info = info

    def clear_meta_info(self) -> None:
        """Remove every ``/Info`` entry."""
        self.document.info = {}

    @property
    def meta_info(self) -> dict[str, str]:
        """Every ``/Info`` entry, as a plain dictionary."""
        return dict(self.document.info)

    @property
    def number_of_pages(self) -> int:
        """How many pages the document has."""
        return len(self.document.pages)

    @property
    def is_encrypted(self) -> bool:
        """Whether the document is still locked -- see ``Document.is_encrypted``."""
        return bool(self.document.is_encrypted)

    #: ``PdfFileInfo.IsPasswordProtected`` in the ported API, same answer.
    is_password_protected = is_encrypted

    @property
    def pdf_version(self) -> str:
        """The version in the file's header, e.g. ``"1.7"``."""
        return self.document.version

    def get_page_width(self, page_number: int) -> float:
        """The width of a page in points, from its media box."""
        return self.document.pages[self._page_index(page_number)].size.width

    def get_page_height(self, page_number: int) -> float:
        """The height of a page in points, from its media box."""
        return self.document.pages[self._page_index(page_number)].size.height

    def get_page_rotation(self, page_number: int) -> int:
        """A page's clockwise rotation in degrees."""
        return self.document.pages[self._page_index(page_number)].rotation

    def get_page_box(self, page_number: int, boundary: str = "MediaBox"):
        """One of a page's five boxes, as ``(x0, y0, x1, y1)``."""
        return self.document.pages[self._page_index(page_number)].get_box(boundary)


def _info_property(key: str) -> property:
    """A property over one ``/Info`` entry."""

    def getter(self: PdfFileInfo) -> str | None:
        return self.document.info.get(key)

    def setter(self: PdfFileInfo, value: str | None) -> None:
        self.set_meta_info(key, value)

    return property(getter, setter, doc=f"The document's ``/{key}`` entry.")


for _name, _key in PdfFileInfo._KEYS.items():
    setattr(PdfFileInfo, _name, _info_property(_key))
del _name, _key


class PdfFileSecurity(_BoundFacade):
    """Passwords and permissions on the bound document.

    Each operation changes the document and answers ``True`` or ``False``;
    :meth:`save` writes the result::

        security = PdfFileSecurity()
        security.bind_pdf("report.pdf")
        security.encrypt_file("user", "owner")
        security.save("protected.pdf")
    """

    _what = "PdfFileSecurity"

    def encrypt_file(
        self,
        user_password: str = "",
        owner_password: str | None = None,
        *,
        permissions: int | Permissions = -4,
        algorithm: str = "AES-256",
    ) -> bool:
        """Apply the standard security handler. See ``Document.encrypt``.

        *permissions* takes a raw ``/P`` or a
        :class:`~aspose_pdf.permissions.Permissions` built by name.
        """
        self._ensure_not_disposed()
        self._operation_start()
        try:
            self.document.encrypt(
                user_password,
                owner_password,
                permissions=permissions,
                algorithm=algorithm,
            )
            return True
        except PDF_OPERATION_ERRORS as exc:
            return self._operation_fail(exc)

    def decrypt_file(self, password: str | None = None) -> bool:
        """Take the protection off, given a password the document accepts.

        Unconditional, as the low-code ``Decryptor`` is and for the same reason:
        ``Document.is_encrypted`` is about being *locked*, and a document opened
        with its password is not.
        """
        self._ensure_not_disposed()
        self._operation_start()
        try:
            self.document.decrypt(
                self._password if password is None else password
            )
            return True
        except PDF_OPERATION_ERRORS as exc:
            return self._operation_fail(exc)

    def change_password(
        self,
        old_password: str,
        new_user_password: str = "",
        new_owner_password: str | None = None,
        *,
        permissions: int | Permissions | None = None,
        algorithm: str | None = None,
    ) -> bool:
        """Replace the passwords, given one that opens the document now.

        *permissions*, when given, takes a raw ``/P`` or a
        :class:`~aspose_pdf.permissions.Permissions`; ``None`` keeps the ones
        the document has.
        """
        self._ensure_not_disposed()
        self._operation_start()
        try:
            extra: dict[str, Any] = {}
            if permissions is not None:
                extra["permissions"] = permissions
            if algorithm is not None:
                extra["algorithm"] = algorithm
            self.document.change_passwords(
                old_password, new_user_password, new_owner_password, **extra
            )
            return True
        except PDF_OPERATION_ERRORS as exc:
            return self._operation_fail(exc)

    @property
    def permissions(self) -> Permissions:
        """What a reader may do with the bound document (Table 22).

        A :class:`~aspose_pdf.permissions.Permissions`, so
        ``security.permissions.can_copy`` answers directly; it is still the
        ``int`` the raw ``/P`` was.
        """
        return self.document.permissions


class PdfBookmarkEditor(_BoundFacade):
    """The document's outline, as the ported API edits it.

    Page numbers are 1-based; :class:`~aspose_pdf.outlines.OutlineItem` is what
    the entries are, so a bookmark's colour, open state and target are available
    on the items themselves.
    """

    _what = "PdfBookmarkEditor"

    def extract_bookmarks(self, *, nested: bool = True) -> list[Any]:
        """Every top-level bookmark, with its children (``nested=False`` flattens)."""
        items = list(self.document.outlines)
        if nested:
            return items

        flat: list[Any] = []

        def walk(entries: list[Any]) -> None:
            for entry in entries:
                flat.append(entry)
                walk(entry.children)

        walk(items)
        return flat

    def create_bookmark_of_page(
        self, title: str, page_number: int, **options: Any
    ) -> Any:
        """Add a top-level bookmark pointing at one page, and return it."""
        from aspose_pdf.outlines import OutlineItem

        index = self._page_index(page_number)
        return self.document.outlines.add(OutlineItem(title, index, **options))

    def create_bookmarks(self, titles: Any) -> list[Any]:
        """Add a bookmark per ``(title, page_number)`` pair, in order."""
        created = []
        for title, page_number in titles:
            created.append(self.create_bookmark_of_page(title, page_number))
        return created

    def delete_bookmarks(self, title: str | None = None) -> int:
        """Delete every bookmark, or the top-level ones titled *title*.

        Returns how many were removed.
        """
        outlines = self.document.outlines
        if title is None:
            removed = len(outlines)
            outlines.clear()
            return removed
        doomed = [item for item in outlines if item.title == title]
        for item in doomed:
            outlines.remove(item)
        return len(doomed)


class PdfAnnotationEditor(_BoundFacade):
    """Reading, deleting and flattening annotations across pages.

    Page numbers are 1-based. The annotations themselves are the typed classes of
    :mod:`aspose_pdf.annotations`, so each one's own entries are available on it.
    """

    _what = "PdfAnnotationEditor"

    def extract_annotations(
        self,
        page_numbers: Any = None,
        subtypes: Any = None,
    ) -> list[Any]:
        """Every annotation on the pages named, optionally of certain subtypes."""
        wanted = None
        if subtypes is not None:
            wanted = {str(subtype).lstrip("/") for subtype in subtypes}
        found: list[Any] = []
        for index in self._page_indices(page_numbers):
            for annotation in self.document.pages[index].annotations:
                if wanted is None or annotation.subtype in wanted:
                    found.append(annotation)
        return found

    def delete_annotations(
        self,
        subtype: Any = None,
        page_numbers: Any = None,
    ) -> int:
        """Delete annotations on the pages named; ``subtype`` narrows to one kind.

        Returns how many were deleted.
        """
        wanted = None if subtype is None else str(subtype).lstrip("/")
        deleted = 0
        for index in self._page_indices(page_numbers):
            annotations = self.document.pages[index].annotations
            if wanted is None:
                deleted += len(annotations)
                annotations.clear()
                continue
            # Last first, so an index is never shifted before it is used.
            for position in range(len(annotations) - 1, -1, -1):
                if annotations[position].subtype == wanted:
                    annotations.delete(position)
                    deleted += 1
        return deleted

    def generate_appearances(self, *, force: bool = False) -> int:
        """Synthesise the missing ``/AP /N`` appearances. See ``Document``."""
        return self.document.generate_appearances(force=force)

    def flatten_annotations(self) -> bool:
        """Bake annotations -- and form fields -- into the page content."""
        self._ensure_not_disposed()
        self._operation_start()
        try:
            self.document.flatten()
            return True
        except PDF_OPERATION_ERRORS as exc:
            return self._operation_fail(exc)


class PdfFileStamp(_BoundFacade):
    """Stamps, running heads and page numbers on the bound document.

    A header or a footer is a text stamp aligned to the top or the bottom of the
    page, which is what one is; :meth:`add_stamp` takes any
    :class:`~aspose_pdf.stamps.Stamp`, a :class:`~aspose_pdf.stamps.PageStamp`
    included, so a letterhead goes on through here too. Page numbers are 1-based.
    """

    _what = "PdfFileStamp"

    def add_stamp(self, stamp: Any, page_numbers: Any = None) -> bool:
        """Put *stamp* on the pages named, or on every page."""
        self._ensure_not_disposed()
        self._operation_start()
        try:
            self.document.add_stamp(stamp, self._page_indices(page_numbers))
            return True
        except PDF_OPERATION_ERRORS as exc:
            return self._operation_fail(exc)

    def add_header(
        self,
        text: str,
        *,
        font_size: float = 10.0,
        margin: float = 18.0,
        page_numbers: Any = None,
        **options: Any,
    ) -> bool:
        """Put *text* along the top of the pages named."""
        return self._running_text(
            text, "Top", font_size, margin, page_numbers, options
        )

    def add_footer(
        self,
        text: str,
        *,
        font_size: float = 10.0,
        margin: float = 18.0,
        page_numbers: Any = None,
        **options: Any,
    ) -> bool:
        """Put *text* along the bottom of the pages named."""
        return self._running_text(
            text, "Bottom", font_size, margin, page_numbers, options
        )

    def add_page_number(
        self,
        template: str = "{page}",
        *,
        starting_number: int = 1,
        font_size: float = 10.0,
        margin: float = 18.0,
        page_numbers: Any = None,
        **options: Any,
    ) -> bool:
        """Number the pages named; *template* takes ``{page}`` and ``{total}``."""
        from aspose_pdf.stamps import PageNumberStamp

        indent = -float(margin) if str(options.get("vertical_alignment", "Bottom")) == "Top" else float(margin)
        stamp = PageNumberStamp(
            value=template,
            starting_number=starting_number,
            font_size=font_size,
            vertical_alignment=options.pop("vertical_alignment", "Bottom"),
            y_indent=options.pop("y_indent", indent),
            **options,
        )
        return self.add_stamp(stamp, page_numbers)

    def _running_text(
        self,
        text: str,
        edge: str,
        font_size: float,
        margin: float,
        page_numbers: Any,
        options: dict[str, Any],
    ) -> bool:
        """A header or a footer: a text stamp pinned to one edge of the page."""
        from aspose_pdf.stamps import TextStamp

        # The indent is measured from the edge the stamp is aligned to, so a
        # header moves *down* from the top and a footer *up* from the bottom.
        y_indent = options.pop("y_indent", -float(margin) if edge == "Top" else float(margin))
        stamp = TextStamp(
            value=text,
            font_size=font_size,
            vertical_alignment=edge,
            y_indent=y_indent,
            **options,
        )
        return self.add_stamp(stamp, page_numbers)


class PdfPageEditor(_BoundFacade):
    """Turning, resizing, moving and removing pages. Page numbers are 1-based."""

    _what = "PdfPageEditor"

    def rotate(
        self,
        degrees: int,
        page_numbers: Any = None,
        *,
        relative: bool = True,
    ) -> bool:
        """Turn the pages named by *degrees*, added to what each already says."""
        self._ensure_not_disposed()
        self._operation_start()
        try:
            for index in self._page_indices(page_numbers):
                page = self.document.pages[index]
                page.rotation = page.rotation + degrees if relative else degrees
            return True
        except PDF_OPERATION_ERRORS as exc:
            return self._operation_fail(exc)

    def resize(self, page_size: Any, page_numbers: Any = None) -> bool:
        """Give the pages named a new size, keeping each box's origin."""
        self._ensure_not_disposed()
        self._operation_start()
        try:
            for index in self._page_indices(page_numbers):
                self.document.pages[index].size = page_size
            return True
        except PDF_OPERATION_ERRORS as exc:
            return self._operation_fail(exc)

    def crop(self, rect: Any, page_numbers: Any = None) -> bool:
        """Set the crop box of the pages named to ``(x0, y0, x1, y1)``."""
        self._ensure_not_disposed()
        self._operation_start()
        try:
            for index in self._page_indices(page_numbers):
                self.document.pages[index].crop_box = rect
            return True
        except PDF_OPERATION_ERRORS as exc:
            return self._operation_fail(exc)

    def move_page(self, page_number: int, new_position: int) -> bool:
        """Move a page to *new_position* (1-based), shifting the rest along."""
        self._ensure_not_disposed()
        self._operation_start()
        try:
            pages = self.document.pages
            index = self._page_index(page_number)
            count = len(pages)
            target = int(new_position)
            if target < 1 or target > count:
                raise AsposePdfException(
                    f"Position {target} is outside this document's {count} pages"
                )
            destination = target - 1
            if destination == index:
                return True
            # Copied in, then the original dropped: the copy carries the page's
            # resources, boxes, rotation and annotations with it. Moving a page
            # *forward* inserts one place beyond the destination, because dropping
            # the original then shifts everything after it back by one -- insert at
            # the destination itself and the page lands one short of where it was
            # asked for.
            if destination > index:
                pages.insert(destination + 1, pages[index])
                pages.delete(index)
            else:
                pages.insert(destination, pages[index])
                pages.delete(index + 1)
            return True
        except PDF_OPERATION_ERRORS as exc:
            return self._operation_fail(exc)

    def delete_pages(self, page_numbers: Any) -> bool:
        """Delete the pages named."""
        self._ensure_not_disposed()
        self._operation_start()
        try:
            indices = sorted(set(self._page_indices(page_numbers)), reverse=True)
            if len(indices) >= len(self.document.pages):
                raise AsposePdfException(
                    "Removing every page would leave no document behind"
                )
            for index in indices:
                self.document.pages.delete(index)
            return True
        except PDF_OPERATION_ERRORS as exc:
            return self._operation_fail(exc)


class PdfXmpMetadata(_BoundFacade):
    """The document's XMP packet, read and written by prefix and name.

    ``aspose_pdf.xmp`` is the full data model; this is the two operations ported
    code uses most::

        xmp = PdfXmpMetadata()
        xmp.bind_pdf("report.pdf")
        xmp.set_value("dc", "title", "Quarterly report")
        xmp.save("report.pdf")
    """

    _what = "PdfXmpMetadata"

    @property
    def packet(self) -> Any:
        """The :class:`~aspose_pdf.xmp.XmpPacket` itself."""
        return self.document.xmp_metadata

    def get_value(self, prefix: str, name: str, default: Any = None) -> Any:
        """One XMP property's value, by namespace prefix (or URI) and name.

        A **language alternative** -- which is what ``dc:title`` and
        ``dc:description`` are -- reads as its default text rather than as the
        array it is written as, and a plain array as a list of its values. The
        field itself, qualifiers and all, is one line away through
        :attr:`packet`.
        """
        packet = self.packet
        field = packet.get(prefix, name)
        if field is None:
            return default
        text = packet.get_localized_text(prefix, name)
        if text is not None:
            return text
        values = packet.get_array(prefix, name)
        if values is not None:
            return values
        return getattr(field, "value", field)

    def set_value(self, prefix: str, name: str, value: Any, *, uri: str = "") -> None:
        """Set one XMP property, and write the packet back to the document."""
        packet = self.packet
        packet.set_value(prefix, name, value, uri=uri)
        self.document.xmp_metadata = packet

    def sync_from_info(self) -> None:
        """Copy the ``/Info`` entries into the XMP packet (``Document.sync_metadata``)."""
        self.document.sync_metadata(direction="info_to_xmp")

    def sync_to_info(self) -> None:
        """Copy the XMP packet's values into the ``/Info`` dictionary."""
        self.document.sync_metadata(direction="xmp_to_info")


class PdfConverter(_BoundFacade):
    """Rendering the bound document's pages, one image at a time.

    The ported shape: :meth:`do_convert` renders, then :meth:`has_next_image` and
    :meth:`get_next_image` walk the results. Page numbers are 1-based.
    """

    _what = "PdfConverter"

    def __init__(self) -> None:
        super().__init__()
        self._images: list[bytes] = []
        self._image_index = 0
        self._dpi: float = 150.0

    @property
    def resolution(self) -> float:
        """The resolution pages are rendered at, in dots per inch."""
        return self._dpi

    @resolution.setter
    def resolution(self, value: float) -> None:
        dpi = float(value)
        if dpi <= 0:
            raise AsposePdfException("A resolution must be above zero")
        self._dpi = dpi

    def do_convert(
        self,
        page_numbers: Any = None,
        *,
        image_format: str = "png",
        quality: int = 85,
        mode: str = "rgb",
    ) -> bool:
        """Render the pages named; ``get_next_image`` then hands them over."""
        self._ensure_not_disposed()
        self._operation_start()
        try:
            self._images = []
            self._image_index = 0
            kind = str(image_format).strip().lower().lstrip(".")
            for index in self._page_indices(page_numbers):
                raster = self.document.render_page(index, dpi=self._dpi)
                if kind in ("jpg", "jpeg"):
                    self._images.append(raster.to_jpeg(quality=quality, mode=mode))
                elif kind in ("tif", "tiff"):
                    self._images.append(raster.to_tiff(mode=mode))
                elif kind == "png":
                    self._images.append(raster.to_png(mode=mode))
                else:
                    raise AsposePdfException(
                        f"Unsupported image format {image_format!r}; use png, "
                        "jpeg or tiff"
                    )
            return True
        except PDF_OPERATION_ERRORS as exc:
            self._images = []
            return self._operation_fail(exc)

    def has_next_image(self) -> bool:
        """Whether another rendered page is waiting."""
        self._ensure_not_disposed()
        return self._image_index < len(self._images)

    def get_next_image(self, destination: Any = None) -> Any:
        """The next rendered page's bytes, written to *destination* if given."""
        self._ensure_not_disposed()
        if not self.has_next_image():
            return None
        data = self._images[self._image_index]
        self._image_index += 1
        if destination is None:
            return data
        if hasattr(destination, "write"):
            destination.write(data)
            return data
        from aspose_pdf.engine.file_output import write_file_atomically

        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_file_atomically(path, data)
        return path

    def save_as_tiff(self, destination: Any, page_numbers: Any = None, **options: Any) -> bool:
        """Render the pages named into one multi-page TIFF file."""
        self._ensure_not_disposed()
        self._operation_start()
        try:
            self.document.save_as_tiff(
                destination,
                pages=self._page_indices(page_numbers),
                dpi=self._dpi,
                **options,
            )
            return True
        except PDF_OPERATION_ERRORS as exc:
            return self._operation_fail(exc)

    def dispose(self) -> None:
        """Release the rendered images along with the document."""
        self._images = []
        self._image_index = 0
        super().dispose()
