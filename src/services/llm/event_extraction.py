"""
LLM-backed extraction of one event detail page's structured fields, plus the
summary-rewrite task that runs on top of the result - see client.py for the
provider plumbing both go through.
"""

import json
import re
from datetime import datetime
from typing import Any

from services.common.config import settings

from .client import _build_user_prompt, _run_llm

__all__ = ["extract_event_fields", "rewrite_summary"]

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
            "page describes an event AT ALL, not about how complete it is. True also for a "
            "cancelled or postponed event (see lifecycle_status below) - a page announcing "
            "'This event has been cancelled' is still a genuine, well-formed description of "
            "a real event, just one that isn't happening; it is NOT the same situation as a "
            "redirect/dead page with no event content at all. Never set this False just "
            "because the event was called off.\n"
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
    "occurrence": {
        "type": "string",
        "enum": ["one_off", "daily", "weekly", "monthly", "yearly", "specific_dates"],
        "description": (
            "How this event recurs. 'one_off' (the default/most common case): happens once, "
            "on a single date. 'specific_dates': the page individually lists/tickets several "
            "distinct dates for the SAME event - confirmed in practice: atwevents.co.uk's open "
            "water swimming page sells a separate ticket per session date ('TUESDAY 18/8', "
            "'THURSDAY 20/8', ...), each its own specific calendar date. 'daily'/'weekly'/"
            "'monthly'/'yearly': a standing recurring rule stated on the page with NO specific "
            "dates listed anywhere at all - confirmed in practice: parkrun's 'every Saturday, "
            "9am', forever, with no page ever listing individual future dates.\n"
            "Use 'specific_dates' whenever the page actually lists individual dates, even if "
            "its own prose also uses recurrence language like 'weekly' - only use daily/weekly/"
            "monthly/yearly when there truly is no list of specific dates to read, just a "
            "stated rule. Do not confuse this with `distances` above: a page offering several "
            "DISTANCE options on the SAME single date is still 'one_off' (each distance is its "
            "own entry in `distances`, not evidence of recurrence) - only classify as "
            "'specific_dates'/recurring when what varies between listed entries is the DATE "
            "itself, not the distance."
        ),
    },
    "occurrences": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "date_text": {"type": "string", "description": "One specific date this event happens on, exactly as written (e.g. '18th Aug 2026')"},
                "date_iso": {
                    "type": "string",
                    "description": (
                        "The date above, converted to ISO YYYY-MM-DD - use the year actually "
                        "implied by the page's own context (e.g. a date shown with no year, on "
                        "a page whose other dates are clearly in 2026, should resolve to 2026)."
                    ),
                },
                "time_text": {"type": ["string", "null"], "description": "That date's own start time, exactly as written, if stated separately (e.g. '06:00 PM')"},
                "time_24h": {"type": ["string", "null"], "description": "That time, converted to 24h HH:MM. Null if no time was stated for this date - never guess/default one."},
                "price_text": {"type": ["string", "null"], "description": "That date's own price, exactly as written, if it differs per date. Null if one overall price covers every date."},
            },
            "required": ["date_text", "date_iso"],
        },
        "description": (
            "Only filled in when occurrence is 'specific_dates' (or, for a plain 'one_off' "
            "event, this may still hold that single date as its only entry). Each entry is one "
            "individually-listed date this event happens on - NOT a distance/ticket-tier option "
            "on the same date; a page offering several DISTANCES on one date belongs in "
            "`distances` above instead, not here. Empty for occurrence 'daily'/'weekly'/"
            "'monthly'/'yearly' (see occurrence_weekdays/occurrence_time below instead), and "
            "whenever is_valid_event is false."
        ),
    },
    "occurrence_weekdays": {
        "type": "array",
        "items": {"type": "string", "enum": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]},
        "description": (
            "Only for occurrence 'daily'/'weekly'/'monthly'/'yearly' - which weekday(s) this "
            "standing rule falls on (e.g. parkrun -> ['sat']). A rule can name more than one "
            "weekday (e.g. 'Tuesday and Thursday evenings' -> ['tue', 'thu']). Empty for "
            "'one_off'/'specific_dates'."
        ),
    },
    "occurrence_time": {
        "type": ["string", "null"],
        "description": (
            "Only for occurrence 'daily'/'weekly'/'monthly'/'yearly' - the stated time of day, "
            "24h HH:MM (e.g. '09:00' for 'every Saturday at 9am'). Null if no time is stated, "
            "or occurrence is 'one_off'/'specific_dates'."
        ),
    },
    "occurrence_starts_on": {
        "type": ["string", "null"],
        "description": (
            "Only for a recurring rule with a stated start of its season/window (e.g. 'from "
            "Easter' or 'from May' -> that date, ISO YYYY-MM-DD, using the year implied by the "
            "page's own context). Null if the rule runs indefinitely/all year, or occurrence is "
            "'one_off'/'specific_dates'."
        ),
    },
    "occurrence_ends_on": {
        "type": ["string", "null"],
        "description": (
            "Only for a recurring rule with a stated end of its season/window (e.g. 'until "
            "end-September' -> that date, ISO YYYY-MM-DD). Null if indefinite, or occurrence is "
            "'one_off'/'specific_dates'."
        ),
    },
    "registration_status": {
        "type": "string",
        "enum": ["not_required", "open", "closed", "unknown"],
        "description": (
            "Whether taking part needs sign-up/entry/a ticket at all, and if so, whether "
            "that's currently open. 'not_required': no entry of any kind is needed - "
            "confirmed in practice: parkrun, just turn up. 'open': the page states entries "
            "are open, or is a future event with nothing suggesting otherwise. 'closed': "
            "the page states entries/registration have closed or sold out - confirmed in "
            "practice: zigzagrunning.co.uk's Two Hundred Miles Challenge page states "
            "outright 'Registration is Closed', with no other detail given at all. "
            "'unknown': entry is clearly needed but the page never actually states whether "
            "it's open or closed right now - the safe default, never guess 'open' just "
            "because nothing was said."
        ),
    },
    "registration_text": {
        "type": ["string", "null"],
        "description": (
            "The page's own wording about registration/entry opening, closing, or current "
            "status, exactly as written (e.g. 'Registration is Closed', 'Entries open 9am "
            "1 March 2026'). Null whenever registration_status is 'not_required', or "
            "nothing at all is stated about it."
        ),
    },
    "registration_opens_date_iso": {
        "type": ["string", "null"],
        "description": (
            "The date entries/registration open, ISO YYYY-MM-DD, if explicitly stated - "
            "using the year implied by the page's own context if none is given. Null if "
            "not stated."
        ),
    },
    "registration_opens_time_24h": {
        "type": ["string", "null"],
        "description": "The time entries/registration open, 24h HH:MM, if stated separately from the date. Null if no time was stated - never guess/default one.",
    },
    "registration_closes_date_iso": {
        "type": ["string", "null"],
        "description": (
            "The date entries/registration close, ISO YYYY-MM-DD, if explicitly stated - "
            "using the year implied by the page's own context if none is given. Null if "
            "not stated."
        ),
    },
    "registration_closes_time_24h": {
        "type": ["string", "null"],
        "description": "The time entries/registration close, 24h HH:MM, if stated separately from the date. Null if no time was stated - never guess/default one.",
    },
    "lifecycle_status": {
        "type": "string",
        "enum": ["scheduled", "cancelled", "postponed"],
        "description": (
            "Whether the event itself is still going ahead as planned - separate from "
            "registration_status above (an event can be sold out and still on, or cancelled "
            "after entries were already closed - these are independent facts, don't infer "
            "one from the other). 'scheduled' (the default/most common case): nothing on the "
            "page suggests otherwise. 'cancelled': the page states the event has been called "
            "off/cancelled. 'postponed': the page states the event has been moved to a later "
            "date (a plain date change with no cancellation mentioned - if the page instead "
            "just states an updated/corrected date with no mention of an earlier cancelled "
            "one, that's just this event's real date, use 'scheduled' and date_text as "
            "normal, not 'postponed')."
        ),
    },
    "lifecycle_text": {
        "type": ["string", "null"],
        "description": (
            "The page's own wording about a cancellation/postponement, exactly as written "
            "(e.g. 'Cancelled due to adverse weather', 'Postponed to 12 September 2026'). "
            "Null whenever lifecycle_status is 'scheduled'."
        ),
    },
}
_EVENT_REQUIRED = [
    "name", "sport", "is_valid_event", "occurrence", "registration_status", "lifecycle_status",
]

