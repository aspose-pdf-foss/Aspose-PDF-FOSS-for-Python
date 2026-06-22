"""High-precision PDF affine transforms.

Composing ``cm`` matrices with binary floats loses precision for large
translations (for example ``2**53 + 1``). Matrix multiplication and
image-placement bounding box math use :class:`decimal.Decimal`; results
coerce to ``float`` only when filling public tuples.
"""

from __future__ import annotations

from decimal import Decimal, localcontext
from typing import Tuple

_PDF_AFFINE_PREC = 80


def pdf_scalar_to_decimal(x: int | float) -> Decimal:
    if isinstance(x, int):
        return Decimal(x)
    return Decimal(str(x))


def multiply_pdf_affine(
    a: Tuple[Decimal, ...], b: Tuple[Decimal, ...]
) -> Tuple[Decimal, ...]:
    """Multiply PDF affines: ``new_ctm = a * b`` (each is a,b,c,d,e,f)."""
    with localcontext() as ctx:
        ctx.prec = _PDF_AFFINE_PREC
        return (
            a[0] * b[0] + a[2] * b[1],
            a[1] * b[0] + a[3] * b[1],
            a[0] * b[2] + a[2] * b[3],
            a[1] * b[2] + a[3] * b[3],
            a[0] * b[4] + a[2] * b[5] + a[4],
            a[1] * b[4] + a[3] * b[5] + a[5],
        )


def identity_affine_decimal() -> Tuple[Decimal, ...]:
    return (
        Decimal(1),
        Decimal(0),
        Decimal(0),
        Decimal(1),
        Decimal(0),
        Decimal(0),
    )


def affine_decimal_to_float(m: Tuple[Decimal, ...]) -> Tuple[float, ...]:
    return tuple(float(x) for x in m)


def image_placement_bbox(
    m: Tuple[Decimal, ...],
    width: int,
    height: int,
) -> Tuple[float, float, float, float]:
    """Return (llx, lly, width, height) from affine (a,b,c,d,e,f) and raster size."""
    a, b, c, d, e, f = m
    w_d = Decimal(width)
    h_d = Decimal(height)
    with localcontext() as ctx:
        ctx.prec = _PDF_AFFINE_PREC
        x0, y0 = e, f
        x1 = a * w_d + e
        y1 = b * w_d + f
        x2 = c * h_d + e
        y2 = d * h_d + f
        x3 = a * w_d + c * h_d + e
        y3 = b * w_d + d * h_d + f
        llx = min(x0, x1, x2, x3)
        lly = min(y0, y1, y2, y3)
        urx = max(x0, x1, x2, x3)
        ury = max(y0, y1, y2, y3)
    return (float(llx), float(lly), float(urx - llx), float(ury - lly))
