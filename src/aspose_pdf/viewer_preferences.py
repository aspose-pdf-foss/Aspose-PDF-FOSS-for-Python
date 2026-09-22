"""How a document asks to be opened and shown.

A PDF says what a viewer should do before it draws anything: which panel to
put beside the page, how to arrange the pages, where to jump to, whether to
hide the toolbar, which way the pages read, what to put in the print dialogue
(ISO 32000-1 tables 28 and 150)::

    document.page_mode = PageMode.USE_OUTLINES
    document.page_layout = PageLayout.TWO_COLUMN_LEFT
    document.open_action = FitDestination(page=2)
    document.viewer_preferences.display_doc_title = True

Every entry has a default, and an entry a document leaves out -- or fills in
with something the specification does not allow -- reads as that default,
which is what pdf.js and PDFBox resolve such an entry to.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any

from aspose_pdf.engine import viewer_preferences as engine_prefs
from aspose_pdf.exceptions import PdfValidationException

if TYPE_CHECKING:
    from aspose_pdf.document import Document

__all__ = [
    "DuplexMode",
    "PageBoundary",
    "PageLayout",
    "PageMode",
    "PrintScaling",
    "ReadingDirection",
    "ViewerPreferences",
]


class PageMode(str, Enum):  # noqa: UP042
    """What a viewer shows beside the page when the document opens (``/PageMode``)."""

    USE_NONE = "UseNone"
    """Nothing: the page alone."""

    USE_OUTLINES = "UseOutlines"
    """The bookmarks panel."""

    USE_THUMBS = "UseThumbs"
    """The page thumbnails."""

    FULL_SCREEN = "FullScreen"
    """No window at all -- the document fills the screen."""

    USE_OC = "UseOC"
    """The optional content (layers) panel."""

    USE_ATTACHMENTS = "UseAttachments"
    """The attachments panel."""


class PageLayout(str, Enum):  # noqa: UP042
    """How the pages are arranged (``/PageLayout``)."""

    SINGLE_PAGE = "SinglePage"
    """One page at a time."""

    ONE_COLUMN = "OneColumn"
    """One continuous column."""

    TWO_COLUMN_LEFT = "TwoColumnLeft"
    """Two continuous columns, odd-numbered pages on the left."""

    TWO_COLUMN_RIGHT = "TwoColumnRight"
    """Two continuous columns, odd-numbered pages on the right."""

    TWO_PAGE_LEFT = "TwoPageLeft"
    """Two pages at a time, odd-numbered pages on the left."""

    TWO_PAGE_RIGHT = "TwoPageRight"
    """Two pages at a time, odd-numbered pages on the right."""


class ReadingDirection(str, Enum):  # noqa: UP042
    """The reading order of a two-column or two-page layout (``/Direction``)."""

    LEFT_TO_RIGHT = "L2R"
    RIGHT_TO_LEFT = "R2L"
    """For a document in Arabic, Hebrew, or vertical Japanese."""


class PrintScaling(str, Enum):  # noqa: UP042
    """What the print dialogue starts on (``/PrintScaling``)."""

    APP_DEFAULT = "AppDefault"
    NONE = "None"
    """Print at actual size, with no scaling to the paper."""


class DuplexMode(str, Enum):  # noqa: UP042
    """How the print dialogue starts out handling both sides (``/Duplex``)."""

    SIMPLEX = "Simplex"
    FLIP_SHORT_EDGE = "DuplexFlipShortEdge"
    FLIP_LONG_EDGE = "DuplexFlipLongEdge"


class PageBoundary(str, Enum):  # noqa: UP042
    """A page box, for the entries that say which one to view or print."""

    MEDIA_BOX = "MediaBox"
    CROP_BOX = "CropBox"
    BLEED_BOX = "BleedBox"
    TRIM_BOX = "TrimBox"
    ART_BOX = "ArtBox"


def _member(enum: type[Enum], value: Any, what: str) -> str:
    """*value* as the enum's stored string, whichever form it arrives in."""
    try:
        return str(enum(value).value)
    except ValueError:
        allowed = ", ".join(member.value for member in enum)
        raise PdfValidationException(f"{what} must be one of: {allowed}") from None


