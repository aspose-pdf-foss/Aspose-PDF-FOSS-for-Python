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

from collections.abc import Sequence
from typing import Any

from aspose_pdf.exceptions import PdfValidationException

from .cos import (
    PdfArray,
    PdfDictionary,
    PdfIndirectReference,
    PdfName,
    PdfNumber,
    PdfStream,
)

__all__ = [
    "OptionalContent",
    "OptionalContentGroup",
    "create_group",
    "flatten",
    "remove_group",
]


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

# ---------------------------------------------------------------------------
# Authoring
# ---------------------------------------------------------------------------
def _catalog_of(pdf: Any) -> PdfDictionary:
    """The document catalog, with a COS graph materialised if needed."""
    if getattr(pdf, "_cos_doc", None) is None:
        pdf._ensure_cos()
    root = pdf._resolve(pdf._cos_doc.trailer.mapping.get(PdfName("Root")))
    if not isinstance(root, PdfDictionary):
        raise PdfValidationException("The document has no catalog")
    return root


def _properties_of(pdf: Any, *, create: bool) -> PdfDictionary | None:
    """The catalog's ``/OCProperties``, created on demand."""
    catalog = _catalog_of(pdf)
    properties = pdf._resolve(catalog.mapping.get(PdfName("OCProperties")))
    if isinstance(properties, PdfDictionary):
        return properties
    if not create:
        return None
    properties = PdfDictionary(
        {
            PdfName("OCGs"): PdfArray([]),
            PdfName("D"): PdfDictionary({PdfName("Order"): PdfArray([])}),
        }
    )
    catalog.mapping[PdfName("OCProperties")] = properties
    return properties


def _config_of(pdf: Any, properties: PdfDictionary) -> PdfDictionary:
    config = pdf._resolve(properties.mapping.get(PdfName("D")))
    if not isinstance(config, PdfDictionary):
        config = PdfDictionary()
        properties.mapping[PdfName("D")] = config
    return config


def create_group(
    pdf: Any,
    name: str,
    *,
    visible: bool = True,
    intent: Sequence[str] = ("View",),
) -> int:
    """Register a new optional content group and return its object number.

    The group is appended to ``/OCProperties /OCGs`` and to the default
    configuration's ``/Order``, which is what a viewer's layers panel lists.
    A document that had no optional content at all gets the whole structure.
    """
    from .simple_pdf import _pdf_text_string

    if not isinstance(name, str) or not name:
        raise PdfValidationException("A layer needs a non-empty name")
    properties = _properties_of(pdf, create=True)
    assert properties is not None
    names = [str(value) for value in intent] or ["View"]
    group = PdfDictionary(
        {
            PdfName("Type"): PdfName("OCG"),
            PdfName("Name"): _pdf_text_string(name),
            PdfName("Intent"): (
                PdfName(names[0])
                if len(names) == 1
                else PdfArray([PdfName(value) for value in names])
            ),
        }
    )
    reference = pdf._cos_doc.register_object(group)

    groups = pdf._resolve(properties.mapping.get(PdfName("OCGs")))
    if not isinstance(groups, PdfArray):
        groups = PdfArray([])
        properties.mapping[PdfName("OCGs")] = groups
    groups.items.append(reference)

    config = _config_of(pdf, properties)
    order = pdf._resolve(config.mapping.get(PdfName("Order")))
    if not isinstance(order, PdfArray):
        order = PdfArray([])
        config.mapping[PdfName("Order")] = order
    order.items.append(reference)

    state = OptionalContent(pdf)
    state.set_visible(reference.object_number, visible)
    return reference.object_number


def remove_group(pdf: Any, object_number: int) -> bool:
    """Drop a group from the document; its content becomes unconditional.

    Removing the *group* is not removing the *content*: marks that named it
    stay in the page, and with nothing left to switch them off they are simply
    always shown -- which is what a viewer does with an ``/OC`` it cannot
    resolve. Use :func:`flatten` to delete hidden content instead.
    """
    properties = _properties_of(pdf, create=False)
    if properties is None:
        return False
    removed = False
    for key in ("OCGs",):
        array = pdf._resolve(properties.mapping.get(PdfName(key)))
        if isinstance(array, PdfArray):
            kept = [
                item
                for item in array.items
                if not _is_reference_to(item, object_number)
            ]
            removed = removed or len(kept) != len(array.items)
            array.items[:] = kept
    config = pdf._resolve(properties.mapping.get(PdfName("D")))
    if isinstance(config, PdfDictionary):
        for key in ("ON", "OFF", "Order", "Locked", "AS"):
            array = pdf._resolve(config.mapping.get(PdfName(key)))
            if isinstance(array, PdfArray):
                array.items[:] = [
                    item
                    for item in array.items
                    if not _is_reference_to(item, object_number)
                ]
    return removed


