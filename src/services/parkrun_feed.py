"""
Reads parkrun's own public events.json (a GeoJSON feed of every parkrun
location worldwide) as a direct, structured source of event URLs - the same
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

import requests

from services import robots
from services.config import settings

EVENTS_JSON_URL = "https://images.parkrun.com/events.json"
_TIMEOUT = 15
UK_COUNTRY_CODE = 97


def get_event_urls(country_code: int = UK_COUNTRY_CODE) -> list[str] | None:
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
    """
    if not robots.is_allowed(EVENTS_JSON_URL):
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

    urls: list[str] = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties")
        if not isinstance(props, dict) or props.get("countrycode") != country_code:
            continue
        event_name = props.get("eventname")
        if event_name:
            urls.append(f"{base_url}/{event_name}/")

    return urls
