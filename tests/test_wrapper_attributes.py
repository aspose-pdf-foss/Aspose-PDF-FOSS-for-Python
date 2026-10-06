"""A name these objects do not have is refused, not quietly kept.

``Page`` is rebuilt on every access -- ``pages[0] is pages[0]`` is ``False`` --
so an attribute set on a misspelling landed on a throwaway object and was gone
before the next statement. ``d.pages[0].rotate = 270`` is the case that found
this: the real property is ``rotation``, nothing complained, and the document
saved unrotated. ``Field`` and ``Form`` are cached rather than rebuilt, so a
typo there survived on the object and was still lost on save.

``layers.Layer``, ``layers.LayerConfiguration``, ``layers.LayerCollection`` and
``pages._LayerSection`` already declared ``__slots__``; the rest of the wrappers
now do too, which is all this takes -- Python then raises ``AttributeError`` for
a name that is not declared.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document
from aspose_pdf.annotations import Annotation, AnnotationCollection, LinkAnnotation
from aspose_pdf.forms import Field, Form
from aspose_pdf.outlines import OutlineCollection, OutlineItem
from aspose_pdf.pages import Page, PageCollection

SLOTTED = [
    Page, PageCollection, Annotation, LinkAnnotation, AnnotationCollection,
    Field, Form, OutlineItem, OutlineCollection,
]


@pytest.fixture
def document() -> Document:
    doc = Document()
    doc.pages.add()
    doc.pages[0].add_text("X", 50, 700)
    return doc


@pytest.mark.parametrize("cls", SLOTTED, ids=lambda c: c.__name__)
def test_no_wrapper_carries_an_instance_dict(cls):
    # A subclass that forgets its own __slots__ hands one back again, which is
    # why LinkAnnotation is in this list.
    assert "__dict__" not in dir(cls), f"{cls.__name__} still accepts any attribute"


# ``size`` used to be in this list and is not any more: a page now has a real
# ``size`` property, which assigns a PageSize or a (width, height) pair and
# refuses anything else with a validation error rather than an AttributeError.
@pytest.mark.parametrize(
    "name", ["rotate", "mediabox", "cropbox", "width", "height", "text"]
)
def test_a_name_a_page_does_not_have_is_refused(document, name):
    with pytest.raises(AttributeError):
        setattr(document.pages[0], name, 90)


def test_the_case_that_found_this(document):
    # `rotate` for `rotation`: it used to be taken and lost, and the page stayed
    # upright with nothing said.
    with pytest.raises(AttributeError, match="rotate"):
        document.pages[0].rotate = 270
    assert document.pages[0].rotation == 0


def test_the_real_properties_still_work_and_persist(document):
    document.pages[0].rotation = 90
    document.pages[0].crop_box = (0, 0, 300, 400)
    buffer = io.BytesIO()
    document.save(buffer)

    reloaded = Document(io.BytesIO(buffer.getvalue()))
    assert reloaded.pages[0].rotation == 90
    assert tuple(reloaded.pages[0].crop_box) == (0.0, 0.0, 300.0, 400.0)


def test_an_outline_items_own_attributes_are_writable_and_typos_are_not():
    item = OutlineItem("Title", 0)
    item.title = "Renamed"
    item.is_bold = True
    item.is_italic = True
    item.children.append(OutlineItem("Child", 0))
    item.page_index = 1          # a property
    assert (item.title, item.is_bold, item.is_italic) == ("Renamed", True, True)
    assert len(item.children) == 1 and item.page_index == 1

    for typo in ("bold", "italic", "titel", "page"):
        with pytest.raises(AttributeError):
            setattr(item, typo, True)


def test_a_field_refuses_a_name_it_does_not_have(document):
    document.form.add_text_field("name", 0, (10, 10, 100, 30), value="v")
    field = document.form["name"]
    field.value = "set through the property"
    assert document.form["name"].value == "set through the property"

    for typo in ("valu", "readonly", "fieldtype"):
        with pytest.raises(AttributeError):
            setattr(field, typo, "x")


def test_an_annotation_refuses_a_name_it_does_not_have(document):
    annotation = document.pages[0].annotations.add("Text", (10, 10, 30, 30), "note")
    annotation.title = "Author"          # a property, and it persists
    assert document.pages[0].annotations[0].title == "Author"

    for typo in ("titl", "subtyp", "rectangle"):
        with pytest.raises(AttributeError):
            setattr(annotation, typo, "x")


def test_a_lazily_built_annotation_collection_still_caches(document):
    # Page.annotations fills a slot on first use; an unset slot reads as absent,
    # so the `hasattr` guard behind it keeps working.
    first = document.pages[0].annotations
    assert document.pages[0].annotations is not first  # a new Page each time
    page = document.pages[0]
    assert page.annotations is page.annotations        # cached on one Page


def test_the_dict_helper_still_describes_a_slotted_object():
    # aspose_pdf.utils reads __dict__; a slotted object has none, so it reads
    # the declared names instead of falling back to a repr string.
    from aspose_pdf.utils import _object_to_dict, are_objects_json_equal

    described = _object_to_dict(OutlineItem("T", 2, is_bold=True))
    assert described == {
        "title": "T", "is_bold": True, "is_italic": False, "children": [],
    }
    are_objects_json_equal(OutlineItem("T", 2), OutlineItem("T", 2))
