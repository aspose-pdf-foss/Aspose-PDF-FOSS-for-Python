"""Independent AcroForm outputs from reusable template and record batches."""

from __future__ import annotations

import io
from collections import UserDict
from itertools import repeat

import pytest

from aspose_pdf import (
    Document,
    FormFiller,
    FormFillerOptions,
    OutlineItem,
    PdfLoadLimits,
    PdfResourceLimitException,
    URIAction,
)
from aspose_pdf.exceptions import AsposePdfException, PdfValidationException
from aspose_pdf.lowcode import (
    ByteArrayDataSource,
    DataSource,
    FileDataSource,
    ResultContainer,
    StreamDataSource,
)
from aspose_pdf.lowcode import (
    FormFiller as ModuleFiller,
)
from aspose_pdf.lowcode import (
    FormFillerOptions as ModuleOptions,
)


def _bytes(document):
    with io.BytesIO() as output:
        document.save(output)
        return output.getvalue()


def _template(title="Template"):
    with Document() as document:
        page = document.pages.add(size=(320, 320))
        page.add_text("Form " + title, 20, 300)
        document.info["Title"] = title
        form = document.form
        form.add_text_field("person.name", page, (20, 260, 220, 282), value=title)
        form.add_text_field("city", page, (20, 225, 220, 247), value="Home")
        form.add_checkbox("agree", page, (20, 190, 40, 210), on_value="Agree")
        form.add_radio_group("size", page, [("S", (60, 190, 80, 210)), ("M", (90, 190, 110, 210))], value="S")
        form.add_list_box("colors", page, (200, 120, 300, 210), [("r", "Red"), ("g", "Green")], multiselect=True, value=["r"])
        form.add_combo_box("country", page, (20, 150, 140, 174), ["NL", "DE"], value="NL")
        form.add_push_button("go", page, (20, 100, 100, 124), caption="Go")
        form.add_signature_field("sig", page, (20, 50, 140, 74))
        page.add_link((230, 50, 300, 74), URIAction("https://example.test"))
        page.annotations.add("Text", (240, 80, 260, 100), "Review note")
        document.outlines.add(OutlineItem("Start", 0))
        document.add_attachment("help.txt", b"Instructions")
        return _bytes(document)


class CountingSource(DataSource):
    def __init__(self, data):
        self.data, self.reads = data, 0

    def read_bytes(self):
        self.reads += 1
        return self.data


def _values(result, password=None):
    with Document(result.to_array(), password=password) as document:
        return document.form.to_dict()


@pytest.mark.parametrize("kind", ["bytes", "file", "stream", "custom"])
def test_each_record_starts_from_template_and_sources_are_not_filled_in_place(kind, tmp_path):
    assert FormFiller is ModuleFiller and FormFillerOptions is ModuleOptions
    raw = _template()
    path = tmp_path / "template.pdf"
    stream = io.BytesIO(raw)
    if kind == "file":
        path.write_bytes(raw)
        source = FileDataSource(path)
    elif kind == "stream":
        source = StreamDataSource(stream)
    elif kind == "custom":
        source = CountingSource(raw)
    else:
        source = ByteArrayDataSource(raw)
    options = FormFillerOptions([
        {"person.name": "Alice", "city": "Paris", "agree": True, "size": "M", "colors": ["g"], "country": "DE"},
        {"person.name": "Bob"},
        {},
    ]).add_input(source)
    results = FormFiller().process(options)
    assert isinstance(results, ResultContainer) and len(results) == 3
    assert all(result.is_byte_array() for result in results)
    first, second, third = map(_values, results)
    assert first == {"person.name": "Alice", "city": "Paris", "agree": True, "size": "M", "colors": ["g"], "country": "DE"}
    assert second == {"person.name": "Bob", "city": "Home", "agree": False, "size": "S", "colors": ["r"], "country": "NL"}
    assert third["person.name"] == "Template" and third["city"] == "Home"
    with Document(raw) as template:
        assert template.form.to_dict() == third
    if kind == "file":
        assert path.read_bytes() == raw
    elif kind == "bytes":
        assert source.read_bytes() == raw
    elif kind == "custom":
        assert source.reads == 1 and source.data == raw
    assert stream.getvalue() == raw and not stream.closed


