"""Saving a document you did not change gives the same document back.

Outlines and attachments are not held in the COS graph between loads -- they
are read into the model and rebuilt from it on every save. Rebuilding used to
claim fresh object numbers each time, so a document that was merely opened and
saved came back four objects and several hundred bytes larger, trailing the
previous copy behind as garbage, and did it again on every round after that. An
incremental save was worse: an object rebuilt under a new number can never
compare equal to the one it replaces, so the whole tree was appended every
time.

The fix is to write each rebuild back into the object numbers it already
occupies. That makes these tests possible to state at all: an unchanged save is
byte-for-byte the file it started from, and an unchanged *incremental* save
appends nothing whatsoever.
"""

from __future__ import annotations

import io
import re

import pytest

from aspose_pdf import Document
from aspose_pdf.outlines import OutlineItem


def _saved(document: Document, **kwargs) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer, **kwargs)
    return buffer.getvalue()


def _object_numbers(data: bytes) -> list[int]:
    return sorted({int(m.group(1)) for m in re.finditer(rb"(\d+) 0 obj", data)})


def _base(*, nested: bool = False, attachments: int = 2) -> bytes:
    """A document with the two things that get rebuilt on every save."""
    document = Document()
    document.pages.add()
    document.pages.add()
    chapter = OutlineItem("Chapter one", 0)
    if nested:
        chapter.children.append(OutlineItem("Section A", 1))
        chapter.children.append(OutlineItem("Section B", 1))
    document.outlines.add(chapter)
    document.outlines.add(OutlineItem("Chapter two", 1))
    for index in range(attachments):
        document.add_attachment(
            f"file{index}.txt",
            f"payload {index}".encode(),
            mime="text/plain",
            description=f"notes {index}",
        )
    return _saved(document)


# ---------------------------------------------------------------------------
# Nothing changed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("nested", [False, True], ids=["flat", "nested"])
def test_saving_an_unchanged_document_reproduces_it_byte_for_byte(nested):
    base = _base(nested=nested)

    data = base
    for _ in range(3):
        data = _saved(Document(io.BytesIO(data)))
        assert data == base


@pytest.mark.parametrize("nested", [False, True], ids=["flat", "nested"])
def test_an_unchanged_incremental_save_appends_nothing(nested):
    """Not "a small revision" -- none at all, so the file is the original."""
    base = _base(nested=nested)
    whole = _saved(Document(io.BytesIO(base)), incremental=True)

    assert whole == base
    assert whole.count(b"%%EOF") == base.count(b"%%EOF")


def test_the_structure_still_reads_back_after_repeated_saves():
    """Stability is worth nothing if the tree is stable and wrong."""
    base = _base(nested=True)

    data = base
    for _ in range(3):
        data = _saved(Document(io.BytesIO(data)))

    document = Document(io.BytesIO(data))
    assert [item.title for item in document.outlines] == [
        "Chapter one",
        "Chapter two",
    ]
    assert [child.title for child in document.outlines[0].children] == [
        "Section A",
        "Section B",
    ]
    assert sorted(document.attachments) == ["file0.txt", "file1.txt"]
    assert document.attachments["file1.txt"] == b"payload 1"


def test_attachment_metadata_survives_a_round_trip():
    """It was read into the model and then ignored by the writer.

    The MIME type, description and dates went in, came back out of a load, and
    vanished on the next save -- which is also what kept an otherwise unchanged
    document from reproducing itself.
    """
    reopened = Document(io.BytesIO(_base()))
    again = Document(io.BytesIO(_saved(reopened)))

    specification = again.get_embedded_file("file0.txt")
    assert specification.mime_type == "text/plain"
    assert specification.description == "notes 0"


# ---------------------------------------------------------------------------
# Something did change
# ---------------------------------------------------------------------------


