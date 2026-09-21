"""How a document asks to be opened and shown.

``/PageMode``, ``/PageLayout``, ``/OpenAction`` and ``/ViewerPreferences``
(ISO 32000-1 tables 28 and 150) survived a load and save but could not be read
or written: a document could not be told to open on its bookmarks, in two
columns, at a page, or with the window titled by its ``/Title``.

The values asserted here were compared with pdf.js (``getPageMode``,
``getPageLayout``, ``getViewerPreferences``, ``getOpenAction``), Apache PDFBox
(``PDDocumentCatalog``, ``PDViewerPreferences``) and MuPDF (``pagemode``,
``pagelayout``) in both directions: each reference reads what this writes, and
this reads their files the way they do -- including the defaults they resolve
a missing or malformed entry to.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import (
    Document,
    DuplexMode,
    PageBoundary,
    PageLayout,
    PageMode,
    PrintScaling,
    ReadingDirection,
)
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfBoolean,
    PdfDictionary,
    PdfName,
    PdfNumber,
)
from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.interactive import (
    FitDestination,
    GoToAction,
    JavaScriptAction,
    NamedAction,
    XYZDestination,
)


def _reloaded(document: Document) -> Document:
    buffer = io.BytesIO()
    document.save(buffer)
    return Document(io.BytesIO(buffer.getvalue()))


def _pages(count: int = 4) -> Document:
    document = Document()
    for _ in range(count):
        document.pages.add()
    return document


def _catalog(document: Document) -> PdfDictionary:
    engine = document._engine_pdf
    return engine._resolve(engine._cos_doc.trailer.mapping[PdfName("Root")])


def _set_preferences(document: Document, **entries) -> Document:
    """A file another producer wrote: ``/ViewerPreferences`` straight in."""
    _catalog(document).mapping[PdfName("ViewerPreferences")] = PdfDictionary(
        {PdfName(key): value for key, value in entries.items()}
    )
    return _reloaded(document)


# --- what the document says ------------------------------------------------


def test_a_document_opens_the_way_it_is_told_to():
    document = _pages()
    document.page_mode = PageMode.USE_OUTLINES
    document.page_layout = PageLayout.TWO_PAGE_RIGHT
    document.open_action = XYZDestination(page=2, left=50, top=650, zoom=1.5)

    reopened = _reloaded(document)
    # pdf.js, PDFBox and MuPDF all read exactly this back.
    assert reopened.page_mode is PageMode.USE_OUTLINES
    assert reopened.page_layout is PageLayout.TWO_PAGE_RIGHT
    assert reopened.open_action == XYZDestination(page=2, left=50.0, top=650.0, zoom=1.5)


def test_a_document_that_says_nothing_opens_on_its_defaults():
    document = _reloaded(_pages(1))
    assert document.page_mode is PageMode.USE_NONE
    assert document.page_layout is PageLayout.SINGLE_PAGE
    assert document.open_action is None


def test_a_name_the_specification_does_not_list_is_the_default():
    # MuPDF hands such a name back as the string it is; pdf.js and PDFBox,
    # which give these entries a type, both fall back to the default.
    document = _pages(1)
    catalog = _catalog(document)
    catalog.mapping[PdfName("PageMode")] = PdfName("Bogus")
    catalog.mapping[PdfName("PageLayout")] = PdfName("Bogus")
    reopened = _reloaded(document)
    assert reopened.page_mode is PageMode.USE_NONE
    assert reopened.page_layout is PageLayout.SINGLE_PAGE


def test_a_mode_or_layout_that_is_not_one_of_the_names_is_refused():
    document = _pages(1)
    with pytest.raises(PdfValidationException, match="page_mode"):
        document.page_mode = "Bogus"
    with pytest.raises(PdfValidationException, match="page_layout"):
        document.page_layout = "Sideways"
    # The names themselves are accepted, spelled either way.
    document.page_mode = "UseAttachments"
    document.page_layout = PageLayout.ONE_COLUMN
    assert _reloaded(document).page_mode is PageMode.USE_ATTACHMENTS


# --- /OpenAction -----------------------------------------------------------


def test_an_open_action_may_be_an_action_as_well_as_a_destination():
    document = _pages()
    for action in (
        GoToAction(FitDestination(page=1)),
        JavaScriptAction("app.alert(1)"),
        NamedAction("NextPage"),
    ):
        document.open_action = action
        assert _reloaded(document).open_action == action


def test_an_open_action_can_be_taken_away():
    document = _pages()
    document.open_action = FitDestination(page=1)
    document.open_action = None
    assert _reloaded(document).open_action is None
    assert PdfName("OpenAction") not in _catalog(document).mapping


def test_an_open_action_pointing_at_a_page_that_is_gone_reads_as_nothing():
    # pikepdf and MuPDF both leave the entry behind when the page goes, so it
    # is still in the file; a viewer ignores it, and so does this.
    document = _reloaded(_pages(3))
    document.open_action = FitDestination(page=2)
    document = _reloaded(document)
    document.pages.delete(2)

    assert document.open_action is None
    buffer = io.BytesIO()
    document.save(buffer)
    assert b"/OpenAction" in buffer.getvalue()


def test_only_an_action_or_a_destination_can_be_opened_with():
    document = _pages()
    with pytest.raises(TypeError):
        document.open_action = "page 2 please"


# --- /ViewerPreferences ----------------------------------------------------


def test_the_window_is_described_as_the_document_asks():
    document = _pages()
    preferences = document.viewer_preferences
    preferences.hide_toolbar = True
    preferences.hide_menubar = True
    preferences.display_doc_title = True
    preferences.center_window = True

    reopened = _reloaded(document).viewer_preferences
    assert (reopened.hide_toolbar, reopened.hide_menubar) == (True, True)
    assert (reopened.display_doc_title, reopened.center_window) == (True, True)
    # The ones it did not ask for stay at their defaults.
    assert (reopened.hide_window_ui, reopened.fit_window) == (False, False)
    assert "hide_toolbar=True" in repr(reopened)
    assert repr(_pages(1).viewer_preferences) == "ViewerPreferences()"


def test_reading_and_printing_are_described_too():
    document = _pages()
    preferences = document.viewer_preferences
    preferences.direction = ReadingDirection.RIGHT_TO_LEFT
    preferences.non_full_screen_page_mode = PageMode.USE_THUMBS
    preferences.print_scaling = PrintScaling.NONE
    preferences.duplex = DuplexMode.FLIP_SHORT_EDGE
    preferences.pick_tray_by_pdf_size = True
    preferences.num_copies = 2
    preferences.print_area = PageBoundary.TRIM_BOX
    preferences.view_clip = PageBoundary.MEDIA_BOX

    reopened = _reloaded(document).viewer_preferences
    assert reopened.direction is ReadingDirection.RIGHT_TO_LEFT
    assert reopened.non_full_screen_page_mode is PageMode.USE_THUMBS
    assert reopened.print_scaling is PrintScaling.NONE
    assert reopened.duplex is DuplexMode.FLIP_SHORT_EDGE
    assert reopened.pick_tray_by_pdf_size is True
    assert reopened.num_copies == 2
    assert reopened.print_area is PageBoundary.TRIM_BOX
    assert reopened.view_clip is PageBoundary.MEDIA_BOX
    # Untouched boxes stay on the crop box, which is the default of all four.
    assert (reopened.view_area, reopened.print_clip) == (
        PageBoundary.CROP_BOX,
        PageBoundary.CROP_BOX,
    )


def test_defaults_answer_for_everything_a_document_leaves_out():
    preferences = _reloaded(_pages(1)).viewer_preferences
    assert preferences.direction is ReadingDirection.LEFT_TO_RIGHT
    assert preferences.non_full_screen_page_mode is PageMode.USE_NONE
    assert preferences.print_scaling is PrintScaling.APP_DEFAULT
    assert preferences.view_area is PageBoundary.CROP_BOX
    # Nothing to fall back on: the viewer decides.
    assert preferences.duplex is None
    assert preferences.pick_tray_by_pdf_size is None
    assert preferences.num_copies is None
    assert preferences.print_page_range == ()
    assert not preferences.hide_toolbar


@pytest.mark.parametrize(
    "entries",
    [
        # A name where a boolean belongs, and names outside the value sets:
        # pdf.js drops or defaults each of these.
        {
            "HideToolbar": PdfName("Yes"),
            "NonFullScreenPageMode": PdfName("FullScreen"),
            "Direction": PdfName("Sideways"),
            "PrintScaling": PdfName("Huge"),
            "Duplex": PdfName("Maybe"),
            "ViewArea": PdfName("WholePage"),
        },
        # Numbers that describe nothing: zero copies, an odd-length range.
        {"NumCopies": PdfNumber(0), "PrintPageRange": PdfArray([PdfNumber(1), PdfNumber(2), PdfNumber(3)])},
    ],
    ids=["names", "numbers"],
)
def test_an_entry_that_says_something_impossible_reads_as_its_default(entries):
    preferences = _set_preferences(_pages(3), **entries).viewer_preferences
    assert preferences.hide_toolbar is False
    assert preferences.non_full_screen_page_mode is PageMode.USE_NONE
    assert preferences.direction is ReadingDirection.LEFT_TO_RIGHT
    assert preferences.print_scaling is PrintScaling.APP_DEFAULT
    assert preferences.duplex is None
    assert preferences.view_area is PageBoundary.CROP_BOX
    assert preferences.num_copies is None
    assert preferences.print_page_range == ()


def test_preferences_that_are_not_a_dictionary_are_no_preferences():
    document = _pages(1)
    _catalog(document).mapping[PdfName("ViewerPreferences")] = PdfArray([PdfNumber(1)])
    preferences = _reloaded(document).viewer_preferences
    assert preferences.hide_toolbar is False and preferences.direction is ReadingDirection.LEFT_TO_RIGHT


def test_full_screen_is_not_something_to_come_back_to():
    # /NonFullScreenPageMode says what to show when full screen ends, so the
    # two modes that are not a panel beside the page are not among its values.
    document = _pages(1)
    with pytest.raises(PdfValidationException, match="non_full_screen_page_mode"):
        document.viewer_preferences.non_full_screen_page_mode = PageMode.FULL_SCREEN
    with pytest.raises(PdfValidationException, match="non_full_screen_page_mode"):
        document.viewer_preferences.non_full_screen_page_mode = PageMode.USE_ATTACHMENTS
    document.viewer_preferences.non_full_screen_page_mode = PageMode.USE_OC
    assert _reloaded(document).viewer_preferences.non_full_screen_page_mode is PageMode.USE_OC


# --- /PrintPageRange -------------------------------------------------------


def test_a_print_range_is_written_as_the_page_numbers_a_dialogue_shows():
    document = _pages(4)
    document.viewer_preferences.print_page_range = [(0, 1), (3, 3)]

    reopened = _reloaded(document)
    assert reopened.viewer_preferences.print_page_range == ((0, 1), (3, 3))
    # In the file the pages are numbered from one, which is what pdf.js and
    # PDFBox read out of it: 1, 2, 4, 4.
    preferences = document._engine_pdf._resolve(
        _catalog(reopened).mapping[PdfName("ViewerPreferences")]
    )
    written = document._engine_pdf._resolve(preferences.mapping[PdfName("PrintPageRange")])
    assert [item.value for item in written.items] == [1, 2, 4, 4]


@pytest.mark.parametrize(
    "numbers",
    [[1, 99], [3, 1], [0, 2], [1, 2, 3], [1.5, 2], [2, 2.5]],
    ids=["past the end", "backwards", "page zero", "unpaired", "half a page", "to half a page"],
)
def test_a_range_that_is_not_pages_this_document_has_is_ignored(numbers):
    document = _set_preferences(
        _pages(3), PrintPageRange=PdfArray([PdfNumber(n) for n in numbers])
    )
    assert document.viewer_preferences.print_page_range == ()


def test_a_page_number_written_as_a_whole_real_is_that_page():
    # 2.0 is page two; the entry only has to *be* whole, not be written so.
    document = _set_preferences(
        _pages(3), PrintPageRange=PdfArray([PdfNumber(1.0), PdfNumber(2.0)])
    )
    assert document.viewer_preferences.print_page_range == ((0, 1),)


def test_a_range_outside_the_document_is_refused_rather_than_written():
    document = _pages(3)
    with pytest.raises(PdfValidationException, match="pages this document has"):
        document.viewer_preferences.print_page_range = [(0, 3)]
    with pytest.raises(PdfValidationException, match="pages this document has"):
        document.viewer_preferences.print_page_range = [(2, 1)]
    with pytest.raises(PdfValidationException, match="whole page indices"):
        document.viewer_preferences.print_page_range = [(0.5, 1)]


def test_a_copy_count_must_be_a_count():
    document = _pages(1)
    with pytest.raises(PdfValidationException, match="num_copies"):
        document.viewer_preferences.num_copies = 0
    with pytest.raises(PdfValidationException, match="num_copies"):
        document.viewer_preferences.num_copies = -1


# --- taking entries away ---------------------------------------------------


def test_an_optional_entry_can_be_taken_back():
    document = _pages(2)
    preferences = document.viewer_preferences
    preferences.num_copies = 3
    preferences.duplex = DuplexMode.SIMPLEX
    preferences.print_page_range = [(0, 1)]
    preferences.pick_tray_by_pdf_size = True

    preferences.num_copies = None
    preferences.duplex = None
    preferences.print_page_range = ()
    preferences.pick_tray_by_pdf_size = None

    reopened = _reloaded(document).viewer_preferences
    assert (reopened.num_copies, reopened.duplex) == (None, None)
    assert reopened.print_page_range == () and reopened.pick_tray_by_pdf_size is None


def test_the_last_entry_taken_away_takes_the_dictionary_with_it():
    document = _pages(1)
    document.viewer_preferences.num_copies = 3
    document.viewer_preferences.num_copies = None
    assert PdfName("ViewerPreferences") not in _catalog(document).mapping

    document.viewer_preferences.hide_toolbar = True
    document.viewer_preferences.clear()
    assert PdfName("ViewerPreferences") not in _catalog(document).mapping
    assert not _reloaded(document).viewer_preferences.hide_toolbar


def test_a_flag_set_to_false_is_written_rather_than_dropped():
    # /HideToolbar false is what a document says when it means "show it",
    # which is not the same as leaving the reader to decide.
    document = _set_preferences(_pages(1), HideToolbar=PdfBoolean(True))
    document.viewer_preferences.hide_toolbar = False
    reopened = _reloaded(document)
    preferences = reopened._engine_pdf._resolve(
        _catalog(reopened).mapping[PdfName("ViewerPreferences")]
    )
    assert preferences.mapping[PdfName("HideToolbar")].value is False


# --- with the rest of the library -------------------------------------------


def test_a_brand_new_document_can_be_told_how_to_open():
    # Nothing has built a COS document yet; asking for a preference must not
    # be what decides whether it gets one.
    document = Document()
    assert document.page_mode is PageMode.USE_NONE
    assert document.viewer_preferences.num_copies is None
    document.pages.add()
    document.page_mode = PageMode.USE_THUMBS
    document.viewer_preferences.fit_window = True
    assert _reloaded(document).page_mode is PageMode.USE_THUMBS
    assert _reloaded(document).viewer_preferences.fit_window is True


def test_the_title_a_screen_reader_announces_satisfies_pdf_ua():
    # PDF/UA asks for /DisplayDocTitle true (ISO 14289-1 7.1); the validator
    # that reports it missing stops reporting it once this sets it.
    document = _pages(1)
    assert any("DisplayDocTitle" in error for error in document.validate_pdfua().errors)
    document.viewer_preferences.display_doc_title = True
    reopened = _reloaded(document)
    assert not any("DisplayDocTitle" in error for error in reopened.validate_pdfua().errors)


def test_preferences_are_refused_once_the_document_is_disposed():
    document = _pages(1)
    preferences = document.viewer_preferences
    document.dispose()
    with pytest.raises(Exception, match="dispose"):
        preferences.hide_toolbar
    with pytest.raises(Exception, match="dispose"):
        document.page_mode
