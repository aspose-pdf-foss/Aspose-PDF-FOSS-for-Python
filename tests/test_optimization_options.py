"""Tests for OptimizationOptions-driven document optimization.

Covers the flag wiring on :meth:`SimplePdf.optimize`, the garbage-collection
fix that preserves trailer-only objects (``/Info``/``/Encrypt``), COS stream
deduplication, the narrow unused-stream prune, font-program compression, the
compression/GC decoupling, and the public ``Document`` / low-code surfaces.
"""

import io
import zlib

from aspose_pdf import (
    ByteArrayDataSource,
    Document,
    OptimizationOptions,
    Optimizer,
    OptimizeOptions,
)
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfStream,
    PdfString,
)
from aspose_pdf.engine.simple_pdf import SimplePdf


def _new_cos_pdf():
    pdf = SimplePdf()
    pdf._ensure_cos()
    return pdf, pdf._cos_doc


def _root(pdf, cos):
    return pdf._resolve(cos.trailer.mapping[PdfName("Root")])


# ---------------------------------------------------------------------------
# Image deduplication flag
# ---------------------------------------------------------------------------


def test_remove_duplicate_images_default_collapses():
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 612, 792)]
    data = b"image content bytes"
    pdf.images = {"img1": data, "img2": data}
    pdf.page_contents = [b"/img2 Do"]

    pdf.optimize()  # default options: remove_duplicate_images=True

    assert len(pdf.images) == 1
    assert b"/img1 Do" in pdf.page_contents[0]


def test_remove_duplicate_images_disabled_keeps_both():
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 612, 792)]
    data = b"image content bytes"
    pdf.images = {"img1": data, "img2": data}
    pdf.page_contents = [b"/img2 Do"]

    pdf.optimize(OptimizationOptions(remove_duplicate_images=False))

    assert len(pdf.images) == 2
    assert b"/img2 Do" in pdf.page_contents[0]


# ---------------------------------------------------------------------------
# Garbage collection: /Info & /Encrypt must survive (regression)
# ---------------------------------------------------------------------------


def test_optimize_preserves_info_and_encrypt():
    """The old inline GC traversed only from /Root and could delete trailer-only
    objects. optimize() now uses the all-trailer-roots garbage_collect()."""
    pdf, cos = _new_cos_pdf()

    info_ref = cos.register_object(
        PdfDictionary({PdfName("Producer"): PdfString("test")})
    )
    cos.trailer.mapping[PdfName("Info")] = info_ref
    enc_ref = cos.register_object(PdfDictionary({PdfName("V"): PdfNumber(2)}))
    cos.trailer.mapping[PdfName("Encrypt")] = enc_ref

    orphan_ref = cos.register_object(PdfDictionary({PdfName("Junk"): PdfName("X")}))
    orphan_num = orphan_ref.object_number

    pdf.optimize()  # default options -> garbage_collect()

    assert info_ref.object_number in cos.objects
    assert enc_ref.object_number in cos.objects
    assert orphan_num not in cos.objects


# ---------------------------------------------------------------------------
# Link duplicate streams
# ---------------------------------------------------------------------------


def test_link_duplicate_streams_collapses():
    pdf, cos = _new_cos_pdf()
    root = _root(pdf, cos)
    ref1 = cos.register_object(PdfStream(b"shared stream payload AAAA"))
    ref2 = cos.register_object(PdfStream(b"shared stream payload AAAA"))
    root.mapping[PdfName("S1")] = ref1
    root.mapping[PdfName("S2")] = ref2
    n1, n2 = ref1.object_number, ref2.object_number

    pdf.optimize(OptimizationOptions(remove_unused_objects=False))

    # References to the byte-identical streams now point at one canonical object.
    assert ref1.object_number == ref2.object_number
    survivor = ref1.object_number
    assert survivor in cos.objects
    removed = n2 if survivor == n1 else n1
    assert removed not in cos.objects


