# Bundled Adobe glyph-name and encoding data

`glyphlist.json.zlib` is a deterministic, zlib-compressed bundle holding:

- the **Adobe Glyph List** — all 4281 names of the official BSD-3-Clause
  `glyphlist.txt` (`adobe-type-tools/agl-aglfn`), as glyph name -> Unicode
  scalar sequence;
- the three PDF predefined base encodings **StandardEncoding**,
  **WinAnsiEncoding**, and **MacRomanEncoding** as code -> glyph name;
- the 391 predefined **CFF standard strings** (SID -> name) used to resolve a
  CFF charset.

The bundle is generated from fontTools (`agl.LEGACY_AGL2UV`,
`encodings.StandardEncoding`, `encodings.MacRoman`, `cffLib.cffStandardStrings`;
WinAnsi is derived from CP1252 and the AGL). fontTools is a build-time
dependency only; the runtime library reads this blob and never imports fontTools
or accesses the network.

Regenerate the file with:

```shell
python scripts/build_agl_data.py
python scripts/build_agl_data.py --check   # verify it is current
```

The generated file's SHA-256 digest is
`334601d3644f58e36dd8ab5bf5ff289bdd5fd92b8f0017d35659573c958b7309`, which the
runtime verifies before decompressing under bounded output limits.

Name resolution follows the Adobe "AGL Specification" algorithm (drop the part
after the first period, split on underscores, then map each component through
the Adobe Glyph List and the algorithmic `uniXXXX` / `uXXXX` forms). See
`LICENSE-ADOBE-AGL.txt` for the notice that applies to this data set.
