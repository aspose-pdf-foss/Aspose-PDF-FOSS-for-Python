"""Public tagged-PDF inspection and remediation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Optional, Sequence

if TYPE_CHECKING:
    from aspose_pdf.document import Document

__all__ = ["StructureElement", "TaggedContent", "TaggedContext"]


@dataclass(slots=True)
class TaggedContext:
    document: Any
    structure_tree_root: Any | None = None
    mark_info: Any | None = None
    language: str | None = None


class TaggedContent:
    """Editable view of a document's logical structure tree.

    Page numbers are one-based, matching :class:`Document.pages`. Reading order
    follows the order of structure elements in each parent's ``/K`` entry.
    """

    __slots__ = ("_document",)

    def __init__(self, document: "Document") -> None:
        self._document = document

    @property
    def _engine(self) -> Any:
        self._document._ensure_not_disposed()
        engine = self._document._engine_pdf
        if engine is None:
            raise RuntimeError("Document engine is unavailable")
        return engine

    def _wrap(self, element: Any) -> "StructureElement":
        return StructureElement(self, element)

    def _unwrap(
        self, element: Optional["StructureElement"], *, name: str
    ) -> Any | None:
        if element is None:
            return None
        if not isinstance(element, StructureElement):
            raise TypeError(f"{name} must be a StructureElement or None")
        if element._content is not self:
            raise ValueError(f"{name} belongs to a different document")
        return element._element

    @property
    def root_elements(self) -> list["StructureElement"]:
        """Return top-level elements in logical reading order."""
        return [self._wrap(element) for element in self._engine._tagged_root_elements()]

    def add_element(
        self,
        structure_type: str,
        *,
        parent: Optional["StructureElement"] = None,
        index: int | None = None,
        page_number: int | None = None,
        mcids: Iterable[int] = (),
        alt_text: str | None = None,
        actual_text: str | None = None,
    ) -> "StructureElement":
        """Create and attach a structure element.

        Supplying ``mcids`` also creates the corresponding ``/ParentTree``
        mappings for ``page_number``. Existing mappings are never overwritten.
        """
        if not isinstance(structure_type, str):
            raise TypeError("structure_type must be a string")
        if alt_text is not None and not isinstance(alt_text, str):
            raise TypeError("alt_text must be a string or None")
        if actual_text is not None and not isinstance(actual_text, str):
            raise TypeError("actual_text must be a string or None")
        normalized_mcids = tuple(mcids)
        element = self._engine._tagged_add_element(
            structure_type,
            parent=self._unwrap(parent, name="parent"),
            index=index,
            page_number=page_number,
            mcids=normalized_mcids,
            alt_text=alt_text,
            actual_text=actual_text,
        )
        return self._wrap(element)

    def move(
        self,
        element: "StructureElement",
        *,
        parent: Optional["StructureElement"] = None,
        index: int | None = None,
    ) -> None:
        """Move an element to a parent and reading-order position."""
        raw_element = self._unwrap(element, name="element")
        self._engine._tagged_move_element(
            raw_element,
            parent=self._unwrap(parent, name="parent"),
            index=index,
        )

    def set_reading_order(
        self,
        elements: Sequence["StructureElement"],
        *,
        parent: Optional["StructureElement"] = None,
    ) -> None:
        """Set the complete order of a parent's direct structure children."""
        raw_elements = [
            self._unwrap(element, name="elements item") for element in elements
        ]
        self._engine._tagged_set_reading_order(
            raw_elements,
            parent=self._unwrap(parent, name="parent"),
        )

    def remove(self, element: "StructureElement") -> None:
        """Remove an element, its descendants, and their ParentTree mappings."""
        self._engine._tagged_remove_element(self._unwrap(element, name="element"))

    def element_for_mcid(
        self, page_number: int, mcid: int
    ) -> "StructureElement | None":
        """Return the structure element mapped to a page MCID, if any."""
        element = self._engine._tagged_element_for_mcid(page_number, mcid)
        return self._wrap(element) if element is not None else None


class StructureElement:
    """A mutable logical-structure element in a tagged PDF."""

    __slots__ = ("_content", "_element")

    def __init__(self, content: TaggedContent, element: Any) -> None:
        self._content = content
        self._element = element

    @property
    def structure_type(self) -> str:
        """Get or set the element's structure type, such as ``P`` or ``Figure``."""
        return self._content._engine._tagged_element_type(self._element)

    @structure_type.setter
    def structure_type(self, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError("structure_type must be a string")
        self._content._engine._tagged_set_element_type(self._element, value)

    @property
    def alt_text(self) -> str | None:
        """Get or set the ``/Alt`` alternate description."""
        return self._content._engine._tagged_element_text(self._element, "Alt")

    @alt_text.setter
    def alt_text(self, value: str | None) -> None:
        self._content._engine._tagged_set_element_text(self._element, "Alt", value)

    @property
    def actual_text(self) -> str | None:
        """Get or set the ``/ActualText`` replacement text."""
        return self._content._engine._tagged_element_text(self._element, "ActualText")

    @actual_text.setter
    def actual_text(self, value: str | None) -> None:
        self._content._engine._tagged_set_element_text(
            self._element, "ActualText", value
        )

    @property
    def page_number(self) -> int | None:
        """Return the one-based page number referenced by ``/Pg``."""
        return self._content._engine._tagged_element_page_number(self._element)

    @property
    def mcids(self) -> tuple[int, ...]:
        """Return direct marked-content IDs referenced by this element."""
        return self._content._engine._tagged_element_mcids(self._element)

    @property
    def parent(self) -> "StructureElement | None":
        """Return the parent element, or ``None`` for a top-level element."""
        parent = self._content._engine._tagged_parent(self._element)
        return self._content._wrap(parent) if parent is not None else None

    @property
    def children(self) -> list["StructureElement"]:
        """Return direct child elements in logical reading order."""
        return [
            self._content._wrap(child)
            for child in self._content._engine._tagged_children(self._element)
        ]

    def add_child(
        self,
        structure_type: str,
        *,
        index: int | None = None,
        page_number: int | None = None,
        mcids: Iterable[int] = (),
        alt_text: str | None = None,
        actual_text: str | None = None,
    ) -> "StructureElement":
        """Create a direct child element."""
        return self._content.add_element(
            structure_type,
            parent=self,
            index=index,
            page_number=page_number,
            mcids=mcids,
            alt_text=alt_text,
            actual_text=actual_text,
        )

    def move_to(
        self,
        parent: Optional["StructureElement"] = None,
        *,
        index: int | None = None,
    ) -> None:
        """Move this element to a parent and reading-order position."""
        self._content.move(self, parent=parent, index=index)

    def set_reading_order(self, elements: Sequence["StructureElement"]) -> None:
        """Set the complete order of this element's direct children."""
        self._content.set_reading_order(elements, parent=self)

    def remove(self) -> None:
        """Remove this element and its descendants from the structure tree."""
        self._content.remove(self)

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, StructureElement)
            and self._content is other._content
            and self._element is other._element
        )

    def __hash__(self) -> int:
        return hash((id(self._content), id(self._element)))

    def __repr__(self) -> str:
        return (
            f"StructureElement(structure_type={self.structure_type!r}, "
            f"page_number={self.page_number!r}, mcids={self.mcids!r})"
        )
