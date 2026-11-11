"""Test the iter_split_spreads generator method."""

from __future__ import annotations

import io

from aspose_pdf import Document, PageSize


def _bytes(document):
    with io.BytesIO() as output:
        document.save(output)
        return output.getvalue()


def test_iter_split_spreads_yields_one_half_per_page():
    """Each page becomes two halves, so 4 pages yield 8 halves."""
    with Document() as doc:
        for i in range(4):
            page = doc.pages.add(PageSize.A4)
            page.add_text(f"Page {i+1}", 72, 700, font_size=24)

        halves = list(doc.iter_split_spreads())
        assert len(halves) == 8  # 4 pages * 2 halves each

        for half in halves:
            assert half.page_count == 1
            half.dispose()


def test_iter_split_spreads_selected_pages():
    """Selected pages yield two halves each."""
    with Document() as doc:
        for i in range(6):
            page = doc.pages.add(PageSize.A4)
            page.add_text(f"Page {i+1}", 72, 700, font_size=24)

        halves = list(doc.iter_split_spreads(pages=[1, 3]))
        assert len(halves) == 4  # 2 pages * 2 halves each

        for half in halves:
            half.dispose()


def test_iter_split_spreads_empty_document():
    """Empty document yields no parts."""
    with Document() as doc:
        halves = list(doc.iter_split_spreads())
        assert len(halves) == 0


def test_iter_split_spreads_empty_selection():
    """Empty selection yields no parts."""
    with Document() as doc:
        doc.pages.add(PageSize.A4)
        halves = list(doc.iter_split_spreads(pages=[]))
        assert len(halves) == 0


def test_iter_split_spreads_source_unchanged():
    """Source document is not modified."""
    with Document() as doc:
        for i in range(3):
            page = doc.pages.add(PageSize.A4)
            page.add_text(f"Page {i+1}", 72, 700, font_size=24)

        original_count = doc.page_count
        list(doc.iter_split_spreads())
        assert doc.page_count == original_count


def test_iter_split_spreads_with_layout():
    """Different layouts work correctly."""
    with Document() as doc:
        page = doc.pages.add(((-20, 0, 80, 50), b""))
        page.draw_rectangle(0, 0, 40, 20, fill_color="#f00", stroke_color=None)

        # Horizontal
        halves_h = list(doc.iter_split_spreads(layout="horizontal"))
        assert len(halves_h) == 2
        for half in halves_h:
            half.dispose()

        # Vertical
        halves_v = list(doc.iter_split_spreads(layout="vertical"))
        assert len(halves_v) == 2
        for half in halves_v:
            half.dispose()


def test_iter_split_spreads_with_reverse():
    """Reverse parameter works correctly."""
    with Document() as doc:
        page = doc.pages.add(((-20, 0, 80, 50), b""))
        page.draw_rectangle(0, 0, 40, 20, fill_color="#f00", stroke_color=None)

        halves_normal = list(doc.iter_split_spreads(reverse=False))
        halves_reversed = list(doc.iter_split_spreads(reverse=True))

        assert len(halves_normal) == 2
        assert len(halves_reversed) == 2

        for half in halves_normal + halves_reversed:
            half.dispose()


def test_iter_split_spreads_with_gutter():
    """Gutter parameter works correctly."""
    with Document() as doc:
        page = doc.pages.add(((-20, 0, 80, 50), b""))
        page.draw_rectangle(0, 0, 40, 20, fill_color="#f00", stroke_color=None)

        halves = list(doc.iter_split_spreads(gutter=4.0))
        assert len(halves) == 2
        for half in halves:
            half.dispose()


def test_iter_split_spreads_with_split_ratio():
    """Split ratio parameter works correctly."""
    with Document() as doc:
        page = doc.pages.add(((-20, 0, 80, 50), b""))
        page.draw_rectangle(0, 0, 40, 20, fill_color="#f00", stroke_color=None)

        halves = list(doc.iter_split_spreads(split_ratio=0.3))
        assert len(halves) == 2
        for half in halves:
            half.dispose()


def test_iter_split_spreads_yields_independent_documents():
    """Each yielded document is independent."""
    with Document() as doc:
        for i in range(2):
            page = doc.pages.add(PageSize.A4)
            page.add_text(f"Page {i+1}", 72, 700, font_size=24)

        halves = list(doc.iter_split_spreads())
        assert len(halves) == 4

        # Modify first half
        halves[0].pages[1].add_text("MODIFIED", 72, 600, font_size=12)

        # Check other halves are unchanged
        for i, half in enumerate(halves):
            text = half.pages[1].extract_text()
            if i == 0:
                assert "MODIFIED" in text
            else:
                assert "MODIFIED" not in text
            half.dispose()


def test_iter_split_spreads_streaming_behavior():
    """Test that iteration works without loading all into memory at once."""
    with Document() as doc:
        for i in range(4):
            page = doc.pages.add(PageSize.A4)
            page.add_text(f"Page {i+1}", 72, 700, font_size=24)

        # Use generator directly without converting to list
        halves_gen = doc.iter_split_spreads()
        count = 0
        for half in halves_gen:
            count += 1
            half.dispose()

        assert count == 8


def test_iter_split_spreads_dispose_yields():
    """Each yielded document can be properly disposed."""
    with Document() as doc:
        for i in range(2):
            page = doc.pages.add(PageSize.A4)
            page.add_text(f"Page {i+1}", 72, 700, font_size=24)

        halves = list(doc.iter_split_spreads())
        for half in halves:
            half.dispose()
            # Should not raise on double dispose
            half.dispose()


def test_iter_split_spreads_context_manager():
    """Yielded documents can be used as context managers."""
    with Document() as doc:
        for i in range(2):
            page = doc.pages.add(PageSize.A4)
            page.add_text(f"Page {i+1}", 72, 700, font_size=24)

        halves = list(doc.iter_split_spreads())
        for half in halves:
            with half:
                assert half.page_count == 1
