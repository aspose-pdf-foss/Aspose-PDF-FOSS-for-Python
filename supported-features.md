# Supported Features

This document describes the implemented and tested feature set for the
`aspose-pdf-foss-for-python` prerelease package. The authoritative release
contract is the active `tests/test_*.py` suite; compatibility modules may expose
additional names, but unsupported operations should fail explicitly.

## Release Gate

The current local release gate is:

```bash
source .venv/bin/activate
python -m pip install -e .[dev]
ruff check src/
pytest -q
python -m compileall -q src/aspose_pdf
python -m build
python -m twine check dist/*
```

CI runs the same core checks on Python 3.11, 3.12, and 3.13. Local wrappers are
available as `scripts/check.sh` and `scripts/build.sh`.

## Documents

Supported:

- Create empty PDF documents.
- Load PDFs from a path, raw bytes, `bytearray`, or binary stream.
- Save PDFs to a path or writable binary stream, with overwrite protection for
  existing path targets.
- Use `Document` as a context manager and release resources with `dispose()` or
  `close()`.
- Read and write document info metadata.
- Read and set the PDF header version used on save.
- Read file identifiers and permission flags.
- Validate, check, and repair basic PDF structure.
- Open documents in streaming/lazy mode and decode page content on demand.
- Merge `Document` instances.
- Run resource optimization and stream compression helpers.
- Preserve and edit outlines/bookmarks.

## Optimization

`Document.optimize(options)` (and its alias `optimize_resources`) reduce file
size and clean up the object graph. Pass an `aspose_pdf.OptimizationOptions` to
control which techniques run; calling `optimize()` with no arguments applies the
defaults below.

Supported (honored options):

- `remove_unused_objects` (default on) — garbage-collect every object that is
  unreachable from the trailer. Reachability is seeded from *all* trailer
  entries, so `/Info` and `/Encrypt` are never collected.
- `remove_unused_streams` — when full object GC is off, prune only unreachable
  stream objects.
- `remove_duplicate_images` (default on) — collapse images with identical
  decoded pixels across the COS object graph. Copies that differ only in
  compression or filters (e.g. one stored raw, one Flate) are merged, not just
  byte-identical streams. Opaque codecs (DCT/JPX/CCITT/JBIG2) and encrypted
  streams fall back to byte-identical matching.
- `link_duplicate_streams` (default on) — share a single copy of byte-identical
  content streams; `allow_reuse_page_content` (default on) controls whether page
  `/Contents` streams participate.
- `compress_fonts` (default on) — include embedded font programs when
  Flate-compressing uncompressed streams. `Document.compress_streams()` runs the
  compression pass on its own.
- `unembed_fonts` (default off) — drop the embedded font program of Standard-14
  fonts (Helvetica/Times/Courier/Symbol/ZapfDingbats, including subset-prefixed
  names), which viewers substitute from built-in metrics. Custom embedded fonts
  are left untouched, so rendering is never degraded.
- `subset_fonts` (default off) — strip unused glyphs from embedded **TrueType**
  (`/FontFile2`) and **CFF** (`/FontFile3`, both name-keyed and CID-keyed) font
  programs. Glyph usage is read from page and form-XObject content streams; only
  glyphs actually drawn — plus the components of composite glyphs and `.notdef` —
  are kept. Glyph ids are preserved (glyph erasure: CFF charstrings of unused
  glyphs become a bare `endchar`), so the font's `cmap`/`charset`/`CIDToGIDMap`/
  `FDSelect` stay valid. Fonts whose code→glyph mapping cannot be resolved
  confidently are left whole.
- `image_compression_quality` (1–100, default off) — re-encode eligible
  RGB/grayscale image XObjects as baseline JPEG (`/DCTDecode`) at that quality
  using the dependency-free encoder (`engine/jpeg_encoder.py`). Images are
  rewritten only when the result is smaller, so already-small or incompressible
  images are left as-is. Masks, soft-mask targets, images with a `/Decode`
  array, Indexed/CMYK/Lab colour, and opaque codecs (JPX/CCITT/JBIG2) are
  skipped so colour and transparency are never altered unexpectedly.
- `image_max_dimension` (pixels, default off) — cap the longest side of an
  image, box-averaging it down first (aspect ratio preserved). Combined with
  `image_compression_quality` the downscale happens before JPEG encoding; on its
  own a lossless raster is downscaled and kept lossless (Flate).
- `use_object_streams` (default on) — after optimizing, a full save packs
  eligible objects into an object stream (`ObjStm`) located by a cross-reference
  stream (`XRef`), the single biggest file-size lever. Produces PDF 1.5+ output
  and is automatically skipped for encrypted/signed saves and when stream
  compression is disabled.

Boundaries:

- Image recompression uses a baseline (4:2:0) JPEG encoder for DeviceRGB /
  DeviceGray (and ICCBased with N=1/3); CMYK, Indexed, Lab, masks and images
  with a `/Decode` array are left untouched, as are JPX/CCITT/JBIG2 payloads.
  Resampling is box-average downscaling only (no upscaling, no DPI target).
