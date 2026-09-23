from __future__ import annotations

import math
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from aspose_pdf.engine.form_fields import validate_choice_value
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

    SIGNATURE = "Signature"
    """Digital signature field."""


class FormType(Enum):
    """Type of PDF form."""

    STANDARD = "Standard"
    """Standard AcroForm."""

    DYNAMIC = "Dynamic"
    """Dynamic XFA form."""

    @staticmethod
    def from_string(value: str) -> FormType:
        return FormType(value)


class InvalidFormTypeOperationException(Exception):
    """Exception thrown when an invalid form type operation is attempted."""

    pass


@dataclass(frozen=True)
class FieldWidget:
    """Where one of a field's widgets sits: the page and the rectangle on it."""

    page_index: int | None
    """Zero-based index of the page, or ``None`` if no page lists the widget."""

    rect: tuple[float, float, float, float]
    """``(x0, y0, x1, y1)`` in default user space, lower-left corner first."""


# Field flags (ISO 32000-1 tables 221, 226, 228, 230), as bit masks.
_READ_ONLY, _REQUIRED, _NO_EXPORT = 1 << 0, 1 << 1, 1 << 2
_MULTILINE, _PASSWORD, _COMB = 1 << 12, 1 << 13, 1 << 24
_EDIT, _MULTI_SELECT = 1 << 18, 1 << 21


