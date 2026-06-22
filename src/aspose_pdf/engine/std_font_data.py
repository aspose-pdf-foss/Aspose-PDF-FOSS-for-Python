"""Resolve and load bundled substitute fonts for the PDF Standard-14 fonts.

The Standard-14 fonts (Helvetica/Times/Courier families, Symbol, ZapfDingbats)
are never embedded in a PDF, so the page renderer has no outline data for them
and falls back to drawing glyph boxes. To render real glyphs we ship
metric-compatible open fonts (Liberation, SIL OFL 1.1; see
``data/fonts/README.md``) subset to a Latin Unicode set and zlib-compressed.

This module maps a PDF base-font name -- optionally refined by FontDescriptor
signals -- to one of twelve bundled faces (``{sans,serif,mono}`` x
``{regular,bold,italic,bolditalic}``) and decompresses it on demand with the
stdlib ``zlib`` module. Symbol and ZapfDingbats have no metric-compatible OFL
source and resolve to ``None`` so the renderer keeps drawing boxes for them.
"""

from __future__ import annotations

import re
import zlib
from importlib.resources import files
from typing import Optional

__all__ = [
    "resolve_substitute_key",
    "load_substitute_sfnt",
    "strip_subset_prefix",
]

# FontDescriptor /Flags bits (PDF 32000-1 Table 121), as 0-based masks.
_FLAG_FIXED_PITCH = 1 << 0
_FLAG_SERIF = 1 << 1
_FLAG_SYMBOLIC = 1 << 2
_FLAG_ITALIC = 1 << 6
_FLAG_FORCE_BOLD = 1 << 18

_SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")

# Substring keywords on the separator-stripped, lower-cased font name.
_MONO_KEYS = ("courier", "mono", "consol", "inconsolata", "fixedsys")
_SERIF_KEYS = (
    "times", "serif", "roman", "georgia", "garamond", "minion", "palatino",
    "bookantiqua", "century", "cambria", "constantia", "caslon", "didot",
)
_SANS_KEYS = (
    "helvetica", "arial", "verdana", "tahoma", "segoe", "calibri", "candara",
    "trebuchet", "geneva", "optima", "frutiger", "univers", "gillsans",
)


def strip_subset_prefix(name: str) -> str:
    """Drop the ``ABCDEF+`` subset tag that prefixes embedded-subset names."""
    return _SUBSET_PREFIX.sub("", name)


def _is_symbolic_standard(name: str) -> bool:
    """True for Symbol / ZapfDingbats, which have no OFL substitute."""
    return name in ("symbol", "zapfdingbats", "dingbats")


def _family(name: str, flags: int) -> Optional[str]:
    """Pick ``sans`` / ``serif`` / ``mono`` from the name, then the flags.

    Returns ``None`` when neither the name nor the flags give a confident family
    *and* the font is symbolic -- substituting a Latin face for an unrecognised
    symbol font (e.g. a non-embedded Wingdings) would draw the wrong glyphs, so
    the caller keeps its box fallback instead.
    """
    if any(k in name for k in _MONO_KEYS):
        return "mono"
    if "sans" in name:  # e.g. "sans-serif", "ptsans" -- sans wins over serif
        return "sans"
    if any(k in name for k in _SERIF_KEYS):
        return "serif"
    if any(k in name for k in _SANS_KEYS):
        return "sans"
    # No family signal in the name: trust the descriptor flags.
    if flags & _FLAG_FIXED_PITCH:
        return "mono"
    if flags & _FLAG_SERIF:
        return "serif"
    if flags & _FLAG_SYMBOLIC:
        return None
    return "sans"


def resolve_substitute_key(
    base_font: Optional[str],
    *,
    flags: int = 0,
    italic_angle: float = 0.0,
    font_weight: Optional[float] = None,
) -> Optional[str]:
    """Return a bundled substitute key for *base_font*, or ``None``.

    *base_font* is the PDF ``/BaseFont`` name (a subset prefix is stripped).
    The FontDescriptor signals refine the choice when the name is ambiguous or
    missing. Returns ``None`` for Symbol/ZapfDingbats (no metric-compatible OFL
    source) so the caller keeps its box fallback.
    """
    raw = strip_subset_prefix(base_font or "")
    # Normalise: lower-case and drop separators so "Times-BoldItalic",
    # "Times New Roman,BoldItalic" and "TimesNewRomanPS-BoldItalicMT" align.
    name = re.sub(r"[\s,_+-]", "", raw).lower()
    if _is_symbolic_standard(name):
        return None

    family = _family(name, flags)
    if family is None:
        return None
    italic = (
        "italic" in name
        or "oblique" in name
        or bool(flags & _FLAG_ITALIC)
        or abs(italic_angle) > 1e-6
    )
    bold = (
        "bold" in name
        or "black" in name
        or "heavy" in name
        or bool(flags & _FLAG_FORCE_BOLD)
        or (font_weight is not None and font_weight >= 600)
    )
    if bold and italic:
        style = "bolditalic"
    elif bold:
        style = "bold"
    elif italic:
        style = "italic"
    else:
        style = "regular"
    return f"{family}-{style}"


_sfnt_cache: dict[str, Optional[bytes]] = {}


def load_substitute_sfnt(key: Optional[str]) -> Optional[bytes]:
    """Decompress the bundled SFNT for *key* (e.g. ``"sans-bold"``), or ``None``.

    Results are cached. A missing or unknown key returns ``None`` rather than
    raising, so a resolution miss degrades to the renderer's box fallback.
    """
    if not key:
        return None
    if key in _sfnt_cache:
        return _sfnt_cache[key]
    sfnt: Optional[bytes] = None
    try:
        resource = files(__package__).joinpath("data", "fonts", f"{key}.ttf.zlib")
        sfnt = zlib.decompress(resource.read_bytes())
    except (FileNotFoundError, OSError, zlib.error, ValueError):
        sfnt = None
    _sfnt_cache[key] = sfnt
    return sfnt
