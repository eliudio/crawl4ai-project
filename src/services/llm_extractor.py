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
    "is_valid_event": {
        "type": "boolean",
        "description": (
            "False if this page does NOT actually describe a specific event - e.g. it's "
            "just a redirect notice ('We are redirecting you to https://example.com. "
            "Continue to https://example.com' and nothing else - confirmed in practice on "
            "runthrough.co.uk/event/running-tours-copenhagen-marathon), a dead/error page, "
            "or otherwise has no genuine event-specific content (no real name, date, or "
            "location - just navigation/boilerplate/an external link). True whenever there "
            "IS genuine event content to read, even if some individual fields below end up "
            "null because the page simply doesn't state them - a real event page missing "
            "its price or age restriction is still a valid event, this is about whether the "
            "page describes an event AT ALL, not about how complete it is.\n"
            "If false: still answer 'name'/'sport' with your best minimal, honest label "
            "(e.g. name 'No event details available', sport 'other') rather than leaving "
            "them unanswered - but never invent a plausible-sounding date/location/price "
            "that isn't actually shown; leave every other field null and distances empty."
        ),
    },
    "invalid_reason": {
        "type": ["string", "null"],
        "description": (
            "1 short sentence explaining why is_valid_event is false (e.g. 'Page is just a "
            "redirect notice to an external site, no event details shown'). Null whenever "
            "is_valid_event is true."
        ),
    },
    "summary": {"type": ["string", "null"], "description": "1-3 sentence rephrased summary of the event description"},
    "date_text": {"type": ["string", "null"], "description": "Date or date range, exactly as written on the page"},
    "location": {"type": ["string", "null"], "description": "Venue/city/postcode, as complete as the page allows"},
    "start_location": {"type": ["string", "null"], "description": "Where the event starts, if stated separately from location"},
    "finish_location": {"type": ["string", "null"], "description": "Where the event finishes, if stated separately from location"},
    "distances": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "distance_text": {"type": "string", "description": "One distance on offer, exactly as written (e.g. '5k', 'Half Marathon')"},
                "price_text": {"type": ["string", "null"], "description": "This distance's own entry price, exactly as written, if stated"},
                "distance_category": {
                    "type": ["string", "null"],
                    "description": (
                        "Standardised machine-readable label for THIS distance, snake_case, "
                        "lowercase, so the exact same physical distance always gets the exact "
                        "same label regardless of how the page phrases it or which unit it uses - "
                        "the underlying distance decides the label, never the page's own wording. "
                        "Pick EXACTLY ONE of these two forms - never mix them for the same "
                        "distance, and never invent a third form:\n"
                        "1. Round metric race distances - 5K, 10K, 15K, 20K - ALWAYS use the bare "
                        "word form 'Nk' ('5k', '10k', '15k', '20k'), with NO underscore, regardless "
                        "of whether the page calls it '5K', '5 km', '5 kilometres', or 'five km'. "
                        "Never use '5_k'/'10_k' for these four distances - that is a distinct, "
                        "wrong label from '5k'/'10k' and must not be produced.\n"
                        "2. Every other numeric distance (any distance that ISN'T one of 5K/10K/"
                        "15K/20K, or a traditional named distance below) uses a plain number + unit, "
                        "with the page's OWN number, not a conversion: '{n}_k' for kilometres (e.g. "
                        "'12_k' for '12 km', '28_k' for '28 km'), '{n}_m' for miles (e.g. '10_m' for "
                        "'10 miles' or '10 m').\n"
                        "3. Traditional distances with an established name that ISN'T a round "
                        "number use that name, converting from whichever unit the page states: "
                        "'marathon' (26.22mi/42.195km), 'half_marathon' (13.1mi/21.1km), 'ultra' "
                        "(any distance longer than a marathon with no single standard length), "
                        "'sprint_triathlon', 'olympic_triathlon' (also called 'Standard Triathlon'), "
                        "'half_ironman' (also called '70.3', 'Middle Distance Triathlon', 'Half "
                        "Ironman Distance' - always use 'half_ironman' for any of these, never "
                        "transcribe the page's own phrase like 'middle_distance_triathlon'), "
                        "'ironman' (also called '140.6', 'Full Distance Triathlon', 'Long Distance "
                        "Triathlon' - always use 'ironman', never transcribe the page's own phrase).\n"
                        "4. A junior/kids/youth race entry - e.g. 'Junior Race', 'Kids Race - Year 3', "
                        "'Kids Race - Reception & Year 1' - ALWAYS uses 'junior', regardless of which "
                        "age/year group is named. Don't create separate categories per age group - "
                        "every age-group variant of a junior/kids race on the same page is still just "
                        "'junior'.\n"
                        "- Null for anything that isn't itself a race/distance category at all (e.g. "
                        "'Workshop', 'Training Night', a charity/fundraising place name, 'Inclusive "
                        "Wave') - these aren't a distance OR a junior race, so don't force a label."
                    ),
                },
            },
            "required": ["distance_text"],
        },
        "description": (
            "Every distance option on offer, each as its own entry - most events offer "
            "more than one distance (e.g. 5k, 10k, half marathon in one race), each "
            "potentially with its own price. Still use one entry even for a single-"
            "distance event. If the page gives one overall price covering every distance "
            "rather than a price per distance, repeat that same price on each entry. "
            "Empty array if no distance is stated anywhere on the page."
        ),
    },
    "age_restriction_text": {"type": ["string", "null"], "description": "Minimum age / age category rules, if stated"},
}
_EVENT_REQUIRED = ["name", "sport", "is_valid_event"]

