"""
Unit tests for llm_extractor.extract_event_fields's known_fields handling (see
structured_data.py) - no real LLM/network calls, _run_llm is monkeypatched with a
canned response, same style as test_sitemap_crawler.py's other llm_extractor tests.
"""

from services import llm_extractor


def _patch_run_llm(monkeypatch, response: dict, capture: dict | None = None):
    def fake_run_llm(system_prompt, user_prompt, schema_properties, required, tool_name, max_tokens=1200):
        if capture is not None:
            capture["schema_properties"] = schema_properties
            capture["required"] = required
            capture["user_prompt"] = user_prompt
        return response

    monkeypatch.setattr(llm_extractor, "_run_llm", fake_run_llm)


_FULL_LLM_RESPONSE = {
    "name": "Should never be used",
    "sport": "should_never_be_used",
    "summary": "Should never be used",
    "date_text": "Should never be used",
    "location": "Should never be used",
    "start_location": None,
    "finish_location": None,
    "age_restriction_text": None,
    "distances": [{"distance_text": "5k", "price_text": "£15", "distance_category": "5k"}],
    "is_valid_event": True,
    "invalid_reason": None,
}


def test_no_known_fields_asks_llm_for_everything(monkeypatch):
    capture = {}
    _patch_run_llm(monkeypatch, _FULL_LLM_RESPONSE, capture)

    result = llm_extractor.extract_event_fields("https://example.com/event", "some markdown")

    assert set(capture["required"]) == {"name", "sport", "is_valid_event"}
    assert "name" in capture["schema_properties"]
    assert "sport" in capture["schema_properties"]
    assert result["name"] == "Should never be used"


def test_known_fields_removed_from_schema_and_required(monkeypatch):
    capture = {}
    _patch_run_llm(monkeypatch, _FULL_LLM_RESPONSE, capture)

    known_fields = {"name": "Poole 3k 2027", "sport": "running", "date_text": "2027-05-29", "location": "Baiter Park"}
    llm_extractor.extract_event_fields("https://example.com/event", "some markdown", known_fields=known_fields)

    for key in known_fields:
        assert key not in capture["schema_properties"]
        assert key not in capture["required"]
    # distances/is_valid_event/invalid_reason have no schema.org equivalent - always asked.
    assert "distances" in capture["schema_properties"]
    assert "is_valid_event" in capture["schema_properties"]
    assert capture["required"] == ["is_valid_event"]


def test_known_fields_win_in_the_final_merged_result(monkeypatch):
    _patch_run_llm(monkeypatch, _FULL_LLM_RESPONSE)

    known_fields = {"name": "Poole 3k 2027", "sport": "running", "date_text": "2027-05-29", "location": "Baiter Park"}
    result = llm_extractor.extract_event_fields("https://example.com/event", "some markdown", known_fields=known_fields)

    # These four come straight from known_fields, not the (deliberately wrong) LLM response.
    assert result["name"] == "Poole 3k 2027"
    assert result["sport"] == "running"
    assert result["date_text"] == "2027-05-29"
    assert result["location"] == "Baiter Park"
    # Whatever known_fields didn't cover still comes from the LLM as normal.
    assert result["distances"] == [{"distance_text": "5k", "price_text": "£15", "distance_category": "5k"}]
    assert result["is_valid_event"] is True


def test_known_fields_mentioned_in_the_prompt_sent_to_the_llm(monkeypatch):
    capture = {}
    _patch_run_llm(monkeypatch, _FULL_LLM_RESPONSE, capture)

    known_fields = {"name": "Poole 3k 2027"}
    llm_extractor.extract_event_fields("https://example.com/event", "some markdown", known_fields=known_fields)

    assert "Poole 3k 2027" in capture["user_prompt"]
    assert "structured data" in capture["user_prompt"].lower()


def test_empty_markdown_returns_none_regardless_of_known_fields():
    assert llm_extractor.extract_event_fields("https://example.com/event", "   ", known_fields={"name": "X"}) is None


def test_extraction_failure_returns_none(monkeypatch):
    def failing_run_llm(*args, **kwargs):
        raise RuntimeError("LLM call failed")

    monkeypatch.setattr(llm_extractor, "_run_llm", failing_run_llm)

    assert llm_extractor.extract_event_fields("https://example.com/event", "some markdown") is None
