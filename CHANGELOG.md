# Changelog

All notable changes to Aspose.PDF FOSS for Python will be documented in this
file.

The project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **An encrypted save keeps the document it was given.** Encryption lived only
  in the legacy writer, which rebuilds a file from an in-memory model, so
  encrypting a loaded document silently discarded its form fields, attachments,
  optional content and marked content — everything the model does not carry.
  The COS writer applies the security handler itself now, enciphering each
  object's strings and stream payload as it serialises them, so encryption is a
  property of the bytes rather than a different way of writing the file. The
  entries that have to stay readable stay readable: the `/Encrypt` dictionary
  (a reader needs it *before* it has a key), the cross-reference stream, a
  signature's `/Contents`, and `/Metadata` under `/EncryptMetadata false`.
  Object streams work under encryption too — the `/ObjStm` is enciphered whole,
  under its own object number, with the strings inside it left alone.
  `optimize()` no longer switches off font subsetting, image recompression and
  content de-duplication on an encrypted document; those were disabled because
  the graph used to hold ciphertext, which it no longer does.

- **Rich text is no longer stuck in Helvetica.** `/RC` and `/RV` markup now
  honours `font-family`, choosing among the Standard 14 text faces — Helvetica,
  Times and Courier, each in regular, bold, italic and bold-italic — and the
  HTML monospace tags (`<tt>`, `<code>`, `<kbd>`, `<samp>`) select Courier. A
  font stack takes the first name it recognises, and a name that signals no
  family leaves the run with the one it inherited rather than resetting it to
  the default. Each family is measured with its own advances, so wrapping and
  alignment are right for Times and for fixed-pitch Courier instead of using
  Helvetica's widths for all three. A form field's `/DA` font seeds the family
  that markup naming none inherits, so a field declared in Times renders its
  styled spans in Times.

- **PDF/A-4 now checks that an attached PDF is PDF/A itself.** ISO 19005-4 6.9
  requires it — an attachment has to declare `pdfaid:part` 1, 2 or 4, with part
  3 deliberately absent because carrying arbitrary files is what PDF/A-3 is
  *for* — and only the `"4f"` level lifts the rule; `"4e"` adds 3D and rich
  media and nothing else. Validation reads the attachment's declaration and
  then checks it against the same rules as any other document, so one that
  merely claims PDF/A does not pass, with each problem named for the file it
  came from. Whether an attachment is a PDF is decided by its bytes rather than
  by the MIME type its producer declared. The structural half descends two
  levels; below that the declaration is taken at its word and a warning says
  so.

- **An encrypted document can be saved incrementally.** `save(incremental=True)`
  refused any encrypted document; it now appends a revision enciphered with the
  file's own key, keeping `/Encrypt` in the new trailer, so the original bytes
  — and any signature over them — stay untouched. Changing the protection is
  still refused, and now says why: adding it, removing it or changing the
  password re-keys every object, and the ones in the preserved prefix cannot
  follow. Building a `/DSS` into an encrypted document is refused for the same
  kind of reason, rather than writing validation material nobody can read.

- **A signed document can be encrypted.** Signing an encrypted document used to
  fall back to the rebuilding writer, losing the same structure. It now takes
  the same path as any other signature — an appended revision over the saved
  bytes — enciphered with the file's own key, with `/Contents` written over it
  in the clear as ISO 32000-1 7.6.2 requires. pyHanko validates the result as
  covering the entire file, with the document's form fields still present.

- **`convert_to_pdfua(part=2)` moves the tag tree into the standard structure
  namespace.** It declared the namespace on the structure root and stopped
  there, so a tree carried over from part 1 kept the unqualified types
  ISO 14289-2 replaced: the declaration says the namespace exists, an element's
  `/NS` says its type comes from it. Every element now names it, an element
  that already names a different namespace keeps it, and `validate_pdfua(2)`
  reports the ones that do not — after the root declaration, so the thing to
  fix first is not buried.

