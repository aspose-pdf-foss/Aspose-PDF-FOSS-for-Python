"""Optional content (layers): which groups are on, and what that hides.

A PDF's optional content groups (ISO 32000-1 8.11) decide whether marked
content, an XObject or an annotation is drawn at all. The default
configuration in ``/OCProperties /D`` says which groups start visible; content
belongs to a group through an ``/OC`` entry naming either an OCG or an OCMD,
and an OCMD combines several groups under a policy.

Nothing here paints or parses content streams: it answers one question --
"is this ``/OC`` object visible in the default configuration?" -- for the
renderer, the text extractor and the graphics absorber, and it backs the
public layer API.
"""

from __future__ import annotations

from typing import Any

from .cos import (
    PdfArray,
    PdfDictionary,
    PdfIndirectReference,
    PdfName,
    PdfStream,
)

__all__ = ["OptionalContent", "OptionalContentGroup"]


class OptionalContentGroup:
    """One optional content group (``/OCG``) of the document."""

    __slots__ = ("intent", "name", "object_number", "visible")

    def __init__(
        self,
        object_number: int,
        name: str,
        visible: bool,
        intent: tuple[str, ...],
    ) -> None:
        self.object_number = object_number
        self.name = name
        self.visible = visible
        self.intent = intent

    def __repr__(self) -> str:
        state = "on" if self.visible else "off"
        return f"OptionalContentGroup({self.name!r}, {state})"