def test_adding_an_outline_item_costs_exactly_one_object():
    base = _base()
    document = Document(io.BytesIO(base))
    document.outlines.add(OutlineItem("Chapter three", 0))
    grown = _saved(document)

    assert len(_object_numbers(grown)) == len(_object_numbers(base)) + 1
    assert [item.title for item in Document(io.BytesIO(grown)).outlines] == [
        "Chapter one",
        "Chapter two",
        "Chapter three",
    ]


def test_adding_an_attachment_costs_exactly_one_pair_of_objects():
    """A file specification and the embedded stream it points at."""
    base = _base()
    document = Document(io.BytesIO(base))
    document.add_attachment("extra.txt", b"extra", mime="text/plain")
    grown = _saved(document)

    assert len(_object_numbers(grown)) == len(_object_numbers(base)) + 2
    assert sorted(Document(io.BytesIO(grown)).attachments) == [
        "extra.txt",
        "file0.txt",
        "file1.txt",
    ]


def test_removing_an_attachment_does_not_renumber_the_others():
    """Slots are kept per name, so the survivors stay where they were."""
    base = _base()
    document = Document(io.BytesIO(base))
    document.remove_attachment("file0.txt")
    shrunk = _saved(document)

    assert sorted(Document(io.BytesIO(shrunk)).attachments) == ["file1.txt"]
    assert len(_object_numbers(shrunk)) <= len(_object_numbers(base))


def test_an_incremental_save_appends_only_what_changed():
    base = _base()
    document = Document(io.BytesIO(base))
    document.add_attachment("extra.txt", b"extra", mime="text/plain")
    whole = _saved(document, incremental=True)

    assert whole[: len(base)] == base
    appended = _object_numbers(whole[len(base) :])
    # The two new objects, plus the catalog that now names them.
    assert len(appended) == 3
    assert sorted(Document(io.BytesIO(whole)).attachments) == [
        "extra.txt",
        "file0.txt",
        "file1.txt",
    ]


def test_collecting_slots_from_a_looping_outline_tree_terminates():
    """The collector walks the COS tree and owns its own termination.

    Loading refuses a cyclic outline tree outright, so no file reaches this
    through the front door -- but a graph can also be built in memory, and a
    walker that leans on a guard in a distant reader is one refactor away from
    running until a resource limit stops it.
    """
    from aspose_pdf.engine.cos import PdfDictionary, PdfName, PdfString

    engine = Document(io.BytesIO(_base()))._engine_pdf
    doc = engine._cos_doc
    root = engine._resolve(doc.trailer.mapping.get(PdfName("Root")))

    outlines = PdfDictionary({PdfName("Type"): PdfName("Outlines")})
    outlines_ref = doc.register_object(outlines)
    first = PdfDictionary({PdfName("Title"): PdfString(b"One")})
    first_ref = doc.register_object(first)
    second = PdfDictionary({PdfName("Title"): PdfString(b"Two")})
    second_ref = doc.register_object(second)
    outlines.mapping[PdfName("First")] = first_ref
    first.mapping[PdfName("Next")] = second_ref
    second.mapping[PdfName("Next")] = first_ref  # back to the start
    root.mapping[PdfName("Outlines")] = outlines_ref

    slots = engine._outline_slots(root)

    assert slots == [
        outlines_ref.object_number,
        first_ref.object_number,
        second_ref.object_number,
    ]


def test_stability_returns_after_the_shape_changes():
    """Reuse has to survive a reshaping, not just an untouched tree.

    Adding an item leaves the tree one slot short, so that save allocates. The
    saves after it have a full complement again and must go back to reproducing
    the file exactly -- otherwise the growth merely restarts.
    """
    document = Document(io.BytesIO(_base()))
    document.outlines.add(OutlineItem("Chapter three", 0))
    document.add_attachment("extra.txt", b"extra", mime="text/plain")
    grown = _saved(document)

    data = grown
    for _ in range(3):
        data = _saved(Document(io.BytesIO(data)))
        assert data == grown
