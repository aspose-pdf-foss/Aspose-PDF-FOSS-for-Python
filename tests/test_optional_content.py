"""Optional content groups (layers).

Hidden layers used to be drawn and extracted like any other content: nothing
in the package looked at ``/OC`` at all. These tests pin what a viewer does --
content in a group the default configuration turns off is not painted, not
extracted and not absorbed -- and the public API for listing and switching
layers.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from aspose_pdf import Document, PdfExtractor
from aspose_pdf.graphics import GraphicsAbsorber


def _raw_pdf(objects: dict[int, bytes], root: int = 1) -> bytes:
    out = bytearray(b"%PDF-1.7\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += f"{number} 0 obj\n".encode() + objects[number] + b"\nendobj\n"
    xref = len(out)
    top = max(objects) + 1
    out += f"xref\n0 {top}\n".encode() + b"0000000000 65535 f \n"
    for number in range(1, top):
        out += (
            f"{offsets[number]:010d} 00000 n \n".encode()
            if number in offsets
            else b"0000000000 65535 f \n"
        )
    out += (
        f"trailer\n<< /Size {top} /Root {root} 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(out)


def _stream(dictionary: bytes, content: bytes) -> bytes:
    return (
        dictionary[:-2]
        + f"/Length {len(content)} >>".encode()
        + b"\nstream\n"
        + content
        + b"\nendstream"
    )


def _layered_pdf(config: bytes = b"<< /OFF [6 0 R] >>") -> bytes:
    """A page with a red rectangle on layer 1, blue on layer 2, green unlayered."""
    content = (
        b"/OC /L1 BDC 1 0 0 rg 10 10 50 50 re f EMC "
        b"/OC /L2 BDC 0 0 1 rg 70 10 50 50 re f EMC "
        b"0 1 0 rg 130 10 50 50 re f"
    )
    return _raw_pdf(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R /OCProperties << /OCGs [5 0 R 6 0 R] "
            b"/D " + config + b" >> >>",
            2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] /Resources "
            b"<< /Properties << /L1 5 0 R /L2 6 0 R >> >> /Contents 4 0 R >>",
            4: _stream(b"<< >>", content),
            5: b"<< /Type /OCG /Name (Base) >>",
            6: b"<< /Type /OCG /Name (Draft) >>",
        }
    )


def _layered_text_pdf() -> bytes:
    content = (
        b"BT /F1 12 Tf 10 60 Td (visible text) Tj ET "
        b"/OC /L2 BDC BT /F1 12 Tf 10 30 Td (secret draft) Tj ET EMC"
    )
    return _raw_pdf(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R /OCProperties << /OCGs [5 0 R 6 0 R] "
            b"/D << /OFF [6 0 R] >> >> >>",
            2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] /Resources "
            b"<< /Font << /F1 7 0 R >> /Properties << /L1 5 0 R /L2 6 0 R >> >> "
            b"/Contents 4 0 R >>",
            4: _stream(b"<< >>", content),
            5: b"<< /Type /OCG /Name (Base) >>",
            6: b"<< /Type /OCG /Name (Draft) >>",
            7: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        }
    )


def _colour_counts(raster) -> dict[str, int]:
    counts = {"red": 0, "green": 0, "blue": 0}
    pixels = raster.pixels
    for i in range(0, len(pixels), 3):
        r, g, b = pixels[i], pixels[i + 1], pixels[i + 2]
        if r > 200 and g < 60 and b < 60:
            counts["red"] += 1
        elif g > 200 and r < 60 and b < 60:
            counts["green"] += 1
        elif b > 200 and r < 60 and g < 60:
            counts["blue"] += 1
    return counts


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def test_hidden_layer_is_not_painted() -> None:
    with Document(io.BytesIO(_layered_pdf())) as document:
        counts = _colour_counts(document.pages[0].render(dpi=72, antialias=False))

    assert counts["red"] > 2000  # layer on
    assert counts["green"] > 2000  # no layer at all
    assert counts["blue"] == 0  # layer off


def test_base_state_off_hides_every_group_not_switched_on() -> None:
    config = b"<< /BaseState /OFF /ON [5 0 R] >>"
    with Document(io.BytesIO(_layered_pdf(config))) as document:
        counts = _colour_counts(document.pages[0].render(dpi=72, antialias=False))

    assert counts["red"] > 2000
    assert counts["blue"] == 0
    assert counts["green"] > 2000  # unlayered content is never optional


def test_switching_a_layer_on_paints_it() -> None:
    with Document(io.BytesIO(_layered_pdf())) as document:
        document.layers["Draft"].visible = True
        counts = _colour_counts(document.pages[0].render(dpi=72, antialias=False))
    assert counts["blue"] > 2000


def test_switching_a_layer_off_hides_it() -> None:
    with Document(io.BytesIO(_layered_pdf())) as document:
        document.layers["Base"].visible = False
        counts = _colour_counts(document.pages[0].render(dpi=72, antialias=False))
    assert counts["red"] == 0


def test_hidden_image_xobject_is_not_painted() -> None:
    """An XObject can carry /OC itself, without any marked content."""
    image = b"\xff\x00\x00" * 4
    data = _raw_pdf(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R /OCProperties << /OCGs [6 0 R] "
            b"/D << /OFF [6 0 R] >> >> >>",
            2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Resources "
            b"<< /XObject << /Im0 5 0 R >> >> /Contents 4 0 R >>",
            4: _stream(b"<< >>", b"q 80 0 0 80 10 10 cm /Im0 Do Q"),
            5: _stream(
                b"<< /Type /XObject /Subtype /Image /Width 2 /Height 2 "
                b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /OC 6 0 R >>",
                image,
            ),
            6: b"<< /Type /OCG /Name (Stamp) >>",
        }
    )
    with Document(io.BytesIO(data)) as document:
        counts = _colour_counts(document.pages[0].render(dpi=72, antialias=False))
    assert counts["red"] == 0

    with Document(io.BytesIO(data)) as document:
        document.layers["Stamp"].visible = True
        counts = _colour_counts(document.pages[0].render(dpi=72, antialias=False))
    assert counts["red"] > 2000


# ---------------------------------------------------------------------------
# Text and graphics
# ---------------------------------------------------------------------------
def test_hidden_layer_text_is_not_extracted() -> None:
    extractor = PdfExtractor()
    extractor.bind_pdf(_layered_text_pdf())
    extractor.extract_text()
    text = extractor.get_text()
    extractor.close()

    assert "visible text" in text
    assert "secret draft" not in text


def test_text_of_a_layer_switched_on_is_extracted(tmp_path: Path) -> None:
    out = tmp_path / "on.pdf"
    with Document(io.BytesIO(_layered_text_pdf())) as document:
        document.layers["Draft"].visible = True
        document.save(out)

    extractor = PdfExtractor()
    extractor.bind_pdf(out)
    extractor.extract_text()
    text = extractor.get_text()
    extractor.close()
    assert "secret draft" in text


def test_graphics_absorber_skips_hidden_layers() -> None:
    with Document(io.BytesIO(_layered_pdf())) as document:
        absorber = GraphicsAbsorber()
        absorber.visit(document.pages[0])
        hidden = [round(e.rectangle.x, 1) for e in absorber.elements]

        document.layers["Draft"].visible = True
        absorber.visit(document.pages[0])
        shown = [round(e.rectangle.x, 1) for e in absorber.elements]

    assert hidden == [10.0, 130.0]
    assert shown == [10.0, 70.0, 130.0]


# ---------------------------------------------------------------------------
# The layer collection
# ---------------------------------------------------------------------------
def test_layers_are_listed_with_their_names_and_state() -> None:
    with Document(io.BytesIO(_layered_pdf())) as document:
        layers = document.layers
        assert layers.names() == ["Base", "Draft"]
        assert [layer.visible for layer in layers] == [True, False]
        assert "Draft" in layers
        assert layers["Draft"].object_number == 6
        assert layers[0].intent == ("View",)


def test_layer_visibility_persists_through_a_save(tmp_path: Path) -> None:
    out = tmp_path / "layers.pdf"
    with Document(io.BytesIO(_layered_pdf())) as document:
        document.layers["Draft"].visible = True
        document.layers["Base"].visible = False
        document.save(out)

    with Document(out) as reloaded:
        assert [(layer.name, layer.visible) for layer in reloaded.layers] == [
            ("Base", False),
            ("Draft", True),
        ]


def test_document_without_layers_has_an_empty_collection() -> None:
    document = Document()
    document.pages.add()
    assert len(document.layers) == 0
    assert document.layers.names() == []
    document.dispose()


def test_unknown_layer_name_raises() -> None:
    with Document(io.BytesIO(_layered_pdf())) as document:
        with pytest.raises(KeyError):
            document.layers["Nope"]


# ---------------------------------------------------------------------------
# OCMD policies
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "policy,visible",
    [(b"/AnyOn", True), (b"/AllOn", False), (b"/AnyOff", True), (b"/AllOff", False)],
)
def test_ocmd_policies(policy: bytes, visible: bool) -> None:
    """One group on, one off: each /P policy resolves differently."""
    content = b"/OC /M1 BDC 1 0 0 rg 10 10 50 50 re f EMC"
    data = _raw_pdf(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R /OCProperties << /OCGs [5 0 R 6 0 R] "
            b"/D << /OFF [6 0 R] >> >> >>",
            2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] /Resources "
            b"<< /Properties << /M1 7 0 R >> >> /Contents 4 0 R >>",
            4: _stream(b"<< >>", content),
            5: b"<< /Type /OCG /Name (On) >>",
            6: b"<< /Type /OCG /Name (Off) >>",
            7: b"<< /Type /OCMD /OCGs [5 0 R 6 0 R] /P " + policy + b" >>",
        }
    )
    with Document(io.BytesIO(data)) as document:
        counts = _colour_counts(document.pages[0].render(dpi=72, antialias=False))
    assert (counts["red"] > 2000) is visible


def test_visibility_expression_negates_a_group() -> None:
    content = b"/OC /M1 BDC 1 0 0 rg 10 10 50 50 re f EMC"
    data = _raw_pdf(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R /OCProperties << /OCGs [6 0 R] "
            b"/D << /OFF [6 0 R] >> >> >>",
            2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] /Resources "
            b"<< /Properties << /M1 7 0 R >> >> /Contents 4 0 R >>",
            4: _stream(b"<< >>", content),
            6: b"<< /Type /OCG /Name (Off) >>",
            7: b"<< /Type /OCMD /VE [/Not 6 0 R] >>",
        }
    )
    with Document(io.BytesIO(data)) as document:
        counts = _colour_counts(document.pages[0].render(dpi=72, antialias=False))
    assert counts["red"] > 2000  # NOT(off) is visible


def test_nested_marked_content_inside_a_hidden_layer_stays_hidden() -> None:
    content = (
        b"/OC /L2 BDC /Span BDC 0 0 1 rg 10 10 50 50 re f EMC EMC "
        b"0 1 0 rg 130 10 50 50 re f"
    )
    data = _raw_pdf(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R /OCProperties << /OCGs [6 0 R] "
            b"/D << /OFF [6 0 R] >> >> >>",
            2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] /Resources "
            b"<< /Properties << /L2 6 0 R >> >> /Contents 4 0 R >>",
            4: _stream(b"<< >>", content),
            6: b"<< /Type /OCG /Name (Draft) >>",
        }
    )
    with Document(io.BytesIO(data)) as document:
        counts = _colour_counts(document.pages[0].render(dpi=72, antialias=False))
    assert counts["blue"] == 0
    assert counts["green"] > 2000  # the outer EMC did not end the hidden run early
