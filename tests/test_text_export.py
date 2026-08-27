"""Exporting a page's structure as HTML or Markdown.

``DocFormat.HTML`` and ``DocFormat.MARKDOWN`` used to be placeholders that
raised. They now produce real documents -- and the interesting part is what
"real" means here: this is a conversion to a *flowing document*, not a
facsimile. The question is not where a run sits but what it *is*, and the
answer comes from the same layout analysis :meth:`Document.auto_tag` uses, so
the export and the tag tree agree by construction rather than by coincidence.

Every fixture below is authored through the public API, so what the tests
assert is what a caller building a page would actually get back.
"""

from __future__ import annotations

import xml.etree.ElementTree as ElementTree
from pathlib import Path

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.text_export import page_blocks
from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.html import HtmlSaveOptions
from aspose_pdf.markdown import MarkdownSaveOptions
from aspose_pdf.save_options import DocFormat


def _report() -> Document:
    """A page with a heading, prose, a bulleted list and a table."""
    document = Document()
    page = document.pages.add()
    page.add_text("Regional results", 60, 740, font_size=20)
    page.add_text("Highlights", 60, 706, font_size=14)
    page.add_text("- Services revenue up 18 per cent", 70, 682, font_size=10)
    page.add_text("- Two new enterprise contracts", 70, 668, font_size=10)
    page.add_text(
        "The table below summarises revenue by region for the quarter.",
        60, 640, font_size=10,
    )
    rows = [
        ("Region", "Revenue", "Growth"),
        ("EMEA", "4,200,000", "12%"),
        ("APAC", "2,800,000", "21%"),
        ("Americas", "6,100,000", "8%"),
    ]
    y = 610
    for row in rows:
        for x, cell in zip((60, 190, 320), row):
            page.add_text(cell, x, y, font_size=10)
        y -= 15
    page.add_text(
        "Growth was strongest in Asia Pacific, driven by new contracts.",
        60, 530, font_size=10,
    )
    return document


def _kinds(document: Document) -> list[str]:
    return [block.kind for block in page_blocks(document._engine_pdf, 0)]


# ---------------------------------------------------------------------------
# Structure inference
# ---------------------------------------------------------------------------


def test_a_page_becomes_headings_lists_a_table_and_paragraphs():
    with _report() as document:
        blocks = page_blocks(document._engine_pdf, 0)

    assert [block.kind for block in blocks] == [
        "heading", "heading", "list", "paragraph", "table", "paragraph",
    ]
    assert blocks[0].text == "Regional results"
    assert blocks[0].level == 1
    assert blocks[1].level == 2  # smaller than the title, so a lower tier


def test_a_list_keeps_its_items_and_drops_its_bullets():
    """The markup supplies the bullet again; keeping the original doubles it."""
    with _report() as document:
        (block,) = [b for b in page_blocks(document._engine_pdf, 0) if b.kind == "list"]

    assert block.items == [
        "Services revenue up 18 per cent",
        "Two new enterprise contracts",
    ]
    assert block.ordered is False


def test_a_numbered_list_is_recognised_as_ordered():
    document = Document()
    page = document.pages.add()
    page.add_text("Steps", 60, 740, font_size=18)
    page.add_text("1. Open the file", 70, 700, font_size=10)
    page.add_text("2. Check the totals", 70, 686, font_size=10)
    page.add_text("3. Sign it off", 70, 672, font_size=10)

    (block,) = [b for b in page_blocks(document._engine_pdf, 0) if b.kind == "list"]

    assert block.ordered is True
    assert block.items == ["Open the file", "Check the totals", "Sign it off"]


def test_an_aligned_grid_becomes_a_table_with_its_cells():
    with _report() as document:
        (table,) = [b for b in page_blocks(document._engine_pdf, 0) if b.kind == "table"]

    assert table.rows[0] == ["Region", "Revenue", "Growth"]
    assert table.rows[1] == ["EMEA", "4,200,000", "12%"]
    assert len(table.rows) == 4


def test_a_table_is_not_mistaken_for_page_columns():
    """A gutter has to be *whitespace*, not just a gap between anchors.

    Widely spaced table cells leave the same anchor gap a two-column page does.
    What tells them apart is the prose above and below running straight through
    the supposed gutter -- which the column splitter now checks, so the grid
    survives to be recognised as a table instead of being read column-major.
    """
    with _report() as document:
        blocks = page_blocks(document._engine_pdf, 0)

    texts = [b.text for b in blocks if b.kind == "paragraph"]
    assert any(text.startswith("The table below") for text in texts)
    assert any(text.startswith("Growth was strongest") for text in texts)
    # Column-major reading would have glued a column's cells into one run.
    assert not any("EMEA APAC" in text for text in texts)