- Font subsetting (glyph erasure) covers embedded **TrueType** (`/FontFile2`) and
  **CFF** (`/FontFile3`) programs. Handled: Type0 fonts with Identity encoding
  over a CIDFontType2 (TrueType) or a CIDFontType0 backed by either a name-keyed
  CFF (CIDs are glyph ids per PDF 32000 9.7.4.2) or a **CID-keyed CFF**
  (`/CIDFontType0C` — the `FDArray`/`FDSelect`/charset and each Font DICT's
  `Private` are relocated, and CIDs are mapped to glyph ids through the CFF
  charset). A simple `/TrueType` font is subset via its symbol `cmap`, or via the
  PDF `/Encoding` (a WinAnsi/MacRoman base plus `uniXXXX` `/Differences`) resolved
  against the font's Unicode `cmap`. **Not** subset (left whole): Type 1
  (`/FontFile`, eexec-encrypted), CFF2, and simple CFF fonts (`/FontFile3` in a
  simple `/Type1`, which would need name→glyph resolution through the CFF
  charset). Simple-TrueType `/Encoding` resolution falls back to leaving the font
  whole for any used code it cannot map exactly (e.g. StandardEncoding or
  non-algorithmic glyph names), so a used glyph is never erased.

## Pages

Supported:

- `Document.pages` exposes a mutable `PageCollection`.
- Get page count with `len(document.pages)` or `document.page_count`.
- Iterate, index, slice, and use negative indexing for pages.
- Add a blank page.
- Insert a blank page or existing page object.
- Delete pages by index and clear all pages.
- Check whether a page belongs to a collection and get its index.
- Read page media box/rectangle through `Page.rect` and `Page.media_box`.
- Read and set page rotation through `Page.rotation` (0/90/180/270, clockwise;
  inherited from parent page-tree nodes, normalised, and persisted on save).
- Read and set the page crop box through `Page.crop_box` (falls back to the
  media box when unset).
- Read decoded page content bytes through `Page.content`.
- Append simple authored content to a page: positioned Standard-14 text with
  `Page.add_text()`, raw/JPEG/PNG image XObjects with `Page.add_image()`,
  rectangles with `Page.draw_rectangle()`, and lines with `Page.draw_line()`.
  Authored segments can opt into tagged PDF structure with `tag=...`,
  `alt=...`, and `actual_text=...`; the writer emits `BDC`/`EMC` marked
  content and maintains `/StructTreeRoot`, page `/StructParents`, and the
  `/ParentTree`.
- Iterate pages with `Document.iter_pages()`.
- Iterate decoded page content streams with `Document.iter_page_content_streams()`.
- Render a page to a dependency-free RGB raster with `Page.render()` or
  `Document.render_page()`, then save it as PNG or TIFF through
  `RasterizedPage.save()` / `Page.save_as_image()` /
  `Document.save_page_as_image()`. The renderer covers common content stream
  operators for graphics state, paths, fills/strokes, clipping, image XObjects,
  form XObjects, and text. Text shown with an embedded font is filled from its
  real glyph outlines for all three program formats -- TrueType `glyf`
  (`/FontFile2`, simple and composite), CFF (`/FontFile3`, name-keyed and
  CID-keyed Type 2 charstrings with subroutines and flex), and Type 1
  (`/FontFile`, eexec/charstring-decrypted Type 1 charstrings with flex and
  hint-replacement OtherSubrs) -- resolved through Identity CID maps, a simple
  font's `/Encoding`/symbol cmap, the CFF charset/built-in encoding, or the
  Type 1 built-in encoding. Fonts with no embedded program -- the Standard 14
  (Helvetica/Times/Courier families) and other non-embedded simple fonts -- are
  filled from bundled metric-compatible open substitutes (Liberation, SIL OFL
  1.1), chosen by base-font name and FontDescriptor flags, so common text
  renders as real glyphs; only Symbol and ZapfDingbats keep the glyph-box
  fallback.
- Anti-alias the raster by supersampling: `antialias=True` (the default) renders
  at 3x and box-downsamples for smooth text, fill, stroke, and image edges; an
  integer 1-8 sets the factor, and `False` (or `1`) renders hard-edged.
- Apply common `/ExtGState` painting controls during rendering: line width,
  fill/stroke constant alpha (`ca`/`CA`), and standard separable blend modes
  (`Normal`, `Multiply`, `Screen`, `Overlay`, `Darken`, `Lighten`,
  `ColorDodge`, `ColorBurn`, `HardLight`, `SoftLight`, `Difference`,
  `Exclusion`) for fills, strokes, shadings, images, and pattern-painted
  content. Unsupported blend modes fall back to `Normal`.
- Apply soft masks. An image XObject's `/SMask` supplies per-pixel alpha, so
  transparent (PNG-style) images composite over the page. An ExtGState
  `/SMask` builds a device-space mask by rendering its `/G` transparency group
  offscreen and reducing it to alpha -- group luminosity for `/S /Luminosity`
  (over the `/BC` backdrop, default black) or painted coverage for
  `/S /Alpha` -- with an optional `/TR` transfer function. The mask modulates
  every subsequent paint (fills, strokes, glyphs, shadings, patterns, images)
  until cleared by `/SMask /None`, and is saved/restored by `q`/`Q`.
- Composite a transparency group (`/Group /S /Transparency`) drawn under a
  constant alpha < 1 as a single unit, so overlapping elements inside it do not
  double-darken.
