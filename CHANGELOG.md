# Changelog

All notable changes to Aspose.PDF FOSS for Python will be documented in this
file.

The project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Added function-based PDF shadings with multidimensional sampled functions,
  shading matrix/domain/bounds/background handling, and Separation/DeviceN
  alternate-colour conversion.
- Replaced fixed patch-mesh tessellation with bounded device-scale-adaptive
  subdivision and added composite preview for common CMYK and spot overprint
  cases through `/OP`, `/op`, and `/OPM`.
- Corrected project metadata links and added minimal-install CI coverage.
- Added bounded fuzz targets and a redistributable parser corpus, replaced the
  skipped signature-extraction placeholder with an end-to-end test, and applied
  `PdfLoadLimits` to authored PNG and WOFF/WOFF2 decoding paths.
