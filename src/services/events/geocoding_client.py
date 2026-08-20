"""
Geocodes an event's own free-text location into latitude/longitude - see
common/models's Event.latitude/longitude: called once per crawl (registration.py),
cached on the row, never looked up at query time - a "near me" query then just
filters on stored coordinates, no external call per query.

Nominatim (OpenStreetMap) is the only provider today - free, no API key,
matches this project's existing "self-hosted/free by default" philosophy
(crawl4ai before Firecrawl, same idea - see scraping/backends/scraper_client.py).
Its usage policy caps at ~1 request/second and requires a descriptive User-Agent
identifying the caller - both satisfied here (settings.user_agent, already
used the same way by scraper_client.py/sitemap_crawler.py, and this module is
the one choke point every geocode call goes through, so throttling here
covers every caller regardless of which event triggered it).
"""

import re
import time as time_module
from datetime import datetime

import requests

from services.common.config import settings

__all__ = ["geocode_event_location"]

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_TIMEOUT = 15

# UK postcode, e.g. "DE45 1AH", "SW1A 1AA", "M1 1AE" - deliberately UK-only, matching
# this project's current UK-only scope (organisers_seed.csv/parkrun_import.py's own
# DEFAULT_COUNTRY), not a general address-parsing pattern.
_UK_POSTCODE_RE = re.compile(r"\b([A-Za-z]{1,2}[0-9][A-Za-z0-9]?\s*[0-9][A-Za-z]{2})\b")


def _extract_uk_postcode(text: str) -> str | None:
    match = _UK_POSTCODE_RE.search(text)
    return match.group(1) if match else None

# Nominatim's usage policy: max 1 request/second, enforced here (not per-caller)
# since this module is the single choke point every geocode() call passes
# through - a process-wide "don't call again within this long" timestamp is
# enough regardless of which organiser/event is asking.
_MIN_INTERVAL_SECONDS = 1.0
_last_request_at: float = 0.0


def _throttle() -> None:
    global _last_request_at
    elapsed = time_module.monotonic() - _last_request_at
    if elapsed < _MIN_INTERVAL_SECONDS:
        time_module.sleep(_MIN_INTERVAL_SECONDS - elapsed)
    _last_request_at = time_module.monotonic()


def geocode(address: str | None) -> tuple[float, float] | None:
    """
    Returns (latitude, longitude) for `address`, or None if it couldn't be
    resolved - blank/whitespace-only input, no match found, or the request
    itself failing are all treated the same way (return None). Callers should
    treat None as "leave Event.latitude/longitude as they were", same
    best-effort spirit as scraping/structured_data.py's own extraction - a
    geocoding hiccup shouldn't fail the whole event crawl.
    """
    if not address or not address.strip():
        return None

    _throttle()
    try:
        response = requests.get(
            _NOMINATIM_URL,
            params={"q": address, "format": "json", "limit": 1},
            headers={"User-Agent": settings.user_agent},
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        results = response.json()
    except Exception as e:
        print(f"{datetime.now():%H:%M:%S} - geocode failed for {address!r}: {type(e).__name__}: {e}")
        return None

    if not results:
        return None

    try:
        return float(results[0]["lat"]), float(results[0]["lon"])
    except (KeyError, ValueError, TypeError):
        return None


def geocode_event_location(
    location: str | None, start_location: str | None, finish_location: str | None
) -> tuple[float, float] | None:
    """
    Geocodes whichever of an event's three location fields is usable, in the same
    priority order admin/export's html_export._render_map already uses (location,
    then start_location, then finish_location) - kept consistent with that existing
    precedent rather than inventing a new one.

    Each non-blank candidate is tried two ways before moving on to the next field:
    1. As-is.
    2. If that fails and a UK postcode can be picked out of it, just that postcode.

    Confirmed in practice (a real reported case): "Bakewell Showground, Bakewell.
    DE45 1AH" fails outright against Nominatim - "Bakewell Showground" isn't indexed
    as a place at all, and Nominatim's free-text search doesn't retry "ignore the part
    it can't match" on its own - but "DE45 1AH" alone, extracted from that same
    string, resolves fine. A plain regex covers this (an unindexed venue name prefixed
    to an otherwise-good postcode) without taking on a full address-parsing library
    for it - not a general "make any address resolve" guarantee, just this one
    confirmed failure mode.

    Falls through to the next field (not just the next candidate check) when a field
    is non-blank but still can't be resolved either way - previously this returned
    None as soon as it found the first non-blank field, even if that field's own
    geocode() call failed, never trying start_location/finish_location at all.
    """
    for candidate in (location, start_location, finish_location):
        if not candidate or not candidate.strip():
            continue

        result = geocode(candidate)
        if result is not None:
            return result

        postcode = _extract_uk_postcode(candidate)
        if postcode and postcode.strip().lower() != candidate.strip().lower():
            result = geocode(postcode)
            if result is not None:
                return result

    return None