def test_link_duplicate_streams_disabled_keeps_both():
    pdf, cos = _new_cos_pdf()
    root = _root(pdf, cos)
    ref1 = cos.register_object(PdfStream(b"shared stream payload AAAA"))
    ref2 = cos.register_object(PdfStream(b"shared stream payload AAAA"))
    root.mapping[PdfName("S1")] = ref1
    root.mapping[PdfName("S2")] = ref2

    pdf.optimize(
        OptimizationOptions(
            remove_unused_objects=False, link_duplicate_streams=False
        )
    )

    assert ref1.object_number != ref2.object_number
    assert ref1.object_number in cos.objects
    assert ref2.object_number in cos.objects


def _two_page_content_pdf():
    pdf, cos = _new_cos_pdf()
    root = _root(pdf, cos)
    c1 = cos.register_object(PdfStream(b"BT (Hi) Tj ET shared content"))
    c2 = cos.register_object(PdfStream(b"BT (Hi) Tj ET shared content"))
    p1 = cos.register_object(
        PdfDictionary({PdfName("Type"): PdfName("Page"), PdfName("Contents"): c1})
    )
    p2 = cos.register_object(
        PdfDictionary({PdfName("Type"): PdfName("Page"), PdfName("Contents"): c2})
    )
    root.mapping[PdfName("P1")] = p1
    root.mapping[PdfName("P2")] = p2
    return pdf, cos, c1, c2


def test_page_content_protected_when_reuse_disallowed():
    pdf, cos, c1, c2 = _two_page_content_pdf()
    pdf.optimize(
        OptimizationOptions(
            remove_unused_objects=False, allow_reuse_page_content=False
        )
    )
    assert c1.object_number != c2.object_number
    assert c1.object_number in cos.objects
    assert c2.object_number in cos.objects


def test_page_content_merged_when_reuse_allowed():
    pdf, cos, c1, c2 = _two_page_content_pdf()
    pdf.optimize(
        OptimizationOptions(
            remove_unused_objects=False, allow_reuse_page_content=True
        )
    )
    assert c1.object_number == c2.object_number


# ---------------------------------------------------------------------------
# Narrow unused-stream prune
# ---------------------------------------------------------------------------


def test_remove_unused_streams_prunes_only_streams():
    pdf, cos = _new_cos_pdf()
    dead_stream = cos.register_object(PdfStream(b"unreferenced stream"))
    dead_dict = cos.register_object(PdfDictionary({PdfName("Foo"): PdfName("Bar")}))
    s_num, d_num = dead_stream.object_number, dead_dict.object_number

    pdf.optimize(
        OptimizationOptions(
            remove_unused_objects=False,
            remove_unused_streams=True,
            link_duplicate_streams=False,
            remove_duplicate_images=False,
        )
    )

    assert s_num not in cos.objects  # unreachable stream pruned
    assert d_num in cos.objects  # unreachable non-stream dict left alone


# ---------------------------------------------------------------------------
# Font-program compression
# ---------------------------------------------------------------------------


def _font_and_plain_pdf():
    pdf, cos = _new_cos_pdf()
    font_ref = cos.register_object(PdfStream(b"A" * 500))
    cos.register_object(PdfDictionary({PdfName("FontFile2"): font_ref}))
    plain_ref = cos.register_object(PdfStream(b"B" * 500))
    return pdf, cos, font_ref, plain_ref


def _no_prune_opts(**kwargs):
    base = dict(
        remove_unused_objects=False,
        remove_unused_streams=False,
        link_duplicate_streams=False,
        remove_duplicate_images=False,
    )
    base.update(kwargs)
    return OptimizationOptions(**base)


def test_compress_fonts_default_includes_font_program():
    pdf, cos, font_ref, plain_ref = _font_and_plain_pdf()
    pdf.optimize(_no_prune_opts(compress_fonts=True))
    assert PdfName("Filter") in cos.objects[font_ref.object_number]
    assert PdfName("Filter") in cos.objects[plain_ref.object_number]


