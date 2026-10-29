"""Lazy page-text extraction and incremental plain-text output."""

from __future__ import annotations

import codecs
import io
from collections.abc import Generator, Iterable, Sequence
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

from aspose_pdf.exceptions import AsposePdfException, PdfValidationException
from aspose_pdf.load_limits import _LoadBudget

from .file_output import write_file_chunks_atomically
from .stream_output import write_all
from .text_region import validate_clip, validate_exclude_regions
from .text_selection import page_indexes, validate_skip_artifacts

if TYPE_CHECKING:
    from aspose_pdf.document import Document

_CHUNK_CHARS = 16384


def iter_text_pages(
    document: Document,
    *,
    pages: Iterable[int] | None,
    clip: Sequence[float] | None,
    skip_artifacts: bool,
    exclude_regions: Iterable[Sequence[float]] | None,
) -> Generator[str, None, None]:
    """Validate and snapshot options now; extract text only when advanced."""
    document._ensure_not_disposed()
    pdf = document._engine_pdf
    if pdf is None:
        raise AsposePdfException("No document loaded")
    count = document.page_count
    indexes = page_indexes(pages, count, limits=document.load_limits)
    selection = range(count) if indexes is None else indexes
    budget = _LoadBudget(document.load_limits)
    budget.check(len(selection), "max_container_items", "text page selection")
    rectangle = None if clip is None else validate_clip(clip)
    skip = validate_skip_artifacts(skip_artifacts)
    exclusions = validate_exclude_regions(exclude_regions, limits=document.load_limits)

    def iterate() -> Generator[str, None, None]:
        total = 0
        for index in selection:
            document._ensure_not_disposed()
            if document._engine_pdf is not pdf or len(pdf.pages) != count:
                raise AsposePdfException(
                    "Source document changed during text iteration"
                )
            text = pdf.extract_page_text(
                index, clip=rectangle, skip_artifacts=skip, exclude_regions=exclusions
            )
            budget.check(len(text), "max_decoded_stream_bytes", "extracted page text")
            size = 0
            try:
                for offset in range(0, len(text), _CHUNK_CHARS):
                    size += len(text[offset : offset + _CHUNK_CHARS].encode("utf-8"))
                    budget.check(
                        size, "max_decoded_stream_bytes", "extracted page text"
                    )
                    budget.check(
                        total + size, "max_total_decoded_bytes", "extracted text"
                    )
            except UnicodeError as exc:
                raise PdfValidationException(
                    "Extracted text contains invalid Unicode"
                ) from exc
            total += size
            yield text
            del text

    return iterate()


def save_as_text(
    document: Document,
    destination: str | Path | BinaryIO,
    *,
    pages: Iterable[int] | None,
    clip: Sequence[float] | None,
    skip_artifacts: bool,
    exclude_regions: Iterable[Sequence[float]] | None,
    page_separator: str,
    encoding: str,
    overwrite: bool,
) -> int:
    """Stream encoded text directly to a caller stream or an atomic path writer."""
    document._ensure_not_disposed()
    if not isinstance(overwrite, bool):
        raise PdfValidationException("overwrite must be a boolean")
    if not isinstance(page_separator, str):
        raise TypeError("page_separator must be a string")
    if not isinstance(encoding, str):
        raise TypeError("encoding must be a string")
    try:
        codec = codecs.lookup(encoding)
        if codec.name in {"idna", "punycode"}:
            raise ValueError("Domain-name codecs do not support streaming TXT output")
        "".encode(encoding)
        encoder = codecs.getincrementalencoder(encoding)(errors="strict")
    except (LookupError, UnicodeError, ValueError, TypeError) as exc:
        raise PdfValidationException(
            "encoding must name a supported streaming text encoding"
        ) from exc
    budget = _LoadBudget(document.load_limits)
    budget.check(len(page_separator), "max_object_bytes", "text page separator")
    try:
        separator_size = 0
        check_encoder = codecs.getincrementalencoder(encoding)(errors="strict")
        for offset in range(0, len(page_separator), _CHUNK_CHARS):
            separator_size += len(
                check_encoder.encode(page_separator[offset : offset + _CHUNK_CHARS])
            )
            budget.check(
                separator_size, "max_object_bytes", "encoded text page separator"
            )
        separator_size += len(check_encoder.encode("", final=True))
        budget.check(separator_size, "max_object_bytes", "encoded text page separator")
    except UnicodeError as exc:
        raise PdfValidationException("page_separator cannot be encoded") from exc
    if isinstance(destination, io.TextIOBase):
        raise TypeError("destination must be a path or writable binary stream")
    is_stream = callable(getattr(destination, "write", None))
    target = None if is_stream else Path(destination)
    if target is not None and target.exists() and not overwrite:
        raise FileExistsError(f"File already exists: {target}")
    texts = iter_text_pages(
        document,
        pages=pages,
        clip=clip,
        skip_artifacts=skip_artifacts,
        exclude_regions=exclude_regions,
    )

    def encoded() -> Generator[bytes, None, None]:
        total = 0

        def encode(text: str, *, final: bool = False) -> bytes:
            nonlocal total
            data = encoder.encode(text, final=final)
            total += len(data)
            budget.check(total, "max_total_decoded_bytes", "TXT output bytes")
            return data

        try:
            first = True
            for text in texts:
                if not first:
                    for offset in range(0, len(page_separator), _CHUNK_CHARS):
                        yield encode(page_separator[offset : offset + _CHUNK_CHARS])
                first = False
                size = 0
                for offset in range(0, len(text), _CHUNK_CHARS):
                    data = encode(text[offset : offset + _CHUNK_CHARS])
                    size += len(data)
                    budget.check(size, "max_decoded_stream_bytes", "encoded page text")
                    yield data
                del text
            yield encode("", final=True)
        except UnicodeError as exc:
            raise PdfValidationException(
                f"Text cannot be encoded using {encoding!r}"
            ) from exc

    with closing(texts), closing(encoded()) as chunks:
        if target is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            return write_file_chunks_atomically(target, chunks)
        total = 0
        for chunk in chunks:
            write_all(destination, chunk)
            total += len(chunk)
        return total