class Field:
    """A field of an interactive form.

    Supports text, checkbox, radio, listbox, combobox, and push-button fields.
    Besides its value, a field reports what its dictionary says, reading an
    inheritable attribute from the nearest ancestor that has it (ISO 32000-1
    table 220), as pdfium, pdf.js, qpdf and MuPDF do.

    A form refresh updates existing instances in place. Removed fields keep
    their last name, value and type for inspection, but can no longer be edited
    or removed again, even if a new field reuses the same name.
    """

    def __init__(
        self,
        form: Form,
        name: str,
        value: Any = None,
        field_type: str | None = None,
    ):
        self._form = form
        self._name = name
        self._value = value
        self._field_type = field_type or "text"
        self._removed = False

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
        engine = self._engine()
        try:
            engine.set_field_value(self._name, val)
        except (TypeError, PdfValidationException):
            raise
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

    # --- what the field's dictionary says --------------------------------------------

    def _engine(self):
        document = self._form._document
        document._ensure_not_disposed()
        if self._removed:
            raise KeyError(f"Field '{self._name}' is no longer in the form")
        return document._engine_pdf

    def _dictionary(self):
        from aspose_pdf.engine.form_fields import field_dictionary

        field = field_dictionary(self._engine(), self._name)
        if field is None:
            raise KeyError(f"Field '{self._name}' is no longer in the form")
        return field

    def _attribute(self, key: str) -> Any:
        from aspose_pdf.engine.form_fields import field_attribute

        return field_attribute(self._engine(), self._dictionary(), key)

    def _own_text(self, key: str) -> str | None:
        from aspose_pdf.engine.cos import PdfName
        from aspose_pdf.engine.form_fields import text_of

        return text_of(self._engine(), self._dictionary().mapping.get(PdfName(key)))

    @property
    def partial_name(self) -> str:
        """The last part of :attr:`name`: the field's own ``/T``."""
        return self._own_text("T") or ""

    @property
    def alternate_name(self) -> str | None:
        """The name shown to a user, often as a tooltip (``/TU``); ``None`` if unset."""
        return self._own_text("TU")

    @property
    def mapping_name(self) -> str | None:
        """The name used when the form is exported (``/TM``); ``None`` if unset."""
        return self._own_text("TM")

    @property
    def flags(self) -> int:
        """The field flags (``/Ff``), inherited when the field has none."""
        from aspose_pdf.engine.cos import PdfNumber

        value = self._attribute("Ff")
        return int(value.value) if isinstance(value, PdfNumber) else 0

    def _set_flag(self, mask: int, on: bool) -> None:
        from aspose_pdf.engine.cos import PdfName, PdfNumber

        if not isinstance(on, bool):
            raise TypeError("a field flag is set with a boolean")
        flags = (self.flags | mask) if on else (self.flags & ~mask)
        # Written on the field itself, from the value it had inherited, so
        # its siblings under the same parent are not changed with it.
        self._dictionary().mapping[PdfName("Ff")] = PdfNumber(flags)

    @property
    def read_only(self) -> bool:
        """Whether the user may not change the field (flag bit 1)."""
        return bool(self.flags & _READ_ONLY)

    @read_only.setter
    def read_only(self, value: bool) -> None:
        self._set_flag(_READ_ONLY, value)

    @property
    def required(self) -> bool:
        """Whether the field must have a value when the form is submitted (bit 2)."""
        return bool(self.flags & _REQUIRED)

    @required.setter
    def required(self, value: bool) -> None:
        self._set_flag(_REQUIRED, value)

    @property
    def no_export(self) -> bool:
        """Whether the field is left out when the form is submitted (bit 3)."""
        return bool(self.flags & _NO_EXPORT)

    @property
    def default_value(self) -> Any:
        """The value a reset restores (``/DV``), in the same form as :attr:`value`."""
        from aspose_pdf.engine.form_fields import form_value

        engine = self._engine()
        return form_value(engine._resolve, engine._load_budget, self._attribute("DV"), self._field_type)

    @property
    def max_length(self) -> int | None:
        """The most characters a text field takes (``/MaxLen``); ``None`` if unlimited."""
        from aspose_pdf.engine.cos import PdfNumber

        if self._field_type != "text":
            return None
        value = self._attribute("MaxLen")
        if isinstance(value, PdfNumber) and float(value.value).is_integer():
            return int(value.value)
        return None

    def _text_flag(self, mask: int) -> bool:
        return self._field_type == "text" and bool(self.flags & mask)

    @property
    def multiline(self) -> bool:
        """Whether a text field takes several lines (bit 13)."""
        return self._text_flag(_MULTILINE)

    @property
    def password(self) -> bool:
        """Whether a text field hides what is typed (bit 14)."""
        return self._text_flag(_PASSWORD)

    @property
    def comb(self) -> bool:
        """Whether a text field spaces its characters into ``max_length`` cells (bit 25)."""
        return self._text_flag(_COMB)

    @property
    def options(self) -> list[tuple[str, str]]:
        """A choice field's options as ``(export value, display text)`` pairs.

        An option given as one string is both. Other fields have none.
        """
        from aspose_pdf.engine.cos import PdfArray
        from aspose_pdf.engine.form_fields import text_of

        if self._field_type not in ("listbox", "combobox"):
            return []
        engine = self._engine()
        items = self._attribute("Opt")
        pairs: list[tuple[str, str]] = []
        for item in items.items if isinstance(items, PdfArray) else ():
            item = engine._resolve(item)
            if isinstance(item, PdfArray) and len(item.items) >= 2:
                export, display = text_of(engine, item.items[0]), text_of(engine, item.items[1])
                if export is not None and display is not None:
                    pairs.append((export, display))
                continue
            text = text_of(engine, item)
            if text is not None:
                pairs.append((text, text))
        return pairs

    @property
    def multi_select(self) -> bool:
        """Whether a list box lets several options be selected (bit 22)."""
        return self._field_type == "listbox" and bool(self.flags & _MULTI_SELECT)

    @property
    def editable(self) -> bool:
        """Whether a combo box takes text that is not one of its options (bit 19)."""
        return self._field_type == "combobox" and bool(self.flags & _EDIT)

    @property
    def export_values(self) -> list[str]:
        """The on state of each widget of a check box or radio button, in widget order."""
        from aspose_pdf.engine.form_fields import on_states

        if self._field_type not in ("checkbox", "radio"):
            return []
        return on_states(self._engine(), self._dictionary())

    @property
    def widgets(self) -> list[FieldWidget]:
        """Each place the field appears: page and rectangle, one per widget."""
        from aspose_pdf.engine.form_fields import field_widgets

        return [
            FieldWidget(page, rect)
            for page, rect in field_widgets(self._engine(), self._dictionary())
        ]

    @property
    def page_index(self) -> int | None:
        """The page of the field's first widget, or ``None`` without one."""
        widgets = self.widgets
        return widgets[0].page_index if widgets else None

    @property
    def rect(self) -> tuple[float, float, float, float] | None:
        """The rectangle of the field's first widget, or ``None`` without one."""
        widgets = self.widgets
        return widgets[0].rect if widgets else None

    def remove(self) -> Field:
        """Remove this field and all of its widgets from the form."""
        self._engine()
        return self._form.remove_field(self._name)

    def __repr__(self) -> str:
        return f"Field(name='{self._name}', value='{self._value}', type='{self._field_type}')"


