"""Bringing one page across from another document.

``pages.insert(i, other.pages[j])`` used to be refused outright -- the way to
move a page between documents was `extract_pages` into a scratch document,
`merge` it, then `move_page` it into position -- while ``pages.add(other_page)``
was *accepted* and quietly produced a broken page: the page dictionary was
copied without the graph it names, so its content kept saying ``/F1`` with no
``/Resources`` of its own, and the name then resolved to the **target's**
``/F1``. Text written in Courier-Bold came out in whatever the target had.

Both now take the import ``merge`` uses, for one page and at a position, so
the page arrives with everything that belongs to it: resources, annotations,
the fields its widgets belong to, its optional content, its structure, its
label, and the bookmarks that pointed at it.

What this does *not* change is `merge`'s page-label rule, which these tests
pin so it is not mistaken for a bug: a document with no labels of its own
contributes empty ones.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import (
    Document,
    NumberingStyle,
    OutlineItem,
    PageLabel,
    PageSize,
)
from aspose_pdf.engine.cos import PdfDictionary, PdfName
from aspose_pdf.exceptions import PdfValidationException

# A 1x1 transparent PNG, as the other page tests use.
_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _reopen(document: Document) -> Document:
    buffer = io.BytesIO()
    document.save(buffer)
    document.close()
    return Document(buffer.getvalue())


def _pages(label: str, count: int = 2, *, font_name: str = "Helvetica") -> Document:
    document = Document()
    for index in range(count):
        page = document.pages.add(PageSize.A4)
        page.add_text(f"{label} {index + 1}", 60, 700, font_size=24, font_name=font_name)
    return _reopen(document)


def _titles(document: Document) -> list[str]:
    return [page.extract_text().strip() for page in document.pages]


def _resources(document: Document, index: int) -> PdfDictionary | None:
    engine = document._engine_pdf
    return engine._resolve(engine._get_page_dict(index).get(PdfName("Resources")))


def _structure_types(document: Document) -> list[str]:
    """Every structure element's type, depth-first, for comparing trees."""

    def walk(elements) -> list[str]:
        out: list[str] = []
        for element in elements:
            out.append(element.structure_type)
            out.extend(walk(element.children))
        return out

    return walk(document.tagged_content.root_elements)


def _base_fonts(document: Document, index: int) -> list[str]:
    engine = document._engine_pdf
    resources = _resources(document, index)
    fonts = engine._resolve(resources.mapping.get(PdfName("Font")))
    return sorted(
        str(engine._resolve(value).mapping.get(PdfName("BaseFont")))
        for value in fonts.mapping.values()
    )


# ---------------------------------------------------------------------------
# The page arrives, where it was asked for
# ---------------------------------------------------------------------------


def test_a_page_from_another_document_can_be_inserted():
    target, source = _pages("TARGET"), _pages("SOURCE", 1)
    target.pages.insert(1, source.pages[0])
    with _reopen(target) as saved:
        assert _titles(saved) == ["TARGET 1", "SOURCE 1", "TARGET 2"]
    source.close()


def test_a_page_from_another_document_can_be_appended():
    target, source = _pages("TARGET"), _pages("SOURCE", 1)
    target.pages.add(source.pages[0])
    with _reopen(target) as saved:
        assert _titles(saved) == ["TARGET 1", "TARGET 2", "SOURCE 1"]
    source.close()


@pytest.mark.parametrize(
    ("index", "expected"),
    [
        (0, ["SOURCE 1", "TARGET 1", "TARGET 2"]),
        (1, ["TARGET 1", "SOURCE 1", "TARGET 2"]),
        (2, ["TARGET 1", "TARGET 2", "SOURCE 1"]),
        (99, ["TARGET 1", "TARGET 2", "SOURCE 1"]),
        (-5, ["SOURCE 1", "TARGET 1", "TARGET 2"]),
    ],
)
def test_the_page_lands_at_the_index_asked_for(index, expected):
    target, source = _pages("TARGET"), _pages("SOURCE", 1)
    target.pages.insert(index, source.pages[0])
    with _reopen(target) as saved:
        assert _titles(saved) == expected
    source.close()


