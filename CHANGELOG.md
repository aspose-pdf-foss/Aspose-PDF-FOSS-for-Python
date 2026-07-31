# Changelog

All notable changes to Aspose.PDF FOSS for Python will be documented in this
file.

The project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- The page renderer now draws glyphs for Type0 fonts whose `/Encoding` names one
  of the eight bundled predefined CJK CMaps, instead of falling back to boxes. It
  splits the show string on the CMap's mixed single/double-byte codespaces, maps
  each code to a CID against the descendant `CIDSystemInfo`, and fills the real
  outlines through `CIDToGIDMap` (CIDFontType2) or the CFF charset
  (CIDFontType0). Unbundled names, embedded CMap streams, and mismatched
  collections still render as boxes.
- `Document(...)` no longer discards its arguments. It now loads the supplied
  source with the same semantics and errors as `Document.load_from(...)`, and
  rejects unknown arguments, so `Document("input.pdf")` can no longer return an
  empty document. `aspose_pdf.generated.document.Document` gained the same
  signature.
- Compatibility placeholders for unimplemented formats now fail explicitly
  through the new `UnsupportedFeatureException` (also a `NotImplementedError`):
  load options such as `SvgLoadOptions` are rejected by the constructor and
  `load_from()`, and `Document.save(destination, save_format)` rejects
  `SaveFormat.PPTX`, non-PDF `DocFormat` members, `HtmlSaveOptions`,
  `MarkdownSaveOptions`, and `PrinterSettings` before writing anything.
- Documented the full inventory of unimplemented compatibility surfaces in
  `supported-features.md`.
- Extended the scheduled security audit to install and audit the `text-layout`
  extra (uharfbuzz, python-bidi, fonttools) alongside `images` and `woff2`, and
  made the minimal-install CI job assert those three modules are absent too, so
  every optional runtime dependency is covered by both gates.
- Added function-based PDF shadings with multidimensional sampled functions,
  shading matrix/domain/bounds/background handling, and Separation/DeviceN
  alternate-colour conversion.
- Replaced fixed patch-mesh tessellation with bounded device-scale-adaptive
  subdivision and added composite preview for common CMYK and spot overprint
  cases through `/OP`, `/op`, and `/OPM`.
- Extended the attachment API with associated-file relationships and typed
  removal: `Document.add_attachment(..., relationship=...)` writes a validated
  `/AFRelationship` (`AF_RELATIONSHIPS`), `FileSpecification` exposes a
  `relationship` field read back from the file spec, and
  `Document.remove_attachment(name)` deletes an embedded file (dropping the
  `/Names /EmbeddedFiles` tree when the last one is removed). Re-adding a name
  now fully supersedes any previously loaded metadata.
- Added public signature-field authoring: `Form.add_signature_field(name, page,
  rect, ...)` creates an empty `/FT /Sig` field with a page widget, sets the
  AcroForm `/SigFlags` SignaturesExist bit (preserving existing bits), renders an
  empty box, and carries no value until signed. Signature fields round-trip,
  report as a `signature` field type (new `FieldType.SIGNATURE`), and can be
  removed like any other field. Signing an authored field and seed-value/lock
  dictionaries remain out of scope.
- Added a public byte-preserving incremental save: `Document.save(...,
  incremental=True)` emits the original file bytes verbatim and appends only the
  objects added or modified since load as a new revision chained through
  `/Prev`. Change detection compares each object's canonical (key-sorted)
  serialization against a re-parse of the original, so unchanged objects are not
  re-emitted and an existing signature's byte range stays intact. Documents
  built from scratch fall back to a full write; encrypted or to-be-signed
  documents are rejected.
- Corrected the overprint composite preview to work in the subtractive device
  colorant model: a non-zero source colorant now replaces the backdrop colorant
  while a zero-tint colorant leaves it untouched (nonzero-overprint semantics for
  DeviceCMYK/DeviceGray, colorant isolation for Separation/DeviceN). The previous
  blanket `Multiply` darkened untouched colorants and could not replace a
  colorant with a lighter tint of itself.
- Corrected project metadata links and added minimal-install CI coverage.
- Added bounded fuzz targets and a redistributable parser corpus, replaced the
  skipped signature-extraction placeholder with an end-to-end test, and applied
  `PdfLoadLimits` to authored PNG and WOFF/WOFF2 decoding paths.
