# Changelog

All notable changes to Aspose.PDF FOSS for Python will be documented in this
file.

The project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **A page with comments could not be read, flattened or edited.** A sticky
  note's `/Popup` is an annotation whose `/Parent` is the note, and a reply's
  `/IRT` is the note it answers. The annotation property channel inlined
  whatever an entry referenced, so reading a note walked note -> popup -> note
  and raised `Annotation property graph contains a cycle`: `page.annotations`,
  `Document.flatten()` and `annotations.delete()` failed on every page carrying
  a comment written by Acrobat or MuPDF. Another annotation, like a page, is
  now never a property value (typed `/Annot`, or untyped with `/Subtype` and
  `/Rect`); editing a note and saving keeps its popup and replies linked, as
  pikepdf and PyMuPDF read the result.

- **Text in a CMap written without line breaks came out as raw codes.** Every
  CMap reader -- ToUnicode, and an embedded Encoding CMap's codespaces,
  `cidrange`, `cidchar` and `WMode` -- went line by line and recognised an
  operator only at the end of a line. A one-line CMap, an entry on the
  `beginbfchar` line or an entry split over two lines gave no mappings, so
  extraction, search and substitute-glyph rendering saw codes like
  `\x01\x02`. They now share one PostScript tokenizer (comments and strings
  skipped, whitespace inside hex strings ignored, an odd final digit padded).
  `bfrange` destinations were also read as one integer, which dropped every
  ligature range (`<00660066>`) and surrogate-pair range (`<D835DC00>`); they
  now count up in the last UTF-16 unit. Thirteen layouts extract exactly as
  pdfium, MuPDF and pdfminer.six do; on malformed CMaps where they disagree the
  majority reading is used. Text from twenty PDFs written by ReportLab, PyMuPDF,
  cairo and matplotlib is unchanged.
- **A form's `/F1` was drawn in the page's `/F1`.** The renderer cached outline
  fonts by resource name, so when a page and a form XObject it draws both name
  a font `/F1` -- most generators name their first font that -- whichever ran
  first drew both; GraphicsAbsorber measured text boxes through a second
  name-keyed cache. Fonts are now cached by font dictionary, as Type 3 fonts
  already were.

- **Lab colour was painted as RGB, and Lab, Separation and DeviceN images as
  their raw components.** The shared colour converter counted Lab's three
  components and read them as red, green and blue, so `60 40 -30 sc` filled
  yellow where pdfium and MuPDF paint mauve, and every Lab shading was wrong.
  The image decoder knew grey, RGB, CMYK and device palettes and nothing else:
  a Separation or DeviceN image's tints were drawn as grey or RGB, and a Lab
  image or a palette over Lab, read as bytes over 255 when L\* spans 0-100,
  came out near black. `optimize(image_compression_quality=...)` baked that
  into the file -- a Lab photo was rewritten as a near-black DeviceRGB JPEG --
  and `save_image` exported it. Lab now converts to sRGB as both references
  render it (relative to the space's own white, unclamped by `/Range` for
  fills; image a\* and b\* as the byte less 128; palette entries over the
  base `/Range`), and one sample conversion serves the renderer, image export
  (eager and streamed) and `optimize`, which agree pixel for pixel. A spot or
  DeviceN image now joins the overprint preview as a fill in its space does.
- **A palette image's `/Decode` rounded to an index.** Both references
  truncate `Dmin + s * (Dmax - Dmin) / (2^bpc - 1)`: `[1 0]` on 8-bit samples
  selects entry 1 for sample 0 and entry 0 for the rest, where every cell came
  out as entry 1. Image export and `optimize` ignored a palette's `/Decode`
  entirely, and `optimize` inverted an image with `/Decode [1 0]` twice once
  its samples had been converted -- a Separation image came out as its
  negative. The array is now applied once, wherever the samples are mapped.

- **Type 3 fonts rendered as black boxes.** The renderer knew TrueType, Type 1
  and CID fonts, and drew a placeholder box for any glyph it could not outline
  -- including every Type 3 glyph, which is not an outline but a content
  stream (ISO 32000-1 9.6.5). matplotlib's PDF backend writes Type 3 fonts by
  default, so every title, axis label and tick number of a matplotlib chart
  came out as a row of boxes; SVG export, a separate path, drew them right.
  Glyph procedures now run under `FontMatrix * text space * Tm * CTM`, with the
  font's `/Resources` (or the page's), advancing by `/Widths` with character
  and word spacing, horizontal scaling and `TJ` kerning applied; a `d1` glyph
  ignores its own colour operators and paints in the text's colour; a glyph may
  show text in another Type 3 font. A real matplotlib chart and ten targeted
  cases (colour, flipped and non-uniform font matrices, rotation, kerning,
  widths, font resources, nesting, invisible text) agree with pdfium and MuPDF.
- **Invisible text did not advance.** `Tr 3` returned before moving the text
  matrix, so `3 Tr (hidden) Tj 0 Tr (seen) Tj` drew the visible word on top of
  the invisible one -- in any font. It now takes up the room it always had.

- **Check boxes written by MuPDF rendered empty.** MuPDF draws a check mark as
  ZapfDingbats `(3)` and declares the font with `/Encoding /WinAnsiEncoding`.
  The renderer overlaid that named Latin encoding on the font's built-in one,
  so code 0x33 became the glyph `three`, which ZapfDingbats does not have, and
  nothing was drawn -- on the live form and in the flattened copy alike. The
  same happened to Symbol under any named base. pdfium and MuPDF keep the
  built-in encoding for these two fonts whatever base is named, and now so do
  we: WinAnsi, Standard and MacRoman render exactly like no `/Encoding`.
  `/Differences` still apply, and are now read in the font's own glyph names
  -- `a20`, the heavy check mark, is no Adobe Glyph List name and used to be
  ignored -- through new code-to-glyph-name tables for both fonts (from their
  Adobe AFM encoding vectors, checked to cover exactly the codes of the
  existing Unicode tables). A difference naming a glyph the font lacks draws
  nothing, as in pdfium.

