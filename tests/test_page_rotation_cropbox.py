"""Tests for ``Page.rotation`` and ``Page.crop_box`` (read/write + round-trip)."""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document
from aspose_pdf.exceptions import PdfValidationException


def _roundtrip(doc: Document) -> Document:
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    reopened = Document()
    reopened.load_from(buf)
    return reopened


def _doc_with_pages(n: int = 1) -> Document:
    doc = Document()
    for _ in range(n):
        doc.pages.add()
    return doc


# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------


def test_rotation_defaults_to_zero():
    assert _doc_with_pages().pages[0].rotation == 0


def test_rotation_set_and_get():
    doc = _doc_with_pages()
    doc.pages[0].rotation = 90
    assert doc.pages[0].rotation == 90


def test_rotation_survives_roundtrip():
    doc = _doc_with_pages(3)
    doc.pages[0].rotation = 90
    doc.pages[1].rotation = 180
    doc.pages[2].rotation = 270
    reopened = _roundtrip(doc)
    assert [p.rotation for p in reopened.pages] == [90, 180, 270]


def test_rotation_is_normalised():
    doc = _doc_with_pages()
    doc.pages[0].rotation = -90
    assert doc.pages[0].rotation == 270
    doc.pages[0].rotation = 450
    assert doc.pages[0].rotation == 90
    doc.pages[0].rotation = 360
    assert doc.pages[0].rotation == 0


def test_rotation_rejects_non_multiples_of_90():
    page = _doc_with_pages().pages[0]
    with pytest.raises(PdfValidationException):
        page.rotation = 45
    with pytest.raises(PdfValidationException):
        page.rotation = "ninety"


def test_rotation_is_inherited_from_parent_node():
    from aspose_pdf.engine.cos import PdfName, PdfNumber

    doc = _doc_with_pages()
    engine = doc._engine_pdf
    page_dict = engine._get_page_dict(0)
    parent = engine._resolve(page_dict.mapping.get(PdfName("Parent")))
    # Set /Rotate on the parent /Pages node; the page itself has none.
    parent.mapping[PdfName("Rotate")] = PdfNumber(90)
    assert PdfName("Rotate") not in page_dict.mapping
    assert doc.pages[0].rotation == 90


# ---------------------------------------------------------------------------
# CropBox
# ---------------------------------------------------------------------------


def test_crop_box_defaults_to_media_box():
    page = _doc_with_pages().pages[0]
    assert page.crop_box == page.rect


def test_crop_box_set_and_get():
    doc = _doc_with_pages()
    doc.pages[0].crop_box = (10, 20, 300, 400)
    assert tuple(doc.pages[0].crop_box) == (10, 20, 300, 400)


def test_crop_box_survives_roundtrip():
    doc = _doc_with_pages()
    doc.pages[0].crop_box = (5, 5, 200, 250)
    reopened = _roundtrip(doc)
    assert tuple(reopened.pages[0].crop_box) == (5, 5, 200, 250)


def test_crop_box_rejects_bad_input():
    page = _doc_with_pages().pages[0]
    with pytest.raises(PdfValidationException):
        page.crop_box = (1, 2, 3)  # too few values
    with pytest.raises(PdfValidationException):
        page.crop_box = ("a", "b", "c", "d")  # non-numeric
