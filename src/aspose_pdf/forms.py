from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any, Dict, Iterable, Iterator, List, Optional, TYPE_CHECKING

from aspose_pdf.exceptions import PdfValidationException

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

    LISTBOX = "ListBox"
    """List box field."""

    COMBOBOX = "ComboBox"
    """Combo box field."""

    PUSHBUTTON = "PushButton"
    """Push button field."""


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

    Supports text, checkbox, radio, listbox, combobox, and push-button fields.

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
        if self._form._document and self._form._document._engine_pdf:
            try:
                self._form._document._engine_pdf.set_field_value(self._name, val)
            except Exception as exc:
                from aspose_pdf.exceptions import AsposePdfException

                raise AsposePdfException(
                    f"Failed to set value for field '{self._name}'"
                ) from exc
        self._value = val

    @property
    def field_type(self) -> str:
        """The field type, such as ``text``, ``checkbox``, or ``combobox``."""
        return self._field_type

    def remove(self) -> "Field":
        """Remove this field and all of its widgets from the form."""
        return self._form.remove_field(self._name)

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

    def _page_index(self, page: Any) -> int:
        if isinstance(page, bool):
            raise TypeError("page must be a Page or a zero-based page index")
        if isinstance(page, int):
            return page
        from aspose_pdf.pages import Page

        if not isinstance(page, Page):
            raise TypeError("page must be a Page or a zero-based page index")
        if page._document is not self._document:
            raise PdfValidationException("Page belongs to a different document")
        return page.index

    @staticmethod
    def _alignment_value(alignment: str | int) -> int:
        if isinstance(alignment, str):
            values = {
                "left": 0,
                "center": 1,
                "centre": 1,
                "right": 2,
            }
            try:
                return values[alignment.lower()]
            except KeyError as exc:
                raise PdfValidationException(
                    "alignment must be 'left', 'center', or 'right'"
                ) from exc
        if isinstance(alignment, bool) or alignment not in (0, 1, 2):
            raise PdfValidationException("alignment must be 0, 1, or 2")
        return int(alignment)

    @staticmethod
    def _default_appearance(font_size: float) -> str:
        if isinstance(font_size, bool) or not isinstance(font_size, (int, float)):
            raise TypeError("font_size must be a number")
        size = float(font_size)
        if not math.isfinite(size) or size < 0:
            raise PdfValidationException("font_size must be finite and non-negative")
        return f"/Helv {size:g} Tf 0 g"

    @staticmethod
    def _common_flags(*, read_only: bool, required: bool) -> int:
        if not isinstance(read_only, bool) or not isinstance(required, bool):
            raise TypeError("read_only and required must be booleans")
        return (1 if read_only else 0) | (2 if required else 0)

    def _create_field(self, name: str, field_type: str, widgets, **kwargs) -> Field:
        self._document._ensure_not_disposed()
        self._document._engine_pdf.create_form_field(
            name, field_type, widgets, **kwargs
        )
        self._load_fields()
        return self._fields[name]

    def add_text_field(
        self,
        name: str,
        page: Any,
        rect: Sequence[float],
        *,
        value: str = "",
        font_size: float = 12,
        multiline: bool = False,
        alignment: str | int = "left",
        read_only: bool = False,
        required: bool = False,
    ) -> Field:
        """Add an editable text field and return it."""
        if not isinstance(value, str):
            raise TypeError("Text field value must be a string")
        if not isinstance(multiline, bool):
            raise TypeError("multiline must be a boolean")
        flags = self._common_flags(read_only=read_only, required=required)
        if multiline:
            flags |= 1 << 12
        return self._create_field(
            name,
            "text",
            [{"page_index": self._page_index(page), "rect": rect}],
            value=value,
            flags=flags,
            default_appearance=self._default_appearance(font_size),
            alignment=self._alignment_value(alignment),
        )

    def add_checkbox(
        self,
        name: str,
        page: Any,
        rect: Sequence[float],
        *,
        checked: bool = False,
        on_value: str = "Yes",
        read_only: bool = False,
        required: bool = False,
    ) -> Field:
        """Add a check box with generated Off/on appearances."""
        if not isinstance(checked, bool):
            raise TypeError("checked must be a boolean")
        return self._create_field(
            name,
            "checkbox",
            [{"page_index": self._page_index(page), "rect": rect}],
            value=checked,
            flags=self._common_flags(read_only=read_only, required=required),
            on_value=on_value,
        )

    def add_radio_group(
        self,
        name: str,
        page: Any,
        options: Mapping[str, Sequence[float]]
        | Sequence[tuple[str, Sequence[float]]],
        *,
        value: Optional[str] = None,
        read_only: bool = False,
        required: bool = False,
    ) -> Field:
        """Add a radio field whose option names map to widget rectangles."""
        items = list(options.items()) if isinstance(options, Mapping) else list(options)
        if not items:
            raise PdfValidationException("Radio group requires at least one option")
        page_index = self._page_index(page)
        widgets = []
        for item in items:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                raise PdfValidationException(
                    "Radio options must be (value, rectangle) pairs"
                )
            export_value, option_rect = item
            if not isinstance(export_value, str):
                raise TypeError("Radio option values must be strings")
            widgets.append(
                {
                    "page_index": page_index,
                    "rect": option_rect,
                    "export_value": export_value,
                }
            )
        if value is not None and not isinstance(value, str):
            raise TypeError("Radio value must be a string or None")
        flags = self._common_flags(read_only=read_only, required=required) | (1 << 15)
        return self._create_field(name, "radio", widgets, value=value, flags=flags)

    @staticmethod
    def _choice_exports(options: Sequence[Any]) -> List[str]:
        exports: List[str] = []
        for option in options:
            if isinstance(option, str):
                exports.append(option)
            elif (
                isinstance(option, (list, tuple))
                and len(option) == 2
                and all(isinstance(item, str) for item in option)
            ):
                exports.append(option[0])
            else:
                raise PdfValidationException(
                    "Choice options must be strings or (export, display) pairs"
                )
        if not exports:
            raise PdfValidationException("Choice field requires at least one option")
        if len(set(exports)) != len(exports):
            raise PdfValidationException("Choice option export values must be unique")
        return exports

    def add_list_box(
        self,
        name: str,
        page: Any,
        rect: Sequence[float],
        options: Sequence[str | tuple[str, str]],
        *,
        value: str | Sequence[str] | None = None,
        multiselect: bool = False,
        font_size: float = 12,
        alignment: str | int = "left",
        read_only: bool = False,
        required: bool = False,
    ) -> Field:
        """Add a list box with string or export/display options."""
        if isinstance(options, (str, bytes)):
            raise TypeError("options must be a sequence of choices")
        normalized_options = list(options)
        exports = self._choice_exports(normalized_options)
        if not isinstance(multiselect, bool):
            raise TypeError("multiselect must be a boolean")
        if multiselect:
            if value is None:
                selected: Any = []
            elif isinstance(value, str) or not isinstance(value, Sequence):
                raise TypeError("A multiselect value must be a sequence of strings")
            else:
                selected = list(value)
                if not all(isinstance(item, str) for item in selected):
                    raise TypeError("A multiselect value must contain only strings")
                if len(set(selected)) != len(selected):
                    raise PdfValidationException(
                        "A multiselect value must not contain duplicates"
                    )
        else:
            if value is not None and not isinstance(value, str):
                raise TypeError("A list box value must be a string or None")
            selected = value
        selected_values = selected if isinstance(selected, list) else [selected]
        if any(item is not None and item not in exports for item in selected_values):
            raise PdfValidationException("List box value is not one of the options")
        flags = self._common_flags(read_only=read_only, required=required)
        if multiselect:
            flags |= 1 << 21
        return self._create_field(
            name,
            "listbox",
            [{"page_index": self._page_index(page), "rect": rect}],
            value=selected,
            flags=flags,
            options=normalized_options,
            default_appearance=self._default_appearance(font_size),
            alignment=self._alignment_value(alignment),
        )

    def add_combo_box(
        self,
        name: str,
        page: Any,
        rect: Sequence[float],
        options: Sequence[str | tuple[str, str]],
        *,
        value: Optional[str] = None,
        editable: bool = False,
        font_size: float = 12,
        alignment: str | int = "left",
        read_only: bool = False,
        required: bool = False,
    ) -> Field:
        """Add a combo box, optionally allowing values outside its option list."""
        if isinstance(options, (str, bytes)):
            raise TypeError("options must be a sequence of choices")
        normalized_options = list(options)
        exports = self._choice_exports(normalized_options)
        if value is not None and not isinstance(value, str):
            raise TypeError("Combo box value must be a string or None")
        if not isinstance(editable, bool):
            raise TypeError("editable must be a boolean")
        if value is not None and not editable and value not in exports:
            raise PdfValidationException("Combo box value is not one of the options")
        flags = self._common_flags(read_only=read_only, required=required) | (1 << 17)
        if editable:
            flags |= 1 << 18
        return self._create_field(
            name,
            "combobox",
            [{"page_index": self._page_index(page), "rect": rect}],
            value=value,
            flags=flags,
            options=normalized_options,
            default_appearance=self._default_appearance(font_size),
            alignment=self._alignment_value(alignment),
        )

    def add_push_button(
        self,
        name: str,
        page: Any,
        rect: Sequence[float],
        *,
        caption: str = "",
        read_only: bool = False,
        required: bool = False,
    ) -> Field:
        """Add a caption-only push button with a generated normal appearance."""
        if not isinstance(caption, str):
            raise TypeError("caption must be a string")
        flags = self._common_flags(read_only=read_only, required=required) | (1 << 16)
        return self._create_field(
            name,
            "pushbutton",
            [{"page_index": self._page_index(page), "rect": rect}],
            flags=flags,
            caption=caption,
        )

    def remove_field(self, name: str) -> Field:
        """Remove a field by fully qualified name and return the removed field."""
        try:
            field = self[name]
        except KeyError:
            raise KeyError(f"Field '{name}' not found") from None
        if not self._document._engine_pdf.remove_form_field(name):
            raise KeyError(f"Field '{name}' not found")
        self._load_fields()
        return field

    def generate_appearances(self) -> int:
        """Regenerate field appearance streams from the current field values.

        Builds the visible appearance (``/AP``) of text and choice fields from
        their values and default appearance, and updates check box / radio
        ``/AS`` states. Missing caption-only push-button appearances are also
        generated, so the form renders without relying on ``/NeedAppearances``.
        Returns the number of widgets updated.
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
