# Changelog

All notable changes to Aspose.PDF FOSS for Python will be documented in this
file.

The project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **`Page.add_image()` accepts every PNG form.** Adam7 **interlacing** and bit
  depths **1/2/4/16** were hard rejections; an ordinary interlaced or 16-bit PNG
  could not be embedded at all. The decoder now reassembles the seven interlace
  passes and normalises each allowed depth to 8 bits per sample — 16-bit keeps
  the high byte, sub-byte greyscale is scaled to full range, and palette indices
  are looked up rather than scaled, which would have corrupted the colour. Bit
  depths a colour type does not allow (ISO 15948 table 11) are now rejected with
  a specific message instead of being decoded as garbage.

- **PDF/A-1 conversion drops inert transparency groups.** A `/Group /S
  /Transparency` on a page or form XObject is removed when nothing it reaches
  actually uses transparency — no ExtGState soft mask, non-Normal blend or alpha
  below 1, no image `/SMask`/`/Mask`, no nested group. Producers stamp such
  groups routinely, so this converts a real class of documents losslessly. The
  scan is conservative (every ExtGState in a resource dictionary counts, not just
  the ones the content selects), and transparency that is genuinely used stays
  and is reported: flattening it correctly requires compositing the page against
  its backdrop — rasterizing away live text and vectors — which is the caller's
  decision, not a silent repair.
- **Push-button icons.** `Form.add_push_button(icon=…)` takes JPEG or
  non-interlaced 8-bit PNG bytes, wraps them in a form XObject as `/MK /I` (an
  icon must be a *form*, not an image), and draws it into the normal, rollover
  and down faces scaled proportionally and centred. `/MK /IF` is written to
  match the baked appearance, and `/MK /TP` is 1 for an icon alone or 2 with the
  caption below it.
- **Submit and reset form actions.** `SubmitFormAction` and `ResetFormAction`
  join the typed action API, covering the `/Fields` name list, the
  include/exclude flag, and the FDF / HTML / XFDF / PDF submit format. Action
  serialization now emits arrays and integers, not only strings.

- **The optimizer's DPI target follows form XObjects.** Display size was
  measured from page-level placements only, so an image drawn inside a form was
  invisible to `image_target_dpi` and kept its full resolution. Forms are now
  descended into, composing each form's `/Matrix` with the CTM at its `Do`, with
  bounded nesting and cycle detection.
- **ICC-based CMYK images are recompressed.** `/ICCBased` with `/N 4` is CMYK,
  which the JPEG encoder already handled; only the colour-space probe rejected
  it.
- **An inverting `/Decode` array is folded into the samples.** `[1 0]` per
  component — the common form on inverted scans — is reproduced exactly by
  inverting the samples and dropping the array, so such images are no longer
  skipped. Other sample remappings are still left alone.
- **Masks are downscaled with the image that carries them.** A mask was skipped
  outright, so a full-resolution soft mask survived a downscale of its image.
  Masks now follow the image's display size but are never JPEG-encoded.

### Fixed

- **The PDF/A checker accepted DeviceCMYK under an sRGB output intent.** It only
  required *some* structurally valid ICC profile, where ISO 19005-1 6.2.3.3 ties
  each device colour family to the output intent's own space. The destination
  profile's colour space is now read from the ICC header and matched against the
  device colour actually used (DeviceGray is satisfied by any intent).
- **Device colour set by operator went undetected.** The scan looked only for
  the names `/DeviceRGB`/`/DeviceGray`/`/DeviceCMYK`, missing `k`/`K`, `rg`/`RG`
  and `g`/`G` — the most common way content selects a device space — so most
  non-conformant colour was never reported. `DeviceGray` is also tracked
  separately from `DeviceRGB` now; folding them together would flag a valid
  CMYK-intent document that only draws gray.

## [0.1.0] - 2026-08-19

### Added

