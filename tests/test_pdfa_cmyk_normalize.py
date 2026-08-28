"""PDF/A conversion normalizes DeviceCMYK content color to RGB.

``convert_to_pdfa`` adds an sRGB OutputIntent, under which DeviceCMYK content is
non-compliant. It now rewrites the ``k``/``K`` operators and ``/DeviceCMYK``
color-space fills/strokes to their RGB equivalents (same naive device
conversion the renderer uses, so appearance is unchanged), and converts CMYK
*image* XObjects to DeviceRGB. ``/Separation`` and ``/DeviceN`` remain a
documented boundary: left as-is and still reported.
"""

from __future__ import annotations

import zlib

from aspose_pdf.document import Document
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfStream,
)
from aspose_pdf.engine.simple_pdf import SimplePdf


def _pdf_with_content(content: bytes) -> bytes:
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 100, 100)]
    pdf.page_contents = [content]
    pdf._ensure_cos()
    cos = pdf._cos_doc
    cos.objects[pdf._page_obj_ids[0]].mapping[PdfName("Resources")] = PdfDictionary(
        {}
    )
    return pdf.to_bytes()


def _converted_page_content(content: bytes) -> bytes:
    doc = Document()
    doc.load_from(_pdf_with_content(content))
    doc.convert_to_pdfa("1b")
    return doc._engine_pdf.page_contents[0]


def test_k_operator_becomes_rgb():
    out = _converted_page_content(b"1 0 0 0 k 10 10 50 50 re f")
    assert b"DeviceCMYK" not in out
    assert b"0 1 1 rg" in out  # cyan -> RGB
    assert b" k " not in b" " + out + b" "  # no CMYK fill operator remains
    # surrounding operators are preserved
    assert b"re" in out and b"f" in out


def test_stroke_and_colorspace_scn_become_rgb():
    out = _converted_page_content(
        b"0 0 0 1 K /DeviceCMYK cs 1 0 0 0 scn 5 5 m 20 20 l S"
    )
    assert b"DeviceCMYK" not in out
    assert b"0 0 0 RG" in out  # black stroke
    assert b"/DeviceRGB cs" in out
    assert b"0 1 1 scn" in out  # 4-operand CMYK scn -> 3-operand RGB


def test_non_cmyk_content_is_untouched():
    original = b"0.5 g 1 0 0 rg 10 10 20 20 re f"
    out = _converted_page_content(original)
    assert b"1 0 0 rg" in out and b"0.5 g" in out


def test_cmyk_image_xobject_becomes_devicergb():
    # A CMYK image XObject is converted to DeviceRGB, pixels and all.
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 100, 100)]
    pdf.page_contents = [b"q 100 0 0 100 0 0 cm /Im0 Do Q"]
    pdf._ensure_cos()
    cos = pdf._cos_doc
    image = cos.register_object(
        PdfStream(
            b"\x00\x00\x00\x00",
            {
                PdfName("Type"): PdfName("XObject"),
                PdfName("Subtype"): PdfName("Image"),
                PdfName("Width"): PdfNumber(1),
                PdfName("Height"): PdfNumber(1),
                PdfName("BitsPerComponent"): PdfNumber(8),
                PdfName("ColorSpace"): PdfName("DeviceCMYK"),
                PdfName("Length"): PdfNumber(4),
            },
        )
    )
    cos.objects[pdf._page_obj_ids[0]].mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("XObject"): PdfDictionary({PdfName("Im0"): image})}
    )
    doc = Document()
    doc.load_from(pdf.to_bytes())
    doc.convert_to_pdfa("1b")

    engine = doc._engine_pdf
    page = engine._get_page_dict(0)
    res = engine._resolve(page.get(PdfName("Resources")))
    xobjects = engine._resolve(res.get(PdfName("XObject")))
    im = engine._resolve(xobjects.get(PdfName("Im0")))
    assert engine._get_name(im.mapping.get(PdfName("ColorSpace"))) == "DeviceRGB"
    assert engine._get_number(im.mapping.get(PdfName("BitsPerComponent"))) == 8
    # 0,0,0,0 CMYK is white; the payload is now three RGB bytes.
    assert zlib.decompress(im.content) == b"\xff\xff\xff"


def test_cmyk_content_inside_a_form_xobject_is_normalized():
    """Form XObject *content* is reached through the page's COS resources.

    The walker previously tested the plain-dict view of the resources against
    PdfDictionary, which never matches, so nested content was silently skipped.
    """
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 100, 100)]
    pdf.page_contents = [b"q /Fx0 Do Q"]
    pdf._ensure_cos()
    cos = pdf._cos_doc
    inner = b"0.1 0.2 0.3 0.4 k 0 0 10 10 re f"
    form = cos.register_object(
        PdfStream(
            inner,
            {
                PdfName("Type"): PdfName("XObject"),
                PdfName("Subtype"): PdfName("Form"),
                PdfName("BBox"): PdfArray([PdfNumber(v) for v in (0, 0, 10, 10)]),
                PdfName("Length"): PdfNumber(len(inner)),
            },
        )
    )
    cos.objects[pdf._page_obj_ids[0]].mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("XObject"): PdfDictionary({PdfName("Fx0"): form})}
    )

    document = Document().load_from(pdf.to_bytes())
    document.convert_to_pdfa("2b")

    engine = document._engine_pdf
    xobjects = engine._resolve(
        engine._cos_page_resources(0).mapping[PdfName("XObject")]
    )
    content = engine._resolve(next(iter(xobjects.mapping.values()))).content
    assert b" k" not in content
    assert b" rg" in content
