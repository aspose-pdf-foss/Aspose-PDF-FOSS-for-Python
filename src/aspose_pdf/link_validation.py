"""Detached diagnostics for local PDF navigation targets."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["LinkValidationIssue"]


@dataclass(frozen=True, slots=True)
class LinkValidationIssue:
    """One local navigation problem, with zero-based source coordinates.

    ``source`` is ``annotation``, ``outline`` or ``named_destination``.
    ``outline_path`` contains sibling indices from the root to the bookmark.
    ``target_path`` identifies the target entry or action-chain position.
    ``destination_name`` uses Latin-1 for byte-string destination names, as
    :class:`NamedDestinationCollection` does. Fields unrelated to the source
    are ``None``. Values remain usable after the document is disposed.
    """

    source: str
    code: str
    message: str
    page_index: int | None = None
    annotation_index: int | None = None
    outline_path: tuple[int, ...] | None = None
    destination_name: str | None = None
    target_path: str = ""
