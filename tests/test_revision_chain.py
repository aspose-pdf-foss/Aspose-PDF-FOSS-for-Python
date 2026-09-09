"""A revision chain: what each link may say, and who owns an object.

Three faults, one rule between them -- **the newest revision to mention an
object owns it, and a revision may only say what is true of the file it is
in**.

* An incremental update always appended a *classic* ``xref`` table, even when
  the revision it chained to ended in a cross-reference **stream**. ISO 32000-1
  7.5.8.4: a classic trailer's ``/Prev`` may only name a classic table, so the
  chain pointed at something no reader can read as one. Everything before the
  update was lost, and only a reader that gave up and rescanned the file found
  the pages again.
* Reading such a chain, an object stream cached **every** member it held,
  including numbers a later revision had re-issued as plain objects -- so the
  stale copy answered for the rest of the document's life. The precedence rule
  was applied to the plain entries and not to these.
* A **full** save copied the whole source trailer, ``/Prev`` and all. A full
  rewrite has no previous revision, so the offset pointed past the end of the
  file it was written into.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document


def _authored(pages: int = 2) -> bytes:
    document = Document()
    for _ in range(pages):
        document.pages.add()
    document.pages[0].add_text("base text", x=72, y=700)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _text(data: bytes) -> str:
    return " | ".join(
        " ".join(page.extract_text().split())
        for page in Document(io.BytesIO(data)).pages
    )


def _saved(document: Document, *, incremental: bool = False) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer, incremental=incremental)
    return buffer.getvalue()


def _with_xref_stream(_ignored: bytes = b"") -> bytes:
    """A two-page document whose objects live in an object stream.

    Written by hand rather than by another library, so the test runs wherever
    the suite does: the catalog, the page tree and *both pages* sit inside an
    object stream, and the file is indexed by a cross-reference stream. An
    update to it therefore has to lift a page out of the stream, which is the
    case that used to answer from the stale copy.
    """
    members = {
        2: b"<< /Type /Catalog /Pages 3 0 R >>",
        3: b"<< /Type /Pages /Kids [4 0 R 5 0 R] /Count 2 >>",
        4: (
            b"<< /Type /Page /Parent 3 0 R /MediaBox [0 0 200 200] "
            b"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 "
            b"/BaseFont /Helvetica >> >> >> /Contents 6 0 R >>"
        ),
        5: b"<< /Type /Page /Parent 3 0 R /MediaBox [0 0 200 200] >>",
    }
    pairs, bodies, cursor = [], bytearray(), 0
    for number, body in members.items():
        pairs.append(f"{number} {cursor}")
        bodies += body + b"\n"
        cursor = len(bodies)
    header = (" ".join(pairs) + "\n").encode("ascii")
    objstm = header + bytes(bodies)
    content = b"BT /F1 12 Tf 20 100 Td (base text) Tj ET"

    raw = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}

    def emit(number: int, body: bytes) -> None:
        offsets[number] = len(raw)
        raw.extend(b"%d 0 obj\n" % number + body + b"\nendobj\n")

    emit(
        1,
        b"<< /Type /ObjStm /N %d /First %d /Length %d >>\nstream\n"
        % (len(members), len(header), len(objstm))
        + objstm
        + b"\nendstream",
    )
    emit(
        6,
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
    )

    # /W [1 2 1]: type, then a two-byte offset-or-stream-number, then a
    # one-byte generation-or-index.
    entries = bytearray(b"\x00\x00\x00\xff")  # object 0, the free-list head
    entries += b"\x01" + offsets[1].to_bytes(2, "big") + b"\x00"
    for index, number in enumerate(members):
        entries += b"\x02" + (1).to_bytes(2, "big") + bytes([index])
        assert number  # the numbers are 2..5, contiguous with the rest
    entries += b"\x01" + offsets[6].to_bytes(2, "big") + b"\x00"
    xref_offset = len(raw)
    entries += b"\x01" + xref_offset.to_bytes(2, "big") + b"\x00"
    raw.extend(
        b"7 0 obj\n<< /Type /XRef /Size 8 /W [ 1 2 1 ] /Index [ 0 8 ] "
        b"/Root 2 0 R /Length %d >>\nstream\n"
        % len(entries)
        + bytes(entries)
        + b"\nendstream\nendobj\nstartxref\n"
        + str(xref_offset).encode("ascii")
        + b"\n%%EOF\n"
    )
    return bytes(raw)


def _free_object_in_a_new_revision(data: bytes, number: int) -> bytes:
    """Append a revision whose cross-reference stream marks *number* free."""
    previous = int(data[data.rfind(b"startxref") + 9 :].split()[0])
    stream_number = 8
    entries = (
        b"\x00"
        + (0).to_bytes(2, "big")
        + b"\xff"  # the freed object
        + b"\x01"  # and this stream itself
    )
    raw = bytearray(data)
    xref_offset = len(raw)
    entries = (
        b"\x00"
        + (0).to_bytes(2, "big")
        + b"\xff"
        + b"\x01"
        + xref_offset.to_bytes(2, "big")
        + b"\x00"
    )
    raw.extend(
        b"%d 0 obj\n<< /Type /XRef /Size %d /W [ 1 2 1 ] /Index [ %d 1 %d 1 ] "
        b"/Prev %d /Root 2 0 R /Length %d >>\nstream\n"
        % (
            stream_number,
            stream_number + 1,
            number,
            stream_number,
            previous,
            len(entries),
        )
        + entries
        + b"\nendstream\nendobj\nstartxref\n"
        + str(xref_offset).encode("ascii")
        + b"\n%%EOF\n"
    )
    return bytes(raw)


def _tail_kind(data: bytes) -> str:
    """Whether the last revision ends in a classic table or a stream."""
    start = int(data[data.rfind(b"startxref") + 9 :].split()[0])
    return "table" if data[start : start + 4] == b"xref" else "stream"


# --- an update matches the section it chains to -----------------------------


def test_an_update_to_a_table_file_appends_a_table():
    document = Document(io.BytesIO(_authored()))
    document.pages[1].add_text("appended", x=72, y=600)
    updated = _saved(document, incremental=True)
    assert _tail_kind(updated) == "table"
    assert _text(updated) == "base text | appended"


def test_an_update_to_a_stream_file_appends_a_stream():
    source = _with_xref_stream()
    assert _tail_kind(source) == "stream"
    document = Document(io.BytesIO(source))
    document.pages[1].add_text("appended", x=72, y=600)
    updated = _saved(document, incremental=True)
    assert _tail_kind(updated) == "stream"
    assert _text(updated) == "base text | appended"


def test_the_appended_stream_keeps_the_original_bytes_in_front():
    source = _with_xref_stream()
    document = Document(io.BytesIO(source))
    document.pages[1].add_text("appended", x=72, y=600)
    updated = _saved(document, incremental=True)
    assert updated.startswith(source)  # an update appends, it never rewrites


def test_the_appended_stream_is_not_compressed_or_enciphered():
    # 7.5.8.2: a reader has to read the cross-reference stream before it knows
    # how to decipher anything, so it is exempt -- and it is written plain.
    source = _with_xref_stream()
    document = Document(io.BytesIO(source))
    document.pages[1].add_text("appended", x=72, y=600)
    tail = _saved(document, incremental=True)[len(source) :]
    xref = tail[tail.rfind(b"/Type /XRef") :]
    assert b"/Filter" not in xref.split(b"stream")[0]


def test_the_appended_stream_lists_itself():
    # 7.5.8.1: a cross-reference stream has an entry for itself, or nothing in
    # the file says where the object a reader just parsed actually lives.
    source = _with_xref_stream()
    document = Document(io.BytesIO(source))
    document.pages[1].add_text("appended", x=72, y=600)
    tail = _saved(document, incremental=True)[len(source) :]
    header = tail[tail.rfind(b"/Type /XRef") :].split(b"stream")[0]
    own_number = int(
        tail[tail.rfind(b"/Type /XRef") :].split(b"0 obj")[0].rsplit(b"\n", 1)[-1] or 0
    ) or int(tail.split(b" 0 obj")[-2].rsplit(b"\n", 1)[-1])
    index = header.split(b"/Index [")[1].split(b"]")[0].split()
    covered = set()
    for start, count in zip(index[::2], index[1::2]):
        covered.update(range(int(start), int(start) + int(count)))
    assert own_number in covered


def test_a_revision_that_frees_an_object_is_obeyed():
    # Our own updates only add and modify, so this shape only arrives from
    # another writer: a later revision marks an object free, and the older
    # definition must not come back.
    source = _with_xref_stream()
    freed = _free_object_in_a_new_revision(source, 6)
    assert _text(source).startswith("base text")
    assert _text(freed) == " | "  # page 1 lost the contents that were freed


def test_an_independent_reader_agrees_about_the_updated_stream_file():
    pikepdf = pytest.importorskip("pikepdf")
    source = _with_xref_stream()
    document = Document(io.BytesIO(source))
    document.pages[1].add_text("appended", x=72, y=600)
    updated = _saved(document, incremental=True)
    with pikepdf.open(io.BytesIO(updated)) as pdf:
        assert len(pdf.pages) == 2
        assert b"appended" in bytes(pdf.pages[1].Contents.read_bytes())


# --- the newest revision owns the object ------------------------------------


def test_the_fixture_really_keeps_its_pages_inside_an_object_stream():
    # Otherwise the tests below would pass without exercising anything: the
    # case that broke is a page being lifted *out* of a stream by an update.
    source = _with_xref_stream()
    assert b"/Type /ObjStm" in source
    assert _tail_kind(source) == "stream"
    # Page 2 appears only inside the (uncompressed) object stream, never as a
    # plain object of its own.
    assert b"5 0 obj" not in source
    assert _text(source) == "base text | "


def test_an_object_lifted_out_of_a_stream_is_read_from_the_newer_revision():
    # The page lives inside an object stream in the source and is re-issued as
    # a plain object by the update. Inflating the stream must not put the old
    # one back.
    source = _with_xref_stream()
    document = Document(io.BytesIO(source))
    document.pages[1].add_text("appended", x=72, y=600)
    assert _text(_saved(document, incremental=True)) == "base text | appended"


def test_the_older_copy_is_still_there_and_still_ignored():
    source = _with_xref_stream()
    document = Document(io.BytesIO(source))
    document.pages[1].add_text("appended", x=72, y=600)
    updated = _saved(document, incremental=True)
    # The whole original, object stream included, is still in the file.
    assert updated.startswith(source)
    assert _text(updated).endswith("appended")


def test_a_chain_of_updates_reads_as_its_last_revision():
    data = _authored(pages=1)
    for index in range(1, 4):
        document = Document(io.BytesIO(data))
        document.pages[0].add_text(f"rev{index}", x=72, y=700 - 20 * index)
        data = _saved(document, incremental=True)
    assert _text(data) == "base text rev1 rev2 rev3"


# --- a full save says only what is true of itself ---------------------------


def test_a_full_save_carries_no_prev():
    data = _authored(pages=1)
    document = Document(io.BytesIO(data))
    document.pages[0].add_text("more", x=72, y=650)
    once = _saved(document, incremental=True)
    # Reload the updated file and write it out whole: there is no revision
    # before this one, so nothing may claim there is.
    rewritten = _saved(Document(io.BytesIO(once)))
    trailer = rewritten[rewritten.rfind(b"trailer") : rewritten.rfind(b"startxref")]
    assert b"/Prev" not in trailer
    assert b"/Root" in trailer


def test_a_full_save_drops_the_keys_of_the_section_it_came_from():
    source = _with_xref_stream()
    rewritten = _saved(Document(io.BytesIO(source)))
    if _tail_kind(rewritten) != "table":
        pytest.skip("this build writes a cross-reference stream")
    trailer = rewritten[rewritten.rfind(b"trailer") : rewritten.rfind(b"startxref")]
    for key in (b"/Type", b"/W", b"/Index", b"/Filter", b"/DecodeParms", b"/XRefStm"):
        assert key not in trailer


def test_a_full_save_of_a_chained_file_needs_no_rescan(caplog):
    import logging

    data = _authored(pages=1)
    for index in range(1, 4):
        document = Document(io.BytesIO(data))
        document.pages[0].add_text(f"rev{index}", x=72, y=700 - 20 * index)
        data = _saved(document, incremental=True)
    rewritten = _saved(Document(io.BytesIO(data)))
    with caplog.at_level(logging.WARNING):
        assert _text(rewritten) == "base text rev1 rev2 rev3"
    assert "reconstruction" not in caplog.text


def test_the_size_a_full_save_writes_is_its_own():
    data = _authored(pages=1)
    document = Document(io.BytesIO(data))
    document.pages.add()
    rewritten = _saved(document)
    trailer = rewritten[rewritten.rfind(b"trailer") : rewritten.rfind(b"startxref")]
    assert b"/Size" in trailer
