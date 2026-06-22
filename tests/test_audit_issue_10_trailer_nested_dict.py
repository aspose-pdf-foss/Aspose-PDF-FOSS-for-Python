"""AUDIT #10: trailer dictionary extraction must balance nested << >> (not <<.*?>>)."""

from __future__ import annotations

from aspose_pdf.engine.cos import PdfDictionary, PdfIndirectReference, PdfName
from aspose_pdf.engine.pdf_parser_cos import PdfCosParser, _cos_dictionary_bytes_at


def test_cos_dictionary_bytes_at_nested() -> None:
    raw = b"<< /Size 2 /Root 1 0 R /Meta << /Marked true >> >>"
    extracted = _cos_dictionary_bytes_at(raw, 0)
    assert extracted == raw


def test_cos_dictionary_bytes_at_rejects_non_dict_start() -> None:
    assert _cos_dictionary_bytes_at(b"foo<<>>", 0) is None
    assert _cos_dictionary_bytes_at(b"<< x >>", 3) is None


def test_traditional_xref_trailer_with_nested_dictionary() -> None:
    """First >> must not close the outer trailer when an inner dict is present."""
    header = b"%PDF-1.7\n"
    obj1 = b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    offset1 = len(header)
    xref_pos = len(header) + len(obj1)
    xref = b"xref\n0 2\n0000000000 65535 f \n%010d 00000 n \n" % offset1
    trailer = b"trailer\n<< /Size 2 /Root 1 0 R /Meta << /Marked true >> >>\n"
    startxref = b"startxref\n%d\n%%EOF" % xref_pos
    pdf_bytes = header + obj1 + xref + trailer + startxref

    doc = PdfCosParser(pdf_bytes).parse()
    root_ref = doc.trailer.mapping.get(PdfName("Root"))
    assert isinstance(root_ref, PdfIndirectReference)
    assert root_ref.object_number == 1

    meta = doc.trailer.mapping.get(PdfName("Meta"))
    assert isinstance(meta, PdfDictionary)
    marked = meta.mapping.get(PdfName("Marked"))
    assert marked is not None
    assert getattr(marked, "value", None) is True


def test_reconstruct_xref_trailer_with_nested_dictionary() -> None:
    """Recovery scan merges trailer dicts; nested << >> must parse completely."""
    header = b"%PDF-1.7\n"
    obj1 = b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    xref_pos = len(header) + len(obj1)
    bad_xref = b"xref\nnot_a_number\n"
    trailer = (
        b"trailer\n<< /Size 2 /Root 1 0 R /PieceInfo << /LastModified (D:1) >> >>\n"
    )
    startxref = b"startxref\n%d\n%%EOF" % xref_pos
    data = header + obj1 + bad_xref + trailer + startxref

    doc = PdfCosParser(data).parse()
    root_ref = doc.trailer.mapping.get(PdfName("Root"))
    assert isinstance(root_ref, PdfIndirectReference)
    piece = doc.trailer.mapping.get(PdfName("PieceInfo"))
    assert isinstance(piece, PdfDictionary)
    assert PdfName("LastModified") in piece.mapping