- **Every Adobe predefined CJK CMap is now bundled** — 141 names across Japan1,
  Korea1, GB1 and CNS1 (both `-H` and `-V`), up from 8 name pairs. Text
  extraction, editing, redaction geometry and glyph rendering now work without
  `/ToUnicode` for the whole Unicode family (`UCS2`/`UTF8`/`UTF16`/`UTF32`,
  including `HW` and `JIS2004` variants) and the legacy encodings (`RKSJ`,
  `EUC`, `UHC`, `Johab`, `GBK`/`GBK2K`, `B5`, `ETen`, `HKscs`, the `pc`/`pv`/`ms`
  platform variants). The `Adobe-<Ordering>-<N>` CMaps stay excluded: their
  codes already are CIDs, not an encoding.
- **Unicode-keyed CMaps take the scalar from the code**, not from
  code → CID → Unicode. This already applied to `-UTF16-`; it now covers
  `-UCS2-`, `-UTF8-` and `-UTF32-` too. Those codes *are* the character, so text
  and code stay a bijection and a replacement can be written back — Adobe maps
  both U+2F47 and U+65E5 to Japan1 CID 3284, which previously made such
  characters extractable but not replaceable under those names.

- **Font subsetting covers predefined encodings.** Simple fonts now resolve a
  used code through `/Differences`, then the predefined base encoding, then the
  font program's own built-in encoding — one shared step for TrueType, CFF and
  Type 1. This lifts three limitations: a simple CFF with a PDF `/Encoding`
  override, a simple CFF carrying a predefined (Standard) encoding, and a Type 1
  font whose codes need a predefined base encoding were all left whole before.
  A base encoding outside the bundled tables (`MacExpertEncoding`, unrecognised
  names) still bails, so a used glyph is never erased.

### Changed

- **The bundled CMap tables are split one file per character collection**,
  behind a small index. A document names exactly one collection through its
  `CIDSystemInfo`, so only that file is decompressed; a single combined file
  would make one CJK document pay for all four. `supported_cmap_names()` — called
  while parsing every composite font — is now answered from the index alone
  instead of loading the whole bundle. Despite carrying 8.8x more CMaps, the
  worst-case resolve loads ~35 MB where the old combined bundle loaded ~24 MB.
  Only the index digest is pinned in code; it pins each collection file in turn.
  `scripts/build_cmap_data.py` now takes `--output-dir` instead of `--output`,
  and refuses to emit a collection whose `usecmap` bases are not all in it.

- **Signing an authored signature field.** `engine.sign_field.sign_field()`
  fills the `/FT /Sig` field created by `Form.add_signature_field()` as an
  incremental update: the original bytes are emitted verbatim, so a signature
  already in the document stays valid and the surrounding COS structure
  (widgets, other fields, outlines, annotations) is preserved instead of being
  rebuilt. Several fields can be signed in turn. Covers `adbe.pkcs7.detached`
  and PAdES (`pades=True`), an embedded chain, local/network timestamps, and
  DocMDP certification (writing `/Perms /DocMDP`). Previously the only signing
  path rebuilt the whole file and synthesised its own single field, so an
  authored field could not be signed at all.
- **Signature seed values and field locks.** `Form.add_signature_field()`
  accepts `seed_value=` (`/SV`: `filter`, `sub_filter`, `digest_method`,
  `reasons`, and `required` naming the entries whose ISO 32000-1 table 234
  `/Ff` bit makes them binding) and `lock=` (`/Lock`: `action` of
  `All`/`Include`/`Exclude` with `fields`). At signing time a required
  `/SubFilter` or `/Reasons` is enforced, and a `/Lock` becomes a **FieldMDP**
  signature reference.

- **PDF/A conversion rewrites CMYK images to DeviceRGB.** `convert_to_pdfa`
  already normalized DeviceCMYK *content* colour; it now also converts CMYK
  **image XObjects** — `/DeviceCMYK` and ICC-CMYK (`/ICCBased` `/N 4`), raw or
  `DCTDecode` — to 8-bit `DeviceRGB` re-encoded with `FlateDecode`, dropping the
  stale `/Decode`/`/DecodeParms`. Pixels go through the same decode path the
  renderer uses (Adobe de-inversion, YCCK), so the page looks the same.
  Transparency remains reported, not converted.
