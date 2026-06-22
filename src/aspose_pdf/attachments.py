"""Typed read model for document-level file attachments (embedded files)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

__all__ = ["FileSpecification"]


@dataclass(frozen=True)
class FileSpecification:
    """A document-level embedded file (``/Filespec``) with typed metadata.

    Returned by :attr:`aspose_pdf.Document.embedded_files`. :attr:`contents` holds
    the decoded file bytes; the remaining fields surface the metadata stored with
    the embedded file. Any of them is ``None`` when the producer omitted it (or
    when the attachment was added without that metadata).
    """

    name: str
    contents: bytes
    mime_type: Optional[str] = None
    description: Optional[str] = None
    creation_date: Optional[datetime] = None
    mod_date: Optional[datetime] = None

    @property
    def size(self) -> int:
        """The size of :attr:`contents` in bytes."""
        return len(self.contents)

    def save(self, path: Union[str, Path]) -> None:
        """Write the decoded attachment bytes to *path*."""
        Path(path).write_bytes(self.contents)
