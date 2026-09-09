"""``PdfFileEditor.insert`` puts pages in, not just their drawings.

The facade's insert was the last of the model-only page copies: it handed
``insert_pages`` a list of page *rectangles* and a list of content *bytes*, so
what arrived was a page's drawing and nothing it drew with. No ``/Resources``,
so every font and image the content named resolved to no object; annotations,
links and form fields were dropped outright.

`Document.merge` and `PdfFileEditor.concatenate`/`extract` had already been
moved onto the real page import; this was the one left behind. It goes through
the same `SimplePdf.append` now, which grew an *at* so that inserting and
appending stay one routine rather than two.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import PdfDictionary, PdfName
from aspose_pdf.facades import PdfFileEditor
from aspose_pdf.interactive import FitDestination


def _rich(path: Path, label: str) -> None:
    """A one-page document that draws with a font and carries interaction."""
    document = Document()
    document.pages.add()
    document.pages[0].add_text(f"{label} text", x=40, y=700, font_size=18)
    document.form.add_text_field(f"{label}_field", 0, (40, 500, 300, 530), value=label)
    document.pages[0].add_link((40, 400, 200, 430), FitDestination(page=0))
    document.save(str(path))


def _facts(path: Path) -> dict:
    document = Document(str(path))
    engine = document._engine_pdf
    engine._ensure_page_cache()
    pages = []
    for index in range(len(document.pages)):
        page = engine._get_page_dict(index)
        resources = engine._resolve(engine._get_inherited_attr(page, "Resources"))
        annots = engine._resolve(page.mapping.get(PdfName("Annots")))
        pages.append(
            {
                "text": " ".join(document.pages[index].extract_text().split()),
                "resources": sorted(name.name for name in resources.mapping)
                if isinstance(resources, PdfDictionary)
                else [],
                "annots": len(annots.items) if annots is not None else 0,
            }
        )
    root = engine._resolve(engine._cos_doc.trailer.mapping[PdfName("Root")])
    acro = engine._resolve(root.mapping.get(PdfName("AcroForm")))
    fields = []
    if isinstance(acro, PdfDictionary):
        for ref in engine._resolve(acro.mapping[PdfName("Fields")]).items:
            title = engine._resolve(engine._resolve(ref).mapping.get(PdfName("T")))
            if title is not None:
                fields.append(title.value.decode("latin-1").strip("\x00"))
    return {"pages": pages, "fields": sorted(fields)}


@pytest.fixture
def documents(tmp_path: Path) -> tuple[Path, Path]:
    base, other = tmp_path / "base.pdf", tmp_path / "other.pdf"
    _rich(base, "base")
    _rich(other, "other")
    return base, other


def test_the_inserted_page_brings_the_font_it_draws_with(documents, tmp_path):
    base, other = documents
    output = tmp_path / "out.pdf"
    assert PdfFileEditor().insert(str(base), str(other), str(output), 1) is True
    facts = _facts(output)
    assert facts["pages"][0]["text"] == "other text"
    assert "/Font" in facts["pages"][0]["resources"]


def test_the_inserted_page_brings_its_annotations(documents, tmp_path):
    base, other = documents
    output = tmp_path / "out.pdf"
    PdfFileEditor().insert(str(base), str(other), str(output), 1)
    # The link and the field's widget, exactly as on the page it came from.
    assert _facts(output)["pages"][0]["annots"] == _facts(other)["pages"][0]["annots"]


def test_the_inserted_page_brings_its_form_field(documents, tmp_path):
    base, other = documents
    output = tmp_path / "out.pdf"
    PdfFileEditor().insert(str(base), str(other), str(output), 1)
    assert _facts(output)["fields"] == ["base_field", "other_field"]


def test_insert_and_merge_agree(documents, tmp_path):
    base, other = documents
    inserted, merged = tmp_path / "ins.pdf", tmp_path / "mrg.pdf"
    # Insert at the end is what merge does, so the two must land the same file.
    PdfFileEditor().insert(str(base), str(other), str(inserted), 2)
    document = Document(str(base))
    document.merge(Document(str(other)))
    document.save(str(merged))
    assert _facts(inserted) == _facts(merged)


@pytest.mark.parametrize(
    ("position", "expected"),
    [
        (1, ["other text", "base text"]),
        (2, ["base text", "other text"]),
        (99, ["base text", "other text"]),  # past the end lands at the end
        (0, ["other text", "base text"]),  # and before the start, at the start
    ],
)
def test_the_position_is_where_the_page_lands(documents, tmp_path, position, expected):
    base, other = documents
    output = tmp_path / f"out{position}.pdf"
    PdfFileEditor().insert(str(base), str(other), str(output), position)
    assert [page["text"] for page in _facts(output)["pages"]] == expected


def test_several_pages_keep_their_order(tmp_path):
    base, other = tmp_path / "base.pdf", tmp_path / "other.pdf"
    _rich(base, "base")
    document = Document()
    for index, label in enumerate(("one", "two", "three")):
        document.pages.add()
        document.pages[index].add_text(f"{label} text", x=40, y=700)
    document.save(str(other))

    output = tmp_path / "out.pdf"
    PdfFileEditor().insert(str(base), str(other), str(output), 1)
    assert [page["text"] for page in _facts(output)["pages"]] == [
        "one text",
        "two text",
        "three text",
        "base text",
    ]


def test_a_bookmark_follows_its_page_to_where_it_landed(tmp_path):
    from aspose_pdf.outlines import OutlineItem

    base, other = tmp_path / "base.pdf", tmp_path / "other.pdf"
    _rich(base, "base")
    document = Document()
    for index, label in enumerate(("one", "two")):
        document.pages.add()
        document.pages[index].add_text(f"{label} text", x=40, y=700)
    document.outlines.add(OutlineItem("second", page_index=1))
    document.save(str(other))

    output = tmp_path / "out.pdf"
    # The two pages land at 0 and 1, so the bookmark's page is 1 -- not the
    # last page of the result, which is where "append" would have put it.
    PdfFileEditor().insert(str(base), str(other), str(output), 1)
    result = Document(str(output))
    assert [page.extract_text().split()[0] for page in result.pages] == [
        "one",
        "two",
        "base",
    ]
    (bookmark,) = [item for item in result.outlines if item.title == "second"]
    assert bookmark.page_index == 1


# --- the engine's own entry point -------------------------------------------


def test_append_at_a_position_is_the_same_import_as_appending(tmp_path):
    from aspose_pdf.engine.simple_pdf import SimplePdf

    base, other = tmp_path / "base.pdf", tmp_path / "other.pdf"
    _rich(base, "base")
    _rich(other, "other")

    at_end = SimplePdf.from_file(str(base))
    at_end.append(SimplePdf.from_file(str(other)))
    at_start = SimplePdf.from_file(str(base))
    at_start.append(SimplePdf.from_file(str(other)), at=0)

    ending, starting = tmp_path / "e.pdf", tmp_path / "s.pdf"
    at_end.save(str(ending))
    at_start.save(str(starting))
    ordered = [page["text"] for page in _facts(ending)["pages"]]
    assert ordered == ["base text", "other text"]
    assert [page["text"] for page in _facts(starting)["pages"]] == ordered[::-1]
    # Same pages, whichever end they went on.
    assert _facts(ending)["fields"] == _facts(starting)["fields"]


@pytest.mark.parametrize("at", [-5, 99])
def test_a_position_outside_the_document_is_brought_inside(tmp_path, at):
    # The facade clamps before it calls, but the import is reachable on its own
    # and `insert` refuses an index out of range -- so it clamps too.
    from aspose_pdf.engine.simple_pdf import SimplePdf

    base, other = tmp_path / "base.pdf", tmp_path / "other.pdf"
    _rich(base, "base")
    _rich(other, "other")
    document = SimplePdf.from_file(str(base))
    document.append(SimplePdf.from_file(str(other)), at=at)
    output = tmp_path / f"out{at}.pdf"
    document.save(str(output))
    landed = [page["text"] for page in _facts(output)["pages"]]
    assert landed == (
        ["other text", "base text"] if at < 0 else ["base text", "other text"]
    )


def test_the_model_only_insert_is_gone():
    # `insert_pages` took rectangles and content bytes; nothing may reach for
    # it again by accident.
    from aspose_pdf.engine.simple_pdf import SimplePdf

    assert not hasattr(SimplePdf, "insert_pages")