def test_multiple_templates_are_ordered_by_template_then_record():
    options = FormFillerOptions([{"city": "A"}, {"city": "B"}])
    for title in ("First", "Second"):
        options.add_input(ByteArrayDataSource(_template(title)))
    results = FormFiller().process(options)
    assert [(_values(result)["person.name"], _values(result)["city"]) for result in results] == [
        ("First", "A"), ("First", "B"), ("Second", "A"), ("Second", "B"),
    ]


def test_snapshots_copy_generators_mappings_and_lists_and_allow_later_reassignment():
    row = UserDict({"person.name": "Alice", "colors": ["g"]})
    options = FormFillerOptions(record for record in [row])
    row["person.name"] = "Changed"
    row["colors"].append("r")
    assert options.records[0]["person.name"] == "Alice"
    assert options.records[0]["colors"] == ("g",)
    with pytest.raises(TypeError):
        options.records[0]["person.name"] = "Blocked"
    options.add_input(ByteArrayDataSource(_template()))
    for _ in range(2):
        assert _values(FormFiller().process(options)[0])["person.name"] == "Alice"
    options.records = [{"person.name": "Bob"}]
    with pytest.raises(TypeError):
        options.records = [{"person.name": 42}]
    assert _values(FormFiller().process(options)[0])["person.name"] == "Bob"


def test_copies_preserve_template_metadata_bookmarks_links_and_attachments(tmp_path):
    options = FormFillerOptions([{"person.name": "Alice"}]).add_input(ByteArrayDataSource(_template()))
    result = FormFiller().process(options)[0]
    path = tmp_path / "copy.pdf"
    result.save(path)
    with Document(path) as document:
        assert document.validate()
        assert document.info["Title"] == "Template"
        assert document.outlines[0].title == "Start"
        assert document.extract_links()[0].uri == "https://example.test"
        assert document.attachments["help.txt"] == b"Instructions"
        assert document.pages[0].size.width == 320


def test_configured_file_and_stream_outputs_receive_matching_results(tmp_path):
    path = tmp_path / "nested" / "alice.pdf"
    with io.BytesIO(b"stale trailing content") as stream:
        options = FormFillerOptions([{"person.name": "Alice"}, {"person.name": "Bob"}])
        options.add_input(ByteArrayDataSource(_template()))
        options.add_output(FileDataSource(path)).add_output(StreamDataSource(stream))
        results = FormFiller().process(options)
        assert path.read_bytes() == results[0].to_array()
        assert stream.getvalue() == results[1].to_array() and not stream.closed


def test_flatten_bakes_fields_and_keeps_links_and_other_annotations():
    options = FormFillerOptions([{"person.name": "Alice", "agree": True}], flatten=True)
    options.add_input(ByteArrayDataSource(_template()))
    with Document(FormFiller().process(options)[0].to_array()) as document:
        assert len(document.form) == 0
        assert document.extract_links()[0].uri == "https://example.test"
        assert any(annotation.contents == "Review note" for annotation in document.pages[0].annotations)
        assert "Alice" in document.extract_text()


def test_nulls_choice_values_and_non_data_fields_follow_form_fill():
    options = FormFillerOptions([{
        "person.name": None, "city": "café", "agree": None, "size": None,
        "colors": (), "country": None, "sig": "ignored", "go": "ignored",
    }]).add_input(ByteArrayDataSource(_template()))
    result = FormFiller().process(options)[0]
    assert _values(result) == {"person.name": "", "city": "café", "agree": False, "size": None, "colors": [], "country": None}
    with Document(result.to_array()) as document:
        assert document.form["sig"].value is None and document.form["go"].value is None


def test_unknown_fields_are_strict_by_default_and_ignored_when_disabled():
    raw = _template()
    options = FormFillerOptions([{"missing": "ignored", "person.name": "Alice"}]).add_input(ByteArrayDataSource(raw))
    with pytest.raises(KeyError, match="missing"):
        FormFiller().process(options)
    options.strict = False
    assert _values(FormFiller().process(options)[0])["person.name"] == "Alice"


