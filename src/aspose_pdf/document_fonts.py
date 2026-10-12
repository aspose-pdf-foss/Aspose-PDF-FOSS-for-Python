"""What fonts a document actually uses.

:class:`~aspose_pdf.font_repository.FontRepository` answers the other question
-- what fonts are available on *this machine*. Nothing answered this one: a
document knows exactly which fonts it draws with, whether it carries them, and
where each is used, and none of it was reachable without walking the COS graph
by hand.

It is the question behind most font trouble. *Will this render the same
elsewhere?* -- only if every font is embedded. *Why is this file 40 MB?* --
look at the program sizes. *Why is the text unsearchable?* -- a symbolic font
with no ``/ToUnicode``. *What has to be licensed?* -- the embedded programs,
by name.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["DocumentFont"]

#: Program formats, by the key the descriptor carries them under and, for
#: ``/FontFile3``, the stream's own ``/Subtype`` (ISO 32000-1 tables 122, 126).
PROGRAM_FORMATS = {
    "FontFile": "Type 1",
    "FontFile2": "TrueType",
    "FontFile3": "compact",
}


@dataclass(frozen=True)
class DocumentFont:
    """One font the document draws with.

    Two font dictionaries that say the same thing -- the same ``/BaseFont``,
    the same kind, the same encoding, the same program -- are one font here,
    with the pages of both: a document whose every page carries its own
    ``/Helv`` dictionary uses one font, not twenty.

    Attributes
    ----------
    base_font : str
        The ``/BaseFont`` name exactly as the file gives it, subset prefix and
        all (``"ABCDEF+Arial"``). Empty when the font names none.
    name : str
        The same with the subset prefix removed (``"Arial"``) -- the name to
        show a person, and to match against a font you have.
    subtype : str
        ``"Type1"``, ``"TrueType"``, ``"Type0"``, ``"Type3"`` or ``"MMType1"``
        (table 110).
    descendant_subtype : str or None
        For a composite font, the kind of its descendant --
        ``"CIDFontType0"`` (compact outlines) or ``"CIDFontType2"``
        (TrueType outlines). ``None`` for every other kind.
    encoding : str or None
        The encoding the font names: a predefined one such as
        ``"WinAnsiEncoding"`` or ``"Identity-H"``, or the ``/BaseEncoding`` of
        an encoding dictionary. ``None`` when it names none, where a reader
        falls back to the font program's own built-in encoding.
    has_differences : bool
        Whether an encoding dictionary remaps individual codes
        (``/Differences``). Such a font draws by its own rules, so a code is
        not the character the encoding's name suggests.
    embedded : bool
        Whether the glyphs are **in the file**, so the page draws the same
        anywhere. A font that is not embedded is drawn with whatever the
        reader happens to find, which is what PDF/A, PDF/UA and PDF/X forbid;
        :meth:`aspose_pdf.Document.embed_fonts` can often supply one.

        A **Type 3** font is embedded with no *program*: its glyphs are the
        content streams of ``/CharProcs`` (9.6.5), so there is nothing to find
        elsewhere and nothing missing. Its ``program_key`` and
        ``program_format`` are therefore ``None`` while this is ``True``.
    program_key : str or None
        Which descriptor entry carries the program -- ``"FontFile"``,
        ``"FontFile2"`` or ``"FontFile3"`` -- or ``None`` when none does.
    program_format : str or None
        What kind of program it is: ``"Type 1"``, ``"TrueType"``,
        ``"Type1C"``, ``"CIDFontType0C"``, ``"OpenType"``, or ``"compact"``
        for a ``/FontFile3`` that does not say.
    program_bytes : int or None
        How big the font program **is**, with its filters undone -- the number
        that says whether a face is a small subset or a whole CJK font. Taken
        from ``/Length1`` where the file states it (table 122) and by decoding
        otherwise; ``None`` for a program this build cannot decode.
    stored_bytes : int or None
        What the program costs **this file**: the bytes as they sit in it,
        compressed by whatever filter the producer used. This is the number
        behind "why is this PDF 40 MB", and it is usually much smaller than
        *program_bytes*.
    is_subset : bool
        Whether the name carries a subset tag -- six capitals and a ``+``
        (9.6.4) -- meaning the program holds only the glyphs this document
        uses. Such a program cannot be reused for other text.
    is_standard_14 : bool
        Whether this is one of the fourteen fonts every reader has
        (Helvetica, Times, Courier, Symbol, ZapfDingbats and their styles), for
        which not embedding has always been legal outside the strict standards.
    has_to_unicode : bool
        Whether the font carries a ``/ToUnicode`` map, which is what lets its
        text be extracted, searched and copied as characters rather than codes.
    page_indexes : tuple of int
        The **zero-based** pages the font is used on, in order, matching
        ``document.pages[i]``. Empty for a font reached from nowhere on a page
        -- see *locations*.
    locations : tuple of str
        Where the font was reached, in words: ``"page 3"``, ``"an annotation
        appearance on page 1"``, ``"the form's default resources"``. The last
        is why *page_indexes* can be empty while the font is genuinely used:
        the AcroForm's ``/DR`` fonts draw field appearances, and a standards
        checker counts them.
    """

    base_font: str
    name: str
    subtype: str
    descendant_subtype: str | None = None
    encoding: str | None = None
    has_differences: bool = False
    embedded: bool = False
    program_key: str | None = None
    program_format: str | None = None
    program_bytes: int | None = None
    stored_bytes: int | None = None
    is_subset: bool = False
    is_standard_14: bool = False
    has_to_unicode: bool = False
    page_indexes: tuple[int, ...] = ()
    locations: tuple[str, ...] = field(default=())

    @property
    def is_composite(self) -> bool:
        """Whether this is a composite (Type 0) font, whose codes select CIDs."""
        return self.subtype == "Type0"

    @property
    def pages(self) -> tuple[int, ...]:
        """The **one-based** page numbers, for showing a person."""
        return tuple(index + 1 for index in self.page_indexes)

    def __str__(self) -> str:
        state = "embedded" if self.embedded else "not embedded"
        if self.embedded and self.program_format:
            state = f"embedded {self.program_format}"
        where = (
            f"pages {', '.join(str(p) for p in self.pages)}"
            if self.pages
            else ", ".join(self.locations) or "nowhere"
        )
        return f"{self.name or '(unnamed)'} ({self.subtype}, {state}) on {where}"
