"""Configure a viewer's field calculation sequence without executing scripts."""

from __future__ import annotations

import io
from itertools import repeat

import pytest

from aspose_pdf import Document, JavaScriptAction, PdfLoadLimits
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfIndirectReference,
    PdfName,
    PdfNull,
    PdfNumber,
    PdfStream,
)
from aspose_pdf.engine.form_fields import field_dictionary
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfParseException,
    PdfResourceLimitException,
    PdfValidationException,
)


def _bytes(document, **options):
    with io.BytesIO() as output:
        document.save(output, **options)
        return output.getvalue()


def _acro(document):
    pdf = document._engine_pdf
    root = pdf._resolve(pdf._cos_doc.trailer.mapping[PdfName("Root")])
    return pdf._resolve(root.mapping.get(PdfName("AcroForm")))


@pytest.fixture
def document():
    with Document() as doc:
        doc.pages.add().add_text("Invoice", 20, 700)
        doc.pages.add()
        for index, name in enumerate(
            ("invoice.subtotal", "invoice.tax", "invoice.total")
        ):
            doc.form.add_text_field(
                name, 1, (20, 600 - index * 40, 200, 625 - index * 40), value="0"
            )
        doc.form["invoice.tax"].actions["calculate"] = JavaScriptAction(
            "event.value = 10;"
        )
        doc.form["invoice.total"].actions["calculate"] = JavaScriptAction(
            "event.value = 110;"
        )
        yield doc


def test_absent_and_cleared_order_do_not_create_a_form():
    with Document() as document:
        form = document.form
        assert form.calculation_order == []
        form.calculation_order = None
        form.calculation_order = []
        assert document._engine_pdf._cos_doc is None
        document.pages.add()
        before = _bytes(document)
        assert form.calculation_order == []
        form.calculation_order = []
        assert _bytes(document) == before


@pytest.mark.parametrize("on_disk", [False, True])
def test_order_round_trips_as_field_references_without_changing_values(
    document, tmp_path, on_disk
):
    form = document.form
    expected = ["invoice.tax", "invoice.total", "invoice.subtotal"]
    values = form.to_dict()
    appearances = [a.appearance_normal for a in document.pages[1].annotations]
    form.calculation_order = (name for name in expected)
    assert form.calculation_order == expected
    assert all(
        isinstance(ref, PdfIndirectReference)
        for ref in _acro(document)[PdfName("CO")].items
    )
    assert form.to_dict() == values
    assert [a.appearance_normal for a in document.pages[1].annotations] == appearances
    assert form["invoice.total"].actions["calculate"].script == "event.value = 110;"
    detached = form.calculation_order
    detached.reverse()
    assert form.calculation_order == expected
    if on_disk:
        source = tmp_path / "calculation-order.pdf"
        document.save(source)
    else:
        source = _bytes(document)
    with Document(source) as reopened:
        assert reopened.form.calculation_order == expected
        assert reopened.form.to_dict() == values
        reopened.form.calculation_order = expected[::-1]
        with Document(_bytes(reopened)) as final:
            assert final.form.calculation_order == expected[::-1]


@pytest.mark.parametrize("empty", [None, []])
def test_clearing_preserves_fields_actions_and_other_form_settings(document, empty):
    document.form.calculation_order = ["invoice.tax", "invoice.total"]
    acro = _acro(document)
    other = {key: value for key, value in acro.mapping.items() if key != PdfName("CO")}
    document.form.calculation_order = empty
    assert document.form.calculation_order == []
    assert PdfName("CO") not in acro.mapping
    assert acro.mapping == other
    assert (
        document.form["invoice.tax"].actions["calculate"].script == "event.value = 10;"
    )


def test_repetitions_are_preserved_and_order_can_precede_calculation_scripts(document):
    expected = ["invoice.subtotal", "invoice.total", "invoice.subtotal"]
    document.form.calculation_order = expected
    assert document.form.calculation_order == expected
    assert document.form["invoice.subtotal"].actions.get("calculate") is None
    document.form["invoice.subtotal"].actions["calculate"] = JavaScriptAction(
        "event.value = 100;"
    )
    with Document(_bytes(document)) as reopened:
        assert reopened.form.calculation_order == expected