@pytest.mark.parametrize("row,error", [
    ({"agree": "Agree"}, TypeError), ({"size": "L"}, PdfValidationException),
    ({"country": "FR"}, PdfValidationException), ({"colors": ["unknown"]}, PdfValidationException),
    ({"person.name": ["wrong type"]}, TypeError),
])
def test_later_invalid_record_does_not_write_any_outputs(row, error):
    options = FormFillerOptions([{"person.name": "Good"}, row]).add_input(ByteArrayDataSource(_template()))
    outputs = [ByteArrayDataSource(b"untouched-one"), ByteArrayDataSource(b"untouched-two")]
    for output in outputs:
        options.add_output(output)
    with pytest.raises(error):
        FormFiller().process(options)
    assert [output.read_bytes() for output in outputs] == [b"untouched-one", b"untouched-two"]


def test_later_invalid_template_does_not_write_any_outputs():
    options = FormFillerOptions([{"person.name": "Good"}])
    options.add_input(ByteArrayDataSource(_template())).add_input(ByteArrayDataSource(b"not PDF"))
    output = ByteArrayDataSource(b"untouched")
    options.add_output(output)
    with pytest.raises(Exception):
        FormFiller().process(options)
    assert output.read_bytes() == b"untouched"


@pytest.mark.parametrize("records", [None, 3, True, "data", b"data", {}, ["row"], [3], [{1: "bad"}], [{"a": 3}], [{"a": {}}], [{"a": [1]}]])
def test_invalid_record_shapes_are_rejected(records):
    with pytest.raises(TypeError):
        FormFillerOptions(records)


@pytest.mark.parametrize("records", [[{"bad\ud800": "value"}], [{"a": "\ud800"}], [{"a": ["\ud800"]}]])
def test_invalid_unicode_records_are_rejected(records):
    with pytest.raises(PdfValidationException, match="Unicode"):
        FormFillerOptions(records)


@pytest.mark.parametrize("options,error", [
    ({"strict": 1}, TypeError), ({"flatten": "yes"}, TypeError),
    ({"password": 3}, TypeError), ({"password": "\ud800"}, PdfValidationException),
])
def test_invalid_options_are_rejected_at_construction_and_processing(options, error):
    with pytest.raises(error):
        FormFillerOptions([{}], **options)
    configuration = FormFillerOptions([{}])
    source = CountingSource(b"not read")
    configuration.add_input(source)
    for name, value in options.items():
        setattr(configuration, name, value)
    with pytest.raises(error):
        FormFiller().process(configuration)
    assert source.reads == 0


def test_duplicate_output_paths_and_streams_are_rejected_before_input_reads(tmp_path):
    source = CountingSource(b"not read")
    options = FormFillerOptions([{}, {}]).add_input(source)
    path = tmp_path / "same.pdf"
    options.add_output(FileDataSource(path)).add_output(FileDataSource(path))
    with pytest.raises(PdfValidationException, match="distinct destinations"):
        FormFiller().process(options)
    assert source.reads == 0 and not path.exists()
    with io.BytesIO() as stream:
        options.outputs = [StreamDataSource(stream), StreamDataSource(stream)]
        with pytest.raises(PdfValidationException, match="distinct destinations"):
            FormFiller().process(options)
        assert stream.getvalue() == b"" and source.reads == 0


def test_more_outputs_than_results_are_rejected_before_input_reads():
    source = CountingSource(b"not read")
    options = FormFillerOptions([{}]).add_input(source)
    options.add_output(ByteArrayDataSource()).add_output(ByteArrayDataSource())
    with pytest.raises(AsposePdfException, match="More output"):
        FormFiller().process(options)
    assert source.reads == 0


def test_empty_records_do_not_read_template_and_missing_inputs_still_raise():
    source = CountingSource(b"not read")
    options = FormFillerOptions([]).add_input(source)
    assert len(FormFiller().process(options)) == 0 and source.reads == 0
    with pytest.raises(AsposePdfException, match="No input"):
        FormFiller().process(FormFillerOptions([]))