_EVENT_SYSTEM_PROMPT = (
    "You are a precise sports event data extractor. Extract only what is "
    "explicitly present on the page. Use null for any field that is missing "
    "or unclear. Do not invent values. Before extracting anything else, check "
    "whether the page actually describes a specific event at all (see "
    "is_valid_event) - a redirect notice, dead page, or other non-event content "
    "must be flagged as invalid rather than mined for plausible-looking details. "
    "Also judge carefully whether this event happens once, on several individually "
    "listed dates, or on a standing recurring schedule with no specific dates given at "
    "all (see occurrence/occurrences/occurrence_weekdays) - don't default to 'one_off' "
    "without checking, but equally don't call something recurring just because its "
    "prose uses a word like 'weekly' if the page actually lists specific dates."
)

_SUMMARY_REWRITE_SCHEMA_PROPERTIES: dict[str, Any] = {
    "summary_alt": {
        "type": ["string", "null"],
        "description": (
            "The summary below, reworded in genuinely different phrasing and sentence "
            "structure while preserving the same facts - not a close paraphrase (a few "
            "synonyms swapped in) and not the organiser's own wording. Exists so a stored "
            "summary is never just another site's copy reproduced verbatim (see e.g. "
            "scraping/structured_data.py, which can pull `summary` straight from a page's "
            "own schema.org description). Null if the input summary is empty."
        ),
    },
    "summary_short": {
        "type": ["string", "null"],
        "description": (
            "A single condensed sentence summarising the summary below - the shortest "
            "version that still captures what the event fundamentally is. Null if the "
            "input summary is empty."
        ),
    },
}
_SUMMARY_REWRITE_REQUIRED: list[str] = []

