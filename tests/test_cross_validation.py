"""What an independent implementation makes of the files this library writes.

Every other test in this suite asks whether the library agrees with itself.
These ask whether qpdf agrees — a parser written by other people, from the
specification rather than from this code, which is the only way to catch a
shared misreading. It is the check the project's own documentation tells users
to run, kept honest by running it here.

qpdf reaches us through pikepdf, which bundles it. The module skips when
pikepdf is absent, so a plain checkout stays green; CI installs it. Its
``check_pdf_syntax`` is qpdf's own consistency pass over the object graph,
cross-reference table and stream lengths, and its ``save`` is a full rewrite
that has to re-read everything first.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

pytest.importorskip("pikepdf")

import pikepdf

from aspose_pdf import Document, OptimizationOptions
from aspose_pdf.outlines import OutlineItem

_PDFA_ID = "http://www.aiim.org/pdfa/ns/id/"
_PDFUA_ID = "http://www.aiim.org/pdfua/ns/id/"


def _saved(document: Document) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _reloaded(document: Document) -> Document:
    return Document(io.BytesIO(_saved(document)))


def _page_of_everything() -> Document:
    """A document exercising the constructs each feature area emits."""
    document = Document()
    page = document.pages.add()
    page.add_text("Heading", 60, 720, font_size=20)
    page.add_text("Body text that runs on for a while.", 60, 700, font_size=11)
    document.outlines.add(OutlineItem("Chapter one", 0))
    document.form.add_text_field("nickname", 0, (60, 100, 260, 130), value="typed")
    document.add_attachment("notes.txt", b"an attachment", mime="text/plain")
    return _reloaded(document)


def _page_content(page) -> bytes:
    """The page's content bytes, whether ``/Contents`` is one stream or many."""
    contents = page.Contents
    if isinstance(contents, pikepdf.Array):
        return b"\n".join(bytes(part.read_bytes()) for part in contents)
    return bytes(contents.read_bytes())


def _qpdf_problems(data: bytes, password: str = "") -> list[str]:
    """qpdf's own complaints about *data*, if any."""
    with pikepdf.open(io.BytesIO(data), password=password) as pdf:
        return list(pdf.check_pdf_syntax())


def _qpdf_rewrite(data: bytes, password: str = "") -> bytes:
    """Re-save through qpdf, which has to re-read every object to do it."""
    out = io.BytesIO()
    with pikepdf.open(io.BytesIO(data), password=password) as pdf:
        pdf.save(out)
    return out.getvalue()


# ---------------------------------------------------------------------------
# The shapes this library writes
# ---------------------------------------------------------------------------


def _documents() -> dict[str, bytes]:
    """One saved document per feature area, built the way a caller would."""
    made: dict[str, bytes] = {}

    made["plain"] = _saved(_page_of_everything())

    layered = _page_of_everything()
    watermark = layered.layers.add("Watermark")
    with layered.pages[0].layer(watermark):
        layered.pages[0].add_text("DRAFT", 100, 400, font_size=40)
    layered.layers["Watermark"].set_usage(printing=False)
    layered.layers.save_configuration("Clean copy")
    made["layers"] = _saved(layered)

    tagged = _page_of_everything()
    tagged.auto_tag()
    made["tagged"] = _saved(tagged)

    for level in ("2b", "4", "4e"):
        converted = _page_of_everything()
        converted.convert_to_pdfa(level)
        made[f"pdfa-{level}"] = _saved(converted)

    for part in (1, 2):
        accessible = _page_of_everything()
        accessible.convert_to_pdfua(part=part, title="Doc", auto_tag=True)
        made[f"pdfua-{part}"] = _saved(accessible)

    optimized = _page_of_everything()
    optimized.optimize(OptimizationOptions(subset_fonts=True, compress_fonts=True))
    made["optimized"] = _saved(optimized)

    # A title makes the string half of the encryption visible: strings and
    # streams are enciphered separately, and only one of them shows up in the
    # page content.
    for algorithm in ("RC4", "AES-128", "AES-256"):
        sealed = _page_of_everything()
        sealed.info["Title"] = "Encrypted and structured"
        sealed.encrypt("u", "owner", algorithm=algorithm)
        made[f"encrypted-{algorithm}"] = _saved(sealed)

    packed = _page_of_everything()
    packed.info["Title"] = "Encrypted and structured"
    packed.optimize(OptimizationOptions(use_object_streams=True))
    packed.encrypt("u", "owner", algorithm="AES-128")
    made["encrypted-objstm"] = _saved(packed)

    signed = _page_of_everything()
    from aspose_pdf.engine.signing import SigningUtils

    cert, key = SigningUtils.create_self_signed_cert()
    engine = signed._engine_pdf
    engine.signing_creds = (cert, key)
    engine.signature = {"Name": "Signature1", "Reason": "Approved"}
    made["signed"] = _saved(signed)

    return made