@pytest.mark.parametrize(
    "selection,error",
    [
        ("invoice.total", TypeError),
        (b"total", TypeError),
        ({"invoice.total": 1}, TypeError),
        (1, TypeError),
        ([1], TypeError),
        ([""], PdfValidationException),
        (["\ud800"], PdfValidationException),
        (["invoice.total", "missing"], KeyError),
        (["invoice"], KeyError),
    ],
)
def test_invalid_selections_fail_without_mutation(document, selection, error):
    document.form.calculation_order = ["invoice.total"]
    before = _bytes(document)
    with pytest.raises(error):
        document.form.calculation_order = selection
    assert _bytes(document) == before


def test_late_iteration_failure_does_not_replace_existing_order(document):
    document.form.calculation_order = ["invoice.tax"]
    before = _bytes(document)

    def names():
        yield "invoice.total"
        raise ValueError("Cannot read next field")

    with pytest.raises(ValueError, match="Cannot read next field"):
        document.form.calculation_order = names()
    assert _bytes(document) == before


def test_removing_a_field_prunes_all_its_occurrences_and_preserves_other_fields(
    document,
):
    form = document.form
    form.calculation_order = [
        "invoice.total",
        "invoice.tax",
        "invoice.total",
        "invoice.subtotal",
    ]
    form["invoice.total"].remove()
    assert form.calculation_order == ["invoice.tax", "invoice.subtotal"]
    with Document(_bytes(document)) as reopened:
        assert reopened.form.calculation_order == ["invoice.tax", "invoice.subtotal"]


def test_selective_flatten_retains_shared_field_until_its_last_widget_is_baked(
    document,
):
    form = document.form
    form.add_text_field(
        "shared",
        widgets=[(1, (250, 600, 400, 625)), (2, (20, 600, 200, 625))],
        value="Shared",
    )
    form.calculation_order = ["invoice.tax", "shared", "invoice.total"]
    document.flatten(fields=["invoice.tax", "shared"], annotations=False, pages=[1])
    assert form.calculation_order == ["shared", "invoice.total"]
    document.flatten(fields=["shared"], annotations=False, pages=[2])
    assert form.calculation_order == ["invoice.total"]
    with Document(_bytes(document)) as reopened:
        assert reopened.form.calculation_order == ["invoice.total"]


@pytest.mark.parametrize(
    "value",
    [
        PdfNumber(1),
        PdfDictionary({}),
        PdfStream(b"invalid"),
        PdfArray([PdfNull()]),
        PdfArray([PdfDictionary({})]),
    ],
)
def test_malformed_order_is_reported_and_can_be_cleared(document, value):
    _acro(document).mapping[PdfName("CO")] = value
    with pytest.raises(PdfParseException):
        _ = document.form.calculation_order
    document.form.calculation_order = None
    assert document.form.calculation_order == []


def test_orphan_and_cyclic_order_references_are_rejected(document):
    pdf = document._engine_pdf
    outside = pdf._cos_doc.register_object(PdfDictionary({}))
    _acro(document).mapping[PdfName("CO")] = PdfArray([outside])
    with pytest.raises(PdfParseException, match="outside the form"):
        _ = document.form.calculation_order
    reference = PdfIndirectReference(999, 0)
    _acro(document).mapping[PdfName("CO")] = PdfArray([reference])
    with pytest.raises(PdfParseException):
        _ = document.form.calculation_order
    pdf._cos_doc.objects[999] = reference
    with pytest.raises(PdfParseException, match="cycle"):
        _ = document.form.calculation_order
    document.form.calculation_order = ["invoice.total"]
    assert document.form.calculation_order == ["invoice.total"]


def test_unicode_field_names_round_trip(document):
    document.form.add_text_field("итог.税", 2, (20, 600, 200, 625))
    document.form.calculation_order = ["invoice.total", "итог.税"]
    with Document(_bytes(document)) as reopened:
        assert reopened.form.calculation_order == ["invoice.total", "итог.税"]


def test_field_reference_generations_are_preserved(document):
    pdf = document._engine_pdf
    field = field_dictionary(pdf, "invoice.tax")
    parent = pdf._resolve(field.mapping[PdfName("Parent")])
    reference = next(
        ref for ref in parent[PdfName("Kids")].items if pdf._resolve(ref) is field
    )
    reference.gen_number = 7
    pdf._cos_doc.generations[reference.object_number] = 7
    document.form.calculation_order = ["invoice.tax"]
    assert _acro(document)[PdfName("CO")].items[0].gen_number == 7
    with Document(_bytes(document)) as reopened:
        assert reopened.form.calculation_order == ["invoice.tax"]
        assert _acro(reopened)[PdfName("CO")].items[0].gen_number == 7


