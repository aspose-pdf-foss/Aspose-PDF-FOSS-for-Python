# Supported Features

This document describes the implemented and tested feature set of the
`aspose-pdf-foss-for-python` package. The authoritative release
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
- Load PDFs from a path, raw bytes, `bytearray`, or binary stream, either
  through `Document(source, password=..., limits=...)` or through
  `Document().load_from(...)`. Both raise on a missing file, non-PDF data, or a
  missing password instead of yielding an empty document.
- Save PDFs to a path or writable binary stream, with overwrite protection for
  existing path targets.
- Save a byte-preserving incremental update with `save(..., incremental=True)`:
  the original file bytes are emitted verbatim and only objects added or
  modified since load are appended as a new revision chained through `/Prev`, so
  an existing signature stays valid. Change detection compares each object's
  canonical serialization against a re-parse of the original. Documents built
  from scratch fall back to a full write; encrypted or to-be-signed documents
  are rejected (their revisions must go through the encrypting/signing writer).
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

## Resource Limits For Untrusted PDFs

`PdfLoadLimits` is the public, immutable resource policy used when a PDF is
loaded and later processed. It can be supplied when constructing a document,
for a particular eager load, or when opening a lazy document:

```python
from aspose_pdf import Document, PdfLoadLimits, PdfResourceLimitException

limits = PdfLoadLimits(
    max_input_bytes=64 * 1024 * 1024,
    max_objects=50_000,
    max_decoded_stream_bytes=16 * 1024 * 1024,
    max_total_decoded_bytes=64 * 1024 * 1024,
)

with Document(limits=limits) as document:
    document.load_from("input.pdf")

with Document() as document:
    document.load_from("input.pdf", limits=limits)

with Document.open_streaming("input.pdf", limits=limits) as lazy_document:
    first_page_bytes = lazy_document.pages[0].content
```

The effective policy is available as `document.load_limits`. Passing no policy
uses the bounded defaults below. Every field accepts a positive integer or
`None`; `None` disables only that field.

| Field | Default | Guarded resource |
| --- | ---: | --- |
| `max_input_bytes` | 512 MiB | Bytes accepted from a path, bytes-like value, or binary stream; streams are read incrementally without being closed. |
| `max_objects` | 250,000 | Object slots and object counts from traditional/reconstructed xref data, xref streams, and object streams. |
| `max_xref_sections` | 256 | Incremental-update xref sections followed through `/Prev`. |
| `max_nesting_depth` | 100 | Nested COS/content values and recursive page, outline, form, signature, annotation, resource, and function graphs. |
| `max_container_items` | 1,000,000 | Parsed container items and materialized mappings, ranges, graph nodes, CMap lines, CID widths, and sampled-function entries. |
| `max_object_bytes` | 128 MiB | Encoded body size of one indirect object. |
| `max_decoded_stream_bytes` | 128 MiB | Decoded output of one PDF stream. |
| `max_codec_work_bytes` | 512 MiB | Estimated temporary working set for DCT, JPX, CCITT, JBIG2, image conversion, and sampled-function paths. |
| `max_compression_ratio` | 2,000 | Expansion ratio of a stream filter chain relative to its encoded input. |
| `max_content_stream_bytes` | 64 MiB | Combined decoded page-content bytes, including `/Contents` arrays. |
| `max_total_decoded_bytes` | 512 MiB | Cumulative decoded-stream budget shared by one document. |
| `max_stream_filters` | 16 | Filters allowed in one stream filter chain. |
| `max_pages` | 100,000 | Pages discovered while walking the page tree. |
| `max_image_pixels` | 100,000,000 | Declared pixels in an image XObject before image processing. |
| `max_raster_pixels` | 100,000,000 | Pixels in the renderer's supersampled working canvas before allocation. |
| `max_content_tokens` | 5,000,000 | Tokens consumed by content interpretation, text editing/location, image-placement, auto-tag, and conformance scanners. |

The eager and streaming/lazy paths use the same checks. Lazy opening still
defers page-content decoding, but on-demand decoding, text parsing/editing,
image access/export, rendering, validation, signatures/DSS, incremental
updates, `PdfExtractor`, and low-code plugins continue to use the effective
policy. CMap/CID ranges, sampled shading functions, and recursive auxiliary COS
graphs are checked before their large materializations. Limit-aware APIs
propagate the public `PdfResourceLimitException` (a `PdfValidationException`)
instead of converting it to a raw-stream fallback, an empty lazy image, or a
repaired empty document. The legacy boolean `PdfFileEditor` facade keeps its
documented `False`/`last_exception` error contract. Cycles in `/Prev`, page,
outline, field, annotation/resource, and function graphs are rejected with an
explicit parse error.

`PdfLoadLimits.unlimited()` returns a policy with every field disabled. This is
an explicit opt-out for trusted inputs and should be paired with external
process, memory, and time controls.

Boundaries:

- These limits cover the main PDF input, COS/xref/object-stream, document-graph
  traversal, PDF stream-filter, content/text scanners, CMap/CID, sampled
  shading, image-dimension, codec, and raster-allocation paths. DCT/JPX headers
  and CCITT/JBIG2 bitmap geometry are checked before their large allocations.
  The limits reduce known memory/CPU amplification risks; they are not a proof
  that every possible PDF denial-of-service technique is bounded.
- Authored PNG input and WOFF/WOFF2 font decoding use the same image, decoded
  stream, compression-ratio, codec-working-set, and input-byte limits. Codec
  implementations supplied by optional third-party dependencies can still have
  dependency-specific behavior.
- Run highly hostile documents in an isolated worker with operating-system
  resource limits even when `PdfLoadLimits` is enabled.

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
  (`/FontFile2`), **CFF** (`/FontFile3`, both name-keyed and CID-keyed) and
  **CFF2** (`/FontFile3` with `/Subtype /OpenType`) font
  programs. Glyph usage is read from page and form-XObject content streams; only
  glyphs actually drawn — plus the components of composite glyphs and `.notdef` —
  are kept. Glyph ids are preserved (glyph erasure: CFF charstrings of unused
  glyphs become a bare `endchar`; CFF2 removed `endchar`, so there the erased
  charstring is empty and the advance width, which lives in `hmtx`, is
  untouched), so the font's `cmap`/`charset`/`CIDToGIDMap`/
  `FDSelect` stay valid. Fonts whose code→glyph mapping cannot be resolved
  confidently are left whole.
- `image_compression_quality` (1–100, default off) — re-encode eligible
  RGB/grayscale image XObjects as baseline JPEG (`/DCTDecode`) at that quality
  using the dependency-free encoder (`engine/jpeg_encoder.py`, with per-image
  optimized Huffman tables). Images are
  rewritten only when the result is smaller, so already-small or incompressible
  images are left as-is. Masks, soft-mask targets, images with a `/Decode`
  array, Indexed/Lab colour, and opaque codecs (JPX/CCITT/JBIG2) are
  skipped so colour and transparency are never altered unexpectedly; DeviceCMYK
  is recompressed (as Adobe-marked 4-channel JPEG), but ICC-based CMYK is not.
- `image_max_dimension` (pixels, default off) — cap the longest side of an
  image, box-averaging it down first (aspect ratio preserved). Combined with
  `image_compression_quality` the downscale happens before JPEG encoding; on its
  own a lossless raster is downscaled and kept lossless (Flate).
