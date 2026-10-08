"""PDF/X (ISO 15930) conformance checks and conversion.

Covers the standards table and the spellings its names take, the identification
keys each part puts in a different place, the output intent and its ICC
profile, the trim/bleed geometry, trapping, colour, transparency, the
prohibitions shared with PDF/A, and the public ``Document`` surface.

The CMYK ICC profile used here is **synthesised in this file**: the checks read
a profile's 128-byte header (and its ``desc`` tag), so a header saying CMYK is
all they need, and a real press profile is third-party content that cannot be
redistributed with the tests. It is a fixture for the plumbing, not a
characterization of any printing condition.
"""

from __future__ import annotations

import io
import struct

import pytest

from aspose_pdf import Document, PageSize, PdfXStandard
from aspose_pdf.engine import conformance
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfStream,
    PdfString,
)
from aspose_pdf.engine.simple_pdf import SimplePdf, _minimal_srgb_icc_profile
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfIOException,
    PdfValidationException,
)
from aspose_pdf.pdfx import (
    PdfXValidateOptions,
    PdfXValidationResult,
    PdfXValidator,
)

ALL_STANDARDS = sorted(conformance.PDFX_STANDARDS)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _icc(space: bytes, description: str) -> bytes:
    """A structurally valid ICC v2 header with a ``desc`` tag, for *space*."""
    desc = description.encode("ascii")
    tag = (
        b"desc"
        + b"\x00" * 4
        + struct.pack(">I", len(desc) + 1)
        + desc
        + b"\x00"
        + b"\x00" * 78  # unicode/scriptcode fields of textDescriptionType
    )
    tag += b"\x00" * ((-len(tag)) % 4)
    header_size = 128
    table = 4 + 12
    header = (
        struct.pack(">I", header_size + table + len(tag))
        + b"\x00" * 4
        + b"\x02\x10\x00\x00"
        + b"prtr"
        + space
        + b"Lab "
        + struct.pack(">6H", 2026, 1, 1, 0, 0, 0)
        + b"acsp"
        + b"APPL"
        + b"\x00" * 4 * 3
        + b"\x00" * 8
        + b"\x00" * 4
        + struct.pack(">3i", 63190, 65536, 54061)
        + b"\x00" * 4
        + b"\x00" * 16
        + b"\x00" * 28
    )
    assert len(header) == header_size
    return (
        header
        + struct.pack(">I", 1)
        + b"desc"
        + struct.pack(">II", header_size + table, len(tag))
        + tag
    )


CMYK_ICC = _icc(b"CMYK", "Test CMYK")
GRAY_ICC = _icc(b"GRAY", "Test Gray")


def _minimal_pdf_bytes() -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >>\n"
        b"endobj\n"
        b"2 0 obj << /Type /Pages /Count 1 /Kids [3 0 R] >>\n"
        b"endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\n"
        b"endobj\n"
        b"xref\n"
        b"0 4\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000062 00000 n \n"
        b"0000000126 00000 n \n"
        b"trailer << /Root 1 0 R /Size 4 >>\n"
        b"startxref\n"
        b"210\n"
        b"%%EOF"
    )


def _pdf() -> SimplePdf:
    return SimplePdf.from_bytes(_minimal_pdf_bytes())


def _converted(standard: str = "PDF/X-4", **kwargs) -> SimplePdf:
    """A minimal document already converted to *standard*."""
    pdf = _pdf()
    kwargs.setdefault("icc_profile", CMYK_ICC)
    pdf.convert_to_pdfx(standard, **kwargs)
    return pdf


def _catalog(pdf: SimplePdf) -> PdfDictionary:
    return pdf._resolve(pdf._cos_doc.trailer.get(PdfName("Root")))


def _page(pdf: SimplePdf) -> PdfDictionary:
    return pdf._get_page_dict(0)


def _errors(pdf: SimplePdf, standard: str = "PDF/X-4") -> list[str]:
    return pdf.check_pdfx_compliance(standard)[0]


def _warnings(pdf: SimplePdf, standard: str = "PDF/X-4") -> list[str]:
    return pdf.check_pdfx_compliance(standard)[1]


def _has(pdf: SimplePdf, needle: str, standard: str = "PDF/X-4") -> bool:
    return any(needle in message for message in _errors(pdf, standard))


def _intent(pdf: SimplePdf) -> PdfDictionary:
    intents = pdf._resolve(_catalog(pdf).get(PdfName("OutputIntents")))
    return pdf._resolve(intents.items[0])


# ---------------------------------------------------------------------------
# The standards table
# ---------------------------------------------------------------------------
class TestStandards:
    def test_five_levels(self):
        assert ALL_STANDARDS == [
            "PDF/X-1a:2001",
            "PDF/X-1a:2003",
            "PDF/X-3:2002",
            "PDF/X-3:2003",
            "PDF/X-4",
        ]

    @pytest.mark.parametrize(
        ("name", "iso_part"),
        [
            ("PDF/X-1a:2001", 1),
            ("PDF/X-1a:2003", 4),
            ("PDF/X-3:2002", 3),
            ("PDF/X-3:2003", 6),
            ("PDF/X-4", 7),
        ],
    )
    def test_iso_part_is_not_the_family_number(self, name, iso_part):
        """PDF/X-1a:2003 is ISO 15930-4, not -1: the two numbers differ."""
        assert conformance.PDFX_STANDARDS[name].iso_part == iso_part

    @pytest.mark.parametrize(
        ("spelling", "expected"),
        [
            ("PDF/X-4", "PDF/X-4"),
            ("x-4", "PDF/X-4"),
            ("X4", "PDF/X-4"),
            ("pdf/x-4", "PDF/X-4"),
            ("x1a", "PDF/X-1a:2003"),
            ("X-1a", "PDF/X-1a:2003"),
            ("x-1a:2001", "PDF/X-1a:2001"),
            ("X1A2001", "PDF/X-1a:2001"),
            ("x3", "PDF/X-3:2003"),
            ("pdf/x-3:2002", "PDF/X-3:2002"),
        ],
    )
    def test_spellings(self, spelling, expected):
        assert conformance.normalize_pdfx_standard(spelling) == expected

    def test_bare_part_gives_the_newest_edition(self):
        assert conformance.normalize_pdfx_standard("x-1a") == "PDF/X-1a:2003"
        assert conformance.normalize_pdfx_standard("x-3") == "PDF/X-3:2003"

    @pytest.mark.parametrize("bad", ["PDF/X-2", "x-5", "PDF/A-1b", "", "4"])
    def test_unknown_standard_raises(self, bad):
        with pytest.raises(ValueError, match="Unknown PDF/X standard"):
            conformance.normalize_pdfx_standard(bad)

    def test_enum_members_are_their_names(self):
        assert PdfXStandard.X4.value == "PDF/X-4"
        assert str(PdfXStandard.X4) == "PdfXStandard.X4"
        assert PdfXStandard.by_name("x-3:2002") is PdfXStandard.X3_2002

    def test_enum_properties(self):
        assert PdfXStandard.X4.allows_transparency is True
        assert PdfXStandard.X1A_2003.allows_transparency is False
        assert PdfXStandard.X3_2003.iso_part == 6

    def test_enum_accepts_a_member(self):
        assert PdfXStandard.by_name(PdfXStandard.X4) is PdfXStandard.X4

    def test_citation_omits_an_unknown_clause(self):
        """Only the two parts whose clause list was read are cited by clause."""
        rules_4 = conformance.PDFX_STANDARDS["PDF/X-4"]
        rules_3 = conformance.PDFX_STANDARDS["PDF/X-3:2002"]
        assert conformance.pdfx_cite(rules_4, "boxes") == "ISO 15930-7 6.12"
        assert conformance.pdfx_cite(rules_3, "boxes") == "ISO 15930-3"


