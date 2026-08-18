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
    "occurrence": "one_off",
    "occurrences": [],
    "occurrence_weekdays": [],
    "occurrence_time": None,
    "occurrence_starts_on": None,
    "occurrence_ends_on": None,
}


def test_no_known_fields_asks_llm_for_everything(monkeypatch):
    capture = {}
    _patch_run_llm(monkeypatch, _FULL_LLM_RESPONSE, capture)

    result = llm_extractor.extract_event_fields("https://example.com/event", "some markdown")

    assert set(capture["required"]) == {"name", "sport", "is_valid_event", "occurrence"}
    assert "name" in capture["schema_properties"]
    assert "sport" in capture["schema_properties"]
    assert result["name"] == "Should never be used"


def test_known_fields_removed_from_schema_and_required(monkeypatch):
    capture = {}
    _patch_run_llm(monkeypatch, _FULL_LLM_RESPONSE, capture)

    known_fields = {"name": "Poole 3k 2027", "sport": "running", "date_text": "2027-05-29", "location": "Baiter Park"}
    llm_extractor.extract_event_fields("https://example.com/event", "some markdown", known_fields=known_fields)

    # date_text is a deliberate exception (see extract_event_fields's own docstring) -
    # always re-asked even when known_fields supplied one, since JSON-LD's single date
    # can be stale/misleading for a multi-occurrence event. Every other known field is
    # removed from the schema/required entirely, as before.
    for key in ("name", "sport", "location"):
        assert key not in capture["schema_properties"]
        assert key not in capture["required"]
    assert "date_text" in capture["schema_properties"]
    assert "date_text" not in capture["required"]  # never was required in the first place
    # distances/is_valid_event/invalid_reason/occurrence* have no schema.org equivalent - always asked.
    assert "distances" in capture["schema_properties"]
    assert "is_valid_event" in capture["schema_properties"]
    assert "occurrence" in capture["schema_properties"]
    assert set(capture["required"]) == {"is_valid_event", "occurrence"}


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


# ---------------------------------------------------------------------------
# occurrence/occurrences/occurrence_weekdays - see the reported case:
# atwevents.co.uk's own JSON-LD startDate ("2023-04-30") is stale and unrelated
# to its real, current weekly sessions - a multi-occurrence event must prefer
# the LLM's own date_text over a known_fields one, unlike a plain one-off event.
# ---------------------------------------------------------------------------

def test_known_date_text_still_wins_for_a_one_off_event(monkeypatch):
    # Contrast with the multi-occurrence case below - this is the common case, and
    # must behave exactly as it always has: known_fields wins outright.
    _patch_run_llm(monkeypatch, {**_FULL_LLM_RESPONSE, "occurrence": "one_off", "date_text": "wrong LLM guess"})

    result = llm_extractor.extract_event_fields(
        "https://example.com/event", "some markdown", known_fields={"date_text": "2027-05-29"}
    )

    assert result["date_text"] == "2027-05-29"


def test_llm_date_text_wins_over_known_fields_for_a_multi_occurrence_event(monkeypatch):
    _patch_run_llm(monkeypatch, {
        **_FULL_LLM_RESPONSE,
        "occurrence": "weekly",
        "date_text": "Every Saturday, 9:00am",
        "occurrence_weekdays": ["sat"],
    })

    result = llm_extractor.extract_event_fields(
        "https://example.com/event", "some markdown", known_fields={"date_text": "2023-04-30"}
    )

    assert result["date_text"] == "Every Saturday, 9:00am"
    assert result["occurrence"] == "weekly"


def test_known_date_text_used_as_fallback_when_llm_leaves_its_own_blank(monkeypatch):
    # Better than ending up with neither, even though occurrence != one_off.
    _patch_run_llm(monkeypatch, {**_FULL_LLM_RESPONSE, "occurrence": "weekly", "date_text": None})

    result = llm_extractor.extract_event_fields(
        "https://example.com/event", "some markdown", known_fields={"date_text": "2023-04-30"}
    )

    assert result["date_text"] == "2023-04-30"


def test_occurrence_invalid_value_falls_back_to_one_off(monkeypatch):
    _patch_run_llm(monkeypatch, {**_FULL_LLM_RESPONSE, "occurrence": "fortnightly"})

    result = llm_extractor.extract_event_fields("https://example.com/event", "some markdown")

    assert result["occurrence"] == "one_off"


