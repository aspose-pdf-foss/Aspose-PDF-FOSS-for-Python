"""Facade layer for PDF extraction and editing operations.

This module provides `PdfExtractor` and `PdfFileEditor` classes for PDF manipulation.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, List, Optional, Union

from aspose_pdf.exceptions import AsposePdfException, PDF_OPERATION_ERRORS

logger = logging.getLogger(__name__)


class PdfExtractor:
    """Simple PDF text and image extractor.

    The extractor stores extracted page texts in `_page_texts` and tracks the
    position of the next unread page with `_current_index`.
    """

    def __init__(self) -> None:
        self._page_texts: List[str] = []
        self._current_index: int = 0
        self._disposed: bool = False
        self._attachments: dict = {}
        self._images: List[Any] = []
        self._image_index: int = 0
        self._bound_pdf = None
        self._password: Optional[str] = None

    def close(self) -> None:
        """Close the extractor and release resources."""
        self.dispose()

    def __enter__(self) -> "PdfExtractor":
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
    def password(self) -> Optional[str]:
        """Optional owner/user password used when binding encrypted PDFs (maps .NET ``Password``)."""
        return self._password

    @password.setter
    def password(self, value: Optional[str]) -> None:
        self._password = value

    def bind_pdf(
        self, source: Union[str, Path, bytes], password: Optional[str] = None
    ) -> None:
        """Bind to a PDF source for extraction.

        For encrypted documents, pass ``password`` or set :attr:`PdfExtractor.password` first.
        An explicit ``password`` argument overrides the property for this call only.
        """
        self._ensure_not_disposed()
        from aspose_pdf.engine.simple_pdf import SimplePdf

        pwd = self._password if password is None else password
        if isinstance(source, (str, Path)):
            self._bound_pdf = SimplePdf.from_file(source, pwd)
        else:
            self._bound_pdf = SimplePdf.from_bytes(source, pwd)

    def extract_text(self) -> None:
        """Extract text from bound PDF pages."""
        self._ensure_not_disposed()
        self._page_texts.clear()
        self._current_index = 0
        if self._bound_pdf is None:
            return

        from aspose_pdf.engine.content_stream_parser import ContentStreamParser

        # SimplePdf holds page contents and potentially a reference to the COS doc for resources
        # We need to get resources for each page
        for i, stream in enumerate(getattr(self._bound_pdf, "page_contents", [])):
            # Try to get resources from COS doc if available
            resources = {}
            if hasattr(self._bound_pdf, "_get_page_resources"):
                resources = self._bound_pdf._get_page_resources(i)

            parser = ContentStreamParser(stream, resources)
            text = parser.extract_text()
            self._page_texts.append(text)

    def _parse_text_from_content(self, data: bytes) -> str:
        """Deprecated: use ContentStreamParser instead."""
        from aspose_pdf.engine.content_stream_parser import ContentStreamParser

        parser = ContentStreamParser(data, {})
        return parser.extract_text()

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
        """Extract images from bound PDF."""
        self._ensure_not_disposed()
        self._images.clear()
        self._image_index = 0
        if self._bound_pdf is None:
            return
        images = getattr(self._bound_pdf, "images", {})
        for name, data in images.items():
            self._images.append((name, data))

    def has_next_image(self) -> bool:
        """Return True if there is another image available."""
        self._ensure_not_disposed()
        return self._image_index < len(self._images)

    def get_next_image(self) -> Any:
        """Return the next image, advancing cursor."""
        self._ensure_not_disposed()
        if self._image_index >= len(self._images):
            return None
        img = self._images[self._image_index]
        self._image_index += 1
        return img[1]

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

    def get_attach_names(self) -> List[str]:
        """Return list of attachment names."""
        self._ensure_not_disposed()
        return list(self._attachments.keys())


class PdfFileEditor:
    """Facade for PDF file editing operations."""

    def __init__(self) -> None:
        self._disposed: bool = False
        self._pages: List[Any] = []
        self._last_exception: Optional[BaseException] = None

    @property
    def last_exception(self) -> Optional[BaseException]:
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

    def __enter__(self) -> "PdfFileEditor":
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

    def concatenate(self, inputs: List[str], output: str) -> bool:
        """Concatenate multiple PDF files into one.

        Args:
            inputs: List of input file paths
            output: Output file path

        Returns:
            True on success, False on failure
        """
        self._ensure_not_disposed()
        self._operation_start()
        docs: List[Any] = []
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

    def extract(
        self,
        source: str,
        destination: str,
        page_from: Optional[int] = None,
        page_to: Optional[int] = None,
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
                        f"Invalid page range: pages {start}–{end} "
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

            base.insert_pages(pos, to_insert.pages, to_insert.page_contents)
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
        page_to: Optional[int] = None,
        page_from: Optional[int] = None,
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
