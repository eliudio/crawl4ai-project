"""
Parses schema.org Event/SportsEvent JSON-LD straight out of a page's raw HTML -
the same <script type="application/ld+json"> block most event/ticketing sites
embed purely so Google can show rich results for it. Confirmed present in
practice on raceforlife.cancerresearchuk.org's event pages.

Deterministic and free (no LLM call) - event_crawler.py calls extract_event_fields()
before llm_extractor.extract_event_fields(), and whatever this finds is passed
through as `known_fields` so the LLM is only ever asked to fill in the rest
(distances/is_valid_event/invalid_reason always come from the LLM - schema.org's
Event vocabulary has no equivalent for any of those).
"""

import json
from typing import Any

from bs4 import BeautifulSoup

_EVENT_TYPE_SUFFIX = "Event"  # matches "Event", "SportsEvent", "MusicEvent", ... - schema.org's own naming convention for every Event subtype


def _iter_ld_json_objects(html: str):
    """Yields every dict found inside any application/ld+json <script> block - unwrapping
    a top-level JSON array or an @graph array, since a real page can use either shape."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("script", type="application/ld+json"):
        if not tag.string:
            continue
        try:
            data = json.loads(tag.string)
        except (ValueError, TypeError):
            continue

        candidates = data if isinstance(data, list) else [data]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            graph = candidate.get("@graph")
            if isinstance(graph, list):
                for item in graph:
                    if isinstance(item, dict):
                        yield item
            else:
                yield candidate


def _is_event_type(value: Any) -> bool:
    types = value if isinstance(value, list) else [value]
    return any(isinstance(t, str) and t.endswith(_EVENT_TYPE_SUFFIX) for t in types)


def find_event_ld_json(html: str) -> dict | None:
    """Returns the first Event-typed (Event, SportsEvent, ...) JSON-LD object found in `html`, or None if there isn't one."""
    for obj in _iter_ld_json_objects(html):
        if _is_event_type(obj.get("@type")):
            return obj
    return None


def _format_location(location: Any) -> str | None:
    """Venue name + street/city/postcode/country, comma-joined - matches the free-text
    convention llm_extractor.py's own "location" field already uses."""
    if not isinstance(location, dict):
        return None
    parts = [location.get("name")]
    address = location.get("address")
    if isinstance(address, dict):
        parts += [
            address.get("streetAddress"),
            address.get("addressLocality"),
            address.get("postalCode"),
            address.get("addressCountry"),
        ]
    elif isinstance(address, str):
        parts.append(address)
    text = ", ".join(str(p) for p in parts if p)
    return text or None


def _format_date(data: dict) -> str | None:
    start = data.get("startDate")
    end = data.get("endDate")
    if not start:
        return None
    if end and end != start:
        return f"{start} to {end}"
    return str(start)


def extract_event_fields(html: str) -> dict[str, str]:
    """
    Best-effort structured-data extraction. Returns only the keys it actually found -
    never a key with an empty/None value - so callers (llm_extractor.extract_event_fields's
    known_fields) can tell "found nothing" apart from "found an empty string". Only ever
    returns keys that are also valid Event schema fields (name/sport/summary/date_text/
    location) - distances, age_restriction_text, and is_valid_event/invalid_reason have no
    schema.org Event equivalent and are always left to the LLM.
    """
    data = find_event_ld_json(html)
    if data is None:
        return {}

    fields: dict[str, str] = {}

    if data.get("name"):
        fields["name"] = str(data["name"])

    sport = data.get("sport")
    if sport:
        fields["sport"] = str(sport).strip().lower()

    if data.get("description"):
        fields["summary"] = str(data["description"])

    date_text = _format_date(data)
    if date_text:
        fields["date_text"] = date_text

    location_text = _format_location(data.get("location"))
    if location_text:
        fields["location"] = location_text

    return fields