- **The renderer composed transforms in the wrong order.** ISO 32000-1 writes
  `cm` as `CTM' = M * CTM`, `Td` as `Tlm = [1 0 0 1 tx ty] * Tlm`, a glyph
  advance as `Tm = [1 0 0 1 tx 0] * Tm` and a form as painted under
  `Matrix * CTM`: the newer matrix applies first. Ten sites applied it last.
  Nothing showed while the transforms commuted, which is why no test or sweep
  caught it; when they did not, a `cm` inside a scaled `cm` -- how Microsoft's
  print-to-PDF places every image -- drew off the page, a word set with
  `20 0 0 20 x y Tm /F1 1 Tf` piled its letters on top of each other, `Td` and
  `T*` under a rotated or scaled `Tm` moved lines the wrong way, and any form
  XObject with a scaling or rotating `/Matrix` vanished outright. A new helper
  states the product in the specification's order, and a sweep of thirteen
  non-commuting cases now agrees with pdfium and MuPDF to a pixel (it
  disagreed on all thirteen before). `ImagePlacementAbsorber` had the same
  inversion in its own helper, whose formula contradicted its docstring, and
  reported such an image at its unscaled position. The glyph-box fallback for
  an unresolvable font also drew nothing under a scaled `Tm`: its padding had
  a floor of half a *text-space* unit, the whole box at size 1.
- **One real written as `.8` made a document impossible to open.** ISO 32000-1
  7.3.3 lists `-.002` among its own examples of a real, and MuPDF and
  Ghostscript write them; the COS tokenizer recognised a number only by a
  digit or a sign, so `/MK << /BG [1 1 .8] >>` in a MuPDF-written form failed
  the whole file. And a number with nothing after it -- the last member of an
  object stream, typically a stream's `/Length` -- sent the reference
  lookahead past the end of the input, so the object was lost.
- **Flattening replaced a form's own appearance with ours.** Every text and
  choice field was rebuilt from its value before flattening, so a form from
  another producer was flattened with a different drawing -- font sizes
  changed, `/MK` rotation lost, a selection bar added to list boxes. A widget
  that carries an appearance now keeps it, as 12.5.5 describes, and only
  `/NeedAppearances true` rebuilds them; MuPDF renders its own form, flattened
  here, pixel-identical to the live form.

- **Saving a signed document broke its signatures, even unchanged.** `save()`
  rewrote every document in full, and a rewrite moves every byte a signature
  covers: open a signed PDF, save it without touching it, and pyHanko reports
  the signature as no longer intact -- in a file whose `/SigFlags 3` still
  declared *AppendOnly*, the flag ISO 32000-1 defines for exactly this. The
  default is now to append: `incremental` defaults to `None`, which writes an
  incremental update whenever the file carries signatures (the AppendOnly bit,
  or a signed field), and a full rewrite otherwise. An untouched signed
  document now saves back byte for byte; an edited one gains a revision on top
  of the signed bytes and its signatures stay valid. Verified with pyHanko on
  documents signed by this library and by pyHanko. `incremental=False` still
  forces a full rewrite and now warns that it invalidates the signatures, as
  does a save that must rewrite because the protection is changing.
  **Behaviour change:** a signed document saved with default arguments is
  now appended to rather than rewritten, so an optimisation run on one
  (`optimize()` then `save()`) grows the file unless `incremental=False` is
  passed.

- **Changing a document's passwords lifted every permission restriction, and
  `decrypt` accepted any password.** `change_passwords` re-encrypted with
  `encrypt`'s defaults, so a document that forbade printing and copying came
  out of a routine password change allowing both -- `/P` -4, confirmed with
  qpdf on files from both qpdf and this library -- and moved to AES-256
  whatever cipher it had used. It now keeps the permissions and the cipher and
  changes only the passwords. `Document.decrypt(password)` checked its
  argument behind a flag that is off after a load, so on any loaded document
  `decrypt("anything")` took the protection off; it now accepts only the user
  or the owner password, checked against the file's own `/Encrypt`, and
  refuses the rest. Both methods now agree on which passwords count: either
  of the document's, where `change_passwords` had accepted only the one the
  document was opened with, and an owner password given to `encrypt` in this
  session could not `decrypt` at all.

- **A save that failed destroyed the file it was saving over.** Every PDF save
  to a path wrote with `Path.write_bytes`, which truncates its target before
  writing. Saving a document over the file it came from -- what
  `overwrite=True` is for -- therefore erased the original the moment the
  write began, and a write that then failed left nothing behind. Reproduced on
  a real, nearly full volume: the save raised `ENOSPC` and the only copy of the
  document was a **0-byte file** that no longer opened. Full saves, incremental
  saves and the low-code plugins' file output now go through one writer that
  stages the bytes beside the target, fsyncs them and renames them over it, so
  the same save now fails with the same `ENOSPC` and the original is untouched,
  with no staging file left behind. Symlinks, permission bits and the refusal
  to write a read-only file behave as before.

- **A streamed document converted to PDF/A came out non-conformant, and the
  validator, also streamed, called it valid.** Three more paths read streaming
  mode's deliberately empty content cache as though it were the document.
  `convert_to_pdfa` iterated that cache to rewrite DeviceCMYK operators, so on
  a document opened with `open_streaming` it rewrote nothing -- and left the
  CMYK content under the sRGB OutputIntent it had just installed.
  `validate_pdfa` skipped its device-colour scan of page content the same way,
  so that exact file passed when opened streamed and failed when opened
  eagerly: convert and check in streaming mode, and a non-conformant file is
  reported conformant. And `repair()` padded the empty cache with one blank
  entry per page, after which every page of the document read as empty. All
  three now read each page through `get_page_content`; a streamed conversion
  writes what an eager one writes (identical apart from IDs and timestamps,
  pixel-identical under pdfium).

