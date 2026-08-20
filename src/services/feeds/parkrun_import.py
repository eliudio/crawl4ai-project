"""
Registered under "parkrun" in feed_importers.py's registry (see that module's own
docstring for the pipeline this belongs to, and why it's kept separate from
pattern_site/listing_crawler.py/discovery_handlers.py entirely rather than being
another handler there - it used to be, see git history for the "parkrun" handler
this supersedes).

Source of truth: https://github.com/josh-justjosh/parkrun-Cancellations's own
events-table.tsv - a community-maintained, MIT-licensed export of every current
parkrun/junior parkrun location worldwide (built for parkruncancellations.com,
refreshed automatically several times a day), not parkrun's own events.json feed.

Why a third-party republication, fetched as a plain "bot" (unattended, no per-run
authorisation override) - and why that's a different call from doing the same for
parkrun's own site: this repo is openly, unambiguously licensed for reuse (MIT) and
hosted on GitHub's own infrastructure, not parkrun's. Fetching it is the same kind of
act as reading any other openly licensed public dataset - it's respecting *this*
publisher's own actual, stated terms, not working around parkrun's own explicitly
stated "please don't scrape us" wish (parkrun.com/scraping) via a technicality on
parkrun's own infrastructure the way reading parkrun's own events.json under an
unattended crawl would be. That distinction is the whole reason this can hardcode
registrator="bot" unconditionally, unlike the old parkrun_feed.py/_parkrun_handler
this replaces, which refused to fetch anything at all under "bot" and only ever
produced real data via a separately-obtained-authorisation override.
"""

import csv
import io
from datetime import date

import requests
from sqlalchemy.orm import Session

from services.common.config import settings
from services.events import register_event_from_fields
from services.scraping import is_allowed

from .feed_importers import get_or_create_organiser, register_importer

__all__ = ["build_event_fields", "fetch_rows", "get_events", "run_import"]

TSV_URL = "https://raw.githubusercontent.com/josh-justjosh/parkrun-Cancellations/master/_data/events-table.tsv"
_TIMEOUT = 30

# One umbrella Organiser row for every event this importer registers - see
# feed_importers.get_or_create_organiser. Matches the name/homepage_url the old
# organisers_seed.csv row used, so an already-existing DB (seeded before this
# importer existed) picks up the very same row/id rather than creating a duplicate.
ORGANISER_NAME = "parkrun UK"
ORGANISER_HOMEPAGE_URL = "https://www.parkrun.org.uk/"

# The TSV is worldwide; this importer only ever registers one country's events per
# call (matches the old feed's own UK-default scope) - a future call with a different
# "country" param would need its own umbrella Organiser row, not this one, since
# ORGANISER_NAME/HOMEPAGE_URL above are UK-specific. Not solved here - there's no
# multi-country use yet.
DEFAULT_COUNTRY = "United Kingdom"

_JUNIOR_STATUS = "junior parkrunning"


def _is_junior(status: str | None) -> bool:
    return (status or "").strip().lower() == _JUNIOR_STATUS


def _location_text(row: dict) -> str | None:
    """County/State/Country, most specific first, skipping the TSV's own "-Unknown-"
    placeholder and any blank cell - there's no single free-text location field here
    the way the old feed's EventLocation was."""
    parts = [row.get(col) for col in ("County", "State", "Country")]
    parts = [p.strip() for p in parts if p and p.strip() and p.strip() != "-Unknown-"]
    return ", ".join(parts) or None


def _normalize_event_url(url: str) -> str:
    """Trailing slash added if missing - matches the old feed's own
    base_url + eventname + "/" convention, so a row already stored under that URL
    (from before this importer existed) gets updated in place rather than duplicated."""
    return url.rstrip("/") + "/"