class Form:
    """Represents an interactive form (AcroForm) within a PDF document."""

    def __init__(self, document: Document):
        self._document = document
        self._fields: dict[str, Field] = {}
        self._load_fields()

    def _load_fields(self):
        """Refresh existing field objects and detach fields removed from the engine."""
        if not self._document or not self._document._engine_pdf:
            return

        raw_fields = self._document._engine_pdf.get_form_fields()
        fields: dict[str, Field] = {}
        for name, data in raw_fields.items():
            if isinstance(data, dict) and "value" in data:
                val = data["value"]
                ftype = data.get("type", "text")
            else:
                val = data
                ftype = "text"
            field = self._fields.get(name)
            if field is None or field._removed:
                field = Field(self, name, val, field_type=ftype)
            else:
                field._value = val
                field._field_type = ftype or "text"
            fields[name] = field
        for name, field in self._fields.items():
            if name not in fields:
                field._removed = True
        self._fields = fields

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
    def fields(self) -> list[Field]:
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
        font: Any = None,
        multiline: bool = False,
        alignment: str | int = "left",
        read_only: bool = False,
        required: bool = False,
    ) -> Field:
        """Add an editable text field and return it.

        *font* embeds a Type0 (CID) field font — bytes/path/``FontDescriptor`` —
        so a non-Latin *value* renders (its ``/AP`` is baked with CID codes at
        authoring time). Without *font*, the Standard-14 ``/DR`` Helvetica is used.
        """
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
            font=font,
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
        value: str | None = None,
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
    def _choice_exports(options: Sequence[Any]) -> list[str]:
        exports: list[str] = []
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
        selected = validate_choice_value(value, exports, multiselect=multiselect)
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
        value: str | None = None,
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
        if not isinstance(editable, bool):
            raise TypeError("editable must be a boolean")
        value = validate_choice_value(value, exports, combo=True, editable=editable)
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
        action: Any = None,
        icon: bytes | None = None,
        border_color: Sequence[float] | None = None,
        background: Sequence[float] | None = None,
        read_only: bool = False,
        required: bool = False,
    ) -> Field:
        """Add a push button with generated caption/rollover/down appearances.

        *action* is an :class:`~aspose_pdf.interactive.Action` (or a
        ``Destination``, treated as a go-to) run when the button is pressed.
        *border_color* and *background* are DeviceRGB/Gray/CMYK component lists
        for the widget ``/MK`` border and background; the rollover and down
        faces are shaded variants of the background.

        *icon* is raw image bytes (JPEG or non-interlaced 8-bit PNG) used as the
        button's ``/MK /I`` icon. It is wrapped in a form XObject, scaled
        proportionally to fit and centred; with a *caption* the icon takes the
        upper band and the caption sits below it (``/MK /TP 2``), otherwise the
        icon fills the face alone (``/TP 1``).
        """
        if not isinstance(caption, str):
            raise TypeError("caption must be a string")
        flags = self._common_flags(read_only=read_only, required=required) | (1 << 16)
        return self._create_field(
            name,
            "pushbutton",
            [{"page_index": self._page_index(page), "rect": rect}],
            flags=flags,
            caption=caption,
            action=action,
            icon=icon,
            border_color=border_color,
            background=background,
        )

    def add_signature_field(
        self,
        name: str,
        page: Any,
        rect: Sequence[float],
        *,
        read_only: bool = False,
        required: bool = False,
        seed_value: Mapping[str, Any] | None = None,
        lock: Mapping[str, Any] | None = None,
    ) -> Field:
        """Add an empty (unsigned) signature field and return it.

        Creates an ``/FT /Sig`` field with a widget on *page* and sets the
        AcroForm ``/SigFlags`` ``SignaturesExist`` bit. The field carries no
        value until it is signed; it renders as an empty box. Fill it later
        with :func:`aspose_pdf.engine.sign_field.sign_field`, which signs the
        saved bytes as an incremental update.

        *seed_value* constrains how the field may be signed (``/SV``) — keys
        ``filter``, ``sub_filter``, ``digest_method``, ``reasons``, plus
        ``required`` naming those a signer must honour rather than prefer.
        *lock* (``/Lock``) names the fields this signature freezes: ``action``
        of ``All``/``Include``/``Exclude`` and, for the latter two, ``fields``.
        """
        return self._create_field(
            name,
            "signature",
            [{"page_index": self._page_index(page), "rect": rect}],
            flags=self._common_flags(read_only=read_only, required=required),
            seed_value=seed_value,
            field_lock=lock,
        )

    # --- form data in and out: FDF and XFDF -------------------------------------------

    def export_fdf(self, destination: Any = None) -> bytes:
        """The form's data as FDF (ISO 32000-1 12.7.8), written to *destination* if given.

        Every field is listed by its partial name under its parents, with the
        value the document stores for it -- on a parent, where a parent holds
        it. A signature's value is not form data and is left out. *destination*
        is a path (replaced in one step) or a writable binary stream.
        """
        from aspose_pdf.engine.form_data import to_fdf

        return self._export(to_fdf, destination)

    def export_xfdf(self, destination: Any = None) -> bytes:
        """The form's data as XFDF (ISO 19444-1), written to *destination* if given.

        See :meth:`export_fdf`.
        """
        from aspose_pdf.engine.form_data import to_xfdf

        return self._export(to_xfdf, destination)

    def import_fdf(self, source: Any) -> list[str]:
        """Fill the form from FDF data and return the names of the fields it named.

        Each value is set as :attr:`Field.value` sets it, redrawing the field;
        a value on a parent field is the value of its kids that have none of
        their own. ``/Ff``, ``/SetFf`` and ``/ClrFf`` change the field's flags
        and ``/F``, ``/SetF`` and ``/ClrF`` its widgets'. Fields the document
        does not have are skipped, and a signature field's value is never
        imported. *source* is a path, bytes or a readable binary stream.
        """
        from aspose_pdf.engine.form_data import read_fdf

        return self._import(read_fdf, source)

    def import_xfdf(self, source: Any) -> list[str]:
        """Fill the form from XFDF data; see :meth:`import_fdf`.

        A file with a DOCTYPE or entity declarations is refused: XFDF needs
        neither, and they are how XML is made to expand without bound.
        """
        from aspose_pdf.engine.form_data import read_xfdf

        return self._import(read_xfdf, source)

    def _export(self, writer: Any, destination: Any) -> bytes:
        from aspose_pdf.engine.file_output import write_file_atomically
        from aspose_pdf.engine.stream_output import write_all

        self._document._ensure_not_disposed()
        data = writer(self._document._engine_pdf)
        if destination is None:
            return data
        if hasattr(destination, "write"):
            write_all(destination, data)
        else:
            write_file_atomically(destination, data)
        return data

    def _import(self, reader: Any, source: Any) -> list[str]:
        import os

        from aspose_pdf.engine.form_data import apply
        from aspose_pdf.load_limits import _LoadBudget, _read_limited

        self._document._ensure_not_disposed()
        engine = self._document._engine_pdf
        budget = _LoadBudget(self._document.load_limits)
        if isinstance(source, (bytes, bytearray)):
            data = bytes(source)
            budget.check_input(len(data))
        elif isinstance(source, (str, os.PathLike)):
            with open(source, "rb") as stream:
                data = _read_limited(stream, budget)
        elif hasattr(source, "read"):
            data = _read_limited(source, budget)
        else:
            raise TypeError("source must be a path, bytes or a readable binary stream")
        imported = apply(engine, reader(data, budget))
        self._load_fields()
        return imported

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
            self._load_fields()


