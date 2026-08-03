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

_EVENT_LINKS_SCHEMA_PROPERTIES: dict[str, Any] = {
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
}
_EVENT_LINKS_REQUIRED = ["event_urls"]

_EVENT_LINKS_SYSTEM_PROMPT = (
    "You are analysing a sports/race listing page to identify which links lead to "
    "an individual event's own detail page, as opposed to navigation, informational, "
    "or other listing pages. Only choose from the candidate links given to you - never "
    "invent a URL."
)


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


def identify_event_links(listing_url: str, markdown: str, candidate_links: list[str]) -> list[str]:
    """
    Given a listing page, confirm which candidate links actually lead to an
    individual event's detail page, filtering out nav/about/results/pagination
    links that a plain domain/junk-pattern filter can't distinguish. Only ever
    returns URLs drawn from `candidate_links`, to guard against the model
    inventing one.
    """
    print(f"{datetime.now():%H:%M:%S} - identify_event_links ({settings.llm_provider}): {listing_url}")
    if not candidate_links:
        return []

    instructions = (
        f"Listing page URL: {listing_url}\n\n"
        f"Listing page content:\n{markdown}\n\n"
        "Candidate same-site links found on this page:\n" + "\n".join(candidate_links)
    )
    user_prompt = _build_user_prompt(instructions, _EVENT_LINKS_SCHEMA_PROPERTIES, _EVENT_LINKS_REQUIRED)
    try:
        fields = _run_llm(
            _EVENT_LINKS_SYSTEM_PROMPT, user_prompt, _EVENT_LINKS_SCHEMA_PROPERTIES, _EVENT_LINKS_REQUIRED, "identify_event_links"
        )
    except Exception as e:
        print(f"{datetime.now():%H:%M:%S} - identify_event_links failed for {listing_url}: {type(e).__name__}: {e}")
        return []

    proposed = fields.get("event_urls") or []
    allowed = set(candidate_links)
    return [url for url in proposed if url in allowed]
