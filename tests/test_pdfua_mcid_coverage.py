"""PDF/UA MCID coverage checks between page content and the /ParentTree."""

from __future__ import annotations

from aspose_pdf.engine.auto_tag import find_mcids
from aspose_pdf.engine.conformance import pdfua_mcid_coverage
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNull,
    PdfNumber,
)
from aspose_pdf.engine.simple_pdf import SimplePdf


def test_find_mcids_extracts_and_skips_strings():
    content = (
        b"/P <</MCID 0>> BDC (hi) Tj EMC\n"
        b"/Figure <</MCID 2>> BDC /Im Do EMC\n"
        b"BT (a BDC <</MCID 99>> lookalike) Tj ET"  # inside a string -> ignored
    )
    assert find_mcids(content) == {0, 2}


def test_find_mcids_ignores_named_property_lists():
    # /Tag /P1 BDC has no inline /MCID dict, so nothing is reported.
    assert find_mcids(b"/Span /P1 BDC (x) Tj EMC") == set()


def _elem():
    return PdfDictionary(
        {PdfName("Type"): PdfName("StructElem"), PdfName("S"): PdfName("P")}
    )


def _doc(content: bytes, parent_slots, *, struct_parents=0, tree_key=None):
    """A one-page doc with a /ParentTree mapping the given MCID slots.

    *parent_slots* is indexed by MCID: a truthy value becomes a StructElem, and
    ``None`` a null slot. *tree_key* defaults to *struct_parents* (the matching
    case); set it different to simulate a missing /ParentTree entry.
    """
    pdf = SimplePdf(pages=[(0, 0, 612, 792)], page_contents=[content])
    pdf._ensure_cos()
    items = [_elem() if slot else PdfNull() for slot in parent_slots]
    parent_tree = PdfDictionary(
        {
            PdfName("Nums"): PdfArray(
                [PdfNumber(struct_parents if tree_key is None else tree_key), PdfArray(items)]
            )
        }
    )
    struct_root = PdfDictionary(
        {PdfName("Type"): PdfName("StructTreeRoot"), PdfName("ParentTree"): parent_tree}
    )
    root = pdf._resolve(pdf._cos_doc.trailer.get(PdfName("Root")))
    root.mapping[PdfName("StructTreeRoot")] = struct_root
    pdf._get_page_dict(0).mapping[PdfName("StructParents")] = PdfNumber(struct_parents)
    return pdf


_TWO_MARKS = b"/P <</MCID 0>> BDC (a) Tj EMC\n/P <</MCID 1>> BDC (b) Tj EMC"


def test_full_coverage_has_no_warnings():
    errors, warnings = pdfua_mcid_coverage(_doc(_TWO_MARKS, [True, True]))
    assert errors == [] and warnings == []


def test_uncovered_content_mcid_warns():
    # Content uses MCID 0 and 1; the parent array only maps index 0.
    _errors, warnings = pdfua_mcid_coverage(_doc(_TWO_MARKS, [True]))
    assert any("MCID 1" in w and "not mapped" in w for w in warnings)


def test_null_slot_leaves_content_uncovered():
    _errors, warnings = pdfua_mcid_coverage(_doc(_TWO_MARKS, [True, None]))
    assert any("MCID 1" in w and "not mapped" in w for w in warnings)


def test_dangling_parent_tree_reference_warns():
    # Content uses only MCID 0; the parent array maps 0 and 1.
    content = b"/P <</MCID 0>> BDC (a) Tj EMC"
    _errors, warnings = pdfua_mcid_coverage(_doc(content, [True, True]))
    assert any("MCID 1" in w and "no marked content" in w for w in warnings)


def test_missing_parent_tree_entry_warns():
    content = b"/P <</MCID 0>> BDC (a) Tj EMC"
    pdf = _doc(content, [True], struct_parents=3, tree_key=7)  # keys mismatch
    _errors, warnings = pdfua_mcid_coverage(pdf)
    assert any("StructParents 3" in w and "no matching" in w for w in warnings)


def test_no_struct_parents_is_skipped():
    # A page without /StructParents is not checked (nothing to map).
    pdf = SimplePdf(pages=[(0, 0, 612, 792)], page_contents=[_TWO_MARKS])
    pdf._ensure_cos()
    root = pdf._resolve(pdf._cos_doc.trailer.get(PdfName("Root")))
    root.mapping[PdfName("StructTreeRoot")] = PdfDictionary(
        {
            PdfName("Type"): PdfName("StructTreeRoot"),
            PdfName("ParentTree"): PdfDictionary(
                {PdfName("Nums"): PdfArray([PdfNumber(0), PdfArray([_elem()])])}
            ),
        }
    )
    errors, warnings = pdfua_mcid_coverage(pdf)
    assert errors == [] and warnings == []
