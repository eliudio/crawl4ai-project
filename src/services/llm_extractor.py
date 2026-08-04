"""
LLM-backed extraction tasks used by the pipeline.

Provider is chosen via LLM_PROVIDER ("grok" or "anthropic") so any task here
can be swapped without touching callers — see config.py. Both providers are
asked to fill the same schema so callers never need to care which one
answered.
"""

import json
from datetime import datetime
from typing import Any

from services.config import settings

_EVENT_SCHEMA_PROPERTIES: dict[str, Any] = {
    "name": {"type": "string", "description": "Full event/race name"},
    "sport": {
        "type": "string",
        "description": "One of: running, cycling, triathlon, swimming, obstacle, other",
    },
    "summary": {"type": ["string", "null"], "description": "1-3 sentence rephrased summary of the event description"},
    "date_text": {"type": ["string", "null"], "description": "Date or date range, exactly as written on the page"},
    "location": {"type": ["string", "null"], "description": "Venue/city/postcode, as complete as the page allows"},
    "start_location": {"type": ["string", "null"], "description": "Where the event starts, if stated separately from location"},
    "finish_location": {"type": ["string", "null"], "description": "Where the event finishes, if stated separately from location"},
    "distance_text": {"type": ["string", "null"], "description": "Distance(s) on offer, exactly as written (e.g. '5k, 10k, half marathon')"},
    "price_text": {"type": ["string", "null"], "description": "Entry price(s), exactly as written"},
    "age_restriction_text": {"type": ["string", "null"], "description": "Minimum age / age category rules, if stated"},
}
_EVENT_REQUIRED = ["name", "sport"]

_EVENT_SYSTEM_PROMPT = (
    "You are a precise sports event data extractor. Extract only what is "
    "explicitly present on the page. Use null for any field that is missing "
    "or unclear. Do not invent values."
)

_LISTING_SCHEMA_PROPERTIES: dict[str, Any] = {
    "listing_urls": {
        "type": "array",
        "items": {"type": "string"},
        "description": (
            "URL(s) where upcoming event/race listings can be found, chosen only "
            "from the homepage URL and the candidate links given to you. Three "
            "cases: (1) the homepage itself lists events directly - return the "
            "homepage URL; (2) there's a single dedicated listing page (e.g. "
            "/events) - return that one URL; (3) listings are split across "
            "several sub-pages (e.g. by category) - return all of them. Return "
            "an empty array if none are apparent."
        ),
    },
}
_LISTING_REQUIRED = ["listing_urls"]

_LISTING_SYSTEM_PROMPT = (
    "You are analysing a sports/race organiser's website to find where its "
    "upcoming event listings live. Only choose from the homepage URL and the "
    "candidate links given to you - never invent a URL."
)

_LISTING_PAGE_SCHEMA_PROPERTIES: dict[str, Any] = {
    "event_urls": {
        "type": "array",
        "items": {"type": "string"},
        "description": (
            "URLs, chosen only from the candidate links given to you, that each lead "
            "to one specific event/race's own detail page. Exclude navigation, about/"
            "contact/sponsor/results/news pages, pagination links, and other listing "
            "or category pages - only include links that point to a single event."
        ),
    },
    "next_page_url": {
        "type": ["string", "null"],
        "description": (
            "If this page is one of several numbered/paginated pages and there is a "
            "REAL LINK to the next one (e.g. a 'Next' link, or '2', '3', ... page "
            "links that are actual <a href> links, possibly with a page number in "
            "the URL) - the URL of that next page, chosen only from the candidate "
            "links given to you. Null if there's no such link - either because this "
            "is the only/last page, or because the next page is reached by "
            "interacting with THIS SAME url instead (a 'load more' button etc - by the "
            "time this is called, that has already been exhausted)."
        ),
    },
}
_LISTING_PAGE_REQUIRED = ["event_urls", "next_page_url"]

_LISTING_PAGE_SYSTEM_PROMPT = (
    "You are analysing a sports/race listing page. Identify which links lead to an "
    "individual event's own detail page (as opposed to navigation, informational, or "
    "other listing pages), and whether there's a REAL LINK (an actual href) to a "
    "further, distinct next-page URL. This page has already been fully loaded - any "
    "'load more' button or infinite scroll has already been exhausted before you see "
    "it, so only report next_page_url for a genuinely separate page reached by a real "
    "link. Only choose URLs from the candidate links given to you - never invent one."
)

