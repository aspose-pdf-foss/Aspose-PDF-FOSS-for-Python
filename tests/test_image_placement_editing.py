"""Replacing and hiding an image a page draws.

``ImagePlacement.replace`` set the bytes on the object in hand and ``hide`` set
a flag on it; neither touched the document, so a save afterwards still wrote the
picture that was there. A placement collected by
``ImagePlacementAbsorber`` is now bound to the document it came from and both
methods change it.

The rule is MuPDF's, read out of its own source: ``Page.replace_image``
"replace[s] the image by changing the object definition stored under xref...
leav[ing] the page's appearance instructions intact, so the new image is
displayed with the same bbox, rotation etc.", and ``Page.delete_image``
"actually replaces by a small transparent Pixmap using Page.replace_image". So
both operations redefine the XObject and leave the content stream alone, and
both are per-object -- every page drawing that image sees the change. pdfium and
MuPDF rendered every case below identically, to the pixel.
"""

from __future__ import annotations

import io
import struct
import zlib

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import PdfName, PdfStream
from aspose_pdf.exceptions import AsposePdfException, PdfValidationException
from aspose_pdf.images import ImagePlacement, ImagePlacementAbsorber

SIZE = 200
IMAGE_BOX = (40, 40, 80, 80)  # x, y, width, height on the page


def _png(rgb: tuple[int, int, int], size: int = 4) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    rows = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def _jpeg(rgb: tuple[int, int, int]) -> bytes:
    Image = pytest.importorskip("PIL.Image")
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), rgb).save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def _reloaded(document: Document) -> Document:
    buffer = io.BytesIO()
    document.save(buffer)
    return Document(io.BytesIO(buffer.getvalue()))


def _document(image: bytes | None = None) -> Document:
    """A green page with one image on it, saved and reopened."""
    document = Document()
    page = document.pages.add()
    page.draw_rectangle(10, 10, 180, 180, fill_color=(0, 1, 0), stroke_color=None)
    page.add_image(image if image is not None else _png((0, 0, 255)), *IMAGE_BOX)
    return _reloaded(document)


def _only_placement(document: Document) -> ImagePlacement:
    absorber = ImagePlacementAbsorber()
    absorber.visit(document)
    assert len(absorber.image_placements) == 1
    return absorber.image_placements[0]


def _colours(document: Document, page: int = 0) -> dict[str, int]:
    """How many pixels of each colour the page renders."""
    raster = document.pages[page].render(antialias=False)
    counts = {"red": 0, "green": 0, "blue": 0}
    for y in range(raster.height):
        for x in range(raster.width):
            r, g, b = raster.get_pixel(x, y)[:3]
            if b > 150 and r < 90:
                counts["blue"] += 1
            elif r > 150 and g < 90:
                counts["red"] += 1
            elif g > 150 and r < 90:
                counts["green"] += 1
    return counts


def _image_objects(document: Document) -> list[PdfStream]:
    engine = document._engine_pdf
    return [
        obj
        for obj in engine._cos_doc.objects.values()
        if isinstance(obj, PdfStream)
        and engine._get_name(obj.mapping.get(PdfName("Subtype"))) == "Image"
    ]


# --- replacing ---------------------------------------------------------------


def test_the_document_shows_the_new_picture_after_a_save():
    document = _document()
    before = _colours(document)
    assert before["blue"] == 80 * 80 and before["red"] == 0

    assert _only_placement(document).replace(_png((255, 0, 0))) is True

    after = _colours(_reloaded(document))
    # The same pixels, a different colour: the placement did not move or resize.
    assert after["red"] == before["blue"]
    assert after["blue"] == 0
    assert after["green"] == before["green"]


def test_the_new_picture_keeps_the_place_whatever_its_own_size():
    # A 4x4 image replaced by a 32x32 one still fills the box the page draws it
    # in: the page's own operators say where and how big, and they are untouched.
    document = _document()
    before = _colours(document)
    _only_placement(document).replace(_png((255, 0, 0), size=32))
    after = _colours(_reloaded(document))
    assert after["red"] == before["blue"]


def test_replacing_leaves_no_second_copy_of_the_image():
    document = _document()
    _only_placement(document).replace(_png((255, 0, 0)))
    # One image object before, one after: the replacement is written onto the
    # object that was there, not registered beside it.
    assert len(_image_objects(document)) == 1
    assert len(_image_objects(_reloaded(document))) == 1


def test_the_samples_that_were_there_are_gone():
    document = _document()
    _only_placement(document).replace(_png((255, 0, 0)))
    [image] = _image_objects(_reloaded(document))
    samples = zlib.decompress(image.content)
    assert samples == bytes([255, 0, 0]) * 16
    assert bytes([0, 0, 255]) * 16 not in samples