- **A render can say where it spent its time.** Pass a `PerformanceLogger`
  (`aspose_pdf.visualization`) to `Page.render()` and it records seconds per
  phase into a dictionary you own — nothing is timed without one, so a plain
  render pays nothing, and two renders in different threads keep their own
  numbers. This is what that class was for: it was a working stopwatch that
  nothing in the package ever fed. `VirtualizationPerformance`, the
  process-global one beside it, stays a caller-only stopwatch — a library that
  timed itself into module-level state would interleave two documents rendered
  at once.

- **CI cross-validates the library's output against an independent
  implementation.** Every other test asks whether the library agrees with
  itself; a new `cross-validate` job asks whether qpdf agrees — it parses and
  rewrites documents covering layers, tagging, PDF/A-4, PDF/UA-2, optimization
  and signing, and reads their structure back out (including that a signature's
  byte range really covers the whole file). `tests/test_cross_validation.py`
  skips when pikepdf is absent, so a plain checkout stays green. The same job
  writes one conformance sample per level with
  `scripts/write_conformance_samples.py` and runs veraPDF over them, publishing
  its report as an artifact — advisory rather than gating, because the useful
  output is which rules a sample fails.

- **`auto_tag` sees four shapes it used to miss.** A table row may now leave a
  column **blank** (it gets an empty `/TD`, so the cells after it stay in their
  own columns instead of shifting left) and a cell reaching past the next
  column is a **merge** carrying `/ColSpan`, which the HTML export writes as
  `colspan`. A **wide-gutter table** is no longer cut into page columns first:
  a clear gap whose text fills less than a third of the band, with content on
  both sides of every line, is a table's column gap, and splitting it read the
  rows inside out. A list item indented past the one before it opens a
  **sub-list** inside that item's `/LBody` rather than continuing flat. And a
  list whose markers are **drawn rather than typed** — a glyph-sized image
  beside each line — becomes an `/L` whose items carry the image as their
  `/Lbl`, instead of loose paragraphs with pictures between them.

- **PDF/A-4 and PDF/UA-2 — the PDF 2.0 conformance parts — are validated and
  produced.** Both are defined *on* PDF 2.0 rather than capped by it, so the
  header is required to be exactly 2.0 and conversion raises an older one.
  ISO 19005-4 dropped the accessible/basic/unicode split: the levels are `"4"`
  (no conformance letter at all), `"4e"` (engineering — the level that exists
  to permit 3D and rich media) and `"4f"` (embedded files of any type), plus a
  required `pdfaid:rev` of 2020. ISO 14289-2 adds a `pdfuaid:rev` of 2024 and
  the PDF 2.0 standard structure namespace in the struct root's `/Namespaces`;
  reach it with `validate_pdfua(part=2)` and `convert_to_pdfua(part=2)`.

- **A signature field's `/SV` seed value is honoured, not just read.** It is
  the field author's instruction to whoever signs, and only `/SubFilter` and
  `/Reasons` were checked — a field demanding SHA-512, a particular handler, a
  timestamp or a certifying signature got one that quietly ignored the demand.
  Every entry now either binds or is refused, and several are *followed*:
  `/DigestMethod` picks the digest (SHA-1 and RIPEMD160 are refused rather than
  silently downgraded to), `/TimeStamp /URL` supplies the authority when the
  caller named none, and `/LockDocument /true` makes the signature certify.
  `/MDP` binds regardless of `/Ff`, being the one entry with no flag.
  `Form.add_signature_field(seed_value=…)` can author all of them.

- **Whole-document signing and field signing are one path.**
  `SimplePdf.signing_creds` used to synthesise its own field and patch its own
  byte range inside the legacy writer, which meant a signed save rebuilt the
  file from the in-memory model — silently dropping form fields and anything
  else only the COS writer preserves. It now authors a field (or reuses one the
  caller already authored, seed value and all), saves normally, and fills it
  with `sign_field`.

- **Layers resolve for printing and exporting, not only for the screen.** A
  group's `/Usage` dictionary says what it should do for an event and the
  configuration's `/AS` usage application dictionaries are what apply it —
  neither was read, so a watermark marked "do not print" printed anyway.
  `Document.layers.resolve("Print")` now reports the states an event calls for
  and `apply_usage("Print")` adopts them, which makes flattening a print copy
  actually drop the watermark. `Layer.set_usage(printing=False)` writes both
  halves, because a `/Usage` entry no `/AS` entry mentions changes nothing.
  Zoom ranges and BCP 47 language tags are evaluated when a magnification or
  locale is supplied, and left alone when one is not.