_LOAD_MORE_SCHEMA_PROPERTIES: dict[str, Any] = {
    "has_more_via_interaction": {
        "type": "boolean",
        "description": (
            "True if there are more events beyond what's currently shown, reachable "
            "only by interacting with THIS SAME url rather than following a real link "
            "- either a 'Load more' / 'Show more' / 'View more' button, OR numbered "
            "page / 'Next' pagination controls that update the list via JavaScript "
            "without a distinct URL to link to (no real href). False if this genuinely "
            "is all the events, or if more is only reachable via a real link elsewhere."
        ),
    },
    "load_more_selector": {
        "type": ["string", "null"],
        "description": (
            "Only meaningful when has_more_via_interaction is true. If the HTML shows a "
            "distinct clickable element for loading more (a 'Load more' / 'Show more' / "
            "'View more' button, or a numbered/'Next' control with no real href), a CSS "
            "selector built from its actual class or id attribute in the HTML that "
            "uniquely targets that element (e.g. '.load-more-btn' or '#load-more'). Null "
            "if has_more_via_interaction is false, or if more content appears via plain "
            "infinite scroll with no distinct element to click."
        ),
    },
}
_LOAD_MORE_REQUIRED = ["has_more_via_interaction", "load_more_selector"]

_LOAD_MORE_SYSTEM_PROMPT = (
    "You are inspecting the raw HTML of a listing page to check for ONE thing: is "
    "there a 'load more'-style affordance - a button/control that reveals more items "
    "on THIS SAME page (or without a real href), as opposed to a normal link to "
    "another page. If so, and it's a distinct clickable element (not plain infinite "
    "scroll), give a CSS selector for it built from its real class/id in the HTML - "
    "never invent one."
)

# Raw HTML can be large even after Firecrawl's own tag exclusion, and a
# "load more"-style button's markup sits after all the (often verbose) event
# cards - a plain head truncation reliably cuts it off before the LLM ever
# sees it. Cap what gets sent, but build the excerpt around wherever a
# load-more keyword actually appears rather than just the start of the page.
_MAX_HTML_CHARS = 60_000
_LOAD_MORE_KEYWORDS = ("load more", "loadmore", "load-more", "show more", "view more")


def _load_more_excerpt(html: str) -> str:
    if len(html) <= _MAX_HTML_CHARS:
        return html

    lower = html.lower()
    windows: list[tuple[int, int]] = [(0, 4_000)]
    search_from = 0
    while True:
        hits = [lower.find(kw, search_from) for kw in _LOAD_MORE_KEYWORDS]
        hits = [h for h in hits if h != -1]
        if not hits:
            break
        idx = min(hits)
        windows.append((max(0, idx - 300), min(len(html), idx + 1500)))
        search_from = idx + 1

    windows.sort()
    merged: list[tuple[int, int]] = []
    for start, end in windows:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    excerpt = "\n...\n".join(html[start:end] for start, end in merged)
    return excerpt[:_MAX_HTML_CHARS]


def _build_user_prompt(instructions: str, schema_properties: dict[str, Any], required: list[str]) -> str:
    schema = {"type": "object", "properties": schema_properties, "required": required}
    return f"{instructions}\n\nJSON schema:\n{json.dumps(schema, indent=2)}"


def _call_grok(system_prompt: str, user_prompt: str) -> dict[str, Any]:
    from openai import OpenAI

    client = OpenAI(api_key=settings.grok_api_key, base_url="https://api.x.ai/v1")
    response = client.chat.completions.create(
        model=settings.grok_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0,
        max_tokens=600,
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


def _call_anthropic(
    system_prompt: str, user_prompt: str, schema_properties: dict[str, Any], required: list[str], tool_name: str
) -> dict[str, Any]:
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    tool = {
        "name": tool_name,
        "description": "Record the requested structured output.",
        "input_schema": {"type": "object", "properties": schema_properties, "required": required},
    }
    response = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=600,
        system=system_prompt,
        tools=[tool],
        tool_choice={"type": "tool", "name": tool_name},
        messages=[{"role": "user", "content": user_prompt}],
    )
    for block in response.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError("Anthropic response did not include a tool_use block")


def _run_llm(
    system_prompt: str, user_prompt: str, schema_properties: dict[str, Any], required: list[str], tool_name: str
) -> dict[str, Any]:
    if settings.llm_provider == "anthropic":
        return _call_anthropic(system_prompt, user_prompt, schema_properties, required, tool_name)
    return _call_grok(system_prompt, user_prompt)


def extract_event_fields(url: str, markdown: str) -> dict[str, Any] | None:
    """Extract structured event fields from an event detail page's markdown."""
    print(f"{datetime.now():%H:%M:%S} - extract_event_fields ({settings.llm_provider}): {url}")
    if not markdown.strip():
        return None

    user_prompt = _build_user_prompt(
        f"Extract from this page content:\n\n{markdown}", _EVENT_SCHEMA_PROPERTIES, _EVENT_REQUIRED
    )
    try:
        fields = _run_llm(_EVENT_SYSTEM_PROMPT, user_prompt, _EVENT_SCHEMA_PROPERTIES, _EVENT_REQUIRED, "extract_event")
    except Exception as e:
        print(f"{datetime.now():%H:%M:%S} - extraction failed for {url}: {type(e).__name__}: {e}")
        return None

    return {key: fields.get(key) for key in _EVENT_SCHEMA_PROPERTIES}


