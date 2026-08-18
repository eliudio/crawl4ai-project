"""
extract_event_fields() against a real local model (Ollama - see conftest.py),
not a canned response. This is the one place in the suite that actually judges
whether the real prompt + schema, sent to a real model, produces sane
structured output - every other llm_extractor test monkeypatches _run_llm/
_call_grok directly, which can't catch a prompt regression or a schema the
model can't actually satisfy.

Assertions are structural/semantic, not exact-match: a local 7B model's exact
wording varies run to run (and would differ again from Grok/Claude's), even at
temperature=0. What must hold regardless of model is the shape of the world -
this really is a running event, it really does offer a 10k and a 5k, the price
figures are the ones written on the page, etc.
"""

from services import llm_extractor

_LYME_REGIS_10K_MARKDOWN = """
# Lyme Regis 10K

Join us for the Lyme Regis 10K on Sunday 12th July 2026, starting and finishing
on the seafront. A scenic, challenging route along the Jurassic Coast.

## Distances

- 10k - £25
- 5k Fun Run - £15

Minimum age for the 10k is 15. The 5k Fun Run has no age restriction.
"""

_REDIRECT_PAGE_MARKDOWN = """
# Page Moved

We are redirecting you to https://example.com/new-location. Continue to
https://example.com/new-location.
"""

# Real (trimmed) structure from runthrough.co.uk/event/southampton-running-festival-
# august-2027 - confirmed in practice to misattribute every price by one: the site's
# distance-tab widget shows the default-selected tab's (5K's) price with no distance
# label directly on it (the label only appears earlier, in the "Select Distance" menu
# line), while every other distance's label does appear right before its own price.
# Read as flat, position-ordered text, that leaves one price with no adjacent label
# followed by three label+price pairs - a naive "match each price to the nearest
# preceding label" reading pairs 10K with 5K's leading price and shifts everything
# after it by one, leaving 5K with no price at all.
_TAB_WIDGET_LEADING_PRICE_MARKDOWN = """
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


def test_real_event_page_extracts_sane_fields():
    fields = llm_extractor.extract_event_fields(
        "https://example.com/event/lyme-regis-10k", _LYME_REGIS_10K_MARKDOWN
    )

    assert fields is not None
    assert fields["is_valid_event"] is True
    assert "lyme regis" in (fields["name"] or "").lower()
    assert fields["sport"] is not None and "run" in fields["sport"].lower()
    assert "2026" in (fields["date_text"] or "")

    distances = fields["distances"]
    assert len(distances) == 2
    texts = " ".join(d["distance_text"].lower() for d in distances)
    assert "10k" in texts
    assert "5k" in texts
    # Whichever distance is the 10k should carry its own £25 price - not just
    # "some price got attached to something".
    ten_k = next(d for d in distances if "10k" in d["distance_text"].lower())
    assert "25" in (ten_k["price_text"] or "")


def test_tab_widget_leading_price_matches_first_menu_distance_not_shifted():
    fields = llm_extractor.extract_event_fields(
        "https://example.com/event/southampton-running-festival-august-2027",
        _TAB_WIDGET_LEADING_PRICE_MARKDOWN,
    )

    assert fields is not None
    distances = fields["distances"]
    assert len(distances) == 4

    def price_of(substring: str) -> str | None:
        d = next(d for d in distances if substring in d["distance_text"].lower())
        return d["price_text"]

    # Bug seen in practice: 5K ended up with no price at all, and every other
    # distance carried the PREVIOUS distance's price (10K got 5K's £28, Half
    # Marathon got 10K's £30) - only Junior Race, at the end of the list, was
    # unaffected. Each distance must carry its own price, not its predecessor's.
    assert price_of("5k") is not None and "28" in price_of("5k")
    assert "30" in (price_of("10k") or "")
    assert "36" in (price_of("half marathon") or "")
    assert "10" in (price_of("junior") or "")


def test_redirect_page_flagged_invalid_not_mined_for_fake_details():
    fields = llm_extractor.extract_event_fields(
        "https://example.com/event/moved", _REDIRECT_PAGE_MARKDOWN
    )

    assert fields is not None
    assert fields["is_valid_event"] is False
    # The real failure mode this guards against: a weaker model inventing a
    # plausible-looking date/location for a page that never stated one.
    assert fields["date_text"] is None
    assert fields["location"] is None
