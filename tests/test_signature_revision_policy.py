"""Revision-aware certification checks on real signed PDFs."""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document
from aspose_pdf.engine import dss
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfStream,
    PdfString,
)
from aspose_pdf.engine.incremental_update import IncrementalUpdate
from aspose_pdf.engine.pdf_parser_cos import PdfCosParser
from aspose_pdf.engine.pdf_writer_cos import PdfCosWriter
from aspose_pdf.engine.signature_revisions import entry, resolve
from aspose_pdf.engine.signing import SigningUtils
from aspose_pdf.validation import (
    CertificationLevel,
    ValidationOptions,
    ValidationStatus,
)
from tests.helpers_signatures import timestamp_authority


@pytest.fixture(scope="module")
def credentials():
    root, key = SigningUtils.create_self_signed_ca("Revision root")
    signer, signer_key = SigningUtils.issue_certificate("Revision signer", root, key)
    tsa = timestamp_authority("Revision TSA", root, key)
    return root, signer, signer_key, tsa


@pytest.fixture(scope="module")
def certified(credentials):
    root, signer, key, _tsa = credentials
    out = {}
    for level in (1, 2, 3):
        document = Document()
        page = document.pages.add()
        page.add_text("Original", 30, 100)
        document.form.add_text_field("Text", page, (10, 10, 100, 40), value="Initial")
        document.form.add_signature_field("Certify", page, (0, 0, 0, 0))
        document.form.add_signature_field("Approve", page, (0, 0, 0, 0))
        document.sign(
            "Certify",
            certificate=signer,
            private_key=key,
            extra_certificates=[root],
            certify=level,
        )
        buffer = io.BytesIO()
        document.save(buffer)
        out[level] = buffer.getvalue()
    return out


def _catalog(doc):
    return entry(doc, doc.trailer, "Root")


def _page(doc):
    pages = entry(doc, _catalog(doc), "Pages")
    return resolve(doc, entry(doc, pages, "Kids").items[0])


def _field(doc, name):
    acro = entry(doc, _catalog(doc), "AcroForm")
    return next(
        resolve(doc, ref)
        for ref in entry(doc, acro, "Fields").items
        if entry(doc, ref, "T").value == name.encode()
    )