# ---------------------------------------------------------------------------
# Identification
# ---------------------------------------------------------------------------
class TestIdentification:
    def test_missing_info_key_reported_for_part_1(self):
        assert _has(_pdf(), "/GTS_PDFXVersion = (PDF/X-1a:2003)", "PDF/X-1a:2003")

    def test_wrong_info_value_names_both(self):
        pdf = _converted("PDF/X-1a:2003")
        pdf.metadata["GTS_PDFXVersion"] = "PDF/X-3:2003"
        messages = _errors(pdf, "PDF/X-1a:2003")
        assert any(
            "'PDF/X-3:2003'" in m and "'PDF/X-1a:2003'" in m for m in messages
        )

    def test_part_1_needs_the_conformance_key(self):
        pdf = _converted("PDF/X-1a:2003")
        del pdf.metadata["GTS_PDFXConformance"]
        assert _has(pdf, "/GTS_PDFXConformance", "PDF/X-1a:2003")

    def test_part_3_has_no_conformance_key(self):
        pdf = _converted("PDF/X-3:2003")
        assert "GTS_PDFXConformance" not in pdf.metadata
        assert _errors(pdf, "PDF/X-3:2003") == []

    @pytest.mark.parametrize(
        ("standard", "version", "conformance_value"),
        [
            ("PDF/X-1a:2001", "PDF/X-1:2001", "PDF/X-1a:2001"),
            ("PDF/X-1a:2003", "PDF/X-1a:2003", "PDF/X-1a:2003"),
            ("PDF/X-3:2002", "PDF/X-3:2002", None),
            ("PDF/X-3:2003", "PDF/X-3:2003", None),
        ],
    )
    def test_conversion_writes_the_part_s_own_strings(
        self, standard, version, conformance_value
    ):
        """ISO 15930-1 omits the conformance letter from the version string."""
        pdf = _converted(standard)
        assert pdf.metadata["GTS_PDFXVersion"] == version
        assert pdf.metadata.get("GTS_PDFXConformance") == conformance_value

    def test_part_4_identifies_itself_in_xmp_alone(self):
        pdf = _converted("PDF/X-4")
        assert "GTS_PDFXVersion" not in pdf.metadata
        xmp = pdf._resolve(_catalog(pdf).get(PdfName("Metadata"))).content
        assert b"http://www.npes.org/pdfx/ns/id/" in xmp
        assert b"GTS_PDFXVersion" in xmp
        assert b"PDF/X-4" in xmp

    def test_part_4_conversion_removes_a_stale_info_key(self):
        pdf = _pdf()
        pdf.metadata["GTS_PDFXVersion"] = "PDF/X-1a:2003"
        pdf.metadata["GTS_PDFXConformance"] = "PDF/X-1a:2003"
        pdf.convert_to_pdfx("PDF/X-4")
        assert "GTS_PDFXVersion" not in pdf.metadata
        assert _warnings(pdf) == []

    def test_part_4_info_key_is_a_warning_not_an_error(self):
        pdf = _converted("PDF/X-4")
        pdf.metadata["GTS_PDFXVersion"] = "PDF/X-4"
        errors, warnings = pdf.check_pdfx_compliance("PDF/X-4")
        assert errors == []
        assert any("information dictionary also carries" in w for w in warnings)

    def test_part_4_rejects_the_part_3_schema(self):
        pdf = _converted("PDF/X-4")
        catalog = _catalog(pdf)
        xmp = pdf._resolve(catalog.get(PdfName("Metadata"))).content
        swapped = xmp.replace(
            b"http://www.npes.org/pdfx/ns/id/", b"http://ns.adobe.com/pdfx/1.3/"
        ).replace(b"pdfxid:", b"pdfx:")
        catalog.mapping[PdfName("Metadata")] = pdf._cos_doc.register_object(
            PdfStream(swapped, {PdfName("Length"): PdfNumber(len(swapped))})
        )
        assert _has(pdf, "must use the PDF/X ID schema")

    def test_missing_xmp_reported_for_part_4(self):
        pdf = _converted("PDF/X-4")
        del _catalog(pdf).mapping[PdfName("Metadata")]
        assert _has(pdf, "pdfxid:GTS_PDFXVersion")

    def test_metadata_stream_is_unfiltered(self):
        pdf = _converted("PDF/X-4")
        stream = pdf._resolve(_catalog(pdf).get(PdfName("Metadata")))
        assert PdfName("Filter") not in stream.mapping

    def test_trailer_id_required_and_added(self):
        assert _has(_pdf(), "file identifier")
        pdf = _converted("PDF/X-4")
        file_id = pdf._resolve(pdf._cos_doc.trailer.get(PdfName("ID")))
        assert isinstance(file_id, PdfArray) and len(file_id.items) == 2


