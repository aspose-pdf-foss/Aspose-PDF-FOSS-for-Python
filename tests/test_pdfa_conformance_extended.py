"""Extended PDF/A structural conformance checks (engine.conformance).

Covers the rules added on top of the original heuristic set: trailer /ID,
header version, catalog /AA and /OCProperties, AcroForm NeedAppearances/XFA,
prohibited annotations/actions/flags, transparency (ExtGState + groups),
PostScript XObjects, image interpolation, and PDF/A level-A tagging — plus the
matching remediation performed by ``convert_to_pdfa``.
"""

from __future__ import annotations

from aspose_pdf.document import Document
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfBoolean,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfStream,
    PdfString,
)


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


def _catalog(pdf: SimplePdf) -> PdfDictionary:
    return pdf._resolve(pdf._cos_doc.trailer.get(PdfName("Root")))


def _page(pdf: SimplePdf) -> PdfDictionary:
    return pdf._get_page_dict(0)


def _errors(pdf: SimplePdf, level: str = "1b") -> list[str]:
    return pdf.check_pdfa_compliance(level)


def _has(pdf: SimplePdf, needle: str, level: str = "1b") -> bool:
    return any(needle in e for e in _errors(pdf, level))


# ---------------------------------------------------------------------------
# Trailer /ID
# ---------------------------------------------------------------------------
class TestTrailerId:
    def test_missing_id_reported(self):
        pdf = _pdf()
        assert _has(pdf, "file identifier")

    def test_conversion_adds_id(self):
        pdf = _pdf()
        pdf.convert_to_pdfa("1b")
        assert not _has(pdf, "file identifier")
        id_obj = pdf._resolve(pdf._cos_doc.trailer.get(PdfName("ID")))
        assert isinstance(id_obj, PdfArray) and len(id_obj.items) == 2


# ---------------------------------------------------------------------------
# Header version
# ---------------------------------------------------------------------------
class TestHeaderVersion:
    def test_pdf17_rejected_for_part1(self):
        pdf = _pdf()
        pdf.pdf_version = "1.7"
        assert _has(pdf, "PDF version 1.4")

    def test_pdf17_allowed_for_part2(self):
        pdf = _pdf()
        pdf.pdf_version = "1.7"
        assert not _has(pdf, "PDF version", level="2b")

    def test_conversion_downgrades_for_part1(self):
        pdf = _pdf()
        pdf.pdf_version = "1.7"
        pdf.convert_to_pdfa("1b")
        assert pdf.pdf_version == "1.4"
        assert not _has(pdf, "PDF version 1.4")


# ---------------------------------------------------------------------------
# Catalog: additional actions & optional content
# ---------------------------------------------------------------------------
class TestCatalogRules:
    def test_additional_actions_reported(self):
        pdf = _pdf()
        _catalog(pdf).mapping[PdfName("AA")] = PdfDictionary({})
        assert _has(pdf, "additional actions")

    def test_additional_actions_removed_on_convert(self):
        pdf = _pdf()
        _catalog(pdf).mapping[PdfName("AA")] = PdfDictionary({})
        pdf.convert_to_pdfa("1b")
        assert not _has(pdf, "additional actions")

    def test_ocproperties_reported_for_part1(self):
        pdf = _pdf()
        _catalog(pdf).mapping[PdfName("OCProperties")] = PdfDictionary({})
        assert _has(pdf, "optional content")

    def test_ocproperties_allowed_for_part2(self):
        pdf = _pdf()
        _catalog(pdf).mapping[PdfName("OCProperties")] = PdfDictionary({})
        assert not _has(pdf, "optional content", level="2b")

    def test_ocproperties_removed_on_convert_part1(self):
        pdf = _pdf()
        _catalog(pdf).mapping[PdfName("OCProperties")] = PdfDictionary({})
        pdf.convert_to_pdfa("1b")
        assert PdfName("OCProperties") not in _catalog(pdf).mapping