def test_shared_array_is_replaced_and_reading_does_not_append_a_revision(document):
    form = document.form
    form.calculation_order = ["invoice.tax"]
    array = _acro(document).mapping[PdfName("CO")]
    _acro(document).mapping[PdfName("CustomOrder")] = array
    form.calculation_order = ["invoice.total"]
    assert _acro(document).mapping[PdfName("CustomOrder")] is array
    assert document._engine_pdf._resolve(array.items[0]) is field_dictionary(
        document._engine_pdf, "invoice.tax"
    )
    original = _bytes(document)
    with Document(original) as reopened:
        assert reopened.form.calculation_order == ["invoice.total"]
        assert _bytes(reopened, incremental=True) == original


def test_inline_field_cannot_be_copied_into_calculation_order(document):
    pdf = document._engine_pdf
    field = field_dictionary(pdf, "invoice.total")
    parent = pdf._resolve(field.mapping[PdfName("Parent")])
    kids = pdf._resolve(parent.mapping[PdfName("Kids")])
    kids.items[-1] = field
    with pytest.raises(PdfValidationException, match="indirect"):
        document.form.calculation_order = ["invoice.total"]
    assert PdfName("CO") not in _acro(document).mapping


def test_repeated_field_tree_nodes_are_rejected(document):
    pdf = document._engine_pdf
    field = field_dictionary(pdf, "invoice.total")
    parent = pdf._resolve(field.mapping[PdfName("Parent")])
    kids = pdf._resolve(parent.mapping[PdfName("Kids")])
    kids.items.append(kids.items[-1])
    with pytest.raises(PdfParseException, match=r"cycle|repeated"):
        document.form.calculation_order = ["invoice.total"]


def test_selection_and_metadata_limits_fail_before_mutation(document):
    form = document.form
    form.calculation_order = ["invoice.total"]
    previous = _acro(document).mapping[PdfName("CO")]
    document._load_limits = PdfLoadLimits(max_container_items=2)
    with pytest.raises(PdfResourceLimitException, match="max_container_items"):
        form.calculation_order = repeat("invoice.total")
    assert _acro(document).mapping[PdfName("CO")] is previous
    document._load_limits = PdfLoadLimits(max_object_bytes=3)
    with pytest.raises(PdfResourceLimitException, match="max_object_bytes"):
        form.calculation_order = ["invoice.total"]
    assert _acro(document).mapping[PdfName("CO")] is previous


@pytest.mark.parametrize("order", [None, ["invoice.total", "invoice.tax"]])
@pytest.mark.parametrize("algorithm", ["AES-128", "AES-256"])
def test_encrypted_streaming_changes_remain_lazy_and_save_incrementally(
    document, tmp_path, monkeypatch, order, algorithm
):
    document.form.calculation_order = ["invoice.tax", "invoice.total"]
    document.encrypt("user", "owner", algorithm=algorithm)
    path = tmp_path / "template.pdf"
    document.save(path)
    original = path.read_bytes()
    with Document.open_streaming(path, password="owner") as reopened:
        pdf = reopened._engine_pdf
        form = reopened.form
        with monkeypatch.context() as patch:
            patch.setattr(
                pdf, "get_page_content", lambda *a: pytest.fail("No page content reads")
            )
            patch.setattr(
                pdf,
                "_decode_cos_stream",
                lambda *a: pytest.fail("No appearance or script reads"),
            )
            assert form.calculation_order == ["invoice.tax", "invoice.total"]
            form.calculation_order = order
            assert form.calculation_order == (order or [])
            assert pdf._lazy and pdf.page_contents == []
        updated = _bytes(reopened, incremental=True)
    assert updated.startswith(original)
    with Document(updated, password="owner") as final:
        assert final.form.calculation_order == (order or [])
        assert final.form.to_dict()["invoice.total"] == "0"


def test_disposed_document_rejects_retained_form_access(document):
    form = document.form
    document.dispose()
    with pytest.raises(AsposePdfException, match="disposed"):
        _ = form.calculation_order
    with pytest.raises(AsposePdfException, match="disposed"):
        form.calculation_order = None