def _edit(data, change):
    doc = PdfCosParser(data).parse()
    changed = change(doc)
    inc = IncrementalUpdate(data)
    writer = PdfCosWriter(doc)
    for obj in changed:
        number = next(n for n in doc.objects if doc.objects[n] is obj)
        body = writer.serialize_indirect(number, obj)
        inc.add_object(number, f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1"))
    return data + inc.generate()


def _set(obj, key, value):
    obj[PdfName(key)] = value
    return [obj]


def _validate(data, credentials):
    document = Document(io.BytesIO(data))
    return next(s for s in document.signatures if s.name == "Certify").validate(
        ValidationOptions(trusted_certificates=[credentials[0]])
    )


@pytest.mark.parametrize("level", [1, 2, 3])
def test_signed_revision_policy_is_valid(certified, credentials, level):
    result = _validate(certified[level], credentials)
    assert result.status == ValidationStatus.VALID, result.errors
    assert result.certification_level == CertificationLevel(level)


@pytest.mark.parametrize("level", [1, 2, 3])
@pytest.mark.parametrize(
    "kind", ["content", "box", "resources", "catalog", "metadata", "form_flags"]
)
def test_forbidden_changes_fail_at_every_level(certified, credentials, level, kind):
    def change(doc):
        if kind == "content":
            contents = entry(doc, _page(doc), "Contents")
            if isinstance(contents, PdfArray):
                contents = resolve(doc, contents.items[0])
            contents.content = b"q 1 0 0 rg 0 0 200 200 re f Q"
            return [contents]
        if kind == "box":
            return _set(
                _page(doc), "MediaBox", PdfArray([PdfNumber(v) for v in (0, 0, 20, 20)])
            )
        if kind == "resources":
            return _set(_page(doc), "Resources", PdfDictionary())
        if kind == "catalog":
            return _set(_catalog(doc), "OpenAction", PdfString("unapproved"))
        if kind == "metadata":
            return _set(_catalog(doc), "Lang", PdfString("fr-FR"))
        return _set(_field(doc, "Text"), "Ff", PdfNumber(1))

    updated = _edit(certified[level], change)
    result = _validate(updated, credentials)
    assert result.status == ValidationStatus.INVALID, result
    assert any("certification" in error for error in result.errors)


@pytest.mark.parametrize("level", [1, 2, 3])
def test_form_value_and_appearance_are_permitted_only_at_levels_2_and_3(
    certified, credentials, level
):
    updated = _edit(
        certified[level],
        lambda doc: _set(_field(doc, "Text"), "V", PdfString("Filled")),
    )
    result = _validate(updated, credentials)
    assert result.status == (
        ValidationStatus.INVALID if level == 1 else ValidationStatus.VALID
    ), result.errors


@pytest.mark.parametrize("level", [1, 2, 3])
def test_annotations_require_level_3(certified, credentials, level, tmp_path):
    def change(doc):
        page = _page(doc)
        annotations = entry(doc, page, "Annots")
        annotations.items.append(
            PdfDictionary(
                {
                    PdfName("Type"): PdfName("Annot"),
                    PdfName("Subtype"): PdfName("Text"),
                    PdfName("Contents"): PdfString("Note"),
                    PdfName("Rect"): PdfArray([PdfNumber(v) for v in (10, 10, 20, 20)]),
                }
            )
        )
        return [page]

    updated = _edit(certified[level], change)
    path = tmp_path / "annotated.pdf"
    path.write_bytes(updated)
    result = _validate(path.read_bytes(), credentials)
    assert result.status == (
        ValidationStatus.VALID if level == 3 else ValidationStatus.INVALID
    ), result.errors


@pytest.mark.parametrize("level", [1, 2, 3])
def test_approval_signature_requires_level_2_or_3(certified, credentials, level):
    root, signer, key, _tsa = credentials
    doc = Document(io.BytesIO(certified[level]))
    doc.sign("Approve", certificate=signer, private_key=key, extra_certificates=[root])
    buffer = io.BytesIO()
    doc.save(buffer)
    result = _validate(buffer.getvalue(), credentials)
    assert result.status == (
        ValidationStatus.INVALID if level == 1 else ValidationStatus.VALID
    ), result.errors


@pytest.mark.parametrize("level", [1, 2, 3])
def test_dss_and_invisible_document_timestamp_are_maintenance(
    certified, credentials, level
):
    root, _signer, _key, tsa = credentials
    from cryptography.hazmat.primitives.serialization import Encoding

    updated = dss.build_dss(
        certified[level], dss.DssMaterial(certs=[root.public_bytes(Encoding.DER)])
    )
    result = _validate(updated, credentials)
    assert result.status == ValidationStatus.VALID, result.errors
    updated = dss.add_document_timestamp(updated, tsa=tsa)
    result = _validate(updated, credentials)
    assert result.status == ValidationStatus.VALID, result.errors


def test_reverting_forbidden_change_does_not_erase_history(certified, credentials):
    changed = _edit(
        certified[3], lambda doc: _set(_catalog(doc), "Lang", PdfString("fr-FR"))
    )

    def revert(doc):
        catalog = _catalog(doc)
        catalog.pop(PdfName("Lang"))
        return [catalog]

    restored = _edit(changed, revert)
    result = _validate(restored, credentials)
    assert result.status == ValidationStatus.INVALID


@pytest.mark.parametrize("policy", [None, 3])
def test_unsigned_policy_cannot_relax_certification(certified, credentials, policy):
    def change(doc):
        value = entry(doc, _field(doc, "Certify"), "V")
        if policy is None:
            value.pop(PdfName("Reference"))
        else:
            reference = entry(doc, value, "Reference").items[0]
            entry(doc, reference, "TransformParams")[PdfName("P")] = PdfNumber(policy)
        return [value]

    updated = _edit(certified[1], change)
    result = _validate(updated, credentials)
    assert result.status == ValidationStatus.INVALID
    assert result.certification_level == CertificationLevel.NO_CHANGES


def test_malformed_xref_does_not_use_recovery(certified, credentials):
    updated = _edit(
        certified[2], lambda doc: _set(_field(doc, "Text"), "V", PdfString("Filled"))
    )
    offset = updated.rfind(b"startxref")
    corrupted = updated[:offset] + b"startxref\n1\n%%EOF\n"
    result = _validate(corrupted, credentials)
    assert result.status == ValidationStatus.INVALID


def test_shared_page_and_form_resource_is_still_protected(certified, credentials):
    # Build the shared resource before certification; an appearance update
    # must not authorize editing the same stream through page /Contents.
    root, signer, key, _tsa = credentials
    base = Document()
    page = base.pages.add()
    page.add_text("Visible", 10, 100)
    base.form.add_text_field("Text", page, (0, 0, 50, 20))
    base.form.add_signature_field("Certify", page, (0, 0, 0, 0))
    buffer = io.BytesIO()
    base.save(buffer)

    def alias(doc):
        page = _page(doc)
        return _set(
            _field(doc, "Text"),
            "AP",
            PdfDictionary({PdfName("N"): page.get(PdfName("Contents"))}),
        )

    before = _edit(buffer.getvalue(), alias)
    document = Document(io.BytesIO(before))
    document.sign(
        "Certify",
        certificate=signer,
        private_key=key,
        extra_certificates=[root],
        certify=2,
    )
    buffer = io.BytesIO()
    document.save(buffer)

    def mutate(doc):
        contents = entry(doc, _page(doc), "Contents")
        assert isinstance(contents, PdfStream)
        contents.content = b"new page content"
        return [contents]

    updated = _edit(buffer.getvalue(), mutate)
    assert _validate(updated, credentials).status == ValidationStatus.INVALID


@pytest.mark.parametrize("field_name", ["Text", "Certify"])
@pytest.mark.parametrize("level", [1, 2, 3])
def test_widget_appearance_respects_filled_signature(
    certified, credentials, level, field_name
):
    def change(doc):
        field = _field(doc, field_name)
        kids = entry(doc, field, "Kids")
        widget = resolve(doc, kids.items[0]) if isinstance(kids, PdfArray) else field
        return _set(widget, "AP", PdfDictionary())

    updated = _edit(certified[level], change)
    result = _validate(updated, credentials)
    allowed = field_name == "Text" and level >= 2
    assert result.status == (
        ValidationStatus.VALID if allowed else ValidationStatus.INVALID
    ), result.errors


def test_revision_checks_honor_resource_limits(certified):
    from aspose_pdf.engine.signature_revisions import revisions
    from aspose_pdf.exceptions import PdfResourceLimitException
    from aspose_pdf.load_limits import PdfLoadLimits, _LoadBudget

    data = certified[2]
    limits = PdfLoadLimits(max_xref_sections=1)
    with pytest.raises(PdfResourceLimitException):
        revisions(data, len(data), budget=_LoadBudget(limits))


@pytest.mark.parametrize("level", [1, 2, 3])
def test_public_form_fill_incremental_round_trip(certified, credentials, level):
    document = Document(io.BytesIO(certified[level]))
    document.form["Text"].value = "Public fill"
    buffer = io.BytesIO()
    document.save(buffer, incremental=True)
    assert buffer.getvalue().startswith(certified[level])
    result = _validate(buffer.getvalue(), credentials)
    assert result.status == (
        ValidationStatus.INVALID if level == 1 else ValidationStatus.VALID
    ), result.errors


@pytest.mark.parametrize("level", [1, 2, 3])
def test_widget_stacking_order_is_an_annotation_change(certified, credentials, level):
    def change(doc):
        page = _page(doc)
        entry(doc, page, "Annots").items.reverse()
        return [page]

    result = _validate(_edit(certified[level], change), credentials)
    assert result.status == (
        ValidationStatus.VALID if level == 3 else ValidationStatus.INVALID
    ), result.errors