def test_unending_records_are_bounded_and_rechecked_if_policy_changes():
    with pytest.raises(PdfResourceLimitException, match="form batch record items"):
        FormFillerOptions(repeat({}), limits=PdfLoadLimits(max_container_items=5))
    options = FormFillerOptions([{}, {}]).add_input(CountingSource(b"not read"))
    options.limits = PdfLoadLimits(max_container_items=1)
    with pytest.raises(PdfResourceLimitException, match="form batch record items"):
        FormFiller().process(options)
    assert options.inputs[0].reads == 0


def _tiny_template():
    with Document() as document:
        page = document.pages.add(size=(240, 120))
        document.form.add_text_field("name", page, (10, 70, 200, 100), value="Base")
        return _bytes(document)


def test_generated_appearance_streams_obey_decoded_stream_limits():
    options = FormFillerOptions(
        [{"name": "A" * 300}], limits=PdfLoadLimits(max_decoded_stream_bytes=128),
    ).add_input(ByteArrayDataSource(_tiny_template()))
    output = ByteArrayDataSource(b"untouched")
    options.add_output(output)
    with pytest.raises(PdfResourceLimitException, match="max_decoded_stream_bytes"):
        FormFiller().process(options)
    assert output.read_bytes() == b"untouched"


def test_record_text_sizes_include_unicode_byte_order_marks():
    with pytest.raises(PdfResourceLimitException, match="field value"):
        FormFillerOptions([{"a": "é"}], limits=PdfLoadLimits(max_object_bytes=3))
    with pytest.raises(PdfResourceLimitException, match="record text"):
        FormFillerOptions([{"a": "xx"}, {"a": "yy"}], limits=PdfLoadLimits(max_total_decoded_bytes=5))


def test_result_count_product_is_bounded_before_reading_any_template():
    options = FormFillerOptions([{}, {}, {}], limits=PdfLoadLimits(max_container_items=5))
    sources = [CountingSource(b"not read"), CountingSource(b"not read")]
    for source in sources:
        options.add_input(source)
    with pytest.raises(PdfResourceLimitException, match="result count"):
        FormFiller().process(options)
    assert all(source.reads == 0 for source in sources)


def test_total_batch_pages_are_bounded_and_no_outputs_are_emitted():
    options = FormFillerOptions(
        [{"name": "A"}, {"name": "B"}], limits=PdfLoadLimits(max_pages=1),
    ).add_input(ByteArrayDataSource(_tiny_template()))
    output = ByteArrayDataSource(b"untouched")
    options.add_output(output)
    with pytest.raises(PdfResourceLimitException, match="max_pages"):
        FormFiller().process(options)
    assert output.read_bytes() == b"untouched"


def test_output_pdf_size_limit_is_checked_before_external_writes():
    raw = _tiny_template()
    options = FormFillerOptions(
        [{"name": "A" * 3000}], limits=PdfLoadLimits(max_input_bytes=len(raw) + 500),
    ).add_input(ByteArrayDataSource(raw))
    output = ByteArrayDataSource(b"untouched")
    options.add_output(output)
    with pytest.raises(PdfResourceLimitException, match="form batch output PDF"):
        FormFiller().process(options)
    assert output.read_bytes() == b"untouched"


def test_all_working_documents_close_on_late_load_failure(monkeypatch):
    raw = _tiny_template()
    initialize = Document.__init__
    created = []

    def tracked(document, *args, **kwargs):
        created.append(document)
        initialize(document, *args, **kwargs)

    monkeypatch.setattr(Document, "__init__", tracked)
    options = FormFillerOptions([{"name": "A"}])
    options.add_input(ByteArrayDataSource(raw)).add_input(ByteArrayDataSource(b"not PDF"))
    with pytest.raises(AsposePdfException):
        FormFiller().process(options)
    assert len(created) == 2 and all(document._disposed for document in created)