def test_compress_fonts_disabled_skips_font_program():
    pdf, cos, font_ref, plain_ref = _font_and_plain_pdf()
    pdf.optimize(_no_prune_opts(compress_fonts=False))
    assert PdfName("Filter") not in cos.objects[font_ref.object_number]
    assert PdfName("Filter") in cos.objects[plain_ref.object_number]


# ---------------------------------------------------------------------------
# Compression / GC decoupling
# ---------------------------------------------------------------------------


def test_compression_runs_when_gc_disabled():
    pdf, cos = _new_cos_pdf()
    root = _root(pdf, cos)
    s = cos.register_object(PdfStream(b"C" * 400))
    root.mapping[PdfName("Blob")] = s

    pdf.optimize(_no_prune_opts())

    assert PdfName("Filter") in cos.objects[s.object_number]


def test_compress_streams_kwarg_suppresses_compression():
    pdf, cos = _new_cos_pdf()
    root = _root(pdf, cos)
    s = cos.register_object(PdfStream(b"C" * 400))
    root.mapping[PdfName("Blob")] = s

    pdf.optimize(_no_prune_opts(), compress_streams=False)

    assert PdfName("Filter") not in cos.objects[s.object_number]


# ---------------------------------------------------------------------------
# Public Document & low-code surfaces
# ---------------------------------------------------------------------------


def test_document_optimize_roundtrip():
    data = SimplePdf(pages=[(0, 0, 200, 200)]).to_bytes()
    doc = Document()
    doc.load_from(data)
    try:
        assert doc.optimize(OptimizationOptions()) is doc
        buffer = io.BytesIO()
        doc.save(buffer)
        out = buffer.getvalue()
        assert out.startswith(b"%PDF")
    finally:
        doc.dispose()

    reopened = Document()
    reopened.load_from(out)
    try:
        assert reopened.page_count == 1
    finally:
        reopened.dispose()


def test_lowcode_optimizer_noop_path_still_valid_pdf():
    data = SimplePdf(pages=[(0, 0, 200, 200)]).to_bytes()
    opts = OptimizeOptions(remove_unused_objects=False, compress_streams=False)
    opts.add_input(ByteArrayDataSource(data))

    out = Optimizer().process(opts)[0].to_array()

    assert out.startswith(b"%PDF")
    reopened = SimplePdf.from_bytes(out)
    try:
        assert len(reopened.pages) == 1
    finally:
        reopened.dispose()


# ---------------------------------------------------------------------------
# Font unembedding (Standard-14 only)
# ---------------------------------------------------------------------------


def _embed_font(cos, base_font, body):
    """Register a Font + FontDescriptor + FontFile2 program.

    Returns ``(font_ref, descriptor, fontfile_obj_number)``.
    """
    ff = cos.register_object(
        PdfStream(body, {PdfName("Length"): PdfNumber(len(body))})
    )
    descriptor = PdfDictionary(
        {
            PdfName("Type"): PdfName("FontDescriptor"),
            PdfName("FontName"): PdfName(base_font),
            PdfName("FontFile2"): ff,
        }
    )
    font = PdfDictionary(
        {
            PdfName("Type"): PdfName("Font"),
            PdfName("Subtype"): PdfName("TrueType"),
            PdfName("BaseFont"): PdfName(base_font),
            PdfName("FontDescriptor"): cos.register_object(descriptor),
        }
    )
    return cos.register_object(font), descriptor, ff.object_number