_EVENT_SYSTEM_PROMPT = (
    "You are a precise sports event data extractor. Extract only what is "
    "explicitly present on the page. Use null for any field that is missing "
    "or unclear. Do not invent values. Before extracting anything else, check "
    "whether the page actually describes a specific event at all (see "
    "is_valid_event) - a redirect notice, dead page, or other non-event content "
    "must be flagged as invalid rather than mined for plausible-looking details."
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
    "event_link_indices": {
        "type": "array",
        "items": {"type": "integer"},
        "description": (
            "Indices (from the numbered candidate list given to you) of links that "
            "each lead to one specific event/race's own detail page. Exclude "
            "navigation, about/contact/sponsor/results/news pages, pagination links, "
            "and other listing or category pages - only include links that point to a "
            "single event. Answer with indices, never the URLs themselves - a large "
            "listing page can have hundreds of candidates, and echoing full URLs back "
            "reliably runs out of room and produces a cut-off, unparseable response; "
            "indices into a list you already have cost a few characters each instead."
        ),
    },
    "next_page_link_index": {
        "type": ["integer", "null"],
        "description": (
            "If this page is one of several numbered/paginated pages and there is a "
            "REAL LINK to the next one (e.g. a 'Next' link, or '2', '3', ... page "
            "links that are actual <a href> links, possibly with a page number in "
            "the URL) - the index (from the numbered candidate list given to you) of "
            "that next page's link. Null if there's no such link - either because "
            "this is the only/last page, or because the next page is reached by "
            "interacting with THIS SAME url instead (a 'load more' button etc - by the "
            "time this is called, that has already been exhausted)."
        ),
    },
}
_LISTING_PAGE_REQUIRED = ["event_link_indices", "next_page_link_index"]

_LISTING_PAGE_SYSTEM_PROMPT = (
    "You are analysing a sports/race listing page. Identify which links lead to an "
    "individual event's own detail page (as opposed to navigation, informational, or "
    "other listing pages), and whether there's a REAL LINK (an actual href) to a "
    "further, distinct next-page URL. This page has already been fully loaded - any "
    "'load more' button or infinite scroll has already been exhausted before you see "
    "it, so only report next_page_link_index for a genuinely separate page reached by "
    "a real link. The candidate links are given to you as a numbered list - answer "
    "with indices into that list, never by retyping a URL, and only ever indices that "
    "were actually given to you."
)