def test_a_jpeg_can_be_replaced_by_a_png():
    # The old image's /Filter and /DecodeParms describe bytes that are no longer
    # there, so they have to go with them.
    document = _document(_jpeg((0, 0, 255)))
    assert _only_placement(document).replace(_png((255, 0, 0))) is True
    [image] = _image_objects(document)
    assert image.mapping[PdfName("Filter")].name.lstrip("/") == "FlateDecode"
    assert PdfName("DecodeParms") not in image.mapping
    assert _colours(_reloaded(document))["red"] > 0


def test_replacing_with_a_transparent_png_shows_what_is_behind():
    document = _document()
    transparent = bytearray(_png((0, 0, 255)))
    assert _only_placement(document).replace(bytes(transparent)) is True
    # And with a real alpha channel the mask goes in as /SMask.
    document = _document()
    Image = pytest.importorskip("PIL.Image")
    buffer = io.BytesIO()
    Image.new("RGBA", (4, 4), (255, 0, 0, 0)).save(buffer, format="PNG")
    _only_placement(document).replace(buffer.getvalue())
    [image] = [
        obj
        for obj in _image_objects(document)
        if PdfName("SMask") in obj.mapping
    ]
    assert PdfName("SMask") in image.mapping
    assert _colours(_reloaded(document))["red"] == 0  # fully transparent


# --- hiding ------------------------------------------------------------------


def test_hiding_stops_the_document_drawing_it():
    document = _document()
    before = _colours(document)
    assert _only_placement(document).hide() is True

    after = _colours(_reloaded(document))
    assert after["blue"] == 0 and after["red"] == 0
    # What the image covered now shows the page behind it.
    assert after["green"] == before["green"] + before["blue"]


def test_hiding_takes_the_samples_with_it():
    document = _document()
    _only_placement(document).hide()
    reopened = _reloaded(document)
    for image in _image_objects(reopened):
        assert bytes([0, 0, 255]) not in zlib.decompress(image.content)


def test_a_hidden_placement_no_longer_hands_out_its_image():
    document = _document()
    placement = _only_placement(document)
    placement.hide()
    with pytest.raises(AsposePdfException, match="hidden"):
        placement.image_data
    with pytest.raises(AsposePdfException, match="hidden"):
        placement.save("unused.png")


def test_replacing_a_hidden_image_brings_it_back():
    document = _document()
    placement = _only_placement(document)
    placement.hide()
    assert placement.replace(_png((255, 0, 0))) is True
    assert placement.image_data  # readable again
    assert _colours(_reloaded(document))["red"] > 0


# --- what a change reaches ---------------------------------------------------


def test_one_image_drawn_on_two_pages_changes_on_both():
    document = Document()
    first, second = document.pages.add(), document.pages.add()
    for page in (first, second):
        page.draw_rectangle(10, 10, 180, 180, fill_color=(0, 1, 0), stroke_color=None)
    name = first.add_image(_png((0, 0, 255)), *IMAGE_BOX)
    engine = document._engine_pdf
    resources = engine._resolve(engine._get_page_dict(0).mapping[PdfName("Resources")])
    shared = engine._resolve(resources.mapping[PdfName("XObject")]).mapping[PdfName(name)]
    engine._ensure_resource_subdict(1, "XObject").mapping[PdfName(name)] = shared
    engine._append_content_to_page(
        1, b"q 80 0 0 80 40 40 cm /" + name.encode("ascii") + b" Do Q\n"
    )
    engine._page_image_map.setdefault(1, []).append(name)
    document = _reloaded(document)

    absorber = ImagePlacementAbsorber()
    absorber.visit(document)
    assert [placement.page_index for placement in absorber.image_placements] == [0, 1]
    # An image XObject is one object: changing it through either page changes it.
    assert absorber.image_placements[0].replace(_png((255, 0, 0))) is True
    reopened = _reloaded(document)
    assert _colours(reopened, page=0)["red"] > 0
    assert _colours(reopened, page=1)["red"] > 0


def test_a_placement_of_one_page_leaves_the_other_pages_image_alone():
    document = Document()
    for colour in ((0, 0, 255), (0, 0, 255)):
        page = document.pages.add()
        page.draw_rectangle(10, 10, 180, 180, fill_color=(0, 1, 0), stroke_color=None)
        page.add_image(_png(colour), *IMAGE_BOX)
    document = _reloaded(document)

    absorber = ImagePlacementAbsorber()
    absorber.visit(document.pages[0])
    assert len(absorber.image_placements) == 1
    absorber.image_placements[0].replace(_png((255, 0, 0)))

    reopened = _reloaded(document)
    assert _colours(reopened, page=0)["red"] > 0 and _colours(reopened, page=0)["blue"] == 0
    assert _colours(reopened, page=1)["blue"] > 0 and _colours(reopened, page=1)["red"] == 0


# --- a placement with no document behind it ----------------------------------


def test_a_placement_made_by_hand_still_holds_its_own_bytes():
    placement = ImagePlacement("Im0", _png((0, 0, 255)))
    # Nothing to write into, so nothing is written -- and it says so.
    assert placement.replace(_png((255, 0, 0))) is False
    assert placement.image_data == _png((255, 0, 0))
    assert placement.hide() is False
    with pytest.raises(AsposePdfException, match="hidden"):
        placement.image_data


