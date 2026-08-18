"""
Reads parkrun's own public events.json (a GeoJSON feed of every parkrun
location worldwide) as a direct, structured source of event data - the same
"prefer a structured feed over LLM-guessed listing pages" idea
sitemap_crawler.py already applies for an organiser's own sitemap, one level
cheaper here: there's no sitemap to discover via robots.txt first, just one
canonical file with everything already resolved (id, coordinates, slug,
series) - no browser, no LLM, no per-page guessing needed at all to find
every location.

Only ever reached for an organiser whose Organiser.handler is "parkrun" (see
listing_crawler.py's _parkrun_handler, registered under that name in
discovery_handlers.py's registry) - every other organiser still goes through
the "default" handler's sitemap/listing_urls mechanism as before.
"""

from datetime import date

import requests

from services import robots
from services.config import settings

EVENTS_JSON_URL = "https://images.parkrun.com/events.json"
_TIMEOUT = 15
UK_COUNTRY_CODE = 97

# "-juniors" is the feed's own eventname convention for junior parkrun - confirmed in
# practice against the real feed: 100% consistent with the feed's own seriesid (1 for
# the regular 5k series, 2 for junior), zero mismatches across all 2,962 UK entries.
_JUNIOR_SUFFIX = "-juniors"


def _country_features(country_code: int, registrator: str) -> tuple[str, list[dict]] | None:
    """
    Shared by get_event_urls/get_events below: fetches events.json (once per call -
    neither function is expected to run often enough to need its own cache) and
    returns (this country's base site URL, every feature belonging to it) - or None on
    any fetch/parse failure, or when the country/feed shape isn't what's expected.
    Only features with both a countrycode match and a non-empty eventname are
    included, since neither caller can build anything useful without one.
    """
    if not robots.is_allowed(EVENTS_JSON_URL, registrator=registrator):
        print(f"ROBOTS-SKIP: {EVENTS_JSON_URL} (parkrun feed)")
        return None

    try:
        response = requests.get(EVENTS_JSON_URL, headers={"User-Agent": settings.user_agent}, timeout=_TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print(f"parkrun_feed: failed to fetch {EVENTS_JSON_URL}: {type(e).__name__}: {e}")
        return None

    countries = data.get("countries") if isinstance(data, dict) else None
    country = (countries or {}).get(str(country_code))
    base_url = country.get("url") if isinstance(country, dict) else None
    if not base_url:
        print(f"parkrun_feed: no usable country entry for countrycode {country_code}")
        return None

    if not base_url.startswith("http"):
        base_url = f"https://{base_url}"
    base_url = base_url.rstrip("/")

    events = data.get("events") if isinstance(data, dict) else None
    features = events.get("features") if isinstance(events, dict) else None
    if not isinstance(features, list):
        print("parkrun_feed: events.features missing or not a list, giving up")
        return None

    matching = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties")
        if not isinstance(props, dict) or props.get("countrycode") != country_code:
            continue
        if not props.get("eventname"):
            continue
        matching.append(feature)

    return base_url, matching


def get_event_urls(country_code: int = UK_COUNTRY_CODE, registrator: str = "bot") -> list[str] | None:
    """
    Returns every parkrun event page URL for the given country (UK by default),
    built from events.json's own `eventname` slug and that country's base site
    URL - the same method parkrun's own third-party API libraries use
    (country.url + '/' + event.name + '/'); events.json has no pre-built full
    URL field, only the pieces to assemble one.

    Returns None on a fetch/parse failure, or when the country/feed shape isn't
    what's expected - matches sitemap_crawler.get_event_urls's own contract, so
    callers can tell "couldn't read the feed this time" apart from "read it
    fine, zero events" (an empty list).

    registrator: forwarded to robots.is_allowed() as-is - see its own docstring and
    listing_crawler.py's _parkrun_handler, the caller that actually resolves this from
    the organiser's own registrator/handler_params override.
    """
    result = _country_features(country_code, registrator)
    if result is None:
        return None
    base_url, features = result
    return [f"{base_url}/{feature['properties']['eventname']}/" for feature in features]


def _is_junior(eventname: str) -> bool:
    return eventname.endswith(_JUNIOR_SUFFIX)


def build_event_fields(feature: dict, today: date | None = None) -> dict | None:
    """
    Maps one events.json GeoJSON feature straight to an extract_event_fields()-shaped
    dict (see event_crawler.py's own _apply_fields, which both this and the real LLM
    extraction path feed into) - no scrape, no LLM call. Everything needed is already
    in the feed itself: name, location, exact coordinates, and (via the eventname
    "-juniors" suffix - see _is_junior above) which of the two standing weekly
    schedules applies - junior parkrun is a separate, 2k, Sunday 9am event for ages
    4-14 (confirmed against parkrun's own support docs - support.parkrun.com/hc/en-us/
    articles/20039077073554), distinct from the regular 5k Saturday 9am event.

    Free, no registration needed, no age restriction for the regular event - all
    confirmed parkrun conventions, not derived from anything page-specific (there's no
    page being read at all here). occurrence_ends_on is deliberately left null (an
    indefinite, standing weekly event - same "both null means indefinite, parkrun's
    actual 'forever'" convention models.py's own Occurrence docstring already
    documents), not a guessed cutoff.

    today: the date this event is being registered as starting from - defaults to the
    real current date; a parameter (not always date.today() inline) purely so tests
    can pin it instead of asserting against a moving target.

    Returns None if the feature has nothing to build a name from at all - same
    "can't build anything useful from this" contract as llm_extractor._normalize_distances.
    """
    props = feature.get("properties") if isinstance(feature, dict) else None
    if not isinstance(props, dict):
        return None
    eventname = props.get("eventname")
    if not eventname:
        return None

    is_junior = _is_junior(eventname)
    name = props.get("EventLongName") or eventname
    location = props.get("EventLocation") or None

    geometry = feature.get("geometry") if isinstance(feature, dict) else None
    coordinates = geometry.get("coordinates") if isinstance(geometry, dict) else None
    has_coordinates = isinstance(coordinates, list) and len(coordinates) == 2
    # GeoJSON order is [longitude, latitude] - the reverse of Event.latitude/longitude.
    longitude = coordinates[0] if has_coordinates else None
    latitude = coordinates[1] if has_coordinates else None

    weekday = "sun" if is_junior else "sat"
    weekday_label = "Sunday" if is_junior else "Saturday"
    distance_text = "2k" if is_junior else "5k"
    # "5k" is one of the four round-metric distances that keep the bare "Nk" form (see
    # llm_extractor.py's own distance_category docs); 2k isn't one of those four, so it
    # takes the general "{n}_k" form instead - same convention, not a special case.
    distance_category = "2_k" if is_junior else "5k"

    return {
        "name": name,
        "summary": name,
        "summary_alt": name,
        "summary_short": name,
        "sport": "running",
        "date_text": f"Every {weekday_label}, 9:00am",
        "location": location,
        "start_location": location,
        "finish_location": location,
        "age_restriction_text": "Ages 4-14" if is_junior else None,
        "is_valid_event": True,
        "invalid_reason": None,
        "registration_status": "not_required",
        "registration_text": None,
        "registration_opens_date_iso": None,
        "registration_opens_time_24h": None,
        "registration_closes_date_iso": None,
        "registration_closes_time_24h": None,
        "lifecycle_status": "scheduled",
        "lifecycle_text": None,
        "distances": [
            {"distance_text": distance_text, "price_text": "Free", "distance_category": distance_category},
        ],
        "occurrence": "weekly",
        "occurrence_weekdays": [weekday],
        "occurrence_time": "09:00",
        "occurrence_starts_on": (today or date.today()).isoformat(),
        "occurrence_ends_on": None,
        "occurrences": [],
        "latitude": latitude,
        "longitude": longitude,
    }


def get_events(
    country_code: int = UK_COUNTRY_CODE, registrator: str = "bot", today: date | None = None
) -> list[tuple[str, dict]] | None:
    """
    Like get_event_urls above, but pairs each URL with its own fully-resolved
    extract_event_fields()-shaped dict (see build_event_fields) instead of just the
    bare URL - used by listing_crawler.py's _parkrun_handler when registering events
    directly from feed data (registrator override active), never for the normal "bot"
    path, which only ever needs the URL list to feed into event_crawler.crawl_event's
    own real scrape-and-extract for each one.

    Same None/[]-vs-populated-list contract as get_event_urls; a feature that fails to
    build_event_fields (shouldn't happen given _country_features already filters out
    anything with no eventname, but never trust that blindly) is skipped rather than
    aborting the whole batch.
    """
    result = _country_features(country_code, registrator)
    if result is None:
        return None
    base_url, features = result

    events = []
    for feature in features:
        fields = build_event_fields(feature, today=today)
        if fields is None:
            continue
        event_url = f"{base_url}/{feature['properties']['eventname']}/"
        events.append((event_url, fields))
    return events
