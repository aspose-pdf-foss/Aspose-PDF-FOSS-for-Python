"""Compatibility names without an implementation must fail explicitly.

The package exposes option/enumeration objects for formats it does not
implement. They must never make an operation silently do nothing: loading a
document, and saving one, reject them with
:class:`~aspose_pdf.exceptions.UnsupportedFeatureException`. The canonical
``Document`` and the ``generated`` compatibility ``Document`` must agree.
"""

from __future__ import annotations

import io
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from aspose_pdf.document import Document
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfParseException,
    PdfSecurityException,
    UnsupportedFeatureException,
)
from aspose_pdf.generated.document import Document as GeneratedDocument
from aspose_pdf.html import HtmlLoadOptions
from aspose_pdf.latex import LatexFragment
from aspose_pdf.load_options import (
    CdrLoadOptions,
    CgmLoadOptions,
    OfdLoadOptions,
    SvgLoadOptions,
)
from aspose_pdf.printing import PrinterSettings
from aspose_pdf.save_format import SaveFormat
from aspose_pdf.save_options import DocFormat
from aspose_pdf.svg import SvgLoadOptions as CompatSvgLoadOptions
from tests.helpers_make_pdfs import write_min_pdf

_DOCUMENT_TYPES = (Document, GeneratedDocument)

_LOAD_PLACEHOLDERS: tuple[tuple[Callable[[], Any], str], ...] = (
    (CdrLoadOptions, "CorelDRAW (CDR) import"),
    (CgmLoadOptions, "CGM import"),
    (HtmlLoadOptions, "HTML import"),
    (OfdLoadOptions, "OFD import"),
    (SvgLoadOptions, "SVG import"),
    (CompatSvgLoadOptions, "SVG import"),
    (lambda: LatexFragment("x^2"), "LaTeX authoring"),
)

_SAVE_PLACEHOLDERS: tuple[tuple[Callable[[], Any], str], ...] = (
    (lambda: SaveFormat.PPTX, "PPTX export"),
    # DocFormat.SVG/HTML/MARKDOWN and their save-options objects are no longer
    # placeholders -- they write real files; see tests/test_svg_export.py and
    # tests/test_text_export.py.
    (PrinterSettings, "printing"),
)


def _ids(cases: tuple[tuple[Callable[[], Any], str], ...]) -> list[str]:
    return [feature.replace(" ", "-") for _, feature in cases]


def _message(feature: str) -> str:
    """Regex asserting the error names the feature, not just "unsupported"."""
    return re.escape(f"{feature} is not implemented")


# ---------------------------------------------------------------------------
# The constructor loads instead of swallowing its arguments
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("document_type", _DOCUMENT_TYPES)
def test_constructor_loads_the_source(document_type: type, tmp_path: Path) -> None:
    path = tmp_path / "in.pdf"
    write_min_pdf(path, page_count=2)

    doc = document_type(str(path))
    try:
        assert doc.page_count == 2
        assert doc.file_name == str(path)
    finally:
        doc.dispose()


@pytest.mark.parametrize("document_type", _DOCUMENT_TYPES)
def test_constructor_without_source_creates_empty_document(
    document_type: type,
) -> None:
    doc = document_type()
    try:
        assert doc.page_count == 0
    finally:
        doc.dispose()


@pytest.mark.parametrize("document_type", _DOCUMENT_TYPES)
def test_constructor_propagates_missing_file(
    document_type: type, tmp_path: Path
) -> None:
    with pytest.raises(FileNotFoundError):
        document_type(str(tmp_path / "nope.pdf"))


@pytest.mark.parametrize("document_type", _DOCUMENT_TYPES)
def test_constructor_propagates_bad_pdf_bytes(document_type: type) -> None:
    with pytest.raises(PdfParseException, match="PDF header"):
        document_type(b"not a pdf")

    with pytest.raises(PdfParseException, match="PDF header"):
        document_type(io.BytesIO(b"not a pdf"))


@pytest.mark.parametrize("document_type", _DOCUMENT_TYPES)
def test_constructor_opens_encrypted_source_with_password(
    document_type: type, tmp_path: Path
) -> None:
    plain = tmp_path / "plain.pdf"
    encrypted = tmp_path / "enc.pdf"
    write_min_pdf(plain, page_count=1)

    encrypter = document_type(str(plain))
    encrypter.encrypt("secret")
    encrypter.save(str(encrypted))
    encrypter.dispose()

    with pytest.raises(PdfSecurityException, match="Password required"):
        document_type(str(encrypted))

    opened = document_type(str(encrypted), password="secret")
    try:
        assert opened.page_count == 1
    finally:
        opened.dispose()


@pytest.mark.parametrize("document_type", _DOCUMENT_TYPES)
def test_constructor_rejects_unknown_arguments(document_type: type) -> None:
    with pytest.raises(TypeError):
        document_type(whatever=1)

    with pytest.raises(TypeError, match="password requires a load source"):
        document_type(password="secret")


def test_constructor_matches_the_generated_compatibility_document(
    tmp_path: Path,
) -> None:
    """Both import paths must agree on the same scenario (audit P0.2)."""
    path = tmp_path / "parity.pdf"
    write_min_pdf(path, page_count=3)

    canonical = Document(str(path))
    generated = GeneratedDocument(str(path))
    try:
        assert canonical.page_count == generated.page_count == 3
        assert canonical.file_name == generated.file_name == str(path)
    finally:
        canonical.dispose()
        generated.dispose()