- `image_target_dpi` (DPI, default off) — cap an image's *effective* resolution
  at its on-page display size. Each image's largest placement is measured from
  the page content (the CTM's transformed unit square); an image whose pixels
  exceed the target DPI at that size is box-downscaled to match (aspect
  preserved, only ever shrinking). An image with no page-level placement has an
  unknown display size and is left untouched.
- `image_progressive` (default off) — emit recompressed JPEGs as progressive
  (SOF2) instead of baseline; applies to the `image_compression_quality` path.
- `use_object_streams` (default on) — after optimizing, a full save packs
  eligible objects into an object stream (`ObjStm`) located by a cross-reference
  stream (`XRef`), the single biggest file-size lever. Produces PDF 1.5+ output
  and is automatically skipped for encrypted/signed saves and when stream
  compression is disabled.

Boundaries:

- Image recompression uses a baseline JPEG encoder for DeviceRGB / DeviceGray
  (and ICCBased with N=1/3, 4:2:0 chroma) and DeviceCMYK — device or **ICCBased
  with N=4** — full-resolution and Adobe-marked. A `/Decode` array that only
  inverts (`[1 0]` per component) is folded into the samples and dropped; any
  other sample remapping is left untouched. Everything else is brought to
  device samples first and then recompressed: an **Indexed** image has its
  palette folded into the samples (the space becomes `DeviceRGB`), **Lab**,
  **Separation**, **DeviceN** and other non-device spaces are converted through
  the same colour machinery the renderer uses, **1/2/4/16-bit** samples are
  normalised to 8, and a **CCITT**, **JBIG2** or **JPEG 2000** payload is
  decoded like any other filter. A **stencil** (`/ImageMask`) is a shape rather
  than a picture, so it is only ever downscaled -- coverage averaged per
  destination cell and thresholded back to one bit, re-packed and Flate-encoded
  -- never JPEG-encoded. Every rewrite still has to make the stream smaller or
  the original is kept, so a low-bit-depth image often stays as it was: widening
  1-bit samples to 8 costs more than it saves. **Soft/stencil masks** are never
  JPEG-encoded — ringing on a sharp mask is visible — but they *are* downscaled
  along with the image that carries them, at that image's display size.
  Resampling is box-average downscaling only (no upscaling). DPI targeting
  follows **form XObjects**, composing each form's `/Matrix` with the CTM at its
  `Do`, so an image drawn only inside a form is measured in page space like any
  other; nesting is bounded and a form that draws itself terminates. An image
  that is never placed keeps its resolution, its display size being unknown.
- Font subsetting (glyph erasure) covers embedded **TrueType** (`/FontFile2`),
  **CFF** and **CFF2** (`/FontFile3`) programs. Handled: Type0 fonts with Identity
  encoding
  over a CIDFontType2 (TrueType) or a CIDFontType0 backed by either a name-keyed
  CFF (CIDs are glyph ids per PDF 32000 9.7.4.2) or a **CID-keyed CFF**
  (`/CIDFontType0C` — the `FDArray`/`FDSelect`/charset and each Font DICT's
  `Private` are relocated, and CIDs are mapped to glyph ids through the CFF
  charset). Simple fonts of every embedded flavour resolve their used codes
  through one shared step: `/Differences`, then the predefined base encoding
  (`Standard`, `WinAnsi`, `MacRoman`, `MacExpert` — the **bundled Adobe tables**,
  not the
  stdlib codecs, which disagree on real codes such as WinAnsi `0xA0`/`0xAD` and
  MacRoman `0xDB`), then the font program's own built-in encoding. A simple
  `/TrueType` font is subset via its symbol `cmap`, or by mapping that glyph name
  to a scalar through the Adobe Glyph List and looking it up in the font's
  Unicode `cmap`. A simple `/Type1` font backed by a name-keyed CFF
  (`/FontFile3`) maps the glyph name to a glyph id through the **CFF charset**
  (including the predefined **Expert** and **ExpertSubset** charsets, whose glyph
  ordering the CFF specification fixes rather than the font storing it),
  falling back to the CFF's own built-in encoding and then to StandardEncoding —
  which is what a CFF carrying a predefined encoding means. A simple **Type 1**
  font (`/FontFile`, eexec-encrypted) is subset by re-encrypting its Private dict
  with each unused glyph's charstring emptied (``0 0 hsbw endchar``), keeping the
  glyph names the same resolution produces. **CFF2** (`/FontFile3` with
  `/Subtype /OpenType`, PDF 2.0) is subset as either a CIDFontType0 — CFF2 has no
  charset, so CIDs are glyph ids — or a simple font resolved through the sfnt's
  own `cmap`; the `FDArray`/`FDSelect`, each Font DICT's `Private` and the
  `ItemVariationStore` are relocated, and every other sfnt table is copied
  through, so a subset variable font still instantiates. **Not** subset (left
  whole): CID-keyed CFF in a simple font, and any font naming a `/BaseEncoding`
  outside the four PDF 32000-1 Table 114 allows — such a name is malformed
  rather than unsupported, and guessing which was meant could erase a used
  glyph. Resolution falls back to leaving the font whole for any used code it
  cannot map exactly, so a used glyph is never erased.

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
- Append simple authored content to a page: positioned Standard-14 text or
  embedded Unicode text with `Page.add_text()`, raw/JPEG/PNG image XObjects
  with `Page.add_image()`, rectangles with `Page.draw_rectangle()`, and lines
  with `Page.draw_line()`. Pass `font=` as a `FontDescriptor`, bytes,
  bytearray, string/path to author Unicode through a subset Type0/CID font;
  the writer emits two-byte codes, `/ToUnicode`, `/W`, and a CID-to-glyph map.
  Authored segments can opt into tagged PDF structure with `tag=...`,
  `alt=...`, and `actual_text=...`; the writer emits `BDC`/`EMC` marked
  content and maintains `/StructTreeRoot`, page `/StructParents`, and the
  `/ParentTree`.
- Collect the **graphic elements** of a page or document with
  `aspose_pdf.graphics.GraphicsAbsorber`: every painted path and placed image
  comes back as a `GraphicElement` with its bounding box in page (user) space,
  the paint operation (`fill`, `stroke`, `fill_stroke`, `clip`), the image's
  XObject name, the device fill/stroke colour and the stroke width in page
  space. The walk tracks `q`/`Q`/`cm`, descends into form XObjects composing
  each `/Matrix` (bounded depth, a form that draws itself terminates), and
  bounds curves exactly rather than by their control points.
- Iterate pages with `Document.iter_pages()`.
- Iterate decoded page content streams with `Document.iter_page_content_streams()`.
- Render a page to a dependency-free RGB raster with `Page.render()` or
  `Document.render_page()`, then save it as PNG, TIFF or JPEG through
  `RasterizedPage.save()` / `Page.save_as_image()` /
  `Document.save_page_as_image()`; the format follows the file suffix
  (`.png`, `.tif`/`.tiff`, `.jpg`/`.jpeg`). The renderer covers common content stream
  operators for graphics state, paths, fills/strokes, clipping, image XObjects,
  form XObjects, and text. Text shown with an embedded font is filled from its
  real glyph outlines for all three program formats -- TrueType `glyf`
  (`/FontFile2`, simple and composite), CFF (`/FontFile3`, name-keyed and
  CID-keyed Type 2 charstrings with subroutines and flex), and Type 1
  (`/FontFile`, eexec/charstring-decrypted Type 1 charstrings with flex and
  hint-replacement OtherSubrs) -- resolved through Identity CID maps, a simple
  font's `/Encoding`/symbol cmap, the CFF charset/built-in encoding, or the
  Type 1 built-in encoding, with Type 1 `seac` accent composites drawn from
  their StandardEncoding components. A composite font whose `/Encoding` names
  one of the bundled predefined CJK CMaps is also
  drawn: the show string is split on the CMap's mixed single/double-byte
  codespaces and each code mapped to a CID before the descendant font's
  `CIDToGIDMap` (CIDFontType2) or CFF charset (CIDFontType0), against the
  descendant `CIDSystemInfo`. Fonts with no embedded program -- the
  Standard 14 and other non-embedded simple fonts -- are filled from bundled
  open substitutes chosen by base-font name and FontDescriptor flags:
  metric-compatible Liberation faces (SIL OFL 1.1) for the
  Helvetica/Times/Courier families, and DejaVu Sans shape subsets (Bitstream
  Vera license) for Symbol and ZapfDingbats, indexed through those fonts'
  built-in encodings -- so common text, Greek/math symbols and dingbats all
  render as real glyphs.
- Choose the output colour form with `mode=`: `"rgb"` (default), `"gray"`
  (Rec. 601 luminance) or `"bilevel"` (1 bit per pixel, thresholded at
  `threshold=`, default 128 -- a plain cut, not dithering). JPEG has no bilevel
  form and rejects it rather than emitting a grey image with ringing.
- TIFF output is **Deflate-compressed by default** (`compression="deflate"`,
  or `"none"` for a raw strip): an uncompressed A4 page at 300 dpi is about
  25 MB. The encoder is pure Python, writes the render resolution into the
  file, and `Document.save_as_tiff()` writes several pages into one
  **multi-page TIFF**, rendering and encoding them one at a time rather than
  holding every raster in memory.
- JPEG output uses the bundled encoder (`quality=`, default 85) and records the
  render resolution as the JFIF pixel density.
- Anti-alias the raster by supersampling: `antialias=True` (the default) renders
  at 3x and box-downsamples for smooth text, fill, stroke, and image edges; an
  integer 1-8 sets the factor, and `False` (or `1`) renders hard-edged.
- Apply common `/ExtGState` painting controls during rendering: line width,
  fill/stroke constant alpha (`ca`/`CA`), and standard separable blend modes
  (`Normal`, `Multiply`, `Screen`, `Overlay`, `Darken`, `Lighten`,
  `ColorDodge`, `ColorBurn`, `HardLight`, `SoftLight`, `Difference`,
  `Exclusion`) plus the non-separable `Hue`, `Saturation`, `Color`, and
  `Luminosity` modes for fills, strokes, shadings, images, and pattern-painted
  content. Unsupported blend modes fall back to `Normal`.
- Apply soft masks. An image XObject's `/SMask` supplies per-pixel alpha, so
  transparent (PNG-style) images composite over the page. An ExtGState
  `/SMask` builds a device-space mask by rendering its `/G` transparency group
  offscreen and reducing it to alpha -- group luminosity for `/S /Luminosity`
  (over the `/BC` backdrop, default black) or painted coverage for
  `/S /Alpha` -- with an optional `/TR` transfer function. The mask modulates
  every subsequent paint (fills, strokes, glyphs, shadings, patterns, images)
  until cleared by `/SMask /None`, and is saved/restored by `q`/`Q`.
- Composite transparency groups (`/Group /S /Transparency`) as units. The
  renderer honors isolated (`/I`) and knockout (`/K`) group backdrops, including
  internal blend modes and partial alpha, and bounds nested offscreen buffers by
  `PdfLoadLimits.max_codec_work_bytes`.
- Paint function-based (`ShadingType 1`), axial (`ShadingType 2`), and radial
  (`ShadingType 3`) gradients through the `sh` operator and shading-pattern
  fills (`PatternType 2`). Type 1 honours `/Domain`, `/Matrix`, `/BBox`, and the
  pattern-only `/Background` semantics. PDF function types 0 (sampled), 2
  (exponential), 3 (stitching), and 4 (bounded PostScript calculator) are
  evaluated over DeviceGray/RGB/CMYK, ICCBased, Separation, DeviceN, and
  NChannel colour spaces; special colours use their alternate space and tint
  transform. Sampled functions support multiple inputs and 1/2/4/8/12/16/24/32
  bits per sample. Calculator source, procedure nesting, operand-stack size,
  sampled interpolation, and execution data are bounded by the document load
  limits.
- Paint free-form and lattice Gouraud triangle meshes (`ShadingType 4` and `5`)
  and Coons and tensor-product patch meshes (`ShadingType 6` and `7`). The mesh
  bitstream decoder supports the ISO coordinate/component/flag widths, shared
  edges, optional color functions, and bounded materialization. Curved patches
  use device-scale-adaptive subdivision up to 64-by-64 cells, with geometry and
  component-error thresholds checked before bounded triangle materialization.
- Resolve Separation, DeviceN, and NChannel colours through their tint
  transforms for path fills and shadings. `/OP`, `/op`, and `/OPM` drive a
  composite overprint preview for spot/DeviceN paints and DeviceCMYK mode 1;
  this uses multiplicative ink approximation on the RGB backdrop.
- Fill with tiling patterns (`PatternType 1`): the pattern cell is repeated on
  its `/XStep`/`/YStep` lattice, clipped to the path being filled. Both coloured
  (`PaintType 1`) and uncoloured (`PaintType 2`, taking the colour from `scn`)
  patterns are supported.
- Use `PdfFileEditor` to concatenate, extract, insert, delete, append, and add a
  blank page through file-based workflows.

Boundaries:

- Page rendering is a best-effort rasterizer, not a certification-grade visual
  engine. Its overprint support is a composite RGB preview, not a plate-accurate
  separation or process/spot ink model, and complete PDF 2.0 imaging semantics
  are not implemented. Curved mesh patches use bounded adaptive tessellation
  rather than analytical inversion, knockout handling uses the renderer's
  supersampled pixel shape, and the `/Alpha` soft-mask subtype approximates
  alpha with painted coverage.
- Glyph outline rasterization covers all three embedded program formats --
  TrueType (`glyf`), CFF (`/FontFile3`), and Type 1 (`/FontFile`, including
  `seac` accent composites). Fonts with no embedded program are filled from
  bundled substitutes (the Standard 14 families plus Symbol/ZapfDingbats, and
  unknown non-embedded fonts routed to a sans/serif/mono substitute by their
  FontDescriptor flags), or, when the caller supplies font sources through
  `FontSubstitutionOptions`, from a real face resolved out of those (see
  [Fonts](#fonts)) -- which is what lets a non-embedded CJK font draw glyphs
  rather than boxes. The Latin substitutes are Latin-subset Liberation
  faces and the symbolic ones DejaVu subsets, so glyphs outside that coverage
  (including Symbol's private-use bracket-extender pieces, and the oldstyle
  figures and small caps an Expert-encoded *non-embedded* font would want) and
  unknown symbolic fonts
  (e.g. non-embedded Wingdings, deliberately boxed rather than mapped to a Latin
  face to avoid drawing the wrong glyphs) are drawn as glyph boxes. Simple CFF
  and Type 1 fonts under any of the four PDF predefined encodings —
  Standard/WinAnsi/MacRoman/**MacExpert** — or a
  `/Differences` map resolve real glyphs through the font's charset (the
  predefined **Expert**/**ExpertSubset** charsets included) and the
  Adobe Glyph List rather than falling back to boxes. Latin GSUB
  (ligatures, kerning) through a substitute face is deliberately **not**
  applied: the PDF's own `/Widths` place every glyph, so kerning would move
  text away from where the producer put it, and a ligature would merge two
  codes that the page positions separately. Complex-script substitute runs
  *are* cursively joined, because that changes glyph shapes without changing
  the count or the advances (`shape_substitute_text`, a no-op for the bundled
  Latin/symbol faces).
- A composite font whose descendant carries **no** embedded program draws
  boxes unless font sources are configured; with them it resolves a face and
  maps each CID to Unicode and on to that face's glyphs (see [Fonts](#fonts)).
- Composite-font glyph rendering covers Identity encodings, every bundled
  predefined CJK CMap, and **embedded (stream) CMaps** (decoded through the same
  parser extraction uses, so a font that ships its own CMap renders real glyphs).
  A *named* predefined CMap outside the bundle, or a bundled name
  whose descendant `CIDSystemInfo` does not match, is still drawn as glyph boxes
  — rendering needs a code→CID table the library does not bundle, exactly as for
  extraction. **Vertical CMaps** (`WMode 1`, bundled or embedded) now position
  correctly: each glyph is offset by its `/W2` (or `/DW2`) position vector and
  the text advances downward by the vertical displacement.
- HTML and Markdown export carry structure, not appearance: exact positioning,
  colour, fonts and inline styling are dropped, and a page becomes a flow
  rather than a fixed layout (HTML pages are separated by a rule). The
  structure is `auto_tag`'s, so it inherits its limits -- headings are inferred
  from font size alone, list nesting is flat, a table needs a regular grid, and
  a figure's alternate text cannot be invented. Use SVG export for a facsimile.
- SVG export writes **polylines, not curves**: the renderer flattens Béziers
  while building a path, so that is what reaches the exporter. Text is glyph
  outlines, which renders exactly and needs no embedded font, but is not
  selectable or searchable. A shading SVG has no gradient for -- function-based
  and mesh -- is sampled into an embedded image rather than dropped, as are
  soft-masked images (through an SVG `<mask>`). Blend modes, transparency
  groups and knockout are not expressed; the affected content is drawn without
  them. SVG has no multi-page model, so a document becomes one file per page.
- `GraphicsAbsorber` collects every mark a page makes: painted paths, placed
  images (XObject and inline `BI`/`ID`/`EI` alike), shown **text runs** and
  `sh` shading fills. A text element carries the box its glyphs occupy,
  measured with the renderer's own font metrics rather than a second set, and
  its font resource name; for the text itself use `TextFragmentAbsorber`. A
  stroked path's box includes the stroke width, which straddles the geometry.
  An `sh` element covers the clip region it paints. Colour set through an
  ICCBased, Indexed, Lab, Separation or DeviceN space is resolved to RGB; only
  a **pattern**, which has no single colour, still reports `None`. Text in
  rendering mode 3 or 7 puts nothing on the page and is not collected. The
  collection it returns is an in-memory container: adding or removing elements
  never changes the page.
- **Export a document as HTML or Markdown** with `Document.to_html()` /
  `to_markdown()`, `save_as_html()` / `save_as_markdown()`, or
  `Document.save(path, DocFormat.HTML)` / `DocFormat.MARKDOWN` (the
  `HtmlSaveOptions` and `MarkdownSaveOptions` objects select the same exports).
  This is a conversion to a **flowing document**, not a facsimile: the same
  layout analysis `auto_tag()` uses infers headings (`H1`-`H3` by size tier),
  paragraphs (wrapped lines joined), bulleted and numbered lists (markers
  recognised and stripped), tables (aligned grids, first row as headers) and
  figures, and the text is decoded exactly as `extract_text()` decodes it.
  Images are embedded as data URIs through the same reconstruction
  `save_image()` performs. Markdown output is GFM, escaped only where a
  character would otherwise change the meaning.
- **Export a page as SVG** with `Page.to_svg()` / `Page.save_as_svg()`,
  `Document.save_as_svg()` or `Document.save(path, DocFormat.SVG)`. The
  exporter *is* the renderer: it subclasses the rasterizer and replaces only
  the places that put marks on a canvas, so the two agree on geometry by
  construction. Paths keep their fill rule (including even-odd, which the
  raster path drops), strokes carry width, dash pattern, cap and join, clips
  become `<clipPath>`, text becomes glyph outlines, images become embedded
  PNGs placed by their matrix, and axial/radial shadings become SVG gradients.
- Layout reflow remains out of scope.

## Optional Content (Layers)

Supported:

- List the document's optional content groups through `Document.layers`: each
  `Layer` reports its `/Name`, its `/Intent`, its COS object number, and
  whether the default configuration (`/OCProperties /D`) shows it.
- Switch a layer on or off (`layer.visible = False`). The change is written
  into the default configuration's `/ON` / `/OFF` arrays, so it survives a
  save and is what every consumer below reads.
- **Rendering skips hidden content**: a `/OC ... BDC` marked-content section
  whose group is off is not painted (nested `BDC`/`BMC` are tracked so the
  matching `EMC` ends it), and neither is an image or form XObject carrying its
  own `/OC`, nor an annotation with one. The content still runs, so graphics
  state, transformations and clipping inside a hidden section apply exactly as
  in a viewer.
- **Text extraction skips hidden content** the same way: text inside a hidden
  layer is not returned by `PdfExtractor` or `Document`-level extraction.
- **Graphics absorption skips hidden content**: `GraphicsAbsorber` reports the
  elements a viewer would show.
- Resolve an `/OCMD`: the `/OCGs` list under the `/P` policy (`AnyOn` -- the
  default -- `AllOn`, `AnyOff`, `AllOff`), and a `/VE` visibility expression
  built from `/Not`, `/And` and `/Or` over group references.
- Honour `/BaseState` in the default configuration, with `/ON` and `/OFF`
  overriding it per group.
- **List and apply alternate configurations.** `Document.layers.configurations`
  reports `/OCProperties /D` and every `/Configs` entry — name, creator, which
  layers each shows and which it locks;
  `Document.layers.apply_configuration(name)` adopts one. Everything here
  resolves visibility from the default configuration, so applying a preset
  copies its state into `/D` (base state, the per-layer overrides, `/Order`,
  `/Locked`, `/AS`) while leaving the preset in place to be chosen again — and
  rendering, extraction, absorption and flattening follow it from then on.
  `Document.layers.save_configuration(name)` snapshots the current states as a
  new preset.
- **Resolve for an event, not just for the screen.** A group's `/Usage`
  dictionary says what it should do when viewed, printed or exported, and the
  configuration's `/AS` usage application dictionaries are what turn that into
  a state — `/Usage` alone is inert. `Document.layers.resolve("Print")` reports
  the states without changing anything and `apply_usage("Print")` makes them
  the document's own, so flattening a print copy really drops a "do not print"
  watermark. `View` (with its `/Zoom` and `/Language` categories), `Print` and
  `Export` are evaluated; several categories on one group combine so that any
  category saying OFF wins. A `/Zoom` or `/Language` group is left at its
  configured state unless a magnification or BCP 47 tag is supplied, rather
  than being decided on an invented viewer. Language tags match whole subtags
  (`de` covers `de-AT`, not `den`), and a `/Preferred` group stands in when
  nothing matched exactly. The `View` event is the default everywhere, so a
  group a `/AS` entry hides on screen is hidden in rendering and extraction too.
- **Say what a layer should do** with `Layer.set_usage(printing=False)` (also
  `view`, `export`, `zoom=(min, max)`, `language=`, `preferred=`), which writes
  both the group's `/Usage` and the configuration's `/AS` entry that applies it.

- **Create a layer** with `Document.layers.add(name, visible=…)`. The group is
  registered in `/OCProperties /OCGs` and in the default configuration's
  `/Order`, which is what a viewer's layers panel lists; a document with no
  optional content at all gains the whole structure.
- **Put content on a layer** with `Page.layer(layer)` as a context manager:
  everything authored inside the block is wrapped in `/OC … BDC` … `EMC` and
  the group is registered in the page's `/Resources /Properties` (reusing the
  name on re-entry). Blocks nest. Switching the layer off then hides that
  content in rendering, extraction and every export.
- **Remove a layer** with `Document.layers.remove(layer)`. The group goes; its
  content stays and becomes unconditionally visible, which is what a viewer
  does with an `/OC` it cannot resolve.
- **Flatten to visible content** with `Document.flatten_layers()`. Hidden
  marked-content sections, hidden XObject invocations and hidden annotations
  are deleted from the page's *existing* content streams (not merely
  unreferenced), every surviving `/OC` reference and marked-content wrapper is
  dropped, and `/OCProperties` is removed -- leaving an ordinary PDF that
  renders exactly as the configuration rendered. Returns the number of pages
  changed.