- **HTML and Markdown export dropped every word inside a form XObject.**
  Headers, footers and stamps usually live in forms, and the export's layout
  analysis reads the page's own content stream, where a form is one `Do` -- so
  those words vanished from `to_html` and `to_markdown` while `extract_text`,
  which walks into forms, kept them: two of our outputs disagreeing on the same
  page. Each text object a form shows now joins the page's own, positioned
  where it lands (the form's `/Matrix`, then the CTM of its `Do`) so it sorts
  into reading order, and decoded with the form's own resources, or the page's
  where it declares none. Text is keyed by element rather than by byte offset,
  since a form's offsets are into its own stream and collided with the page's.
  Reading order matches MuPDF on every case tested: placed by `cm`, by
  `/Matrix`, by both under a scale, nested, a footer drawn first. `replace_text`
  still does not edit inside forms, and now says why: a form is often shared by
  every page that shows it.

- **Outlined text rendered solid.** Table 106 gives four rendering modes that
  stroke a glyph -- `Tr` 1 (stroke), 2 (fill then stroke), and their clipping
  twins 5 and 6 -- and the renderer had all four fall through to the fill. An
  outlined headline or a hollow watermark came out as solid filled text, in the
  *fill* colour, which mode 1 explicitly does not use. The glyph's outline is
  now stroked with the stroke colour and the graphics state's pen; mode 2 fills
  and then strokes; the clipping twins paint exactly as their counterparts.
  Measured against pdfium and MuPDF, which agree with each other to within a
  fraction of a percent on all eight modes, every mode now matches. The SVG
  export already had this right, which is where the two disagreed.
  Contours are closed before stroking: only the TrueType backend returns a
  closed loop, so an embedded CFF or Type 1 glyph would otherwise be drawn with
  one side of every loop missing.

- **A document opened with `open_streaming` extracted no text, and could not
  delete a page.** Streaming mode leaves `page_contents` empty on purpose --
  content is decoded per page on demand -- but three paths read that list as
  though it were the document. `extract_page_text` indexed it directly, so
  every page of every streamed document extracted as `""`, silently, and
  `Document.extract_text` counted it, so the whole document did too; the
  page-text cursor reported no pages at all. `pages.delete` deleted from it
  unconditionally and raised `IndexError: list assignment index out of range`
  for **every index** — while the two parallel lists right below it were
  already guarded for exactly this. Text now comes through `get_page_content`,
  which serves both modes, page counts come from the pages, and deletion
  shifts the page's identity without decoding anything: a streamed document
  stays lazy through both. Deleting from a streamed document now matches the
  eager path page for page across every deletion pattern tested.

- **Metadata could not be removed at all: `doc.info.clear()` cleared nothing.**
  The sync wrote every key it found in `Document.info` and never deleted one,
  so `del`, `pop`, `clear()` and assigning a smaller dictionary all left the
  file exactly as it was — a document stripped of its author and title before
  being shared went out with both, and nothing said so. (`doc.info = {}` did
  not even reach the sync, which returned early on an empty dictionary.) An
  entry that is gone from the dictionary is now removed from `/Info`, on a full
  save and on an incremental one; verified with pikepdf, exiftool and poppler,
  which no longer see the stripped entries. An entry `Document.info` never
  showed — an array, a dictionary, anything with no faithful text — is not
  removed by editing what you could see, and a warning names what stayed
  behind. `/Info` has one writer again: the title `convert_to_pdfa` adds goes
  through `metadata` like everything else, rather than being written straight
  into the COS dictionary where the next save would have taken it for a key the
  caller had deleted.

- **Opening a file and saving it turned its `/Trapped` flag into our own
  `repr`.** Every `/Info` value that was not a string was read with `str()` on
  the parser's object, so `/Trapped /True` arrived in `Document.info` as the
  text `"PdfName(/True)"` and was written back as the *string*
  `(PdfName\(/True\))`. No API call was needed: a plain load-and-save
  destroyed it, and the same happened to a producer's own entries — a boolean
  became `(PdfBoolean(True))`, an array became a string holding a list of
  reprs. A consumer asking whether the document is trapped got a string that
  cannot equal `/True`, which pikepdf confirms. One rendering rule now serves
  both directions: a name reads as its text, a boolean as `true`/`false`, a
  number as its digits, and a value with no faithful text (an array, a
  dictionary) is left out of `Document.info` instead of being flattened into
  it. On save an entry whose text has not changed is left exactly as the file
  had it, whatever its type, and `/Trapped` is written as a **name** — `"true"`,
  `"True"` and `"/True"` all reaching `/True`, an unrecognised value passed
  through as a name, an empty one as the entry's own default `/Unknown`.
  Verified with pikepdf and exiftool, which now read the flag they should.

- **A metadata sync invented dates and wrote non-dates into date fields.** The
  `/Info` ↔ XMP synchronisation read a PDF date with a regex that was anchored
  only at the start and defaulted every missing component: `D:2026` — a legal
  date meaning that year (ISO 32000-1 7.9.4 truncates from the right) — became
  `2026-01-01`, and `2026-09-10`, an ISO date that had found its way into
  `/Info`, became **`2026-01-01`**: a wrong date that looks right. A stray
  digit (`D:202609100`), an hour of 30, trailing prose — all were read as far
  as they parsed. And when conversion failed outright the raw text was written
  into `xmp:CreateDate` regardless, so `"10 September 2026"` ended up in a
  property that holds a date, in the packet PDF/A conformance is judged on.
  Both converters now match the whole string, check that every component is in
  range, and preserve precision in both directions (`D:2026` ↔ `2026`); the one
  thing they pad is a lone hour, because XMP's shortest time is `hh:mm`. What
  is not a date is refused, and the sync **skips** it with a logged warning
  instead of copying it — the neighbouring properties still sync. Verified
  against pikepdf, exiftool and poppler: all three agree with the new readings,
  and pikepdf itself mangles 10- and 12-digit dates (`D:2026091008` →
  `2026-09-01T00:00:08`) where the other two agree with us.

- **`info['CreationDate'] = datetime.now()` failed at save time, three frames
  down, naming nothing.** The document information dictionary maps names to
  **text strings** (ISO 32000-1 14.3.3), dates among them written in the
  `D:YYYYMMDDHHmmSSOHH'mm'` form of 7.9.4 — but nothing checked, so a
  `datetime`, an `int`, a `None` or a non-string key travelled down to the COS
  layer and died there with `PdfString value must be bytes or str` (or
  `PdfName must be a string`) on `save()`, long after the assignment that
  caused it and without naming the entry, the type it got or what it wanted.
  The contract is now checked where `/Info` is written, so it cannot be
  bypassed: the refusal is a `PdfValidationException` naming the entry and the
  offending type, and a value that looks like a date is additionally told how a
  PDF date is spelled — `value.strftime("D:%Y%m%d%H%M%S+00'00'")`, a suggestion
  a test takes out of the message and round-trips, so it stays true. `str`,
  `bytes` and `bytearray` are accepted as before.

- **One circular reference in a page's resources cost the whole page.** A form
  XObject whose own `/Resources` name that same form makes the resource graph
  point back at itself — malformed, and writers do produce it. The converter
  refused the whole graph, so the page lost its fonts, its images and its text;
  and because the renderer converts a form's resources as it draws, `render()`,
  `save_as_image()` and `to_svg()` **raised** rather than returning a page at
  all. The offending edge is dropped now and everything beside it survives,
  which is what pdfminer and MuPDF do; the drop is logged rather than silent. A
  cyclic **page tree** is still refused: there is no document behind that one.

- **Text drawn through a form XObject was not extracted at all.** A form is a
  piece of content a page draws with `Do` (ISO 32000-1 8.10) — headers,
  footers, stamps, and anything a layout tool reuses live in one. The renderer
  walks into a form and so does the graphics absorber, but the text reader
  consumed the `Do` operand and moved on, so a page whose words were all inside
  forms extracted as empty. It walks in now, with the form's own `/Resources`
  governing inside it and the page's standing in where a form declares none (or
  declares an empty set, which older writers use to mean the same). Nested
  forms are followed to a bounded depth, so a form that draws itself stops.
  An **image** XObject is not a content stream and is not read as one, nor are
  its samples inlined into the page's resources to look for words in them.
  `extract_text`, `Document.extract_text` and the text absorbers all see a
  form's text now; pdfminer and MuPDF agree.

