"""
Geocodes an event's own free-text location into latitude/longitude - see
models.py's Event.latitude/longitude: called once per crawl (event_crawler.py),
cached on the row, never looked up at query time - a "near me" query then just
filters on stored coordinates, no external call per query.

Nominatim (OpenStreetMap) is the only provider today - free, no API key,
matches this project's existing "self-hosted/free by default" philosophy
(crawl4ai before Firecrawl, same idea - see scraper_client.py). Its usage
policy caps at ~1 request/second and requires a descriptive User-Agent
identifying the caller - both satisfied here (settings.user_agent, already
used the same way by scraper_client.py/sitemap_crawler.py, and this module is
the one choke point every geocode call goes through, so throttling here
covers every caller regardless of which event triggered it).
"""

import time as time_module
from datetime import datetime

import requests

from services.config import settings

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_TIMEOUT = 15

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
    best-effort spirit as structured_data.py's own extraction - a geocoding
    hiccup shouldn't fail the whole event crawl.
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
    Geocodes whichever of an event's three location fields is usable first,
    in the same priority order export_events.py's own _render_map already
    uses (location, then start_location, then finish_location) - kept
    consistent with that existing precedent rather than inventing a new one.
    """
    for candidate in (location, start_location, finish_location):
        if candidate and candidate.strip():
            return geocode(candidate)
    return None
