"""PDF/A ties each device colour family to the output intent's own space.

ISO 19005-1 6.2.3.3 permits a device colour space only when the output intent's
destination profile uses that space, so an sRGB intent does not license
DeviceCMYK content. The checker used to accept any structurally valid ICC
profile, which made DeviceCMYK pass under the sRGB intent `convert_to_pdfa`
installs — and left the conversion without a real oracle.
"""

from __future__ import annotations

import struct

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfStream,
    PdfString,
)
from aspose_pdf.engine.simple_pdf import (
    SimplePdf,
    _icc_profile_color_space,
    _minimal_srgb_icc_profile,
)


def _icc(space: bytes) -> bytes:
    """A structurally valid ICC header declaring *space* as its data space."""
    header = bytearray(128)
    header[0:4] = struct.pack(">I", 128)
    header[8:12] = b"\x02\x10\x00\x00"
    header[12:16] = b"prtr"
    header[16:20] = space
    header[20:24] = b"XYZ "
    header[36:40] = b"acsp"
    return bytes(header) + b"\x00" * 64


def _document(content: bytes, *, profile: bytes) -> Document:
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 100, 100)]
    pdf.page_contents = [content]
    pdf._ensure_cos()
    cos = pdf._cos_doc
    icc = cos.register_object(
        PdfStream(
            profile,
            {PdfName("N"): PdfNumber(4), PdfName("Length"): PdfNumber(len(profile))},
        )
    )
    intent = cos.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("OutputIntent"),
                PdfName("S"): PdfName("GTS_PDFA1"),
                PdfName("OutputConditionIdentifier"): PdfString("Test"),
                PdfName("DestOutputProfile"): icc,
            }
        )
    )
    root = pdf._resolve(cos.trailer.mapping[PdfName("Root")])
    root.mapping[PdfName("OutputIntents")] = PdfArray([intent])
    document = Document()
    document._engine_pdf = pdf
    return document


def _colour_problems(document: Document) -> list[str]:
    return [
        problem
        for problem in document._engine_pdf.check_pdfa_compliance("2b")
        if "OutputIntent" in problem or "DestOutputProfile" in problem
    ]


# --- the ICC header reader ------------------------------------------------
@pytest.mark.parametrize(
    "signature,expected",
    [(b"RGB ", "RGB"), (b"CMYK", "CMYK"), (b"GRAY", "Gray"), (b"Lab ", None)],
)
def test_icc_color_space_is_read_from_the_header(signature, expected):
    assert _icc_profile_color_space(_icc(signature)) == expected


def test_icc_color_space_rejects_non_profiles():
    assert _icc_profile_color_space(b"") is None
    assert _icc_profile_color_space(b"\x00" * 200) is None  # no 'acsp' signature
    assert _icc_profile_color_space(_icc(b"RGB ")[:64]) is None  # truncated
    assert _icc_profile_color_space(_minimal_srgb_icc_profile()) == "RGB"


# --- the rule -------------------------------------------------------------
def test_cmyk_content_under_an_rgb_intent_is_reported():
    document = _document(b"0.1 0.2 0.3 0.4 k 0 0 10 10 re f", profile=_icc(b"RGB "))
    problems = _colour_problems(document)
    assert problems, "DeviceCMYK under an sRGB intent must not pass"
    assert "DeviceCMYK" in problems[0]


def test_cmyk_content_under_a_cmyk_intent_is_accepted():
    document = _document(b"0.1 0.2 0.3 0.4 k 0 0 10 10 re f", profile=_icc(b"CMYK"))
    assert _colour_problems(document) == []


def test_rgb_content_under_a_cmyk_intent_is_reported():
    document = _document(b"0.1 0.2 0.3 rg 0 0 10 10 re f", profile=_icc(b"CMYK"))
    problems = _colour_problems(document)
    assert problems and "DeviceRGB" in problems[0]


def test_rgb_content_under_an_rgb_intent_is_accepted():
    document = _document(b"0.1 0.2 0.3 rg 0 0 10 10 re f", profile=_icc(b"RGB "))
    assert _colour_problems(document) == []


@pytest.mark.parametrize("space", [b"RGB ", b"CMYK"])
def test_gray_content_is_accepted_under_either_intent(space):
    """DeviceGray is satisfied by any output intent — it must not follow RGB.

    Gray used to be folded into the RGB flag, which would now flag a valid
    CMYK-intent document that only draws gray.
    """
    document = _document(b"0.5 g 0 0 10 10 re f", profile=_icc(space))
    assert _colour_problems(document) == []


def test_unreadable_profile_is_reported_when_device_colour_is_used():
    document = _document(b"0.1 0.2 0.3 rg 0 0 10 10 re f", profile=b"\x00" * 200)
    problems = _colour_problems(document)
    assert problems and "DestOutputProfile" in problems[0]


# --- the conversion still satisfies the stricter rule ----------------------
def test_convert_to_pdfa_leaves_no_colour_problem():
    """`convert_to_pdfa` normalizes CMYK away, so the stricter check passes."""
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 100, 100)]
    pdf.page_contents = [b"0.1 0.2 0.3 0.4 k 0 0 10 10 re f"]
    pdf._ensure_cos()
    document = Document()
    document._engine_pdf = pdf

    # Before conversion there is no output intent at all.
    assert document._engine_pdf.check_pdfa_compliance("2b")

    document.convert_to_pdfa("2b")
    assert _colour_problems(document) == []
    assert b" k" not in document._engine_pdf.page_contents[0]


# --- the operator scan ----------------------------------------------------
def _scan(content: bytes) -> dict[str, bool]:
    from aspose_pdf.engine.simple_pdf import _scan_content_for_device_colors

    rgb, cmyk, gray = [False], [False], [False]
    _scan_content_for_device_colors(content, rgb, cmyk, gray)
    return {"rgb": rgb[0], "cmyk": cmyk[0], "gray": gray[0]}


@pytest.mark.parametrize(
    "content,expected",
    [
        (b"0.1 0.2 0.3 0.4 k 0 0 10 10 re f", "cmyk"),
        (b"0.1 0.2 0.3 0.4 K 2 w S", "cmyk"),
        (b"0.1 0.2 0.3 rg 0 0 1 1 re f", "rgb"),
        (b"1 1 1 RG 2 w S", "rgb"),
        (b"0.5 g 0 0 1 1 re f", "gray"),
        (b"0 G 2 w S", "gray"),
    ],
)
def test_device_colour_operators_are_detected(content, expected):
    """`k`/`rg`/`g` select a device space without naming one."""
    flags = _scan(content)
    assert flags[expected] is True
    assert [k for k, v in flags.items() if v] == [expected]


@pytest.mark.parametrize(
    "content",
    [
        b"1 0 0 1 0 0 cm /Im0 Do",  # a matrix, not a colour
        b"BT /F1 12 Tf 1 0 0 1 5 5 Tm ET",
        b"BT /F1 12 Tf (king kong) Tj ET",  # k/g inside a string
        b"[(a) -3 (b)] TJ",
        b"/GS0 gs 0 0 1 1 re f",  # gs is not g
        b"0 0 1 1 re f",
        b"2 w 0 0 m 10 10 l S",
    ],
)
def test_operator_scan_does_not_false_positive(content):
    assert _scan(content) == {"rgb": False, "cmyk": False, "gray": False}


def test_named_colour_space_is_still_detected():
    assert _scan(b"/DeviceCMYK cs 0 0 0 1 scn")["cmyk"] is True
    assert _scan(b"/DeviceRGB CS 1 0 0 SCN")["rgb"] is True
