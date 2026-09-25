"""Flow exports resolve fonts and images in the scope of each paint operation."""

from __future__ import annotations

import base64
import io
import re
import struct
import zlib

import pytest

from aspose_pdf import Document, PdfLoadLimits, PdfResourceLimitException


def _stream(content: bytes, entries: bytes = b"") -> bytes:
    return (
        b"<< "
        + entries
        + b" /Length %d >>\nstream\n" % len(content)
        + content
        + b"\nendstream"
    )


def _pdf(objects: dict[int, bytes]) -> bytes:
    data = bytearray(b"%PDF-1.7\n")
    offsets = {}
    for number, value in sorted(objects.items()):
        offsets[number] = len(data)
        data += b"%d 0 obj\n" % number + value + b"\nendobj\n"
    start = len(data)
    size = max(objects) + 1
    data += b"xref\n0 %d\n" % size
    for number in range(size):
        data += (
            b"%010d 00000 n \n" % offsets[number]
            if number in offsets
            else b"0000000000 65535 f \n"
        )
    data += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        size,
        start,
    )
    return bytes(data)


def _image(colour: bytes, colour_space: bytes = b"/DeviceRGB") -> bytes:
    return _stream(
        zlib.compress(colour),
        b"/Type /XObject /Subtype /Image /Width 1 /Height 1 /BitsPerComponent 8 "
        b"/Filter /FlateDecode /ColorSpace " + colour_space,
    )


def _form(content: bytes, resources: bytes = b"", matrix: bytes = b"") -> bytes:
    return _stream(
        content,
        b"/Type /XObject /Subtype /Form /BBox [0 0 300 300] " + resources + matrix,
    )


def _font(cmap: int) -> bytes:
    return (
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /ToUnicode %d 0 R >>"
        % cmap
    )


def _cmap(word: str) -> bytes:
    return _stream(
        b"begincmap 1 begincodespacerange <00> <FF> endcodespacerange "
        b"1 beginbfchar <41> <"
        + word.encode("utf-16-be").hex().encode()
        + b"> endbfchar endcmap"
    )


def _objects() -> dict[int, bytes]:
    return {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 /MediaBox [0 0 300 300] "
        b"/Resources << /Font << /F1 10 0 R >> /XObject << /Im0 8 0 R /Fm 12 0 R >> >> >>",
        3: b"<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>",
        4: _stream(b"BT /F1 12 Tf 30 270 Td (A) Tj ET q 30 0 0 30 30 220 cm /Im0 Do Q"),
        5: b"<< /Type /Page /Parent 2 0 R /Contents 6 0 R /Resources << "
        b"/Font << /F1 10 0 R >> /XObject << /Im0 9 0 R /Fm 12 0 R >> >> >>",
        6: _stream(b"q 30 0 0 30 30 220 cm /Im0 Do Q"),
        8: _image(b"\xff\x00\x00"),
        9: _image(b"\x00\x00\xff"),
        10: _font(11),
        11: _cmap("Page"),
        12: _form(
            b"BT /F1 12 Tf 30 160 Td (A) Tj ET q 30 0 0 30 30 110 cm /Im0 Do Q /Nested Do",
            b"/Resources << /Font << /F1 13 0 R >> /XObject << /Im0 9 0 R /Nested 15 0 R >> >> ",
        ),
        13: _font(14),
        14: _cmap("Form"),
        15: _form(
            b"BT /F1 12 Tf 30 60 Td (A) Tj ET q 30 0 0 30 30 10 cm /Im0 Do Q",
            b"/Resources << /Font << /F1 16 0 R >> /XObject << /Im0 8 0 R >> >> ",
        ),
        16: _font(17),
        17: _cmap("Nested"),
    }


@pytest.fixture(params=["memory", "file", "streaming"])
def load(request, tmp_path):
    documents = []

    def open_document(objects, **kwargs):
        raw = _pdf(objects)
        if request.param == "memory":
            document = Document(io.BytesIO(raw), **kwargs)
        else:
            path = tmp_path / f"input-{len(documents)}.pdf"
            path.write_bytes(raw)
            opener = (
                Document.open_streaming if request.param == "streaming" else Document
            )
            document = opener(path, **kwargs)
        documents.append(document)
        return document

    yield open_document
    for document in documents:
        document.dispose()


@pytest.fixture(params=["to_html", "to_markdown"])
def export(request):
    return lambda document, **kwargs: getattr(document, request.param)(**kwargs)


