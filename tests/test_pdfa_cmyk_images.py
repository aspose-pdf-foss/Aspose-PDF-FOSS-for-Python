"""PDF/A conversion rewrites CMYK image XObjects to DeviceRGB.

``convert_to_pdfa`` installs an sRGB OutputIntent, under which DeviceCMYK is
non-conformant. Content-level colour was already normalized; these cover the
image payloads — ``/DeviceCMYK`` and ICC-CMYK, raw or DCT-encoded, at the page
level and nested inside form XObjects — which are decoded through the same path
the renderer uses and re-encoded as 8-bit DeviceRGB.
"""

from __future__ import annotations

import zlib

from aspose_pdf import Document
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfStream,
)
from aspose_pdf.engine.simple_pdf import SimplePdf

# Pure cyan and pure black, 2x1, 8bpc CMYK.
CMYK_PIXELS = bytes([255, 0, 0, 0, 0, 0, 0, 255])
EXPECTED_RGB = bytes([0, 255, 255, 0, 0, 0])


def _image_stream(content: bytes, colorspace, extra=None) -> PdfStream:
    mapping = {
        PdfName("Type"): PdfName("XObject"),
        PdfName("Subtype"): PdfName("Image"),
        PdfName("Width"): PdfNumber(2),
        PdfName("Height"): PdfNumber(1),
        PdfName("BitsPerComponent"): PdfNumber(8),
        PdfName("ColorSpace"): colorspace,
        PdfName("Length"): PdfNumber(len(content)),
    }
    mapping.update(extra or {})
    return PdfStream(content, mapping)


def _document_with(image: PdfStream, *, nested: bool = False) -> Document:
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 100, 100)]
    pdf.page_contents = [
        b"q 100 0 0 100 0 0 cm /Fx0 Do Q" if nested
        else b"q 100 0 0 100 0 0 cm /Im0 Do Q"
    ]
    pdf._ensure_cos()
    cos = pdf._cos_doc
    image_ref = cos.register_object(image)

    if nested:
        inner = b"q 1 0 0 1 0 0 cm /Im0 Do Q"
        form = cos.register_object(
            PdfStream(
                inner,
                {
                    PdfName("Type"): PdfName("XObject"),
                    PdfName("Subtype"): PdfName("Form"),
                    PdfName("BBox"): PdfArray([PdfNumber(v) for v in (0, 0, 1, 1)]),
                    PdfName("Length"): PdfNumber(len(inner)),
                    PdfName("Resources"): PdfDictionary(
                        {PdfName("XObject"): PdfDictionary({PdfName("Im0"): image_ref})}
                    ),
                },
            )
        )
        xobjects = PdfDictionary({PdfName("Fx0"): form})
    else:
        xobjects = PdfDictionary({PdfName("Im0"): image_ref})

    cos.objects[pdf._page_obj_ids[0]].mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("XObject"): xobjects}
    )
    return Document().load_from(pdf.to_bytes())


def _find_image(document: Document) -> PdfDictionary:
    """Return the one image XObject, wherever it sits in the resource tree."""
    engine = document._engine_pdf

    def walk(resources, depth=0):
        if depth > 4 or not isinstance(resources, PdfDictionary):
            return None
        xobjects = engine._resolve(resources.mapping.get(PdfName("XObject")))
        if not isinstance(xobjects, PdfDictionary):
            return None
        for ref in xobjects.mapping.values():
            stream = engine._resolve(ref)
            if not isinstance(stream, PdfStream):
                continue
            if engine._get_name(stream.mapping.get(PdfName("Subtype"))) == "Image":
                return stream
            found = walk(
                engine._resolve(stream.mapping.get(PdfName("Resources"))), depth + 1
            )
            if found is not None:
                return found
        return None

    image = walk(engine._cos_page_resources(0))
    assert image is not None, "image XObject not found"
    return image


