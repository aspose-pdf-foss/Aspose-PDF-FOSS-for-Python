"""HTML and Markdown save options do what they say, and every export is written safely.

``HtmlSaveOptions`` and ``MarkdownSaveOptions`` reached ``save`` with all but
one setting dropped: images always went into the page as ``data:`` URIs
whatever folder was named for them, ``markdown_format`` and the paragraph
spacing had no effect, and ``use_area_clipping`` meant nothing. Each is now
honoured, or -- for the XPS/APS intermediate files an export never produces --
refused. ``save(..., DocFormat.HTML)`` also ignored ``overwrite``, and the HTML,
Markdown, SVG, TIFF, raster, image and attachment writers truncated their
target before writing it, so a failed write destroyed the file it was
replacing; every one now writes atomically.

The crop-box rule is the references' own: MuPDF extracts only what lies inside
the page's crop box by default, as pdfium does when bounded by it, and both
extract everything when asked to (see ``test_content_outside_the_crop_box``).
"""

from __future__ import annotations

import io
import struct
import zlib
from pathlib import Path

import pytest

from aspose_pdf import Document
from aspose_pdf.exceptions import PdfValidationException, UnsupportedFeatureException
from aspose_pdf.html import HtmlSaveOptions
from aspose_pdf.markdown import MarkdownSaveOptions
from aspose_pdf.save_options import DocFormat


def _png(colour: tuple[int, int, int]) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    rows = b"".join(b"\x00" + bytes(colour) * 4 for _ in range(4))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def _illustrated() -> Document:
    """Two pages, the same logo on both, a second picture on the first."""
    document = Document()
    for number in (1, 2):
        page = document.pages.add()
        page.add_text(f"Page {number} heading", 60, 740, font_size=20)
        page.add_image(_png((200, 0, 0)), 60, 500, 120, 120)
    document.pages[0].add_image(_png((0, 0, 200)), 300, 500, 120, 120)
    return document


def _two_lines(step: float) -> Document:
    """Two lines of 10-point body text, *step* points apart, under a heading."""
    document = Document()
    page = document.pages.add()
    page.add_text("Heading", 60, 760, font_size=20)
    page.add_text("First line of the body.", 60, 700, font_size=10)
    page.add_text("Second line of the body.", 60, 700 - step, font_size=10)
    return document


def _paragraphs(markdown: str) -> list[str]:
    return [part for part in markdown.split("\n\n") if part and not part.startswith("#")]


# --- where the figures go ----------------------------------------------------------------


def test_html_resources_directory_writes_each_image_once_and_links_it(tmp_path):
    options = HtmlSaveOptions()
    options.resources_directory = "assets"
    _illustrated().save(tmp_path / "out.html", options)

    html = (tmp_path / "out.html").read_text(encoding="utf-8")
    written = sorted(path.name for path in (tmp_path / "assets").iterdir())
    assert written == ["out-image-1.png", "out-image-2.png"]  # the logo once, not twice
    assert "data:image" not in html
    assert html.count('src="assets/out-image-1.png"') == 2
    assert html.count('src="assets/out-image-2.png"') == 1
    assert (tmp_path / "assets" / "out-image-1.png").read_bytes().startswith(b"\x89PNG")


def test_html_resources_are_shared_by_split_pages_and_urls_are_encoded(tmp_path):
    options = HtmlSaveOptions()
    options.split_into_pages = True
    options.resources_directory = str(tmp_path / "shared images")  # absolute
    target = tmp_path / "pages" / "my report.html"
    _illustrated().save(target, options)

    first = (tmp_path / "pages" / "my report-1.html").read_text(encoding="utf-8")
    second = (tmp_path / "pages" / "my report-2.html").read_text(encoding="utf-8")
    link = 'src="../shared%20images/my%20report-image-1.png"'
    assert link in first and link in second
    assert len(list((tmp_path / "shared images").iterdir())) == 2


def test_without_a_resources_directory_images_stay_embedded(tmp_path):
    _illustrated().save(tmp_path / "out.html", HtmlSaveOptions())
    assert (tmp_path / "out.html").read_text(encoding="utf-8").count("data:image/png") == 3
    assert sorted(path.name for path in tmp_path.iterdir()) == ["out.html"]


@pytest.mark.parametrize("attribute", ["image_directory", "resources_directory_name"])
def test_markdown_images_go_to_the_named_folder(tmp_path, attribute):
    options = MarkdownSaveOptions(**{attribute: "out_images"})
    _illustrated().save(tmp_path / "out.md", options)
    markdown = (tmp_path / "out.md").read_text(encoding="utf-8")
    assert "](out_images/out-image-1.png)" in markdown
    assert "data:image" not in markdown
    assert len(list((tmp_path / "out_images").iterdir())) == 2


