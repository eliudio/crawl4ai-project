"""
Unit tests for llm.listing_extraction.select_events_sitemap: picks which
sub-sitemap of a sitemap index sounds like it lists individual events, by
index (see analyze_listing_page's own index-based fix for why: never echo
full URLs back). See tests/scraping/test_sitemap_crawler.py for this
exercised end-to-end through scraping/sitemap_crawler.get_event_urls.

No real LLM calls - listing_extraction's own _run_llm boundary is
monkeypatched with canned responses.
"""

from services.llm import listing_extraction


def test_select_events_sitemap_resolves_index(monkeypatch):
    sitemap_urls = [
        "https://www.runthrough.co.uk/sitemaps/events.xml",
        "https://www.runthrough.co.uk/sitemaps/event-categories.xml",
        "https://www.runthrough.co.uk/sitemaps/pages.xml",
    ]
    monkeypatch.setattr(listing_extraction, "_run_llm", lambda *a, **k: {"events_sitemap_index": 0})

    assert listing_extraction.select_events_sitemap(sitemap_urls) == sitemap_urls[0]


def test_select_events_sitemap_none_when_model_says_null(monkeypatch):
    monkeypatch.setattr(listing_extraction, "_run_llm", lambda *a, **k: {"events_sitemap_index": None})
    assert listing_extraction.select_events_sitemap(["https://example.com/a.xml"]) is None


def test_select_events_sitemap_none_for_out_of_range_index(monkeypatch):
    monkeypatch.setattr(listing_extraction, "_run_llm", lambda *a, **k: {"events_sitemap_index": 5})
    assert listing_extraction.select_events_sitemap(["https://example.com/a.xml"]) is None


def test_select_events_sitemap_empty_input_skips_llm_call(monkeypatch):
    def fail_if_called(*a, **k):
        raise AssertionError("should not call the LLM for an empty candidate list")

    monkeypatch.setattr(listing_extraction, "_run_llm", fail_if_called)
    assert listing_extraction.select_events_sitemap([]) is None


# ---------------------------------------------------------------------------
# analyze_listing_page: a deep "load more"/pager site fully exhausted before
# this is called can hand it hundreds of candidates - confirming them by
# index instead of echoing full URLs back is what keeps that cheap and avoids
# the truncated-JSON failure seen in practice (331 candidates blew through an
# 8000-token cap and discarded the whole result).
# ---------------------------------------------------------------------------

def test_analyze_listing_page_resolves_indices_not_urls(monkeypatch):
    candidates = [f"https://example.com/event/{i}" for i in range(300)]

    def fake_run_llm(system_prompt, user_prompt, schema_properties, required, tool_name, max_tokens=1200):
        # Confirming by index costs a handful of characters each, nowhere
        # near what retyping 300 full URLs would take.
        assert max_tokens < 4000
        return {"event_link_indices": [0, 5, 299], "next_page_link_index": 10}

    monkeypatch.setattr(listing_extraction, "_run_llm", fake_run_llm)

    result = listing_extraction.analyze_listing_page("https://example.com/listing", "markdown", candidates)

    assert result["event_urls"] == [candidates[0], candidates[5], candidates[299]]
    assert result["next_page_url"] == candidates[10]


def test_analyze_listing_page_ignores_invalid_indices(monkeypatch):
    candidates = ["https://example.com/event/a", "https://example.com/event/b"]

    monkeypatch.setattr(
        listing_extraction, "_run_llm",
        lambda *a, **k: {"event_link_indices": [0, 99, -1, "not-an-int"], "next_page_link_index": 99},
    )

    result = listing_extraction.analyze_listing_page("https://example.com/listing", "markdown", candidates)

    assert result["event_urls"] == [candidates[0]]
    assert result["next_page_url"] is None
