"""Import links from JSON and CSV reports."""

from __future__ import annotations

import io
import json

import pytest

from aspose_pdf import (
    Document,
    FitDestination,
    FitRDestination,
    GoToRAction,
    URIAction,
)
from aspose_pdf.exceptions import PdfValidationException


@pytest.fixture
def document():
    with Document() as doc:
        for _ in range(3):
            doc.pages.add()
        yield doc


def _json_schema(links):
    return json.dumps({"schema_version": 2, "links": links}, ensure_ascii=False).encode()


def test_import_links_json_creates_uri_links(document):
    """Importing a URI link creates a Link annotation."""
    links = [
        {
            "page_index": 1,
            "uri": "https://example.com",
            "rect": [10, 10, 100, 100],
        }
    ]
    count = document.import_links_json(_json_schema(links))
    assert count == 1

    links = document.extract_links()
    assert len(links) == 1
    assert links[0].action_type == "URI"
    assert links[0].uri == "https://example.com"


def test_import_links_json_creates_local_destination(document):
    """Importing a GoTo destination creates a Link annotation."""
    links = [
        {
            "page_index": 1,
            "destination": {
                "page_index": 2,
                "view": "XYZ",
                "parameters": [None, 700, 2],
            },
            "rect": [10, 10, 100, 100],
        }
    ]
    count = document.import_links_json(_json_schema(links))
    assert count == 1

    links = document.extract_links()
    assert len(links) == 1
    assert links[0].action_type == "GoTo"
    assert links[0].destination is not None
    assert links[0].destination.page == 2


def test_import_links_json_creates_remote_destination(document):
    """Importing a GoToR creates a Link annotation with remote file."""
    links = [
        {
            "page_index": 1,
            "file": "remote.pdf",
            "destination": {
                "page_index": 3,
                "view": "FitR",
                "parameters": [0, 0, 600, 800],
            },
            "rect": [10, 10, 100, 100],
        }
    ]
    count = document.import_links_json(_json_schema(links))
    assert count == 1

    links = document.extract_links()
    assert len(links) == 1
    assert links[0].action_type == "GoToR"
    assert links[0].file == "remote.pdf"
    assert links[0].destination is not None


def test_import_links_json_creates_named_action(document):
    """Importing a named action creates a Link annotation."""
    links = [
        {
            "page_index": 1,
            "named_action": "NextPage",
            "rect": [10, 10, 100, 100],
        }
    ]
    count = document.import_links_json(_json_schema(links))
    assert count == 1

    links = document.extract_links()
    assert len(links) == 1
    assert links[0].action_type == "Named"
    assert links[0].named_action == "NextPage"


def test_import_links_json_applies_page_map(document):
    """Importing with page_map maps source pages to destination pages."""
    # Source has page_index 1, but we map it to page 3 in destination
    links = [
        {
            "page_index": 1,
            "uri": "https://example.com",
            "rect": [10, 10, 100, 100],
        }
    ]
    page_map = {1: 3}
    count = document.import_links_json(_json_schema(links), page_map=page_map)
    assert count == 1

    # Check that the link was added to page 3, not page 1
    links = document.extract_links()
    assert len(links) == 1
    assert links[0].page_index == 3  # 1-based


def test_import_links_json_multiple_links(document):
    """Importing multiple links adds all of them."""
    links = [
        {"page_index": 1, "uri": "https://a.com", "rect": [10, 10, 100, 100]},
        {"page_index": 2, "uri": "https://b.com", "rect": [20, 20, 110, 110]},
        {"page_index": 3, "uri": "https://c.com", "rect": [30, 30, 120, 120]},
    ]
    count = document.import_links_json(_json_schema(links))
    assert count == 3

    extracted = document.extract_links()
    assert len(extracted) == 3
    assert extracted[0].uri == "https://a.com"
    assert extracted[1].uri == "https://b.com"
    assert extracted[2].uri == "https://c.com"


