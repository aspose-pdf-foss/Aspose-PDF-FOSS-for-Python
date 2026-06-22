from __future__ import annotations

from typing import Any
from aspose_pdf.exceptions import PdfValidationException, PdfIOException
from pathlib import Path

"""Compatibility ``PdfAValidateOptions`` / ``PdfAValidationResult`` (Aspose.PDF subset)."""


class PdfAValidateOptions:
    """Options for a PDF/A validation run.

    Mirrors the Aspose.PDF ``PdfAValidateOptions`` API.
    """

    def __init__(self, *args, **kwargs) -> None:
        """Initialize the validation options."""
        # Store any provided arguments for later use.
        self._options: dict[str, Any] = {}
        # Positional arguments are stored with generic keys
        for idx, arg in enumerate(args):
            self._options[f"arg{idx}"] = arg
        # Keyword arguments are merged directly
        self._options.update(kwargs)
        # Initialize inputs collection
        self.inputs = []

    # Class-level attribute defaults.
    pdfa_version: Any = None  # maps_from=PdfAVersion
    optimize_file_size: Any = None  # maps_from=OptimizeFileSize
    is_low_memory_mode: Any = None  # maps_from=IsLowMemoryMode
    log_output_source: Any = None  # maps_from=LogOutputSource

    def add_input(self, *args, **kwargs):
        """Register an input source for validation."""
        # Validate first argument if present (assume it's path)
        val = None
        if args:
            val = args[0]
            if not isinstance(val, (str, bytes)) and not hasattr(val, "__fspath__"):
                raise PdfValidationException(f"Invalid input type: {type(val)}")

            # A string path that does not exist is rejected.
            if isinstance(val, str) and not Path(val).exists():
                raise PdfIOException(f"File not found: {val}")

        # A simple positional input is stored directly; complex inputs become
        # a dict entry below.
        if val is not None and not kwargs:
            self.inputs.append(val)
        else:
            # Fallback for complex inputs
            input_entry: dict[str, Any] = {}
            for idx, arg in enumerate(args):
                input_entry[f"arg{idx}"] = arg
            input_entry.update(kwargs)
            self.inputs.append(input_entry)

        return self

    def reset(self) -> None:
        """Clear all stored options and inputs, returning to a clean state."""
        self._options.clear()
        self.inputs.clear()

    def set_option(self, key: str, value: Any) -> None:
        """Set a single option value.

        Args:
            key: Option name.
            value: Option value.
        """
        self._options[key] = value

    def get_options(self) -> dict[str, Any]:
        """Return a shallow copy of the stored options dictionary."""
        return dict(self._options)

    def __repr__(self) -> str:
        return f"PdfAValidateOptions(options={self._options}, inputs={self.inputs})"


class PdfAValidationResult:
    """Result of a PDF/A validation run.

    Mirrors the Aspose.PDF ``PdfAValidationResult`` API.
    """

    def __init__(self, *args, **kwargs) -> None:
        # Store provided arguments for inspection
        self._data: dict[str, Any] = {}
        for idx, arg in enumerate(args):
            self._data[f"arg{idx}"] = arg
        self._data.update(kwargs)
        # Result attributes.
        self.is_valid: bool = self._data.get("is_valid", False)
        self.errors: list[str] = self._data.get("errors", [])

    def reset(self) -> None:
        """Reset the validation result to its default state.

        Clears any recorded errors and marks the result as valid.
        """
        self.errors.clear()
        self.is_valid = True
        # Preserve any additional data that may have been stored
        self._data.clear()

    def add_error(self, error: str) -> None:
        """Add an error message to the result and mark it as invalid.

        Args:
            error: Description of the validation error.
        """
        if not isinstance(error, str):
            raise TypeError("error must be a string")
        self.errors.append(error)
        self.is_valid = False
        # Keep a reference in the internal data dictionary for introspection
        self._data.setdefault("errors", []).append(error)

    def to_dict(self) -> dict[str, Any]:
        """Return a dictionary representation of the validation result.

        Includes the validity flag, error list, and any extra stored data.
        """
        result: dict[str, Any] = {
            "is_valid": self.is_valid,
            "errors": list(self.errors),
        }
        # Merge any additional data that does not duplicate the main fields
        for key, value in self._data.items():
            if key not in result:
                result[key] = value
        return result

    def __repr__(self) -> str:
        return f"PdfAValidationResult(is_valid={self.is_valid}, errors={self.errors})"
