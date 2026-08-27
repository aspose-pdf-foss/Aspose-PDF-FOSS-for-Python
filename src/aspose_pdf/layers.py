"""Optional content layers of a document.

A layer is an optional content group (ISO 32000-1 8.11): content tagged with
it is shown or hidden as a unit. :attr:`aspose_pdf.Document.layers` lists the
groups the document declares, in the order of its default configuration, and
each :class:`Layer` can be switched on or off -- which the page renderer, the
graphics absorber and a later ``save()`` all honour.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

from aspose_pdf.exceptions import AsposePdfException

__all__ = ["Layer", "LayerCollection"]


class Layer:
    """One optional content group, and whether it is currently shown."""

    __slots__ = ("_group", "_state")

    def __init__(self, state: Any, group: Any) -> None:
        self._state = state
        self._group = group

    @property
    def name(self) -> str:
        """The group's ``/Name``, as a viewer shows it in its layers panel."""
        return self._group.name

    @property
    def object_number(self) -> int:
        """COS object number of the group; its identity within the document."""
        return self._group.object_number

    @property
    def intent(self) -> tuple[str, ...]:
        """The group's ``/Intent`` names (``View`` unless stated otherwise)."""
        return self._group.intent

    @property
    def visible(self) -> bool:
        """Whether the default configuration shows this layer."""
        return self._group.visible

    @visible.setter
    def visible(self, value: bool) -> None:
        self._state.set_visible(self._group.object_number, bool(value))

    def __repr__(self) -> str:
        return f"Layer({self.name!r}, visible={self.visible})"


class LayerCollection(Sequence[Layer]):
    """The document's layers, indexable by position or by name."""

    def __init__(self, state: Any) -> None:
        self._state = state
        self._layers = [Layer(state, group) for group in state.groups]

    def __len__(self) -> int:
        return len(self._layers)

    def __iter__(self) -> Iterator[Layer]:
        return iter(self._layers)

    def __getitem__(self, key: int | str) -> Layer:  # type: ignore[override]
        if isinstance(key, str):
            for layer in self._layers:
                if layer.name == key:
                    return layer
            raise KeyError(f"No layer named {key!r}")
        return self._layers[key]

    def __contains__(self, item: object) -> bool:
        if isinstance(item, str):
            return any(layer.name == item for layer in self._layers)
        return item in self._layers

    def add(
        self,
        name: str,
        *,
        visible: bool = True,
        intent: Sequence[str] = ("View",),
    ) -> Layer:
        """Create a layer and return it.

        The group is added to the document's optional content structure -- the
        whole structure is created if the document had none -- and appears in a
        viewer's layers panel under *name*. Nothing is on the layer yet: tag
        content with it through :meth:`aspose_pdf.pages.Page.layer`.

        Example
        -------
        ::

            draft = document.layers.add("Draft", visible=False)
            with document.pages[0].layer(draft):
                document.pages[0].add_text("DRAFT", 200, 400, font_size=64)
        """
        from aspose_pdf.engine.optional_content import create_group

        number = create_group(
            self._state._pdf, name, visible=visible, intent=intent
        )
        self._reload()
        for layer in self._layers:
            if layer.object_number == number:
                return layer
        raise AsposePdfException("The new layer could not be read back")

    def remove(self, layer: Layer | str) -> bool:
        """Remove a layer, leaving its content unconditionally visible.

        Removing the *group* is not removing the *content*: marks that named it
        stay in the page and, with nothing left to switch them off, are simply
        always shown -- which is what a viewer does with an ``/OC`` it cannot
        resolve. To delete what a hidden layer holds, use
        :meth:`aspose_pdf.Document.flatten_layers` instead.
        """
        from aspose_pdf.engine.optional_content import remove_group

        target = self[layer] if isinstance(layer, str) else layer
        removed = remove_group(self._state._pdf, target.object_number)
        self._reload()
        return removed

    def _reload(self) -> None:
        from aspose_pdf.engine.optional_content import OptionalContent

        self._state = OptionalContent(self._state._pdf)
        self._layers = [Layer(self._state, group) for group in self._state.groups]

    def names(self) -> list[str]:
        """Layer names in document order."""
        return [layer.name for layer in self._layers]

    def __repr__(self) -> str:
        return f"LayerCollection({self.names()!r})"