def build_event_fields(row: dict, today: date | None = None) -> dict | None:
    """
    Maps one events-table.tsv row straight to an extract_event_fields()-shaped dict
    (see event_crawler.py's own _apply_fields) - no scrape, no LLM call, same idea as
    the old parkrun_feed.build_event_fields this replaces. Weekly schedule/distance/
    age-restriction conventions are parkrun's own well-known ones (regular: free 5k,
    Saturday 9am, no age limit; junior: free 2k, Sunday 9am, ages 4-14 - confirmed
    against parkrun's own support docs), not derived from the row itself beyond which
    of the two applies (the row's own Status column, not a naming-convention guess).

    today: the date this event is being registered as starting from - defaults to the
    real current date; a parameter (not always date.today() inline) purely so tests
    can pin it instead of asserting against a moving target.

    Returns None if the row has nothing to build a name or URL from at all.
    """
    name = (row.get("Event") or "").strip()
    website = (row.get("Website") or "").strip()
    if not name or not website:
        return None

    is_junior = _is_junior(row.get("Status"))
    location = _location_text(row)

    try:
        latitude = float(row["Latitude"]) if row.get("Latitude") else None
        longitude = float(row["Longitude"]) if row.get("Longitude") else None
    except ValueError:
        latitude = longitude = None

    weekday = "sun" if is_junior else "sat"
    weekday_label = "Sunday" if is_junior else "Saturday"
    distance_text = "2k" if is_junior else "5k"
    # "5k" keeps the bare "Nk" form (one of the four round-metric distances - see
    # llm/event_extraction.py's own distance_category docs); 2k takes the general
    # "{n}_k" form.
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


def fetch_rows(registrator: str = "bot") -> list[dict] | None:
    """
    Fetches and parses the TSV. Returns None on a disallowed/failed/unparseable fetch
    - same "couldn't resolve this way" contract scraping/sitemap_crawler.get_event_urls()/
    the old parkrun_feed.get_event_urls() already use, so callers can tell that apart
    from "read it fine, zero rows" (an empty list).
    """
    if not is_allowed(TSV_URL, registrator=registrator):
        print(f"ROBOTS-SKIP: {TSV_URL} (parkrun TSV import)")
        return None

    try:
        response = requests.get(TSV_URL, headers={"User-Agent": settings.user_agent}, timeout=_TIMEOUT)
        response.raise_for_status()
    except Exception as e:
        print(f"parkrun_import: failed to fetch {TSV_URL}: {type(e).__name__}: {e}")
        return None

    try:
        return list(csv.DictReader(io.StringIO(response.text), delimiter="\t"))
    except Exception as e:
        print(f"parkrun_import: failed to parse {TSV_URL}: {type(e).__name__}: {e}")
        return None


def get_events(
    country: str = DEFAULT_COUNTRY, registrator: str = "bot", today: date | None = None
) -> list[tuple[str, dict]] | None:
    """
    Every (event_url, fields) pair for one country's worth of rows - the direct-
    from-source-data shape events.register_event_from_fields expects. A row
    that fails to build_event_fields (shouldn't happen given the name/website check
    above, but never trust that blindly) is skipped rather than aborting the batch.
    """
    rows = fetch_rows(registrator=registrator)
    if rows is None:
        return None

    events = []
    for row in rows:
        if row.get("Country") != country:
            continue
        fields = build_event_fields(row, today=today)
        if fields is None:
            continue
        website = (row.get("Website") or "").strip()
        events.append((_normalize_event_url(website), fields))
    return events


def run_import(session: Session, params: dict) -> dict:
    """
    The registered "parkrun" importer itself - see feed_importers.register_importer
    below. registrator is always "bot" (see this module's own docstring for why that's
    the right call here, unlike the old registrator-override mechanism it replaces):
    there's no per-run authorisation decision left to make, so callers never pass one.
    """
    country = params.get("country", DEFAULT_COUNTRY)

    organiser = get_or_create_organiser(
        session, name=ORGANISER_NAME, homepage_url=ORGANISER_HOMEPAGE_URL, discovered_via="feed_import:parkrun",
    )

    events = get_events(country=country, registrator="bot")
    if events is None:
        return {"status": "unusable", "registered": 0, "organiser_id": organiser.id}

    for event_url, fields in events:
        register_event_from_fields(session, organiser.id, event_url, fields, "bot")

    return {"status": "ok", "registered": len(events), "organiser_id": organiser.id}


register_importer("parkrun", run_import)
