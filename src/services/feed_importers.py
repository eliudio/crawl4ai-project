"""
Registry for the "structured bulk feed" ingestion pipeline - deliberately separate
from discovery_handlers.py/listing_crawler.py's "Organiser homepage -> listing page ->
event page" pipeline (see README.md's "Feed import pipeline" section). A feed importer
owns a whole external source end to end within one call: fetching it, resolving (or
creating) whichever Organiser row(s) its events belong to, and writing Event rows
directly - there's no separate "discover URLs" vs. "crawl each one" split the other
pipeline has, because there's no per-page scrape at all for a source like this.

Triggered by main.py's own /tasks/feed-import Pub/Sub push handler, naming which
importer to run and (optionally) importer-specific params - see
pubsub_client.publish_feed_import, and local_feed_importer.py for running one locally
without Pub/Sub. Each registered importer is independent and safe to run concurrently
with any other (separate Pub/Sub messages, the same fan-out-friendly shape the other
pipeline's own topics already have).
"""

from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from services.models import Organiser, SourceType

# (session, params) -> a small JSON-able summary dict for logging/response purposes.
# Every registered importer is responsible for doing its own real work (fetch,
# resolve/create its Organiser row(s), upsert Events) inline, before returning -
# there's nothing left for a caller to fan out afterwards.
FeedImporter = Callable[[Session, dict], dict]

_IMPORTERS: dict[str, FeedImporter] = {}


def register_importer(name: str, importer: FeedImporter) -> None:
    _IMPORTERS[name] = importer


def get_importer(name: str) -> FeedImporter | None:
    return _IMPORTERS.get(name)


def get_or_create_organiser(session: Session, *, name: str, homepage_url: str, discovered_via: str) -> Organiser:
    """
    Shared by every feed importer that represents its whole source as one umbrella
    Organiser row (parkrun, meetup, ...) rather than one per real-world event host -
    an OSM-style source is different (it discovers many distinct organisers, one per
    event, not a single umbrella one) and wouldn't use this helper at all.

    Looked up by name, not homepage_url like seed_organisers.py's own CSV-seeding
    lookup does: there's no homepage actually being scraped here to key off of - this
    importer never opens a listing/homepage page at all, so name is the stable
    identity instead. Idempotent: safe to call on every run, same as
    seed_organisers.seed_from_csv's own "sync a few fields on an already-existing row"
    behaviour.

    source_type is forced to PLATFORM on every call, not just at creation - the same
    "exists for provenance/FK purposes, structurally excluded from the pattern-website
    crawl pipeline" contract main.py/local_event_scraper.py already enforce for
    aggregator/platform rows (see models.py's own SourceType docstring: "only rows
    with source_type == organiser are ever fed into event crawling"). That's what
    keeps this row from ever being picked up by crawl_listing() again if a stray
    listing-crawl message or a bulk re-crawl ever names its id - no bespoke check
    needed anywhere else, this reuses the one that already exists.
    """
    organiser = session.scalar(select(Organiser).where(Organiser.name == name))
    if organiser is None:
        organiser = Organiser(
            name=name,
            homepage_url=homepage_url,
            source_type=SourceType.PLATFORM,
            discovered_via=discovered_via,
            registrator="bot",
        )
        session.add(organiser)
        session.flush()  # assign organiser.id
    elif organiser.source_type != SourceType.PLATFORM:
        organiser.source_type = SourceType.PLATFORM
        session.add(organiser)
    return organiser