def _assert_converted(image: PdfStream, expected: bytes = EXPECTED_RGB) -> None:
    assert image.mapping[PdfName("ColorSpace")] == PdfName("DeviceRGB")
    assert image.mapping[PdfName("BitsPerComponent")].value == 8
    assert image.mapping[PdfName("Filter")] == PdfName("FlateDecode")
    assert zlib.decompress(image.content) == expected
    assert int(image.mapping[PdfName("Length")].value) == len(image.content)
    # Parameters that described the CMYK payload must not survive.
    assert PdfName("DecodeParms") not in image.mapping
    assert PdfName("Decode") not in image.mapping


def test_flate_devicecmyk_image_becomes_rgb():
    packed = zlib.compress(CMYK_PIXELS)
    document = _document_with(
        _image_stream(
            packed, PdfName("DeviceCMYK"), {PdfName("Filter"): PdfName("FlateDecode")}
        )
    )
    document.convert_to_pdfa("2b")
    _assert_converted(_find_image(document))


def test_uncompressed_devicecmyk_image_becomes_rgb():
    document = _document_with(_image_stream(CMYK_PIXELS, PdfName("DeviceCMYK")))
    document.convert_to_pdfa("2b")
    _assert_converted(_find_image(document))


def test_icc_cmyk_image_becomes_rgb():
    """An /ICCBased space with /N 4 is CMYK just as much as /DeviceCMYK."""
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 100, 100)]
    pdf.page_contents = [b"q 100 0 0 100 0 0 cm /Im0 Do Q"]
    pdf._ensure_cos()
    cos = pdf._cos_doc
    profile = cos.register_object(
        PdfStream(
            b"\x00" * 128,
            {PdfName("N"): PdfNumber(4), PdfName("Length"): PdfNumber(128)},
        )
    )
    colorspace = PdfArray([PdfName("ICCBased"), profile])
    image = cos.register_object(_image_stream(CMYK_PIXELS, colorspace))
    cos.objects[pdf._page_obj_ids[0]].mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("XObject"): PdfDictionary({PdfName("Im0"): image})}
    )
    document = Document().load_from(pdf.to_bytes())
    document.convert_to_pdfa("2b")
    _assert_converted(_find_image(document))


def test_cmyk_image_inside_a_form_xobject_is_converted():
    """Form XObject resources are walked, not just the page's own."""
    document = _document_with(
        _image_stream(CMYK_PIXELS, PdfName("DeviceCMYK")), nested=True
    )
    document.convert_to_pdfa("2b")
    _assert_converted(_find_image(document))


def test_dct_cmyk_image_becomes_rgb():
    """A DCTDecode CMYK payload decodes through the renderer's JPEG path."""
    from aspose_pdf.engine.jpeg_encoder import encode

    jpeg = encode(2, 1, 4, CMYK_PIXELS, quality=95)
    document = _document_with(
        _image_stream(
            jpeg, PdfName("DeviceCMYK"), {PdfName("Filter"): PdfName("DCTDecode")}
        )
    )
    document.convert_to_pdfa("2b")
    image = _find_image(document)
    assert image.mapping[PdfName("ColorSpace")] == PdfName("DeviceRGB")
    assert image.mapping[PdfName("Filter")] == PdfName("FlateDecode")
    pixels = zlib.decompress(image.content)
    assert len(pixels) == 2 * 1 * 3
    # JPEG is lossy: check the hues, not exact bytes.
    assert pixels[0] < 60 and pixels[1] > 190 and pixels[2] > 190  # cyan
    assert all(channel < 70 for channel in pixels[3:6])  # black


def test_non_cmyk_images_are_untouched():
    rgb = bytes([10, 20, 30, 40, 50, 60])
    document = _document_with(_image_stream(rgb, PdfName("DeviceRGB")))
    document.convert_to_pdfa("2b")
    image = _find_image(document)
    assert image.mapping[PdfName("ColorSpace")] == PdfName("DeviceRGB")
    assert image.content == rgb
    assert PdfName("Filter") not in image.mapping


def test_converted_image_survives_save_and_reload():
    document = _document_with(_image_stream(CMYK_PIXELS, PdfName("DeviceCMYK")))
    document.convert_to_pdfa("2b")
    from io import BytesIO

    output = BytesIO()
    document.save(output)
    _assert_converted(_find_image(Document().load_from(output.getvalue())))