def test_unembed_strips_standard_14_fonts():
    pdf, cos = _new_cos_pdf()
    root = _root(pdf, cos)
    helv_font, helv_desc, helv_ff = _embed_font(cos, "Helvetica", b"HELV" * 50)
    times_font, times_desc, times_ff = _embed_font(
        cos, "ABCDEF+Times-Roman", b"TIMES" * 50
    )
    custom_font, custom_desc, custom_ff = _embed_font(cos, "CustomSans", b"CUST" * 50)
    root.mapping[PdfName("Refs")] = PdfArray([helv_font, times_font, custom_font])

    pdf.optimize(_no_prune_opts(unembed_fonts=True))

    # Standard-14 fonts (including a subset-prefixed name) lose the embedded
    # program, and the now-orphaned stream is deleted.
    assert PdfName("FontFile2") not in helv_desc
    assert PdfName("FontFile2") not in times_desc
    assert helv_ff not in cos.objects
    assert times_ff not in cos.objects
    # A custom (non-standard) font keeps its embedded program.
    assert PdfName("FontFile2") in custom_desc
    assert custom_ff in cos.objects


def test_unembed_disabled_keeps_font_programs():
    pdf, cos = _new_cos_pdf()
    root = _root(pdf, cos)
    helv_font, helv_desc, helv_ff = _embed_font(cos, "Helvetica", b"HELV" * 50)
    root.mapping[PdfName("Refs")] = PdfArray([helv_font])

    pdf.optimize(_no_prune_opts(unembed_fonts=False))

    assert PdfName("FontFile2") in helv_desc
    assert helv_ff in cos.objects


# ---------------------------------------------------------------------------
# Object-stream / cross-reference-stream file compression
# ---------------------------------------------------------------------------


def _doc_with_many_objects(n=60):
    pdf, cos = _new_cos_pdf()
    root = _root(pdf, cos)
    refs = []
    for i in range(n):
        refs.append(
            cos.register_object(
                PdfDictionary(
                    {
                        PdfName("Type"): PdfName("Annot"),
                        PdfName("Subtype"): PdfName("Text"),
                        PdfName("Contents"): PdfString(f"annotation body number {i}"),
                    }
                )
            )
        )
    root.mapping[PdfName("Refs")] = PdfArray(refs)
    return pdf, cos


def test_optimize_emits_object_and_xref_streams():
    pdf, _ = _doc_with_many_objects()
    pdf.optimize(OptimizationOptions())
    out = pdf.to_bytes()
    assert b"/ObjStm" in out
    assert b"/XRef" in out
    assert b"\nxref\n" not in out  # no classic cross-reference table
    assert out.rstrip().endswith(b"%%EOF")


def test_object_streams_roundtrip_preserves_objects():
    pdf, _ = _doc_with_many_objects()
    pdf.optimize(OptimizationOptions())
    out = pdf.to_bytes()

    reopened = SimplePdf.from_bytes(out)
    try:
        annots = [
            o
            for o in reopened._cos_doc.objects.values()
            if isinstance(o, PdfDictionary)
            and o.get(PdfName("Subtype")) == PdfName("Text")
        ]
        assert len(annots) == 60
        bodies = {a.get(PdfName("Contents")).value for a in annots}
        assert b"annotation body number 0" in bodies
        assert b"annotation body number 59" in bodies
    finally:
        reopened.dispose()


def test_object_streams_shrink_file():
    pdf, _ = _doc_with_many_objects()
    classic = pdf.to_bytes()  # never optimized -> classic xref
    # GC/dedup off so the same object graph is compared; isolate the ObjStm win.
    pdf.optimize(_no_prune_opts())
    compressed = pdf.to_bytes()
    assert b"/ObjStm" in compressed
    assert len(compressed) < len(classic)


def test_plain_save_keeps_classic_xref():
    pdf, _ = _doc_with_many_objects()
    out = pdf.to_bytes()  # no optimize() call
    assert b"\nxref\n" in out
    assert b"/ObjStm" not in out


def test_use_object_streams_flag_disables_packing():
    pdf, _ = _doc_with_many_objects()
    pdf.optimize(OptimizationOptions(use_object_streams=False))
    out = pdf.to_bytes()
    assert b"/ObjStm" not in out
    assert b"\nxref\n" in out