- **PDF/A conversion repoints `/Separation` and `/DeviceN` off CMYK.** A space
  whose alternate resolves to DeviceCMYK or ICC-CMYK now gets a resampled Type 0
  tint transform over `DeviceRGB` (PDF has no way to compose the original
  transform with a CMYK→RGB conversion). The space keeps its kind, colorant
  names and component count, so content streams selecting it need no rewriting,
  and a Separation/DeviceN image keeps its tint samples. A linear transform is
  reproduced exactly; a curved one to within a step or two per channel.
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

### Fixed

- **`PdfSignature.valid` returned `True` for tampered documents.** Its digest
  comparison went through `cryptography`'s `load_der_pkcs7_signed_data`, which is
  absent in some releases; that absence — and any error while walking the signed
  attributes — was treated as success, so a document whose signed bytes had been
  modified reported `valid is True` while `validate()` correctly reported
  INVALID. Verification now goes through the same engine path `validate()` uses:
  the digest algorithm comes from the CMS, the `messageDigest` attribute is
  checked against the covered bytes, and the **signature value is verified**
  against the signer certificate — which the old code never did at all. This also
  fixes SHA-384/SHA-512 signatures, which the hardcoded SHA-256 comparison could
  never match.
- **Subsetting resolved base encodings through the stdlib codecs**, which
  disagree with PDF's tables on real codes. MacRomanEncoding `0xDB` is
  `currency`, but `mac_roman` decodes it as the euro sign — so the subsetter
  reasoned about the wrong glyph. WinAnsiEncoding `0xA0`/`0xAD` are `space` and
  `hyphen`, where `cp1252` gives NBSP and a soft hyphen, whose scalars are absent
  from most fonts and made the subsetter give up and embed the whole font.
  Resolution now goes through the bundled Adobe tables and the Adobe Glyph List.
- **PDF/A conversion never reached form XObjects.** The CMYK content walker
  tested `_get_page_resources()` — which returns a converted plain `dict` — with
  `isinstance(..., PdfDictionary)`, a condition that can never hold, so DeviceCMYK
  inside a form XObject was silently left in place. It now walks the live COS
  resource dictionary (following inherited `/Resources`).
- **Attachments in nested embedded-file name trees are no longer invisible.**
  `/Names /EmbeddedFiles` was read only as a flat `/Names` array, so a document
  whose tree another producer balanced into `/Kids` sub-nodes (ISO 32000-1
  7.9.6) reported *no attachments at all* — silently, with no error. The tree is
  now walked in full, preserving its order, with depth, cumulative entry count,
  and revisited nodes bounded by the shared `PdfLoadLimits` budget.

- **Typed action/destination API** (`aspose_pdf.interactive`): destination value
  objects (`FitDestination`, `XYZDestination`, `FitH/V/R/B`…) and actions
  (`GoToAction`, `URIAction`, `GoToRAction`, `NamedAction`, `JavaScriptAction`,
  `LaunchAction`), wired to link annotations (`Page.add_link(rect, target)`),
  outline items (`OutlineItem(..., destination=…)`, replacing the previous
  fixed fit-to-page `/Dest`), and push-button widgets
  (`Form.add_push_button(..., action=…)`). Serialized to COS `/A` and `/Dest`.
- **Push-button visual states.** Push buttons now generate normal/rollover/down
  (`/AP` `N`/`R`/`D`) appearances, and `Form.add_push_button` accepts
  `border_color` / `background` (`/MK` `/BC` / `/BG`); the rollover and down faces
  are shaded variants of the background.
- **Type0 (CID) field fonts.** `Form.add_text_field(font=…)` embeds a Type0 font
  in the AcroForm `/DR` and bakes a CID-encoded `/AP`, so a non-Latin field value
  renders through the embedded CID font. (`generate_appearances` leaves the baked
  Type0 appearance intact; Type0 rich text `/RC` falls back to the plain `/DA`.)