# ---------------------------------------------------------------------------
# Output intent
# ---------------------------------------------------------------------------
class TestOutputIntent:
    def test_missing_intent_reported(self):
        assert _has(_pdf(), "/OutputIntents entry with /S /GTS_PDFX")

    def test_conversion_writes_every_entry(self):
        pdf = _converted(
            "PDF/X-1a:2003",
            output_condition_identifier="FOGRA39",
            output_condition="Offset coated",
        )
        intent = _intent(pdf)
        assert pdf._get_name(intent.get(PdfName("S"))) == "GTS_PDFX"
        assert intent[PdfName("OutputConditionIdentifier")].value == b"FOGRA39"
        assert intent[PdfName("OutputCondition")].value == b"Offset coated"
        assert intent[PdfName("RegistryName")].value == b"http://www.color.org"
        assert isinstance(pdf._resolve(intent.get(PdfName("DestOutputProfile"))), PdfStream)

    def test_identifier_defaults_to_the_profile_description(self):
        pdf = _converted("PDF/X-1a:2003")
        assert _intent(pdf)[PdfName("OutputConditionIdentifier")].value == b"Test CMYK"

    def test_icc_stream_declares_the_profile_s_own_components(self):
        pdf = _converted("PDF/X-1a:2003")
        profile = pdf._resolve(_intent(pdf).get(PdfName("DestOutputProfile")))
        assert profile[PdfName("N")].value == 4
        assert pdf._get_name(profile.get(PdfName("Alternate"))) == "DeviceCMYK"

    def test_gray_profile_declares_one_component(self):
        pdf = _converted("PDF/X-1a:2003", icc_profile=GRAY_ICC)
        profile = pdf._resolve(_intent(pdf).get(PdfName("DestOutputProfile")))
        assert profile[PdfName("N")].value == 1
        assert pdf._get_name(profile.get(PdfName("Alternate"))) == "DeviceGray"

    def test_srgb_profile_still_declares_three(self):
        """The PDF/A conversion's only profile keeps the values it always had."""
        pdf = _converted("PDF/X-4", icc_profile=_minimal_srgb_icc_profile())
        profile = pdf._resolve(_intent(pdf).get(PdfName("DestOutputProfile")))
        assert profile[PdfName("N")].value == 3
        assert pdf._get_name(profile.get(PdfName("Alternate"))) == "DeviceRGB"

    def test_missing_condition_identifier_reported(self):
        pdf = _converted("PDF/X-4")
        del _intent(pdf).mapping[PdfName("OutputConditionIdentifier")]
        assert _has(pdf, "/OutputConditionIdentifier")

    def test_missing_profile_reported(self):
        pdf = _converted("PDF/X-4")
        del _intent(pdf).mapping[PdfName("DestOutputProfile")]
        assert _has(pdf, "requires an embedded ICC profile")

    def test_external_profile_reference_is_named_as_x4p(self):
        pdf = _converted("PDF/X-4")
        intent = _intent(pdf)
        del intent.mapping[PdfName("DestOutputProfile")]
        intent.mapping[PdfName("DestOutputProfileRef")] = PdfDictionary({})
        assert _has(pdf, "PDF/X-4p")

    def test_unreadable_profile_reported(self):
        pdf = _converted("PDF/X-4")
        intent = _intent(pdf)
        intent.mapping[PdfName("DestOutputProfile")] = pdf._cos_doc.register_object(
            PdfStream(b"not a profile", {PdfName("Length"): PdfNumber(13)})
        )
        assert _has(pdf, "not a readable ICC profile")

    def test_part_1_refuses_an_rgb_intent(self):
        pdf = _pdf()
        pdf.convert_to_pdfx("PDF/X-3:2003")  # writes the bundled sRGB intent
        assert _has(pdf, "requires the output intent profile to be", "PDF/X-1a:2003")

    @pytest.mark.parametrize("standard", ["PDF/X-3:2002", "PDF/X-3:2003", "PDF/X-4"])
    def test_rgb_intent_accepted_from_part_3_on(self, standard):
        pdf = _pdf()
        assert pdf.convert_to_pdfx(standard) == []

    def test_part_1_without_a_cmyk_profile_reports_the_lack(self):
        """The printing condition is not invented, as a font is not invented."""
        pdf = _pdf()
        remaining = pdf.convert_to_pdfx("PDF/X-1a:2003", icc_profile=None)
        assert any("/OutputIntents entry" in m for m in remaining)
        assert PdfName("OutputIntents") not in _catalog(pdf).mapping

    def test_second_conversion_does_not_add_a_second_intent(self):
        pdf = _converted("PDF/X-4")
        pdf.convert_to_pdfx("PDF/X-4", icc_profile=CMYK_ICC)
        intents = pdf._resolve(_catalog(pdf).get(PdfName("OutputIntents")))
        assert len(intents.items) == 1

    def test_two_pdfx_intents_reported(self):
        pdf = _converted("PDF/X-4")
        intents = pdf._resolve(_catalog(pdf).get(PdfName("OutputIntents")))
        intents.items.append(intents.items[0])
        assert _has(pdf, "permits one /GTS_PDFX output intent")

    def test_a_pdfa_intent_does_not_count_as_a_pdfx_one(self):
        pdf = _converted("PDF/X-4")
        pdf._get_name(_intent(pdf).get(PdfName("S")))
        _intent(pdf).mapping[PdfName("S")] = PdfName("GTS_PDFA1")
        assert _has(pdf, "/OutputIntents entry with /S /GTS_PDFX")

    def test_existing_cmyk_profile_is_reused_without_one_being_passed(self):
        pdf = _converted("PDF/X-1a:2003")
        pdf.convert_to_pdfx("PDF/X-1a:2003")  # no icc_profile this time
        assert _errors(pdf, "PDF/X-1a:2003") == []

    def test_bad_profile_bytes_raise(self):
        with pytest.raises(PdfValidationException, match="not a usable ICC profile"):
            _pdf().convert_to_pdfx("PDF/X-4", icc_profile=b"nope")

    def test_missing_profile_file_raises(self, tmp_path):
        with pytest.raises(PdfIOException, match="ICC profile file does not exist"):
            _pdf().convert_to_pdfx("PDF/X-4", icc_profile=tmp_path / "absent.icc")

    def test_profile_from_a_file(self, tmp_path):
        path = tmp_path / "press.icc"
        path.write_bytes(CMYK_ICC)
        pdf = _pdf()
        assert pdf.convert_to_pdfx("PDF/X-1a:2003", icc_profile=path) == []


# ---------------------------------------------------------------------------
# Trapping
# ---------------------------------------------------------------------------
class TestTrapped:
    def test_missing_trapped_reported(self):
        assert _has(_pdf(), "requires /Trapped")

    def test_unknown_is_not_an_answer(self):
        pdf = _converted("PDF/X-4")
        pdf.metadata["Trapped"] = "Unknown"
        assert _has(pdf, "PDF/X requires /True or /False")

    @pytest.mark.parametrize(
        ("value", "written"),
        [
            (True, "True"),
            (False, "False"),
            ("True", "True"),
            ("true", "True"),
            ("/False", "False"),
            ("no", "False"),
            ("yes", "True"),
            (1, "True"),
            (0, "False"),
        ],
    )
    def test_conversion_accepts_a_yes_or_no(self, value, written):
        pdf = _converted("PDF/X-4", trapped=value)
        assert pdf.metadata["Trapped"] == written

    @pytest.mark.parametrize("bad", ["Unknown", "maybe", "", None])
    def test_conversion_refuses_anything_else(self, bad):
        with pytest.raises(PdfValidationException, match="trapped must say"):
            _pdf().convert_to_pdfx("PDF/X-4", trapped=bad)

    def test_written_as_a_name(self):
        pdf = _converted("PDF/X-4")
        assert b"/Trapped /False" in pdf.to_bytes()


