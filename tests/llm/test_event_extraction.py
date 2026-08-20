"""
Unit tests for event_extraction.extract_event_fields's known_fields handling (see
structured_data.py) - no real LLM/network calls, _run_llm is monkeypatched with a
canned response, same style as test_sitemap_crawler.py's other llm.event_extraction tests.
"""

from services.llm import event_extraction


def _patch_run_llm(monkeypatch, response: dict, capture: dict | None = None):
    def fake_run_llm(system_prompt, user_prompt, schema_properties, required, tool_name, max_tokens=1200):
        if capture is not None:
            capture["schema_properties"] = schema_properties
            capture["required"] = required
            capture["user_prompt"] = user_prompt
        return response

    monkeypatch.setattr(event_extraction, "_run_llm", fake_run_llm)


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
    "registration_status": "unknown",
    "registration_text": None,
    "registration_opens_date_iso": None,
    "registration_opens_time_24h": None,
    "registration_closes_date_iso": None,
    "registration_closes_time_24h": None,
    "lifecycle_status": "scheduled",
    "lifecycle_text": None,
}


def test_no_known_fields_asks_llm_for_everything(monkeypatch):
    capture = {}
    _patch_run_llm(monkeypatch, _FULL_LLM_RESPONSE, capture)

    result = event_extraction.extract_event_fields("https://example.com/event", "some markdown")

    assert set(capture["required"]) == {
        "name", "sport", "is_valid_event", "occurrence", "registration_status", "lifecycle_status",
    }
    assert "name" in capture["schema_properties"]
    assert "sport" in capture["schema_properties"]
    assert result["name"] == "Should never be used"


def test_known_fields_removed_from_schema_and_required(monkeypatch):
    capture = {}
    _patch_run_llm(monkeypatch, _FULL_LLM_RESPONSE, capture)

    known_fields = {"name": "Poole 3k 2027", "sport": "running", "date_text": "2027-05-29", "location": "Baiter Park"}
    event_extraction.extract_event_fields("https://example.com/event", "some markdown", known_fields=known_fields)

    # date_text is a deliberate exception (see extract_event_fields's own docstring) -
    # always re-asked even when known_fields supplied one, since JSON-LD's single date
    # can be stale/misleading for a multi-occurrence event. Every other known field is
    # removed from the schema/required entirely, as before.
    for key in ("name", "sport", "location"):
        assert key not in capture["schema_properties"]
        assert key not in capture["required"]
    assert "date_text" in capture["schema_properties"]
    assert "date_text" not in capture["required"]  # never was required in the first place
    # distances/is_valid_event/invalid_reason/occurrence*/registration_*/lifecycle_* have no
    # schema.org equivalent - always asked.
    assert "distances" in capture["schema_properties"]
    assert "is_valid_event" in capture["schema_properties"]
    assert "occurrence" in capture["schema_properties"]
    assert set(capture["required"]) == {"is_valid_event", "occurrence", "registration_status", "lifecycle_status"}


