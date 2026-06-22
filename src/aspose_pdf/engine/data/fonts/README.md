# Bundled Standard-14 substitute fonts

These `*.ttf.zlib` files are zlib-compressed, Latin-subset **Liberation** font
programs used by the page renderer to fill glyph outlines for the PDF
Standard-14 fonts (Helvetica/Times/Courier families), which are never embedded
in a PDF. Liberation is metric-compatible with Arial/Times New Roman/Courier
New (and therefore with Helvetica/Times/Courier), so glyph advances match the
Standard-14 metrics.

- `sans-*`  → Liberation Sans  (Helvetica / Arial substitute)
- `serif-*` → Liberation Serif (Times / Times New Roman substitute)
- `mono-*`  → Liberation Mono  (Courier / Courier New substitute)

Symbol and ZapfDingbats are not substituted (no metric-compatible OFL source);
the renderer draws glyph boxes for them.

## License

The font programs are licensed under the **SIL Open Font License, Version 1.1**
(see `LICENSE-OFL.txt`). They remain under the OFL; the rest of the package is
MIT. Liberation is a trademark of Red Hat, Inc.

> Digitized data copyright (c) 2010 Google Corporation with Reserved Font Name
> Arimo, Tinos and Cousine. Copyright (c) 2012 Red Hat, Inc. with Reserved Font
> Name Liberation.

## Regenerating

The compressed subsets are produced by `scripts/build_std_fonts.py` from the
upstream Liberation `.ttf` faces (fontTools is required only for that script,
not at runtime). See that script for the exact Unicode coverage.