def _is_reference_to(item: Any, object_number: int) -> bool:
    return (
        isinstance(item, PdfIndirectReference)
        and item.object_number == object_number
    )

# ---------------------------------------------------------------------------
# Flattening
# ---------------------------------------------------------------------------
_MAX_MARK_DEPTH = 64


def flatten(pdf: Any) -> int:
    """Delete content in hidden layers and drop the optional content structure.

    Switching a layer off changes what is *drawn*; the content stays in the
    file and comes back the moment someone switches it on again. That is the
    right behaviour for a viewer and the wrong one for handing the document to
    somebody: a hidden draft watermark or an alternate-language layer is still
    in there. Flattening resolves the layers once and for all -- hidden marked
    content, hidden XObject invocations and hidden annotations are removed,
    every surviving ``/OC`` reference is dropped, and ``/OCProperties`` goes
    with them, leaving an ordinary PDF that shows exactly what the current
    configuration showed.

    Returns the number of pages whose content changed.
    """
    state = OptionalContent(pdf)
    if not state.present:
        return 0
    changed = 0
    for page_index in range(len(getattr(pdf, "pages", []))):
        if _flatten_page(pdf, page_index, state):
            changed += 1
    _strip_oc_entries(pdf)
    catalog = _catalog_of(pdf)
    catalog.mapping.pop(PdfName("OCProperties"), None)
    return changed


def _flatten_page(pdf: Any, page_index: int, state: OptionalContent) -> bool:
    from .auto_tag import _tokens

    try:
        content = pdf.get_page_content(page_index)
    except Exception:
        return False
    if not content:
        return False

    properties = _page_properties(pdf, page_index)
    hidden_xobjects = _hidden_xobject_names(pdf, page_index, state)
    cuts: list[tuple[int, int]] = []

    pending: list[tuple[str, int]] = []  # (token, start) for the last operands
    depth = 0
    hidden_at: int | None = None  # depth of the open hidden section
    hidden_from = 0
    # Depths of open *visible* /OC sections, with the byte span of their BDC:
    # the wrapper goes too, so the flattened page carries no optional content
    # markup at all.
    visible_marks: list[tuple[int, int, int]] = []

    for token, start, end in _tokens(content):
        if token is None or token in ("[", "]", "{", "}", "<<", ">>"):
            pending.append(("", start))
            continue
        if token.startswith("/") or _looks_numeric(token):
            pending.append((token, start))
            if len(pending) > 8:
                del pending[:-8]
            continue

        if token in ("BDC", "BMC"):
            depth += 1
            if token == "BDC" and len(pending) >= 2:
                tag, tag_start = pending[-2]
                name, _name_start = pending[-1]
                if tag == "/OC":
                    if hidden_at is None and _name_hidden(
                        pdf, properties, name, state
                    ):
                        hidden_at = depth
                        hidden_from = tag_start
                    elif hidden_at is None:
                        visible_marks.append((depth, tag_start, end))
        elif token == "EMC":
            if hidden_at is not None and depth == hidden_at:
                cuts.append((hidden_from, end))
                hidden_at = None
            elif visible_marks and depth == visible_marks[-1][0]:
                _, mark_start, mark_end = visible_marks.pop()
                cuts.append((mark_start, mark_end))
                cuts.append((start, end))
            depth = max(0, depth - 1)
        elif token == "Do" and hidden_at is None and pending:
            name, name_start = pending[-1]
            if name.lstrip("/") in hidden_xobjects:
                cuts.append((name_start, end))
        pending.clear()
        if depth > _MAX_MARK_DEPTH:
            return False

    if not cuts:
        return False
    out = bytearray(content)
    for start, end in sorted(cuts, reverse=True):
        del out[start:end]
    return _replace_page_content(pdf, page_index, bytes(out))