class ViewerPreferences:
    """The document's ``/ViewerPreferences``, read and written in place.

    Reading a property a document says nothing about -- or says something the
    specification does not allow -- gives that entry's default: ``False`` for
    the flags, ``CropBox`` for the boxes, ``L2R``, ``AppDefault``, and
    ``None`` where there is nothing to fall back on. Writing creates the
    dictionary; writing ``None`` to one of the optional entries removes just
    that entry, and removing the last one removes the dictionary too.
    """

    __slots__ = ("_document",)

    def __init__(self, document: Document) -> None:
        self._document = document

    # -- the window ---------------------------------------------------------
    @property
    def hide_toolbar(self) -> bool:
        """Hide the viewer's toolbar while the document is open."""
        return self._flag("HideToolbar")

    @hide_toolbar.setter
    def hide_toolbar(self, value: bool) -> None:
        self._set("HideToolbar", bool(value))

    @property
    def hide_menubar(self) -> bool:
        """Hide the viewer's menu bar."""
        return self._flag("HideMenubar")

    @hide_menubar.setter
    def hide_menubar(self, value: bool) -> None:
        self._set("HideMenubar", bool(value))

    @property
    def hide_window_ui(self) -> bool:
        """Hide everything but the page: no scroll bars, no navigation."""
        return self._flag("HideWindowUI")

    @hide_window_ui.setter
    def hide_window_ui(self, value: bool) -> None:
        self._set("HideWindowUI", bool(value))

    @property
    def fit_window(self) -> bool:
        """Resize the window to the first page."""
        return self._flag("FitWindow")

    @fit_window.setter
    def fit_window(self, value: bool) -> None:
        self._set("FitWindow", bool(value))

    @property
    def center_window(self) -> bool:
        """Put the window in the middle of the screen."""
        return self._flag("CenterWindow")

    @center_window.setter
    def center_window(self, value: bool) -> None:
        self._set("CenterWindow", bool(value))

    @property
    def display_doc_title(self) -> bool:
        """Title the window with the document's ``/Title``, not its file name.

        PDF/UA requires this (ISO 14289-1 7.1): a file name is not a title a
        screen reader can announce.
        """
        return self._flag("DisplayDocTitle")

    @display_doc_title.setter
    def display_doc_title(self, value: bool) -> None:
        self._set("DisplayDocTitle", bool(value))

    # -- reading ------------------------------------------------------------
    @property
    def non_full_screen_page_mode(self) -> PageMode:
        """What to show when the document leaves full screen."""
        return PageMode(self._name("NonFullScreenPageMode"))

    @non_full_screen_page_mode.setter
    def non_full_screen_page_mode(self, value: PageMode | str) -> None:
        name = _member(PageMode, value, "non_full_screen_page_mode")
        if name not in engine_prefs.NON_FULL_SCREEN_PAGE_MODES:
            allowed = ", ".join(engine_prefs.NON_FULL_SCREEN_PAGE_MODES)
            raise PdfValidationException(
                f"non_full_screen_page_mode must be one of: {allowed}"
            )
        self._set("NonFullScreenPageMode", name)

    @property
    def direction(self) -> ReadingDirection:
        """Which way a two-column or two-page layout reads."""
        return ReadingDirection(self._name("Direction"))

    @direction.setter
    def direction(self, value: ReadingDirection | str) -> None:
        self._set("Direction", _member(ReadingDirection, value, "direction"))

    @property
    def view_area(self) -> PageBoundary:
        """The box a page is shown in."""
        return PageBoundary(self._name("ViewArea"))

    @view_area.setter
    def view_area(self, value: PageBoundary | str) -> None:
        self._set("ViewArea", _member(PageBoundary, value, "view_area"))

    @property
    def view_clip(self) -> PageBoundary:
        """The box a page's content is clipped to on screen."""
        return PageBoundary(self._name("ViewClip"))

    @view_clip.setter
    def view_clip(self, value: PageBoundary | str) -> None:
        self._set("ViewClip", _member(PageBoundary, value, "view_clip"))

    # -- printing -----------------------------------------------------------
    @property
    def print_area(self) -> PageBoundary:
        """The box a page is printed in."""
        return PageBoundary(self._name("PrintArea"))

    @print_area.setter
    def print_area(self, value: PageBoundary | str) -> None:
        self._set("PrintArea", _member(PageBoundary, value, "print_area"))

    @property
    def print_clip(self) -> PageBoundary:
        """The box a page's content is clipped to when printed."""
        return PageBoundary(self._name("PrintClip"))

    @print_clip.setter
    def print_clip(self, value: PageBoundary | str) -> None:
        self._set("PrintClip", _member(PageBoundary, value, "print_clip"))

    @property
    def print_scaling(self) -> PrintScaling:
        """What the print dialogue's scaling starts on."""
        return PrintScaling(self._name("PrintScaling"))

    @print_scaling.setter
    def print_scaling(self, value: PrintScaling | str) -> None:
        self._set("PrintScaling", _member(PrintScaling, value, "print_scaling"))

    @property
    def duplex(self) -> DuplexMode | None:
        """How to print both sides, or ``None`` to leave it to the viewer."""
        name = self._name("Duplex")
        return None if name is None else DuplexMode(name)

    @duplex.setter
    def duplex(self, value: DuplexMode | str | None) -> None:
        self._set(
            "Duplex", None if value is None else _member(DuplexMode, value, "duplex")
        )

    @property
    def pick_tray_by_pdf_size(self) -> bool | None:
        """Choose the paper tray by page size, or ``None`` to leave it alone."""
        return engine_prefs.pick_tray_by_pdf_size(self._pdf)

    @pick_tray_by_pdf_size.setter
    def pick_tray_by_pdf_size(self, value: bool | None) -> None:
        self._set("PickTrayByPDFSize", None if value is None else bool(value))

    @property
    def num_copies(self) -> int | None:
        """How many copies the print dialogue starts on, or ``None``."""
        return engine_prefs.num_copies(self._pdf)

    @num_copies.setter
    def num_copies(self, value: int | None) -> None:
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 1
        ):
            raise PdfValidationException("num_copies must be a positive integer")
        self._set("NumCopies", value)

    @property
    def print_page_range(self) -> tuple[tuple[int, int], ...]:
        """The page ranges the print dialogue starts on, both ends included.

        Pairs of 0-based page indices, as everywhere else here; the file holds
        them as 1-based page numbers. A range naming a page the document does
        not have makes the whole entry meaningless, and it reads as empty.
        """
        return engine_prefs.print_page_range(self._pdf, len(self._document.pages))

    @print_page_range.setter
    def print_page_range(self, value: Any) -> None:
        if not value:
            self._set("PrintPageRange", None)
            return
        count = len(self._document.pages)
        pairs = []
        for pair in value:
            first, last = pair
            if (
                isinstance(first, bool)
                or not isinstance(first, int)
                or isinstance(last, bool)
                or not isinstance(last, int)
            ):
                raise PdfValidationException("a print range needs whole page indices")
            if not 0 <= first <= last < count:
                raise PdfValidationException(
                    f"a print range must name pages this document has (0 to {count - 1})"
                )
            pairs.append((first, last))
        self._set("PrintPageRange", pairs)

    # -- the whole entry ----------------------------------------------------
    def clear(self) -> None:
        """Remove ``/ViewerPreferences``, leaving every entry at its default."""
        engine_prefs.clear_preferences(self._pdf)

    def __repr__(self) -> str:
        said = [
            f"{name}={value!r}"
            for name, value in (
                ("hide_toolbar", self.hide_toolbar),
                ("hide_menubar", self.hide_menubar),
                ("hide_window_ui", self.hide_window_ui),
                ("fit_window", self.fit_window),
                ("center_window", self.center_window),
                ("display_doc_title", self.display_doc_title),
            )
            if value
        ]
        return f"ViewerPreferences({', '.join(said)})" if said else "ViewerPreferences()"

    # -- plumbing -----------------------------------------------------------
    @property
    def _pdf(self) -> Any:
        self._document._ensure_not_disposed()
        return self._document._engine_pdf

    def _flag(self, key: str) -> bool:
        return engine_prefs.boolean_preference(self._pdf, key)

    def _name(self, key: str) -> str | None:
        return engine_prefs.name_preference(self._pdf, key)

    def _set(self, key: str, value: Any) -> None:
        pdf = self._pdf
        pdf._ensure_cos()
        engine_prefs.set_preference(pdf, key, value)