def test_a_real_two_column_page_is_still_read_column_by_column():
    document = Document()
    page = document.pages.add()
    for offset in range(5):
        y = 700 - offset * 14
        page.add_text(f"left line {offset}", 60, y, font_size=9)
        page.add_text(f"right line {offset}", 320, y, font_size=9)

    blocks = page_blocks(document._engine_pdf, 0)

    assert len(blocks) == 2
    assert "left line 0" in blocks[0].text and "left line 4" in blocks[0].text
    assert "right line 0" in blocks[1].text


def test_wrapped_lines_join_into_one_paragraph():
    document = Document()
    page = document.pages.add()
    page.add_text("Revenue grew across every region this quarter,", 60, 700, font_size=10)
    page.add_text("with services contributing most of the increase.", 60, 686, font_size=10)

    (block,) = page_blocks(document._engine_pdf, 0)

    assert block.kind == "paragraph"
    assert block.text == (
        "Revenue grew across every region this quarter, "
        "with services contributing most of the increase."
    )


def test_an_empty_page_exports_nothing():
    document = Document()
    document.pages.add()

    assert page_blocks(document._engine_pdf, 0) == []


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------


def test_html_is_well_formed_and_uses_real_elements():
    with _report() as document:
        html = document.to_html(title="Report")

    body = html[html.index("<body>") : html.index("</body>") + len("</body>")]
    root = ElementTree.fromstring(body.replace("<hr>", "<hr/>"))
    tags = [element.tag for element in root.iter()]
    assert "h1" in tags and "ul" in tags and "table" in tags and "p" in tags
    assert "<title>Report</title>" in html


def test_html_escapes_text_that_would_otherwise_be_markup():
    document = Document()
    page = document.pages.add()
    page.add_text("a < b & c > d", 60, 700, font_size=11)

    html = document.to_html()

    assert "a &lt; b &amp; c &gt; d" in html
    assert "<p>a < b" not in html


def test_html_table_gives_the_first_row_header_cells():
    with _report() as document:
        html = document.to_html()

    assert "<th>Region</th>" in html
    assert "<td>EMEA</td>" in html


def test_pages_are_separated_by_a_rule():
    document = Document()
    for index in range(2):
        document.pages.add().add_text(f"Page {index}", 60, 700, font_size=11)

    html = document.to_html()

    assert html.count("<hr>") == 1


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def test_markdown_renders_headings_lists_and_a_gfm_table():
    with _report() as document:
        markdown = document.to_markdown()

    assert "# Regional results" in markdown
    assert "## Highlights" in markdown
    assert "- Services revenue up 18 per cent" in markdown
    assert "| Region | Revenue | Growth |" in markdown
    assert "| --- | --- | --- |" in markdown
    assert "| EMEA | 4,200,000 | 12% |" in markdown


def test_markdown_escapes_only_what_changes_the_meaning():
    """Escaping every punctuation mark turns prose into a thicket of backslashes."""
    document = Document()
    page = document.pages.add()
    page.add_text("Revenue rose 3.1% - a record - for the *EMEA* region.", 60, 700, font_size=11)

    markdown = document.to_markdown()

    assert "3.1%" in markdown  # a full stop mid-sentence is not a list marker
    assert "\\*EMEA\\*" in markdown  # asterisks would become emphasis
    assert "\\." not in markdown


def test_markdown_escapes_a_leading_marker_so_prose_stays_prose():
    document = Document()
    page = document.pages.add()
    page.add_text("Title", 60, 740, font_size=20)
    page.add_text("# not a heading in the original", 60, 700, font_size=10)

    markdown = document.to_markdown()

    assert "\\# not a heading" in markdown


def test_a_numbered_list_renders_with_numbers():
    document = Document()
    page = document.pages.add()
    page.add_text("Steps", 60, 740, font_size=18)
    page.add_text("1. First", 70, 700, font_size=10)
    page.add_text("2. Second", 70, 686, font_size=10)

    markdown = document.to_markdown()

    assert "1. First" in markdown
    assert "2. Second" in markdown


