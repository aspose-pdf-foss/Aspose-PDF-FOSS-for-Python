"""Lightweight font registry used by text and import subsystems."""

from __future__ import annotations

from pathlib import Path

from aspose_pdf.engine.woff import decode as decode_woff
from aspose_pdf.exceptions import FontEmbeddingException


class FontDescriptor:
    """Represents a discoverable font.

    A descriptor may be backed by a file on disk (``path``) or by in-memory
    bytes (for fonts supplied directly to a :class:`MemoryFontSource`). When
    backed, :meth:`get_font_bytes` returns the raw font program suitable for
    embedding.
    """

    def __init__(
        self,
        name: str,
        font_type: str = "Standard",
        *,
        is_embedded: bool = False,
        is_standard: bool = True,
        path: str | None = None,
        face_index: int = 0,
        family_name: str | None = None,
        subfamily_name: str | None = None,
        full_name: str | None = None,
        postscript_name: str | None = None,
        data: bytes | None = None,
    ) -> None:
        self.name = name
        self.font_type = font_type
        self.is_embedded = is_embedded
        self.is_standard = is_standard
        self.path = path
        self.face_index = face_index
        self.family_name = family_name
        self.subfamily_name = subfamily_name
        self.full_name = full_name
        self.postscript_name = postscript_name
        self._data = data

    @property
    def has_font_data(self) -> bool:
        """Return ``True`` if a font program can be produced for embedding."""
        return self._data is not None or self.path is not None

    def get_font_bytes(self) -> bytes:
        """Return the embeddable font program bytes for this descriptor.

        WOFF 1.0 programs are transparently unwrapped to their underlying SFNT
        so the returned bytes are always a directly embeddable TrueType /
        OpenType program.

        Raises:
            FontEmbeddingException: if the descriptor is not backed by data or
                a readable file.
        """
        if self._data is not None:
            raw = self._data
        elif self.path is not None:
            try:
                raw = Path(self.path).read_bytes()
            except OSError as exc:
                raise FontEmbeddingException(
                    f"Could not read font file for {self.name!r}: {exc}"
                ) from exc
        else:
            raise FontEmbeddingException(
                f"Font {self.name!r} has no backing file or in-memory data"
            )
        decoded = decode_woff(raw)
        return decoded if decoded is not None else raw

    def matches(self, query: str) -> bool:
        """Return ``True`` if *query* matches any known name (case-insensitive)."""
        needle = query.strip().casefold()
        if not needle:
            return False
        candidates = (
            self.name,
            self.family_name,
            self.full_name,
            self.postscript_name,
        )
        return any(c and c.casefold() == needle for c in candidates)

    def __repr__(self) -> str:
        return f"FontDescriptor(name={self.name!r}, type={self.font_type!r})"


class FontRegistry:
    """Singleton registry for resolving well-known font names."""

    _instance: FontRegistry | None = None

    STANDARD_FONTS = {
        "Arial": "Helvetica",
        "Arial Black": "Helvetica",
        "Comic Sans MS": "Helvetica",
        "Courier": "Courier",
        "Courier New": "Courier",
        "Georgia": "Helvetica",
        "Helvetica": "Helvetica",
        "Lucida Console": "Courier",
        "Lucida Sans Unicode": "Helvetica",
        "Symbol": "Symbol",
        "Times": "TimesRoman",
        "Times New Roman": "TimesRoman",
        "Times Roman": "TimesRoman",
        "TimesNewRoman": "TimesRoman",
        "Verdana": "Helvetica",
        "ZapfDingbats": "ZapfDingbats",
    }

    def __new__(cls) -> FontRegistry:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def search_font_by_name(self, font_name: str) -> FontDescriptor | None:
        normalized = font_name.strip()
        if not normalized:
            return None
        canonical = self.STANDARD_FONTS.get(normalized)
        if canonical is None:
            return None
        return FontDescriptor(
            name=canonical,
            font_type="Standard",
            is_embedded=False,
            is_standard=True,
        )