def test_occurrence_missing_from_response_falls_back_to_one_off(monkeypatch):
    response = dict(_FULL_LLM_RESPONSE)
    del response["occurrence"]
    _patch_run_llm(monkeypatch, response)

    result = llm_extractor.extract_event_fields("https://example.com/event", "some markdown")

    assert result["occurrence"] == "one_off"


def test_occurrence_weekdays_filters_invalid_entries(monkeypatch):
    _patch_run_llm(monkeypatch, {**_FULL_LLM_RESPONSE, "occurrence_weekdays": ["sat", "SUN", "funday", 5]})

    result = llm_extractor.extract_event_fields("https://example.com/event", "some markdown")

    assert result["occurrence_weekdays"] == ["sat", "sun"]


def test_occurrences_keeps_only_well_formed_entries(monkeypatch):
    _patch_run_llm(monkeypatch, {
        **_FULL_LLM_RESPONSE,
        "occurrence": "specific_dates",
        "occurrences": [
            {"date_text": "18th Aug 2026", "date_iso": "2026-08-18", "time_text": "06:00 PM", "time_24h": "18:00", "price_text": "£10.00"},
            {"date_text": "no iso date given"},  # missing date_iso - dropped
            "not even a dict",  # dropped
            {"date_text": "20th Aug 2026", "date_iso": "2026-08-20"},  # no time/price - fine, both null
        ],
    })

    result = llm_extractor.extract_event_fields("https://example.com/event", "some markdown")

    assert result["occurrences"] == [
        {"date_text": "18th Aug 2026", "date_iso": "2026-08-18", "time_text": "06:00 PM", "time_24h": "18:00", "price_text": "£10.00"},
        {"date_text": "20th Aug 2026", "date_iso": "2026-08-20", "time_text": None, "time_24h": None, "price_text": None},
    ]


def test_occurrences_malformed_response_becomes_empty_list(monkeypatch):
    _patch_run_llm(monkeypatch, {**_FULL_LLM_RESPONSE, "occurrences": "not a list"})

    result = llm_extractor.extract_event_fields("https://example.com/event", "some markdown")

    assert result["occurrences"] == []


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


# ---------------------------------------------------------------------------
# rewrite_summary - AI-generated alternative wording + a condensed summary of
# the summary, so a stored `summary` (see structured_data.py, which can pull
# it verbatim from a page's own JSON-LD description) doesn't have to be
# republished as another site's own copy.
# ---------------------------------------------------------------------------

def test_rewrite_summary_returns_alt_and_short_from_llm(monkeypatch):
    capture = {}
    _patch_run_llm(
        monkeypatch,
        {"summary_alt": "A reworded version of the summary.", "summary_short": "Condensed."},
        capture,
    )

    result = llm_extractor.rewrite_summary("A scenic 10k along the coast, open to all abilities.")

    assert result == {"summary_alt": "A reworded version of the summary.", "summary_short": "Condensed."}
    assert "A scenic 10k along the coast" in capture["user_prompt"]
    assert capture["required"] == []


def test_rewrite_summary_empty_input_skips_llm_call_entirely(monkeypatch):
    def should_not_be_called(*args, **kwargs):
        raise AssertionError("should not call the LLM for an empty summary")

    monkeypatch.setattr(llm_extractor, "_run_llm", should_not_be_called)

    assert llm_extractor.rewrite_summary("") == {"summary_alt": None, "summary_short": None}
    assert llm_extractor.rewrite_summary("   ") == {"summary_alt": None, "summary_short": None}


def test_rewrite_summary_failure_returns_none_values(monkeypatch):
    def failing_run_llm(*args, **kwargs):
        raise RuntimeError("LLM call failed")

    monkeypatch.setattr(llm_extractor, "_run_llm", failing_run_llm)

    assert llm_extractor.rewrite_summary("Some summary.") == {"summary_alt": None, "summary_short": None}


def test_rewrite_summary_missing_keys_in_response_become_none(monkeypatch):
    _patch_run_llm(monkeypatch, {})  # a malformed/partial response
    assert llm_extractor.rewrite_summary("Some summary.") == {"summary_alt": None, "summary_short": None}
