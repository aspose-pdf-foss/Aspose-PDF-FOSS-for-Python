"""How a document asks to be opened and shown (ISO 32000-1 tables 28 and 150).

The catalog carries four things a viewer reads before it draws anything:
``/PageMode`` (which panel stands beside the page), ``/PageLayout`` (how the
pages are arranged), ``/OpenAction`` (where to go, or what to do, on opening)
and ``/ViewerPreferences`` (the window, the reading direction, the print
dialogue).

An entry that is missing, of the wrong type, or not one of the names the
specification lists reads as that entry's *default*. pdf.js, PDFBox and MuPDF
all resolve a *missing* entry that way. For a value the specification does not
allow, the two that give these entries a type agree on the default too --
pdf.js's ``getViewerPreferences`` warns and drops a bad boolean or number and
substitutes the default for a bad name, and PDFBox's ``PageMode`` and
``PageLayout`` getters fall back to theirs. MuPDF, and PDFBox's untyped
getters, hand back the unknown name as the string it is, which a typed reading
cannot do. All three read a ``/ViewerPreferences`` that is not a dictionary as
no preferences at all.

``/PrintPageRange`` follows pdf.js exactly: the whole entry is ignored unless
it is an even-length array of whole page numbers, each pair ascending and
within the document. Page numbers are 1-based in the file and are handed out
here as the 0-based indices the rest of this library speaks in.
"""

from __future__ import annotations

from typing import Any

from .cos import (
    PdfArray,
    PdfBoolean,
    PdfDictionary,
    PdfName,
    PdfNumber,
)

# -- the value sets of tables 28 and 150 ------------------------------------
PAGE_MODES = (
    "UseNone",
    "UseOutlines",
    "UseThumbs",
    "FullScreen",
    "UseOC",
    "UseAttachments",
)
PAGE_LAYOUTS = (
    "SinglePage",
    "OneColumn",
    "TwoColumnLeft",
    "TwoColumnRight",
    "TwoPageLeft",
    "TwoPageRight",
)
#: ``/NonFullScreenPageMode`` says what to show *after* full screen, so the two
#: modes that are not a panel beside the page are not among its values.
NON_FULL_SCREEN_PAGE_MODES = ("UseNone", "UseOutlines", "UseThumbs", "UseOC")
DIRECTIONS = ("L2R", "R2L")
PRINT_SCALINGS = ("AppDefault", "None")
DUPLEXES = ("Simplex", "DuplexFlipShortEdge", "DuplexFlipLongEdge")
BOUNDARIES = ("MediaBox", "CropBox", "BleedBox", "TrimBox", "ArtBox")

#: ``/ViewerPreferences`` entries whose value is a name: the values allowed and
#: the default the entry reads as when it is absent or says something else.
#: ``Duplex`` has no default in the specification -- a viewer uses whatever it
#: would have used anyway -- so it reads as ``None``.
NAME_PREFERENCES: dict[str, tuple[tuple[str, ...], str | None]] = {
    "NonFullScreenPageMode": (NON_FULL_SCREEN_PAGE_MODES, "UseNone"),
    "Direction": (DIRECTIONS, "L2R"),
    "ViewArea": (BOUNDARIES, "CropBox"),
    "ViewClip": (BOUNDARIES, "CropBox"),
    "PrintArea": (BOUNDARIES, "CropBox"),
    "PrintClip": (BOUNDARIES, "CropBox"),
    "PrintScaling": (PRINT_SCALINGS, "AppDefault"),
    "Duplex": (DUPLEXES, None),
}

#: Entries that are false unless the document says otherwise.
BOOLEAN_PREFERENCES = (
    "HideToolbar",
    "HideMenubar",
    "HideWindowUI",
    "FitWindow",
    "CenterWindow",
    "DisplayDocTitle",
)


def _catalog(pdf: Any) -> PdfDictionary | None:
    doc = getattr(pdf, "_cos_doc", None)
    if doc is None:
        return None
    root = pdf._resolve(doc.trailer.mapping.get(PdfName("Root")))
    return root if isinstance(root, PdfDictionary) else None


def _preferences(pdf: Any, *, create: bool = False) -> PdfDictionary | None:
    catalog = _catalog(pdf)
    if catalog is None:
        return None
    preferences = pdf._resolve(catalog.mapping.get(PdfName("ViewerPreferences")))
    if isinstance(preferences, PdfDictionary):
        return preferences
    if not create:
        return None
    preferences = PdfDictionary()
    catalog.mapping[PdfName("ViewerPreferences")] = preferences
    return preferences


def _name_of(pdf: Any, value: Any) -> str | None:
    value = pdf._resolve(value)
    return value.name.lstrip("/") if isinstance(value, PdfName) else None


# -- /PageMode and /PageLayout ----------------------------------------------
def catalog_name(pdf: Any, key: str, allowed: tuple[str, ...], default: str) -> str:
    """The catalog's *key*, or *default* when it says nothing usable."""
    catalog = _catalog(pdf)
    if catalog is None:
        return default
    name = _name_of(pdf, catalog.mapping.get(PdfName(key)))
    return name if name in allowed else default


def set_catalog_name(pdf: Any, key: str, value: str) -> None:
    """Write the catalog's *key* as a name."""
    catalog = _catalog(pdf)
    if catalog is not None:
        catalog.mapping[PdfName(key)] = PdfName(value)


