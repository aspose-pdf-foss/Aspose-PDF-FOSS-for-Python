"""Colour and gradient support for PDF documents.

A :class:`Color` is a colour in one of the three device spaces an authored
content stream can set without naming a colour space first (ISO 32000-1 8.6.8):
DeviceGray, DeviceRGB and DeviceCMYK. It can be handed to anything that takes a
colour -- ``Page.add_text``, ``Page.draw_rectangle``, ``Page.draw_line``, a
stamp, a redaction overlay -- in place of the plain component tuple those also
accept::

    page.draw_rectangle(x, y, w, h, fill_color=Color.cmyk(0, 0.2, 1, 0.05))
    page.add_text("warning", 72, 700, color=Color.from_hex("#cc3300"))
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TypeAlias

from aspose_pdf.exceptions import PdfValidationException, UnsupportedFeatureException

__all__ = ["Color", "ColorValue", "GradientAxialShading", "Point"]

#: What any colour argument in the package accepts: a :class:`Color`, 1/3/4
#: components as a sequence, a single number for grey, or a ``"#rrggbb"`` string.
ColorValue: TypeAlias = "Color | Sequence[float] | str | float"


@dataclass
class Point:
    """Represents a point in 2D space."""

    x: float = 0.0
    y: float = 0.0

    def __repr__(self) -> str:
        return f"Point(x={self.x}, y={self.y})"


class GradientAxialShading:
    """Represents axial (linear) gradient shading.

    This class defines a gradient that varies along a line between two points.

    It is a value object only: a gradient **fill cannot be authored** -- there is
    no shading-pattern writer for it to feed -- so a :class:`Color` carrying one
    raises :class:`~aspose_pdf.exceptions.UnsupportedFeatureException` when a
    drawing operation asks for its components. Axial shadings already *in* a
    document are rendered, exported and read as usual.
    """

    def __init__(
        self,
        start_color: Color,
        end_color: Color,
        start: Point | None = None,
        end: Point | None = None,
    ) -> None:
        """Initialize axial gradient shading.

        Args:
            start_color: The color at the start point.
            end_color: The color at the end point.
            start: The start point of the gradient line. Defaults to (0, 0).
            end: The end point of the gradient line. Defaults to (1, 1).
        """
        self._start_color = start_color
        self._end_color = end_color
        self._start = start if start is not None else Point(0, 0)
        self._end = end if end is not None else Point(1, 1)

    @property
    def start_color(self) -> Color:
        """Get the start color."""
        return self._start_color

    @property
    def end_color(self) -> Color:
        """Get the end color."""
        return self._end_color

    @property
    def start(self) -> Point:
        """Get the start point."""
        return self._start

    @property
    def end(self) -> Point:
        """Get the end point."""
        return self._end


class Color:
    """A colour in one of the three PDF device spaces, or a gradient pattern.

    Build one with :meth:`gray`, :meth:`rgb`, :meth:`cmyk` or :meth:`from_hex`
    rather than through the constructor, whose signature is the one ported code
    uses (``Color(pattern, r, g, b)``) and so keeps RGB in three separate
    arguments.

    :attr:`components` is what a drawing operation reads: one number for grey,
    three for RGB, four for CMYK. Channels are 0..1; :meth:`rgb` and :meth:`gray`
    also take 0..255, as every colour entry point in the package does.
    """

    __slots__ = ("_components", "_pattern_color_space")

    def __init__(
        self,
        pattern_color_space: GradientAxialShading | None = None,
        r: float = 0.0,
        g: float = 0.0,
        b: float = 0.0,
    ) -> None:
        """Initialize a color.

        Args:
            pattern_color_space: Optional gradient shading pattern.
            r: Red component (0.0-1.0) for solid colors.
            g: Green component (0.0-1.0) for solid colors.
            b: Blue component (0.0-1.0) for solid colors.
        """
        self._pattern_color_space = pattern_color_space
        self._components: tuple[float, ...] = _channels((r, g, b))

    # -- construction --------------------------------------------------------

    @classmethod
    def gray(cls, value: float) -> Color:
        """A DeviceGray colour: ``0`` is black, ``1`` (or ``255``) is white."""
        return cls._of(_channels((value,)))

    @classmethod
    def rgb(cls, r: float, g: float, b: float) -> Color:
        """A DeviceRGB colour, in 0..1 or 0..255."""
        return cls._of(_channels((r, g, b)))

    @classmethod
    def cmyk(cls, c: float, m: float, y: float, k: float) -> Color:
        """A DeviceCMYK colour, in 0..1 -- the ink fractions, not percentages."""
        return cls._of(_channels((c, m, y, k)))

    @classmethod
    def from_hex(cls, value: str) -> Color:
        """A DeviceRGB colour from ``"#rgb"`` or ``"#rrggbb"``."""
        return cls._of(_channels(value))

    @classmethod
    def _of(cls, components: tuple[float, ...]) -> Color:
        """A colour holding *components* exactly, whatever their number."""
        color = cls.__new__(cls)
        color._pattern_color_space = None
        color._components = components
        return color

    # -- the colour itself ---------------------------------------------------

    @property
    def components(self) -> tuple[float, ...]:
        """The colour's channels: 1 for grey, 3 for RGB, 4 for CMYK.

        Raises :class:`~aspose_pdf.exceptions.UnsupportedFeatureException` for a
        colour carrying a gradient, which has no components to paint with and no
        writer to author the pattern it names.
        """
        if self._pattern_color_space is not None:
            raise UnsupportedFeatureException(
                "A gradient (pattern) colour cannot be authored: there is no "
                "shading-pattern writer for it. Use a solid Color, or draw the "
                "gradient as the content you want."
            )
        return self._components

    @property
    def color_space(self) -> str:
        """The device space the colour is in, as its PDF name."""
        return {1: "DeviceGray", 3: "DeviceRGB", 4: "DeviceCMYK"}[
            len(self.components)
        ]

    @property
    def pattern_color_space(self) -> GradientAxialShading | None:
        """Get the pattern color space (gradient)."""
        return self._pattern_color_space

    @property
    def r(self) -> float:
        """Get the red component."""
        return self._channel(0)

    @property
    def g(self) -> float:
        """Get the green component."""
        return self._channel(1)

    @property
    def b(self) -> float:
        """Get the blue component."""
        return self._channel(2)

    def _channel(self, index: int) -> float:
        """One RGB channel, or the grey level when that is all there is.

        ``r``, ``g`` and ``b`` are what ported code reads, so a grey colour
        answers its level for all three rather than raising: that is the colour
        it is. A CMYK colour has no RGB channel to hand back and says so instead
        of converting behind the caller's back.
        """
        components = self._components if self._pattern_color_space is None else ()
        if len(components) == 3:
            return components[index]
        if len(components) == 1:
            return components[0]
        raise PdfValidationException(
            "r/g/b are the channels of a grey or RGB colour; this one is "
            f"{self.color_space if components else 'a gradient'}."
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Color):
            return NotImplemented
        return (
            self._pattern_color_space is other._pattern_color_space
            and self._components == other._components
        )

    def __hash__(self) -> int:
        return hash((id(self._pattern_color_space), self._components))

    def __repr__(self) -> str:
        if self._pattern_color_space is not None:
            return "Color(pattern_color_space=<gradient>)"
        values = ", ".join(f"{value:g}" for value in self._components)
        return f"Color.{self.color_space[6:].lower()}({values})"


def _channels(value: str | Sequence[float]) -> tuple[float, ...]:
    """*value* as 0..1 channels, by the rule every colour entry point uses."""
    from aspose_pdf.engine.content_authoring import normalize_color

    return normalize_color(value)