def test_the_returned_page_is_the_one_that_was_inserted():
    target, source = _pages("TARGET"), _pages("SOURCE", 1)
    inserted = target.pages.insert(1, source.pages[0])
    assert inserted.index == 1
    assert "SOURCE 1" in inserted.extract_text()
    target.close()
    source.close()


def test_a_page_can_be_imported_into_an_empty_document():
    target = Document()
    source = _pages("SOURCE", 1)
    target.pages.insert(0, source.pages[0])
    with _reopen(target) as saved:
        assert _titles(saved) == ["SOURCE 1"]
    source.close()


def test_importing_leaves_the_source_document_alone():
    target, source = _pages("TARGET"), _pages("SOURCE", 2)
    target.pages.insert(0, source.pages[1])
    assert _titles(source) == ["SOURCE 1", "SOURCE 2"]
    target.close()
    source.close()


def test_the_same_page_can_be_imported_more_than_once():
    target, source = _pages("TARGET", 1), _pages("SOURCE", 1)
    target.pages.insert(0, source.pages[0])
    target.pages.insert(0, source.pages[0])
    with _reopen(target) as saved:
        assert _titles(saved) == ["SOURCE 1", "SOURCE 1", "TARGET 1"]
        # Two pages, each with its own resources -- not one shared graph that
        # a later edit to either would change for both.
        assert _resources(saved, 0) is not None
        assert _resources(saved, 1) is not None
    source.close()


def test_pages_can_be_imported_from_several_documents():
    target = _pages("TARGET", 1)
    first, second = _pages("FIRST", 1), _pages("SECOND", 1)
    target.pages.insert(0, first.pages[0])
    target.pages.add(second.pages[0])
    with _reopen(target) as saved:
        assert _titles(saved) == ["FIRST 1", "TARGET 1", "SECOND 1"]
    first.close()
    second.close()


# ---------------------------------------------------------------------------
# It arrives with what it names
# ---------------------------------------------------------------------------


def test_the_page_brings_its_own_resources_rather_than_borrowing_the_targets():
    """The defect this closes: the text drew in the target's font, silently."""
    target = _pages("TARGET", 1, font_name="Helvetica")
    source = _pages("SOURCE", 1, font_name="Courier-Bold")
    # Both documents name their font /F1 in their own file.
    assert _base_fonts(target, 0) == ["PdfName(/Helvetica)"]
    assert _base_fonts(source, 0) == ["PdfName(/Courier-Bold)"]

    target.pages.add(source.pages[0])
    with _reopen(target) as saved:
        assert _base_fonts(saved, 0) == ["PdfName(/Helvetica)"]
        assert _base_fonts(saved, 1) == ["PdfName(/Courier-Bold)"]
    source.close()


def test_an_image_the_page_draws_comes_with_it():
    target = _pages("TARGET", 1)
    source = Document()
    page = source.pages.add(PageSize.A4)
    page.add_image(_PNG, 100, 500, width=120, height=120)
    source = _reopen(source)

    target.pages.insert(0, source.pages[0])
    with _reopen(target) as saved:
        engine = saved._engine_pdf
        xobjects = engine._resolve(
            _resources(saved, 0).mapping.get(PdfName("XObject"))
        )
        assert xobjects is not None and len(xobjects.mapping) == 1
    source.close()


def test_the_pages_annotations_come_with_it():
    target = _pages("TARGET", 1)
    source = Document()
    page = source.pages.add(PageSize.A4)
    page.annotations.add("Square", (60, 300, 200, 400), "a note")
    page.add_link([60, 690, 300, 720], "https://example.invalid")
    source = _reopen(source)

    target.pages.insert(0, source.pages[0])
    with _reopen(target) as saved:
        assert sorted(a.subtype for a in saved.pages[0].annotations) == [
            "Link",
            "Square",
        ]
        assert [a.subtype for a in saved.pages[1].annotations] == []
    source.close()


def test_a_form_field_on_the_page_comes_with_it():
    target = _pages("TARGET", 1)
    source = Document()
    page = source.pages.add(PageSize.A4)
    source.form.add_text_field("Comment", page, [60, 400, 300, 430], value="hello")
    source = _reopen(source)

    target.pages.insert(1, source.pages[0])
    with _reopen(target) as saved:
        assert [(f.name, f.value) for f in saved.form] == [("Comment", "hello")]
        assert "Widget" in [a.subtype for a in saved.pages[1].annotations]
    source.close()


