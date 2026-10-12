"""The subtypes that get a synthesised appearance, and the places that say so.

``appearance.SUPPORTED_SUBTYPES`` grew from ten to fifteen -- ``FreeText``,
``Stamp``, ``Caret``, ``Text`` and ``FileAttachment`` were added -- and the
docstring a caller reads kept naming ten. A capability nobody documents is one
nobody uses, and a docstring that lists ten of fifteen is worse than one that
lists none: it reads as a complete answer.

So this holds the three lists to each other -- the builders, the docstring, and
``supported-features.md`` -- and, because a list agreeing with itself proves
nothing, checks that each named subtype really does produce an appearance
through the public API.
"""

from __future__ import annotations

import io
import pathlib
import re

import pytest

from aspose_pdf import Document, PageSize
from aspose_pdf.annotations import Annotation, AnnotationCollection
from aspose_pdf.engine.appearance import _BUILDERS, SUPPORTED_SUBTYPES
from aspose_pdf.engine.cos import PdfName

#: Geometry each subtype needs before it can be drawn at all.
GEOMETRY = {
    "Line": {"L": [60, 600, 260, 680]},
    "Polygon": {"Vertices": [60, 600, 260, 600, 160, 700]},
    "PolyLine": {"Vertices": [60, 600, 260, 600, 160, 700]},
    "Ink": {"InkList": [[60, 600, 120, 660, 200, 610]]},
    "Highlight": {"QuadPoints": [60, 700, 260, 700, 60, 680, 260, 680]},
    "Underline": {"QuadPoints": [60, 700, 260, 700, 60, 680, 260, 680]},
    "StrikeOut": {"QuadPoints": [60, 700, 260, 700, 60, 680, 260, 680]},
    "Squiggly": {"QuadPoints": [60, 700, 260, 700, 60, 680, 260, 680]},
}

_FEATURES = pathlib.Path(__file__).resolve().parent.parent / "supported-features.md"


def _quoted(text: str) -> set[str]:
    """Every ``name`` in *text* that is one of the known subtypes.

    Matching against the known set rather than against every quoted word keeps
    this from tripping over the ``/Name``, ``/L`` and colour operators the same
    prose mentions.
    """
    found = set(re.findall(r"``([A-Za-z]+)``", text))
    found |= set(re.findall(r"`([A-Za-z]+)`", text))
    return found & set(SUPPORTED_SUBTYPES)


def _appearance_bytes(document: Document) -> int:
    engine = document._engine_pdf
    annots = engine._resolve(engine._get_page_dict(0).get(PdfName("Annots")))
    annotation = engine._resolve(annots.items[0])
    appearance = engine._resolve(annotation.mapping.get(PdfName("AP")))
    if appearance is None:
        return 0
    normal = engine._resolve(appearance.mapping.get(PdfName("N")))
    return len(bytes(normal.content)) if normal is not None else 0


# ---------------------------------------------------------------------------
# The lists agree
# ---------------------------------------------------------------------------


def test_every_supported_subtype_has_a_builder():
    assert set(SUPPORTED_SUBTYPES) == set(_BUILDERS)


def test_the_docstring_names_every_subtype_that_has_a_builder():
    """The drift this test exists for: ten named, fifteen built."""
    named = _quoted(Annotation.generate_appearance.__doc__ or "")
    assert named == set(SUPPORTED_SUBTYPES), (
        "generate_appearance's docstring and SUPPORTED_SUBTYPES disagree: "
        f"undocumented {sorted(set(SUPPORTED_SUBTYPES) - named)}, "
        f"claimed but not built {sorted(named - set(SUPPORTED_SUBTYPES))}"
    )


def test_the_collection_points_at_the_one_list_rather_than_repeating_it():
    """Two copies of a list are two things to forget to update."""
    doc = AnnotationCollection.generate_appearances.__doc__ or ""
    assert "generate_appearance" in doc
    assert not _quoted(doc), "it should refer to the list, not restate it"


def test_supported_features_names_every_subtype_too():
    text = _FEATURES.read_text(encoding="utf-8")
    start = text.index("Auto-generate normal appearance streams")
    named = _quoted(text[start : start + 2000])
    assert set(SUPPORTED_SUBTYPES) <= named, (
        "supported-features.md is missing "
        f"{sorted(set(SUPPORTED_SUBTYPES) - named)}"
    )


# ---------------------------------------------------------------------------
# And each one is true
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("subtype", sorted(SUPPORTED_SUBTYPES))
def test_each_documented_subtype_really_gets_an_appearance(subtype):
    document = Document()
    page = document.pages.add(PageSize.A4)
    annotation = page.annotations.add(
        subtype,
        (60, 600, 260, 700),
        "contents here",
        title="Author",
        properties=GEOMETRY.get(subtype),
    )
    assert annotation.generate_appearance() is True

    buffer = io.BytesIO()
    document.save(buffer)
    document.close()
    with Document(buffer.getvalue()) as saved:
        assert _appearance_bytes(saved) > 0, "an /AP /N that draws nothing"


def test_a_subtype_with_no_builder_is_refused_rather_than_faked():
    document = Document()
    page = document.pages.add(PageSize.A4)
    annotation = page.annotations.add("Popup", (60, 600, 260, 700), "x")
    assert annotation.generate_appearance() is False
    document.close()


def test_a_rectangle_enclosing_nothing_gets_no_appearance():
    document = Document()
    page = document.pages.add(PageSize.A4)
    annotation = page.annotations.add("Square", (60, 600, 60, 600), "x")
    assert annotation.generate_appearance() is False
    document.close()