def test_import_links_json_preserves_existing_annotations(document):
    """Importing links does not remove existing annotations."""
    # Add a link first
    document.pages[1].add_link((10, 10, 100, 100), URIAction("https://first.com"))

    # Import more links
    links = [
        {"page_index": 1, "uri": "https://second.com", "rect": [20, 20, 110, 110]},
    ]
    count = document.import_links_json(_json_schema(links))
    assert count == 1

    # Both links should exist
    extracted = document.extract_links()
    assert len(extracted) == 2
    assert extracted[0].uri == "https://first.com"
    assert extracted[1].uri == "https://second.com"


def test_import_links_json_invalid_schema_version(document):
    """Importing with invalid schema_version raises."""
    data = json.dumps({"schema_version": 1, "links": []}, ensure_ascii=False).encode()
    with pytest.raises(PdfValidationException, match="schema_version must be 2"):
        document.import_links_json(data)


def test_import_links_json_invalid_page_index(document):
    """Importing with out-of-range page_index raises."""
    links = [
        {
            "page_index": 100,
            "uri": "https://example.com",
            "rect": [10, 10, 100, 100],
        }
    ]
    with pytest.raises(PdfValidationException, match="target page 100 exceeds"):
        document.import_links_json(_json_schema(links))


def test_import_links_json_invalid_page_map(document):
    """Importing with page_map that doesn't include a required page raises."""
    links = [
        {
            "page_index": 1,
            "uri": "https://example.com",
            "rect": [10, 10, 100, 100],
        }
    ]
    page_map = {2: 3}  # Doesn't include 1
    with pytest.raises(PdfValidationException):
        document.import_links_json(_json_schema(links), page_map=page_map)


def test_import_links_csv_creates_links(document):
    """Importing links from CSV creates Link annotations."""
    # Match the format from export_links_csv
    csv_data = "page_number,annotation_index,action_type,action_path,uri,resolved_uri,target_page_number,remote_page_number,destination_name,destination_view,destination_parameters,file,named_action,x0,y0,x1,y1,issues\n1,0,URI,A,https://example.com,https://example.com,,,,,,,,10.0,10.0,100.0,100.0,[]"

    count = document.import_links_csv(csv_data.encode("utf-8"))
    assert count == 1

    links = document.extract_links()
    assert len(links) == 1
    assert links[0].action_type == "URI"
    assert links[0].uri == "https://example.com"


def test_import_links_csv_with_destination(document):
    """Importing CSV with destination view creates appropriate link."""
    csv_data = "page_number,annotation_index,action_type,action_path,uri,resolved_uri,target_page_number,remote_page_number,destination_name,destination_view,destination_parameters,file,named_action,x0,y0,x1,y1,issues\n1,0,GoTo,Dest,,,,2,,XYZ,\"[null,700,2]\",,,20.0,20.0,110.0,110.0,[]"

    count = document.import_links_csv(csv_data.encode("utf-8"))
    assert count == 1

    links = document.extract_links()
    assert len(links) == 1
    assert links[0].action_type == "GoTo"
    assert links[0].destination is not None


def test_import_links_csv_with_file(document):
    """Importing CSV with file creates GoToR link."""
    csv_data = "page_number,annotation_index,action_type,action_path,uri,resolved_uri,target_page_number,remote_page_number,destination_name,destination_view,destination_parameters,file,named_action,x0,y0,x1,y1,issues\n1,0,GoToR,A,,,,2,,XYZ,\"[null,0,0]\",remote.pdf,,10.0,10.0,100.0,100.0,[]"

    count = document.import_links_csv(csv_data.encode("utf-8"))
    assert count == 1

    links = document.extract_links()
    assert len(links) == 1
    assert links[0].action_type == "GoToR"
    assert links[0].file == "remote.pdf"


