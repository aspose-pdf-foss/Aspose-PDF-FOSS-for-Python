"""Merging two documents that named their resources alike.

Both halves of a merge call their first font ``/F1`` and their first image
``/Im1``; every document this library builds does. The old merge answered that
by pooling the two documents' resources into one namespace, renaming the
newcomers, and *rewriting the content streams* to use the new names -- which is
what left a merged page with no ``/Resources`` of its own. A page now keeps the
resource names it always had, and they resolve through its own dictionary, so
nothing has to be renamed and no content stream is touched.
"""

from __future__ import annotations

import io

from aspose_pdf import Document
from aspose_pdf.engine.cos import PdfName
from aspose_pdf.engine.simple_pdf import SimplePdf


def _font_of(engine: SimplePdf, index: int) -> str:
    engine._ensure_page_cache()
    page = engine._cos_doc.objects.get(engine._page_refs[index])
    fonts = engine._resolve(
        engine._get_inherited_attr(page, "Resources").mapping.get(PdfName("Font"))
    )
    font = engine._resolve(fonts.mapping.get(PdfName("F1")))
    return engine._get_name(font.mapping.get(PdfName("BaseFont")))


def test_two_documents_that_both_call_their_image_img_keep_both():
    first = SimplePdf()
    first.pages = [(0, 0, 612, 792)]
    first.images = {"img": b"data1"}
    first.page_contents = [b"/img Do"]

    second = SimplePdf()
    second.pages = [(0, 0, 612, 792)]
    second.images = {"img": b"data2"}
    second.page_contents = [b"/img Do"]

    merged = SimplePdf.merge(first, second)

    assert sorted(merged.images.values()) == [b"data1", b"data2"]
    # Neither page's content was rewritten: each resolves /img for itself.
    assert merged.page_contents == [b"/img Do", b"/img Do"]


def test_an_image_that_appears_in_both_is_kept_once():
    first = SimplePdf()
    first.pages = [(0, 0, 612, 792)]
    first.images = {"img": b"same"}
    first.page_contents = [b"/img Do"]

    second = SimplePdf()
    second.pages = [(0, 0, 612, 792)]
    second.images = {"img": b"same"}
    second.page_contents = [b"/img Do"]

    merged = SimplePdf.merge(first, second)

    assert list(merged.images.values()) == [b"same"]


def test_two_documents_that_both_call_their_font_f1_keep_their_own():
    documents = []
    try:
        for text, face in (("Part one", "Helvetica"), ("Part two", "Times-Bold")):
            document = Document()
            document.pages.add().add_text(
                text, x=72, y=720, font_size=18, font_name=face
            )
            documents.append(document)

        merged = SimplePdf.merge(*(document._engine_pdf for document in documents))

        assert _font_of(merged, 0) == "Helvetica"
        assert _font_of(merged, 1) == "Times-Bold"
        # The name each page uses is the one its content already names.
        assert b"/F1 18 Tf" in merged.page_contents[0]
        assert b"/F1 18 Tf" in merged.page_contents[1]
    finally:
        for document in documents:
            document.dispose()


def test_the_merged_pages_still_read_back_as_themselves():
    documents = []
    try:
        for text in ("Part one", "Part two"):
            document = Document()
            document.pages.add().add_text(text, x=72, y=720, font_size=18)
            documents.append(document)

        merged = SimplePdf.merge(*(document._engine_pdf for document in documents))
        reloaded = Document(io.BytesIO(merged.to_bytes()))

        assert [
            reloaded.pages[index].to_markdown().strip()
            for index in range(len(reloaded.pages))
        ] == ["Part one", "Part two"]
    finally:
        for document in documents:
            document.dispose()