_LOAD_MORE_SCHEMA_PROPERTIES: dict[str, Any] = {
    "interaction_type": {
        "type": "string",
        "enum": ["none", "append", "paginate"],
        "description": (
            "How more events beyond what's currently shown, if any, are reached on "
            "THIS SAME url (as opposed to following a real link elsewhere). These are "
            "two fundamentally different behaviours, not variants of one thing - pick "
            "carefully:\n"
            "'append' - a 'Load more' / 'Show more' / 'View more' button (or plain "
            "infinite scroll) that ADDS more items underneath the ones already shown - "
            "the current items stay visible and the list grows.\n"
            "'paginate' - numbered page / 'Next' controls that REPLACE the currently "
            "shown items with a different set (the previous items disappear) - no real "
            "href, just a JS-driven pager.\n"
            "'none' - this genuinely is all the events, or more is only reachable via "
            "a real link elsewhere (handled separately as next_page_url, not here)."
        ),
    },
    "load_more_selector": {
        "type": ["string", "null"],
        "description": (
            "Only meaningful when interaction_type is 'append' or 'paginate'. A CSS "
            "selector, built from real class/id attributes in the HTML, that uniquely "
            "matches EXACTLY ONE element in the whole page - the SPECIFIC interactive "
            "control to click. This is not a stylistic preference, it matters equally "
            "for both 'append' and 'paginate' and breaks the crawl silently if ignored: "
            "a click lands on whatever the selector resolves to, so a selector that "
            "matches more than one element clicks an arbitrary one of them (commonly "
            "the first in document order), not the control you meant.\n"
            "Generic classes are the usual trap: a reusable button component (e.g. "
            "class='button button-primary', 'btn', 'cta') is very often reused for "
            "unrelated controls all over the same page - per-card 'Book now'/'Details' "
            "buttons, other CTAs - not just the load-more/pager control. A class "
            "matching more than this one element is not unique even if it happens to "
            "be the class actually on the right element. Before answering, check "
            "whether the class/id you're about to use also appears elsewhere in the "
            "HTML; if it does, drill down using a more specific ancestor (e.g. a "
            "wrapping div/section with its own distinctive class or id that has no "
            "sibling repeats, combined with the element type: '.events__btns button') "
            "or an attribute unique to this one element (id, aria-label, title "
            "containing 'load more'/'next'), rather than the bare shared class alone.\n"
            "For 'paginate' specifically, don't select a wrapper containing several "
            "page-number buttons/links plus a 'next' arrow (e.g. a whole <ul>/<nav> "
            "pagination widget) - that has the same non-uniqueness problem one level "
            "up: the click lands at the CENTER of whatever selector is given, hitting "
            "an arbitrary page number instead of 'next'. Always drill down to the one "
            "element that specifically means 'next' - e.g. a <button>/<a> whose own "
            "class, aria-label, or title contains 'next' (not just a numbered "
            "page-item).\n"
            "If no selector can be made to uniquely identify the right element, return "
            "null rather than guessing with a non-unique one. Null also when "
            "interaction_type is 'none', or content appears via plain infinite scroll "
            "with no distinct element to click."
        ),
    },
}
_LOAD_MORE_REQUIRED = ["interaction_type", "load_more_selector"]

_EVENTS_SITEMAP_SCHEMA_PROPERTIES: dict[str, Any] = {
    "events_sitemap_index": {
        "type": ["integer", "null"],
        "description": (
            "Index (from the numbered list given to you) of the one sub-sitemap "
            "whose own URL suggests it lists individual event/race detail pages "
            "(e.g. a path/filename segment like 'events' or 'races') - not "
            "categories, blog posts, static pages, products, or anything else. "
            "Null if none of them look like that."
        ),
    },
}
_EVENTS_SITEMAP_REQUIRED = ["events_sitemap_index"]

_EVENTS_SITEMAP_SYSTEM_PROMPT = (
    "You are given a numbered list of sub-sitemap URLs referenced from a sitemap "
    "index (a <sitemapindex> pointing at several other sitemaps, e.g. one for "
    "events, one for categories, one for blog posts, one for static pages). Pick "
    "the one that most likely lists individual event/race detail pages, judging "
    "only from each URL's own path/filename - there is no page content to read, "
    "just the URLs themselves. Answer with its index, never the URL itself."
)

