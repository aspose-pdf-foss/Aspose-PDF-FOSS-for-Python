"""The document's named destinations (ISO 32000-1 12.3.2.3).

A named destination is a place in the document under a name, so a link, a
bookmark or another file can point at "chapter-2" instead of at a page and a
view. Moving the page it names updates every reference at once -- that is the
whole point of the indirection -- and it is how a cross-document link stays
valid when the target is re-paginated.

    document.destinations["chapter-2"] = XYZDestination(page=7, top=760)
    page.add_link((72, 700, 200, 720), "chapter-2")
    document.outlines.add(OutlineItem("Chapter 2", destination="chapter-2"))
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from aspose_pdf.exceptions import AsposePdfException, PdfValidationException
from aspose_pdf.interactive import Destination, FitDestination

if TYPE_CHECKING:
    from aspose_pdf.document import Document

__all__ = ["NamedDestinationCollection"]


class NamedDestinationCollection:
    """The document's named destinations, as a mapping of name to destination.

    Reads go straight to the document, so the collection is never a stale copy:
    both places a PDF keeps these are read -- the ``/Names /Dests`` name tree
    (PDF 1.2 and later) and the older ``/Dests`` dictionary -- and a name the two
    disagree about reads as the dictionary's, which is the one a viewer resolves
    first.

    A value is the typed destination the name lands on, or ``None`` for an entry
    this API cannot express: a destination into another file, one whose page has
    since been deleted, or a view ISO 32000-1 does not define. Such an entry
    stays in the file exactly as it is -- writing a name only touches that name.

    Assigning takes a :class:`~aspose_pdf.interactive.Destination`, or a page
    index as a shorthand for "fit that page"::

        document.destinations["cover"] = 0
        document.destinations["chapter-2"] = XYZDestination(page=7, top=760)
        del document.destinations["cover"]
    """

    # Declared, so a misspelled name raises instead of being taken and
    # quietly lost; see ``Page`` in ``pages.py``.
    __slots__ = ("_document",)

    def __init__(self, document: Document) -> None:
        self._document = document

    # -- reading -------------------------------------------------------------

    def _engine(self) -> Any:
        self._document._ensure_not_disposed()
        engine = self._document._engine_pdf
        if engine is None:
            raise AsposePdfException("No document loaded")
        return engine

    def _names(self) -> dict[str, Any]:
        return self._engine().named_destination_entries()

    def __len__(self) -> int:
        return len(self._names())

    def __iter__(self) -> Iterator[str]:
        """The names, in order -- which is the order the file holds them in."""
        return iter(sorted(self._names()))

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._names()

    def __getitem__(self, name: str) -> Destination | None:
        if name not in self._names():
            raise KeyError(name)
        return self._engine().named_destination_target(name)

    def get(self, name: str, default: Any = None) -> Any:
        """The destination *name* lands on, or *default* when there is no such name.

        A name that *is* defined but cannot be typed reads as ``None``, not as
        *default*: the destination is there, and it is this API that has no class
        for it.
        """
        if name not in self._names():
            return default
        return self._engine().named_destination_target(name)

    def keys(self) -> list[str]:
        return sorted(self._names())

    def values(self) -> list[Destination | None]:
        return [self[name] for name in self.keys()]

    def items(self) -> list[tuple[str, Destination | None]]:
        return [(name, self[name]) for name in self.keys()]

    # -- writing -------------------------------------------------------------

    def __setitem__(self, name: str, destination: Destination | int) -> None:
        self.add(name, destination)

    def add(self, name: str, destination: Destination | int) -> Destination:
        """Define (or replace) *name*, and return the destination it now names.

        *destination* is a :class:`~aspose_pdf.interactive.Destination` or a page
        index, which stands for that page fitted to the window.
        """
        engine = self._engine()
        resolved = self._coerce(destination)
        count = len(engine.pages)
        if not count:
            raise PdfValidationException(
                "A named destination needs a page to point at; this document has none."
            )
        if not 0 <= resolved.page < count:
            raise PdfValidationException(
                f"Page index {resolved.page} is outside this document's {count} pages."
            )
        engine.set_named_destination(name, resolved)
        return resolved

    def __delitem__(self, name: str) -> None:
        if not self.remove(name):
            raise KeyError(name)

    def remove(self, name: str) -> bool:
        """Remove *name*; ``True`` when the document defined one."""
        return bool(self._engine().remove_named_destination(name))

    def clear(self) -> None:
        """Remove every named destination.

        Links and bookmarks that pointed at one are left as they are: a name
        nothing defines simply goes nowhere, which is what a viewer does with it
        too, and rewriting them into page references would be a different edit
        from the one that was asked for.
        """
        self._engine().clear_named_destinations()

    @staticmethod
    def _coerce(destination: Destination | int) -> Destination:
        """*destination* as a :class:`Destination`, or a refusal that says why."""
        if isinstance(destination, Destination):
            return destination
        if isinstance(destination, bool):
            raise PdfValidationException(
                "A destination is a Destination or a page index, not a boolean."
            )
        if isinstance(destination, int):
            return FitDestination(destination)
        raise PdfValidationException(
            "A named destination is an aspose_pdf.interactive Destination, or a "
            "page index standing for that page fitted to the window."
        )

    def __repr__(self) -> str:
        return f"NamedDestinationCollection({self.keys()!r})"