def test_import_links_csv_named_action(document):
    """Importing CSV with named_action creates Named action."""
    csv_data = "page_number,annotation_index,action_type,action_path,uri,resolved_uri,target_page_number,remote_page_number,destination_name,destination_view,destination_parameters,file,named_action,x0,y0,x1,y1,issues\n1,0,Named,A,,,,,,,,,NextPage,10.0,10.0,100.0,100.0,[]"

    count = document.import_links_csv(csv_data.encode("utf-8"))
    assert count == 1

    links = document.extract_links()
    assert len(links) == 1
    assert links[0].action_type == "Named"
    assert links[0].named_action == "NextPage"


def test_import_links_csv_preserves_existing(document):
    """Importing CSV links does not remove existing annotations."""
    # Add a link first
    document.pages[1].add_link((10, 10, 100, 100), URIAction("https://first.com"))

    # Import more links
    csv_data = "page_number,annotation_index,action_type,action_path,uri,resolved_uri,target_page_number,remote_page_number,destination_name,destination_view,destination_parameters,file,named_action,x0,y0,x1,y1,issues\n1,0,URI,A,https://second.com,https://second.com,,,,,,,,10.0,10.0,100.0,100.0,[]"

    count = document.import_links_csv(csv_data.encode("utf-8"))
    assert count == 1

    extracted = document.extract_links()
    assert len(extracted) == 2
    assert extracted[0].uri == "https://first.com"
    assert extracted[1].uri == "https://second.com"


def test_import_links_json_from_file(document, tmp_path):
    """Importing from a file path works."""
    links = [
        {
            "page_index": 1,
            "uri": "https://example.com",
            "rect": [10, 10, 100, 100],
        }
    ]
    path = tmp_path / "links.json"
    path.write_bytes(_json_schema(links))

    count = document.import_links_json(path)
    assert count == 1

    links = document.extract_links()
    assert len(links) == 1


def test_import_links_csv_from_file(document, tmp_path):
    """Importing CSV from a file path works."""
    csv_data = "page_number,annotation_index,action_type,action_path,uri,resolved_uri,target_page_number,remote_page_number,destination_name,destination_view,destination_parameters,file,named_action,x0,y0,x1,y1,issues\n1,0,URI,A,https://example.com,https://example.com,,,,,,,,10.0,10.0,100.0,100.0,[]"
    path = tmp_path / "links.csv"
    path.write_text(csv_data, encoding="utf-8")

    count = document.import_links_csv(path)
    assert count == 1

    links = document.extract_links()
    assert len(links) == 1


def test_import_links_json_from_bytes(document):
    """Importing from bytes works."""
    links = [
        {
            "page_index": 1,
            "uri": "https://example.com",
            "rect": [10, 10, 100, 100],
        }
    ]
    data = _json_schema(links)

    count = document.import_links_json(data)
    assert count == 1


def test_import_links_csv_from_bytes(document):
    """Importing CSV from bytes works."""
    csv_data = "page_number,annotation_index,action_type,action_path,uri,resolved_uri,target_page_number,remote_page_number,destination_name,destination_view,destination_parameters,file,named_action,x0,y0,x1,y1,issues\n1,0,URI,A,https://example.com,https://example.com,,,,,,,,10.0,10.0,100.0,100.0,[]"

    count = document.import_links_csv(csv_data.encode("utf-8"))
    assert count == 1


def test_import_links_json_from_stream(document):
    """Importing from a binary stream works."""
    links = [
        {
            "page_index": 1,
            "uri": "https://example.com",
            "rect": [10, 10, 100, 100],
        }
    ]
    data = _json_schema(links)

    stream = io.BytesIO(data)
    count = document.import_links_json(stream)
    assert count == 1


def test_import_links_csv_from_stream(document):
    """Importing CSV from a binary stream works."""
    csv_data = "page_number,annotation_index,action_type,action_path,uri,resolved_uri,target_page_number,remote_page_number,destination_name,destination_view,destination_parameters,file,named_action,x0,y0,x1,y1,issues\n1,0,URI,A,https://example.com,https://example.com,,,,,,,,10.0,10.0,100.0,100.0,[]"

    stream = io.BytesIO(csv_data.encode("utf-8"))
    count = document.import_links_csv(stream)
    assert count == 1


