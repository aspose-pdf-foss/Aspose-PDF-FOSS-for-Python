"""PDF/A-1 conversion drops transparency groups that have no effect.

ISO 19005-1 6.4 forbids transparency groups outright, and many producers stamp
`/Group /S /Transparency` on every page whether or not anything inside uses
transparency. Such a group cannot change the rendered result, so removing it is
lossless. Real transparency is a different matter: flattening it correctly needs
the page composited against its actual backdrop, so it is reported, not removed.
"""

from __future__ import annotations

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfStream,
)
from aspose_pdf.engine.simple_pdf import SimplePdf

TRANSPARENCY_GROUP = PdfDictionary(
    {PdfName("S"): PdfName("Transparency"), PdfName("Type"): PdfName("Group")}
)


def _document(*, resources: PdfDictionary | None = None, group: bool = True):
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 100, 100)]
    pdf.page_contents = [b"0 0 10 10 re f"]
    pdf._ensure_cos()
    page = pdf._get_page_dict(0)
    if group:
        page.mapping[PdfName("Group")] = PdfDictionary(
            dict(TRANSPARENCY_GROUP.mapping)
        )
    page.mapping[PdfName("Resources")] = resources or PdfDictionary({})
    document = Document()
    document._engine_pdf = pdf
    return document


def _has_group(document: Document) -> bool:
    page = document._engine_pdf._get_page_dict(0)
    return PdfName("Group") in page.mapping


def _gs_resources(entries: dict) -> PdfDictionary:
    """Resources holding one ExtGState. PdfName keys cannot go through **kwargs."""
    return PdfDictionary(
        {
            PdfName("ExtGState"): PdfDictionary(
                {PdfName("GS0"): PdfDictionary(entries)}
            )
        }
    )


# --- inert groups are removed ---------------------------------------------
def test_inert_page_group_is_dropped():
    document = _document()
    assert _has_group(document)
    document.convert_to_pdfa("1b")
    assert not _has_group(document)


def test_opaque_extgstate_does_not_keep_the_group():
    document = _document(
        resources=_gs_resources(
            {
                PdfName("ca"): PdfNumber(1),
                PdfName("CA"): PdfNumber(1),
                PdfName("BM"): PdfName("Normal"),
                PdfName("SMask"): PdfName("None"),
            }
        )
    )
    document.convert_to_pdfa("1b")
    assert not _has_group(document)


def test_group_is_kept_for_pdfa_2_where_transparency_is_allowed():
    document = _document()
    document.convert_to_pdfa("2b")
    assert _has_group(document)


# --- real transparency keeps the group ------------------------------------
@pytest.mark.parametrize(
    "entries",
    [
        {PdfName("ca"): PdfNumber(0.5)},
        {PdfName("CA"): PdfNumber(0.25)},
        {PdfName("BM"): PdfName("Multiply")},
        {PdfName("BM"): PdfArray([PdfName("Screen")])},
        {PdfName("SMask"): PdfDictionary({PdfName("S"): PdfName("Luminosity")})},
    ],
)
def test_real_transparency_keeps_the_group(entries):
    document = _document(resources=_gs_resources(entries))
    document.convert_to_pdfa("1b")
    assert _has_group(document), "a group over real transparency must stay"


def test_image_soft_mask_keeps_the_group():
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 100, 100)]
    pdf.page_contents = [b"q /Im0 Do Q"]
    pdf._ensure_cos()
    cos = pdf._cos_doc
    mask = cos.register_object(
        PdfStream(
            b"\x00",
            {
                PdfName("Subtype"): PdfName("Image"),
                PdfName("Width"): PdfNumber(1),
                PdfName("Height"): PdfNumber(1),
                PdfName("Length"): PdfNumber(1),
            },
        )
    )
    image = cos.register_object(
        PdfStream(
            b"\x00\x00\x00",
            {
                PdfName("Subtype"): PdfName("Image"),
                PdfName("Width"): PdfNumber(1),
                PdfName("Height"): PdfNumber(1),
                PdfName("SMask"): mask,
                PdfName("Length"): PdfNumber(3),
            },
        )
    )
    page = pdf._get_page_dict(0)
    page.mapping[PdfName("Group")] = PdfDictionary(dict(TRANSPARENCY_GROUP.mapping))
    page.mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("XObject"): PdfDictionary({PdfName("Im0"): image})}
    )
    document = Document()
    document._engine_pdf = pdf
    document.convert_to_pdfa("1b")
    assert PdfName("Group") in page.mapping


# --- form XObjects ---------------------------------------------------------
def _document_with_form(*, inner_resources=None, form_group=True):
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 100, 100)]
    pdf.page_contents = [b"q /Fx0 Do Q"]
    pdf._ensure_cos()
    cos = pdf._cos_doc
    inner = b"0 0 5 5 re f"
    form_map = {
        PdfName("Subtype"): PdfName("Form"),
        PdfName("BBox"): PdfArray([PdfNumber(v) for v in (0, 0, 10, 10)]),
        PdfName("Length"): PdfNumber(len(inner)),
    }
    if form_group:
        form_map[PdfName("Group")] = PdfDictionary(dict(TRANSPARENCY_GROUP.mapping))
    if inner_resources is not None:
        form_map[PdfName("Resources")] = inner_resources
    form = cos.register_object(PdfStream(inner, form_map))
    page = pdf._get_page_dict(0)
    page.mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("XObject"): PdfDictionary({PdfName("Fx0"): form})}
    )
    document = Document()
    document._engine_pdf = pdf
    return document, cos.objects[form.object_number], page


def test_inert_form_group_is_dropped():
    document, form, _page = _document_with_form()
    document.convert_to_pdfa("1b")
    assert PdfName("Group") not in form.mapping


def test_form_group_over_transparency_is_kept():
    document, form, _page = _document_with_form(
        inner_resources=_gs_resources({PdfName("ca"): PdfNumber(0.4)})
    )
    document.convert_to_pdfa("1b")
    assert PdfName("Group") in form.mapping


def test_a_nested_group_keeps_the_page_group():
    """A group inside a form is transparency in its own right."""
    document, _form, page = _document_with_form(form_group=True)
    page.mapping[PdfName("Group")] = PdfDictionary(dict(TRANSPARENCY_GROUP.mapping))
    # The form's own group is inert and goes, but while scanning the page the
    # nested group still counts, so the page group is preserved.
    document.convert_to_pdfa("1b")
    assert PdfName("Group") in page.mapping


def test_conversion_reports_the_transparency_it_cannot_remove():
    document = _document(resources=_gs_resources({PdfName("ca"): PdfNumber(0.5)}))
    problems = document.convert_to_pdfa("1b")
    assert any("constant alpha" in problem for problem in problems)
    assert any("transparency group" in problem.lower() for problem in problems)


def test_page_content_is_untouched():
    """Dropping an inert group must not rewrite anything that draws."""
    document = _document()
    before = document._engine_pdf.page_contents[0]
    document.convert_to_pdfa("1b")
    assert document._engine_pdf.page_contents[0] == before
