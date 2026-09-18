"""Typed read model for document-level file attachments (embedded files)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

__all__ = ["AF_RELATIONSHIPS", "FileSpecification"]

#: Valid ``/AFRelationship`` values for an associated file (ISO 32000-2 Table 45).
AF_RELATIONSHIPS: frozenset[str] = frozenset(
    {
        "Source",
        "Data",
        "Alternative",
        "Supplement",
        "EncryptedPayload",
        "FormData",
        "Schema",
        "Unspecified",
    }
)


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
    mime_type: str | None = None
    description: str | None = None
    creation_date: datetime | None = None
    mod_date: datetime | None = None
    relationship: str | None = None
    """The associated-file relationship (``/AFRelationship``), e.g. ``"Data"`` or
    ``"Source"``. ``None`` when the attachment uses the default ``Unspecified``."""

    def __post_init__(self) -> None:
        if not isinstance(self.name, str):
            raise TypeError("name must be a string")
        if not isinstance(self.contents, (bytes, bytearray)):
            raise TypeError("contents must be bytes or bytearray")
        object.__setattr__(self, "contents", bytes(self.contents))

    @property
    def size(self) -> int:
        """The size of :attr:`contents` in bytes."""
        return len(self.contents)

    def save(self, path: str | Path) -> None:
        """Write the decoded attachment bytes to *path*, replacing it in one step."""
        from aspose_pdf.engine.file_output import write_file_atomically

        write_file_atomically(path, self.contents)
