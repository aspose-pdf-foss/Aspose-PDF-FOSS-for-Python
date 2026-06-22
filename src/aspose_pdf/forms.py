from __future__ import annotations

from typing import Any, Dict, Iterable, Iterator, List, Optional, TYPE_CHECKING
from enum import Enum

if TYPE_CHECKING:
    from aspose_pdf.document import Document


class FieldType(Enum):
    """Type of form field."""
    
    TEXT = "Text"
    """Text field."""
    
    CHECKBOX = "Checkbox"
    """Checkbox field."""
    
    RADIO = "Radio"
    """Radio button field."""

class FormType(Enum):
    """Type of PDF form."""
    
    STANDARD = "Standard"
    """Standard AcroForm."""
    
    DYNAMIC = "Dynamic"
    """Dynamic XFA form."""

    @staticmethod
    def from_string(value: str) -> "FormType":
        return FormType(value)


class InvalidFormTypeOperationException(Exception):
    """Exception thrown when an invalid form type operation is attempted."""
    pass


class Field:
    """A field of an interactive form.

    Supports text, checkbox, radio, listbox, and combobox field types.
    This is a base class - specific field types inherit from this.
    
    Attributes:
        _field_type: The type of the field (text, checkbox, radio, etc.)
        _value: The current value of the field
    """

    def __init__(
        self,
        form: "Form",
        name: str,
        value: Any = None,
        field_type: Optional[str] = None,
    ):
        self._form = form
        self._name = name
        self._value = value
        self._field_type = field_type or "text"

    @property
    def name(self) -> str:
        """The fully qualified name of the field."""
        return self._name

    @property
    def value(self) -> Any:
        """The value of the field."""
        return self._value

    @value.setter
    def value(self, val: Any):
        """Set the value of the field and update the engine."""
        self._value = val
        if self._form._document and self._form._document._engine_pdf:
            try:
                self._form._document._engine_pdf.set_field_value(self._name, val)
            except Exception as exc:
                from aspose_pdf.exceptions import AsposePdfException

                raise AsposePdfException(
                    f"Failed to set value for field '{self._name}'"
                ) from exc

    @property
    def field_type(self) -> str:
        """The type of the field: 'text', 'checkbox', 'radio', 'listbox', or 'combobox'."""
        return self._field_type

    def __repr__(self) -> str:
        return f"Field(name='{self._name}', value='{self._value}', type='{self._field_type}')"


class Form:
    """Represents an interactive form (AcroForm) within a PDF document."""

    def __init__(self, document: "Document"):
        self._document = document
        self._fields: Dict[str, Field] = {}
        self._load_fields()

    def _load_fields(self):
        """Load fields from engine."""
        if not self._document or not self._document._engine_pdf:
            return

        raw_fields = self._document._engine_pdf.get_form_fields()
        self._fields.clear()
        for name, data in raw_fields.items():
            if isinstance(data, dict) and "value" in data:
                val = data["value"]
                ftype = data.get("type", "text")
            else:
                val = data
                ftype = "text"
            self._fields[name] = Field(self, name, val, field_type=ftype)

    def __getitem__(self, name: str) -> Field:
        if name not in self._fields:
            # Try reloading in case fields were added or names changed
            self._load_fields()
            if name not in self._fields:
                raise KeyError(f"Field '{name}' not found")
        return self._fields[name]

    def __len__(self) -> int:
        return len(self._fields)

    @property
    def fields(self) -> List[Field]:
        """A list of all fields in the form."""
        return list(self._fields.values())

    def __iter__(self) -> Iterator[Field]:
        return iter(self._fields.values())

    def generate_appearances(self) -> int:
        """Regenerate field appearance streams from the current field values.

        Builds the visible appearance (``/AP``) of text and choice fields from
        their values and default appearance, and updates check box / radio
        ``/AS`` states, so the form renders correctly without relying on
        ``/NeedAppearances``. Returns the number of widgets updated.
        """
        if self._document and self._document._engine_pdf:
            return self._document._engine_pdf.generate_field_appearances()
        return 0

    def flatten(self) -> None:
        """Flatten all fields in the form, making them part of the page content."""
        if self._document and self._document._engine_pdf:
            self._document._engine_pdf.flatten()
            self._fields.clear()
            # Reload to ensure consistency if engine removed objects
            self._load_fields()