# ---------------------------------------------------------------------------
# AcroForm
# ---------------------------------------------------------------------------
class TestAcroForm:
    def _add_acroform(self, pdf: SimplePdf, entries: dict) -> PdfDictionary:
        acro = PdfDictionary(entries)
        ref = pdf._cos_doc.register_object(acro)
        _catalog(pdf).mapping[PdfName("AcroForm")] = ref
        return acro

    def test_need_appearances_reported(self):
        pdf = _pdf()
        self._add_acroform(pdf, {PdfName("NeedAppearances"): PdfBoolean(True)})
        assert _has(pdf, "NeedAppearances")

    def test_xfa_reported(self):
        pdf = _pdf()
        self._add_acroform(pdf, {PdfName("XFA"): PdfArray([])})
        assert _has(pdf, "XFA")

    def test_conversion_clears_acroform_issues(self):
        pdf = _pdf()
        self._add_acroform(
            pdf,
            {
                PdfName("NeedAppearances"): PdfBoolean(True),
                PdfName("XFA"): PdfArray([]),
            },
        )
        pdf.convert_to_pdfa("1b")
        assert not _has(pdf, "NeedAppearances")
        assert not _has(pdf, "XFA")


# ---------------------------------------------------------------------------
# Annotations
# ---------------------------------------------------------------------------
class TestAnnotations:
    def _add_annot(self, pdf: SimplePdf, entries: dict) -> PdfDictionary:
        annot = PdfDictionary(entries)
        ref = pdf._cos_doc.register_object(annot)
        _page(pdf).mapping[PdfName("Annots")] = PdfArray([ref])
        return annot

    def test_prohibited_subtype_reported(self):
        pdf = _pdf()
        self._add_annot(
            pdf,
            {PdfName("Subtype"): PdfName("Sound"), PdfName("F"): PdfNumber(4)},
        )
        assert _has(pdf, "/Sound annotations")

    def test_file_attachment_reported_except_a3(self):
        pdf = _pdf()
        self._add_annot(
            pdf,
            {
                PdfName("Subtype"): PdfName("FileAttachment"),
                PdfName("F"): PdfNumber(4),
            },
        )
        assert _has(pdf, "FileAttachment")
        assert not _has(pdf, "FileAttachment", level="3b")

    def test_missing_print_flag_reported(self):
        pdf = _pdf()
        self._add_annot(
            pdf, {PdfName("Subtype"): PdfName("Text"), PdfName("F"): PdfNumber(0)}
        )
        assert _has(pdf, "Print flag")

    def test_hidden_flag_reported(self):
        pdf = _pdf()
        # Print (4) + Hidden (2)
        self._add_annot(
            pdf, {PdfName("Subtype"): PdfName("Text"), PdfName("F"): PdfNumber(6)}
        )
        assert _has(pdf, "Hidden")

    def test_popup_exempt_from_flag_rules(self):
        pdf = _pdf()
        self._add_annot(
            pdf, {PdfName("Subtype"): PdfName("Popup"), PdfName("F"): PdfNumber(0)}
        )
        assert not _has(pdf, "Print flag")

    def test_missing_appearance_is_warning_not_error(self):
        pdf = _pdf()
        self._add_annot(
            pdf, {PdfName("Subtype"): PdfName("Text"), PdfName("F"): PdfNumber(4)}
        )
        errors, warnings = pdf.check_pdfa_compliance_detailed("1b")
        assert any("appearance" in w for w in warnings)
        assert not any("appearance" in e for e in errors)

    def test_conversion_fixes_flags(self):
        pdf = _pdf()
        self._add_annot(
            pdf, {PdfName("Subtype"): PdfName("Text"), PdfName("F"): PdfNumber(2)}
        )
        pdf.convert_to_pdfa("1b")
        assert not _has(pdf, "Print flag")
        assert not _has(pdf, "Hidden")
        annot = pdf._resolve(
            pdf._resolve(_page(pdf).mapping.get(PdfName("Annots"))).items[0]
        )
        flags = int(annot.mapping[PdfName("F")].value)
        assert flags & 4  # Print set
        assert not flags & 2  # Hidden clear

    def test_prohibited_action_reported(self):
        pdf = _pdf()
        action = PdfDictionary({PdfName("S"): PdfName("Launch")})
        self._add_annot(
            pdf,
            {
                PdfName("Subtype"): PdfName("Link"),
                PdfName("F"): PdfNumber(4),
                PdfName("A"): action,
            },
        )
        assert _has(pdf, "/Launch actions")