- **A subset of pages adopted the whole document's fields and layers.** The
  rule for taking part of a document is written down — what belongs to the
  pages comes, the document's own belongings do not — but two things came
  regardless of whether the pages reached them. Every field of the source
  `/AcroForm` was adopted, so a page split off on its own carried the *other*
  pages' fields: controls listed in the form that no page in the document
  draws, and nothing can fill. Every optional content group was adopted too, so
  the same split carried layers nothing in it names — entries in a viewer's
  layers panel that switch nothing on or off. A subset now takes a field only
  when one of its widgets came, and a layer only when a page reaches it.
  Whole-document merges are unchanged: every page comes, so everything is
  reached, and a layer an author left empty or a field whose widgets were never
  placed is theirs to keep — the same way attachments were already handled.

- **An imported page kept its index into a structure tree it had left
  behind.** A page's `/StructParents` and an annotation's `/StructParent` (ISO
  32000-1 14.7.4.4) are keys into *their own document's* `/ParentTree`.
  Merging does not carry the structure tree across — that is a reasoned
  boundary — but the key came anyway. Merged into a tagged document, the
  imported page arrived holding `/StructParents 0` and so claimed the target's
  first page's headings as its own content, with two pages answering to one
  entry; extracted or concatenated into a fresh document, it pointed at a
  `/StructTreeRoot` that was not there. The page import drops both keys now.
  Checked by reading the structure tree back with pdfium's own reader.

- **`PdfFileEditor.insert` brought a page's drawing and nothing it drew with.**
  It was the last of the model-only page copies, handing the engine a list of
  page rectangles and a list of content bytes: the inserted page arrived with
  no `/Resources`, so every font and image its content named resolved to no
  object, and its annotations, links and form fields were dropped. `Document.
  merge` and `PdfFileEditor.concatenate`/`extract` had already been moved onto
  the real page import; this was the one left behind. It goes through the same
  `SimplePdf.append` now, which grew an *at* so that inserting and appending
  stay one routine rather than two, and an insert at the end is byte-for-byte
  the document a merge produces. The model-only `insert_pages` had no callers
  left and is gone.

- **An incremental update to a file indexed by a cross-reference stream broke
  the chain.** The update always appended a *classic* `xref` table, and ISO
  32000-1 7.5.8.4 lets a classic trailer's `/Prev` name only a classic table —
  so the chain pointed at something no reader can read as one. Everything
  before the update was lost, and only a reader that gave up and rescanned the
  whole file found the pages again. An update now writes the same kind of
  section it chains to, written plain and unencrypted as 7.5.8.2 requires.

- **An object stream answered for objects it no longer held.** Reading a
  revision chain, inflating an object stream cached *every* member, including
  numbers a later revision had re-issued as plain objects, so the stale copy
  outranked the newer one for the life of the document. The rule — the newest
  revision to mention an object owns it — was being applied to the plain xref
  entries and not to these, nor to an object a revision had *freed*.

- **A full save copied `/Prev` out of the source trailer.** A full rewrite has
  no previous revision, so the offset pointed past the end of the file it was
  written into, and reading it back fell into a full-file reconstruction scan.
  Only the keys that name something in the file being written are carried now
  — `/Root`, `/Info`, `/ID`, `/Encrypt` — which is the list its
  cross-reference-stream sibling already used four lines away.

- **Setting a form field's value left it drawing the old one.** An appearance
  stream *is* what a reader draws: ISO 32000-1 12.7.3.3 lets it trust the
  stream and never look at `/V` unless the AcroForm sets `/NeedAppearances`,
  and this library writes `/NeedAppearances false` — a promise that the streams
  are current. Assigning to `Field.value` wrote `/V` and stopped, so a filled
  form showed its **authored** value in every reader that follows the rule
  (a text field's stream still said `(Hello) Tj` after the value became
  `CHANGED VALUE HERE`), while readers that regenerate anyway showed the new
  one. The generator was already here — `generate_appearances()` runs it over
  the whole form — and the setter now runs it over the one field it changed,
  leaving every other field's appearance, including a hand-made one, alone.
  Text, multiline, combo, list box, check box and radio all follow their value
  now; verified with pikepdf and by rendering before and after in MuPDF.

- **The two paths that build a field's inherited attributes had drifted.** The
  bulk appearance generator never seeded `rv`, so a rich-text field reached
  that way could not inherit an ancestor's `/RV` while the same field reached
  while being authored could. There is one definition now.

