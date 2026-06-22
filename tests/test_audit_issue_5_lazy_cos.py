"""AUDIT issue #5: lazy COS object bodies (xref metadata up front, parse on access)."""

from __future__ import annotations

from aspose_pdf.engine.pdf_parser_cos import LazyPdfObjectStore, PdfCosParser
from aspose_pdf.engine.simple_pdf import SimplePdf


def _pdf_with_orphan_objects(num_orphans: int) -> bytes:
    """Minimal one-page PDF with *num_orphans* extra objects not referenced from Root."""
    buf = bytearray()
    buf.extend(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}

    def add_obj(num: int, inner: bytes) -> None:
        offsets[num] = len(buf)
        buf.extend(f"{num} 0 obj\n".encode("ascii"))
        buf.extend(inner)
        buf.extend(b"\nendobj\n")

    add_obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    add_obj(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    add_obj(
        3,
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>",
    )
    first_orphan = 4
    for i in range(first_orphan, first_orphan + num_orphans):
        add_obj(i, b"<< /Orphan 1 >>")

    last_obj = first_orphan + num_orphans - 1
    size = last_obj + 1

    xref_pos = len(buf)
    buf.extend(b"xref\n")
    buf.extend(f"0 {size}\n".encode("ascii"))
    buf.extend(b"0000000000 65535 f \n")
    for i in range(1, size):
        buf.extend(f"{offsets[i]:010d} 00000 n \n".encode("ascii"))
    buf.extend(
        f"trailer\n<< /Size {size} /Root 1 0 R >>\n".encode("ascii")
        + f"startxref\n{xref_pos}\n%%EOF".encode("ascii")
    )
    return bytes(buf)


def test_parse_returns_lazy_object_store() -> None:
    from aspose_pdf.engine.cos import PdfName

    data = _pdf_with_orphan_objects(5)
    doc = PdfCosParser(data).parse()
    assert isinstance(doc.objects, LazyPdfObjectStore)
    assert doc.objects.materialized_count == 0
    assert len(doc.objects) == 8  # catalog, pages, page, five orphans → ids 1…8
    assert doc.trailer.get(PdfName("Root")) is not None


def test_lazy_materialization_less_than_xref_count_after_simple_pdf_load() -> None:
    orphans = 15
    data = _pdf_with_orphan_objects(orphans)
    sp = SimplePdf.from_bytes(data)
    assert sp._cos_doc is not None
    store = sp._cos_doc.objects
    assert isinstance(store, LazyPdfObjectStore)
    total_slots = len(store)
    assert total_slots == 3 + orphans
    # Reachability from Root should not pull every orphan object into the cache.
    assert store.materialized_count < total_slots


def test_lazy_store_bool_and_contains_no_eager_load() -> None:
    data = _pdf_with_orphan_objects(4)
    doc = PdfCosParser(data).parse()
    store = doc.objects
    assert isinstance(store, LazyPdfObjectStore)
    assert store
    assert 99 not in store
    assert 5 in store
    assert store.materialized_count == 0


def test_getitem_loads_single_object() -> None:
    data = _pdf_with_orphan_objects(2)
    doc = PdfCosParser(data).parse()
    store = doc.objects
    _ = store[1]
    assert store.materialized_count >= 1
