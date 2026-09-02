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


# ISO 32000-1 table 151. The parameter counts are the inverse of each class's
# ``_spec``: a reader is entitled to fewer than the maximum (a trailing null is
# often simply omitted, and each optional parameter already defaults to the
# ``None`` that means "keep the current value") but never more, and ``FitR``'s
# rectangle is all four numbers or nothing.
_DESTINATION_KINDS: dict[str, tuple[type[Destination], int, int]] = {
    "Fit": (FitDestination, 0, 0),
    "FitB": (FitBDestination, 0, 0),
    "XYZ": (XYZDestination, 0, 3),
    "FitH": (FitHDestination, 0, 1),
    "FitBH": (FitBHDestination, 0, 1),
    "FitV": (FitVDestination, 0, 1),
    "FitBV": (FitBVDestination, 0, 1),
    "FitR": (FitRDestination, 4, 4),
}


def destination_from_spec(
    kind: str, page: int, params: Sequence[float | None]
) -> Destination | None:
    """Rebuild the destination a ``/Dest`` array describes.

    The inverse of :meth:`Destination._spec`. *kind* is the destination name
    without its slash, *page* the zero-based index the array's page reference
    resolves to, and *params* the numbers after it (``None`` for a null).
    Returns ``None`` when *kind* is not one of the eight defined names or the
    parameters do not fit it -- an array that is no destination this API can
    express, which is the caller's to interpret.
    """
    entry = _DESTINATION_KINDS.get(kind)
    if entry is None:
        return None
    cls, minimum, maximum = entry
    if not minimum <= len(params) <= maximum:
        return None
    return cls(page, *params)


def action_from_spec(spec: dict) -> Action | None:
    """Rebuild the action an ``/A`` dictionary describes.

    The inverse of :meth:`Action._spec`. *spec* is that dictionary as plain
    Python -- keys without their slash, strings decoded, and a ``D`` entry
    already turned into a :class:`Destination` (or left as the ``[page number,
    name, ...]`` list a remote destination uses). Returns ``None`` for an action
    type this API does not model, which is the caller's cue to keep what the
    file holds rather than write something else in its place.
    """
    kind = spec.get("S")
    if kind == "GoTo":
        destination = spec.get("D")
        # A named destination is a string looked up in the document's /Dests;
        # `GoToAction` has nowhere to put one.
        return GoToAction(destination) if isinstance(destination, Destination) else None
    if kind == "URI":
        uri = spec.get("URI")
        return URIAction(uri) if isinstance(uri, str) else None
    if kind == "GoToR":
        target = spec.get("F")
        if not isinstance(target, str):
            return None
        destination = spec.get("D")
        if isinstance(destination, (list, tuple)) and len(destination) >= 2:
            # A remote destination names its page by number, so it arrives as a
            # plain list; the number is the page and the rest is the view.
            page, name, *params = destination
            if not isinstance(page, (int, float)) or not isinstance(name, str):
                return None
            destination = destination_from_spec(name, int(page), params)
            if destination is None:
                return None
        elif destination is not None and not isinstance(destination, Destination):
            return None
        return GoToRAction(target, destination)
    if kind == "Named":
        name = spec.get("N")
        return NamedAction(name) if isinstance(name, str) else None
    if kind == "JavaScript":
        script = spec.get("JS")
        return JavaScriptAction(script) if isinstance(script, str) else None
    if kind == "Launch":
        target = spec.get("F")
        return LaunchAction(target) if isinstance(target, str) else None
    if kind in ("SubmitForm", "ResetForm"):
        fields = spec.get("Fields")
        if fields is not None:
            if not isinstance(fields, (list, tuple)) or not all(
                isinstance(name, str) for name in fields
            ):
                return None
            fields = list(fields)
        flags = spec.get("Flags")
        flags = int(flags) if isinstance(flags, (int, float)) else 0
        # ISO 32000-1 tables 237 and 239: bit 1 flips /Fields from the fields to
        # act on to the fields to leave alone, and needs a list to invert.
        exclude = bool(flags & 1)
        if exclude and fields is None:
            return None
        if kind == "ResetForm":
            return ResetFormAction(fields, exclude)
        url = spec.get("F")
        if not isinstance(url, str):
            return None
        formats = {
            bit: name for name, bit in SubmitFormAction._FORMAT_FLAGS.items() if bit
        }
        submit_format = "fdf"
        for bit, name in formats.items():
            if flags & bit:
                submit_format = name
                break
        return SubmitFormAction(url, fields, exclude, submit_format)
    return None


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
    "action_from_spec",
    "destination_from_spec",
]