- **An annotation with no `/AP` was drawn as nothing.** ISO 32000-1 12.5.2
  gives an annotation the properties its appearance is made of — a `Square`'s
  `/IC` and `/C`, a `Line`'s `/L`, a `Highlight`'s `/QuadPoints` — and a reader
  generates the appearance when the file does not carry one, which plenty of
  writers do not. The library could already build them (`generate_appearances()`
  writes them into the document); the *renderer* never asked, so every such
  annotation was missing from a rendered page and from the SVG export. It asks
  now, through a new `SimplePdf.build_annotation_appearance`, and throws the
  stream away rather than editing the document it is drawing. Where pdfium and
  MuPDF agree on what to draw — `Square`, `Circle`, `Ink`, `FreeText` — so do
  we now.

- **A note or attachment icon filled its whole rectangle.** 12.5.6.4 and
  12.5.6.15: a `Text` or `FileAttachment` annotation's icon is a fixed size and
  the rectangle does not scale it, so a 120-point box got a 120-point sticky
  note where both readers draw a small one centred in the space. It is drawn at
  twenty points now, which is where readers settle.

- **A blend mode over bare page blended against paper that is not there.** ISO
  32000-1 11.4.7: a page's contents are a transparency group, and that group is
  *isolated* — it begins with nothing behind it, and the paper is joined once,
  at the end. The canvas was the paper instead, opaque from the first pixel, so
  `Screen` over bare page came out white where it should leave the colour
  alone, and every other non-Normal mode was wrong in its own way wherever it
  painted over nothing. The compositing formula already had the term for it
  (11.3.8, `(1 - αb) · Cs`); it was simply never reached, because the page
  canvas carried no alpha to be zero. It does now, and the finished page meets
  the background in one pass at the end. All sixteen blend modes match pdfium
  and MuPDF, over bare page and over paint alike.

  The pass costs almost nothing: a pixel nothing reached still holds the
  background it was filled with and a fully covered one needs no help, so only
  partial coverage — `ca`/`CA` below 1, or a soft mask — is mixed, and a page
  with none of it is finished by a single scan in C (0.6 ms on a 1.6-megapixel
  supersampled canvas). Coverage is stored as the floor of the alpha rather
  than rounded to nearest, so its complement is exactly the backdrop's share;
  measured over thirteen `ca` values that agrees with pdfium on all thirteen,
  where rounding agreed on nine.

- **A pattern only painted path fills.** ISO 32000-1 8.7.3: a pattern is
  selected with `scn`/`SCN` in a Pattern colour space, and from then on it *is*
  the fill or the stroke colour. `SCN` was ignored outright, so a stroke drawn
  with a pattern came out in whatever colour happened to be set — black on a
  fresh page — and glyphs were filled straight from `fill_color`, so text
  filled with a pattern (how a gradient headline is drawn) came out solid. Both
  now paint the pattern, tiling and shading alike: a glyph hands over its
  outlines the way a path hands over its subpaths, while a stroke — which has
  no such path, only the area the pen covered — has its coverage collected as a
  mask and the clip narrowed to it, after which the same tiler runs for all
  three. Setting a plain colour, or a colour space, puts the pattern down again
  (8.6.8), on the stroking side as well as the filling one. Verified against
  pdfium and MuPDF.

- **`BT` cleared the whole text state, which is graphics state.** ISO 32000-1
  9.4.1 gives `BT` two jobs — the text matrix and the text *line* matrix — and
  9.3.1 puts everything else a text object uses in the **graphics** state: the
  font and its size, `Tc`, `Tw`, `Tz`, `TL`, `Tr`, `Ts`. Those outlive a text
  object and are saved and restored by `q`/`Q`. Both readers reset all of it at
  `BT`, so a page that selects its font once and then opens a text object per
  line — which is how a great many generators write one — rendered every line
  after the first at the 12pt fallback in the wrong face, and its text was
  *extracted* with no font at all, silently dropping every character the
  encoding was needed for (`café` came back as `caf`). A `TL` set in an earlier
  object was lost the same way, so `T*` moved nowhere and ran two lines
  together.

- **Text rendering modes 4 to 7 did not clip.** Table 106: they add what they
  show to the clipping path, which is applied once, at `ET`. The renderer
  stored `Tr` and never looked at it past mode 3, so a text-shaped window — the
  usual way to put a picture inside letters — clipped nothing and the picture
  covered the page. Mode 7 now collects outlines without painting, the
  accumulated glyphs intersect the clip at `ET`, and the result is graphics
  state like any other clip, so `Q` restores it. A glyph that contributes no
  outline (a space) still opens the window, and so leaves it empty; a showing
  operator given an empty string builds no clipping path at all. Verified
  against both pdfium and MuPDF.

- **No filled path ever had a hole in it.** ISO 32000-1 8.5.3.3 settles which
  points a path encloses by one of two rules, and the operator picks one — `f`
  against `f*`, `B` against `B*`, `W` against `W*`. The rule belongs to the
  *path*, not to any one subpath, because that is the point of it: a shape with
  a hole is two subpaths and whether the hole is open depends on counting the
  crossings of both. The renderer filled each subpath on its own, which is
  neither rule, and dropped the star entirely — so a donut, a logo counter, a
  letter drawn as vectors and a region clipped out with `W*` all came out
  solid. Glyph outlines had their own correct nonzero filler, which is why type
  looked right while artwork did not; that filler, the path fill, the clip
  rasterizer and the shading fill are now one scanline pass that takes the rule
  as an argument. The SVG export names the rule it uses (`fill-rule`,
  `clip-rule`) instead of relying on the default. Verified against pdfium, and
  the SVG against a browser engine.