_LOAD_MORE_SYSTEM_PROMPT = (
    "You are inspecting the raw HTML of a listing page to check for ONE thing: is "
    "there a 'load more'-style affordance - a button/control that reveals more items "
    "on THIS SAME page (or without a real href), as opposed to a normal link to "
    "another page. Two fundamentally different behaviours can look superficially "
    "similar in the markup, so tell them apart carefully: a button/infinite-scroll "
    "that APPENDS more items below the current ones (interaction_type 'append'), "
    "versus numbered/'Next' controls that REPLACE the current items with a different "
    "set (interaction_type 'paginate'). If either applies and it's a distinct "
    "clickable element (not plain infinite scroll), give a CSS selector for it built "
    "from its real class/id in the HTML - never invent one - that matches this ONE "
    "element and no other element on the page (reusable button classes are commonly "
    "shared with unrelated per-item CTAs elsewhere on the same page - a shared class "
    "is not unique just because it's the class on the right element), and never "
    "select a wrapper/container that holds several clickable items (e.g. a whole "
    "pagination widget) - always the one specific control that means 'next'."
)

# Raw HTML can be large even after Firecrawl's own tag exclusion, and a
# "load more"-style button's markup (or a numbered pager's) sits after all
# the (often verbose) event cards - a plain head truncation reliably cuts it
# off before the LLM ever sees it. Cap what gets sent, but build the excerpt
# around wherever a load-more/pagination keyword actually appears rather than
# just the start of the page. Covers both interaction_type cases - "append"
# keywords (load more/show more) and "paginate" keywords (numbered/"Next"
# pagers, e.g. "pagination"-class widgets or aria-label="next page") - since
# missing either here means the LLM never even sees the affordance to classify.
_MAX_HTML_CHARS = 60_000
_LOAD_MORE_KEYWORDS = (
    "load more", "loadmore", "load-more", "show more", "view more",
    "pagination", "next page", "page-next", "rel=\"next\"",
)


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