- Paint axial (`ShadingType 2`) and radial (`ShadingType 3`) gradients through
  the `sh` operator and shading-pattern fills (`PatternType 2`). PDF function
  types 0 (sampled), 2 (exponential), and 3 (stitching) are evaluated over
  DeviceGray/RGB/CMYK and ICCBased colour spaces, with `/Extend` honoured.
- Fill with tiling patterns (`PatternType 1`): the pattern cell is repeated on
  its `/XStep`/`/YStep` lattice, clipped to the path being filled. Both coloured
  (`PaintType 1`) and uncoloured (`PaintType 2`, taking the colour from `scn`)
  patterns are supported.
- Use `PdfFileEditor` to concatenate, extract, insert, delete, append, and add a
  blank page through file-based workflows.

Boundaries:

- Page rendering is a best-effort rasterizer, not a certification-grade visual
  engine. It does not yet implement mesh/function-based shadings (types 1 and
  4-7), PostScript-calculator functions (type 4), non-separable blend modes
  (`Hue`, `Saturation`, `Color`, `Luminosity`), overprint, or complete PDF 2.0
  imaging semantics. Soft masks and constant-alpha group compositing are
  supported (above), but transparency groups are treated as isolated (knockout
  and non-isolated backdrops are not modelled) and the `/Alpha` soft-mask
  subtype approximates alpha with painted coverage.
- Glyph outline rasterization covers all three embedded program formats --
  TrueType (`glyf`), CFF (`/FontFile3`), and Type 1 (`/FontFile`). Fonts with no
  embedded program are filled from bundled metric-compatible substitutes (the
  Standard 14 Helvetica/Times/Courier families, and unknown non-embedded fonts
  routed to a sans/serif/mono substitute by their FontDescriptor flags). The
  substitutes are Latin-subset Liberation faces, so glyphs outside that coverage,
  Symbol and ZapfDingbats (no metric-compatible substitute), Type 1 `seac` accent
  composites, a simple CFF font that relies on a predefined (Standard/Expert)
  encoding, and text shaping (ligatures, GSUB/GPOS) are drawn as glyph boxes.
- Layout reflow remains out of scope in this prerelease.

## Text

Supported:

- Extract text from page content streams with `PdfExtractor`.
- Read all extracted text at once with `PdfExtractor.get_text()`.
- Iterate extracted page text with `has_next_page_text()` and
  `get_next_page_text()`.
- Parse common PDF text-showing operators through the content stream parser:
  `Tj`, `TJ`, literal strings, hexadecimal strings, and text arrays.
- Handle text operators mixed with common graphics, color, and text-state
  operators inside `BT`/`ET`.
- Decode WinAnsi and other simple encodings used by tested fonts.
- Decode `ToUnicode` CMaps, including `bfchar`, `bfrange`, comments, multiple
  pairs per line, and Unicode/CJK mappings.
- Apply Identity-H / UTF-16BE fallback for Type0/CID text when no `ToUnicode`
  map is available.
- Use glyph-name fallbacks such as `uniXXXX` and `uXXXX`.
- Use best-effort text extraction fallback for partially broken content streams.
- Use `TextFragmentAbsorber` and `TextAbsorber` to collect text fragments, search
  exact phrases, run regex searches, control case sensitivity, and inspect match
  offsets/page indices.
- Replace or redact existing text in simple page content streams with
  `Document.replace_text`, `Document.redact_text`, `Page.replace_text`, and
  `Page.redact_text`. The editor rewrites literal and hexadecimal string
  operands used by `Tj`, `'`, `"`, and `TJ`. A `TJ` array's string elements are
  matched as one logical string, and consecutive show operators (e.g. two
  adjacent `Tj`, or a `Tj` followed by a `TJ`) separated only by
  positionally-neutral operators are joined into one logical run, so a phrase
  split across several elements or operators (common with kerning or per-word
  painting) is rewritten across the boundary: the replacement is placed in the
  element holding the match start and the remaining matched characters are
  removed from the others, leaving the kerning adjustments and unmatched
  elements intact. A line-moving operator (`'`/`"`) or any positioning, font or
  CTM change starts a new run. Each element keeps its own literal/hex style and
  Latin-1/UTF-16BE encoding. Case-insensitive matching and `max_count` are
  supported (a spanning match counts once); lazy page contents are materialized
  before editing and the rewritten content persists on save.
- Draw a redaction overlay bar with `redact_text(..., overlay=True,
  overlay_color=(r, g, b))`. After removing the matched text, a filled
  rectangle (a DeviceRGB triple of 0..1, default black) is drawn over each
  removed run's location. The location is found by a best-effort text-position
  tracker (CTM, text matrix, and simple-font advance widths from `/Widths` or a
  bundled metric-compatible substitute). The bar is cosmetic — the text is
  already removed from the content — so a run whose position cannot be tracked
  (a multi-byte/Type0 or unresolved font) is left unmarked rather than risking a
  leak.
- Add positioned text to pages with Standard-14 Type1 font resources.
- Mark newly authored text with a structure tag and optional `/ActualText`.

Boundaries:

- OCR is not implemented.
- Existing text replacement/redaction edits the content stream but does not
  reflow layout or infer font-specific `ToUnicode` reverse mappings. Phrases
  split across several `TJ` elements or across consecutive show operators are
  matched and rewritten, but a phrase split across a line break or a
  positioning/font change between operators is not (those start a new run). The
  redaction-overlay position tracker handles single-byte simple fonts only;
  multi-byte/Type0 or unresolved fonts get no bar (the text is still removed),
  and the bar assumes a balanced content stream (identity CTM at its end).
  Layout analysis, font shaping, rich text, and paragraph layout are not
  implemented as public product features.

## Fonts

Supported:

- Use the Standard 14 fonts and read embedded TrueType font programs.
- Decode `ToUnicode` / CMap mappings for accurate text extraction.
- Discover fonts through `FontRepository` and the `FontSource` hierarchy:
  `FolderFontSource` (optionally recursive), `FileFontSource`,
  `MemoryFontSource`, and `SystemFontSource`.
- Parse SFNT containers (TrueType, OpenType/CFF, and TrueType Collections) to
  recover real family, subfamily, full, and PostScript names, and to classify
  the font type. Each face of a `.ttc` collection is reported separately.
- Decode WOFF 1.0 web fonts to their underlying SFNT (dependency-free, via
  zlib): a `.woff` is unwrapped transparently, so it discovers real names,
  classifies its type, embeds, and subsets exactly like a `.ttf` / `.otf`.
- Decode WOFF2 web fonts (`wOF2`), including the transformed `glyf` / `loca`
  representation, when the optional `brotli` package is installed
  (`pip install aspose-pdf-foss-for-python[woff2]`). The reconstructed SFNT is a
  first-class font just like a decoded `.woff`. Without `brotli`, WOFF2 falls
  back to file-name metadata, so the default install stays dependency-free.
- Resolve a font by family / full / PostScript name (case-insensitive), falling
  back to the standard-font registry, and obtain embeddable font bytes through
  `FontRepository.open_font()` or `FontDescriptor.get_font_bytes()` (WOFF
  programs are unwrapped to a directly embeddable SFNT).
- Register custom sources with priorities through `FontRepository.add_source()`.

Boundaries:

- WOFF2 decoding needs the optional `brotli` dependency; WOFF2 font
  *collections* (`ttcf` flavour) are not reconstructed.