# ---------------------------------------------------------------------------
# Bounding boxes
# ---------------------------------------------------------------------------
class TestBoxes:
    def test_neither_box_reported(self):
        assert _has(_pdf(), "requires a /TrimBox or an /ArtBox on page 1")

    def test_conversion_adds_a_trim_box(self):
        pdf = _converted("PDF/X-4")
        assert PdfName("TrimBox") in _page(pdf).mapping

    def test_trim_box_comes_from_the_crop_box(self):
        pdf = _pdf()
        pdf.set_page_crop_box(0, (10, 20, 300, 400))
        pdf.convert_to_pdfx("PDF/X-4")
        box = [n.value for n in _page(pdf)[PdfName("TrimBox")].items]
        assert box == [10, 20, 300, 400]

    def test_an_art_box_alone_is_enough(self):
        pdf = _converted("PDF/X-4")
        page = _page(pdf)
        page.mapping[PdfName("ArtBox")] = page.mapping.pop(PdfName("TrimBox"))
        assert _errors(pdf) == []

    def test_both_boxes_reported(self):
        pdf = _converted("PDF/X-4")
        _page(pdf).mapping[PdfName("ArtBox")] = PdfArray(
            [PdfNumber(0), PdfNumber(0), PdfNumber(10), PdfNumber(10)]
        )
        assert _has(pdf, "not both")

    def test_conversion_keeps_the_trim_box_of_a_page_with_both(self):
        pdf = _pdf()
        page = _page(pdf)
        trim = PdfArray([PdfNumber(5), PdfNumber(5), PdfNumber(600), PdfNumber(780)])
        page.mapping[PdfName("TrimBox")] = trim
        page.mapping[PdfName("ArtBox")] = PdfArray(
            [PdfNumber(0), PdfNumber(0), PdfNumber(10), PdfNumber(10)]
        )
        pdf.convert_to_pdfx("PDF/X-4")
        assert PdfName("ArtBox") not in page.mapping
        assert [n.value for n in page[PdfName("TrimBox")].items] == [5, 5, 600, 780]

    def test_bleed_box_must_contain_the_trim_box(self):
        pdf = _converted("PDF/X-4")
        _page(pdf).mapping[PdfName("BleedBox")] = PdfArray(
            [PdfNumber(10), PdfNumber(10), PdfNumber(100), PdfNumber(100)]
        )
        assert _has(pdf, "/BleedBox on page 1 does not contain")

    def test_a_larger_bleed_box_is_fine(self):
        pdf = _pdf()
        pdf.set_page_crop_box(0, (10, 10, 600, 780))
        pdf.convert_to_pdfx("PDF/X-4")
        _page(pdf).mapping[PdfName("BleedBox")] = PdfArray(
            [PdfNumber(0), PdfNumber(0), PdfNumber(612), PdfNumber(792)]
        )
        assert _errors(pdf) == []

    def test_media_box_must_contain_the_rest(self):
        pdf = _converted("PDF/X-4")
        _page(pdf).mapping[PdfName("TrimBox")] = PdfArray(
            [PdfNumber(0), PdfNumber(0), PdfNumber(2000), PdfNumber(2000)]
        )
        assert _has(pdf, "/MediaBox on page 1 does not contain")

    def test_trim_box_on_the_pages_node_does_not_count(self):
        """None of the production boxes is inheritable (ISO 32000-1 Table 30)."""
        pdf = _pdf()
        pages = pdf._resolve(_catalog(pdf).get(PdfName("Pages")))
        pages.mapping[PdfName("TrimBox")] = PdfArray(
            [PdfNumber(0), PdfNumber(0), PdfNumber(612), PdfNumber(792)]
        )
        assert _has(pdf, "requires a /TrimBox or an /ArtBox on page 1")

    def test_a_malformed_trim_box_is_named(self):
        pdf = _converted("PDF/X-4")
        _page(pdf).mapping[PdfName("TrimBox")] = PdfArray(
            [PdfNumber(0), PdfNumber(0), PdfName("Bad"), PdfNumber(10)]
        )
        assert _has(pdf, "is not four numbers")

    def test_every_page_is_checked(self):
        doc_bytes = _multi_page_bytes(3)
        pdf = SimplePdf.from_bytes(doc_bytes)
        messages = _errors(pdf)
        assert sum("TrimBox or an /ArtBox" in m for m in messages) == 3


def _multi_page_bytes(count: int) -> bytes:
    """A document of *count* blank pages, written through the public API."""
    doc = Document()
    for _ in range(count):
        doc.pages.add(PageSize.A4)
    buffer = io.BytesIO()
    doc.save(buffer)
    doc.close()
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Transparency
# ---------------------------------------------------------------------------
def _transparency_group(pdf: SimplePdf, *, colour_space: bool = True) -> None:
    group = PdfDictionary({PdfName("S"): PdfName("Transparency")})
    if colour_space:
        group.mapping[PdfName("CS")] = PdfName("DeviceCMYK")
    _page(pdf).mapping[PdfName("Group")] = group


class TestTransparency:
    @pytest.mark.parametrize(
        "standard", ["PDF/X-1a:2001", "PDF/X-1a:2003", "PDF/X-3:2002", "PDF/X-3:2003"]
    )
    def test_page_group_prohibited_before_part_4(self, standard):
        pdf = _converted(standard)
        _transparency_group(pdf)
        assert _has(pdf, "prohibits transparency groups", standard)

    def test_page_group_allowed_in_part_4(self):
        pdf = _converted("PDF/X-4")
        _transparency_group(pdf)
        assert _errors(pdf) == []

    def test_part_4_page_group_needs_a_blending_space(self):
        pdf = _converted("PDF/X-4")
        _transparency_group(pdf, colour_space=False)
        assert _has(pdf, "blending colour space (/Group /CS)")

    def test_form_xobject_group_prohibited_before_part_4(self):
        pdf = _converted("PDF/X-1a:2003")
        form = PdfStream(
            b"",
            {
                PdfName("Subtype"): PdfName("Form"),
                PdfName("Group"): PdfDictionary(
                    {PdfName("S"): PdfName("Transparency")}
                ),
            },
        )
        _add_resource(pdf, "XObject", "Fm0", form)
        assert _has(pdf, "transparency groups (form XObject", "PDF/X-1a:2003")

    @pytest.mark.parametrize(
        ("key", "value", "needle"),
        [
            ("SMask", PdfDictionary({PdfName("S"): PdfName("Alpha")}), "soft masks"),
            ("BM", PdfName("Multiply"), "blend mode /Multiply"),
            ("CA", PdfNumber(0.5), "constant alpha < 1 (/CA)"),
            ("ca", PdfNumber(0.5), "constant alpha < 1 (/ca)"),
        ],
    )
    def test_extgstate_transparency_prohibited_before_part_4(self, key, value, needle):
        pdf = _converted("PDF/X-1a:2003")
        _add_resource(
            pdf, "ExtGState", "GS0", PdfDictionary({PdfName(key): value})
        )
        assert _has(pdf, needle, "PDF/X-1a:2003")
        clean = _converted("PDF/X-4")
        _add_resource(
            clean, "ExtGState", "GS0", PdfDictionary({PdfName(key): value})
        )
        assert _errors(clean) == []

    @pytest.mark.parametrize("standard", ALL_STANDARDS)
    @pytest.mark.parametrize("key", ["TR", "TR2"])
    def test_transfer_functions_prohibited_everywhere(self, standard, key):
        pdf = _converted(standard)
        _add_resource(
            pdf, "ExtGState", "GS0", PdfDictionary({PdfName(key): PdfName("Custom")})
        )
        assert _has(pdf, f"transfer functions in ExtGState (/{key})", standard)

    def test_identity_transfer_function_is_allowed(self):
        pdf = _converted("PDF/X-4")
        _add_resource(
            pdf, "ExtGState", "GS0", PdfDictionary({PdfName("TR"): PdfName("Identity")})
        )
        assert _errors(pdf) == []

    def test_image_smask_prohibited_before_part_4(self):
        pdf = _converted("PDF/X-1a:2003")
        mask = PdfStream(b"", {PdfName("Subtype"): PdfName("Image")})
        image = PdfStream(
            b"",
            {
                PdfName("Subtype"): PdfName("Image"),
                PdfName("SMask"): pdf._cos_doc.register_object(mask),
            },
        )
        _add_resource(pdf, "XObject", "Im0", image)
        assert _has(pdf, "soft-mask images (/SMask)", "PDF/X-1a:2003")


def _add_resource(pdf: SimplePdf, category: str, name: str, value) -> None:
    """Put *value* into the first page's resources under *category*/*name*."""
    page = _page(pdf)
    resources = pdf._resolve(page.mapping.get(PdfName("Resources")))
    if not isinstance(resources, PdfDictionary):
        resources = PdfDictionary({})
        page.mapping[PdfName("Resources")] = resources
    bucket = pdf._resolve(resources.mapping.get(PdfName(category)))
    if not isinstance(bucket, PdfDictionary):
        bucket = PdfDictionary({})
        resources.mapping[PdfName(category)] = bucket
    if isinstance(value, PdfStream):
        value = pdf._cos_doc.register_object(value)
    bucket.mapping[PdfName(name)] = value