Boundaries:

- Applying an alternate configuration **writes** it into `/D` rather than
  keeping a separate in-memory selection: this library has one notion of "the
  current state", and that is the one every other feature reads. The preset
  survives in `/Configs`, but `/D`'s previous state is overwritten — snapshot
  it with `save_configuration` first if it mattered.
- The `/User` usage category is not evaluated: it names a person, a position or
  an organisation to match against whoever is viewing, and this library has no
  viewer identity to match. `/CreatorInfo` carries no state by definition.
  Radio-button groups (`/RBGroups`) travel with a configuration but are not
  enforced when layers are switched individually.
- A layer marks content, and only content a page's *own* stream shows: an
  ``/OC`` on a form XObject's contents is honoured, but there is no API to tag
  an existing image or annotation with a layer after the fact -- author it
  inside a `Page.layer` block instead.
- Flattening resolves the *default* configuration as it currently stands; a
  reader cannot get the hidden content back, which is the point, so save a copy
  first if the layers still matter. A `/OC` whose properties operand is an
  inline dictionary rather than a name in `/Properties` is left in place rather
  than guessed at, so its content survives flattening.
- PDF/A-1 prohibits optional content entirely; conversion removes
  `/OCProperties` (see [PDF/A And PDF/UA](#pdfa-and-pdfua)), which makes every
  group's content unconditionally visible. `flatten_layers()` first is the way
  to convert while keeping only what was shown.

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
- Resolve bundled Adobe predefined CJK CMaps without `ToUnicode`. **Every
  encoding CMap** the Adobe Japan1, Korea1, GB1 and CNS1 collections define is
  bundled, in both `-H` and `-V` form — the Unicode families (`UCS2`, `UTF8`,
  `UTF16`, `UTF32`, including the `HW` and `JIS2004` variants) and the legacy
  ones (`RKSJ`, `EUC`, `UHC`, `Johab`, `GBK`/`GBK2K`, `B5`, `ETen`, `HKscs`, the
  `pc`/`pv`/`ms` platform variants, and the rest). The `Adobe-<Ordering>-<N>`
  CMaps are excluded by design: their codes already are CIDs, not an encoding.
  For a Unicode-keyed name the code itself carries the scalar, so text and code
  stay a bijection and a replacement can be written back; legacy names use the
  exact Adobe code → CID → Unicode tables, where a character reachable from
  several codes is extracted but not used as a replacement target.
  Resolution validates the descendant font's Adobe Japan1, Korea1, GB1, or
  CNS1 `CIDSystemInfo`, applies `usecmap` vertical overrides, and uses bundled
  BSD-3-Clause Adobe code → CID and CID → Unicode data without runtime network
  access.
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
  matched as one logical string, and consecutive show operators (including
  line-moving `'`/`"`, e.g. two adjacent `Tj`, or a `Tj` followed by a `'` or
  `TJ`) separated only by positionally-neutral operators are joined into one
  logical run, so a phrase split across several elements or operators (common
  with kerning, per-word painting, or line breaks) is rewritten across the
  boundary: the replacement is placed in the element holding the match start and
  the remaining matched characters are removed from the others, leaving the
  kerning adjustments and unmatched elements intact. Font and text-state
  changes (`Tf`, `Tc`, `Tw`, `Tz`, `TL`) do not move the pen and never break a
  run, so a phrase with a differently-styled word in the middle is matched
  across the font change (each element keeps its own font's encoding). When
  the page's fonts provide advance widths (`/Widths`, the CIDFont `/W`/`/DW`
  arrays, or a bundled Standard-14 substitute), a positioning operator (`Td`,
  `TD`, `Tm`, `T*`) that continues the same baseline within a small horizontal
  gap of the tracked pen also keeps the run open — per-word placement, as
  emitted by TeX-style producers, is matched as one phrase, with a word-sized
  gap (≥ 0.18 em) matching a single space. Rise changes (`Ts`), CTM changes,
  `q`/`Q`, off-baseline or distant (> 1 em) jumps, and unmeasurable fonts
  start a new run. Each element keeps its own literal/hex style and
  Latin-1/UTF-16BE encoding. Type0 (composite) fonts with a usable
  `ToUnicode` CMap are edited for Identity-H/V, named, and embedded CMap
  encodings because `ToUnicode` maps show-string character codes straight to
  Unicode. Exact bundled names additionally require a compatible descendant
  `CIDSystemInfo`; a compact code-to-CID view validates only the bounded
  `ToUnicode` keys without expanding the complete Adobe map. The code
  strings are matched over their ToUnicode-decoded text, matched codes are
  spliced out of the raw operand byte-for-byte (matches must cover whole codes
  — a match ending inside a multi-character ligature code is skipped), and
  replacement text is encoded through the reverse `ToUnicode` mapping (an
  unmappable replacement raises). Code length is inferred from the font's
  encoding and `ToUnicode` keys (a uniform fixed-length codespace, or
  codespace-aware/greedy matching for a mixed codespace). Invalid or unmapped
  codes are opaque match barriers. When
  `ToUnicode` is absent, the bundled predefined CMaps above are editable
  through codespace-aware code → CID mappings: the UTF-16 names preserve the
  Unicode scalar encoded by the source code, while legacy names use the Adobe
  CID → Unicode collection tables. Replacement uses only unambiguous reverse
  mappings. An **embedded
  CIDFontType2 (TrueType) under an Identity encoding** is also editable by
  reconstructing code → text from the font's Unicode `cmap` and
  `CIDToGIDMap`. Unresolved Type0 fonts are opaque and are skipped instead of
  falling back to bytewise Latin-1 edits. Case-insensitive matching and
  `max_count` are supported (a spanning match counts once); lazy page contents
  are materialized before editing and the rewritten content persists on save.
- Draw a redaction overlay bar with `redact_text(..., overlay=True,
  overlay_color=(r, g, b))`. After removing the matched text, a filled
  rectangle (a DeviceRGB triple of 0..1, default black) is drawn over each
  removed run's location. The location is found by a best-effort text-position
  tracker (CTM, text matrix, and advance widths from `/Widths` for simple
  fonts, from the CIDFont `/W`/`/DW` arrays for composite fonts, or
  from a bundled metric-compatible substitute) that shares the redactor's run
  grouping, so bars follow matches across font changes and same-baseline
  positioning gaps, and a match spanning a line-moving `'`/`"` draws one bar
  per baseline. Composite fonts are tracked for Identity-H/V (the code is the
  CID), embedded Encoding CMaps, and the bundled predefined CMaps, with match
  text from `ToUnicode`, Adobe collection data, or a reconstructed CIDFontType2
  cmap. Vertical fonts apply CID-specific `/W2` position/displacement metrics
  and `/DW2` defaults. The bar is cosmetic — the text is already removed from
  the content — so a run whose position cannot be tracked is left unmarked.
- Add positioned text to pages with Standard-14 Type1 font resources.
- Mark newly authored text with a structure tag and optional `/ActualText`.

Boundaries:

- OCR is not implemented.
- Existing text replacement/redaction edits the content stream but does not
  reflow layout. Phrases split across several `TJ` elements, consecutive show
  operators, font changes, and same-baseline positioning gaps are matched and
  rewritten; the positional joining is a geometric heuristic (baseline and
  gap thresholds in em units) that requires advance widths, so fonts without
  usable metrics keep positioning operators as run boundaries, and phrases
  split across columns, rise changes, or CTM changes are not matched. Type0
  editing covers any font with a `ToUnicode` CMap, embedded CIDFontType2 fonts
  under an Identity encoding reconstructed from the font program, and the exact
  bundled predefined names listed above for CIDFontType0 or CIDFontType2.
  Without `ToUnicode`, other predefined CMap names remain opaque. A bundled
  name with missing or mismatched `CIDSystemInfo` is also opaque, including
  when a `ToUnicode` stream is present; other named encodings can still use an
  exact `ToUnicode` map, but may lack overlay geometry. Unsupported runs are
  never extracted heuristically or edited bytewise. (The page renderer draws
  glyphs for these same bundled predefined CMaps; see [Pages](#pages).) The
  redaction overlay still
  assumes a balanced content stream (identity CTM at its end). A replacement
  containing right-to-left or complex-script characters is shaped (HarfBuzz plus
  Unicode bidi) and the phrase is matched in its stored visual order: it reuses
  the run's own embedded font when that font already carries every shaped glyph
  (an embedded, Identity-encoded `CIDFontType2` whose shaped advances match `/W`
  with no positioning adjustment), otherwise a shaping-capable `font=` is
  embedded and the shaped run drawn at the match baseline for an upright,
  uniformly scaled placement. A rotated or sheared placement, or a missing
  `font=` when the run's own font cannot represent the shaped glyphs, raises
  rather than emit misshaped glyphs; reshaping needs the optional `text-layout`
  extra. Rich-text spans and general paragraph reflow are still not implemented.

## Fonts

Supported:

- Use the Standard 14 fonts and read embedded TrueType font programs.
- Author Unicode text with `Page.add_text(..., font=...)`. Font inputs may be
  a `FontDescriptor`, bytes/bytearray, or a string/`Path`. TrueType outlines
  are embedded as `/FontFile2` + `CIDFontType2`; OpenType CFF 1 outlines are
  embedded as `/FontFile3` + `CIDFontType0`. Used glyphs are subset without
  renumbering, descriptor metrics and CID widths come from the SFNT tables,
  and missing glyphs fail with `FontEmbeddingException` before page content is
  appended.
- Enable complex-text authoring with
  `Page.add_text(..., layout=TextLayoutOptions(...))` and the optional
  `text-layout` extra. The layout path uses HarfBuzz for script-aware
  GSUB/GPOS shaping, ligatures, kerning, and glyph positioning, plus the
  Unicode bidi algorithm for mixed left-to-right and right-to-left runs. Runs
  with the same direction are itemized by Unicode script before shaping.
- Select fallback fonts in order per combining/ZWJ text cluster, switch PDF
  font resources within a shaped line, and fail before appending page content
  when no supplied font covers a visible character.
- Wrap text to `max_width`, honor explicit newlines, configure line height,
  and align lines using physical (`left`, `center`, `right`) or bidi-aware
  (`start`, `end`) alignment. Logical text is preserved through `/ActualText`
  while positioned glyph CIDs retain their shaped visual order.
- Assign independent two-byte CIDs to TrueType Unicode scalars and emit an
  explicit `/CIDToGIDMap`. This preserves exact `/ToUnicode` extraction even
  when distinct scalars such as space and non-breaking space share a glyph.
- Extend and reuse an authored font resource across repeated `add_text()`
  calls on the same page. The embedded subset, `/ToUnicode`, widths, and
  CID-to-glyph stream are refreshed together.
- Decode `ToUnicode` / CMap mappings for accurate text extraction.
- **Draw non-embedded fonts with faces the caller makes available.** Pass a
  `FontSubstitutionOptions` (as `Document.font_substitution`, or per call to
  `Page.render` / `Document.render_page`) naming font directories, font
  programs supplied as bytes, and/or the platform's own font directories
  (`FontSubstitutionOptions.system()`). A font with no embedded program then
  draws real glyphs instead of glyph boxes, including composite (Type0/CID)
  fonts, which previously had no substitute path at all. Resolution runs in
  three steps: the document's `/BaseFont` name against the real `name` tables
  of the indexed faces (refined by bold/italic); then, for a composite font,
  the well-known families of its character collection, so a PDF naming
  `SimSun` renders on a machine that only has `PingFang SC`; then any indexed
  face whose `cmap` covers the text, pre-filtered on `OS/2 ulUnicodeRange` and
  confirmed against the real `cmap`. CIDs reach Unicode through the font's own
  `/ToUnicode` first and Adobe's bundled CID-to-Unicode table for the
  collection second. Advances come from the PDF's `/Widths` or `/W`, so a
  substituted face changes which glyphs are drawn, never where they sit; a
  composite font always has `/W` (or `/DW`), and a simple font that omits
  `/Widths` for a code -- only legal for the Standard 14 -- falls back to the
  face's own advance, as it already did for the bundled substitutes.
  Discovery is **opt-in**: with no options the renderer uses only the bundled
  substitute faces, exactly as before, and stays identical across machines.
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
- Bound WOFF/WOFF2 input, declared/reconstructed SFNT output, compression ratio,
  table count, and codec working memory through `PdfLoadLimits`; Brotli output
  is consumed incrementally and rejected when it exceeds the declared size.
- Resolve a font by family / full / PostScript name (case-insensitive), falling
  back to the standard-font registry, and obtain embeddable font bytes through
  `FontRepository.open_font()` or `FontDescriptor.get_font_bytes()` (WOFF
  programs are unwrapped to a directly embeddable SFNT).
- Register custom sources with priorities through `FontRepository.add_source()`.
- Select a TTC face through `FontDescriptor.face_index`; direct TTC bytes or a
  TTC path use face zero. Unicode cmap formats 0, 4, 6, 12, and 13 are read,
  including supplementary-plane characters.

Boundaries:

- WOFF2 decoding needs the optional `brotli` dependency. WOFF2 font
  *collections* (`ttcf` flavour) are reconstructed into a TrueType Collection,
  after which the descendant face is selected as for any TTC.
- **CFF2 outline programs are drawn at a chosen variable instance.** The
  ItemVariationStore's regions are read, `fvar` supplies the axes (with `avar`
  applied), and each `blend` resolves to `default + Σ scalar x delta` for the
  requested coordinates -- so a variable CFF2 draws the instance asked for, not
  only its default master. Without a request the deltas are dropped, which *is*
  the default master. Substitute faces use this to reach a style: a modern
  system font ships as one variable file rather than four static ones, so
  `wght` (and `ital`/`slnt`) are set when Bold or Italic is wanted and the
  face's own name does not already say so. Variable **TrueType** (`gvar`) is a
  different mechanism and is not instanced. CFF2 is subset by the optimizer (the
  ItemVariationStore moves with everything else, so a subset variable font still
  instantiates) but is rejected for authoring new text.
- Complex-text authoring requires the optional `uharfbuzz`, `python-bidi`, and
  `fonttools` dependencies
  (`pip install aspose-pdf-foss-for-python[text-layout]`) and an embedded
  primary font; Standard-14 fonts cannot be shaped. Line breaking is greedy at
  whitespace or text-cluster boundaries and does not implement hyphenation,
  justification, rich-text spans, vertical writing, bidi isolate controls, or
  general paragraph layout. Without `TextLayoutOptions`, callers still supply
  a font with a direct cmap entry for every scalar. OpenType CFF2 authoring is
  rejected; CFF 1 mappings that alias different Unicode scalars to one native
  CID are rejected because they cannot provide an exact `/ToUnicode` round
  trip.
- Font substitution indexes a face's SFNT directory, `name` table and 16 bytes
  of `OS/2`, so a directory of large CJK fonts costs a few small reads each;
  whole programs are read (and a TrueType Collection face lifted out of the
  collection) only for a face that wins, and are cached per options object.
  Indexing is bounded in files scanned, file size and cached bytes. A face is
  matched by the names its own `name` table declares, falling back to the file
  stem (or the caller's label) for a stripped subset that has none. It affects
  **rendering only**: text extraction, editing and PDF/A font embedding are
  unchanged, and no substituted program is written into the document.
- Embedded glyph outlines are rasterized by the page renderer (see
  [Pages](#pages)) for all three program formats: TrueType (`glyf`), CFF
  (`/FontFile3`, name-keyed and CID-keyed), and Type 1 (`/FontFile`, including
  `seac` accent composites). The Standard 14 fonts (never embedded) are
  rendered from bundled open substitutes: metric-compatible Liberation faces
  (SIL OFL 1.1, Latin subset) for the text families, and DejaVu Sans shape
  subsets (Bitstream Vera license) for Symbol and ZapfDingbats via their
  built-in encodings. Rendering paints shaped authored glyphs at their stored
  positions and does not re-lay-out existing content streams; it does, however,
  draw complex-script runs that fall back to a substitute face with
  cursive-joined forms instead of isolated glyphs (order-preserving, so nothing
  moves; needs the `text-layout` extra; toggled by `shape_substitute_text`).
  This is active only when the substitute covers the script, which the bundled
  Latin/symbol faces do not, so it is a no-op for those today.
  (Embedded TrueType and CFF — including CID-keyed CFF — glyph subsetting is
  available through `OptimizationOptions.subset_fonts`; see
  [Optimization](#optimization).)

## Images

Supported:

- Extract image XObjects from parsed PDFs.
- Place raw 8-bit DeviceGray/DeviceRGB/DeviceCMYK images, JPEG images, and
  PNG images on pages as image XObjects, at any allowed bit depth and with or
  without Adam7 interlacing.
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
- Decode JPX/JPEG 2000 with the bundled pure-Python decoder, or with Pillow
  (much faster) when the `images` extra is installed.
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
  even when Pillow is absent. **JPEG 2000 is decoded without Pillow too**
  (`aspose_pdf.engine.jpeg2000`); the optional `images` extra
  (`pip install aspose-pdf-foss-for-python[images]`) is now a speed choice
  rather than a capability one. Arithmetic-coded JPEG remains Pillow-only.
- Read reconstruction metadata from an `ImagePlacement`: `width`, `height`,
  `bits_per_component`, and `color_space`.
- Replace or hide an `ImagePlacement` payload in memory.
- Save, replace, hide, and enumerate images through the lower-level `SimplePdf`
  image helpers.
- Collect image placements with `ImagePlacementAbsorber.visit(...)`, which
  accepts a `Page` (that page alone), a `Document`, or an engine PDF. Each
  `ImagePlacement` carries the image bytes, the rectangle it occupies on the
  page (the unit square through the placement matrix, ISO 32000-1 8.9.5.2), the
  placement matrix, the raster's pixel size, and the effective resolution --
  pixels over the size actually drawn. Images are keyed uniquely per document,
  so two pages that both call their image `/Im0` keep both, while one XObject
  shared by several pages is stored once and reported at each placement.
- Compose whole pages into RGB raster output via the page renderer. Image
  XObjects backed by raw/Flate samples, indexed/gray/RGB/CMYK colour spaces, and
  baseline/progressive DCT/JPEG streams are painted into the page raster,
  honouring an image `/SMask` as per-pixel alpha (see [Pages](#pages)).
- Deduplicate identical image payloads during optimization.
- **Encode** pixels back to a JPEG with a **dependency-free** encoder
  (`aspose_pdf.engine.jpeg_encoder`: grayscale, RGB with 4:2:0 chroma
  subsampling, and CMYK — four full-resolution channels with an Adobe `APP14`
  marker; per-image **optimized Huffman** tables computed from the actual symbol
  statistics — smaller than the fixed Annex K tables at no quality cost; and an
  optional **progressive** (SOF2) mode — one DC scan then one AC scan per
  component, full resolution) and **box-downscale** pixels
  (`aspose_pdf.engine.image_resample`).
  `Document.optimize` uses both to apply `image_compression_quality` (recompress
  to JPEG) and `image_max_dimension` (cap the longest side); see
  [Optimization](#optimization).

Boundaries:

- A JPEG 2000 image the decoder cannot read is **not drawn**. An undecodable
  filter leaves the compressed bytes in the stream, and painting those as if
  they were samples produces a page of noise; the renderer decodes the
  codestream itself or leaves the area untouched.
- `Page.add_image()` accepts raw samples, JPEG, and **PNG** at every bit depth
  and colour type ISO 15948 allows — 1/2/4/8/16 bits, greyscale, truecolour,
  palette and their alpha forms — progressive or **Adam7 interlaced**. Samples
  are normalised to 8 bits (16-bit keeps the high byte, sub-byte greyscale is
  scaled, palette indices are looked up rather than scaled) and an alpha
  channel is dropped.
  PNG input bytes, dimensions, filtered output, compression ratio, and working
  memory are bounded before large decode allocations. The JPEG encoder has
  optimized Huffman tables and baseline or progressive output; baseline RGB
  uses fixed 4:2:0 subsampling while progressive and CMYK are full-resolution
  (progressive is spectral-selection only, without successive approximation);
  resampling is box-average downscaling (no upscaling), with an optional DPI
  target driven by on-page placement size (see Optimization).
- **JPEG 2000 (`/JPXDecode`) decodes without any optional dependency.** The
  bundled decoder covers the JP2 container and the bare codestream, tier-2
  packet decoding (tag trees, every progression order, precincts, multiple
  quality layers, tiles), the EBCOT tier-1 block decoder over the MQ arithmetic
  coder, both wavelets (5/3 reversible and 9/7 irreversible), the reversible
  and irreversible colour transforms, and component subsampling. The reversible
  path is lossless and reproduces the encoder's input exactly; the irreversible
  path is floating point and agrees with OpenJPEG to within a step or two per
  channel. Pillow is still used when installed, being several hundred times
  faster on a full-page scan.
- Arithmetic-coded JPEG remains unsupported by the pure-Python raster path.

## Forms

Supported:

- Read AcroForm fields through `Document.form`.
- Iterate form fields and access fields by name.
- Extract text, checkbox, radio, listbox, combobox, and push-button fields.
- Set a field value by name through the `Field.value` setter.
- Create and remove AcroForm fields entirely through the public API:
  `Form.add_text_field()`, `add_checkbox()`, `add_radio_group()`,
  `add_list_box()`, `add_combo_box()`, `add_push_button()`, and
  `remove_field()` (also available as `Field.remove()`). Pages may be supplied
  as a `Page` or zero-based page index; dotted names create non-terminal field
  parents. The writer emits indirect terminal fields and widget annotations,
  widget `/Parent` and page `/P` links, page `/Annots`, AcroForm `/Fields`,
  `/DR` and `/DA`, common/type-specific flags, choice `/Opt` and multiselect
  `/I`, `/DV`, and generated appearances. Choice options may be strings or
  `(export_value, display_value)` pairs.
- Author empty (unsigned) signature fields with `Form.add_signature_field()`.
  The field is written as `/FT /Sig` with a widget on the page, the AcroForm
  `/SigFlags` SignaturesExist bit is set (existing bits preserved), the widget
  renders as an empty box, and the field carries no value until signed. It
  round-trips and reports as a `signature` field and can be removed like any
  other. A **seed value** (`seed_value=` → `/SV`) constrains how the field may
  be signed (`filter`, `sub_filter`, `digest_method`, `reasons`, `v`,
  `legal_attestation`, `add_rev_info`, `lock_document`, `appearance_filter`,
  `mdp` and `timestamp`, plus
  `required` naming the entries whose `/Ff` bit makes them binding rather than
  advisory — `mdp` binds either way, having no bit), and a **field lock**
  (`lock=` → `/Lock`) names the fields the
  signature freezes (`action` of `All`/`Include`/`Exclude`, with `fields` for
  the latter two).
- Regenerate field appearance streams from their values via
  `Document.generate_field_appearances()` or `Form.generate_appearances()`:
  text and choice fields are drawn from their `/V` and default appearance
  (`/DA` font, size — including auto-size — and colour, with `/Q` quadding and
  multi-line support, wrapping the value to the field width with greedy word
  wrap that honours explicit newlines), resolving the font from the AcroForm
  `/DR` (synthesising
  Helvetica when absent). Wrap points and centre/right quadding use real
  glyph-metric advance widths — from the field font's `/Widths` or the bundled
  metric-compatible substitute (Liberation) — falling back to a flat estimate
  only when neither resolves. Authored text and choice widgets draw their `/MK`
  background and `/BS` border; list boxes draw visible `/Opt` rows and highlight
  the selected values. Check box / radio widgets that already carry an
  on-state appearance have their `/AS` pointed at the value; a widget with *no*
  appearance streams gets a synthesised `/AP /N` Off/On pair — the `/MK`
  background (`/BG`) and border (`/BC`) plus a check mark (a ZapfDingbats `/CA`
  caption glyph, default `4`) for a check box or a filled vector dot for a
  radio — with the on-state name taken from `/AS`/`/V` (defaulting to `On`). The
  normal appearance of a caption-only push button is also synthesised from its
  `/MK` caption, background, and border. The
  AcroForm `/NeedAppearances` flag is cleared so the generated
  appearance is honoured. `flatten()` runs this automatically.
- Flatten form fields and annotations into static page content (generating
  missing appearances first), mapping each appearance form's `/BBox` onto the
  widget `/Rect`.
- Extract unsigned form fields and annotations with `UnsignedContentAbsorber`.

Boundaries:

- Dynamic XFA processing is not implemented.
- Rich-text fields (`/RV`, Ff bit 26) are rendered from their XHTML markup with
  per-span size/colour/bold/italic and paragraph alignment in the Helvetica
  family (see Annotations for the shared renderer's limits); a plain
  variable-text value is single-font and measured per byte code (single-byte
  simple fonts — composite/Type0 field fonts fall back to a flat width
  estimate). Check box / radio appearances are synthesised (a ZapfDingbats check
  or a vector dot). Push buttons author normal/rollover/down (`/AP` `N`/`R`/`D`)
  faces from their caption, accept `/MK` border/background colors and a typed
  `action`, and — with link annotations, outline items, and `Page.add_link` —
  use the typed action/destination API (`aspose_pdf.interactive`: `GoTo`/`URI`/
  `GoToR`/`Named`/`JavaScript`/`Launch` actions and `Fit`/`XYZ`/`FitH`/`FitV`/
  `FitR`/`FitB` destinations, serialized to `/A` and `/Dest`). A text field can
  embed a **Type0 (CID) font** (`add_text_field(font=…)`): the font is added to
  the AcroForm `/DR`, `/DA` points at it, and a CID-encoded `/AP` is baked at
  authoring time so a non-Latin value renders (this baked appearance is left
  intact by later `generate_appearances`, which cannot re-encode a Type0 value).
  A push button can carry an **icon** (`add_push_button(icon=…)`, JPEG or
  PNG): the image is wrapped in a form XObject as `/MK /I`,
  drawn into all three faces scaled proportionally and centred, with a `/MK /IF`
  that matches what is baked and `/MK /TP` 1 (icon only) or 2 (caption below).
  **Submit and reset** are available through the typed action API
  (`SubmitFormAction`, `ResetFormAction`), including the `/Fields` list, its
  include/exclude flag, and the FDF/HTML/XFDF/PDF submit format.
  Type0 field **rich text** (`/RC` values fall back to the plain `/DA`
  appearance) and XFA authoring are not implemented. Signature fields can be authored *and* signed (see above and
  [Security](#security-encryption-and-signatures)); XFA authoring is not.

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
  `StrikeOut`, `Squiggly` — plus the text-bearing/marker subtypes `FreeText`
  (`/Contents` word-wrapped in the `/DA` font size and colour, or the `/RC`
  rich-text markup when present — per-span size/colour/bold/italic and paragraph
  alignment; with `/Q` quadding, `/C` background and a border box), `Stamp` (a
  captioned box from `/Name` or `/Contents`, rubber-stamp red by default), and
  `Caret` (a filled marker triangle) — via `Annotation.generate_appearance()`,
  `Page.annotations.generate_appearances()`, or `Document.generate_appearances()`.
  Text-bearing subtypes register the synthesised Helvetica-family fonts they use
  in the form's `/Resources /Font`.
- Draw border and line decorations on those appearances: dash patterns (from
  `/BS /S /D` with `/D`, or a legacy `/Border` dash array) on `Square`,
  `Circle`, `Line`, `Polygon`, `PolyLine` and `FreeText` borders; `Line`/
  `PolyLine` endings (`/LE`) — `OpenArrow`, `ClosedArrow`, `ROpenArrow`,
  `RClosedArrow`, `Circle`, `Square`, `Diamond`, `Butt`, `Slash` (closed heads
  filled with the interior/line colour); and cloud borders (`/BE /S /C`, with
  intensity `/I`) that replace the straight edges of `Square` and `Polygon` with
  outward Bézier scallops.
- Flatten annotations into page content, mapping each appearance form's `/BBox`
  (and `/Matrix`) onto the annotation `/Rect`; appearances are synthesised for
  supported subtypes first so they are not dropped.

Boundaries:

- Appearance synthesis covers the shape, text-markup, `FreeText`, `Stamp` and
  `Caret`, `Text` and `FileAttachment` subtypes above; text uses the
  synthesised Helvetica family measured
  with the bundled substitute's real glyph metrics. Rich text (`/RC`, `/RV`)
  renders a subset — `<p>`/`<span>`/`<b>`/`<i>` with `font-size`, `color`,
  `font-weight`, `font-style` and `text-align` — in the Helvetica family only
  (no embedded/other fonts — a Type0/CID field font therefore falls back to the
  plain `/DA` appearance rather than styled spans — backgrounds, or nested block
  layout), and `Stamp` is
  a captioned box rather than the standard rubber-stamp artwork. Dash patterns, `/LE` line endings and `/BE` cloud borders
  are drawn (see above); the cloud is a uniform scallop approximation and line
  endings are sized heuristically from the border width. Check-box/radio *widget*
  appearances are synthesised through the forms API (see Forms). **`Text`**
  (sticky note) and **`FileAttachment`** annotations draw their standard icon
  from `/Name` — `Comment`, `Key`, `Note`, `Help`, `NewParagraph`, `Paragraph`,
  `Insert` and `PushPin`, `Graph`, `Paperclip`, `Tag` — as vector artwork
  honouring `/C`, squared and centred in the annotation rectangle, with an
  unknown name falling back to the subtype's default as a viewer does. Other
  subtypes (`Sound`, `3D`, …) still need an appearance supplied via
  `appearance_normal`.
- The page rasterizer **composites annotation appearances** over the page
  content, the way a viewer shows them: each annotation's `/AP` `/N` is placed
  by fitting its `/Matrix`-transformed `/BBox` to `/Rect` (ISO 32000-1 12.5.5),
  an appearance-state subdictionary is resolved through `/AS`, and annotations
  flagged Hidden or NoView — and `Popup` subtypes — are skipped. Pass
  `draw_annotations=False` to `Page.render` / `Document.render_page` for the
  page content alone. An annotation with no appearance draws nothing: the
  renderer never invents one, so call `generate_appearances()` first.

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
  description, creation / modification dates (a `datetime` or a pre-formatted
  `D:` string) stored in the embedded file `/Params`, and an associated-file
  relationship written as `/AFRelationship` (one of `AF_RELATIONSHIPS`; invalid
  values are rejected). Re-adding an existing name replaces it.
- Remove an attachment with `Document.remove_attachment(name)` (returns whether
  one was removed); removing the last one drops the `/EmbeddedFiles` name tree.
- Read attachment metadata back through a typed API: `Document.embedded_files`
  returns `FileSpecification` objects (`name`, `contents`, `mime_type`,
  `description`, `creation_date`, `mod_date`, `relationship`, `size`), and
  `Document.get_embedded_file(name)` looks one up by name. The MIME `/Subtype`,
  `/Desc`, `/Params` dates and `/AFRelationship` are decoded back to Python
  values (`#XX`-escaped names and `D:` dates are parsed; the default
  `Unspecified` relationship reads back as `None`), so a save / reload round trip
  preserves them.
- Flate-compress the embedded payload by default (`compress=True`), skipping
  compression automatically when it would not make the payload smaller.
- Preserve tested attachment names and bytes through COS round trips, including
  attachments added in memory before the first save.

Boundaries:

- The typed `FileSpecification` view is read-only; mutate attachments through
  `Document.add_attachment` / `Document.remove_attachment` (or the `attachments`
  mapping) and re-read `embedded_files`.
- Embedded-file name trees are **read** through their full `/Kids` structure —
  a tree another tool balanced into intermediate nodes yields the same
  attachments as a flat one, in the tree's own order, with depth, cumulative
  entry count, and revisited nodes bounded by `PdfLoadLimits`. They are
  **written** back as a single flat `/Names` array.

## Security, Encryption, And Signatures

Supported:

- Encrypt and decrypt documents with user and owner passwords.
- **Open every standard security handler flavour**, whichever tool wrote it:
  40-bit RC4 (`/V 1 /R 2`), 128-bit RC4 (`/V 2 /R 3`, and `/V 4` with a `/V2`
  crypt filter), AES-128 (`/V 4 /R 4`, `AESV2`) and AES-256 (`/V 5`, both the
  deprecated revision 5 and revision 6). The cipher is taken from `/V` and from
  the crypt filter `/StmF` selects, and the per-object key follows Algorithm 1
  (MD5 over the file key, object and generation number, plus the `sAlT` suffix
  for AES) so streams *and* strings decrypt.
- Decode an encrypted document's **images, form XObjects and appearance
  streams** on demand: the key stays available for the COS graph after load,
  so a rendered page of an encrypted PDF looks like the same page unencrypted.
- Open a document protected by an **owner password only**: an empty user
  password is tried before one is demanded, as every reader does.
- Choose the cipher when encrypting with `Document.encrypt(..., algorithm=...)`:
  `"AES-256"` (the default, written as `/V 5 /R 6` with `/UE`, `/OE` and the
  encrypted `/Perms` revision 6 validates), `"AES-128"` (`/V 4 /R 4`, `AESV2`)
  or `"RC4"` (128-bit, `/V 2 /R 3`). Names are normalised (`aes256`, `AES_128`,
  `rc4-128`); anything else raises instead of quietly using a weaker cipher.
  The declared revision always matches the key derivation actually used, and
  the key is bound to the `/ID` the file carries, so other readers open what
  this library writes.
- **Encrypt for certificate recipients** with the public-key handler
  (`/Adobe.PubSec`) through `Document.encrypt_for_recipients([...])`, and open
  such a document with `Document(source, certificate=..., private_key=...)`.
  There is no password: a random seed is wrapped in a CMS `EnvelopedData` for
  every recipient's RSA public key, and the file key is a SHA-256 (AES-256) or
  SHA-1 (older ciphers) hash over that seed and **every** recipient blob in
  `/Recipients` order. `adbe.pkcs7.s5` (`/V 5 /R 6`, `AESV3`) is written for
  AES-256 and `adbe.pkcs7.s4` (`/V 4 /R 4`) for AES-128 and RC4-128; on read,
  `/Recipients` is taken from the crypt filter for `/V` 4-5 and from the
  dictionary itself for older files, and envelopes using RSA PKCS#1 v1.5 or
  OAEP key transport over AES-128/192/256-CBC or 3DES-CBC content encryption
  are all opened.
- Give **each recipient its own permissions** — one reader may print and
  another only read the same file — which a password cannot express. The
  permission word is not quite the standard handler's `/P`: bit 1 is required,
  bit 2 means "may change encryption settings", bits 7 and 8 are unused, and
  bit 13 ("a missing PDF 2.0 MAC is acceptable") is set because no `/AuthCode`
  is written. `Recipient(certificate, permissions=…)` carries the pair and the
  fixed bits are normalised for you.
- Refuse to encrypt to a certificate whose `keyUsage` extension permits neither
  `keyEncipherment` nor `dataEncipherment` (`ignore_key_usage=True` overrides),
  since a reader that enforces the extension would reject the result.
- Change passwords and read permission flags.
- Reject missing, whitespace-only, and wrong passwords for encrypted PDFs.
- Exercise RC4 and AES-CBC primitives, AES-256 setup, and PDF 2.0 V5/R6 key
  derivation helpers.
- Validate PDF signature ByteRange structure, and verify the signature itself
  through `PdfSignature.valid`: the digest algorithm is taken from the CMS (so
  SHA-256/384/512 all verify), the `messageDigest` signed attribute must match
  the ByteRange-covered bytes, and the signature value must verify against the
  signer certificate. It answers integrity and signer authenticity only — chain,
  revocation and timestamps are `PdfSignature.validate(...)`'s job — and returns
  `False` for anything it cannot verify.
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
- **Sign an authored signature field in place**
  (`engine.sign_field.sign_field(pdf_bytes, name, cert, key, …)`): the field
  created by `Form.add_signature_field()` is filled as an *incremental update*,
  so the original bytes — and any signature already in them — stay
  byte-for-byte intact, and the surrounding structure (widgets, other form
  fields, outlines, annotations) is preserved rather than rebuilt. Several
  fields in one document can be signed in turn, each revision leaving the
  earlier signatures valid. Supports `adbe.pkcs7.detached` and PAdES
  (`pades=True`), an embedded chain, a local or network timestamp, and DocMDP
  certification. A `/Lock` on the field is carried
  into the signature as a **FieldMDP** transform. A field that already carries
  a `/V` is rejected rather than overwritten.
- **Whole-document signing goes through the same path**: `SimplePdf.signing_creds`
  authors (or reuses) a signature field, saves the document normally, and then
  fills that field with `sign_field` — so a signed save preserves the COS
  structure like any other save, and everything below applies to it too.
- **The field's `/SV` seed value is enforced.** An entry is advisory until its
  bit in `/Ff` is set; then a signer that cannot honour it refuses rather than
  signs around it. Checked: `/Filter` (the handler must be `Adobe.PPKLite`),
  `/SubFilter`, `/V` (the seed value version), `/Reasons`,
  `/LegalAttestation` and `/AppearanceFilter` (neither of which this signer can
  produce, so a required one is a refusal), and `/AddRevInfo` (revocation
  material inside the signature — build a `/DSS` with `enable_ltv` instead).
  Some entries are *followed* rather than merely checked: `/DigestMethod`
  selects the digest to sign with (SHA-256/384/512; SHA-1 and RIPEMD160 are
  refused rather than silently substituted), `/TimeStamp /URL` names the
  authority to call when the caller supplied none and its `/Ff` bit 1 makes a
  timestamp mandatory, and `/LockDocument /true` turns the signature into a
  certifying one at "no changes permitted". `/MDP` has no `/Ff` bit of its own
  and therefore always binds: it fixes whether the signature certifies and at
  which level.
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

- The bundled JPEG 2000 decoder is pure Python and therefore slow: roughly a
  second per 100k pixels, so a 300 dpi A4 scan takes minutes. Install the
  `images` extra (Pillow/OpenJPEG) for anything larger than a thumbnail; the
  built-in decoder is what makes the default install *work*, not what makes it
  fast. It also declines rather than guesses on the parts of ISO 15444-1 it
  does not implement: packed packet headers (`PPM`/`PPT`), progression order
  changes (`POC`) and regions of interest (`RGN`) each raise. Output is
  normalised to 8 bits per component, so a 12- or 16-bit codestream is scaled
  down rather than returned at its own depth.
- Public-key encryption covers **RSA** recipients. A certificate carrying an
  EC or DSA key cannot transport a wrapped key and is rejected; key-agreement
  recipients (`kari`), password recipients (`pwri`) and `kekri` are not opened,
  and neither is an envelope whose content cipher is RC2 (Acrobat 5), which no
  supported crypto backend implements. All of these fail explicitly.
- No PDF 2.0 message authentication code (`/AuthCode`) is produced or checked
  for either handler. The public-key permission word therefore always sets the
  "tolerate a missing MAC" bit; a reader that requires a MAC would otherwise
  refuse a document this library wrote.
- Re-saving a loaded public-key document preserves its original `/Encrypt`
  dictionary and `/Recipients` (the streams are never re-keyed), so the same
  recipients still open it. Changing the recipient list means calling
  `encrypt_for_recipients` again, which rebuilds the envelopes around a fresh
  seed and a fresh file key.
- A crypt filter that is neither `V2`, `AESV2` nor `AESV3` -- a custom handler's
  own filter -- is not decrypted. `/StmF /Identity` (streams left in the clear)
  is honoured as written, and a document that leaves streams in the clear while
  encrypting only its strings through `/StrF` keeps those strings as stored.
- PAdES baseline levels (B/T/LT/LTA) are produced and validated against trust
  anchors, but this is not a formally certified eIDAS-grade implementation:
  conformance to ETSI EN 319 142 / final certification is deferred to external
  validators (e.g. veraPDF, eIDAS validation services). Online harvesting of
  fresh revocation material into the `/DSS` at build time is opt-in by supplying
  it to `enable_ltv`; the builder itself stays offline.
- Signing goes through `sign_field` on the *saved bytes*, which is what a
  byte-range signature covers. A document that is **encrypted as well as
  signed** is the one exception and stays on the encrypting writer: an appended
  revision would have to encrypt the strings it writes while leaving the
  signature's `/Contents` in the clear. It produces the same signature layout,
  but rebuilds the file rather than preserving its COS structure.
- The `/SV` entries a signer cannot satisfy — `/LegalAttestation`,
  `/AppearanceFilter`, and `/AddRevInfo true` — are refused when required
  rather than approximated. `/SV /Cert` (constraining *which certificate* may
  sign) is not evaluated: the caller supplies the credentials directly, so a
  mismatch is theirs to notice.

## PDF/A And PDF/UA

Supported:

- Run heuristic PDF/A validation and get structured errors/warnings. Beyond
  encryption, metadata/XMP, fonts and output intents, the checker inspects
  many structural ISO 19005 rules observable from the object graph: a trailer
  `/ID`, the header version per part (1.4 for PDF/A-1, 1.7 for PDF/A-2/3,
  exactly 2.0 for PDF/A-4),
  document/page additional actions (`/AA`), optional content (`/OCProperties`,
  PDF/A-1), AcroForm `/NeedAppearances` and dynamic XFA, prohibited
  annotations (Sound/Movie/Screen/3D/RichMedia — 3D and RichMedia are
  permitted in PDF/A-4e, the engineering level that exists for them;
  FileAttachment outside PDF/A-3 and -4), annotation flags (Print required; Hidden/NoView/Invisible
  forbidden) and appearances, prohibited actions
  (Launch/JavaScript/Sound/Movie/ResetForm/ImportData/SetOCGState/Rendition),
  PostScript XObjects, image `/Interpolate`, and PDF/A-1 transparency
  (ExtGState soft masks, blend modes, constant alpha, transfer functions, and
  `/Group /S /Transparency`). It also flags annotation constant opacity
  (`/CA` < 1, PDF/A-1), the `/Crypt` stream filter, the catalog `/Requirements`
  entry, `CIDFontType2` fonts missing `/CIDToGIDMap`, a filtered XMP `/Metadata`
  stream (which must be plaintext), and embedded files lacking
  `/AFRelationship` in the parts that permit them (PDF/A-3 and -4). PDF/A
  level A additionally requires a tagged
  structure tree, which is walked to verify standard structure types (or
  `/RoleMap` mappings), Figure/Formula alternate text, and `/Note` identifiers.
- **PDF/A-4** (ISO 19005-4) is validated and produced. It is defined *on*
  PDF 2.0 rather than capped by it, so the header is required to be exactly
  2.0 and `convert_to_pdfa("4")` raises an older one rather than leaving it.
  Part 4 dropped the accessible/basic/unicode split: there is no PDF/A-4a, the
  base level `"4"` carries no `pdfaid:conformance` at all, and the variants are
  `"4e"` (engineering — 3D and rich media) and `"4f"` (embedded files of any
  type). A `pdfaid:rev` of `2020` is required and written, and a level that
  does not exist is rejected instead of quietly producing PDF/A-1b metadata.
- **PDF/UA-2** (ISO 14289-2) is validated and produced via
  `validate_pdfua(part=2)` and `convert_to_pdfua(part=2)`: PDF 2.0 header, an
  XMP `pdfuaid:rev` of `2024` alongside `pdfuaid:part`, and the PDF 2.0
  standard structure namespace (`http://iso.org/pdf2/ssn`) declared in the
  struct root's `/Namespaces`. Every part-1 catalog requirement still applies.
- Check **device colour against the output intent** (ISO 19005-1 6.2.3.3): a
  device space is permitted only when an OutputIntent's `DestOutputProfile` is
  a profile of that same space, read from the ICC header, so DeviceCMYK content
  does not pass under an sRGB intent. DeviceGray is satisfied by any intent.
  Device colour is detected both by name (`/DeviceCMYK`) and by operator
  (`k`/`K`, `rg`/`RG`, `g`/`G`), which name no colour space at all.
- Run batch PDF/A validation through `PdfAValidateOptions` and `PdfAValidator`.
- Convert loaded COS-backed documents toward PDF/A by adding OutputIntents and
  XMP metadata, setting a title and trailer `/ID` when missing, capping the
  header version, and removing prohibited JavaScript/OpenAction/AA/OCProperties
  entries, AcroForm `/NeedAppearances`/XFA, and offending annotation flags.
  Non-embedded **Standard-14 and Symbol/ZapfDingbats** fonts are embedded with
  the bundled metric-compatible substitute (with synthesized `/Widths` from that
  face) even without a `font_lookup_directory`; other non-embedded fonts still
  need the directory and stay reported. **DeviceCMYK** content color is
  normalized to RGB (the `k`/`K` operators and `/DeviceCMYK` color-space
  fills/strokes), using the same device conversion the renderer applies so
  appearance is unchanged — in page content **and** inside nested form XObjects.
  CMYK **image XObjects** are converted too: `/DeviceCMYK` and **ICC-CMYK**
  (`/ICCBased` with `/N 4`) payloads, raw or `DCTDecode` (Adobe de-inversion and
  YCCK included, decoded through the renderer's own JPEG path), are rewritten as
  8-bit `DeviceRGB` and re-encoded with `FlateDecode`, dropping the now-stale
  `/Decode` and `/DecodeParms`. `/Separation` and `/DeviceN` spaces over a CMYK
  alternate are **repointed to DeviceRGB**: PDF cannot compose their tint
  transform with a CMYK→RGB conversion, so the composition is resampled into a
  Type 0 (sampled) function over DeviceRGB. The space keeps its kind, colorant
  names and component count, so content streams that select it are untouched,
  and a Separation/DeviceN *image* keeps its tint samples — only the space
  changes. For PDF/A-1, a **transparency group** (`/Group /S /Transparency`) on
  a page or form XObject is dropped when nothing it reaches actually uses
  transparency — no ExtGState soft mask, non-Normal blend mode or alpha below 1,
  no image `/SMask`/`/Mask`, no nested group. Producers stamp such groups
  routinely, and removing an inert one cannot change the rendered result.
  Transparency that *is* used stays and is reported.
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
  nesting, and fonts that are not embedded. **MCID coverage** is cross-checked
  per page: a marked-content `/MCID` with no `/ParentTree` structure element, a
  `/ParentTree` slot referencing an MCID no marked content uses, and a page
  `/StructParents` key with no matching `/ParentTree` entry are all reported.
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
  `Document.auto_tag()` (or `convert_to_pdfua(auto_tag=True)`). Text objects
  (`BT` ... `ET`) and image paints (`/Name Do`) are located with their page
  position (tracking the CTM and text matrix), split into left-to-right column
  bands at whitespace gutters (so a multi-column page is read column-by-column,
  not straight across), sorted into reading order within each column
  (top-to-bottom, then left-to-right) and grouped: consecutive body-text lines
  of similar size and spacing collapse into one `/P` paragraph — a single
  structure element spanning several MCIDs via a `/K` array — while larger lines
  become headings ranked into levels by font size (`/H1` for the largest tier,
  then `/H2`, `/H3`) and each image XObject paint becomes a
  `/Figure` with `/Alt` (the `image_alt` argument takes a string, a name→text
  callable, or `None` to skip images). Consecutive paragraphs whose first line
  begins with a list marker (a bullet/dash, or a numbered/lettered/roman
  `1.`/`(a)`/`iv)` prefix) are wrapped into a nested `/L` → `/LI` → `/LBody`
  list (two or more items required; a continuation line without a marker folds
  into its item). Consecutive rows forming a regular aligned grid (two or more
  rows, each with the same number of column-aligned cells) become a nested
  `/Table` → `/TR` → `/TD`. Elements are wrapped in `BDC`/`EMC` by a
  byte-level splice (originals preserved, inline images skipped) and linked by
  MCID through `/StructParents` and the `/ParentTree`. Pages already carrying
  marked content are left untouched.
- Inspect and remediate existing tag trees through `Document.tagged_content`.
  `TaggedContent.root_elements` and `StructureElement.children` expose logical
  reading order; elements can be added with page/MCID bindings, moved between
  parents, reordered, or removed with matching `/ParentTree` cleanup. Structure
  types, `/Alt`, and `/ActualText` are editable and persist across save/load.
  `element_for_mcid(page_number, mcid)` resolves the inverse mapping. PDF/UA
  MCID coverage also resolves named marked-content property lists such as
  `/Tag /P1 BDC` through inherited page `/Resources /Properties` dictionaries.
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

- Real transparency is reported, never flattened. Flattening it correctly means
  compositing each page against its actual backdrop, which is page
  rasterization: it would replace live text and vectors with an image, making
  the text unsearchable and a PDF/A-1a tag tree meaningless. That is a
  conversion of the document, not a repair of it, so it is left to the caller.
  The inert-group removal above is deliberately conservative — every ExtGState
  in a resource dictionary counts, not only the ones the content selects — so a
  group is kept whenever transparency cannot be ruled out.
- Tint-transform resampling is a grid, not an exact composition: a linear
  transform round-trips exactly, while a curved one is reproduced to within a
  step or two per channel. The grid is one axis per colorant, sized to a fixed
  total-sample budget, so a many-colorant `/DeviceN` is sampled more coarsely
  than a `/Separation`.
- Device-colour detection is a scan of resources and content, not a full
  content-stream parse: an operator is matched against its numeric operands and
  a token boundary, and colour set through `sc`/`scn` is attributed to the space
  its `cs`/`CS` names.
- PDF/A and PDF/UA checks are heuristic signals, not certification-grade
  validation. They inspect document structure, not rendered output, glyph
  coverage, colour fidelity, or the semantic correctness of a tag tree. Use a
  dedicated validator such as veraPDF for formal compliance.
- The PDF 2.0 parts are checked at the same depth as the earlier ones, which
  means their *identification* and the structural rules that differ, not the
  whole of PDF 2.0. In particular PDF/A-4 requires an embedded PDF to itself be
  PDF/A (outside `4f`), which is not verified — only that every file
  specification declares its `/AFRelationship`. `convert_to_pdfua(part=2)`
  declares the standard structure namespace but does not re-tag existing
  elements into it: a tag tree built for part 1 keeps its unqualified types.
- The PDF/UA structure-tree checks validate a tag tree that already exists.
  Marked-content (MCID) coverage is cross-checked against the `/ParentTree`
  (advisory warnings), including named property lists that resolve to an MCID
  through the page's inherited `/Resources /Properties`. `auto_tag()` infers a
  real but **coarse** tree: reading order is geometric
  (columns split at whitespace gutters, then top-to-bottom, left-to-right),
  paragraphs are grouped by proximity, headings are inferred from font size
  only (levels `/H1`–`/H3` by size tier), lists are recognised from leading
  markers, and regular aligned grids are tagged as tables. Image `/Figure`
  alternate text is a caller-supplied placeholder (alt text cannot be inferred),
  column detection is a whitespace heuristic that requires the gutter to be
  genuinely clear -- no line's estimated extent may cross it, which is what
  keeps a widely-spaced table from being read column-major -- but a full-width
  banner over the columns may still be mis-assigned, list detection is marker-text based (an unmarked
  or image-bulleted list is not recognised, and nesting is flat), and table
  detection needs a regular grid whose columns are closer than the
  column-split gutter (a wide-gutter table reads as columns, and merged/empty
  cells break the grid). It is a starting point a human refines through
  `Document.tagged_content`, not certified accessibility.
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

The package keeps a few names from the wider Aspose.PDF API so that ported code
still imports. They carry **no implementation**. Constructing one is allowed —
it is an inert value object — but handing it to a real operation raises
`UnsupportedFeatureException` (exported from `aspose_pdf`, and a subclass of
both `AsposePdfException` and `NotImplementedError`) rather than silently doing
nothing or writing a PDF under a foreign extension:

```python
from aspose_pdf import Document, UnsupportedFeatureException
from aspose_pdf.save_format import SaveFormat

with Document("input.pdf") as document:
    try:
        document.save("output.pptx", SaveFormat.PPTX)
    except UnsupportedFeatureException as error:
        print(error)  # PPTX export is not implemented; ...
```

| Surface | Names | Behaviour |
| --- | --- | --- |
| Non-PDF import | `CdrLoadOptions`, `CgmLoadOptions`, `HtmlLoadOptions`, `OfdLoadOptions`, `SvgLoadOptions` (both `aspose_pdf.load_options` and `aspose_pdf.svg`) | Rejected as the `source` or `options` argument of `Document(...)` and `Document.load_from(...)`. |
| Non-PDF export | `SaveFormat.PPTX` | Rejected by `Document.save(destination, save_format)` before anything is written to the path or stream. (`DocFormat.SVG`/`HTML`/`MARKDOWN`, `HtmlSaveOptions` and `MarkdownSaveOptions` are implemented — see [Pages](#pages).) |
| Printing | `Duplex`, `PrintRange`, `PrinterSettings` | No print operation exists; `PrinterSettings` is rejected by `Document.save`. |
| LaTeX | `LatexFragment` | Rejected as a load source; no LaTeX authoring or import path exists. |
| Presentation drawing model | `FillMode`, `IMatrix`, `IPath` in `aspose_pdf.presentation` | Inert value objects. They accumulate path data that nothing consumes and are not connected to page authoring or rendering. |
| Instrumentation | `PerformanceLogger`, `VirtualizationPerformance` in `aspose_pdf.visualization` | Working stopwatch helpers, but nothing in the package feeds them and they do not virtualise or accelerate rendering. (`RasterizedPage`, re-exported from the same module, is the real render result.) |

`Document.save` accepts `None` (the default), `SaveFormat.PDF`,
`DocFormat.PDF`, `DocFormat.SVG`, `DocFormat.HTML`, `DocFormat.MARKDOWN`, and
the `HtmlSaveOptions` / `MarkdownSaveOptions` containers. `aspose_pdf.clustering` is a self-contained hierarchical
clustering utility, not a PDF feature.

- Runtime package code does not use LLM services, API keys, or `.env` secrets.
