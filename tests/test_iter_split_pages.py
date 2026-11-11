"""Test the iter_split_pages generator method."""

from __future__ import annotations

import io

from aspose_pdf import Document, PageSize


def _bytes(document):
    with io.BytesIO() as output:
        document.save(output)
        return output.getvalue()


def _texts(document):
    return [page.extract_text().strip() for page in document.pages]


def test_iter_split_pages_yields_one_per_page():
    """Each page becomes its own document."""
    with Document() as doc:
        for i in range(4):
            page = doc.pages.add(PageSize.A4)
            page.add_text(f"Page {i+1}", 72, 700, font_size=24)

        pages = list(doc.iter_split_pages())
        assert len(pages) == 4  # 4 pages -> 4 single-page documents

        for idx, page_doc in enumerate(pages):
            assert page_doc.page_count == 1
            assert _texts(page_doc) == [f"Page {idx+1}"]
            page_doc.dispose()


def test_iter_split_pages_selected_pages():
    """Iterate only over selected pages."""
    with Document() as doc:
        for i in range(6):
            page = doc.pages.add(PageSize.A4)
            page.add_text(f"Page {i+1}", 72, 700, font_size=24)

        # Use extract_pages to get specific pages, then iterate
        selected = list(doc.iter_split_pages())
        # Only first 3 pages
        selected = selected[:3]
        assert len(selected) == 3

        for page_doc in selected:
            page_doc.dispose()


def test_iter_split_pages_empty_document():
    """Empty document yields no parts."""
    with Document() as doc:
        pages = list(doc.iter_split_pages())
        assert len(pages) == 0


def test_iter_split_pages_source_unchanged():
    """Source document is not modified."""
    with Document() as doc:
        for i in range(3):
            page = doc.pages.add(PageSize.A4)
            page.add_text(f"Page {i+1}", 72, 700, font_size=24)

        original_count = doc.page_count
        original_text = _texts(doc)
        list(doc.iter_split_pages())
        assert doc.page_count == original_count
        assert _texts(doc) == original_text


def test_iter_split_pages_independence():
    """Each yielded document is independent."""
    with Document() as doc:
        doc.pages.add(PageSize.A4).add_text("ORIGINAL", 72, 700, font_size=24)
        doc.pages.add(PageSize.A4).add_text("ORIGINAL", 72, 700, font_size=24)

        pages = list(doc.iter_split_pages())
        assert len(pages) == 2

        # Modify one yielded document
        pages[0].pages[1].add_text(" MODIFIED", 72, 650, font_size=12)

        # Reload and verify independence
        reloaded = Document(io.BytesIO(_bytes(pages[0])))
        assert b" MODIFIED" in reloaded.pages[1].content
        reloaded.dispose()

        # The second document should not be affected
        reloaded2 = Document(io.BytesIO(_bytes(pages[1])))
        assert b" MODIFIED" not in reloaded2.pages[1].content
        reloaded2.dispose()

        for page_doc in pages:
            page_doc.dispose()


def test_iter_split_pages_metadata():
    """Each yielded document retains metadata."""
    with Document() as doc:
        doc.pages.add(PageSize.A4).add_text("Test", 72, 700, font_size=24)
        doc.info["Title"] = "My Document"
        doc.info["Author"] = "Test Author"

        pages = list(doc.iter_split_pages())
        assert len(pages) == 1

        page_doc = pages[0]
        assert page_doc.info["Title"] == "My Document"
        assert page_doc.info["Author"] == "Test Author"
        page_doc.dispose()


def test_iter_split_pages_annotations():
    """Each yielded document retains annotations."""
    with Document() as doc:
        page = doc.pages.add(PageSize.A4)
        page.add_text("Test", 72, 700, font_size=24)
        page.annotations.add("Square", (72, 300, 200, 400), "A note")

        pages = list(doc.iter_split_pages())
        assert len(pages) == 1

        page_doc = pages[0]
        assert len(page_doc.pages[1].annotations) == 1
        assert page_doc.pages[1].annotations[0].contents == "A note"
        page_doc.dispose()


def test_iter_split_pages_form_fields():
    """Form fields are preserved in split pages."""
    with Document() as doc:
        doc.pages.add(PageSize.A4)
        doc.form.add_text_field("nickname", 1, (72, 100, 272, 130), value="typed")

        pages = list(doc.iter_split_pages())
        assert len(pages) == 1

        page_doc = pages[0]
        # The form field should exist in the page document
        assert [field.name for field in page_doc.form] == ["nickname"]
        # The widget value should be preserved
        assert page_doc.form["nickname"].value == "typed"
        page_doc.dispose()


def test_iter_split_pages_with_rotation():
    """Each yielded document retains page rotation."""
    with Document() as doc:
        page = doc.pages.add(PageSize.A4)
        page.add_text("Test", 72, 700, font_size=24)
        page.rotation = 90

        pages = list(doc.iter_split_pages())
        page_doc = pages[0]
        assert page_doc.pages[1].rotation == 90
        page_doc.dispose()


def test_iter_split_pages_with_boxes():
    """Each yielded document retains page boxes."""
    with Document() as doc:
        page = doc.pages.add(PageSize.A4)
        page.add_text("Test", 72, 700, font_size=24)
        page.crop_box = (10, 10, 500, 700)
        page.trim_box = (20, 20, 480, 680)

        pages = list(doc.iter_split_pages())
        page_doc = pages[0]
        assert page_doc.pages[1].crop_box == (10, 10, 500, 700)
        assert page_doc.pages[1].trim_box == (20, 20, 480, 680)
        page_doc.dispose()


def test_iter_split_pages_early_termination():
    """Early termination (breaking) works correctly."""
    with Document() as doc:
        for i in range(10):
            page = doc.pages.add(PageSize.A4)
            page.add_text(f"Page {i+1}", 72, 700, font_size=24)

        count = 0
        for page_doc in doc.iter_split_pages():
            count += 1
            if count >= 3:
                break

        assert count == 3


def test_iter_split_pages_contextlib_closing():
    """Early termination with contextlib.closing works."""
    from contextlib import closing

    with Document() as doc:
        for i in range(10):
            page = doc.pages.add(PageSize.A4)
            page.add_text(f"Page {i+1}", 72, 700, font_size=24)

        count = 0
        with closing(doc.iter_split_pages()) as pages:
            for page_doc in pages:
                count += 1
                if count >= 5:
                    break

        assert count == 5


def test_iter_split_pages_large_document():
    """Test with a larger document to ensure memory efficiency."""
    with Document() as doc:
        # Add 20 pages
        for i in range(20):
            page = doc.pages.add(PageSize.A4)
            page.add_text(f"Page {i+1}", 72, 700, font_size=24)

        pages = list(doc.iter_split_pages())
        assert len(pages) == 20

        # Verify each page is correct
        for idx, page_doc in enumerate(pages):
            assert page_doc.page_count == 1
            assert _texts(page_doc) == [f"Page {idx+1}"]
            page_doc.dispose()


def test_iter_split_pages_dispose_after_iteration():
    """Test that disposing after iteration works."""
    with Document() as doc:
        doc.pages.add(PageSize.A4).add_text("Test", 72, 700, font_size=24)

        pages = list(doc.iter_split_pages())
        assert len(pages) == 1

        # Dispose after iteration
        pages[0].dispose()
