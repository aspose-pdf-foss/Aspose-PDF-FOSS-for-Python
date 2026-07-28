from __future__ import annotations

import struct
import zlib

__all__ = (
    "CONTENT_PARSER_RECOVERABLE",
    "PDF_OPERATION_ERRORS",
    "PDF_STREAM_DECODE_ERRORS",
    "AsposePdfException",
    "DeprecatedFeatureException",
    "FontEmbeddingException",
    "IncorrectCMapUsageException",
    "InvalidPasswordException",
    "InvalidPdfFileFormatException",
    "InvalidValueFormatException",
    "PdfException",
    "PdfIOException",
    "PdfParseException",
    "PdfResourceLimitException",
    "PdfSecurityException",
    "PdfValidationException",
    "UnsupportedFeatureException",
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


class PdfResourceLimitException(PdfValidationException):
    """Raised when processing a PDF would exceed a configured resource limit."""

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


class UnsupportedFeatureException(AsposePdfException, NotImplementedError):
    """Raised when a compatibility surface names a feature this package lacks.

    The package exposes option and enumeration objects for formats it does not
    implement (HTML/SVG/CGM/OFD/CDR/Markdown/LaTeX conversion, PPTX export,
    printing). Passing one of them to a real operation raises this exception
    instead of silently doing nothing. It derives from both
    :class:`AsposePdfException` and :class:`NotImplementedError` so either
    handler catches it.
    """

    pass


# ---------------------------------------------------------------------------
# Narrow exception groups: avoid ``except Exception`` so internal
# bugs and unexpected failures propagate while I/O and parse errors are handled.
# ---------------------------------------------------------------------------

PDF_OPERATION_ERRORS: tuple[type[BaseException], ...] = (
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

CONTENT_PARSER_RECOVERABLE: tuple[type[BaseException], ...] = (
    ValueError,
    TypeError,
    KeyError,
    IndexError,
    AsposePdfException,
)

PDF_STREAM_DECODE_ERRORS: tuple[type[BaseException], ...] = (*PDF_OPERATION_ERRORS, RuntimeError)
