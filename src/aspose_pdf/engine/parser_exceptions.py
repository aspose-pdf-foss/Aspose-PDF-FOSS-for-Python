"""PDF parser exception hierarchy and warnings collector.

Provides:
- :class:`PdfParseError` – base exception with error message, byte offset and recoverability flag.
- :class:`PdfMalformedError` – recoverable malformed PDF structures.
- :class:`PdfCorruptedError` – unrecoverable PDF corruption.
- :class:`PdfParseWarning` – non‑fatal parsing issue.
- :class:`ParseWarnings` – container for :class:`PdfParseWarning` instances.

Example
-------
>>> from aspose_pdf.engine.parser_exceptions import PdfMalformedError, ParseWarnings
>>> warnings = ParseWarnings()
>>> warnings.add("Missing /Root dictionary", offset=1024)
>>> try:
...     raise PdfMalformedError("Invalid page object", offset=2048)
... except PdfParseError as exc:
...     print(exc.message, exc.offset, exc.recoverable)
Invalid page object 2048 True
>>> bool(warnings)
True
>>> len(warnings)
1
"""

from typing import List


class PdfParseError(Exception):
    """Base exception for PDF parsing errors.

    Attributes:
        message: Error description.
        offset: Byte offset where error occurred (if known).
        recoverable: Whether parsing can continue.
    """

    def __init__(
        self, message: str, offset: int = -1, recoverable: bool = False
    ) -> None:
        super().__init__(message)
        self.message: str = message
        self.offset: int = offset
        self.recoverable: bool = recoverable


class PdfMalformedError(PdfParseError):
    """Recoverable malformed PDF structure.

    The parser may attempt to continue with defaults or recovery.
    """

    def __init__(self, message: str, offset: int = -1) -> None:
        super().__init__(message, offset, recoverable=True)


class PdfCorruptedError(PdfParseError):
    """Unrecoverable PDF corruption.

    The PDF is too damaged to parse.
    """

    def __init__(self, message: str, offset: int = -1) -> None:
        super().__init__(message, offset, recoverable=False)


class PdfSecurityError(PdfParseError):
    """Encryption or permission related error."""

    pass


class PdfEncodingError(PdfParseError):
    """Font or content stream encoding error."""

    pass


class PdfValidationError(PdfParseError):
    """PDF/A or general structural validation error."""

    pass


class PdfParseWarning:
    """Non-fatal parsing issue that was recovered from."""

    def __init__(self, message: str, offset: int = -1) -> None:
        self.message: str = message
        self.offset: int = offset

    def __str__(self) -> str:
        if self.offset >= 0:
            return f"{self.message} (at offset {self.offset})"
        return self.message


class ParseWarnings:
    """Collects warnings during parsing."""

    def __init__(self) -> None:
        self._warnings: List[PdfParseWarning] = []

    def add(self, message: str, offset: int = -1) -> None:
        """Add a new warning.

        Args:
            message: Description of the warning.
            offset: Byte offset where the warning occurred.
        """
        self._warnings.append(PdfParseWarning(message, offset))

    def clear(self) -> None:
        """Remove all collected warnings."""
        self._warnings.clear()

    @property
    def warnings(self) -> List[PdfParseWarning]:
        """Return a shallow copy of the warnings list."""
        return list(self._warnings)

    def __len__(self) -> int:
        return len(self._warnings)

    def __bool__(self) -> bool:
        return bool(self._warnings)