- **`/Mask` was ignored, so a masked image painted an opaque box.** ISO 32000-1
  8.9.6 gives an image three ways to be transparent and it may use one:
  `/SMask`, which was implemented, and `/Mask` in either of its two forms,
  which was not. `/Mask` may name a **stencil**, whose set samples are the ones
  *not* to paint — sampled over the image's unit square at whatever resolution
  it has, with the sense stated by the stencil's own `/Decode` — or hold an
  **array**, which is colour-key masking: `2 × n` bounds on the *raw* sample
  values, dropping a pixel only when every one of its components falls inside
  its own range. Both now produce the same per-pixel alpha map that `/SMask`
  does, so the renderer and the SVG export needed nothing new to honour them,
  and `/SMask` wins where a file writes both. A `/Mask` stream that does not
  declare `/ImageMask true` is left alone, as table 89 requires it. Colour-key
  masking is not applied to a `DCTDecode` or `JPXDecode` image, whose samples
  are still a codestream at that point — 8.9.6.4 advises against keying a
  lossily coded image anyway. Verified against pdfium.

- **A stencil mask painted black instead of the colour that was set.** An image
  with `/ImageMask true` is not a picture (ISO 32000-1 8.9.6.2): its one-bit
  samples say where the *current fill colour* goes and where the page shows
  through. The renderer decoded it as a one-bit grey image, so every stencil
  came out black on white whatever colour was in force — and a stencil is how
  scanned text, logos and Type 3 glyph bitmaps are drawn. It now paints the
  fill colour, carrying that colour's own overprint behaviour with it, and
  leaves masked-out samples untouched. The SVG export emits the same thing as
  an RGBA image rather than a grey one.

- **`/Decode` was collected and never read.** The array that says which
  interval of its colour space a sample spans (8.9.5.2) reached the renderer's
  image metadata and stopped there, so an inverted image rendered
  un-inverted — including the `[1 0 1 0 1 0 1 0]` that Adobe CMYK JPEGs carry.
  The image exporter honoured it for a single grey component and ignored it for
  RGB, CMYK and Indexed. One implementation now serves the renderer, the SVG
  export and the exporter: per component, at any bit depth, applied before the
  colour conversion, and in *index* space for `/Indexed`, whose default range is
  `[0, 2**bpc - 1]` rather than `[0 1]` because its samples are palette entries.
  Verified against pdfium.

- **An inline image's samples were read as content-stream tokens.** Between
  `ID` and `EI` lie the image's bytes, and bytes spell whatever they happen to
  spell — an operator nobody wrote, a name, an opening `(` that closes nowhere.
  Lexed as tokens they were noise, and because a literal string runs to its
  closing paren the noise usually swallowed the rest of the page: text drawn
  after an inline image went missing from `extract_text()` and from the text
  absorbers, and its marks went missing from the renderer, the SVG export and
  the graphics absorber. The rule was implemented in exactly one place — the
  layout reader behind `to_html()`/`to_markdown()` — and the tokenizer the
  other four readers share did not know it. Both now go through one module,
  which also applies the rule ISO 32000-1 8.9.7 gives for finding the end of
  the data: unfiltered samples occupy exactly
  `ceil(Width × BitsPerComponent × components / 8) × Height` bytes, so the
  common case needs no searching for a free-standing `EI` — which samples can
  imitate, and do. A declared length is still checked against the `EI` that
  should follow it, and only a filtered image falls back to the search.

### Added

- **Inline images are painted.** `BI … ID … EI` reached the renderer as
  nothing at all; the abbreviated dictionary is now expanded to the image
  XObject it is a short spelling of (`/W` → `/Width`, `/Fl` → `/FlateDecode`,
  `/RGB` → `/DeviceRGB` and the rest of table 93) and painted by the same code
  as `Do`, including a `/ColorSpace` that names an entry in the page's
  resources. Verified against pdfium across raw, Flate, ASCIIHex, ASCII85 and
  RunLength data, 1/4/8 bits per component, grey, RGB, CMYK and Indexed
  colour.

- **Words ran together in text extracted from kerned `TJ` arrays.** The
  displacements between a `TJ` array's strings are kerning inside a word or the
  space between words, and telling them apart needs to know how wide the glyphs
  are. A Standard 14 font is normally written *without* a `/Widths` array, its
  metrics being the reader's to know, and the extractor then assumed 1000 units
  for every glyph — about four times a typical lowercase letter — putting the
  word-gap threshold four times too high. Ordinary word spacing was read as
  kerning, so `Hello world` came out as `Helloworld`. The bundled
  metric-compatible substitutes already answer this for the appearance
  builders, which measure text with them to wrap and centre it; the extractor
  asks them too now, and matches pdfminer across Helvetica, Times, Courier and
  their bold and italic cuts.

- **Extracted text had no lines, and the absorbers found no text at all.** The
  content-stream reader answered every text-positioning operator with a space —
  `Td`, `TD`, `Tm` and `T*` alike — though those four both start a new line and
  shift along the one in progress. The line structure was discarded at exactly
  the point where it was known, so a page of prose came out as one very long
  line. The separator is now chosen where the text is *shown*, from the baseline
  it is shown at. Alongside it, `'` (show on the next line) was declared as
  taking three operands where ISO 32000-1 table 109 gives it one, so the
  operator was skipped for want of operands and every line it drew was lost.

- **`TextFragmentAbsorber` and `TextAbsorber` collected nothing from a real
  document.** They look for an `extract_text()` method, and neither `Page` nor
  `Document` had one — a hundred tests passed on them because every one fed a
  stub with a `.text` attribute, and not one used a `Document`. Both now have
  the accessor, sharing the single per-page routine that the whole-document
  extractor and the page-at-a-time cursor also use instead of each repeating it.

- **`add_text` drew the wrong glyphs for anything but ASCII.** The string in a
  text-showing operator is a sequence of *codes*, and a simple font gives each
  code a glyph through its encoding. `add_text` wrote the text's UTF-8 into one
  and declared no encoding at all, so the font fell back to its built-in
  StandardEncoding and drew whatever glyphs those bytes named: `café` came out
  with two wrong letters where the accent was, `100€` with three, Cyrillic as a
  row of Latin nonsense. Nothing failed and nothing warned, and reading the page
  back through this library hid it because the extractor made the same
  assumption — pdfminer and Acrobat did not. The font now declares
  `WinAnsiEncoding` and the text is encoded into it, by inverting the very table
  the reader resolves codes through, so what is written and what is read back
  agree by construction. A character that encoding has no code for is refused,
  naming it and pointing at `font=`, rather than drawn as a different one.
  `Symbol` and `ZapfDingbats` keep their built-in encoding, which declaring a
  base encoding would have replaced.