class UnsignedContent:
    """Represents a collection of unsigned content elements in a PDF document.

    Based on net.aspose.pdf.security.unsignedcontentabsorber.unsignedcontentabsorber.unsignedcontent.
    """

    def __init__(
        self,
        pages: list[Any] | None = None,
        form_fields: list[Any] | None = None,
        annotations: list[Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self.pages: list[Any] = pages or []
        self.form_fields: list[Any] = form_fields or []
        self.annotations: list[Any] = annotations or []
        self._extra: dict[str, Any] = kwargs

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
    """Extract the pages, form fields and annotations no signature covers.

    For a :class:`~aspose_pdf.document.Document`, content is *unsigned* when it
    is new or different since the newest signature that verifies: added or
    changed by a later incremental update, or edited in memory. A page counts
    when it, its content streams or the resources it draws with changed; an
    annotation when it or its appearance did; a field when it or its widgets
    did. A document that carries no valid signature is unsigned throughout.

    Any other object is read by duck typing: it exposes ``form_fields`` and
    ``annotations`` (or ``form`` and ``pages``), whose items carry an
    ``is_signed`` or ``signed`` flag; an item with neither is unsigned.
    """

    def __init__(self, document: Document):
        self._document = document
        self._extracted: UnsignedContent | None = None

    def reset(self) -> None:
        """Clear the last extracted content."""
        self._extracted = None

    def get_extracted(self) -> UnsignedContent | None:
        """Return the last extracted content, if any."""
        return self._extracted

    def has_extracted(self) -> bool:
        """True if extraction has been performed."""
        return self._extracted is not None

    @staticmethod
    def _extract_from_document(doc: Any, engine: Any) -> UnsignedContent:
        """What a real document's signatures leave uncovered.

        Reading ``is_signed`` flags that pages, fields and annotations do not
        have reported everything in a signed document as unsigned -- the
        signature field included.
        """
        from .engine.signed_changes import changes_since_signing

        changes = changes_since_signing(engine)
        pages = list(doc.pages)
        fields = list(doc.form.fields)
        annotations = [
            (page_index, annot_index, annotation)
            for page_index, page in enumerate(pages)
            for annot_index, annotation in enumerate(page.annotations)
        ]
        if changes is None:
            return UnsignedContent(
                pages=pages,
                form_fields=fields,
                annotations=[annotation for _, _, annotation in annotations],
            )
        return UnsignedContent(
            pages=[page for index, page in enumerate(pages) if changes.page_changed(index)],
            form_fields=[field for field in fields if changes.field_changed(field.name)],
            annotations=[
                annotation
                for page_index, annot_index, annotation in annotations
                if changes.annotation_changed(page_index, annot_index)
            ],
        )

    @staticmethod
    def _is_unsigned(item: Any) -> bool:
        """Return ``True`` if *item* is not signed."""
        if hasattr(item, "is_signed"):
            return not bool(getattr(item, "is_signed"))
        if hasattr(item, "signed"):
            return not bool(getattr(item, "signed"))
        return True

    @staticmethod
    def _collect_unsigned(items: Iterable[Any]) -> list[Any]:
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
        engine = getattr(doc, "_engine_pdf", None)
        if getattr(engine, "_cos_doc", None) is not None:
            content = self._extract_from_document(doc, engine)
            self._extracted = content
            return content

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