def test_known_fields_win_in_the_final_merged_result(monkeypatch):
    _patch_run_llm(monkeypatch, _FULL_LLM_RESPONSE)

    known_fields = {"name": "Poole 3k 2027", "sport": "running", "date_text": "2027-05-29", "location": "Baiter Park"}
    result = event_extraction.extract_event_fields("https://example.com/event", "some markdown", known_fields=known_fields)

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

    result = event_extraction.extract_event_fields(
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

    result = event_extraction.extract_event_fields(
        "https://example.com/event", "some markdown", known_fields={"date_text": "2023-04-30"}
    )

    assert result["date_text"] == "Every Saturday, 9:00am"
    assert result["occurrence"] == "weekly"


def test_known_date_text_used_as_fallback_when_llm_leaves_its_own_blank(monkeypatch):
    # Better than ending up with neither, even though occurrence != one_off.
    _patch_run_llm(monkeypatch, {**_FULL_LLM_RESPONSE, "occurrence": "weekly", "date_text": None})

    result = event_extraction.extract_event_fields(
        "https://example.com/event", "some markdown", known_fields={"date_text": "2023-04-30"}
    )

    assert result["date_text"] == "2023-04-30"


def test_occurrence_invalid_value_falls_back_to_one_off(monkeypatch):
    _patch_run_llm(monkeypatch, {**_FULL_LLM_RESPONSE, "occurrence": "fortnightly"})

    result = event_extraction.extract_event_fields("https://example.com/event", "some markdown")

    assert result["occurrence"] == "one_off"


def test_occurrence_missing_from_response_falls_back_to_one_off(monkeypatch):
    response = dict(_FULL_LLM_RESPONSE)
    del response["occurrence"]
    _patch_run_llm(monkeypatch, response)

    result = event_extraction.extract_event_fields("https://example.com/event", "some markdown")

    assert result["occurrence"] == "one_off"


def test_occurrence_weekdays_filters_invalid_entries(monkeypatch):
    _patch_run_llm(monkeypatch, {**_FULL_LLM_RESPONSE, "occurrence_weekdays": ["sat", "SUN", "funday", 5]})

    result = event_extraction.extract_event_fields("https://example.com/event", "some markdown")

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

    result = event_extraction.extract_event_fields("https://example.com/event", "some markdown")

    assert result["occurrences"] == [
        {"date_text": "18th Aug 2026", "date_iso": "2026-08-18", "time_text": "06:00 PM", "time_24h": "18:00", "price_text": "£10.00"},
        {"date_text": "20th Aug 2026", "date_iso": "2026-08-20", "time_text": None, "time_24h": None, "price_text": None},
    ]


def test_occurrences_malformed_response_becomes_empty_list(monkeypatch):
    _patch_run_llm(monkeypatch, {**_FULL_LLM_RESPONSE, "occurrences": "not a list"})

    result = event_extraction.extract_event_fields("https://example.com/event", "some markdown")

    assert result["occurrences"] == []


# ---------------------------------------------------------------------------
# registration_status/registration_text/registration_opens_*/registration_closes_* -
# see the reported case: zigzagrunning.co.uk's Two Hundred Miles Challenge page
# states outright "Registration is Closed", no opening/closing date given at all.
# ---------------------------------------------------------------------------

def test_registration_closed_passes_through_raw_text(monkeypatch):
    _patch_run_llm(monkeypatch, {
        **_FULL_LLM_RESPONSE,
        "registration_status": "closed", "registration_text": "Registration is Closed",
    })

    result = event_extraction.extract_event_fields("https://example.com/event", "some markdown")

    assert result["registration_status"] == "closed"
    assert result["registration_text"] == "Registration is Closed"
    assert result["registration_opens_date_iso"] is None
    assert result["registration_closes_date_iso"] is None


def test_registration_status_invalid_value_falls_back_to_unknown(monkeypatch):
    _patch_run_llm(monkeypatch, {**_FULL_LLM_RESPONSE, "registration_status": "sold_out"})

    result = event_extraction.extract_event_fields("https://example.com/event", "some markdown")

    assert result["registration_status"] == "unknown"


def test_registration_status_missing_from_response_falls_back_to_unknown(monkeypatch):
    response = dict(_FULL_LLM_RESPONSE)
    del response["registration_status"]
    _patch_run_llm(monkeypatch, response)

    result = event_extraction.extract_event_fields("https://example.com/event", "some markdown")

    assert result["registration_status"] == "unknown"


def test_registration_open_dates_pass_through(monkeypatch):
    _patch_run_llm(monkeypatch, {
        **_FULL_LLM_RESPONSE,
        "registration_status": "open",
        "registration_opens_date_iso": "2026-03-01", "registration_opens_time_24h": "09:00",
        "registration_closes_date_iso": "2026-06-30", "registration_closes_time_24h": "23:59",
    })

    result = event_extraction.extract_event_fields("https://example.com/event", "some markdown")

    assert result["registration_status"] == "open"
    assert result["registration_opens_date_iso"] == "2026-03-01"
    assert result["registration_opens_time_24h"] == "09:00"
    assert result["registration_closes_date_iso"] == "2026-06-30"
    assert result["registration_closes_time_24h"] == "23:59"


# ---------------------------------------------------------------------------
# lifecycle_status/lifecycle_text - deliberately independent of registration_status
# (see EventLifecycle's own docstring): a cancelled event doesn't imply anything about
# whether registration was open/closed, and vice versa.
# ---------------------------------------------------------------------------

def test_lifecycle_cancelled_passes_through_raw_text(monkeypatch):
    _patch_run_llm(monkeypatch, {
        **_FULL_LLM_RESPONSE,
        "lifecycle_status": "cancelled", "lifecycle_text": "Cancelled due to adverse weather",
    })

    result = event_extraction.extract_event_fields("https://example.com/event", "some markdown")

    assert result["lifecycle_status"] == "cancelled"
    assert result["lifecycle_text"] == "Cancelled due to adverse weather"


def test_lifecycle_postponed_passes_through_raw_text(monkeypatch):
    _patch_run_llm(monkeypatch, {
        **_FULL_LLM_RESPONSE,
        "lifecycle_status": "postponed", "lifecycle_text": "Postponed to 12 September 2026",
    })

    result = event_extraction.extract_event_fields("https://example.com/event", "some markdown")

    assert result["lifecycle_status"] == "postponed"
    assert result["lifecycle_text"] == "Postponed to 12 September 2026"


def test_lifecycle_status_invalid_value_falls_back_to_scheduled(monkeypatch):
    _patch_run_llm(monkeypatch, {**_FULL_LLM_RESPONSE, "lifecycle_status": "delayed"})

    result = event_extraction.extract_event_fields("https://example.com/event", "some markdown")

    assert result["lifecycle_status"] == "scheduled"


def test_lifecycle_status_missing_from_response_falls_back_to_scheduled(monkeypatch):
    response = dict(_FULL_LLM_RESPONSE)
    del response["lifecycle_status"]
    _patch_run_llm(monkeypatch, response)

    result = event_extraction.extract_event_fields("https://example.com/event", "some markdown")

    assert result["lifecycle_status"] == "scheduled"


def test_known_fields_mentioned_in_the_prompt_sent_to_the_llm(monkeypatch):
    capture = {}
    _patch_run_llm(monkeypatch, _FULL_LLM_RESPONSE, capture)

    known_fields = {"name": "Poole 3k 2027"}
    event_extraction.extract_event_fields("https://example.com/event", "some markdown", known_fields=known_fields)

    assert "Poole 3k 2027" in capture["user_prompt"]
    assert "structured data" in capture["user_prompt"].lower()


def test_empty_markdown_returns_none_regardless_of_known_fields():
    assert event_extraction.extract_event_fields("https://example.com/event", "   ", known_fields={"name": "X"}) is None


def test_extraction_failure_returns_none(monkeypatch):
    def failing_run_llm(*args, **kwargs):
        raise RuntimeError("LLM call failed")

    monkeypatch.setattr(event_extraction, "_run_llm", failing_run_llm)

    assert event_extraction.extract_event_fields("https://example.com/event", "some markdown") is None


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

    result = event_extraction.rewrite_summary("A scenic 10k along the coast, open to all abilities.")

    assert result == {"summary_alt": "A reworded version of the summary.", "summary_short": "Condensed."}
    assert "A scenic 10k along the coast" in capture["user_prompt"]
    assert capture["required"] == []


def test_rewrite_summary_empty_input_skips_llm_call_entirely(monkeypatch):
    def should_not_be_called(*args, **kwargs):
        raise AssertionError("should not call the LLM for an empty summary")

    monkeypatch.setattr(event_extraction, "_run_llm", should_not_be_called)

    assert event_extraction.rewrite_summary("") == {"summary_alt": None, "summary_short": None}
    assert event_extraction.rewrite_summary("   ") == {"summary_alt": None, "summary_short": None}


def test_rewrite_summary_failure_returns_none_values(monkeypatch):
    def failing_run_llm(*args, **kwargs):
        raise RuntimeError("LLM call failed")

    monkeypatch.setattr(event_extraction, "_run_llm", failing_run_llm)

    assert event_extraction.rewrite_summary("Some summary.") == {"summary_alt": None, "summary_short": None}


def test_rewrite_summary_missing_keys_in_response_become_none(monkeypatch):
    _patch_run_llm(monkeypatch, {})  # a malformed/partial response


# ---------------------------------------------------------------------------
# _fix_tab_widget_leading_price - see the reported case: runthrough.co.uk's
# Southampton Running Festival page had 5K's price left unattributed and every
# other distance's price shifted onto its predecessor (10K got 5K's £28, Half
# Marathon got 10K's £30) because the page's distance-tab widget shows the
# default-selected tab's price with no label right next to it at all - only the
# tab menu line above names it. A prompt-only fix wasn't reliable enough (see
# tests/llm/local/test_extract_fields_local.py), so the ambiguity is now
# resolved deterministically before the markdown ever reaches the LLM.
# ---------------------------------------------------------------------------

_TAB_WIDGET_MARKDOWN = """
# Southampton Running Festival

Race Distance
Select Distance 5K  10K  Half Marathon  Junior Race

## Race Entry Summary

Here are the races available for RunThrough UK August 2027

£28
10K
£30
Half Marathon
£36
Junior Race
£10
"""


def test_fix_tab_widget_leading_price_inserts_missing_first_label():
    fixed = event_extraction._fix_tab_widget_leading_price(_TAB_WIDGET_MARKDOWN)

    lines = [line.strip() for line in fixed.splitlines() if line.strip()]
    price_idx = lines.index("£28")
    assert lines[price_idx - 1] == "5K"
    # Nothing else in the list should have moved.
    assert lines[price_idx - 1:price_idx + 7] == [
        "5K", "£28", "10K", "£30", "Half Marathon", "£36", "Junior Race", "£10",
    ]


def test_fix_tab_widget_leading_price_noop_when_already_labeled():
    already_fine = """
Select Distance 5K  10K

5K
£28
10K
£30
"""
    assert event_extraction._fix_tab_widget_leading_price(already_fine) == already_fine


def test_fix_tab_widget_leading_price_noop_without_menu_line():
    plain = """
## Distances

- 10k - £25
- 5k Fun Run - £15
"""
    assert event_extraction._fix_tab_widget_leading_price(plain) == plain


def test_fix_tab_widget_leading_price_noop_when_order_doesnt_match_menu():
    # A leading price followed by unrelated content - not this widget's shape at all,
    # so nothing should be invented.
    unrelated = """
Select Distance 5K  10K

£28
Some unrelated paragraph mentioning 10K in passing.
"""
    assert event_extraction._fix_tab_widget_leading_price(unrelated) == unrelated


def test_extract_event_fields_applies_tab_widget_fix_before_prompting_llm(monkeypatch):
    capture = {}
    _patch_run_llm(monkeypatch, _FULL_LLM_RESPONSE, capture)

    event_extraction.extract_event_fields("https://example.com/event", _TAB_WIDGET_MARKDOWN)

    assert "5K\n£28" in capture["user_prompt"]
    assert event_extraction.rewrite_summary("Some summary.") == {"summary_alt": None, "summary_short": None}
