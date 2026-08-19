"""Typed interactive targets: destinations and actions for links, outlines,
and form-field widgets.

These are immutable value objects. The engine turns them into the PDF ``/Dest``
array or ``/A`` action dictionary, resolving a document page index to the page's
indirect reference. A ``page`` is a zero-based index into the document (an
in-document page); ``GoToRAction`` targets a page *number* in a remote file.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar


class Destination:
    """Base class for a view destination on a document page."""

    page: int

    def _spec(self) -> tuple[str, list[float | None]]:
        """Return ``(kind, params)`` for the ``/Dest`` array after the page ref."""
        raise NotImplementedError


@dataclass(frozen=True)
class FitDestination(Destination):
    """Fit the whole page in the window."""

    page: int

    def _spec(self) -> tuple[str, list[float | None]]:
        return "Fit", []


@dataclass(frozen=True)
class FitBDestination(Destination):
    """Fit the page's bounding box in the window."""

    page: int

    def _spec(self) -> tuple[str, list[float | None]]:
        return "FitB", []


@dataclass(frozen=True)
class XYZDestination(Destination):
    """Position ``(left, top)`` at the upper-left with an optional ``zoom``.

    Any of the three may be ``None`` to keep the current value.
    """

    page: int
    left: float | None = None
    top: float | None = None
    zoom: float | None = None

    def _spec(self) -> tuple[str, list[float | None]]:
        return "XYZ", [self.left, self.top, self.zoom]


@dataclass(frozen=True)
class FitHDestination(Destination):
    """Fit the page width with ``top`` at the top of the window."""

    page: int
    top: float | None = None

    def _spec(self) -> tuple[str, list[float | None]]:
        return "FitH", [self.top]


@dataclass(frozen=True)
class FitBHDestination(Destination):
    """Fit the bounding-box width with ``top`` at the top of the window."""

    page: int
    top: float | None = None

    def _spec(self) -> tuple[str, list[float | None]]:
        return "FitBH", [self.top]


@dataclass(frozen=True)
class FitVDestination(Destination):
    """Fit the page height with ``left`` at the left of the window."""

    page: int
    left: float | None = None

    def _spec(self) -> tuple[str, list[float | None]]:
        return "FitV", [self.left]


@dataclass(frozen=True)
class FitBVDestination(Destination):
    """Fit the bounding-box height with ``left`` at the left of the window."""

    page: int
    left: float | None = None

    def _spec(self) -> tuple[str, list[float | None]]:
        return "FitBV", [self.left]


@dataclass(frozen=True)
class FitRDestination(Destination):
    """Fit the rectangle ``(left, bottom, right, top)`` in the window."""

    page: int
    left: float
    bottom: float
    right: float
    top: float

    def _spec(self) -> tuple[str, list[float | None]]:
        return "FitR", [self.left, self.bottom, self.right, self.top]


class Action:
    """Base class for an interactive action."""

    def _spec(self) -> dict:
        """Return a plain ``/A`` spec; ``D`` may hold a :class:`Destination`."""
        raise NotImplementedError


@dataclass(frozen=True)
class GoToAction(Action):
    """Jump to a destination within this document."""

    destination: Destination

    def _spec(self) -> dict:
        return {"S": "GoTo", "D": self.destination}


@dataclass(frozen=True)
class URIAction(Action):
    """Resolve a uniform resource identifier (typically a web link)."""

    uri: str

    def _spec(self) -> dict:
        return {"S": "URI", "URI": self.uri}


@dataclass(frozen=True)
class GoToRAction(Action):
    """Jump to a destination in another (remote) PDF file.

    ``destination.page`` is a zero-based page *number* in the remote file.
    """

    file: str
    destination: Destination | None = None

    def _spec(self) -> dict:
        spec: dict = {"S": "GoToR", "F": self.file}
        if self.destination is not None:
            spec["D"] = self.destination
        return spec


@dataclass(frozen=True)
class NamedAction(Action):
    """A predefined named action, e.g. ``NextPage``, ``FirstPage``, ``Print``."""

    name: str

    def _spec(self) -> dict:
        return {"S": "Named", "N": self.name}


@dataclass(frozen=True)
class JavaScriptAction(Action):
    """Run a JavaScript script (serialized verbatim, not validated)."""

    script: str

    def _spec(self) -> dict:
        return {"S": "JavaScript", "JS": self.script}


@dataclass(frozen=True)
class LaunchAction(Action):
    """Launch an application or open a file (serialized verbatim)."""

    file: str

    def _spec(self) -> dict:
        return {"S": "Launch", "F": self.file}


@dataclass(frozen=True)
class SubmitFormAction(Action):
    """Send the form's field values to *url*.

    *fields* names the fully qualified fields to send; ``None`` sends them all.
    With *exclude* the named fields are the ones left out instead. *submit_format*
    is ``"fdf"`` (the default), ``"html"``, ``"xfdf"`` or ``"pdf"``.
    """

    url: str
    fields: Sequence[str] | None = None
    exclude: bool = False
    submit_format: str = "fdf"

    # ISO 32000-1 table 237, 1-based bit positions.
    _FORMAT_FLAGS: ClassVar[dict[str, int]] = {
        "fdf": 0,
        "html": 1 << 2,  # ExportFormat
        "xfdf": 1 << 5,  # XFDF
        "pdf": 1 << 8,  # SubmitPDF
    }

    def _spec(self) -> dict:
        try:
            flags = self._FORMAT_FLAGS[self.submit_format.lower()]
        except (AttributeError, KeyError):
            raise ValueError(
                "submit_format must be one of 'fdf', 'html', 'xfdf', 'pdf'; "
                f"got {self.submit_format!r}"
            ) from None
        if self.exclude:
            if self.fields is None:
                raise ValueError("exclude=True needs the fields to exclude")
            flags |= 1 << 0  # Include/Exclude
        spec: dict = {"S": "SubmitForm", "F": self.url, "Flags": flags}
        if self.fields is not None:
            spec["Fields"] = list(self.fields)
        return spec


@dataclass(frozen=True)
class ResetFormAction(Action):
    """Reset the form's fields to their default values.

    *fields* names the fully qualified fields to reset; ``None`` resets them all.
    With *exclude* the named fields are the ones left untouched instead.
    """

    fields: Sequence[str] | None = None
    exclude: bool = False

    def _spec(self) -> dict:
        if self.exclude and self.fields is None:
            raise ValueError("exclude=True needs the fields to exclude")
        spec: dict = {"S": "ResetForm"}
        if self.fields is not None:
            spec["Fields"] = list(self.fields)
        # ISO 32000-1 table 239: bit 1 flips /Fields from include to exclude.
        if self.exclude:
            spec["Flags"] = 1
        return spec


__all__ = [
    "Action",
    "Destination",
    "FitBDestination",
    "FitBHDestination",
    "FitBVDestination",
    "FitDestination",
    "FitHDestination",
    "FitRDestination",
    "FitVDestination",
    "GoToAction",
    "GoToRAction",
    "JavaScriptAction",
    "LaunchAction",
    "NamedAction",
    "ResetFormAction",
    "SubmitFormAction",
    "URIAction",
    "XYZDestination",
]