# ---------------------------------------------------------------------------
# Transparency / ExtGState / XObjects (PDF/A-1)
# ---------------------------------------------------------------------------
class TestTransparencyAndXObjects:
    def _set_extgstate(self, pdf: SimplePdf, entries: dict) -> None:
        gs = PdfDictionary(entries)
        res = PdfDictionary(
            {PdfName("ExtGState"): PdfDictionary({PdfName("GS0"): gs})}
        )
        _page(pdf).mapping[PdfName("Resources")] = res

    def _set_xobject(self, pdf: SimplePdf, stream: PdfStream) -> None:
        ref = pdf._cos_doc.register_object(stream)
        res = PdfDictionary(
            {PdfName("XObject"): PdfDictionary({PdfName("X0"): ref})}
        )
        _page(pdf).mapping[PdfName("Resources")] = res

    def test_constant_alpha_reported_part1(self):
        pdf = _pdf()
        self._set_extgstate(pdf, {PdfName("ca"): PdfNumber(0.5)})
        assert _has(pdf, "constant alpha")
        assert not _has(pdf, "constant alpha", level="2b")

    def test_blend_mode_reported_part1(self):
        pdf = _pdf()
        self._set_extgstate(pdf, {PdfName("BM"): PdfName("Multiply")})
        assert _has(pdf, "blend mode")

    def test_normal_blend_mode_ok(self):
        pdf = _pdf()
        self._set_extgstate(pdf, {PdfName("BM"): PdfName("Normal")})
        assert not _has(pdf, "blend mode")

    def test_softmask_reported_part1(self):
        pdf = _pdf()
        self._set_extgstate(
            pdf, {PdfName("SMask"): PdfDictionary({PdfName("S"): PdfName("Alpha")})}
        )
        assert _has(pdf, "soft masks")

    def test_transfer_function_reported(self):
        pdf = _pdf()
        self._set_extgstate(pdf, {PdfName("TR"): PdfArray([])})
        assert _has(pdf, "transfer functions")

    def test_identity_transfer_function_ok(self):
        pdf = _pdf()
        self._set_extgstate(pdf, {PdfName("TR"): PdfName("Identity")})
        assert not _has(pdf, "transfer functions")

    def test_postscript_xobject_reported(self):
        pdf = _pdf()
        self._set_xobject(pdf, PdfStream(content=b"", mapping={PdfName("Subtype"): PdfName("PS")}))
        assert _has(pdf, "PostScript XObjects")

    def test_image_interpolate_reported(self):
        pdf = _pdf()
        self._set_xobject(
            pdf,
            PdfStream(
                content=b"",
                mapping={
                    PdfName("Subtype"): PdfName("Image"),
                    PdfName("Interpolate"): PdfBoolean(True),
                },
            ),
        )
        assert _has(pdf, "interpolation")

    def test_form_xobject_transparency_group_part1(self):
        pdf = _pdf()
        group = PdfDictionary({PdfName("S"): PdfName("Transparency")})
        self._set_xobject(
            pdf,
            PdfStream(
                content=b"",
                mapping={PdfName("Subtype"): PdfName("Form"), PdfName("Group"): group},
            ),
        )
        assert _has(pdf, "transparency groups")

    def test_page_transparency_group_part1(self):
        pdf = _pdf()
        _page(pdf).mapping[PdfName("Group")] = PdfDictionary(
            {PdfName("S"): PdfName("Transparency")}
        )
        assert _has(pdf, "transparency groups")


# ---------------------------------------------------------------------------
# PDF/A level A tagging
# ---------------------------------------------------------------------------
class TestLevelATagging:
    def test_level_a_requires_structtreeroot(self):
        pdf = _pdf()
        errors = _errors(pdf, "1a")
        assert any("StructTreeRoot" in e for e in errors)
        assert any("Marked" in e for e in errors)

    def test_level_b_does_not_require_tagging(self):
        pdf = _pdf()
        assert not _has(pdf, "StructTreeRoot", level="1b")

    def test_level_a_tagged_passes(self):
        pdf = _pdf()
        cat = _catalog(pdf)
        cat.mapping[PdfName("StructTreeRoot")] = PdfDictionary(
            {PdfName("Type"): PdfName("StructTreeRoot")}
        )
        cat.mapping[PdfName("MarkInfo")] = PdfDictionary(
            {PdfName("Marked"): PdfBoolean(True)}
        )
        cat.mapping[PdfName("Lang")] = PdfString("en")
        assert not any("StructTreeRoot" in e for e in _errors(pdf, "1a"))
        assert not any("Marked" in e for e in _errors(pdf, "1a"))


