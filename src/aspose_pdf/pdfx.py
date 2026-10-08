"""PDF/X validation options, results, and the standards this library knows.

PDF/X (ISO 15930) is the print-exchange branch of the PDF standards: where
PDF/A is about a file still being readable in fifty years and PDF/UA about it
being usable by everyone, PDF/X is about a file printing the same way wherever
it is sent. A conforming file carries everything the press needs -- every font
embedded, one output intent naming the printing condition the colour was
prepared for, a stated trim size, a stated trapping status -- and nothing whose
result would depend on the reader.

Results are **heuristic**, as for PDF/A and PDF/UA, and with one difference
worth stating plainly: veraPDF validates PDF/A and PDF/UA but **not** PDF/X,
and no other free validator does either, so these checks have not been
cross-checked against a reference implementation the way the PDF/A ones have.
Treat them as signals, and preflight with a prepress tool before committing a
job to print.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import BinaryIO

from aspose_pdf.engine.conformance import PDFX_STANDARDS, normalize_pdfx_standard
from aspose_pdf.exceptions import PdfIOException, PdfValidationException
from aspose_pdf.load_limits import (
    PdfLoadLimits,
    _coerce_limits,
    _LoadBudget,
    _read_limited,
)

__all__ = [
    "PdfXStandard",
    "PdfXValidateOptions",
    "PdfXValidationResult",
    "PdfXValidator",
    "normalize_pdfx_standard",
]


class PdfXStandard(str, Enum):  # noqa: UP042
    """The PDF/X conformance levels this library validates and writes.

    The members are the canonical identifiers, so a value can be used wherever
    a standard name is taken and prints as itself. Two things about the family
    are worth knowing from the names alone: the conformance level and the ISO
    part are different numbers (``PDF/X-1a:2003`` is ISO 15930-**4**, and
    ``PDF/X-3:2003`` is ISO 15930-**6**), and the 2001 and 2003 editions of
    PDF/X-1a identify themselves with different strings, so they are separate
    members rather than one.

    The external-profile and later variants (PDF/X-4p, PDF/X-5, PDF/X-6) and
    PDF/X-2 are not implemented; see ``supported-features.md``.
    """

    X1A_2001 = "PDF/X-1a:2001"
    X1A_2003 = "PDF/X-1a:2003"
    X3_2002 = "PDF/X-3:2002"
    X3_2003 = "PDF/X-3:2003"
    X4 = "PDF/X-4"

    @staticmethod
    def by_name(value: str | PdfXStandard) -> PdfXStandard:
        """Resolve a spelling of a standard's name to a member.

        Accepts the canonical form, the same without ``PDF/``, and forms
        without punctuation or case (``"x-4"``, ``"X1a"``). A part named
        without a year gives its newest edition, which is what a caller asking
        for "PDF/X-1a" means.
        """
        return PdfXStandard(normalize_pdfx_standard(value))

    @property
    def iso_part(self) -> int:
        """The part of ISO 15930 that defines this level."""
        return PDFX_STANDARDS[self.value].iso_part

    @property
    def allows_transparency(self) -> bool:
        """Whether live transparency is permitted (PDF/X-4 only)."""
        return PDFX_STANDARDS[self.value].allows_transparency


class PdfXValidationResult:
    """Detailed result of a PDF/X validation run (heuristic).

    Attributes
    ----------
    errors : List[str]
        Rule violations that keep the document from conforming to the standard
        that was checked.
    warnings : List[str]
        Advisory findings: an annotation lying over the trimmed area, or a
        PDF/X-4 metadata entry whose normative strength this library does not
        claim to settle.
    standard : str
        The canonical name of the level that was checked, e.g. ``"PDF/X-4"``.
    is_heuristic : bool
        Always ``True`` here. See :attr:`HEURISTIC_VALIDATION_NOTICE`.
    is_valid : bool
        ``True`` when *errors* is empty.
    """

    HEURISTIC_VALIDATION_NOTICE: str = (
        "PDF/X checks in this library are heuristic: they inspect the object "
        "graph for the rules ISO 15930 states about structure, colour, fonts, "
        "boxes and metadata. No free validator covers PDF/X -- veraPDF does "
        "not -- so these rules are not cross-checked against a reference "
        "implementation. Preflight with a prepress tool before printing."
    )

    def __init__(
        self,
        errors: list[str] | None = None,
        warnings: list[str] | None = None,
        standard: str = "",
        *,
        is_heuristic: bool = True,
    ) -> None:
        self.errors: list[str] = list(errors) if errors else []
        self.warnings: list[str] = list(warnings) if warnings else []
        self.standard: str = standard
        self.is_heuristic: bool = is_heuristic

    @property
    def is_valid(self) -> bool:
        """Return ``True`` when there are no validation errors."""
        return len(self.errors) == 0

    def add_error(self, error: str) -> None:
        """Append a conformance error."""
        if not isinstance(error, str):
            raise TypeError("error must be a string")
        self.errors.append(error)

    def add_warning(self, warning: str) -> None:
        """Append an advisory finding."""
        if not isinstance(warning, str):
            raise TypeError("warning must be a string")
        self.warnings.append(warning)

    def to_dict(self) -> dict:
        """Return a plain-dict representation of the result."""
        return {
            "is_valid": self.is_valid,
            "is_heuristic": self.is_heuristic,
            "standard": self.standard,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }

    def __len__(self) -> int:
        """The number of errors, so ``len(result)`` reads as PDF/A's does."""
        return len(self.errors)

    def __repr__(self) -> str:
        return (
            f"PdfXValidationResult(is_valid={self.is_valid!r}, "
            f"is_heuristic={self.is_heuristic!r}, "
            f"standard={self.standard!r}, errors={self.errors!r}, "
            f"warnings={self.warnings!r})"
        )