- **Alternate optional content configurations are listed and can be applied.**
  `Document.layers.configurations` reports `/D` and every `/Configs` entry with
  the layers it shows and locks, `apply_configuration(name)` adopts a preset as
  the document's state, and `save_configuration(name)` snapshots the current
  states as a new one. Removing a layer now also purges it from the alternates
  and from the usage applications, instead of only from `/D`.

- **The optimizer subsets the last two font kinds it left whole.** *CFF2*
  (`/FontFile3` with `/Subtype /OpenType`, PDF 2.0) is now erased glyph by
  glyph like every other embedded program, as a CIDFontType0 or as a simple
  font resolved through the sfnt's own `cmap`. CFF2 removed `endchar`, so an
  erased glyph there is a zero-length charstring, and its advance width — which
  CFF2 keeps in `hmtx` — is untouched. The `FDArray`/`FDSelect`, each Font
  DICT's `Private` and the `ItemVariationStore` move with it, so a subset
  variable font still instantiates; verified against fontTools at four weights.
  *MacExpertEncoding* is now bundled alongside Standard/WinAnsi/MacRoman, as
  are the predefined **Expert** and **ExpertSubset** CFF charsets, whose glyph
  ordering the specification fixes rather than the font storing it. A
  `/BaseEncoding` outside the four names PDF 32000-1 allows still keeps the
  font whole — that name is malformed rather than unsupported, and guessing
  which was meant could erase a used glyph.

- **An Expert-encoded CFF or Type 1 font draws real glyphs instead of boxes.**
  The same two tables the optimizer needed are what the renderer was missing:
  a font under `/MacExpertEncoding` now resolves its oldstyle figures and small
  caps through its charset, and extracts as text through the Adobe Glyph List.

- **A variable CFF2 font can be drawn at a chosen instance.** The
  ItemVariationStore's regions are read, `fvar` supplies the axes (with `avar`
  applied) and each `blend` resolves to `default + Σ scalar x delta`, verified
  against fontTools' own instancer. Substitute faces use it to reach a style:
  a modern system font ships as one variable file rather than four static
  ones, so asking for Bold now moves the `wght` axis instead of settling for
  the default master.

### Fixed

- **Opening a document and saving it made the file bigger, every time.**
  Outlines and attachments live in the model, not the COS graph, and are
  rebuilt on each save — claiming fresh object numbers as they went, so four
  objects and several hundred bytes of superseded copies accumulated per round
  trip. An incremental save was worse: an object rebuilt under a new number can
  never compare equal to the one it replaces, so the whole tree was appended
  every time. Each rebuild now takes over the numbers its previous copy
  occupied, which makes an unchanged save byte-for-byte identical and an
  unchanged incremental save append nothing whatsoever.

- **Saving a loaded document dropped every attachment's MIME type, description
  and dates.** They were read into the model at load and then ignored by the
  writer, so a round trip silently discarded them — and, incidentally, kept an
  otherwise untouched document from reproducing itself. What the file carried
  is now written back out, with anything the caller set taking precedence.

- **Every appended revision this library wrote had a malformed cross-reference
  section.** Entries are read by offset — a fixed twenty bytes each — and one
  byte too many put every entry after the first in a subsection out of step.
  Only the first still read, which is why a signature (whose appended objects
  are rarely consecutive) looked fine while an incremental save of two
  neighbouring objects did not: qpdf reported the file as damaged and rebuilt
  the table. The unit test measured each line after splitting on newlines, so
  it saw twenty bytes where the file had twenty-one; it now measures the
  section the way a reader indexes into it.

- **Re-saving a document opened with a password destroyed every string in it.**
  Strings are decrypted at load; without the writer putting the cipher back
  they were emitted in the clear under a trailer that still declared
  `/Encrypt`, so every reader dutifully "decrypted" them into noise — a title,
  a field name, a bookmark, an attachment name. qpdf read the title of such a
  file as an empty string. The writer now re-enciphers what it emits, and
  `tests/test_cross_validation.py` checks the result against qpdf.