def test_filled_appearances_change_and_survive_independent_round_trips():
    raw = _tiny_template()
    with Document(raw) as template:
        before = template.pages[0].render(dpi=36, antialias=False).pixels
    results = FormFiller().process(FormFillerOptions([
        {"name": "First"}, {"name": "Second"},
    ]).add_input(ByteArrayDataSource(raw)))
    pixels = []
    for result in results:
        with Document(result.to_array()) as document:
            pixels.append(document.pages[0].render(dpi=36, antialias=False).pixels)
            with Document(_bytes(document)) as reopened:
                assert reopened.pages[0].render(dpi=36, antialias=False).pixels == pixels[-1]
    assert pixels[0] != pixels[1] and all(pixel != before for pixel in pixels)


def test_cumulative_result_bytes_are_bounded_before_any_output_is_emitted():
    raw = _tiny_template()
    baseline = FormFiller().process(FormFillerOptions([{}]).add_input(ByteArrayDataSource(raw)))[0].to_array()
    options = FormFillerOptions(
        [{}, {}], limits=PdfLoadLimits(max_total_decoded_bytes=2 * len(baseline) - 1),
    ).add_input(ByteArrayDataSource(raw))
    output = ByteArrayDataSource(b"untouched")
    options.add_output(output)
    with pytest.raises(PdfResourceLimitException, match="form batch retained bytes"):
        FormFiller().process(options)
    assert output.read_bytes() == b"untouched"


def test_encrypted_template_password_and_encryption_are_preserved():
    with Document(_tiny_template()) as document:
        document.encrypt("user", "owner")
        raw = _bytes(document)
    options = FormFillerOptions([{"name": "Alice"}, {"name": "Bob"}], password="user")
    source = ByteArrayDataSource(raw)
    options.add_input(source)
    results = FormFiller().process(options)
    assert [_values(result, password="user")["name"] for result in results] == ["Alice", "Bob"]
    assert source.read_bytes() == raw
    for result in results:
        assert b"/Encrypt" in result.to_array()


def test_current_stream_position_is_used_once_and_stream_stays_open():
    raw = _tiny_template()
    with io.BytesIO(b"prefix" + raw) as stream:
        stream.seek(len(b"prefix"))
        options = FormFillerOptions([{"name": "Alice"}, {"name": "Bob"}])
        options.add_input(StreamDataSource(stream))
        results = FormFiller().process(options)
        assert [_values(result)["name"] for result in results] == ["Alice", "Bob"]
        assert stream.tell() == len(b"prefix" + raw) and not stream.closed


def test_processing_snapshots_flatten_and_records_before_reading_inputs():
    options = FormFillerOptions([{"name": "Alice"}])

    class SteeringSource(CountingSource):
        def read_bytes(self):
            options.flatten = True
            options.records = [{"name": "Changed"}]
            return super().read_bytes()

    options.add_input(SteeringSource(_tiny_template()))
    with Document(FormFiller().process(options)[0].to_array()) as document:
        assert len(document.form) == 1
        assert document.form["name"].value == "Alice"


def test_processing_snapshots_strict_flag_before_reading_inputs():
    options = FormFillerOptions([{"missing": "value"}])

    class SteeringSource(CountingSource):
        def read_bytes(self):
            options.strict = False
            return super().read_bytes()

    options.add_input(SteeringSource(_tiny_template()))
    with pytest.raises(KeyError, match="missing"):
        FormFiller().process(options)


def test_processing_snapshots_password_before_reading_inputs():
    with Document(_tiny_template()) as document:
        document.encrypt("user", "owner")
        raw = _bytes(document)
    options = FormFillerOptions([{"name": "Alice"}], password="user")

    class SteeringSource(CountingSource):
        def read_bytes(self):
            options.password = "wrong"
            return super().read_bytes()

    options.add_input(SteeringSource(raw))
    assert _values(FormFiller().process(options)[0], password="user")["name"] == "Alice"


def test_list_subclass_iteration_is_bounded_by_items_consumed():
    class MiscountedList(list):
        def __iter__(self):
            return iter(("a", "b", "c", "d"))

    with pytest.raises(PdfResourceLimitException, match="form batch record items"):
        FormFillerOptions(
            [{"colors": MiscountedList()}], limits=PdfLoadLimits(max_container_items=3),
        )
