"""Content appended to a page does not inherit the state the page leaves.

A page's content stream can end with the graphics state changed: a ``cm`` it
never undid, a clipping path, a dash pattern, ``3 Tr``, a ``q`` it never
closed. Whatever is appended afterwards is drawn in that state (ISO 32000-1
8.4.2, 9.3.1), so text placed at a point landed somewhere else, a rectangle was
clipped away, a line came out dashed. pdfium and MuPDF showed every one of
those on the pages below before the page's content was saved and restored
around.

Each case is one small page with a single mark of the page's own (blue) and
one appended mark (red), so the assertions read the raster by colour. Dash
state restoration is also covered by the stroke-rendering regressions.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document
from aspose_pdf.engine import simple_pdf as simple_pdf_module
from aspose_pdf.engine.content_isolation import isolation_for
from aspose_pdf.engine.cos import PdfArray, PdfName
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.load_limits import _LoadBudget

SIZE = 100
OWN_MARK = b" 0 0 1 rg 1 1 4 4 re f"

# Content that leaves something behind, each followed by a mark of its own.
LEAKS = {
    "cm": b"2 0 0 2 0 0 cm",
    "unclosed_q": b"q 2 0 0 2 0 0 cm",
    "clip": b"0 0 6 6 re W n",
    "text_render_mode": b"BT 3 Tr ET",
    "open_text_object": b"BT 3 Tr",
    "stray_Q_first": b"Q 2 0 0 2 0 0 cm",
}


def _red(pixel) -> bool:
    return pixel[0] > 200 and pixel[1] < 90 and pixel[2] < 90


def _blue(pixel) -> bool:
    return pixel[2] > 200 and pixel[0] < 90 and pixel[1] < 90


def _page_bytes(content: bytes) -> bytes:
    return SimplePdf([(0, 0, SIZE, SIZE)], page_contents=[content]).to_bytes()


def _saved(document: Document, **kwargs) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer, **kwargs)
    return buffer.getvalue()


def _pixels(data: bytes, colour, *, password: str | None = None) -> set[tuple[int, int]]:
    raster = Document(data, password=password).pages[0].render(antialias=False)
    return {
        (x, y)
        for y in range(raster.height)
        for x in range(raster.width)
        if colour(raster.get_pixel(x, y))
    }


def _add_text(page) -> None:
    page.add_text("HH", 30, 40, font_size=30, color=(1, 0, 0))


def _draw_rectangle(page) -> None:
    page.draw_rectangle(40, 40, 20, 20, fill_color=(1, 0, 0), stroke_color=None)


def _draw_line(page) -> None:
    page.draw_line(20, 50, 80, 50, stroke_color=(1, 0, 0), line_width=4)


def _add_image(page) -> None:
    page.add_image(b"\xff\x00\x00", 40, 40, 20, 20, pixel_width=1, pixel_height=1)


def _layered_rectangle(page) -> None:
    layer = page._document.layers.add("Edits")
    with page.layer(layer):
        _draw_rectangle(page)


OPERATIONS = {
    "add_text": (_add_text, (30, 40, 70, 70)),
    "draw_rectangle": (_draw_rectangle, (40, 40, 60, 60)),
    "draw_line": (_draw_line, (18, 47, 82, 53)),
    "add_image": (_add_image, (40, 40, 60, 60)),
    "layer": (_layered_rectangle, (40, 40, 60, 60)),
}


def _expected_region(box) -> set[tuple[int, int]]:
    """Raster pixels of a page-space box (origin bottom-left)."""
    x0, y0, x1, y1 = box
    return {(x, SIZE - 1 - y) for x in range(x0, x1) for y in range(y0, y1)}


@pytest.mark.parametrize("operation", sorted(OPERATIONS))
@pytest.mark.parametrize("leak", sorted(LEAKS))
def test_appended_content_draws_where_it_was_put(leak: str, operation: str) -> None:
    original = _page_bytes(LEAKS[leak] + OWN_MARK)
    own_before = _pixels(original, _blue)

    document = Document(original)
    author, box = OPERATIONS[operation]
    author(document.pages[0])
    edited = _saved(document)

    red = _pixels(edited, _red)
    region = _expected_region(box)
    assert red, f"nothing appended was drawn on the {leak!r} page"
    assert red <= region | _neighbours(region)
    if operation in ("draw_rectangle", "add_image", "layer"):
        # A solid mark covers its whole box.
        inner = _expected_region((box[0] + 1, box[1] + 1, box[2] - 1, box[3] - 1))
        assert inner <= red
    if operation == "draw_line":
        assert {(x, SIZE - 1 - 50) for x in range(22, 78)} <= red
    # The page's own mark is drawn exactly as it was.
    assert _pixels(edited, _blue) == own_before


def _neighbours(region: set[tuple[int, int]]) -> set[tuple[int, int]]:
    return {(x + dx, y + dy) for x, y in region for dx in (-1, 0, 1) for dy in (-1, 0, 1)}


def test_a_page_that_leaves_nothing_behind_is_not_wrapped() -> None:
    content = b"q 2 0 0 2 0 0 cm 0 0 1 rg 1 1 4 4 re f Q"
    document = Document(_page_bytes(content))
    _draw_rectangle(document.pages[0])

    loaded = SimplePdf.from_bytes(_saved(document))
    contents = loaded._resolve(loaded._get_page_dict(0).mapping[PdfName("Contents")])
    streams = [loaded._resolve(item).content for item in contents.items]
    assert streams[0] == content
    assert len(streams) == 2
    assert not streams[1].startswith(b"Q")


def test_the_page_content_is_referenced_not_rewritten() -> None:
    source = SimplePdf.from_bytes(_page_bytes(b"2 0 0 2 0 0 cm" + OWN_MARK))
    first = source._get_page_dict(0).mapping[PdfName("Contents")]
    document = Document(_page_bytes(b"2 0 0 2 0 0 cm" + OWN_MARK))
    _draw_rectangle(document.pages[0])

    engine = document._engine_pdf
    contents = engine._resolve(engine._get_page_dict(0).mapping[PdfName("Contents")])
    assert isinstance(contents, PdfArray)
    assert len(contents.items) == 3
    assert engine._resolve(contents.items[0]).content == b"q\n"
    assert contents.items[1].object_number == first.object_number
    assert engine._resolve(contents.items[2]).content.startswith(b"Q\n")


def test_repeated_appends_wrap_the_page_once() -> None:
    document = Document(_page_bytes(b"2 0 0 2 0 0 cm" + OWN_MARK))
    page = document.pages[0]
    for _ in range(4):
        _draw_rectangle(page)
    content = document._engine_pdf.page_contents[0]
    assert content.startswith(b"q\n2 0 0 2 0 0 cm")
    assert content.count(b"Q\n") == 1 + 4  # one restore, one per rectangle

    # Saved and opened again, the wrapped page leaves nothing behind either.
    reloaded = Document(_saved(document))
    _draw_rectangle(reloaded.pages[0])
    engine = reloaded._engine_pdf
    contents = engine._resolve(engine._get_page_dict(0).mapping[PdfName("Contents")])
    streams = [engine._resolve(item).content for item in contents.items]
    assert streams.count(b"q\n") == 1
    assert not streams[-1].startswith(b"Q")


def test_a_page_is_read_once_however_many_fragments_are_added(monkeypatch) -> None:
    content = b"2 0 0 2 0 0 cm" + OWN_MARK
    scanned: list[int] = []

    def counting(data: bytes, *, budget):
        scanned.append(len(data))
        return isolation_for(data, budget=budget)

    monkeypatch.setattr(simple_pdf_module, "isolation_for", counting)
    document = Document(_page_bytes(content))
    page = document.pages[0]
    for _ in range(5):
        _draw_rectangle(page)
    # Whole-page reads: only the first, of the original content.
    whole_page = [n for n in scanned if n >= len(content)]
    assert whole_page == [len(content)]


def test_a_page_whose_content_is_replaced_is_read_again() -> None:
    document = Document(_page_bytes(b"q 0 0 1 rg 1 1 4 4 re f Q"))
    engine = document._engine_pdf
    _draw_rectangle(document.pages[0])
    engine._set_page_content(0, b"2 0 0 2 0 0 cm" + OWN_MARK)
    _draw_rectangle(document.pages[0])
    assert engine.page_contents[0].startswith(b"q\n2 0 0 2 0 0 cm")
    assert _expected_region((41, 41, 59, 59)) <= _pixels(_saved(document), _red)


def test_content_appended_raw_that_leaks_is_isolated_before_the_next_addition() -> None:
    document = Document(_page_bytes(b"q 0 0 1 rg 1 1 4 4 re f Q"))
    engine = document._engine_pdf
    engine._append_content_to_page(0, b"2 0 0 2 0 0 cm")
    _draw_rectangle(document.pages[0])
    assert _expected_region((41, 41, 59, 59)) <= _pixels(_saved(document), _red)


def test_a_page_that_cannot_be_isolated_says_so_once(caplog) -> None:
    document = Document(_page_bytes(b"2 0 0 2 0 0 cm Q" + OWN_MARK))
    with caplog.at_level("WARNING", logger="aspose_pdf"):
        for _ in range(3):
            _draw_rectangle(document.pages[0])
    warnings = [r for r in caplog.records if "cannot be undone" in r.getMessage()]
    assert len(warnings) == 1
    assert "Page 1" in warnings[0].getMessage()


@pytest.mark.parametrize("content", [b"q Q", b"2 0 0 2 0 0 cm"])
def test_pages_sharing_a_contents_array_are_edited_separately(content: bytes) -> None:
    engine = SimplePdf.from_bytes(
        SimplePdf(
            [(0, 0, SIZE, SIZE), (0, 0, SIZE, SIZE)],
            page_contents=[content, content],
        ).to_bytes()
    )
    engine._ensure_cos()
    first = engine._get_page_dict(0)
    second = engine._get_page_dict(1)
    shared = engine._cos_doc.register_object(
        PdfArray([first.mapping[PdfName("Contents")]])
    )
    first.mapping[PdfName("Contents")] = shared
    second.mapping[PdfName("Contents")] = shared

    document = Document(engine.to_bytes())
    edited = document._engine_pdf
    second_contents = edited._resolve(
        edited._get_page_dict(1).mapping[PdfName("Contents")]
    )
    before = list(second_contents.items)
    _draw_rectangle(document.pages[0])
    assert second_contents.items == before
    saved = _saved(document)

    reloaded = Document(saved)
    red_on_second = [
        p
        for y in range(SIZE)
        for x in range(SIZE)
        if _red(p := reloaded.pages[1].render(antialias=False).get_pixel(x, y))
    ]
    assert red_on_second == []
    assert _pixels(saved, _red)


def test_an_incremental_save_only_appends() -> None:
    original = _page_bytes(b"2 0 0 2 0 0 cm" + OWN_MARK)
    document = Document(original)
    _draw_rectangle(document.pages[0])
    saved = _saved(document, incremental=True)

    assert saved.startswith(original)
    assert _expected_region((41, 41, 59, 59)) <= _pixels(saved, _red)


def test_an_encrypted_page_is_isolated_too() -> None:
    protected = Document(_page_bytes(b"2 0 0 2 0 0 cm" + OWN_MARK))
    protected.encrypt("user", "owner")
    encrypted = _saved(protected)

    document = Document(encrypted, password="user")
    _draw_rectangle(document.pages[0])
    saved = _saved(document)

    assert b"/Encrypt" in saved
    assert _expected_region((41, 41, 59, 59)) <= _pixels(saved, _red, password="user")


def test_a_streamed_document_is_isolated_too(tmp_path) -> None:
    path = tmp_path / "leaky.pdf"
    path.write_bytes(_page_bytes(b"2 0 0 2 0 0 cm" + OWN_MARK))
    document = Document.open_streaming(path)
    _draw_rectangle(document.pages[0])
    saved = _saved(document)
    document.close()

    assert _expected_region((41, 41, 59, 59)) <= _pixels(saved, _red)


def test_a_redaction_bar_covers_the_text_it_replaces() -> None:
    def redacted(leak: bytes) -> set[tuple[int, int]]:
        document = Document(_page_bytes(b""))
        document.pages[0].add_text("secret", 10, 60, font_size=12)
        engine = document._engine_pdf
        engine._set_page_content(0, engine.page_contents[0] + leak)
        loaded = Document(_saved(document))
        assert loaded.pages[0].redact_text("secret", overlay=True, overlay_color=(1, 0, 0))
        return _pixels(_saved(loaded), _red)

    plain = redacted(b"")
    assert plain
    assert redacted(b"\n2 0 0 2 0 0 cm\n") == plain
    assert redacted(b"\n0 0 1 1 re W n\n") == plain


# ---------------------------------------------------------------------------
# The rule itself
# ---------------------------------------------------------------------------


def _isolation(content: bytes):
    return isolation_for(content, budget=_LoadBudget())


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"0 0 10 10 re f",
        b"q 2 0 0 2 0 0 cm Q",
        b"q 0.5 g BT /F1 12 Tf (x) Tj ET Q",
        b"q BT /F1 12 Tf 3 Tr (x) Tj ET Q /Im0 Do",
        b"/OC /oc0 BDC q 0 0 1 rg 0 0 1 1 re f Q",  # an open marked section is not state
        b"q Q Q",  # a stray Q with nothing changed
    ],
)
def test_content_that_leaves_nothing_needs_nothing(content: bytes) -> None:
    assert not _isolation(content).needed


@pytest.mark.parametrize(
    ("content", "opening", "closing"),
    [
        (b"2 0 0 2 0 0 cm", b"q\n", b"Q\n"),
        (b"[1 40] 0 d", b"q\n", b"Q\n"),
        (b"/GS0 gs", b"q\n", b"Q\n"),
        (b"0 g", b"q\n", b"Q\n"),
        (b"BT 3 Tr ET", b"q\n", b"Q\n"),
        (b"0 0 1 1 re W n", b"q\n", b"Q\n"),
        (b"q 2 0 0 2 0 0 cm", b"", b"Q\n"),
        (b"q q 1 w", b"", b"Q\nQ\n"),
        (b"1 w q 2 w", b"q\n", b"Q\nQ\n"),
        (b"BT /F1 12 Tf", b"q\n", b"ET\nQ\n"),
        (b"BT (x) Tj", b"", b"ET\n"),
        (b"Q 1 w", b"q\nq\n", b"Q\n"),
        (b"Q Q 1 w", b"q\nq\nq\n", b"Q\n"),
    ],
)
def test_what_isolates_leaking_content(content: bytes, opening: bytes, closing: bytes) -> None:
    isolation = _isolation(content)
    assert (isolation.opening, isolation.closing, isolation.complete) == (
        opening,
        closing,
        True,
    )


def test_a_stray_Q_after_a_change_cannot_be_isolated_without_changing_the_page() -> None:
    # A viewer ignores this Q; a q put in front would give it something to
    # restore and undo the cm the page draws with.
    isolation = _isolation(b"2 0 0 2 0 0 cm Q q 1 w")
    assert isolation.opening == b""
    assert isolation.closing == b"Q\n"
    assert not isolation.complete


@pytest.mark.parametrize(
    "content",
    [
        b"(Q q cm) Tj",
        b"<51> Tj",
        b"% 2 0 0 2 0 0 cm\n",
        b"BI /W 2 /H 1 /CS /G /BPC 8 ID qQ EI",
        b"[(cm) 3 (Tf)] TJ",
    ],
)
def test_strings_comments_and_image_samples_are_not_operators(content: bytes) -> None:
    assert not _isolation(content).needed