# ---------------------------------------------------------------------------
# Document-level wiring: warnings surface through PdfAValidationResult
# ---------------------------------------------------------------------------
class TestDocumentWiring:
    def test_warnings_surface_on_result(self):
        doc = Document()
        doc.load_from(_minimal_pdf_bytes())
        engine = doc._engine_pdf
        annot = PdfDictionary(
            {PdfName("Subtype"): PdfName("Text"), PdfName("F"): PdfNumber(4)}
        )
        ref = engine._cos_doc.register_object(annot)
        engine._get_page_dict(0).mapping[PdfName("Annots")] = PdfArray([ref])
        result = doc.validate_pdfa("1b")
        assert any("appearance" in w for w in result.warnings)

    def test_clean_minimal_still_converts_to_valid(self):
        doc = Document()
        doc.load_from(_minimal_pdf_bytes())
        assert doc.convert_to_pdfa("1b") == []
        assert doc.is_pdfa_compliant("1b")


# ---------------------------------------------------------------------------
# Annotation constant opacity (/CA) — PDF/A-1
# ---------------------------------------------------------------------------
class TestAnnotationOpacity:
    def _add_annot(self, pdf: SimplePdf, entries: dict) -> None:
        ref = pdf._cos_doc.register_object(PdfDictionary(entries))
        _page(pdf).mapping[PdfName("Annots")] = PdfArray([ref])

    def test_ca_below_one_reported_part1(self):
        pdf = _pdf()
        self._add_annot(
            pdf,
            {
                PdfName("Subtype"): PdfName("Text"),
                PdfName("F"): PdfNumber(4),
                PdfName("CA"): PdfNumber(0.5),
            },
        )
        assert _has(pdf, "constant opacity")

    def test_ca_one_not_reported(self):
        pdf = _pdf()
        self._add_annot(
            pdf,
            {
                PdfName("Subtype"): PdfName("Text"),
                PdfName("F"): PdfNumber(4),
                PdfName("CA"): PdfNumber(1),
            },
        )
        assert not _has(pdf, "constant opacity")

    def test_ca_below_one_allowed_part2(self):
        pdf = _pdf()
        self._add_annot(
            pdf,
            {
                PdfName("Subtype"): PdfName("Text"),
                PdfName("F"): PdfNumber(4),
                PdfName("CA"): PdfNumber(0.5),
            },
        )
        assert not _has(pdf, "constant opacity", level="2b")


# ---------------------------------------------------------------------------
# Prohibited stream filters: /Crypt
# ---------------------------------------------------------------------------
class TestCryptFilter:
    def _add_crypt_stream(self, pdf: SimplePdf) -> None:
        stream = PdfStream(
            content=b"x",
            mapping={PdfName("Filter"): PdfName("Crypt"), PdfName("Length"): PdfNumber(1)},
        )
        pdf._cos_doc.register_object(stream)

    def test_crypt_filter_reported_all_parts(self):
        pdf = _pdf()
        self._add_crypt_stream(pdf)
        assert _has(pdf, "Crypt stream filter")
        assert _has(pdf, "Crypt stream filter", level="2b")
        assert _has(pdf, "Crypt stream filter", level="3b")


# ---------------------------------------------------------------------------
# Catalog /Requirements
# ---------------------------------------------------------------------------
class TestRequirements:
    def test_requirements_reported(self):
        pdf = _pdf()
        _catalog(pdf).mapping[PdfName("Requirements")] = PdfArray([])
        assert _has(pdf, "/Requirements")


