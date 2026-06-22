from __future__ import annotations

import struct
import zlib
from typing import Tuple, Type

__all__ = (
    "AsposePdfException",
    "PdfException",
    "PdfParseException",
    "InvalidPdfFileFormatException",
    "PdfValidationException",
    "PdfSecurityException",
    "IncorrectCMapUsageException",
    "InvalidPasswordException",
    "InvalidValueFormatException",
    "FontEmbeddingException",
    "PdfIOException",
    "DeprecatedFeatureException",
    "PDF_OPERATION_ERRORS",
    "CONTENT_PARSER_RECOVERABLE",
    "PDF_STREAM_DECODE_ERRORS",
)


class AsposePdfException(Exception):
    """Base class for all aspose_pdf exceptions."""

    pass


class PdfException(AsposePdfException):
    """Base class for PDF-related exceptions."""

    pass


class PdfParseException(PdfException):
    """Raised when there is an error parsing a PDF document."""

    pass


class InvalidPdfFileFormatException(PdfParseException):
    """Raised when the PDF file format is invalid or corrupted."""

    pass


class IncorrectCMapUsageException(AsposePdfException):
    """Raised when there is an incorrect usage of CMap."""

    pass


class PdfValidationException(PdfException):
    """Raised when a PDF document fails validation or compliance checks."""

    pass


class PdfSecurityException(PdfException):
    """Raised when there is an encryption, signature, or permissions error."""

    pass


class InvalidPasswordException(PdfSecurityException):
    """Raised when an incorrect password is provided for an encrypted document."""

    pass


class InvalidValueFormatException(AsposePdfException):
    """Raised when an invalid value is encountered during parsing or conversion."""

    pass


class FontEmbeddingException(AsposePdfException):
    """Raised when there is an error embedding fonts in the PDF."""

    pass


class PdfIOException(PdfException):
    """Raised when there is an I/O error during PDF processing."""

    pass


class DeprecatedFeatureException(AsposePdfException):
    """Raised when a deprecated PDF feature is used that is not allowed in newer PDF versions."""

    pass


# ---------------------------------------------------------------------------
# Narrow exception groups: avoid ``except Exception`` so internal
# bugs and unexpected failures propagate while I/O and parse errors are handled.
# ---------------------------------------------------------------------------

PDF_OPERATION_ERRORS: Tuple[Type[BaseException], ...] = (
    OSError,
    EOFError,
    MemoryError,
    ValueError,
    TypeError,
    KeyError,
    IndexError,
    struct.error,
    zlib.error,
    UnicodeDecodeError,
    UnicodeError,
    AsposePdfException,
    FontEmbeddingException,
)

CONTENT_PARSER_RECOVERABLE: Tuple[Type[BaseException], ...] = (
    ValueError,
    TypeError,
    KeyError,
    IndexError,
    AsposePdfException,
)

PDF_STREAM_DECODE_ERRORS: Tuple[Type[BaseException], ...] = PDF_OPERATION_ERRORS + (
    RuntimeError,
)
