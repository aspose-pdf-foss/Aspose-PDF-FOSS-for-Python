"""PDF/A validation options and results.

This module provides PdfAValidateOptions, PdfAValidationResult, PdfAConversionResult, and
PdfAConverter, PdfAConvertOptions, PdfAStandardVersion, PdfFormat for
PdfAValidator for the full PDF/A validation pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO, List, Optional, Union
from aspose_pdf.exceptions import PdfIOException, PdfValidationException


class PdfAValidationResult:

    """Detailed result of a PDF/A validation run.

    Attributes
    ----------
    errors : List[str]
        Compliance violations that prevent the document from being PDF/A
        conformant.
    warnings : List[str]
        Non-blocking issues that are noteworthy but do not invalidate
        PDF/A conformance.
    level : str
        The PDF/A conformance level that was checked (e.g. ``"1b"``).
    is_heuristic : bool
        When ``True`` (default for this library), checks are **heuristic**
        signals only — not equivalent to a full validator (e.g. veraPDF).
        Callers doing compliance automation should **not** treat
        ``is_valid`` as certification.
    is_valid : bool
        ``True`` when *errors* is empty.
    """

    #: Shown in docs; same semantics as :attr:`is_heuristic`.
    HEURISTIC_VALIDATION_NOTICE: str = (
        "PDF/A validation in this library is heuristic: suitable for signals, "
        "not certification-grade conformance."
    )

    def __init__(
        self,
        errors: List[str] | None = None,
        warnings: List[str] | None = None,
        level: str = "",
        *,
        is_heuristic: bool = True,
    ) -> None:
        self.errors: List[str] = list(errors) if errors else []
        self.warnings: List[str] = list(warnings) if warnings else []
        self.level: str = level
        self.is_heuristic: bool = is_heuristic

    @property
    def is_valid(self) -> bool:
        """Return ``True`` when there are no validation errors."""
        return len(self.errors) == 0

    def add_error(self, error: str) -> None:
        """Append a compliance error.

        Parameters
        ----------
        error : str
            Human-readable description of the violation.

        Raises
        ------
        TypeError
            If *error* is not a string.
        """
        if not isinstance(error, str):
            raise TypeError("error must be a string")
        self.errors.append(error)

    def add_warning(self, warning: str) -> None:
        """Append a non-blocking warning.

        Parameters
        ----------
        warning : str
            Human-readable description of the warning.

        Raises
        ------
        TypeError
            If *warning* is not a string.
        """
        if not isinstance(warning, str):
            raise TypeError("warning must be a string")
        self.warnings.append(warning)

    def to_dict(self) -> dict:
        """Return a plain-dict representation of the result."""
        return {
            "is_valid": self.is_valid,
            "is_heuristic": self.is_heuristic,
            "level": self.level,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }

    def __len__(self) -> int:
        """Return the number of errors (enables ``len(result)`` for backward compat)."""
        return len(self.errors)

    def __repr__(self) -> str:
        return (
            f"PdfAValidationResult(is_valid={self.is_valid!r}, "
            f"is_heuristic={self.is_heuristic!r}, "
            f"level={self.level!r}, errors={self.errors!r}, "
            f"warnings={self.warnings!r})"
        )


class PdfAValidateOptions:
    """Container for PDF/A validation settings."""

    def __init__(self) -> None:
        self._inputs: List[Union[Path, bytes]] = []
        self.pdfa_version: str = "PDF/A-1b"
        self.optimize_file_size: bool = False
        self.is_low_memory_mode: bool = False
        self.font_lookup_directory: Optional[Path] = None

    def add_input(
        self, source: Union[str, Path, bytes, bytearray, BinaryIO]
    ) -> "PdfAValidateOptions":
        """Add an input file or stream for PDF/A validation.

        Parameters
        ----------
        source:
            A path (str or Path), raw bytes, or a binary file-like object.

        Returns
        -------
        PdfAValidateOptions
            Self for method chaining.
        """
        if isinstance(source, (str, Path)):
            path = Path(source)
            if not path.is_file():
                raise PdfIOException(f"Input file does not exist: {path}")
            self._inputs.append(path)
            return self

        if isinstance(source, (bytes, bytearray)):
            self._inputs.append(bytes(source))
            return self

        if hasattr(source, "read"):
            data = source.read()
            if isinstance(data, str):
                data = data.encode()
            if not isinstance(data, (bytes, bytearray)):
                raise PdfValidationException("Binary stream did not return bytes")
            self._inputs.append(bytes(data))
            return self

        raise PdfValidationException("Unsupported input type for add_input")

    @property
    def inputs(self) -> List[Union[Path, bytes]]:
        """Return a copy of the stored inputs."""
        return list(self._inputs)


class PdfAValidator:
    """Plugin that runs PDF/A validation on one or more inputs.

    Results use **heuristic** checks (see :attr:`PdfAValidationResult.is_heuristic`):
    useful for automated *signals*, not certification-grade PDF/A conformance.

    Usage::

        options = PdfAValidateOptions()
        options.pdfa_version = "PDF/A-1b"
        options.add_input("/path/to/document.pdf")

        validator = PdfAValidator()
        results = validator.process(options)
        for result in results:
            print(result.is_valid, result.errors)
    """

    @staticmethod
    def _normalize_level(version: str) -> str:
        """Convert a version string to the short level expected by the engine.

        Examples
        --------
        ``"PDF/A-1b"`` → ``"1b"``,  ``"2B"`` → ``"2b"``
        """
        v = version.strip()
        if v.upper().startswith("PDF/A-"):
            v = v[6:]
        return v.lower()

    def process(self, options: "PdfAValidateOptions") -> List[PdfAValidationResult]:
        """Validate every input defined in *options*.

        Parameters
        ----------
        options : PdfAValidateOptions
            Validation configuration, including one or more inputs added via
            :meth:`PdfAValidateOptions.add_input`.

        Returns
        -------
        List[PdfAValidationResult]
            One result per input, in the same order as the inputs were added.
        """
        from aspose_pdf.document import Document  # local import to avoid circularity

        level = self._normalize_level(options.pdfa_version)
        results: List[PdfAValidationResult] = []

        for inp in options.inputs:
            doc = Document()
            if isinstance(inp, Path):
                doc.load_from(str(inp))
            else:
                doc.load_from(inp)
            result = doc.validate_pdfa(level)
            results.append(result)

        return results


class PdfAConversionResult:
    """Result of a PDF/A conversion operation.

    Attributes
    ----------
    errors : List[str]
        List of compliance issues that could not be fixed automatically.
    is_valid : bool
        True when conversion was successful and the document is PDF/A compliant.
    level : str
        The PDF/A conformance level that was targeted (e.g. ``"1b"``).
    """

    def __init__(
        self,
        errors: List[str] | None = None,
        level: str = "",
    ) -> None:
        self.errors: List[str] = list(errors) if errors else []
        self.level: str = level

    @property
    def is_valid(self) -> bool:
        """Return ``True`` when there are no conversion errors."""
        return len(self.errors) == 0

    def add_error(self, error: str) -> None:
        """Append a compliance error.

        Parameters
        ----------
        error : str
            Human-readable description of the issue.

        Raises
        ------
        TypeError
            If *error* is not a string.
        """