# ---------------------------------------------------------------------------
# Colour
# ---------------------------------------------------------------------------
class TestColour:
    @pytest.mark.parametrize(
        ("space", "needle"),
        [
            (PdfName("DeviceRGB"), "/DeviceRGB"),
            (PdfArray([PdfName("CalRGB"), PdfDictionary({})]), "/CalRGB"),
            (PdfArray([PdfName("CalGray"), PdfDictionary({})]), "/CalGray"),
            (PdfArray([PdfName("Lab"), PdfDictionary({})]), "/Lab"),
        ],
    )
    def test_part_1_permits_no_rgb_or_device_independent_colour(self, space, needle):
        pdf = _converted("PDF/X-1a:2003")
        _add_resource(pdf, "ColorSpace", "CS0", space)
        assert _has(pdf, needle, "PDF/X-1a:2003")

    def test_iccbased_reported_for_part_1(self):
        pdf = _converted("PDF/X-1a:2003")
        profile = pdf._cos_doc.register_object(
            PdfStream(CMYK_ICC, {PdfName("N"): PdfNumber(4)})
        )
        _add_resource(
            pdf, "ColorSpace", "CS0", PdfArray([PdfName("ICCBased"), profile])
        )
        assert _has(pdf, "/ICCBased", "PDF/X-1a:2003")

    @pytest.mark.parametrize("standard", ["PDF/X-3:2003", "PDF/X-4"])
    def test_device_independent_colour_allowed_from_part_3(self, standard):
        pdf = _converted(standard)
        _add_resource(pdf, "ColorSpace", "CS0", PdfName("DeviceRGB"))
        assert _errors(pdf, standard) == []

    @pytest.mark.parametrize(
        "space",
        [
            PdfName("DeviceCMYK"),
            PdfName("DeviceGray"),
            PdfArray([PdfName("Separation"), PdfName("Spot"), PdfName("DeviceCMYK")]),
        ],
    )
    def test_process_and_spot_colour_are_what_part_1_is_for(self, space):
        pdf = _converted("PDF/X-1a:2003")
        _add_resource(pdf, "ColorSpace", "CS0", space)
        assert _errors(pdf, "PDF/X-1a:2003") == []

    def test_indexed_palette_over_rgb_is_followed(self):
        pdf = _converted("PDF/X-1a:2003")
        _add_resource(
            pdf,
            "ColorSpace",
            "CS0",
            PdfArray(
                [
                    PdfName("Indexed"),
                    PdfName("DeviceRGB"),
                    PdfNumber(1),
                    PdfString(b"\x00\x00\x00"),
                ]
            ),
        )
        assert _has(pdf, "/DeviceRGB", "PDF/X-1a:2003")

    def test_separation_alternate_is_followed(self):
        pdf = _converted("PDF/X-1a:2003")
        _add_resource(
            pdf,
            "ColorSpace",
            "CS0",
            PdfArray([PdfName("Separation"), PdfName("Spot"), PdfName("DeviceRGB")]),
        )
        assert _has(pdf, "/DeviceRGB", "PDF/X-1a:2003")

    def test_shading_colour_space_is_checked(self):
        pdf = _converted("PDF/X-1a:2003")
        _add_resource(
            pdf,
            "Shading",
            "Sh0",
            PdfDictionary({PdfName("ColorSpace"): PdfName("DeviceRGB")}),
        )
        assert _has(pdf, "/DeviceRGB", "PDF/X-1a:2003")

    def test_image_colour_space_is_checked(self):
        pdf = _converted("PDF/X-1a:2003")
        image = PdfStream(
            b"",
            {
                PdfName("Subtype"): PdfName("Image"),
                PdfName("ColorSpace"): PdfName("DeviceRGB"),
            },
        )
        _add_resource(pdf, "XObject", "Im0", image)
        assert _has(pdf, "/DeviceRGB", "PDF/X-1a:2003")

    def test_colour_selected_by_operator_is_found(self):
        """``rg`` names no colour space, so the content itself is scanned."""
        doc = Document()
        doc.pages.add(PageSize.A4)
        doc.pages[0].draw_rectangle(10, 10, 100, 100, stroke_color=(1.0, 0.0, 0.0))
        buffer = io.BytesIO()
        doc.save(buffer)
        doc.close()
        pdf = SimplePdf.from_bytes(buffer.getvalue())
        pdf.convert_to_pdfx("PDF/X-1a:2003", icc_profile=CMYK_ICC)
        assert _has(pdf, "content of page 1 selects RGB", "PDF/X-1a:2003")

    def test_cmyk_content_passes_part_1(self):
        from aspose_pdf import Color

        doc = Document()
        doc.pages.add(PageSize.A4)
        doc.pages[0].draw_rectangle(
            10,
            10,
            100,
            100,
            stroke_color=Color.cmyk(0, 0, 0, 1),
            fill_color=Color.cmyk(0, 0.5, 1, 0),
        )
        buffer = io.BytesIO()
        doc.save(buffer)
        doc.close()
        pdf = SimplePdf.from_bytes(buffer.getvalue())
        assert pdf.convert_to_pdfx("PDF/X-1a:2003", icc_profile=CMYK_ICC) == []