# ---------------------------------------------------------------------------
# CIDFontType2 /CIDToGIDMap
# ---------------------------------------------------------------------------
class TestCidToGidMap:
    def _add_cidfont(self, pdf: SimplePdf, with_map: bool) -> None:
        entries = {
            PdfName("Type"): PdfName("Font"),
            PdfName("Subtype"): PdfName("CIDFontType2"),
            PdfName("BaseFont"): PdfName("ABCDEF+Arial"),
        }
        if with_map:
            entries[PdfName("CIDToGIDMap")] = PdfName("Identity")
        pdf._cos_doc.register_object(PdfDictionary(entries))

    def test_missing_cidtogidmap_reported(self):
        pdf = _pdf()
        self._add_cidfont(pdf, with_map=False)
        assert _has(pdf, "CIDToGIDMap")

    def test_present_cidtogidmap_ok(self):
        pdf = _pdf()
        self._add_cidfont(pdf, with_map=True)
        assert not _has(pdf, "CIDToGIDMap")


# ---------------------------------------------------------------------------
# PDF/A-3 embedded files require /AFRelationship
# ---------------------------------------------------------------------------
class TestEmbeddedFilesA3:
    def _add_embedded_file(self, pdf: SimplePdf, with_af: bool) -> None:
        ef = PdfStream(
            content=b"data",
            mapping={PdfName("Type"): PdfName("EmbeddedFile"), PdfName("Length"): PdfNumber(4)},
        )
        ef_ref = pdf._cos_doc.register_object(ef)
        entries = {
            PdfName("Type"): PdfName("Filespec"),
            PdfName("F"): PdfString("a.txt"),
            PdfName("EF"): PdfDictionary({PdfName("F"): ef_ref}),
        }
        if with_af:
            entries[PdfName("AFRelationship")] = PdfName("Data")
        fs_ref = pdf._cos_doc.register_object(PdfDictionary(entries))
        ef_tree = PdfDictionary(
            {PdfName("Names"): PdfArray([PdfString("a.txt"), fs_ref])}
        )
        _catalog(pdf).mapping[PdfName("Names")] = PdfDictionary(
            {PdfName("EmbeddedFiles"): ef_tree}
        )

    def test_missing_af_relationship_reported_a3(self):
        pdf = _pdf()
        self._add_embedded_file(pdf, with_af=False)
        assert _has(pdf, "AFRelationship", level="3b")

    def test_present_af_relationship_ok_a3(self):
        pdf = _pdf()
        self._add_embedded_file(pdf, with_af=True)
        assert not _has(pdf, "AFRelationship", level="3b")

    def test_not_checked_below_a3(self):
        pdf = _pdf()
        self._add_embedded_file(pdf, with_af=False)
        assert not _has(pdf, "AFRelationship", level="1b")

    def test_convert_a3_adds_af_relationship(self):
        pdf = _pdf()
        self._add_embedded_file(pdf, with_af=False)
        pdf.convert_to_pdfa("3b")
        assert not _has(pdf, "AFRelationship", level="3b")


# ---------------------------------------------------------------------------
# XMP /Metadata stream must be unfiltered (ISO 19005 6.7.3)
# ---------------------------------------------------------------------------
class TestMetadataUnfiltered:
    def _set_metadata(self, pdf: SimplePdf, *, filtered: bool) -> None:
        mapping = {PdfName("Type"): PdfName("Metadata"), PdfName("Subtype"): PdfName("XML")}
        if filtered:
            mapping[PdfName("Filter")] = PdfName("FlateDecode")
        md = pdf._cos_doc.register_object(
            PdfStream(content=b"<?xpacket?><x:xmpmeta/>", mapping=mapping)
        )
        _catalog(pdf).mapping[PdfName("Metadata")] = md

    def test_filtered_metadata_reported(self):
        pdf = _pdf()
        self._set_metadata(pdf, filtered=True)
        assert _has(pdf, "unfiltered", level="2b")

    def test_unfiltered_metadata_ok(self):
        pdf = _pdf()
        self._set_metadata(pdf, filtered=False)
        assert not _has(pdf, "unfiltered", level="2b")

    def test_no_metadata_not_reported(self):
        # Absent /Metadata is a different rule; this check must stay silent.
        assert not _has(_pdf(), "unfiltered", level="2b")

    def test_convert_writes_unfiltered_metadata(self):
        pdf = _pdf()
        self._set_metadata(pdf, filtered=True)
        pdf.convert_to_pdfa("2b")
        assert not _has(pdf, "unfiltered", level="2b")
