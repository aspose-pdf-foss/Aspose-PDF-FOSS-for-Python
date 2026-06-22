from __future__ import annotations

from typing import Any, List

"""Compatibility helpers for unsigned-content extraction (Aspose.PDF subset)."""


class UnsignedContentAbsorber:
    """Extracts unsigned content elements; includes form field/annotation info.

    Mirrors the Aspose.PDF ``UnsignedContentAbsorber`` API.
    """

    def __init__(self, *args, **kwargs) -> None:
        """Initialize absorber with optional configuration.
        Stores provided arguments for potential later use.
        """
        self._args = args
        self._kwargs = kwargs
        self._extracted: UnsignedContent | None = None

    def extract(self, *args, **kwargs) -> UnsignedContent:
        """Extract unsigned content into an :class:`UnsignedContent` instance.

        Returns an :class:`UnsignedContent` instance populated with empty
        collections or with data passed via keyword arguments.
        """
        content = UnsignedContent(
            pages=kwargs.get("pages", []),
            form_fields=kwargs.get("form_fields", []),
            annotations=kwargs.get("annotations", []),
        )
        self._extracted = content
        return content

    def reset(self) -> None:
        """Clear any previously extracted content.

        After calling this method ``get_extracted`` will return ``None``.
        """
        self._extracted = None

    def get_extracted(self) -> UnsignedContent | None:
        """Return the last extracted :class:`UnsignedContent` instance, if any."""
        return self._extracted

    def has_extracted(self) -> bool:
        """Convenience method to check whether extraction has been performed."""
        return self._extracted is not None


class UnsignedContent:
    """Container for unsigned content (pages, form fields, annotations).

    Mirrors the Aspose.PDF ``UnsignedContent`` API.
    """

    def __init__(self, *args, **kwargs) -> None:
        """Initialize UnsignedContent with optional collections.
        Instance attributes are set to provided lists or default to empty lists.
        """
        self.pages: List[Any] = kwargs.get("pages", [])
        self.form_fields: List[Any] = kwargs.get("form_fields", [])
        self.annotations: List[Any] = kwargs.get("annotations", [])
        self._extra = kwargs
        # The methods below provide mutable operations on the stored collections.

    def add_page(self, page: Any) -> None:
        """Add a page to the unsigned content."""
        self.pages.append(page)

    def remove_page(self, page: Any) -> None:
        """Remove a page from the unsigned content if present."""
        if page in self.pages:
            self.pages.remove(page)

    def add_form_field(self, field: Any) -> None:
        """Add a form field to the unsigned content."""
        self.form_fields.append(field)

    def remove_form_field(self, field: Any) -> None:
        """Remove a form field from the unsigned content if present."""
        if field in self.form_fields:
            self.form_fields.remove(field)

    def add_annotation(self, annotation: Any) -> None:
        """Add an annotation to the unsigned content."""
        self.annotations.append(annotation)

    def remove_annotation(self, annotation: Any) -> None:
        """Remove an annotation from the unsigned content if present."""
        if annotation in self.annotations:
            self.annotations.remove(annotation)

    def reset(self) -> None:
        """Reset all collections to empty lists."""
        self.pages.clear()
        self.form_fields.clear()
        self.annotations.clear()
        self._extra.clear()

    def __repr__(self) -> str:
        return (
            f"UnsignedContent(pages={len(self.pages)}, "
            f"form_fields={len(self.form_fields)}, "
            f"annotations={len(self.annotations)})"
        )