- **`validate()` called every conforming document invalid, and broken ones
  valid.** The structural check walked the object graph and rejected it if any
  node was reachable from itself. A PDF object graph is not a tree: a page's
  `/Parent`, an annotation's `/P`, an outline's `/Prev` all point back, and
  `/Parent` is *required* on every page (ISO 32000-1 7.7.3.2) — so `validate()`
  and `check()` returned `False` for every document this library wrote, and
  deleting the required entry made them return `True`. They returned `True` for
  genuinely broken files too, because what is actually wrong there is still a
  perfectly traversable graph: a page missing from its parent's `/Kids` is never
  reached, and a catalog whose `/Pages` names nothing simply ends the walk. The
  walk now only has to terminate, and what makes a page tree a page tree is
  checked directly — `/Count`, `/Parent` membership, no node reached twice, a
  `/MediaBox` on every page.

- **`repair()` left a salvaged document claiming a page it did not write.** The
  page sync refused to add anything when the object graph had no pages yet, on
  the grounds that there might be no page tree to add to. A document repaired
  from a truncated file has one — an empty one — and pages in the model that
  belong in it, so it saved with `/Count 0` while reporting one page. What
  decides is now whether there is a tree, and `repair()` runs the sync after
  making sure there is. Found by the fixed `validate()` on its first outing.

- **The file-based editor workflows went through a second merge that lost the
  same things `Document.merge` used to.** `PdfFileEditor.concatenate` and
  `.extract`, and the low-code merger and splitter behind them, called a
  different implementation: it built a fresh model from page rectangles and
  content bytes, pooled the sources' images into one renamed namespace, and
  rewrote the content streams to match. A concatenated page had no `/Resources`
  of its own, every page in the result carried every image in it, and
  annotations were dropped. Both are the same operation as appending a
  document's pages, and are now the same code. Extraction adds one rule to it: a
  subset of pages is a different document, so what belongs to the pages comes
  and the document's embedded files do not, and a bookmark comes only if the page
  it points at did — remapped to where that page landed, which also makes
  extracting pages in a new order work. The content-stream renaming module the
  old merge needed is deleted; nothing renames anything now.

- **Every saved file was missing the comment that marks it binary.** A PDF that
  holds binary data puts a comment of at least four bytes above 127 immediately
  after `%PDF-x.y` (ISO 32000-1 7.5.2), so that a transfer in text mode or an
  editor weighing line-ending translation copies the bytes as they stand.
  ISO 19005-1 6.1.2 requires it, and this library wrote none — so every file it
  produced failed PDF/A-1 on its second line, and its own conformance check
  never looked, reporting a freshly converted PDF/A-1b document as having no
  issues at all. All three writers emit it now, and the checker reports its
  absence as a warning: it is the one requirement here that is not a property of
  the object graph, since a full save supplies one and an incremental save,
  which keeps the original bytes as its prefix, does not.

### Added

- **The PDF/A checker enforces the implementation limits.** ISO 32000-1 annex
  C.1, which ISO 19005-1 6.1.13 adopts: a name of more than 127 bytes, a string
  of more than 65535, an integer past ±2,147,483,647. A conforming reader is not
  obliged to handle any of them, so a file that needs it to is not archivable —
  and all three passed the checker in silence.

- **A number too small or too large was written in a notation PDF does not
  have.** A real is decimal digits with a period, and exponential notation is
  not permitted (ISO 32000-1 7.3.3); Python writes small and large floats as
  `1e-05` and `1.5e+20`, and the COS writer handed those straight to the file,
  where `e` starts a keyword rather than continuing a number. Setting a crop box
  a little too small was enough to produce a document this library could not
  reload. Two neighbouring faults went with it: an infinity or a NaN was written
  as the word Python names it by, and a whole number past +/-2,147,483,647 was
  written without a period — an integer token outside the range annex C.1
  guarantees, which qpdf resolves to null, silently deleting the annotation that
  held it. Content streams had the rule right in their own function all along;
  both paths share it now.

- **A name containing a space or a delimiter produced a file nothing could
  read.** A PDF name escapes any byte that is not a regular character as `#`
  followed by two hex digits (ISO 32000-1 7.3.5); this library wrote the bytes
  as they were. A space, a bracket or a parenthesis *ends* a name, so a document
  with one anywhere came out unparseable — by other tools and by this library
  itself, which raised `Unexpected token` on reloading what it had just written.
  Reading was the mirror: `#20` was taken for three characters, so a name
  another producer escaped came back wrong. Both are reachable from the public
  surface wherever a caller names something — an annotation property's key, or
  a value marked with `annotations.Name`. One place had noticed and fixed it for
  itself, with a private pair of functions that escaped an attachment's media
  type; those are gone, because a name now escapes whatever it holds.

- **Non-Latin text was written in an encoding no other reader could decode.** A
  PDF text string is PDFDocEncoded or UTF-16BE behind a `FEFF` byte order mark
  (ISO 32000-1 7.9.2.2). This library wrote raw UTF-8, which is neither, so a
  conforming reader took those bytes for PDFDocEncoding: every non-Latin title,
  bookmark, annotation, field value and attachment name it produced showed as
  mojibake in Acrobat and in every other tool. Reading its own files back hid it
  completely, because the reader made the same assumption. Field names were the
  exception — they went through the one encoder that was right — and that
  mismatch is what made it visible from inside: the field-value setter matched
  names with a hand-rolled UTF-8 decode, so `form["Ф"].value = "V"` found no
  field, wrote nothing, reported success, and the value was gone after a save.
  There is now one encoder and one decoder, used everywhere a text string is
  written or read; the decoder also accepts UTF-16LE and marked UTF-8, which
  other producers write. Checked against pikepdf in both directions.

