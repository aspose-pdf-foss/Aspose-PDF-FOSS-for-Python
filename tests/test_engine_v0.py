from pathlib import Path

from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.facades import PdfFileEditor
from tests.helpers_make_pdfs import write_min_pdf


def test_engine_roundtrip(tmp_path: Path):
    import aspose_pdf

    print(f"DEBUG: aspose_pdf file: {aspose_pdf.__file__}")
    src = tmp_path / "a.pdf"
    write_min_pdf(src, page_count=2)
    sp = SimplePdf.from_file(src)
    assert len(sp.pages) == 2
    out = tmp_path / "b.pdf"
    sp.save(out)
    sp2 = SimplePdf.from_file(out)
    assert len(sp2.pages) == 2


def test_concatenate_counts_pages(tmp_path: Path):
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    out = tmp_path / "out.pdf"
    write_min_pdf(a, page_count=1)
    write_min_pdf(b, page_count=3)
    editor = PdfFileEditor()
    assert (
        editor.concatenate([str(a), str(b)], out) is True
    )  # API shape might vary, checking facade later
    merged = SimplePdf.from_file(out)
    assert len(merged.pages) == 4


def test_extract_range(tmp_path: Path):
    src = tmp_path / "src.pdf"
    out = tmp_path / "extract.pdf"
    write_min_pdf(src, page_count=5)
    editor = PdfFileEditor()
    assert editor.extract(src, out, page_from=2, page_to=4) is True
    ext = SimplePdf.from_file(out)
    assert len(ext.pages) == 3