class OptionalContent:
    """The document's optional content groups and their default visibility.

    ``visible_by_number`` maps an OCG's object number to whether the default
    configuration shows it; :meth:`is_visible` answers the same question for a
    resolved ``/OC`` value, following an OCMD's ``/P`` policy.
    """

    def __init__(self, pdf: Any) -> None:
        self.groups: list[OptionalContentGroup] = []
        self.visible_by_number: dict[int, bool] = {}
        self._pdf = pdf
        self._load()

    # -- construction -----------------------------------------------------
    def _resolve(self, obj: Any) -> Any:
        resolver = getattr(self._pdf, "_resolve", None)
        if callable(resolver):
            return resolver(obj)
        cos_doc = getattr(self._pdf, "_cos_doc", None)
        if isinstance(obj, PdfIndirectReference) and cos_doc is not None:
            return cos_doc.objects.get(obj.object_number)
        return obj

    def _catalog(self) -> PdfDictionary | None:
        cos_doc = getattr(self._pdf, "_cos_doc", None)
        if cos_doc is None:
            return None
        root = self._resolve(cos_doc.trailer.mapping.get(PdfName("Root")))
        return root if isinstance(root, PdfDictionary) else None

    def _load(self) -> None:
        catalog = self._catalog()
        if catalog is None:
            return
        properties = self._resolve(catalog.mapping.get(PdfName("OCProperties")))
        if not isinstance(properties, PdfDictionary):
            return

        all_groups = self._reference_numbers(properties.mapping.get(PdfName("OCGs")))
        config = self._resolve(properties.mapping.get(PdfName("D")))
        base_state = "ON"
        on_numbers: set[int] = set()
        off_numbers: set[int] = set()
        if isinstance(config, PdfDictionary):
            state = self._name(config.mapping.get(PdfName("BaseState")))
            if state in ("ON", "OFF"):
                base_state = state
            on_numbers = set(
                self._reference_numbers(config.mapping.get(PdfName("ON")))
            )
            off_numbers = set(
                self._reference_numbers(config.mapping.get(PdfName("OFF")))
            )

        for number in all_groups:
            visible = base_state == "ON"
            if number in on_numbers:
                visible = True
            if number in off_numbers:
                visible = False
            self.visible_by_number[number] = visible
            group = self._resolve(self._object(number))
            name = ""
            intent: tuple[str, ...] = ()
            if isinstance(group, PdfDictionary):
                name = self._text(group.mapping.get(PdfName("Name")))
                intent = self._intent(group.mapping.get(PdfName("Intent")))
            self.groups.append(
                OptionalContentGroup(number, name, visible, intent)
            )

    def _object(self, number: int) -> Any:
        cos_doc = getattr(self._pdf, "_cos_doc", None)
        if cos_doc is None:
            return None
        try:
            return cos_doc.objects.get(number)
        except KeyError:
            return None

    def _reference_numbers(self, obj: Any) -> list[int]:
        """Return the object numbers of an array of indirect references."""
        numbers: list[int] = []
        if isinstance(obj, PdfIndirectReference):
            return [obj.object_number]
        array = self._resolve(obj)
        if not isinstance(array, PdfArray):
            return numbers
        for item in array.items:
            if isinstance(item, PdfIndirectReference):
                numbers.append(item.object_number)
        return numbers

    def _name(self, obj: Any) -> str | None:
        obj = self._resolve(obj)
        if isinstance(obj, PdfName):
            return obj.name.lstrip("/")
        if isinstance(obj, str):
            return obj.lstrip("/")
        return None

    def _text(self, obj: Any) -> str:
        obj = self._resolve(obj)
        value = getattr(obj, "value", None)
        if isinstance(value, bytes):
            if value.startswith(b"\xfe\xff"):
                return value[2:].decode("utf-16-be", errors="replace")
            return value.decode("latin-1", errors="replace")
        if isinstance(value, str):
            return value
        return ""

    def _intent(self, obj: Any) -> tuple[str, ...]:
        obj = self._resolve(obj)
        if isinstance(obj, PdfName):
            return (obj.name.lstrip("/"),)
        if isinstance(obj, PdfArray):
            names = [self._name(item) for item in obj.items]
            return tuple(name for name in names if name)
        return ("View",)

    # -- queries ----------------------------------------------------------
    @property
    def present(self) -> bool:
        """True when the document declares any optional content group."""
        return bool(self.visible_by_number)

    def is_visible(self, oc: Any) -> bool:
        """Return whether content tagged with *oc* is shown.

        *oc* is the value of an ``/OC`` entry, or a reference to one: an OCG,
        or an OCMD combining groups under ``/P`` (``AnyOn`` by default,
        plus ``AllOn``, ``AnyOff`` and ``AllOff``). Anything that cannot be
        resolved is treated as visible, which is what a viewer does with
        content it cannot attribute to a group.
        """
        if oc is None:
            return True
        number = oc.object_number if isinstance(oc, PdfIndirectReference) else None
        resolved = self._resolve(oc)
        if not isinstance(resolved, (PdfDictionary, PdfStream)):
            return True
        type_name = self._name(resolved.mapping.get(PdfName("Type")))
        if type_name == "OCMD":
            return self._ocmd_visible(resolved)
        if number is not None and number in self.visible_by_number:
            return self.visible_by_number[number]
        return True

    def _ocmd_visible(self, ocmd: PdfDictionary) -> bool:
        expression = ocmd.mapping.get(PdfName("VE"))
        if expression is not None:
            visible = self._visibility_expression(self._resolve(expression), 0)
            if visible is not None:
                return visible
        states = [
            self.visible_by_number.get(number, True)
            for number in self._reference_numbers(ocmd.mapping.get(PdfName("OCGs")))
        ]
        if not states:
            return True
        policy = self._name(ocmd.mapping.get(PdfName("P"))) or "AnyOn"
        if policy == "AllOn":
            return all(states)
        if policy == "AnyOff":
            return not all(states)
        if policy == "AllOff":
            return not any(states)
        return any(states)  # AnyOn

    def _visibility_expression(self, node: Any, depth: int) -> bool | None:
        """Evaluate a ``/VE`` array: ``[/Not x]``, ``[/And …]``, ``[/Or …]``."""
        if depth > 16 or not isinstance(node, PdfArray) or not node.items:
            return None
        operator = self._name(node.items[0])
        operands: list[bool] = []
        for item in node.items[1:]:
            if isinstance(item, PdfArray):
                value = self._visibility_expression(item, depth + 1)
                if value is None:
                    return None
            elif isinstance(item, PdfIndirectReference):
                value = self.visible_by_number.get(item.object_number, True)
            else:
                return None
            operands.append(value)
        if not operands:
            return None
        if operator == "Not":
            return not operands[0]
        if operator == "And":
            return all(operands)
        if operator == "Or":
            return any(operands)
        return None

    def set_visible(self, object_number: int, visible: bool) -> None:
        """Set a group's visibility in memory and in the default configuration."""
        if object_number not in self.visible_by_number:
            raise KeyError(f"No optional content group with object {object_number}")
        self.visible_by_number[object_number] = visible
        for group in self.groups:
            if group.object_number == object_number:
                group.visible = visible
        self._write_default_config()

    def _write_default_config(self) -> None:
        """Rewrite ``/D /ON`` and ``/D /OFF`` from the current state."""
        catalog = self._catalog()
        if catalog is None:
            return
        properties = self._resolve(catalog.mapping.get(PdfName("OCProperties")))
        if not isinstance(properties, PdfDictionary):
            return
        config = self._resolve(properties.mapping.get(PdfName("D")))
        if not isinstance(config, PdfDictionary):
            config = PdfDictionary()
            properties.mapping[PdfName("D")] = config
        base_state = self._name(config.mapping.get(PdfName("BaseState"))) or "ON"
        on_items = []
        off_items = []
        for number, visible in self.visible_by_number.items():
            reference = PdfIndirectReference(number, 0)
            if visible and base_state == "OFF":
                on_items.append(reference)
            elif not visible and base_state == "ON":
                off_items.append(reference)
        if on_items:
            config.mapping[PdfName("ON")] = PdfArray(on_items)
        else:
            config.mapping.pop(PdfName("ON"), None)
        if off_items:
            config.mapping[PdfName("OFF")] = PdfArray(off_items)
        else:
            config.mapping.pop(PdfName("OFF"), None)