@pytest.fixture(scope="module")
def documents() -> dict[str, bytes]:
    return _documents()


@pytest.mark.parametrize(
    "name",
    [
        "plain",
        "layers",
        "tagged",
        "pdfa-2b",
        "pdfa-4",
        "pdfa-4e",
        "pdfua-1",
        "pdfua-2",
        "optimized",
        "signed",
    ],
)
def test_qpdf_finds_nothing_to_complain_about(documents, name):
    assert _qpdf_problems(documents[name]) == []


@pytest.mark.parametrize(
    "name", ["plain", "layers", "tagged", "pdfa-4", "pdfua-2", "optimized"]
)
def test_qpdf_can_rewrite_the_file(documents, name):
    rewritten = _qpdf_rewrite(documents[name])
    assert rewritten.startswith(b"%PDF-")
    # And what it wrote is itself sound.
    assert _qpdf_problems(rewritten) == []


# ---------------------------------------------------------------------------
# What qpdf reads back out
# ---------------------------------------------------------------------------


def test_the_structure_a_saved_document_carries_is_the_one_we_wrote(documents):
    with pikepdf.open(io.BytesIO(documents["plain"])) as pdf:
        assert len(pdf.pages) == 1
        assert "/Outlines" in pdf.Root
        assert "/AcroForm" in pdf.Root
        names = pdf.Root.Names.EmbeddedFiles.Names
        assert str(names[0]) == "notes.txt"


def test_every_page_reaches_a_resource_dictionary(documents):
    # /Resources is required and inheritable; qpdf repairs a page that has
    # neither its own nor an inherited one, and says so.
    for name, data in documents.items():
        password = "u" if name.startswith("encrypted") else ""
        assert not any(
            "Resources is missing" in problem
            for problem in _qpdf_problems(data, password)
        ), name


def test_layers_survive_an_independent_read(documents):
    with pikepdf.open(io.BytesIO(documents["layers"])) as pdf:
        properties = pdf.Root.OCProperties
        assert [str(group.Name) for group in properties.OCGs] == ["Watermark"]
        assert [str(config.Name) for config in properties.Configs] == ["Clean copy"]
        events = [str(entry.Event) for entry in properties.D.AS]
        assert "/Print" in events


@pytest.mark.parametrize(
    ("name", "version", "expected"),
    [
        ("pdfa-2b", "1.7", {"part": "2", "conformance": "B"}),
        ("pdfa-4", "2.0", {"part": "4", "rev": "2020"}),
        ("pdfa-4e", "2.0", {"part": "4", "rev": "2020", "conformance": "E"}),
    ],
)
def test_pdfa_identification_reads_back_through_another_xmp_parser(
    documents, name, version, expected
):
    with pikepdf.open(io.BytesIO(documents[name])) as pdf:
        assert pdf.pdf_version == version
        meta = pdf.open_metadata()
        found = {
            key.split("}")[1]: value
            for key, value in meta.items()
            if key.startswith("{" + _PDFA_ID)
        }
    assert found == expected


