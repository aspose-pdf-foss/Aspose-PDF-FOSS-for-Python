from pathlib import Path
from aspose_pdf.engine.simple_pdf import SimplePdf


def write_min_pdf(path: Path, page_count: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pages = [(0.0, 0.0, 200.0, 200.0)] * page_count
    SimplePdf(pages=pages).save(path)