class UnsignedContent:
    """Represents a collection of unsigned content elements in a PDF document.

    Based on net.aspose.pdf.security.unsignedcontentabsorber.unsignedcontentabsorber.unsignedcontent.
    """

    def __init__(
        self,
        pages: Optional[List[Any]] = None,
        form_fields: Optional[List[Any]] = None,
        annotations: Optional[List[Any]] = None,
        **kwargs: Any,
    ) -> None:
        self.pages: List[Any] = pages or []
        self.form_fields: List[Any] = form_fields or []
        self.annotations: List[Any] = annotations or []
        self._extra: Dict[str, Any] = kwargs

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


class UnsignedContentAbsorber:
    """Extract unsigned form fields and annotations from a PDF document.

    Parameters
    ----------
    document: Any
        The PDF document instance.  The object is expected to expose the
        following iterable attributes:

        * ``form_fields`` – a collection of form field objects.
        * ``annotations`` – a collection of annotation objects.

        Individual items may expose either ``is_signed`` or ``signed`` boolean
        attributes.  If neither attribute is present the item is treated as
        *unsigned*.
    """

    def __init__(self, document: "Document"):
        self._document = document
        self._extracted: Optional[UnsignedContent] = None

    def reset(self) -> None:
        """Clear the last extracted content."""
        self._extracted = None

    def get_extracted(self) -> Optional[UnsignedContent]:
        """Return the last extracted content, if any."""
        return self._extracted

    def has_extracted(self) -> bool:
        """True if extraction has been performed."""
        return self._extracted is not None

    @staticmethod
    def _is_unsigned(item: Any) -> bool:
        """Return ``True`` if *item* is not signed."""
        if hasattr(item, "is_signed"):
            return not bool(getattr(item, "is_signed"))
        if hasattr(item, "signed"):
            return not bool(getattr(item, "signed"))
        return True

    @staticmethod
    def _collect_unsigned(items: Iterable[Any]) -> List[Any]:
        """Collect unsigned elements from *items*.

        The function materialises the iterator into a list to provide a stable
        deterministic order, matching the iteration order of the source
        collection.
        """
        return [item for item in items if UnsignedContentAbsorber._is_unsigned(item)]

    def extract(self) -> UnsignedContent:
        """Extract unsigned form fields and annotations from the document.

        Returns
        -------
        UnsignedContent
            An object containing lists of unsigned form fields and annotations.
        """
        doc = self._document

        # 1. Collect unsigned form fields
        form_fields: Iterable[Any] = []
        if hasattr(doc, "form"):
            form_fields = doc.form
        elif hasattr(doc, "form_fields"):
            form_fields = doc.form_fields

        unsigned_fields = self._collect_unsigned(form_fields)

        # 2. Collect unsigned annotations from all pages
        unsigned_annotations = []
        if hasattr(doc, "pages"):
            for page in doc.pages:
                if hasattr(page, "annotations"):
                    unsigned_annotations.extend(self._collect_unsigned(page.annotations))
        elif hasattr(doc, "annotations"):
            unsigned_annotations = self._collect_unsigned(doc.annotations)

        # 3. Report all pages as unsigned content.
        unsigned_pages = []
        if hasattr(doc, "pages"):
            unsigned_pages = list(doc.pages)

        content = UnsignedContent(
            pages=unsigned_pages,
            form_fields=unsigned_fields,
            annotations=unsigned_annotations,
        )
        self._extracted = content
        return content