def _replace_page_content(pdf: Any, page_index: int, content: bytes) -> bool:
    """Overwrite the page's existing content streams in place.

    Registering a *new* stream and repointing ``/Contents`` would leave the old
    one in the file as an unreferenced object -- and the whole point of
    flattening is that the hidden content is gone, not merely unreachable.
    """
    page = pdf._get_page_dict(page_index)
    if not isinstance(page, PdfDictionary):
        return False
    entry = page.mapping.get(PdfName("Contents"))
    resolved = pdf._resolve(entry)
    streams: list[PdfStream] = []
    if isinstance(resolved, PdfStream):
        streams = [resolved]
    elif isinstance(resolved, PdfArray):
        for item in resolved.items:
            stream = pdf._resolve(item)
            if isinstance(stream, PdfStream):
                streams.append(stream)
    if not streams:
        pdf._set_page_content(page_index, content)
        return True
    for index, stream in enumerate(streams):
        payload = content if index == 0 else b""
        stream.content = payload
        stream.mapping.pop(PdfName("Filter"), None)
        stream.mapping.pop(PdfName("DecodeParms"), None)
        stream.mapping[PdfName("Length")] = PdfNumber(len(payload))
    if page_index < len(pdf.page_contents):
        pdf.page_contents[page_index] = content
    pdf._extracted_text = None
    return True


def _looks_numeric(token: str) -> bool:
    try:
        float(token)
    except ValueError:
        return False
    return True


def _page_properties(pdf: Any, page_index: int) -> PdfDictionary | None:
    try:
        page = pdf._get_page_dict(page_index)
        resources = pdf._resolve_resources_cos(page)
    except Exception:
        return None
    if not isinstance(resources, PdfDictionary):
        return None
    properties = pdf._resolve(resources.mapping.get(PdfName("Properties")))
    return properties if isinstance(properties, PdfDictionary) else None


def _name_hidden(
    pdf: Any, properties: PdfDictionary | None, name: str, state: OptionalContent
) -> bool:
    """Whether ``/OC <name> BDC`` names a group the configuration turns off."""
    if properties is None or not name.startswith("/"):
        return False
    target = properties.mapping.get(PdfName(name.lstrip("/")))
    if target is None:
        return False
    try:
        return not state.is_visible(target)
    except Exception:
        return False


def _hidden_xobject_names(
    pdf: Any, page_index: int, state: OptionalContent
) -> set[str]:
    """Names of XObjects on the page whose own ``/OC`` is turned off."""
    hidden: set[str] = set()
    try:
        page = pdf._get_page_dict(page_index)
        resources = pdf._resolve_resources_cos(page)
    except Exception:
        return hidden
    if not isinstance(resources, PdfDictionary):
        return hidden
    xobjects = pdf._resolve(resources.mapping.get(PdfName("XObject")))
    if not isinstance(xobjects, PdfDictionary):
        return hidden
    for key, value in xobjects.mapping.items():
        stream = pdf._resolve(value)
        if not isinstance(stream, PdfStream):
            continue
        oc = stream.mapping.get(PdfName("OC"))
        if oc is None:
            continue
        try:
            visible = state.is_visible(oc)
        except Exception:
            visible = True
        if not visible:
            hidden.add(key.name.lstrip("/"))
    return hidden


def _strip_oc_entries(pdf: Any) -> None:
    """Remove hidden annotations and every surviving ``/OC`` reference."""
    state = OptionalContent(pdf)
    for page_index in range(len(getattr(pdf, "pages", []))):
        try:
            page = pdf._get_page_dict(page_index)
        except Exception:
            continue
        if not isinstance(page, PdfDictionary):
            continue
        annots = pdf._resolve(page.mapping.get(PdfName("Annots")))
        if isinstance(annots, PdfArray):
            kept = []
            for item in annots.items:
                annotation = pdf._resolve(item)
                if isinstance(annotation, PdfDictionary):
                    oc = annotation.mapping.get(PdfName("OC"))
                    if oc is not None:
                        try:
                            visible = state.is_visible(oc)
                        except Exception:
                            visible = True
                        if not visible:
                            continue
                        annotation.mapping.pop(PdfName("OC"), None)
                kept.append(item)
            annots.items[:] = kept
        resources = pdf._resolve(page.mapping.get(PdfName("Resources")))
        if isinstance(resources, PdfDictionary):
            xobjects = pdf._resolve(resources.mapping.get(PdfName("XObject")))
            if isinstance(xobjects, PdfDictionary):
                for value in xobjects.mapping.values():
                    stream = pdf._resolve(value)
                    if isinstance(stream, PdfStream):
                        stream.mapping.pop(PdfName("OC"), None)

