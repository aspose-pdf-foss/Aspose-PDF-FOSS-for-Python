"""HTML conversion support for Aspose.PDF Python SDK."""

from __future__ import annotations
from pathlib import Path
from typing import Optional, Union


class HtmlSaveOptions:
    """Options for saving PDF documents as HTML.

    This class provides configuration options for the PDF to HTML conversion process,
    including page splitting, resource handling, and other conversion settings.
    """

    def __init__(self) -> None:
        """Initialize HTML save options with default values."""
        self._split_into_pages: bool = False
        self._resources_directory: Optional[str] = None
        self._xps_intermediate_file: Optional[Union[str, Path]] = None
        self._aps_intermediate_file: Optional[Union[str, Path]] = None
        self._use_area_clipping: bool = True
        self._max_distance_between_text_lines: float = 2.0

    @property
    def split_into_pages(self) -> bool:
        """Get or set whether to split the output into separate HTML files per page."""
        return self._split_into_pages

    @split_into_pages.setter
    def split_into_pages(self, value: bool) -> None:
        self._split_into_pages = bool(value)

    @property
    def resources_directory(self) -> Optional[str]:
        """Get or set the directory where resources (images, CSS, etc.) will be saved."""
        return self._resources_directory

    @resources_directory.setter
    def resources_directory(self, value: Optional[str]) -> None:
        self._resources_directory = value

    @property
    def xps_intermediate_file(self) -> Optional[str]:
        """Get or set the path for the intermediate XPS file."""
        return self._xps_intermediate_file

    @xps_intermediate_file.setter
    def xps_intermediate_file(self, value: Optional[Union[str, Path]]) -> None:
        self._xps_intermediate_file = str(value) if value is not None else None

    @property
    def aps_intermediate_file(self) -> Optional[str]:
        """Get or set the path for the intermediate APS file."""
        return self._aps_intermediate_file

    @aps_intermediate_file.setter
    def aps_intermediate_file(self, value: Optional[Union[str, Path]]) -> None:
        self._aps_intermediate_file = str(value) if value is not None else None

    @property
    def use_area_clipping(self) -> bool:
        """Get or set whether to use area clipping for text extraction."""
        return self._use_area_clipping

    @use_area_clipping.setter
    def use_area_clipping(self, value: bool) -> None:
        self._use_area_clipping = bool(value)

    @property
    def max_distance_between_text_lines(self) -> float:
        """Get or set the maximum distance between text lines for grouping."""
        return self._max_distance_between_text_lines

    @max_distance_between_text_lines.setter
    def max_distance_between_text_lines(self, value: float) -> None:
        self._max_distance_between_text_lines = float(value)


class HtmlLoadOptions:
    """Options for loading HTML documents.

    This class provides configuration options for loading HTML files,
    including resource handling and base directory settings.
    """

    def __init__(self, base_dir: Optional[str] = None) -> None:
        """Initialize HTML load options.

        Args:
            base_dir: Base directory for resolving relative paths in the HTML.
        """
        self._base_dir: Optional[str] = base_dir
        self._xps_intermediate_file: Optional[str] = None
        self._aps_intermediate_file: Optional[str] = None
        self._use_area_clipping: bool = True

    @property
    def base_dir(self) -> Optional[str]:
        """Get or set the base directory for resolving relative paths."""