# ---------------------------------------------------------------------------
# Prohibitions shared with PDF/A
# ---------------------------------------------------------------------------
class TestProhibitions:
    def test_encryption_reported(self):
        pdf = _converted("PDF/X-4")
        data = pdf.to_bytes()
        locked = SimplePdf.from_bytes(data)
        locked.encrypt("owner", "owner")
        assert any("prohibits encryption" in m for m in _errors(locked))

    def test_conversion_refuses_an_encrypted_document(self):
        pdf = _pdf()
        pdf.encrypt("user", "owner")
        with pytest.raises(AsposePdfException, match="prohibits encryption"):
            pdf.convert_to_pdfx("PDF/X-4")

    def test_unembedded_font_reported(self):
        pdf = _converted("PDF/X-4")
        font = PdfDictionary(
            {
                PdfName("Type"): PdfName("Font"),
                PdfName("Subtype"): PdfName("TrueType"),
                PdfName("BaseFont"): PdfName("Arial"),
                PdfName("FontDescriptor"): PdfDictionary(
                    {PdfName("Flags"): PdfNumber(32)}
                ),
            }
        )
        _add_resource(pdf, "Font", "F1", font)
        assert _has(pdf, "requires every font embedded")

    def test_font_without_a_descriptor_reported(self):
        pdf = _converted("PDF/X-4")
        font = PdfDictionary(
            {
                PdfName("Type"): PdfName("Font"),
                PdfName("Subtype"): PdfName("TrueType"),
                PdfName("BaseFont"): PdfName("Arial"),
            }
        )
        _add_resource(pdf, "Font", "F1", font)
        assert _has(pdf, "no /FontDescriptor")

    def test_catalog_additional_actions(self):
        pdf = _converted("PDF/X-4")
        _catalog(pdf).mapping[PdfName("AA")] = PdfDictionary({})
        assert _has(pdf, "document-level additional actions")

    def test_conversion_strips_catalog_and_page_actions(self):
        pdf = _pdf()
        _catalog(pdf).mapping[PdfName("AA")] = PdfDictionary({})
        _page(pdf).mapping[PdfName("AA")] = PdfDictionary({})
        pdf.convert_to_pdfx("PDF/X-4")
        assert PdfName("AA") not in _catalog(pdf).mapping
        assert PdfName("AA") not in _page(pdf).mapping

    def test_page_additional_actions_reported(self):
        pdf = _converted("PDF/X-4")
        _page(pdf).mapping[PdfName("AA")] = PdfDictionary({})
        assert _has(pdf, "page additional actions (/AA) on page 1")

    def test_document_javascript(self):
        pdf = _converted("PDF/X-4")
        _catalog(pdf).mapping[PdfName("Names")] = PdfDictionary(
            {PdfName("JavaScript"): PdfDictionary({})}
        )
        assert _has(pdf, "document-level JavaScript")

    def test_conversion_strips_javascript_and_xfa(self):
        pdf = _pdf()
        _catalog(pdf).mapping[PdfName("Names")] = PdfDictionary(
            {PdfName("JavaScript"): PdfDictionary({})}
        )
        _catalog(pdf).mapping[PdfName("AcroForm")] = PdfDictionary(
            {PdfName("XFA"): PdfArray([])}
        )
        pdf.convert_to_pdfx("PDF/X-4")
        names = pdf._resolve(_catalog(pdf).get(PdfName("Names")))
        acro = pdf._resolve(_catalog(pdf).get(PdfName("AcroForm")))
        assert PdfName("JavaScript") not in names.mapping
        assert PdfName("XFA") not in acro.mapping

    def test_xfa_reported(self):
        pdf = _converted("PDF/X-4")
        _catalog(pdf).mapping[PdfName("AcroForm")] = PdfDictionary(
            {PdfName("XFA"): PdfArray([])}
        )
        assert _has(pdf, "prohibits XFA forms")

    @pytest.mark.parametrize(
        "action", ["Launch", "Movie", "Sound", "JavaScript", "ImportData", "ResetForm"]
    )
    def test_prohibited_open_action_types(self, action):
        pdf = _converted("PDF/X-4")
        _catalog(pdf).mapping[PdfName("OpenAction")] = PdfDictionary(
            {PdfName("S"): PdfName(action)}
        )
        assert _has(pdf, f"prohibits /{action} actions")

    def test_a_goto_open_action_is_fine(self):
        pdf = _converted("PDF/X-4")
        _catalog(pdf).mapping[PdfName("OpenAction")] = PdfDictionary(
            {PdfName("S"): PdfName("GoTo")}
        )
        assert _errors(pdf) == []

    @pytest.mark.parametrize(
        "standard", ["PDF/X-1a:2001", "PDF/X-1a:2003", "PDF/X-3:2002", "PDF/X-3:2003"]
    )
    def test_optional_content_prohibited_before_part_4(self, standard):
        pdf = _converted(standard)
        _catalog(pdf).mapping[PdfName("OCProperties")] = PdfDictionary({})
        assert _has(pdf, "prohibits optional content", standard)

    def test_optional_content_allowed_in_part_4(self):
        pdf = _converted("PDF/X-4")
        _catalog(pdf).mapping[PdfName("OCProperties")] = PdfDictionary({})
        assert _errors(pdf) == []

    def test_conversion_strips_optional_content_only_where_prohibited(self):
        stripped = _pdf()
        _catalog(stripped).mapping[PdfName("OCProperties")] = PdfDictionary({})
        stripped.convert_to_pdfx("PDF/X-1a:2003", icc_profile=CMYK_ICC)
        assert PdfName("OCProperties") not in _catalog(stripped).mapping

        kept = _pdf()
        _catalog(kept).mapping[PdfName("OCProperties")] = PdfDictionary({})
        kept.convert_to_pdfx("PDF/X-4")
        assert PdfName("OCProperties") in _catalog(kept).mapping

    def test_postscript_xobject(self):
        pdf = _converted("PDF/X-4")
        _add_resource(
            pdf, "XObject", "PS0", PdfStream(b"", {PdfName("Subtype"): PdfName("PS")})
        )
        assert _has(pdf, "PostScript XObjects")

    def test_reference_xobject(self):
        pdf = _converted("PDF/X-4")
        _add_resource(
            pdf,
            "XObject",
            "Fm0",
            PdfStream(
                b"",
                {
                    PdfName("Subtype"): PdfName("Form"),
                    PdfName("Ref"): PdfDictionary({}),
                },
            ),
        )
        assert _has(pdf, "reference XObjects")

    def test_nested_form_resources_are_walked(self):
        pdf = _converted("PDF/X-1a:2003")
        inner = pdf._cos_doc.register_object(
            PdfStream(b"", {PdfName("Subtype"): PdfName("PS")})
        )
        outer = PdfStream(
            b"",
            {
                PdfName("Subtype"): PdfName("Form"),
                PdfName("Resources"): PdfDictionary(
                    {PdfName("XObject"): PdfDictionary({PdfName("PS0"): inner})}
                ),
            },
        )
        _add_resource(pdf, "XObject", "Fm0", outer)
        assert _has(pdf, "PostScript XObjects", "PDF/X-1a:2003")

    @pytest.mark.parametrize("standard", ALL_STANDARDS)
    def test_lzw_prohibited_everywhere(self, standard):
        pdf = _converted(standard)
        pdf._cos_doc.register_object(
            PdfStream(b"", {PdfName("Filter"): PdfName("LZWDecode")})
        )
        assert _has(pdf, "/LZWDecode stream filter", standard)

    def test_jbig2_prohibited_before_part_4(self):
        pdf = _converted("PDF/X-1a:2003")
        pdf._cos_doc.register_object(
            PdfStream(b"", {PdfName("Filter"): PdfName("JBIG2Decode")})
        )
        assert _has(pdf, "/JBIG2Decode stream filter", "PDF/X-1a:2003")

        allowed = _converted("PDF/X-4")
        allowed._cos_doc.register_object(
            PdfStream(b"", {PdfName("Filter"): PdfName("JBIG2Decode")})
        )
        assert _errors(allowed) == []

    def test_jpeg2000_prohibited_before_part_4(self):
        pdf = _converted("PDF/X-1a:2003")
        _add_resource(
            pdf,
            "XObject",
            "Im0",
            PdfStream(
                b"",
                {
                    PdfName("Subtype"): PdfName("Image"),
                    PdfName("Filter"): PdfName("JPXDecode"),
                },
            ),
        )
        assert _has(pdf, "JPEG 2000 images", "PDF/X-1a:2003")

        allowed = _converted("PDF/X-4")
        _add_resource(
            allowed,
            "XObject",
            "Im0",
            PdfStream(
                b"",
                {
                    PdfName("Subtype"): PdfName("Image"),
                    PdfName("Filter"): PdfName("JPXDecode"),
                },
            ),
        )
        assert _errors(allowed) == []

    def test_alternate_images_prohibited_before_part_4(self):
        pdf = _converted("PDF/X-1a:2003")
        _add_resource(
            pdf,
            "XObject",
            "Im0",
            PdfStream(
                b"",
                {
                    PdfName("Subtype"): PdfName("Image"),
                    PdfName("Alternates"): PdfArray([]),
                },
            ),
        )
        assert _has(pdf, "alternate images", "PDF/X-1a:2003")


# ---------------------------------------------------------------------------
# Annotations
# ---------------------------------------------------------------------------
def _annotate(pdf: SimplePdf, **entries) -> None:
    annot = PdfDictionary({PdfName(k): v for k, v in entries.items()})
    _page(pdf).mapping[PdfName("Annots")] = PdfArray(
        [pdf._cos_doc.register_object(annot)]
    )