def test_import_links_json_rejects_invalid_source(document):
    """Importing with invalid source type raises."""
    with pytest.raises(TypeError):
        document.import_links_json(123)  # type: ignore


def test_import_links_csv_rejects_invalid_source(document):
    """Importing CSV with invalid source type raises."""
    with pytest.raises(TypeError):
        document.import_links_csv(123)  # type: ignore


def test_import_links_json_empty_links(document):
    """Importing with empty links array returns 0."""
    data = json.dumps({"schema_version": 2, "links": []}, ensure_ascii=False).encode()
    count = document.import_links_json(data)
    assert count == 0


def test_import_links_csv_empty(document):
    """Importing from CSV with no data rows returns 0."""
    csv_data = "page_number,annotation_index,action_type,action_path,uri,resolved_uri,target_page_number,remote_page_number,destination_name,destination_view,destination_parameters,file,named_action,x0,y0,x1,y1,issues"
    count = document.import_links_csv(csv_data.encode("utf-8"))
    assert count == 0


def test_import_links_json_invalid_destination_view(document):
    """Importing with invalid destination view raises."""
    links = [
        {
            "page_index": 1,
            "destination": {
                "page_index": 2,
                "view": "InvalidView",
                "parameters": [],
            },
        }
    ]
    with pytest.raises(PdfValidationException, match="Unknown view type"):
        document.import_links_json(_json_schema(links))


def test_import_links_json_invalid_destination_params(document):
    """Importing with wrong number of destination parameters raises."""
    links = [
        {
            "page_index": 1,
            "destination": {
                "page_index": 2,
                "view": "XYZ",
                "parameters": [1],  # XYZ needs 3
            },
        }
    ]
    with pytest.raises(PdfValidationException, match="expects 3 parameters"):
        document.import_links_json(_json_schema(links))


def test_import_links_json_invalid_rect(document):
    """Importing with invalid rect doesn't create a link."""
    links = [
        {
            "page_index": 1,
            "uri": "https://example.com",
            "rect": [10, 10],  # Only 2 values
        }
    ]
    count = document.import_links_json(_json_schema(links))
    # Should return 0 because rect is invalid
    assert count == 0


def test_round_trip_json(document):
    """Export and re-import links should work."""
    # Add some links
    document.pages[1].add_link((10, 10, 100, 100), URIAction("https://example.com"))
    document.pages[1].add_link((20, 20, 110, 110), FitDestination(2))
    document.pages[2].add_link((30, 30, 120, 120), GoToRAction("remote.pdf", FitRDestination(3, 0, 0, 600, 800)))

    # Export
    json_data = document.export_links_json()
    payload = json.loads(json_data)

    # Clear the document
    with Document() as new_doc:
        for _ in range(3):
            new_doc.pages.add()

        # Re-import
        count = new_doc.import_links_json(json_data)
        assert count == 3

        # Verify
        links = new_doc.extract_links()
        assert len(links) == 3
        assert links[0].action_type == "URI"
        assert links[0].uri == "https://example.com"
        assert links[1].action_type == "GoTo"
        assert links[2].action_type == "GoToR"


def test_round_trip_csv(document):
    """Export to CSV and re-import should work."""
    # Add some links
    document.pages[1].add_link((10, 10, 100, 100), URIAction("https://example.com"))
    document.pages[1].add_link((20, 20, 110, 110), FitDestination(2))

    # Export
    csv_data = document.export_links_csv()

    # Clear the document
    with Document() as new_doc:
        for _ in range(2):
            new_doc.pages.add()

        # Re-import
        count = new_doc.import_links_csv(csv_data)
        assert count == 2

        # Verify
        links = new_doc.extract_links()
        assert len(links) == 2
