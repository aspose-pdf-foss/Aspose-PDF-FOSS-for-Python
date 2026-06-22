"""Font source abstractions and repository used by the text subsystem.

This provides a working font discovery system:

* :class:`FontSource` -- base class with a concrete helper that turns raw
  font bytes into :class:`FontDescriptor` objects by parsing the SFNT
  container (TrueType / OpenType / TrueType Collection).
* :class:`FolderFontSource` / :class:`FileFontSource` -- discover fonts from
  the filesystem.
* :class:`MemoryFontSource` -- expose fonts supplied as in-memory bytes.
* :class:`SystemFontSource` -- discover fonts from common platform
  directories.
* :class:`FontRepository` -- aggregate sources, resolve fonts by name, and
  return embeddable font bytes.

Discovery never raises on malformed font files; unreadable or unparseable
entries are skipped (folder sources) or fall back to file-name metadata.
"""

from __future__ import annotations

import os
from pathlib import Path

from aspose_pdf.engine.sfnt import parse_faces
from aspose_pdf.exceptions import FontEmbeddingException
from aspose_pdf.font_registry import FontDescriptor, FontRegistry

__all__ = [
    "FontDescriptor",
    "FontRepository",
    "FontSource",
    "FolderFontSource",
    "FileFontSource",
    "MemoryFontSource",
    "SystemFontSource",
]

# Recognised font file extensions mapped to a fallback type label used when
# the container cannot be parsed (e.g. compressed WOFF/WOFF2 wrappers).
_FONT_EXTENSIONS = {
    ".otf": "OpenType",
    ".ttc": "TrueTypeCollection",
    ".ttf": "TrueType",
    ".woff": "WOFF",
    ".woff2": "WOFF2",
}


class FontSource:
    """Base class for external font providers.

    Subclasses implement :meth:`get_font_definitions`. The base class also
    provides :meth:`_descriptors_from_bytes`, which parses an SFNT font
    program into one descriptor per contained face.
    """

    def __init__(self, priority: int = 0) -> None:
        self.priority = priority

    def get_font_definitions(self) -> list[FontDescriptor]:
        raise NotImplementedError(
            "Font discovery is not implemented by this base class; subclasses "
            "must implement get_font_definitions()"
        )

    @staticmethod
    def _descriptors_from_bytes(
        data: bytes,
        *,
        path: str | None = None,
        fallback_name: str | None = None,
        fallback_type: str | None = None,
        store_data: bool = False,
    ) -> list[FontDescriptor]:
        """Build descriptors for every face contained in *data*.

        If the SFNT container cannot be parsed (e.g. WOFF), a single fallback
        descriptor is produced from *fallback_name* / *fallback_type* when
        available.
        """
        payload = data if store_data else None
        faces = parse_faces(data)
        descriptors: list[FontDescriptor] = []
        for index, face in enumerate(faces):
            name = face.best_name or fallback_name
            if not name:
                continue
            descriptors.append(
                FontDescriptor(
                    name=name,
                    font_type=face.font_type,
                    is_embedded=False,
                    is_standard=False,
                    path=path,
                    face_index=index,
                    family_name=face.family_name or None,
                    subfamily_name=face.subfamily_name or None,
                    full_name=face.full_name or None,
                    postscript_name=face.postscript_name or None,
                    data=payload,
                )
            )

        if descriptors:
            return descriptors

        if fallback_name:
            return [
                FontDescriptor(
                    name=fallback_name,
                    font_type=fallback_type or "Unknown",
                    is_embedded=False,
                    is_standard=False,
                    path=path,
                    data=payload,
                )
            ]
        return []


class FileFontSource(FontSource):
    """Discover the font(s) contained in a single file."""

    def __init__(self, file_path: str | os.PathLike[str], priority: int = 0) -> None:
        super().__init__(priority=priority)
        self.file_path = str(file_path)

    def get_font_definitions(self) -> list[FontDescriptor]:
        path = Path(self.file_path)
        if not path.is_file():
            return []
        fallback_type = _FONT_EXTENSIONS.get(path.suffix.lower())
        try:
            data = path.read_bytes()
        except OSError:
            return []
        return self._descriptors_from_bytes(
            data,
            path=str(path),
            fallback_name=path.stem,
            fallback_type=fallback_type,
        )


