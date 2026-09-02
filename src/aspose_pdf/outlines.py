"""Outline (bookmark) support for PDF documents."""

from __future__ import annotations

from collections.abc import Iterator


class OutlineItem:
    """A single bookmark entry in a PDF outline tree.

    Attributes
    ----------
    title : str
        Display text of the bookmark.
    page_index : int
        Zero-based index of the destination page.
    is_bold : bool
        Whether the bookmark title should be rendered in bold.
    is_italic : bool
        Whether the bookmark title should be rendered in italic.
    children : List[OutlineItem]
        Nested child bookmarks.
    """

    def __init__(
        self,
        title: str,
        page_index: int = 0,
        *,
        is_bold: bool = False,
        is_italic: bool = False,
        destination: object | None = None,
    ) -> None:
        self.title: str = title
        self.is_bold: bool = is_bold
        self.is_italic: bool = is_italic
        # What the item was loaded with, exactly as the file holds it, so a
        # document that is merely opened and saved keeps its bookmarks. The
        # two public ways of naming a target drop it, because the caller has
        # then said where the bookmark goes.
        self._loaded_target: tuple[str, object] | None = None
        self._page_index = int(page_index)
        self._destination = destination
        self.children: list[OutlineItem] = []

    @property
    def page_index(self) -> int:
        """Zero-based index of the destination page."""
        return self._page_index

    @page_index.setter
    def page_index(self, value: int) -> None:
        self._page_index = int(value)
        self._destination = None
        self._loaded_target = None

    @property
    def destination(self) -> object | None:
        """Typed target: an ``aspose_pdf.interactive`` Destination or Action.

        When set it overrides :attr:`page_index`, which is the fit-to-page
        default. Reading it gives back the target of a bookmark loaded from a
        file, or ``None`` when that target is one this API does not model -- a
        named destination, or an action type it has no class for. Such a target
        is still written back unchanged; it is only unavailable to read.
        """
        return self._destination

    @destination.setter
    def destination(self, value: object | None) -> None:
        self._destination = value
        self._loaded_target = None

    def add(self, item: OutlineItem) -> OutlineItem:
        """Append *item* as a child of this outline entry and return it."""
        if not isinstance(item, OutlineItem):
            raise TypeError("item must be an OutlineItem")
        self.children.append(item)
        return item

    def __repr__(self) -> str:
        return (
            f"OutlineItem(title={self.title!r}, page_index={self.page_index}, "
            f"children={len(self.children)})"
        )

    def _to_dict(self) -> dict:
        return {
            "title": self.title,
            "page_index": self._page_index,
            "is_bold": self.is_bold,
            "is_italic": self.is_italic,
            "target": self._destination,
            "loaded_target": self._loaded_target,
            "children": [c._to_dict() for c in self.children],
        }

    @classmethod
    def _from_dict(cls, d: dict) -> OutlineItem:
        item = cls(
            title=d.get("title", ""),
            page_index=d.get("page_index", 0),
            is_bold=d.get("is_bold", False),
            is_italic=d.get("is_italic", False),
            destination=d.get("target"),
        )
        # Straight onto the attribute: assigning either public one would mean
        # the caller had chosen a target, and would throw this away.
        item._loaded_target = d.get("loaded_target")
        for child_dict in d.get("children", []):
            item.children.append(cls._from_dict(child_dict))
        return item


class OutlineCollection:
    """Top-level collection of :class:`OutlineItem` bookmarks.

    Behaves like a mutable sequence — supports ``add``, ``remove``,
    iteration, ``len``, and index access.
    """

    def __init__(self) -> None:
        self._items: list[OutlineItem] = []

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add(self, item: OutlineItem) -> OutlineItem:
        """Append *item* to the collection and return it."""
        if not isinstance(item, OutlineItem):
            raise TypeError("item must be an OutlineItem")
        self._items.append(item)
        return item

    def remove(self, item: OutlineItem) -> None:
        """Remove *item* from the collection.

        Raises
        ------
        ValueError
            If *item* is not present in the collection.
        """
        self._items.remove(item)

    def clear(self) -> None:
        """Remove all items from the collection."""
        self._items.clear()

    # ------------------------------------------------------------------
    # Sequence protocol
    # ------------------------------------------------------------------

    def __iter__(self) -> Iterator[OutlineItem]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __getitem__(self, index: int) -> OutlineItem:
        return self._items[index]

    def __repr__(self) -> str:
        return f"OutlineCollection({self._items!r})"

    # ------------------------------------------------------------------
    # Serialisation helpers (used by SimplePdf / PdfWriterV0)
    # ------------------------------------------------------------------

    def _to_list(self) -> list[dict]:
        return [item._to_dict() for item in self._items]

    @classmethod
    def _from_list(cls, data: list[dict]) -> OutlineCollection:
        col = cls()
        for d in data:
            col._items.append(OutlineItem._from_dict(d))
        return col
