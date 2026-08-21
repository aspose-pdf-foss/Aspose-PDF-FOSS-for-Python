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

    def names(self) -> list[str]:
        """Layer names in document order."""
        return [layer.name for layer in self._layers]

    def __repr__(self) -> str:
        return f"LayerCollection({self.names()!r})"