def _call_grok(system_prompt: str, user_prompt: str, max_tokens: int) -> dict[str, Any]:
    from openai import OpenAI

    client = OpenAI(api_key=settings.grok_api_key, base_url="https://api.x.ai/v1")
    response = client.chat.completions.create(
        model=settings.grok_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


def _call_anthropic(
    system_prompt: str,
    user_prompt: str,
    schema_properties: dict[str, Any],
    required: list[str],
    tool_name: str,
    max_tokens: int,
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
        max_tokens=max_tokens,
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
    system_prompt: str,
    user_prompt: str,
    schema_properties: dict[str, Any],
    required: list[str],
    tool_name: str,
    max_tokens: int = 1200,
) -> dict[str, Any]:
    if settings.llm_provider == "anthropic":
        return _call_anthropic(system_prompt, user_prompt, schema_properties, required, tool_name, max_tokens)
    return _call_grok(system_prompt, user_prompt, max_tokens)


def extract_event_fields(
    url: str, markdown: str, known_fields: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """
    Extract structured event fields from an event detail page's markdown.

    known_fields (see structured_data.py): whatever was already read straight out of the
    page's own schema.org JSON-LD, if present - deterministic and free. Those keys are
    removed from the schema/required list sent to the LLM entirely (not merely offered as
    a hint) so it's never even asked to re-derive them, only whatever's still missing.
    distances/is_valid_event/invalid_reason never come from JSON-LD (schema.org's Event
    vocabulary has no equivalent for any of those), so those three are always asked
    regardless of what known_fields contains.
    """
    print(f"{datetime.now():%H:%M:%S} - extract_event_fields ({settings.llm_provider}): {url}")
    if not markdown.strip():
        return None

    known_fields = known_fields or {}
    schema_properties = {k: v for k, v in _EVENT_SCHEMA_PROPERTIES.items() if k not in known_fields}
    required = [k for k in _EVENT_REQUIRED if k not in known_fields]

    instructions = f"Extract from this page content:\n\n{markdown}"
    if known_fields:
        instructions += (
            "\n\nAlready read directly from this page's own structured data (schema.org "
            "JSON-LD) - treat these as correct, don't re-derive or contradict them, just "
            "fill in everything else:\n" + json.dumps(known_fields, indent=2)
        )

    user_prompt = _build_user_prompt(instructions, schema_properties, required)
    try:
        fields = _run_llm(_EVENT_SYSTEM_PROMPT, user_prompt, schema_properties, required, "extract_event")
    except Exception as e:
        print(f"{datetime.now():%H:%M:%S} - extraction failed for {url}: {type(e).__name__}: {e}")
        return None

    result = {
        key: fields.get(key)
        for key in schema_properties
        if key not in ("distances", "is_valid_event", "invalid_reason")
    }
    result["distances"] = _normalize_distances(fields.get("distances"))
    # Defaults to True (valid) rather than False on a malformed/missing response - an
    # extraction glitch shouldn't silently mark a perfectly good event invalid.
    is_valid_event = fields.get("is_valid_event")
    result["is_valid_event"] = is_valid_event if isinstance(is_valid_event, bool) else True
    invalid_reason = fields.get("invalid_reason")
    result["invalid_reason"] = str(invalid_reason).strip() if (not result["is_valid_event"] and invalid_reason) else None

    result.update(known_fields)  # structured-data fields win outright, never overwritten by the LLM
    return result


def _normalize_distances(raw: Any) -> list[dict[str, str | None]]:
    """Guards against a malformed/partial LLM response - never trust it's actually a list of well-formed entries."""
    if not isinstance(raw, list):
        return []

    distances: list[dict[str, str | None]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        distance_text = entry.get("distance_text")
        if not distance_text:
            continue
        price_text = entry.get("price_text")
        distance_category = entry.get("distance_category")
        distances.append({
            "distance_text": str(distance_text),
            "price_text": str(price_text) if price_text else None,
            # Light cleanup only (whitespace/case) - race_types.get_or_create_race_type does
            # the real slugifying/validation before this ever reaches a label.
            "distance_category": str(distance_category).strip().lower() if distance_category else None,
        })
    return distances


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
    have a 'load more' style affordance, and if so, does clicking it APPEND
    more items to what's already shown, or REPLACE them with a different page
    of items - and if it's a real clickable element (as opposed to plain
    infinite scroll), what CSS selector targets it? Takes raw `html` (not
    markdown, which strips class/id attributes) so a selector can actually be
    derived.

    These are two distinct cases handled by different code paths in
    listing_crawler.py, not variants of one mechanism: 'append' (Load
    More/infinite scroll) grows the same page and is safe to just keep
    pressing and re-checking; 'paginate' (numbered/'Next' pager, no real
    href) swaps the page's contents each press, so each page's events have to
    be extracted and unioned separately rather than only reading the last
    press's snapshot.

    Returns {"interaction_type": "none" | "append" | "paginate", "load_more_selector": str | None}.
    """
    print(f"{datetime.now():%H:%M:%S} - detect_load_more ({settings.llm_provider}): {listing_url}")
    instructions = f"Listing page URL: {listing_url}\n\nRaw HTML:\n{_load_more_excerpt(html)}"
    user_prompt = _build_user_prompt(instructions, _LOAD_MORE_SCHEMA_PROPERTIES, _LOAD_MORE_REQUIRED)
    try:
        fields = _run_llm(_LOAD_MORE_SYSTEM_PROMPT, user_prompt, _LOAD_MORE_SCHEMA_PROPERTIES, _LOAD_MORE_REQUIRED, "detect_load_more")
    except Exception as e:
        print(f"{datetime.now():%H:%M:%S} - detect_load_more failed for {listing_url}: {type(e).__name__}: {e}")
        return {"interaction_type": "none", "load_more_selector": None}

    interaction_type = fields.get("interaction_type")
    if interaction_type not in ("append", "paginate"):
        interaction_type = "none"

    return {
        "interaction_type": interaction_type,
        "load_more_selector": fields.get("load_more_selector") or None,
    }


def select_events_sitemap(sitemap_urls: list[str]) -> str | None:
    """
    Used by sitemap_crawler.py when a robots.txt-advertised sitemap turns
    out to be a <sitemapindex> (a list of OTHER sitemaps - e.g. one for
    events, one for categories, one for blog posts) rather than a direct
    list of page URLs: picks which of those sub-sitemaps is the one that
    actually lists individual events, judging only from each sub-sitemap's
    own URL (there's no page content here, just sitemap URLs). Answers by
    index into `sitemap_urls`, the same trick analyze_listing_page uses,
    though it matters less here since this list is typically small.

    Returns None if there are no candidates, the call fails, or the model
    doesn't think any of them look like an events sitemap - callers should
    treat that as "couldn't resolve a sitemap here", not "zero events".
    """
    if not sitemap_urls:
        return None

    numbered = "\n".join(f"{i}: {url}" for i, url in enumerate(sitemap_urls))
    instructions = f"Sub-sitemaps found in this sitemap index:\n{numbered}"
    user_prompt = _build_user_prompt(instructions, _EVENTS_SITEMAP_SCHEMA_PROPERTIES, _EVENTS_SITEMAP_REQUIRED)
    try:
        fields = _run_llm(
            _EVENTS_SITEMAP_SYSTEM_PROMPT, user_prompt,
            _EVENTS_SITEMAP_SCHEMA_PROPERTIES, _EVENTS_SITEMAP_REQUIRED,
            "select_events_sitemap", max_tokens=200,
        )
    except Exception as e:
        print(f"{datetime.now():%H:%M:%S} - select_events_sitemap failed: {type(e).__name__}: {e}")
        return None

    index = fields.get("events_sitemap_index")
    if isinstance(index, bool) or not isinstance(index, int):
        return None
    if 0 <= index < len(sitemap_urls):
        return sitemap_urls[index]
    return None


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

    numbered_candidates = "\n".join(f"{i}: {url}" for i, url in enumerate(candidate_links))
    instructions = (
        f"Listing page URL: {listing_url}\n\n"
        f"Listing page content:\n{markdown}\n\n"
        "Candidate same-site links found on this page, numbered - answer with these "
        "numbers, not the URLs:\n" + numbered_candidates
    )
    user_prompt = _build_user_prompt(instructions, _LISTING_PAGE_SCHEMA_PROPERTIES, _LISTING_PAGE_REQUIRED)
    # Answering by index (a few characters each) rather than echoing full URLs
    # back is what makes this cheap regardless of candidate count - a listing
    # page with hundreds of candidates (a deep "load more"/pager site fully
    # exhausted before this is ever called) used to blow straight through any
    # fixed token budget trying to retype every confirmed URL in full, cutting
    # the JSON off mid-string (seen in practice: 331 candidates truncated even
    # an 8000-token cap, discarding the whole result). This scaling is just
    # headroom for the indices array itself, not URL text.
    max_tokens = min(4000, 1000 + len(candidate_links) * 8)
    try:
        fields = _run_llm(
            _LISTING_PAGE_SYSTEM_PROMPT,
            user_prompt,
            _LISTING_PAGE_SCHEMA_PROPERTIES,
            _LISTING_PAGE_REQUIRED,
            "analyze_listing_page",
            max_tokens=max_tokens,
        )
    except Exception as e:
        print(f"{datetime.now():%H:%M:%S} - analyze_listing_page failed for {listing_url}: {type(e).__name__}: {e}")
        return {"event_urls": [], "next_page_url": None}

    def _resolve_index(index: Any) -> str | None:
        if isinstance(index, bool) or not isinstance(index, int):
            return None
        if 0 <= index < len(candidate_links):
            return candidate_links[index]
        return None

    event_urls = []
    seen: set[str] = set()
    for index in fields.get("event_link_indices") or []:
        url = _resolve_index(index)
        if url and url not in seen:
            seen.add(url)
            event_urls.append(url)
    next_page_url = _resolve_index(fields.get("next_page_link_index"))

    return {
        "event_urls": event_urls,
        "next_page_url": next_page_url,
    }