class FolderFontSource(FontSource):
    """Collect fonts from a directory (optionally recursing into subfolders)."""

    def __init__(
        self,
        folder_path: str | os.PathLike[str],
        priority: int = 0,
        *,
        scan_subdirectories: bool = False,
    ) -> None:
        super().__init__(priority=priority)
        self.folder_path = str(folder_path)
        self.scan_subdirectories = scan_subdirectories

    def get_font_definitions(self) -> list[FontDescriptor]:
        folder = Path(self.folder_path)
        if not folder.is_dir():
            return []
        paths = (
            folder.rglob("*") if self.scan_subdirectories else folder.iterdir()
        )
        definitions: list[FontDescriptor] = []
        for path in sorted(paths):
            if not path.is_file():
                continue
            if path.suffix.lower() not in _FONT_EXTENSIONS:
                continue
            definitions.extend(FileFontSource(path).get_font_definitions())
        return definitions


class MemoryFontSource(FontSource):
    """Expose a font program supplied as in-memory bytes."""

    def __init__(
        self,
        font_data: bytes,
        priority: int = 0,
        *,
        name: str | None = None,
    ) -> None:
        super().__init__(priority=priority)
        self.font_data = bytes(font_data)
        self.name = name

    def get_font_definitions(self) -> list[FontDescriptor]:
        return self._descriptors_from_bytes(
            self.font_data,
            path=None,
            fallback_name=self.name or "MemoryFont",
            fallback_type="Unknown",
            store_data=True,
        )


class SystemFontSource(FontSource):
    """Collect fonts from common system font directories."""

    def __init__(self, priority: int = 0) -> None:
        super().__init__(priority=priority)

    @staticmethod
    def _directories() -> list[str]:
        if os.name == "nt":
            windir = os.environ.get("WINDIR", r"C:\Windows")
            local = os.environ.get("LOCALAPPDATA", "")
            dirs = [os.path.join(windir, "Fonts")]
            if local:
                dirs.append(os.path.join(local, "Microsoft", "Windows", "Fonts"))
            return dirs
        return [
            "/usr/share/fonts",
            "/usr/local/share/fonts",
            "/Library/Fonts",
            "/System/Library/Fonts",
            os.path.expanduser("~/.fonts"),
            os.path.expanduser("~/.local/share/fonts"),
            os.path.expanduser("~/Library/Fonts"),
        ]

    def get_font_definitions(self) -> list[FontDescriptor]:
        definitions: list[FontDescriptor] = []
        for directory in self._directories():
            source = FolderFontSource(directory, scan_subdirectories=True)
            definitions.extend(source.get_font_definitions())
        return definitions


class FontRepository:
    """Aggregate font sources and resolve fonts by name."""

    _sources: list[FontSource] = [SystemFontSource()]
    _registry = FontRegistry()

    @classmethod
    def add_source(cls, source: FontSource) -> None:
        """Register *source*; sources are queried highest-priority first."""
        cls._sources.append(source)
        cls._sources.sort(key=lambda item: item.priority, reverse=True)

    @classmethod
    def clear_sources(cls) -> None:
        """Remove all registered sources (including the system source)."""
        cls._sources = []

    @classmethod
    def reset_sources(cls) -> None:
        """Restore the default source list (system fonts only)."""
        cls._sources = [SystemFontSource()]

    @classmethod
    def get_sources(cls) -> list[FontSource]:
        """Return the registered sources in query order."""
        return list(cls._sources)

    @classmethod
    def get_available_fonts(cls) -> list[FontDescriptor]:
        """Return all discoverable fonts, de-duplicated across sources."""
        fonts: list[FontDescriptor] = []
        seen: set[tuple[str, str, int]] = set()
        for source in cls._sources:
            for descriptor in source.get_font_definitions():
                key = (
                    descriptor.name.casefold(),
                    descriptor.font_type,
                    descriptor.face_index,
                )
                if key in seen:
                    continue
                seen.add(key)
                fonts.append(descriptor)
        return fonts

    @classmethod
    def find_font(cls, font_name: str) -> FontDescriptor | None:
        """Resolve *font_name* against registered sources, then standards."""
        query = font_name.strip()
        if not query:
            return None
        for descriptor in cls.get_available_fonts():
            if descriptor.matches(query):
                return descriptor
        return cls._registry.search_font_by_name(query)

    # Backwards-compatible alias.
    @classmethod
    def search(cls, font_name: str) -> FontDescriptor | None:
        return cls.find_font(font_name)

    @classmethod
    def open_font(cls, font_name: str) -> bytes | None:
        """Return embeddable font bytes for *font_name*, or ``None``.

        Standard-font matches that have no backing program return ``None``.
        """
        descriptor = cls.find_font(font_name)
        if descriptor is None:
            return None
        try:
            return descriptor.get_font_bytes()
        except FontEmbeddingException:
            return None