def test_an_image_the_document_no_longer_draws_is_not_replaced():
    document = _document()
    placement = _only_placement(document)
    engine = document._engine_pdf
    resources = engine._resolve(engine._get_page_dict(0).mapping[PdfName("Resources")])
    engine._resolve(resources.mapping[PdfName("XObject")]).mapping.clear()
    assert placement.replace(_png((255, 0, 0))) is False


@pytest.mark.parametrize("bad", [b"", bytearray()])
def test_nothing_is_not_a_picture(bad):
    placement = _only_placement(_document())
    with pytest.raises(PdfValidationException, match="cannot be empty"):
        placement.replace(bad)


def test_a_picture_has_to_be_bytes():
    placement = _only_placement(_document())
    with pytest.raises(TypeError, match="bytes"):
        placement.replace("a file name")


def test_a_disposed_placement_changes_nothing():
    placement = _only_placement(_document())
    placement.dispose()
    with pytest.raises(AsposePdfException, match="disposed"):
        placement.replace(_png((255, 0, 0)))
    with pytest.raises(AsposePdfException, match="disposed"):
        placement.hide()


# --- what must not be mistaken for an image ----------------------------------


def test_a_form_xobject_is_not_an_image_to_replace():
    document = _document()
    engine = document._engine_pdf
    resources = engine._resolve(engine._get_page_dict(0).mapping[PdfName("Resources")])
    xobjects = engine._resolve(resources.mapping[PdfName("XObject")])
    form = PdfStream(
        b"q 1 0 0 1 0 0 cm Q",
        {PdfName("Type"): PdfName("XObject"), PdfName("Subtype"): PdfName("Form")},
    )
    xobjects.mapping[PdfName("Fm0")] = engine._cos_doc.register_object(form)
    # A name that is not an image is not something to put a picture into.
    assert engine.replace_page_image(0, "Fm0", _png((255, 0, 0))) is False
    assert form.content == b"q 1 0 0 1 0 0 cm Q"
    assert engine._get_name(form.mapping[PdfName("Subtype")]) == "Form"


def test_an_xobject_entry_that_is_not_a_stream_is_not_replaced():
    document = _document()
    engine = document._engine_pdf
    resources = engine._resolve(engine._get_page_dict(0).mapping[PdfName("Resources")])
    xobjects = engine._resolve(resources.mapping[PdfName("XObject")])
    xobjects.mapping[PdfName("Bogus")] = PdfName("NotAStream")
    assert engine.replace_page_image(0, "Bogus", _png((255, 0, 0))) is False
    assert engine.replace_page_image(0, "Missing", _png((255, 0, 0))) is False


def test_an_image_a_page_inherits_its_resources_for_is_found():
    # /Resources may sit on a page-tree node above the page; the image is still
    # the one that page draws.
    document = _document()
    engine = document._engine_pdf
    page = engine._get_page_dict(0)
    parent = engine._resolve(page.mapping[PdfName("Parent")])
    parent.mapping[PdfName("Resources")] = engine._resolve(
        page.mapping.pop(PdfName("Resources"))
    )
    assert engine.replace_page_image(0, "Im1", _png((255, 0, 0))) is True
    assert _colours(_reloaded(document))["red"] > 0


# --- the document as it stands, and encrypted ones ---------------------------


def test_the_new_picture_is_what_the_document_reads_back_without_a_save():
    document = _document()
    placement = _only_placement(document)
    placement.replace(_png((255, 0, 0)))
    engine = document._engine_pdf
    # Extraction, save_image and a second absorber all read the engine's own
    # model, so it has to say what the document now holds.
    assert engine.images["Im1"] == bytes([255, 0, 0]) * 16
    assert engine._image_sizes["Im1"] == (4, 4)
    assert engine._image_meta["Im1"]["width"] == 4
    # And the placement itself holds the same thing a freshly read one would:
    # the samples, not the bytes that were handed in.
    assert placement.image_data == bytes([255, 0, 0]) * 16
    assert placement.width == 4 and placement.color_space == "rgb"
    assert _only_placement(document).image_data == placement.image_data


def test_an_image_in_an_encrypted_document_is_replaced_readably():
    # The replacement is plaintext, and the writer has to encrypt it: marked as
    # already through the handler, it would be written as-is and decode to noise.
    document = _document()
    buffer = io.BytesIO()
    document.encrypt("owner", "user")
    document.save(buffer)

    encrypted = Document(io.BytesIO(buffer.getvalue()), password="user")
    assert _only_placement(encrypted).replace(_png((255, 0, 0))) is True
    out = io.BytesIO()
    encrypted.save(out)

    reopened = Document(io.BytesIO(out.getvalue()), password="user")
    assert _colours(reopened)["red"] == 80 * 80
    assert _only_placement(reopened).image_data == bytes([255, 0, 0]) * 16
