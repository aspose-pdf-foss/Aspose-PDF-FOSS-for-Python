"""Public byte-preserving incremental save (``Document.save(incremental=True)``).

The original file bytes must be preserved verbatim as a prefix, only objects
added or modified since load may be appended, and the result must reload as a
valid PDF. Because the signed revision of a signed document falls entirely
within the preserved prefix, its signature stays intact.
"""

import io
import re

import pytest

from aspose_pdf import Document
from aspose_pdf.exceptions import PdfSecurityException


def _base_pdf(page_count: int = 2) -> bytes:
    doc = Document()
    for _ in range(page_count):
        doc.pages.add()
    doc.pages[0].draw_rectangle(10, 10, 40, 40, fill_color=(1, 0, 0))
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def _appended_object_numbers(base: bytes, whole: bytes) -> list[int]:
    appended = whole[len(base):]
    return sorted(int(m.group(1)) for m in re.finditer(rb"(\d+) 0 obj", appended))


def test_incremental_save_preserves_original_bytes_and_reloads():
    base = _base_pdf()

    doc = Document()
    doc.load_from(base)
    doc.pages.add()  # a third page: new objects, mutated page tree
    out = io.BytesIO()
    doc.save(out, incremental=True)
    whole = out.getvalue()

    # The original bytes are an exact prefix (nothing before them changed).
    assert whole[: len(base)] == base
    assert len(whole) > len(base)
    # Exactly one revision was appended.
    assert whole.count(b"%%EOF") == base.count(b"%%EOF") + 1

    reloaded = Document()
    reloaded.load_from(whole)
    assert len(reloaded.pages) == 3


def test_incremental_save_without_changes_returns_original_bytes():
    base = _base_pdf()

    doc = Document()
    doc.load_from(base)
    out = io.BytesIO()
    doc.save(out, incremental=True)

    # No edits: nothing is appended, the output is the original file byte-for-byte.
    assert out.getvalue() == base


def test_incremental_save_emits_only_changed_objects():
    base = _base_pdf(page_count=3)

    doc = Document()
    doc.load_from(base)
    # Draw on the last page only: mutate that page dict + add one content stream.
    doc.pages[2].draw_rectangle(5, 5, 10, 10, fill_color=(0, 1, 0))
    out = io.BytesIO()
    doc.save(out, incremental=True)
    whole = out.getvalue()

    appended = _appended_object_numbers(base, whole)
    # Precisely two objects: the edited page dictionary and its new content stream.
    # The other pages and the catalog stay in the preserved prefix.
    assert len(appended) == 2
    assert whole[: len(base)] == base


def test_incremental_save_metadata_round_trips():
    base = _base_pdf()

    doc = Document()
    doc.load_from(base)
    info = doc.info
    info["Title"] = "Incremental Title"
    doc.info = info
    out = io.BytesIO()
    doc.save(out, incremental=True)
    whole = out.getvalue()

    assert whole[: len(base)] == base
    reloaded = Document()
    reloaded.load_from(whole)
    assert reloaded.info.get("Title") == "Incremental Title"


def test_incremental_save_to_path(tmp_path):
    base = _base_pdf()
    target = tmp_path / "out.pdf"

    doc = Document()
    doc.load_from(base)
    doc.pages[0].draw_rectangle(1, 1, 2, 2, fill_color=(0, 0, 1))
    doc.save(target, incremental=True)

    written = target.read_bytes()
    assert written[: len(base)] == base
    assert len(written) > len(base)


def test_incremental_save_rejects_encrypted_document():
    base = _base_pdf()
    doc = Document()
    doc.load_from(base)
    doc._engine_pdf.encrypted = True

    with pytest.raises(PdfSecurityException):
        doc.save(io.BytesIO(), incremental=True)


def test_incremental_save_scratch_document_falls_back_to_full_save():
    # A document built from scratch has no base revision; incremental save
    # produces a normal, single-revision full write.
    doc = Document()
    doc.pages.add()
    out = io.BytesIO()
    doc.save(out, incremental=True)
    whole = out.getvalue()

    assert whole.startswith(b"%PDF")
    assert whole.count(b"%%EOF") == 1
    reloaded = Document()
    reloaded.load_from(whole)
    assert len(reloaded.pages) == 1


def test_incremental_save_preserves_signed_byte_range():
    from aspose_pdf.engine.signing import SigningUtils
    from aspose_pdf.engine.simple_pdf import SimplePdf

    pdf = SimplePdf()
    pdf.pages = [(0, 0, 200, 200)]
    pdf.page_contents = [b"Signed content"]
    cert, key = SigningUtils.create_self_signed_cert()
    pdf.signing_creds = (cert, key)
    signed = pdf.to_bytes()

    byte_range = re.search(rb"/ByteRange \[(\d+) (\d+) (\d+) (\d+)\]", signed)
    assert byte_range is not None
    _, _, start2, len2 = map(int, byte_range.groups())
    signed_span_end = start2 + len2

    doc = Document()
    doc.load_from(signed)
    doc.pages[0].draw_rectangle(1, 1, 5, 5, fill_color=(0, 0, 0))
    out = io.BytesIO()
    doc.save(out, incremental=True)
    whole = out.getvalue()

    # The entire signed revision is preserved verbatim, so the digest computed
    # over its ByteRange is unchanged and the signature stays valid.
    assert signed_span_end <= len(signed)
    assert whole[: len(signed)] == signed
    assert len(whole) > len(signed)
    assert whole.count(b"%%EOF") == signed.count(b"%%EOF") + 1
    # The signature dictionary is untouched inside the preserved prefix.
    assert whole.count(b"/ByteRange [") == signed.count(b"/ByteRange [")
