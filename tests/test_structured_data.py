"""
Unit tests for structured_data.py - parsing schema.org Event/SportsEvent JSON-LD
straight out of raw HTML, no LLM/network involved anywhere here.
"""

import json

from services import structured_data


def _html_with_ld_json(data: dict | list, extra: str = "") -> str:
    return f"""
    <html><head>
    <script type="application/ld+json">{json.dumps(data)}</script>
    {extra}
    </head><body>real page content here</body></html>
    """


_SPORTS_EVENT = {
    "@context": "https://schema.org",
    "@type": "SportsEvent",
    "sport": "Running",
    "name": "Poole 3k 2027",
    "description": "Loops around Poole Park.",
    "startDate": "2027-05-29T11:00:00+01:00",
    "endDate": "2027-05-29T13:00:00+01:00",
    "location": {
        "@type": "Place",
        "name": "Baiter Park",
        "address": {
            "@type": "PostalAddress",
            "streetAddress": "Catalina Drive",
            "addressLocality": "Poole",
            "postalCode": "BH15 1TQ",
            "addressCountry": "GB",
        },
    },
    "offers": {"@type": "AggregateOffer", "lowPrice": "5.00", "highPrice": "7.49", "priceCurrency": "GBP"},
}


def test_extract_event_fields_from_real_shaped_sports_event():
    html = _html_with_ld_json(_SPORTS_EVENT)
    fields = structured_data.extract_event_fields(html)

    assert fields == {
        "name": "Poole 3k 2027",
        "sport": "running",
        "summary": "Loops around Poole Park.",
        "date_text": "2027-05-29T11:00:00+01:00 to 2027-05-29T13:00:00+01:00",
        "location": "Baiter Park, Catalina Drive, Poole, BH15 1TQ, GB",
    }


def test_no_ld_json_block_returns_empty_dict():
    assert structured_data.extract_event_fields("<html><body>plain page</body></html>") == {}


def test_ld_json_present_but_not_an_event_type_returns_empty_dict():
    html = _html_with_ld_json({"@type": "Organization", "name": "Some Org"})
    assert structured_data.extract_event_fields(html) == {}


def test_malformed_json_is_skipped_not_raised():
    html = """<html><head>
    <script type="application/ld+json">{ not valid json </script>
    </head><body>content</body></html>"""
    assert structured_data.extract_event_fields(html) == {}


def test_multiple_script_blocks_finds_the_event_one():
    html = f"""
    <html><head>
    <script type="application/ld+json">{json.dumps({"@type": "Organization", "name": "Acme"})}</script>
    <script type="application/ld+json">{json.dumps(_SPORTS_EVENT)}</script>
    </head><body>content</body></html>
    """
    fields = structured_data.extract_event_fields(html)
    assert fields["name"] == "Poole 3k 2027"


def test_graph_wrapped_json_ld_is_unwrapped():
    wrapped = {"@context": "https://schema.org", "@graph": [{"@type": "Organization", "name": "Acme"}, _SPORTS_EVENT]}
    html = _html_with_ld_json(wrapped)
    fields = structured_data.extract_event_fields(html)
    assert fields["name"] == "Poole 3k 2027"


def test_top_level_array_of_json_ld_objects():
    html = _html_with_ld_json([{"@type": "Organization", "name": "Acme"}, _SPORTS_EVENT])
    fields = structured_data.extract_event_fields(html)
    assert fields["name"] == "Poole 3k 2027"


def test_bare_event_type_also_matches_not_just_sports_event():
    data = {"@type": "Event", "name": "Some Generic Event"}
    html = _html_with_ld_json(data)
    fields = structured_data.extract_event_fields(html)
    assert fields["name"] == "Some Generic Event"


def test_only_non_empty_fields_are_returned():
    # No description/location/dates at all - only name and sport should appear.
    data = {"@type": "SportsEvent", "name": "Bare Event", "sport": "Cycling"}
    html = _html_with_ld_json(data)
    fields = structured_data.extract_event_fields(html)
    assert fields == {"name": "Bare Event", "sport": "cycling"}


def test_date_text_single_date_when_no_end_date():
    data = {"@type": "SportsEvent", "name": "X", "startDate": "2027-01-01T09:00:00Z"}
    fields = structured_data.extract_event_fields(_html_with_ld_json(data))
    assert fields["date_text"] == "2027-01-01T09:00:00Z"


def test_date_text_omitted_when_no_start_date():
    data = {"@type": "SportsEvent", "name": "X", "endDate": "2027-01-01T09:00:00Z"}
    fields = structured_data.extract_event_fields(_html_with_ld_json(data))
    assert "date_text" not in fields


def test_location_formatted_from_string_address():
    data = {"@type": "SportsEvent", "name": "X", "location": {"name": "Venue", "address": "123 Main St, Town"}}
    fields = structured_data.extract_event_fields(_html_with_ld_json(data))
    assert fields["location"] == "Venue, 123 Main St, Town"


def test_location_omitted_when_not_a_dict():
    data = {"@type": "SportsEvent", "name": "X", "location": "Somewhere"}
    fields = structured_data.extract_event_fields(_html_with_ld_json(data))
    assert "location" not in fields