def test_pdfua_identification_reads_back_through_another_xmp_parser(documents):
    with pikepdf.open(io.BytesIO(documents["pdfua-2"])) as pdf:
        assert pdf.pdf_version == "2.0"
        meta = pdf.open_metadata()
        found = {
            key.split("}")[1]: value
            for key, value in meta.items()
            if key.startswith("{" + _PDFUA_ID)
        }
        assert found == {"part": "2", "rev": "2024"}
        namespaces = [str(entry.NS) for entry in pdf.Root.StructTreeRoot.Namespaces]
        assert "http://iso.org/pdf2/ssn" in namespaces


def test_a_tagged_document_presents_a_structure_tree(documents):
    with pikepdf.open(io.BytesIO(documents["tagged"])) as pdf:
        assert bool(pdf.Root.MarkInfo.Marked) is True
        assert len(pdf.Root.StructTreeRoot.K) > 0


# ---------------------------------------------------------------------------
# Encryption: the writer enciphers, and qpdf has to be able to undo it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["encrypted-RC4", "encrypted-AES-128", "encrypted-AES-256", "encrypted-objstm"],
)
def test_qpdf_opens_what_the_writer_enciphered(documents, name):
    """Every string and stream has to come back, from another implementation.

    Encryption is per object and per value; an entry left in the clear, or
    enciphered twice, is invisible in our own round trip -- we would make the
    same mistake reading it back -- and obvious here.
    """
    data = documents[name]
    assert _qpdf_problems(data, "u") == []
    with pikepdf.open(io.BytesIO(data), password="u") as pdf:
        assert str(pdf.docinfo["/Title"]) == "Encrypted and structured"
        assert [str(f.T) for f in pdf.Root.AcroForm.Fields] == ["nickname"]
        assert str(pdf.Root.Names.EmbeddedFiles.Names[0]) == "notes.txt"
        assert "/Outlines" in pdf.Root
        assert b"Body text" in _page_content(pdf.pages[0])


def test_a_qpdf_rewrite_settles_after_one_save_here():
    """A foreign numbering must stop moving once we have written it once.

    Outlines and attachments are rebuilt from the model on every save, so a
    document arriving with someone else's object numbers -- and possibly a
    nested name tree -- has to be renumbered once and then left alone. Growing
    on every round trip is what this checks against.
    """
    document = _page_of_everything()
    rewritten = _qpdf_rewrite(_saved(document))

    first = _saved(Document(io.BytesIO(rewritten)))
    second = _saved(Document(io.BytesIO(first)))

    assert second == first
    with pikepdf.open(io.BytesIO(second)) as pdf:
        assert "/Outlines" in pdf.Root
        assert str(pdf.Root.Names.EmbeddedFiles.Names[0]) == "notes.txt"
        assert _qpdf_problems(second) == []


def test_qpdf_reads_an_appended_revision_without_repairing_it():
    """An appended cross-reference section is read by offset, not by line.

    Entries are a fixed twenty bytes, so one byte too many puts every entry
    after the first in a subsection out of step. Our own parser scans rather
    than multiplies and never noticed; qpdf reports the file as damaged and
    reconstructs the table.
    """
    document = _page_of_everything()
    document.pages[0].add_text("Appended later", 60, 660, font_size=11)
    out = io.BytesIO()
    document.save(out, incremental=True)
    whole = out.getvalue()

    assert whole.count(b"%%EOF") >= 2
    assert _qpdf_problems(whole) == []
    with pikepdf.open(io.BytesIO(whole)) as pdf:
        assert b"Appended later" in _page_content(pdf.pages[0])


@pytest.mark.parametrize("algorithm", ["RC4", "AES-128", "AES-256"])
def test_qpdf_reads_an_encrypted_incremental_revision(algorithm):
    """An appended revision has to be enciphered like the rest of the file.

    The original bytes stay verbatim, so qpdf is reading two revisions written
    at different times under one key -- exactly the case where an object
    appended in the clear, or under the wrong object number, shows up.
    """
    base = _page_of_everything()
    base.info["Title"] = "Encrypted and structured"
    base.encrypt("u", "owner", algorithm=algorithm)
    sealed = _saved(base)

    document = Document(io.BytesIO(sealed), password="u")
    document.pages[0].add_text("Appended later", 60, 660, font_size=11)
    out = io.BytesIO()
    document.save(out, incremental=True)
    whole = out.getvalue()

    assert whole[: len(sealed)] == sealed
    assert _qpdf_problems(whole, "u") == []
    with pikepdf.open(io.BytesIO(whole), password="u") as pdf:
        assert str(pdf.docinfo["/Title"]) == "Encrypted and structured"
        content = _page_content(pdf.pages[0])
        assert b"Appended later" in content
        assert b"Body text" in content


