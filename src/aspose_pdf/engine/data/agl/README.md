# Bundled Adobe glyph-name and encoding data

`glyphlist.json.zlib` is a deterministic, zlib-compressed bundle holding:

- the **Adobe Glyph List** — all 4281 names of the official BSD-3-Clause
  `glyphlist.txt` (`adobe-type-tools/agl-aglfn`), as glyph name -> Unicode
  scalar sequence;
- the four PDF predefined base encodings **StandardEncoding**,
  **WinAnsiEncoding**, **MacRomanEncoding**, and **MacExpertEncoding** as
  code -> glyph name;
- the **Mac OS Roman supplement**: the 36 codes MacRomanEncoding leaves
  undefined that the wider Mac OS Roman set gives a real glyph name (the twelve
  ASCII control mnemonics it also names are dropped — a name the Adobe Glyph
  List does not know cannot be a glyph). Consulted only *after* a font's own
  built-in encoding;
- the 391 predefined **CFF standard strings** (SID -> name) used to resolve a
  CFF charset;
- the two predefined **CFF charsets** (Expert, ExpertSubset) as glyph id -> name,
  for fonts that store no charset of their own.

The bundle is generated from fontTools (`agl.LEGACY_AGL2UV`,
`encodings.StandardEncoding`, `encodings.MacRoman`, `cffLib.cffStandardStrings`,
`cffLib.cffIExpertStrings`, `cffLib.cffExpertSubsetStrings`; WinAnsi is derived
from CP1252 and the AGL) and from ReportLab (`pdfbase._fontdata_enc_macroman`
and `_fontdata_enc_macexpert` — the tables PDF defines; fontTools' MacRoman is
the wider Mac OS *character set*, which is where the supplement comes from). Both are build-time dependencies only; the runtime library reads this
blob and never imports either or accesses the network.

Regenerate the file with:

```shell
python scripts/build_agl_data.py
python scripts/build_agl_data.py --check   # verify it is current
```

The generated file's SHA-256 digest is
`e0512727c35de809b8b2df71813d4e69ce318aabf4998b0b23049b1193f12a22`, which the
runtime verifies before decompressing under bounded output limits.

Name resolution follows the Adobe "AGL Specification" algorithm (drop the part
after the first period, split on underscores, then map each component through
the Adobe Glyph List and the algorithmic `uniXXXX` / `uXXXX` forms). See
`LICENSE-ADOBE-AGL.txt` for the notice that applies to this data set, and
`LICENSE-REPORTLAB.txt` for the one covering the MacExpertEncoding table.
