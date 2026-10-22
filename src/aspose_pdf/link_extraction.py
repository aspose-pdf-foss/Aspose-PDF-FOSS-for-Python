"""Detached navigation entries extracted from PDF Link annotations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aspose_pdf.interactive import Destination

__all__ = ["ExtractedLink"]


@dataclass(frozen=True, slots=True)
class ExtractedLink:
    """One target or action, with its zero-based source annotation location.

    ``rect`` is normalized ``(x0, y0, x1, y1)`` in unrotated page user space,
    or None for malformed geometry. ``action_type`` keeps the PDF action name;
    a direct Dest is represented as GoTo. ``action_path`` identifies Dest, A,
    or an action's Next position. One annotation can produce several records.

    Local destinations resolve against current page identities and named
    definitions. ``destination`` is an immutable typed page view, also used
    for remote GoToR page numbers; remote named targets retain their name.
    URI strings retain their original spelling; ``resolved_uri`` applies the
    catalog URI Base when present. ``file`` is a declared file specification,
    never opened or resolved against the filesystem. Unknown action types are
    listed without exposing executable payloads. ``issues`` contains stable
    extraction diagnostic codes, not network or full conformance checks.
    Values remain usable after the document is closed.
    """

    page_index: int
    annotation_index: int
    rect: tuple[float, float, float, float] | None
    action_type: str | None
    action_path: str
    uri: str | None = None
    resolved_uri: str | None = None
    destination: Destination | None = None
    destination_name: str | None = None
    file: str | None = None
    named_action: str | None = None
    issues: tuple[str, ...] = ()

    @property
    def target_page_index(self) -> int | None:
        """Return the local target page index, excluding remote destinations."""
        if self.action_type == "GoTo" and self.destination is not None:
            return self.destination.page
        return None