- **A cross-reference stream with a PNG predictor could not be read at all.**
  `/Predictor 12` is what qpdf, Ghostscript and Acrobat write, so this is the
  shape most real cross-reference streams have. Two things stopped it: the
  decode limit was sized to the entries rather than to the predictor'd rows
  inflate actually produces, so the stream was rejected as oversized, and
  `/DecodeParms` reached the filter layer as a COS dictionary, whose
  `get("Predictor")` misses — the predictor was skipped in silence and the
  entries read one byte out of step. Object streams were unreachable in such a
  file as a result.

- **`Document.decrypt(password)` did not remove the protection.** It unlocked
  the document for reading and left `/Encrypt` in place, so the next save wrote
  an encrypted file — and, before the fix above, a corrupt one. It is the
  counterpart of `encrypt` again: the dictionary goes, `/O` and `/U` with it,
  and the next save is a plain file.

- **`MacRomanEncoding` was the Mac OS Roman character set, not the table PDF
  defines.** All 256 codes carried a name, where Annex D.2 defines 208 and
  leaves the rest to the font's own encoding — and once a name resolves,
  nothing looks at the font again. A font whose encoding put something else at
  `0xB0` had the glyph named `infinity` kept and the glyph it actually shows
  erased. The base table is now PDF's (with `space`, not `nbspace`, at `0xCA`),
  and the 36 extra Mac OS Roman names that are real glyphs are kept as a
  supplement consulted *after* the font — where they still resolve the codes a
  Mac-produced font leaves to convention.

- **`IPath` accepted path segments and dropped them.** The compatibility
  surfaces are meant to be constructible but to raise when used, so a caller
  finds out at the call rather than from a blank page;
  `IPath.append_cubic_bezier_curve` silently did nothing instead. It now raises
  `UnsupportedFeatureException` like every other placeholder. `FillMode` and
  `IMatrix` beside it are genuine value objects and are unchanged.

- **Pages were written without a resource dictionary.** `/Resources` is
  required and inheritable (ISO 32000-1 table 30), and qpdf reported every
  document this library produced as needing repair. It now goes on the page
  tree root, where a page that has none inherits it — putting an empty one on
  each page instead would shadow whatever a caller hung on an ancestor.

- **PDF/A-3 rejected the embedded files it exists to carry.** The check
  compared the requested level against the literal `"3"`, which no real level
  string ever equals, so `"3b"` fell on the prohibited side along with
  PDF/A-1 and -2. Asking for a PDF/A level that does not exist (`"4a"`, a typo)
  no longer falls back to PDF/A-1b either, which used to write metadata
  claiming a level nobody asked for.

- **A signature's `/ByteRange` left the `/Contents` delimiters inside the signed
  ranges.** Excluding only the hex digits still yields a verifiable signature,
  but a validator matches the gap against the `/Contents` string to prove the
  signature covers the document — and could not, reporting the coverage as
  indeterminate instead of "entire file" (confirmed with pyHanko before and
  after). The gap now runs from the `<` through the `>`.

- **A signature dictionary was written as `/Type /Signature`**, which is not a
  PDF object type; ISO 32000-1 table 252 says `/Sig`. `/ContactInfo` was
  written but never read back, so it was always `None` on a loaded signature.

- **A corrupt CFF2 INDEX could stall the optimizer.** A CFF2 INDEX counts its
  items in a uint32 rather than CFF1's uint16, so a malformed font could ask
  for four billion offsets out of a few bytes and be walked entry by entry. The
  count is now rejected when it cannot fit in the data that follows it.

- **A variable CFF2 font drew garbage, not its default master.** The CFF DICT
  parser stopped at CFF1's operator range, so the Top DICT's `vstore`
  (operator 24) was read as an operand and the ItemVariationStore was never
  found. With no region counts the `blend` operator could not tell a delta from
  a coordinate, and the deltas went on to be drawn as part of the outline. Both
  that and the two-byte length prefix in front of the store are fixed.