# -- /ViewerPreferences ------------------------------------------------------
def boolean_preference(pdf: Any, key: str) -> bool:
    """One of the flag entries; false unless the document really says true."""
    preferences = _preferences(pdf)
    if preferences is None:
        return False
    value = pdf._resolve(preferences.mapping.get(PdfName(key)))
    return value.value if isinstance(value, PdfBoolean) else False


def name_preference(pdf: Any, key: str) -> str | None:
    """One of the name entries, resolved to its default where unusable."""
    allowed, default = NAME_PREFERENCES[key]
    preferences = _preferences(pdf)
    if preferences is None:
        return default
    name = _name_of(pdf, preferences.mapping.get(PdfName(key)))
    return name if name in allowed else default


def pick_tray_by_pdf_size(pdf: Any) -> bool | None:
    """``/PickTrayByPDFSize``; ``None`` when the document leaves it to the viewer."""
    preferences = _preferences(pdf)
    if preferences is None:
        return None
    value = pdf._resolve(preferences.mapping.get(PdfName("PickTrayByPDFSize")))
    return value.value if isinstance(value, PdfBoolean) else None


def num_copies(pdf: Any) -> int | None:
    """``/NumCopies``; ``None`` unless it is a whole number of at least one."""
    preferences = _preferences(pdf)
    if preferences is None:
        return None
    value = pdf._resolve(preferences.mapping.get(PdfName("NumCopies")))
    if not isinstance(value, PdfNumber):
        return None
    number = value.value
    if number != int(number) or int(number) < 1:
        return None
    return int(number)


def print_page_range(pdf: Any, page_count: int) -> tuple[tuple[int, int], ...]:
    """``/PrintPageRange`` as pairs of 0-based page indices, both inclusive.

    The whole entry goes if any part of it does not describe pages this
    document has, which is what pdf.js does with it.
    """
    preferences = _preferences(pdf)
    if preferences is None:
        return ()
    value = pdf._resolve(preferences.mapping.get(PdfName("PrintPageRange")))
    if not isinstance(value, PdfArray) or not value.items or len(value.items) % 2:
        return ()
    numbers: list[int] = []
    for item in value.items:
        item = pdf._resolve(item)
        if not isinstance(item, PdfNumber) or item.value != int(item.value):
            return ()
        numbers.append(int(item.value))
    pairs = tuple(zip(numbers[0::2], numbers[1::2]))
    for first, last in pairs:
        if not 1 <= first <= last <= page_count:
            return ()
    return tuple((first - 1, last - 1) for first, last in pairs)


def set_preference(pdf: Any, key: str, value: Any) -> None:
    """Write one ``/ViewerPreferences`` entry; ``None`` removes it.

    Removing the last entry removes the dictionary too, rather than leaving an
    empty one saying nothing.
    """
    # Removing from a document that has no dictionary does not make one: the
    # result would be the same either way, since an empty one goes again below.
    preferences = _preferences(pdf, create=value is not None)
    if preferences is None:
        return
    pdf_key = PdfName(key)
    if value is None:
        preferences.mapping.pop(pdf_key, None)
        if not preferences.mapping:
            catalog = _catalog(pdf)
            if catalog is not None:
                catalog.mapping.pop(PdfName("ViewerPreferences"), None)
        return
    if isinstance(value, bool):
        preferences.mapping[pdf_key] = PdfBoolean(value)
    elif isinstance(value, int):
        preferences.mapping[pdf_key] = PdfNumber(value)
    elif isinstance(value, str):
        preferences.mapping[pdf_key] = PdfName(value)
    else:  # pairs of page indices
        preferences.mapping[pdf_key] = PdfArray(
            [PdfNumber(page + 1) for pair in value for page in pair]
        )


def clear_preferences(pdf: Any) -> None:
    """Remove ``/ViewerPreferences`` entirely."""
    catalog = _catalog(pdf)
    if catalog is not None:
        catalog.mapping.pop(PdfName("ViewerPreferences"), None)


# -- /OpenAction -------------------------------------------------------------
def open_action(pdf: Any) -> Any:
    """The ``/OpenAction`` as an :class:`Action` or :class:`Destination`.

    ``None`` when there is none, and also when it points at a page this
    document no longer has: a destination that resolves nowhere is one a
    viewer ignores, and handing back somebody else's page would be worse than
    handing back nothing.
    """
    from .simple_pdf import _UNRESOLVED_DESTINATION, action_from_cos

    catalog = _catalog(pdf)
    if catalog is None:
        return None
    entry = pdf._resolve(catalog.mapping.get(PdfName("OpenAction")))
    if isinstance(entry, PdfArray):
        destination = pdf._destination_from_cos(entry)
        return None if destination is _UNRESOLVED_DESTINATION else destination
    if isinstance(entry, PdfDictionary):
        return action_from_cos(entry, pdf._resolve, pdf._page_index_of)
    return None


def set_open_action(pdf: Any, target: Any) -> None:
    """Write ``/OpenAction``; ``None`` removes it.

    A destination goes in as the array it is, an action as its dictionary --
    ``/OpenAction`` takes either (table 28).
    """
    catalog = _catalog(pdf)
    if catalog is None:
        return
    if target is None:
        catalog.mapping.pop(PdfName("OpenAction"), None)
        return
    value, _key = pdf._interactive_target_cos(target)
    catalog.mapping[PdfName("OpenAction")] = value