def _pixels(png: bytes) -> bytes:
    """Read the unfiltered single RGB pixel emitted by the PNG writer."""
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    offset = 8
    payload = b""
    while offset < len(png):
        size = struct.unpack_from(">I", png, offset)[0]
        if png[offset + 4 : offset + 8] == b"IDAT":
            payload += png[offset + 8 : offset + 8 + size]
        offset += size + 12
    raw = zlib.decompress(payload)
    assert raw[0] == 0
    return raw[1:]


def _images(output: str) -> list[bytes]:
    return [
        _pixels(base64.b64decode(value))
        for value in re.findall(r"data:image/png;base64,([A-Za-z0-9+/=]+)", output)
    ]


def test_inherited_font_decodes_through_tounicode(load, export):
    output = export(load(_objects()), pages=[0], embed_images=False)
    assert "Page" in output


def test_same_image_name_on_different_pages_keeps_its_own_pixels(load, export):
    assert _images(export(load(_objects()))) == [b"\xff\x00\x00", b"\x00\x00\xff"]


def test_nested_forms_keep_their_fonts_and_images(load, export):
    objects = _objects()
    objects[4] = _stream(b"BT /F1 12 Tf 30 270 Td (A) Tj ET /Fm Do")
    output = export(load(objects), pages=[0])
    assert output.index("Page") < output.index("Form") < output.index("Nested")
    assert _images(output) == [b"\x00\x00\xff", b"\xff\x00\x00"]


@pytest.mark.parametrize("resources", [b"", b"/Resources << >>"])
def test_form_without_resources_inherits_images(load, export, resources):
    objects = _objects()
    objects[4] = _stream(b"/Fm Do")
    objects[12] = _form(b"q 30 0 0 30 30 220 cm /Im0 Do Q", resources)
    assert _images(export(load(objects), pages=[0])) == [b"\xff\x00\x00"]


def test_repeated_form_paints_are_preserved_and_transformed(load, export):
    objects = _objects()
    objects[4] = _stream(
        b"q 1 0 0 1 0 100 cm /Fm Do Q /Fm Do q 1 0 0 1 800 0 cm /Fm Do Q"
    )
    objects[12] = _form(
        b"q 30 0 0 30 30 10 cm /Im0 Do Q", b"/Resources << /XObject << /Im0 9 0 R >> >>"
    )
    assert _images(export(load(objects), pages=[0])) == [b"\x00\x00\xff"] * 2
    assert (
        _images(export(load(objects), pages=[0], clip_to_page=False))
        == [b"\x00\x00\xff"] * 3
    )


def test_cyclic_form_keeps_sibling_image_once(load, export):
    objects = _objects()
    objects[4] = _stream(b"/Fm Do")
    objects[12] = _form(b"q 30 0 0 30 30 110 cm /Im0 Do Q /Fm Do")
    assert _images(export(load(objects), pages=[0])) == [b"\xff\x00\x00"]


def test_form_image_decoding_respects_limits(load, export):
    objects = _objects()
    objects[4] = _stream(b"/Fm Do")
    objects[12] = _form(b"/Im0 Do", b"/Resources << /XObject << /Im0 18 0 R >> >>")
    objects[18] = _stream(
        b"x" * 300,
        b"/Subtype /Image /Width 10 /Height 10 /BitsPerComponent 8 /ColorSpace /DeviceRGB",
    )
    document = load(objects, limits=PdfLoadLimits(max_image_pixels=50))
    with pytest.raises(PdfResourceLimitException, match="max_image_pixels"):
        export(document, pages=[0])
    assert not _images(export(document, pages=[0], embed_images=False))


def test_an_unused_form_stream_is_not_decoded(load, export):
    objects = _objects()
    objects[12] = _stream(
        zlib.compress(b" " * 20_000),
        b"/Subtype /Form /BBox [0 0 300 300] /Filter /FlateDecode",
    )
    document = load(objects, limits=PdfLoadLimits(max_decoded_stream_bytes=1000))
    assert "Page" in export(document, pages=[0], embed_images=False)


def test_repeated_empty_forms_count_towards_traversal_limit(load, export):
    objects = _objects()
    objects[4] = _stream(b"/Fm Do " * 8)
    objects[12] = _form(
        b"/Nested Do " * 8, b"/Resources << /XObject << /Nested 15 0 R >> >>"
    )
    objects[15] = _form(b"")
    document = load(objects, limits=PdfLoadLimits(max_container_items=60))
    with pytest.raises(PdfResourceLimitException, match="export layout traversal"):
        export(document, pages=[0], embed_images=False)