- **PDF/A conversion embeds Standard-14 fonts and normalizes DeviceCMYK.**
  `convert_to_pdfa` now embeds non-embedded Standard-14 and Symbol/ZapfDingbats
  fonts with the bundled metric-compatible substitute (synthesizing `/Widths`
  from that face) even without a `font_lookup_directory`, and rewrites
  DeviceCMYK content color (`k`/`K` and `/DeviceCMYK` fills/strokes) to RGB using
  the renderer's device conversion so appearance is unchanged. CMYK image
  XObjects, Separation/DeviceN, ICC-CMYK, and transparency stay reported, not
  converted.
- **Composite (Type0) text renders through embedded (stream) CMaps.** A font
  whose `/Encoding` is a CMap *stream* (rather than Identity or a bundled
  predefined name) is decoded with the same parser extraction uses and fills the
  descendant font's real glyphs instead of boxes. Named predefined CMaps outside
  the eight bundled tables remain boxed (no bundled code→CID table).
- **True vertical writing (`WMode 1`).** Vertical CMaps — bundled or embedded —
  now offset each glyph by its `/W2`/`/DW2` position vector and advance the text
  downward by the vertical displacement, instead of advancing horizontally.
- Bundled the full **Adobe Glyph List** (4281 names) as a deterministic,
  integrity-checked data file, and resolve glyph names through the Adobe AGL
  algorithm (exact list, `.`-suffix stripping, `_` ligature components, then the
  algorithmic `uniXXXX`/`uXXXX` forms). Text extraction and code→Unicode now
  handle named glyphs such as `aacute`, `Euro`, `afii10017`, and `f_f_i`, not
  just `uniXXXX`. Generated by `scripts/build_agl_data.py`; no runtime fontTools.
- Simple **CFF and Type 1 fonts under a predefined encoding now render real
  glyphs instead of boxes.** Added CFF charset `name→gid` resolution and the
  bundled Standard/WinAnsi/MacRoman code→name tables, so a font whose `/Encoding`
  names a base encoding (or supplies `/Differences`) resolves through the font's
  own charset. Expert/MacExpert encodings remain boxed.
- **CFF2 outline programs are rasterized** (default instance): the parser reads
  the CFF2 header, 32-bit INDEXes, FDArray/FDSelect, and Type 2 charstrings, and
  collapses variable-font `blend`/`vsindex` to the default master (region deltas
  dropped). CFF2 tables are also extracted from an OpenType wrapper. CFF2 is left
  whole by the optimizer and still rejected for authoring.
- **WOFF2 font collections** (`ttcf`) are reconstructed into a TrueType
  Collection (shared tables deduplicated) via a new `build_ttc` assembler, after
  which the existing TTC face selection applies.
- `Document.replace_text` / `Page.replace_text` now shape right-to-left and
  complex-script replacements (HarfBuzz + Unicode bidi) instead of encoding them
  code-for-code, and match such phrases in their stored visual order. A
  replacement is reused in the run's own embedded font when that font already
  carries every shaped glyph (an embedded, Identity-encoded `CIDFontType2` whose
  shaped advances match `/W` with no positioning adjustment); otherwise a
  shaping-capable `font=` is embedded and the replacement drawn at the match
  baseline, with optional `layout=TextLayoutOptions(...)` for direction, script,
  and features. When neither path applies the edit raises rather than emit
  misshaped glyphs, and reshaping needs the optional `text-layout` extra. Simple
  LTR/ASCII replacements keep the previous exact byte-splice.
- The page renderer can join complex-script runs that fall back to a bundled
  substitute face, drawing cursive-connected forms instead of isolated glyphs
  (order-preserving, so nothing is reordered or repositioned), controlled by the
  new `shape_substitute_text` flag on `Document.render_page` / `Page.render`
  (default on). It needs the `text-layout` extra and is active only when the
  substitute face covers the script; the bundled Liberation/DejaVu faces cover
  Latin and symbol only, so it is a safe no-op for other scripts today. Embedded
  fonts are unaffected — their glyphs are already final.

[0.1.0]: https://github.com/aspose-pdf-foss/Aspose-PDF-FOSS-for-Python/releases/tag/v0.1.0