- **The optimizer recompresses the images it used to refuse.** Anything that
  was not 8-bit device gray/RGB/CMYK under a raster filter kept its original
  bytes however large: an Indexed, Lab, Separation or DeviceN image, a
  1/2/4/16-bit one, a stencil mask, and every CCITT, JBIG2 or JPEG 2000
  payload -- which is most of a scanned document. Each is now brought to
  device samples first: a palette is folded into the samples (the space
  becomes `DeviceRGB`), a non-device space is converted through the same
  colour machinery the renderer uses, sub-byte and 16-bit samples are
  normalised to 8, and an opaque codec is decoded like any other filter. A
  stencil is a shape rather than a picture, so it is downscaled and re-packed
  at one bit per sample instead of being JPEG-encoded. The never-grow guard
  still has the last word, so an image is only rewritten when that actually
  saves bytes.

- **`GraphicsAbsorber` collects every mark a page makes.** It reported painted
  paths and placed XObject images and nothing else: text runs, inline
  (`BI`/`ID`/`EI`) images and `sh` shading fills were simply missing, a stroked
  path's box stopped at the geometry rather than covering the ink, and any
  colour set through a non-device space came back as `None`. All four are now
  covered. A text element carries the box its glyphs occupy, measured with the
  renderer's own font metrics -- so the reported box and the ink agree -- and
  invisible text (rendering mode 3 or 7) is still left out, because it puts
  nothing on the page. Only a pattern, which has no single colour, still
  reports no colour.

### Fixed

- **An `/Indexed` fill colour was read as a grey level.** `Indexed` is not a
  shading colour space, so the shared converter had no branch for it and passed
  the palette *index* through as a colour component: `1 scn` in a two-entry
  palette painted white instead of the colour it names. The renderer and the
  graphics absorber both take the fix.

- **Layers can be created, written to and resolved.** Optional content was
  read-only: the layers a document declared could be listed and switched, but
  not created, not written to and never resolved. `Document.layers.add(name)`
  now creates a group (building the whole `/OCProperties` structure when the
  document had none), `Page.layer(layer)` is a context manager that marks
  everything authored inside it as belonging to that layer, and
  `Document.layers.remove(layer)` drops a group while leaving its content
  unconditionally visible.
- **`Document.flatten_layers()` resolves optional content for good.** Switching
  a layer off changes what is *drawn*; the content stayed in the file and came
  back the moment somebody switched it on again -- a hidden draft watermark is
  still a draft watermark when the file leaves your hands. Flattening deletes
  what the configuration hides (marked content, XObject invocations and
  annotations alike) from the page's *existing* content streams rather than
  leaving them behind as unreferenced objects, drops every surviving `/OC`
  reference and marked-content wrapper, and removes `/OCProperties` -- leaving
  an ordinary PDF that renders byte-identically to what was visible.

- **Documents export as HTML and Markdown.** `DocFormat.HTML` and
  `DocFormat.MARKDOWN` were placeholders that raised. `Document.to_html()`,
  `to_markdown()`, `save_as_html()`, `save_as_markdown()` and
  `Document.save(path, DocFormat.HTML)` now produce real documents, as do the
  `HtmlSaveOptions` and `MarkdownSaveOptions` containers. This is a conversion
  to a *flowing document*, not a facsimile: the same layout analysis
  `auto_tag()` uses infers headings, paragraphs, bulleted and numbered lists,
  tables and figures, and the text is decoded exactly as `extract_text()`
  decodes it -- so the export and the tag tree agree by construction rather
  than by coincidence. Images are embedded through the same reconstruction
  `save_image()` performs, and Markdown is GFM escaped only where a character
  would otherwise change the meaning.

### Fixed

- **A widely spaced table is no longer read as page columns.** The column
  splitter cut at the widest gap between text anchors, and a table's cells
  leave exactly the gap a two-column page does -- so a page with a table came
  out column-major, its prose interleaved with cell contents and the grid never
  reaching table detection. A gutter now has to be *whitespace*: a split is
  rejected when any line's extent runs across it, which the full-width prose
  above and below a table always does. `auto_tag()` gets the same fix.