def test_a_qpdf_decrypted_rewrite_still_reads_back_here(documents):
    """The other direction: qpdf strips the encryption, we read the result."""
    rewritten = _qpdf_rewrite(documents["encrypted-AES-256"], "u")
    document = Document(io.BytesIO(rewritten))

    assert document.page_count == 1
    assert [field.name for field in document.form] == ["nickname"]


# ---------------------------------------------------------------------------
# Signatures: an independent reader has to agree the signature covers the file
# ---------------------------------------------------------------------------


def test_a_signature_covers_the_whole_file_by_an_independent_reading(documents):
    """The byte-range gap has to match the /Contents string exactly.

    Excluding only the hex digits leaves the delimiters signed, which verifies
    but leaves a validator unable to say the signature covers the document.
    """
    data = documents["signed"]
    with pikepdf.open(io.BytesIO(data)) as pdf:
        (field,) = [f for f in pdf.Root.AcroForm.Fields if str(f.FT) == "/Sig"]
        signature = field.V
        start, length1, start2, length2 = (int(v) for v in signature.ByteRange)

    assert start == 0
    assert data[length1 : length1 + 1] == b"<"
    assert data[start2 - 1 : start2] == b">"
    assert start2 + length2 == len(data)


def test_a_signed_document_keeps_the_structure_it_was_signed_around(documents):
    with pikepdf.open(io.BytesIO(documents["signed"])) as pdf:
        assert "/Outlines" in pdf.Root
        titles = {str(f.T) for f in pdf.Root.AcroForm.Fields}
        assert {"nickname", "Signature1"} <= titles


# ---------------------------------------------------------------------------
# Reading foreign files: what qpdf writes, we read
# ---------------------------------------------------------------------------


def test_a_qpdf_written_file_reads_back_with_the_same_page_text(documents, tmp_path):
    """The other direction: qpdf rewrites the file, and we read its output."""
    rewritten = _qpdf_rewrite(documents["plain"])
    target = Path(tmp_path) / "qpdf.pdf"
    target.write_bytes(rewritten)

    from aspose_pdf.facades import PdfExtractor

    extractor = PdfExtractor()
    extractor.bind_pdf(str(target))
    extractor.extract_text()

    assert "Heading" in extractor.get_text()
    assert "Body text" in extractor.get_text()


def test_layers_still_resolve_after_a_qpdf_rewrite(documents, tmp_path):
    rewritten = _qpdf_rewrite(documents["layers"])
    document = Document(io.BytesIO(rewritten))

    assert document.layers.names() == ["Watermark"]
    assert [c.name for c in document.layers.configurations] == ["", "Clean copy"]
    assert not document.layers.resolve("Print")["Watermark"]


# ---------------------------------------------------------------------------
# The samples CI hands to veraPDF
# ---------------------------------------------------------------------------


def test_the_conformance_samples_script_writes_one_file_per_level(tmp_path):
    """CI feeds these to veraPDF, so they have to exist and be sound first."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    try:
        from write_conformance_samples import write_samples
    finally:
        sys.path.pop(0)

    written = write_samples(Path(tmp_path) / "samples")

    names = sorted(path.name for path in written)
    assert names == [
        "pdfa-1b.pdf",
        "pdfa-2b.pdf",
        "pdfa-2u.pdf",
        "pdfa-3b.pdf",
        "pdfa-4.pdf",
        "pdfa-4e.pdf",
        "pdfa-4f.pdf",
        "pdfua-1.pdf",
        "pdfua-2.pdf",
    ]
    for path in written:
        assert _qpdf_problems(path.read_bytes()) == [], path.name