def test_markdown_image_folder_options_are_one_folder(tmp_path):
    options = MarkdownSaveOptions(image_directory="a", resources_directory_name="b")
    with pytest.raises(PdfValidationException, match="not both"):
        _illustrated().save(tmp_path / "out.md", options)
    assert list(tmp_path.iterdir()) == []


def test_markdown_without_extracted_images_writes_no_image_files(tmp_path):
    options = MarkdownSaveOptions(extract_images=False, image_directory="img")
    _illustrated().save(tmp_path / "out.md", options)
    assert not (tmp_path / "img").exists()
    assert "![" not in (tmp_path / "out.md").read_text(encoding="utf-8")


# --- the Markdown dialect -------------------------------------------------------------------


def _with_table() -> Document:
    """A table, as tests/test_text_export.py's report lays one out."""
    document = Document()
    page = document.pages.add()
    page.add_text("The table below summarises revenue by region.", 60, 640, font_size=10)
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
    page.add_text("Growth was strongest in Asia Pacific.", 60, 530, font_size=10)
    return document


def test_commonmark_writes_a_table_as_html(tmp_path):
    gfm = _with_table().to_markdown()
    commonmark = _with_table().to_markdown(markdown_format="commonmark")
    assert "| Region | Revenue | Growth |" in gfm and "<table>" not in gfm
    assert "<table>" in commonmark and "| Region |" not in commonmark
    assert "<th>Region</th>" in commonmark

    _with_table().save(tmp_path / "t.md", MarkdownSaveOptions(markdown_format="CommonMark"))
    assert "<table>" in (tmp_path / "t.md").read_text(encoding="utf-8")


def test_an_unknown_markdown_format_is_refused(tmp_path):
    with pytest.raises(PdfValidationException, match="Markdown format"):
        _with_table().save(tmp_path / "t.md", MarkdownSaveOptions(markdown_format="MultiMarkdown"))
    with pytest.raises(PdfValidationException, match="Markdown format"):
        _with_table().to_markdown(markdown_format=None)
    assert list(tmp_path.iterdir()) == []


# --- paragraph spacing and the visible area ----------------------------------------------------


@pytest.mark.parametrize(
    ("step", "gap", "paragraphs"),
    [(16, None, 1), (17, None, 2), (16, 1.5, 2), (17, 1.8, 1)],
)
def test_max_distance_between_text_lines_decides_the_paragraphs(tmp_path, step, gap, paragraphs):
    options = HtmlSaveOptions()
    if gap is not None:
        options.max_distance_between_text_lines = gap
    _two_lines(step).save(tmp_path / "p.html", options)
    html = (tmp_path / "p.html").read_text(encoding="utf-8")
    assert html.count("<p>") == paragraphs
    assert _paragraphs(_two_lines(step).to_markdown(paragraph_gap=gap)).__len__() == paragraphs


def test_the_paragraph_gap_must_be_positive():
    with pytest.raises(ValueError, match="positive"):
        HtmlSaveOptions().max_distance_between_text_lines = 0
    with pytest.raises(PdfValidationException, match="positive"):
        _two_lines(16).to_html(paragraph_gap=-1)


def _cropped() -> Document:
    document = Document()
    page = document.pages.add()
    page.add_text("Inside the crop box", 100, 500, font_size=12)
    page.add_text("Outside crop inside media", 100, 740, font_size=12)
    page.add_text("Off the media box", 700, 400, font_size=12)
    page.crop_box = (50, 50, 560, 700)
    return document


def test_content_outside_the_crop_box(tmp_path):
    # MuPDF's get_text(): "Inside the crop box" only; with the media-box clip
    # off and an infinite clip, all three -- pdfium's get_text_range() too.
    clipped = _cropped().to_markdown()
    assert "Inside the crop box" in clipped
    assert "Outside crop" not in clipped and "Off the media box" not in clipped
    everything = _cropped().to_markdown(clip_to_page=False)
    assert all(text in everything for text in ("Inside", "Outside crop", "Off the media box"))

    options = HtmlSaveOptions()
    _cropped().save(tmp_path / "clipped.html", options)
    options.use_area_clipping = False
    _cropped().save(tmp_path / "all.html", options)
    assert "Outside crop" not in (tmp_path / "clipped.html").read_text(encoding="utf-8")
    assert "Outside crop" in (tmp_path / "all.html").read_text(encoding="utf-8")


