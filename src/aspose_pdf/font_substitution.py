"""Choose where the page renderer looks for a non-embedded font's glyphs.

A PDF may reference a font without embedding its program -- the Standard 14
always, and East Asian documents routinely, because the producer assumes the
reader's system has the face. The renderer ships substitute faces for the
Latin Standard 14 plus Symbol and ZapfDingbats, so anything else (a CJK face, a
non-embedded Wingdings, a Cyrillic or Greek text face outside the bundled
coverage) is drawn as glyph boxes.

:class:`FontSubstitutionOptions` opens that up: point the renderer at font
directories, hand it font programs directly, or let it use the platform's own
font directories. Names are matched against the real ``name`` tables of the
fonts found; when the document's font name is not installed, a composite (CJK)
font falls back to the well-known families for its character collection and
then to any face whose ``cmap`` actually covers the text.

    from aspose_pdf import Document, FontSubstitutionOptions

    with Document("report-cjk.pdf") as document:
        document.font_substitution = FontSubstitutionOptions.system()
        document.render_page(0, dpi=150).save("page-1.png")

Font discovery is **opt-in**: without options the renderer behaves exactly as
before and stays deterministic across machines. Advances come from the PDF's
own ``/Widths`` or ``/W`` array, so a substituted face changes which glyphs are
drawn, never where they sit. (A simple font that declares no ``/Widths`` entry
for a code -- which only the Standard 14 may do -- falls back to the face's own
advance, exactly as it already did for the bundled substitutes.)
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from pathlib import Path

from .exceptions import PdfValidationException

__all__ = ["FontSubstitutionOptions"]


class FontSubstitutionOptions:
    """Font sources the renderer may substitute from.

    Parameters
    ----------
    directories:
        Directories to search, recursively, for ``.ttf``, ``.otf``, ``.ttc``,
        ``.otc``, ``.woff`` and ``.woff2`` files. Searched before system fonts.
        A directory that does not exist is skipped.
    fonts:
        Font programs supplied directly as ``name -> bytes``. They are indexed
        before anything discovered on disk and win ties against it, which keeps
        rendering reproducible regardless of what the machine has installed.
        The key is indexed as a name alongside whatever the program's own
        ``name`` table declares, so a stripped subset with no usable names is
        still matchable under the name you gave it.
    use_system_fonts:
        Also index the platform font directories (the same list
        :class:`~aspose_pdf.SystemFontSource` uses). Off by default -- turning
        it on makes rendering depend on the machine's installed fonts.

    The index is built once, lazily, on first use and reused for every page
    rendered with the same options object, so keep one instance around rather
    than constructing a new one per page.
    """

    __slots__ = ("__weakref__", "_directories", "_fonts", "_use_system_fonts")

    def __init__(
        self,
        directories: Iterable[str | os.PathLike[str]] = (),
        *,
        fonts: Mapping[str, bytes] | None = None,
        use_system_fonts: bool = False,
    ) -> None:
        if isinstance(directories, (str, os.PathLike)):
            directories = (directories,)
        self._directories = tuple(Path(entry) for entry in directories)
        programs: dict[str, bytes] = {}
        for name, data in (fonts or {}).items():
            if not isinstance(data, (bytes, bytearray, memoryview)):
                raise PdfValidationException(
                    f"Font {name!r} must be supplied as bytes, not "
                    f"{type(data).__name__}."
                )
            programs[str(name)] = bytes(data)
        self._fonts = programs
        self._use_system_fonts = bool(use_system_fonts)

    @classmethod
    def system(cls) -> FontSubstitutionOptions:
        """Options that use the platform's installed fonts."""
        return cls(use_system_fonts=True)

    @property
    def directories(self) -> tuple[Path, ...]:
        """The font directories to search, in order."""
        return self._directories

    @property
    def fonts(self) -> Mapping[str, bytes]:
        """Font programs supplied directly, by label."""
        return dict(self._fonts)

    @property
    def use_system_fonts(self) -> bool:
        """Whether the platform font directories are indexed too."""
        return self._use_system_fonts

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(directories={[str(p) for p in self._directories]}, "
            f"fonts={sorted(self._fonts)}, use_system_fonts={self._use_system_fonts})"
        )
