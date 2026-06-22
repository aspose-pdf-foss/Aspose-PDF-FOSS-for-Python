"""PDF/UA validation result types.

PDF/UA (universal accessibility) conformance requires a tagged structure tree,
logical reading order, language, alternate descriptions, and much more than
this library inspects.  Results are **heuristic** only.
"""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO, List, Union

from aspose_pdf.exceptions import PdfIOException, PdfValidationException


class PdfUaValidationResult:
    """Detailed result of a PDF/UA structure check (heuristic).

    Attributes
    ----------
    errors : List[str]
        Structural issues that indicate the document is not PDF/UA-ready
        under the checks performed here.
    warnings : List[str]
        Non-blocking hints (e.g. missing recommended ``/Lang``).
    is_heuristic : bool
        When ``True`` (default), checks are **signals only** — not equivalent
        to PDF/UA certification or a full validator (e.g. veraPDF).
        Callers must not treat ``is_valid`` as proof of accessibility.
    is_valid : bool
        ``True`` when *errors* is empty.
    """

    HEURISTIC_VALIDATION_NOTICE: str = (
        "PDF/UA checks in this library are heuristic: they inspect catalog "
        "structure (tagging, MarkInfo) only — not real accessibility or "
        "PDF/UA-1 conformance certification."
    )

    def __init__(
        self,
        errors: List[str] | None = None,
        warnings: List[str] | None = None,
        *,
        is_heuristic: bool = True,
    ) -> None:
        self.errors: List[str] = list(errors) if errors else []
        self.warnings: List[str] = list(warnings) if warnings else []
        self.is_heuristic: bool = is_heuristic

    @property
    def is_valid(self) -> bool:
        """Return ``True`` when there are no validation errors."""
        return len(self.errors) == 0

    def add_error(self, error: str) -> None:
        if not isinstance(error, str):
            raise TypeError("error must be a string")
        self.errors.append(error)

    def add_warning(self, warning: str) -> None:
        if not isinstance(warning, str):
            raise TypeError("warning must be a string")
        self.warnings.append(warning)

    def to_dict(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "is_heuristic": self.is_heuristic,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }

    def __len__(self) -> int:
        return len(self.errors)

    def __repr__(self) -> str:
        return (
            f"PdfUaValidationResult(is_valid={self.is_valid!r}, "
            f"is_heuristic={self.is_heuristic!r}, "
            f"errors={self.errors!r}, warnings={self.warnings!r})"
        )


class PdfUaValidateOptions:
    """Container for batch PDF/UA validation settings.

    Mirrors :class:`aspose_pdf.pdfa.PdfAValidateOptions`: collect one or more
    inputs with :meth:`add_input`, then hand the options to
    :class:`PdfUaValidator`.
    """

    def __init__(self) -> None:
        self._inputs: List[Union[Path, bytes]] = []

    def add_input(
        self, source: Union[str, Path, bytes, bytearray, BinaryIO]
    ) -> "PdfUaValidateOptions":
        """Add an input file or stream for PDF/UA validation.

        Parameters
        ----------
        source:
            A path (str or Path), raw bytes, or a binary file-like object.

        Returns
        -------
        PdfUaValidateOptions
            Self, for method chaining.
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


class PdfUaValidator:
    """Plugin that runs heuristic PDF/UA validation on one or more inputs.

    Results use **heuristic** catalog-level checks (see
    :attr:`PdfUaValidationResult.is_heuristic`): useful for automated
    *signals*, not certification-grade PDF/UA conformance.

    Usage::

        options = PdfUaValidateOptions()
        options.add_input("/path/to/document.pdf")

        validator = PdfUaValidator()
        results = validator.process(options)
        for result in results:
            print(result.is_valid, result.errors)
    """

    def process(self, options: "PdfUaValidateOptions") -> List[PdfUaValidationResult]:
        """Validate every input defined in *options*.

        Parameters
        ----------
        options : PdfUaValidateOptions
            Validation configuration, including one or more inputs added via
            :meth:`PdfUaValidateOptions.add_input`.

        Returns
        -------
        List[PdfUaValidationResult]
            One result per input, in the order the inputs were added.
        """
        from aspose_pdf.document import Document  # local import to avoid circularity

        results: List[PdfUaValidationResult] = []
        for inp in options.inputs:
            doc = Document()
            if isinstance(inp, Path):
                doc.load_from(str(inp))
            else:
                doc.load_from(inp)
            results.append(doc.validate_pdfua())
        return results