class TestAnnotations:
    @pytest.mark.parametrize("subtype", ["Movie", "Sound", "Screen", "RichMedia", "3D"])
    def test_multimedia_annotations_prohibited(self, subtype):
        pdf = _converted("PDF/X-4")
        _annotate(pdf, Subtype=PdfName(subtype))
        assert _has(pdf, f"prohibits /{subtype} annotations")

    def test_printable_annotation_over_the_trim_box_warns(self):
        pdf = _converted("PDF/X-4")
        _annotate(
            pdf,
            Subtype=PdfName("Text"),
            F=PdfNumber(4),
            Rect=PdfArray(
                [PdfNumber(10), PdfNumber(10), PdfNumber(100), PdfNumber(100)]
            ),
        )
        errors, warnings = pdf.check_pdfx_compliance("PDF/X-4")
        assert errors == []
        assert any("reserves for print data" in w for w in warnings)

    def test_a_non_printing_annotation_is_not_warned_about(self):
        pdf = _converted("PDF/X-4")
        _annotate(
            pdf,
            Subtype=PdfName("Text"),
            F=PdfNumber(0),
            Rect=PdfArray(
                [PdfNumber(10), PdfNumber(10), PdfNumber(100), PdfNumber(100)]
            ),
        )
        assert _warnings(pdf) == []

    def test_an_annotation_outside_the_trim_box_is_not_warned_about(self):
        pdf = _pdf()
        pdf.set_page_crop_box(0, (100, 100, 300, 300))
        pdf.convert_to_pdfx("PDF/X-4")
        _annotate(
            pdf,
            Subtype=PdfName("Text"),
            F=PdfNumber(4),
            Rect=PdfArray(
                [PdfNumber(10), PdfNumber(10), PdfNumber(50), PdfNumber(50)]
            ),
        )
        assert _warnings(pdf) == []

    def test_a_trapnet_annotation_is_a_pdfx_construct(self):
        pdf = _converted("PDF/X-4")
        _annotate(
            pdf,
            Subtype=PdfName("TrapNet"),
            F=PdfNumber(4),
            Rect=PdfArray(
                [PdfNumber(0), PdfNumber(0), PdfNumber(612), PdfNumber(792)]
            ),
        )
        errors, warnings = pdf.check_pdfx_compliance("PDF/X-4")
        assert errors == [] and warnings == []


# ---------------------------------------------------------------------------
# Part-4 metadata
# ---------------------------------------------------------------------------
class TestPartFourMetadata:
    def test_conversion_writes_the_dates_and_identifiers(self):
        pdf = _converted("PDF/X-4")
        xmp = pdf._resolve(_catalog(pdf).get(PdfName("Metadata"))).content
        for name in (
            b"xmp:CreateDate",
            b"xmp:ModifyDate",
            b"xmp:MetadataDate",
            b"xmpMM:DocumentID",
            b"xmpMM:InstanceID",
            b"xmpMM:VersionID",
            b"xmpMM:RenditionClass",
        ):
            assert name in xmp, name

    def test_missing_entries_are_warnings(self):
        pdf = _converted("PDF/X-4")
        catalog = _catalog(pdf)
        minimal = (
            b'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>'
            b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
            b'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            b'<rdf:Description rdf:about="" '
            b'xmlns:pdfxid="http://www.npes.org/pdfx/ns/id/">'
            b"<pdfxid:GTS_PDFXVersion>PDF/X-4</pdfxid:GTS_PDFXVersion>"
            b"</rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end=\"w\"?>"
        )
        catalog.mapping[PdfName("Metadata")] = pdf._cos_doc.register_object(
            PdfStream(minimal, {PdfName("Length"): PdfNumber(len(minimal))})
        )
        errors, warnings = pdf.check_pdfx_compliance("PDF/X-4")
        assert errors == []
        assert any("dates and identifies itself in XMP" in w for w in warnings)

    def test_earlier_parts_are_not_asked_for_them(self):
        pdf = _converted("PDF/X-1a:2003")
        assert _warnings(pdf, "PDF/X-1a:2003") == []


# ---------------------------------------------------------------------------
# Reading another producer's identification
# ---------------------------------------------------------------------------
class TestForeignIdentification:
    """A namespace may be bound on the property's own element.

    Only the namespace URI is normative in XMP; the prefix and where it is
    declared are the producer's choice. pikepdf writes a property whose
    namespace nothing else in the packet uses as
    ``<pdfxid:GTS_PDFXVersion xmlns:pdfxid="...">PDF/X-4</...>``, and reading
    only ``name>`` reported such a packet as not declaring the property --
    which rejected a conforming file written by another tool.
    """

    @pytest.mark.parametrize(
        ("packet", "name", "expected"),
        [
            (
                '<pdfxid:GTS_PDFXVersion xmlns:pdfxid="http://www.npes.org/'
                'pdfx/ns/id/">PDF/X-4</pdfxid:GTS_PDFXVersion>',
                "pdfxid:GTS_PDFXVersion",
                "PDF/X-4",
            ),
            ("<pdfaid:part>2</pdfaid:part>", "pdfaid:part", "2"),
            ('<rdf:Description pdfaid:part="1">', "pdfaid:part", "1"),
            ("<pdfuaid:part  >1</pdfuaid:part>", "pdfuaid:part", "1"),
            ("<x:other>y</x:other>", "pdfaid:part", None),
        ],
    )
    def test_element_and_attribute_forms(self, packet, name, expected):
        assert conformance._xmp_value(packet, name) == expected

    def test_an_element_scoped_namespace_identifies_a_part_4_file(self):
        pdf = _converted("PDF/X-4")
        catalog = _catalog(pdf)
        packet = (
            b'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>'
            b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
            b'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            b'<rdf:Description rdf:about=""><pdfxid:GTS_PDFXVersion '
            b'xmlns:pdfxid="http://www.npes.org/pdfx/ns/id/">PDF/X-4'
            b"</pdfxid:GTS_PDFXVersion></rdf:Description></rdf:RDF>"
            b'</x:xmpmeta><?xpacket end="w"?>'
        )
        catalog.mapping[PdfName("Metadata")] = pdf._cos_doc.register_object(
            PdfStream(packet, {PdfName("Length"): PdfNumber(len(packet))})
        )
        assert _errors(pdf) == []


# ---------------------------------------------------------------------------
# Round trips
# ---------------------------------------------------------------------------
class TestRoundTrip:
    @pytest.mark.parametrize("standard", ALL_STANDARDS)
    def test_converted_document_survives_a_save(self, standard):
        pdf = _converted(standard)
        reloaded = SimplePdf.from_bytes(pdf.to_bytes())
        errors, warnings = reloaded.check_pdfx_compliance(standard)
        assert errors == [] and warnings == []

    @pytest.mark.parametrize("standard", ALL_STANDARDS)
    def test_conversion_is_idempotent(self, standard):
        pdf = _converted(standard)
        first = pdf.to_bytes()
        pdf.convert_to_pdfx(standard, icc_profile=CMYK_ICC)
        reloaded = SimplePdf.from_bytes(pdf.to_bytes())
        assert reloaded.check_pdfx_compliance(standard)[0] == []
        assert len(pdf.to_bytes()) == pytest.approx(len(first), rel=0.1)

    def test_header_version_is_raised_not_lowered(self):
        pdf = _converted("PDF/X-4")
        assert pdf.pdf_version == "1.6"
        newer = SimplePdf.from_bytes(_minimal_pdf_bytes().replace(b"%PDF-1.4", b"%PDF-1.7"))
        newer.convert_to_pdfx("PDF/X-1a:2003", icc_profile=CMYK_ICC)
        assert newer.pdf_version == "1.7"

    def test_the_header_version_is_not_a_conformance_rule(self):
        """ISO 15930-4 clause 5: the header shall not decide conformance."""
        pdf = _converted("PDF/X-4")
        pdf.pdf_version = "1.3"
        assert pdf.check_pdfx_compliance("PDF/X-4")[0] == []