def discover_listing_urls(homepage_url: str, markdown: str, candidate_links: list[str]) -> list[str]:
    """
    Find where an organiser's event listings live when none are known yet.
    Only ever returns URLs drawn from `candidate_links` or `homepage_url`
    itself, to guard against the model inventing a URL.
    """
    print(f"{datetime.now():%H:%M:%S} - discover_listing_urls ({settings.llm_provider}): {homepage_url}")

    instructions = (
        f"Homepage URL: {homepage_url}\n\n"
        f"Homepage content:\n{markdown}\n\n"
        "Candidate same-site links found on the homepage:\n" + "\n".join(candidate_links)
    )
    user_prompt = _build_user_prompt(instructions, _LISTING_SCHEMA_PROPERTIES, _LISTING_REQUIRED)
    try:
        fields = _run_llm(
            _LISTING_SYSTEM_PROMPT, user_prompt, _LISTING_SCHEMA_PROPERTIES, _LISTING_REQUIRED, "discover_listing_urls"
        )
    except Exception as e:
        print(f"{datetime.now():%H:%M:%S} - listing discovery failed for {homepage_url}: {type(e).__name__}: {e}")
        return []

    proposed = fields.get("listing_urls") or []
    allowed = set(candidate_links) | {homepage_url}
    return [url for url in proposed if url in allowed]


def detect_load_more(listing_url: str, html: str) -> dict[str, Any]:
    """
    Cheap probe, called BEFORE any event extraction: does this page currently
    have a 'load more' style affordance, and if it's a real clickable element
    (as opposed to plain infinite scroll), what CSS selector targets it? Takes
    raw `html` (not markdown, which strips class/id attributes) so a selector
    can actually be derived. This is what the listing crawler's press-loop
    calls on every round to decide whether/how to keep pressing - event
    extraction (analyze_listing_page) only runs once, after this reports
    has_more_via_interaction=false and the page is considered fully loaded.

    Returns {"has_more_via_interaction": bool, "load_more_selector": str | None}.
    """
    print(f"{datetime.now():%H:%M:%S} - detect_load_more ({settings.llm_provider}): {listing_url}")
    instructions = f"Listing page URL: {listing_url}\n\nRaw HTML:\n{_load_more_excerpt(html)}"
    user_prompt = _build_user_prompt(instructions, _LOAD_MORE_SCHEMA_PROPERTIES, _LOAD_MORE_REQUIRED)
    try:
        fields = _run_llm(_LOAD_MORE_SYSTEM_PROMPT, user_prompt, _LOAD_MORE_SCHEMA_PROPERTIES, _LOAD_MORE_REQUIRED, "detect_load_more")
    except Exception as e:
        print(f"{datetime.now():%H:%M:%S} - detect_load_more failed for {listing_url}: {type(e).__name__}: {e}")
        return {"has_more_via_interaction": False, "load_more_selector": None}

    return {
        "has_more_via_interaction": bool(fields.get("has_more_via_interaction")),
        "load_more_selector": fields.get("load_more_selector") or None,
    }


def analyze_listing_page(listing_url: str, markdown: str, candidate_links: list[str]) -> dict[str, Any]:
    """
    Given one FULLY LOADED listing page (any load-more/infinite-scroll has
    already been exhausted by the caller before this is ever called), confirm
    which candidate links actually lead to an individual event's detail page
    (filtering out nav/about/results links a plain domain/junk-pattern filter
    can't distinguish), and detect a real next-page link (numbered pagination
    with an actual href).

    Returns {"event_urls": [...], "next_page_url": str | None}.
    URLs are only ever drawn from `candidate_links`, to guard against the model
    inventing one.
    """
    print(f"{datetime.now():%H:%M:%S} - analyze_listing_page ({settings.llm_provider}): {listing_url}")
    if not candidate_links:
        return {"event_urls": [], "next_page_url": None}

    instructions = (
        f"Listing page URL: {listing_url}\n\n"
        f"Listing page content:\n{markdown}\n\n"
        "Candidate same-site links found on this page:\n" + "\n".join(candidate_links)
    )
    user_prompt = _build_user_prompt(instructions, _LISTING_PAGE_SCHEMA_PROPERTIES, _LISTING_PAGE_REQUIRED)
    try:
        fields = _run_llm(
            _LISTING_PAGE_SYSTEM_PROMPT, user_prompt, _LISTING_PAGE_SCHEMA_PROPERTIES, _LISTING_PAGE_REQUIRED, "analyze_listing_page"
        )
    except Exception as e:
        print(f"{datetime.now():%H:%M:%S} - analyze_listing_page failed for {listing_url}: {type(e).__name__}: {e}")
        return {"event_urls": [], "next_page_url": None}

    allowed = set(candidate_links)
    event_urls = [url for url in (fields.get("event_urls") or []) if url in allowed]
    next_page_url = fields.get("next_page_url")
    if next_page_url not in allowed:
        next_page_url = None

    return {
        "event_urls": event_urls,
        "next_page_url": next_page_url,
    }