_SUMMARY_REWRITE_SYSTEM_PROMPT = (
    "You are an editor rewriting a short sports event summary. Produce an alternative "
    "version in genuinely original wording and sentence structure - not a close "
    "paraphrase, and not the source's own phrasing - that preserves the same facts, "
    "plus a further single-sentence condensed summary of it. Never invent facts that "
    "aren't in the original summary given to you."
)

_PRICE_LINE_RE = re.compile(r"^\s*[£$€]\s?\d")


def _fix_tab_widget_leading_price(markdown: str) -> str:
    """
    Deterministic pre-LLM markdown fix for a distance-tab widget rendering pattern -
    confirmed in practice on runthrough.co.uk (e.g. the Southampton Running Festival page):
    a "Select Distance 5K  10K  Half Marathon  Junior Race" menu line, followed by a flat
    list where the *first* (default-selected) tab's price has no distance label directly on
    it at all - only the other tabs' labels appear right before their own price ("£28 / 10K /
    £30 / Half Marathon / £36 / Junior Race / £10"). Read as flat text, that's one label-less
    price followed by three clean label+price pairs - pairing each price with its nearest
    preceding label shifts every price back by one distance and leaves the first with none.
    A prompt-only fix ("watch out for this pattern") turned out not to be reliable enough
    against a smaller model (confirmed in practice against qwen2.5:7b - see
    tests/llm/local/test_extract_fields_local.py), so the ambiguity is removed from the text
    itself instead, before it ever reaches the LLM.

    A no-op whenever the pattern doesn't actually match: no "Select Distance" menu line, the
    leading price already has its own label, or the labels after it don't follow the menu's
    own order (too different a layout to safely guess at).
    """
    lines = markdown.splitlines()

    menu_idx = next(
        (i for i, line in enumerate(lines) if re.match(r"^\s*select distance\s+\S", line, re.IGNORECASE)),
        None,
    )
    if menu_idx is None:
        return markdown

    menu_text = re.sub(r"^\s*select distance\s+", "", lines[menu_idx], flags=re.IGNORECASE).strip()
    # Labels are separated by runs of 2+ spaces in practice - a single space is reserved for
    # a multi-word label like "Half Marathon"/"Junior Race" itself.
    menu_labels = [label.strip() for label in re.split(r" {2,}", menu_text) if label.strip()]
    if len(menu_labels) < 2:
        return markdown

    # The first price-like line after the menu - the candidate "leading, label-less price".
    price_idx = next(
        (i for i in range(menu_idx + 1, len(lines)) if _PRICE_LINE_RE.match(lines[i])),
        None,
    )
    if price_idx is None:
        return markdown

    preceding = next((lines[i].strip() for i in range(price_idx - 1, menu_idx, -1) if lines[i].strip()), "")
    if preceding.lower() == menu_labels[0].lower():
        return markdown  # already labeled - not the pattern this guards against

    # Confirm the rest of the list actually follows the menu's own order (label, price,
    # label, price, ...) before touching anything - a coincidental leading price elsewhere
    # on the page shouldn't get a label invented for it.
    expected = menu_labels[1:]
    seen = 0
    for line in lines[price_idx + 1:]:
        item = line.strip()
        if not item:
            continue
        if seen < len(expected) and item.lower() == expected[seen].lower():
            seen += 1
            if seen == len(expected):
                break
    if seen < len(expected):
        return markdown

    fixed_lines = lines[:price_idx] + [menu_labels[0]] + lines[price_idx:]
    return "\n".join(fixed_lines)


