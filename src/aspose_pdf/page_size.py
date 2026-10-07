"""Named page sizes for authoring, in PDF points.

A PDF measures its pages in default user space units, which are 1/72 inch
(ISO 32000-1 8.3.2.3), so every size here is a point pair. The ISO A and B
sizes are defined in millimetres and the US sizes in inches, and both are
converted exactly rather than rounded to a printer's approximation: an A4 page
is 210 x 297 mm, which is 595.275591 x 841.889764 pt as the writer spells it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import ClassVar

__all__ = ["PageSize"]

_POINTS_PER_INCH = 72.0
_MM_PER_INCH = 25.4


@dataclass(frozen=True, slots=True, eq=False)
class PageSize:
    """A page's width and height in PDF points.

    The named sizes are class attributes -- ``PageSize.A4``, ``PageSize.LETTER``
    and the rest listed below -- and each one is *portrait*, the shorter side
    first, which is how both ISO 216 and the US sizes are named. Turn one over
    with :meth:`landscape`.

    ISO A series: ``A0``, ``A1``, ``A2``, ``A3``, ``A4``, ``A5``, ``A6``.
    ISO B series: ``B4``, ``B5``.
    US sizes: ``LETTER``, ``LEGAL``, ``TABLOID``, ``LEDGER``, ``EXECUTIVE``,
    ``STATEMENT``.

    ``LETTER`` is the size a new page has when none is asked for, because it is
    what this package has always created and what a ``/MediaBox`` that cannot
    give a size falls back to.
    """

    width: float
    height: float

    # ClassVar, so the named sizes stay class attributes: an annotation without
    # it would make each one a *field* of the dataclass and PageSize(w, h) would
    # need seventeen arguments. They are assigned below the class body, which is
    # the earliest a PageSize can be built.
    A0: ClassVar[PageSize]
    A1: ClassVar[PageSize]
    A2: ClassVar[PageSize]
    A3: ClassVar[PageSize]
    A4: ClassVar[PageSize]
    A5: ClassVar[PageSize]
    A6: ClassVar[PageSize]
    B4: ClassVar[PageSize]
    B5: ClassVar[PageSize]
    LETTER: ClassVar[PageSize]
    LEGAL: ClassVar[PageSize]
    TABLOID: ClassVar[PageSize]
    LEDGER: ClassVar[PageSize]
    EXECUTIVE: ClassVar[PageSize]
    STATEMENT: ClassVar[PageSize]

    def __post_init__(self) -> None:
        object.__setattr__(self, "width", _positive(self.width, "width"))
        object.__setattr__(self, "height", _positive(self.height, "height"))

    @classmethod
    def from_mm(cls, width: float, height: float) -> PageSize:
        """A size given in millimetres, converted to points."""
        # Multiplied before it is divided: 72 and the millimetres are both
        # exact, so there is one rounding here instead of two, and the result is
        # the number a caller gets computing it by hand.
        return cls(
            _positive(width, "width") * _POINTS_PER_INCH / _MM_PER_INCH,
            _positive(height, "height") * _POINTS_PER_INCH / _MM_PER_INCH,
        )

    @classmethod
    def from_inches(cls, width: float, height: float) -> PageSize:
        """A size given in inches, converted to points."""
        return cls(
            _positive(width, "width") * _POINTS_PER_INCH,
            _positive(height, "height") * _POINTS_PER_INCH,
        )

    @classmethod
    def by_name(cls, name: str) -> PageSize:
        """The named size *name* ("A4", "letter", "half letter", ...).

        Case, spaces, hyphens and underscores are ignored, so a size read from a
        configuration file or a command line needs no normalising first.
        """
        if not isinstance(name, str):
            raise TypeError("page size name must be a string")
        key = "".join(name.split()).replace("-", "").replace("_", "").upper()
        try:
            return _NAMED[key]
        except KeyError:
            known = ", ".join(sorted(_CANONICAL))
            raise ValueError(f"Unknown page size {name!r}; known sizes: {known}") from None

    def landscape(self) -> PageSize:
        """This size with its longer side across: ``width >= height``."""
        if self.width >= self.height:
            return self
        return PageSize(self.height, self.width)

    def portrait(self) -> PageSize:
        """This size with its longer side up: ``width <= height``."""
        if self.width <= self.height:
            return self
        return PageSize(self.height, self.width)

    def rotated(self) -> PageSize:
        """This size with the two sides swapped, whichever way round it is."""
        return PageSize(self.height, self.width)

    def scaled(self, factor: float) -> PageSize:
        """This size with both sides multiplied by *factor*."""
        scale = _positive(factor, "factor")
        return PageSize(self.width * scale, self.height * scale)

    def __eq__(self, other: object) -> bool:
        """Two sizes are equal when they write the same numbers into a file.

        A PDF holds six decimal places (7.3.3), so an A4 page saved and reloaded
        comes back as 595.275591 x 841.889764 -- the same sheet as
        :attr:`PageSize.A4`, half a nanometre from it in floating point. Comparing
        the rounded pair is what makes ``page.size == PageSize.A4`` true for a
        document that has been through a save, which is the only answer that is
        any use.
        """
        if not isinstance(other, PageSize):
            return NotImplemented
        return self._written() == other._written()

    def __hash__(self) -> int:
        return hash(self._written())

    def _written(self) -> tuple[float, float]:
        """The pair as a file would hold it: six decimal places."""
        return (round(self.width, 6), round(self.height, 6))

    def as_rect(self, x: float = 0.0, y: float = 0.0) -> tuple[float, float, float, float]:
        """This size as a rectangle ``(x, y, x + width, y + height)``.

        The origin defaults to ``(0, 0)``, where a page's own coordinate system
        starts; a ``/MediaBox`` may sit elsewhere and this keeps that possible.
        """
        try:
            x0, y0 = float(x), float(y)
        except (TypeError, ValueError):
            raise TypeError("rectangle origin must be two numbers") from None
        if not (math.isfinite(x0) and math.isfinite(y0)):
            raise ValueError("rectangle origin must be finite")
        return (x0, y0, x0 + self.width, y0 + self.height)


def _positive(value: float, name: str) -> float:
    """*value* as a finite positive float, or a refusal that names the field."""
    if isinstance(value, bool):
        raise TypeError(f"{name} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise TypeError(f"{name} must be a number") from None
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    if number <= 0:
        raise ValueError(f"{name} must be above zero")
    return number


# ISO 216 defines the A series by halving A0, one square metre, and the B
# series between two neighbouring A sizes; both are stated in millimetres.
PageSize.A0 = PageSize.from_mm(841, 1189)
PageSize.A1 = PageSize.from_mm(594, 841)
PageSize.A2 = PageSize.from_mm(420, 594)
PageSize.A3 = PageSize.from_mm(297, 420)
PageSize.A4 = PageSize.from_mm(210, 297)
PageSize.A5 = PageSize.from_mm(148, 210)
PageSize.A6 = PageSize.from_mm(105, 148)
PageSize.B4 = PageSize.from_mm(250, 353)
PageSize.B5 = PageSize.from_mm(176, 250)

# The US sizes are whole inches or halves of one, so these are exact.
PageSize.LETTER = PageSize.from_inches(8.5, 11)
PageSize.LEGAL = PageSize.from_inches(8.5, 14)
PageSize.TABLOID = PageSize.from_inches(11, 17)
PageSize.LEDGER = PageSize.from_inches(17, 11)
PageSize.EXECUTIVE = PageSize.from_inches(7.25, 10.5)
PageSize.STATEMENT = PageSize.from_inches(5.5, 8.5)

#: The sizes :meth:`PageSize.by_name` knows, by their canonical name.
_CANONICAL: dict[str, PageSize] = {
    "A0": PageSize.A0,
    "A1": PageSize.A1,
    "A2": PageSize.A2,
    "A3": PageSize.A3,
    "A4": PageSize.A4,
    "A5": PageSize.A5,
    "A6": PageSize.A6,
    "B4": PageSize.B4,
    "B5": PageSize.B5,
    "LETTER": PageSize.LETTER,
    "LEGAL": PageSize.LEGAL,
    "TABLOID": PageSize.TABLOID,
    "LEDGER": PageSize.LEDGER,
    "EXECUTIVE": PageSize.EXECUTIVE,
    "STATEMENT": PageSize.STATEMENT,
}

# Spellings a caller is as likely to write as the canonical one.
_NAMED: dict[str, PageSize] = {
    **_CANONICAL,
    "HALFLETTER": PageSize.STATEMENT,
    "LEDGER11X17": PageSize.TABLOID,
    "11X17": PageSize.TABLOID,
}