# ---------------------------------------------------------------------------
# The public surface
# ---------------------------------------------------------------------------
class TestDocumentSurface:
    def _authored(self) -> bytes:
        doc = Document()
        doc.pages.add(PageSize.A4)
        doc.pages[0].add_text("Print me", 72, 700)
        buffer = io.BytesIO()
        doc.save(buffer)
        doc.close()
        return buffer.getvalue()

    def test_validate_then_convert_then_validate(self):
        with Document(self._authored()) as doc:
            before = doc.validate_pdfx("PDF/X-4")
            assert not before.is_valid
            assert before.standard == "PDF/X-4"
            assert before.is_heuristic is True
            assert doc.convert_to_pdfx("PDF/X-4", title="Poster") == []
            assert doc.is_pdfx_compliant("PDF/X-4")

    def test_convert_accepts_an_enum_member(self):
        with Document(self._authored()) as doc:
            assert doc.convert_to_pdfx(PdfXStandard.X4) == []

    def test_a_saved_document_still_validates(self, tmp_path):
        target = tmp_path / "x4.pdf"
        with Document(self._authored()) as doc:
            doc.convert_to_pdfx("PDF/X-4")
            doc.save(target)
        with Document(target) as doc:
            assert doc.validate_pdfx("PDF/X-4").is_valid

    def test_unknown_standard_raises(self):
        with Document(self._authored()) as doc:
            with pytest.raises(ValueError, match="Unknown PDF/X standard"):
                doc.validate_pdfx("PDF/X-9")

    def test_a_document_with_no_cos_graph_behaves_as_pdfa_does(self):
        """A document that has never been written reports no issues.

        There is nothing to inspect until a save builds the object graph, and
        this follows :meth:`Document.validate_pdfa`, which answers the same way
        for the same reason. Consistency between the two matters more than
        either answer on its own; convert first, or save and reopen.
        """
        doc = Document()
        assert doc.validate_pdfx("PDF/X-4").errors == []
        assert doc.validate_pdfa("1b").errors == []
        doc.close()

    def test_converting_a_document_with_no_cos_graph_raises(self):
        doc = Document()
        with pytest.raises(AsposePdfException, match="requires a document loaded"):
            doc.convert_to_pdfx("PDF/X-4")
        doc.close()

    def test_len_counts_errors(self):
        with Document(self._authored()) as doc:
            result = doc.validate_pdfx("PDF/X-4")
            assert len(result) == len(result.errors)


class TestValidationResult:
    def test_defaults(self):
        result = PdfXValidationResult()
        assert result.is_valid and result.errors == [] and result.warnings == []
        assert result.standard == "" and result.is_heuristic is True

    def test_add_and_serialise(self):
        result = PdfXValidationResult(standard="PDF/X-4")
        result.add_error("boom")
        result.add_warning("hmm")
        assert not result.is_valid
        assert result.to_dict() == {
            "is_valid": False,
            "is_heuristic": True,
            "standard": "PDF/X-4",
            "errors": ["boom"],
            "warnings": ["hmm"],
        }
        assert "PDF/X-4" in repr(result)

    @pytest.mark.parametrize("method", ["add_error", "add_warning"])
    def test_non_string_rejected(self, method):
        result = PdfXValidationResult()
        with pytest.raises(TypeError):
            getattr(result, method)(42)

    def test_notice_is_explicit_about_the_lack_of_a_validator(self):
        assert "veraPDF" in PdfXValidationResult.HEURISTIC_VALIDATION_NOTICE


class TestValidatorPlugin:
    def _x4_bytes(self) -> bytes:
        doc = Document()
        doc.pages.add(PageSize.A4)
        buffer = io.BytesIO()
        doc.save(buffer)
        doc.close()
        with Document(buffer.getvalue()) as reopened:
            reopened.convert_to_pdfx("PDF/X-4")
            out = io.BytesIO()
            reopened.save(out)
        return out.getvalue()

    def test_bytes_and_path_inputs(self, tmp_path):
        data = self._x4_bytes()
        path = tmp_path / "x4.pdf"
        path.write_bytes(data)
        options = PdfXValidateOptions()
        options.pdfx_standard = "PDF/X-4"
        options.add_input(data).add_input(path).add_input(bytearray(data))
        options.add_input(io.BytesIO(data))
        results = PdfXValidator().process(options)
        assert len(results) == 4
        assert all(r.is_valid for r in results)
        assert all(r.standard == "PDF/X-4" for r in results)

    def test_default_standard_is_the_newest(self):
        assert PdfXValidateOptions().pdfx_standard == "PDF/X-4"

    def test_missing_file_rejected(self, tmp_path):
        with pytest.raises(PdfIOException):
            PdfXValidateOptions().add_input(tmp_path / "absent.pdf")

    def test_unsupported_input_rejected(self):
        with pytest.raises(PdfValidationException, match="Unsupported input type"):
            PdfXValidateOptions().add_input(42)

    def test_inputs_are_copied(self):
        options = PdfXValidateOptions()
        options.add_input(b"%PDF-1.4")
        inputs = options.inputs
        inputs.append(b"other")
        assert len(options.inputs) == 1

    def test_a_non_conforming_input_reports_its_errors(self):
        options = PdfXValidateOptions()
        options.add_input(_minimal_pdf_bytes())
        (result,) = PdfXValidator().process(options)
        assert not result.is_valid
        assert any("TrimBox" in e for e in result.errors)


def test_pdfx_does_not_disturb_pdfa_conversion():
    """The shared ICC helper still writes what the PDF/A conversion expects."""
    pdf = _pdf()
    pdf.convert_to_pdfa("2b")
    assert pdf.check_pdfa_compliance("2b") == []
    intents = pdf._resolve(_catalog(pdf).get(PdfName("OutputIntents")))
    intent = pdf._resolve(intents.items[0])
    profile = pdf._resolve(intent.get(PdfName("DestOutputProfile")))
    assert profile[PdfName("N")].value == 3
    assert pdf._get_name(profile.get(PdfName("Alternate"))) == "DeviceRGB"
    assert pdf._get_name(intent.get(PdfName("S"))) == "GTS_PDFA1"


def test_a_pdfa_document_can_also_be_pdfx():
    """Nothing in either conversion undoes the other's identification."""
    pdf = _pdf()
    pdf.convert_to_pdfa("2b")
    pdf.convert_to_pdfx("PDF/X-4", icc_profile=_minimal_srgb_icc_profile())
    assert pdf.check_pdfx_compliance("PDF/X-4")[0] == []
    xmp = pdf._resolve(_catalog(pdf).get(PdfName("Metadata"))).content
    assert b"GTS_PDFXVersion" in xmp
