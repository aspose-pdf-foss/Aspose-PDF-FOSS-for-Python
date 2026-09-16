"""Keep content appended to a page out of the state the page's content leaves.

Everything a content stream draws inherits the graphics state as the stream
before it left it (ISO 32000-1 8.4.2): the current transformation matrix, the
clipping path, the dash pattern, colours, and -- since 9.3.1 makes it graphics
state too -- the text state, rendering mode included. Content appended to an
existing page is drawn *after* the page's own content, so a ``cm`` the page
never undid, a clipping path it left in force, a ``3 Tr`` or a dash pattern
applies to it as well: text added "at (300, 400)" lands off the page, a
rectangle is clipped away, a solid line comes out dashed.

The remedy every writer uses is to save the state before the page's content
and restore it after, so that what follows starts from the initial state. This
module decides whether a page needs that, and how many restores it takes.

A page that leaves nothing behind -- every page this library authors, since
each fragment it writes is its own ``q ... Q`` -- is left exactly as it is.
"""

from __future__ import annotations

from dataclasses import dataclass

from aspose_pdf.load_limits import _LoadBudget

from .auto_tag import _tokens

__all__ = ["Isolation", "isolation_for"]

# Operators that change the graphics state (ISO 32000-1 table 57), the colour
# operators (table 74), the clipping operators (table 61) and the text state
# operators (table 105). Everything else a content stream says either paints
# with the state, builds a path, moves within a text object, or is undone by
# the operator that closes it.
_STATE_OPERATORS = frozenset(
    {
        "cm", "w", "J", "j", "M", "d", "ri", "i", "gs",
        "CS", "cs", "SC", "SCN", "sc", "scn", "G", "g", "RG", "rg", "K", "k",
        "W", "W*",
        "Tc", "Tw", "Tz", "TL", "Tf", "Tr", "Ts",
    }
)  # fmt: skip


@dataclass(frozen=True)
class Isolation:
    """What to put around a page's content before appending to it.

    ``opening`` goes before the page's content and ``closing`` after it, ahead
    of whatever is appended. Both are empty for a page that leaves the
    initial state as it found it.
    """

    opening: bytes = b""
    closing: bytes = b""
    #: False when the page leaves state behind that no ``q``/``Q`` can undo
    #: without changing how the page itself draws (see :func:`isolation_for`);
    #: the caller says so rather than pretend the addition is safe.
    complete: bool = True

    @property
    def needed(self) -> bool:
        return bool(self.opening or self.closing)


def isolation_for(content: bytes, *, budget: _LoadBudget) -> Isolation:
    """Work out the ``q``/``Q`` (and ``ET``) that isolate *content*.

    The stream is read the way a viewer executes it: a ``Q`` with nothing to
    restore is ignored, so it cannot close a level. A level is *dirty* once a
    state operator runs in it and clean again when the ``Q`` that closes it
    runs; the content leaks exactly when a dirty level is still open at its
    end -- level 0 being the page's own.

    * Levels the content opened and never closed are closed by as many ``Q``.
    * A dirty level 0 needs a ``q`` in front of the whole content and one more
      ``Q`` after it.
    * A text object left open is ended first, since ``Q`` is not allowed
      inside one.

    A stray ``Q`` -- one with nothing to restore -- is ignored by a viewer, but
    once a ``q`` stands in front of the content it has something to restore,
    and restores it. While level 0 is still clean that changes nothing (the
    saved state *is* the current one), so each such ``Q`` is simply given a
    ``q`` of its own to consume. After level 0 has been changed it would undo
    the page's own state and change how the page draws; then level 0 is left
    alone, only the open levels are closed, and the result is marked
    incomplete.
    """
    dirty = [False]
    strays = 0
    unrestorable = False
    in_text = False
    for token, _start, _end in _tokens(content, budget=budget):
        if token == "q":
            dirty.append(False)
        elif token == "Q":
            if len(dirty) > 1:
                dirty.pop()
            elif dirty[0]:
                unrestorable = True
            else:
                strays += 1
        elif token == "BT":
            in_text = True
        elif token == "ET":
            in_text = False
        elif token in _STATE_OPERATORS:
            dirty[-1] = True

    if not in_text and not any(dirty):
        return Isolation()

    open_levels = len(dirty) - 1
    wrap = dirty[0] and not unrestorable
    closing = (b"ET\n" if in_text else b"") + b"Q\n" * (open_levels + int(wrap))
    return Isolation(
        opening=b"q\n" * (strays + 1) if wrap else b"",
        closing=closing,
        complete=not (dirty[0] and unrestorable),
    )
