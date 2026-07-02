# Bundled Standard-14 substitute fonts

These `*.ttf.zlib` files are zlib-compressed, subset font programs used by the
page renderer to fill glyph outlines for the PDF Standard-14 fonts, which are
never embedded in a PDF.

The Latin faces are **Liberation** subsets. Liberation is metric-compatible
with Arial/Times New Roman/Courier New (and therefore with
Helvetica/Times/Courier), so glyph advances match the Standard-14 metrics.

- `sans-*`  → Liberation Sans  (Helvetica / Arial substitute)
- `serif-*` → Liberation Serif (Times / Times New Roman substitute)
- `mono-*`  → Liberation Mono  (Courier / Courier New substitute)

Symbol and ZapfDingbats have no metric-compatible open source, so they are
substituted with **DejaVu Sans** subsets covering the Adobe Symbol and ITC
ZapfDingbats repertoires (glyph shapes only — advances are DejaVu's, so the
PDF `/Widths`, when present, take precedence):

- `symbol.ttf.zlib`   → DejaVu Sans subset (Symbol substitute)
- `dingbats.ttf.zlib` → DejaVu Sans subset (ZapfDingbats substitute)

The code→Unicode tables for the Symbol/ZapfDingbats built-in encodings live in
`aspose_pdf/engine/symbol_encodings.py`.

## License

The Liberation subsets are licensed under the **SIL Open Font License,
Version 1.1** (see `LICENSE-OFL.txt`). Liberation is a trademark of Red Hat,
Inc.

> Digitized data copyright (c) 2010 Google Corporation with Reserved Font Name
> Arimo, Tinos and Cousine. Copyright (c) 2012 Red Hat, Inc. with Reserved Font
> Name Liberation.

The DejaVu subsets are licensed under the **Bitstream Vera Fonts license**
with DejaVu changes in the public domain (see `LICENSE-DEJAVU.txt`). Bitstream
Vera is a trademark of Bitstream, Inc.

The fonts remain under their respective licenses; the rest of the package is
MIT.

## Regenerating

The compressed subsets are produced by `scripts/build_std_fonts.py` from the
upstream Liberation and DejaVu `.ttf` faces (fontTools is required only for
that script, not at runtime). See that script for the exact Unicode coverage.
