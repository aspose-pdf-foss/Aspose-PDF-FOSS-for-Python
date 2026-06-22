import compileall
from pathlib import Path


def test_imports_smoke():
    import aspose_pdf  # noqa: F401
    from aspose_pdf.document import Document  # noqa: F401
    from aspose_pdf.pages import PageCollection  # noqa: F401
    from aspose_pdf.facades import PdfFileEditor, PdfExtractor  # noqa: F401


def test_optional_modules_import_smoke():
    import aspose_pdf.annotations.pdf3d  # noqa: F401
    import aspose_pdf.load_options  # noqa: F401
    import aspose_pdf.save_options  # noqa: F401
    import aspose_pdf.svg  # noqa: F401
    import aspose_pdf.xmp  # noqa: F401


def test_source_tree_compiles():
    package_dir = Path(__file__).resolve().parents[1] / "src" / "aspose_pdf"
    assert compileall.compile_dir(package_dir, quiet=1)