def test_a_bookmark_pointing_at_the_page_comes_with_it_and_follows_it():
    target = _pages("TARGET", 2)
    source = _pages("SOURCE", 2)
    source.outlines.add(OutlineItem("Second source page", 1))
    source = _reopen(source)

    target.pages.insert(1, source.pages[1])
    with _reopen(target) as saved:
        assert [(o.title, o.page_index) for o in saved.outlines] == [
            ("Second source page", 1)
        ]
        assert "SOURCE 2" in saved.pages[1].extract_text()
    source.close()


def test_a_bookmark_pointing_at_a_page_left_behind_does_not_come():
    target = _pages("TARGET", 1)
    source = _pages("SOURCE", 2)
    source.outlines.add(OutlineItem("The page not taken", 0))
    source = _reopen(source)

    target.pages.insert(0, source.pages[1])
    with _reopen(target) as saved:
        assert list(saved.outlines) == []
    source.close()


def test_the_pages_tagging_comes_with_it():
    target = _pages("TARGET", 1)
    source = Document()
    page = source.pages.add(PageSize.A4)
    page.add_text("Tagged source", 60, 700, font_size=20)
    source.auto_tag()
    source = _reopen(source)
    tagged_before = _structure_types(source)
    assert tagged_before, "the source was not tagged, so this proves nothing"
    assert not _structure_types(target), "the target was tagged already"

    target.pages.insert(0, source.pages[0])
    with _reopen(target) as saved:
        engine = saved._engine_pdf
        root = engine._resolve(engine._cos_doc.trailer.mapping.get(PdfName("Root")))
        assert isinstance(
            engine._resolve(root.mapping.get(PdfName("StructTreeRoot"))),
            PdfDictionary,
        )
        assert _structure_types(saved) == tagged_before
    source.close()


def test_the_imported_page_keeps_its_label():
    target = _pages("TARGET", 2)
    source = _pages("SOURCE", 2)
    source.page_labels[0] = PageLabel(style=NumberingStyle.NUMERALS_ROMAN_LOWERCASE)
    source = _reopen(source)
    assert [p.label for p in source.pages] == ["i", "ii"]

    target.pages.insert(1, source.pages[1])
    with _reopen(target) as saved:
        # The same rule merge has followed all along: a document with no
        # labels of its own contributes empty ones rather than inventing
        # numbers for pages that never had any.
        assert [p.label for p in saved.pages] == ["", "ii", ""]
    source.close()


# ---------------------------------------------------------------------------
# What is still refused
# ---------------------------------------------------------------------------


def test_a_page_of_a_closed_document_cannot_be_imported():
    target, source = _pages("TARGET", 1), _pages("SOURCE", 1)
    page = source.pages[0]
    source.close()
    with pytest.raises(Exception, match=r"(?i)dispos"):
        target.pages.insert(0, page)
    target.close()


def test_a_page_that_is_no_longer_there_cannot_be_imported():
    target, source = _pages("TARGET", 1), _pages("SOURCE", 2)
    page = source.pages[1]
    source.pages.delete(1)
    with pytest.raises(IndexError, match="may have been deleted"):
        target.pages.insert(0, page)
    target.close()
    source.close()


def test_a_size_cannot_be_combined_with_a_page_to_copy():
    target, source = _pages("TARGET", 1), _pages("SOURCE", 1)
    with pytest.raises(PdfValidationException, match="cannot be combined"):
        target.pages.insert(0, source.pages[0], size=PageSize.A4)
    target.close()
    source.close()


def test_copying_a_page_within_one_document_still_works():
    document = _pages("PAGE", 2)
    document.pages.insert(0, document.pages[1])
    with _reopen(document) as saved:
        assert _titles(saved) == ["PAGE 2", "PAGE 1", "PAGE 2"]


def test_merge_still_takes_whole_documents():
    """The import both now share -- merge is the same call with no subset."""
    target, source = _pages("TARGET", 2), _pages("SOURCE", 2)
    target.merge(source)
    with _reopen(target) as saved:
        assert _titles(saved) == ["TARGET 1", "TARGET 2", "SOURCE 1", "SOURCE 2"]
    source.close()
