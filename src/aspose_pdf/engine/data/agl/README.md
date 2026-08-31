# Bundled Adobe glyph-name and encoding data

`glyphlist.json.zlib` is a deterministic, zlib-compressed bundle holding:

- the **Adobe Glyph List** — all 4281 names of the official BSD-3-Clause
  `glyphlist.txt` (`adobe-type-tools/agl-aglfn`), as glyph name -> Unicode
  scalar sequence;
- the four PDF predefined base encodings **StandardEncoding**,
  **WinAnsiEncoding**, **MacRomanEncoding**, and **MacExpertEncoding** as
  code -> glyph name;
- the 391 predefined **CFF standard strings** (SID -> name) used to resolve a
  CFF charset;
- the two predefined **CFF charsets** (Expert, ExpertSubset) as glyph id -> name,
  for fonts that store no charset of their own.

The bundle is generated from fontTools (`agl.LEGACY_AGL2UV`,
`encodings.StandardEncoding`, `encodings.MacRoman`, `cffLib.cffStandardStrings`,
`cffLib.cffIExpertStrings`, `cffLib.cffExpertSubsetStrings`; WinAnsi is derived
from CP1252 and the AGL) and from ReportLab
(`pdfbase._fontdata_enc_macexpert.MacExpertEncoding`, which fontTools does not
carry). Both are build-time dependencies only; the runtime library reads this
blob and never imports either or accesses the network.

Regenerate the file with:

```shell
python scripts/build_agl_data.py
python scripts/build_agl_data.py --check   # verify it is current
```

The generated file's SHA-256 digest is
`f7a0eb4e232f60ad2f56931e679c92b382ee1087e2693af9d0c4522125b8993c`, which the
runtime verifies before decompressing under bounded output limits.

Name resolution follows the Adobe "AGL Specification" algorithm (drop the part
after the first period, split on underscores, then map each component through
the Adobe Glyph List and the algorithmic `uniXXXX` / `uXXXX` forms). See
`LICENSE-ADOBE-AGL.txt` for the notice that applies to this data set, and
`LICENSE-REPORTLAB.txt` for the one covering the MacExpertEncoding table.