class PdfXValidateOptions:
    """Container for batch PDF/X validation settings.

    Mirrors :class:`aspose_pdf.pdfa.PdfAValidateOptions`: collect one or more
    inputs with :meth:`add_input`, name the standard in :attr:`pdfx_standard`,
    then hand the options to :class:`PdfXValidator`.
    """

    def __init__(self, limits: PdfLoadLimits | None = None) -> None:
        self._inputs: list[Path | bytes] = []
        self.limits = _coerce_limits(limits)
        self.pdfx_standard: str = PdfXStandard.X4.value

    def add_input(
        self, source: str | Path | bytes | bytearray | BinaryIO
    ) -> PdfXValidateOptions:
        """Add an input file or stream for PDF/X validation.

        Parameters
        ----------
        source:
            A path (str or Path), raw bytes, or a binary file-like object.

        Returns
        -------
        PdfXValidateOptions
            Self, for method chaining.
        """
        budget = _LoadBudget(self.limits)
        if isinstance(source, (str, Path)):
            path = Path(source)
            if not path.is_file():
                raise PdfIOException(f"Input file does not exist: {path}")
            budget.check_input(path.stat().st_size)
            self._inputs.append(path)
            return self

        if isinstance(source, bytes):
            budget.check_input(len(source))
            self._inputs.append(source)
            return self

        if isinstance(source, bytearray):
            budget.check_input(len(source))
            self._inputs.append(bytes(source))
            return self

        if hasattr(source, "read"):
            try:
                data = _read_limited(source, budget)
            except TypeError as exc:
                raise PdfValidationException(
                    "Binary stream did not return bytes"
                ) from exc
            self._inputs.append(data)
            return self

        raise PdfValidationException("Unsupported input type for add_input")

    @property
    def inputs(self) -> list[Path | bytes]:
        """Return a copy of the stored inputs."""
        return list(self._inputs)


class PdfXValidator:
    """Plugin that runs heuristic PDF/X validation on one or more inputs.

    Usage::

        options = PdfXValidateOptions()
        options.pdfx_standard = "PDF/X-4"
        options.add_input("/path/to/artwork.pdf")

        validator = PdfXValidator()
        for result in validator.process(options):
            print(result.is_valid, result.errors)
    """

    def process(self, options: PdfXValidateOptions) -> list[PdfXValidationResult]:
        """Validate every input defined in *options*.

        Parameters
        ----------
        options : PdfXValidateOptions
            Validation configuration, including one or more inputs added via
            :meth:`PdfXValidateOptions.add_input`.

        Returns
        -------
        List[PdfXValidationResult]
            One result per input, in the order the inputs were added.
        """
        from aspose_pdf.document import Document  # local import to avoid circularity

        results: list[PdfXValidationResult] = []
        for inp in options.inputs:
            with Document(limits=options.limits) as doc:
                doc.load_from(inp, limits=options.limits)
                results.append(doc.validate_pdfx(options.pdfx_standard))
        return results