def extract_event_fields(
    url: str, markdown: str, known_fields: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """
    Extract structured event fields from an event detail page's markdown.

    known_fields (see scraping/structured_data.py): whatever was already read straight out
    of the page's own schema.org JSON-LD, if present - deterministic and free. Those keys
    are removed from the schema/required list sent to the LLM entirely (not merely offered
    as a hint) so it's never even asked to re-derive them, only whatever's still missing.
    distances/is_valid_event/invalid_reason/occurrence* never come from JSON-LD (schema.org's
    Event vocabulary has no equivalent for any of those), so those are always asked
    regardless of what known_fields contains.

    date_text is a deliberate exception to "known_fields is never re-asked": it's always
    included in the schema sent to the LLM even when JSON-LD supplied one, because JSON-LD's
    startDate/endDate is only ever a single date and can be stale/misleading for a multi-
    occurrence event - confirmed in practice: atwevents.co.uk's own JSON-LD states a leftover
    "2023-04-30" placeholder, completely unrelated to its real, current weekly Aug-2026
    sessions. See the occurrence-aware override below the LLM call.
    """
    print(f"{datetime.now():%H:%M:%S} - extract_event_fields ({settings.llm_provider}): {url}")
    if not markdown.strip():
        return None

    markdown = _fix_tab_widget_leading_price(markdown)

    known_fields = known_fields or {}
    schema_properties = {
        k: v for k, v in _EVENT_SCHEMA_PROPERTIES.items() if k not in known_fields or k == "date_text"
    }
    required = [k for k in _EVENT_REQUIRED if k not in known_fields or k == "date_text"]

    instructions = f"Extract from this page content:\n\n{markdown}"
    if known_fields:
        instructions += (
            "\n\nAlready read directly from this page's own structured data (schema.org "
            "JSON-LD) - treat these as correct, don't re-derive or contradict them, just "
            "fill in everything else:\n" + json.dumps(known_fields, indent=2)
        )
        if "date_text" in known_fields:
            instructions += (
                "\n\nException: still answer date_text yourself from the page content, even "
                "though one was also read from structured data above - that structured-data "
                "date is only a single date and may be stale or incomplete for an event that "
                "recurs or has several listed dates (see occurrence/occurrences)."
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
        if key
        not in (
            "distances", "is_valid_event", "invalid_reason",
            "occurrence", "occurrences", "occurrence_weekdays",
            "registration_status", "lifecycle_status",
        )
    }
    result["distances"] = _normalize_distances(fields.get("distances"))
    # Defaults to True (valid) rather than False on a malformed/missing response - an
    # extraction glitch shouldn't silently mark a perfectly good event invalid.
    is_valid_event = fields.get("is_valid_event")
    result["is_valid_event"] = is_valid_event if isinstance(is_valid_event, bool) else True
    invalid_reason = fields.get("invalid_reason")
    result["invalid_reason"] = str(invalid_reason).strip() if (not result["is_valid_event"] and invalid_reason) else None

    result["occurrence"] = _normalize_occurrence(fields.get("occurrence"))
    result["occurrences"] = _normalize_occurrences(fields.get("occurrences"))
    result["occurrence_weekdays"] = _normalize_occurrence_weekdays(fields.get("occurrence_weekdays"))
    result["registration_status"] = _normalize_registration_status(fields.get("registration_status"))
    result["lifecycle_status"] = _normalize_lifecycle_status(fields.get("lifecycle_status"))

    trusted_known_fields = dict(known_fields)
    if result["occurrence"] != "one_off" and "date_text" in trusted_known_fields:
        # See this function's own docstring: JSON-LD's single startDate/endDate isn't
        # trustworthy once this turns out to be a multi-occurrence event - prefer the LLM's
        # own (occurrence-aware) date_text, falling back to the JSON-LD one only if the LLM
        # left its own blank, rather than ending up with neither.
        trusted_known_fields["date_text"] = result.get("date_text") or trusted_known_fields["date_text"]
    result.update(trusted_known_fields)  # structured-data fields win outright, never overwritten by the LLM
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
            # Light cleanup only (whitespace/case) - events/race_types.get_or_create_race_type
            # does the real slugifying/validation before this ever reaches a label.
            "distance_category": str(distance_category).strip().lower() if distance_category else None,
        })
    return distances


_VALID_OCCURRENCE_VALUES = {"one_off", "daily", "weekly", "monthly", "yearly", "specific_dates"}
_VALID_WEEKDAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}