def test_a_crop_box_may_name_its_corners_in_either_order():
    # ISO 32000-1 7.9.5: any two diagonally opposite corners. MuPDF and
    # pdfium read (560 700 50 50) as the box (50 50 560 700).
    document = _cropped()
    document.pages[0].crop_box = (560, 700, 50, 50)
    assert document.to_markdown() == _cropped().to_markdown()


def test_a_crop_box_off_the_media_box_does_not_empty_the_page():
    document = _cropped()
    document.pages[0].crop_box = (2000, 2000, 2100, 2100)
    assert "Inside the crop box" in document.to_markdown()


# --- what cannot be honoured -----------------------------------------------------------------------


@pytest.mark.parametrize("attribute", ["xps_intermediate_file", "aps_intermediate_file"])
def test_an_intermediate_file_is_refused(tmp_path, attribute):
    options = HtmlSaveOptions()
    setattr(options, attribute, tmp_path / "stage.bin")
    with pytest.raises(UnsupportedFeatureException, match=attribute):
        _illustrated().save(tmp_path / "out.html", options)
    assert list(tmp_path.iterdir()) == []


# --- overwrite ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("save_format", "name"),
    [(DocFormat.HTML, "x.html"), (DocFormat.MARKDOWN, "x.md"), (DocFormat.SVG, "x.svg")],
)
def test_save_honours_overwrite_for_exports(tmp_path, save_format, name):
    target = tmp_path / name
    target.write_bytes(b"keep me")
    with pytest.raises(FileExistsError):
        _two_lines(16).save(target, save_format)  # one page: one SVG file
    assert target.read_bytes() == b"keep me"
    _two_lines(16).save(target, save_format, overwrite=True)
    assert target.read_bytes() != b"keep me"


def test_an_existing_image_file_refuses_the_whole_export(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "out-image-2.png").write_bytes(b"keep me")
    options = HtmlSaveOptions()
    options.resources_directory = "assets"
    with pytest.raises(FileExistsError, match=r"out-image-2\.png"):
        _illustrated().save(tmp_path / "out.html", options)
    # Refused before anything was written, the HTML included.
    assert sorted(p.name for p in tmp_path.rglob("*") if p.is_file()) == ["out-image-2.png"]


def test_the_split_svg_pages_are_checked_together(tmp_path):
    (tmp_path / "x-2.svg").write_bytes(b"keep me")
    with pytest.raises(FileExistsError, match=r"x-2\.svg"):
        _illustrated().save(tmp_path / "x.svg", DocFormat.SVG)
    assert not (tmp_path / "x-1.svg").exists()


def test_the_direct_methods_still_replace_by_default(tmp_path):
    target = tmp_path / "x.html"
    target.write_bytes(b"old")
    assert _illustrated().save_as_html(target) == [target]
    assert target.read_bytes() != b"old"
    with pytest.raises(FileExistsError):
        _illustrated().save_as_markdown(tmp_path / "x.html", overwrite=False)


# --- atomic writes ------------------------------------------------------------------------------------


def _writers(document: Document, tmp_path: Path):
    from aspose_pdf.images import ImagePlacementAbsorber

    absorber = ImagePlacementAbsorber()
    document.pages[0].accept(absorber)
    placement = absorber.image_placements[0]
    document.add_attachment("note.txt", b"attached", mime="text/plain")
    attachment = Document(io.BytesIO(_saved(document))).embedded_files[0]
    return {
        "html": lambda path: document.save_as_html(path),
        "markdown": lambda path: document.save_as_markdown(path),
        "svg": lambda path: document.save_as_svg(path, pages=[0]),
        "page svg": lambda path: document.pages[0].save_as_svg(path),
        "tiff": lambda path: document.save_as_tiff(path, pages=[0]),
        "raster": lambda path: document.pages[0].render(dpi=18).save(path.with_suffix(".png")),
        "image": lambda path: placement.save(path.with_suffix(".png")),
        "attachment": lambda path: attachment.save(path),
    }


def _saved(document: Document) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


@pytest.mark.parametrize(
    "writer", ["html", "markdown", "svg", "page svg", "tiff", "raster", "image", "attachment"]
)
def test_a_failed_write_leaves_the_file_it_was_replacing(tmp_path, monkeypatch, writer):
    from aspose_pdf.engine import file_output

    document = _illustrated()
    write = _writers(document, tmp_path)[writer]
    target = tmp_path / "target.out"
    for path in (target, target.with_suffix(".png")):
        path.write_bytes(b"the previous file")

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(file_output.os, "replace", fail)
    with pytest.raises(OSError, match="disk full"):
        write(target)
    assert target.read_bytes() == b"the previous file"
    assert target.with_suffix(".png").read_bytes() == b"the previous file"
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