# ---------------------------------------------------------------------------
# Content-based image deduplication (COS graph)
# ---------------------------------------------------------------------------


def _image_xobject(cos, samples, *, flate=False, **extra):
    """Register an image XObject carrying *samples* (optionally Flate-encoded)."""
    content = zlib.compress(samples, 9) if flate else samples
    mapping = {
        PdfName("Type"): PdfName("XObject"),
        PdfName("Subtype"): PdfName("Image"),
        PdfName("Width"): PdfNumber(8),
        PdfName("Height"): PdfNumber(8),
        PdfName("BitsPerComponent"): PdfNumber(8),
        PdfName("ColorSpace"): PdfName("DeviceGray"),
        PdfName("Length"): PdfNumber(len(content)),
    }
    if flate:
        mapping[PdfName("Filter")] = PdfName("FlateDecode")
    for key, value in extra.items():
        mapping[PdfName(key)] = value
    return cos.register_object(PdfStream(content, mapping))


_SAMPLES_A = bytes((i * 7) % 256 for i in range(64))
_SAMPLES_B = bytes((i * 5 + 1) % 256 for i in range(64))


def test_dedup_images_collapses_same_pixels_different_compression():
    pdf, cos = _new_cos_pdf()
    root = _root(pdf, cos)
    raw = _image_xobject(cos, _SAMPLES_A, flate=False)
    comp = _image_xobject(cos, _SAMPLES_A, flate=True)
    root.mapping[PdfName("ImgA")] = raw
    root.mapping[PdfName("ImgB")] = comp
    n_raw, n_comp = raw.object_number, comp.object_number

    pdf.optimize(OptimizationOptions(remove_unused_objects=False))

    # Same decoded pixels -> one canonical object, the other repointed/removed.
    assert raw.object_number == comp.object_number
    survivor = raw.object_number
    assert survivor in cos.objects
    removed = n_comp if survivor == n_raw else n_raw
    assert removed not in cos.objects


def test_dedup_images_keeps_distinct_images():
    pdf, cos = _new_cos_pdf()
    root = _root(pdf, cos)
    a = _image_xobject(cos, _SAMPLES_A)
    b = _image_xobject(cos, _SAMPLES_B)
    root.mapping[PdfName("ImgA")] = a
    root.mapping[PdfName("ImgB")] = b

    pdf.optimize(OptimizationOptions(remove_unused_objects=False))

    assert a.object_number != b.object_number
    assert a.object_number in cos.objects
    assert b.object_number in cos.objects


def test_dedup_images_disabled_keeps_both():
    pdf, cos = _new_cos_pdf()
    root = _root(pdf, cos)
    a = _image_xobject(cos, _SAMPLES_A, flate=False)
    b = _image_xobject(cos, _SAMPLES_A, flate=True)
    root.mapping[PdfName("ImgA")] = a
    root.mapping[PdfName("ImgB")] = b

    pdf.optimize(
        OptimizationOptions(
            remove_unused_objects=False, remove_duplicate_images=False
        )
    )

    assert a.object_number != b.object_number
    assert a.object_number in cos.objects
    assert b.object_number in cos.objects


def test_dedup_images_soft_mask_difference_prevents_collapse():
    pdf, cos = _new_cos_pdf()
    root = _root(pdf, cos)
    mask = _image_xobject(cos, bytes(64))
    masked = _image_xobject(cos, _SAMPLES_A, SMask=mask)
    plain = _image_xobject(cos, _SAMPLES_A)
    root.mapping[PdfName("Masked")] = masked
    root.mapping[PdfName("Plain")] = plain

    pdf.optimize(OptimizationOptions(remove_unused_objects=False))

    # Identical base pixels but only one carries a soft mask -> not merged.
    assert masked.object_number != plain.object_number
    assert masked.object_number in cos.objects
    assert plain.object_number in cos.objects
