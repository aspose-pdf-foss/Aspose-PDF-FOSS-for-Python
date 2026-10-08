"""The task-shaped facades ported Aspose.PDF code reaches for.

The layer had two classes: ``PdfExtractor`` and ``PdfFileEditor``. Everything
else a port expects -- the information dictionary, passwords, the outline,
annotations, stamps and running heads, page geometry, the XMP packet, rendering --
had to be rewritten against ``Document`` by hand, which is the one thing a
compatibility surface exists to avoid.

These add no capability: each is a composition of operations documented elsewhere
in ``supported-features.md``. What they add is the shape, and one habit of this
layer the tests below pin down: **page numbers are 1-based** here, as in the
Aspose facades and as ``PdfFileEditor.extract`` already was, while the rest of the
package counts from zero. An operation that writes answers ``True`` or ``False``
and records why in ``last_exception`` rather than raising.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import (
    Document,
    PageSize,
    PdfAnnotationEditor,
    PdfBookmarkEditor,
    PdfConverter,
    PdfFileInfo,
    PdfFileSecurity,
    PdfFileStamp,
    PdfPageEditor,
    PdfXmpMetadata,
    TextStamp,
)
from aspose_pdf.exceptions import AsposePdfException, PdfSecurityException
from aspose_pdf.outlines import OutlineItem


def _document(pages: int = 4, *, annotations: bool = False) -> bytes:
    document = Document()
    for index in range(pages):
        page = document.pages.add(PageSize.A5)
        page.add_text(f"PAGE {index + 1}", 100, 300, font_size=24)
        if annotations:
            page.annotations.add_square((20, 20, 80, 60))
            page.annotations.add_text((100, 500, 120, 520), "note")
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _numbers(data: bytes) -> list[str]:
    """Which source page each page of *data* is, by the text on it."""
    with Document(io.BytesIO(data)) as document:
        return [page.extract_text().split()[1] for page in document.pages]


def _saved(facade) -> bytes:
    buffer = io.BytesIO()
    assert facade.save(buffer) is True
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# What every bound facade does the same way
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "factory",
    [
        PdfFileInfo,
        PdfFileSecurity,
        PdfBookmarkEditor,
        PdfAnnotationEditor,
        PdfFileStamp,
        PdfPageEditor,
        PdfXmpMetadata,
        PdfConverter,
    ],
    ids=lambda cls: cls.__name__,
)
def test_the_lifecycle_every_facade_shares(factory):
    facade = factory()
    assert facade.is_bound is False
    with pytest.raises(AsposePdfException, match="No document is bound"):
        facade.document
    facade.bind_pdf(io.BytesIO(_document(1)))
    assert facade.is_bound is True
    assert facade.document.page_count == 1
    assert facade.last_exception is None
    facade.close()
    assert facade.is_bound is False
    with pytest.raises(AsposePdfException, match="disposed"):
        facade.bind_pdf(io.BytesIO(_document(1)))


@pytest.mark.parametrize("factory", [PdfFileInfo, PdfPageEditor], ids=lambda c: c.__name__)
def test_a_facade_is_a_context_manager(factory):
    with factory() as facade:
        facade.bind_pdf(io.BytesIO(_document(1)))
        assert facade.is_bound
    assert facade.is_bound is False


def test_an_open_document_is_borrowed_rather_than_taken_over():
    document = Document(io.BytesIO(_document(2)))
    facade = PdfFileInfo()
    facade.bind_pdf(document)
    facade.title = "Borrowed"
    facade.close()
    # The facade did not close the document it was handed.
    assert document.page_count == 2
    assert document.info.get("Title") == "Borrowed"
    document.dispose()


def test_a_write_reports_a_failure_rather_than_raising(tmp_path):
    facade = PdfFileInfo()
    facade.bind_pdf(io.BytesIO(_document(1)))
    # A directory is not somewhere a file can be written.
    assert facade.save(str(tmp_path)) is False
    assert isinstance(facade.last_exception, Exception)
    # And the next operation clears it.
    assert facade.save(str(tmp_path / "out.pdf")) is True
    assert facade.last_exception is None


def test_page_numbers_are_one_based_and_checked():
    facade = PdfFileInfo()
    facade.bind_pdf(io.BytesIO(_document(3)))
    assert facade.get_page_width(1) == pytest.approx(PageSize.A5.width)
    for bad in (0, -1, 4, 99):
        with pytest.raises(AsposePdfException, match="outside this document"):
            facade.get_page_width(bad)
    with pytest.raises(AsposePdfException, match="whole number"):
        facade.get_page_width("first")


# ---------------------------------------------------------------------------
# PdfFileInfo
# ---------------------------------------------------------------------------


def test_the_information_dictionary_is_read_and_written():
    facade = PdfFileInfo()
    facade.bind_pdf(io.BytesIO(_document(1)))
    facade.title = "Quarterly report"
    facade.author = "Sergey"
    facade.subject = "Numbers"
    facade.keywords = "pdf, report"
    facade.creator = "aspose-pdf-foss"
    facade.producer = "aspose-pdf-foss"
    facade.creation_date = "D:20261007120000+02'00'"
    facade.mod_date = "D:20261007130000+02'00'"

    reloaded = Document(io.BytesIO(_saved(facade)))
    assert reloaded.info["Title"] == "Quarterly report"
    assert reloaded.info["Author"] == "Sergey"
    assert reloaded.info["Keywords"] == "pdf, report"
    assert reloaded.info["CreationDate"].startswith("D:20261007120000")


def test_a_custom_info_entry_is_read_set_and_removed():
    facade = PdfFileInfo()
    facade.bind_pdf(io.BytesIO(_document(1)))
    assert facade.get_meta_info("Department") is None
    assert facade.get_meta_info("Department", "none") == "none"
    facade.set_meta_info("Department", "Engineering")
    assert facade.get_meta_info("Department") == "Engineering"
    assert facade.meta_info["Department"] == "Engineering"
    assert Document(io.BytesIO(_saved(facade))).info["Department"] == "Engineering"
    facade.set_meta_info("Department", None)
    assert facade.get_meta_info("Department") is None


def test_clearing_removes_every_info_entry():
    facade = PdfFileInfo()
    facade.bind_pdf(io.BytesIO(_document(1)))
    facade.title = "Gone"
    facade.clear_meta_info()
    assert facade.title is None
    assert facade.meta_info == {}


def test_the_page_geometry_a_port_asks_for():
    document = Document()
    document.pages.add(PageSize.A5)
    document.pages.add(PageSize.A4)
    document.pages[1].rotation = 90
    document.pages[0].crop_box = (10, 10, 300, 400)
    buffer = io.BytesIO()
    document.save(buffer)

    facade = PdfFileInfo()
    facade.bind_pdf(io.BytesIO(buffer.getvalue()))
    assert facade.number_of_pages == 2
    assert facade.get_page_width(1) == pytest.approx(PageSize.A5.width)
    assert facade.get_page_height(2) == pytest.approx(PageSize.A4.height)
    assert facade.get_page_rotation(2) == 90
    assert facade.get_page_rotation(1) == 0
    assert tuple(facade.get_page_box(1, "CropBox")) == (10.0, 10.0, 300.0, 400.0)
    assert facade.pdf_version.startswith("1.")
    assert facade.is_encrypted is False


# ---------------------------------------------------------------------------
# PdfFileSecurity
# ---------------------------------------------------------------------------


def test_a_document_is_encrypted_and_decrypted_through_the_facade():
    facade = PdfFileSecurity()
    facade.bind_pdf(io.BytesIO(_document(1)))
    assert facade.encrypt_file("user", "owner") is True
    encrypted = _saved(facade)
    facade.close()

    with pytest.raises(PdfSecurityException):
        Document(io.BytesIO(encrypted))
    assert Document(io.BytesIO(encrypted), password="user").page_count == 1

    opener = PdfFileSecurity()
    opener.bind_pdf(io.BytesIO(encrypted), "user")
    assert opener.decrypt_file("user") is True
    plain = _saved(opener)
    assert Document(io.BytesIO(plain)).page_count == 1  # no password needed


def test_the_passwords_are_changed():
    facade = PdfFileSecurity()
    facade.bind_pdf(io.BytesIO(_document(1)))
    facade.encrypt_file("first", "owner")
    encrypted = _saved(facade)
    facade.close()

    changer = PdfFileSecurity()
    changer.bind_pdf(io.BytesIO(encrypted), "owner")
    assert changer.change_password("owner", "second", "owner2") is True
    changed = _saved(changer)
    assert Document(io.BytesIO(changed), password="second").page_count == 1
    with pytest.raises(PdfSecurityException):
        Document(io.BytesIO(changed), password="first")


def test_a_wrong_password_is_a_failure_not_an_exception():
    facade = PdfFileSecurity()
    facade.bind_pdf(io.BytesIO(_document(1)))
    facade.encrypt_file("user", "owner")
    encrypted = _saved(facade)
    facade.close()

    opener = PdfFileSecurity()
    opener.bind_pdf(io.BytesIO(encrypted), "user")
    assert opener.decrypt_file("nope") is False
    assert isinstance(opener.last_exception, Exception)


def test_the_permissions_are_reported():
    facade = PdfFileSecurity()
    facade.bind_pdf(io.BytesIO(_document(1)))
    assert facade.encrypt_file("user", "owner", permissions=-44) is True
    assert isinstance(facade.permissions, int)


# ---------------------------------------------------------------------------
# PdfBookmarkEditor
# ---------------------------------------------------------------------------


def test_bookmarks_are_created_extracted_and_deleted():
    facade = PdfBookmarkEditor()
    facade.bind_pdf(io.BytesIO(_document(3)))
    first = facade.create_bookmark_of_page("Chapter 1", 1)
    second = facade.create_bookmark_of_page("Chapter 2", 2, color="#cc3300", open=True)
    second.add(OutlineItem("Section 2.1", 1))

    assert [item.title for item in facade.extract_bookmarks()] == [
        "Chapter 1",
        "Chapter 2",
    ]
    assert [item.title for item in facade.extract_bookmarks(nested=False)] == [
        "Chapter 1",
        "Chapter 2",
        "Section 2.1",
    ]
    assert first.page_index == 0  # 1-based in, 0-based on the item
    reloaded = Document(io.BytesIO(_saved(facade)))
    titles = [(item.title, item.page_index, item.open) for item in reloaded.outlines]
    assert titles == [("Chapter 1", 0, False), ("Chapter 2", 1, True)]
    assert reloaded.outlines[1].color == pytest.approx((0.8, 0.2, 0.0))


def test_bookmarks_are_created_in_bulk():
    facade = PdfBookmarkEditor()
    facade.bind_pdf(io.BytesIO(_document(3)))
    created = facade.create_bookmarks([("One", 1), ("Two", 2), ("Three", 3)])
    assert [item.page_index for item in created] == [0, 1, 2]
    assert len(facade.extract_bookmarks()) == 3


def test_bookmarks_are_deleted_by_title_or_altogether():
    facade = PdfBookmarkEditor()
    facade.bind_pdf(io.BytesIO(_document(2)))
    facade.create_bookmarks([("Keep", 1), ("Drop", 2), ("Drop", 1)])
    assert facade.delete_bookmarks("Drop") == 2
    assert [item.title for item in facade.extract_bookmarks()] == ["Keep"]
    assert facade.delete_bookmarks() == 1
    assert facade.extract_bookmarks() == []


def test_a_bookmark_on_a_page_the_document_does_not_have_is_refused():
    facade = PdfBookmarkEditor()
    facade.bind_pdf(io.BytesIO(_document(2)))
    with pytest.raises(AsposePdfException, match="outside this document"):
        facade.create_bookmark_of_page("Nope", 5)


# ---------------------------------------------------------------------------
# PdfAnnotationEditor
# ---------------------------------------------------------------------------


def test_annotations_are_extracted_by_page_and_by_subtype():
    facade = PdfAnnotationEditor()
    facade.bind_pdf(io.BytesIO(_document(3, annotations=True)))
    assert len(facade.extract_annotations()) == 6
    assert len(facade.extract_annotations(1)) == 2
    assert len(facade.extract_annotations([1, 2])) == 4
    squares = facade.extract_annotations(subtypes=["Square"])
    assert len(squares) == 3
    assert all(item.subtype == "Square" for item in squares)
    # The typed classes come back, so each annotation's own entries are there.
    assert squares[0].interior_color == ()


def test_annotations_are_deleted_by_subtype_or_altogether():
    facade = PdfAnnotationEditor()
    facade.bind_pdf(io.BytesIO(_document(3, annotations=True)))
    assert facade.delete_annotations("Square") == 3
    assert len(facade.extract_annotations()) == 3
    assert facade.delete_annotations(page_numbers=[1]) == 1
    assert len(facade.extract_annotations()) == 2
    assert facade.delete_annotations() == 2
    assert facade.extract_annotations() == []


def test_annotations_are_given_appearances_and_flattened():
    facade = PdfAnnotationEditor()
    facade.bind_pdf(io.BytesIO(_document(2, annotations=True)))
    assert facade.generate_appearances() == 4
    assert facade.flatten_annotations() is True
    reloaded = Document(io.BytesIO(_saved(facade)))
    assert len(reloaded.pages[0].annotations) == 0


# ---------------------------------------------------------------------------
# PdfFileStamp
# ---------------------------------------------------------------------------


def test_a_header_a_footer_and_a_page_number():
    facade = PdfFileStamp()
    facade.bind_pdf(io.BytesIO(_document(2)))
    assert facade.add_header("ACME CORP") is True
    assert facade.add_footer("Confidential") is True
    assert facade.add_page_number("{page} of {total}") is True
    reloaded = Document(io.BytesIO(_saved(facade)))
    first = reloaded.pages[0].extract_text()
    assert "ACME CORP" in first
    assert "Confidential" in first
    assert "1 of 2" in first
    assert "2 of 2" in reloaded.pages[1].extract_text()


def test_a_header_sits_above_a_footer():
    facade = PdfFileStamp()
    facade.bind_pdf(io.BytesIO(_document(1)))
    facade.add_header("TOP")
    facade.add_footer("BOTTOM")
    page = Document(io.BytesIO(_saved(facade))).pages[0]
    # The extractor reads a page top-down, so the header comes first.
    text = page.extract_text()
    assert text.index("TOP") < text.index("BOTTOM")


def test_any_stamp_goes_on_through_the_facade():
    facade = PdfFileStamp()
    facade.bind_pdf(io.BytesIO(_document(3)))
    assert facade.add_stamp(TextStamp("DRAFT", font_size=40, opacity=0.2), [1, 3]) is True
    reloaded = Document(io.BytesIO(_saved(facade)))
    assert "DRAFT" in reloaded.pages[0].extract_text()
    assert "DRAFT" not in reloaded.pages[1].extract_text()
    assert "DRAFT" in reloaded.pages[2].extract_text()


def test_a_stamp_on_a_page_that_is_not_there_is_a_failure():
    # A writing operation answers False and records why; it is the readers that
    # raise, which is this layer's habit rather than a decision per method.
    facade = PdfFileStamp()
    facade.bind_pdf(io.BytesIO(_document(1)))
    assert facade.add_stamp(TextStamp("X"), [7]) is False
    assert "outside this document" in str(facade.last_exception)


# ---------------------------------------------------------------------------
# PdfPageEditor
# ---------------------------------------------------------------------------


def test_pages_are_rotated_resized_and_cropped():
    facade = PdfPageEditor()
    facade.bind_pdf(io.BytesIO(_document(3)))
    assert facade.rotate(90, [1, 2]) is True
    assert facade.rotate(90, [1]) is True  # added to what it already said
    assert facade.resize(PageSize.A4, [3]) is True
    assert facade.crop((10, 10, 300, 400), [3]) is True

    reloaded = Document(io.BytesIO(_saved(facade)))
    assert [page.rotation for page in reloaded.pages] == [180, 90, 0]
    assert reloaded.pages[2].size == PageSize.A4
    assert tuple(reloaded.pages[2].crop_box) == (10.0, 10.0, 300.0, 400.0)


def test_a_rotation_can_be_set_rather_than_added():
    facade = PdfPageEditor()
    facade.bind_pdf(io.BytesIO(_document(1)))
    facade.rotate(90)
    facade.rotate(270, relative=False)
    assert Document(io.BytesIO(_saved(facade))).pages[0].rotation == 270


@pytest.mark.parametrize(
    ("page_number", "position", "expected"),
    [
        (1, 3, ["2", "3", "1", "4"]),
        (4, 1, ["4", "1", "2", "3"]),
        (2, 4, ["1", "3", "4", "2"]),
        (1, 4, ["2", "3", "4", "1"]),
        (3, 3, ["1", "2", "3", "4"]),
    ],
)
def test_a_page_is_moved_to_the_position_asked_for(page_number, position, expected):
    # Moving forward has to insert one place beyond the destination, because
    # dropping the original then shifts everything after it back by one.
    facade = PdfPageEditor()
    facade.bind_pdf(io.BytesIO(_document(4)))
    assert facade.move_page(page_number, position) is True
    assert _numbers(_saved(facade)) == expected


def test_moving_to_a_position_that_does_not_exist_is_a_failure():
    facade = PdfPageEditor()
    facade.bind_pdf(io.BytesIO(_document(3)))
    assert facade.move_page(1, 9) is False
    assert isinstance(facade.last_exception, Exception)
    assert _numbers(_saved(facade)) == ["1", "2", "3"]


def test_pages_are_deleted():
    facade = PdfPageEditor()
    facade.bind_pdf(io.BytesIO(_document(4)))
    assert facade.delete_pages([2, 4]) is True
    assert _numbers(_saved(facade)) == ["1", "3"]


def test_deleting_every_page_is_refused():
    facade = PdfPageEditor()
    facade.bind_pdf(io.BytesIO(_document(2)))
    assert facade.delete_pages([1, 2]) is False
    assert isinstance(facade.last_exception, Exception)


# ---------------------------------------------------------------------------
# PdfXmpMetadata
# ---------------------------------------------------------------------------


def test_an_xmp_property_is_read_and_written():
    facade = PdfXmpMetadata()
    facade.bind_pdf(io.BytesIO(_document(1)))
    assert facade.get_value("dc", "title") is None
    assert facade.get_value("dc", "title", "none") == "none"
    facade.set_value("dc", "title", "From XMP")
    assert facade.get_value("dc", "title") == "From XMP"

    reloaded = Document(io.BytesIO(_saved(facade)))
    field = reloaded.xmp_metadata.get("dc", "title")
    assert field is not None
    assert field.value == "From XMP"


def test_the_packet_itself_is_reachable():
    facade = PdfXmpMetadata()
    facade.bind_pdf(io.BytesIO(_document(1)))
    facade.set_value("pdf", "Producer", "aspose-pdf-foss")
    assert facade.packet.get("pdf", "Producer").value == "aspose-pdf-foss"


def test_the_info_dictionary_and_the_packet_are_synced_either_way():
    facade = PdfXmpMetadata()
    facade.bind_pdf(io.BytesIO(_document(1)))
    facade.document.info = {"Title": "From Info"}
    facade.sync_from_info()
    assert facade.get_value("dc", "title") == "From Info"

    facade.set_value("dc", "title", "Back to Info")
    facade.sync_to_info()
    assert facade.document.info["Title"] == "Back to Info"


# ---------------------------------------------------------------------------
# PdfConverter
# ---------------------------------------------------------------------------


def test_pages_are_rendered_one_image_at_a_time():
    facade = PdfConverter()
    facade.bind_pdf(io.BytesIO(_document(3)))
    facade.resolution = 72
    assert facade.do_convert() is True
    images = []
    while facade.has_next_image():
        images.append(facade.get_next_image())
    assert len(images) == 3
    assert all(image.startswith(b"\x89PNG\r\n\x1a\n") for image in images)
    assert facade.get_next_image() is None


def test_only_the_pages_named_are_rendered():
    facade = PdfConverter()
    facade.bind_pdf(io.BytesIO(_document(4)))
    facade.resolution = 36
    assert facade.do_convert([2, 3]) is True
    count = 0
    while facade.has_next_image():
        facade.get_next_image()
        count += 1
    assert count == 2


@pytest.mark.parametrize(
    ("image_format", "magic"),
    [("png", b"\x89PNG"), ("jpeg", b"\xff\xd8"), ("jpg", b"\xff\xd8"), ("tiff", b"II")],
)
def test_every_image_format_the_converter_writes(image_format, magic):
    facade = PdfConverter()
    facade.bind_pdf(io.BytesIO(_document(1)))
    facade.resolution = 36
    assert facade.do_convert(image_format=image_format) is True
    assert facade.get_next_image().startswith(magic)


def test_a_format_with_no_encoder_is_a_failure():
    facade = PdfConverter()
    facade.bind_pdf(io.BytesIO(_document(1)))
    assert facade.do_convert(image_format="webp") is False
    assert isinstance(facade.last_exception, Exception)
    assert facade.has_next_image() is False


def test_an_image_is_written_to_a_path_or_a_stream(tmp_path):
    facade = PdfConverter()
    facade.bind_pdf(io.BytesIO(_document(2)))
    facade.resolution = 36
    facade.do_convert()
    path = facade.get_next_image(tmp_path / "page-1.png")
    assert path.exists()
    assert path.read_bytes().startswith(b"\x89PNG")
    buffer = io.BytesIO()
    facade.get_next_image(buffer)
    assert buffer.getvalue().startswith(b"\x89PNG")


def test_a_multi_page_tiff_is_written(tmp_path):
    facade = PdfConverter()
    facade.bind_pdf(io.BytesIO(_document(3)))
    facade.resolution = 36
    out = tmp_path / "all.tiff"
    assert facade.save_as_tiff(out) is True
    assert out.read_bytes().startswith(b"II")


def test_a_resolution_has_to_be_a_resolution():
    facade = PdfConverter()
    facade.bind_pdf(io.BytesIO(_document(1)))
    with pytest.raises(AsposePdfException, match="above zero"):
        facade.resolution = 0
    assert facade.resolution == 150.0  # the default is untouched


def test_disposal_releases_the_rendered_images():
    facade = PdfConverter()
    facade.bind_pdf(io.BytesIO(_document(1)))
    facade.resolution = 36
    facade.do_convert()
    assert facade.has_next_image() is True
    facade.close()
    with pytest.raises(AsposePdfException, match="disposed"):
        facade.has_next_image()