- **Pages export as SVG.** `DocFormat.SVG` was a placeholder that raised.
  `Page.to_svg()`, `Page.save_as_svg()`, `Document.save_as_svg()` and
  `Document.save(path, DocFormat.SVG)` now write real vectors. The exporter
  subclasses the rasterizer and replaces only its paint sinks, so every
  operator, transform and resource lookup is the same code that renders the
  page -- which is what keeps the two outputs agreeing. Paths carry their fill
  rule, strokes their width, dash pattern, cap and join, clips become
  `<clipPath>`, text becomes glyph outlines, images embedded PNGs placed by
  their matrix, and axial/radial shadings SVG gradients; a mesh or
  function shading is sampled into an image rather than dropped. Verified by
  rendering the output with cairo and comparing against this library's own
  raster.

### Fixed

- **`Q` restores the clipping path.** The renderer intersected one global mask
  and never gave it back, so everything after a `q … W n … Q` stayed clipped to
  a region that had already ended -- which is most documents with a figure in
  them. The clip is graphics state (ISO 32000-1 8.4.4) and is now saved and
  restored with it, at no copying cost: `q` stacks a reference and the clip
  builder makes a new mask instead of editing in place.

- **JPEG 2000 decodes without Pillow.** `/JPXDecode` needed the optional
  `images` extra, and without it the filter raised, the stream decoder fell
  back to handing the *raw codestream* to its caller, and the rasterizer
  painted those compressed bytes as if they were samples -- a page of noise
  where the scan should be. `aspose_pdf.engine.jpeg2000` is a pure-Python
  decoder covering the JP2 container and the bare codestream, tier-2 packet
  decoding (tag trees, all five progression orders, precincts, quality layers,
  tiles, SOP/EPH markers), the EBCOT tier-1 block decoder over the MQ
  arithmetic coder, the 5/3 and 9/7 wavelets, both colour transforms and
  component subsampling. The reversible path is lossless -- it reproduces the
  encoder's input exactly -- and the irreversible path agrees with OpenJPEG to
  within a step or two per channel; both were cross-checked against it over
  tiles, precincts, progression orders, code-block sizes, layers and bit depths.
  Pillow is still preferred when installed, being several hundred times faster
  on a full-size scan, and an image that neither decoder can read is now left
  undrawn rather than painted as noise.

- **Public-key encryption (`/Adobe.PubSec`).** A document encrypted for
  certificate recipients could not be opened at all -- not a degraded read, a
  hard failure -- and there was no way to produce one.
  `Document.encrypt_for_recipients([Recipient(cert), ...])` now writes one and
  `Document(source, certificate=..., private_key=...)` opens one. There is no
  password: a random seed is wrapped in a CMS `EnvelopedData` per recipient and
  the file key is a hash over that seed and every recipient blob. Each
  recipient carries its **own** permissions -- one reader may print and another
  only read the same file -- which no password scheme can express. AES-256
  (`adbe.pkcs7.s5`), AES-128 and RC4-128 (`adbe.pkcs7.s4`) are written; on read,
  `/Recipients` is found in the crypt filter or the dictionary depending on
  `/V`, and PKCS#1 v1.5 or OAEP key transport over AES-CBC or 3DES-CBC content
  encryption is accepted. Encrypting to a certificate whose `keyUsage` forbids
  key transport is refused rather than producing a file the recipient cannot
  open. Verified in both directions against pyHanko and, for the CMS layer,
  OpenSSL.

