"""Glyph-metric advance widths for synthesised appearance text.

The variable-text appearance builders (``field_appearance.py``,
``appearance.py``) emit single-byte (WinAnsi/cp1252) text and need per-character
advance widths to word-wrap and centre it. This module resolves a
``code -> advance`` function (in 1000-unit glyph space) from the bundled
metric-compatible OFL substitute fonts (Liberation, see ``std_font_data.py``),
so Helvetica/Times/Courier text is measured with real glyph metrics instead of
the flat 0.6 em estimate.

Resolution misses (Symbol/ZapfDingbats, unknown families, a missing bundle)
return ``None`` so callers degrade to their flat estimate.
"""

from __future__ import annotations

import struct
from typing import Callable, Dict, Optional

__all__ = ["WidthFn", "substitute_width_fn"]

# code (single-byte, cp1252 domain) -> advance width in 1000-unit glyph space.
WidthFn = Callable[[int], float]

_width_fn_cache: Dict[str, Optional[WidthFn]] = {}


def substitute_width_fn(
    base_font: Optional[str] = "Helvetica",
    *,
    flags: int = 0,
    italic_angle: float = 0.0,
    font_weight: Optional[float] = None,
) -> Optional[WidthFn]:
    """Return a ``code -> advance`` function for *base_font*, or ``None``.

    The font name (plus optional FontDescriptor signals) is mapped to a bundled
    substitute face; codes are decoded as cp1252 (or, for the Symbol and
    ZapfDingbats faces, through their built-in encodings) and looked up in the
    substitute's Unicode cmap. Unmapped codes fall back to a 500-unit advance.
    Results are cached per substitute face.
    """
    from .font_subset import read_unicode_cmap
    from .glyph_outlines import TrueTypeOutlines
    from .std_font_data import (
        load_substitute_sfnt,
        resolve_substitute_key,
        substitute_code_to_unicode,
    )

    key = resolve_substitute_key(
        base_font, flags=flags, italic_angle=italic_angle, font_weight=font_weight
    )
    if key is None:
        return None
    if key in _width_fn_cache:
        return _width_fn_cache[key]

    fn: Optional[WidthFn] = None
    sfnt = load_substitute_sfnt(key)
    if sfnt is not None:
        try:
            outlines = TrueTypeOutlines(sfnt)
            uni = read_unicode_cmap(sfnt) if outlines.ok else {}
        except (struct.error, IndexError, ValueError, TypeError, KeyError):
            outlines, uni = None, {}
        if outlines is not None and outlines.ok and uni:
            upm = outlines.units_per_em or 1000
            builtin = substitute_code_to_unicode(key)

            def fn(code: int, _o=outlines, _u=uni, _upm=upm, _b=builtin) -> float:
                if _b is not None:
                    cp = _b.get(code & 0xFF, code)
                else:
                    try:
                        cp = ord(bytes([code & 0xFF]).decode("cp1252"))
                    except UnicodeDecodeError:
                        cp = code
                gid = _u.get(cp)
                if not gid:
                    return 500.0
                adv = _o.advance_width(gid)
                return adv * 1000.0 / _upm if adv else 500.0

    _width_fn_cache[key] = fn
    return fn