- Embedded glyph outlines are rasterized by the page renderer (see
  [Pages](#pages)) for all three program formats: TrueType (`glyf`), CFF
  (`/FontFile3`, name-keyed and CID-keyed), and Type 1 (`/FontFile`). The
  Standard 14 fonts (never embedded) are rendered from bundled metric-compatible
  open substitutes (Liberation, SIL OFL 1.1, Latin subset); Symbol and
  ZapfDingbats have no substitute and fall back to glyph boxes. Text shaping
  (ligatures, GSUB/GPOS) and Type 1 `seac` accents are not implemented.
  (Embedded TrueType and CFF — including CID-keyed CFF — glyph subsetting is
  available through `OptimizationOptions.subset_fonts`; see
  [Optimization](#optimization).)

## Images

Supported:

- Extract image XObjects from parsed PDFs.
- Place raw 8-bit DeviceGray/DeviceRGB/DeviceCMYK images, JPEG images, and
  simple non-interlaced 8-bit PNG images on pages as image XObjects.
- Mark newly authored images as tagged `/Figure` content by passing `alt=...`
  (or an explicit `tag=...`), producing MCID-backed structure elements.
- Track images by resource name and page association where the page/resource map
  is available.
- Decode image stream filters through the stream decoder where supported:
  Flate, ASCII85, ASCIIHex, LZW, RunLength, CCITT Fax, and JBIG2. DCT/JPEG is
  passed through at the filter level (the JPEG bytes are the canonical stored
  form); a dependency-free baseline **and progressive** JPEG-to-pixels decoder
  (grayscale, RGB/YCbCr, CMYK/YCCK) is available through `aspose_pdf.engine.dct`
  (see Images).
- Encode bytes back into stream data with the matching `StreamEncoder`
  (`aspose_pdf.engine.filters`): Flate, LZW, ASCII85, ASCIIHex and RunLength
  round-trip exactly with the decoder; image codecs are not re-encoded.
- Decode JPX/JPEG 2000 when Pillow is installed; otherwise JPX decoding fails
  explicitly.
- Preserve image dimensions (`/Width`, `/Height`) through save/load round trips.
- Lazily load image payloads in streaming/lazy document workflows.
- Use `ImagePlacementAbsorber` to collect image placements from a `SimplePdf`,
  page-like object, `images` dictionary, or XObject resources.
- Read placement metadata when available: page index, rectangle, resolution,
  rotation, and transformation matrix.
- Reconstruct extracted images into real, openable files. `ImagePlacement.save`
  and `SimplePdf.save_image` rebuild a proper image from the decoded samples and
  the captured metadata (`/Width`, `/Height`, `/BitsPerComponent`, colour space,
  `/Indexed` palette, `/Decode`): raster codecs (Flate/LZW/CCITT/JBIG2) are
  written as **PNG** (pure-Python encoder, no dependencies), DCT/JPEG keeps its
  JPEG bytes (`.jpg`). The output suffix is adjusted to the produced format when
  the requested one would mislabel the file.
- Convert image colour spaces during reconstruction: **CMYK → RGB**, **Indexed →
  RGB** (palette lookup, including a CMYK base), and **Gray ↔ RGB**. `save`/
  `save_image` also accept `color_space="RGB"`/`"Gray"` to force a conversion.
- Decode **baseline and progressive** DCT/JPEG to pixels with a
  **dependency-free** decoder (`aspose_pdf.engine.dct`): grayscale, YCbCr/RGB and
  CMYK/YCCK (4-component, with Adobe de-inversion), any chroma subsampling, and
  restart intervals. Image export uses it to produce a real PNG from such JPEGs
  even when Pillow is absent. Only JPX/JPEG 2000 still needs the optional
  `images` extra (`pip install aspose-pdf-foss[images]`, Pillow); without Pillow
  a JPX image keeps its original encoded bytes (`.jp2`). Arithmetic-coded JPEG is
  also Pillow-only.
- Read reconstruction metadata from an `ImagePlacement`: `width`, `height`,
  `bits_per_component`, and `color_space`.
- Replace or hide an `ImagePlacement` payload in memory.
- Save, replace, hide, and enumerate images through the lower-level `SimplePdf`
  image helpers.
- Compose whole pages into RGB raster output via the page renderer. Image
  XObjects backed by raw/Flate samples, indexed/gray/RGB/CMYK colour spaces, and
  baseline/progressive DCT/JPEG streams are painted into the page raster,
  honouring an image `/SMask` as per-pixel alpha (see [Pages](#pages)).
- Deduplicate identical image payloads during optimization.
- **Encode** pixels back to a baseline JPEG with a **dependency-free** encoder
  (`aspose_pdf.engine.jpeg_encoder`: grayscale and RGB with 4:2:0 chroma
  subsampling) and **box-downscale** pixels (`aspose_pdf.engine.image_resample`).
  `Document.optimize` uses both to apply `image_compression_quality` (recompress
  to JPEG) and `image_max_dimension` (cap the longest side); see
  [Optimization](#optimization).

Boundaries:

- High-level image insertion/placement into pages is not a public feature in this
  prerelease. The JPEG encoder is baseline (4:2:0) for grayscale/RGB only (no
  CMYK, progressive, or optimized-Huffman output); resampling is box-average
  downscaling (no upscaling or DPI-targeted resampling).
- JPX/JPEG 2000 page-render painting still depends on optional Pillow decode
  availability; arithmetic-coded JPEG remains unsupported by the pure-Python
  raster path.
- JPX/JPEG 2000 decoding requires the optional Pillow extra; there is no
  pure-Python JPX decoder.

## Forms

Supported:

- Read AcroForm fields through `Document.form`.
- Iterate form fields and access fields by name.
- Extract text, checkbox, radio, listbox, and combobox values.
- Set a field value by name through the `Field.value` setter.
- Regenerate field appearance streams from their values via
  `Document.generate_field_appearances()` or `Form.generate_appearances()`:
  text and choice fields are drawn from their `/V` and default appearance
  (`/DA` font, size — including auto-size — and colour, with `/Q` quadding and
  multi-line support), resolving the font from the AcroForm `/DR` (synthesising
  Helvetica when absent); check box / radio `/AS` states are pointed at the
  value. The AcroForm `/NeedAppearances` flag is cleared so the generated
  appearance is honoured. `flatten()` runs this automatically.
- Flatten form fields and annotations into static page content (generating
  missing appearances first), mapping each appearance form's `/BBox` onto the
  widget `/Rect`.
- Extract unsigned form fields and annotations with `UnsignedContentAbsorber`.

Boundaries:

- Dynamic XFA processing is not implemented.
- Variable-text layout is single-font with explicit line breaks only (no
  automatic word wrapping or rich-text `/RV`); centre/right quadding uses an
  estimated advance width. Push-button and check box *glyph* appearances are not
  synthesised (existing `/AP` states are reused via `/AS`).

## Annotations

Supported:

- Read page annotations through `Page.annotations`.
- Add, insert, update, delete, clear, iterate, and index annotations.
- Preserve all standard annotation subtypes (for example `Text`, `Link`,
  `FreeText`, `Line`, `Square`, `Circle`, `Polygon`, `PolyLine`, `Highlight`,
  `Underline`, `Squiggly`, `StrikeOut`, `Stamp`, `Caret`, `Ink`) through
  save/load round trips, including their type-specific defining entries
  (for example `C`, `IC`, `L`, `QuadPoints`, `Vertices`, `InkList`, `Name`).
- Read and set type-specific annotation properties through
  `Annotation.properties`, `Annotation.get_property`, and
  `Annotation.set_property`; mark PDF name values with `annotations.Name`.
- Read and write annotation contents, rectangle, title/author, and normal
  appearance stream.
- Auto-generate normal appearance streams (`/AP /N`) from geometry and colours
  for the standard shape and text-markup subtypes — `Square`, `Circle`, `Line`,
  `Polygon`, `PolyLine`, `Ink`, `Highlight` (multiply blend), `Underline`,
  `StrikeOut`, `Squiggly` — via `Annotation.generate_appearance()`,
  `Page.annotations.generate_appearances()`, or `Document.generate_appearances()`.
- Flatten annotations into page content, mapping each appearance form's `/BBox`
  (and `/Matrix`) onto the annotation `/Rect`; appearances are synthesised for
  supported subtypes first so they are not dropped.

Boundaries:

- Appearance synthesis covers the shape / text-markup subtypes above; other
  subtypes (`FreeText`, `Stamp`, `Caret`, widgets, …) and decorations such as
  line endings (`/LE`), dash patterns, and cloud borders (`/BE`) are not drawn —
  supply an appearance via `appearance_normal` for those.

## Attachments

Supported:

- Extract embedded files from the PDF name tree.
- Decode attachment filenames from regular PDF strings and UTF-16BE strings.
- Extract embedded streams under `/EF /F` and `/EF /UF`.
- Decode Flate-compressed embedded file streams.
- Add (embed) new attachments through the `attachments` mapping or
  `Document.add_attachment`; on save they are written to the catalog
  `/Names /EmbeddedFiles` name tree as `/Filespec` + `/EmbeddedFile` objects.
- Attach metadata via `Document.add_attachment`: a MIME media type (written as
  the embedded file `/Subtype`, e.g. `text/plain` → `/text#2Fplain`), a `/Desc`
  description, and creation / modification dates (a `datetime` or a pre-formatted
  `D:` string) stored in the embedded file `/Params`.
- Read attachment metadata back through a typed API: `Document.embedded_files`
  returns `FileSpecification` objects (`name`, `contents`, `mime_type`,
  `description`, `creation_date`, `mod_date`, `size`), and
  `Document.get_embedded_file(name)` looks one up by name. The MIME `/Subtype`,
  `/Desc` and `/Params` dates are decoded back to Python values (`#XX`-escaped
  names and `D:` dates are parsed), so a save / reload round trip preserves them.
- Flate-compress the embedded payload by default (`compress=True`), skipping
  compression automatically when it would not make the payload smaller.
- Preserve tested attachment names and bytes through COS round trips, including
  attachments added in memory before the first save.

Boundaries:

- The typed `FileSpecification` view is read-only; mutate attachments through the
  `attachments` mapping or `Document.add_attachment` and re-read `embedded_files`.

## Security, Encryption, And Signatures

Supported:

- Encrypt and decrypt documents with user and owner passwords.
- Change passwords and read permission flags.
- Reject missing, empty, whitespace-only, and wrong passwords for encrypted PDFs.
- Exercise RC4 and AES-CBC primitives, AES-256 setup, and PDF 2.0 V5/R6 key
  derivation helpers.
- Validate PDF signature ByteRange structure and PKCS#7 container shape.
- Detect meaningful unsigned incremental changes after signed revisions.
- Create self-signed certificates and PKCS#7 signing payloads through the
  signing helpers, optionally embedding an intermediate-CA chain.
- Cryptographically verify the signer (CMS/PKCS#7 signed attributes and
  signature value), not merely the container shape.
- Build and validate the signer's X.509 certificate chain against supplied
  trust anchors (and, opt-in, the operating-system trust store), checking
  validity periods, BasicConstraints, and key usage; self-signed signatures are
  reported as such and accepted by default.
- Check certificate revocation via OCSP and CRL — offline against material
  embedded in the document/CMS, and (opt-in, `ValidationMode.ONLINE`/`AUTO`)
  over the network from the certificate's AIA / CRL-distribution-point URLs.
- Verify embedded RFC 3161 signature timestamps (TSA signature and message
  imprint) and surface the timestamp time; embed a timestamp when signing from a
  local TSA or (opt-in) a network TSA.
- Create and validate DocMDP certification (certifying) signatures, including
  reporting the certification level and flagging changes that violate a
  "no changes permitted" certification.
- Produce **PAdES baseline signatures** (`SimplePdf.pades = True` →
  `ETSI.CAdES.detached`): CAdES-BES signed attributes with the ESS
  `signing-certificate-v2` binding (**PAdES-B**), upgraded to **PAdES-T** by
  embedding a signature timestamp. Validation verifies the signing-certificate
  binding and reports the achieved level via `ValidationResult.pades_level`
  (`PadesLevel.B/T/LT/LTA`).
- Build a **document security store** (`/DSS` with `/Certs`, `/CRLs`, `/OCSPs`
  and per-signature `/VRI`) as an incremental update that leaves existing
  signatures byte-for-byte intact (`engine.dss.build_dss` / `enable_ltv`),
  turning PAdES-T into **PAdES-LT**; validation harvests the `/DSS` so chain
  building and revocation work offline (LTV).
- Add an **archive (document) timestamp** (`ETSI.RFC3161` `/DocTimeStamp`,
  `engine.dss.add_document_timestamp`) over the DSS-augmented document for
  **PAdES-LTA**; document timestamps are validated as RFC 3161 tokens over their
  own ByteRange. The compromise detector treats DSS/archive-timestamp
  incremental updates as legitimate rather than as tampering.
- Inspect structured validation results via `PdfSignature.validate(...)`
  (signer, trust status, revocation status, timestamp, certification level,
  PAdES level).

Boundaries:

- PAdES baseline levels (B/T/LT/LTA) are produced and validated against trust
  anchors, but this is not a formally certified eIDAS-grade implementation:
  conformance to ETSI EN 319 142 / final certification is deferred to external
  validators (e.g. veraPDF, eIDAS validation services). Online harvesting of
  fresh revocation material into the `/DSS` at build time is opt-in by supplying
  it to `enable_ltv`; the builder itself stays offline.

## PDF/A And PDF/UA

Supported:

- Run heuristic PDF/A validation and get structured errors/warnings. Beyond
  encryption, metadata/XMP, fonts and output intents, the checker inspects
  many structural ISO 19005 rules observable from the object graph: a trailer
  `/ID`, the header version per part (1.4 for PDF/A-1, 1.7 for PDF/A-2/3),
  document/page additional actions (`/AA`), optional content (`/OCProperties`,
  PDF/A-1), AcroForm `/NeedAppearances` and dynamic XFA, prohibited
  annotations (Sound/Movie/Screen/3D/RichMedia; FileAttachment outside
  PDF/A-3), annotation flags (Print required; Hidden/NoView/Invisible
  forbidden) and appearances, prohibited actions
  (Launch/JavaScript/Sound/Movie/ResetForm/ImportData/SetOCGState/Rendition),
  PostScript XObjects, image `/Interpolate`, and PDF/A-1 transparency
  (ExtGState soft masks, blend modes, constant alpha, transfer functions, and
  `/Group /S /Transparency`). It also flags annotation constant opacity
  (`/CA` < 1, PDF/A-1), the `/Crypt` stream filter, the catalog `/Requirements`
  entry, `CIDFontType2` fonts missing `/CIDToGIDMap`, a filtered XMP `/Metadata`
  stream (which must be plaintext), and PDF/A-3 embedded files lacking
  `/AFRelationship`. PDF/A level A additionally requires a tagged
  structure tree, which is walked to verify standard structure types (or
  `/RoleMap` mappings), Figure/Formula alternate text, and `/Note` identifiers.
- Run batch PDF/A validation through `PdfAValidateOptions` and `PdfAValidator`.
- Convert loaded COS-backed documents toward PDF/A by adding OutputIntents and
  XMP metadata, setting a title and trailer `/ID` when missing, capping the
  header version, and removing prohibited JavaScript/OpenAction/AA/OCProperties
  entries, AcroForm `/NeedAppearances`/XFA, and offending annotation flags.
- Run heuristic PDF/UA checks. The catalog-level prerequisites
  (`/StructTreeRoot`, `/MarkInfo /Marked true`, ViewerPreferences
  `/DisplayDocTitle true`, a document title, and an XMP `pdfuaid:part`
  declaration; `/Lang` recommended) are joined by a walk of the real structure
  tree and the page/annotation rules: non-standard structure types without a
  `/RoleMap` mapping, `/Figure` and `/Formula` elements without alternate text
  (`/Alt` or `/ActualText`), `/Note` elements without `/ID`, a missing
  `/ParentTree` once the tree carries content, heading-level skips, list/table
  containment, `/MarkInfo /Suspects true`, pages with annotations that omit
  `/Tabs /S`, annotations missing a `/Contents` text alternative or structure
  nesting, and fonts that are not embedded.
- Add the PDF/UA catalog shell with `Document.convert_to_pdfua` (structure tree
  with an empty `/ParentTree`, MarkInfo without `/Suspects`, `/Tabs /S` on pages
  carrying annotations, language, DisplayDocTitle, title, and a `pdfuaid` XMP
  packet merged with any existing PDF/A identifier), and run batch PDF/UA
  validation through `PdfUaValidateOptions` and `PdfUaValidator`.
- Generate real PDF/UA structure for newly authored page content: `Page.add_text`
  can emit `/P` (or another explicit tag) with `/ActualText`; `Page.add_image`
  can emit `/Figure` with `/Alt`; `Page.draw_rectangle` and `Page.draw_line`
  can also be tagged explicitly. These segments are linked by MCID through the
  page `/StructParents` entry and `/StructTreeRoot /ParentTree`.
- Heuristically tag *existing* page content into a structure tree with
  `Document.auto_tag()` (or `convert_to_pdfua(auto_tag=True)`). Each text object
  (`BT` ... `ET`) becomes a `/P` element (or `/H1` when its font size dominates
  the page), and each image XObject paint (`/Name Do`) becomes a `/Figure` with
  `/Alt` (the `image_alt` argument takes a string, a name→text callable, or
  `None` to skip images). Elements are wrapped in `BDC`/`EMC` by a byte-level
  splice (originals preserved, inline images skipped) and linked by MCID in
  reading order through `/StructParents` and the `/ParentTree`. Pages already
  carrying marked content are left untouched.
- Resolve XMP namespace prefixes and URIs with `NamespaceProvider` (public) /
  `XmpNamespaceProvider` (engine), preloaded with the standard XMP namespaces
  (Dublin Core, Adobe XMP, PDF, PDF/A, EXIF, TIFF, ...) and extensible with
  custom mappings.
- Parse and serialize XMP packets with `aspose_pdf.xmp.parse` / `serialize`
  (simple properties, `rdf:Bag`/`Seq`/`Alt` arrays, `xml:lang`, and the
  abbreviated attribute form). DTD/entity declarations are rejected.
- Model structured XMP values with `XmpStruct` — `rdf:parseType="Resource"`
  blocks and nested `rdf:Description` structs (e.g. `xmpTPg:MaxPageSize`/`stDim`,
  `xmpMM:DerivedFrom`/`stRef`), including arrays of structs such as the
  `xmpMM:History` `Seq` of `stEvt` entries and arbitrarily nested structs.
  Member namespaces are declared automatically on serialization.
- Model property qualifiers — the `rdf:value` + sibling qualifier form (e.g. an
  identifier value qualified by `xmpidq:Scheme`), in both element and
  abbreviated-attribute syntax. Top-level qualified properties are an
  `XmpProperty`; qualifiers on values nested inside an array item or struct
  member are carried on the `XmpField` (`XmpField.qualifiers`), and a qualifier
  may itself carry qualifiers (recursive qualification). All round-trip through
  `parse`/`serialize`.
- Round-trip URI-valued properties: an `rdf:resource` value is parsed into an
  `XmpField` with `is_uri` set and re-serialized as an `rdf:resource` attribute
  (as a simple property, an `rdf:li`, or a struct member).
- Read/write typed values with `XmpPacket` convenience accessors covering the
  XMP value types — `set_date`/`get_date` (ISO-8601 ↔ `datetime`),
  `set_bool`/`get_bool`, `set_int`/`get_int`, `set_real`/`get_real`,
  `set_localized_text`/`get_localized_text` (`rdf:Alt`), and
  `set_array`/`get_array` (`Seq`/`Bag`/`Alt`).
- Read and write the document's XMP metadata stream through
  `Document.xmp_metadata` (catalog `/Metadata`); edits persist on `save`.
- Synchronise the `/Info` dictionary and the XMP packet with
  `Document.sync_metadata(direction=...)` — maps `Title`/`Author`/`Subject`/
  `Keywords`/`Creator`/`Producer`/`CreationDate`/`ModDate` to `dc:title`/
  `dc:creator`/`dc:description`/`pdf:Keywords`/`xmp:CreatorTool`/`pdf:Producer`/
  `xmp:CreateDate`/`xmp:ModifyDate`, converting PDF dates to/from ISO-8601
  (keeping the two consistent is required for PDF/A). The underlying
  `aspose_pdf.xmp.info_to_xmp` / `xmp_to_info` helpers are public.

Boundaries:

- PDF/A and PDF/UA checks are heuristic signals, not certification-grade
  validation. They inspect document structure, not rendered output, glyph
  coverage, colour fidelity, or the semantic correctness of a tag tree. Use a
  dedicated validator such as veraPDF for formal compliance.
- The PDF/UA structure-tree checks validate a tag tree that already exists; they
  do not verify full marked-content (MCID) coverage or visual reading order for
  arbitrary existing PDFs. `auto_tag()` infers a real but **coarse** tree: one
  element per text object, headings by font size only, and image `/Figure`
  alternate text that is a caller-supplied placeholder (alt text cannot be
  inferred) — with no paragraph/list/table grouping or fine reading-order
  analysis. It is a starting point a human refines, not certified accessibility.
  Content authored through the page APIs carries caller-supplied semantic tags
  and alt text.
- XMP covers the full data model: simple values, structured values, arrays
  (`Bag`/`Seq`/`Alt`), language alternatives, qualifiers (top-level, nested in
  arrays/structs, and recursive), URI (`rdf:resource`) values, and the typed
  value accessors above. Values are serialized as text per the RDF model, and
  `serialize` emits a canonical form — a parse → serialize round-trip preserves
  the data model (it is not guaranteed byte-identical to a foreign input).

## Low-Level PDF Engine

Supported:

- Parse traditional xref tables, xref streams, object streams, trailers,
  streams, page trees, resources, metadata, outlines, annotations, forms,
  attachments, signatures, and encryption dictionaries.
- Preserve COS-backed documents where supported.
- Support lazy COS object materialization and lazy page/content/image traversal.
- Write PDFs, save full documents, and create incremental updates for tested
  workflows.
- Decode common PDF stream filters and surface unsupported/corrupt filters as
  explicit validation errors.

## Low-Code Plugins

Supported:

- Run common workflows through a plugin layer in `aspose_pdf.lowcode`:
  `Merger`, `Splitter`, `Optimizer`, and `TextExtractor`.
- Describe inputs and outputs with data sources that abstract over files
  (`FileDataSource`), in-memory bytes (`ByteArrayDataSource`), and binary
  streams (`StreamDataSource`).
- Collect results through a `ResultContainer` of `OperationResult` objects,
  each of which can be saved to a data source, path, or stream, or read as
  bytes/text.
- `Merger` concatenates all inputs; `Splitter` emits one document per page;
  `Optimizer` compresses and garbage-collects each input; `TextExtractor`
  returns extracted text per input.

Boundaries:

- The plugin layer composes existing high-level operations; it does not add
  new conversion or generation capabilities, and does not integrate with
  hosted services or billing.

## Known Unsupported Compatibility Surfaces

- Runtime package code does not use LLM services, API keys, or `.env` secrets.