def test_a_selected_streaming_page_does_not_decode_other_pages(tmp_path, export):
    objects = _objects()
    objects[6] = _stream(zlib.compress(b" " * 20_000), b"/Filter /FlateDecode")
    path = tmp_path / "selected-page.pdf"
    path.write_bytes(_pdf(objects))
    with Document.open_streaming(
        path, limits=PdfLoadLimits(max_decoded_stream_bytes=1000)
    ) as document:
        assert _images(export(document, pages=[0])) == [b"\xff\x00\x00"]
        with pytest.raises(PdfResourceLimitException, match="max_decoded_stream_bytes"):
            export(document, pages=[1])


def test_form_resources_do_not_merge_with_parent_resources(load, export):
    objects = _objects()
    objects[4] = _stream(b"/Fm Do")
    objects[12] = _form(
        b"BT /F1 12 Tf 30 200 Td (A) Tj ET /Im0 Do",
        b"/Resources << /Font << /F1 13 0 R >> /XObject << >> >>",
    )
    output = export(load(objects), pages=[0])
    assert "Form" in output
    assert not _images(output)


def test_form_matrix_and_calling_ctm_determine_image_order(load, export):
    objects = _objects()
    objects[4] = _stream(b"q 30 0 0 30 30 100 cm /Im0 Do Q q 2 0 0 2 0 0 cm /Fm Do Q")
    objects[12] = _form(
        b"q 15 0 0 15 15 0 cm /Im0 Do Q",
        b"/Resources << /XObject << /Im0 9 0 R >> >> ",
        b"/Matrix [1 0 0 1 0 80]",
    )
    assert _images(export(load(objects), pages=[0])) == [
        b"\x00\x00\xff",
        b"\xff\x00\x00",
    ]


@pytest.mark.parametrize("format", ["html", "markdown"])
def test_external_image_files_match_page_and_form_resources(load, tmp_path, format):
    objects = _objects()
    objects[4] = _stream(b"q 30 0 0 30 30 220 cm /Im0 Do Q /Fm Do")
    document = load(objects)
    if format == "html":
        paths = document.save_as_html(
            tmp_path / "out.html", resources_directory="images", split_into_pages=True
        )
    else:
        paths = [
            document.save_as_markdown(tmp_path / "out.md", image_directory="images")
        ]
    pictures = list((tmp_path / "images").glob("*.png"))
    assert len(pictures) == 2
    assert {_pixels(path.read_bytes()) for path in pictures} == {
        b"\xff\x00\x00",
        b"\x00\x00\xff",
    }
    output = "\n".join(path.read_text() for path in paths)
    assert "data:image" not in output
    for path in pictures:
        assert output.count("images/" + path.name) == 2


def test_pdf_round_trip_preserves_export_resource_scopes(load, tmp_path, export):
    objects = _objects()
    objects[4] = _stream(b"BT /F1 12 Tf 30 270 Td (A) Tj ET /Fm Do")
    original = load(objects)
    path = tmp_path / "round-trip.pdf"
    original.save(path)
    with Document(path) as reopened:
        output = export(reopened, pages=[0])
        assert output.index("Page") < output.index("Form") < output.index("Nested")
        assert _images(output) == [b"\x00\x00\xff", b"\xff\x00\x00"]


def test_named_image_colour_space_is_resolved_in_its_resource_scope(load, export):
    objects = _objects()
    objects[4] = _stream(b"q 30 0 0 30 30 220 cm /Im0 Do Q /Fm Do")
    objects[2] = objects[2].replace(
        b"/Font <<", b"/ColorSpace << /CS1 /DeviceRGB >> /Font <<"
    )
    objects[8] = _image(b"\x00\xff\xff\x00", b"/CS1")
    objects[12] = _form(
        b"q 30 0 0 30 30 110 cm /Im0 Do Q",
        b"/Resources << /ColorSpace << /CS1 /DeviceCMYK >> /XObject << /Im0 8 0 R >> >>",
    )
    document = load(objects)
    assert _images(export(document, pages=[0])) == [b"\x00\xff\xff", b"\xff\x00\x00"]
    assert _images(export(document, pages=[0])) == [b"\x00\xff\xff", b"\xff\x00\x00"]


@pytest.mark.parametrize("algorithm", ["RC4", "AES-128", "AES-256"])
def test_encrypted_form_resources_export_after_unlock(tmp_path, export, algorithm):
    objects = _objects()
    objects[4] = _stream(b"BT /F1 12 Tf 30 270 Td (A) Tj ET /Fm Do")
    path = tmp_path / "encrypted.pdf"
    with Document(_pdf(objects)) as original:
        original.encrypt("user", "owner", algorithm=algorithm)
        original.save(path)
    for opener in (Document, Document.open_streaming):
        with opener(path, password="user") as document:
            output = export(document, pages=[0])
            assert output.index("Page") < output.index("Form") < output.index("Nested")
            assert _images(output) == [b"\x00\x00\xff", b"\xff\x00\x00"]
