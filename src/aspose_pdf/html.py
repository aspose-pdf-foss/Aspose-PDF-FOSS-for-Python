"""HTML conversion support for Aspose.PDF Python SDK."""

from __future__ import annotations

from pathlib import Path


class HtmlSaveOptions:
    """Options for saving PDF documents as HTML.

    Passed to :meth:`aspose_pdf.Document.save`, every setting is honoured, as
    the matching argument of :meth:`~aspose_pdf.Document.save_as_html`, or the
    save is refused: nothing here is silently ignored.
    """

    def __init__(self) -> None:
        """Initialize HTML save options with default values."""
        self._split_into_pages: bool = False
        self._resources_directory: str | None = None
        self._xps_intermediate_file: str | Path | None = None
        self._aps_intermediate_file: str | Path | None = None
        self._use_area_clipping: bool = True
        self._max_distance_between_text_lines: float = 1.6

    @property
    def split_into_pages(self) -> bool:
        """Whether each page is written as a file of its own, ``name-1.html`` and on."""
        return self._split_into_pages

    @split_into_pages.setter
    def split_into_pages(self, value: bool) -> None:
        self._split_into_pages = bool(value)

    @property
    def resources_directory(self) -> str | None:
        """Folder the figures are written to as PNG files, linked by relative URL.

        ``None`` (the default) embeds them as ``data:`` URIs. A relative path
        is taken from the folder the HTML is written to.
        """
        return self._resources_directory

    @resources_directory.setter
    def resources_directory(self, value: str | None) -> None:
        self._resources_directory = value

    @property
    def xps_intermediate_file(self) -> str | None:
        """Not supported: the export has no XPS stage. Saving with it set raises."""
        return self._xps_intermediate_file

    @xps_intermediate_file.setter
    def xps_intermediate_file(self, value: str | Path | None) -> None:
        self._xps_intermediate_file = str(value) if value is not None else None

    @property
    def aps_intermediate_file(self) -> str | None:
        """Not supported: the export has no APS stage. Saving with it set raises."""
        return self._aps_intermediate_file

    @aps_intermediate_file.setter
    def aps_intermediate_file(self, value: str | Path | None) -> None:
        self._aps_intermediate_file = str(value) if value is not None else None

    @property
    def use_area_clipping(self) -> bool:
        """Whether content outside the page's visible area is left out (default).

        Text and images anchored outside the crop box -- which no viewer
        shows -- are dropped; ``False`` exports them too. Clipping paths
        inside the page are not taken into account.
        """
        return self._use_area_clipping

    @use_area_clipping.setter
    def use_area_clipping(self, value: bool) -> None:
        self._use_area_clipping = bool(value)

    @property
    def max_distance_between_text_lines(self) -> float:
        """Largest line step, in font sizes, that keeps two lines in one paragraph.

        Measured baseline to baseline: at the default 1.6, lines of 10-point
        text 16 points apart form one paragraph and lines 17 points apart
        two.
        """
        return self._max_distance_between_text_lines

    @max_distance_between_text_lines.setter
    def max_distance_between_text_lines(self, value: float) -> None:
        number = float(value)
        if not number > 0:
            raise ValueError("max_distance_between_text_lines must be positive")
        self._max_distance_between_text_lines = number


class HtmlLoadOptions:
    """Options for loading HTML documents.

    This class provides configuration options for loading HTML files,
    including resource handling and base directory settings.
    """

    def __init__(self, base_dir: str | None = None) -> None:
        """Initialize HTML load options.

        Args:
            base_dir: Base directory for resolving relative paths in the HTML.
        """
        self._base_dir: str | None = base_dir
        self._xps_intermediate_file: str | None = None
        self._aps_intermediate_file: str | None = None
        self._use_area_clipping: bool = True

    @property
    def base_dir(self) -> str | None:
        """Get or set the base directory for resolving relative paths."""
