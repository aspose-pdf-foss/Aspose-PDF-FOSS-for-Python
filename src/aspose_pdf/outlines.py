"""Outline (bookmark) support for PDF documents."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any


class OutlineItem:
    """A single bookmark entry in a PDF outline tree.

    Attributes
    ----------
    title : str
        Display text of the bookmark.
    page_index : int or None
        Zero-based index of the destination page; ``None`` for a bookmark
        that lands on no page of this document.
    is_bold : bool
        Whether the bookmark title should be rendered in bold.
    is_italic : bool
        Whether the bookmark title should be rendered in italic.
    open : bool
        Whether a viewer shows this bookmark unfolded, with its children
        visible. ``False`` by default, which is how a bookmark tree is written
        unless asked otherwise.
    children : List[OutlineItem]
        Nested child bookmarks.
    """

    # Declared, so a misspelled name raises instead of being taken and
    # quietly lost; see ``Page`` in ``pages.py``.
    __slots__ = (
        "_color",
        "_destination",
        "_loaded_target",
        "_page_index",
        "children",
        "is_bold",
        "is_italic",
        "open",
        "title",
    )


    def __init__(
        self,
        title: str,
        page_index: int | None = 0,
        *,
        is_bold: bool = False,
        is_italic: bool = False,
        color: Any = None,
        open: bool = False,
        destination: object | None = None,
    ) -> None:
        self.title: str = title
        self.is_bold: bool = is_bold
        self.is_italic: bool = is_italic
        self.open: bool = bool(open)
        self._color: tuple[float, float, float] | None = None
        self.color = color
        # What the item was loaded with, exactly as the file holds it, so a
        # document that is merely opened and saved keeps its bookmarks. The
        # two public ways of naming a target drop it, because the caller has
        # then said where the bookmark goes.
        self._loaded_target: tuple[str, object] | None = None
        self._page_index = None if page_index is None else int(page_index)
        self._destination = destination
        self.children: list[OutlineItem] = []

    @property
    def color(self) -> tuple[float, float, float] | None:
        """The colour a viewer draws the title in (``/C``), or ``None``.

        ISO 32000-1 Table 153 types the entry as three **DeviceRGB** numbers, so
        this is the one colour in the package that is RGB only: a grey or a CMYK
        value has no entry to go in. It is read the way every other colour is --
        three components in 0..1 or 0..255, a ``"#rrggbb"`` string, or a
        :class:`~aspose_pdf.color.Color` -- and ``None`` leaves the entry out,
        which is what makes a viewer use its own colour.
        """
        return self._color

    @color.setter
    def color(self, value: Any) -> None:
        if value is None:
            self._color = None
            return
        from aspose_pdf.engine.content_authoring import normalize_color
        from aspose_pdf.exceptions import PdfValidationException

        components = normalize_color(value)
        if len(components) != 3:
            raise PdfValidationException(
                "A bookmark colour is three DeviceRGB components (Table 153); "
                f"this one has {len(components)}."
            )
        self._color = components

    @property
    def page_index(self) -> int | None:
        """Zero-based index of the page the bookmark lands on.

        ``None`` when it lands on no page of this document: it has no target,
        its action goes elsewhere (a URI, a script), or it names a destination
        the document does not define. Such a bookmark used to read as page 0,
        and saving wrote a jump to page 1 into one that had no target at all.
        Setting ``None`` makes a bookmark with no target.
        """
        return self._page_index

    @page_index.setter
    def page_index(self, value: int | None) -> None:
        self._page_index = None if value is None else int(value)
        self._destination = None
        self._loaded_target = None

    @property
    def destination(self) -> object | None:
        """Typed target: an ``aspose_pdf.interactive`` Destination or Action.

        When set it overrides :attr:`page_index`, which is the fit-to-page
        default. A **string** names a destination the document defines (see
        :attr:`aspose_pdf.Document.destinations`), and is written as the name
        itself so the bookmark follows whatever that name is later pointed at.

        Reading it gives back the target of a bookmark loaded from a file, or
        ``None`` when that target is one this API does not model -- a named
        destination, or an action type it has no class for. Such a target is
        still written back unchanged, and :attr:`page_index` still reports the
        page a defined name lands on; it is only this typed view that is
        unavailable.
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

    @property
    def destination_name(self) -> str | None:
        """The destination name this bookmark targets, when it targets one."""
        if isinstance(self._destination, str):
            return self._destination
        loaded = self._loaded_target
        if loaded is not None and loaded[0] == "Dest":
            raw = loaded[1]
            value = getattr(raw, "value", None)
            if isinstance(value, bytes):
                return value.decode("latin-1")
            name = getattr(raw, "name", None)
            if isinstance(name, str):
                return name.lstrip("/")
        return None

    def _to_dict(self) -> dict:
        return {
            "title": self.title,
            "page_index": self._page_index,
            "is_bold": self.is_bold,
            "is_italic": self.is_italic,
            "color": self._color,
            "open": self.open,
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
            color=d.get("color"),
            open=d.get("open", False),
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

    # Declared, so a misspelled name raises instead of being taken and
    # quietly lost; see ``Page`` in ``pages.py``.
    __slots__ = ("_items",)


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
    # Serialisation helpers (used by SimplePdf)
    # ------------------------------------------------------------------

    def _to_list(self) -> list[dict]:
        return [item._to_dict() for item in self._items]

    @classmethod
    def _from_list(cls, data: list[dict]) -> OutlineCollection:
        col = cls()
        for d in data:
            col._items.append(OutlineItem._from_dict(d))
        return col
