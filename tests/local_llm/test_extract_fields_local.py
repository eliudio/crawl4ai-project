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