- **Non-embedded fonts can be drawn with real faces.** The renderer only ever
  had the bundled Latin and symbol substitutes, so a font with no embedded
  program that was not one of the Standard 14 drew glyph boxes -- and a
  composite (Type0/CID) font had no substitute path at all, which is every
  East Asian PDF that leaves the system fonts unembedded. Assigning a
  `FontSubstitutionOptions` (to `Document.font_substitution`, or per call to
  `Page.render` / `Document.render_page`) points the renderer at font
  directories, font programs supplied as bytes, or the platform's own fonts
  (`FontSubstitutionOptions.system()`). A face is resolved by the document's
  `/BaseFont` name against the real `name` tables of the indexed fonts, then --
  for a composite font -- by the well-known families of its character
  collection, so a PDF naming `SimSun` renders on a machine that only has
  `PingFang SC`, and finally by `cmap` coverage of the text itself. CIDs reach
  Unicode through the font's own `/ToUnicode` and Adobe's bundled
  CID-to-Unicode table for the collection. Advances still come from the PDF's
  `/Widths` / `/W`, so a substitute changes which glyphs are drawn, never where
  they sit (a simple font that omits `/Widths` for a code still falls back to
  the face's own advance, as it did for the bundled substitutes). Indexing reads only each face's table directory, `name` table and
  `OS/2` ranges -- about 1200 system faces in under a second -- and pulls a
  whole program (lifting a TrueType Collection face out of its collection) only
  for a face that wins. Discovery is opt-in: without options rendering is
  byte-for-byte what it was, and independent of what the machine has installed.
  `SystemFontSource` also now looks in macOS's `/System/Library/AssetsV2`,
  where downloadable system fonts (PingFang, Hiragino and the other CJK
  families) are installed.

- **Optional content (layers) is honoured.** Nothing in the package looked at
  `/OC`, so a hidden layer — a draft watermark, an alternate language, a CAD
  overlay — was painted and extracted like any other content. Rendering, text
  extraction and `GraphicsAbsorber` now skip content in a group the default
  configuration turns off: marked-content sections (with nested `BDC`/`BMC`
  tracked to the matching `EMC`), image and form XObjects carrying their own
  `/OC`, and annotations. `/OCMD` is resolved under its `/P` policy and simple
  `/VE` expressions, and `/BaseState` is honoured.
- **`Document.layers`.** Lists the document's optional content groups with
  their names and state; setting `layer.visible` rewrites the default
  configuration's `/ON`/`/OFF`, which the renderer, the extractor and a later
  `save()` all follow.
- **Rendered pages encode to compressed TIFF, JPEG, greyscale and bilevel.** A
  raster could only be written as PNG or as an *uncompressed* RGB TIFF — an A4
  page at 300 dpi came to about 25 MB of file for a page of text. `to_tiff()`
  now Deflate-compresses by default (`compression="none"` keeps the raw strip),
  `to_jpeg(quality=…)` uses the bundled encoder, and `mode="gray"`/`"bilevel"`
  (with a `threshold=`) cut a text page down further; `save()` picks the format
  from the suffix, `.jpg`/`.jpeg` included. Both encoders record the render
  resolution — TIFF in its resolution tags, JPEG as the JFIF pixel density.
- **`Document.save_as_tiff()` writes a multi-page TIFF.** Every page becomes one
  image in a single file (`pages=` selects and orders them), rendered and
  encoded one at a time so only the compressed result accumulates.
- **`GraphicsAbsorber` actually absorbs graphics.** `visit()` cleared its
  collection and returned nothing at all, while the documentation described it
  as collecting a page's graphic elements. It now walks the content stream the
  way the rasterizer does — tracking `q`/`Q`/`cm`, descending into form
  XObjects with each `/Matrix` composed, bounded in depth and terminating on a
  form that draws itself — and reports each painted path and placed image as a
  `GraphicElement`: bounding box in page space (curves bounded exactly, not by
  their control points), paint operation, image resource name, device fill and
  stroke colour, and stroke width in page space. Text, inline images and `sh`
  fills are out of scope and documented as such.
- **`Document.encrypt(..., algorithm=…)`.** The engine has always supported
  AES-256, AES-128 and RC4, but the public API could only reach the default.
  Names are normalised (`aes256`, `AES_128`, `rc4-128`), and an unrecognised
  one raises `PdfSecurityException` instead of silently falling back to a
  weaker cipher.

- **The page rasterizer draws annotations.** It rendered page content only, so
  every highlight, stamp, sticky note and form-field widget was missing from a
  rendered page even with its `/AP` present. Each visible annotation's normal
  appearance is now composited, placed by fitting its `/Matrix`-transformed
  `/BBox` to `/Rect` (ISO 32000-1 12.5.5), with `/AS` selecting among appearance
  states and Hidden/NoView annotations and `Popup` subtypes skipped. A malformed
  annotation is skipped rather than allowed to abort the page.
  `Page.render(draw_annotations=False)` (also on `Document.render_page`) keeps
  the old content-only behaviour.

- **Standard icons for `Text` and `FileAttachment` annotations.** Two of the
  most common annotations in a reviewed document could not synthesise an
  appearance at all — `generate_appearances()` declined and left them with no
  `/AP`. Both now draw their standard `/Name` icon as vector artwork (`Comment`,
  `Key`, `Note`, `Help`, `NewParagraph`, `Paragraph`, `Insert`; `PushPin`,
  `Graph`, `Paperclip`, `Tag`), honouring `/C`, squared and centred in the
  annotation rectangle, with an unknown name falling back to the subtype's
  default the way a viewer does.

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

- **`ImagePlacementAbsorber` found nothing when handed a page.** It understood
  only the internal engine object, so `visit(document.pages[0])` — the obvious
  call, and the one the docs describe — returned an empty list. It now accepts a
  `Page` (that page's images), a `Document`, or the engine object, and still
  accepts objects that carry image data directly.
- **Image placement rectangles were scaled by the raster's pixel size.** An
  image is painted into the unit square of its own space (ISO 32000-1 8.9.5.2),
  so a 200×100 image drawn 100pt wide reported a 20000×5000pt rectangle.
  `ImagePlacement.resolution` now reports real DPI — pixels over the size drawn
  on the page — instead of a hardcoded 72.
- **Pages that reuse a resource name lost all but the last image.** Resource
  names are page-local and most producers restart at `/Im0` on every page, but
  images were stored under the bare name, so each collision overwrote the
  previous image's bytes, size and metadata. Images are now keyed uniquely per
  document (a name taken by a different object gets a numbered suffix) while a
  single XObject shared by several pages still stores one copy.
- **Encrypted PDFs written by other tools could not be opened.** Only AES-256
  worked. A 128-bit RC4 document (`/V 2 /R 3`) was misread as AES-128 because
  the cipher was guessed from `/V`/`/R` alone instead of the crypt filter
  `/StmF` selects, and AES-128 (`AESV2`) failed because the file key was used
  directly as the object key: ISO 32000-1 Algorithm 1 derives a per-object key
  from the object and generation number, with a `sAlT` suffix for AES. Both are
  implemented now, and qpdf-produced fixtures for every standard-handler
  flavour — RC4-40, RC4-128, AES-128, AES-256 revision 5 and 6 — are part of
  the test suite. A failure to decrypt raises `PdfSecurityException` rather
  than a bare cipher `ValueError`.
- **Encrypted documents this library wrote were readable only by this library.**
  Three defects compounded: the `/Encrypt` dictionary declared a revision whose
  key derivation was not the one used (`/V 5 /R 5` for keys built with the
  revision 6 algorithm 2.B, `/V 1 /R 2` for a 128-bit RC4 key), `/Perms` and
  `/Length` were missing, and — worst — the trailer `/ID` was regenerated at
  save time while the key had been derived from a different one, so every
  conforming reader computed a different key and rejected the correct password.
  The dictionary is now written to match the keys, the `/ID` is bound to the
  derivation, and `/Perms` follows the revision 6 layout. Output opens in qpdf
  with either password for all three algorithms.
- **Strings in an encrypted document.** They were neither encrypted on write
  nor decrypted on read, so a title or annotation text sat in the clear inside
  an otherwise encrypted file. Strings are now encrypted with their object's
  key (a signature's `/Contents` and the `/Encrypt` dictionary excepted) and
  decrypted as objects are materialised, including for documents produced
  elsewhere.
- **Images and form XObjects vanished from an encrypted document.** Loading with
  a password decrypts the page contents and then cleared the key, but every
  other stream -- images, form XObjects, appearance streams -- is decoded on
  demand from the COS graph and still needed it, so a rendered page of an
  encrypted PDF came out with its images missing. The key the graph needs is
  now kept for as long as those bytes are around, separately from the
  writer-facing one.
- **A document protected by an owner password only would not open.** An empty
  user password is a valid password that every reader tries before asking for
  one; loading demanded a password and raised instead.
- **AES-256 revision 5 documents were rejected.** Password verification always
  used the revision 6 hardened hash; revision 5 — the deprecated Adobe
  extension, still found in the wild — is a single SHA-256 and is now handled
  by revision.

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