def _normalize_occurrence(raw: Any) -> str:
    """Falls back to 'one_off' on anything malformed/unexpected - both providers' JSON modes
    (Grok's response_format, Ollama's format="json") aren't schema-enforcing the way
    Anthropic's tool_choice is, so an off-list value has to be tolerated, not trusted."""
    if isinstance(raw, str) and raw.strip().lower() in _VALID_OCCURRENCE_VALUES:
        return raw.strip().lower()
    return "one_off"


_VALID_REGISTRATION_STATUS_VALUES = {"not_required", "open", "closed", "unknown"}


def _normalize_registration_status(raw: Any) -> str:
    """Falls back to 'unknown' on anything malformed/unexpected - same reasoning as
    _normalize_occurrence's own 'one_off' fallback, but 'unknown' rather than the most
    common value: unlike occurrence, there IS no safe "most events are like this" default
    to fall back on here (see RegistrationStatus's own docstring)."""
    if isinstance(raw, str) and raw.strip().lower() in _VALID_REGISTRATION_STATUS_VALUES:
        return raw.strip().lower()
    return "unknown"


_VALID_LIFECYCLE_STATUS_VALUES = {"scheduled", "cancelled", "postponed"}


def _normalize_lifecycle_status(raw: Any) -> str:
    """Falls back to 'scheduled' on anything malformed/unexpected - same reasoning as
    _normalize_occurrence's own 'one_off' fallback: unlike registration_status, silence/a
    malformed response here really does mean "going ahead" (see EventLifecycle's own
    docstring), so 'scheduled' is a safe default, not just a placeholder."""
    if isinstance(raw, str) and raw.strip().lower() in _VALID_LIFECYCLE_STATUS_VALUES:
        return raw.strip().lower()
    return "scheduled"


def _normalize_occurrence_weekdays(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return sorted({w.strip().lower() for w in raw if isinstance(w, str) and w.strip().lower() in _VALID_WEEKDAYS})


def _normalize_occurrences(raw: Any) -> list[dict[str, str | None]]:
    """Guards against a malformed/partial LLM response, same spirit as _normalize_distances -
    date_iso is required here (pattern_site/event_crawler.py needs a real parseable date to
    build an EventOccurrence row at all), so an entry missing/failing to parse one is dropped
    entirely rather than stored with a nonsense or absent date."""
    if not isinstance(raw, list):
        return []

    occurrences: list[dict[str, str | None]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        date_text = entry.get("date_text")
        date_iso = entry.get("date_iso")
        if not date_text or not date_iso:
            continue
        time_text = entry.get("time_text")
        time_24h = entry.get("time_24h")
        price_text = entry.get("price_text")
        occurrences.append({
            "date_text": str(date_text),
            "date_iso": str(date_iso),
            "time_text": str(time_text) if time_text else None,
            "time_24h": str(time_24h) if time_24h else None,
            "price_text": str(price_text) if price_text else None,
        })
    return occurrences


def rewrite_summary(summary: str) -> dict[str, str | None]:
    """
    Given an event's own `summary` (see extract_event_fields - either LLM-rephrased
    from the page's markdown, or read verbatim from the page's own schema.org JSON-LD
    description, see scraping/structured_data.py), asks the LLM for two derived fields:

    - summary_alt: an alternative version in genuinely original wording, not a close
      paraphrase - reduces the risk of storing/republishing another site's own copy
      verbatim (relevant since e.g. admin/export's HTML export renders it straight
      into a page other people view).
    - summary_short: a further-condensed, single-sentence summary of that.

    Returns {"summary_alt": None, "summary_short": None} without making any LLM call
    if `summary` is empty/blank - there's nothing to rewrite or condense.
    """
    if not summary or not summary.strip():
        return {"summary_alt": None, "summary_short": None}

    print(f"{datetime.now():%H:%M:%S} - rewrite_summary ({settings.llm_provider})")
    instructions = f"Original summary:\n{summary}"
    user_prompt = _build_user_prompt(instructions, _SUMMARY_REWRITE_SCHEMA_PROPERTIES, _SUMMARY_REWRITE_REQUIRED)
    try:
        fields = _run_llm(
            _SUMMARY_REWRITE_SYSTEM_PROMPT, user_prompt,
            _SUMMARY_REWRITE_SCHEMA_PROPERTIES, _SUMMARY_REWRITE_REQUIRED,
            "rewrite_summary", max_tokens=400,
        )
    except Exception as e:
        print(f"{datetime.now():%H:%M:%S} - rewrite_summary failed: {type(e).__name__}: {e}")
        return {"summary_alt": None, "summary_short": None}

    return {
        "summary_alt": fields.get("summary_alt") or None,
        "summary_short": fields.get("summary_short") or None,
    }
