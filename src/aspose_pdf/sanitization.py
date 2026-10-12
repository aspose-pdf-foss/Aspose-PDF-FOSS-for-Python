"""What :meth:`aspose_pdf.Document.sanitize` took out.

A PDF carries more than it shows. Who wrote it and when, what tool made it and
what that tool kept in its private store, scripts a viewer runs on open, files
embedded whole, layers switched off but still there, comments with the
reviewer's name on them -- none of it is on the page, and all of it travels
with the file.

:class:`SanitizationReport` is the count of what went, so a caller can say what
was done rather than hope.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

__all__ = ["SanitizationReport"]


@dataclass(frozen=True)
class SanitizationReport:
    """How much of each kind of thing :meth:`~aspose_pdf.Document.sanitize` removed.

    Every count is of things **taken out**, so an all-zero report means the
    document carried none of it -- which is a result worth having, not a
    failure. ``bool(report)`` is ``True`` when anything was removed.

    Attributes
    ----------
    metadata_entries : int
        ``/Info`` entries cleared (title, author, producer, dates), plus one
        for an XMP packet and one for each private ``/PieceInfo`` store.
    scripts : int
        Document-level scripts removed from the ``/Names /JavaScript`` tree.
    actions : int
        Actions removed because of what they do: the open action, the
        catalog's and each page's additional actions, and any action on an
        annotation or form field that runs script, launches a file, or sends
        data somewhere.
    attachments : int
        Embedded files removed, counting both the ``/EmbeddedFiles`` name tree
        and file-attachment annotations.
    layers : int
        Optional content groups resolved away. The hidden ones' content is
        deleted; the visible ones simply stop being switchable.
    pages_flattened : int
        Pages whose content changed when the layers were resolved.
    comments : int
        Markup annotations removed -- notes, highlights, stamps and the rest,
        each of which carries an author and a date.
    links : int
        Link annotations removed. Off unless asked for: a link is usually
        content, and a dangerous action on one is dealt with by *actions*.
    form_fields : int
        Form fields removed, with the widgets that showed them.
    """

    metadata_entries: int = 0
    scripts: int = 0
    actions: int = 0
    attachments: int = 0
    layers: int = 0
    pages_flattened: int = 0
    comments: int = 0
    links: int = 0
    form_fields: int = 0

    @property
    def total(self) -> int:
        """Everything removed, added up.

        ``pages_flattened`` is left out: it counts pages *changed*, not things
        removed, and adding it would count the same layers twice.
        """
        return sum(
            getattr(self, entry.name)
            for entry in fields(self)
            if entry.name != "pages_flattened"
        )

    def __bool__(self) -> bool:
        return self.total > 0

    def __str__(self) -> str:
        parts = [
            f"{getattr(self, entry.name)} {entry.name.replace('_', ' ')}"
            for entry in fields(self)
            if getattr(self, entry.name)
        ]
        return "removed " + (", ".join(parts) if parts else "nothing")