# ---------------------------------------------------------------------------
# The public API
# ---------------------------------------------------------------------------


def test_page_level_export_covers_one_page():
    document = Document()
    document.pages.add().add_text("One", 60, 700, font_size=11)
    document.pages.add().add_text("Two", 60, 700, font_size=11)

    markdown = document.pages[1].to_markdown()

    assert "Two" in markdown
    assert "One" not in markdown


def test_save_as_html_writes_one_file(tmp_path: Path):
    with _report() as document:
        written = document.save_as_html(tmp_path / "report.html")

    assert [path.name for path in written] == ["report.html"]
    assert "<h1>Regional results</h1>" in written[0].read_text(encoding="utf-8")


def test_save_as_html_can_split_into_one_file_per_page(tmp_path: Path):
    document = Document()
    for index in range(3):
        document.pages.add().add_text(f"Page {index}", 60, 700, font_size=11)

    written = document.save_as_html(tmp_path / "out.html", split_into_pages=True)

    assert [path.name for path in written] == [
        "out-1.html", "out-2.html", "out-3.html",
    ]
    assert "Page 2" in written[2].read_text(encoding="utf-8")


def test_save_as_markdown_writes_the_file(tmp_path: Path):
    with _report() as document:
        written = document.save_as_markdown(tmp_path / "report.md")

    assert written.exists()
    assert "# Regional results" in written.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("save_format", "suffix", "marker"),
    [
        (DocFormat.HTML, ".html", "<h1>"),
        (DocFormat.MARKDOWN, ".md", "# "),
    ],
)
def test_save_with_a_structure_format_writes_it(
    tmp_path: Path, save_format, suffix: str, marker: str
):
    """These formats used to raise ``UnsupportedFeatureException``."""
    with _report() as document:
        target = tmp_path / f"doc{suffix}"
        document.save(target, save_format)

    assert marker in target.read_text(encoding="utf-8")


def test_the_save_options_objects_select_the_same_exports(tmp_path: Path):
    """Ported code that builds an options object reaches the same place."""
    with _report() as document:
        html_target = tmp_path / "opts.html"
        document.save(html_target, HtmlSaveOptions())
        markdown_target = tmp_path / "opts.md"
        document.save(markdown_target, MarkdownSaveOptions())

    assert "<h1>" in html_target.read_text(encoding="utf-8")
    assert "# Regional results" in markdown_target.read_text(encoding="utf-8")


def test_html_save_options_split_into_pages_is_honoured(tmp_path: Path):
    document = Document()
    document.pages.add().add_text("One", 60, 700, font_size=11)
    document.pages.add().add_text("Two", 60, 700, font_size=11)
    options = HtmlSaveOptions()
    options.split_into_pages = True

    document.save(tmp_path / "split.html", options)

    assert (tmp_path / "split-1.html").exists()
    assert (tmp_path / "split-2.html").exists()


def test_saving_a_structure_format_to_a_stream_is_refused(tmp_path: Path):
    document = Document()
    document.pages.add()

    with (tmp_path / "x.html").open("wb") as stream:
        with pytest.raises(PdfValidationException, match="need a file path"):
            document.save(stream, DocFormat.HTML)


def test_the_document_title_becomes_the_html_title(tmp_path: Path):
    document = Document()
    document.pages.add().add_text("Body", 60, 700, font_size=11)
    document.info["Title"] = "From metadata"

    assert "<title>From metadata</title>" in document.to_html()


def test_a_metadata_title_is_not_repeated_over_the_page_heading():
    """A document that already states its title should not state it twice."""
    with _report() as document:
        document.info["Title"] = "Regional results"
        markdown = document.to_markdown()

    assert markdown.count("Regional results") == 1


def test_a_metadata_title_is_used_when_the_page_has_no_heading():
    document = Document()
    document.pages.add().add_text("Just a paragraph of body text.", 60, 700, font_size=11)
    document.info["Title"] = "Report title"

    markdown = document.to_markdown()

    assert markdown.startswith("# Report title")


def test_selected_pages_only():
    document = Document()
    document.pages.add().add_text("Alpha", 60, 700, font_size=11)
    document.pages.add().add_text("Beta", 60, 700, font_size=11)

    markdown = document.to_markdown(pages=[1])

    assert "Beta" in markdown and "Alpha" not in markdown
