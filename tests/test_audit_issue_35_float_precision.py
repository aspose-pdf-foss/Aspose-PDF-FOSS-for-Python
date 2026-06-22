"""AUDIT #35: geometry / ``cm`` transforms use extended precision.

Binary float composition of PDF affines drops large integer translations
(e.g. ``2**53 + 1``). :mod:`aspose_pdf.engine.pdf_matrix` and
`parse_image_placements_from_content` use :class:`decimal.Decimal` for
``cm`` chaining; placement bboxes use the same precision before coercing
to ``float``.
"""

from __future__ import annotations

from decimal import Decimal

from aspose_pdf.engine.content_stream_parser import parse_image_placements_from_content
from aspose_pdf.engine.pdf_matrix import (
    identity_affine_decimal,
    image_placement_bbox,
    multiply_pdf_affine,
    pdf_scalar_to_decimal,
)


def test_multiply_pdf_affine_accumulates_large_integer_translation() -> None:
    """``translate(1) * translate(2**53)`` must yield e = 2**53 + 1 (not 2**53)."""
    t1 = (
        Decimal(1),
        Decimal(0),
        Decimal(0),
        Decimal(1),
        pdf_scalar_to_decimal(2**53),
        Decimal(0),
    )
    t2 = (
        Decimal(1),
        Decimal(0),
        Decimal(0),
        Decimal(1),
        Decimal(1),
        Decimal(0),
    )
    out = multiply_pdf_affine(t2, t1)
    assert out[4] == Decimal(2**53 + 1)


def test_parse_image_placements_cm_chain_preserves_large_translation() -> None:
    content = b"q 1 0 0 1 9007199254740992 0 cm 1 0 0 1 1 0 cm /ImgZ Do Q"
    placements = parse_image_placements_from_content(content)
    assert len(placements) == 1
    _name, m = placements[0]
    assert m[4] == Decimal(9007199254740993)


def test_image_placement_bbox_uses_decimal_translation() -> None:
    """Corner ``a*w + e`` must use exact ``e`` from Decimal matrix (AUDIT #35)."""
    m = (
        Decimal(1),
        Decimal(0),
        Decimal(0),
        Decimal(1),
        Decimal(10**12 + 42),
        Decimal(0),
    )
    llx, _lly, _w, _h = image_placement_bbox(m, width=100, height=1)
    assert llx == float(10**12 + 42)


def test_parse_image_placements_medium_integers_sum_exactly() -> None:
    """Chained ``cm`` translations with mid-size ints match integer sum."""
    base = 10**10 + 12345
    step = 67890
    content = f"q 1 0 0 1 {base} 0 cm 1 0 0 1 {step} 0 cm /ImA Do Q".encode("ascii")
    placements = parse_image_placements_from_content(content)
    assert placements[0][1][4] == Decimal(base + step)


def test_identity_multiply_is_stable() -> None:
    i = identity_affine_decimal()
    m = (
        Decimal(1),
        pdf_scalar_to_decimal(0.25),
        pdf_scalar_to_decimal(-0.5),
        Decimal(1),
        Decimal(333),
        Decimal(-1000),
    )
    assert multiply_pdf_affine(i, m) == m
    assert multiply_pdf_affine(m, i) == m
