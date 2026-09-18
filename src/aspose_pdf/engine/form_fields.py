"""What a form field says about itself (ISO 32000-1 12.7.3-12.7.4).

A field's attributes live on its own dictionary or, for the inheritable ones
(table 220: ``/FT``, ``/Ff``, ``/V``, ``/DV``; ``/MaxLen``, ``/Opt``, ``/DA``,
``/Q``), on an ancestor's; its widgets are the dictionary itself when field
and widget are merged, and its ``/Kids`` without a ``/T`` otherwise. pdfium,
pdf.js, qpdf and MuPDF all read them so, and the page a widget is on is the
one whose ``/Annots`` lists it.
"""

from __future__ import annotations

from typing import Any

from .cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfString,
    decode_pdf_text_string,
)

__all__ = [
    "field_attribute",
    "field_dictionary",
    "field_widgets",
    "form_value",
    "on_states",
    "text_of",
    "widget_dictionaries",
]

_MAX_ANCESTORS = 64


def field_dictionary(pdf: Any, name: str) -> PdfDictionary | None:
    """The dictionary of the field fully named *name*, or ``None``."""
    if pdf._cos_doc is None:
        return None
    for candidate, entry in pdf._iter_form_fields():
        if candidate == name:
            return entry  # the field comes before its unnamed widget kids
    return None


def field_attribute(pdf: Any, field: PdfDictionary, key: str) -> Any:
    """*key* on *field*, or on the nearest ancestor that has it."""
    name = PdfName(key)
    node: Any = field
    for _ in range(_MAX_ANCESTORS):  # a /Parent loop ends here too
        if not isinstance(node, PdfDictionary):
            return None
        if name in node.mapping:
            return pdf._resolve(node.mapping[name])
        node = pdf._resolve(node.mapping.get(PdfName("Parent")))
    return None


def text_of(pdf: Any, value: Any) -> str | None:
    """A text string or name as text; ``None`` for anything else."""
    value = pdf._resolve(value)
    if isinstance(value, PdfString):
        return decode_pdf_text_string(value)
    if isinstance(value, PdfName):
        return value.name.lstrip("/")
    return None


def _is_widget(pdf: Any, entry: PdfDictionary) -> bool:
    return pdf._resolve(entry.mapping.get(PdfName("Subtype"))) == PdfName("Widget")


def widget_dictionaries(pdf: Any, field: PdfDictionary) -> list[PdfDictionary]:
    """Each of a terminal *field*'s widget annotations: itself, or its kids."""
    widgets: list[PdfDictionary] = []
    if _is_widget(pdf, field):
        widgets.append(field)
    kids = pdf._resolve(field.mapping.get(PdfName("Kids")))
    for kid in kids.items if isinstance(kids, PdfArray) else ():
        entry = pdf._resolve(kid)
        if isinstance(entry, PdfDictionary) and _is_widget(pdf, entry):
            widgets.append(entry)
    return widgets


def _annotation_pages(pdf: Any) -> dict[int, int]:
    """Page index by annotation dictionary, by identity.

    The object store hands out one instance per object, so identity is what
    a widget and the entry in a page's ``/Annots`` share, direct or indirect.
    """
    by_annotation: dict[int, int] = {}
    for index in range(len(pdf.pages)):
        page = pdf._get_page_dict(index)
        annots = pdf._resolve(page.mapping.get(PdfName("Annots"))) if isinstance(page, PdfDictionary) else None
        for item in annots.items if isinstance(annots, PdfArray) else ():
            by_annotation.setdefault(id(pdf._resolve(item)), index)
    return by_annotation


def field_widgets(pdf: Any, field: PdfDictionary) -> list[tuple[int | None, tuple[float, float, float, float]]]:
    """``(page index or None, normalised rectangle)`` for each widget of *field*.

    A widget is on the page whose ``/Annots`` lists it, whatever its ``/P``
    says; one that no page lists is shown on none, as pdf.js, pdfium and MuPDF
    read it, and has no page.
    """
    by_annotation = _annotation_pages(pdf)
    return [
        (by_annotation.get(id(widget)), _rectangle(pdf, widget))
        for widget in widget_dictionaries(pdf, field)
    ]


def _rectangle(pdf: Any, widget: PdfDictionary) -> tuple[float, float, float, float]:
    rect = pdf._resolve(widget.mapping.get(PdfName("Rect")))
    values = [pdf._resolve(item) for item in rect.items] if isinstance(rect, PdfArray) else []
    if len(values) < 4 or not all(isinstance(v, PdfNumber) for v in values[:4]):
        return (0.0, 0.0, 0.0, 0.0)
    x0, y0, x1, y1 = (float(v.value) for v in values[:4])
    # A rectangle may name its corners in either order (ISO 32000-1 7.9.5).
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def on_states(pdf: Any, field: PdfDictionary) -> list[str]:
    """Each widget's on state: its normal appearance's name that is not ``Off``."""
    states: list[str] = []
    for widget in widget_dictionaries(pdf, field):
        appearance = pdf._resolve(widget.mapping.get(PdfName("AP")))
        normal = pdf._resolve(appearance.mapping.get(PdfName("N"))) if isinstance(appearance, PdfDictionary) else None
        if not isinstance(normal, PdfDictionary):
            normal = pdf._resolve(appearance.mapping.get(PdfName("D"))) if isinstance(appearance, PdfDictionary) else None
        names = [key.name.lstrip("/") for key in normal.mapping] if isinstance(normal, PdfDictionary) else []
        on = next((name for name in names if name != "Off"), None)
        if on is not None:
            states.append(on)
    return states


_OFF_VALUES = ("", "Off", "0", "false", "No")


def form_value(resolve: Any, budget: Any, value: Any, kind: str) -> Any:
    """A field's ``/V`` or ``/DV`` as Python, for a field of *kind*.

    Text for text and choice fields; ``True``/``False`` for a check box (off
    unless the value names another state); a radio button's state name, or
    ``None`` at ``Off``; a list where several choices are selected.
    """
    if value is None:
        return False if kind == "checkbox" else None
    if isinstance(value, (PdfString, PdfName)):
        text = (
            decode_pdf_text_string(value)
            if isinstance(value, PdfString)
            else value.name.lstrip("/")
        )
        if kind == "checkbox":
            return text not in _OFF_VALUES
        if kind == "radio" and text == "Off":
            return None
        return text
    if isinstance(value, PdfArray):
        budget.check(len(value.items), "max_container_items", "form field value entries")
        items = []
        for item in value.items:
            item = resolve(item)
            if isinstance(item, PdfString):
                items.append(decode_pdf_text_string(item))
            elif isinstance(item, PdfName):
                items.append(item.name.lstrip("/"))
        return items if len(items) > 1 else (items[0] if items else None)
    return str(value)