- **Merging a document brought across a tracing of its pages, not the pages.**
  `Document.merge` copied each page's rectangle and its content bytes and
  nothing else. A page without its `/Resources` names fonts and images that
  resolve to no object, so it drew blank — or, worse, drew with whatever the
  *target* had registered under the same name, which for two documents built by
  this library is always `/F1`: merged text rendered in the wrong typeface with
  nothing to show that anything was wrong. Everything else was dropped in
  silence — annotations, form fields, bookmarks, attachments, layers. A page is
  now imported: its dictionary is copied into the target's graph with every
  object it reaches, once each, references remapped, and attributes inherited
  from an ancestor of the source's page tree resolved onto it. The structures
  those pages belong to come with them, since a widget whose field is not in
  `/AcroForm /Fields` is a control the form does not know about, and an
  optional content group missing from `/OCProperties` is not a layer any viewer
  offers to switch. A field or attachment name used by both documents is given
  to the newcomer under a numbered variant rather than replacing what is there.
  The source document is untouched and shares no object with the result, and
  merging no longer takes the other document's title.

- **Opening a document and saving it rewrote every bookmark.** The outline model
  read a bookmark back as one number — the index of the page its `/Dest` named —
  and wrote it out again as "fit that page". A `/XYZ` view lost its position and
  zoom; a bookmark carrying an *action* lost the action altogether and became a
  jump to page 1. Nothing warned, and no structure check could notice, because
  "fit page 1" is a perfectly well-formed destination. An index is also not an
  identity: deleting or inserting a single page silently repointed every
  bookmark past it, while link annotations — which keep the page *reference* —
  were never affected. A bookmark loaded from a file now carries the target the
  file held, verbatim, and that is what gets written back; naming a target
  through `page_index` or `destination` replaces it. `OutlineItem.destination`
  also reads the target back typed, so a bookmark's action or view can be
  inspected and not only written.

- **A literal string lost its parentheses and backslashes.** Inside `( ... )` a
  backslash introduces an escape (ISO 32000-1 7.3.4.2); the reader consumed the
  backslash *and* the character after it and kept neither. Since `\(`, `\)` and
  `\\` are exactly what the writer escapes, any title, bookmark label,
  annotation, field value or script containing a parenthesis or a backslash came
  back short of them — a document could be opened and saved with its own text
  quietly edited. The other escape forms had never been read at all: `\ddd` is
  an octal byte, a backslash before an end-of-line joins the lines, and an
  end-of-line without one is a single line feed however the file writes it. The
  value is now assembled as bytes, so a high byte is the file's byte rather than
  the UTF-8 of the character it resembles in Latin-1. Checked against pikepdf
  over every escape form.

- **An internal link can be read back.** `Page.annotations` raised
  `Annotation property graph contains a cycle` for any link pointing at a page
  of the same document — the commonest annotation there is — and because the
  failure happened while building the collection, no annotation on that page
  could be read at all. The property channel turns an annotation entry into
  plain Python and resolves indirect references as it goes, so a `/Dest` pulled
  in a copy of the page it named; that page lists the annotation holding the
  destination, and the walk came back to where it started. A `/Dest`, or the
  `/D` of a `/GoTo` action, now reads back as the `aspose_pdf.interactive`
  destination it was written from, carrying the page index, and writing that
  property back reproduces the page reference. A remote (`GoToR`) destination
  keeps naming its page by number, since it belongs to another file.

- **A link or bookmark written after inserting a page pointed at the wrong
  one.** Both places that turn a page index into a page reference read
  `_page_obj_ids`, a list kept by hand alongside the edits, which carries a `0`
  placeholder for an inserted page until the next save: every page after the
  insert was off by one, and a destination naming the inserted page itself was
  written as `0 0 R` — the head of the free list (ISO 32000-1 7.5.4), never a
  real object, so the link led nowhere. Both now resolve the page by walking
  the page tree, which is right at every moment, and share one helper.

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

- **`Document.update_attachment` changes one embedded file without replacing
  it.** `add_attachment` supersedes an entry outright, so re-adding a name to
  change its description silently dropped the MIME type, dates and
  relationship the file already carried — and there was no other way to edit a
  single field. An argument left out is now left alone, `new_name=` renames
  (carrying the metadata across, and refusing to replace a different
  attachment), and the resulting `FileSpecification` comes back.

- **A form field's rich text is drawn in the font the field declares.** The
  markup was laid out in a Standard-14 face whatever the `/DA` named, so a form
  using an embedded brand font rendered visibly wrong type. Runs asking for
  exactly that face now use it by reference to the form's own `/DR` resource,
  measured with its `/Widths`, so wrapping follows the real advances too; a run
  wanting bold, italic or another family still falls back to the Standard 14 of
  the matching family, since an arbitrary embedded face has no bold sibling. A
  Type0/CID field font is deliberately left out: its appearance is written in
  CID codes, and drawing PDFDocEncoded literals with it would mis-encode every
  word.

- **Existing content can be put on a layer.** A layer could be created and
  content authored onto it inside a `Page.layer` block, but a watermark, a
  stamp or a reviewer's comments already in the document had no way onto one at
  all. `Layer.add(content)` tags an `Annotation` or an `ImagePlacement`,
  `Layer.remove` takes the tag off — only its own, so removing one layer never
  reveals content belonging to another — and `Layer.contains` reports
  membership, counting content that reaches the group through an `/OCMD`. The
  renderer already honoured `/OC` on annotations and XObjects, so switching the
  layer off stops drawing them immediately.

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

- **Inserting an existing page produced a blank one.** `pages.insert(index,
  page)` is documented as taking an existing page, but carried over only the
  media box and the content bytes: the new page named fonts and images that
  were not in *its* resources, so every reference dangled, nothing was drawn,
  and no structural error showed for it — annotations were dropped too. A copy
  now keeps the page's resources, rotation and boxes, takes its own content
  stream so the two can be edited apart, and gets fresh copies of the
  annotations (widgets excepted: one is a form field's presence on a page, and
  duplicating it would put a single field in two places). A page from another
  document is refused rather than half-copied — `Document.merge` is what brings
  pages across.

- **A rich-text field whose widget was a separate object never showed its
  markup.** `/RV` is a field-level entry, like `/V`, so a field with its widget
  under `/Kids` — which is what this library's own `Form.add_text_field`
  produces — keeps it on the field. It was read off the widget alone, so every
  such field rendered its plain `/V` and the markup was never seen. It is now
  inherited down the field tree beside `/V`.

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
