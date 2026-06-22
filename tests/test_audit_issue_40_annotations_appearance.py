"""AUDIT #40: non-text annotation subtypes and appearance streams (/AP)."""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

from aspose_pdf import Document

# Minimal form content in annot-local coords (0,0)-(w,h); rect is 100x100.
_AP_GRAY_FILL = b"0.5 g\n0 0 100 100 re f\n"


def test_non_text_annotation_subtypes_roundtrip() -> None:
    """Link, Highlight, Square, and Circle subtypes persist through save/load."""
    doc = Document()
    doc.pages.add()
    page = doc.pages[0]
    specs = [
        ("Link", (10, 10, 110, 50), "jump"),
        ("Highlight", (10, 60, 110, 90), ""),
        ("Square", (10, 100, 110, 200), "box"),
        ("Circle", (10, 210, 110, 310), "round"),
    ]
    for subtype, rect, contents in specs:
        page.annotations.add(subtype, rect, contents)

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        path = f.name
    try:
        doc.save(path, overwrite=True)
        doc2 = Document()
        doc2.load_from(path)
        page2 = doc2.pages[0]
        assert len(page2.annotations) == len(specs)
        for i, (subtype, rect, contents) in enumerate(specs):
            a = page2.annotations[i]
            assert a.subtype == subtype
            assert a.rect == rect
            assert a.contents == contents
            assert not a.has_appearance
    finally:
        Path(path).unlink(missing_ok=True)


def test_appearance_stream_save_load_and_facade() -> None:
    doc = Document()
    doc.pages.add()
    page = doc.pages[0]
    page.annotations.add(
        "Text",
        (100, 100, 200, 200),
        "with AP",
        appearance_normal=_AP_GRAY_FILL,
    )
    a0 = page.annotations[0]
    assert a0.has_appearance
    assert a0.appearance_normal == _AP_GRAY_FILL

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    doc2 = Document()
    doc2.load_from(buf)
    b0 = doc2.pages[0].annotations[0]
    assert b0.has_appearance
    assert len(b0.appearance_normal) > 0
    engine_ann = doc2._engine_pdf.get_annotations(0)[0]
    assert engine_ann["has_AP"] is True
    assert len(engine_ann.get("AP_N", b"")) > 0


def test_flatten_uses_normal_appearance_stream() -> None:
    doc = Document()
    doc.pages.add()
    page = doc.pages[0]
    page.annotations.add(
        "Highlight",
        (100, 100, 200, 200),
        "",
        appearance_normal=_AP_GRAY_FILL,
    )
    before = doc._engine_pdf.page_contents[0]
    doc.flatten()
    after = doc._engine_pdf.page_contents[0]
    assert len(after) > len(before)
    assert b"Do" in after
    assert len(doc._engine_pdf.get_annotations(0)) == 0


def test_insert_with_appearance_and_clear_appearance_via_setter() -> None:
    doc = Document()
    doc.pages.add()
    page = doc.pages[0]
    page.annotations.insert(
        0,
        "Square",
        (0, 0, 50, 50),
        "x",
        appearance_normal=_AP_GRAY_FILL,
    )
    ann = page.annotations[0]
    assert ann.has_appearance
    ann.appearance_normal = None
    assert not ann.has_appearance
    assert ann.appearance_normal == b""