# ---------------------------------------------------------------------------
# Load-side placeholders
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("document_type", _DOCUMENT_TYPES)
@pytest.mark.parametrize(
    ("factory", "feature"), _LOAD_PLACEHOLDERS, ids=_ids(_LOAD_PLACEHOLDERS)
)
def test_load_options_placeholder_is_rejected_as_options(
    document_type: type,
    factory: Callable[[], Any],
    feature: str,
    tmp_path: Path,
) -> None:
    path = tmp_path / "in.pdf"
    write_min_pdf(path, page_count=1)

    with pytest.raises(UnsupportedFeatureException, match=_message(feature)):
        document_type(str(path), factory())


@pytest.mark.parametrize("document_type", _DOCUMENT_TYPES)
@pytest.mark.parametrize(
    ("factory", "feature"), _LOAD_PLACEHOLDERS, ids=_ids(_LOAD_PLACEHOLDERS)
)
def test_load_options_placeholder_is_rejected_as_source(
    document_type: type, factory: Callable[[], Any], feature: str
) -> None:
    with pytest.raises(UnsupportedFeatureException, match=_message(feature)):
        document_type(factory())

    doc = document_type()
    try:
        with pytest.raises(UnsupportedFeatureException, match=_message(feature)):
            doc.load_from(factory())
    finally:
        doc.dispose()


def test_unknown_options_object_is_rejected_too(tmp_path: Path) -> None:
    path = tmp_path / "in.pdf"
    write_min_pdf(path, page_count=1)

    with pytest.raises(TypeError, match="load options are not supported"):
        Document(str(path), object())


def test_unsupported_feature_exception_is_catchable_both_ways() -> None:
    with pytest.raises(NotImplementedError):
        Document(SvgLoadOptions())

    with pytest.raises(AsposePdfException):
        Document(SvgLoadOptions())


# ---------------------------------------------------------------------------
# Save-side placeholders
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("document_type", _DOCUMENT_TYPES)
@pytest.mark.parametrize(
    ("factory", "feature"), _SAVE_PLACEHOLDERS, ids=_ids(_SAVE_PLACEHOLDERS)
)
def test_save_rejects_export_placeholder_without_writing(
    document_type: type,
    factory: Callable[[], Any],
    feature: str,
    tmp_path: Path,
) -> None:
    source = tmp_path / "in.pdf"
    write_min_pdf(source, page_count=1)
    destination = tmp_path / "out.bin"

    doc = document_type(str(source))
    try:
        with pytest.raises(UnsupportedFeatureException, match=_message(feature)):
            doc.save(str(destination), factory())
        assert not destination.exists()

        stream = io.BytesIO()
        with pytest.raises(UnsupportedFeatureException, match=_message(feature)):
            doc.save(stream, factory())
        assert stream.getvalue() == b""
    finally:
        doc.dispose()


@pytest.mark.parametrize("document_type", _DOCUMENT_TYPES)
@pytest.mark.parametrize(
    "save_format", [None, SaveFormat.PDF, DocFormat.PDF], ids=["none", "SaveFormat", "DocFormat"]
)
def test_save_accepts_pdf_formats(
    document_type: type, save_format: Any, tmp_path: Path
) -> None:
    source = tmp_path / "in.pdf"
    write_min_pdf(source, page_count=1)
    destination = tmp_path / "out.pdf"

    doc = document_type(str(source))
    try:
        assert doc.save(str(destination), save_format) is doc
        assert destination.read_bytes().startswith(b"%PDF-")
    finally:
        doc.dispose()


@pytest.mark.parametrize("document_type", _DOCUMENT_TYPES)
def test_save_rejects_unknown_format_object(
    document_type: type, tmp_path: Path
) -> None:
    source = tmp_path / "in.pdf"
    write_min_pdf(source, page_count=1)
    destination = tmp_path / "out.pdf"

    doc = document_type(str(source))
    try:
        with pytest.raises(TypeError, match="save_format must be"):
            doc.save(str(destination), object())
        assert not destination.exists()
    finally:
        doc.dispose()


def test_save_still_honours_the_overwrite_guard(tmp_path: Path) -> None:
    """The added format argument must not disturb the existing keyword."""
    source = tmp_path / "in.pdf"
    write_min_pdf(source, page_count=1)
    destination = tmp_path / "out.pdf"

    with Document(str(source)) as doc:
        doc.save(str(destination))
        with pytest.raises(FileExistsError):
            doc.save(str(destination))
        doc.save(str(destination), SaveFormat.PDF, overwrite=True)


# ---------------------------------------------------------------------------
# The placeholder modules stay import-only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_name",
    [
        "aspose_pdf.cgm",
        "aspose_pdf.html",
        "aspose_pdf.latex",
        "aspose_pdf.markdown",
        "aspose_pdf.ofd",
        "aspose_pdf.presentation",
        "aspose_pdf.printing",
        "aspose_pdf.svg",
    ],
)
def test_compatibility_modules_expose_no_conversion_entry_point(
    module_name: str,
) -> None:
    """These modules hold option/value objects only — nothing that converts."""
    import importlib

    module = importlib.import_module(module_name)
    for attribute in ("convert", "save", "load", "print", "render"):
        assert not hasattr(module, attribute), (
            f"{module_name}.{attribute} would be an unimplemented operation"
        )
